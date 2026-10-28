#!/usr/bin/env bash
# 全量一键回归：单元级 -> 严格 lint -> NEMU 自测 -> RTL↔NEMU 协同仿真 -> 基线
# 用法：bash scripts/run_all.sh
# 输出：逐步 PASS/FAIL 汇总；任一步失败即返回非 0。中间日志写入 build/regress/。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
OUT="build/regress"
mkdir -p "$OUT"

VERILATOR="$(command -v verilator || true)"
[ -x .tools/verilator/bin/verilator ] && VERILATOR=".tools/verilator/bin/verilator"
NEMU=".tools/nemu/build/riscv64-nemu-interpreter"

fails=0
run_step() {
  local name="$1"; shift
  echo "== $name =="
  if "$@" > "$OUT/${name// /_}.log" 2>&1; then
    echo "   PASS"
  else
    echo "   FAIL（日志 $OUT/${name// /_}.log）"
    fails=$((fails + 1))
  fi
}

# 1) 单元级：模型自检 + 向量 + C 模型 + 构建 + 1518 条 RTL 仿真 + 波形
run_step "unit" bash scripts/run_unit_tests.sh

# 2) 严格 lint（-Wall 下任何告警都会以非 0 退出）
if [ -n "$VERILATOR" ]; then
  run_step "lint" "$VERILATOR" --lint-only -Wall -Wno-DECLFILENAME \
    rtl/verilog/VDot.v --top-module VDot
else
  echo "== lint ==  SKIP（未找到 verilator）"
  fails=$((fails + 1))
fi

# 3) NEMU 协同仿真：先跑 NEMU 与金标准比对，再用同一用例顺序驱动 RTL 并 diff
if [ -x "$NEMU" ]; then
  run_step "nemu" python3 tests/run_nemu_cosim.py --out "$OUT/01_nemu.log"
  run_step "rtl_export" ./build/obj_dir/tb_vdot build/nemu/cosim_vectors.txt - "$OUT/rtl_export.txt"
  run_step "cosim" python3 tests/run_nemu_cosim.py \
    --rtl-export "$OUT/rtl_export.txt" --out "$OUT/02_cosim.log"
  # 4) 基线等价性与指令数对比
  run_step "baseline" python3 tests/run_nemu_baseline.py --out "$OUT/03_baseline.log"
else
  echo "== nemu ==  SKIP（未构建 $NEMU）"
  fails=$((fails + 1))
fi

echo "---------------------------------------------"
if [ "$fails" -eq 0 ]; then
  echo "ALL : PASS"
  exit 0
fi
echo "ALL : FAIL ($fails step(s))"
exit 1
