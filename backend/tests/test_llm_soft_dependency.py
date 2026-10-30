# -*- coding: utf-8 -*-
"""🔴 防回归：**LLM 必须是软依赖**（2026-10-30）

问题
----
`backend/api/routes.py` 原本在**模块顶层**硬 import + **模块级实例化**：

    from llm.explainer import WordExplainer
    word_explainer = WordExplainer()

⇒ 只要 `langchain_core` 缺失（没装 / 装坏 / 升级降级失败），
**整个后端起不来** —— 而用户的核心需求「实时字幕 + 点词」**完全不依赖 LLM**。

实际发生：2026-10-30 本机 venv 里 `langchain` 整个消失 ⇒
后端反复重启 13 次全失败 ⇒ **整个软件打不开**，而 LLM 只是「AI 讲解」这个附加功能。

修法
----
- 顶层不再 import LLM ⇒ 改成**按需构造**（`_get_llm`）
- 不可用时 `_llm_call` 抛 **503 + 明确原因**（而不是 AttributeError → 500）
- `main.py` 的 LLM 状态探测：import 放进 try（原来在 try 外面，
  异常会冲出协程、让 `_llm_status_loop` 直接死掉）

本测试守住的东西
----------------
① **不装 langchain 时 `api.routes` 仍能 import**（用假模块屏蔽 langchain 验证）
② 缺依赖时 LLM 端点返回 **503**（带可操作提示），不是 500
"""
from __future__ import annotations

import builtins
import importlib
import sys

import pytest


@pytest.fixture()
def without_langchain(monkeypatch):
    """把 `langchain*` 的 import 变成必失败，模拟「依赖没装/装坏」"""
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "langchain" or name.startswith("langchain_"):
            raise ModuleNotFoundError(f"No module named '{name}'（测试模拟）")
        return real_import(name, *a, **kw)

    # ⚠️ 必须把**已缓存**的模块也清掉 ——
    #    否则 `from llm.explainer import WordExplainer` 直接命中 sys.modules 缓存，
    #    根本不会触发 import ⇒ 测不出「缺依赖」的真实路径。
    for mod in list(sys.modules):
        if (mod == "langchain" or mod.startswith("langchain_")
                or mod == "llm" or mod.startswith("llm.")):
            monkeypatch.delitem(sys.modules, mod, raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    yield


def test_routes_importable_without_langchain(without_langchain):
    """🔴 核心：不装 langchain 时，路由模块**仍能 import**"""
    for mod in ("api.routes", "main"):
        sys.modules.pop(mod, None)
    mod = importlib.import_module("api.routes")
    assert hasattr(mod, "_get_llm")
    assert hasattr(mod, "_llm_call")


def test_llm_call_raises_503_when_unavailable(without_langchain):
    """缺依赖时 LLM 调用应抛 503（可操作的提示），不是 AttributeError/500"""
    for mod in ("api.routes",):
        sys.modules.pop(mod, None)
    routes = importlib.import_module("api.routes")

    with pytest.raises(Exception) as ei:
        routes._llm_call("word_explainer", "explain")
    exc = ei.value
    assert getattr(exc, "status_code", None) == 503, f"期望 503，实际 {exc!r}"
    # 提示里要说清「主功能不受影响」
    assert "不受影响" in str(exc.detail)


def test_get_llm_returns_none_and_records_reason(without_langchain):
    """构造失败应记下原因（便于排障），且返回 None 而不是抛异常"""
    for mod in ("api.routes",):
        sys.modules.pop(mod, None)
    routes = importlib.import_module("api.routes")
    routes._llm_instances.clear()
    routes._LLM_IMPORT_ERRORS.clear()

    obj = routes._get_llm("word_explainer")
    assert obj is None
    assert "word_explainer" in routes._LLM_IMPORT_ERRORS
