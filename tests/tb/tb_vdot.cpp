//=============================================================================
// VDot 单元级测试台（Verilator）
//
// 功能：
//   1) 读入 tests/vectors/vdot_vectors.txt，逐个用例驱动并逐位比对 128 bit 输出
//   2) 统计单条延迟（in_valid -> out_valid 的周期数）
//   3) 背靠背连续 1024 条流式测试：乱序/流水交错检查 + 吞吐统计
//
// 判定：任何不一致 -> 退出码 1
//=============================================================================
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>
#include <deque>
#include <fstream>
#include <sstream>
#include <iostream>
#include <algorithm>

#include "VVDot.h"
#include "verilated.h"
#include "verilated_vcd_c.h"

static VVDot *top = nullptr;
static VerilatedVcdC *tfp = nullptr;
static vluint64_t cyc = 0;
static vluint64_t vtime = 0;
static const vluint64_t VCD_MAX_CYCLES = 400;   // 只记录前若干周期，控制文件体积

static void tick() {
    top->clk = 0;
    top->eval();
    if (tfp && cyc < VCD_MAX_CYCLES) tfp->dump(vtime++);
    top->clk = 1;
    top->eval();
    if (tfp && cyc < VCD_MAX_CYCLES) tfp->dump(vtime++);
    ++cyc;
}

struct Wid {
    uint32_t w[4];
};

static Wid hex_to_wid(const std::string &h) {
    Wid r{};
    // 32 hex chars, MSB first -> 需要按 8 字符一段切到 w[3..0]
    // 这里按整体 128 bit 处理：先取高 64 / 低 64
    std::string hi = h.substr(0, 16);
    std::string lo = h.substr(16, 16);
    uint64_t hi64 = std::stoull(hi, nullptr, 16);
    uint64_t lo64 = std::stoull(lo, nullptr, 16);
    r.w[0] = (uint32_t)(lo64 & 0xFFFFFFFFu);
    r.w[1] = (uint32_t)(lo64 >> 32);
    r.w[2] = (uint32_t)(hi64 & 0xFFFFFFFFu);
    r.w[3] = (uint32_t)(hi64 >> 32);
    return r;
}

static Wid vd_read() {
    Wid r{};
    // Verilator 对 128 bit 端口展开为 4 个 32 bit word（小端序）
    r.w[0] = (uint32_t)top->out_vd[0];
    r.w[1] = (uint32_t)top->out_vd[1];
    r.w[2] = (uint32_t)top->out_vd[2];
    r.w[3] = (uint32_t)top->out_vd[3];
    return r;
}

static bool wid_eq(const Wid &a, const Wid &b) {
    for (int i = 0; i < 4; i++)
        if (a.w[i] != b.w[i]) return false;
    return true;
}

static std::string wid_str(const Wid &a) {
    char buf[40];
    std::snprintf(buf, sizeof(buf), "%08x%08x%08x%08x", a.w[3], a.w[2], a.w[1], a.w[0]);
    return std::string(buf);
}

struct Case {
    std::string id;
    int vl_zero, vm, sew, lmul;
    std::string vs1, vs2, expected;
    int wr, ill;
};

struct CaseResult {
    bool pass;
    int lat;
    std::string detail;
    Wid got;            // 实际观测到的 vd（仅写回用例有效）
};

static CaseResult run_case(const Case &c) {
    CaseResult r{true, 0, "", Wid{}};

    top->in_valid   = 1;
    top->in_vl_zero = c.vl_zero;
    top->in_vm      = c.vm;
    top->in_sew     = c.sew;
    top->in_lmul    = c.lmul;
    Wid a = hex_to_wid(c.vs1);
    Wid b = hex_to_wid(c.vs2);
    for (int i = 0; i < 4; i++) {
        top->in_vs1[i] = a.w[i];
        top->in_vs2[i] = b.w[i];
    }
    top->eval();

    tick();                 // 边沿 1：进入 s1
    top->in_valid = 0;
    top->eval();

    bool seen_valid = false, seen_ill = false;
    int lat = 0;
    for (int k = 1; k <= 8; k++) {
        if (top->out_valid || top->out_illegal) {
            seen_valid = (bool)top->out_valid;
            seen_ill   = (bool)top->out_illegal;
            lat = k;
            break;
        }
        if (k < 8) tick();
    }

    if (c.ill) {
        if (!seen_ill) { r.pass = false; r.detail = "期望 illegal 未出现"; }
        else if (seen_valid) { r.pass = false; r.detail = "illegal 同时写回"; }
    } else if (c.vl_zero) {
        if (seen_valid || seen_ill) { r.pass = false; r.detail = "vl=0 时不应写回/报错"; }
    } else {
        if (!seen_valid) { r.pass = false; r.detail = "未出现写回 valid"; }
        else {
            Wid exp = hex_to_wid(c.expected);
            Wid got = vd_read();
            r.got = got;
            if (!wid_eq(exp, got)) {
                r.pass = false;
                r.detail = "vd 不匹配: exp=" + wid_str(exp) + " got=" + wid_str(got);
            }
        }
    }
    r.lat = lat;
    return r;
}

// 流式测试：连续 in_valid，按序检查
static bool run_stream(int n, int *out_count, uint64_t *first_cyc, uint64_t *last_cyc) {
    std::deque<Wid> q;
    int done = 0;
    *first_cyc = 0;
    *last_cyc = 0;
    uint32_t lfsr = 0x12345678u;

    for (int i = 0; i < n + 8; i++) {
        bool issue = (i < n);
        top->in_valid = issue ? 1 : 0;
        if (issue) {
            Wid a{}, b{};
            for (int w = 0; w < 4; w++) {
                lfsr = lfsr * 1664525u + 1013904223u;
                uint32_t x = lfsr;
                lfsr = lfsr * 1664525u + 1013904223u;
                uint32_t y = lfsr;
                top->in_vs1[w] = x;
                top->in_vs2[w] = y;
                a.w[w] = x;
                b.w[w] = y;
            }
            top->in_vl_zero = 0;
            top->in_vm      = 1;
            top->in_sew     = 0;
            top->in_lmul    = 0;
            // 期望值由 C++ 内联模型计算（与 Python 模型构成独立交叉实现）
            int64_t s = 0;
            for (int k = 0; k < 8; k++) {
                int8_t av = (int8_t)((a.w[k / 4] >> (8 * (k % 4))) & 0xFF);
                int8_t bv = (int8_t)((b.w[k / 4] >> (8 * (k % 4))) & 0xFF);
                s += (int)av * (int)bv;
            }
            // 输出布局：vd[31:0] = S，vd[127:32] = 0
            Wid e{};
            e.w[0] = (uint32_t)(s & 0xFFFFFFFF);
            e.w[1] = 0;
            e.w[2] = 0;
            e.w[3] = 0;
            q.push_back(e);
        }
        top->eval();
        bool ov = top->out_valid;
        Wid vd_got = vd_read();
        tick();
        if (ov) {
            if (q.empty()) return false;
            Wid e = q.front();
            q.pop_front();
            if (!wid_eq(e, vd_got)) {
                std::cerr << "stream mismatch at output #" << done << ": exp="
                          << wid_str(e) << " got=" << wid_str(vd_got) << "\n";
                return false;
            }
            if (done == 0) *first_cyc = cyc;
            *last_cyc = cyc;
            done++;
        }
    }
    *out_count = done;
    return done == n;
}

int main(int argc, char **argv) {
    Verilated::commandArgs(argc, argv);
    if (argc < 2) {
        std::cerr << "usage: tb_vdot <vectors.txt> [out.vcd] [result_export.txt]\n";
        return 2;
    }
    top = new VVDot;

    // 可选波形：tb_vdot <vectors.txt> <out.vcd>，传 "-" 表示不导出波形
    if (argc >= 3 && std::string(argv[2]) != "-") {
        Verilated::traceEverOn(true);
        tfp = new VerilatedVcdC;
        top->trace(tfp, 99);
        tfp->open(argv[2]);
    }

    // 复位
    top->clk = 0;
    top->reset = 1;
    top->in_valid = 0;
    top->in_vl_zero = 0;
    top->in_vm = 1;
    top->in_sew = 0;
    top->in_lmul = 0;
    for (int i = 0; i < 4; i++) { top->in_vs1[i] = 0; top->in_vs2[i] = 0; }
    for (int i = 0; i < 3; i++) tick();
    top->reset = 0;
    top->eval();

    // 可选：导出逐用例 128 bit 结果，供 NEMU 协同仿真直接 diff
    // 只导出"合法且写回"的用例，顺序与输入文件一致
    std::ofstream export_f;
    if (argc >= 4) {
        export_f.open(argv[3]);
        if (!export_f) {
            std::cerr << "cannot open export file " << argv[3] << "\n";
            return 2;
        }
        export_f << "# one 128-bit result per write-back case (vl_zero=0, ill=0)\n";
    }

    std::ifstream f(argv[1]);
    if (!f) {
        std::cerr << "cannot open " << argv[1] << "\n";
        return 2;
    }

    std::vector<Case> cases;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream ss(line);
        Case c;
        if (!(ss >> c.id >> c.vl_zero >> c.vm >> c.sew >> c.lmul
                 >> c.vs1 >> c.vs2 >> c.expected >> c.wr >> c.ill)) {
            std::cerr << "bad line: " << line << "\n";
            return 2;
        }
        cases.push_back(c);
    }

    int pass = 0, fail = 0;
    std::vector<int> lat_by_class(4, 0); // 0 write,1 illegal,2 vl0,3 other
    int lat_write = 0, lat_ill = 0;
    for (const auto &c : cases) {
        CaseResult r = run_case(c);
        if (r.pass) {
            pass++;
            if (c.ill) lat_ill = r.lat;
            else if (!c.vl_zero) lat_write = r.lat;
        } else {
            fail++;
            std::cout << "FAIL " << c.id << " : " << r.detail << "\n";
        }
        if (export_f.is_open() && !c.ill && !c.vl_zero) {
            export_f << wid_str(r.got) << "\n";
        }
        // 排空流水线，避免用例间干扰
        top->in_valid = 0;
        top->eval();
        tick();
        tick();
        tick();
    }

    int stream_n = 1024, stream_got = 0;
    uint64_t c0 = 0, c1 = 0;
    bool stream_ok = run_stream(stream_n, &stream_got, &c0, &c1);

    double tp = (c1 > c0) ? (double)(stream_got - 1) / (double)(c1 - c0) : 0.0;

    std::cout << "---------------------------------------------\n";
    std::cout << "cases_total      : " << cases.size() << "\n";
    std::cout << "cases_pass       : " << pass << "\n";
    std::cout << "cases_fail       : " << fail << "\n";
    std::cout << "latency_write    : " << lat_write << " cycle\n";
    std::cout << "latency_illegal  : " << lat_ill << " cycle\n";
    std::cout << "stream_issued    : " << stream_n << "\n";
    std::cout << "stream_completed : " << stream_got << "\n";
    std::cout << "stream_ok        : " << (stream_ok ? "true" : "false") << "\n";
    std::cout << "throughput       : " << tp << " inst/cycle\n";
    std::cout << "total_cycles     : " << cyc << "\n";
    std::cout << "RESULT           : " << ((fail == 0 && stream_ok) ? "PASS" : "FAIL") << "\n";

    if (export_f.is_open()) export_f.close();

    if (tfp) {
        tfp->close();
        delete tfp;
        tfp = nullptr;
    }
    top->final();
    delete top;
    return (fail == 0 && stream_ok) ? 0 : 1;
}
