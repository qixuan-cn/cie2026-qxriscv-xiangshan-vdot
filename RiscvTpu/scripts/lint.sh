#!/usr/bin/env bash
#=============================================================================
# lint.sh -- RiscvTpu RTL 静态检查（Verilator -Wall，有告警即失败）
#
# 用法： scripts/lint.sh
# 依赖： VERILATOR 环境变量或 ../.tools/verilator/bin/verilator
#=============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VL=${VERILATOR:-$ROOT/../.tools/verilator/bin/verilator}
if [ ! -x "$VL" ]; then
  echo "找不到 verilator：$VL" >&2
  exit 1
fi

# 注意顺序：package 必须在所有 import 它的模块之前
RTL=(
  rtl/tpu_pkg.sv
  rtl/tpu_act.sv
  rtl/tpu_tmem.sv
  rtl/tpu_mxu.sv
  rtl/tpu_quant.sv
  rtl/tpu_copy.sv
  rtl/tpu_csr.sv
  rtl/tpu_top.sv
)
SIM=(sim/tpu_mem_model.sv sim/tpu_sim_top.sv)

fail=0
lint() {
  local top=$1; shift
  local out
  echo "== lint $top"
  set +e
  out=$("$VL" --lint-only -Wall -Wno-fatal -sv --top-module "$top" "$@" 2>&1)
  set -e
  if echo "$out" | grep -qE '^%(Warning|Error)'; then
    echo "$out" | grep -E '^%(Warning|Error)' | head -20
    echo "  -> 有告警/错误"
    fail=1
  else
    echo "  -> clean"
  fi
}

ARGS=()
for f in "${RTL[@]}"; do ARGS+=("$ROOT/$f"); done
lint tpu_top "${ARGS[@]}"

SIMARGS=("${ARGS[@]}")
for f in "${SIM[@]}"; do SIMARGS+=("$ROOT/$f"); done
lint tpu_sim_top "${SIMARGS[@]}"

exit $fail
