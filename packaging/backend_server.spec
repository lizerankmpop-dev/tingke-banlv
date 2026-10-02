# -*- mode: python ; coding: utf-8 -*-
"""
听课伴侣 后端冻结打包（PyInstaller spec）

为什么需要这个文件
------------------
安装包的目标机器上**没有 Python，也没有任何第三方依赖**。
主进程原有的 `resolvePythonCommand()` 会去探测系统 Python 并用
`python -c "import fastapi, uvicorn"` 验证，在干净的用户机器上必然全部失败，
应用卡在「未找到可用的 Python 环境」直接退出。
因此后端必须冻结成自带解释器与依赖的独立可执行文件。

产物布局（--onedir，与 electron-builder 的 extraResources 对齐）
----------------------------------------------------------------
    packaging/out/backend-server/
        tingkebanlv-server.exe      ← 主进程优先启动它
        _internal/                  ← 解释器 + 依赖 + 原生库

调用方式
--------
    cd <repo>
    <venv>/Scripts/python.exe -m PyInstaller packaging/backend_server.spec \
        --noconfirm --distpath packaging/out --workpath packaging/build

注意
----
* 必须 `console=True`：主进程靠子进程的 stdout/stderr 把后端日志写进
  <TINGKEBANLV_HOME>/logs/electron.log，并保留最后 40 行用于失败弹窗。
  控制台窗口不会真的弹出来 —— 主进程 spawn 时带 windowsHide: true。
* 模型权重**不打进包里**（base 139MB / small 464MB），运行时下载到
  ~/.tingkebanlv/whisper_models，见 asr/whisper_engine.py。
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

SPEC_DIR = Path(SPECPATH).resolve()
REPO_ROOT = SPEC_DIR.parent
BACKEND_DIR = REPO_ROOT / "backend"

if not (BACKEND_DIR / "main.py").is_file():
    raise SystemExit(f"未找到后端入口: {BACKEND_DIR / 'main.py'}")

# ---- 数据文件 ----
datas = []
# 内置词表现 3804 词，是术语热词注入与本地词典的运行时依赖，必须随包携带
for name in ("basic_vocab.json",):
    candidate = BACKEND_DIR / "dictionary" / name
    if candidate.is_file():
        datas.append((str(candidate), "dictionary"))
    else:
        raise SystemExit(f"缺少词表文件: {candidate}")

# 自检音频：`tingkebanlv-server.exe --selftest` 会用它真实跑一次识别，
# 验证「模型能加载 + 能出字」这两件最难通过静态检查确认的事。
fixture_wav = BACKEND_DIR / "tests" / "fixtures" / "jfk.wav"
if fixture_wav.is_file():
    datas.append((str(fixture_wav), "tests/fixtures"))
else:
    print(f"[spec] 未找到自检音频，--selftest 将跳过识别链路检查: {fixture_wav}")

# ---- 需要连数据/原生库一起收集的包 ----
# 这些包要么自带 .dll/.so，要么自带模型/词表等非 .py 文件，
# 只靠静态分析会漏，运行时才在 import 或首次调用时报错。
COLLECT_ALL = [
    "ctranslate2",      # ctranslate2.dll 等原生库，缺了就加载不了任何模型
    "faster_whisper",   # 自带 VAD 资源与分词器资源
    "onnxruntime",      # onnxruntime.dll + provider 库（Silero VAD 走 ONNX 后端）
    "silero_vad",       # data/silero_vad.onnx 就藏在包目录里
    "av",               # PyAV：自带 ffmpeg 动态库
]

binaries = []
hiddenimports = []

for pkg in COLLECT_ALL:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(pkg)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception as exc:  # 某个包没装不应让整个打包失败
        print(f"[spec] 收集 {pkg} 失败（跳过）: {exc}")

# 音频采集：Windows 上是 PyAudioWPatch（发布为 pyaudiowpatch 模块名），
# 其余平台是 pyaudio。两者都是单个扩展模块，静态分析不一定能跟上别名导入。
for pkg in ("pyaudiowpatch", "pyaudio"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass
    hiddenimports.append(pkg)

# uvicorn 的协议/worker 实现是运行时按字符串动态导入的，静态分析抓不到
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "anyio._backends._asyncio",
    "aiosqlite",
    "httpx",
    "silero_vad",
]

# torch 只是「如果装了就用它探测 CUDA」的可选依赖（见 asr/whisper_engine.py 的
# _detect_device）。刻意**不**打包 torch：它会额外带来 2GB+ 体积，
# 而流式转写跑在 CPU + int8 上，收益与体积不成比例。
#
# hf_xet 也是可选依赖：huggingface_hub 对它的 import 都在
# `except ImportError` 或 is_xet_available() 分支里，而我们已由
# HF_HUB_DISABLE_XET=1 明确关闭 Xet（镜像站根本不代理它的 CAS 服务）。
# 排除它可省下 9MB，且不影响任何下载路径。
excludes = [
    "torch", "torchaudio", "torchvision",
    "hf_xet",
    "tkinter", "matplotlib", "pandas", "scipy", "PIL",
    "IPython", "pytest", "setuptools._distutils",
]

a = Analysis(
    [str(BACKEND_DIR / "main.py")],
    pathex=[str(BACKEND_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="tingkebanlv-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX 压缩过 ctranslate2/onnxruntime 的 DLL 容易触发杀软误报
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="backend-server",
)
