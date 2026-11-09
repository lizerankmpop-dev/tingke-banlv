# -*- coding: utf-8 -*-
"""字幕文本的 CJK 空白归一化。

为什么需要：Whisper 的中文输出习惯在「字/词」之间插 ASCII 空格
（`那 我们 今天`），照搬到字幕里就成了用户投诉的「每个字之间都有空格」。
归一化规则刻意保守：**只删两个 CJK 字符之间的空白**，
拉丁字母/数字旁边的空格原样保留（`Python 语言`、`版本 2.0` 不动）。

应用位置：whisper_engine 收到 segment 后的第一时间（回显清洗之前），
保证下游所有消费方（字幕展示、导出、提示词上下文、重复检测）看到的是同一份归一化文本。
"""
from __future__ import annotations

import re

# CJK 统一表意文字 + 扩展A + CJK 标点 + 全角符号
_CJK_CLASS = r"\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef"
# 两个 CJK 字符之间的 ASCII 空白（不含换行——多行字幕的行结构不属于本函数职责）
_CJK_GAP = re.compile(rf"(?<=[{_CJK_CLASS}])[ \t]+(?=[{_CJK_CLASS}])")


def normalize_cjk_spacing(text: str | None) -> str | None:
    """去掉两个 CJK 字符之间的 ASCII 空白，其余空白原样保留。

    >>> normalize_cjk_spacing("那 我们 今天 又 开始 了")
    '那我们今天又开始了'
    >>> normalize_cjk_spacing("Python 语言 的 课程")
    'Python 语言的课程'
    """
    if not text:
        return text
    return _CJK_GAP.sub("", text)
