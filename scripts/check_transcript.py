#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐字稿合规校验（P3 素材验收前置）

P3 的唯一阻塞项是「真实中文课程音频 ≥10min + 人工逐字稿」。逐字稿是人写的，
就一定会出现「忘了改完就交」「直接把 ASR 草稿当逐字稿交」这类问题 —— 而它们的
后果很隐蔽：CER 看起来很漂亮，因为参考答案本身就是模型自己的输出。

本脚本把「逐字稿是否真的可用」变成机器可判的结论：

    <venv>/python.exe scripts/check_transcript.py --audio <音频> [--transcript <txt>]

判定项：
  1. 文件存在 / UTF-8 可读 / 无 BOM
  2. 非空、段数 > 0、字数达下限
  3. **字数与音频时长的合理区间**（语速字/秒）—— 时长对不上通常意味着截断或串了别的稿
  4. 未校对痕迹（`待校对` / `TODO` / `???` 等占位标记）
  5. **重复段**（同一长句出现 ≥3 次）—— ASR 重复循环没清理的典型指纹
  6. 截断迹象（最后一段过短）
  7. 与 `*.draft.json` 的差异度 —— 既证明「确实人工校对过」，也给出人工修改率

`--selftest` 会用合成的坏样本反过来验证「每条规则真的会红」——
没有这一步，校验器很容易写成「只在正确输入下返回 0」的空壳。
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MIN_CHARS = 200
CPS_RANGE = (1.2, 8.0)          # 字/秒 合理区间（中文讲稿实测约 2.5~6；含英文术语会更低）
REPEAT_MIN_LEN = 8              # 参与「重复段」判定的最短行
REPEAT_MAX_HITS = 3             # 同一行出现达到此次数即判 FAIL
TAIL_MIN_CHARS = 2
PLACEHOLDERS = ("待校对", "待补", "TODO", "todo", "???", "***", "固定为", "占位")


class Finding:
    __slots__ = ("level", "name", "detail")

    def __init__(self, level: str, name: str, detail: str) -> None:
        self.level = level          # OK | WARN | FAIL
        self.name = name
        self.detail = detail

    def as_dict(self) -> dict:
        return {"level": self.level, "name": self.name, "detail": self.detail}


def read_text(path: Path) -> tuple[str, list[str]]:
    raw = path.read_bytes()
    notes: list[str] = []
    if raw.startswith(b"\xef\xbb\xbf"):
        notes.append("文件带 UTF-8 BOM（建议去掉）")
        raw = raw[3:]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"{path.name} 不是合法 UTF-8：{exc}")
    return text, notes


def audit(
    text: str,
    audio_seconds: float | None,
    draft_text: str | None,
    notes: list[str] | None = None,
) -> list[Finding]:
    """纯函数：文本 + 音频时长 → 判定清单（便于自检探针直接调用）"""
    out: list[Finding] = []
    out.extend(Finding("WARN", "编码提示", n) for n in (notes or []))

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    chars = len(re.sub(r"\s+", "", text))

    if not lines or chars == 0:
        out.append(Finding("FAIL", "非空", "逐字稿为空"))
        return out
    out.append(Finding("OK", "非空", f"{len(lines)} 段 / {chars} 字"))

    if chars < MIN_CHARS:
        out.append(Finding("FAIL", "字数下限", f"{chars} 字 < {MIN_CHARS} —— 不足以支撑 CER 统计"))
    else:
        out.append(Finding("OK", "字数下限", f"{chars} 字 ≥ {MIN_CHARS}"))

    if audio_seconds:
        cps = chars / audio_seconds
        lo, hi = CPS_RANGE
        detail = f"{chars} 字 / {audio_seconds / 60:.2f} 分钟 = {cps:.2f} 字/秒（合理区间 {lo}~{hi}）"
        out.append(Finding("OK" if lo <= cps <= hi else "FAIL", "语速区间", detail))
    else:
        out.append(Finding("WARN", "语速区间", "未提供音频，跳过（时长对不上通常是截断或串稿）"))

    hits = [p for p in PLACEHOLDERS if p in text]
    out.append(
        Finding("FAIL", "未校对痕迹", f"命中占位标记 {hits} —— 草稿未改完") if hits
        else Finding("OK", "未校对痕迹", "无占位标记")
    )

    counter: dict[str, int] = {}
    for ln in lines:
        if len(ln) >= REPEAT_MIN_LEN:
            counter[ln] = counter.get(ln, 0) + 1
    repeats = {k: v for k, v in counter.items() if v >= REPEAT_MAX_HITS}
    out.append(
        Finding("FAIL", "重复段", f"{len(repeats)} 段重复 ≥{REPEAT_MAX_HITS} 次，例：{list(repeats)[:1]}") if repeats
        else Finding("OK", "重复段", "无异常重复")
    )

    out.append(
        Finding("WARN", "尾部完整性", f"末段仅 {len(lines[-1])} 字，疑似截断")
        if len(lines[-1]) < TAIL_MIN_CHARS
        else Finding("OK", "尾部完整性", "末段正常")
    )

    if draft_text:
        draft = re.sub(r"\s+", "", draft_text)
        cur = re.sub(r"\s+", "", text)
        ratio = difflib.SequenceMatcher(None, draft, cur).ratio()
        kept = ratio
        if kept > 0.985:
            out.append(
                Finding(
                    "WARN",
                    "人工校对痕迹",
                    f"与 ASR 草稿相似度 {kept:.3f} —— 几乎未修改。"
                    "若草稿确实全对可接受，但请确认不是「直接拿草稿当逐字稿」",
                )
            )
        else:
            out.append(
                Finding("OK", "人工校对痕迹", f"与草稿相似度 {kept:.3f}（人工修改率 {1 - kept:.1%}）")
            )

    return out


def draft_path_of(transcript: Path) -> Path | None:
    for cand in (transcript.with_suffix(".draft.txt"), transcript.parent / f"{transcript.stem}.draft.txt"):
        if cand.is_file():
            return cand
    return None


def audio_seconds(path: Path) -> float:
    """只读时长，不解码全部音频"""
    import wave

    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as w:
                return w.getnframes() / float(w.getframerate())
        except Exception:
            pass
    try:
        import av

        with av.open(str(path)) as c:
            for s in c.streams:
                if s.type == "audio" and s.duration is not None and s.time_base is not None:
                    return float(s.duration * s.time_base)
    except Exception:
        pass
    return 0.0


# ---------------- 自检探针：证明每条规则真的会红 ----------------
def selftest() -> int:
    good = "同学们好，今天这节课我们讲卷积神经网络。这个操作我们称为 convolution。"
    good = (good + "它的本质是用一个小的卷积核在输入特征图上滑动，每一步做一次加权求和。") * 6
    probes = [
        ("空稿", "", 600.0, None, "非空"),
        ("未校对标记", good + "\n待校对", 600.0, None, "未校对痕迹"),
        ("重复段", "\n".join(["这一句会重复很多次用来触发重复段判定"] * 5) + "\n" + good, 600.0, None, "重复段"),
        ("时长不匹配", good, 6.0, None, "语速区间"),
        ("字数不足", "短稿。", 600.0, None, "字数下限"),
        ("末段截断", good + "\n啊", 600.0, None, "尾部完整性"),
        ("直接交草稿", good, 600.0, good, "人工校对痕迹"),
    ]
    bad = 0
    for name, text, secs, draft, expect in probes:
        findings = audit(text, secs, draft)
        hit = [f for f in findings if f.name == expect and f.level != "OK"]
        mark = "✓" if hit else "✗"
        if not hit:
            bad += 1
        print(f"  {mark} 探针「{name}」→ 期望规则「{expect}」被判 {hit[0].level if hit else '未触发'}")
    print(f"自检结论：{len(probes) - bad}/{len(probes)} 条探针按预期触发" + ("" if bad == 0 else " ✗ 校验器存在盲区"))
    return 0 if bad == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="逐字稿合规校验")
    ap.add_argument("--audio", help="对应音频（用于时长一致性）")
    ap.add_argument("--transcript", help="逐字稿 txt（默认取音频同名 .txt）")
    ap.add_argument("--report", default=str(ROOT / "packaging" / "eval" / "transcript_check.json"))
    ap.add_argument("--selftest", action="store_true", help="用坏样本验证每条规则会红")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not args.transcript and not args.audio:
        raise SystemExit("需要 --audio 或 --transcript 之一")
    audio = Path(args.audio).expanduser().resolve() if args.audio else None
    transcript = Path(args.transcript).expanduser().resolve() if args.transcript else audio.with_suffix(".txt")
    if not transcript.is_file():
        raise SystemExit(f"找不到逐字稿：{transcript}")

    text, notes = read_text(transcript)
    secs = audio_seconds(audio) if audio and audio.is_file() else 0.0
    draft = draft_path_of(transcript)
    draft_text = draft.read_text(encoding="utf-8") if draft else None

    findings = audit(text, secs or None, draft_text, notes)

    print("=" * 96)
    print(f"逐字稿合规校验：{transcript.name}")
    if audio:
        print(f"  音频  : {audio.name}" + (f"（{secs / 60:.2f} 分钟）" if secs else "（未读到时长）"))
    print(f"  草稿  : {draft.name if draft else '未找到 *.draft.txt（跳过校对痕迹比对）'}")
    print("=" * 96)
    for f in findings:
        icon = {"OK": " OK ", "WARN": "WARN", "FAIL": "FAIL"}[f.level]
        print(f"[{icon}] {f.name}: {f.detail}")

    failed = [f for f in findings if f.level == "FAIL"]
    report = {
        "transcript": str(transcript),
        "audio": str(audio) if audio else None,
        "audioSeconds": round(secs, 3),
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "passed": not failed,
        "findings": [f.as_dict() for f in findings],
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("-" * 96)
    print(f"报告：{args.report}")
    print("结论：" + ("可用 ✓" if not failed else f"存在 {len(failed)} 项阻断 ✗"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
