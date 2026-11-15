//=============================================================================
// tpu_copy.sv -- 2D strided 搬移
//
// mode 0: DRAM -> DRAM（A_addr/C_addr 为字节地址，ldb/ldc 为字节行步长）
// mode 1: TMEM -> DRAM（A_addr 为 TMEM 字地址，ldb 为字行步长；
//                       C_addr 为字节地址，ldc 为字节行步长）
//
// 每行 lda 字节，按 64 B beat 搬运，最后一个 beat 用字节掩码。
//=============================================================================
module tpu_copy
  import tpu_pkg::*;
#(
  // DRAM 读延迟（拍）。0 = 组合读，与 V1 基线逐周期一致。
  parameter int RD_LATENCY = 0
)
(
  input  logic         clk,
  input  logic         rst_n,
  input  logic         start,
  input  logic         d_mode,       // 0 = DRAM->DRAM, 1 = TMEM->DRAM
  input  logic [6:0]   d_m,
  input  logic [31:0]  d_a_addr,
  input  logic [31:0]  d_c_addr,
  input  logic [31:0]  d_lda,
  input  logic [31:0]  d_ldb,
  input  logic [31:0]  d_ldc,
  output logic         done,
  output logic [31:0]  dma_bytes,
  output logic         stall_mem,
  // DRAM
  output logic [31:0]  mem_raddr,
  input  logic [511:0] mem_rdata,
  output logic         mem_we,
  output logic [31:0]  mem_waddr,
  output logic [511:0] mem_wdata,
  output logic [63:0]  mem_wstrb,
  // TMEM
  output logic [13:0]  tm_raddr1,
  input  logic [31:0]  tm_rdata1
);

  typedef enum logic [2:0] {C_IDLE, C_RD, C_TMRD, C_WR, C_DONE} c_state_t;
  c_state_t    state;
  logic        mode;
  logic [6:0]  row;
  logic [15:0] beat;
  logic [4:0]  widx;
  logic [2:0]  rwait;          // DRAM 读等待计数
  logic [511:0] blkbuf;
  logic [31:0] src_b, dst_b, nbytes, beats;

  always_comb begin
    beats = (d_lda + 32'd63) >> 6;
    if (d_lda - 32'(beat) * 32'd64 > 32'd64) nbytes = 32'd64;
    else nbytes = d_lda - 32'(beat) * 32'd64;
    if (mode)
      src_b = 32'(d_a_addr) + 32'(row) * d_ldb + 32'(beat) * 32'd16;
    else
      src_b = d_a_addr + 32'(row) * d_ldb + 32'(beat) * 32'd64;
    dst_b = d_c_addr + 32'(row) * d_ldc + 32'(beat) * 32'd64;
  end

  assign mem_raddr  = mode ? 32'd0 : src_b;
  assign tm_raddr1  = 14'(src_b + 32'(widx));
  assign mem_waddr  = dst_b;
  assign mem_wdata  = blkbuf;
  assign mem_we     = (state == C_WR);

  assign stall_mem  = (state == C_RD) && (int'(rwait) < RD_LATENCY);

  always_comb begin
    for (int i = 0; i < 64; i++)
      mem_wstrb[i] = (32'(i) < nbytes);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state     <= C_IDLE;
      done      <= 1'b0;
      mode      <= 1'b0;
      row       <= 7'd0;
      beat      <= 16'd0;
      widx      <= 5'd0;
      rwait     <= 3'd0;
      blkbuf       <= 512'd0;
      dma_bytes <= 32'd0;
    end else begin
      done <= 1'b0;
      case (state)
        C_IDLE: begin
          if (start) begin
            mode  <= d_mode;
            row   <= 7'd0;
            beat  <= 16'd0;
            widx  <= 5'd0;
            rwait <= 3'd0;
            dma_bytes <= 32'd0;
            state <= d_mode ? C_TMRD : C_RD;
          end
        end
        // DRAM -> 缓冲（RD_LATENCY>0 时在此等待读数据）
        C_RD: begin
          if (int'(rwait) >= RD_LATENCY) begin
            blkbuf <= mem_rdata;
            rwait  <= 3'd0;
            state  <= C_WR;
          end else begin
            rwait <= rwait + 3'd1;
          end
        end
        // TMEM 逐字 -> 缓冲（16 字 = 64 B）
        C_TMRD: begin
          blkbuf[widx*32 +: 32] <= tm_rdata1;
          if (widx == 5'd15) begin
            widx  <= 5'd0;
            rwait <= 3'd0;
            state <= C_WR;
          end else begin
            widx <= widx + 5'd1;
          end
        end
        // 缓冲 -> DRAM（写在本拍，mem_we 与 mem_wdata 已就绪）
        C_WR: begin
          dma_bytes <= dma_bytes + nbytes;
          if (32'(beat) + 32'd1 < beats) begin
            beat  <= beat + 16'd1;
            rwait <= 3'd0;
            state <= mode ? C_TMRD : C_RD;
          end else if (32'(row) + 32'd1 < 32'(d_m)) begin
            row   <= row + 7'd1;
            beat  <= 16'd0;
            rwait <= 3'd0;
            state <= mode ? C_TMRD : C_RD;
          end else begin
            state <= C_DONE;
          end
        end
        C_DONE: begin
          done  <= 1'b1;
          state <= C_IDLE;
        end
        default: state <= C_IDLE;
      endcase
    end
  end

endmodule
