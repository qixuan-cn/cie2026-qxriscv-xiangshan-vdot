# 08 · 验证工具链与 difftest 方案

> 版本：V1 · 状态：方案（2026-11-15）
> 回答两个问题：**RiscvTpu 能用哪些仿真工具**、**能不能照搬香山的 difftest / NEMU / gem5 方法论**。
> 工具清单基于本机实测（§2），不是"业界通用推荐"；凡未装的一律标为缺口并给替代。
> 与 [05 测试方案](05_测试方案与测试用例.md) 的关系：05 定义"测什么"，本文定义"用什么跑、怎么自动对拍"，
> 与 [07 偏差修复计划](07_偏差修复计划.md) 的批次对齐关系见 §7。

---

## 0. 结论先行

| # | 结论 | 依据 |
|---|---|---|
| C1 | **Verilator 是唯一主仿真器，且已经够用**，V1 无需商用仿真器 | 5.034 已支持 `--timing`/`--coverage`/`--savable`，当前 41 个用例 + 49.5 万条 fp32 对拍全在其上跑通 |
| C2 | **NEMU 可以直接复用为 ISS**，不需要重新移植参考模型 | `.tools/nemu` 已打 `vdot` 补丁、已构建 `riscv64-nemu-interpreter(-so)`，且**树内已含 AME 矩阵扩展**（`src/isa/riscv64/instr/ame/`）——"把 tile 寄存器文件建模进 ISS"在本树里有先例 |
| C3 | **difftest 方法论可以照搬，但提交点必须从"指令退休"降级为"描述符退休"** | TPU 是 AXI 从设备，不退休指令；香山的 `difftest_step(pc, npc)` 无法直接用，需自定义提交协议（§5.2） |
| C4 | **gem5 / XS-GEM5 在 V1 不建议引入** | 它做性能不做位精确；TPU 需另写 SimObject，成本高于收益。性能改用 CSR 计数器 + 解析模型（§4 T4） |
| C5 | **真正缺的不是仿真器，而是"设备级对拍胶水"** | 缺 `sim/difftest/*` + NEMU 侧 `src/device/tpu.c` 两块，规模 M（§5） |
| C6 | **spike / cocotb / sby 均为"可选且当前不推荐"** | spike 的 RVV 与 vdot 需再打补丁（与 NEMU 重复）；cocotb 会引入第二套 TB 设施；sby 的运行时断言已由 Verilator `--assert` 覆盖 |

一句话：**照搬香山的"层次"（指令级 ISS + 提交级比对），不照搬它的"器件"（全芯片 emu、gem5）**。

---

## 1. 验证对象分层

RiscvTpu 不是一个模块，而是四层计算塔。工具必须按层选，否则会出现"用系统级手段测一个乘法器"的浪费。

| 层 | 验证对象 | 真值来源 | 主工具 | 当前状态 |
|---|---|---|---|---|
| **V0 数值原语** | `tpu_pkg` 的 `f_mul`/`f_add`/`f_i2f48`/`f_scale_pow2` | C++ IEEE-754 参考（`sim/tb_fp32.cpp`） | Verilator + `tb_fp32` | ✅ 495,448 条，0 失败 |
| **V1 指令** | `vdot`/`vdot4x2`/`vtdot` FU（3 级流水） | `tests/ref_vdot.py` + NEMU | Verilator + NEMU `-so` | ✅ 3,047 单元 + 3,032 协同用例 |
| **V2 单元** | `tpu_mxu` / `tpu_tmem` / `tpu_act` / `tpu_quant` / `tpu_copy` | `tests/ref_tpu.py` 的对应函数 | Verilator（out-of-context） | ⚠️ 仅有整机 `tpu_top` 用例，无单元级 OOC TB |
| **V3 设备** | `tpu_top` + 存储器模型（描述符流、CSR、错误码） | `tests/ref_tpu.py` 全套 | Verilator + `tb_tpu.cpp` | ✅ 41/41 用例 |
| **V4 系统** | 香山核 + TPU + DDR 的整机 | NEMU 整机 / 香山 emu | emu / 自研 SoC TB | ❌ 未开始（依赖 07 文 D8/D9） |
| **V5 应用** | tile 库 + 驱动 + 微基准 | 数值正确性 + 计数器 | V4 的环境 | ❌ 未开始 |

> **V2 是个真实缺口**：现在的 41 个用例全部走 `tpu_top`，单元级（尤其 `tpu_mxu` 的 8 列 × 64 深阵列）
> 只有间接覆盖。补 OOC TB 的收益高于引入任何新工具——这点在 §6 的选型里体现为"优先级 P1"。

---

## 2. 本地工具现状盘点（实测）

### 2.1 已装且可用

| 工具 | 版本 / commit | 路径 | 用途 | 备注 |
|---|---|---|---|---|
| Verilator | **5.034**（2025-02-24） | `.tools/verilator/bin/` | 唯一 RTL 仿真器 | 含 `verilator_coverage`；支持 `--timing`/`--coverage`/`--savable`/`--assert` |
| Yosys | **0.52** | `.tools/yosys/bin/` | 通用门级综合、面积口径（ADR-06） | **不支持 `return <expr>` 式 function**，`tpu_pkg` 已改写；完整 SV 前端仍受限（见 §2.3） |
| GTKWave | 系统包 | `/usr/bin/gtkwave` | 波形查看 | 配合 `--trace-fst`；已有 `scripts/vcd_timeline.py` 做文本化 |
| RISC-V GCC | xPack **15.2.0**（`riscv-none-elf-*`） | `.tools/riscv/bin/` | 裸机程序、`-march=rv64gcv` | 生成 `.bin` 给 NEMU / emu |
| NEMU | **a683e0b + `nemu-vdot.patch`** | `.tools/nemu/` | 指令级参考模型（REF） | 已构建 `build/riscv64-nemu-interpreter` 与 `-so` DPI 库 |
| 香山 emu | `16ae9dd`，Verilator 生成 | `build/xs-work/XiangShan/build/emu` | 系统级 DUT | 阶段一/二/三已用；重建约 75 min |
| Python | 3.13 + numpy 2.5.4 + matplotlib | 系统 | 黄金模型、用例生成、绘图 | 无 pytest |
| JDK / mill | Temurin 21.0.13 / mill 0.12.3 | `.tools/jdk` | 香山 Chisel 生成 | 仅在改香山 RTL 时需要 |

### 2.2 NEMU 这棵树里已经有的、值得直接用的东西

这条最关键——**它决定了"能照搬多少"**：

| 能力 | 位置 | 对 RiscvTpu 的意义 |
|---|---|---|
| DUT/REF 双向拷贝 API | `include/cpu/difftest.h`：`ref_difftest_memcpy/regcpy/exec` | 设备级对拍可直接复用同一套拷贝语义 |
| 提交点跳过 | `difftest_skip_ref()` / `difftest_skip_dut(nr_ref, nr_dut)` | TPU 的 CSR 读写对 REF 是"无副作用"的，必须 skip |
| store commit 对拍 | `CONFIG_DIFFTEST_STORE_COMMIT` + `difftest_check_store()` | **TPU 的 DRAM 写就"像"一组 store**，可直接借用这套队列 |
| **AME 矩阵扩展** | `src/isa/riscv64/instr/ame/{mreg.c,mldst_impl.c,mcompute_impl.c}` | tile 寄存器文件（`mtilem/n/k`）+ 矩阵 load/store 在 ISS 内的完整建模，**是 TPU 设备模型的现成骨架** |
| AME 专用对拍 | `CONFIG_DIFFTEST_AMU_CTRL`、`ref_difftest_matrix_store_commit` | 矩阵写比对接口，比逐字节 memcmp 更贴切 |
| MMIO 差异标记 | `add_mmio_map_with_diff(..., MMIO_DIFF_FLAG, ...)`（`include/device/map.h`） | 可声明"TPU 窗口只在写方向参与对拍" |
| lightSSS / QS 快照 | `CONFIG_LIGHTQS`（`src/cpu/difftest/ref.c`） | 长跑时回滚重放，V1 用不上 |
| SimPoint | `src/checkpoint/simpoint.cpp`、`resource/simpoint/` | 负载采样，V1 用不上 |
| gem5 参考适配 | `CONFIG_DIFFTEST_REF_FOR_GEM5` | 证明这条路走过，但不等于我们要走 |

> **要点**：`src/isa/riscv64/instr/ame/` 的存在说明"在 NEMU 里建一个带自己寄存器文件的矩阵单元"
> 是这棵树支持的一等公民。TPU 设备模型不是新范式，只是"又一个 AME 式设备"。

### 2.3 缺口

| 缺什么 | 影响 | 处理 |
|---|---|---|
| `gem5` / `XS-GEM5` | 无性能模型 | **不引入**（§4 T4） |
| `spike` | 无第二 ISS | 不需要（NEMU 已足够）；若要做交叉 ISS 对拍再评估 |
| `cocotb` / `pytest` | 无 Python 驱动 TB | 不引入；现有 C++ 文本命令 TB 已验证有效 |
| `sv2v` | `tpu_pkg` 的 SV 语法过不了 Yosys 完整前端 | 07 文 D15 首选方案；未装则按 D15 备选改写函数 |
| `sby` / `z3` | 无形式验证 | 用 Verilator `--assert` 做运行时断言（05 文 §7 的 A1~A10） |
| `verilator`/`yosys` 不在 PATH | 脚本需显式导出 | `scripts/*.sh` 已处理 |

---

## 3. 香山方法论拆解：哪些能迁移

### 3.1 香山验证栈的四个部件

```
        ┌─────────────────────────────────────────────────┐
        │ ① emu：Verilator 编出的全芯片（RTL 级 DUT）        │
        │    出波形、出 cycle 数、能跑裸机                   │
        └───────────────────┬─────────────────────────────┘
                            │ 每条退休指令回传 (pc, npc, 架构状态)
        ┌───────────────────▼─────────────────────────────┐
        │ ② difftest：提交级比对框架                        │
        │    DUT ──DPI──> NEMU(-so) ──> 逐寄存器/逐 store 比 │
        └───────────────────┬─────────────────────────────┘
                            │ 失配时
        ┌───────────────────▼─────────────────────────────┐
        │ ③ lightSSS/QS：分叉快照，回滚到失配前 N 条重放     │
        │ ④ checkpoint + SimPoint：从任意点起跑，缩短长测    │
        └─────────────────────────────────────────────────┘
        （gem5/XS-GEM5 是另一条支线：只做性能，不进 difftest 环路）
```

### 3.2 逐项迁移评分

| 香山部件 | 在香山解决什么 | 对 RiscvTpu 是否成立 | 迁移成本 | 结论 |
|---|---|---|---|---|
| **emu（全芯片 Verilator）** | 有完整 SoC，能跑 Linux/裸机 | **部分成立**：TPU 无 SoC，只有 `tpu_top`；且缺 AXI 壳（D8/D9） | L | **降级使用**：V3 用 `tpu_sim_top`，V4 待 D8/D9 完成后接 emu |
| **NEMU 作为 REF** | 指令级真值 | **完全成立**，且已打 `vdot` 补丁 | XS | **直接复用** |
| **difftest 提交级比对** | 每指令比 32 GPR + 32 向量寄存器 + store | **需改造**：TPU 的"退休单位"是描述符，不是指令 | M | **照搬框架、重定义提交点**（§5.2） |
| **lightSSS / QS 快照** | 随机长跑失配后快速回滚 | 成立但当前无需求（41 个用例秒级完成） | M | **暂不引入**，留作 V4 随机长跑 |
| **checkpoint + SimPoint** | 把 SPEC 长跑切成 1000 万指令片段 | 无 SPEC 级负载 | M | **不引入** |
| **gem5 / XS-GEM5** | 乱序核 + cache + DDR 的性能模型 | 只解决"TPU 与 CPU 重叠、DDR 带宽"这类问题 | **L+** | **不引入**（§6） |

### 3.3 核心差异：指令提交 ≠ 描述符提交

这是全部设计的分水岭，必须说清楚：

| 维度 | 香山 CPU difftest | RiscvTpu 需要的 |
|---|---|---|
| 提交信号 | `difftest_step(pc, npc)`，每指令一次 | 每个描述符 `DRAIN→HALT` 一次 |
| 架构状态 | GPR / FPR / VR / CSR / PC | CSR 窗口（32 字）+ TMEM（16K 字）+ ACT（可选）+ **TPU 写过的 DRAM 区间** |
| 失配定位 | PC + 寄存器号 | 描述符地址 + 首个失配字偏移 + `DBG_ACC0/1` |
| 副作用 | store 队列 | DRAM 写 + TMEM 写（`dirty` 语义） |
| 不确定性 | 无（顺序提交） | **有**：`timeout`、`STALL_MEM`、`CYCLES` 天然与 REF 不一致 |

> 结论：**把 `CYCLES`/`MAC`/`STALL_MEM` 从"必须相等"降级为"趋势检查"**，其余位精确。
> 这一条如果搞错，difftest 会被"周期数不同"这种假阳性淹没。

---

## 4. 四层 difftest 方案

### T0 · 模型级（现状，保持）

- **DUT**：`tpu_sim_top`（Verilator 可执行）
- **REF**：`tests/ref_tpu.py`（纯 Python，内层调用 `tests/ref_vdot.py` 强制 G3 成立）
- **提交点**：每个用例结束（`STATUS.HALTED`）
- **比对**：TMEM 区间 + DRAM 输出区间，**16 字节为单位逐位**
- **现状**：41/41 通过，已是最强的一道闸门
- **局限**：Python 模型与 RTL 由同一批人写，**共模错误无法发现**

> T0 的局限正是 T1/T2 的存在理由：换一个独立实现（NEMU）来打破共模。

### T1 · 指令级（已有，可选升级）

- **现状（T1a）**：`tests/run_nemu_cosim.py` 做"同向量集"比对，3,032 用例 / 4,551 结果字 / 0 mismatch
- **T1b（可选）**：把 `tb_vdot.cpp` 改成"程序驱动"——NEMU 跑真实 `.bin`，每条 `vdot` 把 `(vs1, vs2, vl, vd)` 经 DPI 送给 RTL FU，逐指令比对 `vd`
- **收益**：覆盖"连续 `vdot` + 寄存器重命名冲突 + `vl` 边界"这类只有程序才会出现的模式
- **成本**：S（复用 `tb_vdot.cpp` 与 NEMU 的 `-so`）
- **建议**：**做，优先级 P2**；工作量小，且能顺带验证 `vdot` 在 V4 的真实调用形态

### T2 · 设备级（**核心新增，优先级 P0**）

DUT 是 `tpu_top`，REF 是 NEMU 里的 TPU 设备模型。两者吃同一串描述符，按描述符退休比对。

```
   ┌─────────────────── difftest 主循环（C++） ─────────────────────┐
   │  for each desc in stream:                                       │
   │    1. 灌入初始 DRAM 镜像（仅首次）                                 │
   │    2. DUT: 写 DQ_HEAD → 跑至 HALT/ERR → 读回 CSR/TMEM/DRAM         │
   │    3. REF: ref_difftest_tpu_exec_desc(addr) → 读回同样的窗口        │
   │    4. 比 CSR(白名单) → TMEM 区间 → DRAM 写区间 → 首个失配即停        │
   └──────────┬──────────────────────────────────┬──────────────────┘
              │ Verilator 直接调用                 │ dlopen + DPI
       ┌──────▼───────┐                   ┌───────▼────────────┐
       │ tpu_sim_top  │                   │ NEMU -so            │
       │ (+ mem model)│                   │ ref: device/tpu.c   │
       └──────────────┘                   └────────────────────┘
```

**为什么 REF 侧不直接用 Python 模型**：那样只是把 T0 换个壳，仍是共模。设备模型必须以 **C 移植的独立实现**落在 NEMU 里，且必须能读写 NEMU 的物理地址空间（`paddr_read/paddr_write`），才能验证"DMA 地址语义"这一层。

**比对白名单**（其余位精确）：

| 类别 | 字段 | 处理 |
|---|---|---|
| 必须相等 | `ID`、`VERSION`、`CAPS`、`ERROR_CODE`、`ICOUNT`、`DQ_TAIL`、`RETIRE_*`、`DMA_BYTES`、`DBG_DESC` | 逐位 |
| 必须相等 | TMEM 全部 16K 字、ACT 被写块、TPU 写过的 DRAM 区间 | 逐 16 B |
| 降级为趋势 | `CYCLES_LO/HI`、`MAC_*`（上限内一致）、`STALL_MEM` | 只检查单调性与上界 |
| 忽略 | `STATUS` 的瞬态位、`DBG_ACC0/1`（仅失配时打印） | — |
| 特殊 | `DBG_STATE`：REF 无 `FETCH2(8)`，比对时把 8 归一化为 1 | 归一化后比 |

### T3 · 系统级（依赖 07 文 D8/D9）

分两步，**顺序不能颠倒**：

| 步 | 形态 | 目的 | 前置 |
|---|---|---|---|
| **T3a** | 香山 emu + **C++ 行为模型**（emu 的 device 层挂一段 `tpu_mmio_*`） | 先让软件栈、驱动、描述符链在整机里跑通 | 无（不碰 RTL） |
| **T3b** | 香山 emu + **Verilator `tpu_sim_top`**，AXI-Lite + AXI master 对接 | RTL 级整机验证、真实带宽/延迟 | D8、D9（V1.2） |

- T3a 的真值来自 T2 的 NEMU 设备模型（同一份 C 代码可直接复用），所以 **T3a 几乎免费**
- T3b 的难点不在 difftest，而在"Axi 壳 + 时钟域 + 内存一致性"——这正是 07 文 D8/D9 的内容
- **不建议**在 T3b 之前跑 difftest：没有 AXI 壳的 `tpu_top` 无法接进 emu 的总线

**T3a 落地状态（与最初估计的偏差）**：香山 emu 的 MMIO 并不在宿主 `common/device.cpp` 里，
而是在 `src/test/scala/top/SimMMIO.scala` 展开的 AXI4 设备树（UART/Flash/SD/IntrGen 都是
`AXI4SlaveModule`）。因此 T3a 需要多一步 **Chisel 侧设备**，并非"不碰 RTL"：

| 已落地（宿主侧可验证） | 位置 |
|---|---|
| MMIO 适配层（偏移合取、`CTRL.RUN`/`DQ_HEAD` 门铃、CSR 读回） | `sim/emu/tpu_emu.c`、`tpu_emu.h` |
| emu RAM 后端（`difftest_ram_read/write` 的 8 B 读改写） | `sim/emu/tpu_emu_ram_xs.c` |
| 回归：MMIO 路径 vs 直接 API，CSR/TMEM/DRAM 逐位一致（28/28） | `sim/emu/tpu_emu_selftest.c`、`scripts/emu_selftest.sh` |

| 待执行（需重生成 RTL + 重建 emu） | 内容 |
|---|---|
| `AXI4TPU.scala` + `SimMMIO.scala` 挂载 + `init_device()` 里 `tpu_emu_attach_xiangshan_ram()` | 见 `patches/xiangshan-tpu-device.md`，含完整源码与 5 步操作 |

> 该步会改动 `build/xs-work/XiangShan` 并重建 emu（耗时长且会影响既有 vdot emu 基线），
> 因此本次只交付源码与步骤，未在外部树上执行。

### T4 · 性能级（不做 gem5）

| 方案 | 能回答 | 成本 | 结论 |
|---|---|---|---|
| **CSR 计数器 + 解析模型**（现有 `MAC/CYCLES/RETIRE_*`） | MAC 利用率、每精度吞吐、带宽需求 | XS | ✅ **V1 用这个** |
| 扩展 `STALL_MEM`（D12）+ AXI 口统计 | 权重/激活等待占比 | S | ✅ V1.1 顺带做 |
| 自研 DRAM 延迟模型（在 `tpu_mem_model.sv` 加可配延迟与 bank 冲突） | 单/双通道 DDR 下的 MXU 利用率 | S | ✅ 推荐，比 gem5 便宜一个数量级 |
| **gem5 / XS-GEM5** | CPU 与 TPU 重叠、cache 行为、整机 IPC | L+ | ❌ **不引入** |
| DRAMSim3 | 精确 DDR 时序 | M | 可选，仅在做"带宽结论"时引入 |

> 关键判断：RiscvTpu 的性能问题**几乎全在加速器侧**（MAC 利用率、权重带宽），
> 而 gem5 擅长的是**CPU 侧**（乱序、cache）。用 gem5 测 TPU 是"用显微镜称重"。
> 02 文 §5 的带宽守恒（`17.07 GB/s` 峰值 vs `8.53 GB/s` 单通道）用解析模型就能算清。

**T4 落地状态**（默认配置逐周期不变）：

| 改动 | 位置 | 说明 |
|---|---|---|
| 可配读延迟 | `sim/tpu_mem_model.sv` | `RD_LATENCY` 参数，读地址经 L 级移位后组合读；`dbg_*` 后门不吃延迟 |
| 消费侧等待 | `rtl/tpu_top.sv`（FETCH）、`rtl/tpu_mxu.sv`（MX_SW/MX_WR）、`rtl/tpu_copy.sv`（C_RD） | `rwait`/`fwait` 计数；`RD_LATENCY=0` 时路径与基线完全一致 |
| `STALL_MEM` | `rtl/tpu_csr.sv` | 由 `ev_busy && ev_mem_stall` 累加；`ev_mem_stall` 是 fetch/MXU/COPY 三处等待的或 |
| 验证 | `RD_LATENCY=2 scripts/difftest.sh` | 28/28 功能一致；`stall` 由 0 变为非零 |

未做：bank 冲突 / 双通道建模（需要带宽结论时再加，不影响 V1 功能门禁）。

---

## 5. T2 设备级 difftest 详细设计

### 5.1 新增文件

```
RiscvTpu/
├── sim/difftest/
│   ├── tpu_difftest.h          # 两侧共用的数据结构与 REF/DUT 接口
│   ├── tpu_ref_model.c         # 独立 C 设备模型（T2 的 REF，也是 T3a 的行为模型）
│   ├── tpu_difftest_dut.cpp    # Verilator DUT 封装：doorbell/轮询/读窗口
│   └── tpu_difftest_main.cpp   # 主循环、白名单比对、失配诊断
├── sim/emu/                    # T3a：emu 宿主侧适配（不改 RTL）
│   ├── tpu_emu.h / tpu_emu.c   # MMIO 窗口 → tpu_ref_model
│   ├── tpu_emu_ram_xs.c        # DMA → difftest_ram_read/write
│   └── tpu_emu_selftest.c      # MMIO 路径 vs 直接 API 的逐位回归
├── tests/difftest/
│   └── gen_desc_stream.py      # 由 ref_tpu 生成描述符流 + 初始 DRAM 镜像
└── scripts/
    ├── difftest.sh             # T2/T4：构建 + 全量 stream 比对（支持 RD_LATENCY）
    └── emu_selftest.sh         # T3a：适配层回归
```

NEMU 侧（改 `.tools/nemu`，产出 `patches/nemu-tpu-device.patch`）：

```
src/device/tpu.c / tpu.h       # 设备模型：CSR 影子 + 描述符执行 + paddr 读写
src/device/tpu_ref_model.c     # 与 sim/difftest 同一份 C 模型
src/device/Kconfig             # CONFIG_TPU_DEV
src/device/device.c            # init_tpu() 挂载
```

### 5.2 提交协议（时序）

一个描述符的"提交"由三段组成，缺一段就会漏掉错误：

```
DUT 侧（tb 视角）                          REF 侧（NEMU 视角）
  write DQ_HEAD = addr                       —
  poll STATUS.HALTED == 1                    ref_difftest_tpu_exec_desc(addr)
  ── 提交点 ──                               ── 提交点 ──
  read ERROR_CODE / DQ_TAIL / TMEM           read 同样的窗口
  compare(...)                               —
```

三个必须遵守的时序事实（都是 V1 实测踩过的）：

1. **错误码晚一拍**：`ERROR_CODE` 比 `state=ERR` 晚 1 拍落定。DUT 侧必须在 `HALTED/ERROR` 后
   **补 2 拍**再读（`tb_tpu.cpp` 已这么做），否则会读到 0 而误判"REF 错"。
2. **`DBG_STATE` 会读到 8**（`FETCH2`）：比对前把 8 归一化为 1。
3. **`DQ_TAIL` 是"最后完成的地址"（inclusive）**，不是"下一个待处理"。REF 侧必须用同一语义，
   否则链式描述符会整体错位一格。

### 5.3 DPI 接口清单

DUT 侧（由 `tpu_ref_shadow.c` 或 NEMU `-so` 提供）：

| 函数 | 作用 |
|---|---|
| `void difftest_tpu_init(uint64_t dram_base, size_t dram_size)` | 建立 REF 侧地址窗口与影子内存 |
| `void difftest_tpu_memcpy(uint32_t addr, const void *src, size_t n, bool to_ref)` | 初始镜像 / 回写 |
| `void difftest_tpu_exec_desc(uint32_t desc_addr)` | REF 执行一条描述符 |
| `uint32_t difftest_tpu_csr_read(uint32_t off)` | REF 侧 CSR 读回 |
| `int difftest_tpu_tmem_read(uint32_t word_addr, uint32_t *buf, size_t n)` | REF 侧 TMEM 读回 |
| `int difftest_tpu_check_dram(uint32_t addr, const uint8_t *dut, size_t n)` | DRAM 区间比对 |
| `void difftest_tpu_set_skip(bool)` | 对"故意越界/超时"类用例临时关闭比对 |

**注意方向**：`difftest_skip_ref()` 是"REF 不执行、DUT 继续"，用于 **CSR 读写**（REF 无这些 CSR）。
而 TPU 的 DRAM 写**不能** skip——那正是要验证的东西。

### 5.4 NEMU 侧设备模型要点

- **地址窗口**：`0x4000_0000 + 0x0 ~ 0xFFFF` 映射 CSR（与 02 文 §6.2 一致），用
  `add_mmio_map_with_diff("tpu", 0x40000000, tpu_space, 0x10000, MMIO_WRITE | MMIO_READ, tpu_io_handler)`
- **描述符取指**：设备模型直接从 `paddr_read(desc_addr, 64)` 读——与 RTL 的 `mem_raddr` 同源，
  这样"描述符地址语义"也被验证到
- **数值内核**：直接调用 `tests/ref_tpu.py` 的 C 移植（`tpu_ref_shadow.c`），避免两份数值实现
- **执行时机**：doorbell 写（`CTRL.RUN` 或 `DQ_HEAD` 写）时**同步执行**整条描述符链，因为 NEMU 是解释器，
  没有"异步设备"的必要；这恰好等价于 V1 的 `S_RUN` 阻塞语义
- **`CONFIG_TPU_DEV` 默认关闭**：保证现有 3,032 协同用例与香山 emu 的 NEMU 构建不受影响

### 5.5 失配诊断输出（必须一次到位）

失配时打印下列内容，缺一项就要二次复现：

```
[TPU-DIFF] mismatch at desc=0x00000200 seq=3 field=TMEM[0x1A2C]
  ref = 0x3F800000  dut = 0x3F800001
  desc dump: w0..w15 = ...
  DBG_ACC0/1 = 0x... / 0x...
  first bad byte offset in tile = 12
  CSR: ERROR_CODE=0x00 ICOUNT=4 DQ_TAIL=0x200 MAC=131072
```

---

## 6. 工具矩阵与选型建议

| 工具 | 层级 | 结论 | 理由 |
|---|---|---|---|
| **Verilator 5.034** | V0~V4 | **必选** | 唯一 RTL 仿真器；`--coverage` + `--assert` + `--savable` 够用 |
| **verilator_coverage** | 覆盖率 | **必选** | 05 文 Q4 的语句/分支/功能覆盖目标靠它 |
| **NEMU（含 vdot 补丁）** | V1/V3 REF | **必选** | 已构建 `-so`；树内含 AME，设备建模有先例 |
| **GTKWave + `vcd_timeline.py`** | 调试 | **必选** | 波形是唯一的"最后手段" |
| **RISC-V GCC 15.2.0** | V3~V5 | **必选** | 裸机程序与 microbench |
| **Yosys 0.52** | 面积/门级 | **推荐** | ADR-06 的可复现口径；配合 `sv2v` 后覆盖 D15 |
| **`sv2v`** | D15 | **推荐（缺口）** | 让 Yosys 吃下 `tpu_pkg` 的 SV 语法 |
| **自研 DRAM 延迟模型** | T4 | **推荐** | 比 gem5 便宜一个数量级，直击带宽结论 |
| **`tb_vdot` 程序级驱动（T1b）** | V1 | 可选（P2） | 小成本补"连续 vdot"覆盖 |
| **cocotb / pytest** | V2 | **不推荐** | 与现有 C++ 文本命令 TB 重复，且需重写 41 个用例 |
| **spike** | — | **不推荐** | RVV+vdot 需再打补丁，与 NEMU 完全重复 |
| **sby / z3** | 形式 | 可选（P3） | 05 文 A1~A10 先用 Verilator `--assert` 覆盖 |
| **gem5 / XS-GEM5** | T4 | **不推荐** | 只解决 CPU 侧问题；TPU 需另写 SimObject |
| **Vivado / 商仿** | — | 不做 | 本地无 PDK，ADR-06 已定 generic 口径 |

---

## 7. 落地路线（与 07 文批次对齐）

| 阶段 | 内容 | 产出 | 验收 | 前置 |
|---|---|---|---|---|
| **D0** | V2 单元 OOC TB（`mxu`/`quant`/`copy`/`act`） | `sim/tb_mxu.cpp` 等 | 单元级逐位一致，覆盖率可 annotate | 无 |
| **D1** | T2 骨架：CSR 白名单比对 + 单描述符 | `sim/difftest/*`、`scripts/difftest.sh` | ✅ 单条 GEMM 三精度 0 失配 | D0 |
| **D2** | T2 完整：描述符链 + 错误注入 + DRAM 区间比对 | `patches/nemu-tpu-device.patch` | ✅ 28 个 stream（含 7 类可达错误码）全绿 | D1、07 文 V1.1 |
| **D3** | T1b 程序级 FU difftest | `tests/difftest/fu/*` | 连续 vdot 程序 0 失配 | D1 |
| **D4** | T3a 整机行为模型 → T3b 整机 RTL | `sim/emu/*`、`patches/xiangshan-tpu-device.md` | ⏳ 适配层 28/28；emu 内挂载待重生成 RTL | D2、07 文 V1.2 |
| **D5** | T4 性能模型（DRAM 延迟 + `STALL_MEM`） | `RD_LATENCY` + `STALL_MEM` | ✅ `RD_LATENCY=2` 功能 28/28，`STALL_MEM` 生效 | D4（可先于 D4） |

**门禁**：D1 之后，任何 P0 用例必须在 **T0 与 T2 双通道**通过（T0 快、T2 独立），
单通道通过不算绿。这直接回应 §4 T0 的共模风险。

---

## 8. 风险与替代

| 风险 | 表现 | 缓解 |
|---|---|---|
| **共模错误**（Python 模型与 RTL 同源） | 双通道同时"绿"，但都错 | T2 用 NEMU 独立实现；必要时再引入第三次实现（openTPU 文档伪码） |
| **不确定量污染 difftest** | `CYCLES`/`STALL_MEM` 天然不等 | 白名单机制（§4 T2），非确定量只做上界检查 |
| **AXI 壳缺失阻塞 T3b** | 无法接 emu | 先做 T3a（不碰 RTL），T3b 挂在 07 文 V1.2 之后 |
| **NEMU 补丁漂移** | 上游更新导致补丁失效 | 锁 commit `a683e0b`；补丁文件入库 `patches/` |
| **`sv2v` 缺失** | D15 无法闭环 | 备选：改写 `tpu_pkg` 的 `return` 式 function（07 文已列） |
| **gem5 诱惑** | 花大力气做整机性能，却回答不了 TPU 问题 | §4 T4 的判据写进评审门：**"这个问题能不能用 CSR 计数器回答？"** |

---

## 9. 复现命令（当前可用）

```bash
cd /home/dbqxxs/xsriscv/cieriscv/RiscvTpu

# V0+V3：数值与端到端（现状，无需任何新装）
scripts/lint.sh
scripts/regress.sh

# V1：RTL ↔ NEMU 同向量协同（沿用根项目）
python3 ../tests/run_nemu_cosim.py

# 覆盖率（Verilator）
export PATH=../.tools/verilator/bin:$PATH
verilator_coverage --annotate build/cov_annotated build/cov_*.dat

# 面积（generic 口径）
../.tools/yosys/bin/yosys -p "read_verilog -sv rtl/*.sv; hierarchy -top tpu_top; synth -top tpu_top; stat"

# 系统级（现状：仅 vdot 阶段二/三的用例）
cd ../build/xs-work/XiangShan && ./build/emu --help
```

T2 落地后新增：

```bash
scripts/difftest.sh --cases tests/difftest/cases/gemm_int8.json --seed 1
```

---

## 10. 与其它文档的对齐

| 文档 | 本文补充了什么 |
|---|---|
| 01 可行性 §工具链 | 从"可行性判断"细化为"具体工具 + 版本 + 路径 + 缺口" |
| 02 架构 §3/§6 | 为 V3/V4 的仿真拓扑提供落地形态（`tpu_sim_top` → AXI 壳 → emu） |
| 03 功能规格 §5 | 明确 `FETCH2`/错误码晚一拍对 difftest 的影响（§5.2 三条时序事实） |
| 05 测试方案 §2/§3 | 把 L0~L4 映射到 T0~T4，补"双通道门禁"（§7） |
| 06 实现报告 §7 | 把"下一步"的 difftest 一句展开为可执行路线 |
| 07 偏差修复计划 | D8/D9 → T3b；D12 → T4；D14 → §5.2 归一化；D15 → `sv2v` |
