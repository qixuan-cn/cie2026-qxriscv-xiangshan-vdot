//=============================================================================
// tpu_difftest_main.cpp -- 设备级 difftest 主程序（T2）
//
// 一次运行：读取一个 stream 文件，用同一份初始镜像分别喂给
//   DUT = Verilator tpu_sim_top
//   REF = tpu_ref_model.c（独立 C 实现）
// 然后按描述符退休点比对：退休地址序列 / CSR 白名单 / TMEM / DRAM。
//
// stream 文件格式（纯文本，'#' 注释）：
//   IMAGE <addr_hex> <bytes_hex>     预装载 DRAM（权重/scale/描述符/输入）
//   ACT   <idx> <128hex> <16hex>      预装载 ACT 块（64 B 数据 + 8 B scale）
//   TMEM  <addr_hex> <val32_hex>      预装载 TMEM 字
//   HEAD  <addr_hex>                  写 DQ_HEAD
//   DOOR                              写 CTRL.RUN=1（门铃）
//   EXPECT_ERR <code>                 生成端自检：REF 必须给出该错误码
//
// 退出码：0 = PASS；1 = 失配；2 = 用法/IO 错误。
//=============================================================================
#include "tpu_difftest.h"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

// ---------------------------------------------------------------- 参考侧 DRAM
static uint8_t g_mem[TPU_MEM_BYTES];

static void flat_read(void *ctx, uint32_t addr, void *buf, uint32_t n) {
  (void)ctx;
  uint8_t *out = (uint8_t *)buf;
  for (uint32_t i = 0; i < n; i++)
    out[i] = g_mem[(addr + i) & (TPU_MEM_BYTES - 1)];
}

static void flat_write(void *ctx, uint32_t addr, const void *buf, uint32_t n,
                       uint64_t strb) {
  (void)ctx;
  const uint8_t *in = (const uint8_t *)buf;
  for (uint32_t i = 0; i < n; i++)
    if (strb & (1ull << i))
      g_mem[(addr + i) & (TPU_MEM_BYTES - 1)] = in[i];
}

static const tpu_mem_ops g_ops = { flat_read, flat_write };

// ---------------------------------------------------------------- 工具
static uint32_t hex32(const std::string &s) {
  return (uint32_t)strtoul(s.c_str(), nullptr, 16);
}

static std::vector<uint8_t> parse_hex(const std::string &s) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < s.size(); i += 2) {
    char b[3] = { s[i], s[i + 1], 0 };
    out.push_back((uint8_t)strtoul(b, nullptr, 16));
  }
  return out;
}

static void image_write(tpu_dut_t *dut, uint32_t addr,
                        const std::vector<uint8_t> &bytes) {
  tpu_dut_dram_write(dut, addr, bytes.data(), (uint32_t)bytes.size());
  flat_write(nullptr, addr, bytes.data(), (uint32_t)bytes.size(), ~0ull);
}

static void act_preload(tpu_dut_t *dut, tpu_ref_t *ref, uint32_t idx,
                        const std::vector<uint8_t> &dat,
                        const std::vector<uint8_t> &scl) {
  uint8_t d[64] = {0}, s[8] = {0};
  for (size_t i = 0; i < dat.size() && i < 64; i++) d[i] = dat[i];
  for (size_t i = 0; i < scl.size() && i < 8; i++) s[i] = scl[i];
  for (uint32_t off = 0; off < 8; off++) {
    uint64_t w = 0;
    memcpy(&w, d + off * 8, 8);
    tpu_dut_act_write(dut, idx, off, w);
    tpu_ref_act_write(ref, idx, off, w);
  }
  uint64_t w = 0;
  memcpy(&w, s, 8);
  tpu_dut_act_write(dut, idx, 8, w);
  tpu_ref_act_write(ref, idx, 8, w);
}

// ---------------------------------------------------------------- 比对
static int g_fail = 0;

static void report_csr(tpu_dut_t *dut, tpu_ref_t *ref, uint32_t off,
                       const char *name, uint32_t mask) {
  uint32_t dv = tpu_dut_csr_read(dut, off) & mask;
  uint32_t rv = tpu_ref_csr_read(ref, off) & mask;
  if (dv != rv) {
    std::printf("  MISMATCH CSR %s (0x%03x): dut=%08x ref=%08x\n",
                name, off, dv, rv);
    g_fail++;
  }
}

struct CsrItem { uint32_t off; const char *name; uint32_t mask; };

static const CsrItem kCsrWhitelist[] = {
  { TPU_CS_ID,        "ID",        0xFFFFFFFFu },
  { TPU_CS_VERSION,   "VERSION",   0xFFFFFFFFu },
  { TPU_CS_CAPS,      "CAPS",      0xFFFFFFFFu },
  { TPU_CS_ERROR,     "ERROR",     0xFFFFFFFFu },
  { TPU_CS_ICOUNT,    "ICOUNT",    0xFFFFFFFFu },
  { TPU_CS_DQ_HEAD,   "DQ_HEAD",   0xFFFFFFFFu },
  { TPU_CS_DQ_TAIL,   "DQ_TAIL",   0xFFFFFFFFu },
  { TPU_CS_RET_INT8,  "RET_INT8",  0xFFFFFFFFu },
  { TPU_CS_RET_INT4,  "RET_INT4",  0xFFFFFFFFu },
  { TPU_CS_RET_TERN,  "RET_TERN",  0xFFFFFFFFu },
  { TPU_CS_DMA_LO,    "DMA_LO",    0xFFFFFFFFu },
  { TPU_CS_DMA_HI,    "DMA_HI",    0xFFFFFFFFu },
  { TPU_CS_MAC_LO,    "MAC_LO",    0xFFFFFFFFu },
  { TPU_CS_MAC_HI,    "MAC_HI",    0xFFFFFFFFu },
  { TPU_CS_DBG_DESC,  "DBG_DESC",  0xFFFFFFFFu },
  { TPU_CS_DBG_STATE, "DBG_STATE", 0xFFFFFFFFu },
  // STATUS 只比稳定位：tmem_dirty(7) / empty(3) / err(2) / halted(1) / busy(0)
  { TPU_CS_STATUS,    "STATUS",    0x8Fu },
};

int main(int argc, char **argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s <stream-file> [max_cycles]\n", argv[0]);
    return 2;
  }
  int max_cycles = (argc >= 3) ? atoi(argv[2]) : 300000;

  std::ifstream fh(argv[1]);
  if (!fh) {
    std::fprintf(stderr, "cannot open %s\n", argv[1]);
    return 2;
  }

  tpu_dut_t *dut = tpu_dut_create();
  tpu_ref_t *ref = tpu_ref_create(&g_ops, nullptr);
  if (!dut || !ref) {
    std::fprintf(stderr, "alloc failed\n");
    return 2;
  }

  int expect_err = -1;
  std::string line;
  int lineno = 0;
  while (std::getline(fh, line)) {
    lineno++;
    size_t h = line.find('#');
    if (h != std::string::npos) line = line.substr(0, h);
    if (line.empty()) continue;
    char cmd[32] = {0};
    std::istringstream is(line);
    if (!(is >> cmd)) continue;

    if (std::strcmp(cmd, "IMAGE") == 0) {
      std::string a, hx;
      is >> a >> hx;
      image_write(dut, hex32(a), parse_hex(hx));
    } else if (std::strcmp(cmd, "ACT") == 0) {
      std::string i, d, s;
      is >> i >> d >> s;
      act_preload(dut, ref, hex32(i), parse_hex(d), parse_hex(s));
    } else if (std::strcmp(cmd, "TMEM") == 0) {
      std::string a, v;
      is >> a >> v;
      uint32_t addr = hex32(a), val = hex32(v);
      tpu_dut_tmem_write(dut, addr, val);
      tpu_ref_tmem_write(ref, addr, val);
    } else if (std::strcmp(cmd, "HEAD") == 0) {
      std::string a;
      is >> a;
      uint32_t addr = hex32(a);
      tpu_dut_csr_write(dut, TPU_CS_DQ_HEAD, addr);
      tpu_ref_csr_write(ref, TPU_CS_DQ_HEAD, addr);
    } else if (std::strcmp(cmd, "DOOR") == 0) {
      tpu_dut_csr_write(dut, TPU_CS_CTRL, 1);
      tpu_ref_csr_write(ref, TPU_CS_CTRL, 1);
      tpu_ref_doorbell(ref);
    } else if (std::strcmp(cmd, "EXPECT_ERR") == 0) {
      std::string v;
      is >> v;
      expect_err = (int)hex32(v);
    } else {
      std::fprintf(stderr, "line %d: unknown command '%s'\n", lineno, cmd);
      return 2;
    }
  }

  // DUT 在 DOOR 之后才真正开始跑；REF 已在 DOOR 行内跑完。
  static uint32_t retire_dut[4096];
  int nr_dut = tpu_dut_run(dut, max_cycles, retire_dut, 4096);
  int nr_ref = tpu_ref_retire_count(ref);

  // ---------------------------------------------------------------- 比对
  int st_dut = tpu_dut_state(dut);
  int st_ref = tpu_ref_state(ref);
  if (st_dut != st_ref) {
    std::printf("  MISMATCH state: dut=%d ref=%d\n", st_dut, st_ref);
    g_fail++;
  }

  if (nr_dut != nr_ref) {
    std::printf("  MISMATCH retire count: dut=%d ref=%d\n", nr_dut, nr_ref);
    g_fail++;
  }
  int nr_cmp = (nr_dut < nr_ref) ? nr_dut : nr_ref;
  for (int i = 0; i < nr_cmp; i++) {
    uint32_t a = retire_dut[i];
    uint32_t b = tpu_ref_retire_addr(ref, i);
    if (a != b) {
      std::printf("  MISMATCH retire[%d]: dut=%08x ref=%08x\n", i, a, b);
      g_fail++;
      break;
    }
  }

  for (const CsrItem &it : kCsrWhitelist)
    report_csr(dut, ref, it.off, it.name, it.mask);

  if (expect_err >= 0) {
    uint32_t got = tpu_ref_csr_read(ref, TPU_CS_ERROR);
    if ((int)got != expect_err) {
      std::printf("  EXPECT_ERR mismatch: want=%02x ref=%02x\n", expect_err, got);
      g_fail++;
    }
  }

  // TMEM 全量
  for (uint32_t i = 0; i < TPU_TMEM_WORDS; i++) {
    uint32_t dv = tpu_dut_tmem_read(dut, i);
    uint32_t rv = tpu_ref_tmem_read(ref, i);
    if (dv != rv) {
      std::printf("  MISMATCH TMEM[%04x]: dut=%08x ref=%08x\n", i, dv, rv);
      g_fail++;
      break;
    }
  }

  // DRAM 全量（1 MiB）
  static uint8_t dbuf[TPU_MEM_BYTES];
  tpu_dut_dram_read(dut, 0, dbuf, TPU_MEM_BYTES);
  for (uint32_t i = 0; i < TPU_MEM_BYTES; i++) {
    if (dbuf[i] != g_mem[i]) {
      std::printf("  MISMATCH DRAM[%05x]: dut=%02x ref=%02x\n", i, dbuf[i],
                  g_mem[i]);
      g_fail++;
      break;
    }
  }

  // T4：STALL_MEM 只做趋势观察（DUT 独有，不参与相等比对）
  uint32_t stall_mem = tpu_dut_csr_read(dut, TPU_CS_STALL_MEM);

  std::printf("%s %s (retire=%d/%d state=%d stall=%u)\n",
              g_fail ? "FAIL" : "PASS", argv[1], nr_dut, nr_ref, st_dut,
              stall_mem);

  tpu_ref_destroy(ref);
  tpu_dut_destroy(dut);
  return g_fail ? 1 : 0;
}
