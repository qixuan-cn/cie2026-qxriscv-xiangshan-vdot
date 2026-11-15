"""RiscvTpu V1 黄金模型（tile 级）。

契约（与 docs/03_功能设计规格.md 一致）：
  * 整数域精确：每个 group 16 个元素的乘积和是精确整数，按 2^sh 移位后累加；
  * 每个输出只做 3 次 fp32 舍入乘（i2f(acc)*Sw、Sa*2^-W、两者相乘）；
  * RELU / ACC 在 fp32 域处理；
  * 零结果统一规范化为 +0（RTL 与模型同款，见 f_mul/f_add）。

本文件同时提供：
  * 三种精度的权重/激活打包（与 vdot / vdot4x2 / vtdot 的字节内次序一致）；
  * openTPU 兼容的 scale 行打包（WF=0 / WF=1）与 ACT scale 记录打包；
  * 一个纯 Python 的 DRAM/ACT 镜像类，用于生成 RTL 测试向量。
"""

from __future__ import annotations

import numpy as np

D = 64                    # MXU 深度（每 block 元素数）
GROUP = D // 4            # 每个 group 的元素数 = 16
NGROUP = 4                # 每个 block 的 group 数
ACT_ROWS = 64
ACT_BLOCKS = 32
TMEM_WORDS = 16384
MCOLS = 8

W_SHIFT = {0: 16, 1: 11, 2: 11}          # prec -> W
BLK_BYTES = {0: 64, 1: 32, 2: 16}        # 权重 block 的字节数
WELEM_BYTES = {0: 1, 1: 0.5, 2: 0.25}    # 每个元素占的字节

F_ACC = 1 << 0
F_RMAX = 1 << 1
F_ASCALE = 1 << 2
F_RELU = 1 << 3
F_TRANSPOSE_B = 1 << 4
F_ZERO_PAD = 1 << 5
F_PAIR = 1 << 6


# ---------------------------------------------------------------- 基础数值
def f32(x) -> np.float32:
    return np.float32(x)


def bits(x) -> int:
    return int(np.float32(x).view(np.uint32))


def from_bits(b: int) -> np.float32:
    return np.uint32(b & 0xFFFFFFFF).view(np.float32)


def _canon_zero(r):
    """零结果统一为 +0（RTL 同款）。"""
    return np.float32(0.0) if r == 0 else r


_SUB_MIN = np.float32(2.0 ** -126)


def _ftz(x):
    """FTZ：次正规与零统一为 +0（RTL f_mul/f_add 的语义）。"""
    if isinstance(x, np.ndarray):
        x = np.float32(x.reshape(-1)[0]) if x.size else np.float32(0.0)
    else:
        x = np.float32(x)
    if x == 0:
        return np.float32(0.0)
    if np.abs(x) < _SUB_MIN:
        return np.float32(0.0)
    return x


def fp_mul(a, b) -> np.float32:
    with np.errstate(over="ignore", under="ignore"):
        return _ftz(np.float32(a) * np.float32(b))


def fp_add(a, b) -> np.float32:
    with np.errstate(over="ignore", under="ignore"):
        return _ftz(np.float32(a) + np.float32(b))


def scale_pow2(x, e: int) -> np.float32:
    """精确乘 2^e（对应 RTL 的 f_mul(x, 2^e)，2^e 可精确表示）。"""
    return np.float32(np.ldexp(np.float32(x), e))


def sext4(n: int) -> int:
    return n - 16 if n & 0x8 else n


def sext8(b: int) -> int:
    return b - 256 if b & 0x80 else b


def w_elem(blk, i: int, prec: int) -> int:
    """从权重块里取第 i 个元素（int8 / int4 / 三值码）。blk 为 bytes/bytearray。"""
    if prec == 0:
        return sext8(blk[i])
    if prec == 1:
        b = blk[i // 2]
        return sext4((b >> (4 * (i % 2))) & 0xF)
    b = blk[i // 4]
    return (b >> (2 * (i % 4))) & 0x3


def a_elem(blk, i: int) -> int:
    return sext8(blk[i])


# ---------------------------------------------------------------- 打包
def pack_row(vals, prec: int) -> bytes:
    """把一行整数值按 vdot 的字节内次序打包。长度 = D 个元素的紧凑字节数。"""
    out = bytearray()
    if prec == 0:
        for v in vals:
            out.append(int(v) & 0xFF)
    elif prec == 1:
        for i in range(0, len(vals), 2):
            lo = int(vals[i]) & 0xF
            hi = (int(vals[i + 1]) & 0xF) if i + 1 < len(vals) else 0
            out.append(lo | (hi << 4))
    else:
        for i in range(0, len(vals), 4):
            b = 0
            for t in range(4):
                code = 0 if i + t >= len(vals) else int(vals[i + t]) & 0x3
                b |= code << (2 * t)
            out.append(b)
    return bytes(out)


def pad_block(packed: bytes) -> bytes:
    """补齐到 64 B（RTL 权重/激活块端口总是 64 B）。"""
    return bytes(packed) + bytes(64 - len(packed))


def pack_tern_from_int(vals) -> list:
    """{-1,0,+1} -> 码（01=+1, 10=-1, 00/11=0）。"""
    return [1 if v > 0 else (2 if v < 0 else 0) for v in vals]


def quant_vals(x, prec: int, sw=None):
    """行量化：返回 (Sw, code 列表)。code 已按 prec 的域裁剪。

    int8: round(x/Sw) 到 -128..127；int4: -8..7；三值: sign，阈值 0.5*Sw。
    """
    arr = np.asarray(x, dtype=np.float32)
    amax = float(np.max(np.abs(arr))) if arr.size else 0.0
    if amax == 0.0:
        return np.float32(0.0), [0] * len(arr)
    if sw is None:
        div = 127.0 if prec == 0 else (7.0 if prec == 1 else 1.0)
        sw = np.float32(amax / div)
    q = []
    for v in arr:
        if prec == 2:
            q.append(0 if abs(float(v)) < 0.5 * float(sw) else (1 if v > 0 else 2))
        else:
            r = np.float32(np.float32(v) / np.float32(sw))
            iv = int(np.rint(r))
            lo, hi = (-128, 127) if prec == 0 else (-8, 7)
            q.append(max(lo, min(hi, iv)))
    return np.float32(sw), q


# ---------------------------------------------------------------- scale 行
def pack_wscale_row_int8(Sw, dw_list) -> bytes:
    """openTPU WF=0：Sw(4B) + 每 block 1 字节 dw。"""
    out = bytearray(np.float32(Sw).tobytes())
    for dw in dw_list:
        out.append(int(dw) & 0xFF)
    return bytes(out)


def pack_wscale_row_wf1(Sw, dw_list, m_list) -> bytes:
    """openTPU WF=1（int4/三值）：Sw(4B) + 保留 4B + 每 block 1 字。

    字的 bits[7:0] = dw，group b 的 m = bits[8+6b +: 6]。
    """
    out = bytearray(np.float32(Sw).tobytes())
    out += b"\x00\x00\x00\x00"
    for k, dw in enumerate(dw_list):
        w = int(dw) & 0xFF
        for b in range(4):
            mm = int(m_list[k][b]) & 0x3F
            w |= mm << (8 + 6 * b)
        out += int(w).to_bytes(4, "little")
    return bytes(out)


def pack_wscale_row(Sw, prec, dw_list, m_list=None) -> bytes:
    if prec == 0:
        return pack_wscale_row_int8(Sw, dw_list)
    return pack_wscale_row_wf1(Sw, dw_list, m_list)


def pack_ascale_rec(Sa, da_list) -> bytes:
    """ACT 每 (row, block) 8B：Sa(fp32) + 4×da。"""
    return bytes(np.float32(Sa).tobytes()) + bytes(int(d) & 0xFF for d in da_list)


def parse_wscale_row(blob: bytes, prec: int, k: int):
    """从 scale 行里取出 block k 的 (Sw, dw, m[4])。k 已含 k0。"""
    Sw = np.frombuffer(blob[0:4], dtype=np.float32)[0]
    if prec == 0:
        return Sw, blob[4 + k], [1, 1, 1, 1]
    w = int.from_bytes(blob[8 + 4 * k: 12 + 4 * k], "little")
    return Sw, w & 0xFF, [(w >> (8 + 6 * b)) & 0x3F for b in range(4)]


# ---------------------------------------------------------------- tile GEMM
def group_sum(blk_a, blk_w, prec: int, g: int, cnt: int = D) -> int:
    """块内第 g 个 group 的精确点积；cnt 为该块有效元素数（末块尾部按 0 处理）。"""
    s = 0
    base = g * GROUP
    for i in range(base, min(base + GROUP, cnt)):
        a = a_elem(blk_a, i)
        w = w_elem(blk_w, i, prec)
        if prec == 2:
            s += a if w == 1 else (-a if w == 2 else 0)
        else:
            s += a * w
    return s


def i2f(v: int) -> np.float32:
    return _ftz(np.float32(v))


def epilogue(acc: int, Sw, Sa, W: int, c_old=None, flags: int = 0) -> np.float32:
    """3 次舍入乘 + RELU + ACC，与 RTL epilogue 逐位一致。"""
    t1 = fp_mul(i2f(acc), Sw)
    t2 = fp_mul(Sa, np.float32(2.0 ** -W))
    y = fp_mul(t1, t2)
    if (flags & F_RELU) and y < 0:
        y = np.float32(0.0)
    if (flags & F_ACC) and c_old is not None:
        y = fp_add(c_old, y)
    return np.float32(y)


def gemm_tile(m: int, n: int, k: int, prec: int,
              wblk, ablk, wscale, ascale,
              flags: int = 0, c_old=None, k0: int = 0):
    """计算一个 GEMM tile，返回 (m, n) 的 fp32 结果矩阵。

    wblk(col, b)     -> 权重块字节（64 B，pad 过）；b 为 tile 内相对块号
    ablk(j, b)       -> 激活块字节（64 B，int8）
    wscale(col, b)   -> (dw, m[4], Sw)；b 为相对块号，内部按 k0+b 解析
    ascale(j, b)     -> (Sa, da[4])
    c_old(j, col)    -> 旧值（ACC 用）
    """
    W = W_SHIFT[prec]
    kb = (k + D - 1) // D
    out = [[np.float32(0.0)] * n for _ in range(m)]
    for j in range(m):
        for col in range(n):
            acc = 0
            Sw = wscale(col, 0)[2]
            Sa = ascale(j, 0)[0]
            for b in range(kb):
                wb = wblk(col, b)
                ab = ablk(j, b)
                dw, mg, _ = wscale(col, b)
                _, da = ascale(j, b)
                cnt = min(D, k - b * D)
                for g in range(NGROUP):
                    sh = W - int(dw) - int(da[g])
                    if sh < 0:
                        continue
                    c = group_sum(ab, wb, prec, g, cnt)
                    mul = 1 if prec == 0 else int(mg[g])
                    acc += (mul << sh) * c
            assert -(1 << 47) < acc < (1 << 47), f"acc overflow: {acc}"
            old = None if c_old is None else c_old(j, col)
            out[j][col] = epilogue(acc, Sw, Sa, W, old, flags)
    return out


# ---------------------------------------------------------------- 存储器镜像
class TpuImage:
    """DRAM 字节镜像 + ACT 块镜像 + TMEM 字镜像。"""

    def __init__(self, dram_bytes: int = 1 << 20):
        self.dram = bytearray(dram_bytes)
        self.act_dat = {}
        self.act_scl = {}
        self.tmem = {}

    # --- DRAM
    def put(self, addr: int, data: bytes):
        addr &= (len(self.dram) - 1)
        self.dram[addr:addr + len(data)] = data

    def get(self, addr: int, n: int) -> bytes:
        addr &= (len(self.dram) - 1)
        return bytes(self.dram[addr:addr + n])

    # --- ACT
    def put_act(self, idx: int, data: bytes, rec: bytes):
        self.act_dat[idx] = bytes(data) + bytes(64 - len(data))
        self.act_scl[idx] = rec

    # --- TMEM
    def put_tmem(self, waddr: int, val):
        self.tmem[waddr] = bits(val) if not isinstance(val, int) else val & 0xFFFFFFFF

    def get_tmem(self, waddr: int):
        return self.tmem.get(waddr, 0)
