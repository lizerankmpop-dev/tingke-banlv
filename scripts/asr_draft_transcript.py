#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""真实课程音频 → 带时间戳的分段草稿（P3 素材准备工具）

用途：拿到一集真实中文课程录音后，先用本机 ASR 跑一遍，产出**可校对的结构化草稿**；
人工只需在草稿上改错字，就能得到 P3 验收要的「逐字稿」。没有它，10 分钟的课要逐字听打。

产出（默认落在音频同目录）：
    <stem>.draft.json   分段草稿（时间戳 + 文本 + 标记）
    <stem>.draft.txt    纯文本草稿（一段一行，可直接改）
    <stem>.draft.srt    字幕式，便于用播放器对着听
    <stem>.review.html  校对页：内嵌音频播放器 + 逐段编辑 + 一键导出 txt

**为什么要分块喂入（--block-seconds）**
    非实时整段灌进去，所有字幕几乎同时产出，时间戳全部塌到文件末尾 —— 草稿就失去了
    「对着听」的价值；纯实时喂则 10 分钟音频要等 10 分钟。
    折中：每喂 N 秒音频就 drain 一次，把字幕归属到该块。块内若「片段数 == 字幕数」
    则 1:1 精确配对；不相等（发生合并降级）时如实标记 `merged`，并把该块的所有文本
    合成一段 —— 绝不硬凑 1:1，那会得到看起来合理、实际完全错位的对照关系。

依赖：项目 venv（faster-whisper / PyAV / numpy / silero-vad 都在里面）

    <venv>/python.exe scripts/asr_draft_transcript.py --audio <音频> [--out <目录>]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from eval_asr_quality import (  # noqa: E402
    CHUNK_SIZE,
    SAMPLE_RATE,
    Recorder,
    SubtitleRouter,
    build_engine,
    build_vad,
    drain,
    load_audio,
)


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def load_prompt(args) -> str:
    """与 eval_asr_quality 同源：术语热词开启时才组装提示词。"""
    if args.terms != "on":
        return ""
    try:
        from dictionary.terminology import build_terminology

        custom = [x.strip() for x in (args.custom_terms or "").split(",") if x.strip()]
        term = build_terminology(domain=args.domain, custom_terms=custom)
        return term["prompt"]
    except Exception as exc:  # 术语构建失败不该阻断出草稿
        print(f"  ⚠ 术语热词构建失败，按不注入处理: {exc}")
        return ""


def transcribe_blocks(path: Path, args, engine, router: SubtitleRouter) -> tuple[list[dict], dict]:
    """分块喂入 → 返回 (段落列表, 统计)"""
    audio = load_audio(path)
    if audio.size == 0:
        raise SystemExit(f"音频为空或无法解码：{path.name}")
    duration = len(audio) / SAMPLE_RATE
    block = max(5.0, float(args.block_seconds))
    block_size = int(block * SAMPLE_RATE)

    recorder = Recorder()
    router.bind(recorder)          # 必须：否则字幕会记到别的实例上（见 SubtitleRouter 注释）
    vad = build_vad(recorder, engine)
    router.bind(recorder)

    segments: list[dict] = []
    stats_before = engine.get_stats()
    t0 = time.perf_counter()
    seg_cursor = 0                 # 已被消费的 VAD 片段下标
    sub_cursor = 0                 # 已被消费的字幕下标
    # ⚠ 两个游标必须各自独立维护：发生过合并时「段落数」少于「字幕数」，
    # 用 len(segments) 当字幕游标会把旧字幕反复吃进来（静默重复文本）。
    merged_blocks = 0

    for start in range(0, len(audio), block_size):
        chunk = audio[start : start + block_size]
        for i in range(0, len(chunk), CHUNK_SIZE):
            vad.process_chunk(chunk[i : i + CHUNK_SIZE])
        if not drain(engine, timeout=args.drain_timeout):
            raise SystemExit("引擎在 drain 超时前未清空队列，草稿可能不完整 —— 请检查 backlog")

        block_end = min(start + block_size, len(audio)) / SAMPLE_RATE
        segs = recorder.segments[seg_cursor:]
        subs = recorder.subtitles[sub_cursor:]
        seg_cursor = len(recorder.segments)
        sub_cursor = len(recorder.subtitles)

        if not segs:
            continue
        if len(segs) == len(subs):
            for seg, sub in zip(segs, subs):
                segments.append(
                    {
                        "index": len(segments),
                        "audioStart": seg["audioStart"],
                        "audioEnd": seg["audioEnd"],
                        "text": sub["text"].strip(),
                        "merged": False,
                    }
                )
        else:
            # 合并/降级发生了：块内无法 1:1 归属。合成一段并标记，交人工判断。
            merged_blocks += 1
            segments.append(
                {
                    "index": len(segments),
                    "audioStart": segs[0]["audioStart"],
                    "audioEnd": segs[-1]["audioEnd"],
                    "text": "".join(s["text"].strip() for s in subs),
                    "merged": True,
                    "blockEnd": round(block_end, 3),
                    "segmentsInBlock": len(segs),
                    "subtitlesInBlock": len(subs),
                }
            )
        print(
            f"  … {block_end:6.1f}s / {duration:.1f}s  片段 {len(recorder.segments):4d}  "
            f"段落 {len(segments):4d}  用时 {time.perf_counter() - t0:.0f}s"
        )

    vad.flush()
    if not drain(engine, timeout=args.drain_timeout):
        raise SystemExit("尾部 drain 超时")
    # 尾部残留片段（flush 切出的最后一段）
    tail_segs = recorder.segments[seg_cursor:]
    tail_subs = recorder.subtitles[sub_cursor:]
    if tail_segs and tail_subs:
        if len(tail_segs) == len(tail_subs):
            for seg, sub in zip(tail_segs, tail_subs):
                segments.append(
                    {
                        "index": len(segments),
                        "audioStart": seg["audioStart"],
                        "audioEnd": seg["audioEnd"],
                        "text": sub["text"].strip(),
                        "merged": False,
                    }
                )
        else:
            merged_blocks += 1
            segments.append(
                {
                    "index": len(segments),
                    "audioStart": tail_segs[0]["audioStart"],
                    "audioEnd": tail_segs[-1]["audioEnd"],
                    "text": "".join(s["text"].strip() for s in tail_subs),
                    "merged": True,
                    "segmentsInBlock": len(tail_segs),
                    "subtitlesInBlock": len(tail_subs),
                }
            )

    stats_after = engine.get_stats()
    info = {
        "audio": path.name,
        "durationSeconds": round(duration, 3),
        "durationMinutes": round(duration / 60, 2),
        "vadSegments": len(recorder.segments),
        "paragraphs": len(segments),
        "mergedBlocks": merged_blocks,
        "chars": sum(len(s["text"]) for s in segments),
        "elapsedSeconds": round(time.perf_counter() - t0, 1),
        "engine": {
            "model": engine.model_size,
            "language": args.language,
            "terms": args.terms,
            "domain": args.domain,
            "promptChars": len(engine.initial_prompt or ""),
            "degradeEvents": int(stats_after.get("degradeEvents", 0))
            - int(stats_before.get("degradeEvents", 0)),
        },
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    return segments, info


REVIEW_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>逐字稿校对 · __STEM__</title>
<style>
  :root { --bg:#12161f; --panel:#1b2130; --line:#2c3446; --fg:#e6ebf5; --dim:#8d9ab3; --accent:#4a90d9; --ok:#3fb6a8; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.6 "Microsoft YaHei",sans-serif; }
  header { position:sticky; top:0; z-index:9; background:var(--panel); border-bottom:1px solid var(--line); padding:10px 16px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  h1 { font-size:15px; margin:0; font-weight:600; }
  .meta { color:var(--dim); font-size:12px; }
  button { background:#26314a; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:5px 10px; cursor:pointer; font-size:12px; }
  button:hover { border-color:var(--accent); }
  button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
  audio { width:100%; margin:10px 0 4px; }
  main { padding:8px 16px 60px; }
  .row { display:grid; grid-template-columns:96px 34px 1fr; gap:10px; padding:8px; border:1px solid transparent; border-radius:8px; }
  .row.active { background:var(--panel); border-color:var(--line); }
  .row.merged { border-left:3px solid #d98b4a; }
  .t { color:var(--dim); font-size:12px; font-variant-numeric:tabular-nums; padding-top:6px; }
  .p { background:#222b3d; border-color:var(--line); color:var(--fg); height:28px; width:30px; }
  textarea { width:100%; background:#0e131c; color:var(--fg); border:1px solid var(--line); border-radius:6px;
             padding:8px; font:inherit; resize:vertical; min-height:52px; }
  textarea:focus { outline:1px solid var(--accent); }
  .flag { color:#d98b4a; font-size:11px; grid-column:1/-1; }
  .hint { color:var(--dim); font-size:12px; margin:6px 0 14px; }
  kbd { background:#26314a; border-radius:4px; padding:1px 5px; font-size:11px; }
</style>
</head>
<body>
<header>
  <h1>逐字稿校对 · __STEM__</h1>
  <span class="meta" id="meta"></span>
  <button id="btnExport" class="primary">导出 txt</button>
  <button id="btnCopy">复制全文</button>
  <button id="btnReset">还原草稿</button>
</header>
<main>
  <audio id="player" controls preload="metadata" src="__AUDIO__"></audio>
  <div class="hint">
    用法：按 <kbd>空格</kbd> 播放/暂停当前段，<kbd>Ctrl</kbd>+<kbd>←/→</kbd> 切段；
    每段文本直接改错字，自动保存在本浏览器。改完点「导出 txt」，覆盖到音频同名的 <code>.txt</code> 即完成逐字稿。
    橙色左边框 = 该块发生了合并降级，草稿文本可能跨多句，需对照听一遍。
  </div>
  <div id="list"></div>
</main>
<script>
const DRAFT = __DRAFT__;
const KEY = 'tingke-transcript-' + DRAFT.info.audio;
const player = document.getElementById('player');
let cur = -1, stopAt = null;

const saved = JSON.parse(localStorage.getItem(KEY) || 'null');
const texts = saved && saved.length === DRAFT.segments.length
  ? saved : DRAFT.segments.map(s => s.text);

document.getElementById('meta').textContent =
  `${DRAFT.info.durationMinutes} 分钟 · ${DRAFT.info.paragraphs} 段 · ${DRAFT.info.chars} 字`
  + (DRAFT.info.mergedBlocks ? ` · 合并块 ${DRAFT.info.mergedBlocks}` : '');

const list = document.getElementById('list');
DRAFT.segments.forEach((seg, i) => {
  const row = document.createElement('div');
  row.className = 'row' + (seg.merged ? ' merged' : '');
  row.innerHTML = `<div class="t">${fmt(seg.audioStart)}<br>${fmt(seg.audioEnd)}</div>
    <button class="p" title="播放本段">▶</button>`;
  const ta = document.createElement('textarea');
  ta.value = texts[i];
  ta.addEventListener('input', () => { texts[i] = ta.value; persist(); });
  row.appendChild(ta);
  if (seg.merged) {
    const f = document.createElement('div');
    f.className = 'flag';
    f.textContent = `⚠ 合并块：VAD ${seg.segmentsInBlock ?? '?'} 片段 → ${seg.subtitlesInBlock ?? '?'} 字幕，请对照音频核对`;
    row.appendChild(f);
  }
  row.querySelector('.p').onclick = () => playSeg(i);
  row.querySelector('.p').style.gridColumn = '2';
  row.querySelector('.t').style.gridColumn = '1';
  ta.style.gridColumn = '3';
  list.appendChild(row);
});

function fmt(s) { const m = Math.floor(s/60), x = (s%60).toFixed(1).padStart(4,'0'); return `${m}:${x}`; }
function persist() { localStorage.setItem(KEY, JSON.stringify(texts)); }
function highlight(i) {
  [...list.children].forEach((el, k) => el.classList.toggle('active', k === i));
  if (i >= 0 && list.children[i]) list.children[i].scrollIntoView({block:'center', behavior:'smooth'});
}
function playSeg(i) {
  cur = i; highlight(i);
  const seg = DRAFT.segments[i];
  player.currentTime = Math.max(0, seg.audioStart - 0.2);
  stopAt = seg.audioEnd + 0.3;
  player.play();
}
player.addEventListener('timeupdate', () => {
  if (stopAt !== null && player.currentTime >= stopAt) { player.pause(); stopAt = null; }
});
document.addEventListener('keydown', e => {
  const typing = /^(TEXTAREA|INPUT)$/.test(document.activeElement.tagName);
  if (e.code === 'Space' && !typing) { e.preventDefault(); player.paused ? player.play() : player.pause(); }
  if (e.ctrlKey && e.key === 'ArrowRight') { e.preventDefault(); playSeg(Math.min(cur+1, DRAFT.segments.length-1)); }
  if (e.ctrlKey && e.key === 'ArrowLeft') { e.preventDefault(); playSeg(Math.max(cur-1, 0)); }
});
document.getElementById('btnExport').onclick = () => {
  const blob = new Blob([texts.map(t => t.trim()).filter(Boolean).join('\\n') + '\\n'],
                        {type:'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = '__STEM__' + '.txt';
  a.click();
};
document.getElementById('btnCopy').onclick = async () => {
  await navigator.clipboard.writeText(texts.map(t => t.trim()).filter(Boolean).join('\\n'));
  document.getElementById('btnCopy').textContent = '已复制 ✓';
  setTimeout(() => document.getElementById('btnCopy').textContent = '复制全文', 1500);
};
document.getElementById('btnReset').onclick = () => {
  if (!confirm('丢弃修改，还原为 ASR 草稿？')) return;
  DRAFT.segments.forEach((s, i) => texts[i] = s.text);
  [...list.querySelectorAll('textarea')].forEach((ta, i) => ta.value = texts[i]);
  persist();
};
</script>
</body>
</html>
"""


def write_outputs(audio: Path, segments: list[dict], info: dict, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    stem = audio.stem
    (out / f"{stem}.draft.json").write_text(
        json.dumps({"info": info, "segments": segments}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out / f"{stem}.draft.txt").write_text(
        "\n".join(s["text"] for s in segments if s["text"]) + "\n", encoding="utf-8"
    )
    srt = []
    for i, s in enumerate(segments, 1):
        srt.append(f"{i}\n{srt_time(s['audioStart'])} --> {srt_time(s['audioEnd'])}\n{s['text']}\n")
    (out / f"{stem}.draft.srt").write_text("\n".join(srt), encoding="utf-8")

    html = (
        REVIEW_HTML.replace("__STEM__", stem)
        .replace("__AUDIO__", audio.name)
        .replace("__DRAFT__", json.dumps({"info": info, "segments": segments}, ensure_ascii=False))
    )
    (out / f"{stem}.review.html").write_text(html, encoding="utf-8")
    return {
        "json": str(out / f"{stem}.draft.json"),
        "txt": str(out / f"{stem}.draft.txt"),
        "srt": str(out / f"{stem}.draft.srt"),
        "html": str(out / f"{stem}.review.html"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="真实课程音频 → 带时间戳的分段草稿")
    ap.add_argument("--audio", required=True, help="音频文件（wav/mp3/m4a/mp4/flac/aac/ogg）")
    ap.add_argument("--out", default="", help="输出目录（默认音频同目录）")
    ap.add_argument("--block-seconds", type=float, default=30.0, help="分块喂入长度（秒）")
    ap.add_argument("--model", default=None)
    ap.add_argument("--model-timeout", type=float, default=300.0)
    ap.add_argument("--language", default="zh")
    ap.add_argument("--terms", choices=("on", "off"), default="on")
    ap.add_argument("--domain", default="")
    ap.add_argument("--custom-terms", default="")
    ap.add_argument("--autodegrade", choices=("on", "off"), default=None)
    ap.add_argument("--drain-timeout", type=float, default=600.0)
    args = ap.parse_args()

    audio = Path(args.audio).expanduser().resolve()
    if not audio.is_file():
        raise SystemExit(f"找不到音频：{audio}")
    out = Path(args.out).expanduser().resolve() if args.out else audio.parent

    print("=" * 90)
    print(f"生成逐字稿草稿：{audio.name}")
    print(f"  分块    : {args.block_seconds:.0f}s（块内 1:1 配对，不齐则标记 merged）")
    print(f"  术语热词: {args.terms}" + (f"（领域 {args.domain}）" if args.domain else ""))
    print("=" * 90)

    prompt = load_prompt(args)
    router = SubtitleRouter()
    engine = build_engine(args, router, prompt)
    if args.autodegrade == "off":
        engine.set_auto_degrade(False)
        print("  防循环兜底：已关闭（评估用）")

    segments, info = transcribe_blocks(audio, args, engine, router)
    files = write_outputs(audio, segments, info, out)

    print("-" * 90)
    print(f"段落 {info['paragraphs']}（VAD 片段 {info['vadSegments']}）｜{info['chars']} 字｜"
          f"{info['durationMinutes']} 分钟｜合并块 {info['mergedBlocks']}｜用时 {info['elapsedSeconds']}s")
    for k, v in files.items():
        print(f"  {k:5s} → {v}")
    print("下一步：打开 review.html 校对，导出 txt 覆盖为 <同名>.txt，再跑 scripts/check_transcript.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
