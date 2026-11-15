//=============================================================================
// tpu_ref_model.c -- RiscvTpu V1 设备级参考模型（T2）
//
// 这是一份「独立于 Verilator RTL」的 C 实现：完全按 docs/03 的数值契约与
// rtl/tpu_{top,csr,mxu,quant,copy,act,tmem}.sv 的时序语义重写，不调用 Python
// 黄金模型，从而打破 T0 的共模误差。
//
// 提交模型：一次门铃从 DQ_HEAD 开始，按描述符链跑到 HALT 或 ERR；
// 每个 DRAIN 视为一次「退休」，记录 cur_addr。
//
// 数值原语（f_mul / f_add / f_i2f48 / f_scale_pow2）逐行移植 tpu_pkg.sv。
//=============================================================================
#include "tpu_difftest.h"

#include <stdlib.h>
#include <string.h>

#define F_ACC   (1u << 0)
#define F_RMAX  (1u << 1)
#define F_ASCL  (1u << 2)
#define F_RELU  (1u << 3)
#define F_TB    (1u << 4)
#define F_ZP    (1u << 5)
#define F_PAIR  (1u << 6)
#define FLAGS_V1 0x29u

#define RETIRE_MAX 4096

struct tpu_ref {
  const tpu_mem_ops *ops;
  void *ctx;

  uint8_t *act;   // TPU_ACT_NBLK * 64
  uint8_t *scl;   // TPU_ACT_NBLK * 8
  uint32_t *tmem; // TPU_TMEM_WORDS

  uint32_t dq_base, dq_head, dq_tail;
  uint32_t ctrl;
  uint8_t  err_code;
  uint8_t  irq_pend;
  uint8_t  tmem_dirty;
  uint8_t  timeout_flag;

  uint32_t icount;
  uint64_t mac;
  uint64_t dma;
  uint32_t ret_i8, ret_i4, ret_tn;

  int      state;
  uint32_t cur_addr;

  uint32_t retire[RETIRE_MAX];
  int      nretire;
};

// ---------------------------------------------------------------- fp32 原语
static uint32_t f_i2f48(int64_t v) {
  int      sgn = (v < 0);
  uint64_t mag = sgn ? (uint64_t)(-v) : (uint64_t)v;
  mag &= 0xFFFFFFFFFFFFull;
  if (mag == 0) return 0;
  int h = 0;
  for (int i = 0; i < 48; i++) if ((mag >> i) & 1ull) h = i;
  uint32_t m25;
  if (h >= 23) {
    uint32_t shf = (uint32_t)((mag >> (h - 23)) & 0xFFFFFFu);
    m25 = shf;
    int gd = 0, st = 0;
    if (h >= 24) {
      gd = (int)((mag >> (h - 24)) & 1ull);
      uint64_t lowmask = (1ull << (h - 24)) - 1ull;
      st = (mag & lowmask) != 0;
    }
    if (gd && (st || (m25 & 1u))) m25++;
    if (m25 & (1u << 24)) { m25 >>= 1; h++; }
  } else {
    m25 = (uint32_t)((mag << (23 - h)) & 0xFFFFFFu);
  }
  return ((uint32_t)sgn << 31) | ((uint32_t)(h + 127) << 23) | (m25 & 0x7FFFFFu);
}

static uint32_t f_mul(uint32_t a, uint32_t b) {
  uint32_t ea = (a >> 23) & 0xFFu, eb = (b >> 23) & 0xFFu;
  if (ea == 0 || eb == 0) return 0;
  if (ea == 0xFF || eb == 0xFF) return 0;
  uint32_t sg = (a ^ b) & 0x80000000u;
  uint64_t p = (uint64_t)(0x800000u | (a & 0x7FFFFFu)) *
               (uint64_t)(0x800000u | (b & 0x7FFFFFu));
  int e = (int)ea + (int)eb - 127;
  if (p & (1ull << 47)) { p = (p >> 1) | (p & 1ull); e++; }
  uint32_t m24 = (uint32_t)((p >> 23) & 0x7FFFFFu);   // {1'b0, p[45:23]}
  uint32_t guard = (uint32_t)((p >> 22) & 1u);
  uint32_t sticky = (p & 0x3FFFFFu) != 0;
  uint32_t lsb = (uint32_t)((p >> 23) & 1u);
  if (guard && (sticky || lsb)) m24++;
  if (m24 & 0x800000u) { m24 >>= 1; e++; }
  if (e <= 0) return 0;
  if (e >= 255) return sg | 0x7F800000u;
  return sg | ((uint32_t)e << 23) | (m24 & 0x7FFFFFu);
}

static uint32_t f_add(uint32_t a, uint32_t b) {
  uint32_t ea0 = (a >> 23) & 0xFFu, eb0 = (b >> 23) & 0xFFu;
  uint32_t av = (ea0 == 0 || ea0 == 0xFF) ? 0 : a;
  uint32_t bv = (eb0 == 0 || eb0 == 0xFF) ? 0 : b;
  if ((av & 0x7FFFFFFFu) == 0)
    return ((bv & 0x7FFFFFFFu) == 0) ? 0 : bv;
  if ((bv & 0x7FFFFFFFu) == 0) return av;
  uint32_t hi, lo;
  if ((av & 0x7FFFFFFFu) >= (bv & 0x7FFFFFFFu)) { hi = av; lo = bv; }
  else { hi = bv; lo = av; }
  uint32_t ea = (hi >> 23) & 0xFFu, eb = (lo >> 23) & 0xFFu;
  uint32_t ma = (0x800000u | (hi & 0x7FFFFFu)) << 5;   // 29 bit
  uint32_t mb = (0x800000u | (lo & 0x7FFFFFu)) << 5;
  int d = (int)ea - (int)eb;
  if (d > 27) d = 27;
  uint32_t st = (d == 0) ? 0 : ((mb & ((1u << d) - 1u)) != 0);
  mb = (mb >> d) | st;
  uint32_t ms = ((hi >> 31) == (lo >> 31)) ? (ma + mb) : (ma - mb);
  if (ms == 0) return 0;
  int h = 0;
  for (int i = 0; i <= 30; i++) if ((ms >> i) & 1u) h = i;
  int e = (int)ea + (h - 28);
  if (h > 28) {
    st = (ms & ((1u << (h - 28)) - 1u)) != 0;
    ms = (ms >> (h - 28)) | st;
  } else if (h < 28) {
    ms = ms << (28 - h);
  }
  if ((ms & 0x10u) && (((ms & 0xFu) != 0) || (ms & 0x20u))) ms += 32u;
  if (ms & (1u << 29)) { ms >>= 1; e++; }
  if (e <= 0) return 0;
  if (e >= 255) return (hi & 0x80000000u) | 0x7F800000u;
  return (hi & 0x80000000u) | ((uint32_t)e << 23) | ((ms >> 5) & 0x7FFFFFu);
}

static uint32_t f_scale_pow2(uint32_t x, int e) {
  uint32_t p = (uint32_t)((127 + e) & 0xFF) << 23;
  return f_mul(x, p);
}

// ---------------------------------------------------------------- 存储器
static void mem_rd(tpu_ref_t *r, uint32_t addr, void *buf, uint32_t n) {
  r->ops->read(r->ctx, addr, buf, n);
}
static void mem_wr(tpu_ref_t *r, uint32_t addr, const void *buf, uint32_t n,
                   uint64_t strb) {
  r->ops->write(r->ctx, addr, buf, n, strb);
}

// ---------------------------------------------------------------- 生命周期
tpu_ref_t *tpu_ref_create(const tpu_mem_ops *ops, void *ctx) {
  tpu_ref_t *r = (tpu_ref_t *)calloc(1, sizeof(tpu_ref_t));
  if (!r) return NULL;
  r->ops = ops;
  r->ctx = ctx;
  r->act = (uint8_t *)calloc(TPU_ACT_NBLK * 64u, 1);
  r->scl = (uint8_t *)calloc(TPU_ACT_NBLK * 8u, 1);
  r->tmem = (uint32_t *)calloc(TPU_TMEM_WORDS, sizeof(uint32_t));
  if (!r->act || !r->scl || !r->tmem) {
    tpu_ref_destroy(r);
    return NULL;
  }
  tpu_ref_reset(r);
  return r;
}

void tpu_ref_destroy(tpu_ref_t *r) {
  if (!r) return;
  free(r->act);
  free(r->scl);
  free(r->tmem);
  free(r);
}

void tpu_ref_reset(tpu_ref_t *r) {
  memset(r->act, 0, TPU_ACT_NBLK * 64u);
  memset(r->scl, 0, TPU_ACT_NBLK * 8u);
  memset(r->tmem, 0, TPU_TMEM_WORDS * sizeof(uint32_t));
  r->dq_base = r->dq_head = r->dq_tail = 0;
  r->ctrl = 0;
  r->err_code = 0;
  r->irq_pend = 0;
  r->tmem_dirty = 0;
  r->timeout_flag = 0;
  r->icount = 0;
  r->mac = r->dma = 0;
  r->ret_i8 = r->ret_i4 = r->ret_tn = 0;
  r->state = TPU_S_IDLE;
  r->cur_addr = 0;
  r->nretire = 0;
}

// ---------------------------------------------------------------- CSR
uint32_t tpu_ref_csr_read(tpu_ref_t *r, uint32_t off) {
  switch (off) {
    case TPU_CS_ID:        return 0x52545650u;
    case TPU_CS_VERSION:   return 0x01010840u;
    case TPU_CS_CTRL:      return r->ctrl & 0xFFu;
    case TPU_CS_STATUS: {
      uint32_t v = 0;
      v |= (uint32_t)(r->tmem_dirty & 1) << 7;
      v |= (uint32_t)(r->irq_pend & 1) << 6;
      v |= (uint32_t)(r->timeout_flag & 1) << 5;
      v |= (uint32_t)(r->dq_head == r->dq_tail) << 3;
      v |= (uint32_t)(r->err_code != 0) << 2;
      v |= (uint32_t)(r->state == TPU_S_HALT) << 1;
      v |= 0u;
      return v;
    }
    case TPU_CS_DQ_BASE:   return r->dq_base;
    case TPU_CS_DQ_HEAD:   return r->dq_head;
    case TPU_CS_DQ_TAIL:   return r->dq_tail;
    case TPU_CS_CYCLES_LO: return 0;
    case TPU_CS_CYCLES_HI: return 0;
    case TPU_CS_ICOUNT:    return r->icount;
    case TPU_CS_MAC_LO:    return (uint32_t)r->mac;
    case TPU_CS_MAC_HI:    return (uint32_t)(r->mac >> 32);
    case TPU_CS_STALL_MEM: return 0;
    case TPU_CS_RET_INT8:  return r->ret_i8;
    case TPU_CS_RET_INT4:  return r->ret_i4;
    case TPU_CS_RET_TERN:  return r->ret_tn;
    case TPU_CS_CAPS:      return 0x27u;
    case TPU_CS_ERROR:     return r->err_code;
    case TPU_CS_DMA_LO:    return (uint32_t)r->dma;
    case TPU_CS_DMA_HI:    return (uint32_t)(r->dma >> 32);
    case TPU_CS_DBG_DESC:  return r->cur_addr;
    case TPU_CS_DBG_STATE: return (uint32_t)r->state;
    case TPU_CS_DBG_ACC0:  return 0;
    case TPU_CS_DBG_ACC1:  return 0;
    default:               return 0;
  }
}

void tpu_ref_csr_write(tpu_ref_t *r, uint32_t off, uint32_t val) {
  if (off == TPU_CS_CTRL) {
    r->ctrl = val & 0xFFu;
    if (val & 0x4u) {  // CTRL.CLR_CNT
      r->icount = 0; r->mac = 0; r->dma = 0;
      r->ret_i8 = r->ret_i4 = r->ret_tn = 0;
    }
    if (val & 0x2u) {  // CTRL.SOFT_RESET
      r->state = TPU_S_IDLE;
      r->tmem_dirty = 0;
      r->timeout_flag = 0;
    }
  } else if (off == TPU_CS_DQ_BASE) {
    r->dq_base = val;
  } else if (off == TPU_CS_DQ_HEAD) {
    r->dq_head = val;
  } else if (off == TPU_CS_ERROR) {
    if (val & 1u) r->err_code = 0;
  }
}

// ---------------------------------------------------------------- 内部访问
void tpu_ref_tmem_write(tpu_ref_t *r, uint32_t idx, uint32_t val) {
  r->tmem[idx & (TPU_TMEM_WORDS - 1)] = val;
}
uint32_t tpu_ref_tmem_read(tpu_ref_t *r, uint32_t idx) {
  return r->tmem[idx & (TPU_TMEM_WORDS - 1)];
}
void tpu_ref_act_write(tpu_ref_t *r, uint32_t idx, uint32_t off, uint64_t data) {
  idx &= (TPU_ACT_NBLK - 1);
  if (off < 8) memcpy(r->act + (size_t)idx * 64u + off * 8u, &data, 8);
  else         memcpy(r->scl + (size_t)idx * 8u, &data, 8);
}

int tpu_ref_state(tpu_ref_t *r) { return r->state; }
int tpu_ref_retire_count(tpu_ref_t *r) { return r->nretire; }
uint32_t tpu_ref_retire_addr(tpu_ref_t *r, int i) {
  return (i >= 0 && i < r->nretire) ? r->retire[i] : 0xFFFFFFFFu;
}

// ---------------------------------------------------------------- 校验
static int check_desc(const uint32_t *w, uint32_t addr) {
  uint32_t w0 = w[0];
  if ((addr & 0x3Fu) != 0) return TPU_E_DESC_ALIGN;
  if ((w0 >> 22) != 0 || (w[12] >> 16) != 0 || (w[15] >> 16) != 0 ||
      (((w0 >> 8) & 0xFFu) & ~FLAGS_V1) != 0 ||
      (((w0 >> 20) & 3u) == 3u))
    return TPU_E_DESC_FLAGS;
  uint32_t op = w0 & 0xFFu;
  if (!(op == TPU_OP_GEMM || op == TPU_OP_COPY || op == TPU_OP_QUANT ||
        op == TPU_OP_BAR || op == TPU_OP_NOP || op == TPU_OP_END))
    return TPU_E_DESC_OP;
  uint32_t sbuf = (w0 >> 16) & 0xFu;
  if (sbuf > 1u) return TPU_E_SBUF;
  uint32_t m = w[1], n = w[2], k = w[3];
  uint32_t a = w[4], b = w[5], c = w[6], ldc = w[9], sc = w[10];
  uint32_t prec = (w0 >> 20) & 3u;
  if (op == TPU_OP_GEMM) {
    if (m == 0 || m > 64u || n == 0 || k == 0 || k > 2048u) return TPU_E_SHAPE;
    if ((uint32_t)(b + m * 32u) > 2048u) return TPU_E_DESC_RANGE;
    if ((a & 0xFu) != 0 || (ldc & 3u) != 0 ||
        ((prec == 0) ? ((sc & 3u) != 0) : ((sc & 7u) != 0)))
      return TPU_E_ADDR_ALIGN;
    if ((uint32_t)(c + (m - 1u) * (ldc >> 2) + n) > 16384u)
      return TPU_E_DESC_RANGE;
  } else if (op == TPU_OP_QUANT) {
    if (m == 0 || m > 64u) return TPU_E_SHAPE;
    if ((a & 3u) != 0 || (b & 3u) != 0 || (w[7] & 3u) != 0) return TPU_E_ADDR_ALIGN;
    if ((uint32_t)(c + m) > 2048u) return TPU_E_DESC_RANGE;
  } else if (op == TPU_OP_COPY) {
    if (m == 0) return TPU_E_SHAPE;
  }
  return TPU_E_NONE;
}

// ---------------------------------------------------------------- 单元执行
static int32_t grp_sum(const uint8_t *ab, const uint8_t *wb, uint32_t prec,
                       int g, int cnt) {
  int32_t s = 0;
  for (int i = 0; i < TPU_GROUP; i++) {
    int e = g * TPU_GROUP + i;
    if (e >= cnt) continue;
    int a = (int)(int8_t)ab[e];
    int w;
    if (prec == 0) {
      w = (int)(int8_t)wb[e];
    } else if (prec == 1) {
      uint8_t byt = wb[e / 2];
      int nib = (byt >> (4 * (e % 2))) & 0xF;
      w = (nib & 8) ? nib - 16 : nib;
    } else {
      uint8_t byt = wb[e / 4];
      int code = (byt >> (2 * (e % 4))) & 3;
      w = (code == 1) ? 1 : (code == 2 ? -1 : 0);
    }
    int16_t p = (int16_t)(a * w);
    int32_t t = s + (int32_t)p;
    s = (int32_t)((t << 8) >> 8);   // 24-bit signed wrap
  }
  return s;
}

static void exec_gemm(tpu_ref_t *r, const uint32_t *w, int *mac_out,
                      uint64_t *dma_out) {
  uint32_t w0 = w[0];
  uint32_t prec = (w0 >> 20) & 3u;
  uint32_t flags = (w0 >> 8) & 0xFFu;
  uint32_t m = w[1], n = w[2], k = w[3];
  uint32_t a = w[4], b = w[5], c = w[6];
  uint32_t lda = w[7], ldc = w[9], sc = w[10];
  uint32_t k0 = w[12] & 0xFFFFu;
  uint32_t srs = w[15] & 0xFFFFu;
  uint32_t kb = (k + 63u) >> 6;
  int W = (prec == 0) ? 16 : 11;
  uint32_t blk_bytes = (prec == 0) ? 64u : (prec == 1 ? 32u : 16u);

  uint32_t srs_eff = srs;
  if (srs_eff == 0) srs_eff = (prec == 0) ? (4u + kb) : (8u + 4u * kb);

  *mac_out = 0;
  *dma_out = 0;
  int ovf = 0;

  for (uint32_t col = 0; col < n; col++) {
    uint8_t sw[4];
    mem_rd(r, sc + col * srs_eff, sw, 4);
    uint32_t sw_bits = (uint32_t)sw[0] | ((uint32_t)sw[1] << 8) |
                       ((uint32_t)sw[2] << 16) | ((uint32_t)sw[3] << 24);
    for (uint32_t jg = 0; jg * TPU_MCOLS < m; jg++) {
      int64_t acc[TPU_MCOLS];
      uint32_t sa[TPU_MCOLS];
      for (int cc = 0; cc < TPU_MCOLS; cc++) { acc[cc] = 0; sa[cc] = 0; }
      for (uint32_t kb_i = 0; kb_i < kb; kb_i++) {
        int cnt = (kb_i == kb - 1) ? (int)(k - (kb_i << 6)) : 64;
        uint8_t wb[64];
        mem_rd(r, a + col * lda + kb_i * blk_bytes, wb, 64);
        uint8_t swd[4];
        uint32_t saddr = (prec == 0)
            ? (sc + col * srs_eff + 4u + k0 + kb_i)
            : (sc + col * srs_eff + 8u + 4u * (k0 + kb_i));
        mem_rd(r, saddr, swd, 4);
        uint32_t sc_word = (uint32_t)swd[0] | ((uint32_t)swd[1] << 8) |
                           ((uint32_t)swd[2] << 16) | ((uint32_t)swd[3] << 24);
        uint32_t dw = (prec == 0) ? (sc_word & 0xFFu) : ((sc_word >> 0) & 0xFFu);
        for (int cc = 0; cc < TPU_MCOLS; cc++) {
          if (jg * TPU_MCOLS + (uint32_t)cc >= m) continue;
          uint32_t idx = (b + (jg * TPU_MCOLS + (uint32_t)cc) * TPU_ACT_BLOCKS +
                          kb_i) & (TPU_ACT_NBLK - 1);
          const uint8_t *ab = r->act + (size_t)idx * 64u;
          const uint8_t *sa_rec = r->scl + (size_t)idx * 8u;
          if (kb_i == 0) {
            sa[cc] = (uint32_t)sa_rec[0] | ((uint32_t)sa_rec[1] << 8) |
                     ((uint32_t)sa_rec[2] << 16) | ((uint32_t)sa_rec[3] << 24);
          }
          for (int g = 0; g < TPU_NGROUP; g++) {
            int sh = W - (int)dw - (int)sa_rec[4 + g];
            uint32_t mul = (prec == 0) ? 1u : ((sc_word >> (8 + 6 * g)) & 0x3Fu);
            if (sh < 0) continue;
            int32_t gs = grp_sum(ab, wb, prec, g, cnt);
            int64_t mg = (int64_t)((uint64_t)mul << (uint32_t)sh);
            acc[cc] += mg * (int64_t)gs;
          }
          if (acc[cc] >= (int64_t)140737488355328LL ||
              acc[cc] < -(int64_t)140737488355328LL)
            ovf = 1;
        }
      }
      // epilogue
      for (int cc = 0; cc < TPU_MCOLS; cc++) {
        if (jg * TPU_MCOLS + (uint32_t)cc >= m) continue;
        uint32_t y = f_mul(f_mul(f_i2f48(acc[cc]), sw_bits),
                           f_scale_pow2(sa[cc], -W));
        if ((flags & F_RELU) && (y & 0x80000000u) && (y & 0x7FFFFFFFu))
          y = 0;
        uint32_t epi = (c + (jg * TPU_MCOLS + (uint32_t)cc) * (ldc >> 2) +
                        col) & (TPU_TMEM_WORDS - 1);
        if (flags & F_ACC) y = f_add(r->tmem[epi], y);
        r->tmem[epi] = y;
      }
    }
  }

  if (ovf) {
    r->state = TPU_S_ERR;
    r->err_code = TPU_E_ACC_OVF;
  }
  *mac_out = (int)(uint64_t)m * n * kb * TPU_D;
  *dma_out = 0;
}

static void exec_copy(tpu_ref_t *r, const uint32_t *w, uint64_t *dma_out) {
  uint32_t w0 = w[0];
  uint32_t mode = (w0 >> 16) & 1u;
  uint32_t m = w[1];
  uint32_t a = w[4], c = w[6];
  uint32_t lda = w[7], ldb = w[8], ldc = w[9];
  uint64_t dma = 0;
  uint32_t beats = (lda + 63u) >> 6;
  for (uint32_t row = 0; row < m; row++) {
    for (uint32_t beat = 0; beat < beats; beat++) {
      uint32_t nbytes = (lda - beat * 64u > 64u) ? 64u : (lda - beat * 64u);
      uint32_t src_b, dst_b;
      uint8_t blk[64];
      if (mode) {
        src_b = a + row * ldb + beat * 16u;
        for (int i = 0; i < 16; i++) {
          uint32_t v = r->tmem[(src_b + (uint32_t)i) & (TPU_TMEM_WORDS - 1)];
          blk[i * 4 + 0] = (uint8_t)(v);
          blk[i * 4 + 1] = (uint8_t)(v >> 8);
          blk[i * 4 + 2] = (uint8_t)(v >> 16);
          blk[i * 4 + 3] = (uint8_t)(v >> 24);
        }
      } else {
        src_b = a + row * ldb + beat * 64u;
        mem_rd(r, src_b, blk, 64);
      }
      dst_b = c + row * ldc + beat * 64u;
      uint64_t strb = (nbytes >= 64u) ? ~0ull : ((1ull << nbytes) - 1ull);
      mem_wr(r, dst_b, blk, 64, strb);
      dma += nbytes;
    }
  }
  *dma_out = dma;
}

static void exec_quant(tpu_ref_t *r, const uint32_t *w, uint64_t *dma_out) {
  uint32_t m = w[1];
  uint32_t a = w[4], b = w[5], c = w[6];
  uint32_t lda = w[7];
  for (uint32_t row = 0; row < m; row++) {
    uint32_t base_w = a + row * (lda >> 2);
    uint32_t sbase_w = b + row * 2u;
    uint8_t data[64];
    for (int i = 0; i < 16; i++) {
      uint32_t v = r->tmem[(base_w + (uint32_t)i) & (TPU_TMEM_WORDS - 1)];
      data[i * 4 + 0] = (uint8_t)(v);
      data[i * 4 + 1] = (uint8_t)(v >> 8);
      data[i * 4 + 2] = (uint8_t)(v >> 16);
      data[i * 4 + 3] = (uint8_t)(v >> 24);
    }
    uint8_t rec[8];
    for (int i = 0; i < 2; i++) {
      uint32_t v = r->tmem[(sbase_w + (uint32_t)i) & (TPU_TMEM_WORDS - 1)];
      rec[i * 4 + 0] = (uint8_t)(v);
      rec[i * 4 + 1] = (uint8_t)(v >> 8);
      rec[i * 4 + 2] = (uint8_t)(v >> 16);
      rec[i * 4 + 3] = (uint8_t)(v >> 24);
    }
    uint32_t idx = (c + row) & (TPU_ACT_NBLK - 1);
    for (uint32_t off = 0; off < 8; off++) {
      uint64_t dw;
      memcpy(&dw, data + off * 8u, 8);
      tpu_ref_act_write(r, idx, off, dw);
    }
    uint64_t rw;
    memcpy(&rw, rec, 8);
    tpu_ref_act_write(r, idx, 8, rw);
  }
  *dma_out = (uint64_t)m * 72u;
}

// ---------------------------------------------------------------- 门铃
int tpu_ref_doorbell(tpu_ref_t *r) {
  if (r->state == TPU_S_ERR) return 0;   // 需要先清错误或软复位
  if (r->dq_head == r->dq_tail) return 0;

  int guard = 0;
  for (;;) {
    if (++guard > 100000) {
      r->state = TPU_S_ERR;
      r->err_code = TPU_E_TIMEOUT;
      break;
    }
    uint32_t w[16];
    r->cur_addr = r->dq_head;
    mem_rd(r, r->cur_addr, w, 64);

    int e = check_desc(w, r->cur_addr);
    if (e != TPU_E_NONE) {
      r->err_code = (uint8_t)e;
      r->state = TPU_S_ERR;
      break;
    }

    uint32_t op = w[0] & 0xFFu;
    uint32_t prec = (w[0] >> 20) & 3u;
    int mac = 0;
    uint64_t dma = 0;

    if (op == TPU_OP_GEMM) {
      exec_gemm(r, w, &mac, &dma);
      if (r->state == TPU_S_ERR) break;   // ACC_OVF
      r->tmem_dirty = 1;
    } else if (op == TPU_OP_COPY) {
      exec_copy(r, w, &dma);
      if (((w[0] >> 16) & 1u)) r->tmem_dirty = 0;
    } else if (op == TPU_OP_QUANT) {
      exec_quant(r, w, &dma);
    }

    // 退休（DRAIN 语义）
    if (op != TPU_OP_NOP) {
      r->icount++;
      r->mac += (uint64_t)mac;
      r->dma += dma;
      if (op == TPU_OP_GEMM) {
        if (prec == 0) r->ret_i8++;
        else if (prec == 1) r->ret_i4++;
        else r->ret_tn++;
      } else {
        r->ret_tn++;   // BAR/COPY/QUANT/END：RTL ev_prec=2'b11
      }
    }
    if (r->nretire < RETIRE_MAX) r->retire[r->nretire++] = r->cur_addr;

    uint32_t cur = r->cur_addr;
    uint32_t next = w[11];
    r->dq_tail = cur;
    if (next == 0 || op == TPU_OP_END) {
      r->dq_head = cur;
      r->state = TPU_S_HALT;
      break;
    }
    r->dq_head = next;
  }
  return r->nretire;
}
