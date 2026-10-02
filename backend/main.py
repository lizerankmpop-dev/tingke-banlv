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
import time
from pathlib import Path
from contextlib import asynccontextmanager

# ============ 标准输出统一为 UTF-8 ============
# 冻结成 exe 后 stdout 由 Electron 用管道接管，Python 会按系统区域代码页
# （简体中文 Windows 上是 GBK/cp936）编码输出，而主进程按 UTF-8 解码后写入
# electron.log —— 结果日志里的中文全部变成乱码，偏偏排障最需要看的
# 就是那几行中文错误信息。
#
# 必须加两道前提，否则会误伤宿主环境：
#   1) 只处理「进程自己的」输出流 —— 若 sys.stdout 已被宿主替换
#      （pytest 捕获、Jupyter、部分 IDE 都会替换），一律不碰。
#      实测对 pytest 的捕获流调用 reconfigure 会让它在收尾时抛
#      "I/O operation on closed file"，整个测试会话崩掉。
#   2) fileno() 可用 —— 兜底拦掉没有真实文件描述符的流对象。
#
# errors="replace" 保证即使出现无法编码的字符也不会把日志线程炸掉。
def _force_utf8_streams() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or stream is not getattr(sys, f"__{name}__", None):
            continue  # 已被宿主替换的流不属于我们，不要动
        if not hasattr(stream, "reconfigure"):
            continue
        try:
            if stream.fileno() < 0:
                continue
        except (OSError, ValueError):
            # io.UnsupportedOperation 同时是 OSError 与 ValueError 的子类：
            # 走到这里说明流背后没有真实文件描述符，跳过即可
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_streams()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.routes import router
from dependencies import db

APP_VERSION = "3.0.0"

# ============ 回环流量必须绕过系统代理 ============
# 企业/校园网络常预设 HTTP_PROXY / HTTPS_PROXY，而 httpx 与 urllib 默认会信任
# 这两个环境变量 —— 于是连对 127.0.0.1 的请求也会被丢给代理服务器。
# 实测后果（打包后首次启动复现）：
#   代理对 http://127.0.0.1:11434/api/tags 返回 502，耗时整整 2 秒；
#   而 /api/health 里的 LLM 探测是同步等待它的，于是 health 接口要 2 秒才返回；
#   Electron 主进程的就绪探测超时设为 2 秒 → 永远探测失败 →
#   界面整整 90 秒后才弹「后端启动超时」。
# 回环流量本就不该走代理，这里显式并入 NO_PROXY（两个大小写都设，避免遗漏）。
_PROXY_BYPASS = "127.0.0.1,localhost,::1"
for _proxy_var in ("NO_PROXY", "no_proxy"):
    _existing_bypass = os.environ.get(_proxy_var, "").strip()
    os.environ[_proxy_var] = ",".join(filter(None, [_existing_bypass, _PROXY_BYPASS]))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


def _resolve_dist_dir() -> Path | None:
    """
    定位前端构建产物目录（兼容开发/源码打包/冻结 exe 三种布局）

    三种布局下的落点各不相同，逐一列出避免再次「猜路径」：
      开发：      repo/frontend/dist                       （后端在 repo/backend）
      源码打包：  resourcesPath/dist                        （后端被 extraResources 放到 resourcesPath/backend）
      冻结 exe：  resourcesPath/dist                        （exe 在 resourcesPath/backend-server/tingkebanlv-server.exe）

    冻结后 __file__ 指向 _internal 内的模块，`__file__.parent.parent` 不再是
    resourcesPath，因此必须改从 sys.executable 反推；主进程也会显式传入
    TINGKEBANLV_DIST_DIR 作为第一优先级（上面那支）。
    """
    if env_dir := os.environ.get("TINGKEBANLV_DIST_DIR"):
        p = Path(env_dir)
        return p if (p / "index.html").is_file() else None

    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "dist",               # 源码打包：resourcesPath/dist
        here.parent / "frontend" / "dist",  # 开发：repo/frontend/dist
    ]

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        frozen_candidates = [
            exe_dir.parent / "dist",        # backend-server/ 的上一级
            exe_dir / "dist",
        ]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            frozen_candidates.append(Path(meipass).resolve().parent.parent / "dist")
        candidates = frozen_candidates + candidates

    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    logger.warning(
        "未找到前端构建产物，已尝试的路径: "
        + ", ".join(str(c) for c in candidates)
    )
    return None



@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    await db.initialize()
    logger.info("数据库初始化完成")
    # LLM 可用性在后台定期探测：health 接口只读缓存，绝不阻塞请求
    probe_task = asyncio.create_task(_llm_status_loop())
    yield
    probe_task.cancel()
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


# ============ LLM 可用性缓存 ============
# 探测本身要走一次 HTTP（本地 Ollama 或云端），最慢可能好几秒。
# 把它塞进 /api/health 会让这个最常用的探活接口变成慢接口，
# 并直接拖垮 Electron 的就绪探测（见下方 /api/health 注释）。
# 因此改为：后台每 60 秒刷新一次，接口只读缓存。
_LLM_PROBE_INTERVAL = float(os.environ.get("TINGKEBANLV_LLM_PROBE_INTERVAL", "60"))
_llm_status: dict = {"available": False, "provider": "ollama", "checked_at": 0.0}


async def _refresh_llm_status() -> None:
    from llm.client import get_provider, is_available

    _llm_status["provider"] = get_provider()
    try:
        _llm_status["available"] = await asyncio.to_thread(is_available)
    except Exception as exc:
        logger.debug(f"LLM 可用性探测失败: {exc}")
        _llm_status["available"] = False
    _llm_status["checked_at"] = time.time()


async def _llm_status_loop() -> None:
    while True:
        await _refresh_llm_status()
        await asyncio.sleep(_LLM_PROBE_INTERVAL)


@app.get("/api/ping")
async def ping():
    """
    极轻量存活探测

    不碰数据库、不碰外部服务、不做任何 await —— 供 Electron 主进程判断
    「后端是否已经能接受请求」。就绪探测绝不能用带副作用的接口：
    /api/health 曾因为内含一次外部 LLM 探测而耗时 2 秒，
    恰好超过主进程 2 秒的探测超时，导致应用永远等不到就绪。
    """
    return {"ok": True, "pid": os.getpid(), "version": APP_VERSION}


@app.get("/api/health")
async def health_check():
    """
    健康检查（诊断用）

    这里刻意只读缓存：任何一次真实探测都可能耗时数秒，
    而主进程/前端都可能高频调用本接口。
    """
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
        "llm_provider": _llm_status["provider"],
        "llm_available": _llm_status["available"],
        "llm_checked_at": _llm_status["checked_at"],
        # 兼容旧字段名
        "ollama_available": _llm_status["available"],
        "device": DEVICE,
        "compute_type": COMPUTE_TYPE,
        "gpu_name": gpu_name,
    }


@app.post("/api/llm/probe")
async def probe_llm():
    """立即重新探测 LLM 可用性（设置页手动检查时使用）"""
    await _refresh_llm_status()
    return {
        "provider": _llm_status["provider"],
        "available": _llm_status["available"],
        "checked_at": _llm_status["checked_at"],
    }


def check_whisper() -> bool:
    """检查 Faster-Whisper 是否可用"""
    try:
        from faster_whisper import WhisperModel
        return True
    except ImportError:
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
