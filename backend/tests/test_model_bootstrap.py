"""内置模型播种与完整性校验的回归测试

覆盖 asr/model_bootstrap.py。这几条路径直接决定安装包「首启即可用、断网可用」
这个卖点是否成立，因此必须锁住：

  * 空数据目录 → 从内置种子播种（seeded）
  * 已完整 → 不重复拷贝（verified，幂等）
  * model.bin 被截断 → 大小校验先发现，自动修复（repaired）
  * model.bin 被改 1 个字节 → 只有 sha256 能发现，同样自动修复
  * 没有内置种子 → 返回 unavailable 而不是抛异常（交给联网下载）
  * 播种结果必须含 refs/main

种子目录（packaging/seed/whisper_models）是 gitignore 的 141MB 二进制，
由 scripts/prepare_seed_model.py 生成。未生成时相关用例 skip，
而不是失败 —— CI 上不该要求先跑一次模型下载。
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asr import model_bootstrap as mb  # noqa: E402


@pytest.fixture
def seed_dir():
    """内置种子目录；未生成则跳过"""
    root = mb.seed_root()
    if root is None:
        pytest.skip("未生成内置模型种子（先执行 scripts/prepare_seed_model.py）")
    return root


@pytest.fixture
def work_home(tmp_path, monkeypatch):
    """把工作目录指到临时目录，避免污染真实 ~/.tingkebanlv"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("TINGKEBANLV_HOME", str(home))
    monkeypatch.delenv("WHISPER_MODEL_DIR", raising=False)
    return home


# ============ 路径解析 ============

def test_seed_root_dev_mode(seed_dir):
    """开发模式应从 repo 的 packaging/seed/whisper_models 解析到种子"""
    assert (seed_dir / mb.MANIFEST_NAME).is_file()
    manifest = json.loads((seed_dir / mb.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["schema"] == mb.INTEGRITY_SCHEMA
    assert manifest["size"] in mb.SEED_SIZES
    # blobs/ 不随包分发：Windows 无符号链接，留着等于体积翻倍
    assert not (seed_dir / f"models--{manifest['repoId'].replace('/', '--')}" / "blobs").exists()


def test_env_override_wins(work_home, tmp_path, monkeypatch):
    """TINGKEBANLV_SEED_MODEL_DIR 优先级最高（主进程显式告知）"""
    fake = tmp_path / "fake_seed"
    fake.mkdir()
    # 显式指定（且目录真实存在）时必须被采信，哪怕里面还没有清单 ——
    # 缺清单属于「种子内容不完整」，会在 load_manifest 处被明确拒绝，
    # 不应该悄悄回退到 repo 里的真种子，否则打包机与开发机行为不一致。
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(fake))
    assert mb.seed_root() == fake
    assert mb.seed_available()["available"] is False

    # 目录不存在 → 视为未指定
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(tmp_path / "not_exist"))
    assert mb.seed_root() is None


def test_model_root_follows_home(work_home):
    assert mb.model_root() == work_home / "whisper_models"


# ============ 校验 ============

def test_check_installed_missing(work_home):
    status = mb.check_installed("base")
    assert status["ok"] is False
    assert status["reason"] == "missing"


def test_size_check_catches_truncation(work_home, seed_dir):
    """浅校验（只比大小）必须能发现截断 —— 这是每次启动的快路径"""
    assert mb.ensure_model("base")["ok"]
    snapshot = Path(mb.check_installed("base")["snapshot"])

    with (snapshot / "model.bin").open("r+b") as fh:
        fh.truncate(4096)

    status = mb.check_installed("base", deep=False)
    assert status["ok"] is False
    assert any("model.bin" in p for p in status["problems"])


def test_deep_check_catches_single_byte_flip(work_home, seed_dir):
    """大小不变、内容变了 —— 只有 sha256 抓得到"""
    assert mb.ensure_model("base")["ok"]
    snapshot = Path(mb.check_installed("base")["snapshot"])

    with (snapshot / "model.bin").open("r+b") as fh:
        fh.seek(1000)
        fh.write(b"\xff")

    assert mb.check_installed("base", deep=False)["ok"] is True  # 浅校验放行（预期）
    deep = mb.check_installed("base", deep=True)
    assert deep["ok"] is False
    assert any("校验和" in p for p in deep["problems"])


def test_no_manifest_degrades_to_size_check(work_home, seed_dir, monkeypatch):
    """用户自己下载的 small/medium 没有清单，退化为大小检查而不是判死"""
    assert mb.ensure_model("base")["ok"]
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(work_home / "absent"))
    assert mb.load_manifest(mb.seed_root()) is None
    assert mb.check_installed("base", deep=True)["ok"] is True


# ============ 播种 ============

def test_seed_creates_refs_and_snapshot(work_home, seed_dir):
    result = mb.ensure_model("base", deep_check=True)
    assert result["ok"] is True
    assert result["action"] == "seeded"

    model_dir = work_home / "whisper_models" / "models--Systran--faster-whisper-base"
    # refs/main 必须有：缺了它 local_files_only 加载会报找不到 snapshot
    assert (model_dir / "refs" / "main").read_text(encoding="utf-8") == mb.SNAPSHOT_NAME
    snapshot = model_dir / "snapshots" / mb.SNAPSHOT_NAME
    for name in mb.REQUIRED_FILES:
        assert (snapshot / name).is_file()


def test_seed_is_idempotent(work_home, seed_dir):
    assert mb.ensure_model("base", deep_check=True)["action"] == "seeded"
    second = mb.ensure_model("base", deep_check=True)
    assert second["action"] == "verified"


def test_repair_after_truncation(work_home, seed_dir):
    assert mb.ensure_model("base", deep_check=True)["ok"]
    snapshot = Path(mb.check_installed("base")["snapshot"])
    original_size = (snapshot / "model.bin").stat().st_size

    with (snapshot / "model.bin").open("r+b") as fh:
        fh.truncate(4096)

    repaired = mb.ensure_model("base", deep_check=True)
    assert repaired["ok"] is True
    assert repaired["action"] == "repaired"
    assert (snapshot / "model.bin").stat().st_size == original_size
    assert mb.check_installed("base", deep=True)["ok"] is True


def test_seed_cleans_stale_snapshot(work_home, seed_dir):
    """播种/修复时要清掉残缺旧快照，否则下次加载仍报 model.bin incomplete"""
    assert mb.ensure_model("base", deep_check=True)["ok"]
    model_dir = work_home / "whisper_models" / "models--Systran--faster-whisper-base"
    snapshots = model_dir / "snapshots"
    stale = snapshots / "deadbeef"
    stale.mkdir()
    (stale / "model.bin").write_bytes(b"garbage")

    # 只加了个垃圾目录、正本仍完好 → 走快路径，不做无谓的 141MB 拷贝
    assert mb.ensure_model("base", deep_check=True)["action"] == "verified"

    # 真的需要重播（正本被截断）时，垃圾目录必须一并清掉
    with (snapshots / mb.SNAPSHOT_NAME / "model.bin").open("r+b") as fh:
        fh.truncate(4096)

    assert mb.ensure_model("base", deep_check=True)["action"] == "repaired"
    assert not stale.exists()
    assert mb.check_installed("base", deep=True)["ok"] is True


def test_unavailable_without_seed(work_home, seed_dir, monkeypatch):
    """没有种子不是错误：交给上层走联网下载，不能抛异常把启动打挂"""
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(work_home / "absent"))
    result = mb.ensure_model("base", deep_check=True)
    assert result["ok"] is False
    assert result["action"] == "unavailable"
    assert "联网下载" in result["detail"]


def test_size_mismatch_between_seed_and_request(work_home, seed_dir, monkeypatch):
    """内置的只有 base，请求 small 时应明确拒绝而不是拿 base 冒充"""
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(seed_dir))
    result = mb.ensure_model("small", deep_check=True)
    # 契约是 ok=False；action 只是给人看的诊断信息
    assert result["ok"] is False
    assert result["action"] == "no-seed"
    assert "不含 small" in result["detail"]
    # 不能因为失败就写出半个 small 目录，那会污染后续下载的判断
    assert not (work_home / "whisper_models" / "models--Systran--faster-whisper-small").exists()


# ============ 报告 ============

def test_seed_available_reports_files(seed_dir, monkeypatch):
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(seed_dir))
    info = mb.seed_available()
    assert info["available"] is True
    assert "base" in info["detail"]


def test_seed_available_when_missing(work_home, monkeypatch):
    monkeypatch.setenv("TINGKEBANLV_SEED_MODEL_DIR", str(work_home / "absent"))
    assert mb.seed_available()["available"] is False


def test_cleanup_marker_roundtrip(work_home, seed_dir):
    assert mb.has_verified_marker("base") is False
    assert mb.ensure_model("base", deep_check=True)["ok"]
    # ensure_model 内部会落标记，避免每次启动重算 145MB 的 sha256
    assert mb.has_verified_marker("base") is True
    shutil.rmtree(mb.model_root(), ignore_errors=True)
    assert mb.has_verified_marker("base") is False
