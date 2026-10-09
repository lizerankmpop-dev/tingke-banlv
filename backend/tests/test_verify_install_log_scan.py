"""
`verify_install.py` 日志体检：traceback 必须「按块判终端异常」，不能「按关键词一票否决」

## 为什么需要这个模块

原实现是 `[m for m in LOG_ERROR_MARKERS if m in text]` —— 全文子串匹配。
2026-10-07 18:43 那轮真的判过一次 FAIL（`packaging/verify/report-icon-rehearsal-v4-full.json`
里 `ok=false, critical=true, detail="命中: ['Traceback']"`），而同一场景 22 分钟后重跑
（`report-icon-rehearsal-v4-full-2.json`）却是「干净」—— **偶发**。

两条缺陷都在那次暴露：

  1. **没有区分度**：日志里出现 `Traceback` 就判 critical FAIL，良性 traceback
     （客户端在响应写回前断开 → `ConnectionResetError [WinError 10054]`；
     解释器收尾 → `RuntimeError: Event loop is closed`）与真实崩溃同罪。
  2. **没有证据**：报告只留了「命中 Traceback」四个字。既没留是哪一段、也没留
     终端异常行 —— 想回溯时原日志已被下一轮覆盖，**只能靠猜**。

## 这个模块测什么 / 不测什么

测的是**判定语义**（纯函数 `scan_log_errors`）：

  · 只有终端异常落在 `BENIGN_TRACEBACK_TERMINALS` 里的 traceback 才被豁免；
  · 块内/链式里任何**其它**异常仍然判坏（反向断言，防止「一有白名单就全放过」）；
  · 解析不出终端异常时**保守判坏**（宁可报，不可漏）；
  · 其余 marker（`FATAL` 等）行为不变。

不测的是：真实生产日志长什么样 —— 那段原文已经丢了（见上「缺陷 2」）。
所以修法的另一半是**把扫描结果整体写进报告 data**，让下次出现自带证据；
「报告里确实带了证据」由本模块末尾的 AST 结构守卫负责，否则这段改动
下次被人顺手删掉时，上面的语义测试全都还是绿的。
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "verify_install.py"


def _load_verify_install():
    """按路径加载 scripts/verify_install.py（它不在 sys.path 上）"""
    spec = importlib.util.spec_from_file_location("_verify_install_under_test", SRC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # 先塞进 sys.modules 再 exec：否则 dataclass / 类型注解解析会拿到不完整的命名空间
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


VI = _load_verify_install()

# ---------------- 样本 ----------------
# 全部沿用应用真实的日志格式（`main.py:78 logging.basicConfig`：
#   `%(asctime)s [%(levelname)s] %(name)s: %(message)s`
# 后端 stdout 经 main.cjs 的 onBackendData 逐行加 `[backend] ` 前缀落盘；
# 为可读性这里只保留后端原始行。）

_CLEAN_BODY = [
    "2026-10-07 10:43:00,001 [INFO] __main__: 启动服务 host=127.0.0.1 port=5000 reload=False",
    "INFO:     Started server process [1234]",
    "INFO:     Application startup complete.",
    "2026-10-07 10:43:01,000 [INFO] __main__: 数据库初始化完成",
    "[2026-10-07T10:43:02.000Z] [INFO] [window] 界面加载完成",
]
CLEAN = "\n".join(_CLEAN_BODY)

BENIGN_TERMINAL = "ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。"
BENIGN_BLOCK = [
    "2026-10-07 10:43:30,500 [ERROR] uvicorn.error: Exception in ASGI application",
    "Traceback (most recent call last):",
    '  File "C:\\Python313\\Lib\\asyncio\\tasks.py", line 123, in run',
    "    result = await app(",
    '  File "C:\\Python313\\Lib\\site-packages\\uvicorn\\protocols\\http\\h11_impl.py", line 456, in run_asgi',
    "    await self.send(message)",
    BENIGN_TERMINAL,
]
GENUINE_TERMINAL = "ValueError: 课程配置缺少 language 字段"
GENUINE_BLOCK = [
    "2026-10-07 10:43:40,000 [ERROR] __main__: 处理课程配置失败",
    "Traceback (most recent call last):",
    '  File "C:\\app\\backend\\api\\routes.py", line 10, in handler',
    "    raise ValueError('课程配置缺少 language 字段')",
    GENUINE_TERMINAL,
]

BENIGN_LOG = "\n".join(_CLEAN_BODY + BENIGN_BLOCK + ["[2026-10-07T10:43:31.000Z] [INFO] [window] 界面加载完成"])
GENUINE_LOG = "\n".join(_CLEAN_BODY + GENUINE_BLOCK)
CHAINED_LOG = "\n".join(
    _CLEAN_BODY
    + BENIGN_BLOCK
    + [
        "",
        "During handling of the above exception, another exception occurred:",
        "",
    ]
    + GENUINE_BLOCK
)
TRUNCATED_LOG = "\n".join(_CLEAN_BODY + ["Traceback (most recent call last):"])
FATAL_LOG = "\n".join(_CLEAN_BODY + ["FATAL: 端口被占用，放弃启动"])


# ---------------- 正向 ----------------

def test_clean_log_has_no_findings():
    scan = VI.scan_log_errors(CLEAN)
    assert scan["hitMarkers"] == [], "干净日志不应命中任何 marker"
    assert scan["tracebacks"] == 0
    assert scan["badTerminals"] == []


def test_benign_connection_reset_traceback_is_exempted():
    """
    客户端断开（WinError 10054）是唯一被豁免的一类 —— 它不反映产品缺陷。

    注意 `[ERROR]` 与 marker 里的 `ERROR:` 不相等（后者带冒号）：原来的 marker
    表恰恰漏掉了应用自己格式的 `[ERROR]`，只靠 `Traceback` 兜底 —— 这也是为什么
    不能简单地把 `Traceback` 从 marker 里删掉（删了就真的漏检了）。
    """
    scan = VI.scan_log_errors(BENIGN_LOG)
    assert scan["hitMarkers"] == [], "`[ERROR]` 不应命中 `ERROR:`"
    assert scan["tracebacks"] == 1
    assert scan["terminals"] == [BENIGN_TERMINAL]
    assert scan["benignTerminals"] == [BENIGN_TERMINAL]
    assert scan["badTerminals"] == [], "良性终端异常必须被豁免"


def test_report_evidence_is_json_serializable():
    """`report.step(..., data=scan)` 会 json.dumps 落盘，任何非基础类型都会炸在写报告时"""
    payload = json.dumps(VI.scan_log_errors(BENIGN_LOG), ensure_ascii=False)
    assert "benignTerminals" in payload


# ---------------- 反向（教训 5：每条断言都要有否定形式） ----------------

def test_genuine_traceback_still_fails():
    """白名单只豁免它自己那一类；真实崩溃必须仍然红"""
    scan = VI.scan_log_errors(GENUINE_LOG)
    assert scan["badTerminals"] == [GENUINE_TERMINAL]
    assert scan["benignTerminals"] == []


def test_chained_exception_is_not_laundered_by_the_benign_one():
    """
    链式异常（`During handling of the above exception...`）里只要有一个非良性终端
    就必须判坏 —— 否则「先良性、后崩溃」的日志会被前者的白名单顺手放过。
    """
    scan = VI.scan_log_errors(CHAINED_LOG)
    assert scan["tracebacks"] == 2
    assert scan["benignTerminals"] == [BENIGN_TERMINAL]
    assert scan["badTerminals"] == [GENUINE_TERMINAL], "链式里的真实异常必须留下"


def test_truncated_traceback_is_treated_as_bad():
    """取不到终端异常（日志被截断）→ 保守判坏：宁可让人来核对，也不放过真崩溃"""
    scan = VI.scan_log_errors(TRUNCATED_LOG)
    assert scan["terminals"] == [""]
    assert scan["badTerminals"] == [""], "空终端行不得被当成良性"


def test_traceback_word_without_header_is_bad():
    """有 `Traceback` 字样却解析不出块（格式变了 / 被截断）→ 保守判坏"""
    scan = VI.scan_log_errors("日志被截断，只剩半句 ...Traceback (most rec")
    assert scan["tracebacks"] == 0
    assert scan["badTerminals"], "解析不出块时必须报"


def test_other_markers_unchanged():
    """豁免逻辑只作用于 traceback；`FATAL` 等 marker 的原有行为不能被动到"""
    scan = VI.scan_log_errors(FATAL_LOG)
    assert scan["hitMarkers"] == ["FATAL"]
    assert scan["badTerminals"] == []


def test_describe_detail_distinguishes_benign_from_clean():
    """detail 要能一眼看出「干净」与「干净但豁免了 N 个良性 traceback」"""
    assert VI._describe_log_scan(VI.scan_log_errors(CLEAN), CLEAN) == "干净"
    benign_detail = VI._describe_log_scan(VI.scan_log_errors(BENIGN_LOG), BENIGN_LOG)
    assert "豁免 1 个良性 traceback" in benign_detail, benign_detail
    assert VI._describe_log_scan(VI.scan_log_errors(""), "") == "日志为空，无从判断"


# ---------------- 结构守卫：证据必须随报告落盘 ----------------

def _log_step_calls(src: str) -> list[ast.Call]:
    """找出 `report.step("启动日志无错误字样", ...)` 这个调用节点"""
    tree = ast.parse(src)
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "step"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) \
                and node.args[0].value == "启动日志无错误字样":
            found.append(node)
    return found


def test_log_step_records_evidence():
    """
    命中时必须把扫描结果写进报告 data。

    没有这条守卫的话，把 `data=` 删掉后上面所有语义测试**依然全绿** ——
    而「事后无法追溯」正是这次修它的一半理由。
    """
    src = SRC.read_text(encoding="utf-8")
    calls = _log_step_calls(src)
    assert len(calls) == 1, f"应当恰好有 1 处这样的调用，实为 {len(calls)}"
    kwarg_names = {kw.arg for kw in calls[0].keywords}
    assert "data" in kwarg_names, "该步必须把扫描结果作为 data 落盘"


def test_log_step_uses_block_aware_scan():
    """
    判定必须取自 `scan_log_errors`，不得退回 `marker in text` 的全文子串匹配。

    注意 `scan_log_errors(text)` 是在 `report.step(...)` 的**上一行**调用的，
    所以只能查「该步的 ok 表达式是否引用 scan」，不能要求调用点出现在 step 源码段里。
    """
    src = SRC.read_text(encoding="utf-8")
    assert "scan_log_errors(text)" in src, "必须真的调用 scan_log_errors"

    call = _log_step_calls(src)[0]
    segment = ast.get_source_segment(src, call) or ""
    assert "LOG_ERROR_MARKERS" not in segment, (
        "不得在 step 里直接拿 LOG_ERROR_MARKERS 做全文子串匹配（那正是被修掉的写法）"
    )
    # ok 判据（第二个位置参数）必须引用 scan，否则等于换了个变量名继续看老结果
    ok_expr = ast.get_source_segment(src, call.args[1]) or ""
    assert "scan" in ok_expr, f"ok 判据必须来自 scan，实为: {ok_expr!r}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
