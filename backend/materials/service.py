# -*- coding: utf-8 -*-
"""课程资料存储与文本提取（ADR-004 L1）。

定位
----
课程配置页的「课程资料」窗口背后的能力层：

1. 文件落地：`<materials_root>/<course_id>/<安全文件名>`，**只存本机、不上传云端**；
2. 纯文本资料（TXT / MD）可被抽成「术语候选」，一键追加进自定义术语框（V7 术语层入口）；
3. 纯文本资料同时进入答疑上下文（/api/ai/ask 的 context 前缀）。

范围纪律（ADR-004 §2）：PDF / PPT / DOCX 本次**只登记不解析**（indexed=0），
解析进路线图 —— 别在这里偷偷加 PyMuPDF。

为什么不依赖 python-multipart：上传走 raw body（前端 `fetch(url, { body: file })`），
文件名放 `X-Filename` 请求头。少一个依赖 = CI / 打包 / 新机克隆少一个翻车点。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from collections import Counter
from pathlib import Path

logger = logging.getLogger(__name__)

#: 扩展名白名单 —— 不在名单内的一律 400，别让上传窗口变成任意文件仓库
ALLOWED_EXTS = {".txt", ".md", ".pdf", ".ppt", ".pptx", ".docx"}
#: 这些扩展名可以抽文本（术语候选 + 答疑上下文）
TEXT_EXTS = {".txt", ".md"}

#: 单文件上限（20MB）。测试会 monkeypatch 这个值，别写成常量表达式
MAX_SIZE_BYTES = 20 * 1024 * 1024

#: 术语候选输出上限 —— 候选是给人挑的，塞 200 个等于让人自己读原文
MAX_TERM_CANDIDATES = 30

#: 单份资料进入答疑上下文的字符上限（全部资料合计另有限制）
MATERIAL_CONTEXT_CHARS = 4000

# 英文停用词：资料里频率最高的大多是这些，当术语候选纯属噪声
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "are", "was", "were",
    "you", "your", "our", "their", "have", "has", "had", "not", "but", "can",
    "will", "would", "should", "could", "into", "than", "then", "them", "they",
    "when", "what", "which", "where", "who", "how", "why", "also", "such",
    "may", "might", "must", "shall", "does", "did", "been", "being", "its",
    "it's", "one", "two", "all", "any", "each", "more", "most", "other",
    "some", "these", "those", "there", "here", "about", "after", "before",
    "between", "both", "under", "over", "again", "further", "once", "because",
    "while", "during", "above", "below", "only", "very", "just", "using",
    "use", "used", "example", "note", "chapter", "section", "figure", "table",
}

# 文件名消毒：只保留「字母数字中文连字符下划线点空格括号」，其余全换成 _
_SANITIZE_RE = re.compile(r"[^\w.\-()（）\[\] ]+", flags=re.UNICODE)
# 英文词条：字母开头，可含连字符（state-of-the-art）
_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']+")
# 中文连续段（术语候选在其上切 n-gram）—— 先抓整段再切，直接 {2,6} 会把
# 「静脉注射是基础操作」整段吞掉，「静脉注射」永远凑不够频次
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")


def materials_root() -> Path:
    """资料根目录：优先 TINGKEBANLV_HOME（测试隔离靠它），默认 ~/.tingkebanlv/materials"""
    home = os.environ.get("TINGKEBANLV_HOME")
    base = Path(home) if home else Path.home() / ".tingkebanlv"
    return base / "materials"


def sanitize_filename(name: str) -> str:
    """文件名消毒：去路径分隔符、控制字符与危险字符，限长 120。

    返回值不含路径 —— 调用方负责拼 course 目录。空结果回落 ``material``
    （扩展名由调用方校验，这里不动它）。
    """
    # 只取 basename，防 ../ 逃逸
    name = name.replace("\\", "/").split("/")[-1]
    name = _SANITIZE_RE.sub("_", name).strip("._ ")
    name = name[:120]
    return name or "material"


def unique_name(directory: Path, filename: str) -> str:
    """同名文件不覆盖：a.txt / a-2.txt / a-3.txt ...（登记与磁盘必须一一对应）"""
    if not (directory / filename).exists():
        return filename
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for i in range(2, 1000):
        candidate = f"{stem}-{i}{suffix}"
        if not (directory / candidate).exists():
            return candidate
    # 到 1000 份同名资料还没排开 —— 直接 uuid 兜底，绝不覆盖
    return f"{stem}-{uuid.uuid4().hex[:8]}{suffix}"


def save_material(course_id: str, filename: str, data: bytes) -> dict:
    """把上传内容落盘，返回资料记录（不含 id —— id 由 DB 层生成）。

    抛 ``ValueError``（带中文信息，直接可当 API detail）当扩展名/大小不合规。
    """
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"不支持的文件类型 {ext or '(无扩展名)'}，仅支持 {'/'.join(sorted(ALLOWED_EXTS))}")
    if not data:
        raise ValueError("文件内容为空")
    if len(data) > MAX_SIZE_BYTES:
        raise ValueError(f"文件超过 {MAX_SIZE_BYTES // (1024 * 1024)}MB 上限")

    safe = sanitize_filename(filename)
    if Path(safe).suffix.lower() != ext:
        # 消毒把扩展名弄坏了（比如名字就是 ".txt" 全被 strip 掉）—— 补回去
        safe = f"{safe}{ext}"

    directory = materials_root() / course_id
    directory.mkdir(parents=True, exist_ok=True)
    final_name = unique_name(directory, safe)
    target = directory / final_name
    target.write_bytes(data)

    logger.info("资料已保存：%s（%d 字节，课程 %s）", target.name, len(data), course_id)
    return {
        "name": final_name,
        "ext": ext,
        "size": len(data),
        # 纯文本才进答疑上下文/术语候选；其余只登记（ADR-004 L1 纪律）
        "indexed": 1 if ext in TEXT_EXTS else 0,
    }


def delete_material_file(course_id: str, name: str) -> None:
    """删资料文件。行没了文件还在 = 磁盘垃圾；这里失败只记日志不抛（DB 是事实源）。"""
    target = materials_root() / course_id / sanitize_filename(name)
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover —— Windows 文件被占用等
        logger.warning("资料文件删除失败（忽略）：%s：%s", target, exc)


def delete_course_materials(course_id: str) -> None:
    """删课程时清空其资料目录（路由层在 DELETE /courses 时调用）。"""
    shutil.rmtree(materials_root() / course_id, ignore_errors=True)


def read_material_text(course_id: str, name: str) -> str:
    """读纯文本资料。读不到（被删/编码怪异）返回空串，不抛 —— 答疑上下文要尽力而为。"""
    target = materials_root() / course_id / sanitize_filename(name)
    try:
        return target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def extract_term_candidates(text: str, limit: int = MAX_TERM_CANDIDATES) -> list[str]:
    r"""从资料文本抽「术语候选」。

    策略刻意保守 —— 候选是给人一眼扫过再挑的，宁缺勿滥：

    - 英文短语（2~3 词，多词搭配比单词更像术语）与单词：出现 ≥2 次、非停用词；
    - 中文：连续段上切 2~4 字 n-gram 计频（≥2 次），再**子串去重** ——
      「静脉」「注射」是「静脉注射」的子串，长者胜出，短碎片全部丢弃；
    - 排序：三词短语 > 二词短语 > 单词 > 中文，同级按频次降序、再按字典序（可测试）。

    ⚠️ 短语用 token 序列的 bigram/trigram 计数，**不要**换回正则
    ``[a-z]+(?:\s+[a-z]+){1,2}`` —— 贪婪匹配会把 "a b a b" 吃成三词短语
    "a b a"，二词短语永远凑不够频次（实测踩过）。
    """
    if not text:
        return []

    def _ok(word: str) -> bool:
        return len(word) >= 2 and word not in _STOPWORDS

    seq = [m.group(0).lower() for m in _EN_TOKEN_RE.finditer(text)]

    trigrams: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    for i in range(len(seq) - 1):
        a, b = seq[i], seq[i + 1]
        if _ok(a) and _ok(b):
            bigrams[f"{a} {b}"] += 1
    for i in range(len(seq) - 2):
        a, b, c = seq[i], seq[i + 1], seq[i + 2]
        if _ok(a) and _ok(b) and _ok(c):
            trigrams[f"{a} {b} {c}"] += 1

    tokens: Counter[str] = Counter(w for w in seq if _ok(w))

    # 中文 n-gram（2~4 字）计频
    cjk: Counter[str] = Counter()
    for run_match in _CJK_RUN_RE.finditer(text):
        run = run_match.group(0)
        for n in range(2, 5):
            for i in range(len(run) - n + 1):
                cjk[run[i:i + n]] += 1

    # 子串去重：先按频次过滤，再「长者胜」—— 静脉/脉注/注射 ⊂ 静脉注射
    cjk_candidates = sorted(
        (term for term, freq in cjk.items() if freq >= 2),
        key=lambda t: (-len(t), t),
    )
    cjk_kept: list[str] = []
    for term in cjk_candidates:
        if not any(term in kept for kept in cjk_kept):
            cjk_kept.append(term)

    candidates: list[tuple[int, int, str]] = []
    for phrase, freq in trigrams.items():
        if freq >= 2:
            candidates.append((0, -freq, phrase))
    for phrase, freq in bigrams.items():
        if freq >= 2:
            candidates.append((1, -freq, phrase))
    for token, freq in tokens.items():
        if freq >= 2:
            candidates.append((2, -freq, token))
    # 中文按「长度优先」引入（频次在子串去重时已用掉，这里长度即信息量）
    candidates.extend((3, -len(term), term) for term in cjk_kept)

    candidates.sort()
    # 去重保序（短语与单词可能重复出现同一概念）
    seen: set[str] = set()
    result: list[str] = []
    for _, _, term in candidates:
        if term not in seen:
            seen.add(term)
            result.append(term)
        if len(result) >= limit:
            break
    return result


def build_material_context(records: list[dict], course_id: str,
                           total_chars: int = MATERIAL_CONTEXT_CHARS) -> str:
    """把已索引资料拼成答疑上下文前缀（带 [课程资料] 标记，截断到 total_chars）。

    ``records`` 是 DB 行（含 name / indexed）。只读 indexed=1 的行。
    """
    texts: list[str] = []
    remaining = total_chars
    for record in records:
        if not record.get("indexed"):
            continue
        if remaining <= 0:
            break
        content = read_material_text(course_id, record.get("name", ""))
        if not content:
            continue
        content = content[:remaining]
        remaining -= len(content)
        texts.append(content)
    if not texts:
        return ""
    joined = "\n".join(t.strip() for t in texts if t.strip())
    if not joined:
        return ""
    return f"[课程资料摘录]\n{joined[:total_chars]}\n[资料摘录结束]\n"


def now_ms() -> int:
    """毫秒时间戳（与 manager.py 的 created_at 口径一致）"""
    return int(time.time() * 1000)
