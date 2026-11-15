//=============================================================================
// tpu_tmem.sv -- 64 KiB tile 存储（16384 x 32 bit）
//
// 1 个同步写口（MXU epilogue / COPY / QUANT 共用）、2 个组合读口、
// 1 个 host 口（仿真预装载/回读；真实设计里由 DMA 取代）。
//=============================================================================
module tpu_tmem #(
  parameter int WORDS = 16384
) (
  input  logic        clk,
  // 写口
  input  logic        we,
  input  logic [13:0] waddr,
  input  logic [31:0] wdata,
  // 读口
  input  logic [13:0] raddr0,
  output logic [31:0] rdata0,
  input  logic [13:0] raddr1,
  output logic [31:0] rdata1,
  // host 口
  input  logic        hwe,
  input  logic [13:0] haddr,
  input  logic [31:0] hdata,
  output logic [31:0] hrdata
);

  logic [31:0] mem [WORDS];

  assign rdata0 = mem[raddr0];
  assign rdata1 = mem[raddr1];
  assign hrdata = mem[haddr];

  always_ff @(posedge clk) begin
    if (we)  mem[waddr] <= wdata;
    if (hwe) mem[haddr] <= hdata;
  end

endmodule
