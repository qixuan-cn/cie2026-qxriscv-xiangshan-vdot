//=============================================================================
// VDot —— vdot.vv 功能单元（Chisel 主体实现）
//
// 与 rtl/verilog/VDot.v 为同一数据通路语义的两种实现：
//   S = sum_{i=0..7} sext8(vs1[i]) * sext8(vs2[i])
//   vd[OUTW-1:0] = S, vd[VLEN-1:OUTW] = 0
//
// 3 级流水：EX1 乘 / EX2 加法树 / WB 格式化写回；无状态，可背靠背发射
//
// 说明：本文件面向香山（Chisel）集成。当前开发环境无 sbt/Scala 工具链，
//       故以 Verilog 版本做了实际仿真验证；本文件需在 xs-env 中就绪后编译校验。
//=============================================================================
package vdot

import chisel3._
import chisel3.util._

/** VDot 请求/写回接口（单元级，不含香山 VFu 框架字段） */
class VDotIO(val VLEN: Int = 128, val OUTW: Int = 32) extends Bundle {
  val in = Input(new Bundle {
    val valid  = Bool()
    val vs1    = UInt(VLEN.W)
    val vs2    = UInt(VLEN.W)
    val vlZero = Bool()          // vl == 0：不写回
    val vm     = Bool()          // vm == 1 才合法
    val sew    = UInt(2.W)       // 0 = e8
    val lmul   = UInt(3.W)       // 0 = m1
  })
  val out = Output(new Bundle {
    val vd      = UInt(VLEN.W)
    val valid   = Bool()
    val illegal = Bool()
    val dirty   = Bool()
  })
}

class VDotCore(
  val VLEN: Int = 128,
  val K:    Int = 8,
  val ACCW: Int = 20,
  val OUTW: Int = 32
) extends Module {
  require(VLEN == 128, "赛题固定 VLEN=128")
  require(K == 8, "vdot.vv 语义固定 K=8")
  require(ACCW >= 19, "8 项和上界 +131072，需 >= 19 bit 有符号")
  require(OUTW >= ACCW, "OUT_W 必须能容纳累加结果")

  val io = IO(new VDotIO(VLEN, OUTW))

  /** 取第 i 个 int8 lane 并解释为有符号数 */
  private def laneSInt(x: UInt, i: Int): SInt = x(8 * i + 7, 8 * i).asSInt

  /** 符号扩展到指定位宽 */
  private def ext(x: SInt, w: Int): SInt = {
    val pad = w - x.getWidth
    require(pad >= 0, s"ext: $w < ${x.getWidth}")
    Cat(Fill(pad, x(x.getWidth - 1)), x).asSInt
  }

  /** 树形归约（log2(K) 级） */
  private def treeSum(ps: Seq[SInt]): SInt = {
    var cur: Seq[SInt] = ps.map(p => ext(p, ACCW))
    while (cur.length > 1) {
      cur = cur.grouped(2).map {
        case Seq(a, b) => a + b
        case Seq(a)    => a
      }.toSeq
    }
    cur.head
  }

  //--------------------------------------------------------------------------
  // EX1：K 路有符号 8x8 乘法
  //--------------------------------------------------------------------------
  val prod = Wire(Vec(K, SInt(16.W)))
  for (i <- 0 until K) {
    prod(i) := laneSInt(io.in.vs1, i) * laneSInt(io.in.vs2, i)
  }

  val legal = io.in.vm && io.in.sew === 0.U && io.in.lmul === 0.U

  //--------------------------------------------------------------------------
  // 流水寄存器
  //--------------------------------------------------------------------------
  val s1Valid   = RegNext(io.in.valid, false.B)
  val s1Illegal = RegNext(io.in.valid && !legal, false.B)
  val s1Write   = RegNext(io.in.valid && legal && !io.in.vlZero, false.B)
  val s1Prod    = RegNext(prod)

  // EX2：加法树
  val sum2 = treeSum(s1Prod)

  val s2Valid   = RegNext(s1Valid, false.B)
  val s2Illegal = RegNext(s1Illegal, false.B)
  val s2Write   = RegNext(s1Write, false.B)
  val s2Sum     = RegNext(sum2)

  // WB：符号扩展 + 高位清零
  val vdPayload = Cat(0.U((VLEN - OUTW).W), ext(s2Sum, OUTW).asUInt)

  io.out.vd      := RegNext(vdPayload)
  io.out.valid   := RegNext(s2Write, false.B)
  io.out.illegal := RegNext(s2Illegal, false.B)
  io.out.dirty   := RegNext(s2Write, false.B)

  //--------------------------------------------------------------------------
  // 仿真断言
  //--------------------------------------------------------------------------
  when(io.out.valid) {
    assert(io.out.vd(VLEN - 1, OUTW) === 0.U, "VDot: 高位通道必须为零")
    assert(!io.out.illegal, "VDot: valid 与 illegal 不可同时有效")
  }
}
