"""
听课伴侣 Backend - 主入口
启动 FastAPI 服务，管理课程数据、ASR 管道和词典服务

打包说明：
    生产环境下同一进程同时提供 API 与前端静态资源（frontend/dist），
    前端与后端同源，因此前端的 fetch("/api/...") 无需代理即可工作。
    开发环境不挂载静态资源，前端由 Vite dev server 提供并代理 /api。
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router
from dependencies import db

APP_VERSION = "3.0.0"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def _resolve_dist_dir() -> Path | None:
    """
    定位前端构建产物目录（兼容开发/打包两种布局）

    打包后 Electron 通过 extraResources 把 dist 放到 resourcesPath/dist，
    而后端位于 resourcesPath/backend，因此 ../dist 命中；
    开发时后端在 repo/backend，../frontend/dist 命中。
    """
    if env_dir := os.environ.get("TINGKEBANLV_DIST_DIR"):
        p = Path(env_dir)
        return p if (p / "index.html").is_file() else None

    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "dist",               # 打包：resourcesPath/dist
        here.parent / "frontend" / "dist",  # 开发：repo/frontend/dist
    ]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None



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
    version=APP_VERSION,
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
        "version": APP_VERSION,
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


# ============ 前端静态资源托管 ============
# 必须放在所有 API 路由之后注册：Starlette 按注册顺序匹配，
# 挂载在 "/" 的静态资源是兜底，不能抢占 /api 路由。

_dist_dir = _resolve_dist_dir()
if _dist_dir is not None:
    app.mount("/", StaticFiles(directory=str(_dist_dir), html=True), name="frontend")
    logger.info(f"前端静态资源已挂载: {_dist_dir}")
else:
    logger.info("未发现前端构建产物，仅提供 API（开发模式请使用 Vite dev server）")


if __name__ == "__main__":
    import uvicorn

    PORT = int(os.environ.get("TINGKEBANLV_PORT", "8765"))
    # 桌面应用只监听本机回环地址：绑 0.0.0.0 会把课程数据暴露给同一局域网的其他设备
    HOST = os.environ.get("TINGKEBANLV_HOST", "127.0.0.1")

    # 打包后（PyInstaller 置 sys.frozen）必须关闭 reload：
    # reload 会额外拉起一个 reloader 父进程，既拖慢启动又让进程清理变复杂
    _default_reload = "0" if getattr(sys, "frozen", False) else "1"
    RELOAD = os.environ.get("TINGKEBANLV_RELOAD", _default_reload) == "1"

    logger.info(f"启动服务 host={HOST} port={PORT} reload={RELOAD}")

    if RELOAD:
        # reload 模式要求传入 import 字符串
        uvicorn.run("main:app", host=HOST, port=PORT, reload=True, log_level="info")
    else:
        # 非 reload 模式直接传 app 对象，避免打包后 import 路径解析失败
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
