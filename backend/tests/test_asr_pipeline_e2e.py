"""
ASR 链路集成测试

这里的每个用例都对应一个**实际存在过的缺陷**，属于回归测试：
    1. VAD 窗口对齐 —— CHUNK_SIZE=480 而 Silero 要求恰好 512，导致 VAD 从未生效
    2. 音频回调 —— pyaudio 只在函数内 import，回调每次抛 NameError
    3. 消息队列 —— list 的 copy()+clear() 竞态会永久丢失字幕
    4. 术语入库 —— lambda 包裹 async 函数，协程被丢弃，生词本永远为空
    5. 回环检测 —— 装了 PyAudioWPatch 时 list_devices 恒返回空
    6. 端到端 —— 文件音频 → VAD → Whisper → 字幕（需模型与音频，缺失时自动跳过）

跑法：
    cd backend && PYTHONPATH=. python -m pytest tests/test_asr_pipeline_e2e.py -v

端到端用例需要 faster-whisper 模型，通过环境变量指定模型目录：
    TINGKEBANLV_TEST_MODEL_DIR=/path/to/whisper_models   # 内含 models--Systran--faster-whisper-<size>
"""

import asyncio
import os
import sys
import threading
import time
import wave
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

FIXTURE_WAV = Path(__file__).parent / "fixtures" / "jfk.wav"


# ============ 工具 ============

def load_fixture_audio() -> np.ndarray:
    with wave.open(str(FIXTURE_WAV), "rb") as w:
        assert w.getframerate() == 16000, "fixture 必须是 16kHz"
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def synth_speech_like(duration: float = 6.0) -> np.ndarray:
    """合成一段「有语音段也有静音段」的信号，用于不依赖 fixture 的分段测试"""
    sr = 16000
    n = int(sr * duration)
    t = np.arange(n) / sr
    signal = np.zeros(n, dtype=np.float32)
    # 0.5~2.5s 与 3.5~5.5s 两段"语音"（带调制的谐波，能量足够）
    for start, end in ((0.5, 2.5), (3.5, 5.5)):
        a, b = int(start * sr), int(end * sr)
        seg_t = t[a:b]
        tone = (0.4 * np.sin(2 * np.pi * 220 * seg_t)
                + 0.2 * np.sin(2 * np.pi * 440 * seg_t))
        env = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * seg_t)
        signal[a:b] = (tone * env).astype(np.float32)
    return signal


# ============ 1. VAD 窗口对齐 ============

class TestVadWindowAlignment:
    """CHUNK_SIZE 曾经是 480，而 Silero VAD 要求恰好 512 样本，
    于是 _detect_silero 每次抛 ValueError 并被 except 吞掉，
    全项目实际一直跑在能量检测上。"""

    def test_vad_window_is_512(self):
        from audio.vad import VAD_WINDOW
        assert VAD_WINDOW == 512, "Silero VAD(16kHz) 只接受恰好 512 样本"

    def test_capture_chunk_matches_vad_window(self):
        from audio.capture import CHUNK_SIZE
        # 捕获块经过重采样后应能整除 VAD 窗口，避免每次都有残余样本
        assert CHUNK_SIZE == 512

    def test_vad_silero_accepts_exact_window(self):
        """若本机装了 silero-vad，确认 512 样本可用、480 样本不可用"""
        pytest.importorskip("silero_vad")
        import torch
        from silero_vad import load_silero_vad

        model = load_silero_vad(onnx=True)
        model.reset_states()
        model(torch.zeros(512, dtype=torch.float32), 16000)  # 不应抛异常

        model.reset_states()
        with pytest.raises(Exception):
            model(torch.zeros(480, dtype=torch.float32), 16000)

    def test_segmentation_is_chunk_size_independent(self):
        """上游块大小任意时，切分结果必须一致（内部按 512 对齐）"""
        from audio.vad import VoiceActivityDetector

        signal = synth_speech_like(6.0)
        results = {}
        for chunk_size in (512, 777, 1024, 4096):
            segs = []
            vad = VoiceActivityDetector(
                on_speech_segment=lambda a, s, e: segs.append((len(a), round(s, 2), round(e, 2)))
            )
            for i in range(0, len(signal), chunk_size):
                vad.process_chunk(signal[i:i + chunk_size])
            vad.flush()
            results[chunk_size] = segs

        baseline = results[512]
        assert baseline, "没有切出任何片段，合成信号能量可能不足"
        for size, segs in results.items():
            assert segs == baseline, f"块大小 {size} 的切分结果与 512 不一致：{segs} vs {baseline}"

    def test_vad_reports_backend(self):
        """必须能查出实际生效的后端，否则无法发现「静默降级」"""
        from audio.vad import VoiceActivityDetector
        vad = VoiceActivityDetector(on_speech_segment=lambda a, s, e: None)
        assert vad.backend in ("silero-onnx", "silero-jit", "energy")
        if vad.is_model_loaded:
            assert vad.backend.startswith("silero")

    def test_short_fragment_is_dropped(self):
        """低于最小片段长度的语音不提交识别（避免用 2 秒片段去换 RTF 2.8）"""
        from audio.vad import VoiceActivityDetector, MIN_SPEECH_DURATION
        segs = []
        vad = VoiceActivityDetector(on_speech_segment=lambda a, s, e: segs.append(e - s))
        # 只有 0.2s 的"语音"，远低于 MIN_SPEECH_DURATION
        burst = np.ones(int(16000 * 0.2), dtype=np.float32) * 0.5
        vad.process_chunk(burst)
        vad.process_chunk(np.zeros(int(16000 * 2.0), dtype=np.float32))
        vad.flush()
        assert all(d >= MIN_SPEECH_DURATION for d in segs), f"提交了过短片段: {segs}"


# ============ 2. 音频回调 ============

class TestAudioCallback:
    """回调里写 pyaudio.paContinue，但 pyaudio 只在 _capture_loop 内部 import，
    模块级没有 → 每次回调都抛 NameError（原代码用 # noqa: F821 把告警压掉了）。"""

    def test_module_level_continue_flag_exists(self):
        import audio.capture as cap
        assert isinstance(cap.PA_CONTINUE, int)

    def test_callback_returns_without_raising(self):
        import audio.capture as cap

        received = []
        capture = cap.AudioCapture(on_audio_chunk=lambda a: received.append(a))
        ret = capture._audio_callback(np.zeros(512, dtype=np.int16).tobytes(), 512, None, 0)

        assert isinstance(ret, tuple) and len(ret) == 2
        assert ret[1] == cap.PA_CONTINUE
        assert received and len(received[0]) == 512

    def test_callback_converts_channels_and_rate(self):
        """立体声 + 非 16k 采样率应被下混并重采样到 16k"""
        import audio.capture as cap

        received = []
        capture = cap.AudioCapture(on_audio_chunk=lambda a: received.append(a))
        capture._native_rate = 48000
        capture._in_channels = 2

        stereo = np.zeros((1536, 2), dtype=np.int16)
        stereo[:, 0] = 16000
        stereo[:, 1] = 16000
        capture._audio_callback(stereo.tobytes(), 1536, None, 0)

        assert received, "回调没有产出音频"
        out = received[0]
        assert len(out) == 512, f"48000→16000 应得到 512 样本，实际 {len(out)}"
        assert out.max() > 0.4, "声道下混后幅度异常"

    def test_callback_swallows_internal_errors(self):
        """回调内任何异常都不能冒出去 —— PyAudio 会因此中止整个音频流"""
        import audio.capture as cap

        def boom(_audio):
            raise RuntimeError("上层处理炸了")

        capture = cap.AudioCapture(on_audio_chunk=boom)
        # 不应抛出
        capture._audio_callback(np.zeros(512, dtype=np.int16).tobytes(), 512, None, 0)


# ============ 3. 消息队列 ============

class TestMessageQueue:
    """原实现 poll 里 queue.copy() 后 queue.clear()，
    两步之间到达的消息同时不在副本也不在列表里 → 永久丢失；
    队列超限时 `queue = queue[-50:]` 重新绑定对象，工作线程会继续写孤儿列表。"""

    def test_no_message_loss_when_consumer_keeps_up(self):
        import api.routes as routes

        cid = "queue-race-test"
        routes.asr_message_queues[cid] = deque()
        enqueue = routes._make_enqueue(cid)

        old_max = routes.MAX_QUEUE_MESSAGES
        routes.MAX_QUEUE_MESSAGES = 10 ** 9   # 本用例只验证「不因竞态丢消息」
        try:
            total = 4000
            received = []
            produced_done = threading.Event()

            def producer():
                for i in range(total):
                    enqueue("subtitle", {"i": i})
                produced_done.set()

            def consumer():
                while not produced_done.is_set():
                    queue = routes.asr_message_queues[cid]
                    while queue:
                        try:
                            received.append(queue.popleft())
                        except IndexError:
                            break

            tp = threading.Thread(target=producer)
            tc = threading.Thread(target=consumer)
            tp.start(); tc.start()
            tp.join(); tc.join()

            queue = routes.asr_message_queues[cid]
            while queue:
                received.append(queue.popleft())

            assert len(received) == total, f"期望 {total} 条，实际收到 {len(received)} 条（发生丢失）"
            assert [m["data"]["i"] for m in received] == list(range(total)), "消息顺序错乱"
        finally:
            routes.MAX_QUEUE_MESSAGES = old_max
            routes.asr_message_queues.pop(cid, None)

    def test_overflow_drops_oldest_not_newest(self):
        import api.routes as routes

        cid = "queue-cap-test"
        routes.asr_message_queues[cid] = deque()
        enqueue = routes._make_enqueue(cid)

        old_max = routes.MAX_QUEUE_MESSAGES
        routes.MAX_QUEUE_MESSAGES = 50
        try:
            for i in range(500):
                enqueue("subtitle", {"i": i})
            queue = routes.asr_message_queues[cid]
            assert len(queue) <= 50
            # 保留的应该是最新的内容，而不是最旧的
            assert queue[-1]["data"]["i"] == 499
            assert queue[0]["data"]["i"] > 400
        finally:
            routes.MAX_QUEUE_MESSAGES = old_max
            routes.asr_message_queues.pop(cid, None)

    def test_enqueue_after_stop_is_noop(self):
        import api.routes as routes
        enqueue = routes._make_enqueue("not-exist-course")
        enqueue("subtitle", {"x": 1})   # 不应抛异常


# ============ 4. 术语入库 ============

class TestTermPersistence:
    """on_term_extracted 写成 lambda term: _handle_term(term, course_id)，
    而 _handle_term 是 async def → 只得到协程对象并丢弃，
    db.upsert_vocabulary 永不执行（伴随 "coroutine was never awaited" 警告）。"""

    def test_handler_dispatches_coroutine_to_event_loop(self):
        import api.routes as routes

        calls = []

        async def fake_upsert(payload):
            calls.append(payload)

        old_db = routes.db
        mock_db = MagicMock()
        mock_db.upsert_vocabulary = fake_upsert
        routes.db = mock_db
        try:
            async def scenario():
                loop = asyncio.get_running_loop()
                handler = routes._make_term_handler("course-1", loop)
                # 从没有事件循环的工作线程调用，模拟 asr/pipeline.py 的真实场景
                worker = threading.Thread(
                    target=handler,
                    args=({"word": "convolution", "translation": "卷积"},),
                )
                worker.start()
                worker.join()
                for _ in range(100):
                    if calls:
                        break
                    await asyncio.sleep(0.02)

            asyncio.run(scenario())
        finally:
            routes.db = old_db

        assert calls, "术语协程没有被投递到事件循环执行 → 生词本会一直是空的"
        assert calls[0]["word"] == "convolution"

    def test_naive_lambda_would_lose_coroutine(self):
        """反证：不加 await 的写法确实不会执行（说明上面的回归测试有意义）"""
        import warnings

        import api.routes as routes

        calls = []

        async def fake_upsert(payload):
            calls.append(payload)

        old_db = routes.db
        mock_db = MagicMock()
        mock_db.upsert_vocabulary = fake_upsert
        routes.db = mock_db
        try:
            async def scenario():
                # 这就是原写法：同步调用 async 函数，返回的协程无人接收
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    routes._handle_term({"word": "x"}, "course-1")
                # 给当前事件循环充分机会去执行任何被调度的工作
                for _ in range(10):
                    await asyncio.sleep(0.02)

            asyncio.run(scenario())
        finally:
            routes.db = old_db

        assert not calls, "预期不该写入（若写入了说明 _handle_term 已不是 async）"


# ============ 5. 回环设备检测 ============

class TestLoopbackDetection:
    def test_check_loopback_endpoint(self):
        """装了 PyAudioWPatch 时 list_devices 必须能返回设备（原来恒为空）"""
        fastapi = pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        import main as backend_main

        with TestClient(backend_main.app) as client:
            resp = client.get("/api/audio/check-loopback")
        assert resp.status_code == 200
        body = resp.json()
        assert "hasLoopback" in body
        assert "audioBackend" in body
        # 本机存在 WASAPI 回环设备时，必须能被识别出来
        if body.get("audioBackend") == "pyaudiowpatch":
            assert body["supportsLoopbackApi"] is True

    def test_list_devices_returns_isloopback_flag(self):
        import audio.capture as cap
        devices = cap.AudioCapture.list_devices()
        if not devices:
            pytest.skip("本机无可用音频设备或未安装音频库")
        assert all("isLoopback" in d for d in devices)


# ============ 6. 背压 ============

def _bare_engine(
    initial_prompt: str | None = None,
    auto_degrade: bool = True,
    sections: list | None = None,
    language: str = "en",
):
    """
    构造一个**不加载模型**的引擎实例，供队列 / 收尾 / 重复循环防护的单元测试使用

    `WhisperEngine.__new__` 会绕过 `__init__`，所以这里必须手工补齐所有实例字段。
    新增字段时要同步加在这里，否则测试会在用到时抛 AttributeError。
    """
    from asr.prompt_echo import EchoCleaner
    from asr.whisper_engine import (
        SIMP_HINT_LANGS,
        SIMP_MODE_AUTO_SIMPLIFIED,
        WhisperEngine,
        get_simp_hint,
    )

    engine = WhisperEngine.__new__(WhisperEngine)   # 不触发模型加载
    engine.model_size = "base"
    engine.device = "cpu"
    engine.compute_type = "int8"
    engine.cpu_threads = 4
    engine.language = language
    engine._last_text = None
    engine._model = None
    engine._loading = False
    engine._running = True
    engine._lock = threading.Lock()
    engine._total_duration = 0.0
    engine._jobs = deque()
    engine._jobs_cv = threading.Condition()
    engine._stop_worker = threading.Event()
    engine._worker = None
    engine._busy = False
    engine._merged_jobs = 0
    engine._dropped_jobs = 0
    engine.on_partial = None
    engine.on_progress = None
    # ---- 术语提示词重复循环防护 ----
    engine.initial_prompt = initial_prompt
    engine._auto_degrade = auto_degrade
    engine._prompt_degraded = False
    engine._prompt_original = None
    engine._stat_in_segments = 0
    engine._stat_out_segments = 0
    engine._repetition_events = 0
    engine._degrade_events = 0
    # 新判据（文本自我重复检测）的字段，见 asr/repetition.py
    engine._recent_text = ""
    engine._last_verdict = None
    engine._advisory_logged = False
    # P0-3：分层术语 + token 预算埋点，见 asr/prompt_budget.py
    engine._prompt_sections = list(sections) if sections else None
    engine._prompt_original_sections = None
    engine._last_budget = None
    # 提示词回显清洗，见 asr/prompt_echo.py
    # V5-1 起 auto 也可能注入引导语（探测判为中文时），所以判据不再是
    # 「language 是否中文」，而是 `_hint_possible()` —— 直接复用它，
    # 免得测试里再抄一份判据、与生产代码漂移。
    engine.simp_mode = SIMP_MODE_AUTO_SIMPLIFIED
    engine._lang_decided = False
    engine._lang_decision = None
    engine._lang_confidence = 0.0
    engine._lang_source = "unset"
    engine._lang_detect_events = 0
    engine._lang_low_conf_events = 0
    engine._lang_non_target_streak = 0
    # V5-8：语种决策回调（默认为空；本文件只测队列/收尾/循环防护，不接回调）
    engine.on_language_decided = None
    engine._lang_decided_notified = False
    engine._echo = EchoCleaner(get_simp_hint() if engine._hint_possible() else "")
    engine._echo_logged = False
    return engine


class TestBackpressure:
    """原来每段音频裸起一个线程 transcribe，无队列上限、无串行化。
    识别跟不上时（small 模型在 2~4 秒片段上 RTF>1）线程无界堆积。"""

    def test_job_queue_is_bounded(self):
        from asr.whisper_engine import MAX_PENDING_JOBS

        engine = _bare_engine()

        for _ in range(MAX_PENDING_JOBS * 5):
            engine.add_audio(np.zeros(16000, dtype=np.float32))

        assert len(engine._jobs) <= MAX_PENDING_JOBS, "队列没有被背压限制住"
        assert engine._dropped_jobs > 0, "超限时应记录丢弃计数"

    def test_flush_does_not_return_during_merge(self):
        """
        `_busy` 必须与「清空队列」在同一个临界区内置位

        否则 `_merge_jobs()` 执行期间会同时满足「队列空 + 不忙」，
        `flush()` 据此判定已经处理完并提前返回 —— 而 `flush()` 正是 stop() 之前
        的最后一道保险，提前返回就等于**停止听课时丢掉最后一段字幕**。
        这个窗口在单片段时只有几微秒、在合并降级（要做多段 concatenate）时
        会被放大到毫秒级，靠人工测试基本撞不到。

        这里用慢速 `_merge_jobs` 把窗口撑开：如果 `_busy` 置位太晚，
        `flush()` 会几乎立刻返回（实测 0.0s），断言随即失败。
        """
        engine = _bare_engine()
        engine._model = object()          # 非 None 即可进入处理流程

        entered = threading.Event()
        release = threading.Event()

        def slow_merge(jobs):
            entered.set()
            release.wait(10.0)            # 撑开窗口，直到测试放行
            return jobs[0]

        engine._merge_jobs = slow_merge
        engine._transcribe = lambda *a, **k: None

        try:
            engine.add_audio(np.zeros(16000, dtype=np.float32))
            assert entered.wait(5.0), "消费者线程没有开始处理"

            started = time.perf_counter()
            engine.flush(timeout=0.8)     # merge 正在进行中，不该判为空闲
            elapsed = time.perf_counter() - started

            assert elapsed >= 0.6, (
                f"merge 期间 flush 只等了 {elapsed:.2f}s 就返回了 —— "
                "_busy 置位晚于队列清空，存在假空闲窗口，停止听课时会丢最后一段字幕"
            )

            release.set()
            engine.flush(timeout=5.0)
            assert engine._busy is False
        finally:
            release.set()
            engine._stop_worker.set()
            with engine._jobs_cv:
                engine._jobs_cv.notify_all()

    def test_merge_jobs_preserves_timeline(self):
        from asr.whisper_engine import WhisperEngine

        # 两段音频之间有 1 秒空隙，合并后应插入等长静音
        a = np.full(16000, 0.5, dtype=np.float32)      # 1s @0.0
        b = np.full(8000, 0.25, dtype=np.float32)      # 0.5s @2.5
        audio, start, duration = WhisperEngine._merge_jobs([(a, 0.0, 1.0), (b, 2.5, 0.5)])

        assert start == 0.0
        assert duration == pytest.approx(3.0, abs=0.05)
        # 1s 语音 + 1.5s 静音 + 0.5s 语音 = 3s
        assert len(audio) == pytest.approx(16000 * 3.0, abs=1600)


# ============ 6b. 术语提示词重复循环防护 ============

# 约 970 字符，与 TINGKEBANLV_TERM_PROMPT_MAX 默认值同量级
LONG_PROMPT = ", ".join(f"term{i}" for i in range(120))

# ---- 实测样本（2026-10-06 中文代理集 A/B 的真实输出，不是编的）----
# 旧判据（产出/送入 > 1.2）在这一条上**漏报**（比值只有 1.089）；
# 新判据（文本自我重复）把它稳稳抓住，最长重复块 80 字符。
REPEAT_TANDEM = "级测试" * 40
# 成句复读：run2 on-noguard 实录，最长重复 ≈54 字符
REPEAT_SENTENCE = "相当于在训练时随机丢掉一部分神经缘" * 5
# 干净样本：真实讲稿风格，**句句不同**（刻意不用 `* N` 拼，否则用例自相矛盾）
CLEAN_LECTURE = (
    "那么我们今天继续讲卷积神经网络的结构。"
    "卷积层负责提取局部特征，池化层负责降低空间分辨率。"
    "反向传播的时候，梯度会逐层往回传，越靠前的层梯度越小。"
    "所以后来有人提出残差连接，让梯度可以跳过若干层直接传回去。"
)


class _FakeSegment:
    def __init__(self, text: str, start: float = 0.0, end: float = 1.0):
        self.text = text
        self.start = start
        self.end = end
        self.words = None


class _FakeInfo:
    language = "en"
    language_probability = 0.9


class _FakeModel:
    """按给定文本列表吐 segment 的假模型，用来单独驱动重复循环计数"""

    def __init__(self, texts: list[str]):
        self.texts = texts

    def transcribe(self, audio, **kwargs):
        # 必须返回 (segments, info) 元组：返回 list 会被解包成 segment/info 两个元素
        return [_FakeSegment(t) for t in self.texts], _FakeInfo()


class TestTermPromptGuard:
    """
    术语提示词「重复循环」的运行时兜底

    判据在 2026-10-06 的中文代理集 A/B 后被**整条换掉**：旧的「产出/送入 > 1.2」
    在中文下误报与漏报并存（`--terms off` 的天然比值就是 1.625，而真复读样本只有
    1.089），现在主判据是**文本自我重复检测**（asr/repetition.py），比值降级为辅助
    告警。这里覆盖：触发、不触发（含中文自然断句防误报）、可关闭、可观测、
    热更新不顶掉降级、跨片段累积、整链。
    """

    def test_degrades_when_repetition_detected(self):
        from asr.whisper_engine import DEGRADE_TOKEN_BUDGET

        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_produced_text(REPEAT_TANDEM)

        assert engine._prompt_degraded is True
        assert engine._repetition_events == 1
        assert engine._degrade_events == 1
        assert "重复" in engine._last_verdict.summary
        # P0-3 起降级改的是**预算**而不是字符串 —— 按字符砍尾巴会砍掉优先级最高
        # 的术语（那正是 P0-3 的老毛病），所以完整版只留档、不裁剪。
        assert engine._prompt_original == LONG_PROMPT
        assert engine.initial_prompt == LONG_PROMPT
        engine._build_prompt()
        assert engine._last_budget.budget == DEGRADE_TOKEN_BUDGET

    def test_repeated_detection_counts_but_degrades_once(self):
        """持续复读：检出次数持续增长，但降级只执行一次（幂等）"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        for _ in range(5):
            engine._note_produced_text(REPEAT_TANDEM)

        assert engine._repetition_events == 5, "每次检出都要计数（这是复读严重程度信号）"
        assert engine._degrade_events == 1, "降级只需一次，重复降级没有意义"

    def test_sentence_loop_detected(self):
        """成句复读（不是「级测试」那种机械重复）也要抓到"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_produced_text(REPEAT_SENTENCE)

        assert engine._prompt_degraded is True

    def test_clean_lecture_not_degraded(self):
        """
        中文自然断句（句句不同）不能误报 —— 这是旧判据最主要的失分点。
        旧判据在 `--terms off` 的干净样本上就误报了（比值 1.625）。
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_produced_text(CLEAN_LECTURE)

        assert engine._prompt_degraded is False
        assert engine.initial_prompt == LONG_PROMPT
        assert engine._last_verdict is not None
        assert engine._last_verdict.triggered is False

    def test_advisory_ratio_no_longer_degrades(self):
        """
        比值只是辅助告警：即使远超阈值也不许改提示词。
        （`--terms off` 的中文天然比值 1.625，拿它降级必然误报 —— 这里锁死该回归。）
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_segment_counts(4, 20)          # 比值 5.0 ≫ 1.2

        assert engine._prompt_degraded is False
        assert engine.initial_prompt == LONG_PROMPT
        assert engine._advisory_logged is True, "辅助告警本身仍应记录一条"

    def test_waits_for_enough_text(self):
        """文本太少不判：窗口不足时重复率没有统计意义"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_produced_text("级测试")          # 3 字符，远不足窗口下限

        assert engine._prompt_degraded is False
        assert engine._last_verdict.triggered is False

    def test_repeat_accumulated_across_segments(self):
        """
        复读会跨多条字幕累积 —— 这正是要维护滚动缓冲的原因。
        单条 12 字看不出来，拼起来才够成句。
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        for _ in range(8):
            engine._note_produced_text("级测试级测试级测试级测试")   # 每 12 字

        assert engine._prompt_degraded is True

    def test_can_be_disabled(self):
        """
        关闭「自动降级」后，再严重的重复也不动提示词，但**结论仍要可观测**

        `repetitionEvents` 统计的是检出次数、与是否降级无关 —— 这条曾经只在
        真的降级时才 +1，于是 2026-10-06 的 A/B 里 `on-noguard` 条件下
        `repetitionEvents` 恒为 0，一条 148 字的真实复读被埋点漏记，
        差点误判成「判据漏报」。
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT, auto_degrade=False)
        engine._note_produced_text(REPEAT_TANDEM)

        assert engine._prompt_degraded is False
        assert engine.initial_prompt == LONG_PROMPT
        assert engine._repetition_events == 1, "关闭降级也必须记下检出次数"
        assert engine._degrade_events == 0, "关闭降级就不该有降级次数"
        # 关了保护不代表看不见：最近一次结论仍要留痕
        assert engine._last_verdict.triggered is True
        assert engine.get_stats()["lastRepeat"]

    def test_short_prompt_not_degraded(self):
        """本来就短，降无可降"""
        engine = _bare_engine(initial_prompt="convolution, gradient descent")
        engine._note_produced_text(REPEAT_TANDEM)

        assert engine._prompt_degraded is False

    def test_hot_update_keeps_degraded_prompt(self):
        """
        `pipeline.update_terminology()` 每次都会重推完整提示词，
        不能让热更新把降级顶掉 —— 否则会「恢复→再触发→再降级」来回抖动
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_produced_text(REPEAT_TANDEM)
        degraded = engine.initial_prompt
        assert engine._prompt_degraded is True

        fuller = LONG_PROMPT + ", extra"
        engine.set_initial_prompt(fuller)

        assert engine.initial_prompt == degraded, "降级状态被热更新顶掉了"
        assert engine._prompt_original == fuller, "完整版应被留存以便恢复"

    def test_restore_full_prompt(self):
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_produced_text(REPEAT_TANDEM)
        engine.restore_full_prompt()

        assert engine.initial_prompt == LONG_PROMPT
        assert engine._prompt_degraded is False
        assert engine._stat_in_segments == 0
        assert engine._stat_out_segments == 0
        # 新判据的状态也要一起复位，否则换课程后残留的旧文本会立刻二次触发
        assert engine._recent_text == ""
        assert engine._last_verdict is None
        assert engine._advisory_logged is False

    def test_transcribe_detects_repetition_end_to_end(self):
        """整链：mock 识别输出 → 累积文本 → 触发降级（不是只测计数器本身）"""
        from asr.whisper_engine import DEGRADE_TOKEN_BUDGET

        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._model = _FakeModel([REPEAT_TANDEM])

        engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert engine._stat_in_segments == 1
        assert engine._stat_out_segments == 1
        assert engine._prompt_degraded is True
        assert len(engine.initial_prompt) == len(LONG_PROMPT)   # 字符串不再被裁剪

        stats = engine.get_stats()
        assert stats["termPromptDegraded"] is True
        assert stats["repetitionDetected"] is True
        assert stats["lastRepeat"]
        # 降级对**下一轮**识别生效（本轮已经用旧预算跑完了），所以这里再组一次
        engine._build_prompt()
        stats = engine.get_stats()
        assert stats["promptBudget"] == DEGRADE_TOKEN_BUDGET
        assert stats["promptTokens"] <= DEGRADE_TOKEN_BUDGET

    def test_term_prompt_chars_is_not_a_degradation_indicator(self):
        """
        B4 回归守卫：`termPromptChars` **不能**用来判断降级效果。

        P0-3 之前降级靠裁剪字符串（794 → 200 字符），该字段才等于「当前生效长度」；
        P0-3 起降级只调 token 预算、提示词字符串原样保留，于是它恒为**完整长度**。
        悬浮窗控制面板曾据此显示「提示词已缩减到 794 字符」—— 794 正是完整长度，
        自相矛盾（Backlog B4）。

        这条用例把语义钉住：**判断降级效果要看 promptBudget / droppedTerms**。
        如果有人把降级「顺手」改回裁剪字符串，这里会失败 —— 那正是需要
        重新审视前端文案（frontend/src/lib/termGuard.ts）的时候，
        而不是简单地把断言改掉。
        """
        from asr.whisper_engine import DEGRADE_TOKEN_BUDGET, PROMPT_TOKEN_BUDGET

        # 30 个中文术语 ≈ 300+ token（按 1.7 token/字估算），远超 70 的降级预算，
        # 保证既触发降级、又真的丢掉一批术语
        terms = [f"卷积神经网络变体{i}" for i in range(30)]
        # 同时给 initial_prompt 与 sections：前者只为占住「字符串长度」这个口径，
        # 后者才是实际参与组装的分层结构（与 pipeline 的真实用法一致）
        engine = _bare_engine(initial_prompt=LONG_PROMPT, sections=[("custom", terms)])

        engine._build_prompt()
        before = engine.get_stats()
        assert before["promptBudget"] == PROMPT_TOKEN_BUDGET
        assert before["termPromptChars"] == len(LONG_PROMPT), "前置条件：降级前长度=完整长度"

        engine._note_produced_text(REPEAT_TANDEM)
        assert engine._prompt_degraded is True

        engine._build_prompt()          # 降级对下一轮组装生效
        after = engine.get_stats()

        # ① 字符串长度**不变** —— B4 的根因，必须钉住
        assert after["termPromptChars"] == before["termPromptChars"], (
            "降级不应改变提示词字符串长度。若此处失败，说明降级又变回了裁剪字符串，"
            "前端文案（lib/termGuard.ts）与 types 里的 @deprecated 说明需同步重新审视"
        )

        # ② 真正会变的是预算与丢弃清单 —— 前端应当依赖这两个
        assert after["promptBudget"] == DEGRADE_TOKEN_BUDGET
        assert after["promptBudget"] < before["promptBudget"]
        assert isinstance(after["droppedTerms"], list)
        assert after["droppedTerms"], "预算从 220 骤降到 70 后应当有术语被丢弃"
        assert after["promptTokens"] <= DEGRADE_TOKEN_BUDGET

        # ③ 分层保留数也要能看见 —— 前端文案里的「N 个低优先级术语未参与」
        #    就来自 droppedTerms.length，两侧口径必须同源
        assert after["promptSections"].get("custom", 0) < len(terms)
        assert len(after["droppedTerms"]) == len(terms) - after["promptSections"]["custom"]

    def test_stats_expose_fields_the_panel_consumes(self):
        """
        控制面板消费的字段必须在 stats 里齐备 —— 少一个就会静默退化成
        「不显示」或「显示旧字段」，那正是 B4 的成因（后端换了语义、
        前端还在读老字段，而没有任何测试会发现）。
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._build_prompt()
        stats = engine.get_stats()

        for key in (
            "termPromptDegraded", "termAutoDegrade", "repetitionDetected",
            "promptTokens", "promptBudget", "droppedTerms", "promptSections",
        ):
            assert key in stats, f"stats 缺少控制面板依赖的字段 {key}"

        assert isinstance(stats["droppedTerms"], list)
        assert isinstance(stats["promptSections"], dict)
        assert stats["promptBudget"] > 0
        # 未产出文本时不应误报「已检出重复」
        assert stats["repetitionDetected"] is False
        assert stats["lastRepeat"] is None

    def test_empty_text_not_counted_as_subtitle(self):
        """空文本不会变成字幕，不该计入产出数，也不该污染重复检测的缓冲"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._model = _FakeModel(["a", "", "   "])

        for _ in range(6):
            engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert engine._stat_in_segments == 6
        assert engine._stat_out_segments == 6       # 每轮只有 1 条非空
        assert engine._prompt_degraded is False
        assert engine._recent_text == "a" * 6       # 只有非空文本进了缓冲


class TestPromptEchoEndToEnd:
    """提示词回显清洗在**真实 `_transcribe()` 路径**上的行为（不只是静态顺序守卫）

    与 `tests/test_prompt_echo.py` 的分工：
        * 那边覆盖纯函数两条规则 + 源码顺序守卫（静态）；
        * 这里补「跑完一轮 `_transcribe` 之后，回调收到了什么、`_last_text`
          变成了什么、计数怎么走」—— 静态守卫只能证明「代码写着清洗在前面」，
          **证明不了清洗真的发生过**。

    一个必须写下来的事实：**`isPureEcho=True` 必然 `text==""`**。
    覆盖率判据是「归一化后 100% 被回显字面量覆盖」，删完就只剩分隔符，
    `_tidy` 再把分隔符去掉 → 空串。所以引擎里
    `if echo.isPureEcho: … continue` 与外层 `if not text: continue`
    **在输出上完全等价**，唯一差别是前者会触发一次 `_log_echo_once()`。
    这条推论有两个后果：
        1. 「纯回显被丢弃」不能靠输出断言区分（两条路径都丢），
           只能靠**日志**与 `segmentsDropped` 埋点 —— 所以下面用 `caplog`。
        2. 该分支的价值是**可观测性**，不是过滤强度；若哪天把
           `is_pure` 阈值放宽（`test_almost_pure_echo_is_not_dropped` 拦的就是这个），
           它就会开始真的丢掉残字 —— 那时行为差异才会显形。

    变异探针（= 本组用例的否定形式，三个都实测过，方法：把源码在内存里改掉后
    以 `sys.modules` 注入再跑本类，不落任何真实文件）：
        * 删掉 `if echo.isPureEcho: … continue` 分支 → **只**红
          `test_pure_echo_segment_is_dropped_end_to_end`（caplog 断言），另两条照过；
        * 让 `build_blacklist()` 返回 `[]`（模拟黑名单派生失效）→ 纯回显与混入
          两条都红（引导语原样进回调），英文那条照过；
        * 把清洗结果丢掉、仍用 `raw_text` 写 `_last_text` → **只**红
          `test_echo_inside_real_text_is_stripped_end_to_end`（自我喂食那条断言）。

    真实素材依据：B站 `BV1BA411i7PT`（630s，近静音 27.6%）草稿里引导语命中 17 次，
    其中一条 2.5s 静音片段整段就是「请使用简体中文转写。」= 一条纯虚假字幕。
    """

    def test_pure_echo_segment_is_dropped_end_to_end(self, caplog):
        """规则①整链：整段只有提示词 → 不回回调、不进 `_last_text`、不计产出"""
        import logging

        from asr.whisper_engine import get_simp_hint

        real = "那么我们现在打开 Origin 这个软件。"
        seen: list[str] = []
        engine = _bare_engine(initial_prompt=LONG_PROMPT, language="zh")
        engine.on_partial = lambda text, words: seen.append(text)
        engine._model = _FakeModel([get_simp_hint(), real])

        with caplog.at_level(logging.INFO, logger="asr.whisper_engine"):
            engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert seen == [real], f"纯回显不该变成字幕，实际收到 {seen}"
        assert engine._last_text == real
        # 回显段不产出字幕 → 产出计数是 1 而不是 2。
        # 这个计数进的是「字幕/片段」辅助告警的分母，虚高会引发假告警。
        assert engine._stat_in_segments == 1
        assert engine._stat_out_segments == 1
        echo = engine.echo_stats()
        assert echo["enabled"] is True
        assert echo["segmentsDropped"] == 1
        assert echo["segmentsCleaned"] == 0
        # 唯一能区分「纯回显分支」与「空文本分支」的判据（见类文档字符串）
        assert any("[echo]" in r.getMessage() for r in caplog.records), (
            "纯回显被丢弃时必须留痕 —— 否则『清洗在跑』和『清洗一条没匹配到』"
            "在日志上无法区分，又回到静默失效"
        )

    def test_echo_inside_real_text_is_stripped_end_to_end(self, caplog):
        """规则②整链：回显夹在真实文本里 → 只剔除回显，其余原样进回调"""
        import logging

        from asr.whisper_engine import get_simp_hint

        raw = f"我们要继续做行图线, {get_simp_hint()}也就是这个文件了。"
        seen: list[str] = []
        engine = _bare_engine(language="zh")
        engine.on_partial = lambda text, words: seen.append(text)
        engine._model = _FakeModel([raw])

        with caplog.at_level(logging.INFO, logger="asr.whisper_engine"):
            engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert len(seen) == 1
        assert "简体中文转写" not in seen[0], f"回调拿到的是未清洗文本：{seen[0]!r}"
        assert "我们要继续做行图线" in seen[0]
        assert "也就是这个文件了" in seen[0]
        # `_last_text` 会被下一轮 `_build_prompt()` 当成连贯性提示 —— 若这里仍是
        # 带回显的原串，就形成自我喂食（实测同一 14s 块滚到 16 次）。
        assert engine._last_text == seen[0], "_last_text 必须是清洗后的文本"
        echo = engine.echo_stats()
        assert echo["segmentsDropped"] == 0
        assert echo["segmentsCleaned"] == 1
        assert echo["substringsRemoved"] >= 1
        assert any("[echo]" in r.getMessage() for r in caplog.records)

    def test_english_never_gets_echo_cleaning(self):
        """反向断言：英文场景不注入引导语，就不可能回显 —— 清洗器必须不介入。

        两个方向都要钉住：
            * 若清洗器在英文下被**无条件启用**，英文音频里任何恰好撞上中文
              引导语的句子都会被静默删除；
            * 反过来，若 `enabled is False` 但代码仍走清洗路径，
              又会引入无意义开销与未来的误删面。
        英文段落照原样产出，且埋点显示 `segmentsSeen == 0`（清洗器压根没被调用）。
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT, language="en")
        engine._model = _FakeModel(["hello world", "this is a lecture"])
        engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert engine._stat_out_segments == 2
        echo = engine.echo_stats()
        assert echo["enabled"] is False
        assert echo["inertReason"], "未启用必须说明原因，不能静默"
        assert echo["segmentsDropped"] == 0
        assert echo["segmentsSeen"] == 2, "英文下清洗器仍在被调用（inert 时也应计数）"


class TestBuildPromptSimpHint:
    """
    P0-1：简体引导语必须落在提示词**最末尾**

    实测依据：提示词为空 → 繁体占 34.4%；只注入英文术语 → 34.5%（毫无作用）；
    提示词里出现简体中文 → **0.1%**。而 faster-whisper 的 `get_prompt()` 只保留
    `previous_tokens[-223:]`（只留尾部），引导语放开头或中间会被静默截断。
    """

    def test_zh_appends_hint_at_tail(self):
        from asr.whisper_engine import SIMP_HINT

        # 用分层入口（正常路径）：高优先级术语贴尾，引导语再贴在它后面
        engine = _bare_engine(
            sections=[("custom", ["卷积", "池化", "梯度下降"])], language="zh"
        )
        prompt = engine._build_prompt()

        assert prompt.endswith(SIMP_HINT), "引导语必须落在末尾，否则会被 token 预算截掉"
        assert "卷积" in prompt
        # 术语在引导语之前 —— 顺序是「低优先级 … 高优先级 引导语」
        assert prompt.index("卷积") < prompt.index(SIMP_HINT)

    def test_zh_variants_also_get_hint(self):
        from asr.whisper_engine import SIMP_HINT

        for lang in ("zh", "zh-CN", "zh-TW", "ZH"):
            engine = _bare_engine(initial_prompt="卷积, 梯度", language=lang)
            assert engine._build_prompt().endswith(SIMP_HINT), lang

    def test_non_zh_no_hint(self):
        from asr.whisper_engine import SIMP_HINT

        assert SIMP_HINT not in _bare_engine(initial_prompt=LONG_PROMPT, language="en")._build_prompt()

    def test_hint_alone_when_no_terms(self):
        """新课程 / 用户没选领域时术语为空，引导语必须仍能单独生效"""
        from asr.whisper_engine import SIMP_HINT

        assert _bare_engine(initial_prompt=None, language="zh")._build_prompt() == SIMP_HINT

    def test_hint_reserves_budget(self):
        """
        引导语要先占住预算，不能被正文挤出 token 上限

        注意断言口径已从字符改为 token：字符上限（1000）正是 P0-3 的病根 ——
        中文 1000 字符 ≈ 1350 token，远超库保留的 223。
        """
        from asr.prompt_budget import estimate_tokens
        from asr.whisper_engine import PROMPT_TOKEN_BUDGET, SIMP_HINT

        engine = _bare_engine(initial_prompt="t" * 1200, language="zh")   # 刻意超长
        prompt = engine._build_prompt()

        assert estimate_tokens(prompt) <= PROMPT_TOKEN_BUDGET
        assert prompt.endswith(SIMP_HINT)

    def test_degraded_prompt_still_gets_hint(self):
        """
        降级只压缩术语预算，不能把简体引导语一起砍掉
        —— 否则复读修好了、繁体又回来了，两个 P0 互相打架
        """
        from asr.whisper_engine import DEGRADE_TOKEN_BUDGET, SIMP_HINT

        engine = _bare_engine(initial_prompt=LONG_PROMPT, language="zh")
        engine._note_produced_text(REPEAT_TANDEM)

        assert engine._prompt_degraded is True
        prompt = engine._build_prompt()
        assert prompt.endswith(SIMP_HINT)
        assert engine._last_budget.budget == DEGRADE_TOKEN_BUDGET

    def test_no_prompt_no_hint_returns_none(self):
        """英文且无任何提示词 → 返回 None（保持旧行为，别给 Whisper 塞空串）"""
        assert _bare_engine(initial_prompt=None, language="en")._build_prompt() is None


class TestPromptTokenBudget:
    """
    P0-3：按 token 预算组装 + 优先级倒序拼接

    根因（ASR 报告 §6.10）：faster-whisper 只保留提示词末尾 223 token，而旧实现
    把**高优先级术语放在前面**，导致自定义中文术语存活率 25%、优先级最低的内置
    英文术语 100% —— 完全颠倒。这里锁死修复后的行为。
    """

    CN_CUSTOM = ["卷积", "池化", "梯度下降", "反向传播", "过拟合", "正则化", "注意力机制"]

    @staticmethod
    def _long_domain(n: int = 80) -> list[str]:
        return [f"domainterm{i}" for i in range(n)]

    def test_overflow_drops_low_priority_not_high(self):
        """大中文术语表 token 溢出：高优先级必须全留，被丢的只能是低优先级"""
        from asr.prompt_budget import estimate_tokens
        from asr.whisper_engine import PROMPT_TOKEN_BUDGET

        engine = _bare_engine(
            sections=[("custom", self.CN_CUSTOM), ("domain", self._long_domain())],
            language="zh",
        )
        body = engine._build_prompt()
        b = engine._last_budget

        assert b.dropped, "溢出时必须真的丢东西，否则这个用例是空转的"
        for t in self.CN_CUSTOM:
            assert t in body, f"高优先级术语被丢了：{t}"
        for t in b.dropped:
            assert t not in body
        # 全部被丢的都是低优先级层
        assert set(b.dropped) <= set(self._long_domain())
        assert b.used_tokens <= PROMPT_TOKEN_BUDGET
        assert estimate_tokens(body) <= PROMPT_TOKEN_BUDGET

    def test_high_priority_sits_at_tail(self):
        """尾部才是库的保留窗口 → 高优先级必须排在低优先级之后"""
        engine = _bare_engine(
            sections=[("custom", ["卷积"]), ("domain", self._long_domain(20))],
            language="zh",
        )
        body = engine._build_prompt()
        b = engine._last_budget

        assert b.kept.get("domain"), "低优先级层也要有内容，否则比不出先后"
        assert body.index("卷积") > body.index(b.kept["domain"][0])

    def test_prev_text_no_longer_eats_terms(self):
        """
        复现「第二段起术语被挤光」

        旧实现是 `_last_text[-200:]` 字符 —— 中文 200 字 ≈ 280 token，
        单独一项就吃穿 223 的预算，术语全部出局。
        """
        from asr.prompt_budget import estimate_tokens
        from asr.whisper_engine import PROMPT_PREV_TOKENS, PROMPT_TOKEN_BUDGET

        engine = _bare_engine(sections=[("custom", self.CN_CUSTOM)], language="zh")
        engine._last_text = "这是一段很长的上一段识别文本，用来挤占提示词预算。" * 12
        body = engine._build_prompt()
        b = engine._last_budget

        assert b.prev_kept, "上一段文本应该保留一部分（有配额）"
        assert estimate_tokens(b.prev_kept) <= PROMPT_PREV_TOKENS
        for t in self.CN_CUSTOM:
            assert t in body, f"术语被上一段文本挤掉了：{t}"
        assert b.used_tokens <= PROMPT_TOKEN_BUDGET

    def test_prev_text_is_first_to_sacrifice(self):
        """配额不够时先牺牲上一段文本（它排在最前 = 最低优先级）"""
        engine = _bare_engine(sections=[("custom", self.CN_CUSTOM)], language="zh")
        engine._last_text = "上一段文本。" * 40
        body = engine._build_prompt()

        # 术语在引导语之前、上一段文本之后
        assert body.index(self.CN_CUSTOM[0]) > body.index("上一段文本")

    def test_empty_sections_returns_none_for_en(self):
        engine = _bare_engine(sections=[("custom", []), ("domain", [])], language="en")

        assert engine._build_prompt() is None

    def test_empty_sections_still_emits_hint_for_zh(self):
        from asr.whisper_engine import SIMP_HINT

        engine = _bare_engine(sections=[("custom", []), ("domain", [])], language="zh")

        assert engine._build_prompt() == SIMP_HINT

    def test_pure_english_terms_fit(self):
        """纯英文术语：token 密度低（0.30/字符），同样字符数能放下的更多"""
        engine = _bare_engine(
            sections=[("custom", ["convolution", "pooling", "gradient descent"])],
            language="en",
        )
        body = engine._build_prompt()

        assert "convolution" in body
        assert "gradient descent" in body

    def test_mixed_cn_en_keeps_both(self):
        """中英混杂：两层都要留住（预算 220 够用）"""
        engine = _bare_engine(
            sections=[
                ("custom", ["卷积神经网络", "attention"]),
                ("domain", ["convolution", "backpropagation"]),
            ],
            language="zh",
        )
        body = engine._build_prompt()

        for t in ("卷积神经网络", "attention", "convolution", "backpropagation"):
            assert t in body, f"中英混杂时丢了：{t}"

    def test_body_never_exceeds_budget_with_oversized_prev(self):
        """上一段文本极长 + 中英混杂术语：总量仍不许越预算（否则库会截掉尾部）"""
        from asr.prompt_budget import estimate_tokens
        from asr.whisper_engine import PROMPT_TOKEN_BUDGET

        engine = _bare_engine(
            sections=[
                ("custom", self.CN_CUSTOM),
                ("discovered", ["注意力", "残差"]),
                ("domain", self._long_domain(40)),
            ],
            language="zh",
        )
        engine._last_text = "老师刚才讲到卷积核的滑动窗口。" * 30
        body = engine._build_prompt()

        assert estimate_tokens(body) <= PROMPT_TOKEN_BUDGET

    def test_prebuilt_body_not_reordered(self):
        """
        退化路径（只给拼好的字符串）**不许重排**

        `build_initial_prompt()` 的输出已是「低 → 高」，引擎若再反转一次就转回
        原样，等于把 P0-3 又引入一遍。
        """
        engine = _bare_engine(initial_prompt="phrase_a, phrase_b, 自定义术语", language="en")
        body = engine._build_prompt()

        assert body.index("phrase_a") < body.index("自定义术语")

    def test_stats_expose_budget_and_dropped(self):
        engine = _bare_engine(
            sections=[("custom", self.CN_CUSTOM), ("domain", self._long_domain())],
            language="zh",
        )
        engine._build_prompt()
        stats = engine.get_stats()

        assert stats["promptBudget"] > 0
        assert stats["promptTokens"] <= stats["promptBudget"]
        assert stats["promptSections"]["custom"] == len(self.CN_CUSTOM)
        assert stats["droppedTerms"], "溢出了就要能在 stats 里看到丢了哪些"
        assert stats["promptTokenExact"] is False      # 无模型 → 启发式估算


class TestTruncateToTokensEdge:
    """
    `truncate_to_tokens` 在「配额小于单个字符代价」时必须返回空串

    2026-10-08 修复的真实缺陷（不是理论问题，是被 `test_prev_text_*` 之外的
    预算区间命中的）：

    byte-level BPE 对中文单字回退到 UTF-8 字节序列，**一个汉字 ≈ 4 token**
    （`count("好") == 4`，用真 tokenizer 实测）。于是 `max_tokens=1` 时任何非空
    子串都超预算 → 二分找不到可行解、`lo` 停在 0 → 最后的 `text[-lo:]` 在
    `lo == 0` 时**等于 `text[0:]` 也就是全文**（Python 里 `-0 == 0`）。

    后果是**反向吃光预算**：预算 90 时 `prev_budget = min(48, 90-29-60) = 1`，
    本该留 0 字符的上一段文本实际留下整段 120 字符 / 100 token，把术语层的
    remaining 挤成负数，24 个术语**一个都没进去**（`kept=[]`、`used=126 > 90`）。

    命中区间实测为 budget ∈ [90, 92]（3 个值）—— 默认 220 与降级 70 都不在其内，
    但 `TINGKEBANLV_TERM_PROMPT_TOKENS` 是用户可配的环境变量，落在区间里就会中招。

    ⚠️ 这个用例**必须用真 tokenizer**：启发式 `estimate_tokens` 按字符数算，
    单字代价 = 1，根本构造不出「连 1 个字都放不下」的情形 —— 用它写出来的
    用例是**空的**（前提断言就会失败，这正是第一版踩的坑）。
    真 tokenizer 不可用时跳过，而不是退化成假用例。
    """

    PREV = "老师说这个坐标系的横坐标要设置成厘米，纵坐标也要跟着一起改。" * 4

    @staticmethod
    def _real_tokenizer():
        """
        真 tokenizer（`安装空间/whisper_models/plain/tokenizer.json`）

        单字 ≈ 4 token 这个性质只有它能体现；不可用时返回 None，由调用方跳过。
        """
        import os
        from pathlib import Path

        candidates = []
        env = os.environ.get("TINGKEBANLV_TOKENIZER_JSON")
        if env:
            candidates.append(Path(env))
        # 仓库 → 桌面/听课伴侣/安装空间/whisper_models/plain/tokenizer.json
        here = Path(__file__).resolve()
        for parent in here.parents:
            candidates.append(parent / "安装空间" / "whisper_models" / "plain" / "tokenizer.json")
            candidates.append(parent.parent / "安装空间" / "whisper_models" / "plain" / "tokenizer.json")

        for p in candidates:
            try:
                if p.is_file():
                    from tokenizers import Tokenizer
                    from asr.prompt_budget import make_counter
                    return make_counter(Tokenizer.from_file(str(p)))[0]
            except Exception:
                continue
        return None

    def _require_tokenizer(self):
        count = self._real_tokenizer()
        if count is None:
            import pytest
            pytest.skip("真 tokenizer 不可用（设 TINGKEBANLV_TOKENIZER_JSON 可指定）")
        return count

    def test_tiny_quota_returns_empty_not_full_text(self):
        """极小额配额：必须返回空串，绝不能把全文放回去（-0 陷阱）"""
        from asr.prompt_budget import truncate_to_tokens

        count = self._require_tokenizer()
        one_char = count(self.PREV[-1])          # 单个汉字的真实代价（≈4）
        assert one_char > 1, (
            f"前提：中文单字代价应大于 1，实测 {one_char}。"
            "若这里失败，说明 tokenizer 不是预期的 byte-level BPE"
        )

        for quota in range(1, one_char):
            out = truncate_to_tokens(self.PREV, quota, count)
            assert out == "", (
                f"配额 {quota} 连 1 个字都放不下，应返回空串，"
                f"实际返回 {len(out)} 字符 —— `text[-0:]` 把全文放回去了"
            )

    def test_zero_and_negative_quota_return_empty(self):
        from asr.prompt_budget import truncate_to_tokens

        assert truncate_to_tokens(self.PREV, 0, self._require_tokenizer()) == ""
        assert truncate_to_tokens(self.PREV, -5, self._require_tokenizer()) == ""

    def test_normal_quota_still_truncates_from_tail(self):
        """反向断言：正常配额下不能因为修了小额分支就把截断也关掉"""
        from asr.prompt_budget import truncate_to_tokens

        count = self._require_tokenizer()
        quota = count(self.PREV) // 4
        assert quota > count(self.PREV[-1]), "前提：配额要够放几个字"

        out = truncate_to_tokens(self.PREV, quota, count)
        assert out, "正常配额必须截出内容"
        assert count(out) <= quota, "截断结果超预算"
        assert out == self.PREV[-len(out):], "必须从尾部截"

    def test_assemble_keeps_terms_when_prev_quota_is_tiny(self):
        """
        端到端后果：`prev_budget` 落在极小区间时，术语**不许**被挤光

        修复前（真 tokenizer，budget=90）`kept=[]`、`used=126 > 90`。
        """
        from asr import prompt_budget as pb

        count = self._require_tokenizer()
        terms = ["项目管理器", "工作区", "工具栏", "菜单栏", "新建图",
                 "画布", "坐标系", "图层", "子图", "折线图"]
        hint = "以下是普通话的句子，请使用简体中文转写。"

        hint_cost = count(hint) + pb.SEP_TOKENS
        # 让 prev_budget = budget - hint_cost - MIN_TERMS_TOKENS 落在 (0, 单字代价)
        budget = hint_cost + pb.MIN_TERMS_TOKENS + 1

        b = pb.assemble(
            [("custom", terms)], prev_text=self.PREV, hint=hint,
            budget=budget, count=count,
            prev_max_tokens=pb.DEFAULT_PREV_TEXT_TOKENS,
        )

        assert b.prev_kept == "", (
            f"预算 {budget} 下上一段文本配额仅 1 token（低于单字代价），应留空，"
            f"实际留了 {len(b.prev_kept)} 字符"
        )
        assert b.kept["custom"], (
            f"上一段文本不该吃掉术语预算；实际 kept={b.kept['custom']}"
        )
        assert b.used_tokens <= budget, (
            f"组装结果超预算：used={b.used_tokens} > budget={budget}"
        )

    def test_oversized_prev_does_not_starve_terms(self):
        """
        反向断言：修好小额分支后，**大额**配额下上一段文本仍应正常保留

        否则「一律返回空串」也能让上面那个用例变绿 —— 那是把功能砍掉换绿灯。
        """
        from asr import prompt_budget as pb

        count = self._require_tokenizer()
        hint = "以下是普通话的句子，请使用简体中文转写。"
        b = pb.assemble(
            [("custom", ["坐标系"])], prev_text=self.PREV, hint=hint,
            budget=220, count=count,
            prev_max_tokens=pb.DEFAULT_PREV_TEXT_TOKENS,
        )

        assert b.prev_kept, "预算充足时上一段文本必须有内容"
        assert count(b.prev_kept) <= pb.DEFAULT_PREV_TEXT_TOKENS
        assert "坐标系" in (b.kept.get("custom") or [])


# ============ 7. 端到端（需要模型，缺失自动跳过）============

def _resolve_test_model() -> str | None:
    """
    找一个可用的模型目录用于端到端测试

    同时兼容两种布局：
      - huggingface cache:  <root>/models--Systran--faster-whisper-<size>/snapshots/<hash>/model.bin
      - local_dir 直写:     <root>/<任意名>/model.bin
    优先返回最小的模型，让测试跑得快。
    """
    model_dir = os.environ.get("TINGKEBANLV_TEST_MODEL_DIR")
    if not model_dir:
        return None
    root = Path(model_dir)
    if not root.is_dir():
        return None

    candidates: list[Path] = []
    for bin_file in root.rglob("model.bin"):
        try:
            if bin_file.stat().st_size > 1024 * 1024:
                candidates.append(bin_file.parent)
        except OSError:
            continue
    if not candidates:
        return None

    candidates.sort(key=lambda p: (p / "model.bin").stat().st_size)
    return str(candidates[0])


class TestEndToEnd:
    """完整链路：文件音频 → VAD → Whisper → 字幕 → 术语入库

    需要 faster-whisper 与模型，缺失时自动跳过。
    手动跑法：
        TINGKEBANLV_TEST_MODEL_DIR=<含 models--Systran--faster-whisper-tiny 的目录> \
        PYTHONPATH=. python -m pytest tests/test_asr_pipeline_e2e.py -v -k end_to_end
    """

    def test_end_to_end_pipeline_produces_subtitles(self):
        pytest.importorskip("faster_whisper")
        model_path = _resolve_test_model()
        if not model_path:
            pytest.skip("未提供 TINGKEBANLV_TEST_MODEL_DIR 或其中没有可用模型")

        import asr.pipeline as pipeline_mod

        audio = load_fixture_audio()
        subtitles = []
        terms = []

        # 用文件喂音频替代录音设备
        class FileCapture:
            def __init__(self, on_audio_chunk, mode="loopback", **kwargs):
                self.on_audio_chunk = on_audio_chunk
                self.mode = mode

            def start(self):
                for i in range(0, len(audio), 512):
                    self.on_audio_chunk(audio[i:i + 512])

            def stop(self):
                pass

            def pause(self):
                pass

            def resume(self):
                pass

        original_capture = pipeline_mod.AudioCapture
        pipeline_mod.AudioCapture = FileCapture
        try:
            pipeline = pipeline_mod.ASRPipeline(
                course_id="e2e",
                course_config={
                    "domain": "计算机科学",
                    "custom_terms": [],
                    "language": "en",
                    "courseType": "online",
                    "modelSize": model_path,
                },
                on_subtitle=lambda d: subtitles.append(d),
                on_interim=lambda t, w: None,
                on_status=lambda s, m: None,
                on_term_extracted=lambda t: terms.append(t),
            )

            # 等模型加载完成
            deadline = time.time() + 120
            while pipeline._whisper.is_loading and time.time() < deadline:
                time.sleep(0.3)
            assert pipeline._whisper.is_loaded, "Whisper 模型未能加载"

            asyncio.run(pipeline.start())

            # FileCapture 在 start() 里已经把音频一次性喂完，这里等整体排空。
            # 不能一看到第一条字幕就停 —— 那样会漏掉后面的片段，
            # 也发现不了「最后一段被 stop 丢掉」这类缺陷。
            deadline = time.time() + 180
            while not subtitles and time.time() < deadline:
                time.sleep(0.3)

            idle_since = None
            while time.time() < deadline:
                st = pipeline.get_stats()["whisper"]
                if st["backlog"] == 0 and not st["busy"]:
                    idle_since = idle_since or time.time()
                    if time.time() - idle_since >= 1.0:
                        break
                else:
                    idle_since = None
                time.sleep(0.2)

            asyncio.run(pipeline.stop())
        finally:
            pipeline_mod.AudioCapture = original_capture

        assert subtitles, "端到端跑完没有任何字幕产出"

        text = " ".join(s["text"] for s in subtitles).lower()
        print(f"\n[端到端] 字幕 {len(subtitles)} 条:")
        for s in subtitles:
            print(f"  {s['start']/1000:>6.2f}s  {s['text']}")

        # fixture 共 11 秒、含两段语音，应当切出多于一段。
        # 只产出 1 段通常意味着最后一段在 stop 时被丢掉（_running 先置假再 flush）。
        assert len(subtitles) >= 2, (
            f"只产出 {len(subtitles)} 条字幕，疑似结尾片段被丢弃；"
            f"识别文本: {text[:200]}"
        )

        assert "country" in text or "americans" in text, f"识别文本与预期不符: {text[:200]}"

        # 逐词时间戳是前端高亮的基础
        assert any(s.get("words") for s in subtitles), "没有产出词级时间戳"

        stats = pipeline.get_stats()
        print(f"[端到端] 运行统计: {stats}")
        assert stats["vadBackend"] in ("silero-onnx", "silero-jit", "energy")
        assert stats["whisper"]["audioSeconds"] > 8.0, "大部分音频没有被送进识别"
