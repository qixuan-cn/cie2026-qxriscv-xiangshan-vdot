#!/usr/bin/env python3
"""RiscvTpu V1 Verilator 回归。

流程：
  1. 用 ref_tpu.py（黄金模型）生成 DRAM/ACT/TMEM 预装载与 64 B 描述符；
  2. 把用例写成 tb_tpu 的文本命令文件；
  3. 跑 Verilator 模型；
  4. 逐位比对 TMEM/DRAM 结果与 ERROR_CODE。

用法：
    python3 tests/run_tpu_tests.py            # 增量构建 + 全量跑
    python3 tests/run_tpu_tests.py --rebuild  # 强制重新生成 Verilator 模型
    python3 tests/run_tpu_tests.py -k i4      # 只跑名字含 i4 的用例
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import ref_tpu as R  # noqa: E402

BUILD = os.path.join(ROOT, "build")
OBJ = os.path.join(BUILD, "obj_tpu")
CASES = os.path.join(BUILD, "cases")
SIM = os.path.join(OBJ, "tb_tpu")

VERILATOR = os.environ.get("VERILATOR") or os.path.join(
    ROOT, "..", ".tools", "verilator", "bin", "verilator")
SRCS = [
    "rtl/tpu_pkg.sv", "rtl/tpu_act.sv", "rtl/tpu_tmem.sv", "rtl/tpu_mxu.sv",
    "rtl/tpu_quant.sv", "rtl/tpu_copy.sv", "rtl/tpu_csr.sv", "rtl/tpu_top.sv",
    "sim/tpu_mem_model.sv", "sim/tpu_sim_top.sv",
]

# ---------------------------------------------------------------- 布局常量
DESC = 0x0200          # 描述符区（64 B 对齐；不能是 0，否则与复位后的 DQ_TAIL 相等）
WA = 0x1000            # 权重区
SC = 0x8000            # 权重 scale 行区
TM_C = 0x0100          # GEMM 输出 TMEM 基址（字地址）

CS_CTRL, CS_DQ_HEAD, CS_ICOUNT = 0x008, 0x014, 0x024
CS_ERROR, CS_RET_I8, CS_RET_I4, CS_RET_TN = 0x044, 0x034, 0x038, 0x03C
CS_DMA_LO = 0x048


def align(a: int, n: int) -> int:
    return (a + n - 1) // n * n


# ---------------------------------------------------------------- 构建
def build_sim(rebuild: bool) -> None:
    os.makedirs(CASES, exist_ok=True)
    if not rebuild and os.path.exists(SIM):
        # 源文件比可执行文件新则重建
        newest = max(os.path.getmtime(os.path.join(ROOT, f))
                     for f in SRCS + ["sim/tb_tpu.cpp"])
        if newest < os.path.getmtime(SIM):
            return
    if not os.path.exists(VERILATOR):
        sys.exit(f"找不到 verilator：{VERILATOR}")
    cmd = [VERILATOR, "-Wno-fatal", "--cc", "-O2", "--x-assign", "fast",
           "--x-initial", "fast", "-sv", "--top-module", "tpu_sim_top",
           "--Mdir", OBJ, "--exe", "sim/tb_tpu.cpp", "-o", "tb_tpu"] + SRCS
    print("[build]", " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["make", "-C", OBJ, "-f", "Vtpu_sim_top.mk", "-j8"],
                   check=True, stdout=subprocess.DEVNULL)


# ---------------------------------------------------------------- 描述符
def desc_words(op, flags=0, sbuf=0, prec=0, m=1, n=1, k=64, a=0, b=0, c=TM_C,
               lda=64, ldb=0, ldc=4, sc=0, nxt=0, k0=0, tmo=0, seq=0, srs=0):
    w0 = (op & 0xFF) | ((flags & 0xFF) << 8) | ((sbuf & 0xF) << 16) | ((prec & 3) << 20)
    return [w0, m, n, k, a, b, c, lda, ldb, ldc, sc, nxt, k0, tmo, seq, srs]


def x_command(addr: int, words) -> str:
    return "X %04x %s" % (addr, " ".join("%08x" % (w & 0xFFFFFFFF) for w in words))


# ---------------------------------------------------------------- 用例容器
class Case:
    def __init__(self, name: str):
        self.name = name
        self.lines: list[str] = []      # 激励（按顺序执行）
        self.checks: list[str] = []     # 期望（全部激励执行完再比对）
        self.expect_err = None

    def dram(self, addr: int, data: bytes):
        if data:
            self.lines.append("D %04x %s" % (addr, bytes(data).hex()))

    def act(self, idx: int, dat: bytes, scl: bytes):
        self.lines.append("A %03x %s %s" % (idx, bytes(dat).hex(), bytes(scl).hex()))

    def tmem(self, addr: int, val):
        self.lines.append("T %04x %08x" % (addr, R.bits(val) if not isinstance(val, int) else val))

    def watch_tmem(self, addr: int, val):
        self.checks.append("E_T %04x %08x" % (addr, R.bits(val)))

    def watch_dram(self, addr: int, data: bytes):
        self.checks.append("E_D %04x %s" % (addr, bytes(data).hex()))

    def watch_csr(self, addr: int, val: int):
        self.checks.append("E_CS %03x %08x" % (addr, val))

    def watch_err(self, code: int):
        self.checks.append("E_ERR %02x" % code)
        self.expect_err = code

    def expect_halt(self):
        self.checks.append("E_HALT")

    def run(self, maxc: int = 300000):
        self.lines.append("RUN %d" % maxc)

    def go(self, head: int = DESC, maxc: int = 300000):
        self.lines += ["HEAD %04x" % head, "DOOR", "RUN %d" % maxc]

    def text(self) -> str:
        return "\n".join(self.lines + self.checks) + "\n"


# ---------------------------------------------------------------- GEMM 用例
def gemm_case(name: str, m: int, n: int, k: int, prec: int, *,
              flags: int = 0, k0: int = 0, seed: int = 1,
              c_base: int = TM_C, acc_init=None) -> Case:
    """构造一个 GEMM 描述符 + 三份输入（权重/scale/激活），并给出期望值。"""
    rng = random.Random(seed)
    kb = (k + R.D - 1) // R.D
    W = R.W_SHIFT[prec]
    cs = Case(name)

    # ---- 权重与 scale
    wv = []
    for col in range(n):
        rows = []
        for b in range(kb):
            if prec == 0:
                rows.append([rng.randint(-128, 127) for _ in range(R.D)])
            elif prec == 1:
                rows.append([rng.randint(-8, 7) for _ in range(R.D)])
            else:
                rows.append(R.pack_tern_from_int(
                    [rng.choice([-1, 0, 1]) for _ in range(R.D)]))
        wv.append(rows)

    # scale 行按「绝对 block 号」编号：0..k0-1 是同一 tile 用不到的前缀（填默认值）
    dw = [[0] * k0 + [rng.randint(0, 3) for _ in range(kb)] for _ in range(n)]
    mg = [[[1, 1, 1, 1] for _ in range(k0)] +
          [[rng.choice([1, 2, 4]) for _ in range(4)] for _ in range(kb)]
          for _ in range(n)]
    Sw = [np.float32(2.0 ** rng.randint(-12, -8)) for _ in range(n)]

    # 权重块在 DRAM 里按 BLK_BYTES 紧凑排列（int8 64 B / int4 32 B / 三值 16 B）
    blk_bytes = R.BLK_BYTES[prec]
    lda = blk_bytes * kb
    blob = {}
    for col in range(n):
        for b in range(kb):
            raw = R.pack_row(wv[col][b], prec)
            blkb = raw + bytes(blk_bytes - len(raw))
            blob[(col, b)] = R.pad_block(blkb)
            cs.dram(WA + col * lda + b * blk_bytes, blkb)

    sc_rows = []
    for col in range(n):
        row = R.pack_wscale_row(Sw[col], prec, dw[col], mg[col])
        sc_rows.append(row)
    srs = align(len(sc_rows[0]), 8)
    for col in range(n):
        cs.dram(SC + col * srs, sc_rows[col])

    # ---- 激活与 ACT scale
    act = {}
    ascl = {}
    for j in range(m):
        for b in range(kb):
            lo, hi = (-128, 127) if prec != 1 else (-8, 7)
            vals = [rng.randint(lo, hi) for _ in range(R.D)]
            act[(j, b)] = R.pad_block(R.pack_row(vals, 0))
            Sa = np.float32(2.0 ** rng.randint(-6, -3))
            da = [rng.randint(0, 3) for _ in range(4)]
            ascl[(j, b)] = (Sa, da)
            cs.act(j * R.ACT_BLOCKS + b, act[(j, b)], R.pack_ascale_rec(Sa, da))

    # ---- ACC 旧值（先落定成矩阵，避免同一 (j,col) 被采样两次得到不同值）
    c_old = None
    if flags & R.F_ACC:
        if acc_init is None:
            acc_init = (lambda j, col: np.float32(rng.uniform(-4.0, 4.0)))
        matrix = {(j, col): np.float32(acc_init(j, col))
                  for j in range(m) for col in range(n)}
        c_old = lambda j, col: matrix[(j, col)]
        ldc = 4 * n
        for j in range(m):
            for col in range(n):
                cs.tmem(c_base + j * (ldc // 4) + col, c_old(j, col))

    ldc = 4 * n
    words = desc_words(0x01, flags=flags, prec=prec, m=m, n=n, k=k,
                       a=WA, b=0, c=c_base, lda=lda, ldb=0, ldc=ldc,
                       sc=SC, nxt=0, k0=k0, srs=srs)
    cs.lines.append(x_command(DESC, words))

    # ---- 期望值
    def wblk(col, b):
        return blob[(col, b)]

    def ablk(j, b):
        return act[(j, b)]

    def wscale(col, b):
        Sw_b, dw_b, mg_b = R.parse_wscale_row(sc_rows[col], prec, k0 + b)
        return dw_b, mg_b, np.float32(Sw_b)

    def ascale(j, b):
        Sa, da = ascl[(j, b)]
        return Sa, da

    exp = R.gemm_tile(m, n, k, prec, wblk, ablk, wscale, ascale,
                      flags=flags, c_old=c_old, k0=k0)
    for j in range(m):
        for col in range(n):
            cs.watch_tmem(c_base + j * (ldc // 4) + col, exp[j][col])

    cs.go(DESC)
    if not cs.expect_err:
        cs.watch_csr(CS_ICOUNT, 1)
        cs.watch_csr([CS_RET_I8, CS_RET_I4, CS_RET_TN][prec], 1)
        cs.expect_halt()
    return cs


# ---------------------------------------------------------------- COPY 用例
def copy_d2d_case(name: str, m: int, nbytes: int, ldb: int, ldc: int,
                  seed: int = 3) -> Case:
    rng = random.Random(seed)
    cs = Case(name)
    src = bytearray()
    for j in range(m):
        row = bytes(rng.randint(0, 255) for _ in range(nbytes))
        cs.dram(WA + j * ldb, row)
        src += row
    words = desc_words(0x02, m=m, a=WA, c=0xC000, lda=nbytes, ldb=ldb, ldc=ldc)
    cs.lines.append(x_command(DESC, words))
    exp = bytearray()
    for j in range(m):
        exp += src[j * nbytes:(j + 1) * nbytes]
    cs.watch_dram(0xC000, bytes(exp))
    cs.go(DESC)
    cs.expect_halt()
    return cs


def copy_t2d_case(name: str, m: int, nbytes: int, ldb: int = 16,
                  ldc: int = 64) -> Case:
    cs = Case(name)
    exp = bytearray()
    for j in range(m):
        row = bytearray()
        for w in range(16):
            v = 0x5A000000 + j * 0x1000 + w
            cs.tmem(0x0400 + j * ldb + w, v)
            row += v.to_bytes(4, "little")
        exp += row[:nbytes]
    words = desc_words(0x02, sbuf=1, m=m, a=0x0400, c=0xC000,
                       lda=nbytes, ldb=ldb, ldc=ldc)
    cs.lines.append(x_command(DESC, words))
    cs.watch_dram(0xC000, bytes(exp))
    cs.go(DESC)
    cs.expect_halt()
    return cs


# ---------------------------------------------------------------- QUANT 用例
def quant_then_gemm_case(name: str) -> Case:
    """QUANT 把 TMEM 中的 int8 行打包进 ACT 块，再用 GEMM 消费该块。"""
    rng = random.Random(11)
    cs = Case(name)
    vals = [rng.randint(-128, 127) for _ in range(64)]
    packed = R.pack_row(vals, 0)
    # TMEM 16 个字（每字 4 个 int8）+ 2 个 scale 字
    for w in range(16):
        v = int.from_bytes(packed[w * 4:w * 4 + 4], "little")
        cs.tmem(0x0000 + w, v)
    Sa = np.float32(2.0 ** -5)
    da = [1, 2, 3, 4]
    scl = R.pack_ascale_rec(Sa, da)
    cs.tmem(0x0010, int.from_bytes(scl[0:4], "little"))
    cs.tmem(0x0011, int.from_bytes(scl[4:8], "little"))

    qw = desc_words(0x03, m=1, a=0x0000, b=0x0010, c=5, lda=64)
    cs.lines.append(x_command(DESC, qw))
    cs.go(DESC, maxc=20000)

    # 用 ACT 块 5 做 1x1x64 的 GEMM
    wv = [rng.randint(-128, 127) for _ in range(64)]
    cs.dram(WA, R.pad_block(R.pack_row(wv, 0)))
    sc = R.pack_wscale_row(np.float32(2.0 ** -9), 0, [2])
    cs.dram(SC, sc)
    gw = desc_words(0x01, prec=0, m=1, n=1, k=64, a=WA, b=5, c=TM_C,
                    lda=64, ldb=0, ldc=4, sc=SC, srs=align(len(sc), 8))
    cs.lines.append(x_command(DESC + 0x40, gw))
    cs.go(DESC + 0x40, maxc=20000)

    ablk = R.pad_block(packed)
    wblk = R.pad_block(R.pack_row(wv, 0))
    exp = R.gemm_tile(1, 1, 64, 0, lambda c, b: wblk, lambda j, b: ablk,
                      lambda c, b: (2, [1, 1, 1, 1], np.float32(2.0 ** -9)),
                      lambda j, b: (Sa, da))
    cs.watch_tmem(TM_C, exp[0][0])
    cs.expect_halt()
    return cs


# ---------------------------------------------------------------- 错误用例
def err_case(name: str, mutate, code: int, head: int = DESC) -> Case:
    cs = Case(name)
    words = desc_words(0x01, prec=0, m=1, n=1, k=64, a=WA, b=0, c=TM_C,
                       lda=64, ldc=4, sc=SC, srs=8)
    mutate(words)
    cs.lines.append(x_command(DESC, words))
    cs.watch_err(code)
    cs.go(head, maxc=2000)
    return cs


def chain_case(name: str) -> Case:
    """NOP -> GEMM 链；检查 ICOUNT 只统计 GEMM。"""
    cs = Case(name)
    rng = random.Random(21)
    vals = [rng.randint(-128, 127) for _ in range(64)]
    cs.dram(WA, R.pad_block(R.pack_row(vals, 0)))
    sc = R.pack_wscale_row(np.float32(2.0 ** -9), 0, [1])
    cs.dram(SC, sc)
    for w in range(64):
        cs.act(w, b"\x01" * 64, R.pack_ascale_rec(np.float32(1.0), [0, 0, 0, 0]))
    gw = desc_words(0x01, prec=0, m=1, n=1, k=64, a=WA, b=1, c=TM_C,
                    lda=64, ldc=4, sc=SC, srs=align(len(sc), 8))
    nop = desc_words(0x05, nxt=DESC + 0x40)
    cs.lines.append(x_command(DESC, nop))
    cs.lines.append(x_command(DESC + 0x40, gw))
    cs.go(DESC)
    exp = R.gemm_tile(1, 1, 64, 0,
                      lambda c, b: R.pad_block(R.pack_row(vals, 0)),
                      lambda j, b: b"\x01" * 64,
                      lambda c, b: (1, [1, 1, 1, 1], np.float32(2.0 ** -9)),
                      lambda j, b: (np.float32(1.0), [0, 0, 0, 0]))
    cs.watch_tmem(TM_C, exp[0][0])
    cs.watch_csr(CS_ICOUNT, 1)
    cs.expect_halt()
    return cs


# ---------------------------------------------------------------- 用例集合
def all_cases() -> list[Case]:
    cases: list[Case] = []
    # --- int8
    cases.append(gemm_case("i8_1x1_k64", 1, 1, 64, 0, seed=1))
    cases.append(gemm_case("i8_4x8_k64", 4, 8, 64, 0, seed=2))
    cases.append(gemm_case("i8_8x8_k128", 8, 8, 128, 0, seed=3))
    cases.append(gemm_case("i8_2x3_k32", 2, 3, 32, 0, seed=4))
    cases.append(gemm_case("i8_3x5_k100", 3, 5, 100, 0, seed=5))
    cases.append(gemm_case("i8_64x2_k64", 64, 2, 64, 0, seed=6))
    cases.append(gemm_case("i8_relu_4x4", 4, 4, 64, 0, flags=R.F_RELU, seed=7))
    cases.append(gemm_case("i8_acc_4x4", 4, 4, 64, 0, flags=R.F_ACC, seed=8))
    cases.append(gemm_case("i8_acc_relu_4x4", 4, 4, 128, 0,
                           flags=R.F_ACC | R.F_RELU, seed=9))
    # --- int4
    cases.append(gemm_case("i4_4x8_k64", 4, 8, 64, 1, seed=11))
    cases.append(gemm_case("i4_8x8_k128", 8, 8, 128, 1, seed=12))
    cases.append(gemm_case("i4_3x5_k96", 3, 5, 96, 1, seed=13))
    # --- 三值
    cases.append(gemm_case("tern_4x8_k64", 4, 8, 64, 2, seed=21))
    cases.append(gemm_case("tern_8x8_k128", 8, 8, 128, 2, seed=22))
    cases.append(gemm_case("tern_3x5_k100", 3, 5, 100, 2, seed=23))
    # --- 多 jg（m > 8，验证每个 jg 的累加器清零）
    cases.append(gemm_case("i8_16x8_k64", 16, 8, 64, 0, seed=101))
    cases.append(gemm_case("i8_64x8_k128", 64, 8, 128, 0, seed=102))
    # --- 多 block（int4 / 三值 3 个 block）
    cases.append(gemm_case("i4_8x8_k192", 8, 8, 192, 1, seed=103))
    cases.append(gemm_case("tern_8x8_k192", 8, 8, 192, 2, seed=104))
    # --- 末块不足 64 元素
    cases.append(gemm_case("i4_2x4_k70", 2, 4, 70, 1, seed=105))
    cases.append(gemm_case("tern_2x4_k70", 2, 4, 70, 2, seed=106))
    # --- ACC 跨 jg
    cases.append(gemm_case("i8_acc_16x8_k128", 16, 8, 128, 0,
                           flags=R.F_ACC, seed=107))
    # --- k0 分块（尺度行绝对块号）
    cases.append(gemm_case("i8_k0_1_k64", 2, 2, 64, 0, k0=1, seed=31))
    # --- COPY
    cases.append(copy_d2d_case("copy_d2d_3x64_ldb128", 3, 64, 128, 64))
    cases.append(copy_d2d_case("copy_d2d_2x100_ldb128", 2, 100, 128, 100))
    cases.append(copy_t2d_case("copy_t2d_2x64", 2, 64))
    # --- QUANT -> GEMM
    cases.append(quant_then_gemm_case("quant_then_gemm"))
    # --- 链
    cases.append(chain_case("chain_nop_gemm"))
    # --- 错误
    cases.append(err_case("err_align", lambda w: None, 0x01, head=DESC + 8))
    cases.append(err_case("err_op", lambda w: w.__setitem__(0, 0x10), 0x03))
    cases.append(err_case("err_flags_pair",
                          lambda w: w.__setitem__(0, w[0] | (1 << 14)), 0x04))
    cases.append(err_case("err_flags_w0",
                          lambda w: w.__setitem__(0, w[0] | (1 << 22)), 0x04))
    # V1 未实现的 flags：RMAX / ASCALE / TRANSPOSE_B 必须报 DESC_FLAGS
    for bit in (1, 2, 4):
        cases.append(err_case("err_flags_bit%d" % bit,
                              lambda w, b=bit: w.__setitem__(0, w[0] | (1 << (8 + b))),
                              0x04))
    cases.append(err_case("err_sbuf", lambda w: w.__setitem__(0, w[0] | (2 << 16)), 0x0B))
    cases.append(err_case("err_shape_m", lambda w: w.__setitem__(1, 65), 0x05))
    cases.append(err_case("err_shape_n", lambda w: w.__setitem__(2, 0), 0x05))
    cases.append(err_case("err_shape_k", lambda w: w.__setitem__(3, 2049), 0x05))
    cases.append(err_case("err_align_a", lambda w: w.__setitem__(4, WA + 8), 0x06))
    cases.append(err_case("err_range_c", lambda w: w.__setitem__(6, 0x4000), 0x02))
    return cases


# ---------------------------------------------------------------- 执行
def run_one(cs: Case) -> tuple[bool, str]:
    path = os.path.join(CASES, cs.name + ".case")
    with open(path, "w") as fh:
        fh.write(cs.text())
    r = subprocess.run([SIM, path], capture_output=True, text=True)
    out = r.stdout + r.stderr
    return (r.returncode == 0 and "fails=0" in out), out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("-k", dest="pat", default="")
    args = ap.parse_args()

    build_sim(args.rebuild)
    cases = [c for c in all_cases() if args.pat in c.name]

    logpath = os.path.join(BUILD, "regress.log")
    npass = 0
    with open(logpath, "w") as log:
        for cs in cases:
            ok, out = run_one(cs)
            log.write(f"===== {cs.name} : {'PASS' if ok else 'FAIL'}\n{out}\n")
            if ok:
                npass += 1
                print(f"  PASS {cs.name}")
            else:
                print(f"  FAIL {cs.name}")
                for ln in out.strip().splitlines()[:12]:
                    print("       " + ln)

    print(f"\nRiscvTpu 回归：{npass}/{len(cases)} 通过，日志 {logpath}")
    return 0 if npass == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main())
