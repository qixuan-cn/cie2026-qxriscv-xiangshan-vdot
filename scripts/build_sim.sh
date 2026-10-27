#!/usr/bin/env bash
# 构建 VDot 单元级仿真（Verilator）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${VERILATOR:-}" ]; then
  if [ -x "$ROOT/.tools/verilator-inst/bin/verilator" ]; then
    VERILATOR="$ROOT/.tools/verilator-inst/bin/verilator"
  elif [ -x "$ROOT/.tools/verilator/bin/verilator" ]; then
    # 本地源码树直接使用（未执行 make install，仅缺少 man page 生成）
    export VERILATOR_ROOT="$ROOT/.tools/verilator"
    VERILATOR="$VERILATOR_ROOT/bin/verilator"
  else
    VERILATOR="verilator"
  fi
fi

echo "[build] VERILATOR = $VERILATOR"
"$VERILATOR" --version

mkdir -p "$ROOT/build"

"$VERILATOR" --cc --exe --build -j "$(nproc)" --assert --trace \
  --top-module VDot \
  -Mdir "$ROOT/build/obj_dir" \
  -o tb_vdot \
  -CFLAGS "-O2 -std=c++17" \
  "$ROOT/rtl/verilog/VDot.v" \
  "$ROOT/tests/tb/tb_vdot.cpp"

echo "[build] OK: $ROOT/build/obj_dir/tb_vdot"
