#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海獭图标：分层矢量源 →（光栅化）→ 8 档 PNG → icon.ico  （零美术路线）

设计空间 256x256，与 SVG viewBox 一致。所有几何参数集中在下方「几何定义」区，
改参数即可同时改变矢量源与位图产物 —— 单一数据源，不会两边漂移。

图层顺序（底→顶）：1 底色 / 2 主体渐变 / 3 面部 / 4 学士帽 / 5 笔记本声波 / 6 白描边
  16px、24px 是**独立手绘剪影**（见 render 的 s24 / s16 分支），不是主矢量缩放。

依赖：Pillow + numpy（**不是**项目 venv 的依赖，venv 内没有 PIL）。
     本机可用系统 python 或托管 env：
       python frontend/build/icon-source/build_otter.py --out <输出目录>
     产物：<输出目录>/png/icon_{256,128,96,64,48,32,24,16}.png、<输出目录>/tingke-otter.ico
     以及本目录下的 tingke-otter-master.svg / -24.svg / -16.svg（矢量源，始终重写）

改完 icon.ico 后必须走 docs/m3/icon_asset_spec.md §8.5 的唯一清单验收。
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
S = 8      # 超采样倍数
N = 256    # 设计空间边长
W = N * S
OW = 6.0   # 白贴纸描边宽度（设计空间单位）

COL = {
    "bg": "#243447",
    "body_top": "#7C93DC",
    "body_bot": "#4E63B0",
    "face": "#F2F7FC",
    "belly": "#D9E6F7",
    "cheek": "#8E7BC8",
    "ink": "#16202C",
    "cap_board": "#E8B84B",
    "cap_skull": "#C9992F",
    "tassel": "#E8873A",
    "book": "#3FB6A8",
    "white": "#FFFFFF",
}

# ---------- 几何定义（设计空间 256x256） ----------
BG = (128, 128, 126)                       # 底色圆
EARS = [(74, 86, 17), (182, 86, 17)]
HEAD = (128, 112, 64)
BODY = (128, 156, 74, 72)                  # cx cy rx ry
MUZZLE = (128, 134, 46, 34)
BELLY = (128, 196, 40, 32)
EYES = [(104, 106, 9.5), (152, 106, 9.5)]
NOSE = (128, 126, 12.5, 8.5)
CHEEKS = [(84, 128, 13, 9), (172, 128, 13, 9)]
CAP_BOARD = [(128, 26), (196, 52), (128, 78), (60, 52)]      # 学士帽方板（菱形）
CAP_SKULL = (128, 62, 48, 32)                                 # 帽体
CAP_BTN = (128, 38, 5)
TASSEL_A = (192, 54)
TASSEL_B = (204, 88)
TASSEL = (204, 94, 6.5)
BOOK = (178, 184, 52, 58, 11)              # 笔记本 cx cy w h r
BOOK_SPIRAL = [(156, 166, 3.2), (156, 184, 3.2), (156, 202, 3.2)]
BOOK_BARS = [(170, 184, 5, 15), (178, 184, 5, 27), (186, 184, 5, 19)]
GRAD_TOP, GRAD_BOT = 0, 256                # 渐变覆盖整幅画布


def px(v):
    return v * S


def ell(d, c, fill, grow=0.0):
    cx, cy, rx = c[0], c[1], c[2]
    ry = c[3] if len(c) > 3 else c[2]
    d.ellipse([px(cx - rx - grow), px(cy - ry - grow), px(cx + rx + grow), px(cy + ry + grow)], fill=fill)


def circ(d, c, fill, grow=0.0):
    ell(d, (c[0], c[1], c[2], c[2]), fill, grow)


def poly(d, pts, fill, grow=0.0):
    if grow:
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        out = []
        for x, y in pts:
            dx, dy = x - cx, y - cy
            n = math.hypot(dx, dy) or 1
            out.append((x + dx / n * grow * 1.35, y + dy / n * grow * 1.35))
        pts = out
    d.polygon([(px(x), px(y)) for x, y in pts], fill=fill)


def rrect(d, box, r, fill, grow=0.0):
    cx, cy, w, h, _ = box
    d.rounded_rectangle(
        [px(cx - w / 2 - grow), px(cy - h / 2 - grow), px(cx + w / 2 + grow), px(cy + h / 2 + grow)],
        radius=px(r + grow), fill=fill)


def bar(d, x, cy, w, h, fill):
    d.rounded_rectangle([px(x - w / 2), px(cy - h / 2), px(x + w / 2), px(cy + h / 2)],
                        radius=px(w / 2), fill=fill)


def mask_of(fn):
    m = Image.new("L", (W, W), 0)
    fn(ImageDraw.Draw(m))
    return m


def gradient(top, bottom):
    a = np.array([int(top[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
    b = np.array([int(bottom[i:i + 2], 16) for i in (1, 3, 5)], dtype=float)
    t = np.linspace(0, 1, W)[:, None]
    rows = (a[None, :] * (1 - t) + b[None, :] * t).astype(np.uint8)
    arr = np.repeat(rows[:, None, :], W, axis=1)
    return Image.fromarray(arr, "RGB")


def render(kind="master", tiers=(256, 128, 96, 64, 48, 32)):
    """kind: master | s24 | s16 —— 返回 {尺寸: 图像}, 超采样原图"""
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # --- L1 底色圆 ---
    ell(d, BG, COL["bg"])

    if kind == "master":
        # --- L2 主体：白描边（扩边绘制）→ 渐变填充 ---
        for c in EARS:
            circ(d, c, COL["white"], OW)
        ell(d, BODY, COL["white"], OW)
        circ(d, HEAD, COL["white"], OW)
        body_mask = mask_of(lambda dd: (
            [circ(dd, c, 255) for c in EARS],
            ell(dd, BODY, 255),
            circ(dd, HEAD, 255)))
        img.paste(gradient(COL["body_top"], COL["body_bot"]), (0, 0), body_mask)

        # --- L3 面部 ---
        ell(d, BELLY, COL["belly"])
        ell(d, MUZZLE, COL["face"])
        for c in CHEEKS:
            ch = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            ell(ImageDraw.Draw(ch), c, COL["cheek"] + "B4")
            img.alpha_composite(ch)
        for c in EYES:
            circ(d, c, COL["ink"])
            circ(d, (c[0] - 3.2, c[1] - 3.4, 3.0), COL["white"])
        ell(d, NOSE, COL["ink"])
        d.arc([px(117), px(133), px(139), px(155)], 25, 155, fill=COL["ink"], width=int(px(4.5)))

        # --- L4 学士帽 ---
        ell(d, CAP_SKULL, COL["white"], OW)
        poly(d, CAP_BOARD, COL["white"], OW)
        ell(d, CAP_SKULL, COL["cap_skull"])
        poly(d, CAP_BOARD, COL["cap_board"])
        circ(d, CAP_BTN, COL["cap_skull"])
        d.line([(px(TASSEL_A[0]), px(TASSEL_A[1])), (px(TASSEL_B[0]), px(TASSEL_B[1]))],
               fill=COL["white"], width=int(px(7)))
        d.line([(px(TASSEL_A[0]), px(TASSEL_A[1])), (px(TASSEL_B[0]), px(TASSEL_B[1]))],
               fill=COL["tassel"], width=int(px(4.5)))
        circ(d, TASSEL, COL["white"], 2.5)
        circ(d, TASSEL, COL["tassel"])

        # --- L5 笔记本 + 声波 ---
        for c in BOOK_SPIRAL:
            circ(d, c, COL["white"], OW)
        rrect(d, BOOK, BOOK[4], COL["white"], OW)
        for c in BOOK_SPIRAL:
            circ(d, c, COL["white"])
        rrect(d, BOOK, BOOK[4], COL["book"])
        for x, cy, w, h in BOOK_BARS:
            bar(d, x, cy, w, h, COL["white"])
        circ(d, (166, 166, 5.5), COL["white"])
        circ(d, (166, 166, 2.6), COL["book"])

    elif kind == "s24":
        # 24px 手绘：圆头 + 学士帽 + 极简笔记本色块（无身体、无五官细节）
        head_mask = mask_of(lambda dd: ([circ(dd, c, 255) for c in EARS], circ(dd, HEAD, 255)))
        white_mask = mask_of(lambda dd: ([circ(dd, c, 255, 3) for c in EARS], circ(dd, HEAD, 255, 3)))
        img.paste(Image.new("RGB", (W, W), COL["white"]), (0, 0), white_mask)
        img.paste(gradient(COL["body_top"], COL["body_bot"]), (0, 0), head_mask)
        ell(d, (128, 140, 40, 30), COL["face"])
        for c in [(108, 116, 8.5), (148, 116, 8.5)]:
            circ(d, c, COL["ink"])
        ell(d, (128, 132, 11, 7.5), COL["ink"])
        cap_w = mask_of(lambda dd: (ell(dd, CAP_SKULL, 255, 3), poly(dd, CAP_BOARD, 255, 3)))
        img.paste(Image.new("RGB", (W, W), COL["white"]), (0, 0), cap_w)
        ell(d, CAP_SKULL, COL["cap_skull"])
        poly(d, CAP_BOARD, COL["cap_board"])
        circ(d, CAP_BTN, COL["cap_skull"])
        rrect(d, (128, 206, 40, 26, 6), 6, COL["white"], 3)
        rrect(d, (128, 206, 40, 26, 6), 6, COL["book"])

    elif kind == "s16":
        # 16px 手绘：仅头部 + 学士帽剪影；高对比、零细节，保证深色任务栏可辨
        HEAD16 = (128, 116, 72)
        head_mask = mask_of(lambda dd: circ(dd, HEAD16, 255))
        white_mask = mask_of(lambda dd: circ(dd, HEAD16, 255, 2))
        img.paste(Image.new("RGB", (W, W), COL["white"]), (0, 0), white_mask)
        img.paste(gradient(COL["body_top"], COL["body_bot"]), (0, 0), head_mask)
        ell(d, (128, 148, 44, 32), COL["face"])
        ell(d, (128, 70, 54, 20), COL["white"], 2)
        poly(d, [(128, 22), (210, 52), (128, 82), (46, 52)], COL["white"], 2)
        ell(d, (128, 70, 54, 20), COL["cap_skull"])
        poly(d, [(128, 22), (210, 52), (128, 82), (46, 52)], COL["cap_board"])
        circ(d, (128, 34, 6), COL["cap_skull"])

    else:
        raise ValueError(kind)

    return {t: img.resize((t, t), Image.LANCZOS) for t in tiers}, img


# ---------- 分层 SVG 矢量源 ----------
def svg_ell(c, fill, extra=""):
    cx, cy, rx = c[0], c[1], c[2]
    ry = c[3] if len(c) > 3 else c[2]
    f = f'fill="{fill}" ' if fill else ""
    return f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" {f}{extra}/>'


def svg_poly(pts, fill, extra=""):
    p = " ".join(f"{x},{y}" for x, y in pts)
    f = f'fill="{fill}" ' if fill else ""
    return f'<polygon points="{p}" {f}{extra}/>'


def svg_rrect(box, fill, extra=""):
    cx, cy, w, h, r = box
    return (f'<rect x="{cx - w / 2}" y="{cy - h / 2}" width="{w}" height="{h}" '
            f'rx="{r}" {f"fill=\"{fill}\" " if fill else ""}{extra}/>')


GRAD = (f'<linearGradient id="bodyGrad" gradientUnits="userSpaceOnUse" '
        f'x1="128" y1="{GRAD_TOP}" x2="128" y2="{GRAD_BOT}">'
        f'<stop offset="0" stop-color="{COL["body_top"]}"/>'
        f'<stop offset="1" stop-color="{COL["body_bot"]}"/></linearGradient>')
INK = f'stroke="{COL["ink"]}" stroke-width="4.5" fill="none" stroke-linecap="round"'
STROKE = f'fill="none" stroke="{COL["white"]}" stroke-width="10"'
SVG_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            'width="256" height="256" viewBox="0 0 256 256">\n'
            f'<defs>{GRAD}</defs>\n')
SMALL_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">\n'
              f'<defs>{GRAD}</defs>\n')
MOUTH_ARC = (f'<path d="M 137.97 148.65 A 11 11 0 0 1 118.03 148.65" {INK}/>')


def svg_master() -> str:
    face = "".join([
        svg_ell(BELLY, COL["belly"]),
        svg_ell(MUZZLE, COL["face"]),
        "".join(svg_ell(c, COL["cheek"], 'fill-opacity="0.71"') for c in CHEEKS),
        "".join(svg_ell(c, COL["ink"]) for c in EYES),
        "".join(svg_ell((c[0] - 3.2, c[1] - 3.4, 3.0), COL["white"]) for c in EYES),
        svg_ell(NOSE, COL["ink"]),
        MOUTH_ARC,
    ])
    cap = "".join([
        svg_ell(CAP_SKULL, COL["cap_skull"]),
        svg_poly(CAP_BOARD, COL["cap_board"]),
        svg_ell(CAP_BTN, COL["cap_skull"]),
        f'<line x1="{TASSEL_A[0]}" y1="{TASSEL_A[1]}" x2="{TASSEL_B[0]}" y2="{TASSEL_B[1]}" '
        f'stroke="{COL["tassel"]}" stroke-width="4.5" stroke-linecap="round"/>',
        svg_ell(TASSEL, COL["tassel"]),
    ])
    book = "".join([
        svg_rrect(BOOK, COL["white"]),
        svg_rrect(BOOK, COL["book"]),
        "".join(svg_ell(c, COL["white"]) for c in BOOK_SPIRAL),
        "".join(f'<rect x="{x - w / 2}" y="{cy - h / 2}" width="{w}" height="{h}" '
                f'rx="{w / 2}" fill="{COL["white"]}"/>' for x, cy, w, h in BOOK_BARS),
        svg_ell((166, 166, 5.5), COL["white"]),
        svg_ell((166, 166, 2.6), COL["book"]),
    ])
    outline = "".join([
        svg_ell(BODY, None, STROKE), svg_ell(HEAD, None, STROKE),
        "".join(svg_ell(c, None, STROKE) for c in EARS),
        svg_ell(CAP_SKULL, None, STROKE), svg_poly(CAP_BOARD, None, STROKE),
    ])
    layers = [
        ("1-底色", svg_ell(BG, COL["bg"])),
        ("2-主体渐变", "".join(svg_ell(c, "url(#bodyGrad)") for c in EARS)
         + svg_ell(BODY, "url(#bodyGrad)") + svg_ell(HEAD, "url(#bodyGrad)")),
        ("3-面部", face),
        ("4-学士帽", cap),
        ("5-笔记本声波", book),
        ("6-白描边", outline),
    ]
    body = "".join(
        f'<g inkscape:groupmode="layer" inkscape:label="{lab}" id="layer{i + 1}">{c}</g>'
        for i, (lab, c) in enumerate(layers))
    return SVG_HEAD + body + "</svg>\n"


def svg_small(kind: int) -> str:
    if kind == 24:
        shapes = (
            "".join(svg_ell(c, COL["white"]) for c in [(74, 86, 20), (182, 86, 20), (128, 112, 67)])
            + "".join(svg_ell(c, "url(#bodyGrad)") for c in
                      [(74, 86, 17), (182, 86, 17), (128, 112, 64)])
            + svg_ell((128, 140, 40, 30), COL["face"])
            + "".join(svg_ell(c, COL["ink"]) for c in [(108, 116, 8.5), (148, 116, 8.5)])
            + svg_ell((128, 132, 11, 7.5), COL["ink"])
            + svg_ell(CAP_SKULL, COL["cap_skull"])
            + svg_poly(CAP_BOARD, COL["cap_board"])
            + svg_ell(CAP_BTN, COL["cap_skull"])
            + svg_rrect((128, 206, 40, 26, 6), COL["white"])
            + svg_rrect((128, 206, 40, 26, 6), COL["book"])
        )
    elif kind == 16:
        shapes = (
            svg_ell((128, 116, 74), COL["white"])
            + svg_ell((128, 116, 72), "url(#bodyGrad)")
            + svg_ell((128, 148, 44, 32), COL["face"])
            + svg_ell((128, 70, 56, 22), COL["cap_skull"])
            + svg_poly([(128, 22), (210, 52), (128, 82), (46, 52)], COL["cap_board"])
            + svg_ell((128, 34, 6), COL["cap_skull"])
        )
    else:
        raise ValueError(kind)
    return SMALL_HEAD + shapes + "</svg>\n"


TIERS = [256, 128, 96, 64, 48, 32, 24, 16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=".", help="PNG / ico 输出目录（默认当前目录）")
    ap.add_argument("--compare-with", default="", help="可选：与另一份 ico 出对照图")
    args = ap.parse_args()
    out = Path(args.out).resolve()
    (out / "png").mkdir(parents=True, exist_ok=True)

    # 矢量源始终写在本脚本同目录（仓库内，随提交归档）
    (HERE / "tingke-otter-master.svg").write_text(svg_master(), encoding="utf-8")
    (HERE / "tingke-otter-24.svg").write_text(svg_small(24), encoding="utf-8")
    (HERE / "tingke-otter-16.svg").write_text(svg_small(16), encoding="utf-8")

    master, _ = render("master", (256, 128, 96, 64, 48, 32))
    s24, _ = render("s24", (24,))
    s16, _ = render("s16", (16,))
    imgs = {**master, 24: s24[24], 16: s16[16]}

    for t in (256, 128, 96, 64, 48, 32):
        master[t].save(out / "png" / f"icon_{t}.png")
    s24[24].save(out / "png" / "icon_24.png")
    s16[16].save(out / "png" / "icon_16.png")

    ico = out / "tingke-otter.ico"
    imgs[256].save(ico, format="ICO", sizes=[(t, t) for t in TIERS],
                   append_images=[imgs[t] for t in TIERS if t != 256])
    print(f"ico  -> {ico}（{ico.stat().st_size / 1024:.1f} KB，{len(TIERS)} 档）")
    print(f"png  -> {out / 'png'}（8 张）")
    print(f"svg  -> {HERE}（tingke-otter-master.svg / -24.svg / -16.svg）")

    if args.compare_with:
        other = Image.open(args.compare_with)
        sheet = Image.new("RGB", (2 * 96, 96), (240, 244, 248))
        sheet.paste(other.resize((48, 48), Image.LANCZOS).resize((64, 64), Image.NEAREST), (16, 16))
        sheet.paste(ico and Image.open(ico).resize((48, 48), Image.LANCZOS).resize((64, 64), Image.NEAREST),
                    (112, 16))
        sheet.save(out / "compare.png")


if __name__ == "__main__":
    main()
