//=============================================================================
// tpu_sim_top.sv -- 仿真外壳：tpu_top + tpu_mem_model + 后门端口
//
// 只用于 Verilator 回归；综合时不使用本文件。
//=============================================================================
module tpu_sim_top #(
  parameter int MEM_BYTES  = 1 << 20,
  parameter int RD_LATENCY = 0
) (
  input  logic        clk,
  input  logic        rst_n,
  // CSR
  input  logic        cfg_we,
  input  logic        cfg_re,
  input  logic [11:0] cfg_addr,
  input  logic [31:0] cfg_wdata,
  output logic [31:0] cfg_rdata,
  output logic        irq,
  // ACT 后门
  input  logic        act_h_we,
  input  logic [10:0] act_h_idx,
  input  logic [3:0]  act_h_off,
  input  logic [63:0] act_h_data,
  // TMEM 后门
  input  logic        tm_h_we,
  input  logic [13:0] tm_h_addr,
  input  logic [31:0] tm_h_data,
  output logic [31:0] tm_h_rdata,
  // DRAM 后门（4 B 粒度，便于 tb 预装载/回读）
  input  logic        dram_we,
  input  logic        dram_re,
  input  logic [31:0] dram_addr,
  input  logic [31:0] dram_wdata,
  input  logic [3:0]  dram_wstrb,
  output logic [31:0] dram_rdata,
  // 调试
  output logic [3:0]  dbg_state
);

  logic [31:0]  mem_raddr, mem_s_raddr, mem_waddr;
  logic [511:0] mem_rdata, mem_wdata;
  logic [31:0]  mem_s_rdata;
  logic [63:0]  mem_wstrb;
  logic         mem_we;
  logic [511:0] rd64_data;
  logic [31:0]  rd8_data;
  logic [31:0]  dbg_data;
  logic [511:0] model_wdata;
  logic [63:0]  model_wstrb;
  logic         model_we;
  logic [31:0]  model_waddr;

  tpu_top #(.RD_LATENCY (RD_LATENCY)) u_top (
    .clk (clk), .rst_n (rst_n),
    .cfg_we (cfg_we), .cfg_re (cfg_re), .cfg_addr (cfg_addr),
    .cfg_wdata (cfg_wdata), .cfg_rdata (cfg_rdata), .irq (irq),
    .mem_raddr (mem_raddr), .mem_rdata (mem_rdata),
    .mem_s_raddr (mem_s_raddr), .mem_s_rdata (mem_s_rdata),
    .mem_we (mem_we), .mem_waddr (mem_waddr), .mem_wdata (mem_wdata),
    .mem_wstrb (mem_wstrb),
    .act_h_we (act_h_we), .act_h_idx (act_h_idx), .act_h_off (act_h_off),
    .act_h_data (act_h_data),
    .tm_h_we (tm_h_we), .tm_h_addr (tm_h_addr), .tm_h_data (tm_h_data),
    .tm_h_rdata (tm_h_rdata),
    .dbg_state (dbg_state)
  );

  assign mem_rdata  = rd64_data;
  assign mem_s_rdata= rd8_data;
  assign dram_rdata = dram_re ? dbg_data : 32'h0;

  always_comb begin
    model_wdata = {16{dram_wdata}};
    model_wstrb = {60'd0, dram_wstrb};
  end
  assign model_we    = dram_we | mem_we;
  assign model_waddr = dram_we ? dram_addr : mem_waddr;

  tpu_mem_model #(.MEM_BYTES (MEM_BYTES), .RD_LATENCY (RD_LATENCY)) u_mem (
    .clk (clk),
    .rd64_addr (mem_raddr), .rd64_data (rd64_data),
    .rd8_addr (mem_s_raddr), .rd8_data (rd8_data),
    .we (model_we), .waddr (model_waddr),
    .wdata (dram_we ? model_wdata : mem_wdata),
    .wstrb (dram_we ? model_wstrb : mem_wstrb),
    .dbg_addr (dram_addr), .dbg_data (dbg_data)
  );

endmodule
