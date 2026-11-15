# RiscvTpu —— 基于 RISC-V / 香山的 tile 级 TPU

本目录是 RiscvTpu 项目的设计基线与 V1 实现（RTL 已落地并通过回归）。目标是在**不发明私有指令集**的前提下，用 RISC-V 向量扩展 + 三条已落地的 `vdot` 扩展指令承载"矩阵"这一层算力，形成一个从标量核到 tile 引擎的**三层计算塔**。

设计输入：

- 本项目已完成的 `vdot` / `vdot4x2` / `vtdot` FU（香山昆明湖 V2、VFEX0，3 级流水、无状态、MAC/cycle = 8 : 16 : 16），综合实测 7,427 通用单元 / 356 FF / 25 级。
- `openTPU-main` 的工程经验（精确整数 + 每输出一次 fp32 epilogue、权重广播的脉动 MXU、CSR 化观测性、单元级面积/fmax 方法）。分析见 `../openTPU-main/docs/vdot-integration/`。

## 文档

| 文档 | 内容 | 读者 |
|---|---|---|
| [01_可行性分析.md](docs/01_可行性分析.md) | 需求、技术/数值/面积/带宽/工具链可行性、风险表、与 openTPU 的取舍、Go/No-Go 结论 | 决策者、评审 |
| [02_架构设计规格.md](docs/02_架构设计规格.md) | 三层计算塔、模块划分与接口、存储层次与带宽预算、MXU 微架构、香山集成方案三选一、面积/时序预算 | 架构、RTL |
| [03_功能设计规格.md](docs/03_功能设计规格.md) | 指令与 CSR 契约、64 B 描述符格式、各单元精确行为、状态机、错误模型、时序契约、性能计数器 | RTL、验证、软件 |
| [04_项目开发计划.md](docs/04_项目开发计划.md) | M0–M5 阶段、每阶段交付物与退出准则、依赖图、风险缓解、评审门 | 项目管理 |
| [05_测试方案与测试用例.md](docs/05_测试方案与测试用例.md) | 五级测试体系、黄金模型与 difftest、覆盖率模型、40+ 具体用例、回归门禁 | 验证 |
| [06_实现说明与验证报告.md](docs/06_实现说明与验证报告.md) | V1 RTL 的模块映射、数值契约落地、15 条实现偏差、复现命令与实测结果（41/41 用例、49.5 万条 fp32 对拍） | RTL、验证、项目管理 |
| [07_偏差修复计划.md](docs/07_偏差修复计划.md) | D1~D15 的修复方案、改动点、验收标准、V1.1/V1.2/V1.3/V2 批次与门禁、待澄清规格项 | 架构、RTL、项目管理 |
| [08_验证工具链与difftest方案.md](docs/08_验证工具链与difftest方案.md) | 本地工具实测盘点、香山 difftest/NEMU/gem5 方法论的迁移评分、T0~T4 四层 difftest 方案、落地路线 | 验证、架构 |

## 一句话定位

> **openTPU 证明了"用私有宏指令做 TPU"能跑通；RiscvTpu 要证明"用 RISC-V 就能做 TPU"。**

## 与已有资产的关系

| 已有 | 在新项目中的角色 |
|---|---|
| `rtl/verilog/VDot.v`（等价模型） | L1 向量层点积数据通路，直接复用 |
| `rtl/chisel/VDot.scala`（香山 FU 包装） | L1 集成基线，不再改动 |
| `tests/ref_vdot.py` | L0/L1 黄金模型，扩展为 tile 级黄金模型 |
| 3,047 单元用例 / 55 覆盖率点 / 3,032 协同用例 | 作为 L1 回归的起点，不平移、不重写 |
| `openTPU-main/docs/vdot-integration/02` 的语义映射 | tile 引擎整数域的外部对照实现 |

## 目录（V1 实现已落地）

```
RiscvTpu/
├── README.md                 本文件
├── docs/                     设计基线 + 实现报告
│   ├── 01_可行性分析.md
│   ├── 02_架构设计规格.md
│   ├── 03_功能设计规格.md
│   ├── 04_项目开发计划.md
│   ├── 05_测试方案与测试用例.md
│   ├── 06_实现说明与验证报告.md
│   ├── 07_偏差修复计划.md
│   └── 08_验证工具链与difftest方案.md
├── rtl/                      tpu_pkg / tpu_top / tpu_mxu / tpu_tmem / tpu_act /
│                             tpu_quant / tpu_copy / tpu_csr
├── sim/                      存储器模型、仿真顶层、fp32/tile testbench；
│                             difftest/（设备级 difftest 与 C 参考模型）、emu/（T3a 适配层）
├── tests/                    ref_tpu.py 黄金模型 + run_tpu_tests.py 用例生成与对拍；
│                             difftest/gen_desc_stream.py 描述符流生成
├── scripts/                  lint.sh（Verilator -Wall）、regress.sh（数值 + 端到端）、
│                             difftest.sh（T2/T4 设备级 difftest）、emu_selftest.sh（T3a 适配层）
└── sw/                       M4 起：intrinsics 封装、tile 库、描述符生成器
```

## 快速验证

```bash
cd RiscvTpu
scripts/lint.sh       # Verilator -Wall 零告警
scripts/regress.sh    # fp32 495k 条逐位对拍 + 41 个 tile 用例
scripts/difftest.sh   # 设备级 difftest：28 个描述符流，DUT(RTL) vs REF(C 模型)
RD_LATENCY=2 scripts/difftest.sh   # T4：DRAM 读延迟 2 拍，功能仍须 28/28
scripts/emu_selftest.sh            # T3a：emu MMIO 适配层回归
```

结果：`FP32: 495448 checks, 0 failures`、`41/41 通过`、difftest `28/28`（详见 06/08 文）。

## 已定决策

| # | 决策 | 落点 |
|---|---|---|
| Q1 | **量化 scale 行兼容 openTPU 格式**：权重行用 `WF=0`/`WF=1` 布局，ACT RAM 每 `(行,block)` 8 字节 `Sa + 4×da`，分组 `D/4 = 16` | 03 文 §6.5；可直接加载 openTPU `quant.py` 产物对拍（05 文 XCH 组） |
| Q2 | **MXU 输出落 TMEM**：`GEMM` 的 `C_addr` 为 TMEM 字地址，回 DRAM 由后续 `COPY` 完成 | 03 文 §6.2；02 文 §11 |
| Q3 | 多 slice `BAR` 语义 | 未决，V2 |
| Q4 | `CONV1D` 描述符 | 未决，V2 |

## 状态

- V1 的 RTL、黄金模型、仿真设施与回归脚本已落地并跑通：**41/41 tile 用例通过，49.5 万条 fp32 数值对拍 0 失败**，Verilator `-Wall` 零告警。
- 面积、频率、吞吐数字仍为**预算或目标值**，待门级综合确认；实现与规格的 15 条偏差见 [06 文](docs/06_实现说明与验证报告.md) §4。
- 未提交 git；`vdot` FU 与香山补丁保持现状。
