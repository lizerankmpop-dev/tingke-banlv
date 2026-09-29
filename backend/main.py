"""
听课伴侣 Backend - 主入口
启动 FastAPI 服务，管理课程数据、ASR 管道和词典服务
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import router
from dependencies import db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    await db.initialize()
    logger.info("数据库初始化完成")
    yield
    await db.close()
    logger.info("数据库已关闭")


app = FastAPI(
    title="听课伴侣 API",
    version="0.1.0",
    description="听课伴侣 - 实时字幕与交互式单词学习后端服务",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/api/health")
async def health_check():
    """健康检查"""
    from asr.whisper_engine import DEVICE, COMPUTE_TYPE
    gpu_name = None
    if DEVICE == "cuda":
        try:
            import torch
            gpu_name = torch.cuda.get_device_name(0)
        except Exception:
            pass
    return {
        "status": "ok",
        "version": "0.1.0",
        "whisper_available": check_whisper(),
        "ollama_available": await check_ollama(),
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "gpu_name": gpu_name,
    }


def check_whisper() -> bool:
    """检查 Faster-Whisper 是否可用"""
    try:
        from faster_whisper import WhisperModel
        return True
    except ImportError:
        return False


async def check_ollama() -> bool:
    """检查 Ollama 是否可用"""
    try:
        import ollama
        models = ollama.list()
        return len(models.get('models', [])) > 0
    except Exception:
        return False


if __name__ == "__main__":
    import uvicorn
    PORT = int(os.environ.get("TINGKEBANLV_PORT", "8765"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=PORT,
        reload=True,
        log_level="info",
    )
