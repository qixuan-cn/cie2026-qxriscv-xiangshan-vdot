#!/usr/bin/env bash
#=============================================================================
# regress.sh -- RiscvTpu V1 一键回归
#
#   1) fp32 数值原语：tpu_pkg 的 f_mul/f_add/f_i2f48/f_scale_pow2 与 C++ IEEE 参考对拍
#   2) tile 级端到端：三精度 GEMM / COPY / QUANT / 错误码，与 tests/ref_tpu.py 逐位比对
#
# 用法： scripts/regress.sh [run_tpu_tests.py 的参数，如 -k i4 / --rebuild]
#=============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VL=${VERILATOR:-$ROOT/../.tools/verilator/bin/verilator}
FP32_OBJ=$ROOT/build/obj_fp32
FP32_BIN=$FP32_OBJ/tb_fp32

if [ ! -x "$VL" ]; then
  echo "找不到 verilator：$VL" >&2
  exit 1
fi

echo "[1/2] fp32 数值原语回归"
need_build=1
if [ -x "$FP32_BIN" ]; then
  if [ -z "$(find "$ROOT/rtl/tpu_pkg.sv" "$ROOT/sim/fp32_dut.sv" \
                 "$ROOT/sim/tb_fp32.cpp" -newer "$FP32_BIN" 2>/dev/null)" ]; then
    need_build=0
  fi
fi
if [ "$need_build" = 1 ]; then
  "$VL" -Wno-fatal --cc -O2 --x-assign fast --x-initial fast -sv \
        --top-module fp32_dut --Mdir "$FP32_OBJ" \
        --exe "$ROOT/sim/tb_fp32.cpp" -o tb_fp32 \
        "$ROOT/rtl/tpu_pkg.sv" "$ROOT/sim/fp32_dut.sv"
  make -C "$FP32_OBJ" -f Vfp32_dut.mk -j8 >/dev/null
fi
"$FP32_BIN"

echo "[2/2] tile 级端到端回归"
python3 "$ROOT/tests/run_tpu_tests.py" "$@"
