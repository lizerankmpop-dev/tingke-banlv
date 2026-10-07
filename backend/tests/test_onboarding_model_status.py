"""
首启向导第 3 步的模型状态接口（GET /api/asr/model-status）—— 接口测试

这个接口的每条契约都对应一次真实踩坑：

1. **判据必须与引擎一致**
   引擎用 `model_bootstrap.check_installed` 判断模型能不能用。向导如果自己写一套
   （比如直接找 model.bin 文件），就会出现「引导说已就绪、点开始听课却报模型缺失」
   —— 两处判断各自看都像是对的，只有放在一起才矛盾。
   因此这里用 spy 断言「它确实调了 check_installed」，而不只是「返回了 ok」。
   只断言返回值的话，把实现换成 `return {"ok": True}` 也能通过测试。

2. **必须是浅校验（deep=False）**
   这个接口在启动路径上被向导轮询。deep=True 会逐个文件算 sha256（秒级），
   界面表现就是「正在准备」卡住不动。

3. **字段集合固定：ok / model / seed_exists / seeding / error**
   前端按位看这四个位（外加 model 标识）。多返回一个诊断字段，迟早会有人按里面的
   中文字符串做分支 —— 而 `action` 可能是 copied / reused / verified（都是成功），
   项目历史上正是按 action 判断才把「播种后校验仍不通」误报成通过。

4. **不泄露诊断文本**
   `error` 只允许出现**稳定枚举**（no-seed / io-error / integrity / unknown），
   中文细节（problems、校验和、tokenizer…）只进后端日志。理由不是保密，
   而是那些文本随文案改造而变，一旦被依赖就会静默失效。

另外这个接口兼作**幂等播种触发点**：探测到模型不可用时会启动后台播种
（`start_background_seed`），让「从旧版本升上来、没跑过 lifespan 播种」的用户也能自愈。
"""

import pytest
from fastapi.testclient import TestClient

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


ALLOWED_ERROR_KINDS = (None, "no-seed", "io-error", "integrity", "unknown")


@pytest.fixture(autouse=True)
def _no_real_seed_thread(monkeypatch):
    """
    默认不让测试真的启动后台播种线程

    接口在 ok=False 时会调 `start_background_seed`，而它默认的实现会真去读
    142MB 种子并拷贝 —— 测试里纯属白等（还会污染 TINGKEBANLV_HOME）。
    需要验证「确实触发了」的用例会自己覆盖这个 spy。
    """
    import asr.model_bootstrap as mb

    calls: list = []
    monkeypatch.setattr(mb, "start_background_seed", lambda *a, **k: (calls.append(a), False)[1])
    monkeypatch.setattr(mb, "background_seed_state", lambda: {
        "running": False, "done": False, "ok": None, "error": None, "at": None,
    })
    monkeypatch.setattr(mb, "seed_exists", lambda *a, **k: True)
    return calls


def test_contract_fields_are_fixed():
    """响应字段集合固定：ok / model / seed_exists / seeding / error"""
    with TestClient(app) as client:
        r = client.get("/api/asr/model-status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"ok", "model", "seed_exists", "seeding", "error"}, (
        f"字段集合变了：{sorted(body.keys())}"
    )
    assert isinstance(body["ok"], bool), "ok 必须是布尔，不能是字符串或数字"
    assert isinstance(body["seed_exists"], bool), "seed_exists 必须是布尔"
    assert isinstance(body["seeding"], bool), "seeding 必须是布尔"
    assert isinstance(body["model"], str) and body["model"], "model 必须是非空字符串"
    assert body["error"] in ALLOWED_ERROR_KINDS, f"error 只允许稳定枚举，得到 {body['error']!r}"


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


def test_unavailable_triggers_background_seed(monkeypatch, _no_real_seed_thread):
    """
    探测到不可用时必须**触发**后台播种

    这是缺陷修复的核心：旧实现只如实报告「不可用」，谁也不会去播种，
    于是首启向导第 3 步永远失败、点「重试」也没用。
    """
    import asr.model_bootstrap as mb

    monkeypatch.setattr(
        mb,
        "check_installed",
        lambda size="base", deep=False: {"ok": False, "reason": "missing", "problems": ["未找到模型文件"]},
    )

    with TestClient(app) as client:
        client.get("/api/asr/model-status")

    assert _no_real_seed_thread, "模型不可用时没有触发后台播种 —— 用户会卡在第 3 步"


def test_available_model_does_not_trigger_seed(monkeypatch):
    """模型已就绪时不该再触发播种（幂等的上半段：省掉一次无谓的线程）"""
    import asr.model_bootstrap as mb

    monkeypatch.setattr(mb, "check_installed", lambda size="base", deep=False: {"ok": True, "reason": "ok", "problems": []})
    triggered: list = []
    monkeypatch.setattr(mb, "start_background_seed", lambda *a, **k: triggered.append(a) or False)

    with TestClient(app) as client:
        client.get("/api/asr/model-status")

    assert not triggered, "模型已就绪却仍触发了播种"


def test_seeding_flag_follows_background_state(monkeypatch):
    """播种进行中：ok=False 且 seeding=True —— 前端据此显示「请稍候」而不是「失败」"""
    import asr.model_bootstrap as mb

    monkeypatch.setattr(
        mb, "check_installed",
        lambda size="base", deep=False: {"ok": False, "reason": "missing", "problems": ["未找到模型文件"]},
    )
    monkeypatch.setattr(mb, "start_background_seed", lambda *a, **k: True)
    monkeypatch.setattr(mb, "background_seed_state", lambda: {
        "running": True, "done": False, "ok": None, "error": None, "at": None,
    })

    with TestClient(app) as client:
        body = client.get("/api/asr/model-status").json()

    assert body["ok"] is False
    assert body["seeding"] is True, "播种进行中却没报 seeding —— 界面会误显示「准备失败」"
    assert body["error"] is None, "还在播种就不该报错"


def test_seed_exists_reflects_seed_root(monkeypatch):
    """seed_exists 为 False 时前端可直接走失败分支（再等也不会好）"""
    import asr.model_bootstrap as mb

    monkeypatch.setattr(
        mb, "check_installed",
        lambda size="base", deep=False: {"ok": False, "reason": "missing", "problems": ["未找到模型文件"]},
    )
    monkeypatch.setattr(mb, "seed_exists", lambda *a, **k: False)

    with TestClient(app) as client:
        body = client.get("/api/asr/model-status").json()

    assert body["seed_exists"] is False


def test_failure_detail_is_not_leaked(monkeypatch):
    """
    失败原因的中文细节不允许出现在响应里

    只允许出现稳定枚举（如 integrity）。理由见模块 docstring 第 4 条。
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
    monkeypatch.setattr(mb, "background_seed_state", lambda: {
        "running": False, "done": True, "ok": False, "error": "integrity", "at": 1.0,
    })

    with TestClient(app) as client:
        r = client.get("/api/asr/model-status")

    text = r.text
    for leaked in ("校验和", "tokenizer", "verify-failed", "problems"):
        assert leaked not in text, f"响应里泄露了内部诊断细节：{leaked}"
    assert r.json()["error"] == "integrity", "失败时应给出稳定分类，便于排障与统计"
