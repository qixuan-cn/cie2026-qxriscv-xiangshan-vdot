//=============================================================================
// tb_fp32.cpp -- tpu_pkg fp32 原语随机对拍
//
// 参考实现：C++ float（SSE，IEEE-754 单精度，RNE）+ FTZ/DAZ 语义。
// 输入限制为有限正规数或 0（与 docs/03 §6.2 的数值契约一致）。
//=============================================================================
#include "Vfp32_dut.h"
#include "verilated.h"

#include <cstdint>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>

static Vfp32_dut *dut;

static inline uint32_t fb(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }
static inline float bf(uint32_t u) { float f; std::memcpy(&f, &u, 4); return f; }

// 正规/零过滤：指数 1..254 或 +0/-0
static inline uint32_t norm(uint32_t u) {
  uint32_t e = (u >> 23) & 0xFF;
  if (e == 0 || e == 0xFF) return 0u;
  return u;
}

static inline float ftz(float f) {
  uint32_t u = fb(f);
  if (((u >> 23) & 0xFF) == 0) return 0.0f;
  return f;
}

static inline float ref_mul(uint32_t a, uint32_t b) {
  float r = ftz(bf(norm(a)) * bf(norm(b)));
  if (r == 0.0f) return 0.0f;              // 零结果规范化为 +0
  return r;
}

static inline float ref_add(uint32_t a, uint32_t b) {
  float r = ftz(bf(norm(a)) + bf(norm(b)));
  if (r == 0.0f) return 0.0f;
  return r;
}

static inline float ref_i2f_u(uint32_t lo32) {
  float r = (float)(uint32_t)lo32;         // 48-bit 零扩展 → 非负
  if (r == 0.0f) return 0.0f;
  return ftz(r);
}

static inline float ref_i2f_s(uint32_t lo32) {
  float r = (float)(int32_t)lo32;          // 48-bit 符号扩展 → 可负
  if (r == 0.0f) return 0.0f;
  return ftz(r);
}

int main(int argc, char **argv) {
  Verilated::commandArgs(argc, argv);
  dut = new Vfp32_dut;

  std::mt19937 rng(0xC0FFEE);
  int fails = 0, total = 0;

  auto one = [&](int op, uint32_t a, uint32_t b, int bin) {
    dut->op = (uint8_t)op;
    dut->a  = a;
    dut->b  = b;
    dut->bin = (uint8_t)bin;
    dut->eval();
    uint32_t got = dut->y;
    uint32_t exp;
    if (op == 0) exp = fb(ref_mul(a, b));
    else if (op == 1) exp = fb(ref_add(a, b));
    else if (op == 2) exp = fb(ref_i2f_u(a));
    else if (op == 4) exp = fb(ref_i2f_s(a));
    else exp = fb(ref_mul(a, fb(ldexpf(1.0f, -bin))));
    total++;
    if (got != exp) {
      if (fails < 20)
        std::printf("FAIL op=%d a=%08x b=%08x bin=%d got=%08x exp=%08x\n",
                    op, a, b, bin, got, exp);
      fails++;
    }
  };

  // 边界值
  const uint32_t edge[] = {
      0x00000000u, 0x80000000u, 0x3F800000u, 0xBF800000u,
      0x7F7FFFFFu, 0xFF7FFFFFu, 0x00800000u, 0x80800000u,
      0x40000000u, 0x3F000000u, 0x4B000000u, 0x4F000000u,
      0x33800000u, 0x34000000u,
  };
  for (uint32_t a : edge)
    for (uint32_t b : edge) {
      one(0, a, b, 0);
      one(1, a, b, 0);
    }
  for (uint32_t a : edge) {
    one(2, a, 0, 0); one(4, a, 0, 0);
    one(3, a, 0, 16); one(3, a, 0, 11);
  }

  // 随机正规数
  for (int i = 0; i < 200000; i++) {
    uint32_t a = (rng() & 0x807FFFFFu) | ((1u + (rng() % 254u)) << 23);
    uint32_t b = (rng() & 0x807FFFFFu) | ((1u + (rng() % 254u)) << 23);
    if (i & 1) b = norm(b);
    one(0, a, b, 0);
    one(1, a, b, 0);
    if ((i & 7) == 0) { one(3, a, 0, 16); one(3, a, 0, 11); }
    if ((i & 15) == 0) { one(2, a, 0, 0); one(4, a, 0, 0); }
  }

  // 近抵消（加法最难的情况）
  for (int i = 0; i < 20000; i++) {
    uint32_t a = (rng() & 0x807FFFFFu) | ((1u + (rng() % 200u)) << 23);
    uint32_t b = a ^ 0x80000000u;
    b = (b & 0xFFFFF000u) | (rng() & 0xFFFu);
    one(1, a, b, 0);
  }

  std::printf("FP32: %d checks, %d failures\n", total, fails);
  delete dut;
  return fails ? 1 : 0;
}
