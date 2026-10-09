"""
`verify_install.py` 的「交付物指纹」：报告必须能机器可读地绑定到某一个安装包

## 为什么需要这个模块

在这之前，L4 报告里关于「我验的是哪个包」只有三样东西：**文件名、体积、时间戳**
—— 换一个同名同体积的包就能「继承」上一轮 73 步全绿的结论。实测 grep 报告全文，
`md5|sha256|hash` **零命中**（2026-10-09 发现）。

现在 `Report.set_artifact()` 把 `bytes / sha256 / md5` 写进报告顶层 `artifact`，
`--expect-sha256` 还能把它变成门禁。

## 这个模块测什么

  · `sha256_file()` 的算法正确性（**硬编码已知向量**，不从被测函数反推 —— 教训 9）
  · 分块循环没漏块/没重复（分块 vs 一次性两条路径对拍）
  · `artifact` 真的落进 JSON 顶层，且能序列化
  · 🔴 **哈希只算一次** —— 这是本模块最重要的断言，见下
  · 失败路径（文件读不到）不能把脚本带崩
  · 两条**结构守卫**：删掉 `data=` 或把哈希塞回 `dump()` 时，上面的语义断言全绿也拦不住

## 最重要的一条：哈希只能算一次

`Report.step()` **每走一步都会 `dump()`**（原实现如此，为了「进程被杀也留证据」）。
所以哈希若写在 `dump()` 里，一轮 73 步就是 **73 × 302MB ≈ 22GB** 的读取。
`test_artifact_hashed_only_once` 直接钉住这个数（计数 == 1）；
`test_dump_does_not_hash` 再把「别在 dump 里算」变成机器可查的规则。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "scripts" / "verify_install.py"


def _load_verify_install():
    """按路径加载 scripts/verify_install.py（它不在 sys.path 上）

    与 `test_verify_install_log_scan.py` 同款做法。先塞进 `sys.modules` 再 exec，
    否则 dataclass / 类型注解解析会拿到不完整的命名空间。
    """
    spec = importlib.util.spec_from_file_location("_verify_install_artifact_under_test", SRC)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


VI = _load_verify_install()

# `hashlib` 的已知向量，**硬编码**（不是跑一遍被测函数抄回来 —— 那是自证）
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
ABC_MD5 = "900150983cd24fb0d6963f7d28e17f72"


# ---------------- 纯函数：算法正确性 ----------------

def test_sha256_file_known_vector(tmp_path: Path):
    """已知向量：`b"abc"` 的 sha256 / md5 都是公开可查的定值"""
    f = tmp_path / "abc.bin"
    f.write_bytes(b"abc")
    sha256, md5 = VI.sha256_file(f)
    assert sha256 == ABC_SHA256, "sha256 与已知向量不符"
    assert md5 == ABC_MD5, "md5 与已知向量不符（别把 md5 写成了截断的 sha256）"


def test_chunked_read_matches_single_shot(tmp_path: Path):
    """
    分块循环必须与「一次读完」等价

    `chunk=7` 故意取得极小 → 5001 字节会被切成 715 块，能暴露
    「只读第一块」「块被重复 update」「尾部余数丢失」这三类写法。
    """
    data = bytes((i * 37 + 11) % 256 for i in range(5001))
    f = tmp_path / "many-chunks.bin"
    f.write_bytes(data)

    sha256, md5 = VI.sha256_file(f, chunk=7)
    assert sha256 == hashlib.sha256(data).hexdigest()
    assert md5 == hashlib.md5(data).hexdigest()

    # 反向自证：这个文件确实被切成了多块（否则本用例没在验分块路径）
    assert len(data) > 7 * 100, "样本太小，分块路径不会被真正跑到"


def test_empty_file_hashes_without_error(tmp_path: Path):
    """空文件是合法的边界（循环体一次都不进）"""
    f = tmp_path / "empty.bin"
    f.write_bytes(b"")
    sha256, md5 = VI.sha256_file(f)
    assert sha256 == hashlib.sha256(b"").hexdigest()
    assert md5 == hashlib.md5(b"").hexdigest()


# ---------------- Report：指纹的收集与落盘 ----------------

def test_set_artifact_records_identity(tmp_path: Path):
    """`set_artifact` 要把「这一个文件」的身份记全"""
    data = b"x" * 4096
    f = tmp_path / "tingkebanlv-9.9.9-setup.exe"
    f.write_bytes(data)

    art = VI.Report().set_artifact(f)
    assert art["name"] == "tingkebanlv-9.9.9-setup.exe"
    assert art["bytes"] == 4096, "体积必须是实测 stat"
    assert art["sha256"] == hashlib.sha256(data).hexdigest()
    assert art["md5"] == hashlib.md5(data).hexdigest()
    assert art["error"] is None
    assert str(f) == art["path"]


def test_report_dump_contains_artifact(tmp_path: Path):
    """指纹必须落到 JSON **顶层**（消费方不该去 steps 里捞）"""
    f = tmp_path / "setup.exe"
    f.write_bytes(b"payload")
    report = VI.Report()
    report.set_artifact(f)
    report.step("某一步", True, "细节")

    out = tmp_path / "report.json"
    report.dump(out)
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert "artifact" in payload, "报告顶层必须有 artifact"
    assert payload["artifact"]["sha256"] == hashlib.sha256(b"payload").hexdigest()
    assert payload["steps"], "原有 steps 不能被挤掉"
    assert payload["passed"] is True


def test_artifact_hashed_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    🔴 本模块最重要的一条：**302MB 只能读一遍**

    `Report.step()` 每步都会 `dump()`。若哈希在 `dump()` 里算，
    一轮 73 步就是 73 遍 ≈ 22GB。这里用计数包装把「算了几次」变成可观测量。
    """
    calls = {"n": 0}
    real = VI.sha256_file

    def counted(path, chunk=1024 * 1024):
        calls["n"] += 1
        return real(path, chunk)

    monkeypatch.setattr(VI, "sha256_file", counted)

    f = tmp_path / "setup.exe"
    f.write_bytes(b"once only")
    report = VI.Report()
    report.set_artifact(f)
    for i in range(3):
        report.step(f"第 {i} 步", True, "细节")
    report.dump(tmp_path / "r.json")

    assert calls["n"] == 1, (
        f"哈希被算了 {calls['n']} 次；必须恰好 1 次"
        "（`Report.step` 每步都会落盘，写在 dump 里就是 73 遍 × 302MB）"
    )


def test_set_artifact_missing_file_is_not_fatal(tmp_path: Path):
    """反向：文件读不到时不许抛异常，且失败信息要留在产物里"""
    missing = tmp_path / "not-there.exe"
    report = VI.Report()
    art = report.set_artifact(missing)  # 不得抛

    assert art["sha256"] is None
    assert art["bytes"] is None
    assert art["error"], "读不到文件必须写明原因，不能静默留空"

    out = tmp_path / "r.json"
    report.dump(out)  # 失败路径上仍要能把已收集的步骤落盘
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["artifact"]["error"]


# ---------------- 结构守卫：语义测试拦不住的两种退化 ----------------

def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def _locating_call(src: str) -> ast.Call:
    """取出 `report.step("定位安装包", True, ...)` 这个调用节点"""
    found: list[ast.Call] = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "step"):
            continue
        if len(node.args) < 2:
            continue
        head, ok = node.args[0], node.args[1]
        if (
            isinstance(head, ast.Constant)
            and head.value == "定位安装包"
            and isinstance(ok, ast.Constant)
            and ok.value is True
        ):
            found.append(node)
    assert len(found) == 1, f"应当恰好有 1 个「定位安装包(True)」调用，实为 {len(found)}"
    return found[0]


def test_locating_step_carries_artifact_data():
    """
    「定位安装包」那一步必须把指纹作为 `data` 落盘。

    没有这条守卫的话，把 `data=artifact` 删掉后**上面所有语义测试依然全绿**
    （它们直接调 `set_artifact` / `dump`，不经过 `main()`）。
    """
    call = _locating_call(_src())
    kwarg_names = {kw.arg for kw in call.keywords}
    assert "data" in kwarg_names, "该步必须把安装包指纹作为 data 落盘"
    seg = ast.get_source_segment(_src(), call) or ""
    assert "artifact" in seg, f"data 必须引用指纹字典，实为: {seg[:200]!r}"


def test_dump_does_not_hash():
    """
    `Report.dump()` 函数体内**不得**出现 `sha256_file(` —— 把性能陷阱变成规则。

    这条是 `test_artifact_hashed_only_once` 的静态镜像：计数断言能被「先算好再缓存」
    之类的写法绕过，而这条直接禁止了「在 dump 里算」这种形状。
    """
    tree = ast.parse(_src())
    dump_fn: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Report":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "dump":
                    dump_fn = item
    assert dump_fn is not None, "找不到 Report.dump"

    for node in ast.walk(dump_fn):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name != "sha256_file", (
                "dump() 里不能算哈希：`Report.step()` 每步都会调它，"
                "一轮 73 步 = 73 遍 × 302MB ≈ 22GB"
            )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
