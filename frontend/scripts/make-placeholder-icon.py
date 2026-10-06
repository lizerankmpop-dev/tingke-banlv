#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 `frontend/build/icon.ico` 的**占位**版本（多尺寸容器）。

为什么需要这个脚本
------------------
`docs/m3/icon_asset_spec.md` 规定了 Windows 图标必须是一个含 7 档尺寸的
`.ico` 多尺寸容器（16/24/32/48/64/128/256），且 256 档要用 PNG 压缩、
32 位 RGBA、背景完全透明。正式美术资源尚未交付，但图标加载链路
（`main.cjs` 的 `resolveAppIconPath()`、`package.json` 的 `build.win.icon`、
NSIS 的 installerIcon）必须能端到端跑通并被验证 —— 没有文件就永远是
「代码写了但没人验证过」。所以先产出一份**符合格式规范**的占位图标。

它是占位，不是终稿
------------------
图形语义：深紫底 + 两条居中字幕条（设计稿 §4.1 建议的「字幕条」方向）。
刻意用**大色块、无渐变、无文字、无细线**，保证 16px 下不糊成一团
（设计稿 §4.1 第 1 条：16×16 必须可辨识，这是最硬的一条）。
美术资源交付后**直接覆盖 `frontend/build/icon.ico`** 即可，本脚本随之作废。

为什么要自己写编码器
--------------------
本机 venv 没有 Pillow，`pip install` 又是网络依赖。ICO / BMP / PNG 三种
容器的写法都是固定的几十行，用标准库 `struct` + `zlib` 直接拼反而更可控
（也能顺带说明白了「多尺寸容器」到底长什么样）。**不要**为了跑一次性脚本
往项目环境里装依赖。

用法
----
    python frontend/scripts/make-placeholder-icon.py            # 生成 ico
    python frontend/scripts/make-placeholder-icon.py --preview DIR
                                                                # 额外导出
                                                                # 各尺寸 PNG
                                                                # 供目视检查
"""

import argparse
import os
import struct
import sys
import zlib

# ---------------------------------------------------------------- 常量

# 设计稿 §3.1：这七档必须齐。16/24/32/48 必需，64/128 建议，256 必需。
SIZES = (16, 24, 32, 48, 64, 128, 256)

# 每像素超采样倍数（SS×SS 个子采样点）。4 是「够用且不至于慢到离谱」的档位：
# 纯 Python 下 256 档 = 1024×1024 个采样点，约几秒。
SS = 4

# 256 档用 PNG 压缩，其余用 BMP。这是真实设计稿导出的通行做法：
# PNG 能压住 256 档（否则单张就 256KB），而小尺寸用 BMP 兼容性最好。
PNG_SIZES = (256,)

# 配色直接取 global.css 的 --accent，保证图标和界面是同一个色系。
BG_COLOR = (108, 92, 231)   # --accent #6c5ce7
FG_COLOR = (255, 255, 255)

# 几何全部用 0..1 的归一化坐标描述，按目标尺寸换算 —— 这样同一份形状定义
# 能渲染出所有尺寸，不会出现「大图改了小图忘了改」。
INSET = 0.06          # 画布内缩 6% → 内容占 88%，符合设计稿 §4 的安全区
BG_RADIUS = 0.20      # 底色圆角
BAR_RADIUS = 0.07     # 字幕条圆角（= 条高一半 → 两端全圆）
BARS = (
    # (x0, y0, x1, y1)  两条居中字幕条，一长一短，视觉上像「在出字幕」。
    # 条高 0.15、间距 0.09 是按 16px 反推的：0.15×16 ≈ 2.4px（条）、
    # 0.09×16 ≈ 1.4px（缝）。再细的话 16px 下两条会糊成一块
    # （设计稿 §4.1：16×16 必须可辨识，这是最硬的一条）。
    (0.235, 0.320, 0.765, 0.470),
    (0.330, 0.560, 0.670, 0.710),
)


# ---------------------------------------------------------------- 几何

def inside_rounded_rect(px, py, x0, y0, x1, y1, r):
    """点是否落在圆角矩形内（用圆角矩形的 SDF，免开方）。

    为什么不用 `math.hypot`：每条像素要采样 SS² 次、每次要算 3 个形状，
    256 档就是百万级调用，`hypot` 的开销会直接翻倍。这里改成比较平方。
    """
    qx = abs(px - (x0 + x1) * 0.5) - ((x1 - x0) * 0.5 - r)
    qy = abs(py - (y0 + y1) * 0.5) - ((y1 - y0) * 0.5 - r)
    if qx <= 0.0 and qy <= 0.0:
        return True
    if qx < 0.0:
        qx = 0.0
    if qy < 0.0:
        qy = 0.0
    return qx * qx + qy * qy <= r * r


def _edges(box):
    """把归一化坐标加上 INSET 映射到实际画布比例。"""
    x0, y0, x1, y1 = box
    span = 1.0 - INSET * 2.0
    return (
        INSET + x0 * span,
        INSET + y0 * span,
        INSET + x1 * span,
        INSET + y1 * span,
    )


def render_rgba(size):
    """渲染单个尺寸，返回 `size*size*4` 的 RGBA 字节串（自上而下）。"""
    bg_box = _edges((0.0, 0.0, 1.0, 1.0))
    bg_r = BG_RADIUS * (1.0 - INSET * 2.0)
    bar_boxes = [_edges(b) for b in BARS]
    bar_r = BAR_RADIUS * (1.0 - INSET * 2.0)

    step = 1.0 / (size * SS)
    total = SS * SS
    out = bytearray(size * size * 4)

    for py in range(size):
        for px in range(size):
            cov_bg = 0
            cov_bar = 0
            # 超采样：把像素切成 SS×SS 格，逐格判定，覆盖率 = 命中格数/total
            base_y = py * SS * step
            base_x = px * SS * step
            for sy in range(SS):
                sy_ = base_y + (sy + 0.5) * step
                for sx in range(SS):
                    sx_ = base_x + (sx + 0.5) * step
                    if not inside_rounded_rect(sx_, sy_, *bg_box, bg_r):
                        continue
                    cov_bg += 1
                    for bb in bar_boxes:
                        if inside_rounded_rect(sx_, sy_, *bb, bar_r):
                            cov_bar += 1
                            break

            i = (py * size + px) * 4
            if cov_bg == 0:
                # 背景完全透明（设计稿 §4：透明通道必须有，TaskBar 自己铺底）
                continue
            a = cov_bg / total
            # 字幕条盖在底色上。条一定在底色内部，所以直接用条的覆盖率当
            # 叠加 alpha 即可，不需要再做一遍 source-over 的除法。
            t = cov_bar / total
            if t > 0.0:
                # t ≤ a 恒成立（子像素先命中底色才可能命中条），浮点误差留个闸
                tt = min(t, a)
                r = BG_COLOR[0] + (FG_COLOR[0] - BG_COLOR[0]) * (tt / a if a > 0 else 1.0)
                g = BG_COLOR[1] + (FG_COLOR[1] - BG_COLOR[1]) * (tt / a if a > 0 else 1.0)
                b = BG_COLOR[2] + (FG_COLOR[2] - BG_COLOR[2]) * (tt / a if a > 0 else 1.0)
                out[i] = int(round(max(0.0, min(255.0, r))))
                out[i + 1] = int(round(max(0.0, min(255.0, g))))
                out[i + 2] = int(round(max(0.0, min(255.0, b))))
            else:
                out[i] = BG_COLOR[0]
                out[i + 1] = BG_COLOR[1]
                out[i + 2] = BG_COLOR[2]
            out[i + 3] = int(round(a * 255.0))
    return bytes(out)


# ---------------------------------------------------------------- 编码

def _png_chunk(tag, data):
    head = struct.pack('>I', len(data)) + tag + data
    return head + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode_png(size, rgba):
    """把 RGBA 字节串编成 PNG（8 位、色彩类型 6 = RGBA）。"""
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)  # filter type 0（None）—— 图标是色块，压缩已经足够
        raw += rgba[y * stride:(y + 1) * stride]
    return (
        b'\x89PNG\r\n\x1a\n'
        + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
        + _png_chunk(b'IDAT', zlib.compress(bytes(raw), 9))
        + _png_chunk(b'IEND', b'')
    )


def encode_bmp_entry(size, rgba):
    """把 RGBA 字节串编成 ICO 内嵌的 BMP 条目。

    ICO 里的 BMP 和独立 .bmp 有两处不同，都容易写错：
      1. `biHeight` 要填 **两倍**高度（XOR 位图 + AND 掩码上下叠放）
      2. XOR 位图是**自下而上**存储、通道顺序是 **BGRA**
    AND 掩码（1bpp）仍然必须存在，即使 32 位图靠 alpha 表达透明；
    行要按 4 字节对齐补齐。这里全部填 0。
    """
    header = struct.pack(
        '<IiiHHIIiiII',
        40,          # biSize
        size,        # biWidth
        size * 2,    # biHeight（两倍！）
        1,           # biPlanes
        32,          # biBitCount
        0,           # biCompression = BI_RGB
        size * size * 4,  # biSizeImage
        0, 0, 0, 0,
    )
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        row = y * size * 4
        for x in range(size):
            i = row + x * 4
            xor += bytes((rgba[i + 2], rgba[i + 1], rgba[i], rgba[i + 3]))
    mask_stride = ((size + 31) // 32) * 4
    return header + bytes(xor) + b'\x00' * (mask_stride * size)


def write_ico(path, entries):
    """entries: [(size, encoded_bytes), ...]，按 size 升序。"""
    count = len(entries)
    offset = 6 + 16 * count
    directory = bytearray(struct.pack('<HHH', 0, 1, count))
    for size, data in entries:
        # 256 在目录项里用 0 表示（1 字节存不下 256）
        dim = 0 if size >= 256 else size
        directory += struct.pack(
            '<BBBBHHII', dim, dim, 0, 0, 1, 32, len(data), offset,
        )
        offset += len(data)
    payload = bytearray(directory)
    for _, data in entries:
        payload += data
    with open(path, 'wb') as fh:
        fh.write(bytes(payload))
    return len(payload)


# ---------------------------------------------------------------- 入口

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    build_dir = os.path.join(here, '..', 'build')
    build_dir = os.path.normpath(build_dir)
    target = os.path.join(build_dir, 'icon.ico')

    parser = argparse.ArgumentParser(description='生成占位多尺寸 ico')
    parser.add_argument('--preview', metavar='DIR',
                        help='额外导出各尺寸 PNG 到该目录，供目视检查 16px 可辨识性')
    parser.add_argument('--out', default=target, help='ico 输出路径')
    args = parser.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)

    entries = []
    for size in SIZES:
        rgba = render_rgba(size)
        if size in PNG_SIZES:
            entries.append((size, encode_png(size, rgba)))
        else:
            entries.append((size, encode_bmp_entry(size, rgba)))
        print(f'  渲染 {size}x{size} ... {"PNG" if size in PNG_SIZES else "BMP"}')

    written = write_ico(args.out, entries)
    print(f'\n已写入 {args.out}（{written} 字节，{len(entries)} 档尺寸）')
    print(f'尺寸清单: {", ".join(str(s) for s, _ in entries)}')

    if args.preview:
        os.makedirs(args.preview, exist_ok=True)
        for size, _ in entries:
            p = os.path.join(args.preview, f'icon-{size}.png')
            with open(p, 'wb') as fh:
                fh.write(encode_png(size, render_rgba(size)))
            print(f'  预览 {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
