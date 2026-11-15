//=============================================================================
// tpu_mem_model.sv -- 可配读延迟的 DRAM 模型（仿真/性能模型）
//
// 64 B 读（TPU 的 beat 口）、4 B 读（scale 窗口）、64 B 带字节掩码写。
// 地址按 MEM_BYTES 回绕，避免越界。
//
// RD_LATENCY = 0：完全组合读，与改造前逐周期等价（默认，回归口径不变）。
// RD_LATENCY = L：读地址经 L 级流水后在组合读数据，即 t 拍发的地址在 t+L 拍
//                 返回数据；写口仍是同步写。只延迟地址而不缓存数据，对
//                 "读后写同一地址"的流不改变语义（V1 用例均无该模式），
//                 但能真实暴露消费侧缺少等待状态的问题。
//
// dbg_* 是独立的后门 4 B 组合读通道，不受 RD_LATENCY 影响（difftest 预装载
// / 回读用）。
//=============================================================================
module tpu_mem_model #(
  parameter int MEM_BYTES  = 1 << 20,
  parameter int RD_LATENCY = 0
) (
  input  logic         clk,
  input  logic [31:0]  rd64_addr,
  output logic [511:0] rd64_data,
  input  logic [31:0]  rd8_addr,
  output logic [31:0]  rd8_data,
  input  logic         we,
  input  logic [31:0]  waddr,
  input  logic [511:0] wdata,
  input  logic [63:0]  wstrb,
  // 后门（组合，无延迟）
  input  logic [31:0]  dbg_addr,
  output logic [31:0]  dbg_data
);

  logic [7:0] mem [MEM_BYTES];

  int unsigned a64, a8, adbg;

  always_comb begin
    a64  = int'(rd64_addr[31:0]) & (MEM_BYTES - 1);
    a8   = int'(rd8_addr[31:0]) & (MEM_BYTES - 1);
    adbg = int'(dbg_addr[31:0]) & (MEM_BYTES - 1);
    for (int i = 0; i < 4; i++)
      dbg_data[i*8 +: 8] = mem[(adbg + i) & (MEM_BYTES - 1)];
  end

  generate
    if (RD_LATENCY <= 0) begin : g_comb
      always_comb begin
        for (int i = 0; i < 64; i++)
          rd64_data[i*8 +: 8] = mem[(a64 + i) & (MEM_BYTES - 1)];
        for (int i = 0; i < 4; i++)
          rd8_data[i*8 +: 8] = mem[(a8 + i) & (MEM_BYTES - 1)];
      end
    end else begin : g_lat
      int unsigned a64_p [RD_LATENCY];
      int unsigned a8_p  [RD_LATENCY];

      always_ff @(posedge clk) begin
        a64_p[0] <= a64;
        a8_p[0]  <= a8;
        for (int i = 1; i < RD_LATENCY; i++) begin
          a64_p[i] <= a64_p[i-1];
          a8_p[i]  <= a8_p[i-1];
        end
      end

      always_comb begin
        for (int i = 0; i < 64; i++)
          rd64_data[i*8 +: 8] = mem[(a64_p[RD_LATENCY-1] + i) & (MEM_BYTES - 1)];
        for (int i = 0; i < 4; i++)
          rd8_data[i*8 +: 8] = mem[(a8_p[RD_LATENCY-1] + i) & (MEM_BYTES - 1)];
      end
    end
  endgenerate

  always_ff @(posedge clk) begin
    if (we) begin
      for (int i = 0; i < 64; i++)
        if (wstrb[i])
          mem[(int'(waddr[31:0]) + i) & (MEM_BYTES - 1)] <= wdata[i*8 +: 8];
    end
  end

  initial begin
    for (int i = 0; i < MEM_BYTES; i++) mem[i] = 8'h0;
  end

endmodule
