//=============================================================================
// tpu_quant.sv -- TMEM -> ACT 的打包搬运 + scale 记录落盘（V1）
//
// V1 的 QUANT 只做"给定尺度的打包通路"：amax / Sw 由 RVV 侧（vdot 量化例程）
// 算好并写进 TMEM，本单元负责：
//   1) 每行 64 B 数据（16 个 TMEM 字）写入 ACT block 的 64 B；
//   2) 每行 8 B scale 记录（2 个 TMEM 字：Sa + 4xda）写入 ACT 的 scale 记录。
// 描述符：A_addr = 数据源 TMEM 字地址；B_addr = scale 源 TMEM 字地址；
//         C_addr = ACT 线性 block 基号；M = 行数；lda = 数据行步长（字节）。
// 行 j 的数据 = TMEM[A_addr + j*lda/4 .. +16]，scale = TMEM[B_addr + j*2 .. +2]。
//=============================================================================
module tpu_quant
  import tpu_pkg::*;
(
  input  logic         clk,
  input  logic         rst_n,
  input  logic         start,
  input  logic [6:0]   d_m,
  input  logic [31:0]  d_a_addr,
  input  logic [31:0]  d_b_addr,
  input  logic [31:0]  d_c_addr,
  input  logic [31:0]  d_lda,
  output logic         done,
  output logic [31:0]  dma_bytes,
  // TMEM 源读口
  output logic [13:0]  tm_raddr0,
  input  logic [31:0]  tm_rdata0,
  // ACT 写口
  output logic         act_we,
  output logic [10:0]  act_idx,
  output logic [3:0]   act_off,
  output logic [63:0]  act_wdata
);

  typedef enum logic [2:0] {Q_IDLE, Q_RD, Q_WR, Q_DONE} q_state_t;
  q_state_t    state;
  logic [6:0]  row;
  logic [4:0]  widx;          // 0..15 数据字，16..17 scale 字
  logic [511:0] blkbuf;
  logic [63:0]  sbuf;
  logic [31:0]  base_w;
  logic [31:0]  sbase_w;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state     <= Q_IDLE;
      done      <= 1'b0;
      act_we    <= 1'b0;
      row       <= 7'd0;
      widx      <= 5'd0;
      blkbuf       <= 512'd0;
      sbuf      <= 64'd0;
      base_w    <= 32'd0;
      sbase_w   <= 32'd0;
      dma_bytes <= 32'd0;
    end else begin
      done   <= 1'b0;
      act_we <= 1'b0;

      case (state)
        Q_IDLE: begin
          if (start) begin
            row     <= 7'd0;
            widx    <= 5'd0;
            base_w  <= d_a_addr;
            sbase_w <= d_b_addr;
            blkbuf     <= 512'd0;
            sbuf    <= 64'd0;
            state   <= Q_RD;
          end
        end
        // 逐字读源：16 个数据字 + 2 个 scale 字
        Q_RD: begin
          if (widx < 5'd16)
            blkbuf[widx*32 +: 32] <= tm_rdata0;
          else if (widx == 5'd16)
            sbuf[31:0] <= tm_rdata0;
          else
            sbuf[63:32] <= tm_rdata0;
          if (widx == 5'd17) begin
            widx  <= 5'd0;
            state <= Q_WR;
          end else begin
            widx <= widx + 5'd1;
          end
        end
        // 逐 8 B 写 ACT：8 个数据字 + 1 个 scale 字
        Q_WR: begin
          act_we <= 1'b1;
          act_idx <= 11'(d_c_addr + 32'(row));
          act_off <= (widx == 5'd8) ? 4'd8 : widx[3:0];
          act_wdata <= (widx == 5'd8) ? sbuf : blkbuf[widx*64 +: 64];
          if (widx == 5'd8) begin
            widx  <= 5'd0;
            if (32'(row) + 32'd1 < 32'(d_m)) begin
              row     <= row + 7'd1;
              base_w  <= base_w + (d_lda >> 2);
              sbase_w <= sbase_w + 32'd2;
              state   <= Q_RD;
            end else begin
              state <= Q_DONE;
            end
          end else begin
            widx <= widx + 5'd1;
          end
        end
        Q_DONE: begin
          done      <= 1'b1;
          dma_bytes <= 32'(d_m) * 32'd72;
          state     <= Q_IDLE;
        end
        default: state <= Q_IDLE;
      endcase
    end
  end

  // 源地址：数据字优先，读完 16 字后读 2 个 scale 字
  always_comb begin
    if (widx < 5'd16) tm_raddr0 = 14'(base_w + 32'(widx));
    else              tm_raddr0 = 14'(sbase_w + 32'(widx) - 32'd16);
  end

endmodule
