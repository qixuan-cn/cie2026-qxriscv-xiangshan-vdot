//=============================================================================
// tpu_emu.h -- T3a：把 tpu_ref_model.c 挂进香山 emu 的宿主侧设备层
//
// 与 NEMU 插件（.tools/nemu/src/device/tpu.c）的区别只在于内存后端：
//   NEMU      -> guest_to_host()
//   XiangShan -> difftest_ram_read/write()（见 tpu_emu_ram_xs.c）
// 描述符链的语义、CSR 偏移、门铃条件完全复用 tpu_ref_model.c。
//=============================================================================
#ifndef TPU_EMU_H
#define TPU_EMU_H

#include <stdint.h>

#include "tpu_difftest.h"

#ifdef __cplusplus
extern "C" {
#endif

// 创建/复位模型；ops 为 DMA 使用的内存后端（见 tpu_emu_ram_xs.c），
// ctx 原样传给 ops 回调（emu 用不到，宿主自检用扁平内存）。
void tpu_emu_init(const tpu_mem_ops *ops, void *ctx);
void tpu_emu_reset(void);
void tpu_emu_free(void);

// 0x100 字节 MMIO 窗口的读写；off 为窗口内字节偏移。
// strb 为按字节的写掩码（bit i 对应 data 的第 i 个字节）。
uint32_t tpu_emu_mmio_read(uint32_t off);
void     tpu_emu_mmio_write(uint32_t off, uint32_t data, uint32_t strb);

// 调试/自检用
tpu_ref_t *tpu_emu_model(void);

// 挂到香山 emu 的 RAM（tpu_emu_ram_xs.c，需 -DTPU_EMU_XIANGSHAN 编译）
void tpu_emu_attach_xiangshan_ram(void);

#ifdef __cplusplus
}
#endif

#endif // TPU_EMU_H
