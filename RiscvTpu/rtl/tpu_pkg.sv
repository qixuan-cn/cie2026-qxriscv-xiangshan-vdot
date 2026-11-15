//=============================================================================
// tpu_pkg.sv -- RiscvTpu V1 参数、描述符常量、fp32 收缩函数
//
// 数值契约（docs/03_功能设计规格.md §6.2）：
//   内层整数精确；epilogue 只有 3 次 fp32 舍入乘 + 可选 fadd；
//   零结果统一规范化为 +0（与 tests/ref_tpu.py 的 fp_mul/fp_add 一致），
//   这样模型与 RTL 的位精确比较不受 ±0 符号影响。
//=============================================================================
package tpu_pkg;

  // ---------------------------------------------------------------- 结构参数
  localparam int D          = 64;      // MXU 深度（每 block 元素数）
  localparam int GROUP      = D / 4;   // 每 group 元素数 = 16
  localparam int NGROUP     = 4;       // 每 block 的 group 数
  localparam int MCOLS      = 8;       // MXU 列数
  localparam int ACT_ROWS   = 64;
  localparam int ACT_BLOCKS = 32;
  localparam int ACT_NBLK   = ACT_ROWS * ACT_BLOCKS;   // 2048
  localparam int TMEM_WORDS = 16384;

  localparam int WPB        = 1;       // V1 实际实现：1 个权重块/cycle

  localparam logic [31:0] TPU_ID = 32'h5254_5650;      // "RVTP"
  localparam logic [31:0] TPU_VERSION = {8'd1, 4'd0, 4'(WPB), 8'(MCOLS), 8'(D)};

  // ---------------------------------------------------------------- op
  localparam logic [7:0] OP_GEMM  = 8'h01;
  localparam logic [7:0] OP_COPY  = 8'h02;
  localparam logic [7:0] OP_QUANT = 8'h03;
  localparam logic [7:0] OP_BAR   = 8'h04;
  localparam logic [7:0] OP_NOP   = 8'h05;
  localparam logic [7:0] OP_END   = 8'h06;

  // ---------------------------------------------------------------- flags
  /* verilator lint_off UNUSEDPARAM */
  localparam int F_ACC  = 0;
  localparam int F_RMAX = 1;
  localparam int F_ASCL = 2;
  localparam int F_RELU = 3;
  localparam int F_TB   = 4;
  localparam int F_ZP   = 5;
  localparam int F_PAIR = 6;
  // V1 实际支持的 flag 组合（其余位必须为 0，否则 DESC_FLAGS）
  localparam logic [7:0] FLAGS_V1 = (8'd1 << F_ACC) | (8'd1 << F_RELU) | (8'd1 << F_ZP);
  /* verilator lint_on UNUSEDPARAM */

  // ---------------------------------------------------------------- 错误码
  /* verilator lint_off UNUSEDPARAM */
  localparam logic [7:0] E_NONE       = 8'h00;
  localparam logic [7:0] E_DESC_ALIGN = 8'h01;
  localparam logic [7:0] E_DESC_RANGE = 8'h02;
  localparam logic [7:0] E_DESC_OP    = 8'h03;
  localparam logic [7:0] E_DESC_FLAGS = 8'h04;
  localparam logic [7:0] E_SHAPE      = 8'h05;
  localparam logic [7:0] E_ADDR_ALIGN = 8'h06;
  localparam logic [7:0] E_AXI_DECERR = 8'h07;
  localparam logic [7:0] E_AXI_SLVERR = 8'h08;
  localparam logic [7:0] E_TIMEOUT    = 8'h09;
  localparam logic [7:0] E_ACC_OVF    = 8'h0A;
  localparam logic [7:0] E_SBUF       = 8'h0B;
  /* verilator lint_on UNUSEDPARAM */

  // ---------------------------------------------------------------- 状态机
  localparam logic [3:0] S_IDLE   = 4'd0;
  localparam logic [3:0] S_FETCH  = 4'd1;
  localparam logic [3:0] S_CHECK  = 4'd2;
  localparam logic [3:0] S_DISP   = 4'd3;
  localparam logic [3:0] S_RUN    = 4'd4;
  localparam logic [3:0] S_DRAIN  = 4'd5;
  localparam logic [3:0] S_HALT   = 4'd6;
  localparam logic [3:0] S_ERR    = 4'd7;
  localparam logic [3:0] S_FETCH2 = 4'd8;   // 取指第二拍（描述符 64 B 到齐）

  // ---------------------------------------------------------------- CSR 偏移
  localparam logic [11:0] CS_ID        = 12'h000;
  localparam logic [11:0] CS_VERSION   = 12'h004;
  localparam logic [11:0] CS_CTRL      = 12'h008;
  localparam logic [11:0] CS_STATUS    = 12'h00C;
  localparam logic [11:0] CS_DQ_BASE   = 12'h010;
  localparam logic [11:0] CS_DQ_HEAD   = 12'h014;
  localparam logic [11:0] CS_DQ_TAIL   = 12'h018;
  localparam logic [11:0] CS_CYCLES_LO = 12'h01C;
  localparam logic [11:0] CS_CYCLES_HI = 12'h020;
  localparam logic [11:0] CS_ICOUNT    = 12'h024;
  localparam logic [11:0] CS_MAC_LO    = 12'h028;
  localparam logic [11:0] CS_MAC_HI    = 12'h02C;
  localparam logic [11:0] CS_STALL_MEM = 12'h030;
  localparam logic [11:0] CS_RET_INT8  = 12'h034;
  localparam logic [11:0] CS_RET_INT4  = 12'h038;
  localparam logic [11:0] CS_RET_TERN  = 12'h03C;
  localparam logic [11:0] CS_CAPS      = 12'h040;
  localparam logic [11:0] CS_ERROR     = 12'h044;
  localparam logic [11:0] CS_DMA_LO    = 12'h048;
  localparam logic [11:0] CS_DMA_HI    = 12'h04C;
  localparam logic [11:0] CS_DBG_DESC  = 12'h050;
  localparam logic [11:0] CS_DBG_STATE = 12'h054;
  localparam logic [11:0] CS_DBG_ACC0  = 12'h058;
  localparam logic [11:0] CS_DBG_ACC1  = 12'h05C;

  localparam logic [31:0] CAPS_VAL = 32'h0000_0027;    // int8|int4|tern|RELU

  // 权重/激活 block 的紧凑字节数
  function automatic int blk_bytes(input logic [1:0] prec);
    if (prec == 2'd0) return 64;
    else if (prec == 2'd1) return 32;
    else return 16;
  endfunction

  function automatic int w_shift(input logic [1:0] prec);
    return (prec == 2'd0) ? 16 : 11;
  endfunction

  // ---------------------------------------------------------------- fp32
  // 48-bit 有符号整数 -> fp32（RNE，零规范化为 +0）
  function automatic logic [31:0] f_i2f48(input logic signed [47:0] v);
    logic               sgn;
    logic [47:0]        mag;
    logic [24:0]        m25;
    logic               gd, st;
    logic [23:0]        shf;
    int                 h, i;
    sgn = v[47];
    mag = sgn ? (~v + 48'd1) : v[47:0];
    if (mag == 48'd0) return 32'h0;
    h = 0;
    for (i = 0; i < 48; i++) if (mag[i]) h = i;
    if (h >= 23) begin
      shf = 24'(mag >> (h - 23));
      m25 = {1'b0, shf};
      if (h >= 24) begin
        gd = mag[h - 24];
        st = |(mag & ((48'd1 << (h - 24)) - 48'd1));   // guard 以下才计入 sticky
      end else begin
        gd = 1'b0;
        st = 1'b0;
      end
      if (gd && (st || m25[0])) m25 = m25 + 25'd1;
      if (m25[24]) begin m25 = m25 >> 1; h = h + 1; end
    end else begin
      m25 = {1'b0, mag[23:0]} << (23 - h);
    end
    return {sgn, 8'(h + 127), m25[22:0]};
  endfunction

  // fp32 乘法：RNE、FTZ（输入/输出次正规均归零）、零规范化为 +0
  function automatic logic [31:0] f_mul(input logic [31:0] a, input logic [31:0] b);
    logic [7:0]        ea, eb;
    logic [47:0]       p;
    logic [23:0]       m24;
    logic signed [10:0] e;
    logic               sg;
    ea = a[30:23];
    eb = b[30:23];
    if (ea == 8'd0 || eb == 8'd0) return 32'h0;
    if (ea == 8'hFF || eb == 8'hFF) return 32'h0;
    sg = a[31] ^ b[31];
    p  = {1'b1, a[22:0]} * {1'b1, b[22:0]};
    e  = $signed(11'(ea)) + $signed(11'(eb)) - 11'sd127;
    if (p[47]) begin
      p = (p >> 1) | {47'd0, p[0]};   // 保留 sticky 信息
      e = e + 1;
    end
    m24 = {1'b0, p[45:23]};
    if (p[22] && (|p[21:0] || p[23])) m24 = m24 + 24'd1;
    if (m24[23]) begin
      m24 = m24 >> 1;
      e   = e + 1;
    end
    if (e <= 0) return 32'h0;
    if (e >= 255) return {sg, 8'hFF, 23'd0};
    return {sg, e[7:0], m24[22:0]};
  endfunction

  // fp32 加法：RNE、DAZ+FTZ、零规范化为 +0；输入约定为有限数
  function automatic logic [31:0] f_add(input logic [31:0] a, input logic [31:0] b);
    logic [31:0]        hi, lo, av, bv;
    logic [7:0]         ea, eb;
    logic [28:0]        ma, mb;
    logic [30:0]        ms;
    logic signed [10:0] e;
    logic               st;
    int                 d, h, i;
    av = (a[30:23] == 8'd0 || a[30:23] == 8'hFF) ? 32'h0 : a;
    bv = (b[30:23] == 8'd0 || b[30:23] == 8'hFF) ? 32'h0 : b;
    if (av[30:0] == 31'd0) return (bv[30:0] == 31'd0) ? 32'h0 : bv;
    if (bv[30:0] == 31'd0) return av;
    if (av[30:0] >= bv[30:0]) begin
      hi = av;
      lo = bv;
    end else begin
      hi = bv;
      lo = av;
    end
    ea = hi[30:23];
    eb = lo[30:23];
    ma = {1'b1, hi[22:0], 5'b0};
    mb = {1'b1, lo[22:0], 5'b0};
    d  = int'(ea) - int'(eb);
    if (d > 27) d = 27;
    st = (d == 0) ? 1'b0 : |(mb & ((29'd1 << d) - 29'd1));
    mb = (mb >> d) | 29'(st);
    if (hi[31] == lo[31]) ms = {2'b0, ma} + {2'b0, mb};
    else                  ms = {2'b0, ma} - {2'b0, mb};
    if (ms == 31'd0) return 32'h0;
    h = 0;
    for (i = 0; i <= 30; i++) if (ms[i]) h = i;
    e = $signed(11'(ea)) + $signed(11'(h - 28));
    if (h > 28) begin
      st = |(ms & ((31'd1 << (h - 28)) - 31'd1));
      ms = (ms >> (h - 28)) | 31'(st);
    end else if (h < 28) begin
      ms = ms << (28 - h);
    end
    if (ms[4] && (|ms[3:0] || ms[5])) ms = ms + 31'd32;
    if (ms[29]) begin
      ms = ms >> 1;
      e  = e + 1;
    end
    if (e <= 0) return 32'h0;
    if (e >= 255) return {hi[31], 8'hFF, 23'd0};
    return {hi[31], e[7:0], ms[27:5]};
  endfunction

  // 乘 2^e（e 为常数），等价于 f_mul(x, 2^e)
  function automatic logic [31:0] f_scale_pow2(input logic [31:0] x, input int e);
    logic [31:0] p;
    p = 32'({1'b0, 8'((127 + e) & 8'hFF), 23'd0});
    return f_mul(x, p);
  endfunction

endpackage
