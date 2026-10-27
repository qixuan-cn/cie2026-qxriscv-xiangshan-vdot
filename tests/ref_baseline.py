#!/usr/bin/env python3
"""基线（已有 RVV 指令组合）的功能等价性模型

阶段三 3.2 要求"用 RISC-V 已有向量指令组合实现 vdot 相同功能作为基线"。
本脚本在元素粒度上模拟该指令序列，逐条复核其语义确实等价于 vdot，再对
tests/vectors/vdot_vectors.txt 的 1518 条向量做交叉比对。

基线指令序列（SEW=8 → 逐步加宽到 32 bit，再乘、再归约）：

    vsetvli  e8, m1
    vsext.vf2  v4, v1          # int8  -> int16 (m2)
    vsext.vf2  v6, v2
    vsetvli  e32, m4
    vsext.vf2  v8,  v4         # int16 -> int32 (m4)
    vsext.vf2  v12, v6
    vmul.vv    v16, v8, v12    # int32 乘
    vsetvli  e32, m1
    vredsum.vs v3, v16, v0     # 32 bit 求和（v0 需先清零）

说明：本模型只验证"指令语义等价"，不测量硬件时序。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ref_vdot import sext8  # noqa: E402

VLEN = 128
K = 8


def sext16(x: int) -> int:
    """vsext.vf2 int8 -> int16 后读回的值（无信息损失，仅示范加宽）。"""
    return sext8(x)


def baseline_dot(vs1_bytes, vs2_bytes) -> int:
    """按基线指令序列的元素语义计算点积（32 bit 有符号和）。"""
    # vsext.vf2 #1：int8 -> int16
    a16 = [sext16(b) for b in vs1_bytes[:K]]
    b16 = [sext16(b) for b in vs2_bytes[:K]]
    # vsext.vf2 #2：int16 -> int32
    a32 = [a for a in a16]
    b32 = [b for b in b16]
    # vmul.vv（SEW=32）：逐元素乘积
    prod = [a32[i] * b32[i] for i in range(K)]
    # vredsum.vs（SEW=32）：求和，v0 清零即普通和
    return sum(prod)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "tests/vectors/vdot_vectors.txt"
    total = 0
    checked = 0
    fail = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) != 10:
                continue
            cid = fields[0]
            vl_zero, vm, sew, lmul = (int(x) for x in fields[1:5])
            vs1_hex, vs2_hex, exp_hex, wr, ill = fields[5:10]
            total += 1
            # 只在合法且写回的用例上比较功能等价性
            if not (vm == 1 and sew == 0 and lmul == 0) or vl_zero == 1:
                continue
            v1 = int(vs1_hex, 16)
            v2 = int(vs2_hex, 16)
            b1 = [(v1 >> (8 * i)) & 0xFF for i in range(K)]
            b2 = [(v2 >> (8 * i)) & 0xFF for i in range(K)]
            got = baseline_dot(b1, b2) & 0xFFFFFFFF
            exp = int(exp_hex, 16) & 0xFFFFFFFF
            checked += 1
            if got != exp:
                fail += 1
                if fail <= 5:
                    print(f"FAIL {cid}: baseline={got:#010x} vdot={exp:#010x}")
    print(f"baseline_equiv: total={total} checked={checked} fail={fail}")
    print("RESULT:", "PASS" if fail == 0 else "FAIL")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
