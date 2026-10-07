"""
应用图标「产物层」校验工具（美术图标交付后换图用）

为什么需要它
------------
`frontend/tests/onboarding-aumid.test.cjs` 已经守住了**源码与配置层**：AUMID 与
`build.appId` 逐字一致、`setAppUserModelId` 早于 `whenReady`、`win.icon` 指向
`build/icon.ico`、ico 是 7 档多尺寸容器。但那些断言全部建立在「文件就在那儿」之上，
**证明不了图标真的被塞进了 exe**。

而 Windows 的图标是**六处独立读取**的（`docs/m3/icon_asset_spec.md` §二），其中
4 处（桌面快捷方式 / 开始菜单 / 任务栏 / 控制面板卸载项）都是**继承 exe 内嵌图标**的。
也就是说：只要 exe 里的图标组不对，这 4 处会一起错，而且**不报错、不崩溃** ——
只会在用户桌面上表现为「有的地方是新图标、有的还是默认图标」。

所以换图后必须验的是**产物**，不是配置。本工具用纯标准库读 PE 资源目录里的
`RT_GROUP_ICON`，把 exe 实际内嵌的尺寸档列出来，与 `build/icon.ico` 对拍。

用法
----
    python scripts/icon_assets.py check <ico>        # 校验一份 ico 本体（可先验美术交付件）
    python scripts/icon_assets.py pe <exe> [...]     # 列出 exe 内嵌图标档位并与 build/icon.ico 对拍
    python scripts/icon_assets.py audit              # 一键：ico 本体 + 所有已知产物 + 注册表引用

退出码：0 全部符合预期；1 有不符合项；2 环境不满足（文件不存在）

本工具自身的正确性由 `backend/tests/test_icon_assets.py` 保证（进程内构造坏 ico 的
变异用例，改坏哪一条都必须报错）。改动本文件后要重跑那个测试。

已知局限（写清楚，免得当成「验过了」）
------------------------------------
1. exe 那一段**只对拍尺寸档位**，不校验内嵌图标的编码与位深 —— 即「exe 里的 256 档
   是不是 PNG」本工具不判。理论 Electron 会原样搬运 ico，但如果后续发现任务栏图标
   体积异常，这里就是第一个要扩展的点。
2. 快捷方式只报存在性，不做 FAIL 判定 —— 快捷方式自身不带图标，继承 target exe，
   图标正确性已由 exe 那一项覆盖（详见 `_report_shortcuts`）。
3. 目视类判据（16px 可辨识性、任务栏是否只有一个图标、Alt+Tab、安装向导界面）
   机器判不了，只能列成清单交给人，见 `docs/m3/icon_asset_spec.md` §8.5.5
   （该节所在的 §8.5 是**唯一执行清单**，含完整执行顺序与失败兜底）。

不依赖 Pillow / pefile —— 本机 venv 没有 PIL，且这一个用途不值得引依赖。
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ICON = REPO / "frontend" / "build" / "icon.ico"
EXPECTED_SIZES = (16, 24, 32, 48, 64, 128, 256)
MAX_ICO_BYTES = 400 * 1024  # 设计稿 §6.1：>400KB 说明 256 档没做 PNG 压缩

# Windows 资源类型
RT_ICON = 3
RT_GROUP_ICON = 14


# ============ ico 本体 ============

def parse_ico(blob: bytes) -> list[dict]:
    """解析 ico 容器，返回每档的 {size, bit_count, kind, bytes}"""
    if len(blob) < 6:
        raise ValueError("文件太小，不是有效 ico")
    reserved, itype, count = struct.unpack_from("<HHH", blob, 0)
    if reserved != 0 or itype != 1:
        raise ValueError(f"ico 头部非法（reserved={reserved} type={itype}）")

    out: list[dict] = []
    for i in range(count):
        off = 6 + i * 16
        if off + 16 > len(blob):
            raise ValueError(f"第 {i + 1} 档目录项超出文件末尾")
        w, h, _colors, _res, _planes, bit_count, nbytes, data_off = struct.unpack_from(
            "<BBBBHHII", blob, off
        )
        w = w or 256
        h = h or 256
        entry = {"size": w, "height": h, "bytes": nbytes, "bit_count": bit_count}

        if blob[data_off:data_off + 4] == b"\x89PNG":
            entry["kind"] = "png"
            # IHDR: 8 字节签名 + 4 长度 + 4 'IHDR' + w(4) + h(4) + depth(1) + colortype(1)
            ihdr = data_off + 8 + 4 + 4
            pw, ph = struct.unpack_from(">II", blob, ihdr)
            entry["bit_count"] = blob[ihdr + 8]
            entry["color_type"] = blob[ihdr + 9]
            entry["png_size"] = (pw, ph)
        else:
            entry["kind"] = "bmp"
            # DIB 头（无 BITMAPFILEHEADER）：biBitCount 在 +14，biHeight 是 图像+掩码 的两倍
            entry["bit_count"] = struct.unpack_from("<H", blob, data_off + 14)[0]
            dib_h = struct.unpack_from("<i", blob, data_off + 8)[0]
            entry["dib_height"] = dib_h
        out.append(entry)
    return out


def check_ico(path: Path, verbose: bool = True) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"{path} 不存在"]
    blob = path.read_bytes()
    try:
        entries = parse_ico(blob)
    except ValueError as exc:
        return [f"{path.name}: {exc}"]

    sizes = [e["size"] for e in entries]
    if verbose:
        print(f"  文件    : {path}（{len(blob) / 1024:.1f} KB，{len(entries)} 档）")
        print(f"  档位    : {', '.join(str(s) for s in sizes)}")

    for need in EXPECTED_SIZES:
        if need not in sizes:
            problems.append(f"缺少 {need}x{need} 档（设计稿 §3.1 要求 7 档）")

    if len(blob) > MAX_ICO_BYTES:
        problems.append(
            f"体积 {len(blob) / 1024:.1f} KB 超过 {MAX_ICO_BYTES // 1024} KB —— "
            "通常意味着 256 档没做 PNG 压缩（§6.1 第 2 条）"
        )

    for e in entries:
        tag = f"{e['size']}x{e['size']} 档"
        if e["height"] != e["size"]:
            problems.append(f"{tag} 宽高不一致（{e['size']}x{e['height']}）")

        # 256 档必须 PNG 压缩 —— 这条**两个方向都要判**。
        # 只判「PNG 分支里的 256 档」会漏掉最要命的那一种：256 档直接拿 BMP 塞进去，
        # 此时走的是 else 分支，位深/掩码全对，体积却会涨到 MB 级（美术用普通导出工具
        # 存 ico 时最常见的产物）。变异验证就是靠这条用例把漏检抓出来的。
        if e["size"] == 256 and e["kind"] != "png":
            problems.append(
                f"{tag} 未使用 PNG 压缩（实际是 {e['kind'].upper()}）—— "
                "256 档裸 BMP 会让 ico 体积暴涨，也是 §6.1 体积上限失控的根因"
            )

        if e["kind"] == "png":
            if e["size"] != 256:
                # 非 256 档用 PNG 是允许但少见的写法，不判错，只提示
                if verbose:
                    print(f"  提示    : {tag} 使用 PNG 编码（通常是 BMP）")
            if e.get("color_type") != 6:
                problems.append(
                    f"{tag} PNG 色彩类型为 {e.get('color_type')}，应为 6（RGBA）—— "
                    "无 alpha 会在深色任务栏上出现白边"
                )
            if e.get("png_size") and e["png_size"] != (e["size"], e["height"]):
                # 目录项写 256、PNG 本体写 128 这类不自洽，Windows 按目录项取图 → 糊
                problems.append(
                    f"{tag} 目录项尺寸 {e['size']}x{e['height']} 与 PNG 本体 "
                    f"{e['png_size'][0]}x{e['png_size'][1]} 不一致"
                )
        else:
            if e["bit_count"] != 32:
                problems.append(
                    f"{tag} 位深 {e['bit_count']}，应为 32 位 RGBA —— "
                    "24 位会丢掉透明圆角，深色任务栏上是一块方块"
                )
            if e.get("dib_height") not in (None, e["size"] * 2):
                problems.append(f"{tag} DIB 高度 {e['dib_height']}，应为图像的 2 倍（含 AND 掩码）")
    return problems


# ============ PE 内嵌图标 ============

def _pe_sections(data: bytes) -> tuple[list[tuple[int, int, int, int]], int]:
    """返回 (sections, res_rva)；sections 元素为 (VirtualAddress, VirtualSize, RawPtr, RawSize)"""
    if data[:2] != b"MZ":
        raise ValueError("不是 PE 文件（缺 MZ）")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\x00\x00":
        raise ValueError("不是 PE 文件（缺 PE 签名）")

    coff = e_lfanew + 4
    n_sections = struct.unpack_from("<H", data, coff + 2)[0]
    size_optional = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    dd_off = opt + (112 if magic == 0x20B else 96)  # PE32+ / PE32
    res_rva = struct.unpack_from("<I", data, dd_off + 2 * 8)[0]

    sections = []
    sec_off = opt + size_optional
    for i in range(n_sections):
        base = sec_off + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, base + 8)
        sections.append((vaddr, vsize, rawptr, rawsize))
    return sections, res_rva


def _rva2off(sections, rva: int) -> int | None:
    for vaddr, vsize, rawptr, rawsize in sections:
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return rawptr + (rva - vaddr)
    return None


def _dirents(data: bytes, base: int, off: int) -> list[tuple[int, int]]:
    """读一个 IMAGE_RESOURCE_DIRECTORY 的条目；返回 [(Name/Id, OffsetToData)]"""
    n_named, n_id = struct.unpack_from("<HH", data, off + 12)
    p = off + 16
    out = []
    for _ in range(n_named + n_id):
        name, child = struct.unpack_from("<II", data, p)
        out.append((name, child))
        p += 8
    return out


def parse_pe_icon_sizes(data: bytes) -> dict[str, list[int]]:
    """
    读出 exe 内嵌的图标组 → {组名: [尺寸...]}

    路径：资源目录根 → 类型 RT_GROUP_ICON(14) → 各资源 id → 语言 → 数据项。
    注意 `OffsetToData` 在目录层级里是**相对资源目录起点**的偏移（不是文件偏移），
    只有最底层数据项里才是 RVA —— 这一点写错就会读出垃圾。
    """
    sections, res_rva = _pe_sections(data)
    if res_rva == 0:
        return {}
    res_off = _rva2off(sections, res_rva)
    if res_off is None:
        raise ValueError("资源目录 RVA 映射不到文件偏移")

    groups: dict[str, list[int]] = {}
    for name, child in _dirents(data, res_off, res_off):
        if name & 0x80000000:  # 命名类型，跳过（图标类型是数字 id）
            continue
        if name != RT_GROUP_ICON:
            continue
        type_dir = res_off + (child & 0x7FFFFFFF)
        for rid, rchild in _dirents(data, res_off, type_dir):
            id_dir = res_off + (rchild & 0x7FFFFFFF)
            for _lang, lchild in _dirents(data, res_off, id_dir):
                if lchild & 0x80000000:
                    continue
                entry = res_off + lchild
                data_rva, size = struct.unpack_from("<II", data, entry)
                blob_off = _rva2off(sections, data_rva)
                if blob_off is None:
                    continue
                blob = data[blob_off:blob_off + size]
                if len(blob) < 6:
                    continue
                count = struct.unpack_from("<H", blob, 4)[0]
                sizes = []
                for i in range(count):
                    e = 6 + i * 14  # GRPICONDIRENTRY 是 14 字节（比 ico 的 16 少 DataSize 以外的填充）
                    if e + 14 > len(blob):
                        break
                    w = blob[e] or 256
                    sizes.append(w)
                groups[f"id={rid}"] = sorted(sizes)
    return groups


def check_pe(path: Path, reference: list[int], verbose: bool = True) -> list[str]:
    problems: list[str] = []
    if not path.is_file():
        return [f"{path} 不存在"]
    try:
        groups = parse_pe_icon_sizes(path.read_bytes())
    except (ValueError, struct.error) as exc:
        # 结构异常（不是 PE / 资源目录被截断）也要给结论，不能让工具抛栈退出 ——
        # 换图流程里第一个被怀疑的应该是图标，不是「工具崩了」
        return [f"{path.name}: 解析失败 —— {exc}"]
    if not groups:
        problems.append(f"{path.name}: exe 里没有 RT_GROUP_ICON 资源（几乎肯定回落了默认图标）")
        return problems

    for gid, sizes in groups.items():
        missing = [s for s in reference if s not in sizes]
        if verbose:
            print(f"  {path.name} [{gid}]: {', '.join(str(s) for s in sizes)}")
        if missing:
            problems.append(
                f"{path.name} 的图标组 {gid} 缺少档位 {missing} —— "
                "继承 exe 图标的 4 处落点（桌面/开始菜单/任务栏/控制面板）会一起显示旧图标"
            )
    return problems


# ============ 一键审计 ============

def audit() -> int:
    problems: list[str] = []
    print("=" * 74)
    print("图标产物层审计（机器可判部分）")
    print("=" * 74)

    print("\n[1] 本体：frontend/build/icon.ico")
    problems += check_ico(ICON)

    if not ICON.is_file():
        print("\n本体缺失，后续对拍无基准，终止")
        return 1
    reference = sorted({e["size"] for e in parse_ico(ICON.read_bytes())})

    release = REPO / "frontend" / "release"
    win_unpacked = release / "win-unpacked"

    print("\n[2] 产物：release/win-unpacked 下的应用 exe（落点 1，其余 4 处都继承它）")
    app_exes = sorted(win_unpacked.glob("*.exe")) if win_unpacked.is_dir() else []
    if not app_exes:
        print("  （未找到 release/win-unpacked/*.exe —— 先跑 npm run electron:build）")
    for exe in app_exes:
        problems += check_pe(exe, reference)

    print("\n[3] 产物：NSIS 安装包（落点 5：安装向导界面 + 安装包本体）")
    setups = sorted(release.glob("*setup.exe")) if release.is_dir() else []
    if not setups:
        print("  （未找到 release/*setup.exe）")
    for exe in setups:
        # 与 app exe 同等级严格：2026-10-07 实测 electron-builder 走
        # `win.icon` 回落时，NSIS 安装包保留**全部 7 档**（RT_GROUP_ICON id 随构建变动），
        # 所以「档位不足」在安装包上同样是缺陷，不该只当提示。
        problems += check_pe(exe, reference)

    print("\n[4] 实装：已安装的 exe、快捷方式与注册表引用（落点 2/3/6）")
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "tingke-banlv"
    installed = local / "听课伴侣.exe"
    if installed.is_file():
        problems += check_pe(installed, reference)
        _report_shortcuts()
        _report_display_icon(problems)
    else:
        print(f"  （未安装：{installed} 不存在 —— 装完再跑本项；"
              "也可用 verify_install --keep-installed 保装）")

    # 标题写「人工确认」而不是「目检」：末项是看构建日志，不是用眼睛看图。
    # 清单本身与 §8.5.5 同源，改一处必须同步另一处。
    print("\n[5] 仍需人工确认（工具判不了，见 docs/m3/icon_asset_spec.md §8.5.5）")
    for item in (
        "16px 下是否还能辨识（缩到 16px 目视，不可跳过）",
        "任务栏运行中只有一个图标（AUMID 生效的唯一可靠判据）",
        "Alt+Tab 切换器显示新图标",
        "双击 setup.exe 时安装向导界面显示新图标",
        "构建输出无「default Electron icon is used」警告",
    ):
        print(f"  □ {item}")

    print("\n" + "=" * 74)
    if problems:
        print(f"结论：发现 {len(problems)} 个问题")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("结论：机器可判部分全部通过 ✓（人工目检项见上方清单）")
    return 0


def _report_shortcuts() -> None:
    """
    报告快捷方式落点（只报存在性，不判失败）。

    快捷方式**自己不带图标**（继承 target exe），所以「图标对不对」已经由 exe 那一项
    覆盖了；这里只回答「快捷方式到底建出来没有」—— 缺了它就没地方体现图标，
    属于安装器行为问题而不是图标问题，因此不做 FAIL 判定。
    """
    home = Path(os.environ.get("USERPROFILE", ""))
    appdata = Path(os.environ.get("APPDATA", ""))
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    for label, path in (
        ("桌面（当前用户）", home / "Desktop" / "听课伴侣.lnk"),
        ("桌面（公共）", public / "Desktop" / "听课伴侣.lnk"),
        ("开始菜单", appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "听课伴侣.lnk"),
    ):
        print(f"  {label}: {'存在' if path.is_file() else '不存在'} — {path}")


def _report_display_icon(problems: list[str]) -> None:
    """控制面板「程序和功能」里的图标来自注册表 DisplayIcon"""
    import winreg

    found: list[str] = []
    for hive, label in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
        for sub in (
            r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, name) as item:
                            try:
                                display, _ = winreg.QueryValueEx(item, "DisplayName")
                            except OSError:
                                continue
                            if "听课伴侣" not in str(display):
                                continue
                            try:
                                ico, _ = winreg.QueryValueEx(item, "DisplayIcon")
                            except OSError:
                                ico = "(未设置，回落应用图标)"
                            found.append(f"{label}\\...\\{name} → {ico}")
            except OSError:
                pass
    if found:
        for line in found:
            print(f"  注册表 DisplayIcon: {line}")
    else:
        print("  注册表 DisplayIcon: 未找到卸载项（未安装）")
        problems.append("未找到注册表卸载项 —— 落点 6（控制面板卸载项）无从验证")


def main() -> int:
    parser = argparse.ArgumentParser(description="应用图标产物层校验")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check", help="校验一份 ico 本体")
    p_check.add_argument("ico")
    p_pe = sub.add_parser("pe", help="列出 exe 内嵌图标并与 build/icon.ico 对拍")
    p_pe.add_argument("exe", nargs="+")
    sub.add_parser("audit", help="一键审计所有已知产物")

    args = parser.parse_args()
    if args.cmd == "check":
        problems = check_ico(Path(args.ico).resolve())
    elif args.cmd == "pe":
        if not ICON.is_file():
            print(f"基准图标不存在：{ICON}", file=sys.stderr)
            return 2
        reference = sorted({e["size"] for e in parse_ico(ICON.read_bytes())})
        print(f"基准 {ICON.name} 档位: {', '.join(str(s) for s in reference)}")
        problems = []
        for name in args.exe:
            problems += check_pe(Path(name).resolve(), reference)
    else:
        return audit()

    if problems:
        print(f"\n发现问题 {len(problems)} 个：")
        for p in problems:
            print(f"  ✗ {p}")
        return 1
    print("\n全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
