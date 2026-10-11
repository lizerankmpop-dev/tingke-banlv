#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
accent（口音）场景基线运行器 —— 素材校验 + 一键跑（V5-7）

## 为什么需要一个独立的校验步骤

跑一次基线要先把模型加载起来（base 约 3~4 s + 识别 60~130 s 实时），
如果素材缺个参考稿、或文件名不匹配场景词，会在**跑完之后**才体现为
「进了 other 桶 / 没有 accuracy 字段」—— 白等十分钟，还得回头翻日志找原因。
所以把"素材就位没有"提前成一次**秒级**的静态校验。

## 三种退出状态（刻意区分，不合并）

| 退出码 | 状态 | 含义 |
|---|---|---|
| 0 | `ok` / `no-material` | 素材就位可跑；或素材**尚未提供**（这是当前预期状态，D4 待办） |
| 1 | `invalid-material` | 素材在了但**不合规**（分辨率/声道/时长/参考稿/文件名/元数据），必须修 |
| 2 | 用法错误 / `--run` 执行失败 | |

为什么把"没有素材"和"通过"分开打印而不是都退出 0：
「跳过」与「通过」在流水线上完全同形是这套评估里最危险的失效形态
（同 `packaging/verify/check_simplified.py` 模块头的讨论）。所以本脚本
**显式打印 `SKIPPED-NO-MATERIAL`**，并在 `--json` 里落 `status` 字段。

## 场景名不另抄一份

文件名 → 场景名 的规则直接从 `scripts/eval_asr_quality.py` 里 **import 同一个
`scenario_of()`**。另抄一份正则的下场是：评估脚本改了匹配规则，本脚本还在用旧的
判"通过"，于是校验说 OK、跑出来却进了 other 桶。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ACCENT_DIR = REPO / "testset" / "chinese_lecture" / "accent"
EVAL_SCRIPT = REPO / "scripts" / "eval_asr_quality.py"
REPORT_OUT = REPO / "packaging" / "eval" / "accent_baseline.json"

# 素材规格（与 accent/README.md 的表一致）
EXPECTED_SR = 16000
EXPECTED_CHANNELS = 1
MIN_SECONDS = 30.0          # 下界放宽到 30s：短素材也能反映口音，只是统计更抖
MAX_SECONDS = 300.0
SOURCE_KIND = "human-recording"

STATUS_OK = "ok"
STATUS_NO_MATERIAL = "no-material"
STATUS_INVALID = "invalid-material"


def _load_scenario_of():
    """从评估脚本里取 `scenario_of`（唯一真源，见模块头）"""
    spec = importlib.util.spec_from_file_location("_eval_asr_quality", EVAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {EVAL_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.scenario_of


def _probe_wav(path: Path) -> dict:
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        frames = w.getnframes()
    return {
        "sampleRate": sr,
        "channels": ch,
        "seconds": round(frames / sr, 2) if sr else 0.0,
    }


def validate(accent_dir: Path = ACCENT_DIR) -> dict:
    """静态校验：返回 {status, problems[], wav, reference, seconds...}"""
    problems: list[str] = []
    wavs = sorted(p for p in accent_dir.glob("*.wav")) if accent_dir.is_dir() else []

    if not wavs:
        return {
            "status": STATUS_NO_MATERIAL,
            "accentDir": str(accent_dir),
            "problems": [],
            "detail": (
                "accent 素材尚未提供 —— 这是**预期状态**：accent 场景刻意不合成，"
                "等真人带口音的录音（见 accent/README.md 与 known_defects.md D4）。"
                "需要的是：BV 号 + 起止时间，然后按 README 第二节抓取并做定稿参考稿。"
            ),
        }

    scenario_of = _load_scenario_of()
    results = []
    for wav in wavs:
        item: dict = {"audio": wav.name, "problems": []}

        # 1) 文件名必须被评估脚本认成 accent（否则进 other 桶，静默丢结果）
        scene = scenario_of(wav.name)
        item["scenario"] = scene
        if scene != "accent":
            item["problems"].append(
                f"文件名被判成 scenario={scene}（应为 accent）。"
                f"命名规则见 accent/README.md 第一节：需要 `accent_*` / `*_accent_*` / `*_accent.`"
            )

        # 2) 参考稿：定稿 `.preproof.txt` 优先，退化到 `.txt`
        ref = None
        for cand in (wav.with_suffix(".preproof.txt"), wav.with_suffix(".txt")):
            if cand.is_file() and cand.stat().st_size > 0:
                ref = cand
                break
        item["reference"] = ref.name if ref else None
        if ref is None:
            item["problems"].append(
                "缺参考稿：需要同名 `.preproof.txt`（定稿，优先）或 `.txt`。没有参考稿就算不出 CER"
            )
        else:
            item["referenceIsPreproof"] = ref.suffixes[-2:] == [".preproof", ".txt"]

        # 3) 规格
        try:
            info = _probe_wav(wav)
            item.update(info)
            if info["sampleRate"] != EXPECTED_SR:
                item["problems"].append(f"采样率 {info['sampleRate']} ≠ {EXPECTED_SR}")
            if info["channels"] != EXPECTED_CHANNELS:
                item["problems"].append(f"声道数 {info['channels']} ≠ {EXPECTED_CHANNELS}")
            if not (MIN_SECONDS <= info["seconds"] <= MAX_SECONDS):
                item["problems"].append(
                    f"时长 {info['seconds']}s 不在 {MIN_SECONDS}~{MAX_SECONDS}s 区间"
                )
        except Exception as e:                        # noqa: BLE001
            item["problems"].append(f"读取 wav 失败：{e}")

        if item["problems"]:
            problems += [f"{wav.name}: {p}" for p in item["problems"]]
        results.append(item)

    # 4) 元数据：没有它 sourceKind 会落 unknown，汇总里会被单独标出来
    meta = accent_dir / "metadata.json"
    if not meta.is_file():
        problems.append(
            "缺 accent/metadata.json：sourceKind 会落 unknown 并在汇总里被标记。"
            "模板见 accent/README.md 第二节"
        )

    return {
        "status": STATUS_INVALID if problems else STATUS_OK,
        "accentDir": str(accent_dir),
        "files": results,
        "problems": problems,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="accent 场景素材校验 + 基线运行（V5-7）")
    ap.add_argument("--run", action="store_true", help="校验通过后真正跑基线")
    ap.add_argument("--accent-dir", type=Path, default=ACCENT_DIR)
    ap.add_argument("--report", type=Path, default=REPORT_OUT)
    ap.add_argument("--json", type=Path, help="把校验结果落盘")
    args = ap.parse_args(argv)

    rep = validate(args.accent_dir)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    if rep["status"] == STATUS_NO_MATERIAL:
        print("SKIPPED-NO-MATERIAL")
        print(rep["detail"])
        return 0

    if rep["status"] == STATUS_INVALID:
        print(f"❌ accent 素材不合规（{len(rep['problems'])} 项）：")
        for p in rep["problems"]:
            print(f"  · {p}")
        return 1

    print(f"✅ accent 素材就位：{len(rep['files'])} 个文件")

    if not args.run:
        for f in rep["files"]:
            print(f"   · {f['audio']} → scenario={f['scenario']} / {f['seconds']}s "
                  f"/ 参考稿 {f['reference']}")
        print("\n（未加 --run，只做校验。真跑请加 --run）")
        return 0

    wav = args.accent_dir / rep["files"][0]["audio"]
    cmd = [sys.executable, str(EVAL_SCRIPT), "--audio", str(wav.relative_to(REPO)),
           "--realtime", "--source-kind", SOURCE_KIND, "--report", str(args.report)]
    print("执行：" + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(REPO))
    if proc.returncode != 0:
        print(f"!! 基线执行失败 rc={proc.returncode}")
        return 2
    print(f"报告已写入 {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
