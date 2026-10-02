"""pytest 公共配置

必须在导入 database.manager 之前设置 TINGKEBANLV_DATA_DIR，
否则 DB_PATH 会指向真实用户目录（~/.tingkebanlv/data），污染本机数据。
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ["TINGKEBANLV_DATA_DIR"] = tempfile.mkdtemp(prefix="tingke-banlv-test-")

# 让测试可以直接导入 backend 目录下的模块（api/ asr/ database/ llm/ ...）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_llm_probe(monkeypatch):
    """
    测试里不发真实的 LLM 探测请求

    1) 单元测试不该依赖外部服务。本机 127.0.0.1:11434 存在一个只接受连接
       但从不响应的端点，真实探测要卡满 2 秒才失败，纯属白等。
    2) 后端可用性探测跑在后台线程里（asyncio.to_thread 无法取消）。
       让它发真实网络请求，就可能出现「测试已结束、线程还在写日志」的情况，
       对 pytest 的捕获流是额外风险。测试应当完全不出网。
    """
    import llm.client

    monkeypatch.setattr(llm.client, "is_available", lambda: False)
    monkeypatch.setattr(llm.client, "get_provider", lambda: "ollama")
