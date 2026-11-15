//=============================================================================
// fp32_dut.sv -- tpu_pkg 里 fp32 原语的独立验证外壳
//
// 只把 f_mul / f_add / f_i2f48 组合暴露出来，供 tb_fp32.cpp 随机对拍。
//=============================================================================
module fp32_dut
  import tpu_pkg::*;
(
  input  logic [2:0]  op,        // 0=mul 1=add 2=i2f48(零扩展) 3=scale_pow2 4=i2f48(符号扩展)
  input  logic [31:0] a,
  input  logic [31:0] b,
  input  logic [5:0]  bin,       // scale_pow2 的指数 (a * 2^-bin)
  output logic [31:0] y
);

  always_comb begin
    unique case (op)
      3'd0: y = f_mul(a, b);
      3'd1: y = f_add(a, b);
      3'd2: y = f_i2f48({16'd0, a});
      3'd3: y = f_scale_pow2(a, -int'(bin));
      default: y = f_i2f48({{16{a[31]}}, a});
    endcase
  end

endmodule
