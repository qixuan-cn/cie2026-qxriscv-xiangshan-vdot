//=============================================================================
// tpu_mxu.sv -- 8 列 x 64 深精确整数点积阵列 + fp32 epilogue
//
// 一轮 (n, jg) 的处理：
//   MX_SW    读权重 scale 行的行尺度 Sw[n]
//   MX_WR    per block：发权重块地址、scale 块地址、8 个 ACT 读块号
//   MX_CALC  取回数据，4 个 group 各 16 元素精确求和，按 2^sh 移位累加（48-bit 契约）
//   MX_EPI*  每列 2 拍：读 C_old（ACC）→ 3 次舍入乘 + RELU + fadd → 写 TMEM
//
// 与模型的一致性：所有浮点都走 tpu_pkg 的 f_mul/f_add/f_i2f48/f_scale_pow2。
//=============================================================================
module tpu_mxu
  import tpu_pkg::*;
#(
  // DRAM 读延迟（拍）。0 = 组合读，与 V1 基线逐周期一致。
  parameter int RD_LATENCY = 0
)
(
  input  logic         clk,
  input  logic         rst_n,
  // 命令
  input  logic         start,
  input  logic [6:0]   d_m,
  input  logic [15:0]  d_n,
  input  logic [15:0]  d_k,
  input  logic [1:0]   d_prec,
  input  logic [7:0]   d_flags,
  input  logic [31:0]  d_a_addr,
  input  logic [31:0]  d_scale_addr,
  input  logic [15:0]  d_srs,
  input  logic [15:0]  d_k0,
  input  logic [31:0]  d_lda,
  input  logic [31:0]  d_ldc,
  input  logic [31:0]  d_c_addr,
  input  logic [31:0]  d_b_addr,
  output logic         done,
  output logic         err_ovf,
  output logic [63:0]  mac_count,
  output logic         stall_mem,
  // 权重块读口（DRAM）
  output logic [31:0]  w_raddr,
  input  logic [511:0] w_rdata,
  // scale 读口（DRAM，4 B 窗口）
  output logic [31:0]  s_raddr,
  input  logic [31:0]  s_rdata,
  // ACT 读口
  output logic [10:0]  act_idx [MCOLS],
  input  logic [511:0] act_dat [MCOLS],
  input  logic [63:0]  act_scl [MCOLS],
  // TMEM
  output logic [13:0]  tm_raddr0,
  input  logic [31:0]  tm_rdata0,
  output logic         tm_we,
  output logic [13:0]  tm_waddr,
  output logic [31:0]  tm_wdata,
  // 调试
  output logic [31:0]  dbg_acc0,
  output logic [31:0]  dbg_acc1
);

  // ---------------------------------------------------------------- 状态
  typedef enum logic [2:0] {
    MX_IDLE, MX_SW, MX_WR, MX_CALC, MX_EPI0, MX_EPI1, MX_DONE
  } mx_state_t;

  mx_state_t           state;
  logic [15:0]         cfg_n;
  logic [6:0]          cfg_m;
  logic [15:0]         cfg_k, cfg_kb;
  logic [1:0]          cfg_prec;
  logic [7:0]          cfg_flags;
  logic [31:0]         cfg_a, cfg_sc;
  logic [15:0]         cfg_srs, cfg_k0;
  logic [31:0]         cfg_lda, cfg_ldc, cfg_c, cfg_b;
  logic [3:0]          col;
  logic [2:0]          jg;
  logic [5:0]          kb_i;
  logic [2:0]          epi_c;
  logic [2:0]          rwait;          // DRAM 读等待计数
  logic signed [63:0]  acc [MCOLS];
  logic [31:0]         sw_reg;
  logic [31:0]         sa_reg [MCOLS];

  // ---------------------------------------------------------------- 组合地址
  logic [31:0] blk_b, srs_eff;
  logic [6:0]  blk_cnt;

  // srs = 0 表示紧凑排列（行内只有实际用到的 block）
  always_comb begin
    if (cfg_srs != 16'd0)
      srs_eff = 32'(cfg_srs);
    else if (cfg_prec == 2'd0)
      srs_eff = 32'd4 + 32'(cfg_kb);
    else
      srs_eff = 32'd8 + 32'd4 * 32'(cfg_kb);
  end

  // 末块可能的不足 64 个元素（其余按 0 参与，等价 ZERO_PAD）
  always_comb begin
    if (kb_i == 6'(cfg_kb - 16'd1))
      blk_cnt = 7'(cfg_k - (16'(kb_i) << 6));
    else
      blk_cnt = 7'd64;
  end

  always_comb begin
    blk_b = 32'(blk_bytes(cfg_prec));
    w_raddr = 32'(cfg_a) + 32'(col) * cfg_lda + 32'(kb_i) * blk_b;
    if (state == MX_SW)
      s_raddr = 32'(cfg_sc) + 32'(col) * srs_eff;
    else if (cfg_prec == 2'd0)
      s_raddr = 32'(cfg_sc) + 32'(col) * srs_eff + 32'd4 + 32'(cfg_k0) + 32'(kb_i);
    else
      s_raddr = 32'(cfg_sc) + 32'(col) * srs_eff + 32'd8 + 32'd4 * (32'(cfg_k0) + 32'(kb_i));
  end

  always_comb begin
    for (int c = 0; c < MCOLS; c++)
      act_idx[c] = 11'(cfg_b + 32'((jg * MCOLS + c) * ACT_BLOCKS + kb_i));
  end

  // ---------------------------------------------------------------- group 求和
  function automatic logic signed [23:0] grp_sum(
    input logic [511:0] ab,
    input logic [511:0] wb,
    input logic [1:0]   prec,
    input int           g,
    input int           cnt
  );
    logic signed [15:0] a, w, p;
    logic signed [23:0] s;
    logic [7:0]         byt;
    logic [3:0]         nib;
    logic [1:0]         code;
    int                 i, e;
    s = 24'sd0;
    for (i = 0; i < GROUP; i++) begin
      e = g * GROUP + i;
      if (e < cnt) begin
        a = $signed({{8{ab[e*8 + 7]}}, ab[e*8 +: 8]});
        if (prec == 2'd0) begin
          w = $signed({{8{wb[e*8 + 7]}}, wb[e*8 +: 8]});
        end else if (prec == 2'd1) begin
          byt = wb[(e/2)*8 +: 8];
          nib = 4'((byt >> (4 * (e % 2))) & 8'h0F);
          w   = $signed({{12{nib[3]}}, nib});
        end else begin
          byt  = wb[(e/4)*8 +: 8];
          code = 2'(byt >> (2 * (e % 4)));
          w    = (code == 2'd1) ? 16'sd1 : (code == 2'd2) ? -16'sd1 : 16'sd0;
        end
        p = 16'(a * w);
        s = s + {{8{p[15]}}, p};
      end
    end
    return s;
  endfunction

  // ---------------------------------------------------------------- 派生
  logic [13:0] epi_addr, tm_waddr_r;
  logic [31:0] nxt_jg;
  assign epi_addr = 14'(cfg_c + 32'((jg * MCOLS + 32'(epi_c)) * (cfg_ldc >> 2) + 32'(col)));
  assign nxt_jg   = 32'(jg + 3'd1) * 32'(MCOLS);
  assign tm_raddr0 = epi_addr;
  assign tm_waddr  = tm_waddr_r;
  assign dbg_acc0  = acc[0][31:0];
  assign dbg_acc1  = acc[1][31:0];

  // 读延迟等待：MX_SW（Sw）与 MX_WR（权重/scale/ACT 地址）各需 RD_LATENCY 拍
  assign stall_mem = ((state == MX_SW) && (int'(rwait) < RD_LATENCY)) ||
                     ((state == MX_WR) && (int'(rwait) + 1 < RD_LATENCY));

  // ---------------------------------------------------------------- 主状态机
  /* verilator lint_off BLKSEQ */
  always_ff @(posedge clk or negedge rst_n) begin
    logic signed [63:0] sum_tmp [MCOLS];
    logic signed [63:0] term;
    logic signed [23:0] gs;
    logic [5:0]         mul_g;
    int                 sh_g;
    logic [31:0]        sc_word;
    logic [7:0]         dw_g;
    logic [31:0]        y_val;
    if (!rst_n) begin
      state     <= MX_IDLE;
      done      <= 1'b0;
      tm_we     <= 1'b0;
      tm_wdata  <= 32'h0;
      tm_waddr_r<= 14'd0;
      err_ovf   <= 1'b0;
      mac_count <= 64'd0;
      col       <= 4'd0;
      jg        <= 3'd0;
      kb_i      <= 6'd0;
      epi_c     <= 3'd0;
      rwait     <= 3'd0;
      sw_reg    <= 32'h0;
      for (int c = 0; c < MCOLS; c++) begin
        acc[c]    <= 64'sd0;
        sa_reg[c] <= 32'h0;
      end
    end else begin
      done  <= 1'b0;
      tm_we <= 1'b0;

      case (state)
        // ---------------------------------------------------------- 启动
        MX_IDLE: begin
          if (start) begin
            cfg_m     <= d_m;
            cfg_n     <= d_n;
            cfg_k     <= d_k;
            cfg_kb    <= (d_k + 16'd63) >> 6;
            cfg_prec  <= d_prec;
            cfg_flags <= d_flags;
            cfg_a     <= d_a_addr;
            cfg_sc    <= d_scale_addr;
            cfg_srs   <= d_srs;
            cfg_k0    <= d_k0;
            cfg_lda   <= d_lda;
            cfg_ldc   <= d_ldc;
            cfg_c     <= d_c_addr;
            cfg_b     <= d_b_addr;
            col       <= 4'd0;
            jg        <= 3'd0;
            kb_i      <= 6'd0;
            epi_c     <= 3'd0;
            rwait     <= 3'd0;
            err_ovf   <= 1'b0;
            for (int c = 0; c < MCOLS; c++) acc[c] <= 64'sd0;
            state     <= MX_SW;
          end
        end
        // ---------------------------------------------------------- 读 Sw
        MX_SW: begin
          if (int'(rwait) >= RD_LATENCY) begin
            sw_reg <= s_rdata[31:0];
            rwait  <= 3'd0;
            state  <= MX_WR;
          end else begin
            rwait <= rwait + 3'd1;
          end
        end
        // ---------------------------------------------------------- 发块地址
        MX_WR: begin
          if (int'(rwait) + 1 >= RD_LATENCY) begin
            rwait <= 3'd0;
            state <= MX_CALC;
          end else begin
            rwait <= rwait + 3'd1;
          end
        end
        // ---------------------------------------------------------- 精确累加
        MX_CALC: begin
          sc_word = s_rdata[31:0];
          dw_g    = (cfg_prec == 2'd0) ? s_rdata[7:0] : sc_word[7:0];
          for (int c = 0; c < MCOLS; c++) begin
            if (32'(jg * MCOLS + c) < 32'(cfg_m)) begin
              if (kb_i == 6'd0) sa_reg[c] <= act_scl[c][31:0];
              // 每个 (col, jg) 的累加器从 0 开始（kb_i==0 即新一列的起点）
              sum_tmp[c] = (kb_i == 6'd0) ? 64'sd0 : acc[c];
              for (int g = 0; g < NGROUP; g++) begin
                sh_g  = w_shift(cfg_prec) - int'(dw_g) - int'(act_scl[c][(4+g)*8 +: 8]);
                mul_g = (cfg_prec == 2'd0) ? 6'd1 : 6'(sc_word[8 + 6*g +: 6]);
                if (sh_g >= 0) begin
                  gs   = grp_sum(act_dat[c], w_rdata, cfg_prec, g, int'(blk_cnt));
                  term = ($signed({58'd0, mul_g}) <<< sh_g) * {{40{gs[23]}}, gs};
                  sum_tmp[c] = sum_tmp[c] + term;
                end
              end
              acc[c] <= sum_tmp[c];
              if (sum_tmp[c] >= 64'sd140737488355328 ||
                  sum_tmp[c] < -64'sd140737488355328)
                err_ovf <= 1'b1;
            end
          end
          if (kb_i == 6'(cfg_kb - 16'd1)) begin
            kb_i  <= 6'd0;
            epi_c <= 3'd0;
            rwait <= 3'd0;
            state <= MX_EPI0;
          end else begin
            kb_i  <= kb_i + 6'd1;
            state <= MX_WR;
          end
        end
        // ---------------------------------------------------------- epilogue 读
        MX_EPI0: begin
          state <= MX_EPI1;
        end
        // ---------------------------------------------------------- epilogue 写
        MX_EPI1: begin
          if (32'(jg * MCOLS + epi_c) < 32'(cfg_m)) begin
            y_val = f_mul(f_mul(f_i2f48(48'(acc[epi_c])), sw_reg),
                          f_scale_pow2(sa_reg[epi_c], -w_shift(cfg_prec)));
            if (cfg_flags[F_RELU] && y_val[31] && (y_val[30:0] != 31'd0))
              y_val = 32'h0;
            if (cfg_flags[F_ACC])
              y_val = f_add(tm_rdata0, y_val);
            tm_waddr_r <= epi_addr;
            tm_we      <= 1'b1;
            tm_wdata   <= y_val;
          end
          if (epi_c != 3'd7) begin
            epi_c <= epi_c + 3'd1;
            state <= MX_EPI0;
          end else if (nxt_jg < 32'(cfg_m)) begin
            jg    <= jg + 3'd1;
            epi_c <= 3'd0;
            state <= MX_SW;
          end else if (32'(col) + 32'd1 < 32'(cfg_n)) begin
            col   <= col + 4'd1;
            jg    <= 3'd0;
            epi_c <= 3'd0;
            state <= MX_SW;
          end else begin
            state <= MX_DONE;
          end
        end
        // ---------------------------------------------------------- 收尾
        MX_DONE: begin
          mac_count <= 64'(cfg_m) * 64'(cfg_n) * 64'(cfg_kb) * 64'(D);
          done      <= 1'b1;
          state     <= MX_IDLE;
        end
        default: state <= MX_IDLE;
      endcase
    end
  end
  /* verilator lint_on BLKSEQ */

endmodule
