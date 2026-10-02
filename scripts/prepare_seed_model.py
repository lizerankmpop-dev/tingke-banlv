"""
生成「随安装包分发的模型种子目录」

背景
----
安装包内置 base 模型，用户装完首次听课即可离线使用，不必等 139MB 下载。
模型放在 `packaging/seed/whisper_models/`，由 electron-builder 的
extraResources 复制到安装目录 `resources/whisper_models/`（不进 asar），
程序首次启动时再播种到应用本地数据目录（默认 `~/.tingkebanlv/whisper_models`），
与程序本体分离 —— 后续单独更新模型不必重装整个应用。

为什么只存一份文件
------------------
HF 缓存的标准布局是 blobs/ 存实体、snapshots/<rev>/ 放链接（Windows 上退化为
**实拷贝**）。若按标准布局整目录打包，同一个 model.bin 会以两份 145MB 进入
安装包（+290MB）。这里只保留 snapshots/ 里的实体文件 + refs/main 指向它，
体积正好一份，且 huggingface_hub 的 snapshot_download 能正常识别。

用法
----
    <venv>/Scripts/python.exe scripts/prepare_seed_model.py \
        --source C:/Users/Administrator/tk_test/model_base \
        --size base

    也可以直接指向已有的 HF 缓存目录（会自动挑出 snapshots 下的快照）。

产物
----
    packaging/seed/whisper_models/models--Systran--faster-whisper-<size>/
        refs/main                     ← 内容为快照目录名（固定 "seed"）
        snapshots/seed/*              ← 模型实体文件
    packaging/seed/whisper_models/manifest.json   ← sha256 + 大小，运行时校验用
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEED_DIR = REPO_ROOT / "packaging" / "seed" / "whisper_models"
SNAPSHOT_NAME = "seed"

# 与 faster_whisper.utils.download_model 的 allow_patterns 对齐，
# 少一个都可能让加载阶段报缺文件
MODEL_FILE_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.json",
    "vocabulary.txt",
)


def log(msg: str) -> None:
    print(msg, flush=True)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_model_files(source: Path) -> dict[str, Path]:
    """
    从 source 里找出模型文件

    两种来源都支持：
      - 扁平目录（faster-whisper 的 local_dir 下载结果）
      - HF 缓存目录（models--Systran--.../snapshots/<rev>/）
    """
    if not source.is_dir():
        raise SystemExit(f"源目录不存在: {source}")

    candidates: list[Path] = []
    for pattern in MODEL_FILE_PATTERNS:
        direct = source / pattern
        if direct.is_file():
            candidates.append(direct)

    if not candidates:
        # 试 HF 缓存布局：取最新修改的 snapshot
        snapshots_root = source / "snapshots"
        if snapshots_root.is_dir():
            snaps = sorted(
                (d for d in snapshots_root.iterdir() if d.is_dir()),
                key=lambda d: d.stat().st_mtime,
                reverse=True,
            )
            for snap in snaps:
                found = [snap / p for p in MODEL_FILE_PATTERNS if (snap / p).is_file()]
                if found:
                    candidates = found
                    break

    if not candidates:
        raise SystemExit(
            f"在 {source} 下没找到模型文件。期望其中至少包含 model.bin 与 config.json；\n"
            f"查找的模式: {', '.join(MODEL_FILE_PATTERNS)}"
        )

    files = {p.name: p for p in candidates}
    for required in ("model.bin", "config.json"):
        if required not in files:
            raise SystemExit(f"源目录缺少必需文件 {required}（实际找到: {sorted(files)}）")
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="生成随包分发的模型种子目录")
    parser.add_argument(
        "--source",
        required=True,
        help="模型来源目录（扁平目录或 HF 缓存目录）",
    )
    parser.add_argument("--size", default="base", help="模型规格，默认 base（约定只内置 base）")
    parser.add_argument("--seed-dir", default=str(DEFAULT_SEED_DIR), help="种子输出目录")
    parser.add_argument(
        "--force",
        action="store_true",
        help="已存在时强制重建（默认存在即跳过，避免重复拷 145MB）",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    seed_dir = Path(args.seed_dir).expanduser().resolve()
    repo_id = f"Systran/faster-whisper-{args.size}"
    model_root = seed_dir / f"models--{repo_id.replace('/', '--')}"

    ref_file = model_root / "refs" / "main"
    if ref_file.is_file() and not args.force:
        log(f"[跳过] 种子已存在: {model_root}")
        log("        如需重建请加 --force")
        return 0

    files = collect_model_files(source)
    log(f"[源] {source}")
    for name, path in sorted(files.items()):
        log(f"     {name:28s} {path.stat().st_size / 1048576:8.1f} MB")

    snapshot_dir = model_root / "snapshots" / SNAPSHOT_NAME
    if model_root.exists():
        log(f"[清理] 移除旧种子 {model_root}")
        shutil.rmtree(model_root)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    manifest_files: list[dict] = []
    total = 0
    for name, path in sorted(files.items()):
        target = snapshot_dir / name
        shutil.copy2(path, target)
        size = target.stat().st_size
        digest = sha256_of(target)
        manifest_files.append({"name": name, "size": size, "sha256": digest})
        total += size
        log(f"[拷贝] {name} -> {target.relative_to(seed_dir)}  ({size / 1048576:.1f} MB)")

    ref_file.parent.mkdir(parents=True, exist_ok=True)
    ref_file.write_text(SNAPSHOT_NAME, encoding="utf-8")

    manifest = {
        "schema": 1,
        "repoId": repo_id,
        "size": args.size,
        "snapshot": SNAPSHOT_NAME,
        "totalBytes": total,
        "files": manifest_files,
    }
    manifest_path = seed_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    log("")
    log(f"[完成] 种子目录: {seed_dir}")
    log(f"       模型体积: {total / 1048576:.1f} MB")
    log(f"       清单文件: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
