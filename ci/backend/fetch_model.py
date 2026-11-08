"""Download the fixed faster-whisper model revision for the py311-v3 CI image."""

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

ALLOW_PATTERNS = (
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.json",
    "vocabulary.txt",
)
REQUIRED_FILES = ("model.bin", "config.json", "tokenizer.json")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fail(message: str) -> None:
    print(f"MODEL DOWNLOAD FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    repo_id = os.environ["MODEL_REPO_ID"]
    revision = os.environ["MODEL_REVISION"]
    model_dir = Path(os.environ["MODEL_DIR"]).resolve()
    ci_root = Path(os.environ["TINGKE_CI_ROOT"]).resolve()
    endpoint = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com")

    os.environ.setdefault("HF_ENDPOINT", endpoint)
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        fail(f"invalid revision: {revision!r}")

    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        fail(f"huggingface_hub is not installed: {exc}")

    model_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=str(model_dir),
            allow_patterns=list(ALLOW_PATTERNS),
        )
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")

    shutil.rmtree(model_dir / ".cache", ignore_errors=True)

    missing = [name for name in REQUIRED_FILES if not (model_dir / name).is_file()]
    if missing:
        fail(f"missing required files: {missing}")
    if (model_dir / "model.bin").stat().st_size < 1024 * 1024:
        fail("model.bin is smaller than 1MB")

    files = sorted(path for path in model_dir.rglob("*") if path.is_file())
    manifest_lines = []
    total = 0
    for path in files:
        relative = path.relative_to(model_dir).as_posix()
        size = path.stat().st_size
        manifest_lines.append(f"{sha256(path)}  {relative}")
        total += size
        print(f"MODEL FILE {relative} {size}")

    ci_root.mkdir(parents=True, exist_ok=True)
    (ci_root / "model.sha256").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8"
    )
    (ci_root / "model-revision.txt").write_text(revision + "\n", encoding="utf-8")
    print(f"MODEL DOWNLOAD OK {repo_id}@{revision} files={len(files)} bytes={total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
