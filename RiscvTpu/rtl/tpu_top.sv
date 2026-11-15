//=============================================================================
// tpu_top.sv -- RiscvTpu V1 顶层
//
// 结构：CSR 窗口 + 描述符前端（FETCH/CHECK/DISPATCH/RUN/DRAIN）+
//       {tpu_mxu, tpu_quant, tpu_copy} + tpu_act + tpu_tmem
//
// 对外只有：
//   * cfg_*    : AXI4-Lite 窗口的寄存器侧（tb 直接驱动；真实 SoC 里由 axil 壳适配）
//   * mem_*    : DRAM 侧 64 B 读 / 8 B scale 读 / 64 B 写（V1 用零延迟存储器模型）
//   * act_h_*  : ACT 预装载后门（仿真/DFT；真实设计由 QUANT 写）
//   * tm_h_*   : TMEM 预装载后门（仿真/DFT；真实设计由 DMA 写）
//=============================================================================
module tpu_top
  import tpu_pkg::*;
#(
  // DRAM 读延迟（拍）。0 = 零延迟组合读，与 V1 基线逐周期一致；
  // >0 时前端/COPY/MXU 用等待状态吸收延迟，STALL_MEM 计数等待拍数。
  parameter int RD_LATENCY = 0
)
(
  input  logic         clk,
  input  logic         rst_n,
  // ---------------------------------------------------------- CSR 窗口
  input  logic         cfg_we,
  input  logic         cfg_re,
  input  logic [11:0]  cfg_addr,
  input  logic [31:0]  cfg_wdata,
  output logic [31:0]  cfg_rdata,
  output logic         irq,
  // ---------------------------------------------------------- DRAM 侧
  output logic [31:0]  mem_raddr,
  input  logic [511:0] mem_rdata,
  output logic [31:0]  mem_s_raddr,
  input  logic [31:0]  mem_s_rdata,
  output logic         mem_we,
  output logic [31:0]  mem_waddr,
  output logic [511:0] mem_wdata,
  output logic [63:0]  mem_wstrb,
  // ---------------------------------------------------------- ACT 后门
  input  logic         act_h_we,
  input  logic [10:0]  act_h_idx,
  input  logic [3:0]   act_h_off,
  input  logic [63:0]  act_h_data,
  // ---------------------------------------------------------- TMEM 后门
  input  logic         tm_h_we,
  input  logic [13:0]  tm_h_addr,
  input  logic [31:0]  tm_h_data,
  output logic [31:0]  tm_h_rdata,
  // ---------------------------------------------------------- 调试
  output logic [3:0]   dbg_state
);

  // ================================================================ 前端寄存器
  logic [3:0]   state;
  /* verilator lint_off UNUSEDSIGNAL */   // desc_buf[479:448] = w14(seq)，V1 不参与调度
  logic [511:0] desc_buf;
  /* verilator lint_on UNUSEDSIGNAL */
  logic [31:0]  cur_addr;
  logic [1:0]   unit;              // 0=none 1=mxu 2=quant 3=copy
  logic [31:0]  run_cnt;
  logic [31:0]  dma_this;
  logic         halted_r, tmem_dirty, timeout_flag;
  logic         mxu_start, q_start, c_start;
  logic [2:0]   fwait;             // FETCH 等待计数（RD_LATENCY>0 时非零）

  logic [31:0] w0, w1, w2, w3, w4, w5, w6, w7, w8, w9, w10, w11, w12, w13, w15;
  logic [7:0]  d_op, d_flags;
  logic [3:0]  d_sbuf;
  logic [1:0]  d_prec;
  logic [31:0] d_m, d_n, d_k;
  logic [31:0] d_a, d_b, d_c, d_scale;
  logic [31:0] d_lda, d_ldb, d_ldc;
  logic [31:0] d_next, d_timeout;
  logic [15:0] d_k0, d_srs;

  assign w0  = desc_buf[31:0];
  assign w1  = desc_buf[63:32];
  assign w2  = desc_buf[95:64];
  assign w3  = desc_buf[127:96];
  assign w4  = desc_buf[159:128];
  assign w5  = desc_buf[191:160];
  assign w6  = desc_buf[223:192];
  assign w7  = desc_buf[255:224];
  assign w8  = desc_buf[287:256];
  assign w9  = desc_buf[319:288];
  assign w10 = desc_buf[351:320];
  assign w11 = desc_buf[383:352];
  assign w12 = desc_buf[415:384];
  assign w13 = desc_buf[447:416];
  assign w15 = desc_buf[511:480];

  assign d_op      = w0[7:0];
  assign d_flags   = w0[15:8];
  assign d_sbuf    = w0[19:16];
  assign d_prec    = w0[21:20];
  assign d_m       = w1;
  assign d_n       = w2;
  assign d_k       = w3;
  assign d_a       = w4;
  assign d_b       = w5;
  assign d_c       = w6;
  assign d_lda     = w7;
  assign d_ldb     = w8;
  assign d_ldc     = w9;
  assign d_scale   = w10;
  assign d_next    = w11;
  assign d_k0      = w12[15:0];
  assign d_srs     = w15[15:0];
  assign d_timeout = w13;

  // ================================================================ CSR 侧
  logic        o_soft_reset, o_start_p;
  logic [31:0] dq_head, dq_tail;
  logic [31:0] head_next, tail_val;
  logic        ev_desc_done, err_set, err_code_clear;
  logic [1:0]  ev_prec;
  logic [63:0] ev_mac, ev_dma;
  logic [7:0]  err_code_in;
  logic        busy_r;
  logic        mxu_stall, copy_stall, mem_stall;

  // ================================================================ 单元互联
  logic        mxu_done, mxu_err_ovf;
  logic [63:0] mxu_mac;
  logic [31:0] mxu_w_raddr, mxu_s_raddr;
  logic [10:0] mxu_act_idx [MCOLS];
  logic [511:0] act_dat [MCOLS];
  logic [63:0] act_scl [MCOLS];
  logic [13:0] mxu_tm_raddr0;
  logic        mxu_tm_we;
  logic [13:0] mxu_tm_waddr;
  logic [31:0] mxu_tm_wdata, mxu_acc0, mxu_acc1;

  logic        q_done, q_act_we;
  logic [13:0] q_tm_raddr0;
  logic [10:0] q_act_idx;
  logic [3:0]  q_act_off;
  logic [63:0] q_act_data;
  logic [31:0] q_dma;

  logic        c_done, c_mem_we;
  logic [31:0] c_raddr, c_waddr, c_dma;
  logic [63:0] c_wstrb;
  logic [511:0] c_wdata;
  logic [13:0] c_tm_raddr1;
  logic [31:0] tm_rdata1;

  logic [31:0] tm_rdata0;
  logic [13:0] tm_raddr0;

  logic        act_we;
  logic [10:0] act_widx;
  logic [3:0]  act_woff;
  logic [63:0] act_wdata;
  logic        tm_we;
  logic [13:0] tm_waddr;
  logic [31:0] tm_wdata;

  // ================================================================ 校验
  logic [7:0] chk_err;
  always_comb begin
    chk_err = E_NONE;
    if (cur_addr[5:0] != 6'd0)
      chk_err = E_DESC_ALIGN;
    else if ((w0[31:22] != 0) || (w12[31:16] != 0) || (w15[31:16] != 0) ||
             ((d_flags & ~FLAGS_V1) != 8'd0) || (d_prec == 2'b11))
      chk_err = E_DESC_FLAGS;
    else if (!((d_op == OP_GEMM) || (d_op == OP_COPY) || (d_op == OP_QUANT) ||
               (d_op == OP_BAR) || (d_op == OP_NOP) || (d_op == OP_END)))
      chk_err = E_DESC_OP;
    else if (d_sbuf > 4'd1)
      chk_err = E_SBUF;
    else if (d_op == OP_GEMM) begin
      if ((d_m == 0) || (d_m > 32'(ACT_ROWS)) || (d_n == 0) || (d_k == 0) ||
          (d_k > 32'(ACT_BLOCKS * D)))
        chk_err = E_SHAPE;
      else if (32'(d_b) + d_m * 32'(ACT_BLOCKS) > 32'(ACT_NBLK))
        chk_err = E_DESC_RANGE;
      else if ((d_a[3:0] != 0) || (d_ldc[1:0] != 0) ||
               ((d_prec == 2'd0) ? (d_scale[1:0] != 0) : (d_scale[2:0] != 0)))
        chk_err = E_ADDR_ALIGN;
      else if (32'(d_c) + (d_m - 32'd1) * (d_ldc >> 2) + d_n > 32'(TMEM_WORDS))
        chk_err = E_DESC_RANGE;
    end else if (d_op == OP_QUANT) begin
      if ((d_m == 0) || (d_m > 32'(ACT_ROWS)))
        chk_err = E_SHAPE;
      else if ((d_a[1:0] != 0) || (d_b[1:0] != 0) || (d_lda[1:0] != 0))
        chk_err = E_ADDR_ALIGN;
      else if (32'(d_c) + d_m > 32'(ACT_NBLK))
        chk_err = E_DESC_RANGE;
    end else if (d_op == OP_COPY) begin
      if (d_m == 0) chk_err = E_SHAPE;
    end
  end

  // ================================================================ 共享端口
  always_comb begin
    mem_raddr = 32'd0;
    if ((state == S_FETCH) || (state == S_FETCH2))
      mem_raddr = dq_head;
    else if (unit == 2'd1)
      mem_raddr = mxu_w_raddr;
    else if (unit == 2'd3)
      mem_raddr = c_raddr;
  end

  assign mem_s_raddr = mxu_s_raddr;
  assign mem_we      = c_mem_we;
  assign mem_waddr   = c_waddr;
  assign mem_wdata   = c_wdata;
  assign mem_wstrb   = c_wstrb;

  assign tm_raddr0 = (unit == 2'd2) ? q_tm_raddr0 :
                     (unit == 2'd1) ? mxu_tm_raddr0 : 14'd0;

  assign act_we    = act_h_we | q_act_we;
  assign act_widx  = act_h_we ? act_h_idx  : q_act_idx;
  assign act_woff  = act_h_we ? act_h_off  : q_act_off;
  assign act_wdata = act_h_we ? act_h_data : q_act_data;

  assign tm_we    = mxu_tm_we | tm_h_we;
  assign tm_waddr = tm_h_we ? tm_h_addr : mxu_tm_waddr;
  assign tm_wdata = tm_h_we ? tm_h_data : mxu_tm_wdata;

  // 完成时推进队列
  logic        head_adv, tail_set;
  logic [31:0] eff_timeout;
  assign head_adv   = (state == S_DRAIN);
  assign head_next  = (d_next == 0) ? cur_addr : d_next;
  assign tail_set   = (state == S_DRAIN);
  assign tail_val   = cur_addr;
  assign eff_timeout= (d_timeout == 0) ? 32'd1_000_000 : d_timeout;
  assign err_code_clear = cfg_we && (cfg_addr == CS_ERROR) && cfg_wdata[0];
  assign busy_r     = (state != S_IDLE) && (state != S_HALT) && (state != S_ERR);
  assign ev_desc_done = (state == S_DRAIN) && (d_op != OP_NOP);
  assign ev_prec    = (d_op == OP_GEMM) ? d_prec : 2'b11;
  assign ev_mac     = (d_op == OP_GEMM) ? mxu_mac : 64'd0;
  assign dma_this   = (unit == 2'd2) ? 32'(q_dma) :
                      (unit == 2'd3) ? 32'(c_dma) : 32'd0;
  assign ev_dma     = 64'(dma_this);
  assign dbg_state  = state;
  // 存储等待：任一在途单元因 DRAM 读延迟而空转
  assign mem_stall  = ((state == S_FETCH) && (int'(fwait) < RD_LATENCY)) |
                      mxu_stall | copy_stall;

  // ================================================================ 前端 FSM
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state        <= S_IDLE;
      desc_buf     <= 512'd0;
      cur_addr     <= 32'd0;
      unit         <= 2'd0;
      run_cnt      <= 32'd0;
      halted_r     <= 1'b1;
      tmem_dirty   <= 1'b0;
      timeout_flag <= 1'b0;
      err_set      <= 1'b0;
      err_code_in  <= E_NONE;
      mxu_start    <= 1'b0;
      q_start      <= 1'b0;
      c_start      <= 1'b0;
      fwait        <= 3'd0;
    end else begin
      err_set <= 1'b0;
      case (state)
        // ---------------------------------------------------------- 空闲/停机
        S_IDLE, S_HALT: begin
          if (start_pulse_now && (dq_head != dq_tail)) begin
            state        <= S_FETCH;
            halted_r     <= 1'b0;
            run_cnt      <= 32'd0;
            timeout_flag <= 1'b0;
            fwait        <= 3'd0;
          end
        end
        // ---------------------------------------------------------- 取指
        // RD_LATENCY>0 时在此停留到读数据返回；地址 dq_head 全程保持，
        // 因此等待期间重复发同一地址是安全的。
        S_FETCH: begin
          if (int'(fwait) + 1 >= RD_LATENCY)
            state <= S_FETCH2;
          else
            fwait <= fwait + 3'd1;
        end
        S_FETCH2: begin
          desc_buf <= mem_rdata;
          cur_addr <= dq_head;
          state    <= S_CHECK;
        end
        // ---------------------------------------------------------- 校验
        S_CHECK: begin
          if (chk_err != E_NONE) begin
            err_set     <= 1'b1;
            err_code_in <= chk_err;
            state       <= S_ERR;
          end else begin
            state <= S_DISP;
          end
        end
        // ---------------------------------------------------------- 发射
        S_DISP: begin
          case (d_op)
            OP_GEMM:  begin unit <= 2'd1; mxu_start <= 1'b1; end
            OP_QUANT: begin unit <= 2'd2; q_start   <= 1'b1; end
            OP_COPY:  begin unit <= 2'd3; c_start   <= 1'b1; end
            default:  unit <= 2'd0;
          endcase
          state <= S_RUN;
        end
        // ---------------------------------------------------------- 执行
        S_RUN: begin
          mxu_start <= 1'b0;
          q_start   <= 1'b0;
          c_start   <= 1'b0;
          run_cnt   <= run_cnt + 32'd1;
          if (run_cnt > eff_timeout) begin
            timeout_flag <= 1'b1;
            err_set      <= 1'b1;
            err_code_in  <= E_TIMEOUT;
            state        <= S_ERR;
          end else if (unit == 2'd1) begin
            if (mxu_done) begin
              if (mxu_err_ovf) begin
                err_set     <= 1'b1;
                err_code_in <= E_ACC_OVF;
                state       <= S_ERR;
              end else begin
                tmem_dirty <= 1'b1;
                state      <= S_DRAIN;
              end
            end
          end else if (unit == 2'd2) begin
            if (q_done) state <= S_DRAIN;
          end else if (unit == 2'd3) begin
            if (c_done) begin
              if (d_sbuf == 4'd1) tmem_dirty <= 1'b0;
              state <= S_DRAIN;
            end
          end else begin
            state <= S_DRAIN;
          end
        end
        // ---------------------------------------------------------- 排空
        S_DRAIN: begin
          unit     <= 2'd0;
          if ((d_next == 0) || (d_op == OP_END)) begin
            state    <= S_HALT;
            halted_r <= 1'b1;
          end else begin
            state <= S_FETCH;
            fwait <= 3'd0;
          end
        end
        // ---------------------------------------------------------- 错误
        S_ERR: if (err_code_clear) state <= S_IDLE;
        default: state <= S_IDLE;
      endcase
      if (o_soft_reset) begin
        state        <= S_IDLE;
        halted_r     <= 1'b1;
        unit         <= 2'd0;
        tmem_dirty   <= 1'b0;
        timeout_flag <= 1'b0;
        mxu_start    <= 1'b0;
        q_start      <= 1'b0;
        c_start      <= 1'b0;
        fwait        <= 3'd0;
      end
    end
  end

  logic start_pulse_now;
  assign start_pulse_now = o_start_p;

  // ================================================================ 例化
  tpu_csr u_csr (
    .clk (clk), .rst_n (rst_n),
    .cfg_we (cfg_we), .cfg_re (cfg_re), .cfg_addr (cfg_addr),
    .cfg_wdata (cfg_wdata), .cfg_rdata (cfg_rdata),
    .i_busy (busy_r), .i_halted (halted_r), .i_tmem_dirty (tmem_dirty),
    .i_timeout (timeout_flag), .i_dbg_state (state), .i_dbg_desc (cur_addr),
    .i_dbg_acc0 (mxu_acc0), .i_dbg_acc1 (mxu_acc1),
    .ev_busy (busy_r), .ev_desc_done (ev_desc_done), .ev_mem_stall (mem_stall),
    .ev_prec (ev_prec),
    .ev_mac (ev_mac), .ev_dma (ev_dma),
    .i_err_set (err_set), .i_err_code (err_code_in),
    .o_soft_reset (o_soft_reset), .o_start_p (o_start_p),
    .o_dq_head (dq_head), .o_dq_tail (dq_tail),
    .head_adv (head_adv), .head_next (head_next),
    .tail_set (tail_set), .tail_val (tail_val),
    .irq (irq)
  );

  tpu_mxu #(.RD_LATENCY (RD_LATENCY)) u_mxu (
    .clk (clk), .rst_n (rst_n),
    .start (mxu_start),
    .d_m (7'(d_m)), .d_n (16'(d_n)), .d_k (16'(d_k)),
    .d_prec (d_prec), .d_flags (d_flags),
    .d_a_addr (d_a), .d_scale_addr (d_scale),
    .d_srs (d_srs), .d_k0 (d_k0),
    .d_lda (d_lda), .d_ldc (d_ldc), .d_c_addr (d_c), .d_b_addr (d_b),
    .done (mxu_done), .err_ovf (mxu_err_ovf),
    .mac_count (mxu_mac), .stall_mem (mxu_stall),
    .w_raddr (mxu_w_raddr), .w_rdata (mem_rdata),
    .s_raddr (mxu_s_raddr), .s_rdata (mem_s_rdata),
    .act_idx (mxu_act_idx), .act_dat (act_dat), .act_scl (act_scl),
    .tm_raddr0 (mxu_tm_raddr0), .tm_rdata0 (tm_rdata0),
    .tm_we (mxu_tm_we), .tm_waddr (mxu_tm_waddr), .tm_wdata (mxu_tm_wdata),
    .dbg_acc0 (mxu_acc0), .dbg_acc1 (mxu_acc1)
  );

  tpu_quant u_quant (
    .clk (clk), .rst_n (rst_n),
    .start (q_start), .d_m (7'(d_m)),
    .d_a_addr (d_a), .d_b_addr (d_b), .d_c_addr (d_c), .d_lda (d_lda),
    .done (q_done), .dma_bytes (q_dma),
    .tm_raddr0 (q_tm_raddr0), .tm_rdata0 (tm_rdata0),
    .act_we (q_act_we), .act_idx (q_act_idx), .act_off (q_act_off),
    .act_wdata (q_act_data)
  );

  tpu_copy #(.RD_LATENCY (RD_LATENCY)) u_copy (
    .clk (clk), .rst_n (rst_n),
    .start (c_start), .d_mode (d_sbuf[0]), .d_m (7'(d_m)),
    .d_a_addr (d_a), .d_c_addr (d_c),
    .d_lda (d_lda), .d_ldb (d_ldb), .d_ldc (d_ldc),
    .done (c_done), .dma_bytes (c_dma), .stall_mem (copy_stall),
    .mem_raddr (c_raddr), .mem_rdata (mem_rdata),
    .mem_we (c_mem_we), .mem_waddr (c_waddr), .mem_wdata (c_wdata),
    .mem_wstrb (c_wstrb),
    .tm_raddr1 (c_tm_raddr1), .tm_rdata1 (tm_rdata1)
  );

  tpu_act u_act (
    .clk (clk),
    .rd_idx (mxu_act_idx), .rd_dat (act_dat), .rd_scl (act_scl),
    .we (act_we), .w_idx (act_widx), .w_off (act_woff), .w_data (act_wdata)
  );

  tpu_tmem u_tmem (
    .clk (clk),
    .we (tm_we), .waddr (tm_waddr), .wdata (tm_wdata),
    .raddr0 (tm_raddr0), .rdata0 (tm_rdata0),
    .raddr1 (c_tm_raddr1), .rdata1 (tm_rdata1),
    .hwe (tm_h_we), .haddr (tm_h_addr), .hdata (tm_h_data), .hrdata (tm_h_rdata)
  );

endmodule
