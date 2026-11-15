//=============================================================================
// tb_tpu.cpp -- RiscvTpu 顶层 case 驱动（Verilator）
//
// 输入：一个纯文本 case 文件，逐行一条命令：
//   D   <addr> <hexbytes>        写 DRAM（任意长度，内部按 4 B 窗口）
//   T   <addr> <val32>           写 TMEM 字
//   A   <idx> <128hex> <16hex>   写 ACT 块（64 B 数据 + 8 B scale 记录）
//   X   <addr> <w0..w15>         写 64 B 描述符
//   C   <csr_addr> <val32>       CSR 写
//   HEAD <addr>                  写 DQ_HEAD
//   DOOR                         写 CTRL.RUN=1（doorbell）
//   TICK <n>                     空跑 n 个周期
//   RUN  <maxcycles>             跑到 HALTED/ERR 或超时
//   E_T  <addr> <val32>          期望 TMEM 字
//   E_D  <addr> <hexbytes>       期望 DRAM 字节
//   E_ERR <code>                 期望 ERROR_CODE
//   E_CS <addr> <val32>          期望 CSR 读值
//   E_HALT                       期望最终状态为 HALT
//
// 退出码：0 = 全部通过；1 = 有失败；2 = 语法/IO 错误。
//=============================================================================
#include "Vtpu_sim_top.h"
#include "verilated.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

static Vtpu_sim_top *dut;

static int g_checks = 0;
static int g_fails = 0;
static int g_line = 0;

static void tick() {
  dut->clk = 0;
  dut->eval();
  dut->clk = 1;
  dut->eval();
}

static void inputs_idle() {
  dut->cfg_we = 0;
  dut->cfg_re = 0;
  dut->cfg_addr = 0;
  dut->cfg_wdata = 0;
  dut->act_h_we = 0;
  dut->act_h_idx = 0;
  dut->act_h_off = 0;
  dut->act_h_data = 0;
  dut->tm_h_we = 0;
  dut->tm_h_addr = 0;
  dut->tm_h_data = 0;
  dut->dram_we = 0;
  dut->dram_re = 0;
  dut->dram_addr = 0;
  dut->dram_wdata = 0;
  dut->dram_wstrb = 0;
}

static void reset() {
  inputs_idle();
  dut->rst_n = 0;
  dut->clk = 0;
  for (int i = 0; i < 4; i++) tick();
  dut->rst_n = 1;
  tick();
}

// ---------------------------------------------------------------- 基础动作
static void dram_write(uint32_t addr, const std::vector<uint8_t> &bytes) {
  size_t i = 0;
  while (i < bytes.size()) {
    uint32_t wa = (addr + (uint32_t)i) & ~3u;
    uint32_t wd = 0;
    uint8_t wstrb = 0;
    for (uint32_t b = 0; b < 4; b++) {
      size_t idx = (size_t)(wa - addr) + b;
      if (wa + b >= addr && idx < bytes.size()) {
        wd |= (uint32_t)bytes[idx] << (8 * b);
        wstrb |= (uint8_t)(1u << b);
      }
    }
    dut->dram_we = 1;
    dut->dram_addr = wa;
    dut->dram_wdata = wd;
    dut->dram_wstrb = wstrb;
    tick();
    dut->dram_we = 0;
    dut->dram_wstrb = 0;
    i = (size_t)(wa - addr) + 4;
  }
}

static uint32_t dram_read32(uint32_t addr) {
  dut->dram_re = 1;
  dut->dram_addr = addr;
  dut->eval();
  uint32_t v = dut->dram_rdata;
  dut->dram_re = 0;
  return v;
}

static void tmem_write(uint32_t addr, uint32_t val) {
  dut->tm_h_we = 1;
  dut->tm_h_addr = addr & 0x3FFF;
  dut->tm_h_data = val;
  tick();
  dut->tm_h_we = 0;
}

static uint32_t tmem_read(uint32_t addr) {
  dut->tm_h_addr = addr & 0x3FFF;
  dut->eval();
  return dut->tm_h_rdata;
}

static void act_write(uint32_t idx, const std::vector<uint8_t> &dat,
                      const std::vector<uint8_t> &scl) {
  for (uint32_t off = 0; off < 9; off++) {
    uint64_t w = 0;
    for (uint32_t b = 0; b < 8; b++) {
      uint8_t by = 0;
      if (off < 8) {
        if (off * 8 + b < dat.size()) by = dat[off * 8 + b];
      } else {
        if (b < scl.size()) by = scl[b];
      }
      w |= (uint64_t)by << (8 * b);
    }
    dut->act_h_we = 1;
    dut->act_h_idx = idx & 0x7FF;
    dut->act_h_off = off;
    dut->act_h_data = w;
    tick();
    dut->act_h_we = 0;
  }
}

static void csr_write(uint32_t addr, uint32_t val) {
  dut->cfg_we = 1;
  dut->cfg_addr = addr & 0xFFF;
  dut->cfg_wdata = val;
  tick();
  dut->cfg_we = 0;
}

static uint32_t csr_read(uint32_t addr) {
  dut->cfg_re = 1;
  dut->cfg_addr = addr & 0xFFF;
  dut->eval();
  uint32_t v = dut->cfg_rdata;
  dut->cfg_re = 0;
  return v;
}

// ---------------------------------------------------------------- 解析工具
static std::vector<uint8_t> parse_hex_bytes(const std::string &s) {
  std::vector<uint8_t> out;
  for (size_t i = 0; i + 1 < s.size(); i += 2) {
    char buf[3] = {s[i], s[i + 1], 0};
    out.push_back((uint8_t)strtoul(buf, nullptr, 16));
  }
  return out;
}

static uint32_t hex32(const std::string &s) {
  return (uint32_t)strtoul(s.c_str(), nullptr, 16);
}

static void fail(const std::string &msg) {
  g_fails++;
  std::printf("  line %d: FAIL %s\n", g_line, msg.c_str());
}

int main(int argc, char **argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s <case-file>\n", argv[0]);
    return 2;
  }
  Verilated::commandArgs(argc, argv);
  dut = new Vtpu_sim_top;
  reset();

  std::ifstream f(argv[1]);
  if (!f) {
    std::fprintf(stderr, "cannot open %s\n", argv[1]);
    return 2;
  }

  std::string line;
  while (std::getline(f, line)) {
    g_line++;
    // 去掉注释
    size_t hp = line.find('#');
    if (hp != std::string::npos) line = line.substr(0, hp);
    std::istringstream is(line);
    std::string cmd;
    if (!(is >> cmd)) continue;

    if (cmd == "D") {
      std::string a, h;
      is >> a >> h;
      dram_write(hex32(a), parse_hex_bytes(h));
    } else if (cmd == "T") {
      std::string a, v;
      is >> a >> v;
      tmem_write(hex32(a), hex32(v));
    } else if (cmd == "A") {
      std::string i, d, s;
      is >> i >> d >> s;
      act_write(hex32(i), parse_hex_bytes(d), parse_hex_bytes(s));
    } else if (cmd == "X") {
      std::string a;
      is >> a;
      uint32_t base = hex32(a);
      for (int w = 0; w < 16; w++) {
        std::string v;
        if (!(is >> v)) break;
        uint32_t word = hex32(v);
        std::vector<uint8_t> b(4);
        for (int k = 0; k < 4; k++) b[k] = (uint8_t)(word >> (8 * k));
        dram_write(base + 4 * (uint32_t)w, b);
      }
    } else if (cmd == "C") {
      std::string a, v;
      is >> a >> v;
      csr_write(hex32(a), hex32(v));
    } else if (cmd == "HEAD") {
      std::string a;
      is >> a;
      csr_write(0x014, hex32(a));
    } else if (cmd == "DOOR") {
      csr_write(0x008, 1);
    } else if (cmd == "TICK") {
      int n = 1;
      is >> n;
      for (int i = 0; i < n; i++) tick();
    } else if (cmd == "RUN") {
      int maxc = 200000;
      is >> maxc;
      int c = 0;
      while (c < maxc) {
        tick();
        c++;
        uint8_t st = dut->dbg_state;
        if (st == 6 || st == 7) break;
      }
      if (c >= maxc && dut->dbg_state != 6 && dut->dbg_state != 7)
        fail("RUN timeout (state=" + std::to_string(dut->dbg_state) + ")");
      // 状态/错误码是寄存器输出，多跑 2 拍让 CSR 落定
      tick();
      tick();
    } else if (cmd == "E_T") {
      std::string a, v;
      is >> a >> v;
      uint32_t addr = hex32(a), exp = hex32(v), got = tmem_read(addr);
      g_checks++;
      if (got != exp) {
        char m[128];
        std::snprintf(m, sizeof m, "TMEM[%04x] got=%08x exp=%08x", addr, got, exp);
        fail(m);
      }
    } else if (cmd == "E_D") {
      std::string a, h;
      is >> a >> h;
      uint32_t addr = hex32(a);
      auto exp = parse_hex_bytes(h);
      for (size_t i = 0; i < exp.size(); i += 4) {
        uint32_t got = dram_read32(addr + (uint32_t)i);
        uint32_t e = 0;
        for (size_t k = 0; k < 4 && i + k < exp.size(); k++)
          e |= (uint32_t)exp[i + k] << (8 * k);
        g_checks++;
        if (got != e) {
          char m[128];
          std::snprintf(m, sizeof m, "DRAM[%04x] got=%08x exp=%08x",
                        addr + (uint32_t)i, got, e);
          fail(m);
        }
      }
    } else if (cmd == "E_ERR") {
      std::string v;
      is >> v;
      uint32_t exp = hex32(v), got = csr_read(0x044) & 0xFF;
      g_checks++;
      if (got != exp) {
        char m[96];
        std::snprintf(m, sizeof m, "ERROR_CODE got=%02x exp=%02x", got, exp);
        fail(m);
      }
    } else if (cmd == "E_CS") {
      std::string a, v;
      is >> a >> v;
      uint32_t addr = hex32(a), exp = hex32(v), got = csr_read(addr);
      g_checks++;
      if (got != exp) {
        char m[96];
        std::snprintf(m, sizeof m, "CSR[%03x] got=%08x exp=%08x", addr, got, exp);
        fail(m);
      }
    } else if (cmd == "E_HALT") {
      g_checks++;
      if (dut->dbg_state != 6)
        fail("expect HALT, state=" + std::to_string(dut->dbg_state));
    } else {
      std::fprintf(stderr, "line %d: unknown command '%s'\n", g_line, cmd.c_str());
      return 2;
    }
  }

  std::printf("SUMMARY checks=%d fails=%d\n", g_checks, g_fails);
  delete dut;
  return g_fails ? 1 : 0;
}
