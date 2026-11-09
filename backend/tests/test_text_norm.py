# -*- coding: utf-8 -*-
"""asr.text_norm 的 CJK 空白归一化单测。

背景：Whisper 中文输出会在字/词之间插 ASCII 空格（`那 我们 今天`），
用户实测投诉「每个字之间都有空格」。规则必须保守：
只删两个 CJK 字符之间的空白，拉丁/数字旁的空格原样保留。
"""
from __future__ import annotations

from asr.text_norm import normalize_cjk_spacing


class TestCjkOnly:
    def test_chinese_words_merged(self):
        assert normalize_cjk_spacing("那 我们 今天 又 开始 了") == "那我们今天又开始了"

    def test_single_char_spaces(self):
        assert normalize_cjk_spacing("每 个 字 之 间 都 有 空 格") == "每个字之间都有空格"

    def test_fullwidth_punctuation_neighbors(self):
        # 全角标点属于 CJK 类：标点两侧的空格也该删
        assert normalize_cjk_spacing("你好 ， 世界 。") == "你好，世界。"

    def test_no_space_unchanged(self):
        assert normalize_cjk_spacing("那我们今天") == "那我们今天"


class TestLatinPreserved:
    def test_latin_words_keep_spaces(self):
        assert normalize_cjk_spacing("Now, backtracking is not relevant") == \
            "Now, backtracking is not relevant"

    def test_mixed_cjk_latin(self):
        # 空格一侧是拉丁/数字 ⇒ 保留（「Python 语言」）；
        # 但纯 CJK 词之间的空格仍删（「语言 的 课程」→「语言的课程」）
        assert normalize_cjk_spacing("Python 语言 的 课程") == "Python 语言的课程"
        assert normalize_cjk_spacing("版本 2.0 版") == "版本 2.0 版"

    def test_cjk_then_digit_boundary(self):
        # 「一个 2.0 版」：个|2 之间保留（右侧是数字），0|版 之间保留（左侧是数字）
        assert normalize_cjk_spacing("一个 2.0 版") == "一个 2.0 版"


class TestSafety:
    def test_empty(self):
        assert normalize_cjk_spacing("") == ""

    def test_none(self):
        assert normalize_cjk_spacing(None) is None

    def test_whitespace_only(self):
        assert normalize_cjk_spacing("   ") == "   "

    def test_newline_not_touched(self):
        # 行结构不属于本函数职责：换行原样保留
        assert normalize_cjk_spacing("第一行\n第二行") == "第一行\n第二行"

    def test_tab_between_cjk(self):
        assert normalize_cjk_spacing("那\t我们") == "那我们"
