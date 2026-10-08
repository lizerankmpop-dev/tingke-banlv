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

APP_VERSION = "3.4.0"

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
    # 内置模型播种：**必须后台线程**（141MB 拷贝几秒到几十秒，同步做会拖长就绪
    # 时间，而主进程按「[backend] 就绪」标记解析端口 —— 卡住等于整个应用起不来）。
    # 幂等：模型已就绪时 `ensure_model` 只做一次大小校验就返回（毫秒级）。
    #
    # 放在这里的理由：模块文档的装配图一直写着「首次启动播种」，但此前没有调用点，
    # 播种只发生在引擎加载模型时 —— 于是首启向导第 3 步探测时可写目录还是空的，
    # 必然显示「模型准备失败」（M3 实装验收发现的阻断缺陷）。
    from asr.model_bootstrap import start_background_seed

    # 测试环境显式关掉：conftest 会设 TINGKEBANLV_SKIP_BACKGROUND_SEED=1。
    # 否则走 TestClient 的每个用例都会触发 lifespan，每个测试会话都真拷一遍 142MB 种子。
    if os.environ.get("TINGKEBANLV_SKIP_BACKGROUND_SEED") != "1":
        start_background_seed()
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


# ============ 后端自检 ============
# 场景：用户装完后「字幕不出来」，但没有能力翻日志、也描述不清现象。
# 让他直接跑
#     <安装目录>\resources\backend-server\tingkebanlv-server.exe --selftest
# 就能得到一份各项是否通过的清单，复制粘贴即可定位。
# 同时也用于打包后的冒烟验证（冻结包最容易漏的正是原生库与数据文件）。

SELFTEST_FIXTURE = Path(__file__).resolve().parent / "tests" / "fixtures" / "jfk.wav"


def _selftest_model_cached(model_size: str) -> bool:
    cache_dir = Path(_model_root()) / f"models--Systran--faster-whisper-{model_size}"
    snapshots = cache_dir / "snapshots"
    if not snapshots.is_dir():
        return False
    for snapshot in snapshots.iterdir():
        if not snapshot.is_dir():
            continue
        for bin_file in snapshot.glob("*.bin"):
            try:
                if bin_file.stat().st_size > 1024 * 1024:
                    return True
            except OSError:
                continue
    return False


def _model_root() -> str:
    if env_dir := os.environ.get("WHISPER_MODEL_DIR"):
        return env_dir
    if home := os.environ.get("TINGKEBANLV_HOME"):
        return str(Path(home) / "whisper_models")
    return str(Path.home() / ".tingkebanlv" / "whisper_models")


def run_selftest() -> int:
    import json

    results: list[dict] = []

    def check(name: str, fn, critical: bool = True):
        try:
            detail = fn()
            ok = True
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
        results.append({"name": name, "ok": ok, "critical": critical, "detail": str(detail)})
        if ok:
            print(f"[ OK ] {name}: {detail}")
        else:
            print(f"[{'FAIL' if critical else 'WARN'}] {name}: {detail}")
        return ok

    print("=" * 72)
    print("听课伴侣后端自检")
    print(f"  版本      : {APP_VERSION}")
    print(f"  冻结模式  : {getattr(sys, 'frozen', False)}")
    print(f"  可执行文件: {sys.executable}")
    print(f"  数据目录  : {os.environ.get('TINGKEBANLV_HOME') or '(默认 ~/.tingkebanlv)'}")
    print("=" * 72)

    check("Python 运行时", lambda: sys.version.split()[0])

    def _proxy_bypass():
        # 主进程的就绪探测依赖 /api/ping 快速返回；一旦回环流量走了系统代理，
        # 探测会被代理拖慢到超时，界面要等 90 秒才报「后端启动超时」。
        bypass = os.environ.get("NO_PROXY", "")
        if "127.0.0.1" not in bypass or "localhost" not in bypass:
            raise RuntimeError(f"NO_PROXY 未包含回环地址: {bypass!r}")
        return bypass

    check("回环流量绕过系统代理", _proxy_bypass)

    def _fastapi():
        import fastapi
        import uvicorn
        return f"fastapi {fastapi.__version__} / uvicorn {uvicorn.__version__}"

    check("FastAPI + uvicorn", _fastapi)

    def _whisper():
        import ctranslate2
        import faster_whisper
        return f"faster-whisper {faster_whisper.__version__} / ctranslate2 {ctranslate2.__version__}"

    check("faster-whisper + ctranslate2（原生库）", _whisper)

    def _onnx():
        import onnxruntime
        return f"onnxruntime {onnxruntime.__version__}"

    check("onnxruntime（Silero VAD ONNX 后端）", _onnx)

    def _audio():
        from audio.capture import AUDIO_BACKEND
        if AUDIO_BACKEND is None:
            raise RuntimeError("未安装任何音频采集库")
        return AUDIO_BACKEND

    check("音频采集后端", _audio)

    def _vocab():
        from dictionary.terminology import load_basic_vocab
        vocab = load_basic_vocab()
        if not vocab:
            raise RuntimeError("词表为空（basic_vocab.json 可能未随包携带）")
        return f"{len(vocab)} 词"

    check("内置词表", _vocab)

    def _terms():
        from dictionary.terminology import build_terminology
        # 领域名用前端 CourseConfig.tsx 中的中文键，与真实调用保持一致
        # `language="zh"`：自检必须覆盖「中文课不产出英文短语层」这条（缺陷 b）。
        # 漏传 language 时这里会退回 auto、短语层照旧生成 —— 自检就测不到该回归。
        built = build_terminology(
            domain="计算机",
            custom_terms=["backpropagation", "convolutional"],
            language="zh",
        )
        phrases = dict(built.get("sections") or []).get("phrases") or []
        return (
            f"术语 {built['stats']['total']} 个，"
            f"热词提示 {built['promptChars']} 字符，"
            f"短语层 {len(phrases)} 条"
        )

    check("术语热词库", _terms)

    def _llm():
        from llm.client import get_provider, is_available

        provider = get_provider()
        available = is_available()
        if provider == "cloud":
            if not available:
                raise RuntimeError("云端 LLM 配置不完整，需设置 BASE / KEY / MODEL")
            return "provider=cloud，配置完整"
        return (
            "provider=ollama，本地服务已就绪"
            if available
            else "provider=ollama，本地服务未运行（AI 讲解/答疑会自动跳过，不影响字幕）"
        )

    check("LLM（AI 讲解 / 答疑）", _llm, critical=False)

    def _dist():
        resolved = _resolve_dist_dir()
        if resolved is None:
            raise RuntimeError("未找到 index.html（仅开发模式可接受）")
        return str(resolved)

    check("前端构建产物", _dist, critical=False)

    def _db():
        import asyncio as _asyncio

        async def _run():
            await db.initialize()
            await db.close()

        _asyncio.run(_run())
        return "数据库可读写"

    check("数据库", _db)

    from asr.model_bootstrap import check_installed, ensure_model, seed_available
    from asr.whisper_engine import DEFAULT_MODEL

    def _seed():
        info = seed_available()
        if not info["available"]:
            raise RuntimeError(info["detail"])
        return info["detail"]

    # 安装版必须内置模型，缺了就是打包错误；开发模式没生成种子属正常
    check("内置模型种子", _seed, critical=bool(getattr(sys, "frozen", False)))

    def _ensure():
        # 契约是 result["ok"]，不是 action 字符串：
        # 播种失败时 action 可能是 "verify-failed"/"no-seed"/"error"，
        # 只判 "unavailable" 会把「播种后 sha256 仍不通」误报成通过。
        result = ensure_model(DEFAULT_MODEL, deep_check=True)
        if not result["ok"]:
            raise RuntimeError(f"{result['action']}：{result['detail']}")
        return f"{result['action']}：{result['detail']}"

    check("模型完整性（sha256，损坏自动修复）", _ensure)

    cached = _selftest_model_cached(DEFAULT_MODEL)

    def _model_status():
        detail = check_installed(DEFAULT_MODEL, deep=True)
        return (
            f"{DEFAULT_MODEL} 已缓存且校验通过（{_model_root()}）"
            if detail["ok"]
            else f"{DEFAULT_MODEL} 不可用：{'; '.join(detail['problems']) or detail['reason']}"
        )

    check("Whisper 模型缓存", _model_status, critical=False)

    if cached and SELFTEST_FIXTURE.is_file():
        def _transcribe():
            import wave

            import numpy as np
            from faster_whisper import WhisperModel

            with wave.open(str(SELFTEST_FIXTURE), "rb") as wav:
                channels = wav.getnchannels()
                width = wav.getsampwidth()
                rate = wav.getframerate()
                frames = wav.readframes(wav.getnframes())
            if width != 2:
                raise RuntimeError(f"自检音频位深非 16bit: {width * 8}")
            pcm = np.frombuffer(frames, dtype=np.int16)
            if channels > 1:
                pcm = pcm.reshape(-1, channels).mean(axis=1).astype(np.int16)
            audio = pcm.astype(np.float32) / 32768.0
            if rate != 16000:
                raise RuntimeError(f"自检音频采样率非 16k: {rate}")

            from asr.whisper_engine import COMPUTE_TYPE, DEVICE

            def _load(local_only: bool):
                return WhisperModel(
                    DEFAULT_MODEL,
                    device=DEVICE,
                    compute_type=COMPUTE_TYPE,
                    num_workers=1,
                    download_root=_model_root(),
                    local_files_only=local_only,
                )

            note = ""
            try:
                # 先强制离线：缓存必须**真能被加载**才算通过。
                # 这正是「快照报下载成功但留下 0 字节 model.bin」「缺 refs/main」
                # 这类问题的分水岭 —— 只在联网时才成功的话，用户断网就崩。
                model = _load(True)
            except Exception as offline_exc:
                print(f"       本地缓存无法直接加载（{type(offline_exc).__name__}: {offline_exc}）")
                print("       尝试联网修复后重新加载…")
                model = _load(False)
                note = "（本地缓存不完整，已联网修复）"

            segments, _info = model.transcribe(audio, language="en", beam_size=1)
            text = " ".join(s.text.strip() for s in segments).strip()
            if not text:
                raise RuntimeError("识别结果为空")
            return f"{len(audio) / 16000:.1f}s 音频 -> \"{text[:70]}\"{note}"

        check("完整识别链路（真实音频）", _transcribe)
    elif cached:
        check("完整识别链路（真实音频）", lambda: f"自检音频缺失: {SELFTEST_FIXTURE}", critical=False)
    else:
        print("[SKIP] 完整识别链路（真实音频）: 模型未缓存，先联网下载后再自检")

    failed = [r for r in results if not r["ok"] and r["critical"]]
    print("=" * 72)
    print(f"结论: {'全部通过' if not failed else f'{len(failed)} 项关键检查未通过'}")
    print(json.dumps({"selftest": results, "passed": not failed}, ensure_ascii=False, indent=2))
    return 1 if failed else 0


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

    # 自检模式：只验证冻结包完整性与模型可用性，不启动 HTTP 服务
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())

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
