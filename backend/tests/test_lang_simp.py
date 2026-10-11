# -*- coding: utf-8 -*-
"""
V5-1 ~ V5-4：auto 模式下的语种探测与简体强制（缺陷 D1）

## 被测的缺陷

`SIMP_HINT_LANGS` 不含 `"auto"`，而前后端 6 处默认值全是 `"auto"` —— 走默认路径的
中文课程**从不注入**简体引导语，输出满屏繁体（`docs/asr_eval/known_defects.md` D1）。

修复思路（提示词路线，不是 zhconv 后处理）：auto 下先跑一次 encoder-only 的
`detect_language()`，判定为中文就按 `zh` 解码并注入简体引导语 —— 复用模块上方
P0-1 已实测的 34.4% → 0.1% 机制。

## 为什么这些用例必须存在

1. **探测结果驱动两件事**（解码语种 + 引导语注入）。只测其中之一，会出现
   「按 zh 解码但没注引导语」这种**看起来修了、实际还是繁体**的半修状态。
   所以 `test_transcribe_wires_detection_to_both` 直接断言 `transcribe` 收到的实参。
2. **回显清洗器与注入判据必须同源**。auto 现在也可能注入引导语，若清洗器仍按
   「`self.language` 是否中文」构造，就会以为「auto 不会注入」而**静默失效** ——
   静音块上引导语被模型接着念出来、当字幕显示给用户。这一条用静态守卫兜住。
3. **粘滞不能反过来变成缺陷**。会话级锁定是为了避免逐块翻转，但锁早了
   （英文片头）会让整场课都不注引导语，缺陷原样复发。所以要同时测「锁住」与
   「不该锁时不锁」。
"""

from __future__ import annotations

import re
import threading
from collections import deque
from pathlib import Path

import numpy as np
import pytest

from asr.prompt_echo import EchoCleaner
from asr.whisper_engine import (
    LANG_MIN_CONFIDENCE,
    LANG_NON_TARGET_LOCK_AFTER,
    SIMP_HINT,
    SIMP_HINT_LANGS,
    SIMP_MODE_AUTO_SIMPLIFIED,
    SIMP_MODE_RAW,
    WhisperEngine,
    normalize_simp_mode,
)

ENGINE_SRC = Path(__file__).resolve().parent.parent / "asr" / "whisper_engine.py"


class _FakeInfo:
    def __init__(self, language: str, probability: float = 0.99):
        self.language = language
        self.language_probability = probability


class _RecordingModel:
    """记录 `transcribe` 收到的实参，并按脚本回答 `detect_language`。

    `detect_result=None` 表示「不回答」（模拟探测失败/模型不提供该方法）。
    `shape=3` 用来覆盖 faster-whisper 返回 (lang, prob, all_probs) 的版本。
    """

    def __init__(self, detect_result=None, shape: int = 2, segments=None):
        self.detect_result = detect_result
        self.shape = shape
        self.segments = segments or []
        self.detect_calls = 0
        self.transcribe_kwargs: dict | None = None

    def detect_language(self, audio, **kwargs):
        self.detect_calls += 1
        if self.detect_result is None:
            raise RuntimeError("探测不可用")
        lang, prob = self.detect_result
        if self.shape == 3:
            return lang, prob, {lang: prob}
        return lang, prob

    def transcribe(self, audio, **kwargs):
        self.transcribe_kwargs = kwargs
        lang = self.detect_result[0] if self.detect_result else "en"
        return list(self.segments), _FakeInfo(lang)


def _engine(language: str = "auto", model=None, simp_mode: str = SIMP_MODE_AUTO_SIMPLIFIED):
    """不加载模型的引擎实例（`__new__` 绕过 `__init__`，避免起模型加载线程）。

    刻意与生产代码共用 `_hint_possible()` 等实例方法，而不是另抄一份判据 ——
    测试里抄判据等于把「判据漂移」这个要防的东西复制到测试里。
    """
    engine = WhisperEngine.__new__(WhisperEngine)
    engine.model_size = "base"
    engine.device = "cpu"
    engine.compute_type = "int8"
    engine.cpu_threads = 4
    engine.language = language
    engine.simp_mode = normalize_simp_mode(simp_mode)
    engine._model = model
    engine._last_text = None
    engine.initial_prompt = None
    engine._prompt_sections = None
    engine._prompt_degraded = False
    engine._prompt_original = None
    engine._prompt_original_sections = None
    engine._last_budget = None
    engine._auto_degrade = True
    engine._stat_in_segments = 0
    engine._stat_out_segments = 0
    engine._advisory_logged = False
    engine._repetition_events = 0
    engine._degrade_events = 0
    engine._recent_text = ""
    engine._last_verdict = None
    # get_stats() 会读的队列字段
    engine._jobs = deque()
    engine._jobs_cv = threading.Condition()
    engine._busy = False
    engine._merged_jobs = 0
    engine._dropped_jobs = 0
    engine._total_duration = 0.0
    # V5-1 的语种决策字段（与 `__init__` 保持一致）
    engine._lang_decided = False
    engine._lang_decision = None
    engine._lang_confidence = 0.0
    engine._lang_source = "unset"
    engine._lang_detect_events = 0
    engine._lang_low_conf_events = 0
    engine._lang_non_target_streak = 0
    engine._echo = EchoCleaner("")
    engine._echo_logged = False
    return engine


def _audio(seconds: float = 1.0) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def _detect(engine, seconds: float = 1.0):
    return engine._resolve_language(_audio(seconds))


# ============ V5-1：显式语种不受影响（回归保护）============


@pytest.mark.parametrize("lang", ["zh", "zh-CN", "zh-tw"])
def test_explicit_chinese_never_probes_and_keeps_hint(lang):
    """显式指定中文时不该引入任何探测开销 —— 老路径必须原样保持。"""
    model = _RecordingModel(("zh", 0.99))
    engine = _engine(language=lang, model=model)

    decode_lang, conf, source = _detect(engine)

    assert source == "explicit"
    assert decode_lang == lang, "显式语种必须原样透传，不能被改写成探测结果"
    assert model.detect_calls == 0, "显式语种下不该调用探测"
    assert (decode_lang or "").lower() in SIMP_HINT_LANGS
    assert engine._build_prompt(simp=True).endswith(SIMP_HINT)


def test_explicit_english_never_probes_and_has_no_hint():
    model = _RecordingModel(("en", 0.99))
    engine = _engine(language="en", model=model)

    decode_lang, _conf, source = _detect(engine)

    assert source == "explicit"
    assert decode_lang == "en"
    assert model.detect_calls == 0
    # 断言「不含引导语」而不是「prompt 为空」：英文场景仍可能有术语提示词
    assert SIMP_HINT not in (engine._build_prompt(simp=False) or "")


# ============ V5-1：auto + 中文 ⇒ 探测并注入引导语 ============


def test_auto_detects_chinese_and_forces_hint():
    model = _RecordingModel(("zh", 0.97))
    engine = _engine(model=model)

    decode_lang, conf, source = _detect(engine)

    assert (decode_lang, source) == ("zh", "detected")
    assert conf == pytest.approx(0.97)
    assert engine._build_prompt(simp=True).endswith(SIMP_HINT)


def test_auto_simplified_works_when_detector_is_missing():
    """探测不可用时必须**降级**而不是抛错 —— 识别功能比简体强制重要得多。"""
    model = _RecordingModel(detect_result=None)
    engine = _engine(model=model)

    decode_lang, _conf, source = _detect(engine)

    assert (decode_lang, source) == (None, "detect-failed")
    assert engine._lang_decided is False, "探测失败不能锁定会话"


def test_detect_result_shape_tolerance():
    """faster-whisper 的返回值形状改过版（2 元组 / 3 元组），两种都要能用。"""
    engine2 = _engine(model=_RecordingModel(("zh", 0.95), shape=2))
    engine3 = _engine(model=_RecordingModel(("zh", 0.95), shape=3))

    assert _detect(engine2)[0] == "zh"
    assert _detect(engine3)[0] == "zh"


# ============ 端到端接线：探测结论必须驱动 transcribe 的实参 ============


def test_transcribe_wires_detection_to_both():
    """`decode_lang` 与引导语注入必须由**同一次决策**驱动。

    只断言 `_resolve_language()` 的返回值是不够的：真缺陷形态是「决策对了、
    但传给 transcribe 的还是老的 auto / 老的空引导语」—— 于是看起来修了，
    实际输出仍是繁体。所以这里直接断言 `transcribe` 收到的两个实参。
    """
    model = _RecordingModel(("zh", 0.97))
    engine = _engine(model=model)

    engine._transcribe(_audio(), 0.0, 1.0, 1)

    kw = model.transcribe_kwargs
    assert kw is not None
    assert kw["language"] == "zh", "判定中文后必须按 zh 解码"
    assert SIMP_HINT in (kw["initial_prompt"] or ""), "判定中文后必须注入简体引导语"
    assert engine._lang_detect_events == 1


def test_transcribe_english_leaves_prompt_clean():
    model = _RecordingModel(("en", 0.98))
    engine = _engine(model=model)

    engine._transcribe(_audio(), 0.0, 1.0, 1)

    kw = model.transcribe_kwargs
    assert kw["language"] is None, "非中文必须保持 auto 解码，不能强行指定语种"
    assert SIMP_HINT not in (kw["initial_prompt"] or "")


def test_transcribe_low_confidence_keeps_legacy_behaviour():
    model = _RecordingModel(("zh", 0.20))
    engine = _engine(model=model)

    engine._transcribe(_audio(), 0.0, 1.0, 1)

    kw = model.transcribe_kwargs
    assert kw["language"] is None
    assert SIMP_HINT not in (kw["initial_prompt"] or "")


# ============ V5-2：置信阈值与降级 ============


def test_low_confidence_degrades_without_locking():
    model = _RecordingModel(("zh", 0.30))
    engine = _engine(model=model)

    decode_lang, conf, source = _detect(engine)

    assert (decode_lang, source) == (None, "low-confidence")
    assert conf == pytest.approx(0.30)
    assert engine._lang_decided is False, "判不准不该锁定会话"
    assert engine._lang_low_conf_events == 1
    # 不锁定 ⇒ 下一块继续探（静音块判不准不代表整场课判不准）
    _detect(engine)
    assert engine._lang_detect_events == 2


def test_threshold_is_configurable(monkeypatch):
    from asr import whisper_engine as we

    model = _RecordingModel(("zh", 0.70))
    engine = _engine(model=model)

    # 默认 0.6 下 0.70 算通过
    assert _detect(engine)[0] == "zh"

    monkeypatch.setattr(we, "LANG_MIN_CONFIDENCE", 0.90)
    engine2 = _engine(model=model)
    assert _detect(engine2)[1] == pytest.approx(0.70)
    assert engine2._lang_decided is False


def test_detect_can_be_disabled_by_env_switch(monkeypatch):
    from asr import whisper_engine as we

    model = _RecordingModel(("zh", 0.99))
    engine = _engine(model=model)

    monkeypatch.setattr(we, "LANG_DETECT_ENABLED", False)
    decode_lang, _conf, source = _detect(engine)

    assert (decode_lang, source) == (None, "disabled")
    assert model.detect_calls == 0


def test_threshold_default_is_sane():
    assert 0.0 < LANG_MIN_CONFIDENCE <= 1.0


# ============ V5-3 的等价实现：会话级粘滞 ============


def test_chinese_decision_is_sticky_and_probes_once():
    """判定中文后整场课复用该决策：既避免逐块翻转，也让稳态探测开销归零。"""
    model = _RecordingModel(("zh", 0.96))
    engine = _engine(model=model)

    assert _detect(engine)[2] == "detected"
    for _ in range(5):
        decode_lang, _conf, source = _detect(engine)
        assert (decode_lang, source) == ("zh", "sticky")

    assert engine._lang_detect_events == 1, "粘滞后不该再探测（稳态零开销）"


def test_non_target_locks_only_after_streak():
    """英文片头不能一次就把整个会话锁成「不注引导语」——否则 D1 原样复发。"""
    model = _RecordingModel(("en", 0.99))
    engine = _engine(model=model)

    for _ in range(LANG_NON_TARGET_LOCK_AFTER - 1):
        assert _detect(engine)[2] == "non-target-pending"
        assert engine._lang_decided is False

    assert _detect(engine)[2] == "non-target"
    assert engine._lang_decided is True
    assert engine._lang_decision is None


def test_english_intro_then_chinese_still_gets_hint():
    """真实场景：中文课开头的英文报幕之后必须还能转到简体。"""
    engine = _engine(model=_RecordingModel(("en", 0.99)))
    assert _detect(engine)[0] is None            # 第 1 次：待定
    assert engine._lang_decided is False

    engine._model = _RecordingModel(("zh", 0.95))  # 换回中文音频
    assert _detect(engine)[0] == "zh"
    assert engine._build_prompt(simp=True).endswith(SIMP_HINT)


def test_reset_language_decision_reprobes():
    engine = _engine(model=_RecordingModel(("zh", 0.96)))
    _detect(engine)
    assert engine._lang_decided is True

    engine.reset_language_decision()

    assert engine._lang_decided is False
    assert engine._lang_source == "unset"
    assert _detect(engine)[2] == "detected"


def test_model_not_ready_does_not_crash_or_lock():
    engine = _engine(model=None)
    decode_lang, _conf, source = _detect(engine)
    assert (decode_lang, source) == (None, "model-not-ready")
    assert engine._lang_decided is False


# ============ V5-4：简繁开关 ============


def test_normalize_simp_mode_falls_back_on_garbage():
    assert normalize_simp_mode(None) == SIMP_MODE_AUTO_SIMPLIFIED
    assert normalize_simp_mode("") == SIMP_MODE_AUTO_SIMPLIFIED
    assert normalize_simp_mode("AUTO-SIMPLIFIED") == SIMP_MODE_AUTO_SIMPLIFIED
    assert normalize_simp_mode("raw") == SIMP_MODE_RAW
    assert normalize_simp_mode("  Raw ") == SIMP_MODE_RAW
    # 非法值不能抛错：这个值来自数据库历史行与前端表单，让识别起不来不划算
    assert normalize_simp_mode("乱写的值") == SIMP_MODE_AUTO_SIMPLIFIED


def test_raw_mode_disables_detection_entirely():
    model = _RecordingModel(("zh", 0.99))
    engine = _engine(model=model, simp_mode=SIMP_MODE_RAW)

    decode_lang, _conf, source = _detect(engine)

    assert (decode_lang, source) == (None, "disabled")
    assert model.detect_calls == 0, "关掉开关后不该再付探测开销"


def test_set_simp_mode_clears_locked_decision():
    """切换开关必须清掉已锁定的决策，否则「关掉开关」在本会话内不生效。"""
    engine = _engine(model=_RecordingModel(("zh", 0.96)))
    _detect(engine)
    assert engine._lang_decided is True

    engine.set_simp_mode(SIMP_MODE_RAW)

    assert engine.simp_mode == SIMP_MODE_RAW
    assert engine._lang_decided is False
    assert _detect(engine)[2] == "disabled"


def test_stats_expose_language_decision():
    """锁错语种时用户只能看到「还是繁体」—— 必须能从统计里看出原因。"""
    engine = _engine(model=_RecordingModel(("zh", 0.96)))
    _detect(engine)
    stats = engine.get_stats()

    assert stats["simpMode"] == SIMP_MODE_AUTO_SIMPLIFIED
    assert stats["languageDecision"] == "zh"
    assert stats["languageDecided"] is True
    assert stats["languageSource"] == "detected"
    assert stats["langDetectEvents"] == 1


def test_simp_mode_default_agrees_with_db_layer():
    """数据库层刻意不导入 ASR 层，默认值各存一份 —— 靠这条断言防漂移。

    漂移的后果不是崩溃，而是「新课程存进去的默认值不被引擎认作默认」：
    界面上开关显示「强制简体」，实际却没有注入引导语（或反之），
    用户看到的就是「修了还是繁体」，且无从判断是代码问题还是设置问题。
    """
    from database import manager as db_manager

    assert db_manager.SIMP_MODE_DEFAULT == SIMP_MODE_AUTO_SIMPLIFIED
    assert normalize_simp_mode(db_manager.SIMP_MODE_DEFAULT) == SIMP_MODE_AUTO_SIMPLIFIED


# ============ 🔴 耦合守卫：回显清洗器必须与注入判据同源 ============


def test_hint_possible_true_for_auto():
    """auto 现在**可能**注入引导语 ⇒ `_hint_possible()` 必须为真。"""
    assert _engine(language="auto")._hint_possible() is True
    assert _engine(language="zh")._hint_possible() is True
    assert _engine(language="en")._hint_possible() is False
    assert _engine(language="auto", simp_mode=SIMP_MODE_RAW)._hint_possible() is False


def test_engine_enables_echo_cleaner_for_auto():
    """真接线（走 `__init__`）：auto 下清洗器必须处于启用态。

    这是本次最容易漏的一处：auto 注入引导语后，静音块上模型会把引导语接着念出来。
    若清洗器仍按「`self.language` 是否中文」构造，它会被判为不需要、**静默失效**。
    """
    engine = WhisperEngine(language="auto")
    stats = engine.echo_stats()
    assert stats["enabled"] is True, "auto 会注入引导语，清洗器必须启用"
    assert stats["blacklistSize"] > 0


def test_echo_cleaner_construction_is_derived_not_duplicated():
    """静态守卫：`__init__` 里不得再出现按 `self.language` 判断注入的旧写法。

    为什么用静态守卫而不是行为断言：漏改的后果是「清洗器静默失效」，此时行为断言
    仍然全绿（因为没注入引导语的会话本来也不需要清洗），只有源码形状能区分。
    这类守卫本项目已有先例（无障碍 ARIA 静态守卫、控制面板禁词守卫）。
    """
    src = ENGINE_SRC.read_text(encoding="utf-8")
    src = re.sub(r'"""(?:.|\n)*?"""', "", src)     # 剥文档字符串：注释里引用会假绿
    lines = [ln for ln in src.splitlines() if not ln.lstrip().startswith("#")]
    code = "\n".join(lines)

    assert "_hint_possible()" in code, "清洗器判据必须从 `_hint_possible()` 派生"
    assert 'if (self.language or "").lower() in SIMP_HINT_LANGS else ""' not in code, (
        "清洗器又变回了按 self.language 判断的旧写法 —— auto 注入的引导语将漏洗"
    )


# ============ V5-4：老库迁移（保护所有已装用户）============

# 迁移前的 courses 表形状（没有 simp_mode 列）。刻意逐字照抄真实历史形状：
# 漏掉任何一列都会让下面的「老数据必须还在」断言失去意义。
LEGACY_COURSES_DDL = """
CREATE TABLE courses (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, domain TEXT NOT NULL DEFAULT '其他',
    description TEXT DEFAULT '', custom_terms TEXT DEFAULT '[]',
    course_type TEXT NOT NULL DEFAULT 'online', language TEXT DEFAULT 'auto',
    auto_extract_terms INTEGER DEFAULT 1, ollama_model TEXT DEFAULT 'qwen2.5:7b',
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
);
"""


def _legacy_db_with_one_course(db_file):
    import sqlite3
    import time

    conn = sqlite3.connect(db_file)
    conn.executescript(LEGACY_COURSES_DDL)
    now = int(time.time() * 1000)
    conn.execute(
        "INSERT INTO courses (id, name, created_at, updated_at) VALUES ('c1', '旧中文课', ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()
    return now


def test_legacy_db_gets_simp_mode_column(tmp_path, monkeypatch):
    """老库必须能加上 simp_mode 列，且老课程立刻可用。

    这是 V5-4 里唯一会**伤害存量用户**的改动：`CREATE TABLE IF NOT EXISTS` 对已存在的
    courses 表会被整条跳过，漏写 ALTER 的后果是——老用户升级后一读取课程就抛
    `no such column: simp_mode`，后端启动即失败、应用打不开。
    """
    import asyncio
    import sqlite3

    from database import manager as mgr

    db_file = tmp_path / "legacy.db"
    _legacy_db_with_one_course(db_file)

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = mgr.DatabaseManager()
        await db.initialize()
        try:
            course = await db.get_course("c1")
            assert course["name"] == "旧中文课", "迁移不能丢老数据"
            assert course["language"] == "auto", "老字段必须原样保留"
            assert course["simpMode"] == mgr.SIMP_MODE_DEFAULT, "迁移后必须拿到默认开关值"
        finally:
            await db.close()

    asyncio.run(scenario())

    conn = sqlite3.connect(db_file)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(courses)")}
    conn.close()
    assert "simp_mode" in cols
    assert "language" in cols, "迁移只许加列，不许删列"


def test_simp_mode_migration_is_idempotent(tmp_path, monkeypatch):
    """重复 initialize 不应报错（每次启动都会跑一次迁移）"""
    import asyncio

    from database import manager as mgr

    db_file = tmp_path / "again.db"
    _legacy_db_with_one_course(db_file)

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        for _ in range(2):
            db = mgr.DatabaseManager()
            await db.initialize()
            try:
                course = await db.get_course("c1")
                assert course["simpMode"] == mgr.SIMP_MODE_DEFAULT
            finally:
                await db.close()

    asyncio.run(scenario())


def test_db_layer_persists_and_leaves_untouched(tmp_path, monkeypatch):
    """开关值的读写：能存、能改、不传时不动（与 language 的语义保持一致）"""
    import asyncio

    from database import manager as mgr

    db_file = tmp_path / "crud.db"

    async def scenario():
        monkeypatch.setattr(mgr, "DB_PATH", db_file)
        db = mgr.DatabaseManager()
        await db.initialize()
        try:
            created = await db.create_course({"name": "英语课", "simpMode": SIMP_MODE_RAW})
            assert created["simpMode"] == SIMP_MODE_RAW
            cid = created["id"]

            defaulted = await db.create_course({"name": "没给开关的新课"})
            assert defaulted["simpMode"] == mgr.SIMP_MODE_DEFAULT

            updated = await db.update_course(cid, {"simpMode": SIMP_MODE_AUTO_SIMPLIFIED})
            assert updated["simpMode"] == SIMP_MODE_AUTO_SIMPLIFIED

            # 只改名字：开关不应被顺带清掉
            renamed = await db.update_course(cid, {"name": "改个名字"})
            assert renamed["simpMode"] == SIMP_MODE_AUTO_SIMPLIFIED
        finally:
            await db.close()

    asyncio.run(scenario())


# ============ V5-4：热更新链路（改了开关必须立刻生效，不能等重开课）============
#
# 为什么这一段单独列：DB 层的读写有用例覆盖了，但**「改完之后正在跑的那场课有没有
# 立刻改过来」**没人管。这条链路上有两个静默失败点：
#   1. 路由拿到了 `simpMode` 却忘了往 `active_pipelines` 下发 ⇒ 界面显示已关闭，
#      字幕仍满屏繁体，用户只能停课重开——而开关的意义就是当场生效；
#   2. `ASRPipeline.update_simp_mode()` 只改引擎、不改 `course_config` ⇒ 用户改了别的
#      配置触发管道重建，开关**静默回退**（表现为「改了，过一会儿又变回繁体」）。
# 第 2 条的后果尤其坏：它把「代码 bug」伪装成「玄学」。
#
# 这两条都靠 `__new__` + 假管道验证，不加载真模型/VAD。


def test_pipeline_update_simp_mode_updates_course_config():
    """热更新必须**同时**落到 `course_config`，否则管道重建后静默回退。"""
    from asr.pipeline import ASRPipeline

    class FakeWhisper:
        def __init__(self):
            self.calls = []

        def set_simp_mode(self, mode):
            self.calls.append(mode)

    pipe = ASRPipeline.__new__(ASRPipeline)
    pipe.course_config = {"language": "auto", "simpMode": SIMP_MODE_AUTO_SIMPLIFIED}
    pipe._whisper = FakeWhisper()

    pipe.update_simp_mode(SIMP_MODE_RAW)

    assert pipe.course_config["simpMode"] == SIMP_MODE_RAW, (
        "只改引擎内存、不改 course_config：管道重建后开关会静默回退"
    )
    assert pipe._whisper.calls == [SIMP_MODE_RAW]


def test_pipeline_update_simp_mode_without_engine_is_safe():
    """引擎还没起来（模型加载中）时改开关不能崩，且 `course_config` 仍要更新 ——
    新引擎就是从它构造的。"""
    from asr.pipeline import ASRPipeline

    pipe = ASRPipeline.__new__(ASRPipeline)
    pipe.course_config = {"simpMode": SIMP_MODE_AUTO_SIMPLIFIED}
    pipe._whisper = None

    pipe.update_simp_mode(SIMP_MODE_RAW)

    assert pipe.course_config["simpMode"] == SIMP_MODE_RAW


@pytest.fixture(scope="module")
def api_client():
    """真 TestClient：覆盖「路由 → 下发到 active_pipelines」这一段真实链路。

    `db` 侧用 monkeypatch 顶掉（不写用户真实数据库），其余走真实路由代码 ——
    这样「忘了下发」才会真的被测出来，而不是被我自己搭的假链路掩盖。
    """
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as c:
        yield c


def test_update_course_hot_updates_running_pipeline(api_client, monkeypatch):
    """PUT /api/courses/{id} 带 simpMode ⇒ 正在跑的管道必须收到。"""
    from api import routes

    received: list[str] = []

    class FakePipeline:
        def update_simp_mode(self, mode):
            received.append(mode)

    async def fake_update_course(course_id, data):
        return {"id": course_id, **data}

    monkeypatch.setattr(routes.db, "update_course", fake_update_course)

    cid = "fake-simp-hot-1"
    routes.active_pipelines[cid] = FakePipeline()  # type: ignore[assignment]
    try:
        resp = api_client.put(f"/api/courses/{cid}", json={"simpMode": SIMP_MODE_RAW})
        assert resp.status_code == 200, resp.text
        assert received == [SIMP_MODE_RAW], (
            "开关变更必须下发到正在运行的管道，否则用户得停课重开才生效"
        )
    finally:
        routes.active_pipelines.pop(cid, None)


def test_update_course_absent_simp_mode_does_not_hot_update(api_client, monkeypatch):
    """不带 simpMode 的 PUT 视为「不动」—— 不能因为改了个名字就把开关冲回默认。

    与 `test_asr_config_api.py::test_missing_field_does_not_reset` 同一条语义，
    这里是它在本字段上的落地。
    """
    from api import routes

    received: list[str] = []
    captured: dict = {}

    class FakePipeline:
        def update_simp_mode(self, mode):
            received.append(mode)

    async def fake_update_course(course_id, data):
        captured.update(data)
        return {"id": course_id, **data}

    monkeypatch.setattr(routes.db, "update_course", fake_update_course)

    cid = "fake-simp-absent-1"
    routes.active_pipelines[cid] = FakePipeline()  # type: ignore[assignment]
    try:
        resp = api_client.put(f"/api/courses/{cid}", json={"name": "只改名字"})
        assert resp.status_code == 200, resp.text
        assert "simpMode" not in captured, "缺字段被当成了「传默认值」"
        assert received == []
    finally:
        routes.active_pipelines.pop(cid, None)


def test_update_course_normalizes_garbage_simp_mode(api_client, monkeypatch):
    """非法值在路由入口收敛成默认（与引擎侧同一套策略），**不报错**。

    刻意不返回 422：为一个显示开关让「保存课程」整体失败，代价远大于收益；
    用户看到的是开关停在默认档，而不是一个看不懂的校验错误。
    """
    from api import routes

    captured: dict = {}

    async def fake_update_course(course_id, data):
        captured.update(data)
        return {"id": course_id, **data}

    monkeypatch.setattr(routes.db, "update_course", fake_update_course)

    resp = api_client.put("/api/courses/whatever", json={"simpMode": "zh-hant-please"})
    assert resp.status_code == 200, resp.text
    assert captured["simpMode"] == SIMP_MODE_AUTO_SIMPLIFIED


def test_create_course_normalizes_garbage_simp_mode(api_client, monkeypatch):
    """建课入口同样收敛：`CreateCourseRequest` 的默认值走的是**唯一入口**那条线。"""
    from api import routes

    captured: dict = {}

    async def fake_create_course(data):
        captured.update(data)
        return {"id": "new", **data}

    monkeypatch.setattr(routes.db, "create_course", fake_create_course)

    resp = api_client.post(
        "/api/courses", json={"name": "新课", "simpMode": "bogus"}
    )
    assert resp.status_code == 200, resp.text
    assert captured["simpMode"] == SIMP_MODE_AUTO_SIMPLIFIED

    captured.clear()
    resp2 = api_client.post("/api/courses", json={"name": "不提开关的新课"})
    assert resp2.status_code == 200, resp2.text
    assert captured["simpMode"] == SIMP_MODE_AUTO_SIMPLIFIED, (
        "不传时必须落默认（auto-simplified），这是 D1 要修的默认路径"
    )
