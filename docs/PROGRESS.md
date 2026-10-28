# 项目进度跟踪表（Living Document）

> **维护规则**
> 1. 每次推进后**当天更新**本表，不允许"做完再补"。
> 2. 状态词表固定为：`未开始` / `进行中` / `阻塞` / `完成` / `已取消`，不得使用其它措辞。
> 3. 每个"完成"项必须挂**可验证证据**（日志、波形、报告、commit），无证据不得标完成。
> 4. 阻塞项必须写清"缺什么、谁能解锁、解锁后的下一步"。
> 5. 表尾"变更记录"按日期倒序追加，一行一条。

| 项 | 内容 |
|---|---|
| 项目 | cie2026-qxriscv-xiangshan-vdot |
| 目标平台 | 香山昆明湖 V2（VLEN=128） |
| 团队 | qxriscv（2–3 人） |
| 计划基线 | [ipd/03 开发计划](file:///mnt/hdata/qxriscv/cieriscv/ipd/03_开发计划_WBS与里程碑.md)（D1–D20 / M1–M5） |
| 最后更新 | 2026-10-27 |

---

## 1. 里程碑总览

| 里程碑 | 内容 | IPD 门径 | 计划 | 状态 | 完成度 |
|---|---|---|---|---|---|
| M1 | 环境就绪 + 向量加法波形 | TR3 | D6 | **阻塞** | 20%（分析完成，环境缺失） |
| M2 | vdot 单元 RTL + 单元验证 | TR3 | D10 | **完成** | 100% |
| M3 | 香山内集成 + 汇编级正/负/边界测试 | TR4 | D12 | **阻塞** | 30%（集成规程就绪） |
| M4 | NEMU 扩展 + 协同仿真 | TR4 | D15 | **完成** | 100%（1513/1513，RTL↔NEMU 0 mismatch） |
| M5 | 基线与加速比 + 提交 | TR6/ADCP | D20 | 进行中 | 85%（基线与协同仿真全绿；generic 综合、逻辑深度与加速模型已完成；平台实测与提交待环境） |

## 2. 官方交付要求对照

| 要求 | 交付物 | 状态 | 证据 |
|---|---|---|---|
| 1.1 hello + IP | 运行输出截图/日志 | **阻塞**（无 xs-env） | — |
| 1.2 操作日志 | 环境部署日志 | **阻塞** | — |
| 1.3 vadd 执行过程文档 | 波形 + 分析 | **阻塞** | — |
| 2.1 设计文档 | 设计文档 | **完成** | [docs/phase2/design.md](file:///mnt/hdata/qxriscv/cieriscv/docs/phase2/design.md) |
| 2.2 RTL 实现 | Verilog/Chisel | **完成**（模块级已验证） | [VDot.v](file:///mnt/hdata/qxriscv/cieriscv/rtl/verilog/VDot.v) |
| 2.3.1 正数测试 | 用例 + 结果 | **完成**（单元级） | [测试报告 §4.1](file:///mnt/hdata/qxriscv/cieriscv/docs/phase2/test_report.md) |
| 2.3.2 负数测试 | 用例 + 结果 | **完成**（单元级） | 同上 |
| 2.3.3 边界测试 | 用例 + 结果 | **完成**（单元级） | 同上 §4.2 |
| 3.1.1 NEMU 扩展 | 源码改动 | **完成** | [nemu-vdot.patch](file:///mnt/hdata/qxriscv/cieriscv/patches/nemu-vdot.patch) |
| 3.1.2 协同仿真 | NEMU vs RTL 比对 | **完成** | [01_nemu_cosim.log](file:///mnt/hdata/qxriscv/cieriscv/docs/phase3/01_nemu_cosim.log)：1513/1513，0 mismatch |
| 3.2 基线实现 | 组合指令程序 | **完成**（NEMU 上跑通） | [run_nemu_baseline.py](file:///mnt/hdata/qxriscv/cieriscv/tests/run_nemu_baseline.py)：1513/1513 |
| 3.3 加速分析 | 分析报告 | 完成（模型口径）：数据通路 6→1、模型延迟 12→3 cycle（4.0×）、吞吐 6.0×；平台实测待环境 | [release_report.md](file:///mnt/hdata/qxriscv/cieriscv/docs/phase4/release_report.md) §5 |

## 3. 详细任务表（当前迭代）

| ID | 任务 | 交付物 | 状态 | 依赖 | 证据 |
|---|---|---|---|---|---|
| T2.1 | vdot 语义与位宽裁决 | 规格书 §2 | 完成 | — | `ipd/01` |
| T2.2 | 模块级 RTL | `rtl/verilog/VDot.v` | 完成 | T2.1 | 1518/1518 PASS |
| T2.3 | 参考模型（Python/C） | `tests/ref_vdot.py`、`model/vdot_ref.c` | 完成 | T2.1 | `logs/06_*` |
| T2.4 | 单元测试台与向量 | `tb_vdot.cpp`、1518 条向量 | 完成 | T2.2/T2.3 | `logs/03_*`、VCD |
| T2.5 | 阶段二报告与设计文档 | `docs/phase2/*` | 完成 | T2.4 | — |
| T3.1 | NEMU 中新增 `vdot.vv` 译码与执行 | NEMU 源码改动 | 完成 | NEMU 源码 | `patches/nemu-vdot.patch`（3 文件 +44 行） |
| T3.2 | 最小 RISC-V 测试程序（手写机器码） | `tests/as/*.py` + 二进制 | 完成 | — | `tests/as/mini_asm.py`；1513 用例镜像 48720 B |
| T3.3 | NEMU 单指令自测（vdot 结果正确） | 运行日志 | 完成 | T3.1/T3.2 | `docs/phase3/01_nemu_cosim.log`：1513/1513 PASS |
| T3.4 | RTL ↔ NEMU 协同仿真（同向量集比对） | 比对日志 + 报告 | 完成 | T3.3 | 1513 vs 1513，0 mismatch（`02_rtl_export.txt`） |
| T3.5 | 基线汇编实现（RVV 组合） | 汇编程序 | 完成 | T3.2 | `tests/run_nemu_baseline.py`：1513/1513，6→1 数据通路指令 |
| T3.6 | 延迟/吞吐实测（rdcycle） | 测量日志 | 完成（单元级实测） | 需香山或 NEMU 计时 | `04_rtl_unit_perf.log`：3 cycle / 1.0 inst/cycle；NEMU 动态指令 78701 vs 89294 |
| T3.7 | 阶段三报告（实测替换估算） | `docs/phase3/*` | 完成 | T3.4/T3.6 | [cosim_report.md](file:///mnt/hdata/qxriscv/cieriscv/docs/phase3/cosim_report.md) |
| T3.8 | 香山内集成（VFU） | 香山源码改动 | 阻塞 | xs-env + 工具链 | 集成规程已就绪 |
| T3.9 | 阶段一（环境/hello/vadd 波形） | 日志 + 波形 + 文档 | 阻塞 | xs-env | — |
| T4.1 | RTL 死逻辑清理与严格 lint 门禁 | `VDot.v` 修订 | 完成 | T2.2 | `02_verilator_lint.log`：4 告警 → **0** |
| T4.2 | generic 综合与逻辑深度评估 | 综合日志 | 完成 | T4.1 | `01_yosys_synth.log`：4186 单元 / 172 FF / 最长路径 25 级 |
| T4.3 | 面积估算（GE 口径） | 估算表 | 完成 | T4.2 | `release_report.md` §3.2：≈5.3k–8.4k GE |
| T4.4 | 加速模型（延迟/吞吐）与答辩口径 | 模型表 | 完成 | T4.1 | `release_report.md` §5：4.0× 延迟 / 6.0× 吞吐（模型） |
| T4.5 | 清理后全量回归（单元/协同/基线） | 回归日志 | 完成 | T4.1 | 1518/1518；1513/1513，0 mismatch；基线 6.0× |
| T4.6 | 一键回归脚本（单元+lint+协同+基线） | `scripts/run_all.sh` | 完成 | T4.5 | 本机 `ALL : PASS`（6 步全绿） |
| T4.7 | 文档一致性同步（行号/信号/面积口径） | `design.md` v1.1、`perf_analysis.md` §5、`ipd/01` Q5 | 完成 | T4.1–T4.4 | 清除 `s1_valid/s2_valid`、`sum_c` 旧引用；面积改为 generic 综合口径 |

## 4. 阻塞与风险

| # | 阻塞项 | 缺什么 | 解锁条件 | 解锁后下一步 |
|---|---|---|---|---|
| B1 | 阶段一全部内容 | `xs-env`、RISC-V 工具链、≥32 GB 内存 | 提供环境或允许安装 | 跑 hello、采 vadd 波形 |
| B2 | 香山内集成与回归 | 香山源码仓库 | B1 解锁 | 按 `patches/xiangshan-vdot-integration.md` 执行 I1–I7 |
| B3 | 真实加速比 | 香山/NEMU 计时环境 | B1 或 NEMU 可运行 | 按 `perf_analysis.md` §6 协议实测 |
| B4 | PDK 面积/时序签核 | 工艺库 + DC/Genus | 环境允许 | **部分关闭**：generic 规模/深度已取得（`docs/phase4/01_yosys_synth.log`）；µm² 与 ns 仍待做 |

> **表述约束（长期有效）**：在 B1/B2/B3 关闭前，任何材料不得宣称"已在香山平台验证"或给出"实测加速比"。

## 5. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-10-28 | 阶段四文档一致性收口（T4.7）：`docs/phase2/design.md` v1.1 对齐清理后的 RTL 行号/信号与综合结果；`docs/phase3/perf_analysis.md` §5 面积改为 generic 综合口径；`docs/phase2/test_report.md` U-05 与 `ipd/01` Q5 标注"部分关闭"；`scripts/run_all.sh` 6 步一键回归本机全绿 |
| 2026-10-28 | 阶段四完成（T4.1–T4.5）：RTL 死逻辑清理（`sum_c` 加法树、`s1_valid`/`s2_valid`）+ 严格 lint 告警 4→0；yosys generic 综合 4186 单元 / 172 FF / 最长 R2R 路径 25 级，估算 ≈5.3k–8.4k GE；补加速模型（延迟 12→3 cycle，4.0×；吞吐 6.0×）；清理后全量回归 1518/1518、1513/1513 0 mismatch、基线 6.0× 全绿；B4 部分关闭，报告 `docs/phase4/release_report.md` |
| 2026-10-27 | 阶段三文档一致性收口：README 阶段三状态/证据链/仓库结构/复现命令/版本锁定（NEMU `a683e0b`）同步；`vdot` 编码由 `funct6=100000`（建议值）定值为 `111111`，回写至 `ipd/01` §2.2、`docs/phase2/design.md` §2.1、`patches/xiangshan-vdot-integration.md` I1，O2 关闭 |
| 2026-10-27 | 阶段三 T3.1–T3.7 全部完成：NEMU `vdot` 实现 + 协同仿真 1513/1513（RTL↔NEMU 0 mismatch）+ RVV 组合基线跑通（6→1 数据通路指令）+ 单元级实测 3 cycle/1.0 inst/cycle；M4 关闭，报告 `docs/phase3/cosim_report.md` |
| 2026-10-27 | 建立本表；阶段二（M2）标记完成，证据为 1518/1518 PASS + 3 cycle/1 inst/cycle；启动阶段三（M4/M5） |

