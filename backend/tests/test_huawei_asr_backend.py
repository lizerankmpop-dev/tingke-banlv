"""
华为云 ASR 后端骨架 —— 单元测试

约束（任务书）：
  · 测试不访问网络；
  · 不依赖 faster_whisper / PyAudio；
  · 用 fake transport 驱动收包，不写死任何真实密钥。
"""

import logging
import threading
import time

import numpy as np
import pytest

from asr import backend_factory
from asr.huawei_asr import (
    ENV_AK,
    ENV_AUTH_TOKEN,
    ENV_ENDPOINT,
    ENV_SK,
    HuaweiASRBackend,
    HuaweiASRConfig,
    HuaweiSISWebSocketTransport,
)
from asr.whisper_engine import WordTimestamp

_ENV_KEYS = (
    ENV_AK,
    ENV_SK,
    "HUAWEI_ASR_REGION",
    "HUAWEI_ASR_PROJECT_ID",
    ENV_ENDPOINT,
    ENV_AUTH_TOKEN,
    "TINGKEBANLV_ASR_BACKEND",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """本机可能配过真实华为云变量，测试必须从「全空」开始。"""
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


class FakeTransport:
    """不联网的 transport：记录音频，并在第一次发送时回放事件"""

    def __init__(self, events=None):
        self.events = list(events or [])
        self.audio = []
        self.started = False
        self.closed = False
        self.on_event = None
        self._lock = threading.Lock()

    def start(self, on_event):
        self.started = True
        self.on_event = on_event

    def send_audio(self, data: bytes):
        with self._lock:
            self.audio.append(data)
            pending, self.events = self.events, []
        for event in pending:
            self.on_event(event)

    def close(self):
        self.closed = True


class BrokenTransport(FakeTransport):
    def start(self, on_event):
        raise ConnectionError("fake connection failed")


def _wait_until(predicate, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _fake_config() -> HuaweiASRConfig:
    return HuaweiASRConfig(
        ak="fake-ak",
        sk="fake-sk",
        region="fake-region",
        project_id="fake-project",
        endpoint="wss://example.invalid/asr",
    )


# ---------------------------------------------------------------------------
# 工厂选择
# ---------------------------------------------------------------------------


class _FakeLocal:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeCloud:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def fake_factory(monkeypatch):
    monkeypatch.setattr(backend_factory, "WhisperEngine", _FakeLocal)
    monkeypatch.setattr(backend_factory, "HuaweiASRBackend", _FakeCloud)


def test_default_backend_is_local(fake_factory):
    backend = backend_factory.create_asr_backend({})
    assert isinstance(backend, _FakeLocal)


def test_explicit_local_passes_all_kwargs(fake_factory):
    kwargs = dict(
        model_size="base",
        device="cpu",
        compute_type="int8",
        language="zh",
        on_partial=lambda text, words: None,
        on_progress=lambda stage, pct: None,
        initial_prompt="术语",
        prompt_sections=[("custom", ["术语"])],
        simp_mode="raw",
        on_language_decided=lambda lang: None,
    )
    backend = backend_factory.create_asr_backend({"asrBackend": "local"}, **kwargs)
    assert isinstance(backend, _FakeLocal)
    assert backend.kwargs == kwargs, "本地分支必须原样透传现有参数"


def test_env_var_selects_cloud(fake_factory, monkeypatch):
    monkeypatch.setenv("TINGKEBANLV_ASR_BACKEND", "cloud")
    backend = backend_factory.create_asr_backend({}, transport=FakeTransport())
    assert isinstance(backend, _FakeCloud)


def test_course_config_overrides_env(fake_factory, monkeypatch):
    monkeypatch.setenv("TINGKEBANLV_ASR_BACKEND", "cloud")
    backend = backend_factory.create_asr_backend({"asrBackend": "local"})
    assert isinstance(backend, _FakeLocal)


@pytest.mark.parametrize("name", ["huawei", "HUAWEI", "cloud", "huawei-asr"])
def test_huawei_aliases_select_cloud(fake_factory, name):
    backend = backend_factory.create_asr_backend({"asrBackend": name})
    assert isinstance(backend, _FakeCloud)


def test_unknown_value_falls_back_to_local_with_warning(fake_factory, caplog):
    with caplog.at_level(logging.WARNING):
        backend = backend_factory.create_asr_backend({"asrBackend": "aliyun"})
    assert isinstance(backend, _FakeLocal)
    assert "未知的 ASR 后端配置" in caplog.text


# ---------------------------------------------------------------------------
# 配置校验：缺配置必须明确报错且不发起连接
# ---------------------------------------------------------------------------


def test_missing_all_credentials_reports_clear_error():
    fake = FakeTransport()
    backend = HuaweiASRBackend(config=HuaweiASRConfig(), transport=fake)
    try:
        assert backend.is_loading is False
        assert backend.is_loaded is False
        assert backend.is_running is False
        assert backend.last_error
        for name in ("HUAWEI_ASR_ENDPOINT", "HUAWEI_ASR_AK", "HUAWEI_ASR_SK"):
            assert name in backend.last_error
        assert fake.started is False, "配置缺失时不得调用 transport.start()"
    finally:
        backend.stop()


def test_missing_endpoint_only():
    config = HuaweiASRConfig(ak="fake-ak", sk="fake-sk", endpoint="")
    assert config.configured is False
    assert config.missing == [ENV_ENDPOINT]
    assert ENV_ENDPOINT in config.describe_error()


def test_missing_sk_only():
    config = HuaweiASRConfig(ak="fake-ak", endpoint="wss://example.invalid/asr")
    assert config.configured is False
    assert config.missing == [ENV_SK]


def test_auth_token_replaces_ak_sk():
    config = HuaweiASRConfig(
        endpoint="wss://example.invalid/asr",
        auth_token="fake-token",
    )
    assert config.configured is True
    assert config.missing == []


def test_config_repr_does_not_leak_secrets():
    config = _fake_config()
    text = repr(config)
    assert "fake-ak" not in text
    assert "fake-sk" not in text


def test_default_transport_fails_clearly_without_network():
    backend = HuaweiASRBackend(config=_fake_config())
    try:
        assert _wait_until(lambda: backend.last_error is not None)
        assert backend.is_loaded is False
        assert backend.is_loading is False
        assert "尚未接入" in backend.last_error
        assert "未发起网络连接" in backend.last_error
    finally:
        backend.stop()
    assert backend.is_running is False


def test_connect_failure_is_reported():
    backend = HuaweiASRBackend(config=_fake_config(), transport=BrokenTransport())
    try:
        assert _wait_until(lambda: backend.last_error is not None)
        assert "fake connection failed" in backend.last_error
        assert backend.is_loaded is False
    finally:
        backend.stop()


# ---------------------------------------------------------------------------
# fake transport 端到端：音频分块 → 事件 → 回调 → 收尾
# ---------------------------------------------------------------------------


def test_fake_transport_partial_stats_and_stop():
    events = [
        {
            "type": "result",
            "text": "你好世界",
            "words": [
                {"word": "你好", "start": 0.0, "end": 0.5, "confidence": 0.97},
                {"word": "世界", "start": 0.5, "end": 1.0, "confidence": 0.95},
            ],
        },
        {"type": "partial", "text": "第二段"},
    ]
    fake = FakeTransport(events=events)

    received = []
    partial_seen = threading.Event()

    def on_partial(text, words):
        received.append((text, words))
        partial_seen.set()

    backend = HuaweiASRBackend(
        config=_fake_config(),
        transport=fake,
        on_partial=on_partial,
        initial_prompt="卷积神经网络",
    )
    try:
        assert _wait_until(lambda: backend.is_loaded), backend.last_error
        assert fake.started is True

        backend.add_audio(np.zeros(16000, dtype=np.float32))   # 1 秒
        assert partial_seen.wait(2.0), "fake 事件没有触发 on_partial"
        backend.flush(timeout=2.0)

        assert [text for text, _words in received] == ["你好世界", "第二段"]

        words = received[0][1]
        assert len(words) == 2
        assert all(isinstance(word, WordTimestamp) for word in words)
        assert words[0].word == "你好"
        assert words[0].start == pytest.approx(0.0)
        assert words[0].end == pytest.approx(0.5)
        assert words[0].confidence == pytest.approx(0.97)
        assert received[1][1] == [], "拿不到词级时间戳时必须传空列表"

        assert len(fake.audio) == 5, "1 秒 / 200ms = 5 块"
        assert all(len(chunk) == 3200 * 2 for chunk in fake.audio), "必须是 int16 PCM"
        assert b"".join(fake.audio)  # 有实际音频内容

        stats = backend.get_stats()
        assert stats["backend"] == "huawei-asr"
        assert stats["configured"] is True
        assert stats["missing"] == []
        assert stats["audioSeconds"] == pytest.approx(1.0)
        assert stats["sentAudioSeconds"] == pytest.approx(1.0)
        assert stats["chunksSent"] == 5
        assert stats["droppedChunks"] == 0
        assert stats["events"] == 2
        assert stats["lastError"] is None
        assert stats["termPromptChars"] == len("卷积神经网络")
    finally:
        backend.stop()

    assert fake.closed is True
    assert backend.is_loaded is False
    assert backend.is_loading is False
    assert backend.is_running is False, "stop 后后台线程必须退出"


def test_stop_is_idempotent_and_filters_audio_after_stop():
    fake = FakeTransport()
    backend = HuaweiASRBackend(config=_fake_config(), transport=fake)
    assert _wait_until(lambda: backend.is_loaded)

    backend.stop()
    backend.stop()

    backend.add_audio(np.zeros(16000, dtype=np.float32))
    assert backend.get_stats()["droppedChunks"] == 1
    assert fake.audio == []


def test_pipeline_surfaces_backend_last_error():
    """pipeline 的错误提示优先透出后端的 last_error（云端配置缺失场景）"""
    from asr.pipeline import ASRPipeline

    pipeline = ASRPipeline.__new__(ASRPipeline)
    pipeline._whisper = HuaweiASRBackend(config=HuaweiASRConfig())
    try:
        message = pipeline._backend_unavailable_message()
        assert "HUAWEI_ASR_ENDPOINT" in message
    finally:
        pipeline._whisper.stop()

    class _CloudWithoutError:
        backend_name = "huawei-asr"
        last_error = None

    pipeline._whisper = _CloudWithoutError()
    assert "华为云" in pipeline._backend_unavailable_message()

    class _LocalStub:
        backend_name = "whisper"

    pipeline._whisper = _LocalStub()
    assert pipeline._backend_unavailable_message() == "Whisper 模型加载失败"


def test_sis_transport_requires_config_before_touching_network():
    transport = HuaweiSISWebSocketTransport(HuaweiASRConfig())
    with pytest.raises(Exception) as exc_info:
        transport.start(lambda event: None)
    assert "HUAWEI_ASR_ENDPOINT" in str(exc_info.value)
    transport.close()