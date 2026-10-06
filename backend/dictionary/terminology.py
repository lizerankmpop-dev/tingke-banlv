"""
术语热词库 & Whisper initial_prompt 构建器

解决的问题：
    Whisper 对专业术语识别很差 —— "convolution" → "convalution"，
    "eigenvalue" → "eigen value"，学生看到的字幕就是错的，点词也查不到。

解决方式：
    给 Whisper 传 initial_prompt（一段先验文本），模型会把它当作"前文语境"，
    从而大幅提升这些词被正确拼写的概率。

热词来源（三层，按优先级合并）：
    1. 用户自定义术语（course.custom_terms）—— 最高优先级，永远排在前
    2. 课程领域术语（DOMAIN_TERMS[domain]）
    3. 课程内已提取词汇（vocabulary 表中 auto_generated 的词）

术语库构建：
    build_terminology() 把三层 + basic_vocab.json 交叉，标注每个词是否已有释义，
    供上层决定用词典还是走 AI 讲解。

环境变量：
    TINGKEBANLV_TERM_PROMPT_MAX   术语层的**字符**粗筛上限（默认 800），
                                  不是最终约束 —— 见下方 DEFAULT_PROMPT_MAX_CHARS
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VOCAB_PATH = Path(__file__).parent / "basic_vocab.json"

# initial_prompt 的**字符**上限 —— 只是粗筛，不是最终约束
#
# ⚠️ P0-3 起，真正决定「哪些术语进模型」的是 **token 预算**
#    （`WhisperEngine.PROMPT_TOKEN_BUDGET`，默认 220 token，见 asr/prompt_budget.py）。
#    本字符上限只在术语层先砍一刀，省后续的 token 计数开销。
#
# 为什么它当不了约束：中文的 token 密度约 1.8 token/字（byte-level BPE 对
# 常用字表外的专业词会回退到 UTF-8 字节序列），800 字符 ≈ 1480 token，
# 远超 220 的预算 —— 也就是说**对中文课程这个上限永远不会生效**。
# 调它观察不到任何效果，不要在这里调参，改预算请用
# TINGKEBANLV_TERM_PROMPT_TOKENS / TINGKEBANLV_TERM_DEGRADE_TOKENS。
#
# 保留它的原因：英文场景下 800 字符 ≈ 208 token，与预算同量级，
# 能先挡掉明显超长的列表。
DEFAULT_PROMPT_MAX_CHARS = 800


def get_prompt_max_chars() -> int:
    try:
        return int(os.environ.get("TINGKEBANLV_TERM_PROMPT_MAX", DEFAULT_PROMPT_MAX_CHARS))
    except ValueError:
        return DEFAULT_PROMPT_MAX_CHARS


# ============ 领域术语表 ============
# 每个领域的术语按"识别难度 × 出现频率"排序：
# 越容易被 Whisper 听错、越常在课堂出现的词，越靠前 —— 因为 `prompt_budget.assemble`
# 是按列表顺序贪心填预算的，靠前的词**优先拿到** token 配额，也就优先被塞进 prompt。
#
# 注意别把「优先」和「位置」搞混：P0-3 的拼接是低优先级段在前、高优先级段贴尾，
# 所以本列表里最靠前的词最终会出现在**领域段的开头**（即字符串的靠前位置）。
# 位置靠前不危险 —— 我们自己把总量压到 220 token、低于库保留的 223，
# 库不会再截；真正决定取舍的是 assemble 的填充顺序，而那个顺序是「靠前优先」。

DOMAIN_TERMS: dict[str, list[str]] = {
    "计算机": [
        "algorithm", "asynchronous", "convolution", "convolutional", "cache", "polymorphism",
        "inheritance", "recursion", "iteration", "pointer", "compiler", "interpreter",
        "latency", "throughput", "concurrency", "deadlock", "mutex", "semaphore",
        "encapsulation", "abstraction", "garbage collection", "heap", "stack", "queue",
        "hash table", "binary tree", "traversal", "greedy algorithm", "backtracking",
        "dynamic programming", "gradient descent", "overfitting", "regularization",
        "hyperparameter", "inference", "embedding", "transformer", "attention", "tokenizer",
    ],
    "人工智能": [
        "neural network", "convolution", "convolutional", "backpropagation", "activation function",
        "gradient descent", "stochastic", "overfitting", "underfitting", "regularization",
        "dropout", "batch normalization", "embedding", "attention mechanism", "transformer",
        "encoder", "decoder", "supervised learning", "unsupervised learning", "reinforcement learning",
        "loss function", "learning rate", "epoch", "batch size", "validation set",
        "precision", "recall", "f1 score", "confusion matrix", "hyperparameter tuning",
    ],
    "数学": [
        "eigenvalue", "eigenvector", "derivative", "integral", "gradient", "divergence",
        "curl", "laplacian", "determinant", "matrix", "vector space", "orthogonal",
        "convergence", "diverge", "theorem", "lemma", "corollary", "asymptotic",
        "polynomial", "factorization", "permutation", "combination", "probability distribution",
        "expected value", "variance", "covariance", "correlation", "hypothesis testing",
    ],
    "物理": [
        "momentum", "acceleration", "velocity", "entropy", "enthalpy", "thermodynamics",
        "electromagnetic", "wavelength", "frequency", "amplitude", "interference",
        "diffraction", "refraction", "quantum", "photon", "electron", "neutron",
        "relativity", "inertia", "torque", "angular momentum", "kinetic energy",
        "potential energy", "superconductivity", "semiconductor", "capacitance", "inductance",
    ],
    "生物": [
        "mitochondria", "ribosome", "chromosome", "deoxyribonucleic", "transcription",
        "translation", "enzyme", "protein", "metabolism", "photosynthesis", "respiration",
        "eukaryotic", "prokaryotic", "membrane", "cytoplasm", "nucleotide", "amino acid",
        "allele", "genotype", "phenotype", "mutation", "natural selection", "homeostasis",
    ],
    "医学": [
        "diagnosis", "prognosis", "symptom", "syndrome", "pathology", "etiology",
        "inflammation", "carcinoma", "metastasis", "biopsy", "cardiovascular",
        "hypertension", "diabetes", "antibiotic", "vaccine", "immunity", "anesthesia",
        "catheter", "endoscopy", "ultrasound", "magnetic resonance", "computed tomography",
    ],
    "经济": [
        "inflation", "deflation", "recession", "gdp", "monetary policy", "fiscal policy",
        "interest rate", "liquidity", "equilibrium", "elasticity", "marginal utility",
        "opportunity cost", "comparative advantage", "supply and demand", "oligopoly",
        "monopoly", "arbitrage", "derivative", "portfolio", "diversification", "valuation",
    ],
    "金融": [
        "equity", "bond", "yield", "volatility", "hedge", "leverage", "liquidity",
        "arbitrage", "derivative", "portfolio", "diversification", "valuation",
        "discounted cash flow", "capital asset pricing", "beta coefficient",
        "quantitative easing", "credit default swap", "market capitalization",
    ],
    "法律": [
        "jurisdiction", "statute", "precedent", "liability", "tort", "plaintiff",
        "defendant", "verdict", "appeal", "jurisprudence", "due diligence",
        "breach of contract", "intellectual property", "fiduciary duty",
    ],
    "化学": [
        "molecule", "compound", "covalent", "ionic", "electronegativity", "stoichiometry",
        "catalyst", "oxidation", "reduction", "titration", "spectroscopy", "chromatography",
        "enthalpy", "activation energy", "polymer", "isomer", "solvent", "solute",
    ],
    "工程": [
        "torque", "stress", "strain", "tolerance", "actuator", "sensor", "calibration",
        "tensile", "compressive", "shear", "fatigue", "corrosion", "thermal conductivity",
        "efficiency", "prototype", "iteration", "specification", "redundancy",
    ],
    "心理学": [
        "cognition", "perception", "stimulus", "reinforcement", "conditioning",
        "working memory", "attention span", "personality trait", "cognitive bias",
        "confirmation bias", "attachment theory", "neuroplasticity", "amygdala",
        "prefrontal cortex", "neurotransmitter", "serotonin", "dopamine",
    ],
    "商科": [
        "stakeholder", "revenue", "margin", "roi", "kpi", "supply chain", "outsourcing",
        "benchmark", "scalability", "market segmentation", "value proposition",
        "swot analysis", "break-even", "cash flow", "bootstrapping", "due diligence",
    ],
    "环境": [
        "sustainability", "biodiversity", "ecosystem", "carbon footprint", "emission",
        "renewable energy", "greenhouse gas", "deforestation", "conservation",
        "mitigation", "adaptation", "watershed", "aquifer", "sediment",
    ],
    "人文": [
        "epistemology", "ontology", "paradigm", "dialectic", "hermeneutics",
        "discourse", "narrative", "rhetoric", "semiotics", "ideology", "hegemony",
        "positivism", "empiricism", "phenomenology", "existentialism",
    ],
}

# 领域名归一化：课程里填的"其他"/空/英文都要能映射上
# 键必须与前端 CourseConfig.tsx 的 DOMAINS 常量保持一致
_DOMAIN_ALIASES: dict[str, str] = {
    "计算机科学": "计算机", "计算机": "计算机", "cs": "计算机", "computer science": "计算机",
    "编程": "计算机", "软件工程": "计算机", "人工智能": "人工智能", "ai": "人工智能",
    "机器学习": "人工智能", "深度学习": "人工智能", "machine learning": "人工智能",
    "数学": "数学", "统计学": "数学", "统计": "数学", "math": "数学", "应用数学": "数学",
    "物理学": "物理", "物理": "物理", "physics": "物理",
    "生物学": "生物", "生物": "生物", "biology": "生物",
    "医学": "医学", "医疗": "医学", "medicine": "医学", "临床医学": "医学",
    "经济学": "经济", "经济": "经济", "economics": "经济",
    "金融学": "金融", "金融": "金融", "finance": "金融",
    "法律": "法律", "法学": "法律", "law": "法律",
    "化学": "化学", "chemistry": "化学",
    "工程": "工程", "机械": "工程", "电气": "工程", "土木": "工程", "engineering": "工程",
    "心理学": "心理学", "psychology": "心理学",
    "商科": "商科", "管理学": "商科", "管理": "商科", "市场营销": "商科", "business": "商科",
    "环境": "环境", "地球科学": "环境", "environment": "环境",
    "哲学": "人文", "历史": "人文", "文学": "人文", "人文": "人文",
}


def resolve_domain(domain: str) -> Optional[str]:
    """把课程领域名归一化到 DOMAIN_TERMS 的键"""
    if not domain:
        return None
    key = domain.strip().lower()
    return _DOMAIN_ALIASES.get(key) or _DOMAIN_ALIASES.get(domain.strip())


# ============ 词表加载 ============

_vocab_cache: dict | None = None


def load_basic_vocab() -> dict:
    """加载内置词表（进程内缓存）"""
    global _vocab_cache
    if _vocab_cache is None:
        try:
            with open(VOCAB_PATH, "r", encoding="utf-8") as f:
                _vocab_cache = json.load(f)
            logger.info(f"术语库已加载内置词表: {len(_vocab_cache)} 词")
        except Exception as e:
            logger.warning(f"加载内置词表失败: {e}")
            _vocab_cache = {}
    return _vocab_cache


def is_vocab_key(word: str) -> bool:
    """查词是否为用户输入了中文释义的键（用于判定长短语是否意图查词）"""
    return word.lower().strip() in load_basic_vocab()


def lookup_vocab(word: str) -> Optional[dict]:
    """从内置词表查词，返回 {translation, definition} 或 None（大小写不敏感）"""
    return load_basic_vocab().get(word.lower().strip())


# ============ initial_prompt 构建 ============

def _dedup_keep_order(items: list[str]) -> list[str]:
    """去重但保持顺序（Prompt 里顺序即优先级）"""
    seen = set()
    out = []
    for it in items:
        key = it.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append(it.strip())
    return out


def build_prompt_sections(
    domain: str = "",
    custom_terms: Optional[list[str]] = None,
    discovered_terms: Optional[list[str]] = None,
) -> list[tuple[str, list[str]]]:
    """
    按优先级分层返回术语（**不做任何截断**），交给调用方按 token 预算组装

    为什么需要它（P0-3）：faster-whisper 只保留 initial_prompt 的**末尾 223 token**，
    而 `build_initial_prompt()` 把它们拼成一个**高优先级在前**的字符串 —— 结果
    优先级最高的自定义术语最先被截掉。引擎拿到分层结构后，才能按
    「高优先级贴尾」重新拼接（见 `asr/prompt_budget.py`）。

    优先级（高 → 低）：
        1. custom     用户手填，一定是课程核心词
        2. discovered 本课程已被 ASR+LLM 提取出的词（说明课堂确实在用）
        3. domain     领域标准术语表
        4. phrases    词表里的多词短语

    ⚠ 短语层刻意**不在此处排序取前 60** —— 排序需要知道最终预算，属于组装方的
    职责。这里只保证「层内顺序 = 领域命中优先」。
    """
    resolved = resolve_domain(domain)

    phrases: list[str] = []
    vocab = load_basic_vocab()
    phrases = [w for w in vocab if " " in w and len(w) < 40]
    # 领域命中优先，避免大词表里无关短语挤占空间
    if resolved:
        domain_lower = [t.lower() for t in DOMAIN_TERMS.get(resolved, [])]
        phrases.sort(key=lambda w: (w.lower() not in domain_lower, len(w)))
    phrases = phrases[:60]

    return [
        ("custom", _dedup_keep_order(custom_terms or [])),
        ("discovered", _dedup_keep_order(discovered_terms or [])),
        ("domain", _dedup_keep_order(DOMAIN_TERMS.get(resolved, [])) if resolved else []),
        ("phrases", _dedup_keep_order(phrases)),
    ]


def build_initial_prompt(
    domain: str = "",
    custom_terms: Optional[list[str]] = None,
    discovered_terms: Optional[list[str]] = None,
    max_chars: Optional[int] = None,
) -> str:
    """
    构建 Whisper initial_prompt（**字符口径**的兜底实现）

    优先级（高 → 低）：custom_terms → discovered_terms → DOMAIN_TERMS → 词表短语。
    领域名可用中文，内部会 `resolve_domain()` 归一化。

    ⚠ 输出顺序是**低优先级在前、高优先级在后**：

        [词表短语] ... [领域术语] ... [课程已发现] ... [自定义术语]

    这不是排版问题而是正确性问题 —— faster-whisper 只保留提示词的**末尾 223 token**
    （`WhisperModel.get_prompt()`），尾部才是真正进模型的部分。旧实现按
    高优先级在前拼接，导致中文术语文档里**优先级最高的自定义术语存活率只有 25%，
    而优先级最低的内置英文术语 100% 存活**（实测见 ASR 报告 §6.10）。

    真正的修复在引擎侧（`asr/prompt_budget.py`，按 token 预算组装，还会额外
    安排简体引导语与上一段文本）；这个函数保留字符口径，供不经引擎的调用方使用。

    Returns:
        一段逗号分隔的术语文本；无可用术语时返回空字符串（此时应传 initial_prompt=None）
    """
    max_chars = max_chars if max_chars is not None else get_prompt_max_chars()
    sections = build_prompt_sections(domain, custom_terms, discovered_terms)

    # 与引擎同口径：按高 → 低分配字符预算（保证高优先级不被丢），再按低 → 高输出
    remaining = max_chars
    kept_by_section: list[list[str]] = []
    for _name, terms in sections:
        kept: list[str] = []
        for t in terms:
            cost = len(t) + (2 if kept else 0)      # ", " 分隔符
            if cost > remaining:
                continue                              # 层内跳过放不下的，继续试更短的
            remaining -= cost
            kept.append(t)
        kept_by_section.append(kept)

    ordered: list[str] = []
    for kept in reversed(kept_by_section):
        ordered.extend(kept)
    return ", ".join(ordered)


# ============ 术语库（词表 + 术语来源合并） ============

def build_terminology(
    domain: str = "",
    custom_terms: Optional[list[str]] = None,
    discovered_terms: Optional[list[str]] = None,
) -> dict:
    """
    构建课程术语库摘要，供上层判断"这个词有没有现成释义"

    Returns:
        {
            "prompt": initial_prompt 字符串,
            "promptChars": 长度,
            "sections": [(层名, [术语...]), ...]  高优先级 → 低优先级，**未截断**，
                        供引擎按 token 预算自行组装（P0-3）
            "terms": [{"term": str, "source": "custom"|"course"|"domain",
                       "inVocab": bool, "translation": str, "definition": str}],
            "stats": {"total": n, "inVocab": n, "vocabHits": {...}}
        }
    """
    prompt = build_initial_prompt(domain, custom_terms, discovered_terms)
    sections = build_prompt_sections(domain, custom_terms, discovered_terms)

    entries: list[dict] = []
    seen: set[str] = set()

    def add(term: str, source: str):
        key = term.lower().strip()
        if not key or key in seen:
            return
        seen.add(key)
        hit = lookup_vocab(key)
        entries.append({
            "term": term.strip(),
            "source": source,
            "inVocab": hit is not None,
            "translation": (hit or {}).get("translation", ""),
            "definition": (hit or {}).get("definition", ""),
        })

    for t in (custom_terms or []):
        add(t, "custom")
    for t in (discovered_terms or []):
        add(t, "course")
    resolved = resolve_domain(domain)
    if resolved:
        for t in DOMAIN_TERMS.get(resolved, []):
            add(t, "domain")

    in_vocab = sum(1 for e in entries if e["inVocab"])

    return {
        "prompt": prompt,
        "promptChars": len(prompt),
        "sections": sections,
        "domain": resolved or "",
        "terms": entries,
        "stats": {
            "total": len(entries),
            "inVocab": in_vocab,
            "custom": sum(1 for e in entries if e["source"] == "custom"),
            "domain": sum(1 for e in entries if e["source"] == "domain"),
            "coverage": round(in_vocab / len(entries), 3) if entries else 0.0,
        },
    }


# ============ 字幕术语匹配（不依赖 LLM） ============

# 词表规模较大（3000+），逐词扫字幕太慢。
# 策略：字幕先提取候选 n-gram（1~5 词），只在候选里查表，复杂度与字幕长度线性相关。
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z\-']*")
_MAX_NGRAM = 5

# 停用词：这些词虽然词表里也有释义，但属于"学生本来就会"的基础词，
# 混进术语表只会稀释专业词的权重，必须过滤掉。
# 判定依据：不是学术术语，而是讲座中的高频口语/连接/基础动词。
STOPWORDS: frozenset[str] = frozenset("""
a an the this that these those and or but if then so such as of in on at to for with by from
is are was were be been being am do does did done have has had having will would shall should
can could may might must
i you he she it we they me him her us them my your his its our their
what which who whom whose when where why how
not no nor only just also too very much many more most some any all both each few other
about above after again against before below between during into through under over up down out
now here there then than because while although though since until unless
say said says tell told talk talking look looking see seen know known think thought want wanted
go going gone get got give given make made take taken come came want need try trying use used
using work working call called find found put keep kept let mean means meant
today tomorrow yesterday week month year day time thing things way ways part parts point points
really actually basically okay ok yeah yes well right good bad new old big small long short
please thank thanks hello hi welcome
discuss discussion specifically generally usually often sometimes always never
explain explanation describe description mention mentioned example examples
understand understanding important importance interesting difficult easy hard simple complex
student students teacher professor class course lecture lesson chapter section
question questions answer answers problem problems idea ideas topic topics
start begin end finish continue stop change changes different same similar
""".split())


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text)


def match_terms_in_text(text: str, max_terms: int = 8) -> list[dict]:
    """
    从字幕文本中匹配已知术语（纯本地，零外部依赖）

    匹配策略：
    - 优先长短语（"gradient descent" 优于单独的 "gradient"）
    - 已匹配过的词不再被更短的片段重复匹配
    - 过滤停用词与过短单词（<3 字符）

    Returns:
        [{word, translation, definition, context_sentence}]，按出现顺序
    """
    if not text or not text.strip():
        return []

    vocab = load_basic_vocab()
    tokens = _tokenize(text)
    if not tokens:
        return []

    consumed = [False] * len(tokens)
    hits: list[dict] = []

    # 从最长 n-gram 开始扫描，保证长短语优先
    for n in range(_MAX_NGRAM, 0, -1):
        for i in range(len(tokens) - n + 1):
            if any(consumed[i:i + n]):
                continue
            candidate = " ".join(tokens[i:i + n])
            key = candidate.lower()

            # 停用词过滤：整个短语都是停用词就跳过
            if all(t.lower() in STOPWORDS for t in tokens[i:i + n]):
                continue
            # 单词太短不可能是术语（ph, cs 这类缩写除外时由词表决定）
            if n == 1 and len(key) < 4:
                continue

            entry = vocab.get(key)
            if not entry:
                continue

            consumed[i:i + n] = [True] * n
            hits.append({
                "word": candidate,
                "translation": entry.get("translation", ""),
                "definition": entry.get("definition", ""),
                "context_sentence": _sentence_around(text, candidate),
            })
            if len(hits) >= max_terms:
                return hits

    return hits


def _sentence_around(text: str, word: str) -> str:
    """取包含该词的句子作为上下文"""
    for sentence in re.split(r"[.!?。！？]+", text):
        if word.lower() in sentence.lower():
            return sentence.strip()[:300]
    return ""
