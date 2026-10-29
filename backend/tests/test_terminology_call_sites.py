"""
`build_terminology` 调用点完整性 + 真实链路透传（缺陷 b 的工程侧收口，2026-10-08）

## 为什么需要这个模块

缺陷 (b) 的第一版修复把 `language` 透传**只加在 `pipeline.py:61` 一处**，
其余 6 处全部遗漏。后果不是「少优化一点」，而是：

  · `pipeline.py:143`（术语热更新）—— 用户改一次术语表，中文课的英文短语层就复原；
  · `api/routes.py:636` / `678` —— 把 41 条英文短语当作「本课会重点识别的词」
    展示给用户；
  · `scripts/eval_asr_quality.py:890` —— A/B harness 测不到这个开关：两个代码锚点
    下发的提示词 **md5 逐字节相同**，整组对照变成空实验，却被当成「修复已验证」
    写进报告（该结论已于复核时撤回）。

而当时「看起来已经修好」的原因正是**测试直接调函数**：
`build_terminology(..., language="zh")` 必然生效，它绕过了真实调用链。
所以本模块**不重复测函数行为**（那是 `test_terminology_language.py` 的事），
只钉两件事：

  1. **源码层**：每个 `build_terminology(` 调用点都显式写了 `language=`（AST 扫描）；
  2. **链路层**：经真实入口（HTTP 端点 / 管道热更新 / 前端 fetch）调用时，
     `language` 确实被透传，且行为正确。

## 语义（改动前先读）

`audit_call_sites(src, filename)` → **返回问题清单**，空列表 = 通过。
这是本仓库结构守卫的统一形态：不抛异常、不返回布尔，便于断言里打印具体问题。

## 为什么用 AST 而不是正则

正则要么被注释里的示例代码骗（假失败），要么要自己剥注释。
`ast.parse` 直接给出 Call 节点与关键字，**没有注释/字符串的歧义**，
也不需要「剥注释只剥块注释不剥 `//`」那类历史包袱。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# 扫描范围：生产代码 + 脚本。**不含 `tests/`** —— 测试刻意验证「不传 language 时的
# 默认行为」是合理的，强制它们也传反而会挡掉反向断言。
SCAN_DIRS = ("backend", "scripts")
EXCLUDE_PARTS = frozenset({"tests", "tingke-banlv", "__pycache__", "node_modules"})


def audit_call_sites(src: str, filename: str = "<src>") -> list[str]:
    """
    扫描一份源码里所有 `build_terminology(...)` 调用，返回**缺 `language=` 的问题清单**

    判据：
    * 传了 `language=` → 通过；
    * 只有位置参数 / 其它关键字 → 报问题；
    * 用 `**kwargs` 转发 → **也报问题**。静态无法确认它到底有没有带上 language，
      而本守卫存在的意义就是「不许有静态看不见的漏网」，宁可要求写明。
      真需要包装函数时，请让包装本身也显式出现 `language=`。

    语法错误同样算问题（这份文件没法被静态核对）。
    """
    problems: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [f"{filename}: 无法解析为 Python（{exc}）—— 该文件的调用点无法核对"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "build_terminology":
            continue

        if any(kw.arg == "language" for kw in node.keywords):
            continue
        if any(kw.arg is None for kw in node.keywords):
            problems.append(
                f"{filename}:{node.lineno} 以 `**` 转发参数，静态无法确认 language —— "
                f"请把 language= 显式写出来"
            )
            continue
        problems.append(
            f"{filename}:{node.lineno} 调用 build_terminology 未传 language= "
            f"（会静默退回 auto，中文课的英文短语层不会被关掉）"
        )
    return problems


def _iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_DIRS:
        for path in (REPO / root).rglob("*.py"):
            if EXCLUDE_PARTS & set(path.relative_to(REPO).parts):
                continue
            files.append(path)
    return sorted(files)


def test_every_call_site_passes_language():
    """生产代码与脚本里，每个 build_terminology 调用点都必须显式传 language"""
    files = _iter_scanned_files()
    assert files, "扫描列表为空 —— 路径假设可能已失效，先修守卫再谈结论"

    problems: list[str] = []
    for path in files:
        rel = path.relative_to(REPO).as_posix()
        problems.extend(audit_call_sites(path.read_text(encoding="utf-8"), rel))

    assert not problems, (
        "以下 build_terminology 调用点漏传 language=（缺陷 (b) 就是漏在这类地方）：\n  "
        + "\n  ".join(problems)
    )


def test_scan_actually_found_call_sites():
    """
    不变式：扫描必须真的扫到了调用点

    否则「零问题」可能只是因为路径写错、一个文件都没读到 —— 那这条守卫是空的。
    """
    hits = 0
    for path in _iter_scanned_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "build_terminology")
                or getattr(node.func, "id", None) == "build_terminology"
            ):
                hits += 1
    assert hits >= 5, f"只扫到 {hits} 个调用点，明显少于实际数量 —— 扫描范围可能失效"


def test_audit_reports_missing_language():
    """变异用例：漏传 language 的调用必须被点名（否则守卫没有分辨力）"""
    src = 'x = build_terminology(domain="", custom_terms=[])\n'
    problems = audit_call_sites(src, "fake.py")
    assert len(problems) == 1, problems
    assert "fake.py:1" in problems[0]
    assert "language" in problems[0]


def test_audit_reports_kwargs_forwarding():
    """变异用例：`**kw` 转发同样要点名（静态看不见的漏网正是本守卫的目标）"""
    src = 'x = build_terminology(**kw)\n'
    problems = audit_call_sites(src, "fake.py")
    assert len(problems) == 1, problems
    assert "**" in problems[0]


def test_audit_passes_correct_call():
    """
    反向用例：写法正确的调用**不能**被误报

    没有这条，「一个都没扫到 → 零问题」也能让上面的用例变绿（教训 5 的同族）。
    """
    src = (
        "x = build_terminology(\n"
        '    domain="d", custom_terms=[], language="zh"\n'
        ")\n"
        "y = terminology.build_terminology(domain=\"\", language=lang)\n"
    )
    assert audit_call_sites(src, "fake.py") == []


def test_audit_ignores_comments_and_strings():
    """注释/字符串里的示例代码不该被算成调用点（AST 的直接收益）"""
    src = (
        "# 示例：build_terminology(domain='', custom_terms=[])\n"
        'DOC = "build_terminology(domain=\'\')"\n'
        'x = build_terminology(language="zh")\n'
    )
    assert audit_call_sites(src, "fake.py") == []


# ============================ 真实调用链 ============================


def _json_stringify_object(src: str, url: str) -> str:
    """
    取出 `url` 之后第一个 `JSON.stringify({...})` 的**对象字面量原文**

    为什么不能只截一段窗口找 `language`：同一段代码里还有
    `useEffect(..., [domain, language, customTerms.join(',')])` 和组件参数表，
    它们都含 `language` —— 窗口式断言在「请求体被删掉 language」时**依然是绿的**。
    实测验证过：变异探针 M4 正是被这条假绿挡下的，所以改成按大括号配对精确截取。
    """
    idx = src.find(url)
    assert idx > 0, f"没找到 {url} 的调用 —— 路径假设已失效"
    window = src[idx:]
    marker = "JSON.stringify("
    start = window.find(marker)
    assert start >= 0, f"{url} 附近没有 JSON.stringify( —— 请求体构造方式已变"
    start += len(marker)

    depth = 0
    for i in range(start, len(window)):
        ch = window[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return window[start : i + 1]
    raise AssertionError(f"{url} 的请求体大括号不配对，无法静态核对")


def test_frontend_preview_sends_language():
    """
    前端调 `/api/terminology/preview` 时必须带上 language

    后端默认 `auto` 是为兼容老客户端，但**产品界面必须自己把语种传对**，
    否则中文课的配置页会显示 41 条英文短语（缺陷 b 的现场）。
    JSX 组件跑不了单测，故按源码结构核对：只认 fetch 里那个 JSON body。
    """
    src = (REPO / "frontend/src/pages/CourseConfig.tsx").read_text(encoding="utf-8")
    body = _json_stringify_object(src, "/api/terminology/preview")

    assert "language" in body, (
        f"preview 请求体里没有 language —— 中文课的英文短语层关不掉。请求体：{body}"
    )


def test_json_stringify_object_extraction_is_precise():
    """
    反向用例：断言必须只认请求体

    构造一份「请求体没有 language、但别处有」的源码 —— 精确截取应当看不到它，
    否则上面那条断言就是假绿（这正是变异探针 M4 抓到的原始写法）。
    """
    fake = (
        "fetch('/api/terminology/preview', {\n"
        "  method: 'POST',\n"
        "  body: JSON.stringify({ domain, customTerms }),\n"
        "})\n"
        "// 依赖里有 language，函数参数里也有 language(language)\n"
        "useEffect(() => {}, [domain, language])\n"
    )
    body = _json_stringify_object(fake, "/api/terminology/preview")
    assert body == "{ domain, customTerms }", body
    assert "language" not in body


class TestRealCallChain:
    """经真实入口调用时，language 必须一路透传到 build_terminology"""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from main import app

        with TestClient(app) as c:
            yield c

    @pytest.fixture
    def spy(self, monkeypatch):
        """把 build_terminology 换成间谍，记录真实收到的关键字参数"""
        import dictionary.terminology as term_mod

        captured: dict = {}
        real = term_mod.build_terminology

        def wrapper(**kwargs):
            captured.clear()
            captured.update(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(term_mod, "build_terminology", wrapper)
        return captured

    def test_preview_endpoint_forwards_language(self, client, spy):
        resp = client.post(
            "/api/terminology/preview",
            json={"domain": "", "customTerms": [], "language": "zh"},
        )
        assert resp.status_code == 200, resp.text
        assert spy.get("language") == "zh", (
            f"preview 端点没把 language 传下去，实际收到 {spy!r}"
        )
        sections = {k: len(v) for k, v in (resp.json().get("sections") or [])}
        assert sections.get("phrases") == 0, f"中文课仍产出短语层：{sections}"

    def test_preview_endpoint_defaults_to_auto(self, client, spy):
        """反向断言：不传 language 时必须保持旧行为 auto（不能把英文课也砍了）"""
        resp = client.post("/api/terminology/preview", json={"domain": "", "customTerms": []})
        assert resp.status_code == 200, resp.text
        assert spy.get("language") == "auto", f"默认值被改掉了：{spy!r}"
        sections = {k: len(v) for k, v in (resp.json().get("sections") or [])}
        assert sections.get("phrases"), "auto 下短语层不该消失（英文课能力被误伤）"

    def test_course_terminology_endpoint_uses_course_language(self, client, spy):
        import uuid

        course = client.post(
            "/api/courses",
            json={
                "name": f"语种透传-{uuid.uuid4().hex[:6]}",
                "domain": "",
                "language": "zh",
            },
        ).json()

        resp = client.get(f"/api/courses/{course['id']}/terminology")
        assert resp.status_code == 200, resp.text
        assert spy.get("language") == "zh", (
            f"课程术语接口没按课程语种调用，实际收到 {spy!r}"
        )
        sections = {k: len(v) for k, v in (resp.json().get("sections") or [])}
        assert sections.get("phrases") == 0, f"中文课程仍产出短语层：{sections}"

    def test_pipeline_hot_update_uses_course_language(self):
        """
        术语热更新必须与首次构建用同一个语种来源

        这里不构造完整管道（要加载模型），直接用 `object.__new__` 拿到实例、
        补上 `update_terminology` 用到的属性 —— 被测的是**这个方法体**，
        所以绕开 `__init__` 不影响结论。
        """
        from asr.pipeline import ASRPipeline

        pipe = object.__new__(ASRPipeline)
        pipe.course_config = {"domain": "", "custom_terms": [], "language": "zh"}
        pipe._discovered_terms = []
        pipe._terminology = {}
        # V5-8 起术语层语种另有来源（探测结果优先），未解析时为 None
        pipe._term_language = None

        captured: dict = {}

        class _FakeWhisper:
            def set_initial_prompt(self, prompt, sections=None):
                captured["prompt"] = prompt
                captured["sections"] = sections

        pipe._whisper = _FakeWhisper()
        pipe.update_terminology()

        sections = {k: len(v) for k, v in (pipe._terminology.get("sections") or [])}
        assert sections.get("phrases") == 0, (
            f"热更新把中文课的英文短语层又装回来了：{sections}"
        )
        assert captured.get("sections") == pipe._terminology.get("sections"), (
            "热更新没把新的分层结构交给引擎"
        )

    def test_pipeline_hot_update_keeps_phrases_for_english(self):
        """反向断言：**热更新路径本身没坏**（术语层仍被正确重建）

        ⚠️ **这条断言的前提在 2026-10-28 变了**（V7 P9）：

        它原本断言「英文课热更新后**短语层仍在**」，用来防止「修中文课时
        顺手把英文课的能力也砍了」（那是砍功能换绿灯）。

        现在**英文课本来就不该有通用短语层** —— 短语层的内容是
        `basic_vocab.json` 里的通用心理学/教育学短语，**与用户学的课无关**；
        V7 在 MIT 6.034 三个讲次上实测排除它之后 WER **三讲一致**下降
        −0.0123 ~ −0.0190（详见 `docs/m3/plan_v7.md` §六之四）。

        ⇒ **本测试的价值不在于「有 phrases 层」，而在于「热更新这条调用链没坏」**
        ⇒ 断言改成验证**热更新确实重建了术语层**，并**顺带钉住新行为**：
        英文课**没有**通用短语层，但 **custom 层在热更新后确实更新了**。
        """
        from asr.pipeline import ASRPipeline

        pipe = object.__new__(ASRPipeline)
        pipe.course_config = {"domain": "", "custom_terms": [], "language": "en"}
        pipe._discovered_terms = []
        pipe._terminology = {}
        pipe._term_language = None

        captured = {}

        class _FakeWhisper:
            def set_initial_prompt(self, prompt, sections=None):
                captured["prompt"] = prompt
                captured["sections"] = sections

        pipe._whisper = _FakeWhisper()
        pipe.update_terminology()

        sections = {k: len(v) for k, v in (pipe._terminology.get("sections") or [])}
        # ① 热更新确实重建了术语层（sections 非空 ⇒ 走了完整构建路径）
        assert sections, f"热更新后术语层没有被重建：{sections}"
        # ② 英文课**不再有**通用短语层（V7 P9 的结论，在真实调用链上钉住）
        assert not sections.get("phrases"), (
            f"英文课不应再有通用短语层（内容与课程无关，实测有害）：{sections}"
        )
        # ③ 🔑 **热更新后 custom 层确实更新了** —— 这是本测试真正要守的东西
        pipe.course_config["custom_terms"] = ["beam search", "minimax"]
        pipe.update_terminology()
        sections2 = {k: len(v) for k, v in (pipe._terminology.get("sections") or [])}
        assert sections2.get("custom") == 2, (
            f"热更新后 custom 层没有更新：{sections2}"
        )
