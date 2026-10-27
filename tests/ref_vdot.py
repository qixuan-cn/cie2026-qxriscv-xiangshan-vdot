#!/usr/bin/env python3
"""vdot.vv 参考模型（Golden Model）

与 IPD/01 设计规格书 §2.3 的规范性伪代码逐行对应：
  S = sum_{i=0..7} sext8(vs1[i]) * sext8(vs2[i])
  vd[31:0] = S (二补码)，vd[127:32] = 0

本模型是测试期望值的唯一来源，不依赖 RTL 输出。
"""
from typing import List

VLEN = 128
K = 8
OUT_W = 32


def sext8(x: int) -> int:
    """把 0..255 解释为 int8。"""
    x &= 0xFF
    return x - 0x100 if x & 0x80 else x


def ref_vdot(vs1_bytes: List[int], vs2_bytes: List[int]) -> int:
    """返回 vd 的 128 bit 无符号整数值。

    vs1_bytes / vs2_bytes：长度 >= 8 的字节列表，lane i 为第 i 个字节。
    高 64 bit 不参与运算。
    """
    s = 0
    for i in range(K):
        s += sext8(vs1_bytes[i]) * sext8(vs2_bytes[i])
    # 32 bit 二补码截断 + 高位通道清零
    return s & ((1 << OUT_W) - 1)


def ref_vdot_signed(vs1_bytes: List[int], vs2_bytes: List[int]) -> int:
    """返回有符号 32 bit 结果（便于打印十进制）。"""
    v = ref_vdot(vs1_bytes, vs2_bytes)
    return v - (1 << OUT_W) if v & (1 << (OUT_W - 1)) else v


def bytes_from_int(value: int, nbytes: int = VLEN // 8) -> List[int]:
    """128 bit 整数（lane0 在最低字节）转字节列表。"""
    return [(value >> (8 * i)) & 0xFF for i in range(nbytes)]


def int_from_bytes(bs: List[int]) -> int:
    v = 0
    for i, b in enumerate(bs):
        v |= (b & 0xFF) << (8 * i)
    return v


if __name__ == "__main__":
    # 自检：闭式边界结论（与设计规格书 §2.6 一致）
    assert ref_vdot([127] * 8, [127] * 8) == 0x0001F808, "max positive"
    assert ref_vdot([0x80] * 8, [0x80] * 8) == 0x00020000, "19-bit boundary"
    assert ref_vdot([0x80] * 8, [0x7F] * 8) == 0xFFFE0400, "max negative"
    assert ref_vdot([0] * 8, [0] * 8) == 0, "zero"
    assert ref_vdot([1, 255] * 4, [1] * 8) == 0, "cancellation"
    print("ref_vdot self-check: PASS")
