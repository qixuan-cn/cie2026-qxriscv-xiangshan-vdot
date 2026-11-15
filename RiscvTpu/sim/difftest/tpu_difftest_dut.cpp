//=============================================================================
// tpu_difftest_dut.cpp -- Verilator DUT 封装（T2）
//
// 把 Vtpu_sim_top 包成 C 接口，供 tpu_difftest_main.cpp 与 NEMU 侧复用。
// 时序约定与 sim/tb_tpu.cpp 完全一致（复位 4 拍、RUN 结束后补 2 拍让
// ERROR_CODE / STATUS 落定）。
//=============================================================================
#include "tpu_difftest.h"

#include "Vtpu_sim_top.h"
#include "verilated.h"

#include <cstring>
#include <vector>

struct tpu_dut {
  Vtpu_sim_top *v;
  int state;
};

static void tick(tpu_dut_t *d) {
  d->v->clk = 0;
  d->v->eval();
  d->v->clk = 1;
  d->v->eval();
}

static void inputs_idle(tpu_dut_t *d) {
  Vtpu_sim_top *v = d->v;
  v->cfg_we = 0;
  v->cfg_re = 0;
  v->cfg_addr = 0;
  v->cfg_wdata = 0;
  v->act_h_we = 0;
  v->act_h_idx = 0;
  v->act_h_off = 0;
  v->act_h_data = 0;
  v->tm_h_we = 0;
  v->tm_h_addr = 0;
  v->tm_h_data = 0;
  v->dram_we = 0;
  v->dram_re = 0;
  v->dram_addr = 0;
  v->dram_wdata = 0;
  v->dram_wstrb = 0;
}

extern "C" tpu_dut_t *tpu_dut_create(void) {
  tpu_dut_t *d = new tpu_dut();
  d->v = new Vtpu_sim_top();
  d->state = TPU_S_IDLE;
  tpu_dut_reset(d);
  return d;
}

extern "C" void tpu_dut_destroy(tpu_dut_t *d) {
  if (!d) return;
  delete d->v;
  delete d;
}

extern "C" void tpu_dut_reset(tpu_dut_t *d) {
  inputs_idle(d);
  d->v->rst_n = 0;
  d->v->clk = 0;
  for (int i = 0; i < 4; i++) tick(d);
  d->v->rst_n = 1;
  tick(d);
  d->state = TPU_S_IDLE;
}

extern "C" void tpu_dut_dram_write(tpu_dut_t *d, uint32_t addr,
                                   const uint8_t *buf, uint32_t n) {
  size_t i = 0;
  while (i < n) {
    uint32_t wa = (addr + (uint32_t)i) & ~3u;
    uint32_t wd = 0;
    uint8_t strb = 0;
    for (uint32_t b = 0; b < 4; b++) {
      size_t idx = (size_t)(wa + b - addr);
      if (wa + b >= addr && idx < n) {
        wd |= (uint32_t)buf[idx] << (8 * b);
        strb |= (uint8_t)(1u << b);
      }
    }
    d->v->dram_we = 1;
    d->v->dram_addr = wa;
    d->v->dram_wdata = wd;
    d->v->dram_wstrb = strb;
    tick(d);
    d->v->dram_we = 0;
    d->v->dram_wstrb = 0;
    i = (size_t)(wa + 4 - addr);
  }
}

static uint32_t dram_read32(tpu_dut_t *d, uint32_t addr) {
  d->v->dram_re = 1;
  d->v->dram_addr = addr;
  d->v->eval();
  uint32_t v = d->v->dram_rdata;
  d->v->dram_re = 0;
  return v;
}

extern "C" void tpu_dut_dram_read(tpu_dut_t *d, uint32_t addr, uint8_t *buf,
                                  uint32_t n) {
  for (uint32_t i = 0; i < n; i += 4) {
    uint32_t wa = (addr + i) & ~3u;
    uint32_t w = dram_read32(d, wa);
    for (uint32_t b = 0; b < 4 && i + b < n; b++) {
      uint32_t a = addr + i + b;
      if (a < wa) continue;
      buf[i + b] = (uint8_t)(w >> (8 * (a - wa)));
    }
  }
}

extern "C" uint32_t tpu_dut_csr_read(tpu_dut_t *d, uint32_t off) {
  d->v->cfg_re = 1;
  d->v->cfg_addr = off & 0xFFFu;
  d->v->eval();
  uint32_t v = d->v->cfg_rdata;
  d->v->cfg_re = 0;
  return v;
}

extern "C" void tpu_dut_csr_write(tpu_dut_t *d, uint32_t off, uint32_t val) {
  d->v->cfg_we = 1;
  d->v->cfg_addr = off & 0xFFFu;
  d->v->cfg_wdata = val;
  tick(d);
  d->v->cfg_we = 0;
}

extern "C" void tpu_dut_tmem_write(tpu_dut_t *d, uint32_t idx, uint32_t val) {
  d->v->tm_h_we = 1;
  d->v->tm_h_addr = idx & 0x3FFFu;
  d->v->tm_h_data = val;
  tick(d);
  d->v->tm_h_we = 0;
}

extern "C" uint32_t tpu_dut_tmem_read(tpu_dut_t *d, uint32_t idx) {
  d->v->tm_h_addr = idx & 0x3FFFu;
  d->v->eval();
  return d->v->tm_h_rdata;
}

extern "C" void tpu_dut_act_write(tpu_dut_t *d, uint32_t idx, uint32_t off,
                                  uint64_t data) {
  d->v->act_h_we = 1;
  d->v->act_h_idx = idx & 0x7FFu;
  d->v->act_h_off = off & 0xFu;
  d->v->act_h_data = data;
  tick(d);
  d->v->act_h_we = 0;
}

extern "C" int tpu_dut_run(tpu_dut_t *d, int max_cycles, uint32_t *retire,
                           int max_retire) {
  int prev = d->v->dbg_state;
  int nr = 0;
  int c = 0;
  while (c < max_cycles) {
    tick(d);
    c++;
    int st = d->v->dbg_state;
    if (st == TPU_S_DRAIN && prev != TPU_S_DRAIN) {
      if (nr < max_retire) retire[nr++] = tpu_dut_csr_read(d, TPU_CS_DBG_DESC);
    }
    prev = st;
    d->state = st;
    if (st == TPU_S_HALT || st == TPU_S_ERR) break;
  }
  // CSR 落定（与 tb_tpu 一致）
  tick(d);
  tick(d);
  return nr;
}

extern "C" int tpu_dut_state(tpu_dut_t *d) { return d->state; }
