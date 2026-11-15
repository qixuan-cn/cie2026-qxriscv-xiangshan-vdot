# RiscvTpu 待办与交接（2026-11-15）

## 需要决策

1. **是否执行 T3a 的 emu 重建**
   - 内容：按 [patches/xiangshan-tpu-device.md](../patches/xiangshan-tpu-device.md) 在 `build/xs-work/XiangShan`
     新增 `AXI4TPU.scala`、挂到 `SimMMIO`、`init_device()` 里 `tpu_emu_attach_xiangshan_ram()`，然后重生成
     Verilog 并重建 emu。
   - 影响：耗时长；会改动外部香山树，可能影响现有 vdot emu 基线。
   - 现状：宿主侧适配层已跑通（`scripts/emu_selftest.sh` 28/28），只差 emu 内挂载与裸机驱动验证。

## 待办（按优先级）

| # | 事项 | 入口 | 状态 |
|---|---|---|---|
| 1 | T3a：emu 内挂载 TPU 设备并跑裸机驱动 | `patches/xiangshan-tpu-device.md` | 待决策后执行 |
| 2 | NEMU：启用 `CONFIG_TPU_DEV=y` 完整构建并验证插件链接（当前只做 `-fsyntax-only`） | `.tools/nemu` + `patches/nemu-tpu-device.patch` | 未做 |
| 3 | T3b：接 RTL 整机 difftest | 依赖 07 文 D8/D9 的 AXI-Lite/AXI master 壳 | 阻塞 |
| 4 | D3（T1b）：程序级 FU difftest（连续 vdot 程序） | `tests/difftest/fu/`（未创建） | 未开始 |
| 5 | T4 可选：bank 冲突 / 双通道 DRAM 建模，用 `STALL_MEM` 出带宽与利用率结论 | `sim/tpu_mem_model.sv` | 未开始 |

## 回归基线（改动前必须全绿）

```bash
cd RiscvTpu
scripts/lint.sh                    # Verilator -Wall 零告警
scripts/regress.sh                 # fp32 495448 checks / 41 个 tile 用例
scripts/difftest.sh                # 28/28，stall=0
RD_LATENCY=2 scripts/difftest.sh   # 28/28，stall>0
scripts/emu_selftest.sh            # 28/28（MMIO 适配层）
```

## 本次已完成（备查）

- T2：设备级 difftest（`tpu_top` vs `tpu_ref_model.c`）、28 个描述符流、NEMU 插件与 patch
- T4：`RD_LATENCY` 读延迟模型 + FETCH/MXU/COPY 等待状态 + 真实 `STALL_MEM` 计数；默认 0 时逐周期不变
- T3a：emu 宿主侧 MMIO 适配层与 RAM 后端，自检 28/28；Chisel 设备补丁待执行
- 文档：08 文 §5/§7 路线图、05 文 §3.2、README 目录树与快速验证
