"""图标产物层校验工具（scripts/icon_assets.py）的常驻变异用例

为什么这条测试必须存在
----------------------
`frontend/tests/onboarding-aumid.test.cjs` 只守源码与配置（"win.icon 指到了
build/icon.ico"），而 Windows 有六处图标落点是靠自己读文件/PE 资源的。换图后
真正要验的是**产物**：exe 里到底嵌了哪几档。这个判据只有脚本能给，所以脚本
一旦漏检，"六处落点"里那 4 处继承 exe 图标的就会静默错下去。

首版就漏过一条：`check_ico` 只在 `kind == 'png'` 分支里检查 256 档，于是
「7 档齐、但 256 档是裸 BMP」这种最典型的坏 ico（美术用普通导出工具存盘的默认
产物）完全不报错。补上反向断言后才抓住。这条用例就是为了钉死它。

放在 backend/tests 下：本仓库唯一的 Python 测试根（`pytest backend/tests/`），
放别处就不会进回归。变异在**进程内构造字节流**，不改任何真实文件。

约定：每个坏样本都必须让 check_ico 报出**至少一条**问题，且问题文案要能指出
病灶（断关键子串）。只判"非空"是不够的 —— 报错报错在别处同样算漏检。
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "icon_assets.py"
ICON = REPO / "frontend" / "build" / "icon.ico"

pytestmark = pytest.mark.skipif(
    not SCRIPT.is_file(), reason="scripts/icon_assets.py 不存在（仓库不完整？）"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("icon_assets_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ia = _load_tool()


# ============ ico 字节流构造器（只造工具会读的那些字段） ============

def _png_blob(size: int, color_type: int = 6, depth: int = 8, png_size: int | None = None) -> bytes:
    """最小 PNG：签名 + IHDR。工具只读到 color_type 为止，不校验 CRC。"""
    px = size if png_size is None else png_size
    ihdr = struct.pack(">IIBBBBB", px, px, depth, color_type, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"


def _bmp_blob(size: int, bit_count: int = 32, dib_height: int | None = None) -> bytes:
    """BITMAPINFOHEADER（无 BITMAPFILEHEADER）。biBitCount 在 +14，biHeight 在 +8。"""
    h = size * 2 if dib_height is None else dib_height
    return struct.pack("<IiiHHIIiiII", 40, size, h, 1, bit_count, 0, 0, 0, 0, 0, 0)


def _build_ico(specs: list[tuple[int, str, dict]]) -> bytes:
    """
    specs: [(尺寸, 'bmp'|'png', 构造参数)]

    目录项按 ico 规范排布（`<BBBBHHII`），数据区紧跟在目录之后。
    """
    offset = 6 + 16 * len(specs)
    dir_entries: list[bytes] = []
    payload = b""
    for size, kind, kw in specs:
        blob = _png_blob(size, **kw) if kind == "png" else _bmp_blob(size, **kw)
        w = 0 if size == 256 else size  # ico 里 256 用 0 表示
        dir_entries.append(
            struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(blob), offset)
        )
        payload += blob
        offset += len(blob)
    return struct.pack("<HHH", 0, 1, len(specs)) + b"".join(dir_entries) + payload


def _good_specs() -> list[tuple[int, str, dict]]:
    """设计稿要求的 7 档：小尺寸 BMP 32 位，256 档 PNG/RGBA。"""
    specs: list[tuple[int, str, dict]] = [(s, "bmp", {}) for s in (16, 24, 32, 48, 64, 128)]
    specs.append((256, "png", {}))
    return specs


def _check(tmp_path: Path, data: bytes, name: str = "m.ico") -> list[str]:
    path = tmp_path / name
    path.write_bytes(data)
    return ia.check_ico(path, verbose=False)


def _assert_catches(problems: list[str], *needles: str) -> None:
    assert problems, "坏样本没有报出任何问题（漏检）"
    joined = " | ".join(problems)
    for needle in needles:
        assert needle in joined, f"报了问题但没指出病灶 {needle!r}：{joined}"


# ============ 正例：先证明工具不会乱报 ============

def test_expected_seven_tiers_is_clean(tmp_path: Path):
    """7 档齐 + 小尺寸 32 位 + 256 档 PNG/RGBA → 必须一条问题都不报。

    没有这条，后面所有"能抓住"的断言都可能只是工具永远报错。
    """
    assert _check(tmp_path, _build_ico(_good_specs())) == []


def test_real_shipped_icon_is_clean():
    """仓库里这份占位 ico 必须过 —— 换图后新文件也要过，这是换图流程的一环。"""
    if not ICON.is_file():
        pytest.skip("frontend/build/icon.ico 不存在")
    assert ia.check_ico(ICON, verbose=False) == []


# ============ 变异用例：每一个坏样本都必须被抓住 ============

def test_catches_missing_tiers(tmp_path: Path):
    """美术只导了一档 —— 桌面/任务栏会糊，必须报缺档。"""
    problems = _check(tmp_path, _build_ico([(32, "bmp", {})]))
    _assert_catches(problems, "缺少 16x16", "缺少 256x256")


def test_catches_24bit_bmp(tmp_path: Path):
    """全 24 位 → 丢透明圆角，深色任务栏上是方块。"""
    specs = [(s, "bmp", {"bit_count": 24}) for s in (16, 24, 32, 48, 64, 128)]
    specs.append((256, "png", {}))
    problems = _check(tmp_path, _build_ico(specs))
    _assert_catches(problems, "位深 24", "32 位 RGBA")


def test_catches_256_tier_without_png_compression(tmp_path: Path):
    """**首版漏检的那一条**：7 档齐、位深/掩码全对，只有 256 档是裸 BMP。

    体积会涨到 MB 级，且这是美术用普通导出工具存 ico 时最常见的产物。
    工具走的是 else 分支，所以只在 PNG 分支里查 256 档是抓不住的。
    """
    specs = [(s, "bmp", {}) for s in (16, 24, 32, 48, 64, 128, 256)]
    problems = _check(tmp_path, _build_ico(specs))
    _assert_catches(problems, "256x256 档", "PNG")


def test_catches_256_png_without_alpha(tmp_path: Path):
    """256 档 PNG 但色彩类型不是 6（无 alpha）→ 深色任务栏白边。"""
    specs = [(s, "bmp", {}) for s in (16, 24, 32, 48, 64, 128)]
    specs.append((256, "png", {"color_type": 2}))
    problems = _check(tmp_path, _build_ico(specs))
    _assert_catches(problems, "色彩类型为 2", "RGBA")


def test_catches_directory_size_not_matching_png_body(tmp_path: Path):
    """目录项写 256、PNG 本体是 128 → Windows 按目录项取图，实际会糊。"""
    specs = [(s, "bmp", {}) for s in (16, 24, 32, 48, 64, 128)]
    specs.append((256, "png", {"png_size": 128}))
    problems = _check(tmp_path, _build_ico(specs))
    _assert_catches(problems, "目录项尺寸", "不一致")


def test_catches_oversized_file(tmp_path: Path):
    """超 400KB → §6.1 体积上限失控（正是 256 档没压缩的后果）。"""
    data = _build_ico(_good_specs()) + b"\x00" * (401 * 1024)
    problems = _check(tmp_path, data)
    _assert_catches(problems, "超过 400 KB")


def test_bad_container_reports_instead_of_raising(tmp_path: Path):
    """垃圾文件必须给出结论，不能抛栈 —— 换图时第一个被怀疑的该是图标，不是工具。"""
    problems = _check(tmp_path, b"not an ico at all")
    _assert_catches(problems, "ico")


def test_truncated_directory_is_reported(tmp_path: Path):
    """目录项声称 7 档但文件被截断 → 报错而不是 IndexError。"""
    data = _build_ico(_good_specs())[:20]
    problems = _check(tmp_path, data)
    _assert_catches(problems, "超出文件末尾")


def test_missing_file_is_reported_not_raised(tmp_path: Path):
    problems = ia.check_ico(tmp_path / "nope.ico", verbose=False)
    _assert_catches(problems, "不存在")


# ============ PE 侧：解析失败也要给结论 ============

def test_non_pe_file_reports_instead_of_raising(tmp_path: Path):
    """exe 路径给错（或文件被占用读成半截）时，必须返回问题清单。

    这条是"工具自身健壮性"的变异用例：check_pe 内部读的是二进制结构，
    少一个 try/except 就会在换图当天抛 ValueError 让人误以为是自己搞坏了图标。

    断的是「解析失败」而不是文件名 —— 文件名在任何分支里都会出现，
    拿它当关键字等于什么都没断。
    """
    bogus = tmp_path / "bogus.exe"
    bogus.write_bytes(b"MZ" + b"\x00" * 200)  # 有 MZ 没 PE 签名
    problems = ia.check_pe(bogus, [16, 256], verbose=False)
    _assert_catches(problems, "解析失败")

    notpe = tmp_path / "text.exe"
    notpe.write_bytes(b"just text")  # 连 MZ 都没有
    problems = ia.check_pe(notpe, [16, 256], verbose=False)
    _assert_catches(problems, "解析失败")

    truncated = tmp_path / "half.exe"
    truncated.write_bytes(b"MZ" + b"\x00" * 30)  # 有 MZ，但 e_lfanew 指向文件外 → struct.error 那条路
    problems = ia.check_pe(truncated, [16, 256], verbose=False)
    _assert_catches(problems, "解析失败")
