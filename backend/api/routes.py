"""
API 路由
提供课程、字幕、词汇的 REST API
"""

import json
import logging
import asyncio
import platform
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
from typing import Optional

from dependencies import db
from asr.pipeline import ASRPipeline
from llm.explainer import WordExplainer
from llm.summarizer import LectureSummarizer
from llm.qa import ChatQA

logger = logging.getLogger(__name__)
router = APIRouter()

# 活跃的 ASR 管道（按课程 ID）
active_pipelines: dict[str, ASRPipeline] = {}

# 轮询消息队列（按课程 ID）
asr_message_queues: dict[str, list[dict]] = {}

# 全局单词讲解器（复用缓存）
word_explainer = WordExplainer()

# 全局摘要生成器
lecture_summarizer = LectureSummarizer()

# 全局答疑器
chat_qa = ChatQA()


# ============ 请求模型 ============

class CreateCourseRequest(BaseModel):
    name: str
    domain: str = "其他"
    description: str = ""
    customTerms: list[str] = []
    courseType: str = "online"  # 'online' | 'offline'
    language: str = "auto"      # 'auto' / 'en' / 'zh' / 'ja' / ...


class UpdateCourseRequest(BaseModel):
    name: Optional[str] = None
    domain: Optional[str] = None
    description: Optional[str] = None
    customTerms: Optional[list[str]] = None
    courseType: Optional[str] = None
    language: Optional[str] = None


class UpdateVocabularyRequest(BaseModel):
    isMemorized: Optional[bool] = None
    reviewCount: Optional[int] = None


class ExplainRequest(BaseModel):
    word: str
    courseId: str
    context: Optional[str] = None  # 点击单词时所在的字幕句子（可选）


class AskRequest(BaseModel):
    question: str
    courseId: str


class PreviewTerminologyRequest(BaseModel):
    domain: str = ""
    customTerms: list[str] = []


def _parse_custom_terms(course: dict) -> list[str]:
    """
    统一解析课程的自定义术语字段

    背景：db._row_to_dict 会把 custom_terms 改名为 customTerms 并反序列化，
    但历史代码有的读 custom_terms（已不存在）、有的读原始 JSON 字符串，
    导致自定义术语实际被静默丢弃。这里统一收口。
    """
    value = course.get("customTerms")
    if value is None:
        value = course.get("custom_terms") or []

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []

    # 过滤空串与非字符串，避免污染 prompt
    return [str(t).strip() for t in value if isinstance(t, str) and str(t).strip()]


def _course_config_for_asr(course: dict) -> dict:
    """构建 ASR 管道所需的课程配置（供轮询与 WebSocket 两条路径复用）"""
    return {
        "domain": course.get("domain", ""),
        "custom_terms": _parse_custom_terms(course),
        "name": course.get("name", ""),
        "courseType": course.get("courseType", "online"),
        "language": course.get("language", "auto"),
    }


# ============ 课程 API ============

@router.get("/courses")
async def get_courses():
    """获取所有课程列表"""
    return await db.get_courses()


@router.get("/courses/{course_id}")
async def get_course(course_id: str):
    """获取单个课程详情"""
    course = await db.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")
    return course


@router.post("/courses")
async def create_course(req: CreateCourseRequest):
    """创建新课程"""
    return await db.create_course(req.model_dump())


@router.put("/courses/{course_id}")
async def update_course(course_id: str, req: UpdateCourseRequest):
    """更新课程信息"""
    result = await db.update_course(course_id, req.model_dump(exclude_none=True))
    if not result:
        raise HTTPException(status_code=404, detail="课程不存在")

    # 自定义术语变更 → 热更新正在运行的 ASR 管道热词（无需重启监听）
    if req.customTerms is not None and course_id in active_pipelines:
        active_pipelines[course_id].update_terminology(req.customTerms)

    return result


@router.delete("/courses/{course_id}")
async def delete_course(course_id: str):
    """删除课程及其所有数据"""
    success = await db.delete_course(course_id)
    if not success:
        raise HTTPException(status_code=404, detail="课程不存在")
    # 停止关联的 ASR 管道
    if course_id in active_pipelines:
        await active_pipelines[course_id].stop()
        del active_pipelines[course_id]
    return {"ok": True}


# ============ 字幕 API ============

@router.get("/courses/{course_id}/subtitles")
async def get_subtitles(course_id: str, limit: int = Query(default=100, le=500)):
    """获取课程字幕记录"""
    return await db.get_subtitles(course_id, limit)


def _ms_to_srt_time(ms) -> str:
    """毫秒转 SRT 时间格式 HH:MM:SS,mmm（防御负数/浮点/None）"""
    try:
        ms = int(ms)
    except (TypeError, ValueError):
        ms = 0
    ms = max(0, ms)
    h = ms // 3600000
    m = (ms % 3600000) // 60000
    s = (ms % 60000) // 1000
    ms_part = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms_part:03d}"


@router.get("/courses/{course_id}/export-srt")
async def export_srt(course_id: str):
    """导出课程字幕为 SRT 格式"""
    course = await db.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")

    subtitles = await db.get_subtitles(course_id, limit=500)
    # 数据库按 start_ms DESC 返回，SRT 需要时间正序
    subtitles = list(reversed(subtitles))

    lines = []
    for i, sub in enumerate(subtitles, 1):
        start = _ms_to_srt_time(sub.get("start_ms", sub.get("start", 0)))
        end = _ms_to_srt_time(sub.get("end_ms", sub.get("end", 0)))
        text = sub.get("text", "")
        lines.append(str(i))
        lines.append(f"{start} --> {end}")
        lines.append(text)
        lines.append("")

    return {"srt": "\n".join(lines), "count": len(subtitles)}


# ============ 词汇 API ============

@router.get("/courses/{course_id}/vocabulary")
async def get_vocabulary(course_id: str):
    """获取课程词汇表"""
    return await db.get_vocabulary(course_id)


@router.get("/courses/{course_id}/vocabulary/{word}")
async def get_vocabulary_word(course_id: str, word: str):
    """查询单个词汇"""
    entry = await db.get_vocabulary_by_word(course_id, word)
    if not entry:
        raise HTTPException(status_code=404, detail="词汇未收录")
    return entry


@router.put("/courses/{course_id}/vocabulary/{vocab_id}")
async def update_vocabulary_entry(course_id: str, vocab_id: str, req: UpdateVocabularyRequest):
    """更新词汇（标记已掌握等）"""
    result = await db.update_vocabulary(vocab_id, req.model_dump(exclude_none=True))
    if not result:
        raise HTTPException(status_code=404, detail="词汇不存在")
    return result


# ============ AI 讲解 API ============

@router.post("/ai/explain")
async def ai_explain(req: ExplainRequest):
    """
    AI 深度讲解单词：结合课程领域 + 字幕上下文
    Ollama 同步调用通过 to_thread 避免阻塞事件循环
    """
    course = await db.get_course(req.courseId)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")

    word = req.word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="单词不能为空")

    # 若前端没传上下文，从最近字幕中找包含该词的句子
    context = req.context or ""
    if not context:
        subtitles = await db.get_subtitles(req.courseId, limit=50)
        for sub in subtitles:  # 已按时间倒序，最近的在前
            text = sub.get("text", "")
            if word.lower() in text.lower():
                context = text
                break

    result = await asyncio.to_thread(
        word_explainer.explain,
        word,
        course.get("domain", ""),
        course.get("name", ""),
        context,
    )

    if result is None:
        # Ollama 不可用或解析失败——区分两种情况
        return {
            "available": False,
            "message": "AI 讲解服务不可用，请确认 Ollama 已启动并拉取了模型",
        }

    return {"available": True, "context": context, "explanation": result}


@router.post("/courses/{course_id}/summary")
async def generate_summary(course_id: str):
    """
    AI 课后摘要：最近字幕 + 未掌握词汇 → 摘要 / 知识点 / 生词复习清单
    生成耗时较长（本地模型推理），前端需展示 loading
    """
    course = await db.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")

    # 取最近字幕（数据库按时间倒序，需要反转为正序拼接）
    subtitles = await db.get_subtitles(course_id, limit=200)
    if not subtitles:
        raise HTTPException(status_code=400, detail="暂无字幕记录，无法生成摘要")
    text = " ".join(sub.get("text", "") for sub in reversed(subtitles))

    # 课程词汇表
    vocabulary = await db.get_vocabulary(course_id)

    result = await asyncio.to_thread(
        lecture_summarizer.summarize,
        text,
        course.get("domain", ""),
        course.get("name", ""),
        vocabulary,
    )

    if result is None:
        return {
            "available": False,
            "message": "AI 摘要服务不可用，请确认 Ollama 已启动并拉取了模型",
        }

    return {
        "available": True,
        "subtitleCount": len(subtitles),
        "wordCount": len(vocabulary),
        **result,
    }


@router.post("/ai/ask")
async def ai_ask(req: AskRequest):
    """
    对话式答疑：基于最近字幕上下文回答问题
    """
    course = await db.get_course(req.courseId)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")

    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    # 上下文：最近 100 条字幕（倒序取，反转为正序）
    subtitles = await db.get_subtitles(req.courseId, limit=100)
    context = " ".join(sub.get("text", "") for sub in reversed(subtitles))

    # 自定义术语（兼容 str/list 两种存储形态）
    custom_terms = _parse_custom_terms(course)

    answer = await asyncio.to_thread(
        chat_qa.answer,
        question,
        context,
        course.get("domain", ""),
        course.get("name", ""),
        custom_terms,
    )

    if answer is None:
        return {
            "available": False,
            "message": "AI 答疑服务不可用，请确认 Ollama 已启动或已配置云端 API",
        }

    return {"available": True, "answer": answer}


# ============ 术语库 API ============

@router.get("/courses/{course_id}/terminology")
async def get_terminology(course_id: str):
    """
    课程术语库总览

    返回三层术语（自定义 / 课程已识别 / 领域标准）+ 各自的释义覆盖情况，
    供前端展示"本课会重点识别哪些词"，也用于诊断识别优化是否生效。
    """
    course = await db.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")

    # 自定义术语：_row_to_dict 已把字段名转为 customTerms，但值是 JSON 字符串，需反序列化
    custom_terms = _parse_custom_terms(course)

    # 已识别术语：直接从词汇表取（这些词一定在课堂里出现过）
    vocabulary = await db.get_vocabulary(course_id)
    discovered = [v.get("word", "") for v in vocabulary if v.get("word")]

    from dictionary.terminology import build_terminology
    result = build_terminology(
        domain=course.get("domain", ""),
        custom_terms=custom_terms,
        discovered_terms=discovered,
    )

    # 补充运行中管道的信息（若正在监听，热词是运行时的最新状态）
    runtime = None
    if course_id in active_pipelines:
        try:
            runtime = active_pipelines[course_id].get_terminology().get("stats")
        except Exception:
            runtime = None

    return {
        **result,
        "courseVocabulary": len(vocabulary),
        "runtime": runtime,
    }


@router.get("/terminology/domains")
async def list_terminology_domains():
    """列出内置术语库覆盖的领域及其术语数量（供课程创建时的领域选择参考）"""
    from dictionary.terminology import DOMAIN_TERMS
    return {
        "domains": [
            {"name": name, "termCount": len(terms)}
            for name, terms in sorted(DOMAIN_TERMS.items(), key=lambda kv: -len(kv[1]))
        ]
    }


@router.post("/terminology/preview")
async def preview_terminology(req: PreviewTerminologyRequest):
    """
    术语库预览（不落库，纯计算）

    用于课程配置页实时展示：切换领域 / 增删自定义术语时，
    立即看到识别优化会覆盖哪些词、词库能直接给出释义的比例。
    """
    from dictionary.terminology import build_terminology
    return build_terminology(
        domain=req.domain,
        custom_terms=req.customTerms,
    )


# ============ 音频设备检测 ============

@router.get("/audio/check-loopback")
async def check_loopback():
    """检查 Loopback 设备是否可用，返回安装指引"""
    from audio.capture import AudioCapture

    devices = AudioCapture.list_devices()
    has_loopback = False
    system = platform.system()

    for d in devices:
        name = d["name"].lower()
        if system == "Windows":
            if "loopback" in name or "立体声混音" in name or "stereo mix" in name or "vb-cable" in name or "cable" in name:
                has_loopback = True
                break
        elif system == "Darwin":
            if "blackhole" in name or "soundflower" in name:
                has_loopback = True
                break
        elif system == "Linux":
            if "monitor" in name:
                has_loopback = True
                break

    guides = {
        "Windows": {
            "name": "VB-Cable",
            "url": "https://vb-audio.com/Cable/",
            "steps": [
                "下载 VB-Cable 安装程序",
                "右键以管理员身份运行",
                "重启电脑",
                "在声音设置中将 VB-Cable 设为默认播放设备",
            ],
        },
        "Darwin": {
            "name": "BlackHole",
            "url": "https://github.com/ExistentialAudio/BlackHole",
            "steps": [
                "打开终端运行: brew install blackhole-2ch",
                "打开「音频 MIDI 设置」",
                "创建多输出设备，勾选 BlackHole 和扬声器",
            ],
        },
        "Linux": {
            "name": "PulseAudio Loopback",
            "url": "",
            "steps": [
                "运行: pactl load-module module-loopback",
                "或安装 pavucontrol 图形化管理工具",
            ],
        },
    }

    return {
        "hasLoopback": has_loopback,
        "platform": system,
        "guide": guides.get(system, guides["Linux"]),
    }


# ============ 轮询 ASR（WebSocket 不可用时降级）============

@router.post("/asr/start")
async def start_asr(course_id: str = Query(...)):
    """启动 ASR 管道（轮询模式）"""
    course = await db.get_course(course_id)
    if not course:
        raise HTTPException(status_code=404, detail="课程不存在")

    if course_id in active_pipelines:
        return {"status": "already_running"}

    # 初始化消息队列
    asr_message_queues[course_id] = []

    def enqueue(msg_type: str, data: dict):
        if course_id in asr_message_queues:
            asr_message_queues[course_id].append({"type": msg_type, "data": data})
            # 限制队列长度
            if len(asr_message_queues[course_id]) > 100:
                asr_message_queues[course_id] = asr_message_queues[course_id][-50:]

    pipeline = ASRPipeline(
        course_id=course_id,
        course_config=_course_config_for_asr(course),
        on_subtitle=lambda data: enqueue("subtitle", data),
        on_interim=lambda text, words: enqueue("interim", {"text": text, "words": words}),
        on_status=lambda status, msg: enqueue("asr_status", {"status": status, "message": msg}),
        on_term_extracted=lambda term: _handle_term(term, course_id),
    )
    active_pipelines[course_id] = pipeline

    await pipeline.start()

    return {"status": "started"}


@router.post("/asr/stop")
async def stop_asr(course_id: str = Query(...)):
    """停止 ASR 管道"""
    if course_id in active_pipelines:
        await active_pipelines[course_id].stop()
        del active_pipelines[course_id]
    if course_id in asr_message_queues:
        del asr_message_queues[course_id]
    return {"status": "stopped"}


@router.get("/asr/poll")
async def poll_asr(course_id: str = Query(...)):
    """轮询获取 ASR 消息"""
    if course_id not in active_pipelines:
        return {"messages": [], "status": "not_running"}

    queue = asr_message_queues.get(course_id, [])
    messages = queue.copy()
    queue.clear()

    return {
        "messages": messages,
        "status": "running",
    }


# ============ WebSocket：实时 ASR（保留作为首选）============

@router.websocket("/ws/asr")
async def ws_asr(websocket: WebSocket, course_id: str = Query(...)):
    """
    WebSocket 端点：实时音频流 → ASR 识别 → 推送字幕
    """
    await websocket.accept()
    logger.info(f"ASR WebSocket 已连接: course_id={course_id}")

    course = await db.get_course(course_id)
    if not course:
        await websocket.send_json({"type": "error", "data": {"message": "课程不存在"}})
        await websocket.close()
        return

    if course_id in active_pipelines:
        pipeline = active_pipelines[course_id]
    else:
        pipeline = ASRPipeline(
            course_id=course_id,
            course_config=_course_config_for_asr(course),
            on_subtitle=lambda data: _safe_send(websocket, {"type": "subtitle", "data": data}),
            on_interim=lambda text, words: _safe_send(websocket, {"type": "interim", "data": {"text": text, "words": words}}),
            on_status=lambda status, msg: _safe_send(websocket, {"type": "asr_status", "data": {"status": status, "message": msg}}),
            on_term_extracted=lambda term: _handle_term(term, course_id),
        )
        active_pipelines[course_id] = pipeline

    try:
        await pipeline.start()

        while True:
            msg = await websocket.receive_text()
            data = json.loads(msg)
            if data.get("action") == "stop":
                await pipeline.stop()
                break
            elif data.get("action") == "pause":
                await pipeline.pause()
            elif data.get("action") == "resume":
                await pipeline.resume()

    except WebSocketDisconnect:
        logger.info(f"ASR WebSocket 断开: course_id={course_id}")
    except Exception as e:
        logger.error(f"ASR 错误: {e}")
        await _safe_send(websocket, {"type": "error", "data": {"message": str(e)}})
    finally:
        await pipeline.stop()
        if course_id in active_pipelines:
            del active_pipelines[course_id]


async def _safe_send(ws: WebSocket, data: dict):
    """安全发送 WebSocket 消息"""
    try:
        await ws.send_json(data)
    except Exception:
        pass


async def _handle_term(term_data: dict, course_id: str):
    """处理 AI 提取的术语，自动入库"""
    try:
        await db.upsert_vocabulary({
            "course_id": course_id,
            "word": term_data["word"],
            "translation": term_data.get("translation", ""),
            "definition": term_data.get("definition", ""),
            "domain": term_data.get("domain", ""),
            "context_sentence": term_data.get("context_sentence", ""),
            "source_subtitle_id": term_data.get("source_subtitle_id", ""),
            "isAutoGenerated": True,
        })
        logger.info(f"术语已入库: {term_data['word']}")
    except Exception as e:
        logger.error(f"术语入库失败: {e}")
