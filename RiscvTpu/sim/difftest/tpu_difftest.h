//=============================================================================
// tpu_difftest.h -- RiscvTpu 设备级 difftest 公共接口（T2）
//
// 两个消费方共用同一份 C 参考模型：
//   * 独立 difftest 主程序（本目录，DUT = Verilator tpu_sim_top）
//   * NEMU 设备模型（nemu/src/device/tpu.c，REF = NEMU 地址空间）
//
// 提交点是「描述符退休」（DRAIN），不是指令退休。比对分三档：
//   必须相等：CSR 白名单、TMEM 全部、DRAM 全部、退休地址序列
//   忽略：CYCLES / STALL_MEM（与周期相关）
//   归一化：STATUS 的 irq/timeout 位不参与比较
//=============================================================================
#ifndef TPU_DIFFTEST_H
#define TPU_DIFFTEST_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// ---------------------------------------------------------------- 结构参数
#define TPU_D          64
#define TPU_GROUP      16
#define TPU_NGROUP     4
#define TPU_MCOLS      8
#define TPU_ACT_ROWS   64
#define TPU_ACT_BLOCKS 32
#define TPU_ACT_NBLK   2048
#define TPU_TMEM_WORDS 16384
#define TPU_MEM_BYTES  (1u << 20)   // 与 tpu_sim_top 的 MEM_BYTES 一致

// ---------------------------------------------------------------- 描述符
#define TPU_DESC_BYTES 64

// ---------------------------------------------------------------- CSR 偏移
#define TPU_CS_ID        0x000
#define TPU_CS_VERSION   0x004
#define TPU_CS_CTRL      0x008
#define TPU_CS_STATUS    0x00C
#define TPU_CS_DQ_BASE   0x010
#define TPU_CS_DQ_HEAD   0x014
#define TPU_CS_DQ_TAIL   0x018
#define TPU_CS_CYCLES_LO 0x01C
#define TPU_CS_CYCLES_HI 0x020
#define TPU_CS_ICOUNT    0x024
#define TPU_CS_MAC_LO    0x028
#define TPU_CS_MAC_HI    0x02C
#define TPU_CS_STALL_MEM 0x030
#define TPU_CS_RET_INT8  0x034
#define TPU_CS_RET_INT4  0x038
#define TPU_CS_RET_TERN  0x03C
#define TPU_CS_CAPS      0x040
#define TPU_CS_ERROR     0x044
#define TPU_CS_DMA_LO    0x048
#define TPU_CS_DMA_HI    0x04C
#define TPU_CS_DBG_DESC  0x050
#define TPU_CS_DBG_STATE 0x054
#define TPU_CS_DBG_ACC0  0x058
#define TPU_CS_DBG_ACC1  0x05C

// ---------------------------------------------------------------- op / prec
#define TPU_OP_GEMM  0x01
#define TPU_OP_COPY  0x02
#define TPU_OP_QUANT 0x03
#define TPU_OP_BAR   0x04
#define TPU_OP_NOP   0x05
#define TPU_OP_END   0x06

// ---------------------------------------------------------------- 状态
#define TPU_S_IDLE  0
#define TPU_S_FETCH 1
#define TPU_S_CHECK 2
#define TPU_S_DISP  3
#define TPU_S_RUN   4
#define TPU_S_DRAIN 5
#define TPU_S_HALT  6
#define TPU_S_ERR   7

// ---------------------------------------------------------------- 错误码
#define TPU_E_NONE       0x00
#define TPU_E_DESC_ALIGN 0x01
#define TPU_E_DESC_RANGE 0x02
#define TPU_E_DESC_OP    0x03
#define TPU_E_DESC_FLAGS 0x04
#define TPU_E_SHAPE      0x05
#define TPU_E_ADDR_ALIGN 0x06
#define TPU_E_AXI_DECERR 0x07
#define TPU_E_AXI_SLVERR 0x08
#define TPU_E_TIMEOUT    0x09
#define TPU_E_ACC_OVF    0x0A
#define TPU_E_SBUF       0x0B

// ---------------------------------------------------------------- 存储器接口
// 参考模型通过该接口访问「DRAM」。独立模式填 flat 1 MiB 数组；NEMU 模式填
// paddr_read/paddr_write。n 通常为 64（权重/描述符）或 4（scale）。
// write 的 strb 按字节掩码，n <= 64。
typedef struct {
  void (*read)(void *ctx, uint32_t addr, void *buf, uint32_t n);
  void (*write)(void *ctx, uint32_t addr, const void *buf, uint32_t n,
                uint64_t strb);
} tpu_mem_ops;

// ---------------------------------------------------------------- 参考模型
typedef struct tpu_ref tpu_ref_t;

tpu_ref_t *tpu_ref_create(const tpu_mem_ops *ops, void *ctx);
void       tpu_ref_destroy(tpu_ref_t *r);
void       tpu_ref_reset(tpu_ref_t *r);

uint32_t   tpu_ref_csr_read(tpu_ref_t *r, uint32_t off);
void       tpu_ref_csr_write(tpu_ref_t *r, uint32_t off, uint32_t val);

void       tpu_ref_tmem_write(tpu_ref_t *r, uint32_t idx, uint32_t val);
uint32_t   tpu_ref_tmem_read(tpu_ref_t *r, uint32_t idx);
void       tpu_ref_act_write(tpu_ref_t *r, uint32_t idx, uint32_t off,
                             uint64_t data);

// 执行一次门铃（门铃前调用方已写好 DQ_HEAD）。返回退休描述符数。
// 运行终止状态可用 tpu_ref_state() 读取（TPU_S_HALT / TPU_S_ERR）。
int        tpu_ref_doorbell(tpu_ref_t *r);
int        tpu_ref_state(tpu_ref_t *r);
int        tpu_ref_retire_count(tpu_ref_t *r);
uint32_t   tpu_ref_retire_addr(tpu_ref_t *r, int i);

// ---------------------------------------------------------------- DUT 封装
typedef struct tpu_dut tpu_dut_t;

tpu_dut_t *tpu_dut_create(void);
void       tpu_dut_destroy(tpu_dut_t *d);
void       tpu_dut_reset(tpu_dut_t *d);

void       tpu_dut_dram_write(tpu_dut_t *d, uint32_t addr, const uint8_t *buf,
                              uint32_t n);
void       tpu_dut_dram_read(tpu_dut_t *d, uint32_t addr, uint8_t *buf,
                             uint32_t n);
uint32_t   tpu_dut_csr_read(tpu_dut_t *d, uint32_t off);
void       tpu_dut_csr_write(tpu_dut_t *d, uint32_t off, uint32_t val);
void       tpu_dut_tmem_write(tpu_dut_t *d, uint32_t idx, uint32_t val);
uint32_t   tpu_dut_tmem_read(tpu_dut_t *d, uint32_t idx);
void       tpu_dut_act_write(tpu_dut_t *d, uint32_t idx, uint32_t off,
                             uint64_t data);

// 跑到 HALT/ERR 或 max_cycles；把每次 DRAIN 的 cur_addr 依次写入 retire。
// 返回退休描述符数；d->state 可由 tpu_dut_state() 读取。
int        tpu_dut_run(tpu_dut_t *d, int max_cycles, uint32_t *retire,
                       int max_retire);
int        tpu_dut_state(tpu_dut_t *d);

#ifdef __cplusplus
}
#endif

#endif // TPU_DIFFTEST_H
