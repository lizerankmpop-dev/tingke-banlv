"""v4.4.0 AI 全功能可用性守卫。

覆盖本轮修复的三个真实根因：
1. is_available() 只看 ollama.list() 服务活着 → 没拉模型也误报可用
2. 可用性缓存一旦 False 永久不恢复 → 先开软件后启动 Ollama 死锁
3. /api/ai/ask 不接 sessionId → 多轮记忆链路在后端实现却无法被前端使用
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def test_is_available_checks_model_pulled(monkeypatch):
    """服务在跑但没拉目标模型时，必须判定不可用（旧实现误报 available）。"""
    from llm.chains import base

    monkeypatch.setattr(base, "get_provider", lambda: "ollama")
    monkeypatch.setattr(base, "_ollama_model_name", lambda: "qwen2.5:7b")

    class _FakeOllama:
        models = []  # 一个模型都没拉

    fake = type("FakeModule", (), {"list": staticmethod(lambda: _FakeOllama()), "models": []})
    monkeypatch.setitem(sys.modules, "ollama", fake)
    assert base.is_available() is False

    # 拉了无关模型也不算可用
    class _Other:
        models = [type("M", (), {"model": "llama3:8b"})()]
    fake2 = type("FakeModule2", (), {"list": staticmethod(lambda: _Other())})
    monkeypatch.setitem(sys.modules, "ollama", fake2)
    assert base.is_available() is False

    # 拉了目标模型 → 可用
    class _Right:
        models = [type("M", (), {"model": "qwen2.5:7b"})()]
    fake3 = type("FakeModule3", (), {"list": staticmethod(lambda: _Right())})
    monkeypatch.setitem(sys.modules, "ollama", fake3)
    assert base.is_available() is True


def test_availability_ttl_recovers_after_failure(monkeypatch):
    """失败缓存 20s 内保持 False，超时后自动重新探测（Ollama 后启动可自愈）。"""
    from llm.chains import base

    state = {}
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        return calls["n"] >= 2  # 第一次失败，第二次成功

    monkeypatch.setattr(base, "is_available", _flaky)
    assert base.is_available_ttl(state) is False
    assert base.is_available_ttl(state) is False  # TTL 内不再探测
    assert calls["n"] == 1

    state["at"] -= 21  # 模拟 21 秒后
    assert base.is_available_ttl(state) is True
    assert calls["n"] == 2


def test_ask_request_has_session_id():
    """前端能传 sessionId，多轮记忆才接得上。"""
    from api.routes import AskRequest

    req = AskRequest(question="它刚才说的那个词是什么？", courseId="c1", sessionId="qa-123")
    assert req.sessionId == "qa-123"
    # 不传也要能工作（单轮问答兼容）
    assert AskRequest(question="你好", courseId="c1").sessionId == ""


def test_runtime_config_model_name_is_used():
    """翻译提示用运行时配置的模型名，不是写死 env。"""
    source = (BACKEND / "api" / "routes.py").read_text(encoding="utf-8")
    assert "_ollama_model_name" in source
