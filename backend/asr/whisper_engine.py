"""
Faster-Whisper 语音识别引擎
提供实时语音转文字功能，支持词级时间戳
"""

import logging
import threading
import time
import os
from typing import Optional, Callable
from dataclasses import dataclass
from collections import deque
from pathlib import Path

import numpy as np

from asr.model_bootstrap import check_installed, ensure_model, model_root
from asr import prompt_budget
from asr import repetition
from asr.prompt_echo import EchoCleaner

logger = logging.getLogger(__name__)


def _configure_hf_environment(model_dir: str):
    """
    配置 HuggingFace 下载环境（直接决定首次启动能否下到模型）

    1) 镜像：huggingface.co 在国内网络下常常**完全不可达**
       （实测连接 10 秒超时、http 状态 000），而 hf-mirror.com 正常（0.88s）。
       原代码没有任何兜底，用户首次启动只会看到「模型下载失败」。
       默认走镜像，可用 HF_ENDPOINT 覆盖（例如有专线时指回官方站）。

    2) 关闭 Xet：huggingface-hub 默认启用 hf-xet，文件内容会走
       cas-server.xethub.hf.co，而镜像站**不代理**这个 CAS 服务，返回 401 Unauthorized。
       最坑的是：cache 模式下它会一边报「下载成功」一边留下 **0 字节的快照文件**，
       直到加载模型才报 "model.bin is incomplete: failed to read a value of size 4"。
       关掉之后走普通 HTTPS 下载，配合镜像可正常工作。

    3) HF_HOME 与 download_root 必须指向同一目录，理由见 _load_model 注释。
    """
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("HF_HOME", model_dir)


_MODEL_DIR = str(model_root())
os.makedirs(_MODEL_DIR, exist_ok=True)
_configure_hf_environment(_MODEL_DIR)

# 模型配置
# 默认 base 而非 small：实测在 32 核机器、4 秒片段下
#   small 需 RTF 1.438（跟不上实时），base 为 0.482（跟得上）
# 术语热词注入已能补回一部分专业词准确率，故优先保证实时性。
DEFAULT_MODEL = os.environ.get("TINGKEBANLV_WHISPER_MODEL", "base")


def _detect_cpu_threads() -> int:
    """
    选择 ctranslate2 的 CPU 线程数

    原实现硬编码 4，在 32 核机器上只用了 1/8 算力。实测（small 模型 / 8 秒片段）：
        4 线程 → RTF 0.738
        8 线程 → RTF 0.584
       16 线程 → RTF 0.493
    收益随线程数递减，且过多线程会与其他环节抢 CPU，故上限取 8。
    """
    env_threads = os.environ.get("TINGKEBANLV_WHISPER_THREADS")
    if env_threads:
        try:
            return max(1, int(env_threads))
        except ValueError:
            logger.warning(f"无法解析 TINGKEBANLV_WHISPER_THREADS={env_threads}，改用自动探测")
    logical = os.cpu_count() or 4
    return max(2, min(8, logical // 2))


def _detect_device() -> str:
    """自动检测最佳计算设备"""
    # 1. 环境变量强制指定
    if env_device := os.environ.get("WHISPER_DEVICE"):
        logger.info(f"使用环境变量指定的设备: {env_device}")
        return env_device

    # 2. 检查 CUDA
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            logger.info(f"检测到 CUDA GPU: {gpu_name}")
            return "cuda"
    except ImportError:
        pass

    # 3. 检查 Apple Silicon (MPS)
    try:
        import torch
        if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
            logger.info("检测到 Apple Silicon MPS，当前降级使用 CPU（faster-whisper 对 MPS 支持不稳定）")
            return "cpu"
    except ImportError:
        pass

    logger.info("未检测到 GPU，使用 CPU")
    return "cpu"


def _detect_compute_type(device: str) -> str:
    """根据设备选择最优 compute_type"""
    if device == "cuda":
        return "float16"
    return "int8"


DEVICE = _detect_device()
COMPUTE_TYPE = _detect_compute_type(DEVICE)

# 实时处理配置
#
# MIN_SEGMENT_DURATION / MAX_BUFFER_DURATION / OVERLAP_DURATION 已移除：
# 分段由 audio/vad.py 负责，识别侧不再自己攒缓冲。那三个常量原先从未被真正使用
# （MAX_BUFFER_DURATION 的「最大缓冲」从未生效），留着只会误导后来的人。

# 待识别队列上限（背压）
# 正常跟得上时队列始终只有 1 个任务；只有在识别落后时才会积压，
# 消费者会把积压片段合并成一次调用，所以这个上限只用于兜底防内存失控。
MAX_PENDING_JOBS = int(os.environ.get("TINGKEBANLV_MAX_PENDING_JOBS", "8"))


# ---- 术语提示词「重复循环」的运行时防护 ----
#
# 背景（实测于 scripts/eval_asr_quality.py）：术语提示词给到 794 字符（当前默认上限）时
#   出现「产出字幕片段数 > 送入片段数」，且单次识别 RTF 突破 1 —— Whisper 重复循环的
#   特征，同一句会被吐两遍。短提示词（≤195 字符）在 5/5 用例里都没出现。
#
# ⚠ 判据在 2026-10-06 的中文代理集 A/B 后被**整条换掉**，原因见下面 REPETITION_* 注释：
#   旧的「字幕数/片段数 > 1.2」在中文下两个方向都错，实测既误报又漏报。
#   新判据是**文本自我重复检测**（backend/asr/repetition.py），字段与常量一并保留原名，
#   避免配置漂移。
AUTO_DEGRADE_ENABLED = os.environ.get("TINGKEBANLV_TERM_AUTODEGRADE", "1").lower() not in (
    "0",
    "false",
    "no",
)
# 降级后保留的长度。取 200 是因为实测 ≤195 字符的档位没出现过重复循环。
DEGRADE_PROMPT_CHARS = int(os.environ.get("TINGKEBANLV_TERM_DEGRADE_TO", "200"))
# 供 repetition.detect() 使用的滚动文本缓冲长度。
# 取窗口的 2 倍：detect 只看尾部窗口，多留一截是为了让「刚产出的内容」总是完整落在窗口里，
# 不至于因为恰好跨在边界上而被劈成两半。
REPETITION_BUFFER_CHARS = int(os.environ.get("TINGKEBANLV_TERM_REPEAT_BUFFER", "320"))

# 旧的「字幕/片段」比值：**降级为辅助告警**，不再触发任何动作。
#
# 中文代理集实测（两轮 6 样本）：
#   · `--terms off`（**没有任何提示词**）比值天然 1.625 —— 中文一句话被标点切成多条字幕，
#     这是中文的天然形态，拿它当故障信号必然误报；
#   · 真复读样本（「级测试」重复 40+ 次）比值只有 1.089 —— 复读会把大段文字塞进**一条**
#     字幕，分子分母都不涨，最危险的情况恰好漏报。
# 保留常量只为在日志里留一条可对照的参考线。
ADVISORY_SUBTITLE_RATIO = float(os.environ.get("TINGKEBANLV_TERM_REPEAT_RATIO", "1.2"))

# `_last_text` 送进提示词的上下文长度（字符）。
#
# ⚠ 这个字符口径的常量**只用于「降级后是否还有内容」的粗筛**，
# 真正的截断已改成 token 口径（见 `PROMPT_PREV_TOKENS`）。
# 保留它是为了不改动配置项名称，避免设置面板 / 环境变量漂移。
LAST_TEXT_PROMPT_CHARS = int(os.environ.get("TINGKEBANLV_TERM_LAST_TEXT_CHARS", "200"))

# ---- P0-3：提示词总预算改成 **token 口径** ----
#
# 原因：faster-whisper 的 `get_prompt()` 只保留 `previous_tokens[-223:]`
# （`max_length // 2 - 1`），**超出部分静默丢弃**。而产品上限是「字符」，
# 两个口径在中文上差 2~4.7 倍（中文每字 ≈ 1.35 token，英文每字符 ≈ 0.30 token），
# 于是中文 799 字符 = 506 token，被砍掉 283（56%），且砍掉的正是优先级最高的
# 自定义术语（存活率 25% vs 内置英文的 100%）。详见 ASR 报告 §6.10。
#
# 预算取 220 而库保留 223，留 3 个 token 冗余吸收估算误差。
PROMPT_TOKEN_BUDGET = int(os.environ.get("TINGKEBANLV_TERM_PROMPT_TOKENS", "220"))
# `_last_text` 的 token 配额（旧实现 `[-200:]` 字符在中文下约 280 token，
# 单它一项就吃穿整个预算 —— 这才是「第二段起术语全丢」的直接原因）
PROMPT_PREV_TOKENS = int(os.environ.get("TINGKEBANLV_TERM_PREV_TOKENS", "48"))
# 降级后（检测到重复循环）的总预算。降到这个量级只剩最高优先级的少数术语 +
# 引导语，足以止血，又不像旧实现那样直接按字符砍掉尾部（= 砍掉高优先级）。
DEGRADE_TOKEN_BUDGET = int(os.environ.get("TINGKEBANLV_TERM_DEGRADE_TOKENS", "70"))

# 简体中文引导语（P0-1：Whisper 的 language="zh" 不区分简繁，默认倾向输出繁体）
#
# 实测依据（中文代理集 A/B）：
#   · 提示词为空 → 繁体字占识别结果的 34.4%，简繁差异吃掉 51.6%~56.9% 的「错误」；
#   · 提示词里**只有英文术语** → 繁体占比 34.5%，毫无改善；
#   · 提示词里有**简体中文术语** → 繁体占比 **0.1%**。
# 所以只要让简体中文出现在提示词里就能把输出拉回简体，不需要额外的繁简转换依赖。
# 但术语表可能为空（新课程、用户没选领域），所以必须**显式注入**这句，不能靠术语表顺带解决。
SIMP_HINT = "以下是普通话的句子，请使用简体中文转写。"
# 注入引导语的语种：只有中文需要（英日韩等简繁不分，加了纯属浪费 prompt 预算）
SIMP_HINT_LANGS = frozenset({"zh", "zh-cn", "zh-tw"})


def get_simp_hint() -> str:
    """
    当前生效的简体引导语

    文案可用 `TINGKEBANLV_SIMP_HINT` 覆盖 —— 一是方便做「只换引导语、其它全不动」
    的单变量对照实验（改文案要重新编译常量就没法对照了），二是产品侧以后可以
    按课程配置。空值/纯空白视为未设置，回落到内置默认文案。
    """
    return (os.environ.get("TINGKEBANLV_SIMP_HINT") or "").strip() or SIMP_HINT


# ---------------------------------------------------------------------------
# V5-1：auto 模式下的语种探测与简体强制（缺陷 D1）
# ---------------------------------------------------------------------------
#
# **问题**：`SIMP_HINT_LANGS` 不含 `"auto"`，而前后端 6 处默认值全是 `"auto"`
# ⇒ 走默认路径的中文课程**从不注入**简体引导语 ⇒ 输出满屏繁体
# （`docs/asr_eval/known_defects.md` D1）。
#
# **为什么必须做一次「前置」探测**：引导语要在**解码之前**进 prompt，而语种要
# **解码之后**才能从 `info.language` 读到 —— 等拿到语种再补提示词已经晚了。
# 所以 auto 下只能自己先跑一次 encoder-only 的 `detect_language()`，
# 据此决定这一块走不走简体路径。
#
# **为什么不用 zhconv 之类的后处理**：上面的 P0-1 实测已经证明「提示词里出现
# 简体中文」就能把繁体占比从 34.4% 压到 0.1%，且**不需要额外依赖**。后处理是在
# 识别结果上硬改字形，会引入新的误改风险（专有名词、古文、异体字）；而引导语是
# 让模型在**源头**直接吐简体。两条路线的实测对比见
# `docs/asr_eval/WER_REPORT_PLAN.md` 的「简繁质量」小节。
#
# **开销**：探测本身要跑一遍 encoder，但**每个会话最多只付几次**（见粘滞策略），
# 稳态下不再重复探测 ⇒ 对 RTF 的影响可忽略。
LANG_DETECT_ENABLED = os.environ.get("TINGKEBANLV_LANG_DETECT", "1") not in ("0", "false", "False")

# 语种探测置信度阈值：低于它视为「判不准」⇒ **降级**，既不强制中文、也不注入引导语，
# 完全维持修复前的 auto 行为。宁可维持现状，也不要在判不准时乱改字形（V5-2）。
LANG_MIN_CONFIDENCE = float(os.environ.get("TINGKEBANLV_LANG_MIN_PROB", "0.6"))

# 判定为**目标语种**（中文）时，第 1 次高置信即锁定 —— 这正是要修的场景。
# 判定为**非目标语种**（如英文课）时要连续观察到这么多次才锁定：只看到 1 次就锁，
# 中文课开头的英文片头/报幕会把整个会话锁成「不注引导语」，缺陷原样复发。
LANG_NON_TARGET_LOCK_AFTER = int(os.environ.get("TINGKEBANLV_LANG_NON_TARGET_LOCK", "2"))

# V5-4 开关的两个取值（课程配置项，见 backend/api/routes.py 与 CourseConfig.tsx）
SIMP_MODE_AUTO_SIMPLIFIED = "auto-simplified"   # 默认：auto 判为中文时强制简体
SIMP_MODE_RAW = "raw"                           # 保留原始识别字形（= 修复前的行为）
SIMP_MODES = (SIMP_MODE_AUTO_SIMPLIFIED, SIMP_MODE_RAW)


def normalize_simp_mode(value: Optional[str]) -> str:
    """把外部传进来的开关值收敛到合法取值，非法值一律回落到默认（不报错）。

    为什么要收敛而不是抛错：这个值来自数据库里的历史行与前端表单，
    两者都可能带上旧值/空值。为它让整个 ASR 管道起不来是不划算的 ——
    识别功能正常远比开关取值严格重要。
    """
    v = (value or "").strip().lower()
    return v if v in SIMP_MODES else SIMP_MODE_AUTO_SIMPLIFIED


@dataclass
class WordTimestamp:
    word: str
    start: float  # 秒
    end: float    # 秒
    confidence: float


@dataclass
class SubtitleSegment:
    text: str
    words: list[WordTimestamp]
    start: float
    end: float
    is_final: bool


class WhisperEngine:
    """
    Faster-Whisper 识别引擎
    接收语音片段，返回带时间戳的文本
    """

    def __init__(
        self,
        model_size: str = DEFAULT_MODEL,
        device: str = DEVICE,
        compute_type: str = COMPUTE_TYPE,
        language: str = "auto",
        on_partial: Optional[Callable[[str, list[WordTimestamp]], None]] = None,
        on_progress: Optional[Callable[[str, float], None]] = None,
        initial_prompt: Optional[str] = None,
        prompt_sections: Optional[list[tuple[str, list[str]]]] = None,
        cpu_threads: Optional[int] = None,
        # V5-4：简繁输出开关。刻意放在签名**末尾**而不是 `language` 旁边 ——
        # 老调用点只用到 `cpu_threads` 为止，放中间会让按位置传参的调用点静默错位。
        simp_mode: str = SIMP_MODE_AUTO_SIMPLIFIED,
        # V5-8：auto 下判定出目标语种后**一次性**回调，让上层用这个语种重建术语层。
        # 引擎自己不知道术语分了几层、每层该不该有内容，那属于 `dictionary.terminology`
        # 的知识；引擎只负责把「语种已经定了」这件事告诉上层。同样放在末尾。
        on_language_decided: Optional[Callable[[str], None]] = None,
    ):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.simp_mode = normalize_simp_mode(simp_mode)
        self.cpu_threads = cpu_threads or _detect_cpu_threads()
        self.on_partial = on_partial
        self.on_progress = on_progress
        self.on_language_decided = on_language_decided
        # 术语热词先验：给 Whisper 一段领域术语文本，显著提升专业词拼写准确率
        self.initial_prompt = (initial_prompt or "").strip() or None
        # 分层术语 `[(层名, [术语...]), ...]`（高 → 低优先级），来自
        # `dictionary.terminology.build_prompt_sections()`。有它才能按 token 预算
        # 重排——高优先级贴尾（P0-3）。为 None 时退化为只用 `initial_prompt` 单串。
        self._prompt_sections = list(prompt_sections) if prompt_sections else None

        # 多轮识别共享的"上一段文本"，让跨片段的术语拼写保持连贯
        self._last_text: Optional[str] = None

        # ---- 提示词回显清洗（见 asr/prompt_echo.py 模块注释）----
        # 🔴 与 V5-1 的耦合点，别改回去：auto 路径现在**也可能**注入引导语
        # （探测判为中文时），所以这里不能再只按 `self.language in SIMP_HINT_LANGS`
        # 判断。否则 auto 下注入的引导语**不会被清洗**，静音块上会被模型接着念出来、
        # 当字幕显示给用户 —— 正是 prompt_echo 模块开头描述的那条虚假字幕。
        # 判据与 `_build_prompt()` 的注入判据必须同源（见 `_hint_possible()`）。
        self._echo = EchoCleaner(get_simp_hint() if self._hint_possible() else "")
        self._echo_logged = False

        # ---- V5-1：auto 模式下的语种决策（会话级粘滞，等价实现原 V5-3）----
        # 决策对象是「本次调用该以什么语种解码」，它同时驱动引导语注入与 transcribe
        # 的 `language=` 参数 —— 两处必须由**同一次**决策驱动，各判一次迟早会不一致。
        self._lang_decided = False            # 是否已锁定（锁定后不再探测）
        self._lang_decision: Optional[str] = None   # 锁定到的语种；None = 已判定为非目标语种
        self._lang_confidence = 0.0
        self._lang_source = "unset"           # explicit / disabled / sticky / detected / ...
        self._lang_detect_events = 0          # 实际跑了探测的次数（观测用）
        self._lang_low_conf_events = 0        # 因置信不足而降级的次数（V5-2 观测用）
        self._lang_non_target_streak = 0      # 连续观察到非目标语种的次数
        # V5-8：`on_language_decided` 只许发一次（同一场课重建一次术语层就够；
        # 每块都重建会让提示词反复变化，`condition_on_previous_text` 的连贯性
        # 条件随之抖动）。`reset_language_decision()` 会把它清零，使「换课程/
        # 换素材后重新探测」也能重新通知。
        self._lang_decided_notified = False

        self._model = None
        self._loading = False
        self._lock = threading.Lock()
        self._total_duration = 0.0
        self._running = False

        # ---- 识别任务队列（背压所在）----
        # 原实现每来一段音频就裸起一个线程去 transcribe，既无队列上限也无串行化：
        # 一旦识别速度跟不上音频速度（实测 small 模型在 2~4 秒片段上 RTF>1），
        # 线程会无界堆积 → CPU 被抢死、字幕乱序、内存持续上涨直至 OOM。
        # 现在改为「单消费者 + 有界队列」：
        #   - 正常情况下队列里只有 1 个任务，字幕照常即时产出
        #   - 跟不上时任务会积压，消费者把积压片段**合并成一次调用**
        #     （单次调用有约 4 秒固定开销，合并远优于丢弃）
        self._jobs: deque[tuple[np.ndarray, float, float]] = deque()
        self._jobs_cv = threading.Condition()
        self._stop_worker = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._busy = False             # 是否正在做一次识别（供 flush 判断）
        self._merged_jobs = 0          # 合并过几次（用于观测是否在降级）
        self._dropped_jobs = 0

        # ---- 术语提示词重复循环防护（见模块顶部 AUTO_DEGRADE_* 注释）----
        self._auto_degrade = AUTO_DEGRADE_ENABLED
        self._prompt_degraded = False          # 是否已因检测到重复而缩短过提示词
        self._prompt_original: Optional[str] = None   # 缩短前的完整提示词（供恢复）
        self._prompt_original_sections: Optional[list] = None  # 同上，分层版本
        self._stat_in_segments = 0             # 累计送入识别的片段数（辅助告警分母）
        self._stat_out_segments = 0            # 累计产出字幕的片段数（辅助告警分子）
        self._repetition_events = 0            # 检测到复读的次数（**含未降级的**）
        self._degrade_events = 0               # 因复读真正执行降级的次数
        self._recent_text = ""                 # 最近识别文本的滚动缓冲（新判据的输入）
        self._last_verdict = None              # 最近一次重复检测结论（供 get_stats 观测）
        self._advisory_logged = False          # 辅助告警只记一次，避免刷屏
        # 最近一次提示词组装的预算结果（P0-3 埋点，供 get_stats 观测）
        self._last_budget: Optional[prompt_budget.BudgetedPrompt] = None

        # 后台异步加载模型（不阻塞事件循环）
        self._loading = True
        self._load_thread = threading.Thread(target=self._load_model, daemon=True)
        self._load_thread.start()

    def _report_progress(self, stage: str, progress: float):
        """通过回调报告加载进度"""
        if self.on_progress:
            self.on_progress(stage, progress)
        logger.info(f"模型加载进度: {stage} {progress:.0f}%")

    def _is_model_cached(self) -> bool:
        """
        检查本地是否已有可用模型

        校验逻辑集中在 asr/model_bootstrap.check_installed，这里只做转发：
        两处各写一份「什么算缓存可用」迟早会漂移，而它们漂移的后果是
        「安装包说内置了模型、加载时却以为没有又去下载」。

        刻意校验文件**大小**而不只是「是否存在」：
        在「HF 镜像 + hf-xet」组合下 snapshot_download 会一边报告下载成功、
        一边留下 0 字节的 model.bin。只判断存在性会把这种损坏缓存当成可用，
        之后每次启动都在加载阶段抛 "model.bin is incomplete"。
        """
        return bool(check_installed(self.model_size)["ok"])

    def _monitor_download(self, stop_event: threading.Event):
        """监控下载进度，通过缓存目录大小估算百分比"""
        # 数值来自对 Systran/faster-whisper-* 的实际测量（model.bin + tokenizer + config）：
        #   tiny 72MB / base 139MB / small 461MB / medium 1458MB / large-v3 约3.1GB
        # 注意该值就是进度条的**分母**：原实现给 small 写 2GB（真实 464MB）、
        # medium 写 5GB（真实 1.46GB），导致进度条长期封顶在 24% 左右再突跳完成。
        expected_sizes = {
            "tiny": 75 * 1024 * 1024,
            "base": 141 * 1024 * 1024,
            "small": 464 * 1024 * 1024,
            "medium": 1460 * 1024 * 1024,
            "large-v3": 3100 * 1024 * 1024,
        }
        expected = expected_sizes.get(self.model_size, 464 * 1024 * 1024)
        cache_dir = Path(_MODEL_DIR) / f"models--Systran--faster-whisper-{self.model_size}"

        while not stop_event.is_set():
            try:
                if cache_dir.exists():
                    # 只统计 blobs/：在不支持符号链接的 Windows 上，
                    # snapshots/ 里会有一份实拷贝，两份都算会让进度显示翻倍
                    blobs = cache_dir / "blobs"
                    if blobs.is_dir():
                        total_size = sum(f.stat().st_size for f in blobs.rglob("*") if f.is_file())
                    else:
                        total_size = sum(f.stat().st_size for f in cache_dir.rglob("*") if f.is_file())
                    progress = min(total_size / expected, 0.99) * 100
                    self._report_progress("downloading", progress)
            except Exception:
                pass
            stop_event.wait(2)

    def _load_model(self):
        """加载 Faster-Whisper 模型（在后台线程中执行）"""
        try:
            from faster_whisper import WhisperModel

            # 启用高速下载
            os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "1")

            # 内置模型种子的**兜底**播种：
            # 正常路径上，后端 lifespan 已在服务启动时后台播种（见 model_bootstrap
            # 的 start_background_seed），用户看到的是首启向导第 3 步「正在复制模型
            # 种子」。这里再调一次是双保险，覆盖三种漏网情形：
            #   · 以其他方式加载引擎（--selftest / 脚本 / 单测），没走 lifespan；
            #   · lifespan 的后台线程还没跑完，用户已提前点了「开始识别」；
            #   · 上次播种中断留下损坏的缓存。
            # ensure_model 自身幂等（先 check_installed，已就绪毫秒级返回），
            # 因此重复调用没有额外拷贝开销；命中时同样能修复损坏缓存（不联网）。
            bootstrap = ensure_model(self.model_size)
            if bootstrap["action"] in ("seeded", "repaired"):
                logger.info(f"已从内置模型播种: {bootstrap['detail']}")
            elif not bootstrap["ok"]:
                logger.info(f"内置模型不可用: {bootstrap['detail']}")

            cached = self._is_model_cached()

            if not cached:
                # 需要下载
                self._report_progress("downloading", 0)
                logger.info(f"模型 {self.model_size} 未缓存，开始下载...")

                # 启动进度监控线程
                monitor_stop = threading.Event()
                monitor = threading.Thread(
                    target=self._monitor_download,
                    args=(monitor_stop,),
                    daemon=True,
                )
                monitor.start()

                try:
                    import huggingface_hub
                    # cache_dir 必须与下面 WhisperModel(download_root=_MODEL_DIR) 一致。
                    # 原实现没传 cache_dir，配合 HF_HOME=_MODEL_DIR 会落到
                    # <root>/hub/models--... ，而加载与缓存检查都看
                    # <root>/models--... ，于是首次启动把同一个模型下了两遍：
                    # 预下载一份（没人用），WhisperModel 再下一份。
                    huggingface_hub.snapshot_download(
                        f"Systran/faster-whisper-{self.model_size}",
                        cache_dir=_MODEL_DIR,
                        allow_patterns=["*.bin", "*.json", "*.txt"],
                    )
                except Exception as e:
                    logger.warning(f"huggingface_hub 预下载失败，将使用 WhisperModel 内置下载: {e}")

                monitor_stop.set()
            else:
                logger.info("模型已缓存，跳过下载")

            self._report_progress("loading", 0)
            logger.info(f"加载 Faster-Whisper 模型: {self.model_size} ({self.device}/{self.compute_type})")

            # 自动选择 compute_type
            ct = self.compute_type
            if self.device == "cpu" and ct == "float16":
                ct = "int8"

            self._model = self._instantiate_model(WhisperModel, ct, cached)

            self._report_progress("ready", 100)
            logger.info("Faster-Whisper 模型加载完成")

        except Exception as e:
            logger.error(f"Faster-Whisper 加载失败: {e}")
            self._model = None
            self._report_progress("error", 0)
        finally:
            self._loading = False

    def _instantiate_model(self, whisper_model_cls, compute_type: str, cached: bool):
        """
        真正构造 WhisperModel

        cached=True 时**强制离线加载**（local_files_only=True）：
        安装包内置了 base 模型，用户首启可能完全没网 —— 此时
        faster-whisper 默认会去 HF 校验元数据，网络不通就直接失败，
        等于把「内置模型」这个卖点浪费了。
        只有离线加载真的报错（例如缓存被改坏）时才回退到联网，
        让它有机会自己修复。
        """
        common = dict(
            device=self.device,
            compute_type=compute_type,
            # num_workers>1 会创建多份模型副本（每份数百 MB 内存），
            # 那是为批量离线转写准备的；流式场景一次只处理一段，用 1 即可
            num_workers=1,
            cpu_threads=self.cpu_threads,
            download_root=_MODEL_DIR,
        )

        if not cached:
            return whisper_model_cls(self.model_size, **common)

        try:
            return whisper_model_cls(self.model_size, local_files_only=True, **common)
        except Exception as exc:
            logger.warning(
                f"离线加载模型失败（{type(exc).__name__}: {exc}），回退联网加载以尝试修复缓存"
            )
            # 再给一次机会：从内置种子修复后重试
            repaired = ensure_model(self.model_size, deep_check=True)
            if repaired["action"] in ("seeded", "repaired"):
                logger.info(f"已用内置模型修复缓存: {repaired['detail']}")
            return whisper_model_cls(self.model_size, **common)

    def add_audio(self, audio: np.ndarray):
        """
        提交一段音频用于识别

        audio: float32 numpy array, 采样率 16000
        由上游 VAD 切出的独立语音片段，自带起止时间。
        """
        if audio is None or len(audio) == 0:
            return

        duration = len(audio) / 16000.0
        with self._lock:
            segment_start = self._total_duration
            self._total_duration += duration

        with self._jobs_cv:
            self._jobs.append((audio.copy(), segment_start, duration))
            # 背压上限：极端情况下（长时间跟不上）也不让队列无限增长。
            # 丢弃最旧的片段而不是最新的 —— 越新的内容对用户越有价值。
            while len(self._jobs) > MAX_PENDING_JOBS:
                self._jobs.popleft()
                self._dropped_jobs += 1
                if self._dropped_jobs % 5 == 1:
                    logger.warning(
                        f"识别积压严重，已丢弃 {self._dropped_jobs} 个片段 "
                        f"（当前模型 {self.model_size}，可考虑换更小的模型）"
                    )
            self._jobs_cv.notify()

        self._ensure_worker()

    def _ensure_worker(self):
        """惰性启动唯一的识别消费者线程"""
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop_worker.clear()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def _worker_loop(self):
        """
        唯一的识别消费者

        取出当前所有待处理片段；多于一个时合并成一次调用。
        合并是有意的降级策略：每次 transcribe 有约 4 秒固定开销
        （Whisper 按 30 秒窗口处理，短输入会被补齐），
        把两段 4 秒合成一次 8 秒调用，比串行两次调用快得多。
        """
        while not self._stop_worker.is_set():
            with self._jobs_cv:
                while not self._jobs and not self._stop_worker.is_set():
                    self._jobs_cv.wait(0.2)
                if self._stop_worker.is_set():
                    return
                if not self._model:
                    # 模型还没加载完：保留队列，等下一轮
                    self._jobs_cv.wait(0.5)
                    continue
                jobs = list(self._jobs)
                self._jobs.clear()
                # _busy 必须与「清空队列」在同一个临界区内置位。
                # 观察者（flush()、以及任何轮询 backlog 的调用方）的判据是
                # 「队列空 **且** 不忙」；若等到下面 _merge_jobs() 跑完再置位，
                # merge 那段时间就是一个「队列已空 + 还不忙」的假空闲窗口 ——
                # flush() 会据此提前返回，于是**停止听课时最后一段字幕被丢掉**。
                # 这正是 flush() 注释里说要修的那个问题，之前只修了 sleep 那一半。
                self._busy = True
                self._jobs_cv.notify_all()

            if len(jobs) > 1:
                self._merged_jobs += 1
                logger.info(f"识别落后，合并 {len(jobs)} 个片段为一次调用（第 {self._merged_jobs} 次）")

            audio, start, duration = self._merge_jobs(jobs)
            try:
                self._transcribe(audio, start, duration, len(jobs))
            finally:
                with self._jobs_cv:
                    self._busy = False
                    self._jobs_cv.notify_all()

    @staticmethod
    def _merge_jobs(jobs: list) -> tuple:
        """
        把多个片段合并成一段音频

        片段之间按时间轴补零，保证 Whisper 输出的时间戳仍对应真实时间，
        否则字幕时间会整体前移、与课件进度对不上。
        """
        if len(jobs) == 1:
            return jobs[0]

        parts: list[np.ndarray] = []
        cursor = jobs[0][1]
        for audio, start, duration in jobs:
            gap = start - cursor
            if gap > 0.05:
                parts.append(np.zeros(int(gap * 16000), dtype=np.float32))
            parts.append(audio)
            cursor = start + duration

        merged = np.concatenate(parts)
        total = sum(d for _, _, d in jobs) + sum(
            max(0.0, jobs[i][1] - (jobs[i - 1][1] + jobs[i - 1][2])) for i in range(1, len(jobs))
        )
        return merged, jobs[0][1], total

    def _transcribe(self, audio: np.ndarray, segment_start: float, duration: float, job_count: int = 1):
        """
        执行语音识别

        job_count：本次调用实际合并了几个输入片段，仅用于重复循环检测
        （见模块顶部 AUTO_DEGRADE_* 的说明）。
        """
        produced = 0   # 本次产出的**非空**字幕片段数（空文本不会变成字幕，不计入）
        try:
            start_time = time.time()

            # 确保音频是 float32 格式
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)

            # 调用 Faster-Whisper
            # V5-1：auto 下先探测语种，再据此一次性决定「按什么语种解码」与
            # 「要不要注入简体引导语」。两件事必须由**同一次决策**驱动 ——
            # 分开判会出现「按 zh 解码但没注引导语」（仍旧繁体）或
            # 「注了引导语却按 auto 解码」这类半修状态，后者更糟：引导语会被送去
            # 解码非中文音频，可能反而拉低识别质量。
            decode_lang, _lang_conf, _lang_source = self._resolve_language(audio)
            self._lang_source = _lang_source
            # 只认 `SIMP_HINT_LANGS` 这一个集合：`decode_lang` 落在里面才注引导语。
            simp = (decode_lang or "").lower() in SIMP_HINT_LANGS

            segments, info = self._model.transcribe(
                audio,
                language=decode_lang,         # None=auto / en / zh / ja / ...
                beam_size=5,
                word_timestamps=True,         # 启用词级时间戳
                vad_filter=False,             # 我们已有 VAD
                vad_parameters=dict(
                    min_silence_duration_ms=200,
                ),
                # 术语热词注入：领域术语 + 课程自定义术语（+ 中文场景的简体引导语）
                initial_prompt=self._build_prompt(simp=simp),
                condition_on_previous_text=True,
                no_speech_threshold=0.4,
            )

            detected_lang = info.language
            logger.debug(
                f"检测语言: {detected_lang}, 概率: {info.language_probability:.2f}"
                f"；解码语种: {decode_lang or 'auto'}，简体引导语: {'on' if simp else 'off'}"
                f"（来源 {_lang_source}）"
            )

            # 处理识别结果
            for segment in segments:
                raw_text = segment.text.strip()

                # 提示词回显清洗，位置是刻意的：必须在**回调之前、`_last_text` 之前**。
                # 静音段上 Whisper 会把 initial_prompt 接着念下去，若让这段回显进了
                # `_last_text`，下一轮的提示词里又会带上它 —— 自我喂食，越滚越多
                # （实测同一个 14s 块里回显 16 次）。详见 asr/prompt_echo.py。
                echo = self._echo.clean(raw_text)
                if echo.isPureEcho:
                    # 整段只有提示词、没有任何语音内容 —— 这不是字幕，是模型的续写。
                    # 丢弃它，且不计入 produced（否则会污染字幕/片段比值告警）。
                    self._log_echo_once()
                    continue
                text = echo.text
                if not text:
                    continue
                if echo.hits:
                    self._log_echo_once()

                words = []
                if segment.words:
                    for w in segment.words:
                        words.append(WordTimestamp(
                            word=w.word.strip(),
                            start=segment_start + w.start,
                            end=segment_start + w.end,
                            confidence=w.probability,
                        ))

                segment_data = SubtitleSegment(
                    text=text,
                    words=words,
                    start=segment_start + segment.start,
                    end=segment_start + segment.end,
                    is_final=True,
                )

                # 回调
                if self.on_partial:
                    self.on_partial(segment_data.text, words)

                # 记录本段文本，供下一轮识别作为连贯性提示
                if segment_data.text:
                    produced += 1
                    self._last_text = segment_data.text
                    # 新判据的输入就在这儿：每产出一条字幕，用**文本自身**判断是否在重复循环
                    self._note_produced_text(segment_data.text)

            elapsed = time.time() - start_time
            rtf = elapsed / max(duration, 0.01)
            logger.debug(f"识别完成: {duration:.1f}s 音频 → {elapsed:.1f}s 处理 (RTF={rtf:.2f})")

        except Exception as e:
            logger.error(f"语音识别错误: {e}")
        finally:
            # 计数放 finally：即使识别抛错也要把「送入量」计入分母，
            # 否则分母长期偏小会让比值虚高、出现假触发。
            self._note_segment_counts(job_count, produced)

    def _note_segment_counts(self, incoming: int, outgoing: int):
        """
        累计「送入片段数 / 产出字幕数」，只用于**辅助告警**

        这两个计数曾是降级判据（`产出/送入 > 1.2`），中文代理集 A/B 证明它在中文下
        误报与漏报并存（推导见 asr/repetition.py 模块开头），所以**降级决定权已交给
        `_note_produced_text()` 的文本重复检测**，这里只保留计数与一条参考日志。
        """
        self._stat_in_segments += incoming
        self._stat_out_segments += outgoing

        if self._advisory_logged or self._stat_in_segments < 1:
            return
        adv = repetition.subtitle_ratio_advisory(self._stat_out_segments, self._stat_in_segments)
        if adv["aboveAdvisory"]:
            self._advisory_logged = True
            logger.info(
                f"[perf] 辅助指标：字幕/片段 = {adv['ratio']:.3f} 已越过 {adv['threshold']}"
                f"（{adv['note']}）"
            )

    def _log_echo_once(self):
        """提示词回显第一次命中时留痕（埋点，只记一次，避免刷日志）。

        为什么要记：这类清洗最容易变成「看起来在跑、其实一条都没匹配到」的静默失效。
        要么在第一次命中时就说明「确实在生效」，要么在建构造时就说明「为什么没生效」
        （见 EchoCleaner.inertReason）。
        """
        if self._echo_logged:
            return
        self._echo_logged = True
        logger.info(
            f"[echo] 检测到提示词回显，已启用清洗："
            f"字面量 {len(self._echo.blacklist)} 条，示例 {self._echo.blacklist[:2]}"
        )

    def echo_stats(self) -> dict:
        """回显清洗的累计埋点（供草稿/评估脚本汇总上报）。"""
        stats = self._echo.stats.asDict()
        stats["enabled"] = self._echo.enabled
        stats["inertReason"] = self._echo.inertReason
        stats["blacklistSize"] = len(self._echo.blacklist)
        return stats

    def _prompt_cost_tokens(self) -> int:
        """
        当前术语提示词占多少 token（用于判断「降无可降」）

        有分层信息时按术语逐项累计（这才是真正要送去模型的内容）；
        只有字符串时按整串计。
        """
        count, _ = prompt_budget.make_counter(self._tokenizer())
        if self._prompt_sections:
            return sum(
                count(t) + 1
                for _name, terms in self._prompt_sections
                for t in (terms or [])
            )
        return count(self.initial_prompt or "")

    def _note_produced_text(self, text: str):
        """
        每产出一条字幕就用**文本自我重复检测**判断是否落入重复循环（新判据）

        为什么换掉旧的比值判据、为什么只在尾部窗口里扫、阈值怎么标定的，
        全部在 `asr/repetition.py` 的模块注释里（含两轮 6 样本的实测表）。
        这里只负责：滚动缓冲 → 检测 → 命中则缩短术语提示词。

        每产出一条字幕调一次，单次开销 O(窗口) = O(160)，可忽略。
        """
        if not text:
            return
        self._recent_text = (self._recent_text + text)[-REPETITION_BUFFER_CHARS:]

        # 结论无论开关如何都要算：关闭保护时也要能在 get_stats() 里看到
        # `lastRepeat`，否则「关了保护之后到底有没有复读」无法观测。
        verdict = repetition.detect(self._recent_text)
        self._last_verdict = verdict
        if not verdict.triggered:
            return

        # 检测到复读就计数，**与是否降级无关**。
        #
        # 这里曾经只在「真的执行了降级」时 +1，于是关闭降级的条件下
        # `repetitionEvents` 恒为 0 —— 2026-10-06 的 A/B 里 `on-noguard` 就是
        # 这样漏掉了一条 148 字的真实复读（离线拿全文复核才发现），
        # 差点把「判据漏报」和「埋点没记」搞混。
        self._repetition_events += 1

        if not self._auto_degrade or self._prompt_degraded:
            return

        # 降级改的是**预算**（见 _build_prompt），不再按字符砍字符串 ——
        # 按字符砍尾巴等于砍掉优先级最高的术语，那是 P0-3 的老毛病。
        cost = self._prompt_cost_tokens()
        if cost <= DEGRADE_TOKEN_BUDGET:
            return          # 本来就短，降无可降

        self._degrade_events += 1
        self._prompt_original = self.initial_prompt
        self._prompt_original_sections = self._prompt_sections
        self._prompt_degraded = True
        logger.warning(
            f"[WARN] 检测到字幕重复循环，自动缩减术语提示词预算: "
            f"{cost} → {DEGRADE_TOKEN_BUDGET} token。"
            f"{verdict.summary}。"
            "设 TINGKEBANLV_TERM_AUTODEGRADE=0 可关闭该保护"
        )

    def flush(self, timeout: float = 8.0):
        """
        等待队列中的识别任务处理完

        原实现只是 sleep(0.5)，无论队列里还有多少任务都会「假装等完了」，
        停止听课时就可能丢掉最后几段字幕。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._jobs_cv:
                if not self._jobs and not self._busy:
                    return
                self._jobs_cv.wait(0.1)
        with self._jobs_cv:
            remaining = len(self._jobs)
        if remaining:
            logger.warning(f"flush 超时，仍有 {remaining} 个片段未识别")

    def set_initial_prompt(
        self,
        prompt: str,
        sections: Optional[list[tuple[str, list[str]]]] = None,
    ):
        """
        运行时更新术语热词（用户中途添加自定义术语时可热更新，无需重启管道）

        注意：只在下一轮识别生效，不影响正在进行中的识别。

        `sections` 是分层术语（高 → 低优先级），来自
        `dictionary.terminology.build_prompt_sections()`。**强烈建议传它** ——
        引擎只有拿到分层信息才能按 token 预算重排（P0-3）。

        若已因重复循环降级过，则**保持降级**：`pipeline.update_terminology()`
        每次热更新都会推完整提示词，若在这里恢复成完整版，就会变成
        「恢复 → 再次触发循环 → 再降级」的来回抖动，比一直保持降级更糟。
        要恢复请显式调用 `restore_full_prompt()`。
        """
        new_prompt = (prompt or "").strip() or None
        new_sections = list(sections) if sections else None

        if self._prompt_degraded and (new_prompt or new_sections):
            self._prompt_original = new_prompt
            self._prompt_original_sections = new_sections
            logger.debug(
                f"术语提示词处于降级保护中，继续使用降级预算 "
                f"（本次收到 {len(new_prompt or '')} 字符，已留存备用）"
            )
            return

        changed = new_prompt != self.initial_prompt or new_sections != self._prompt_sections
        self.initial_prompt = new_prompt
        self._prompt_sections = new_sections
        if changed:
            logger.info(
                f"术语热词已更新 ({len(new_prompt or '')} 字符"
                + (f"，{sum(len(terms) for _n, terms in (new_sections or []))} 个分层术语"
                   if new_sections else "")
                + ")"
            )

    def set_auto_degrade(self, enabled: bool):
        """
        运行时开关「术语提示词自动降级」（对应设置面板同名开关，默认开启）

        关闭只影响**之后**的检测；已降级过的提示词不会自动恢复 ——
        要恢复请显式调用 `restore_full_prompt()`，理由同上：自动恢复会来回抖动。
        """
        self._auto_degrade = bool(enabled)
        logger.info(f"术语提示词自动降级已{'开启' if self._auto_degrade else '关闭'}")

    def restore_full_prompt(self):
        """
        解除降级保护，恢复被缩短前的完整提示词（换课程 / 重开管道时调用）

        计数与最近文本一并清零，重新观察若干片段后再决定是否需要再次降级。
        """
        if self._prompt_original:
            self.initial_prompt = self._prompt_original
        if self._prompt_original_sections:
            self._prompt_sections = self._prompt_original_sections
        self._prompt_original = None
        self._prompt_original_sections = None
        self._prompt_degraded = False
        self._stat_in_segments = 0
        self._stat_out_segments = 0
        self._recent_text = ""
        self._last_verdict = None
        self._advisory_logged = False
        logger.info(
            f"术语提示词已恢复完整版本 ({len(self.initial_prompt or '')} 字符 / "
            f"{self._prompt_cost_tokens()} token)"
        )

    def _tokenizer(self):
        """
        取真 tokenizer 用于**精确** token 计数（模型已加载时）

        `WhisperModel.hf_tokenizer` 与 faster-whisper 内部 `get_prompt()` 编码
        initial_prompt 用的是**同一个词表**，所以计数口径与库的截断判定一致。
        模型未就绪（或对象上没有该属性）时返回 None → 退回启发式估算。
        """
        model = self._model
        if model is None:
            return None
        return getattr(model, "hf_tokenizer", None)

    # ---- V5-1 / V5-2 / V5-4：语种决策 ----

    def _hint_possible(self) -> bool:
        """本引擎**是否有可能**注入简体引导语（构造 EchoCleaner 用）

        与 `_build_prompt()` 的注入判据同源：显式中文 ⇒ 一定注入；auto + 简体强制
        ⇒ 探测判为中文时注入。两处判据分家的后果是「清洗器以为不会注入、实际注入了」
        ⇒ 回显漏网（见 `__init__` 里的 🔴 注释）。
        """
        lang = (self.language or "").lower()
        if lang in SIMP_HINT_LANGS:
            return True
        return (
            lang == "auto"
            and LANG_DETECT_ENABLED
            and self.simp_mode == SIMP_MODE_AUTO_SIMPLIFIED
        )

    def _resolve_language(self, audio: np.ndarray) -> tuple[Optional[str], float, str]:
        """决定**本次调用**的解码语种（V5-1 / V5-2）

        返回 `(decode_lang, confidence, source)`：`decode_lang=None` 表示按 auto 解码
        （不强制语种）；非 None 表示要显式按该语种解码，且它落在 `SIMP_HINT_LANGS`
        里时 `_build_prompt()` 会注入简体引导语。

        四种来源：

        * `explicit`  —— 用户显式指定了语种（`language != "auto"`），不探测、不打折；
        * `disabled`  —— V5-4 开关关掉了简体强制 / 探测被 env 关掉 ⇒ 退回修复前行为；
        * `sticky`    —— 本会话已锁定，直接复用（稳态零探测开销，同时避免逐块翻转
                          导致字幕在简繁/语种之间来回抖 —— 原 V5-3 的真正风险点，
                          它描述的「切在词语中间」在按段处理下并不存在，见
                          `docs/m3/backlog_v350.md` V5-3 的替换说明）；
        * `detected` / `non-target` / `low-confidence` / `detect-failed` —— 本轮探测结果。
        """
        configured = (self.language or "auto").lower()
        if configured != "auto":
            return self.language, 1.0, "explicit"

        if not LANG_DETECT_ENABLED or self.simp_mode == SIMP_MODE_RAW:
            return None, 0.0, "disabled"

        if self._lang_decided:
            return self._lang_decision, self._lang_confidence, "sticky"

        if self._model is None:
            return None, 0.0, "model-not-ready"

        self._lang_detect_events += 1
        try:
            # faster-whisper 各版本返回值形状不一（`(lang, prob)` / `(lang, prob, all)`），
            # 按下标取前两项，避免把版本差异变成硬依赖。
            info = self._model.detect_language(audio)
        except Exception as e:                      # noqa: BLE001 —— 探测失败不能拖垮识别
            logger.warning(f"语种探测失败，本会话退回 auto 行为：{e}")
            return None, 0.0, "detect-failed"

        lang = (info[0] or "").lower() if info else ""
        try:
            conf = float(info[1]) if info and len(info) > 1 else 0.0
        except (TypeError, ValueError):
            conf = 0.0
        self._lang_confidence = conf

        if conf < LANG_MIN_CONFIDENCE:
            # V5-2 的降级分支：判不准就什么都别做。这里**不锁定**——下一块接着探，
            # 因为「这一块是静音/噪声」并不代表整场课判不准。
            self._lang_low_conf_events += 1
            self._lang_source = "low-confidence"
            logger.info(
                f"[lang] 探测结果 {lang or '未知'} 置信 {conf:.2f} < "
                f"{LANG_MIN_CONFIDENCE} ⇒ 降级为不强制简体（V5-2）"
            )
            return None, conf, "low-confidence"

        if lang in SIMP_HINT_LANGS:
            self._lang_decided = True
            self._lang_decision = lang
            self._lang_source = "detected"
            logger.info(
                f"[lang] auto 判定为 {lang}（置信 {conf:.2f}）"
                f"⇒ 本会话启用简体引导语（V5-1）"
            )
            # V5-8：语种定了 ⇒ 通知上层用**这个语种**重建术语层。
            # 调用点在这里（而不是 `_transcribe` 之后）是有意的：回调会同步更新
            # `_prompt_sections`，于是紧接着的 `_build_prompt()` 就已经用上新分层 ——
            # 被判为中文的**第一块**就拿不到英文短语层，而不是从第二块才开始修。
            self._notify_language_decided(lang)
            return lang, conf, "detected"

        # 非目标语种：要连续观察到 `LANG_NON_TARGET_LOCK_AFTER` 次才锁定，
        # 免得中文课开头的英文片头把整个会话锁成「不注引导语」。
        self._lang_non_target_streak += 1
        if self._lang_non_target_streak >= LANG_NON_TARGET_LOCK_AFTER:
            self._lang_decided = True
            self._lang_decision = None
            self._lang_source = "non-target"
            logger.info(
                f"[lang] auto 判定为 {lang}（置信 {conf:.2f}）"
                f"，连续 {self._lang_non_target_streak} 次 ⇒ 本会话不注入简体引导语"
            )
            return None, conf, "non-target"

        self._lang_source = "non-target-pending"
        return None, conf, "non-target-pending"

    def set_simp_mode(self, mode: str):
        """切换简繁输出开关（V5-4）。切换会**清掉已锁定的语种决策**——
        否则「关掉开关」后本会话仍会沿用之前的简体决策，表现为开关不生效。
        """
        self.simp_mode = normalize_simp_mode(mode)
        self.reset_language_decision()

    def _notify_language_decided(self, language: str) -> None:
        """auto 下判定出目标语种后一次性通知上层（V5-8）

        ## 为什么要这一跳

        `build_prompt_sections(language="auto")` 会产出「词表短语层」，而该层由
        `basic_vocab.json` 里**含空格的词**筛出 —— 即**只有英文多词短语**。中文课
        拿到它是纯干扰：实测 `hypUnits` 375→487（多吐 112 字乱码）、CER +0.2876
        （+65.7%）。关掉它对中文语种本就有现成开关（`_PHRASES_LAYER_EXCLUDED_LANGS`），
        只是 auto 时判不了语种、只能走旧行为。

        V5-1 让引擎在**解码前**就拿到了语种 ⇒ 这个「判不了」的前提消失了（见
        `docs/m3/backlog_v350.md` §十二 V5-8 立案）。

        ## 为什么由引擎发、由上层重建

        引擎不知道术语分了几层、哪层该有内容 —— 那是 `dictionary.terminology` 的
        知识。引擎只负责把「语种已经定了」这件事告诉上层，让上层用它重建。

        ## 三个必须守住的点

        1. **只发一次**。每块都重建会让提示词反复变化，而
           `condition_on_previous_text=True` 的连贯性条件会跨这次变化抖动。
        2. **异常必须吞掉**。回调里要做的是重建术语层（读词表、重排 token 预算），
           它失败只该让「短语层没关掉」，绝不能让识别本身崩掉。
        3. **`language` 为空时不发**。宁可不触发，也不要让上层拿着空串去重建。
        """
        if self.on_language_decided is None:
            return
        if self._lang_decided_notified:
            return
        if not (language or "").strip():
            return
        self._lang_decided_notified = True
        try:
            self.on_language_decided(language)
        except Exception as e:                      # noqa: BLE001 —— 见 docstring 第 2 点
            logger.warning(f"语种决策回调失败（不影响识别）：{e}")

    def reset_language_decision(self):
        """清掉会话级语种粘滞，让下一次识别重新探测（换课程/换素材时调用）。"""
        self._lang_decided = False
        self._lang_decision = None
        self._lang_confidence = 0.0
        self._lang_source = "unset"
        self._lang_non_target_streak = 0
        # V5-8：重新探测后要能重新通知，否则「换到中文课」时术语层还是旧语种建的
        self._lang_decided_notified = False

    def _build_prompt(self, simp: Optional[bool] = None) -> Optional[str]:
        """
        组装提示词：术语（按 token 预算排优先级）+ 上一段文本 + 简体引导语

        这里有三个坑，每一个都实测踩过：

        1. **总预算必须按 token 算，不能按字符算。**
           faster-whisper 的 `get_prompt()` 只保留 `previous_tokens[-223:]`
           （`max_length // 2 - 1`），超出部分**静默丢弃**。中文每字 ≈ 1.35 token，
           英文每字符 ≈ 0.30 token —— 同样的「800 字符」上限，中文吃掉的 token 是
           英文的 4.5 倍。实测中文术语表 799 字符 = 506 token，被砍掉 283（56%）。

        2. **高优先级必须贴尾。** 库保留的是尾部，所以「谁在末尾谁存活」。
           旧实现是 `custom → discovered → domain → phrases`（高优先级在前），
           结果自定义中文术语存活率 25%、内置英文术语 100% —— 完全颠倒。

        3. **简体引导语必须在最末尾。** 它决定繁体占比是 34% 还是 0.1%（P0-1），
           放开头或中间会被第 1 条的静默截断吃掉。

        实现见 `asr/prompt_budget.py`：按高→低分配预算，按低→高拼接。

        `simp` 是**本次调用是否注入简体引导语**，由 `_resolve_language()` 的决策驱动
        （V5-1）。默认 `None` = 退回按 `self.language` 判断的老行为，便于老调用点与
        既有单测不受影响。注意新代码不要再传 `None` —— auto 下这样会漏注引导语，
        正是缺陷 D1 的成因。
        """
        count, _exact = prompt_budget.make_counter(self._tokenizer())

        # 中文才注入简体引导语；其它语种简繁不分，加了纯属浪费 token 预算。
        # auto 下 `self.language` 恒为 "auto"，单看它永远判不出中文 —— 必须用
        # `_resolve_language()` 探测出来的**有效语种**，见 V5-1 注释块。
        if simp is None:
            simp = (self.language or "").lower() in SIMP_HINT_LANGS
        hint = get_simp_hint() if simp else ""

        # 降级后砍的是**预算**而不是字符串 —— 这样高优先级术语天然排在前面，
        # 不会像旧实现那样按字符砍尾巴（= 砍掉最该保住的那些词）。
        budget = DEGRADE_TOKEN_BUDGET if self._prompt_degraded else PROMPT_TOKEN_BUDGET

        sections = self._prompt_sections
        prebuilt = ""
        if not sections and self.initial_prompt:
            # 退化路径：上游只给了拼好的字符串。`build_initial_prompt()` 的输出
            # 已是「低 → 高」顺序，所以**原样保留**，绝不能在这里再排一次。
            prebuilt = self.initial_prompt

        if not sections and not prebuilt and not hint and not self._last_text:
            self._last_budget = None
            return None

        result = prompt_budget.assemble(
            sections,
            prev_text=self._last_text or "",
            hint=hint,
            budget=budget,
            count=count,
            prebuilt_body=prebuilt,
            prev_max_tokens=PROMPT_PREV_TOKENS,
        )
        self._last_budget = result

        if result.dropped:
            # 只在真的有术语被丢时记一行（每次识别都刷会淹掉日志）
            logger.debug(f"[prompt] {result.summary()}；被丢术语：{result.dropped[:8]}")

        return result.body

    def stop(self):
        """停止引擎：先把手头积压的片段识别完，再收掉消费者线程"""
        self._running = False
        self.flush(timeout=10.0)
        self._stop_worker.set()
        with self._jobs_cv:
            self._jobs.clear()
            self._jobs_cv.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=3)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    def get_stats(self) -> dict:
        """
        运行时统计（供 /api/health 与排障）

        重点看 backlog 与 merged：
          - backlog 持续 > 0 → 识别跟不上音频速度
          - merged > 0       → 已经进入「合并片段」的降级状态，字幕会更粗
          - dropped > 0      → 已开始丢内容，必须减小模型或调大 cpu_threads
          - termPromptDegraded=true → 检测到过字幕重复循环并已自动缩短术语提示词

        P0-3 起 `promptChars` 不再是唯一指标 —— 真正决定「有多少内容进了模型」的是
        `promptTokens`（faster-whisper 只保留末尾 223 token）。若
        `promptTokens` 接近 `promptBudget` 且 `droppedTerms` 非空，
        说明这个课程的术语表已经超出预算，丢的是最低优先级的那些。
        """
        with self._jobs_cv:
            backlog = len(self._jobs)
            busy = self._busy
        b = self._last_budget          # 最近一次提示词组装的预算结果（P0-3 埋点）
        return {
            "model": self.model_size,
            "device": self.device,
            "computeType": self.compute_type,
            "cpuThreads": self.cpu_threads,
            "backlog": backlog,
            "busy": busy,
            "mergedCalls": self._merged_jobs,
            "droppedSegments": self._dropped_jobs,
            "audioSeconds": round(self._total_duration, 1),
            # ---- 术语提示词健康度（供设置面板展示降级状态）----
            "termPromptChars": len(self.initial_prompt or ""),
            "termPromptDegraded": self._prompt_degraded,
            "termAutoDegrade": self._auto_degrade,
            "repetitionDetected": self._repetition_events > 0,
            # 次数而非仅布尔：评估脚本要判断「是否触发降级告警」，
            # 以及一次运行里是偶发一次还是持续触发（后者说明术语表本身就过长）
            "repetitionEvents": self._repetition_events,
            # 真正执行降级的次数。`repetitionEvents` 统计的是**检出**次数，
            # 关闭自动降级时它仍会增长（否则就是观测盲区，见 _note_produced_text）
            "degradeEvents": self._degrade_events,
            # 最近一次文本重复检测的证据摘要（新判据）。关闭自动降级时依然有值，
            # 这样「用户关了保护之后到底有没有复读」是可观测的，而不是只能靠猜。
            "lastRepeat": (
                self._last_verdict.summary if self._last_verdict is not None else None
            ),
            # ---- P0-3：提示词 token 预算埋点（原先只有字符数，看不出被库截断多少）----
            # `promptTokens` 才是真正送进模型的分量；`promptBudget` 是本次预算
            # （降级后会用更小的预算）；`dedTerms` 是被预算丢掉的术语。
            "promptTokens": b.used_tokens if b else 0,
            "promptBudget": b.budget if b else PROMPT_TOKEN_BUDGET,
            "promptRemaining": b.remaining_tokens if b else 0,
            "promptTokenExact": b.exact if b else False,
            "droppedTerms": list(b.dropped) if b else [],
            "promptSections": (
                {k: len(v) for k, v in b.kept.items()} if b else {}
            ),
            "promptPrevChars": len(b.prev_kept) if b else 0,
            "inSegments": self._stat_in_segments,
            "outSegments": self._stat_out_segments,
            # ---- V5-1/V5-2/V5-4：语种决策与简体强制（供评估脚本与排障）----
            # 为什么要把这几项暴露出来：简体强制是**按会话锁定**的，一旦锁错
            # （例如被英文片头锁成非目标语种），用户在界面上只会看到「还是繁体」，
            # 却无从判断是「开关关了」「置信不足降级」还是「锁错了语种」。
            "simpMode": self.simp_mode,
            "languageDecision": self._lang_decision,       # None = 已判定为非目标语种
            "languageDecided": self._lang_decided,
            "languageConfidence": round(self._lang_confidence, 4),
            "languageSource": self._lang_source,
            "langDetectEvents": self._lang_detect_events,
            "langLowConfidenceEvents": self._lang_low_conf_events,
            # V5-8：语种决策有没有真的通知到上层。为 False 时「术语层仍是按 auto
            # 建的」—— 中文课会带着英文短语层跑，CER 差 0.29，而这一点从
            # languageSource=detected 看不出来（决策对了，只是没通知出去）。
            "langDecidedNotified": self._lang_decided_notified,
        }

