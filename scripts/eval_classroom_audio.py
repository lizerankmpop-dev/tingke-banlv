"""Run the ADR-003 10 dB classroom A/B gate at real-time speed."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from eval_asr_quality import reference_path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-dir", type=Path, default=root / "testset/chinese_lecture/real")
    parser.add_argument("--output", type=Path, default=root / "packaging/eval/classroom")
    parser.add_argument("--model", default="base")
    parser.add_argument("--realtime", action="store_true", required=True)
    args = parser.parse_args()
    source = args.audio_dir.resolve()
    clips = sorted(source.glob("*.wav"))
    if not clips:
        parser.error(f"未找到真实课堂 WAV：{source}")
    for clip in clips:
        reference = reference_path(clip)
        if reference is None or not reference.read_text(encoding="utf-8").strip():
            parser.error(f"缺少参考逐字稿：{clip.name}")

    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    comparison = []
    for index, clip in enumerate(clips, start=1):
        scores = {}
        for mode in ("baseline", "enhanced"):
            report = output / f"{index:03d}-{mode}.json"
            code = subprocess.call([
                sys.executable, str(root / "scripts/eval_asr_quality.py"),
                "--audio", str(clip), "--realtime", "--classroom", mode,
                "--language", "zh", "--model", args.model, "--report", str(report),
            ])
            if code != 0:
                print(f"评估未完成或未通过流式质量检查：{clip.name} / {mode}", file=sys.stderr)
                return 1
            result = json.loads(report.read_text(encoding="utf-8"))["results"][0]
            accuracy = result.get("accuracy") or {}
            if accuracy.get("cer") is None:
                print(f"报告缺少 CER：{report}", file=sys.stderr)
                return 1
            scores[mode] = accuracy["cer"]

        comparison.append({
            "file": clip.name,
            "baselineCer": scores["baseline"],
            "enhancedCer": scores["enhanced"],
            "passed": scores["enhanced"] <= scores["baseline"],
        })

    passed = all(row["passed"] for row in comparison)
    (output / "comparison.json").write_text(
        json.dumps({"snrDb": 10, "realtime": True, "passed": passed, "results": comparison},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for row in comparison:
        print(f"{row['file']}: CER {row['baselineCer']:.4f} → {row['enhancedCer']:.4f}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
