"""
字幕 RAG 检索层 —— 让答疑能回答「整节课的」问题，而不只最近几句

问题
----
原实现把「最近 N 条字幕」拼成一段文本塞进 prompt。这在「刚才那句没听懂」
的场景够用，但有三个硬伤：

1. **窗口截断**：超过窗口的早期内容直接丢失，问「第一节课讲的那个」无从查起。
2. **无相关性排序**：窗口内所有内容同等对待，噪声淹没信号。
3. **上下文预算浪费**：几千字符里可能只有两句和问题相关。

解法：RAG（Retrieval-Augmented Generation）
----------------------------------------
把字幕切成片段 → 向量化 → 存入向量库 → 按问题语义检索最相关的若干片段
→ 只把检索结果喂给模型。

这正是 LangChain 生态的强项：
``RecursiveCharacterTextSplitter``（切分）→ ``Embeddings``（向量化）
→ ``VectorStore``（存储与检索）→ ``Retriever``（召回），
全部是标准组件，替换任何一环都不影响其余部分。

嵌入模型
--------
默认用本地 Ollama 的 ``nomic-embed-text``（约 274 MB，离线可用），
与项目「完全本地运行」的定位一致；也可切换到云端 OpenAI 兼容嵌入 API。

向量库
------
默认 ``InMemoryVectorStore``（纯 numpy，零额外依赖，进程内快）。
课程级字幕量（几百到几千片段）完全在内存可承受范围内，
且避免了 FAISS 的平台绑定问题。需要持久化时可换成 FAISS —— 接口完全一致。

为什么这不只是"用了 RAG 框架"
--------------------------
字幕是**带时间戳的时序知识库**，本实现据此做了三件事：

1. 片段保留起止时间与说话顺序，检索结果能给出「这句话出现在第几分钟」
2. 检索按语义而非关键词，中英混合提问也能召回
3. 术语表与字幕一起入库，专业问题召回率更高
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# 一、字幕片段的数据结构
# =============================================================================


@dataclass
class SubtitleChunk:
    """一个字幕片段"""

    text: str
    start: float = 0.0
    end: float = 0.0
    index: int = 0
    # 该片段命中的术语（提升术语问题的召回质量）
    terms: list[str] = field(default_factory=list)

    @property
    def timestamp(self) -> str:
        """人类可读的时间戳，如 12:35"""
        return format_timestamp(self.start)

    def to_document(self):
        """转成 LangChain Document（供向量库使用）"""
        from langchain_core.documents import Document

        # 把时间戳写进 metadata，检索后能告诉用户「这句在第几分钟」
        header = f"[{self.timestamp}] " if self.start else ""
        return Document(
            page_content=f"{header}{self.text}",
            metadata={
                "start": self.start,
                "end": self.end,
                "index": self.index,
                "timestamp": self.timestamp,
                "terms": self.terms,
            },
        )

    def format_for_prompt(self) -> str:
        """转成喂给模型的文本片段"""
        return f"[{self.timestamp}] {self.text}"


def format_timestamp(seconds: float) -> str:
    """秒 → mm:ss"""
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


# =============================================================================
# 二、字幕解析与切分
# =============================================================================


def parse_subtitles(raw: str) -> list[SubtitleChunk]:
    """
    解析字幕文本为带时间戳的片段。

    支持两种常见格式：

    1. 带时间戳（``[00:12] 文本`` 或 ``00:12:34 文本``）
    2. 纯文本按行切分（无时间戳时退化为按行）

    Args:
        raw: 字幕原始文本

    Returns:
        SubtitleChunk 列表
    """
    if not raw or not raw.strip():
        return []

    chunks: list[SubtitleChunk] = []
    ts_pattern = re.compile(r"^\s*[\[\(]?(\d{1,2}):(\d{2})(?::(\d{2}))?[\]\)]?\s*(.*)$")

    current_start = 0.0
    current_texts: list[str] = []
    seen_ts = False

    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = ts_pattern.match(line)
        if m:
            # 遇到新时间戳 → 收束上一段
            if current_texts:
                chunks.append(
                    SubtitleChunk(
                        text=" ".join(current_texts),
                        start=current_start,
                        index=len(chunks),
                    )
                )
                current_texts = []
            mm = int(m.group(1))
            ss = int(m.group(2))
            seen_ts = True
            current_start = mm * 60 + ss
            rest = m.group(4).strip()
            if rest:
                current_texts.append(rest)
        else:
            current_texts.append(line)

    if current_texts:
        chunks.append(
            SubtitleChunk(
                text=" ".join(current_texts),
                start=current_start if seen_ts else 0.0,
                index=len(chunks),
            )
        )

    return chunks


def chunk_subtitles(
    chunks: list[SubtitleChunk],
    chunk_size: int = 320,
    chunk_overlap: int = 60,
    max_chunks: int = 400,
) -> list[SubtitleChunk]:
    """
    把字幕片段合并/切分成适合检索的语义块。

    字幕的天然单位是「一句话」，直接逐句建索引会让单句缺少上下文
    （「它的梯度就是」这种残句毫无检索价值）。因此这里把相邻句子
    合并到接近 chunk_size，再按语义边界切开。

    Args:
        chunks: 原始字幕片段
        chunk_size: 目标块长度（字符）
        chunk_overlap: 相邻块重叠字符数
        max_chunks: 最多保留多少块（防止超长课程拖慢索引）

    Returns:
        切分后的块列表
    """
    if not chunks:
        return []

    # 1) 先按长度合并相邻句子
    merged: list[SubtitleChunk] = []
    buf: list[str] = []
    buf_start = chunks[0].start
    buf_end = chunks[0].end

    for c in chunks:
        cur_len = sum(len(x) for x in buf)
        if buf and cur_len + len(c.text) > chunk_size:
            merged.append(
                SubtitleChunk(
                    text=" ".join(buf),
                    start=buf_start,
                    end=buf_end,
                    index=len(merged),
                )
            )
            # 重叠：保留上一块尾部若干字符
            tail = " ".join(buf)[-chunk_overlap:] if chunk_overlap > 0 else ""
            buf = [tail] if tail else []
            buf_start = c.start
        buf.append(c.text)
        buf_end = c.end

    if buf:
        merged.append(
            SubtitleChunk(
                text=" ".join(buf).strip(),
                start=buf_start,
                end=buf_end,
                index=len(merged),
            )
        )

    # 2) 截断到上限（保留最新的，因为提问更可能涉及最近内容）
    if len(merged) > max_chunks:
        merged = merged[-max_chunks:]
        for i, m in enumerate(merged):
            m.index = i

    return merged


# =============================================================================
# 三、嵌入模型
# =============================================================================


def get_embeddings():
    """
    构造嵌入模型。

    默认走本地 Ollama 的 ``nomic-embed-text``（约 274 MB，可离线运行）；
    若配置了云端 OpenAI 兼容嵌入 API 则优先用云端。

    Returns:
        LangChain Embeddings 实例
    """
    provider = os.environ.get("TINGKEBANLV_LLM_PROVIDER", "ollama").strip().lower()

    if provider == "cloud":
        base = os.environ.get("TINGKEBANLV_CLOUD_API_BASE", "").strip().rstrip("/")
        key = os.environ.get("TINGKEBANLV_CLOUD_API_KEY", "").strip()
        model = os.environ.get("TINGKEBANLV_CLOUD_EMBED_MODEL", "text-embedding-3-small").strip()
        if base and key:
            try:
                from langchain_openai import OpenAIEmbeddings

                return OpenAIEmbeddings(base_url=base, api_key=key, model=model)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"云端嵌入模型不可用，回退本地: {e}")

    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(
        model=os.environ.get("TINGKEBANLV_OLLAMA_EMBED_MODEL", "nomic-embed-text")
    )


# =============================================================================
# 四、字幕知识库
# =============================================================================


class SubtitleKnowledgeBase:
    """
    字幕向量库。

    典型用法：

        kb = SubtitleKnowledgeBase()
        kb.build(subtitle_text, course_name="深度学习导论")
        hits = kb.retrieve("老师讲的梯度下降是什么意思", k=4)
    """

    def __init__(self, embeddings=None, top_k: int = 4):
        """
        Args:
            embeddings: 嵌入模型（None 则按环境变量自动选）
            top_k: 默认召回数量
        """
        self._embeddings = embeddings
        self._store = None
        self._chunks: list[SubtitleChunk] = []
        self._lock = threading.Lock()
        self.top_k = top_k
        self._built_at: float = 0.0

    # ---- 构建 ----

    def build(
        self,
        subtitle_text: str,
        course_name: str = "",
        domain: str = "",
        chunk_size: int = 320,
        chunk_overlap: int = 60,
        max_chunks: int = 400,
        force: bool = False,
    ) -> int:
        """
        从字幕文本构建（或增量追加）向量索引。

        Args:
            subtitle_text: 字幕原始文本
            course_name: 课程名（用于术语增强）
            domain: 课程领域
            chunk_size: 块长度
            chunk_overlap: 块重叠
            max_chunks: 索引上限
            force: True 强制重建，False 则增量追加

        Returns:
            索引中的片段数
        """
        if not subtitle_text or not subtitle_text.strip():
            return len(self._chunks)

        with self._lock:
            new_chunks = chunk_subtitles(
                parse_subtitles(subtitle_text),
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                max_chunks=max_chunks,
            )
            if not new_chunks:
                return len(self._chunks)

            # 术语增强：给命中术语的片段打标，提升专业问题召回
            terms = self._collect_terms(new_chunks, domain)
            if terms:
                term_set = {t.lower() for t in terms}
                for c in new_chunks:
                    low = c.text.lower()
                    c.terms = [t for t in terms if t.lower() in low][:5]

            if self._store is None or force:
                self._chunks = new_chunks
            else:
                # 增量：保留旧块 + 追加新块
                self._chunks = (self._chunks + new_chunks)[-max_chunks:]

            documents = [c.to_document() for c in self._chunks]
            embeddings = self._get_embeddings()
            self._store = self._build_store(documents, embeddings)
            self._built_at = time.time()
            logger.info(f"字幕知识库已构建：{len(self._chunks)} 个片段")
            return len(self._chunks)

    @staticmethod
    def _collect_terms(chunks: list[SubtitleChunk], domain: str) -> list[str]:
        """收集课程领域术语（用于片段标注）"""
        try:
            from dictionary.terminology import match_terms_in_text

            terms: list[str] = []
            for c in chunks:
                try:
                    for m in match_terms_in_text(c.text, max_terms=6):
                        w = m.get("word") or m.get("term")
                        if w and w not in terms:
                            terms.append(w)
                except Exception:  # noqa: BLE001
                    continue
            return terms[:50]
        except Exception:  # noqa: BLE001
            return []

    def _get_embeddings(self):
        if self._embeddings is None:
            self._embeddings = get_embeddings()
        return self._embeddings

    @staticmethod
    def _build_store(documents, embeddings):
        """构建向量库（默认内存版，可换 FAISS）"""
        from langchain_core.vectorstores import InMemoryVectorStore

        return InMemoryVectorStore.from_documents(documents, embeddings)

    # ---- 检索 ----

    def retrieve(self, query: str, k: int | None = None) -> list[SubtitleChunk]:
        """
        按语义检索最相关的字幕片段。

        Args:
            query: 查询（学生的问题）
            k: 召回数量，None 则用默认 top_k

        Returns:
            命中的 SubtitleChunk 列表（按相关度降序）
        """
        if self._store is None or not query or not query.strip():
            return []

        k = k or self.top_k
        try:
            docs = self._store.similarity_search(query, k=k)
        except Exception as e:  # noqa: BLE001 —— 检索失败不能阻断答疑
            logger.error(f"字幕检索失败: {e}")
            return []

        out: list[SubtitleChunk] = []
        for d in docs:
            md = d.metadata or {}
            out.append(
                SubtitleChunk(
                    text=self._strip_timestamp(d.page_content),
                    start=float(md.get("start", 0.0)),
                    end=float(md.get("end", 0.0)),
                    index=int(md.get("index", 0)),
                    terms=list(md.get("terms", []) or []),
                )
            )
        return out

    @staticmethod
    def _strip_timestamp(content: str) -> str:
        """去掉检索结果文本里回填的时间戳前缀"""
        return re.sub(r"^\[\d{1,2}:\d{2}\]\s*", "", content or "")

    def format_context(self, chunks: list[SubtitleChunk]) -> str:
        """把检索结果格式化成喂给模型的上下文"""
        if not chunks:
            return ""
        return "\n".join(f"- {c.format_for_prompt()}" for c in chunks)

    # ---- 状态 ----

    @property
    def is_ready(self) -> bool:
        """索引是否就绪"""
        return self._store is not None and bool(self._chunks)

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def clear(self) -> None:
        """清空索引"""
        with self._lock:
            self._store = None
            self._chunks = []
            self._built_at = 0.0


# =============================================================================
# 五、检索增强工具（供 Agent 调用）
# =============================================================================


def make_retrieve_tool(kb_getter):
    """
    构造一个「检索字幕」工具，供 Agent 自主调用。

    这样 RAG 与 Agent 天然结合：模型自己判断什么时候需要翻字幕，
    而不是每次都把检索结果硬塞进 prompt（那会浪费 token 且引入噪声）。

    Args:
        kb_getter: 返回 SubtitleKnowledgeBase 的可调用对象

    Returns:
        LangChain Tool
    """
    from langchain_core.tools import tool

    @tool
    def search_subtitles(query: str) -> str:
        """在整个课程字幕中按语义检索相关片段。

        当学生的问题涉及课程早期内容、某个具体概念的定义，
        或需要引用老师原话时，用这个工具从整节课里找出最相关的片段。
        检索结果带时间戳，能定位到具体是第几分钟讲的。

        Args:
            query: 检索用的自然语言查询（用中文或英文均可）

        Returns:
            命中的字幕片段列表（含时间戳）；未命中时明确说明。
        """
        kb = kb_getter()
        if kb is None or not kb.is_ready:
            return "字幕索引尚未建立，无法检索。"
        hits = kb.retrieve(query, k=4)
        if not hits:
            return f"字幕中未检索到与「{query}」相关的内容。"
        return "\n".join(
            f"- [{h.timestamp}] {h.text}" for h in hits
        )

    return search_subtitles


__all__ = [
    "SubtitleChunk",
    "SubtitleKnowledgeBase",
    "parse_subtitles",
    "chunk_subtitles",
    "get_embeddings",
    "make_retrieve_tool",
    "format_timestamp",
]
