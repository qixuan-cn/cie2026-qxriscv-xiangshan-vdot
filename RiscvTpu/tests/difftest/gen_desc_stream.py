#!/usr/bin/env python3
"""生成设备级 difftest 的「描述符流 + 初始镜像」（T2）。

复用 tests/ref_tpu.py 的打包函数（权重/scale/ACT 记录），但**不**生成期望值：
真值由 sim/difftest/tpu_ref_model.c 独立计算，DUT 与之逐位比对。

输出目录下每个 *.stream 对应一次门铃：
    IMAGE <addr> <hex>     预装载 DRAM
    ACT   <idx> <dat> <scl> 预装载 ACT 块
    TMEM  <addr> <val>     预装载 TMEM 字
    HEAD  <addr>           写 DQ_HEAD
    DOOR                   门铃
    EXPECT_ERR <code>      生成端自检（可选）

用法：
    python3 tests/difftest/gen_desc_stream.py [--out DIR]
"""

from __future__ import annotations

import argparse
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests"))
import ref_tpu as R  # noqa: E402

DESC = 0x0200
WA = 0x1000
SC = 0x8000
TM_C = 0x0100


def align(a: int, n: int) -> int:
    return (a + n - 1) // n * n


def desc_words(op, flags=0, sbuf=0, prec=0, m=1, n=1, k=64, a=0, b=0, c=TM_C,
               lda=64, ldb=0, ldc=4, sc=0, nxt=0, k0=0, tmo=0, seq=0, srs=0):
    w0 = (op & 0xFF) | ((flags & 0xFF) << 8) | ((sbuf & 0xF) << 16) | ((prec & 3) << 20)
    return [w0, m, n, k, a, b, c, lda, ldb, ldc, sc, nxt, k0, tmo, seq, srs]


def words_bytes(words) -> bytes:
    out = bytearray()
    for w in words:
        out += (int(w) & 0xFFFFFFFF).to_bytes(4, "little")
    return bytes(out)


class Stream:
    def __init__(self, name: str):
        self.name = name
        self.image: list[tuple[int, bytes]] = []
        self.acts: list[tuple[int, bytes, bytes]] = []
        self.tmem: list[tuple[int, int]] = []
        self.descs: list[tuple[int, list[int]]] = []
        self.head = DESC
        self.expect_err = None

    def dram(self, addr: int, data: bytes):
        if data:
            self.image.append((addr, bytes(data)))

    def act(self, idx: int, dat: bytes, scl: bytes):
        self.acts.append((idx, bytes(dat), bytes(scl)))

    def tmem_w(self, addr: int, val):
        self.tmem.append((addr, R.bits(val) if not isinstance(val, int) else val & 0xFFFFFFFF))

    def desc(self, addr: int, words):
        self.descs.append((addr, list(words)))

    def text(self) -> str:
        lines: list[str] = []
        for addr, data in self.image:
            lines.append("IMAGE %04x %s" % (addr, data.hex()))
        for idx, dat, scl in self.acts:
            lines.append("ACT %03x %s %s" % (idx, dat.hex(), scl.hex()))
        for addr, val in self.tmem:
            lines.append("TMEM %04x %08x" % (addr, val))
        for addr, words in self.descs:
            lines.append("IMAGE %04x %s" % (addr, words_bytes(words).hex()))
        lines.append("HEAD %04x" % self.head)
        lines.append("DOOR")
        if self.expect_err is not None:
            lines.append("EXPECT_ERR %02x" % self.expect_err)
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- GEMM
def gemm_stream(name: str, m: int, n: int, k: int, prec: int, *,
                flags: int = 0, k0: int = 0, seed: int = 1,
                c_base: int = TM_C) -> Stream:
    rng = random.Random(seed)
    kb = (k + R.D - 1) // R.D
    cs = Stream(name)

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

    dw = [[0] * k0 + [rng.randint(0, 3) for _ in range(kb)] for _ in range(n)]
    mg = [[[1, 1, 1, 1] for _ in range(k0)] +
          [[rng.choice([1, 2, 4]) for _ in range(4)] for _ in range(kb)]
          for _ in range(n)]
    Sw = [np.float32(2.0 ** rng.randint(-12, -8)) for _ in range(n)]

    blk_bytes = R.BLK_BYTES[prec]
    lda = blk_bytes * kb
    for col in range(n):
        for b in range(kb):
            raw = R.pack_row(wv[col][b], prec)
            cs.dram(WA + col * lda + b * blk_bytes, raw + bytes(blk_bytes - len(raw)))

    sc_rows = []
    for col in range(n):
        row = R.pack_wscale_row(Sw[col], prec, dw[col], mg[col])
        sc_rows.append(row)
    srs = align(len(sc_rows[0]), 8)
    for col in range(n):
        cs.dram(SC + col * srs, sc_rows[col])

    for j in range(m):
        for b in range(kb):
            lo, hi = (-128, 127) if prec != 1 else (-8, 7)
            vals = [rng.randint(lo, hi) for _ in range(R.D)]
            dat = R.pad_block(R.pack_row(vals, 0))
            Sa = np.float32(2.0 ** rng.randint(-6, -3))
            da = [rng.randint(0, 3) for _ in range(4)]
            cs.act(j * R.ACT_BLOCKS + b, dat, R.pack_ascale_rec(Sa, da))

    ldc = 4 * n
    if flags & R.F_ACC:
        for j in range(m):
            for col in range(n):
                cs.tmem_w(c_base + j * (ldc // 4) + col,
                          np.float32(rng.uniform(-4.0, 4.0)))

    cs.desc(DESC, desc_words(0x01, flags=flags, prec=prec, m=m, n=n, k=k,
                             a=WA, b=0, c=c_base, lda=lda, ldb=0, ldc=ldc,
                             sc=SC, nxt=0, k0=k0, srs=srs))
    return cs


# ---------------------------------------------------------------- COPY
def copy_d2d_stream(name: str, m: int, nbytes: int, ldb: int, ldc: int,
                    seed: int = 3) -> Stream:
    rng = random.Random(seed)
    cs = Stream(name)
    for j in range(m):
        cs.dram(WA + j * ldb, bytes(rng.randint(0, 255) for _ in range(nbytes)))
    cs.desc(DESC, desc_words(0x02, m=m, a=WA, c=0xC000, lda=nbytes,
                             ldb=ldb, ldc=ldc))
    return cs


def copy_t2d_stream(name: str, m: int, nbytes: int, ldb: int = 16,
                    ldc: int = 64) -> Stream:
    cs = Stream(name)
    for j in range(m):
        for w in range(16):
            cs.tmem_w(0x0400 + j * ldb + w, 0x5A000000 + j * 0x1000 + w)
    cs.desc(DESC, desc_words(0x02, sbuf=1, m=m, a=0x0400, c=0xC000,
                             lda=nbytes, ldb=ldb, ldc=ldc))
    return cs


# ---------------------------------------------------------------- QUANT -> GEMM
def quant_then_gemm_stream(name: str) -> Stream:
    rng = random.Random(11)
    cs = Stream(name)
    vals = [rng.randint(-128, 127) for _ in range(64)]
    packed = R.pack_row(vals, 0)
    for w in range(16):
        cs.tmem_w(0x0000 + w, int.from_bytes(packed[w * 4:w * 4 + 4], "little"))
    Sa = np.float32(2.0 ** -5)
    da = [1, 2, 3, 4]
    scl = R.pack_ascale_rec(Sa, da)
    cs.tmem_w(0x0010, int.from_bytes(scl[0:4], "little"))
    cs.tmem_w(0x0011, int.from_bytes(scl[4:8], "little"))

    qw = desc_words(0x03, m=1, a=0x0000, b=0x0010, c=5, lda=64,
                    nxt=DESC + 0x40)
    cs.desc(DESC, qw)

    wv = [rng.randint(-128, 127) for _ in range(64)]
    cs.dram(WA, R.pad_block(R.pack_row(wv, 0)))
    sc = R.pack_wscale_row(np.float32(2.0 ** -9), 0, [2])
    cs.dram(SC, sc)
    gw = desc_words(0x01, prec=0, m=1, n=1, k=64, a=WA, b=5, c=TM_C,
                    lda=64, ldb=0, ldc=4, sc=SC, srs=align(len(sc), 8))
    cs.desc(DESC + 0x40, gw)
    return cs


# ---------------------------------------------------------------- 链
def chain_nop_gemm_stream(name: str) -> Stream:
    rng = random.Random(21)
    cs = Stream(name)
    vals = [rng.randint(-128, 127) for _ in range(64)]
    cs.dram(WA, R.pad_block(R.pack_row(vals, 0)))
    sc = R.pack_wscale_row(np.float32(2.0 ** -9), 0, [1])
    cs.dram(SC, sc)
    for w in range(64):
        cs.act(w, b"\x01" * 64, R.pack_ascale_rec(np.float32(1.0), [0, 0, 0, 0]))
    cs.desc(DESC, desc_words(0x05, nxt=DESC + 0x40))
    cs.desc(DESC + 0x40, desc_words(0x01, prec=0, m=1, n=1, k=64, a=WA, b=1,
                                    c=TM_C, lda=64, ldc=4, sc=SC,
                                    srs=align(len(sc), 8)))
    return cs


# ---------------------------------------------------------------- 错误
def err_stream(name: str, mutate, code: int, head: int = DESC) -> Stream:
    cs = Stream(name)
    words = desc_words(0x01, prec=0, m=1, n=1, k=64, a=WA, b=0, c=TM_C,
                       lda=64, ldc=4, sc=SC, srs=8)
    mutate(words)
    cs.desc(DESC, words)
    cs.head = head
    cs.expect_err = code
    return cs


def all_streams() -> list[Stream]:
    ss: list[Stream] = []
    # --- 单描述符 GEMM，三精度
    ss.append(gemm_stream("i8_1x1_k64", 1, 1, 64, 0, seed=1))
    ss.append(gemm_stream("i8_4x8_k64", 4, 8, 64, 0, seed=2))
    ss.append(gemm_stream("i8_8x8_k128", 8, 8, 128, 0, seed=3))
    ss.append(gemm_stream("i8_relu_4x4", 4, 4, 64, 0, flags=R.F_RELU, seed=7))
    ss.append(gemm_stream("i8_acc_4x4", 4, 4, 64, 0, flags=R.F_ACC, seed=8))
    ss.append(gemm_stream("i8_acc_relu_4x4", 4, 4, 128, 0,
                          flags=R.F_ACC | R.F_RELU, seed=9))
    ss.append(gemm_stream("i4_4x8_k64", 4, 8, 64, 1, seed=11))
    ss.append(gemm_stream("i4_8x8_k128", 8, 8, 128, 1, seed=12))
    ss.append(gemm_stream("tern_4x8_k64", 4, 8, 64, 2, seed=21))
    ss.append(gemm_stream("tern_8x8_k128", 8, 8, 128, 2, seed=22))
    ss.append(gemm_stream("i8_16x8_k64", 16, 8, 64, 0, seed=101))
    ss.append(gemm_stream("i4_2x4_k70", 2, 4, 70, 1, seed=105))
    ss.append(gemm_stream("tern_2x4_k70", 2, 4, 70, 2, seed=106))
    ss.append(gemm_stream("i8_k0_1_k64", 2, 2, 64, 0, k0=1, seed=31))
    # --- COPY
    ss.append(copy_d2d_stream("copy_d2d_3x64", 3, 64, 128, 64))
    ss.append(copy_d2d_stream("copy_d2d_2x100", 2, 100, 128, 100))
    ss.append(copy_t2d_stream("copy_t2d_2x64", 2, 64))
    # --- QUANT -> GEMM
    ss.append(quant_then_gemm_stream("quant_then_gemm"))
    # --- 链
    ss.append(chain_nop_gemm_stream("chain_nop_gemm"))
    # --- 错误码（V1 可达的检查期错误）
    ss.append(err_stream("err_align", lambda w: None, 0x01, head=DESC + 8))
    ss.append(err_stream("err_op", lambda w: w.__setitem__(0, 0x10), 0x03))
    ss.append(err_stream("err_flags_pair",
                         lambda w: w.__setitem__(0, w[0] | (1 << 14)), 0x04))
    ss.append(err_stream("err_flags_bit1",
                         lambda w: w.__setitem__(0, w[0] | (1 << 9)), 0x04))
    ss.append(err_stream("err_sbuf", lambda w: w.__setitem__(0, w[0] | (2 << 16)), 0x0B))
    ss.append(err_stream("err_shape_m", lambda w: w.__setitem__(1, 65), 0x05))
    ss.append(err_stream("err_shape_n", lambda w: w.__setitem__(2, 0), 0x05))
    ss.append(err_stream("err_align_a", lambda w: w.__setitem__(4, WA + 8), 0x06))
    ss.append(err_stream("err_range_c", lambda w: w.__setitem__(6, 0x4000), 0x02))
    return ss


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "difftest", "streams"))
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    streams = all_streams()
    for cs in streams:
        path = os.path.join(args.out, cs.name + ".stream")
        with open(path, "w") as fh:
            fh.write(cs.text())
    print("生成 %d 个 difftest stream -> %s" % (len(streams), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
