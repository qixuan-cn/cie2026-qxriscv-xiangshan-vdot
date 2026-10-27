#!/usr/bin/env python3
"""NEMU 协同仿真驱动：用同一向量集驱动 NEMU 的 vdot.vv 实现并与金标准比对

流程：
  1) 读 tests/vectors/vdot_vectors.txt，筛出"合法 + 写回"用例
  2) 调用 tests/as/mini_asm.py 生成平面镜像（代码 + 数据）
  3) `nemu -b <img>` 运行，程序把每条 vdot 的 vd[31:0] 以 8 位十六进制打到 UART
  4) 按行与金标准（ref_vdot.py）比对；若提供 RTL 导出文件，再做 NEMU vs RTL 直接 diff

用法：
  python3 tests/run_nemu_cosim.py --nemu .tools/nemu/build/riscv64-nemu-interpreter
  python3 tests/run_nemu_cosim.py --rtl-export docs/phase3/rtl_export.txt
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "as"))
from mini_asm import build_image, UART_MMIO          # noqa: E402

VECTORS = os.path.join(HERE, "vectors", "vdot_vectors.txt")
HEXLINE = re.compile(r"^[0-9a-f]{8}$")


def load_cases(path):
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = line.split()
            cid, vl_zero, vm, sew, lmul = p[0], int(p[1]), int(p[2]), int(p[3]), int(p[4])
            vs1, vs2, exp = p[5], p[6], p[7]
            cases.append(dict(id=cid, vl_zero=vl_zero, vm=vm, sew=sew, lmul=lmul,
                              vs1=int(vs1, 16), vs2=int(vs2, 16), exp=int(exp, 16)))
    return cases


def select_runnable(cases):
    """程序固定用 e8/m1/vm=1 且 vl=VLMAX，故只包含合法且写回的用例。"""
    return [c for c in cases
            if c["vm"] == 1 and c["sew"] == 0 and c["lmul"] == 0 and c["vl_zero"] == 0]


def hex128_bytes(v):
    return (v & ((1 << 128) - 1)).to_bytes(16, "little")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nemu", default=".tools/nemu/build/riscv64-nemu-interpreter")
    ap.add_argument("--vectors", default=VECTORS)
    ap.add_argument("--image", default="build/nemu/vdot_cosim.bin")
    ap.add_argument("--max-instr", default="2000000")
    ap.add_argument("--rtl-export", default=None,
                    help="tb_vdot 导出的逐用例 128 bit 结果（每行一个）")
    ap.add_argument("--out", default="docs/phase3/01_nemu_cosim.log")
    args = ap.parse_args()

    cases = load_cases(args.vectors)
    run = select_runnable(cases)
    print(f"vectors total={len(cases)} runnable={len(run)}")

    img, sym, data_off = build_image(
        [(hex128_bytes(c["vs1"]), hex128_bytes(c["vs2"])) for c in run])
    os.makedirs(os.path.dirname(args.image), exist_ok=True)
    with open(args.image, "wb") as f:
        f.write(img)
    print(f"image  {args.image}: {len(img)} bytes, data_off={data_off}, "
          f"uart=0x{UART_MMIO:08x}")

    # 同步导出"写回用例"子集，保证 tb_vdot 的用例顺序与 NEMU 镜像完全一致
    subset = os.path.join(os.path.dirname(args.image), "cosim_vectors.txt")
    with open(subset, "w", encoding="utf-8") as f:
        f.write("# write-back subset for tb_vdot result export (same order as NEMU image)\n")
        for c in run:
            f.write(f"{c['id']} {c['vl_zero']} {c['vm']} {c['sew']} {c['lmul']} "
                    f"{c['vs1']:032x} {c['vs2']:032x} {c['exp']:032x} 1 0\n")
    print(f"subset {subset}: {len(run)} cases (feed to tb_vdot as argv[1])")

    cmd = [args.nemu, "-b", "-I", str(args.max_instr), args.image]
    print("run    " + " ".join(cmd))
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    raw = p.stdout.decode("utf-8", "replace")

    lines = [ln for ln in raw.splitlines() if HEXLINE.match(ln)]
    if "VDOT" not in raw:
        print("!! 未看到 UART banner 'VDOT'，UART 通路可能异常，原始输出如下：")
        print(raw[:2000])
    print(f"nemu exit={p.returncode} results={len(lines)}")

    fails = []
    for i, c in enumerate(run):
        if i >= len(lines):
            fails.append((i, c["id"], "缺输出", hex(c["exp"] & 0xFFFFFFFF), "-"))
            continue
        got = int(lines[i], 16)
        exp = c["exp"] & 0xFFFFFFFF
        if got != exp:
            fails.append((i, c["id"], "值不符", f"{exp:08x}", f"{got:08x}"))

    extra = max(0, len(lines) - len(run))

    rtl_fail = 0
    rtl_note = "未提供"
    if args.rtl_export:
        with open(args.rtl_export, encoding="utf-8") as f:
            rtl = [ln.strip().lower() for ln in f if ln.strip() and not ln.startswith("#")]
        n = min(len(rtl), len(lines))
        mism = [(i, rtl[i], lines[i]) for i in range(n) if rtl[i][-8:] != lines[i]]
        rtl_fail = len(mism) + abs(len(rtl) - len(lines))
        rtl_note = f"lines={len(rtl)}/nemu={len(lines)} mismatch={len(mism)}"
        for i, a, b in mism[:10]:
            print(f"RTL!=NEMU  #{i} rtl={a} nemu={b}")

    verdict = "PASS" if (not fails and extra == 0 and rtl_fail == 0
                         and p.returncode == 0) else "FAIL"
    report = []
    report.append("NEMU co-simulation (vdot.vv vs golden model)")
    report.append(f"nemu_binary      : {args.nemu}")
    report.append(f"image            : {args.image} ({len(img)} bytes)")
    report.append(f"uart_mmio        : 0x{UART_MMIO:08x}")
    report.append(f"cases_expected   : {len(run)}")
    report.append(f"cases_observed   : {len(lines)}")
    report.append(f"cases_fail       : {len(fails)}")
    report.append(f"cases_extra      : {extra}")
    report.append(f"nemu_exit_code   : {p.returncode}")
    report.append(f"rtl_vs_nemu      : {rtl_note}")
    if fails:
        for i, cid, why, exp, got in fails[:20]:
            report.append(f"  FAIL #{i} {cid} {why} exp={exp} got={got}")
    report.append(f"RESULT           : {verdict}")
    text = "\n".join(report) + "\n"

    if args.out:
        os.makedirs(os.path.dirname(args.out), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
