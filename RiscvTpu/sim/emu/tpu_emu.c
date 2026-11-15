//=============================================================================
// tpu_emu.c -- T3a：emu 宿主侧 MMIO 适配层
//
// 只做三件事：
//   1. 把 MMIO 窗口内的按字节写合成为 32 位寄存器写；
//   2. 写 DQ_HEAD / CTRL.RUN=1 时触发门铃（与 RTL 的 start pulse 一致）；
//   3. 读时直接返回 tpu_ref_csr_read()。
// 真正的设备行为在 tpu_ref_model.c，与 T2 的 REF 是同一份代码。
//=============================================================================
#include <string.h>

#include "tpu_emu.h"

static tpu_ref_t           *g_ref;
static uint32_t             g_csr[0x100 / 4];
static const tpu_mem_ops   *g_ops;
static void                *g_ctx;

void tpu_emu_init(const tpu_mem_ops *ops, void *ctx) {
  tpu_emu_free();
  g_ops = ops;
  g_ctx = ctx;
  memset(g_csr, 0, sizeof(g_csr));
  g_ref = tpu_ref_create(ops, ctx);
}

void tpu_emu_reset(void) {
  tpu_emu_init(g_ops, g_ctx);
}

void tpu_emu_free(void) {
  if (g_ref) {
    tpu_ref_destroy(g_ref);
    g_ref = NULL;
  }
}

uint32_t tpu_emu_mmio_read(uint32_t off) {
  if (!g_ref) return 0;
  return tpu_ref_csr_read(g_ref, off);
}

void tpu_emu_mmio_write(uint32_t off, uint32_t data, uint32_t strb) {
  if (!g_ref) return;
  uint32_t idx = (off >> 2) & 0x3Fu;
  uint32_t val = g_csr[idx];
  for (int b = 0; b < 4; b++)
    if (strb & (1u << b))
      val = (val & ~(0xFFu << (8 * b))) | (data & (0xFFu << (8 * b)));
  g_csr[idx] = val;

  tpu_ref_csr_write(g_ref, off, val);
  // RTL：写 DQ_HEAD 或 CTRL[0] 都会拉高 start pulse
  if (off == TPU_CS_DQ_HEAD || (off == TPU_CS_CTRL && (val & 1u)))
    tpu_ref_doorbell(g_ref);
}

tpu_ref_t *tpu_emu_model(void) {
  return g_ref;
}
