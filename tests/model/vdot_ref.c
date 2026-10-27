//=============================================================================
// vdot.vv C 参考模型（Golden Model / NEMU-ready）
//
// 语义（与 ipd/01 设计规格书 §2.3 一致）：
//   S = sum_{i=0..7} (int8)vs1[i] * (int8)vs2[i]
//   vd[31:0]  = S 的二补码（ACC_W=20 符号扩展到 32 bit）
//   vd[127:32] = 0
//
// 用途：
//   1) 作为第三份独立实现，与 tests/ref_vdot.py（Python）和
//      tests/tb/tb_vdot.cpp（C++ 内联）交叉比对
//   2) 保留纯函数式接口 vdot_vv_u32()，可直接移植进 NEMU 的 RVV 执行路径
//
// 构建：  gcc -O2 -std=c11 -o build/vdot_ref tests/model/vdot_ref.c
// 自检：  build/vdot_ref --selfcheck
// 比对：  build/vdot_ref tests/vectors/vdot_vectors.txt
//=============================================================================
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <ctype.h>

#define K      8
#define ACC_W  20
#define OUT_W  32

// --- NEMU 移植接口 -----------------------------------------------------------
// 入参：两个 64 bit 低半部（lane0 在最低字节），返回 128 bit 结果中低 32 bit
// 的有符号值。NEMU 侧只需把 vs1/vs2 的 8 个字节顺序传入即可。
typedef struct {
    int32_t  s;        // 有符号和，范围 [-130048, 131072]
    uint32_t bits;     // vd[31:0] 的原始位模式
} vdot_result_t;

static inline int8_t sext8(uint8_t b) {
    return (int8_t)b;
}

vdot_result_t vdot_vv(const uint8_t vs1[K], const uint8_t vs2[K]) {
    vdot_result_t r;
    int32_t s = 0;
    for (int i = 0; i < K; i++) {
        s += (int32_t)sext8(vs1[i]) * (int32_t)sext8(vs2[i]);
    }
    // ACC_W=20 位宽足以承载 [-130048, +131072]，此处直接以 32 bit 承载
    r.s    = s;
    r.bits = (uint32_t)s;
    return r;
}

// 把 128 bit 十六进制串转成 8 个 lane 字节（lane0 = 最低字节）
static void hex128_to_lanes(const char *hex, uint8_t lanes[16], uint8_t out[K]) {
    // hex 为 32 个字符，MSB 在左；lane i 是整体 128 bit 的第 i 个字节
    for (int i = 0; i < 16; i++) {
        int hi = i * 2;
        char a = hex[30 - hi], b = hex[31 - hi];
        unsigned v = 0;
        for (int k = 0; k < 2; k++) {
            char c = (k == 0) ? a : b;
            unsigned d = (c <= '9') ? (unsigned)(c - '0') : (unsigned)(tolower((unsigned char)c) - 'a' + 10);
            v = (v << 4) | d;
        }
        lanes[i] = (uint8_t)v;
    }
    for (int i = 0; i < K; i++) out[i] = lanes[i];
}

// 由 8 个 lane 构造 128 bit 期望串（vd[31:0]=S，其余 0）
static void lanes_to_hex128(uint32_t lo, char out[33]) {
    snprintf(out, 33, "%016llx%016llx", 0ull, (unsigned long long)lo);
}

// 十六进制串大小写无关比较（不依赖 POSIX strcasecmp）
static int hex_streq(const char *a, const char *b) {
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++; b++;
    }
    return *a == '\0' && *b == '\0';
}

static int selfcheck(void) {
    struct { int8_t a[K]; int8_t b[K]; int32_t want; } tc[] = {
        {{127,127,127,127,127,127,127,127}, {127,127,127,127,127,127,127,127}, 0x1F808},
        {{-128,-128,-128,-128,-128,-128,-128,-128}, {-128,-128,-128,-128,-128,-128,-128,-128}, 0x20000},
        {{-128,-128,-128,-128,-128,-128,-128,-128}, {127,127,127,127,127,127,127,127}, -130048},
        {{0,0,0,0,0,0,0,0}, {0,0,0,0,0,0,0,0}, 0},
        {{1,0xFF,1,0xFF,1,0xFF,1,0xFF}, {1,1,1,1,1,1,1,1}, 0},
    };
    int n = (int)(sizeof(tc) / sizeof(tc[0]));
    int bad = 0;
    for (int i = 0; i < n; i++) {
        uint8_t a[K], b[K];
        for (int k = 0; k < K; k++) { a[k] = (uint8_t)tc[i].a[k]; b[k] = (uint8_t)tc[i].b[k]; }
        vdot_result_t r = vdot_vv(a, b);
        if (r.s != tc[i].want) {
            printf("SELFCHECK FAIL case %d: got %d want %d\n", i, r.s, tc[i].want);
            bad++;
        }
    }
    if (bad == 0) printf("SELFCHECK PASS (%d cases)\n", n);
    return bad ? 1 : 0;
}

static int check_vectors(const char *path) {
    FILE *f = fopen(path, "r");
    if (!f) { fprintf(stderr, "cannot open %s\n", path); return 2; }

    char line[512];
    int total = 0, pass = 0, fail = 0;
    while (fgets(line, sizeof(line), f)) {
        if (line[0] == '#' || line[0] == '\n') continue;
        char id[64], vs1h[64], vs2h[64], expdh[64];
        int vl_zero, vm, sew, lmul, wr, ill;
        if (sscanf(line, "%63s %d %d %d %d %63s %63s %63s %d %d",
                   id, &vl_zero, &vm, &sew, &lmul, vs1h, vs2h, expdh, &wr, &ill) != 10) {
            fprintf(stderr, "bad line: %s", line);
            fclose(f);
            return 2;
        }
        total++;
        uint8_t lanes[16], a[K], b[K];
        hex128_to_lanes(vs1h, lanes, a);
        hex128_to_lanes(vs2h, lanes, b);
        vdot_result_t r = vdot_vv(a, b);

        int legal = (vm == 1 && sew == 0 && lmul == 0);
        char got[33];
        lanes_to_hex128(r.bits, got);

        int ok = 1;
        if (!legal) {
            if (ill != 1) ok = 0;
        } else if (vl_zero) {
            if (wr != 0) ok = 0;
        } else {
            if (wr != 1 || !hex_streq(got, expdh)) ok = 0;
        }
        if (ok) pass++;
        else {
            fail++;
            printf("FAIL %s: legal=%d vl_zero=%d got=%s expected=%s\n",
                   id, legal, vl_zero, got, expdh);
        }
    }
    fclose(f);
    printf("vector_crosscheck: total=%d pass=%d fail=%d\n", total, pass, fail);
    printf("RESULT: %s\n", fail == 0 ? "PASS" : "FAIL");
    return fail == 0 ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s --selfcheck | <vectors.txt>\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "--selfcheck") == 0) return selfcheck();
    return check_vectors(argv[1]);
}
