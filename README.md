# cie2026-qxriscv-xiangshan-vdot

2026CIE 全国 RISC-V 高水平创新及应用大赛参赛作品：面向边缘 AI 推理的**香山昆明湖 V2 自定义向量点积指令 `vdot.vv`**。

- 目标平台：香山（XiangShan）昆明湖 V2
- 指令约束：`SEW = 8`，`VLEN = 128`，分组长度 `K = 8`
- 交付范围：环境验证 → 指令语义与 RTL 实现 → NEMU 协同仿真与性能分析

## 项目状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| 阶段一 | 环境部署、`hello` 输出、vadd 波形分析 | **阻塞**：本机无 `xs-env` / RISC-V 工具链 |
| 阶段二 | vdot.vv 设计文档、RTL、正/负/边界测试 | **设计 + RTL + 单元级验证完成**（1518/1518 PASS）；香山内集成待环境 |
| 阶段三 | NEMU 扩展、协同仿真、基线对比与加速分析 | **NEMU 扩展 + 协同仿真完成**（1513/1513，RTL↔NEMU 0 mismatch）；单元级实测 3 cycle / 1.0 inst/cycle；平台实测待环境 |

### 已完成并验证（模块级 RTL 仿真 + NEMU 协同仿真）

| 项 | 结果 | 证据 |
|---|---|---|
| 用例通过率 | **1518 / 1518**（含正数、负数、8 类边界、配置异常、1500 随机） | [测试报告](docs/phase2/test_report.md) |
| 背靠背流式 | 1024 / 1024，按序比对 | 同上 |
| 延迟 / 吞吐 | 3 cycle / 1.0 inst/cycle | 同上 |
| 三方模型一致性 | Python / C / C++ 内联，1518 条一致 | `docs/phase2/logs/06_c_model_crosscheck.log` |
| 基线（RVV 组合）功能等价 | 1513 / 1513 | `docs/phase3/00_baseline_equiv.log` |
| NEMU 内 `vdot.vv` 语义 | **1513 / 1513**，0 fail | [01_nemu_cosim.log](docs/phase3/01_nemu_cosim.log) |
| RTL ↔ NEMU 协同仿真 | **1513 vs 1513，mismatch 0** | [02_rtl_export.txt](docs/phase3/02_rtl_export.txt) |
| 基线在 NEMU 上跑通 | **1513 / 1513**，6→1 数据通路指令 | [03_baseline_nemu.log](docs/phase3/03_baseline_nemu.log) |
| 单元级时序实测 | 3 cycle / 1.0 inst·cycle⁻¹（1024 条背靠背，10113 cycles） | [04_rtl_unit_perf.log](docs/phase3/04_rtl_unit_perf.log) |
| 波形 | `docs/phase2/waveform/vdot_unit.vcd` | — |

> **表述约束**：以上均为**功能单元模块级**仿真与 **NEMU 指令集模拟器**结果。香山昆明湖 V2 内集成与真实加速比**尚未执行**，不得表述为"已在香山平台验证"或给出实测加速比。

## 仓库结构

```
.
├── README.md
├── doc/                 # 赛题官方材料（提交要求、赛题资料、提交流程指南）
├── ipd/                 # IPD 流程文档集（方案、规格、测试方案、计划、对照表）
├── docs/                # 阶段交付文档与证据
│   ├── PROGRESS.md      # 任务进度跟踪表（Living Document，持续更新）
│   ├── phase2/          # 设计文档、测试报告、日志、波形
│   └── phase3/          # 协同仿真报告、基线日志、单元级性能日志（含估算口径声明）
├── rtl/
│   ├── verilog/VDot.v   # 已验证的模块级 RTL（SystemVerilog 语法）
│   └── chisel/VDot.scala# 香山集成用的 Chisel 骨架（未编译校验）
├── patches/
│   ├── nemu-vdot.patch  # NEMU 扩展补丁（3 文件，+44 行）
│   └── xiangshan-vdot-integration.md  # 香山集成说明（规程，未执行）
├── tests/
│   ├── ref_vdot.py      # Python golden model
│   ├── ref_baseline.py  # 基线指令序列的语义等价模型
│   ├── model/vdot_ref.c # C 参考模型（NEMU-ready）
│   ├── as/mini_asm.py   # 手写 RISC-V/RVV 汇编器（无工具链环境下的镜像生成）
│   ├── run_nemu_cosim.py    # NEMU 自测 + 协同仿真 + RTL 结果导出
│   ├── run_nemu_baseline.py # RVV 组合基线在 NEMU 上跑通并计数
│   ├── gen_vectors.py   # 向量生成（定向 + 随机 + 边界密集）
│   ├── tb/tb_vdot.cpp   # Verilator C++ 测试台
│   └── vectors/         # 1518 条测试向量
└── scripts/             # 一键构建与回归
```

## 方案与交付文档

| 文档 | 内容 |
|---|---|
| [项目章程与总体方案](ipd/00_项目章程与总体方案.md) | 赛题解读、目标与范围、IPD 阶段与门径、角色分工、风险登记册 |
| [vdot 指令与微架构设计规格书](ipd/01_vdot指令与微架构设计规格书.md) | 精确语义、位宽推导、数据通路、流水时序、香山集成点、ADR |
| [测试与验证方案](ipd/02_测试与验证方案.md) | 分层验证策略、用例矩阵、协同仿真、性能测量方法学 |
| [开发计划、WBS 与里程碑](ipd/03_开发计划_WBS与里程碑.md) | 4 周排期、WBS、门禁、降级预案 |
| [评审对照与提交检查表](ipd/04_评审对照与提交检查表.md) | 官方要求 → 交付物映射、提交清单、录屏与 PPT 脚本 |
| [阶段二设计文档](docs/phase2/design.md) | 提交版：指令语义、微架构、数据通路、RTL 结构、追溯 |
| [阶段二测试报告](docs/phase2/test_report.md) | 1518 条用例结果、时序实测、断言、缺陷记录、未验证项 |
| [香山集成说明](patches/xiangshan-vdot-integration.md) | 集成规程与核对表（**未执行**） |
| [NEMU 扩展补丁](patches/nemu-vdot.patch) | 阶段三提交版：`vdot.vv` 译码 + 执行语义（3 文件，+44 行） |
| [阶段三协同仿真报告](docs/phase3/cosim_report.md) | NEMU 扩展改动、1513 条协同仿真、基线对比、单元级实测与缺陷记录 |
| [阶段三性能分析](docs/phase3/perf_analysis.md) | 基线定义与等价性、延迟/吞吐估算、平台实测协议（**估算部分非实测**） |
| [任务进度跟踪表](docs/PROGRESS.md) | 里程碑、官方交付对照、详细任务、阻塞项与变更记录（持续更新） |

## 关键设计结论（摘要）

1. `vdot.vv` 对两个源寄存器的低 64 bit 内 8 个 int8 做有符号点积，结果写入 `vd[31:0]`，`vd[127:32]` 清零。
2. 8 项和的范围为 `[-130048, +131072]`，**必须 ≥19 bit 有符号累加**；内部取 20 bit，输出 32 bit。
3. 微架构为新增单 uop、无状态、全流水的 `VDot` 功能单元，接入香山既有向量发射/写回框架，不新增流水线。

## 复现

```bash
# 一键回归：Python 模型自检 -> 生成向量 -> C 模型交叉比对 -> 构建 -> RTL 仿真 -> 归档日志与波形
bash scripts/run_unit_tests.sh
```

依赖：`python3`、`gcc`、Verilator（脚本依次探测 `verilator-inst/`、`.tools/verilator/`、系统 PATH）。

预期输出：

```
cases_total      : 1518
cases_pass       : 1518
cases_fail       : 0
latency_write    : 3 cycle
throughput       : 1 inst/cycle
RESULT           : PASS
```

### 阶段三：NEMU 协同仿真

```bash
# 1) 应用扩展补丁并构建 NEMU（首次或补丁变更后）
git -C .tools/nemu apply ../../patches/nemu-vdot.patch   # 已在源码树中时跳过
make -C .tools/nemu -j"$(nproc)" NEMU_HOME="$(cd .tools/nemu && pwd)" LIBRARY_PATH=/tmp/sdllib

# 2) NEMU 自测 + 导出 1513 条写回结果（喂给 tb_vdot）
python3 tests/run_nemu_cosim.py --rtl-export docs/phase3/02_rtl_export.txt

# 3) RTL 侧跑同一顺序的用例并导出结果
bash scripts/build_sim.sh
./build/obj_dir/tb_vdot build/nemu/cosim_vectors.txt - docs/phase3/02_rtl_export.txt

# 4) RVV 组合基线在 NEMU 上跑通并计数
python3 tests/run_nemu_baseline.py
```

预期输出：`cases_fail 0`、`rtl_vs_nemu : lines=1513/nemu=1513 mismatch=0`、`alu_op_ratio : 6.0x`，均以 `RESULT : PASS` 结尾。

> `tests/as/mini_asm.py` 是本项目自带的最小 RISC-V/RVV 汇编器，用于在无 RISC-V 工具链的环境中生成平面镜像。

## 版本锁定

| 组件 | 版本 / commit |
|---|---|
| xs-env | 待记录（阶段一，环境就绪后补） |
| XiangShan | 待记录（阶段一） |
| NEMU | `a683e0b`（XiangShan 版源码）+ `patches/nemu-vdot.patch` |
| Verilator | 5.034（本机源码构建，`--assert --trace`） |
| g++ | Debian 14.2.0 |
| Python | 3.13.5 |

## 许可与引用

本项目基于开源香山（XiangShan）与 NEMU 进行扩展，相关代码遵循上游许可；新增代码仅用于参赛作品。
