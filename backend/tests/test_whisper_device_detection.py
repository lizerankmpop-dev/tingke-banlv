"""ASR 设备探测与 CUDA 回落回归测试（2026-11-11）

背景：修延迟时顺带发现一个**架构级错误** —— `_detect_device()` 原先只用
`torch.cuda.is_available()` 探测 GPU。faster-whisper 底层是 **ctranslate2**，
CUDA 支持由 ctranslate2 + CUDA 运行时（cuBLAS/cuDNN）决定，**与 torch 无关**。
而 CPU 版 torch（`torch+cpu`）的 `is_available()` 永远是 False ⇒
**有 RTX 独显的机器也被判成 CPU**，独显全程闲置。

本文件锁死三件事：
    ① 设备探测以 ctranslate2 为准，不以 torch 为准；
    ② **枚举到 GPU ≠ 能推理** —— 缺 cuBLAS 时必须安全走 CPU，
       而不是等到 encoder 阶段才抛 `RuntimeError: Library cublas64_12.dll ...`
       把整条识别管道炸掉；
    ③ 构造阶段失败时能自动回落 CPU（第二道防线）。
"""

import sys
import types

import pytest

from asr import whisper_engine


def _fake_ctranslate2(gpu_count: int):
    """造一个可控的 ctranslate2 替身"""
    mod = types.SimpleNamespace(get_cuda_device_count=lambda: gpu_count)
    return mod


# --------------------------------------------------------------------------
# ① 设备探测以 ctranslate2 为准
# --------------------------------------------------------------------------

def test_ctranslate2_gpu_is_detected_even_when_torch_says_no(monkeypatch):
    """🔴 ctranslate2 枚举到 GPU 就该用 GPU，哪怕 torch 说没有。

    修复前：torch+cpu 的 `cuda.is_available()` 永远 False ⇒ 独显被判成 CPU。
    """
    monkeypatch.delenv("WHISPER_DEVICE", raising=False)
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ctranslate2(1))
    monkeypatch.setattr(whisper_engine, "_cuda_runtime_ready", lambda: (True, "就位"))

    assert whisper_engine._detect_device() == "cuda"


def test_env_var_overrides_detection(monkeypatch):
    """✅ WHISPER_DEVICE 仍然是最强优先级（用户显式指定不许被覆盖）。"""
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ctranslate2(1))
    monkeypatch.setattr(whisper_engine, "_cuda_runtime_ready", lambda: (True, "就位"))

    assert whisper_engine._detect_device() == "cpu"


def test_torch_gpu_used_as_fallback_when_ctranslate2_missing(monkeypatch):
    """✅ 万一 ctranslate2 异常，torch 探测仍能兜住（不回归旧能力）。"""
    monkeypatch.delenv("WHISPER_DEVICE", raising=False)

    class _BrokenCT:
        @staticmethod
        def get_cuda_device_count():
            raise RuntimeError("驱动异常")

    monkeypatch.setitem(sys.modules, "ctranslate2", _BrokenCT())

    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "get_device_name", lambda i: "FakeGPU")

    assert whisper_engine._detect_device() == "cuda"


# --------------------------------------------------------------------------
# ② 枚举到 GPU 但运行时不可用 ⇒ 必须安全走 CPU
# --------------------------------------------------------------------------

def test_gpu_without_cuda_runtime_falls_back_to_cpu(monkeypatch):
    """🔴 有 GPU 枚举、但缺 cuBLAS ⇒ 走 CPU，**不能**返回 "cuda"。

    背景：缺 `cublas64_12.dll` 时模型**构造成功**，直到 encoder 阶段才抛
    `RuntimeError: Library cublas64_12.dll is not found` —— 那时整条
    识别管道已经崩了。所以必须在**决定用 CUDA 之前**就确认运行时齐全。
    """
    monkeypatch.delenv("WHISPER_DEVICE", raising=False)
    monkeypatch.setitem(sys.modules, "ctranslate2", _fake_ctranslate2(1))
    monkeypatch.setattr(
        whisper_engine, "_cuda_runtime_ready",
        lambda: (False, "缺少 CUDA 运行时 cublas（已搜 4 个目录）"),
    )

    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    assert whisper_engine._detect_device() == "cpu"


def test_cuda_runtime_ready_reports_missing_libraries(monkeypatch, tmp_path):
    """✅ 运行时缺失时必须给出**可执行**的提示，而不是一句 'cuda unavailable'。"""
    # 造一个空的包目录树 ⇒ 什么都搜不到 ⇒ 必然报缺失
    fake_site = tmp_path / "site-packages"
    (fake_site / "nvidia").mkdir(parents=True)
    ct_pkg = tmp_path / "site-packages" / "ctranslate2"
    ct_pkg.mkdir()

    fake = types.ModuleType("ctranslate2")
    fake.__file__ = str(ct_pkg / "__init__.py")
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)

    ok, detail = whisper_engine._cuda_runtime_ready()
    assert ok is False
    assert "cublas" in detail
    assert "cudnn" in detail
    # 提示里要带搜索目录数，方便排障
    assert "目录" in detail


def test_cuda_runtime_ready_accepts_present_libraries(monkeypatch, tmp_path):
    """✅ cuBLAS 与 cuDNN 都在 ⇒ 判定为就绪（这里只验证"找得到"这一层，
    真正的 DLL 实加载在 Windows 上由 ctypes.WinDLL 完成）。"""
    ct_pkg = tmp_path / "site-packages" / "ctranslate2"
    ct_pkg.mkdir(parents=True)
    (ct_pkg / "cublas64_12.dll").write_bytes(b"fake")
    (ct_pkg / "cudnn64_9.dll").write_bytes(b"fake")

    fake = types.ModuleType("ctranslate2")
    fake.__file__ = str(ct_pkg / "__init__.py")
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)

    # 跳过真实 DLL 加载（测试环境没有真的 CUDA 运行时）
    import ctypes
    monkeypatch.setattr(ctypes, "WinDLL", lambda p: object())

    ok, detail = whisper_engine._cuda_runtime_ready()
    assert ok is True
    assert "就位" in detail


# --------------------------------------------------------------------------
# ③ 构造阶段失败 ⇒ 自动回落 CPU（第二道防线）
# --------------------------------------------------------------------------

class _BoomModel:
    """构造即抛错的模型替身"""

    def __init__(self, *args, **kwargs):
        raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")


def _bare_engine(device: str = "cuda"):
    """构造一个只带必要字段的引擎实例（绕过 __init__，避免加载模型）"""
    engine = whisper_engine.WhisperEngine.__new__(whisper_engine.WhisperEngine)
    engine.device = device
    engine.compute_type = "float16" if device == "cuda" else "int8"
    engine.model_size = "base"
    engine.cpu_threads = 4
    engine._cuda_fallback_done = False
    return engine


def test_model_construction_falls_back_to_cpu(monkeypatch):
    """🔴 探测通过但构造仍失败时，必须自动回落 CPU，不能让软件直接不可用。"""
    engine = _bare_engine("cuda")

    seen = []

    def _construct(model_cls, common, cached):
        seen.append(dict(common))
        if common["device"] == "cuda":
            raise RuntimeError("Library cublas64_12.dll is not found")
        return "cpu-model"

    monkeypatch.setattr(engine, "_construct", _construct)

    result = engine._instantiate_model(_BoomModel, dict(device="cuda", compute_type="float16"), False)

    assert result == "cpu-model"
    assert seen[0]["device"] == "cuda"
    assert seen[1]["device"] == "cpu"
    assert seen[1]["compute_type"] == "int8"
    # 回落之后设备状态必须同步改掉，否则后续逻辑仍会按 cuda 走
    assert engine.device == "cpu"
    assert engine.compute_type == "int8"


def test_fallback_happens_only_once(monkeypatch):
    """✅ 回落只做一次：CPU 也失败时必须把原始异常抛出去，不能无限重试。"""
    engine = _bare_engine("cuda")

    calls = {"n": 0}

    def _construct(model_cls, common, cached):
        calls["n"] += 1
        raise RuntimeError("always fails")

    monkeypatch.setattr(engine, "_construct", _construct)

    with pytest.raises(RuntimeError):
        engine._instantiate_model(_BoomModel, dict(device="cuda", compute_type="float16"), False)

    # 第一次：cuda 失败 → 回落 cpu → cpu 也失败 ⇒ 抛出，calls 应为 2
    assert calls["n"] == 2
    assert engine._cuda_fallback_done is True


def test_cpu_error_is_not_swallowed(monkeypatch):
    """✅ 本来就是 CPU 时，构造失败必须原样抛出（不能被当成 CUDA 问题回落）。"""
    engine = _bare_engine("cpu")

    monkeypatch.setattr(
        engine, "_construct",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("模型文件损坏")),
    )

    with pytest.raises(RuntimeError, match="模型文件损坏"):
        engine._instantiate_model(_BoomModel, dict(device="cpu", compute_type="int8"), False)
