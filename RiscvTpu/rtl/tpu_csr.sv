//=============================================================================
// tpu_csr.sv -- CSR 窗口、计数器、中断与门铃（docs/03 §4）
//
// 布局族与 openTPU 的 observability 对齐：ID/VERSION/CTRL/STATUS/CYCLES/
// ICOUNT/端口计数/CAPS，另加 per-precision 退休计数与 DBG_*。
//=============================================================================
module tpu_csr
  import tpu_pkg::*;
(
  input  logic        clk,
  input  logic        rst_n,
  // CSR 窗口
  input  logic        cfg_we,
  input  logic        cfg_re,
  input  logic [11:0] cfg_addr,
  input  logic [31:0] cfg_wdata,
  output logic [31:0] cfg_rdata,
  // 状态输入（来自 top）
  input  logic        i_busy,
  input  logic        i_halted,
  input  logic        i_tmem_dirty,
  input  logic        i_timeout,
  input  logic [3:0]  i_dbg_state,
  input  logic [31:0] i_dbg_desc,
  input  logic [31:0] i_dbg_acc0,
  input  logic [31:0] i_dbg_acc1,
  // 事件
  input  logic        ev_busy,
  input  logic        ev_desc_done,
  input  logic        ev_mem_stall,
  input  logic [1:0]  ev_prec,
  input  logic [63:0] ev_mac,
  input  logic [63:0] ev_dma,
  // 错误
  input  logic        i_err_set,
  input  logic [7:0]  i_err_code,
  // 控制输出
  output logic        o_soft_reset,
  output logic        o_start_p,
  output logic [31:0] o_dq_head,
  output logic [31:0] o_dq_tail,
  // 硬件推进
  input  logic        head_adv,
  input  logic [31:0] head_next,
  input  logic        tail_set,
  input  logic [31:0] tail_val,
  output logic        irq
);

  logic [7:0]  ctrl;
  logic [7:0]  err_code;
  logic        irq_pend;
  logic [31:0] dq_base, dq_head, dq_tail;
  logic [63:0] cycles;
  logic [31:0] icount;
  logic [63:0] mac;
  logic [63:0] dma;
  logic [31:0] stall_mem;
  logic [31:0] ret_i8, ret_i4, ret_tn;

  assign o_soft_reset = cfg_we && (cfg_addr == CS_CTRL) && cfg_wdata[1];
  assign o_dq_head    = dq_head;
  assign o_dq_tail    = dq_tail;
  assign o_start_p    = cfg_we && (((cfg_addr == CS_CTRL) && cfg_wdata[0]) ||
                                   (cfg_addr == CS_DQ_HEAD));
  assign irq          = irq_pend;

  // ---------------------------------------------------------------- 写
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      ctrl     <= 8'd0;
      err_code <= 8'd0;
      irq_pend <= 1'b0;
      dq_base  <= 32'd0;
      dq_head  <= 32'd0;
      dq_tail  <= 32'd0;
      cycles   <= 64'd0;
      icount   <= 32'd0;
      mac      <= 64'd0;
      dma      <= 64'd0;
      stall_mem<= 32'd0;
      ret_i8   <= 32'd0;
      ret_i4   <= 32'd0;
      ret_tn   <= 32'd0;
    end else begin
      if (cfg_we) begin
        case (cfg_addr)
          CS_CTRL:    ctrl    <= cfg_wdata[7:0];
          CS_DQ_BASE: dq_base <= cfg_wdata;
          CS_DQ_HEAD: dq_head <= cfg_wdata;
          CS_ERROR:   if (cfg_wdata[0]) err_code <= 8'd0;
          default: ;
        endcase
      end
      if (cfg_we && (cfg_addr == CS_CTRL) && cfg_wdata[2]) begin
        cycles <= 64'd0;
        icount <= 32'd0;
        mac    <= 64'd0;
        dma    <= 64'd0;
        stall_mem <= 32'd0;
        ret_i8 <= 32'd0;
        ret_i4 <= 32'd0;
        ret_tn <= 32'd0;
      end
      if (ev_busy) cycles <= cycles + 64'd1;
      if (ev_busy && ev_mem_stall) stall_mem <= stall_mem + 32'd1;
      if (ev_desc_done) begin
        icount <= icount + 32'd1;
        mac    <= mac + ev_mac;
        dma    <= dma + ev_dma;
        case (ev_prec)
          2'd0:    ret_i8 <= ret_i8 + 32'd1;
          2'd1:    ret_i4 <= ret_i4 + 32'd1;
          default: ret_tn <= ret_tn + 32'd1;
        endcase
      end
      if (head_adv) dq_head <= head_next;
      if (tail_set) dq_tail <= tail_val;
      if (i_err_set) err_code <= i_err_code;
      if (i_halted && ctrl[3]) irq_pend <= 1'b1;
      if (o_start_p) irq_pend <= 1'b0;
    end
  end

  // ---------------------------------------------------------------- 读
  always_comb begin
    cfg_rdata = 32'h0;
    if (cfg_re) begin
      case (cfg_addr)
        CS_ID:        cfg_rdata = TPU_ID;
        CS_VERSION:   cfg_rdata = TPU_VERSION;
        CS_CTRL:      cfg_rdata = {24'd0, ctrl};
        CS_STATUS:    cfg_rdata = {24'd0, i_tmem_dirty, irq_pend, i_timeout, 1'b0,
                                   (dq_head == dq_tail), (err_code != 8'd0),
                                   i_halted, i_busy};
        CS_DQ_BASE:   cfg_rdata = dq_base;
        CS_DQ_HEAD:   cfg_rdata = dq_head;
        CS_DQ_TAIL:   cfg_rdata = dq_tail;
        CS_CYCLES_LO: cfg_rdata = cycles[31:0];
        CS_CYCLES_HI: cfg_rdata = cycles[63:32];
        CS_ICOUNT:    cfg_rdata = icount;
        CS_MAC_LO:    cfg_rdata = mac[31:0];
        CS_MAC_HI:    cfg_rdata = mac[63:32];
        CS_STALL_MEM: cfg_rdata = stall_mem;
        CS_RET_INT8:  cfg_rdata = ret_i8;
        CS_RET_INT4:  cfg_rdata = ret_i4;
        CS_RET_TERN:  cfg_rdata = ret_tn;
        CS_CAPS:      cfg_rdata = CAPS_VAL;
        CS_ERROR:     cfg_rdata = {24'd0, err_code};
        CS_DMA_LO:    cfg_rdata = dma[31:0];
        CS_DMA_HI:    cfg_rdata = dma[63:32];
        CS_DBG_DESC:  cfg_rdata = i_dbg_desc;
        CS_DBG_STATE: cfg_rdata = {28'd0, i_dbg_state};
        CS_DBG_ACC0:  cfg_rdata = i_dbg_acc0;
        CS_DBG_ACC1:  cfg_rdata = i_dbg_acc1;
        default:      cfg_rdata = 32'h0;
      endcase
    end
  end

endmodule
