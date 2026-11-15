//=============================================================================
// tpu_emu_selftest.c -- T3a：emu MMIO 适配层回归（宿主侧，无需重建 emu）
//
// 对每个 *.stream 跑两遍同一个 tpu_ref_model：
//   A：CSR 写/门铃走 tpu_emu_mmio_write（模拟 emu 的 MMIO 路径）
//   B：直接调 tpu_ref_csr_write + tpu_ref_doorbell
// 两者必须逐位一致（CSR 白名单、TMEM 16K 字、DRAM 1 MiB、错误码）。
// 这样在 emu 重建之前就能锁住偏移合取、字节掩码与门铃时机。
//
// 用法： tpu_emu_selftest <streams_dir>
//=============================================================================
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "tpu_difftest.h"
#include "tpu_emu.h"

static uint8_t g_memA[TPU_MEM_BYTES], g_memB[TPU_MEM_BYTES];
static int     g_fail;

// ---------------------------------------------------------------- 扁平内存
static void flat_read(void *ctx, uint32_t addr, void *buf, uint32_t n) {
  uint8_t *m = (uint8_t *)ctx;
  for (uint32_t i = 0; i < n; i++) ((uint8_t *)buf)[i] = m[(addr + i) & (TPU_MEM_BYTES - 1)];
}

static void flat_write(void *ctx, uint32_t addr, const void *buf, uint32_t n,
                       uint64_t strb) {
  uint8_t *m = (uint8_t *)ctx;
  const uint8_t *in = (const uint8_t *)buf;
  for (uint32_t i = 0; i < n; i++)
    if (strb & (1ull << i)) m[(addr + i) & (TPU_MEM_BYTES - 1)] = in[i];
}

static const tpu_mem_ops kFlatOps = { flat_read, flat_write };

// ---------------------------------------------------------------- 工具
static int hexval(int c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

static uint32_t hex2bin(const char *s, uint8_t *out, uint32_t max) {
  uint32_t n = 0;
  while (s[0] && s[1] && n < max) {
    int hi = hexval(s[0]), lo = hexval(s[1]);
    if (hi < 0 || lo < 0) break;
    out[n++] = (uint8_t)((hi << 4) | lo);
    s += 2;
  }
  return n;
}

static const uint32_t kCsr[] = {
  TPU_CS_ID, TPU_CS_VERSION, TPU_CS_CAPS, TPU_CS_ERROR, TPU_CS_ICOUNT,
  TPU_CS_DQ_HEAD, TPU_CS_DQ_TAIL, TPU_CS_RET_INT8, TPU_CS_RET_INT4,
  TPU_CS_RET_TERN, TPU_CS_DMA_LO, TPU_CS_DMA_HI, TPU_CS_MAC_LO, TPU_CS_MAC_HI,
};

static void compare(tpu_ref_t *a, tpu_ref_t *b, const char *name) {
  for (size_t i = 0; i < sizeof(kCsr) / sizeof(kCsr[0]); i++) {
    uint32_t va = tpu_ref_csr_read(a, kCsr[i]);
    uint32_t vb = tpu_ref_csr_read(b, kCsr[i]);
    if (va != vb) {
      printf("MISMATCH %s CSR[%03x]: mmio=%08x direct=%08x\n", name, kCsr[i], va, vb);
      g_fail = 1;
      return;
    }
  }
  for (uint32_t i = 0; i < TPU_TMEM_WORDS; i++) {
    uint32_t va = tpu_ref_tmem_read(a, i), vb = tpu_ref_tmem_read(b, i);
    if (va != vb) {
      printf("MISMATCH %s TMEM[%04x]: mmio=%08x direct=%08x\n", name, i, va, vb);
      g_fail = 1;
      return;
    }
  }
  if (memcmp(g_memA, g_memB, TPU_MEM_BYTES) != 0) {
    for (uint32_t i = 0; i < TPU_MEM_BYTES; i++)
      if (g_memA[i] != g_memB[i]) {
        printf("MISMATCH %s DRAM[%05x]: mmio=%02x direct=%02x\n", name, i,
               g_memA[i], g_memB[i]);
        break;
      }
    g_fail = 1;
  }
}

// ---------------------------------------------------------------- 单个 stream
static void run_stream(const char *path) {
  FILE *fp = fopen(path, "r");
  if (!fp) { printf("FAIL 打开 %s\n", path); g_fail = 1; return; }

  memset(g_memA, 0, sizeof(g_memA));
  memset(g_memB, 0, sizeof(g_memB));
  tpu_emu_init(&kFlatOps, g_memA);
  tpu_ref_t *A = tpu_emu_model();
  tpu_ref_t *B = tpu_ref_create(&kFlatOps, g_memB);

  char *line = NULL;
  size_t cap = 0;
  ssize_t len;
  char cmd[32];
  int expect_err = -1, saw_door = 0;

  while ((len = getline(&line, &cap, fp)) > 0) {
    if (line[0] == '\n' || line[0] == '#') continue;
    unsigned a, v;
    if (sscanf(line, "%31s", cmd) != 1) continue;

    if (strcmp(cmd, "IMAGE") == 0) {
      char *hex = strchr(line, ' ');
      if (!hex || sscanf(hex + 1, "%x", &a) != 1) continue;
      char *sp = strchr(hex + 1, ' ');
      if (!sp) continue;
      uint8_t buf[1 << 16];
      uint32_t n = hex2bin(sp + 1, buf, sizeof(buf));
      memcpy(g_memA + (a & (TPU_MEM_BYTES - 1)), buf, n);
      memcpy(g_memB + (a & (TPU_MEM_BYTES - 1)), buf, n);
    } else if (strcmp(cmd, "ACT") == 0) {
      unsigned idx;
      char dath[256], sclh[256];
      if (sscanf(line, "ACT %x %255s %255s", &idx, dath, sclh) != 3) continue;
      uint8_t d[64] = {0}, s[8] = {0};
      hex2bin(dath, d, 64);
      hex2bin(sclh, s, 8);
      for (uint32_t k = 0; k < 8; k++) {
        uint64_t w = 0;
        memcpy(&w, d + k * 8, 8);
        tpu_ref_act_write(A, idx, k, w);
        tpu_ref_act_write(B, idx, k, w);
      }
      uint64_t w = 0;
      memcpy(&w, s, 8);
      tpu_ref_act_write(A, idx, 8, w);
      tpu_ref_act_write(B, idx, 8, w);
    } else if (strcmp(cmd, "TMEM") == 0) {
      if (sscanf(line, "TMEM %x %x", &a, &v) != 2) continue;
      tpu_ref_tmem_write(A, a, v);
      tpu_ref_tmem_write(B, a, v);
    } else if (strcmp(cmd, "HEAD") == 0) {
      if (sscanf(line, "HEAD %x", &a) != 1) continue;
      tpu_emu_mmio_write(TPU_CS_DQ_HEAD, a, 0xF);   // A：MMIO（会触发门铃）
      tpu_ref_csr_write(B, TPU_CS_DQ_HEAD, a);       // B：只写寄存器
    } else if (strcmp(cmd, "DOOR") == 0) {
      saw_door = 1;
      tpu_emu_mmio_write(TPU_CS_CTRL, 1, 0x1);       // A：CTRL.RUN=1
      tpu_ref_doorbell(B);                           // B：直接门铃
    } else if (strcmp(cmd, "EXPECT_ERR") == 0) {
      if (sscanf(line, "EXPECT_ERR %x", &v) == 1) expect_err = (int)v;
    }
  }
  free(line);
  fclose(fp);

  (void)saw_door;
  const char *name = strrchr(path, '/');
  name = name ? name + 1 : path;

  if (expect_err >= 0 && (int)tpu_ref_csr_read(A, TPU_CS_ERROR) != expect_err) {
    printf("FAIL %s 错误码 %02x != 期望 %02x\n", name,
           tpu_ref_csr_read(A, TPU_CS_ERROR), expect_err);
    g_fail = 1;
  }

  compare(A, B, name);
  if (!g_fail) printf("  ok   %s (retire=%d)\n", name, tpu_ref_retire_count(A));

  tpu_ref_destroy(B);
  tpu_emu_free();
}

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "用法: %s <file.stream> [...]\n", argv[0]);
    return 2;
  }
  for (int i = 1; i < argc; i++) run_stream(argv[i]);
  printf("emu selftest: %s (%d 个 stream)\n", g_fail ? "FAIL" : "PASS", argc - 1);
  return g_fail ? 1 : 0;
}
