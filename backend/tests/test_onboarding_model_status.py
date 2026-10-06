"""
首启向导第 3 步的模型状态接口（GET /api/asr/model-status）—— 接口测试

这个接口只有两个契约，而两个都容易被后来的改动破坏：

1. **判据必须与引擎一致**
   引擎用 `model_bootstrap.check_installed` 判断模型能不能用。向导如果自己写一套
   （比如直接找 model.bin 文件），就会出现「引导说已就绪、点开始听课却报模型缺失」
   —— 两处判断各自看都像是对的，只有放在一起才矛盾。
   因此这里用 spy 断言「它确实调了 check_installed」，而不只是「返回了 ok」。
   只断言返回值的话，把实现换成 `return {"ok": True}` 也能通过测试。

2. **必须是浅校验（deep=False）**
   这个接口在启动路径上被向导轮询。deep=True 会逐个文件算 sha256（秒级），
   界面表现就是「正在准备」卡住不动。

另外钉住**响应字段集合**：多返回一个 `problems` 数组，迟早会有人按里面的
中文字符串做分支 —— 而 `action` 可能是 copied / reused / verified（都是成功），
项目历史上正是按 action 判断才把「播种后校验仍不通」误报成通过。
"""

from fastapi.testclient import TestClient

import pytest

# 副本仓库的 backend/ 只同步了 api/ 与 tests/（没有 asr/）—— 缺引擎模块时
# 整组跳过（与该副本里其它依赖引擎的用例同一待遇），主仓库照常全跑。
pytest.importorskip("asr")

try:
    # 主仓库：完整后端，直接用真实 app（校验的是它真实的路由挂载）
    from main import app
except ModuleNotFoundError:  # 精简副本：backend/ 下没有 main.py
    from fastapi import FastAPI

    from api.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")


def test_contract_fields_are_minimal():
    """响应只允许 {ok, model}：多一个字段就意味着多一条会被依赖的隐式契约"""
    with TestClient(app) as client:
        r = client.get("/api/asr/model-status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"ok", "model"}, f"字段集合变了：{sorted(body.keys())}"
    assert isinstance(body["ok"], bool), "ok 必须是布尔，不能是字符串或数字"
    assert isinstance(body["model"], str) and body["model"], "model 必须是非空字符串"


def test_reuses_engine_judgement(monkeypatch):
    """
    必须把判断委托给 check_installed，而不是自己实现

    用 spy 同时钉住两件事：调用发生、且 deep=False。
    """
    import asr.model_bootstrap as mb

    calls: list[tuple] = []

    def spy(size="base", deep=False):
        calls.append((size, deep))
        return {"ok": True, "reason": "ok", "problems": []}

    monkeypatch.setattr(mb, "check_installed", spy)

    with TestClient(app) as client:
        r = client.get("/api/asr/model-status")

    assert r.status_code == 200, r.text
    assert calls, "接口没有调用 check_installed —— 说明它自己另写了一套判据"
    assert calls[0][1] is False, "必须用 deep=False（浅校验），否则启动路径上会卡秒级"


def test_unavailable_model_reports_false(monkeypatch):
    """模型不可用时必须如实返回 ok=False（向导据此显示可操作的错误分支）"""
    import asr.model_bootstrap as mb

    monkeypatch.setattr(
        mb,
        "check_installed",
        lambda size="base", deep=False: {"ok": False, "reason": "missing", "problems": ["未找到模型文件"]},
    )

    with TestClient(app) as client:
        r = client.get("/api/asr/model-status")

    assert r.status_code == 200, r.text
    assert r.json()["ok"] is False


def test_failure_detail_is_not_leaked(monkeypatch):
    """
    失败原因不允许出现在响应里

    理由不是保密，而是**防止有人按错误文本分支**：那些文本是给排障的人看的，
    随时可能改写（本次改注释/文案就改过好几处），一旦被代码依赖就会静默失效。
    """
    import asr.model_bootstrap as mb

    monkeypatch.setattr(
        mb,
        "check_installed",
        lambda size="base", deep=False: {
            "ok": False,
            "reason": "verify-failed",
            "problems": ["model.bin 校验和不通", "缺少 tokenizer.json"],
        },
    )

    with TestClient(app) as client:
        r = client.get("/api/asr/model-status")

    text = r.text
    for leaked in ("校验和", "tokenizer", "verify-failed", "problems"):
        assert leaked not in text, f"响应里泄露了内部诊断细节：{leaked}"
