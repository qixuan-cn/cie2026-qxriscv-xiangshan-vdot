//=============================================================================
// tpu_act.sv -- 激活 RAM：ACT_ROWS x ACT_BLOCKS x 64 B + 每 (row,block) 8 B scale
//
// 写口：QUANT（或仿真预装载）按 8 B 写，偏移 0..7 = 数据，8 = scale 记录。
// 读口：8 个组合读口，返回 64 B 激活 + 8 B scale（Sa + 4xda）。
//=============================================================================
module tpu_act #(
  parameter int ROWS   = 64,
  parameter int BLOCKS = 32,
  parameter int NRD    = 8
) (
  input  logic         clk,
  // 读口
  input  logic [10:0]  rd_idx  [NRD],
  output logic [511:0] rd_dat  [NRD],
  output logic [63:0]  rd_scl  [NRD],
  // 写口（8 B）
  input  logic         we,
  input  logic [10:0]  w_idx,
  input  logic [3:0]   w_off,
  input  logic [63:0]  w_data
);

  localparam int NBLK = ROWS * BLOCKS;

  logic [7:0] dat [NBLK * 64];
  logic [7:0] scl [NBLK * 8];

  always_comb begin
    for (int p = 0; p < NRD; p++) begin
      int unsigned b64;
      int unsigned b8;
      b64 = int'(rd_idx[p]) * 64;
      b8  = int'(rd_idx[p]) * 8;
      for (int i = 0; i < 64; i++) rd_dat[p][i*8 +: 8] = dat[b64 + i];
      for (int i = 0; i < 8; i++)  rd_scl[p][i*8 +: 8] = scl[b8 + i];
    end
  end

  always_ff @(posedge clk) begin
    if (we) begin
      if (w_off < 4'd8) begin
        for (int b = 0; b < 8; b++)
          dat[int'(w_idx) * 64 + int'(w_off) * 8 + b] <= w_data[b*8 +: 8];
      end else begin
        for (int b = 0; b < 8; b++)
          scl[int'(w_idx) * 8 + b] <= w_data[b*8 +: 8];
      end
    end
  end

endmodule
