"""生成小程序 tabBar 图标（81x81 PNG，普通/选中两态）。

微信 tabBar 只接受位图，不能内联 SVG，所以图标在这里用 Pillow 画出来，
保持与 Web 端 Lucide 一致的 1.7 描边与圆角端点风格，避免两端观感割裂。
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw

SIZE = 81
STROKE = 6
NORMAL = (107, 114, 128)   # --text-secondary
ACTIVE = (37, 99, 235)     # --primary
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "miniprogram", "images")


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def _line(d, pts, color, width=STROKE):
    d.line(pts, fill=color, width=width, joint="curve")
    # 圆角端点：Pillow 的 line 没有 cap 参数，手动补圆点
    r = width / 2 - 0.5
    for x, y in (pts[0], pts[-1]):
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _round_rect(d, box, color, width=STROKE, radius=10):
    d.rounded_rectangle(box, radius=radius, outline=color, width=width)


def icon_home(color) -> Image.Image:
    img, d = _canvas()
    _line(d, [(14, 38), (40, 15), (66, 38)], color)
    _line(d, [(21, 36), (21, 64)], color)
    _line(d, [(59, 36), (59, 64)], color)
    _line(d, [(21, 64), (59, 64)], color)
    _line(d, [(34, 64), (34, 48)], color)
    _line(d, [(46, 64), (46, 48)], color)
    _line(d, [(34, 48), (46, 48)], color)
    return img


def icon_notice(color) -> Image.Image:
    img, d = _canvas()
    _round_rect(d, (19, 13, 61, 67), color, radius=8)
    _line(d, [(29, 29), (51, 29)], color)
    _line(d, [(29, 41), (51, 41)], color)
    _line(d, [(29, 53), (42, 53)], color)
    return img


def icon_match(color) -> Image.Image:
    """四角星：表达「推荐/命中」，与 Web 端 i-spark 同源。"""
    img, d = _canvas()
    d.polygon([(40, 10), (47, 33), (70, 40), (47, 47), (40, 70), (33, 47), (10, 40), (33, 33)],
              outline=color, width=STROKE)
    return img


def icon_mine(color) -> Image.Image:
    img, d = _canvas()
    d.ellipse([29, 13, 51, 35], outline=color, width=STROKE)
    d.arc([16, 46, 64, 94], start=200, end=340, fill=color, width=STROKE)
    return img


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    icons = (("home", icon_home), ("notice", icon_notice),
             ("match", icon_match), ("mine", icon_mine))
    for name, fn in icons:
        fn(NORMAL).save(os.path.join(OUT_DIR, f"tab-{name}.png"))
        fn(ACTIVE).save(os.path.join(OUT_DIR, f"tab-{name}-active.png"))
        print(f"tab-{name}.png / tab-{name}-active.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
