# -*- coding: utf-8 -*-
"""生成一键地图图标：assets/yijianditu.ico（多尺寸）+ assets/logo.png。
设计：深色圆角底 + 绿色定位针 + 坐标网格 + 十字准星，呼应"框选地点→118.5° 坐标/DXF"。
依赖 PIL（managed python 已装）。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw

ACCENT = (63, 214, 140, 255)      # #3fd68c
BG = (10, 21, 36, 255)            # #0a1524
BORDER = (44, 74, 110, 255)       # #2c4a6e
GRID = (159, 217, 255, 14)        # 极淡网格
HOLE = (10, 21, 36, 255)
CROSS = (207, 238, 222, 255)      # #cfeede


def _rounded_mask(size: int, box: list, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    return mask


def draw_icon(size: int) -> Image.Image:
    s = size / 256.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 背景圆角矩形
    m = int(10 * s)
    r = int(52 * s)
    d.rounded_rectangle([m, m, size - m, size - m], radius=r, fill=BG)
    d.rounded_rectangle([m, m, size - m, size - m], radius=r,
                        outline=BORDER, width=max(1, int(2 * s)))

    # 网格层（裁剪到圆角内，用 alpha_composite 保留背景）
    grid = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    step = int(32 * s)
    for x in range(int(42 * s), size, step):
        gd.line([(x, m), (x, size - m)], fill=GRID, width=max(1, int(2 * s)))
    for y in range(int(42 * s), size, step):
        gd.line([(m, y), (size - m, y)], fill=GRID, width=max(1, int(2 * s)))
    grid.putalpha(_rounded_mask(size, [m, m, size - m, size - m], r))
    img.alpha_composite(grid)

    # 定位针单独绘制后整体叠加，避免网格线穿透
    pin = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    p = ImageDraw.Draw(pin)
    cx, by = int(128 * s), int(100 * s)
    R = int(50 * s)
    p.ellipse([cx - R, by - R, cx + R, by + R], fill=ACCENT)
    tip = (int(128 * s), int(186 * s))
    p.polygon([(cx - int(38 * s), by + int(34 * s)),
               (cx + int(38 * s), by + int(34 * s)), tip], fill=ACCENT)
    p.ellipse([cx - int(20 * s), by - int(20 * s),
               cx + int(20 * s), by + int(20 * s)], fill=HOLE)
    p.ellipse([cx - int(8 * s), by - int(8 * s),
               cx + int(8 * s), by + int(8 * s)], fill=ACCENT)
    cl = max(1, int(3 * s))
    p.line([(tip[0], tip[1] - int(8 * s)), (tip[0], tip[1] + int(8 * s))], fill=CROSS, width=cl)
    p.line([(tip[0] - int(8 * s), tip[1]), (tip[0] + int(8 * s), tip[1])], fill=CROSS, width=cl)
    img.alpha_composite(pin)
    return img


def main() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(here, "assets")
    os.makedirs(assets, exist_ok=True)
    draw_icon(256).save(
        os.path.join(assets, "yijianditu.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    draw_icon(512).save(os.path.join(assets, "logo.png"))
    print("written:", os.path.join(assets, "yijianditu.ico"),
          os.path.join(assets, "logo.png"))


if __name__ == "__main__":
    main()
