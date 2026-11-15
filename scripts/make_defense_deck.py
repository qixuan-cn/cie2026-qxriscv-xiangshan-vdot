#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成答辩 PPT（v1.3 三模式：INT8 / INT4 打包 / BitNet b1.58 三值）。

用法：python3 scripts/make_defense_deck.py [输出路径]
依赖：python-pptx、Pillow（仅用于排版自检）
"""
import os, sys, re, subprocess

sys.path.insert(0, "/tmp/pdflibs")
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ---------------------------------------------------------------- 设计常量
NAVY = RGBColor(0x0B, 0x25, 0x45)
TEAL = RGBColor(0x0E, 0x7C, 0x86)
GREY = RGBColor(0x5B, 0x6B, 0x7C)
LINE = RGBColor(0xDC, 0xE3, 0xEA)
LIGHT = RGBColor(0xF3, 0xF7, 0xF9)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
AMBER = RGBColor(0xB4, 0x6A, 0x0B)

CJK = "Microsoft YaHei"
MONO = "Consolas"
W, H = 13.333, 7.5
M = 0.72
CW = W - 2 * M

CHECKS = []          # (slide_no, text, w_in, h_in, font_pt, mono)


def _tb(slide, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    return tf


def txt(slide, x, y, w, h, s, size=12, bold=False, color=NAVY, font=CJK,
        align=PP_ALIGN.LEFT, spacing=1.0, anchor=MSO_ANCHOR.TOP, check=True):
    tf = _tb(slide, x, y, w, h, anchor)
    lines = s.split("\n")
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.line_spacing = spacing
        r = p.add_run()
        r.text = ln
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.name = font
        r.font.color.rgb = color
    if check:
        CHECKS.append((len(PRES.slides), s, w, h, size, font == MONO))
    return tf


def rect(slide, x, y, w, h, fill=WHITE, line=LINE, radius=None):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if fill is None:
        shape.fill.background()
    else:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill
    if line is None:
        shape.line.fill.background()
    else:
        shape.line.color.rgb = line
        shape.line.width = Pt(0.75)
    shape.shadow.inherit = False
    if radius:
        shape.adjustments[0] = radius
    return shape


def header(tag, title, sub=None):
    s = PRES.slides.add_slide(PRES.slide_layouts[6])
    txt(s, M, 0.40, 3.2, 0.26, tag, 10.5, True, TEAL, MONO, check=False)
    txt(s, M, 0.64, CW, 0.66, title, 25, True, NAVY)
    rect(s, M, 1.38, CW, 0.012, fill=LINE, line=None)
    if sub:
        txt(s, M, 1.50, CW, 0.32, sub, 11.5, False, GREY)
    return s


def footer(s, note=""):
    if note:
        txt(s, M, 7.02, CW - 1.0, 0.24, note, 8.5, False, GREY, check=False)
    txt(s, W - M - 0.9, 7.02, 0.9, 0.24, str(len(PRES.slides)).zfill(2),
        9, True, TEAL, MONO, align=PP_ALIGN.RIGHT, check=False)


def cards(s, items, y=1.92, h=1.40, gap=0.16, num_size=28, label_size=10):
    n = len(items)
    w = (CW - gap * (n - 1)) / n
    for i, (num, label) in enumerate(items):
        x = M + i * (w + gap)
        rect(s, x, y, w, h, WHITE, LINE, radius=0.06)
        rect(s, x, y, 0.05, h, fill=TEAL, line=None)
        txt(s, x + 0.16, y + 0.16, w - 0.3, 0.52, num, num_size, True, NAVY,
            CJK if not any(c.isascii() and c.isalpha() for c in num[:1]) else MONO,
            check=False)
        txt(s, x + 0.16, y + 0.80, w - 0.3, h - 0.86, label, label_size, False, GREY)


def bullets(s, items, x=M, y=3.5, w=CW, size=11.5, gap=0.40, mark="—"):
    """要点列表：支持 **加粗** 与 `等宽` 两种行内标记。"""
    yy = y
    for it in items:
        tf = _tb(s, x, yy, w, gap)
        p = tf.paragraphs[0]
        p.line_spacing = 1.0
        r0 = p.add_run(); r0.text = mark + " "
        r0.font.size = Pt(size); r0.font.name = CJK; r0.font.color.rgb = TEAL; r0.font.bold = True
        for tok in re.split(r"(\*\*.+?\*\*|`.+?`)", it):
            if not tok:
                continue
            r = p.add_run()
            if tok.startswith("**") and tok.endswith("**"):
                r.text = tok[2:-2]
                r.font.bold = True
                r.font.name = CJK
                r.font.color.rgb = NAVY
            elif tok.startswith("`") and tok.endswith("`"):
                r.text = tok[1:-1]
                r.font.name = MONO
                r.font.color.rgb = RGBColor(0x0E, 0x5A, 0x8A)
            else:
                r.text = tok
                r.font.name = CJK
                r.font.color.rgb = GREY
            r.font.size = Pt(size)
        plain = it.replace("**", "").replace("`", "")
        CHECKS.append((len(PRES.slides), mark + " " + plain, w, gap, size, False))
        yy += gap


def table(s, head, rows, x=M, y=3.5, w=CW, col=None, size=9.5, head_fill=NAVY,
          row_h=0.33, head_h=0.34):
    nr, nc = len(rows) + 1, len(head)
    shape = s.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(head_h + row_h * len(rows)))
    tb = shape.table
    tb.first_row = False
    if col:
        total = sum(col)
        for i, c in enumerate(col):
            tb.columns[i].width = Emu(int(Inches(w) * c / total))
    tb.rows[0].height = Inches(head_h)
    for i in range(len(rows)):
        tb.rows[i + 1].height = Inches(row_h)
    for j, htxt in enumerate(head):
        cell = tb.cell(0, j)
        cell.fill.solid(); cell.fill.fore_color.rgb = head_fill
        cell.margin_left = cell.margin_right = Inches(0.08)
        cell.margin_top = cell.margin_bottom = Inches(0.03)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        p = cell.text_frame.paragraphs[0]
        r = p.add_run(); r.text = htxt
        r.font.size = Pt(size); r.font.bold = True; r.font.name = CJK; r.font.color.rgb = WHITE
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            cell = tb.cell(i + 1, j)
            cell.fill.solid()
            cell.fill.fore_color.rgb = WHITE if i % 2 == 0 else LIGHT
            cell.margin_left = cell.margin_right = Inches(0.08)
            cell.margin_top = cell.margin_bottom = Inches(0.03)
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            p = cell.text_frame.paragraphs[0]
            r = p.add_run(); r.text = str(val)
            r.font.size = Pt(size); r.font.name = CJK; r.font.color.rgb = NAVY
    return tb


def strip(s, text, y=6.36, h=0.46, color=TEAL):
    rect(s, M, y, CW, h, LIGHT, LINE, radius=0.04)
    rect(s, M, y, 0.05, h, fill=color, line=None)
    txt(s, M + 0.18, y + 0.10, CW - 0.36, h - 0.14, text, 10, False, NAVY,
        check=False)


def flow(s, boxes, y=1.95, h=1.05, gap=0.30):
    n = len(boxes)
    aw = 0.42
    w = (CW - gap * (n - 1) - aw * (n - 1)) / n
    x = M
    for i, (t1, t2) in enumerate(boxes):
        rect(s, x, y, w, h, WHITE, LINE, radius=0.07)
        txt(s, x + 0.14, y + 0.14, w - 0.28, 0.30, t1, 11.5, True, TEAL, MONO)
        txt(s, x + 0.14, y + 0.48, w - 0.28, h - 0.56, t2, 10, False, NAVY)
        x += w
        if i < n - 1:
            txt(s, x + 0.06, y + h / 2 - 0.19, aw - 0.12, 0.38, "→", 16, True,
                RGBColor(0x9D, 0xB3, 0xC6), MONO, align=PP_ALIGN.CENTER, check=False)
            x += aw + gap


def hbar(s, x, y, w, label, value, vmax, color=TEAL, suffix="", lw=1.55,
         bh=0.34, size=10):
    """横向条形图：左标签、右数值、条长按 vmax 归一。"""
    txt(s, x, y + 0.05, lw, bh - 0.06, label, size, False, GREY, check=False)
    bar_w = (w - lw - 1.05) * value / vmax
    rect(s, x + lw, y, bar_w, bh, color, None)
    txt(s, x + lw + bar_w + 0.10, y + 0.05, 1.0, bh - 0.06,
        f"{value:,}{suffix}", size, True, NAVY, MONO)


PRES = Presentation()
PRES.slide_width = Inches(W)
PRES.slide_height = Inches(H)

# ------------------------------------------------------------------ 01 封面
s = PRES.slides.add_slide(PRES.slide_layouts[6])
rect(s, 0, 0, W, 0.10, fill=TEAL, line=None)
txt(s, M, 1.35, CW, 0.30, "2026 CIE  ·  RISC-V 自定义指令赛  ·  香山昆明湖 V2",
    11, True, TEAL, MONO, check=False)
txt(s, M, 1.80, CW, 1.85,
    "vdot 指令族：INT8 点积、INT4 打包双流\n与 BitNet b1.58 三值融合",
    37, True, NAVY, CJK, spacing=1.12)
rect(s, M, 3.78, 1.9, 0.035, fill=TEAL, line=None)
txt(s, M, 3.98, CW, 0.62,
    "一条 uop 完成 8 / 16 路乘累加  ·  三模式共享流水  ·  BitNet 路径零乘法器  ·  16 MAC/cycle",
    13.5, False, GREY)
for i, (a, b) in enumerate([("vdot.vv", "INT8 · 8 MAC"), ("vdot4x2.vv", "INT4 · 16 MAC"),
                            ("vtdot.vv", "三值 · 16 MAC · 0 乘法器")]):
    x = M + i * 3.30
    rect(s, x, 4.95, 3.10, 0.86, WHITE, LINE, radius=0.08)
    txt(s, x + 0.16, 5.06, 2.8, 0.28, a, 13, True, TEAL, MONO)
    txt(s, x + 0.16, 5.38, 2.8, 0.26, b, 9.5, False, GREY)
txt(s, M, 6.42, CW, 0.30,
    "启煊Vdot  ·  单元回归 3047/3047  ·  协同仿真 3032 用例 / 4551 字 / 0 mismatch  ·  综合 7427 单元 / 25 级",
    9.5, False, GREY, check=False)

# ------------------------------------------------------------------ 02 问题
s = header("01 / 问题", "为什么需要一条专用的点积指令",
           "边缘 LLM 推理的瓶颈不是单条乘加，而是数据通路上的指令条数与权重带宽")
cards(s, [("6 → 1", "每 8 元素点积的数据通路指令数（纯 RVV 组合 → vdot）"),
          ("9.8×", "对标量点积的实测加速（8-lane 公平口径，NITER=2000）"),
          ("4×", "BitNet 三值权重的等效存储压缩（2 bit vs INT8）"),
          ("16", "INT4 / 三值模式的单指令 MAC 数（INT8 为 8）")])
bullets(s, [
    "**量化推理已成主流**：INT8 之外，W4A8 与 BitNet b1.58（权重 ∈ {−1,0,+1}）在端侧快速铺开，通用向量指令只能用多条指令拼出点积。",
    "**现有 RVV 组合的代价**：`vsext`×4 + `vmul` + `vredsum` 共 6 条数据通路指令，且中间结果要以 32 bit 元素展开，寄存器压力与带宽被放大。",
    "**本设计与竞品的路线差异**：不做“单结果 + 三档数据类型 + 调度器 bypass”的方案，而是**一条指令产出两个部分和**，并把 BitNet 三值直接做进数据通路。",
], y=3.62, gap=0.85)
strip(s, "结论：把“读权重—展开—乘—归约”压进一条 uop，是端侧 LLM 推理最直接的结构性收益。")

# ------------------------------------------------------------------ 03 赛题理解
s = header("02 / 赛题理解", "约束先行，差异化在“一条指令两个结果”",
           "平台约束不可妥协，创新只能落在数据通路内部")
table(s, ["约束", "本设计的处理"], [
    ["SEW=8 / LMUL=1 / vm=1", "三条指令共用同一合法性子集，其余配置一律判非法、不写回"],
    ["单 uop、不新建流水段", "3 级流水（EX1→EX2→WB），延迟 3 cycle，吞吐 1 inst/cycle"],
    ["复用既有向量发射与写回", "仅新增 1 个 VFu，7 个注册点，不改调度器、不加 bypass 通路"],
    ["VLEN=128 的固定布局", "vs2 高 64 bit 三模式均不用；INT4 只消费低 64 bit，三值只消费低 32 bit"],
], y=1.94, row_h=0.42, col=[1.0, 1.75])
bullets(s, [
    "**同一条编码空间承载三种数据类型**：opcode/funct3/vm 完全一致，仅 funct6 区分，译码表只多两行，软件侧零成本切换。",
    "**双累加流是本设计的结构性差异**：INT4 与三值都把 16 路拆成两组 8 路，一次写回 `r0`/`r1`，配合软件侧两条累加链，避免单累加器的串行依赖。",
    "**不受调度器改动影响**：竞品需要改 bypass2 唤醒，本设计保持原发射时序，集成风险与评审成本都更低。",
], y=4.34, gap=0.62)
strip(s, "一句话：别人做“更宽的单结果”，我们做“同宽的双结果 + 权重免乘法”。")

# ------------------------------------------------------------------ 04 指令族
s = header("03 / 指令族", "三条指令，一套编码，三种数据解释",
           "opcode 0x57 · funct3 000（OPIVV）· vm=1 · funct6 区分模式")
table(s, ["指令", "funct6", "vs1", "vs2", "输出（vd）", "MAC"], [
    ["vdot.vv", "111111", "8 × int8", "8 × int8", "[31:0]=S，其余清零", "8"],
    ["vdot4x2.vv", "111110", "16 × int4（低 64 bit）", "16 × int4（低 64 bit）", "[31:0]=r0，[63:32]=r1", "16"],
    ["vtdot.vv", "111101", "16 × int8 激活", "16 × 2-bit 三值码（低 32 bit）", "[31:0]=r0，[63:32]=r1", "16"],
], y=1.94, row_h=0.40, col=[0.95, 0.72, 1.5, 1.6, 1.75, 0.45])
bullets(s, [
    "**操作数严格按 RISC-V 字段**：vs1 在 bits[19:15]（激活/被乘数），vs2 在 bits[24:20]（权重/乘数），不做任何字段重定义。",
    "**INT4 是打包而非窄化**：每字节拆两个 nibble 分别参与 4×4 乘法，16 个 MAC 全部在 EX1 内完成。",
    "**三值走的是选择而非乘法**：`01→+a`、`10→−a`、`00/11→0`；`11` 也归零，防止未初始化权重被当成有效值。",
], y=3.90, gap=0.72)
strip(s, "新增硬件：16 个 4×4 乘法器（INT4）+ 16 条取反/清零选择（三值），共用两棵 12-bit 归约树。")

# ------------------------------------------------------------------ 05 BitNet
s = header("04 / BitNet b1.58 融合", "把三值权重做进数据通路：零乘法器、4× 压缩",
           "W1.58A8：激活保持 INT8，权重用 2-bit 码，缩放移出指令")
table(s, ["三值码", "含义", "计算", "说明"], [
    ["01", "+1", "r += a", "正权重"],
    ["10", "−1", "r −= a", "负权重"],
    ["00 / 11", "0", "不累加", "11 归零可屏蔽未初始化数据"],
], x=M, y=1.92, w=5.55, row_h=0.36, col=[0.7, 0.7, 1.0, 2.4])
txt(s, M, 3.62, 5.4, 0.30, "三值码取自 vs2 低 32 bit，每 16 个码对应 16 条 lane", 9.5, False, GREY)
bullets(s, [
    "**EX1 没有乘法器**：每条 lane 只做“9-bit 取值 + 取反 + 三选一”，`a=−128` 取反后得 +128，故 lane 需 9 bit。",
    "**权重 4× 压缩**：16 个权重压进 32 bit；若按 1.58-bit 变长打包，16 个权重需 25.3 bit，定长 3 字节也只有 24 bit，寄存器内无收益，故用 2-bit 定长。",
    "**量化在指令之外**：absmean 量化与 `β·γ` 反量化由软件完成，指令只保证整数部分逐位精确。",
], x=6.55, y=1.92, w=6.05, gap=0.78, size=10.8)
flow(s, [
    ("vs1  激活", "16 × int8\n全 128 bit"),
    ("EX1  三值选择", "±a / 0\n无乘法器，9-bit lane"),
    ("EX2 + WB", "两棵 12-bit 树\nr0 / r1 双写回"),
], y=4.42, h=1.02)
txt(s, M, 5.60, CW, 0.28,
    "单条指令内 16 路并行 → 两个独立部分和，软件侧可直接接两条累加链",
    9.5, False, GREY)
strip(s, "边界：组和 ∈ [−1024, +1024]，12-bit 有符号足够；与 INT4 共用同一套归约树与写回格式。")

# ------------------------------------------------------------------ 06 微架构
s = header("05 / 微架构", "三级共享流水：EX1 分叉，EX2 合流",
           "INT8 走 8×8 乘法 + 20-bit 单树；INT4 与三值走 16 lane + 两棵 12-bit 双树")
flow(s, [
    ("EX1-A  INT8", "8 × 8×8 有符号乘法\n→ 16-bit 乘积"),
    ("EX1-B  INT4", "16 × 4×4 有符号乘法\n→ 8-bit 乘积"),
    ("EX1-C  VTDOT", "16 × (±a / 0) 选择\n无乘法器"),
    ("EX2  归约", "20-bit 单树 / 双 12-bit 树\n两组各 8 路"),
    ("WB  格式化", "三模式按 fuOpType 选\n写回 128 bit"),
])
bullets(s, [
    "**分叉只在 EX1**：三种数据类型各自的乘/选单元，之后立即收敛到寄存器，不给后续阶段增加模式判断。",
    "**EX2 两套树各有其位宽理由**：INT8 上界 131072 需 19 bit（取 20）；INT4 组和 ∈ [−448,512]、三值 ∈ [−1024,1024] 均 12 bit 可覆盖。",
    "**吞吐与延迟**：延迟 3 cycle、吞吐 1 inst/cycle；MAC/cycle = 8 : 16 : 16（INT8 : INT4 : 三值）。",
], y=3.34, gap=0.92)
strip(s, "无状态设计：`vstart≠0` 重放同一 uop 结果一致，寄存器只在模式下多存一组 9-bit 中间值。")

# ------------------------------------------------------------------ 07 位宽
s = header("06 / 位宽与边界", "三个“差一位”的坑，都用位宽论证关掉",
           "整数语义逐位精确，边界值全部进回归向量")
table(s, ["路径", "最坏取值", "所需位宽", "实现", "验证用例"], [
    ["INT8 累加", "−130048 … +131072", "19 bit", "ACC_W = 20", "TC-L00..07、131072 边界"],
    ["INT4 组和", "−448 … +512", "11 bit", "DUAL_W = 12", "int4_nibble 16/16"],
    ["三值单 lane", "−128 取反 = +128", "9 bit", "TERN_W = 9", "ternary_boundary 3/3"],
    ["三值组和", "−1024 … +1024", "12 bit", "DUAL_W = 12", "ternary_boundary 3/3"],
], y=1.94, row_h=0.40, col=[1.05, 1.55, 0.9, 0.95, 1.9])
bullets(s, [
    "**18 bit 不够**：8×128×128 = 131072，有符号 18 bit 上限 131071，故 INT8 取 20 bit。",
    "**9 bit 的由来**：三值 lane 只有一个值越界，就是 −128 取反，若仍用 8 bit 会溢出成 −128。",
    "**非法配置全覆盖**：`vm=0`、`vsew=1/2/3/4`、`vlmul=m2`、`mode=3` 均触发非法，不写回、不污染 vd；`vl=0` 直接退休不写回。",
], y=4.20, gap=0.66)
strip(s, "三模式的边界用例已固化进向量文件与 tb 补充自检，回归时逐条断言。")

# ------------------------------------------------------------------ 08 集成
s = header("07 / 香山集成", "只做加法：一个 VFu、七个挂点、零调度器改动",
           "复用既有向量发射/写回框架，模式由 fuOpType 选择")
table(s, ["环节", "改动"], [
    ["译码", "VecDecoder 增加 3 条 BitPat，fuOpType[1:0] = 0/1/2 对应三种模式，3 保留给非法"],
    ["配置", "VdotCfg：latency = 3，writeVecRf，exceptionOut = illegalInstr"],
    ["功能单元", "新增 VDotUnit（Chisel），内部三模式共享数据通路，接 VFEX0"],
    ["写回", "写回向量寄存器，沿用既有 vecWen 传播，不自定义端口时序"],
    ["验证入口", "既有向量冒烟 + vdot 定向用例，每改一版跑一次"],
], y=1.94, row_h=0.38, col=[0.85, 3.2])
bullets(s, [
    "**不新建流水段**：单元延迟 3 与既有 VFu 对齐，调度器无需感知新写回时延。",
    "**不加 bypass 通路**：没有改 bypass2 唤醒，寄存器入、寄存器出的形态与原框架一致。",
    "**编译验证**：香山全量 Scala 编译 573 个任务、0 error；Verilog 与 Chisel 双版本同向量等价（3047/3047）。",
], y=4.72, gap=0.54)
strip(s, "集成面越小，评审越容易复核：改动集中在 1 个新文件 + 2 处注册。", y=6.30)

# ------------------------------------------------------------------ 09 验证
s = header("08 / 功能验证", "3047 条用例、四个维度、逐位比对 128 bit",
           "RTL 单元回归 + 断言 + 覆盖率 + Chisel 等价，四路交叉")
cards(s, [("3047/3047", "单元回归全通过（INT8 / INT4 / 三值）"),
          ("55/55", "功能覆盖点全覆盖"),
          ("16/16", "INT4 逐 nibble 定位用例"),
          ("3+12", "三值边界 3 项 + 三模式非法配置 12 项")])
bullets(s, [
    "**逐位比对**：每个合法用例比对完整 128 bit 输出，高位清零与双结果布局都在断言里显式检查。",
    "**非法与边界**：`vl=0` 不写回（哨兵值保持不变）、四类非法配置各触发一次，三模式一致。",
    "**Chisel 与 Verilog 同向量等价**：由 Chisel 生成 `VDotCore.sv` 后跑同一套 3047 条向量，结果一致。",
], y=3.62, gap=0.80)
strip(s, "覆盖率与断言的判定口径：写回、清零、非法、边界四类各自独立断言，避免互相掩盖。")

# ------------------------------------------------------------------ 10 协同仿真
s = header("09 / 协同仿真与基线", "同一向量集，三条执行路径，0 mismatch",
           "NEMU 语义 ↔ 金标准 ↔ RTL 逐字比对；再用纯 RVV 组合做功能基线")
cards(s, [("3032", "可运行用例（INT8 1513 / INT4 759 / 三值 760）"),
          ("4551", "结果字（双结果用例按 r0/r1 两行）"),
          ("0", "RTL 与 NEMU 的逐字不匹配数"),
          ("6 → 1", "数据通路指令数（RVV 组合 → vdot）")])
bullets(s, [
    "**NEMU 侧实现三条指令**：同一套译码/执行扩展，非法配置统一抛 `EX_II`，`vl=0` 正常退休不写回。",
    "**基线等价性**：用 `vsext`×4 + `vmul` + `vredsum` 复现 INT8 语义，1513/1513 与金标准一致，单用例核心指令 14 → 7。",
    "**覆盖分工**：15 条不进入可运行集——13 条 `vl=0`/非法配置 + 2 条保留模式，单独由边界自检覆盖。",
], y=3.62, gap=0.80)
strip(s, "结论：三模式的语义在模拟器、金标准、RTL 三处一致，且相对纯 RVV 组合确有结构性削减。")

# ------------------------------------------------------------------ 11 综合与性能
s = header("10 / 综合与性能", "7427 单元、356 触发器、25 级：面积换吞吐，时序不变",
           "Yosys 0.52 工艺无关综合（synth -noabc + techmap），同流程对 INT8 单模式复核")
table(s, ["配置", "通用单元", "触发器", "最长 R2R", "GE 估算"], [
    ["三模式（现行）", "7427", "356", "25 级", "≈10.1k – 15.8k"],
    ["INT8 单模式（对照）", "4187（历史 4186）", "172", "25 级", "≈5.35k – 8.38k"],
    ["增量", "+3240（+77.4%）", "+184", "0（不变）", "≈+89%"],
], x=M, y=1.92, w=6.55, row_h=0.36, col=[1.35, 1.2, 0.65, 0.75, 1.15])
bullets(s, [
    "**关键路径没动**：两种配置最长路径都是 25 级，起点 `s1_prod[1][1]`、终点 `s2_sum[15]` 完全一致。",
    "**INT4 最划算**：INT8 为 512 部分积 / 8 MAC = 64 每 MAC，INT4 为 256 / 16 = 16，单位 MAC 乘法器面积约为 1/4。",
    "**实测性能**：0.5075 cycle/点积，对标量 9.8×（8-lane 公平），对 16-lane RVV 基线 11.8×（上界）。",
], x=7.55, y=1.92, w=5.05, gap=0.90, size=10.8)
txt(s, M, 3.62, 6.55, 0.28, "通用单元数（同一门级口径）", 9.5, True, GREY)
hbar(s, M, 3.98, 6.55, "INT8 单模式", 4187, 7427, RGBColor(0x9D, 0xB3, 0xC6))
hbar(s, M, 4.46, 6.55, "三模式", 7427, 7427, TEAL)
txt(s, M, 5.02, 6.55, 0.28, "MAC / cycle：8（INT8） → 16（INT4 / 三值）", 10, True, NAVY)
txt(s, M, 5.36, 6.55, 0.55,
    "面积归一化吞吐：8/4187 → 16/7427，按单元数 +12.7%（GE 口径约 +5.8%）；\n"
    "INT8 路径逐位不变，新增面积全部落在 INT4 / 三值两条新通路上。",
    9.5, False, GREY)
strip(s, "口径：generic 单元数（二输入门）与拓扑深度，不含 µm²/ns；PDK 签核列为后续项。", y=6.30)

# ------------------------------------------------------------------ 12 结论
s = header("11 / 结论", "完成度、限制与下一步",
           "把可验证的部分做透，把没做的边界说清楚")
table(s, ["阶段", "状态", "关键证据"], [
    ["阶段一", "完成", "emu / hello 打印 / vadd 波形入库"],
    ["阶段二", "完成", "3047/3047、55/55、Chisel 等价、香山编译 PASS"],
    ["阶段三", "完成", "3032 用例 / 4551 字 / 0 mismatch；基线 1513/1513"],
    ["阶段四", "完成", "lint 0 告警、综合 7427/356/25、九步回归 ALL PASS"],
], y=1.92, row_h=0.36, col=[0.7, 0.6, 3.4])
bullets(s, [
    "**已闭环**：三条指令的语义、RTL、Chisel、NEMU、香山集成、单元与协同验证、综合面积与时序拓扑。",
    "**未闭环**：PDK 级面积/时序签核未做；系统级 emu 实测仍只覆盖 INT8，INT4/三值的 emu 重跑列为后续项。",
    "**下一步**：把三值路径的取反逻辑合并为“掩码累加 + 一次减法”，并复用 INT8 乘法阵列承载 INT4，可再压缩 15%–20% 面积。",
], y=4.00, gap=0.66)
strip(s, "一句话总结：同一条编码空间，8/16 MAC 三档吞吐，BitNet 权重免乘法，集成面最小。")

OUT = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/dbqxxs/xsriscv/cieriscv/submission_bundle/04_其他材料附件/答辩PPT_启煊Vdot_12页.pptx"
os.makedirs(os.path.dirname(OUT), exist_ok=True)
PRES.save(OUT)
print(f"saved {OUT}  slides={len(PRES.slides)}")

# ------------------------------------------------------------ 排版自检
def font_path(family):
    try:
        out = subprocess.check_output(["fc-match", "-f", "%{file}", family], text=True).strip()
        return out or None
    except Exception:
        return None

try:
    from PIL import ImageFont
    fp_map = {"cjk": font_path("Noto Sans CJK SC"), "mono": font_path("DejaVu Sans Mono")}
    EMU_IN = 914400
    bad = 0
    for idx, text, w, h, pt, mono in CHECKS:
        fp = fp_map["mono" if mono else "cjk"]
        if not fp:
            continue
        f = ImageFont.truetype(fp, int(pt * 96 / 72))
        maxw = w * EMU_IN / 9144  # inch -> px @96dpi
        lines = 0
        for seg in text.split("\n"):
            cur = ""
            for ch in seg:
                if f.getlength(cur + ch) > maxw and cur:
                    lines += 1
                    cur = ch
                else:
                    cur += ch
            lines += 1
        need = lines * pt * 1.34 / 72
        if need > h + 0.02:
            bad += 1
            print(f"  [溢出] p{idx} 需 {need:.2f}in > 框 {h:.2f}in :: {text[:40]}")
    print(f"排版自检: {len(CHECKS)} 处文本框，{bad} 处疑似溢出")
except ImportError:
    print("排版自检跳过（无 Pillow）")
