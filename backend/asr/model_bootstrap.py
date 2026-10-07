"""
内置模型的播种与完整性校验

解决的问题
----------
安装包内置了 base 模型（放在安装目录 `resources/whisper_models/`），
但模型不能直接放在程序目录里用：

1. **要与程序本体分离**。程序目录在 `Program Files` 或
   `%LOCALAPPDATA%\\Programs` 下，可能只读、且重装即被覆盖；
   后续想单独更新模型也不该让用户重装整个应用。
   → 首次启动把模型**播种**到应用本地数据目录（默认 `~/.tingkebanlv/whisper_models`）。

2. **损坏要能自愈**。磁盘错误、杀软误删、上次下载中断都会留下残缺的
   model.bin，而 faster-whisper 只在加载时抛 "model.bin is incomplete"，
   用户完全不知道该干什么。
   → 用 manifest.json（sha256 + 大小）校验，损坏就从安装包资源目录重新拷贝，
     **不联网**。

3. **离线必须可用**。内置模型的意义就是首启即用，因此加载时若本地已有
   完整模型，走 local_files_only 分支，绝不因为联网校验失败而卡住。

装配关系
--------
    packaging/seed/whisper_models/          ← 构建期生成（scripts/prepare_seed_model.py）
        manifest.json
        models--Systran--faster-whisper-base/{refs/main, snapshots/seed/*}
    ↓ electron-builder extraResources（不进 asar）
    <安装目录>/resources/whisper_models/     ← 只读的「种子」
    ↓ 首次启动播种（本模块）
    ~/.tingkebanlv/whisper_models/           ← 可写的工作副本
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
SNAPSHOT_NAME = "seed"
REQUIRED_FILES = ("model.bin", "config.json")

# 完整性验证标记：首次校验通过后落盘，避免每次启动都重算 145MB 的 sha256
MARKER_NAME = ".seed-integrity.json"
SEED_SIZES = ("base",)
INTEGRITY_SCHEMA = 1

_copy_lock = threading.Lock()


# ============ 路径解析 ============

def model_root() -> Path:
    """
    模型工作目录（可写）

    与 asr/whisper_engine.py 共用同一套解析规则，避免两处漂移。
    """
    if env_dir := os.environ.get("WHISPER_MODEL_DIR"):
        return Path(env_dir)
    if home := os.environ.get("TINGKEBANLV_HOME"):
        return Path(home) / "whisper_models"
    return Path.home() / ".tingkebanlv" / "whisper_models"


def seed_root() -> Path | None:
    """
    随包分发的模型种子目录（只读）

    解析顺序：
      1. TINGKEBANLV_SEED_MODEL_DIR（主进程显式告知，最可靠）
      2. 冻结模式：<exe 所在目录>/../whisper_models
         —— exe 在 resources/backend-server/，因此命中 resources/whisper_models
      3. 开发模式：<repo>/packaging/seed/whisper_models
    """
    if env_dir := os.environ.get("TINGKEBANLV_SEED_MODEL_DIR"):
        p = Path(env_dir)
        return p if p.is_dir() else None

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates += [exe_dir.parent / "whisper_models", exe_dir / "whisper_models"]

    repo_root = Path(__file__).resolve().parents[2]
    candidates.append(repo_root / "packaging" / "seed" / "whisper_models")

    for candidate in candidates:
        if (candidate / MANIFEST_NAME).is_file():
            return candidate
    return None


# ============ 校验 ============

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(seed_dir: Path | None) -> dict | None:
    if seed_dir is None:
        return None
    path = seed_dir / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"模型清单解析失败: {exc}")
        return None
    if data.get("schema") != INTEGRITY_SCHEMA or not data.get("files"):
        logger.warning("模型清单结构不符合预期，按不可用处理")
        return None
    return data


def _manifest_size_map(manifest: dict) -> dict[str, int]:
    return {f["name"]: int(f["size"]) for f in manifest["files"]}


def installed_snapshot(root: Path, size: str) -> Path | None:
    """
    找到已安装模型所在的快照目录

    不假定快照目录名（HF 用 commit hash，内置种子用 "seed"），
    只要求该目录里有完整的必需文件。
    """
    snapshots = root / f"models--Systran--faster-whisper-{size}" / "snapshots"
    if not snapshots.is_dir():
        return None
    for snap in sorted(snapshots.iterdir()):
        if snap.is_dir() and all((snap / name).is_file() for name in REQUIRED_FILES):
            return snap
    return None


def check_installed(size: str = "base", deep: bool = False) -> dict:
    """
    校验已安装模型

    deep=False：只比大小（毫秒级，用于每次启动的快路径）
    deep=True ：逐个文件算 sha256（秒级，用于首次启动与 --selftest）
    """
    root = model_root()
    snapshot = installed_snapshot(root, size)
    if snapshot is None:
        return {"ok": False, "reason": "missing", "snapshot": None, "problems": ["未找到模型文件"]}

    manifest = load_manifest(seed_root())
    problems: list[str] = []

    if manifest and manifest.get("size") == size:
        expected = _manifest_size_map(manifest)
        for name, want in expected.items():
            target = snapshot / name
            if not target.is_file():
                problems.append(f"缺少 {name}")
                continue
            actual = target.stat().st_size
            if actual != want:
                problems.append(f"{name} 大小不符（{actual} != {want}）")
        if deep and not problems:
            for entry in manifest["files"]:
                target = snapshot / entry["name"]
                if _sha256(target) != entry["sha256"]:
                    problems.append(f"{entry['name']} 校验和不通")
    else:
        # 清单不可用（例如用户自己下载的 small/medium 模型）：退化为大小检查。
        # model.bin 单独要求 >1MB —— 「快照报成功却留下 0 字节文件」是实际发生过的坑。
        for name in REQUIRED_FILES:
            target = snapshot / name
            if not target.is_file():
                problems.append(f"缺少 {name}")
                continue
            size_bytes = target.stat().st_size
            if name == "model.bin" and size_bytes < 1024 * 1024:
                problems.append("model.bin 过小（疑似 0 字节或被截断）")
            elif size_bytes < 1024:
                problems.append(f"{name} 过小")

    return {
        "ok": not problems,
        "reason": "ok" if not problems else "corrupt",
        "snapshot": str(snapshot),
        "problems": problems,
    }


def _marker_path(size: str) -> Path:
    return model_root() / f".seed-integrity-{size}.json"


def has_verified_marker(size: str = "base") -> bool:
    """是否做过一次完整的 hash 校验（避免每次启动都重算）"""
    path = _marker_path(size)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    snapshot = installed_snapshot(model_root(), size)
    return bool(data.get("verified")) and data.get("snapshot") == (str(snapshot) if snapshot else None)


def write_verified_marker(size: str = "base", snapshot: str | None = None) -> None:
    try:
        _marker_path(size).write_text(
            json.dumps(
                {"verified": True, "size": size, "snapshot": snapshot, "at": time.time()},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.debug(f"写入完整性标记失败（不影响运行）: {exc}")


# ============ 播种 ============

def _seed_files(seed_dir: Path, manifest: dict) -> list[tuple[Path, str]]:
    repo_id = manifest["repoId"]
    model_dir = seed_dir / f"models--{repo_id.replace('/', '--')}"
    snap = manifest.get("snapshot") or SNAPSHOT_NAME
    snapshot_dir = model_dir / "snapshots" / snap
    files: list[tuple[Path, str]] = []
    for entry in manifest["files"]:
        src = snapshot_dir / entry["name"]
        if src.is_file():
            files.append((src, entry["name"]))
    return files


def seed_model(size: str = "base", reason: str = "missing") -> dict:
    """
    把内置模型播种（或修复）到工作目录

    返回 {"ok": bool, "action": str, "detail": str}
    """
    seed_dir = seed_root()
    if seed_dir is None:
        return {"ok": False, "action": "no-seed", "detail": "未找到内置模型种子目录"}

    manifest = load_manifest(seed_dir)
    if manifest is None:
        return {"ok": False, "action": "no-seed", "detail": f"种子清单不可用: {seed_dir / MANIFEST_NAME}"}
    if manifest.get("size") != size:
        return {
            "ok": False,
            "action": "no-seed",
            "detail": f"内置种子只有 {manifest.get('size')}，不含 {size}",
        }

    files = _seed_files(seed_dir, manifest)
    if len(files) != len(manifest["files"]):
        return {"ok": False, "action": "no-seed", "detail": "种子目录文件不完整"}

    root = model_root()
    root.mkdir(parents=True, exist_ok=True)
    model_dir = root / f"models--Systran--faster-whisper-{size}"
    snapshot_dir = model_dir / "snapshots" / SNAPSHOT_NAME

    total_mb = sum(src.stat().st_size for src, _ in files) / 1048576
    logger.info(f"开始播种内置模型（{size}，{total_mb:.1f}MB，原因: {reason}）")

    started = time.time()
    try:
        # 先清掉可能残缺的旧目录：留着只会让下一次加载继续报错
        if snapshot_dir.parent.is_dir():
            for stale in snapshot_dir.parent.iterdir():
                if stale.is_dir() and stale != snapshot_dir:
                    shutil.rmtree(stale, ignore_errors=True)
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        for src, name in files:
            shutil.copy2(src, snapshot_dir / name)

        # refs/main 决定 huggingface_hub 认为「当前版本」是哪个快照，
        # 少了它 local_files_only 加载会直接报找不到 snapshot
        refs = model_dir / "refs" / "main"
        refs.parent.mkdir(parents=True, exist_ok=True)
        refs.write_text(SNAPSHOT_NAME, encoding="utf-8")
    except Exception as exc:
        logger.error(f"播种内置模型失败: {type(exc).__name__}: {exc}")
        return {"ok": False, "action": "error", "detail": f"{type(exc).__name__}: {exc}"}

    elapsed = time.time() - started
    verified = check_installed(size, deep=True)
    if verified["ok"]:
        write_verified_marker(size, verified["snapshot"])
        logger.info(f"内置模型播种完成并校验通过（{elapsed:.1f}s）")
        return {
            "ok": True,
            "action": reason,
            "detail": f"{total_mb:.1f}MB，耗时 {elapsed:.1f}s，sha256 校验通过",
        }

    logger.error(f"播种后校验仍不通过: {verified['problems']}")
    return {"ok": False, "action": "verify-failed", "detail": "; ".join(verified["problems"])}


def ensure_model(size: str = "base", deep_check: bool = False) -> dict:
    """
    确保模型可用：已装且完整就直接返回，否则从内置种子播种

    这是启动路径上的入口，因此默认只做**大小**校验（毫秒级）；
    deep_check=True 时额外算 sha256，用于首次启动与 --selftest。

    **判断可用性请看 `ok`，不要只看 action 字符串** —— 失败时 action 可能是
    `unavailable`（无种子）、`no-seed`（种子不含该尺寸）、
    `verify-failed`（播种后校验仍不通过）或 `error`（IO 失败），
    四者的共同点只有 ok=False。action 仅用于写日志和给用户看。

    返回 {"ok": bool, "action": str, "detail": str}
    """
    with _copy_lock:
        status = check_installed(size, deep=deep_check or not has_verified_marker(size))
        if status["ok"]:
            if deep_check or not has_verified_marker(size):
                write_verified_marker(size, status["snapshot"])
            return {"ok": True, "action": "verified", "detail": status["snapshot"] or ""}

        seed_dir = seed_root()
        if seed_dir is None:
            # 没有内置种子（例如开发模式没生成、或用户手动删了资源目录）：
            # 交给上层走原有的联网下载路径，这里不算错误
            return {
                "ok": False,
                "action": "unavailable",
                "detail": f"模型不完整（{'; '.join(status['problems']) or status['reason']}）且无内置种子，将尝试联网下载",
            }

        reason = "seeded" if status["reason"] == "missing" else "repaired"
        return seed_model(size, reason=reason)


def seed_available() -> dict:
    """内置种子是否可用（供 --selftest 报告）"""
    seed_dir = seed_root()
    if seed_dir is None:
        return {"available": False, "detail": "未找到 TINGKEBANLV_SEED_MODEL_DIR / resources/whisper_models"}
    manifest = load_manifest(seed_dir)
    if manifest is None:
        return {"available": False, "detail": f"清单不可用: {seed_dir / MANIFEST_NAME}"}
    expected = _manifest_size_map(manifest)
    found = {name for _, name in _seed_files(seed_dir, manifest)}
    missing = [name for name in expected if name not in found]
    total_mb = manifest.get("totalBytes", 0) / 1048576
    if missing:
        return {"available": False, "detail": f"种子文件缺失: {missing}"}
    return {
        "available": True,
        "detail": f"{manifest.get('size')} {total_mb:.1f}MB，{len(expected)} 个文件，位于 {seed_dir}",
    }


# ============ 后台播种（首启向导第 3 步「正在准备」的来源） ============
#
# 为什么需要它：`ensure_model` 原先只在**引擎加载模型**时调用，而首启向导第 3 步
# 探测的是「模型文件是否已就位」—— 两者时机错位，于是全新安装的用户在第 3 步
# 必然看到「模型准备失败」，而且点「重试」也不会触发播种（M3 实装验收发现，
# 六步正常路径被阻断；本文件顶部的装配关系图本来就写着「首次启动播种」，
# 但那个调用点此前并不存在）。
#
# 这三条是设计约束，不是实现细节：
#   · **必须后台线程**：141MB 拷贝要几秒到几十秒，同步做会让后端就绪时间
#     被拖长（主进程按「[backend] 就绪」标记解析端口，卡住等于整个应用起不来）。
#   · **必须幂等**：`ensure_model` 自己先 check_installed，已就绪时毫秒级返回；
#     并且与引擎加载共用 `_copy_lock`，既不会重复拷贝，也不会和识别抢文件。
#   · **失败不崩溃**：异常与失败原因落在状态里，交给 `/api/asr/model-status`
#     暴露给前端 —— 播种失败不该让后端起不来。

_seed_thread: threading.Thread | None = None
_seed_state_lock = threading.Lock()
_seed_state: dict = {
    "running": False,  # 后台线程是否正在播种
    "done": False,     # 是否已得出过一次结论
    "ok": None,        # None = 尚无结论
    "error": None,     # 失败分类（机器可读枚举，见 _ERROR_KINDS）；细节只进日志
    "at": None,        # 结论时间戳
}

# 失败分类：**只放稳定枚举，不放诊断文本**。
# 接口会把 error 原样返回，而历史教训是「一旦返回中文细节，迟早有人按文本分支」
# （那些文本随文案改造而变）；分类枚举能让排障/统计用，又不构成隐性契约。
_ERROR_KINDS = {
    "unavailable": "no-seed",   # 没有内置种子，且本地模型也不完整 → 需联网下载
    "no-seed": "no-seed",       # 有种子目录但清单/文件不可用
    "error": "io-error",        # 拷贝期 IO 异常
    "verify-failed": "integrity",  # 播种完成但校验不过
}


def _error_kind(action: str) -> str:
    return _ERROR_KINDS.get(action, "unknown")


def seed_exists(size: str = "base") -> bool:
    """
    内置种子是否**可用于播种**（只查清单与文件，不做任何拷贝）

    与 `seed_available()` 的分工：这一只回布尔、带尺寸参数，给接口用；
    那个回中文 detail，给 `--selftest` 报告用。判据同源（都看 manifest 与文件齐全）。
    """
    seed_dir = seed_root()
    if seed_dir is None:
        return False
    manifest = load_manifest(seed_dir)
    if manifest is None or manifest.get("size") != size:
        return False
    return len(_seed_files(seed_dir, manifest)) == len(manifest["files"])


def background_seed_state() -> dict:
    """后台播种的当前状态快照（线程安全）"""
    with _seed_state_lock:
        return dict(_seed_state)


def start_background_seed(size: str = "base") -> bool:
    """
    幂等启动后台播种

    返回 True 表示本次真的起了线程；False 表示已在运行、或已经成功过（不必重复）。
    **绝不做同步拷贝** —— 因此 lifetime 钩子与请求处理里都可以放心调用。
    """
    global _seed_thread
    with _seed_state_lock:
        if _seed_state["running"]:
            return False
        if _seed_state["done"] and _seed_state["ok"] is True:
            return False
        _seed_state["running"] = True
        _seed_state["error"] = None

    def _run() -> None:
        try:
            result = ensure_model(size)
            ok = bool(result.get("ok"))
            if ok:
                error = None
            else:
                action = str(result.get("action") or "unknown")
                error = _error_kind(action)
                # 细节只进日志（与 routes.py 的约定一致）：用户看不到，排障时能拿到
                logger.warning(f"后台播种未成功: action={action} detail={result.get('detail')}")
        except Exception as exc:  # 兜底：线程里漏掉的异常会让状态永远停在 running
            ok, error = False, "unknown"
            logger.error(f"后台播种异常: {type(exc).__name__}: {exc}")
        with _seed_state_lock:
            _seed_state.update(
                {"running": False, "done": True, "ok": ok, "error": error, "at": time.time()}
            )

    _seed_thread = threading.Thread(target=_run, name="model-seed", daemon=True)
    _seed_thread.start()
    return True


def reset_background_seed_state_for_tests() -> None:
    """
    仅供测试：把状态清回初始

    刻意不 join/打断正在跑的线程 —— 测试里用的是假种子，线程自己会很快结束；
    强行 join 反而容易把「线程真的在跑」这种真实场景测成假象。
    """
    with _seed_state_lock:
        _seed_state.update(
            {"running": False, "done": False, "ok": None, "error": None, "at": None}
        )
