# -*- coding: utf-8 -*-
"""逐字稿校对页（review.html）的**单一模板来源**

为什么单独成模块
----------------
这个模板原先内嵌在 `asr_draft_transcript.py` 里。P3 的预校对流程（见
`preproof_transcript.py`）也要产出同一种校对页 —— 一旦复制第二份，
两份模板**必然漂移**：改了一份忘了另一份，页面上少个按钮或少了键盘快捷键，
而**不会报错**。所以只留这一份，两边都从这里取。

占位符的分工（三者不可合并）
---------------------------
    __STEM__   → 页面标题 与「导出 txt」的下载文件名
    __AUDIO__  → `<audio src>`，必须是音频的**真实文件名**
    __DRAFT__  → 内嵌 JSON（info + segments）

`info["audio"]` 还兼作 localStorage 的键（模板里的 `KEY`）。这条约定很重要：
模板只检查「保存的条数 == 段数」就还原已保存的编辑内容，所以两份文本不同的页面
若共用同一个 `info["audio"]`，第二份页面会把**第一份的编辑**静默当成校对稿还原回来。
调用方需要隔离时，必须传入不同的 `info["audio"]`（见 preproof_transcript.py）。
"""

from __future__ import annotations

import json


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
  .controls { display:flex; gap:16px; align-items:center; color:var(--dim); font-size:12px; margin:6px 0; }
  .controls select { background:#26314a; color:var(--fg); border:1px solid var(--line); border-radius:6px; padding:3px 6px; }
  .controls label { display:flex; gap:5px; align-items:center; cursor:pointer; }
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
  <div class="controls">
    倍速 <select id="rate">
      <option value="0.75">0.75×</option>
      <option value="1" selected>1×</option>
      <option value="1.25">1.25×</option>
      <option value="1.5">1.5×</option>
      <option value="2">2×</option>
    </select>
    <label><input type="checkbox" id="loop"> 循环本段</label>
    <label><input type="checkbox" id="autoNext"> 播完自动下一段</label>
  </div>
  <div class="hint">
    用法：按 <kbd>空格</kbd> 播放/暂停当前段，<kbd>Ctrl</kbd>+<kbd>←/→</kbd> 切段；
    每段文本直接改错字，自动保存在本浏览器。改完点「导出 txt」，覆盖到音频同名的 <code>.txt</code> 即完成逐字稿。
    橙色左边框 = 该块发生了合并降级，草稿文本可能跨多句，需对照听一遍。
    <br>长音频建议 1.25×~1.5× 校对（同样的时间能多听一遍），遇到听不清的段落切回 1× 或 0.75×。
  </div>
  <div id="list"></div>
</main>
<script>
const DRAFT = __DRAFT__;
const KEY = 'tingke-transcript-' + DRAFT.info.audio;
const player = document.getElementById('player');
const rateSel = document.getElementById('rate');
const loopChk = document.getElementById('loop');
const nextChk = document.getElementById('autoNext');
let cur = -1, stopAt = null;

let rate = parseFloat(localStorage.getItem(KEY + '-rate') || '1');
rateSel.value = String(rate);
player.playbackRate = rate;
rateSel.onchange = () => {
  rate = parseFloat(rateSel.value);
  player.playbackRate = rate;               // 只设一次会被某些浏览器在 play() 后重置，这里显式再设
  localStorage.setItem(KEY + '-rate', String(rate));
};

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
  player.playbackRate = rate;
  player.play();
}
player.addEventListener('timeupdate', () => {
  if (stopAt === null || player.currentTime < stopAt) return;
  if (loopChk.checked && cur >= 0) {
    player.currentTime = Math.max(0, DRAFT.segments[cur].audioStart - 0.2);
    return;
  }
  player.pause();
  stopAt = null;
  if (nextChk.checked && cur >= 0 && cur + 1 < DRAFT.segments.length) playSeg(cur + 1);
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


def render_review_html(stem: str, audio_name: str, segments: list, info: dict) -> str:
    """填充模板，返回可直接写盘的 HTML 文本。"""
    payload = json.dumps({"info": info, "segments": segments}, ensure_ascii=False)
    return (
        REVIEW_HTML.replace("__STEM__", stem)
        .replace("__AUDIO__", audio_name)
        .replace("__DRAFT__", payload)
    )
