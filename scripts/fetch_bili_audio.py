#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站视频 → 16kHz 单声道 WAV 素材切片（P3 素材准备工具）

用途
----
P3 需要「真实中文专业课 ≥10min 连续录音」，但公开课素材在视频站上，
拿到的是一整集 20 分钟的视频。本工具把「BV 号 → 可评估的音频切片」一次做完。


为什么不用 yt-dlp / ffmpeg
--------------------------
1. 本机**没有** ffmpeg，也没有 yt-dlp；项目 venv 里已有 PyAV（faster-whisper 的依赖），
   能直接解 m4s(fMP4/AAC)，没有必要再引入外部二进制。
2. yt-dlp 会连带下载视频流，而只要音频。走 B 站官方 DASH 接口只取音频轨，体积降一个量级。

因此网络部分用**纯标准库**（urllib），解码复用 `eval_asr_quality.load_audio` ——
与评估链路**同一套重采样实现**，避免「素材转换和评估用的不是同一个东西」这种隐蔽偏差。


匿名访问的三条边界（2026-10-07 实测）
------------------------------------
1. **搜索接口不可用**：`search/type` 匿名返回 HTML（风控），所以本工具只接受 BV 号/链接，
   不做关键词搜索 —— 视频得由人挑。
2. **字幕接口匿名返回空**：`player/v2` 的 `subtitle` 恒为 `[]`，所以别指望平台字幕当参考
   答案；逐字稿一律走 `asr_draft_transcript.py` + 人工校对。
3. **playurl 必须带 Referer**：音频 CDN 直链不带 `Referer: https://www.bilibili.com` 会 403。


版权纪律（重要，别绕过）
------------------------
公开课是**公开传播**，不等于**授权使用**。本工具产出的音频含他人声音，因此：

* 音频只落本地，`.gitignore` 已屏蔽 `testset/**/*.{wav,m4s,mp4,...}`；
* 元数据必须登记真实来源（BV 号、UP 主、截取区间），由 `--print-metadata` 生成；
* 逐字稿同样受版权保护，**不入库**（见 `testset/chinese_lecture/real/README.md`）。

用法
----
    <venv>/python.exe scripts/fetch_bili_audio.py BV1BA411i7PT \\
        --start 00:40 --end 11:10 \\
        --name real_zhlecture_origin_plot \\
        --out-dir testset/chinese_lecture/real

    # 只看信息、不下载
    <venv>/python.exe scripts/fetch_bili_audio.py BV1BA411i7PT --dry-run

    # 生成 metadata 片段（填进 real/metadata.json 的 entries）
    <venv>/python.exe scripts/fetch_bili_audio.py BV1BA411i7PT --start 00:40 --end 11:10 --print-metadata
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

REPO = HERE.parent

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REFERER = "https://www.bilibili.com"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"

# 音频流优先级：30280=192K，30232=132K，30216=64K（B 站一贯的 id 约定）。
# 拿最高码率是为了让「录音本身的缺陷」而不是「转码损失」成为误差来源；
# 素材是给 ASR 测真实场景用的，人为降质会污染结论。
AUDIO_ID_PREFERENCE = (30280, 30232, 30216)

_BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})", re.IGNORECASE)
_TS_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$|^\s*(\d+(?:\.\d+)?)\s*$")


# ============ 纯逻辑（可单测，不碰网络） ============


def extract_bv(text: str) -> str:
    """从 BV 号或任意含 BV 号的 URL/文案里取出 BV 号，统一成标准大小写。

    返回 "BV" + 10 位原样大小写（B 站大小写敏感，不能 upper/lower 整个）。
    """
    if not text:
        raise ValueError("未提供 BV 号或链接")
    m = _BV_RE.search(text)
    if not m:
        raise ValueError(f"无法从 {text!r} 中解析出 BV 号（形如 BV1BA411i7PT）")
    raw = m.group(1)
    return "BV" + raw[2:]


def parse_timecode(text: str) -> float:
    """把 `90` / `1:30` / `00:40` / `1:02:03.5` 解析成秒。

    纯数字按**秒**解释（不是分钟），因为脚本里 `--start 90` 比 `--start 1:30`
    更不容易被误读；超过一小时的素材才需要 `H:MM:SS`。
    """
    if text is None:
        raise ValueError("时间参数为空")
    s = str(text).strip()
    if not s:
        raise ValueError("时间参数为空")
    m = _TS_RE.match(s)
    if not m:
        raise ValueError(f"无法解析时间 {text!r}（支持 秒 / MM:SS / H:MM:SS）")
    if m.group(4) is not None:
        return float(m.group(4))
    hours = float(m.group(1) or 0)
    minutes = float(m.group(2))
    seconds = float(m.group(3))
    if minutes >= 60 and hours > 0:
        raise ValueError(f"时间 {text!r} 的分钟位应为 0-59")
    if seconds >= 60:
        raise ValueError(f"时间 {text!r} 的秒位应为 0-59")
    return hours * 3600 + minutes * 60 + seconds


def format_timecode(seconds: float) -> str:
    """秒 → `MM:SS`（超一小时才带小时位），用于日志与元数据。"""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def pick_audio_stream(streams: list[dict]) -> dict:
    """从 DASH 音频轨里挑一条：先按 id 偏好，再按带宽兜底。

    必须报错而不是返回 None —— 调用方拿不到音频轨就没法继续，
    静默返回 None 只会把问题推迟到解码阶段，报错信息还更含糊。
    """
    usable = [s for s in streams if isinstance(s, dict) and s.get("baseUrl")]
    if not usable:
        raise ValueError("DASH 音频轨列表为空或无 baseUrl")
    for pref in AUDIO_ID_PREFERENCE:
        for s in usable:
            if s.get("id") == pref:
                return s
    return max(usable, key=lambda s: s.get("bandwidth") or 0)


def slice_samples(audio, sample_rate: int, start_s: float, end_s: float):
    """按秒切片。越界即报错，**不做静默裁剪**。

    静默裁剪最危险的地方是「得到一段短音频但没人发现」，于是评估结论
    建立在一个和预期不同的样本上。宁可让调用方显式调整参数。
    """
    total_s = len(audio) / sample_rate
    if start_s < 0:
        raise ValueError(f"起始时间不能为负：{start_s}")
    if end_s <= start_s:
        raise ValueError(f"结束时间必须大于起始时间：{start_s} → {end_s}")
    if end_s > total_s + 1e-6:
        raise ValueError(
            f"结束时间 {format_timecode(end_s)} 超出音频总长 {format_timecode(total_s)}"
        )
    i0 = int(round(start_s * sample_rate))
    i1 = int(round(end_s * sample_rate))
    return audio[i0:i1]


def write_wav(path: Path, audio, sample_rate: int) -> None:
    """写 16-bit PCM 单声道 WAV（标准库）。float32 [-1,1] → int16。"""
    import numpy as np

    clipped = np.clip(audio, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())


def audio_stats(audio, sample_rate: int) -> dict:
    """RMS / 峰值 / 静音占比 —— 用来判断「这段到底有没有人在讲话」。

    切片区间是人工按时间戳定的，很可能落进片头音乐或长时间停顿里。
    用户在跑完 ASR 之前无法察觉，所以这里先给一个客观信号。
    """
    import numpy as np

    if len(audio) == 0:
        return {"rmsDbfs": None, "peakDbfs": None, "quietRatio": 1.0}
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio))))
    # 20ms 窗统计「近似静音」比例（低于 -50 dBFS 视为静音）
    win = max(1, sample_rate // 50)
    n = len(audio) // win
    if n:
        frames = audio[: n * win].reshape(n, win)
        frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
        quiet = float(np.mean(frame_rms < 10 ** (-50 / 20)))
    else:
        quiet = 0.0

    def db(x: float) -> float | None:
        return round(20 * float(np.log10(x)), 2) if x > 0 else None

    return {"rmsDbfs": db(rms), "peakDbfs": db(peak), "quietRatio": round(quiet, 3)}


# ============ 网络 ============


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    return ctx


def api_get(url: str, referer: str | None = None, timeout: int = 20) -> dict:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json, */*")
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
        body = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        # 匿名被风控时接口返回的是 HTML 页面而不是 JSON，这里给出可读提示
        raise SystemExit(
            f"接口未返回 JSON（很可能是风控/需登录）：{url}\n"
            f"  返回内容开头：{body[:120]!r}"
        )


def resolve_video(bvid: str) -> dict:
    d = api_get(f"{API_VIEW}?bvid={bvid}")
    if d.get("code") != 0:
        # BV 号对大小写敏感，手打过来的号码最常见的失败原因就是大小写错了，
        # 而接口只会回一个含糊的「请求错误」。这里补一句可执行的提示。
        raise SystemExit(
            f"view 接口失败：code={d.get('code')} msg={d.get('message')}\n"
            f"  传入的 bvid = {bvid!r}（本工具不会改写你给的 10 位字符）\n"
            f"  请核对大小写是否与视频页一致 —— BV 号大小写敏感，错误大小写会指向"
            f"不存在的视频。建议直接从浏览器地址栏复制。"
        )
    v = d["data"]
    pages = v.get("pages") or []
    return {
        "bvid": v["bvid"],
        "aid": v["aid"],
        "title": v["title"],
        "duration": float(v["duration"]),
        "owner": (v.get("owner") or {}).get("name", ""),
        "pages": [
            {"page": p["page"], "cid": p["cid"], "duration": float(p["duration"]),
             "part": p.get("part", "")}
            for p in pages
        ],
    }


def get_dash_audio(bvid: str, cid: int) -> tuple[list[dict], str | None]:
    """返回 (音频轨列表, durl 兜底链接)。fnval=16 才会给 DASH。"""
    d = api_get(f"{API_PLAYURL}?bvid={bvid}&cid={cid}&fnval=16&fourk=1",
                referer=REFERER)
    if d.get("code") != 0:
        raise SystemExit(f"playurl 接口失败：code={d.get('code')} msg={d.get('message')}")
    data = d.get("data") or {}
    dash = data.get("dash") or {}
    streams = dash.get("audio") or []
    durl = data.get("durl") or []
    fallback = durl[0].get("url") if durl else None
    return streams, fallback


def download(url: str, dest: Path, referer: str = REFERER,
             min_bytes: int = 0, max_bytes: int = 0, retries: int = 3) -> int:
    """流式下载（带 Referer）。返回字节数。

    🔴 **完整性校验 + 续传 + 够用即停**（2026-10-26）

    这三件事是配套的，因为**「整流下载」这个前提本身是错的**：

    * 本工具只要**目标区间**那几百秒音频。B 站 DASH 音频轨是**整集**（P1 = 47 分钟
      ⇒ 48 MB），而 630 秒按 135 kbps 算**只要约 10.7 MB**。
      为了 10% 的内容下 100% 的流，既慢又更容易被截断。
    * 实测这个 CDN **会稳定地在 20,971,520 B 处切断**（连试 3 次，每次断点完全相同）——
      所以「下完整个流」这个目标**经常达不到**，而**够切片用的那部分每次都拿得到**。

    因此改成：
    * **够用即停**：`max_bytes` 给出上限，`min_bytes` 给出「够切片用」的下限；
      拿到 `min_bytes` 就可以停，不必等 `Content-Length`。
    * **Range 续传**：断了从断点续（`Range: bytes=N-`），重试若干次。
    * **校验**：以 `min_bytes` 为准，**不再**要求 `written == Content-Length`。
      仍然拦「明显不够」的情况（否则会拿一个连切片都做不出来的文件去解码）。

    保留的那道闸门（`written == Content-Length` 时才判失败）在 `min_bytes == 0`
    时依然生效 —— 即「要求完整」的老用法不受影响。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    total = 0
    t0 = time.time()

    for attempt in range(1, retries + 1):
        have = tmp.stat().st_size if tmp.is_file() else 0
        req = urllib.request.Request(url)
        req.add_header("User-Agent", USER_AGENT)
        req.add_header("Referer", referer)
        req.add_header("Accept", "*/*")
        if have:
            req.add_header("Range", f"bytes={have}-")
        try:
            with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx()) as resp:
                code = getattr(resp, "status", 200)
                clen = int(resp.headers.get("Content-Length") or 0)
                # 续传时 Content-Length 是**剩余**长度 ⇒ 还原成总长
                full = (have + clen) if (code == 206 and clen) else clen
                if not have and clen:
                    print(f"  远端总长 {clen/1e6:.2f} MB")
                with open(tmp, "ab" if have else "wb") as fh:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        if max_bytes and have >= max_bytes:
                            break
                        if min_bytes and have >= min_bytes and max_bytes == 0:
                            break
                        if full and have >= full:
                            break
                        if clen:
                            pct = have * 100 // full
                            print(f"\r  下载中 {pct:3d}%  {have/1e6:6.2f}/{full/1e6:.2f} MB",
                                  end="", flush=True)
            if max_bytes and have > max_bytes:
                # 截到上限：把多余部分截掉
                with open(tmp, "r+b") as fh:
                    fh.truncate(max_bytes)
                have = max_bytes
            print(f"\r  下载完成 {have/1e6:.2f} MB，用时 {time.time()-t0:.1f}s" + " " * 20)
            total = have
            break
        except urllib.error.HTTPError as exc:
            tmp.unlink(missing_ok=True)
            raise SystemExit(
                f"下载失败 HTTP {exc.code}。音频直链需带 Referer 且会过期，请重跑本工具。"
            )
        except Exception as exc:
            print(f"  ⚠ 第 {attempt}/{retries} 次中断：{type(exc).__name__}: {exc}")
            if attempt >= retries:
                tmp.unlink(missing_ok=True)
                raise SystemExit(
                    f"下载连续 {retries} 次中断（已得 {have/1e6:.2f} MB）。已放弃。"
                    f"请重跑本工具 —— **会从 `.part` 断点续传**，不会从头来。"
                )
            time.sleep(2)

    if min_bytes and total < min_bytes:
        tmp.unlink(missing_ok=True)
        raise SystemExit(
            f"下载不足：实得 {total:,} B < 需要的 {min_bytes:,} B（够切片用的下限）。"
            f"**缓存未写入**。请重跑本工具。"
        )
    if not min_bytes and total == 0:
        tmp.unlink(missing_ok=True)
        raise SystemExit("下载得到 0 字节。请重跑本工具（音频直链可能已过期）。")
    tmp.replace(dest)
    return total


# ============ 主流程 ============


def sha256_of(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="B 站视频 → 16kHz 单声道 WAV 素材切片",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("target", help="BV 号或含 BV 号的链接")
    p.add_argument("--page", type=int, default=1, help="分P 序号（默认 1）")
    p.add_argument("--start", default="0", help="起始时间：秒 / MM:SS / H:MM:SS（默认 0）")
    p.add_argument("--end", default=None, help="结束时间（与 --duration 二选一）")
    p.add_argument("--duration", default=None, help="持续时长（与 --end 二选一）")
    p.add_argument("--name", default=None,
                   help="输出文件名（不含后缀）。默认 real_zhlecture_<bvid>")
    p.add_argument("--out-dir", default="testset/chinese_lecture/real",
                   help="输出目录（默认 testset/chinese_lecture/real）")
    p.add_argument("--cache-dir", default="packaging/eval/bili-cache",
                   help="源 m4s 缓存目录（默认 packaging/eval/bili-cache，已 gitignore）")
    p.add_argument("--keep-source", action="store_true", help="保留缓存的源 m4s")
    p.add_argument("--dry-run", action="store_true", help="只打印视频信息，不下载")
    p.add_argument("--print-metadata", action="store_true",
                   help="打印可粘贴进 metadata.json 的条目（不下载）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    bvid = extract_bv(args.target)
    start_s = parse_timecode(args.start)

    print(f"[1/5] 解析视频 {bvid}")
    info = resolve_video(bvid)
    print(f"  标题   : {info['title']}")
    print(f"  UP 主  : {info['owner']}")
    print(f"  总时长 : {format_timecode(info['duration'])}（{info['duration']:.0f}s）")
    if len(info["pages"]) > 1:
        for pg in info["pages"]:
            print(f"    P{pg['page']} cid={pg['cid']} {format_timecode(pg['duration'])} {pg['part']}")

    page = next((p for p in info["pages"] if p["page"] == args.page), None)
    if page is None:
        raise SystemExit(f"未找到 P{args.page}（该视频共 {len(info['pages'])} 个分P）")
    cid = page["cid"]
    page_dur = page["duration"]

    if args.end is not None and args.duration is not None:
        raise SystemExit("--end 与 --duration 只能给一个")
    if args.end is not None:
        end_s = parse_timecode(args.end)
    elif args.duration is not None:
        end_s = start_s + parse_timecode(args.duration)
    else:
        end_s = page_dur

    if end_s > page_dur + 1e-6:
        raise SystemExit(
            f"截取区间 {format_timecode(start_s)}~{format_timecode(end_s)} 超出该分P 时长 "
            f"{format_timecode(page_dur)}"
        )
    clip_s = end_s - start_s
    print(f"[2/5] 截取区间 {format_timecode(start_s)} ~ {format_timecode(end_s)}"
          f"，共 {clip_s:.0f}s（{clip_s/60:.2f} min）")

    name = args.name or f"real_zhlecture_{bvid.lower()}"
    out_wav = (REPO / args.out_dir / f"{name}.wav") if not Path(args.out_dir).is_absolute() \
        else Path(args.out_dir) / f"{name}.wav"

    src_desc = {
        "bvid": bvid, "aid": info["aid"], "cid": cid, "page": page["page"],
        "title": info["title"], "author": info["owner"],
        "url": f"https://www.bilibili.com/video/{bvid}"
               + (f"?p={page['page']}" if page["page"] != 1 else ""),
        "videoDurationSeconds": round(info["duration"], 2),
    }

    if args.dry_run:
        print("[3/5] --dry-run：跳过下载")
        print(f"  计划输出 : {out_wav}")
        return 0

    if args.print_metadata:
        entry = {
            "name": name,
            "audio": f"{name}.wav",
            "reference": f"{name}.txt",
            "sha256": "<运行一次后由本工具写入>",
            "durationSeconds": round(clip_s, 1),
            "durationMinutes": round(clip_s / 60, 2),
            "referenceChars": 0,
            "source": f"B站《{info['title']}》（UP：{info['owner']}）",
            "channel": "bilibili",
            "bv": bvid,
            "provenance": {
                "url": src_desc["url"], "title": info["title"], "author": info["owner"],
                "clip": f"{format_timecode(start_s)} ~ {format_timecode(end_s)}",
                "license": "平台公开传播；未经作者授权禁止转载 → 音频与逐字稿均不入库",
            },
            "consent": "仅用于本地项目内部评估，不再分发；含他人声音，故音频不入库",
            "recordedAt": time.strftime("%Y-%m-%d"),
            "notes": f"从 {format_timecode(start_s)} 起连续截取 {clip_s:.0f}s；源视频共 "
                     f"{info['duration']:.0f}s，单P 单人授课",
        }
        print("[3/5] --print-metadata：元数据条目如下")
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        return 0

    print("[3/5] 取 DASH 音频直链")
    streams, fallback = get_dash_audio(bvid, cid)
    print(f"  DASH 音频轨 {len(streams)} 条"
          + (f"；durl 兜底 {'有' if fallback else '无'}"))
    for s in streams:
        print(f"    id={s.get('id')} codec={s.get('codecs')} bw={s.get('bandwidth')}")

    src: Path
    # 🔴 下载策略（2026-10-26，两轮修正后定稿）
    #
    # ① **必须下完整**：`need_bytes`（按码率估的「够切片用」字节数）**只作参考打印，
    #    不作停止条件**。实测：B 站 DASH 的音频是 fMP4，**moov atom 在文件尾部**，
    #    截断到 30% 时 PyAV 解码直接炸在 `avcodec_send_packet()`。
    #    ⇒ 「够用即停」在这条链路上**不成立**。
    #
    # ② **靠 Range 续传把多次尝试累积起来**：这个 CDN **稳定地在 20,971,520 B 处切断**
    #    （连试 3 次，断点完全相同），但**能下到 46.1 MB / 48.0 MB（96%）**。
    #    ⇒ 单次下不完是常态，**续传**才是解药：每次从 `.part` 的长度继续 Range 请求，
    #    累积到 `Content-Length` 为止。
    #
    # ③ 因此 `min_bytes=0` ⇒ 语义是「下到 Content-Length 完整为止」，
    #    配合 `download()` 里的 Range 续传 + 重试。
    chosen = pick_audio_stream(streams) if streams else None
    need_s = end_s
    bw = int(chosen.get("bandwidth") or 0) if chosen else 0
    if bw:
        need_bytes = int(need_s * bw / 8 * 1.35)
        print(f"  目标区间到 {need_s:.0f}s ⇒ 码率 {bw/1000:.0f} kbps ⇒ "
              f"切片本身约需 {need_bytes/1e6:.1f} MB（仅参考；moov 在尾部，必须下完整）")
    if chosen:
        print(f"  选用 id={chosen.get('id')} bandwidth={chosen.get('bandwidth')}")
        src = Path(args.cache_dir) / f"{bvid}_{cid}.m4s"
        if not src.is_absolute():
            src = REPO / src
        if src.is_file() and src.stat().st_size > 0:
            print(f"  命中缓存：{src.name}（{src.stat().st_size/1e6:.2f} MB）")
        else:
            download(chosen["baseUrl"], src)
    elif fallback:
        print("  ⚠ 无 DASH 音频轨，回落到 durl（含视频流，体积更大）")
        src = Path(args.cache_dir) / f"{bvid}_{cid}.mp4"
        if not src.is_absolute():
            src = REPO / src
        download(fallback, src)
    else:
        raise SystemExit("既无 DASH 音频轨也无 durl，无法获取音频")

    print("[4/5] PyAV 解码 → 16k 单声道 → 切片")
    from eval_asr_quality import SAMPLE_RATE, load_audio  # noqa: E402

    audio = load_audio(src)
    if audio.size == 0:
        raise SystemExit(f"解码得到空音频：{src}")
    full_s = len(audio) / SAMPLE_RATE
    print(f"  解码成功：{full_s:.2f}s @ {SAMPLE_RATE}Hz，"
          f"{audio.size} 样本，{audio.nbytes/1e6:.1f} MB(float32)")

    clip = slice_samples(audio, SAMPLE_RATE, start_s, end_s)
    stats = audio_stats(clip, SAMPLE_RATE)
    write_wav(out_wav, clip, SAMPLE_RATE)
    digest = sha256_of(out_wav)

    print(f"[5/5] 已写出 {out_wav}")
    print(f"  时长     : {len(clip)/SAMPLE_RATE:.2f}s "
          f"（{len(clip)/SAMPLE_RATE/60:.2f} min）")
    print(f"  RMS/峰值 : {stats['rmsDbfs']} / {stats['peakDbfs']} dBFS")
    print(f"  近静音占比: {stats['quietRatio']:.1%}"
          + ("  ⚠ 偏高，请确认截取区间是否落在人声段落" if stats["quietRatio"] > 0.35 else ""))
    print(f"  sha256   : {digest}")
    print(f"  文件大小 : {out_wav.stat().st_size/1e6:.2f} MB")

    if not args.keep_source:
        src.unlink(missing_ok=True)
        print(f"  已清理源文件 {src.name}")
    else:
        print(f"  保留源文件 {src}")

    print("\n元数据条目（粘贴进 metadata.json 的 entries）：")
    entry = {
        "name": name,
        "audio": out_wav.name,
        "reference": f"{name}.txt",
        "sha256": digest,
        "durationSeconds": round(len(clip) / SAMPLE_RATE, 1),
        "durationMinutes": round(len(clip) / SAMPLE_RATE / 60, 2),
        "referenceChars": 0,
        "source": f"B站《{info['title']}》（UP：{info['owner']}）",
        "channel": "bilibili",
        "bv": bvid,
        "provenance": {
            "url": src_desc["url"], "title": info["title"], "author": info["owner"],
            "clip": f"{format_timecode(start_s)} ~ {format_timecode(end_s)}",
            "license": "平台公开传播；未经作者授权禁止转载 → 音频与逐字稿均不入库",
        },
        "consent": "仅用于本地项目内部评估，不再分发；含他人声音，故音频不入库",
        "recordedAt": time.strftime("%Y-%m-%d"),
        "notes": f"从 {format_timecode(start_s)} 起连续截取 "
                 f"{len(clip)/SAMPLE_RATE:.0f}s；近静音占比 {stats['quietRatio']:.0%}",
    }
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
