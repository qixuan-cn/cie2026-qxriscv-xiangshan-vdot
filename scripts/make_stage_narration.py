#!/usr/bin/env python3
"""为四个阶段的录屏生成带中文字幕与解说的版本。

输入：build/opt_record_frames/<stage>/（由 scripts/make_operation_recordings.py 渲染）
输出：doc/operation_record/<stage>/video/<stage>_narrated.mp4 与同名 .srt

依赖：ffmpeg（libx264 + libass）、edge-tts（在线语音合成）
      pip install edge-tts   # 本机安装于 /tmp/pylibs
用法：python3 scripts/make_stage_narration.py [stage ...]
      不带参数时处理全部四个阶段。
"""

import math
import os
import re
import shutil
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(ROOT, "doc", "operation_record")
FRAME_ROOT = os.path.join(ROOT, "build", "opt_record_frames")
WORK_ROOT = os.path.join(ROOT, "build", "stage_narration")
FFMPEG = os.path.join(ROOT, ".tools", "ffmpeg", "ffmpeg")
EDGE_TTS_PYPATH = "/tmp/pylibs"
VOICE = "zh-CN-XiaoxiaoNeural"
RATE = "+8%"
FPS = 30

# 解说词按“画面块”组织：每块对应视频中连续的一段画面
BLOCKS = {
    "stage1_environment": [
        ("intro", "阶段一：环境搭建与 RTL 链路贯通。"),
        ("s1", "工具链自检：GCC 15.2、Verilator 5.034。"),
        ("s2", "锁定香山与 NEMU 的源码版本。"),
        ("s3", "构建产物核对：EMU 107 MB，两个镜像已就绪。"),
        ("s4", "hello 打印 qxriscv 与 IP，命中 GOOD TRAP。"),
        ("s5", "vadd 只采集 8200 到 8360 周期，避免超大 VCD。"),
        ("s6", "时间线覆盖译码、发射、写回与提交四个节点。"),
        ("s7a", "GTKWave 打开真实 VCD：九条 valid 信号，贯通取指到写回。"),
        ("s7b", "放大执行与写回窗口，十六进制核对写回数据。"),
        ("s7c", "波形、复现脚本与截图均已归档。"),
        ("outro", "阶段一完成，环境与 RTL 链路贯通。"),
    ],
    "stage2_design_rtl": [
        ("intro", "阶段二：三模式设计实现、RTL 验证与香山集成。"),
        ("s1", "设计文档给出指令语义、微架构与接口约束。"),
        ("s2", "Verilog 与 Chisel 双版本 RTL，规模已核对。"),
        ("s3", "三模式单元回归，三千零四十七条全部通过。延迟三周期，每周期一条吞吐。"),
        ("s4", "行覆盖率五十五项全中；Chisel 与 Verilog 等价，同样是三千零四十七。"),
        ("s5", "三条指令靠 funct6 区分：INT8、INT4 打包、BitNet 三值。INT4 每拍十六次乘加，三值十六次、不走乘法器。"),
        ("s6", "香山 emu 定向七条用例，全部 GOOD TRAP。"),
        ("s7", "五类非法配置全部触发异常，other 为零。"),
        ("s8", "集成补丁五个文件，加一百五十七、减七；只挂一个 VFu，不动调度器。"),
        ("outro", "阶段二完成，三模式设计、RTL 与集成闭环。"),
    ],
    "stage3_nemu_cosim": [
        ("intro", "阶段三：NEMU 参考模型、三模式协同仿真与加速评估。"),
        ("s1", "NEMU 补丁反向校验通过，状态一致。"),
        ("s2", "三千零三十二条可运行用例，产出四千五百五十一个结果字。INT8、INT4、三值三条路径都覆盖。"),
        ("s3", "逐例比对，双结果按 r0 和 r1 两行展开，零失配。"),
        ("s4", "边界自检：vl 等于零不写回，三模式各一次全过；四类非法配置，合计十二次全过。"),
        ("s5", "纯 RVV 组合基线，一千五百一十三例一致；数据通路指令从六条降到一条。"),
        ("s6", "单元级三周期延迟、每周期一条吞吐。"),
        ("s7", "四个内核各跑三次，极差为零。"),
        ("s8", "零点五零七五周期每点积：对标量公平九点八倍，对 RVV 上界十一点八倍。"),
        ("outro", "阶段三完成，三模式功能与性能均有量化结论。"),
    ],
    "stage4_release": [
        ("intro", "阶段四：质量门径、三模式综合与提交检查。"),
        ("s1", "Verilator 严格 Lint：零告警、零错误。"),
        ("s2", "Yosys 现场综合三模式 RTL：七千四百二十七单元，三百五十六个触发器。"),
        ("s3", "INT8 单模式对照：四千一百八十七单元，一百七十二个触发器。"),
        ("s4", "两种口径最长路径都是二十五级，起终点不变。三模式面积约十点一到十五点八千门，比单模式增加七成七。"),
        ("s5", "七步一键回归全部通过。"),
        ("s6", "检查项已勾选，材料与视频已就位。"),
        ("outro", "四个阶段完成，项目具备提交条件。"),
    ],
}


def sh(cmd, env=None):
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}\n{p.stderr[-800:]}")
    return p.stdout


def parse_concat(frames):
    """返回 [(帧路径, 帧数)]，并把 0.55s 等非整帧时长对齐到 30fps 帧网格。"""
    items, cur = [], None
    with open(os.path.join(frames, "concat.txt"), encoding="utf-8") as f:
        for raw in f:
            ln = raw.strip()
            m = re.match(r"file '(.+)'", ln)
            if m:
                cur = m.group(1)
            m = re.match(r"duration ([\d.]+)", ln)
            if m and cur:
                items.append((cur, math.floor(float(m.group(1)) * FPS + 0.5)))
                cur = None
    return items


def block_of(stage, name):
    if name.startswith("000_intro"):
        return "intro"
    if name.startswith("999_outro"):
        return "outro"
    if stage == "stage1_environment" and name.startswith("07_"):
        if name.startswith(("07_cmd", "07_wave1")):
            return "s7a"
        if name.startswith("07_wave2"):
            return "s7b"
        return "s7c"
    m = re.match(r"(\d+)_", name)
    return f"s{int(m.group(1))}" if m else "misc"


def audio_duration(path):
    wav = path + ".dur.wav"
    sh([FFMPEG, "-y", "-i", path, "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav])
    with wave.open(wav) as w:
        d = w.getnframes() / w.getframerate()
    os.remove(wav)
    return d


def tts(text, path):
    env = dict(os.environ, PYTHONPATH=EDGE_TTS_PYPATH)
    for attempt in range(3):
        try:
            sh([sys.executable, "-m", "edge_tts", "--voice", VOICE,
                "--rate", RATE, "--text", text, "--write-media", path], env=env)
            if os.path.getsize(path) > 1000:
                return
        except RuntimeError:
            if attempt == 2:
                raise


def ass_time(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def srt_time(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    if ms == 1000:
        s, ms = s + 1, 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narration,Noto Sans CJK SC,42,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,1,0,1,3,0,2,60,60,14,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def make(stage):
    frames = os.path.join(FRAME_ROOT, stage)
    if not os.path.isdir(frames):
        raise SystemExit(f"缺少帧目录：{frames}（先运行 make_operation_recordings.py）")
    blocks = BLOCKS[stage]
    work = os.path.join(WORK_ROOT, stage)
    seq = os.path.join(work, "seq")
    items = parse_concat(frames)
    for p, _ in items:
        if not os.path.exists(p):
            raise SystemExit(f"缺少帧文件: {p}")

    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(seq, exist_ok=True)

    idx = 0
    block_frames = {}
    for path, n in items:
        key = block_of(stage, os.path.basename(path))
        block_frames[key] = block_frames.get(key, 0) + n
        for _ in range(n):
            os.symlink(path, os.path.join(seq, f"{idx:05d}.png"))
            idx += 1
    total = idx / FPS

    unknown = set(block_frames) - {k for k, _ in blocks}
    if unknown:
        raise SystemExit(f"{stage} 存在未定义解说的画面块：{sorted(unknown)}")

    narr_dir = os.path.join(work, "audio")
    os.makedirs(narr_dir, exist_ok=True)
    audio_parts, events, durations = [], [], {}
    t = 0.0
    for key, text in blocks:
        dur = block_frames[key] / FPS
        mp3 = os.path.join(narr_dir, f"{key}.mp3")
        tts(text, mp3)
        src = audio_duration(mp3)
        tempo = min(src / (dur - 0.2), 1.6) if src > dur - 0.2 else 1.0
        out = os.path.join(narr_dir, f"{key}.wav")
        sh([FFMPEG, "-y", "-i", mp3, "-filter:a", f"atempo={tempo:.3f},apad",
            "-t", f"{dur:.4f}", "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", out])
        audio_parts.append(out)
        durations[key] = (dur, src, tempo > 1.0)
        events.append((t + 0.10, t + dur - 0.08, text))
        t += dur

    list_txt = os.path.join(work, "audio.txt")
    with open(list_txt, "w", encoding="utf-8") as f:
        for p in audio_parts:
            f.write(f"file '{p}'\n")
    narration = os.path.join(work, "narration.wav")
    sh([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", list_txt,
        "-c:a", "pcm_s16le", narration])

    ass_path = os.path.join(work, "narration.ass")
    vdir = os.path.join(OUT_ROOT, stage, "video")
    os.makedirs(vdir, exist_ok=True)
    srt_path = os.path.join(vdir, f"{stage}_narrated.srt")
    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ASS_HEADER)
        for s, e, text in events:
            f.write(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Narration,,0,0,0,,{text}\n")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, (s, e, text) in enumerate(events, 1):
            f.write(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{text}\n\n")

    out = os.path.join(vdir, f"{stage}_narrated.mp4")
    sh([FFMPEG, "-y", "-framerate", str(FPS), "-start_number", "0",
        "-i", os.path.join(seq, "%05d.png"), "-i", narration,
        # 内容等比缩放至 1778x1000，底部留 80px 黑边专供字幕，避免遮挡画面
        "-vf", ("scale=1778:1000:flags=lanczos,pad=1920:1080:71:0:black,"
                f"subtitles={ass_path}:fontsdir=/usr/share/fonts,format=yuv420p"),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", out])

    print(f"[{stage}] 总时长 {total:.3f}s / {idx} 帧  加速块 {sum(v[2] for v in durations.values())}/{len(durations)}")
    print(f"VIDEO {out}")
    print(f"SRT   {srt_path}")


def main():
    wanted = sys.argv[1:] or list(BLOCKS)
    for stage in wanted:
        if stage not in BLOCKS:
            raise SystemExit(f"未知阶段：{stage}（可选 {', '.join(BLOCKS)}）")
        make(stage)


if __name__ == "__main__":
    main()
