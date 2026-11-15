//=============================================================================
// tpu_emu_ram_xs.c -- T3a：把模型 DMA 接到香山 emu 的 RAM
//
// emu 的物理内存以 64 位字数组保存（difftest_ram_read/write，索引 = 地址/8）。
// 这里按 8 字节窗口做读改写，保留模型传入的字节掩码。
//
// 编译：随 emu 一起编译时加 -DTPU_EMU_XIANGSHAN（见 docs/08 §9）。
//=============================================================================
#ifdef TPU_EMU_XIANGSHAN

#include <stdint.h>
#include <string.h>

#include "tpu_emu.h"

extern uint64_t difftest_ram_read(uint64_t rIdx);
extern void     difftest_ram_write(uint64_t wIdx, uint64_t wdata, uint64_t wmask);

static void xs_read(void *ctx, uint32_t addr, void *buf, uint32_t n) {
  (void)ctx;
  uint8_t *out = (uint8_t *)buf;
  uint32_t i = 0;
  while (i < n) {
    uint32_t a = addr + i;
    uint32_t off = a & 7u;
    uint32_t k = 8u - off;
    if (k > n - i) k = n - i;
    uint64_t w = difftest_ram_read(a >> 3);
    memcpy(out + i, ((const uint8_t *)&w) + off, k);
    i += k;
  }
}

static void xs_write(void *ctx, uint32_t addr, const void *buf, uint32_t n,
                     uint64_t strb) {
  (void)ctx;
  const uint8_t *in = (const uint8_t *)buf;
  uint32_t i = 0;
  while (i < n) {
    uint32_t a = addr + i;
    uint32_t off = a & 7u;
    uint32_t k = 8u - off;
    if (k > n - i) k = n - i;
    uint64_t mask = 0, val = 0;
    for (uint32_t b = 0; b < k; b++) {
      if (strb & (1ull << (i + b))) {
        mask |= 0xFFull << (8 * (off + b));
        val  |= (uint64_t)in[i + b] << (8 * (off + b));
      }
    }
    if (mask) difftest_ram_write(a >> 3, val, mask);
    i += k;
  }
}

static const tpu_mem_ops xs_ops = { xs_read, xs_write };

void tpu_emu_attach_xiangshan_ram(void) {
  tpu_emu_init(&xs_ops, NULL);
}

#endif // TPU_EMU_XIANGSHAN
