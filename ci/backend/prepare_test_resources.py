"""准备后端 CI 测试所需的真实模型种子资源

流程：
  1. 解析 Systran/faster-whisper-base 的 main 分支当前 commit SHA（打印后再用该 SHA 下载）；
  2. 调用仓库现有的 scripts/prepare_seed_model.py 生成 packaging/seed/whisper_models；
  3. 用 backend/asr/model_bootstrap.py 的既有校验逻辑验证清单、文件与 sha256。

产物全部位于 .gitignore 覆盖的 packaging/seed/ 内，不会进入提交。
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
SEED_SCRIPT = REPO_ROOT / "scripts" / "prepare_seed_model.py"
MODEL_SIZE = "base"
REPO_ID = f"Systran/faster-whisper-{MODEL_SIZE}"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from prepare_seed_model import DEFAULT_SEED_DIR, MODEL_FILE_PATTERNS  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="准备后端 CI 测试所需的真实模型种子")
    parser.add_argument("--seed-dir", default=str(DEFAULT_SEED_DIR), help="种子输出目录")
    parser.add_argument(
        "--cache-dir",
        default=str(Path(tempfile.gettempdir()) / "tingke-banlv-ci-hf"),
        help="HuggingFace 下载缓存（仓库外，避免被提交）",
    )
    parser.add_argument("--endpoint", default=None, help="HF_ENDPOINT，默认 hf-mirror.com")
    parser.add_argument(
        "--emit-env",
        action="store_true",
        help="stdout 只输出 TINGKEBANLV_TOKENIZER_JSON / TINGKEBANLV_TEST_MODEL_DIR 的 export",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    def log(message: str) -> None:
        print(message, file=sys.stderr if args.emit_env else sys.stdout)

    def fail(message: str) -> int:
        log(f"[失败] {message}")
        return 1

    if os.environ.get("HF_HUB_OFFLINE") == "1":
        return fail("HF_HUB_OFFLINE=1 会阻止下载；准备资源阶段不要设置该变量")

    if args.endpoint:
        os.environ["HF_ENDPOINT"] = args.endpoint
    else:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError:
        return fail("未安装 huggingface_hub；请先安装 backend/requirements.txt")

    cache_dir = Path(args.cache_dir).expanduser().resolve()
    seed_dir = Path(args.seed_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    log(f"[仓库] {REPO_ID}")
    log(f"[endpoint] {os.environ['HF_ENDPOINT']}")
    log(f"[缓存] {cache_dir}")

    try:
        info = HfApi().model_info(REPO_ID, revision="main")
    except Exception as exc:
        return fail(f"解析 revision 失败: {type(exc).__name__}: {exc}")

    revision = str(getattr(info, "sha", "") or "")
    if len(revision) < 7:
        return fail(f"model_info 未返回有效的 commit SHA: {info!r}")
    log(f"[revision] {REPO_ID}@main -> {revision}")

    try:
        snapshot = snapshot_download(
            repo_id=REPO_ID,
            revision=revision,
            cache_dir=str(cache_dir),
            allow_patterns=list(MODEL_FILE_PATTERNS),
        )
    except Exception as exc:
        return fail(f"下载 {REPO_ID}@{revision} 失败: {type(exc).__name__}: {exc}")

    snapshot_dir = Path(snapshot).resolve()
    log(f"[下载快照] {snapshot_dir}")

    missing = [name for name in ("model.bin", "config.json", "tokenizer.json") if not (snapshot_dir / name).is_file()]
    if missing:
        return fail(f"下载结果缺少文件: {missing}")
    if (snapshot_dir / "model.bin").stat().st_size < 1024 * 1024:
        return fail("model.bin 小于 1MB，疑似下载不完整")

    cmd = [
        sys.executable,
        str(SEED_SCRIPT),
        "--source",
        str(snapshot_dir),
        "--size",
        MODEL_SIZE,
        "--seed-dir",
        str(seed_dir),
        "--force",
    ]
    log(f"[生成种子] {' '.join(shlex.quote(part) for part in cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        log(line)
    for line in proc.stderr.splitlines():
        log(line)
    if proc.returncode != 0:
        return fail(f"scripts/prepare_seed_model.py 退出码 {proc.returncode}")

    work_dir = Path(tempfile.mkdtemp(prefix="tingke-ci-model-"))
    os.environ["TINGKEBANLV_SEED_MODEL_DIR"] = str(seed_dir)
    os.environ["WHISPER_MODEL_DIR"] = str(work_dir)
    sys.path.insert(0, str(BACKEND_DIR))
    try:
        from asr import model_bootstrap as mb

        root = mb.seed_root()
        if root is None or root.resolve() != seed_dir:
            return fail(f"seed_root() 未解析到目标种子目录: {root}")

        manifest = mb.load_manifest(root)
        if manifest is None:
            return fail(f"manifest.json 不可用: {root / mb.MANIFEST_NAME}")
        if manifest.get("repoId") != REPO_ID or manifest.get("size") != MODEL_SIZE:
            return fail(
                f"manifest 与预期不符: repoId={manifest.get('repoId')} size={manifest.get('size')}"
            )

        available = mb.seed_available()
        if not available.get("available"):
            return fail(f"seed_available() 判定种子不可用: {available.get('detail')}")

        seeded = mb.seed_model(MODEL_SIZE, reason="ci-verify")
        if not seeded.get("ok"):
            return fail(f"播种校验失败: {seeded.get('action')}: {seeded.get('detail')}")

        checked = mb.check_installed(MODEL_SIZE, deep=True)
        if not checked.get("ok"):
            return fail(f"播种后 sha256 校验失败: {checked.get('problems')}")

        seed_snapshot = (
            root
            / f"models--{manifest['repoId'].replace('/', '--')}"
            / "snapshots"
            / (manifest.get("snapshot") or mb.SNAPSHOT_NAME)
        )
        for name in (*mb.REQUIRED_FILES, "tokenizer.json"):
            if not (seed_snapshot / name).is_file():
                return fail(f"种子缺少模型加载必需文件: {name}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    total_bytes = int(manifest.get("totalBytes") or 0)
    actual_bytes = sum(path.stat().st_size for path in seed_snapshot.iterdir() if path.is_file())
    log("")
    log(f"[种子目录] {seed_dir}")
    log(f"[快照目录] {seed_snapshot}")
    log(f"[清单文件] {root / mb.MANIFEST_NAME}")
    for entry in manifest["files"]:
        log(f"  - {entry['name']:<26} {entry['size'] / 1048576:8.2f} MB  sha256={entry['sha256'][:12]}…")
    log(f"[体积] manifest={total_bytes} B / 实际={actual_bytes} B ({actual_bytes / 1048576:.1f} MB)")
    log(f"[模型 revision] {revision}")

    tokenizer_json = seed_snapshot / "tokenizer.json"
    if args.emit_env:
        print(f"export TINGKEBANLV_TOKENIZER_JSON={shlex.quote(str(tokenizer_json.resolve()))}")
        print(f"export TINGKEBANLV_TEST_MODEL_DIR={shlex.quote(str(seed_dir))}")
    else:
        log("")
        log("测试环境变量（绝对路径）：")
        log(f"  export TINGKEBANLV_TOKENIZER_JSON={shlex.quote(str(tokenizer_json.resolve()))}")
        log(f"  export TINGKEBANLV_TEST_MODEL_DIR={shlex.quote(str(seed_dir))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
