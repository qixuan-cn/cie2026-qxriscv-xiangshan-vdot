#!/usr/bin/env bash
#=============================================================================
# emu_selftest.sh -- T3a：emu MMIO 适配层回归（宿主侧）
#
# 用同一份 tpu_ref_model.c 跑两遍：
#   A：CSR/门铃走 tpu_emu_mmio_write（模拟 emu 的 MMIO 路径）
#   B：直接调模型 API
# 比对 CSR 白名单 / TMEM / DRAM。emu 侧真正接入后，这个脚本仍可作为
# 适配层的最小回归（不需要重建香山）。
#
# 用法： scripts/emu_selftest.sh [<name 过滤>]
#=============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OBJ=$ROOT/build/emu_selftest
STREAMS=$ROOT/build/difftest/streams
CC=${CC:-gcc}

mkdir -p "$OBJ"

if ! ls "$STREAMS"/*.stream >/dev/null 2>&1; then
  echo "[gen] 描述符流 ..."
  python3 "$ROOT/tests/difftest/gen_desc_stream.py" --out "$STREAMS"
fi

echo "[build] emu selftest ..."
$CC -O2 -Wall -Wextra \
    -I "$ROOT/sim/difftest" -I "$ROOT/sim/emu" \
    -o "$OBJ/tpu_emu_selftest" \
    "$ROOT/sim/emu/tpu_emu_selftest.c" \
    "$ROOT/sim/emu/tpu_emu.c" \
    "$ROOT/sim/difftest/tpu_ref_model.c"

FILES=()
for f in "$STREAMS"/*.stream; do
  if [ $# -gt 0 ] && [[ "$(basename "$f")" != *"$1"* ]]; then continue; fi
  FILES+=("$f")
done

"$OBJ/tpu_emu_selftest" "${FILES[@]}"
