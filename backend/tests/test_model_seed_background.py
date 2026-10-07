"""
后台播种状态机（`model_bootstrap.start_background_seed`）—— 单元测试

为什么单独测：它是首启向导第 3 步「正在复制模型种子」的唯一数据来源，而它跑在
**后台线程**里 —— 状态更新的竞态、异常吞噬、重复启动这三类问题都无法在接口层
观察到（接口只看到一份快照）。这里用假 `ensure_model` 在毫秒级把它们测全。

设计约束（改动时要一起保持）：
  · 幂等：正在跑 / 已成功 → 不再启动；**失败后允许重试**（用户点「重试」或重启应用）
  · 不阻塞：`start_background_seed` 只启线程就返回，绝不自己拷贝
  · 不崩溃：线程里任何异常都要落成状态，不能让「running」永远为真
  · 状态里只放**稳定枚举**，中文细节只进日志（接口会把它原样返回，见 routes.py）
"""

import json
import threading
import time

import pytest

import asr.model_bootstrap as mb


@pytest.fixture(autouse=True)
def _reset_state():
    """每个用例前后都清状态：模块级单例状态最容易在用例间串味"""
    mb.reset_background_seed_state_for_tests()
    yield
    mb.reset_background_seed_state_for_tests()


def _wait_done(timeout: float = 5.0) -> dict:
    """等后台线程收敛（假 ensure_model 是毫秒级的，超时只防挂死）"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = mb.background_seed_state()
        if state["done"] and not state["running"]:
            return state
        time.sleep(0.02)
    return mb.background_seed_state()


def test_success_updates_state(monkeypatch):
    monkeypatch.setattr(mb, "ensure_model", lambda size="base": {"ok": True, "action": "seeded", "detail": "x"})
    assert mb.start_background_seed() is True, "首次调用应当真的起线程"
    state = _wait_done()
    assert state["done"] is True
    assert state["ok"] is True
    assert state["error"] is None
    assert state["running"] is False
    assert isinstance(state["at"], float)


def test_failure_records_stable_error_kind(monkeypatch):
    """失败时状态里只放分类枚举，中文 detail 不进状态（只进日志）"""
    monkeypatch.setattr(
        mb, "ensure_model",
        lambda size="base": {"ok": False, "action": "verify-failed", "detail": "model.bin 校验和不通"},
    )
    mb.start_background_seed()
    state = _wait_done()
    assert state["ok"] is False
    assert state["error"] == "integrity"
    assert "校验和" not in json.dumps(state, ensure_ascii=False), "诊断细节不该进状态"


def test_exception_is_swallowed(monkeypatch):
    """线程里抛异常不能让 running 永远为真（否则前端永远「请稍候」）"""
    def boom(size="base"):
        raise OSError("disk full")

    monkeypatch.setattr(mb, "ensure_model", boom)
    mb.start_background_seed()
    state = _wait_done()
    assert state["ok"] is False
    assert state["error"] == "unknown"
    assert state["running"] is False, "异常后必须把 running 置回 False"


def test_idempotent_after_success(monkeypatch):
    """已经成功过就不再启动（避免每次启动都白跑一遍线程）"""
    monkeypatch.setattr(mb, "ensure_model", lambda size="base": {"ok": True, "action": "verified", "detail": ""})
    assert mb.start_background_seed() is True
    assert _wait_done()["ok"] is True
    assert mb.start_background_seed() is False, "已成功过仍重复启动"

    def should_not_run(size="base"):
        raise AssertionError("已成功过却又调用了 ensure_model")

    monkeypatch.setattr(mb, "ensure_model", should_not_run)
    assert mb.start_background_seed() is False


def test_idempotent_while_running(monkeypatch):
    """进行中再调用只返回 False，不会起第二个线程"""
    gate = threading.Event()

    def slow(size="base"):
        gate.wait(2)
        return {"ok": True, "action": "seeded", "detail": ""}

    monkeypatch.setattr(mb, "ensure_model", slow)
    assert mb.start_background_seed() is True
    time.sleep(0.05)
    assert mb.background_seed_state()["running"] is True
    assert mb.start_background_seed() is False, "播种进行中不该再起一个线程"
    gate.set()
    assert _wait_done()["ok"] is True


def test_retry_after_failure(monkeypatch):
    """失败之后**允许**重试：用户点「重试」、或关掉应用重开都会走到这里"""
    monkeypatch.setattr(mb, "ensure_model", lambda size="base": {"ok": False, "action": "no-seed", "detail": ""})
    mb.start_background_seed()
    assert _wait_done()["error"] == "no-seed"

    monkeypatch.setattr(mb, "ensure_model", lambda size="base": {"ok": True, "action": "seeded", "detail": ""})
    assert mb.start_background_seed() is True, "失败后应当允许重试"
    assert _wait_done()["ok"] is True


def test_error_kind_mapping():
    """分类映射是接口对外的稳定契约，改名要显式改这里"""
    assert mb._error_kind("unavailable") == "no-seed"
    assert mb._error_kind("no-seed") == "no-seed"
    assert mb._error_kind("error") == "io-error"
    assert mb._error_kind("verify-failed") == "integrity"
    assert mb._error_kind("something-new") == "unknown"


# ============ seed_exists（接口的 seed_exists 字段来源） ============

def _fake_seed_dir(tmp_path, names=("model.bin", "config.json"), size="base"):
    d = tmp_path / "whisper_models"
    snap = d / "models--Systran--faster-whisper-base" / "snapshots" / "seed"
    snap.mkdir(parents=True, exist_ok=True)
    for n in names:
        (snap / n).write_bytes(b"x")
    (d / "manifest.json").write_text(
        json.dumps({
            # schema 是 load_manifest 的硬校验项（INTEGRITY_SCHEMA=1）——
            # 少了它清单会被判「结构不符合预期」，整个测试就变成在测失败路径
            "schema": 1,
            "repoId": "Systran/faster-whisper-base",
            "size": size,
            "snapshot": "seed",
            "totalBytes": len(names),
            "files": [{"name": n, "size": 1, "sha256": "0" * 64} for n in names],
        }),
        encoding="utf-8",
    )
    return d


def test_seed_exists_true_for_complete_seed(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "seed_root", lambda: _fake_seed_dir(tmp_path))
    assert mb.seed_exists("base") is True


def test_seed_exists_false_without_seed_root(monkeypatch):
    monkeypatch.setattr(mb, "seed_root", lambda: None)
    assert mb.seed_exists("base") is False


def test_seed_exists_false_when_file_missing(monkeypatch, tmp_path):
    """清单里有两个文件但只落了一个 → 不可播种（否则拷出来是残缺模型）"""
    d = _fake_seed_dir(tmp_path)
    (d / "models--Systran--faster-whisper-base" / "snapshots" / "seed" / "config.json").unlink()
    monkeypatch.setattr(mb, "seed_root", lambda: d)
    assert mb.seed_exists("base") is False


def test_seed_exists_false_for_other_size(monkeypatch, tmp_path):
    """种子只有 base 却问 small → 不可播种"""
    monkeypatch.setattr(mb, "seed_root", lambda: _fake_seed_dir(tmp_path))
    assert mb.seed_exists("small") is False
