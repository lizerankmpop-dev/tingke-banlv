"""
ASR 运行时配置接口（POST /api/asr/config）—— 接口测试

这个接口只有两件事要做对，而两件都容易错：

1. **没有管道在跑时也要记住**
   用户最自然的操作顺序是「先改设置 → 再点开始听课」。若只把值写进
   active_pipelines 里，没有管道时改动就丢了，表现是「开关点了没反应，
   开始听课后又变回默认」—— 且不报任何错。

2. **缺字段时不能把已有关掉的值冲回默认**
   请求体全字段可选（以后加开关时老客户端不用一次传全）。若把缺字段当成
   「传了默认值」，用户关掉的开关会在下一次其它字段变更时被悄悄打开。

另外验证「开关关掉时新建的管道要套用关闭状态」—— 这条用真实的 ASRPipeline
太重（要加载 VAD + Whisper 模型），改为直接验证模块级默认值与管道方法委托。
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from main import app
from api import routes


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _restore_default():
    """每个用例前后都把全局默认值复位，避免用例之间互相污染"""
    original = routes.term_auto_degrade_default
    yield
    routes.term_auto_degrade_default = original


def test_can_disable_and_enable(client):
    r = client.post("/api/asr/config", json={"termAutoDegrade": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["applied"]["termAutoDegrade"] is False
    # 关键：没有管道在跑也要落进全局默认值
    assert routes.term_auto_degrade_default is False

    r2 = client.post("/api/asr/config", json={"termAutoDegrade": True})
    assert r2.status_code == 200, r2.text
    assert routes.term_auto_degrade_default is True


def test_missing_field_does_not_reset(client):
    """缺字段 = 不动，不是「恢复默认」"""
    client.post("/api/asr/config", json={"termAutoDegrade": False})
    assert routes.term_auto_degrade_default is False

    r = client.post("/api/asr/config", json={})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == {}
    assert routes.term_auto_degrade_default is False, "空请求体把用户关掉的开关冲回开启了"

    # 显式 null 同样视为「不动」（Optional 语义，前端可能整字段传 null）
    r2 = client.post("/api/asr/config", json={"termAutoDegrade": None})
    assert r2.status_code == 200, r2.text
    assert routes.term_auto_degrade_default is False


def test_applies_to_running_pipeline(client):
    """有管道在跑时必须真正下发到引擎（用一个假管道验证，不加载真模型）"""

    class FakePipeline:
        def __init__(self):
            self.received = []

        def set_term_auto_degrade(self, enabled):
            self.received.append(enabled)
            return True

    cid = f"fake-{uuid.uuid4().hex[:6]}"
    fake = FakePipeline()
    routes.active_pipelines[cid] = fake  # type: ignore[assignment]
    try:
        r = client.post("/api/asr/config", json={"termAutoDegrade": False})
        assert r.status_code == 200, r.text
        assert r.json()["activePipelines"] >= 1
        assert fake.received == [False]

        # 值没变也照常下发：管道可能是刚启动的，它没收到过上一次的值
        client.post("/api/asr/config", json={"termAutoDegrade": False})
        assert fake.received == [False, False]
    finally:
        routes.active_pipelines.pop(cid, None)


def test_pipeline_failure_does_not_break_request(client):
    """单个管道抛异常不能让整个请求 500（否则用户点一下开关界面就报错）"""

    class BrokenPipeline:
        def set_term_auto_degrade(self, enabled):
            raise RuntimeError("模拟引擎已停止")

    cid = f"broken-{uuid.uuid4().hex[:6]}"
    routes.active_pipelines[cid] = BrokenPipeline()  # type: ignore[assignment]
    try:
        r = client.post("/api/asr/config", json={"termAutoDegrade": True})
        assert r.status_code == 200, r.text
        assert routes.term_auto_degrade_default is True
    finally:
        routes.active_pipelines.pop(cid, None)


def test_pipeline_delegates_to_engine():
    """ASRPipeline.set_term_auto_degrade 必须把值交给引擎，且失败返回 False"""
    from asr.pipeline import ASRPipeline

    class FakeWhisper:
        def __init__(self):
            self.calls = []

        def set_auto_degrade(self, enabled):
            self.calls.append(enabled)

    class BrokenWhisper:
        def set_auto_degrade(self, enabled):
            raise RuntimeError("引擎崩了")

    # 不走 __init__（那会加载真实模型），只挂上被测方法需要的属性
    pipe = ASRPipeline.__new__(ASRPipeline)
    pipe._whisper = FakeWhisper()
    assert pipe.set_term_auto_degrade(False) is True
    assert pipe._whisper.calls == [False]

    pipe._whisper = BrokenWhisper()
    assert pipe.set_term_auto_degrade(True) is False, "引擎异常必须返回 False 而不是抛出"
