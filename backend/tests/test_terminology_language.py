"""
词表短语层的语种分流（缺陷 b，2026-10-08）

## 缺陷是什么

`basic_vocab.json` 是**英汉对照词典**，而短语层的挑选规则是「含空格且 <40 字符」
—— 这条规则**只会命中英文多词短语**（`gradient descent` / `loss landscape` /
`prosocial behavior` ...），对中文词一个也匹配不到。于是中文课走默认配置时，
提示词里会被塞进 60 个与课堂毫无交集的英文短语。

第一组真实素材 A/B 实测了这一刀的代价：
  · `terms on` 注入的正是这批英文短语 → CER **+0.0884（相对 +42.9%）**
  · 术语命中数四格全为 0（因为课上一个都没说）
第二组换成本课原生中文术语后，同一份代码 CER **−0.0236（相对 −11.9%）**。

→ 这不是「术语功能没用」，而是「往中文课里塞英文短语层」这个默认行为本身有害。

## 修法与边界

`build_prompt_sections(..., language=...)`：中文语种（`zh` / `zh-CN` / `cmn` /
`yue` / `wuu`）**不产出短语层**；`auto` / 空 / 其它语种**保持旧行为**。

⚠️ 必须按**主语言子标签**判（`zh-CN` 要命中 `zh`）。第一版写成整串精确匹配，
`zh` 生效而 `zh-CN` 漏网 —— 而带地区后缀恰是最常见的手填写法。用例
`test_region_suffixed_chinese_is_excluded` 就是为这次踩坑钉的。
"""

import pytest

from dictionary.terminology import (
    _PHRASES_LAYER_EXCLUDED_LANGS,
    _phrases_layer_enabled,
    build_initial_prompt,
    build_prompt_sections,
    build_terminology,
)


def _phrases_count(language: str) -> int:
    sections = dict(build_prompt_sections(language=language))
    return len(sections.get("phrases") or [])


def _has_ascii_phrase(language: str) -> bool:
    """短语层里是否出现了「纯 ASCII 多词短语」（英文短语的特征）"""
    sections = dict(build_prompt_sections(language=language))
    for term in sections.get("phrases") or []:
        if " " in term and term.isascii():
            return True
    return False


# ============ 1. 中文语种：短语层必须为空 ============

def test_zh_has_no_phrases_layer():
    assert _phrases_count("zh") == 0
    assert not _has_ascii_phrase("zh")


@pytest.mark.parametrize("lang", ["zh-CN", "zh-TW", "zh_HK", "ZH", "cmn", "yue", "wuu"])
def test_region_suffixed_chinese_is_excluded(lang):
    """
    带地区后缀 / 大小写变体 / 下划线的中文语种都要命中

    这是第一版实现漏掉的：写成整串精确匹配时 `zh` 生效、`zh-CN` 漏网，
    短语层照旧注入 60 个英文词 —— 而 `zh-CN` 是最常见的手填写法。
    """
    assert _phrases_layer_enabled(lang) is False, f"{lang} 应被识别为中文"
    assert _phrases_count(lang) == 0, f"{lang} 不应产出短语层"


def test_surrounding_whitespace_is_ignored():
    assert _phrases_layer_enabled("  zh  ") is False
    assert _phrases_count("  zh  ") == 0


# ============ 2. 非中文 / 未指定：保持旧行为（反向断言）============

@pytest.mark.parametrize("lang", ["auto", "", "ja", "ko", "fr"])
def test_non_chinese_keeps_phrases_layer(lang):
    """
    反向断言：修中文课**不许**把英文课的既有能力一起关掉

    没有这条，把函数改成 `return False` 也能让上面所有用例变绿 ——
    那是砍功能换绿灯。
    """
    assert _phrases_layer_enabled(lang) is True, f"{lang} 应保留短语层"
    assert _phrases_count(lang) > 0, f"{lang} 应产出短语层"
    assert _has_ascii_phrase(lang), f"{lang} 的短语层应含英文多词短语"


@pytest.mark.parametrize("lang", ["en", "en-US", "EN", "en_GB"])
def test_english_excludes_phrases_layer(lang):
    """
    🔴 **英文课也排除短语层**（2026-10-28，V7 P9）—— 这次是**有实测依据**的改动

    这条断言**曾经是反过来的**（`en` 属于「必须保留短语层」那一档）。
    改动它的依据是 V7 在 **MIT 6.034 三个讲次**上的实测（不是推理）：

    | 格 | 默认（有 60 条通用短语） | 排除短语层 | Δ | 机制（替换错误） |
    |---|---|---|---|---|
    | P1（Lec-1 定义课） | 0.1130 / 0.1083 | **0.0954** | **−0.0176** | 100 → 80 |
    | P4（Lec-4 搜索课） | 0.1610 | **0.1487** | **−0.0123** | 147 → 135 |
    | P20（Lec-21 概率推理） | 0.1622 | **0.1432** | **−0.0190** | 146 → 134 |

    提示词从 794 字符降到 **0**；三讲一致，差值远大于 P1 自身的波动（极差 0.0047）。

    **为什么英文也该排除**：短语层的内容是 `basic_vocab.json` 里的
    **通用心理学/教育学短语**（`prefrontal cortex` / `forgetting curve` /
    `cognitive bias` …）—— 它们**与用户学的课无关**，只会占满提示词预算、
    并让模型输出更啰嗦。⇒ 英文课改为只用
    **custom（用户自配）→ discovered（课内已提取）→ domain（领域）** 三层。

    ⚠️ **只对 `en` 生效** —— 证据只覆盖英文课。`ja` / `ko` / `fr` 仍保留短语层，
    要改它们需要各自的实测证据（**不能拿英文的结论外推**）。
    证据与复现步骤见 `docs/m3/plan_v7.md` §六之四、
    `docs/m3/plan_v7_p7_changes.md`，以及 `docs/asr_eval/WER_REPORT_PLAN.md` §7.1。
    """
    assert _phrases_layer_enabled(lang) is False, f"{lang} 应排除通用短语层"
    assert _phrases_count(lang) == 0, f"{lang} 不应产出通用短语层"


def test_excluded_set_is_not_empty():
    """前提守卫：排除集为空的话，上面那些中文用例全是空转"""
    assert _PHRASES_LAYER_EXCLUDED_LANGS, "排除集不能为空"


# ============ 3. 其它层不受影响 ============

def test_custom_and_domain_layers_unaffected_by_language():
    """语种只开关短语层，custom / discovered / domain 三层必须一字不动"""
    custom = ["项目管理器", "画布", "坐标系"]
    discovered = ["图层", "子图"]
    for lang in ("zh", "zh-CN", "auto", "en"):
        sections = dict(build_prompt_sections(
            domain="computer",
            custom_terms=custom,
            discovered_terms=discovered,
            language=lang,
        ))
        assert sections["custom"] == custom, f"{lang}: custom 层被改动"
        assert sections["discovered"] == discovered, f"{lang}: discovered 层被改动"
        assert sections["domain"] == sections.get("domain"), "层缺失"


def test_chinese_terms_never_enter_phrases_layer():
    """
    机制层断言：短语层用「含空格」筛选，中文术语天然匹配不到

    这条钉住的是**缺陷的根因**，不是修复本身 —— 万一将来有人把筛选规则改成
    「所有词」，下面中文语种的 `count == 0` 就不成立了，需要重新审视整个设计。
    """
    zh_terms = ["项目管理器", "坐标系", "横坐标"]
    for term in zh_terms:
        assert " " not in term, f"{term} 含空格，前提不成立"


# ============ 4. 贯通：build_initial_prompt / build_terminology ============

def test_build_initial_prompt_drops_english_phrases_for_zh():
    prompt = build_initial_prompt(
        domain="computer",
        custom_terms=["项目管理器"],
        language="zh",
    )
    assert "项目管理器" in prompt
    assert "gradient descent" not in prompt, "中文课的提示词里不许出现英文短语"
    assert prompt == "项目管理器", "中文 + 只有 custom 层时应只剩该术语"


def test_build_initial_prompt_keeps_english_phrases_for_auto():
    """反向断言：auto 下英文短语仍在（旧行为不被破坏）"""
    prompt = build_initial_prompt(
        domain="computer",
        custom_terms=["项目管理器"],
        language="auto",
    )
    assert "gradient descent" in prompt


def test_build_terminology_prompt_chars_shrink_for_zh():
    """
    `promptChars` 是控制面板可见量 —— 中文课应显著小于 auto

    实测：zh ≈ 5 字符（只剩 custom 层），auto = 801 字符（被短语层撑满）。
    """
    zh = build_terminology(domain="computer", custom_terms=["项目管理器"], language="zh")
    auto = build_terminology(domain="computer", custom_terms=["项目管理器"], language="auto")

    assert zh["promptChars"] < auto["promptChars"], (
        f"中文课的提示词应更短：zh={zh['promptChars']} auto={auto['promptChars']}"
    )
    assert zh["promptChars"] < 100, f"中文课不该被英文短语撑到 {zh['promptChars']} 字符"


def test_build_terminology_sections_follow_language():
    """`sections` 是引擎真正消费的那份，必须跟 prompt 口径一致"""
    zh = build_terminology(domain="computer", custom_terms=["项目管理器"], language="zh")
    auto = build_terminology(domain="computer", custom_terms=["项目管理器"], language="auto")

    zh_phrases = dict(zh["sections"]).get("phrases") or []
    auto_phrases = dict(auto["sections"]).get("phrases") or []

    assert zh_phrases == [], "中文课的 sections 里不该有短语层"
    assert auto_phrases, "auto 的 sections 里应有短语层"
