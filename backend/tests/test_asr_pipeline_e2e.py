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

def _bare_engine(initial_prompt: str | None = None, auto_degrade: bool = True):
    """
    构造一个**不加载模型**的引擎实例，供队列 / 收尾 / 重复循环防护的单元测试使用

    `WhisperEngine.__new__` 会绕过 `__init__`，所以这里必须手工补齐所有实例字段。
    新增字段时要同步加在这里，否则测试会在用到时抛 AttributeError。
    """
    from asr.whisper_engine import WhisperEngine

    engine = WhisperEngine.__new__(WhisperEngine)   # 不触发模型加载
    engine.model_size = "base"
    engine.device = "cpu"
    engine.compute_type = "int8"
    engine.cpu_threads = 4
    engine.language = "en"
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

# 约 800 字符，与 TINGKEBANLV_TERM_PROMPT_MAX 默认值同量级
LONG_PROMPT = ", ".join(f"term{i}" for i in range(120))


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

    背景：实测提示词 794 字符时出现「产出片段数 > 送入片段数」的重复循环，
    但 WER 在各档位不单调，不能直接改默认上限 —— 所以只在真的观察到循环时
    才缩短提示词。这里覆盖触发、不触发、可关闭、热更新不顶掉降级等分支。
    """

    def test_degrades_when_repetition_detected(self):
        from asr.whisper_engine import DEGRADE_PROMPT_CHARS

        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_segment_counts(4, 6)          # 比值 1.5 > 1.2，样本也够

        assert engine._prompt_degraded is True
        assert engine.initial_prompt == LONG_PROMPT[:DEGRADE_PROMPT_CHARS]
        assert engine._repetition_events == 1

    def test_waits_for_min_samples(self):
        """样本太少不判：1 进 3 出就是 3 倍，纯噪声，不该据此改提示词"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_segment_counts(1, 3)

        assert engine._prompt_degraded is False
        assert engine.initial_prompt == LONG_PROMPT

    def test_ratio_within_tolerance_not_triggered(self):
        """长片段被 Whisper 自己切成多段是正常行为，容差内不降级"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_segment_counts(10, 11)        # 1.1 ≤ 1.2

        assert engine._prompt_degraded is False

    def test_can_be_disabled(self):
        """用户关闭「自动降级」后，再严重的重复也不动提示词"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT, auto_degrade=False)
        engine._note_segment_counts(4, 20)

        assert engine._prompt_degraded is False
        assert engine.initial_prompt == LONG_PROMPT

    def test_short_prompt_not_degraded(self):
        """本来就短，降无可降"""
        engine = _bare_engine(initial_prompt="convolution, gradient descent")
        engine._note_segment_counts(4, 20)

        assert engine._prompt_degraded is False

    def test_hot_update_keeps_degraded_prompt(self):
        """
        `pipeline.update_terminology()` 每次都会重推完整提示词，
        不能让热更新把降级顶掉 —— 否则会「恢复→再触发→再降级」来回抖动
        """
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_segment_counts(4, 6)
        degraded = engine.initial_prompt
        assert engine._prompt_degraded is True

        fuller = LONG_PROMPT + ", extra"
        engine.set_initial_prompt(fuller)

        assert engine.initial_prompt == degraded, "降级状态被热更新顶掉了"
        assert engine._prompt_original == fuller, "完整版应被留存以便恢复"

    def test_restore_full_prompt(self):
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._note_segment_counts(4, 6)
        engine.restore_full_prompt()

        assert engine.initial_prompt == LONG_PROMPT
        assert engine._prompt_degraded is False
        assert engine._stat_in_segments == 0
        assert engine._stat_out_segments == 0

    def test_transcribe_counts_towards_detection(self):
        """整链：mock 识别输出 → 计数 → 触发降级（不是只测计数器本身）"""
        from asr.whisper_engine import DEGRADE_PROMPT_CHARS

        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._model = _FakeModel(["a", "b"])     # 每 1 段输入产出 2 条字幕

        for _ in range(4):
            engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert engine._stat_in_segments == 4
        assert engine._stat_out_segments == 8
        assert engine._prompt_degraded is True
        assert len(engine.initial_prompt) == DEGRADE_PROMPT_CHARS

        stats = engine.get_stats()
        assert stats["termPromptDegraded"] is True
        assert stats["repetitionDetected"] is True
        assert stats["termPromptChars"] == DEGRADE_PROMPT_CHARS

    def test_empty_text_not_counted_as_subtitle(self):
        """空文本不会变成字幕，不该计入产出数（否则分母被高估、误触发降级）"""
        engine = _bare_engine(initial_prompt=LONG_PROMPT)
        engine._model = _FakeModel(["a", "", "   "])

        for _ in range(6):
            engine._transcribe(np.zeros(16000, dtype=np.float32), 0.0, 1.0, 1)

        assert engine._stat_in_segments == 6
        assert engine._stat_out_segments == 6       # 每轮只有 1 条非空
        assert engine._prompt_degraded is False


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
