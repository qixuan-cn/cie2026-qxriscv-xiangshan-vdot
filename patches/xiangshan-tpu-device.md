# T3a：把 TPU 行为模型挂进香山 emu（未应用，需重生成 RTL + 重建 emu）

> 本文是 **待执行补丁**。宿主侧适配层已经在仓库内并跑通
> （`scripts/emu_selftest.sh`，28/28）；下面 5 步只改 `build/xs-work/XiangShan`，
> 会重新生成 Verilog 并重建 emu，耗时长且会影响既有 vdot emu 基线，因此没有自动执行。

## 0. 前置

- `build/xs-work/XiangShan` 已打过 `patches/xiangshan-vdot.patch`（vdot 指令可用）
- `build/emu` 可正常跑通现有工作负载（重建前的基线）

## 1. 复制设备模型到 emu

```bash
cd /home/dbqxxs/xsriscv/cieriscv
D=build/xs-work/XiangShan/difftest/src/test/csrc/common/tpu
mkdir -p "$D"
cp RiscvTpu/sim/difftest/tpu_difftest.h  "$D/"
cp RiscvTpu/sim/difftest/tpu_ref_model.c "$D/"
cp RiscvTpu/sim/emu/tpu_emu.h            "$D/"
cp RiscvTpu/sim/emu/tpu_emu.c            "$D/"
cp RiscvTpu/sim/emu/tpu_emu_ram_xs.c     "$D/"
```

## 2. 把 .c 加进 emu 的 C++ 构建

`difftest/Makefile`（`SIM_CXXFILES` 只收 `*.cpp`，需要显式加 `.c`）：

```make
SIM_CXXFILES += $(shell find $(SIM_CSRC_DIR)/tpu -name "*.c")
```

> `tpu_ref_model.c` 用 `g++ -x c++ -std=c++17 -Wall -Wextra` 编译无告警，
> 与 `tpu_emu.c` 同一个 C++ 链接域，不会出现 C/C++ 名字修饰不一致。

## 3. 在 `init_device()` 里挂载

`difftest/src/test/csrc/common/device.cpp`：

```cpp
#include "tpu/tpu_emu.h"      // 新增

void init_device(void) {
#ifdef SHOW_SCREEN
  init_sdl();
#endif
  init_uart();
  init_sd();
  tpu_emu_attach_xiangshan_ram();   // 新增（emu.cpp 中 init_ram 在此之前）
}
```

## 4. 新增 Chisel 设备

新文件 `src/main/scala/device/AXI4TPU.scala`：

```scala
package device

import chisel3._
import chisel3.util._
import chisel3.experimental.{ExtModule, HasExtModuleInline}
import freechips.rocketchip.diplomacy.AddressSet
import org.chipsalliance.cde.config.Parameters

// DPI 转发：Verilog 侧调用 C 的 tpu_emu_mmio_read/write
class TPUMMIO extends ExtModule with HasExtModuleInline {
  val clock = IO(Input(Clock()))
  val raddr = IO(Input(UInt(8.W)))
  val rdata = IO(Output(UInt(32.W)))
  val wen   = IO(Input(Bool()))
  val waddr = IO(Input(UInt(8.W)))
  val wdata = IO(Input(UInt(32.W)))
  val wstrb = IO(Input(UInt(8.W)))

  setInline("TPUMMIO.v",
    s"""
       |import "DPI-C" function int tpu_emu_mmio_read(input int off);
       |import "DPI-C" function void tpu_emu_mmio_write(input int off, input int data, input int strb);
       |
       |module TPUMMIO(
       |  input         clock,
       |  input  [7:0]  raddr,
       |  output [31:0] rdata,
       |  input         wen,
       |  input  [7:0]  waddr,
       |  input  [31:0] wdata,
       |  input  [7:0]  wstrb
       |);
       |  assign rdata = tpu_emu_mmio_read({24'd0, raddr});
       |  always @(posedge clock)
       |    if (wen) tpu_emu_mmio_write({24'd0, waddr}, wdata, {24'd0, wstrb});
       |endmodule
       |""".stripMargin)
}

// 0x100 字节 CSR 窗口；AXI 侧只支持 4 B 访问（软件按 CSR 逐字读写）
class AXI4TPU(address: Seq[AddressSet])(implicit p: Parameters)
  extends AXI4SlaveModule[Bundle](address, executable = false)
{
  override lazy val module = new AXI4SlaveModuleImp[Bundle](this) {
    val dev = Module(new TPUMMIO)
    dev.clock := clock
    dev.raddr := raddr(7, 0)
    dev.wen   := in.w.fire
    dev.waddr := waddr(7, 0)
    dev.wdata := in.w.bits.data(31, 0)
    dev.wstrb := in.w.bits.strb
    in.r.bits.data := Cat(0.U(32.W), dev.rdata)
  }
}
```

## 5. 挂到 SimMMIO 设备树

`src/test/scala/top/SimMMIO.scala`：

```scala
  val tpuRange = AddressSet(0x40003000L, 0xfff)          // 新增：避开 sd/uart/intrGen

  val illegalRange = (onChipPeripheralRanges.values ++ Seq(
    soc.UARTLiteRange,
    flashRange,
    sdRange,
    intrGenRange,
    tpuRange                                             // 新增
  )).foldLeft(...)

  val tpu = LazyModule(new AXI4TPU(Seq(tpuRange)))       // 新增
  tpu.node := axiBus                                     // 新增
```

## 6. 重生成 + 重建 + 验证

```bash
cd build/xs-work/XiangShan
make -j"$(nproc)" emu      # 会先重跑 Chisel 生成 Verilog，再编译 emu
```

验证（驱动侧按 0x4000_3000 访问 CSR）：

1. 裸机程序写 `DQ_BASE`/`DQ_HEAD`，或直接写 `CTRL.RUN=1`；
2. 轮询 `STATUS[1]`（halted）或 `STATUS[2]`（error）；
3. 比对 `ICOUNT/RETIRE_*/DQ_TAIL` 与 NEMU 侧同程序的结果（T2 的 stream 可直接复用作输入）。

## 已知边界

- 单拍 AXI 访问（`len=0`）；CSR 都是 32 位，够用
- 模型同步执行整条描述符链，emu 在这段时间停住（与 RTL 的 `busy` 语义等价，但不是时序精确）
- 地址范围固定 0x100；`TPU_MMIO` 的基址需要与驱动一致
- 未在 emu 内实测；宿主侧只保证 MMIO 适配层与模型 API 逐位一致
