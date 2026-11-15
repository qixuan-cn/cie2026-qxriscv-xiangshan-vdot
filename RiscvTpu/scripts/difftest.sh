#!/usr/bin/env bash
#=============================================================================
# difftest.sh -- RiscvTpu 设备级 difftest（T2）
#
#   DUT = Verilator 编译的 tpu_sim_top
#   REF = sim/difftest/tpu_ref_model.c（独立 C 设备模型，非 Python 黄金模型）
#
# 流程：编译一次 -> 生成描述符流 -> 逐个 stream 跑，按「描述符退休」比对
#       CSR 白名单 / TMEM / DRAM / 退休地址序列。
#
# 用法：
#   scripts/difftest.sh            # 全量
#   scripts/difftest.sh --rebuild  # 强制重编
#   scripts/difftest.sh <name>     # 只跑匹配 <name> 的 stream
#   RD_LATENCY=2 scripts/difftest.sh   # T4：DRAM 读延迟 2 拍
#=============================================================================
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VL=${VERILATOR:-$ROOT/../.tools/verilator/bin/verilator}
RDL=${RD_LATENCY:-0}
OBJ=$ROOT/build/obj_difftest
if [ "$RDL" != "0" ]; then OBJ=${OBJ}_l$RDL; fi
BIN=$OBJ/tpu_difftest
STREAMS=$ROOT/build/difftest/streams
MAXCYCLES=${MAXCYCLES:-300000}

REBUILD=0
FILTER=""
for a in "$@"; do
  case "$a" in
    --rebuild) REBUILD=1 ;;
    *) FILTER="$a" ;;
  esac
done

if [ ! -x "$VL" ]; then
  echo "找不到 verilator：$VL" >&2
  exit 1
fi

SRC=(
  "$ROOT/rtl/tpu_pkg.sv" "$ROOT/rtl/tpu_csr.sv" "$ROOT/rtl/tpu_mxu.sv"
  "$ROOT/rtl/tpu_quant.sv" "$ROOT/rtl/tpu_copy.sv" "$ROOT/rtl/tpu_act.sv"
  "$ROOT/rtl/tpu_tmem.sv" "$ROOT/rtl/tpu_top.sv"
  "$ROOT/sim/tpu_mem_model.sv" "$ROOT/sim/tpu_sim_top.sv"
)

need_build=$REBUILD
if [ "$need_build" = 0 ]; then
  if [ ! -x "$BIN" ]; then
    need_build=1
  elif [ -n "$(find "${SRC[@]}" \
        "$ROOT/sim/difftest/tpu_difftest.h" \
        "$ROOT/sim/difftest/tpu_ref_model.c" \
        "$ROOT/sim/difftest/tpu_difftest_dut.cpp" \
        "$ROOT/sim/difftest/tpu_difftest_main.cpp" -newer "$BIN" 2>/dev/null)" ]; then
    need_build=1
  fi
fi

if [ "$need_build" = 1 ]; then
  echo "[build] Verilator difftest (RD_LATENCY=$RDL) ..."
  mkdir -p "$OBJ"
  "$VL" -Wno-fatal --cc -O2 --x-assign fast --x-initial fast -sv \
        -GRD_LATENCY="$RDL" \
        --top-module tpu_sim_top --Mdir "$OBJ" \
        --exe "$ROOT/sim/difftest/tpu_difftest_main.cpp" \
               "$ROOT/sim/difftest/tpu_difftest_dut.cpp" \
               "$ROOT/sim/difftest/tpu_ref_model.c" \
        -o tpu_difftest \
        "${SRC[@]}" || exit 1
  make -C "$OBJ" -f Vtpu_sim_top.mk -j8 >/dev/null || exit 1
fi

echo "[gen] 描述符流 ..."
python3 "$ROOT/tests/difftest/gen_desc_stream.py" --out "$STREAMS" || exit 1

pass=0
fail=0
failed_list=()
for f in "$STREAMS"/*.stream; do
  name=$(basename "$f" .stream)
  if [ -n "$FILTER" ] && [[ "$name" != *"$FILTER"* ]]; then continue; fi
  out=$("$BIN" "$f" "$MAXCYCLES" 2>&1)
  rc=$?
  if [ $rc -eq 0 ]; then
    pass=$((pass + 1))
    echo "  ok   $out"
  else
    fail=$((fail + 1))
    failed_list+=("$name")
    echo "  FAIL $name"
    echo "$out" | sed 's/^/       /'
  fi
done

echo "---------------------------------------------"
echo "difftest: $pass 通过, $fail 失败"
if [ $fail -ne 0 ]; then
  echo "失败用例： ${failed_list[*]}"
  exit 1
fi
