"""生成 PWA 图标（纯标准库，不引入 Pillow 等新依赖）。

    python scripts/gen_pwa_icons.py

产物：app/static/icons/icon-192.png / icon-512.png / icon-maskable-512.png
设计沿用 Arctic Glass：蓝色渐变底 + 白色通知气泡 + 紫色 AI 星标。
maskable 版本铺满画布并把内容缩进安全区，避免被系统圆形遮罩裁掉。
"""
from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "static" / "icons"

BLUE_TOP = (59, 130, 246)
BLUE_BOTTOM = (29, 78, 216)
WHITE = (255, 255, 255)
LINE_BLUE = (37, 99, 235)
STAR = (221, 214, 254)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[float, float, float]:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _sd_round_rect(px: float, py: float, hw: float, hh: float, r: float) -> float:
    dx = abs(px) - (hw - r)
    dy = abs(py) - (hh - r)
    ax, ay = max(dx, 0.0), max(dy, 0.0)
    return math.hypot(ax, ay) + min(max(dx, dy), 0.0) - r


def _coverage(dist: float, size: int) -> float:
    """把归一化有符号距离换算成 1px 抗锯齿覆盖率。"""
    return _clamp(0.5 - dist * size / 2.0)


def _pixel(nx: float, ny: float, size: int, maskable: bool) -> tuple[int, int, int, int]:
    # 普通图标：圆角矩形画布；maskable：满幅画布，内容缩到 78% 安全区
    canvas_alpha = 1.0
    if not maskable:
        canvas_alpha = _coverage(_sd_round_rect(nx, ny, 0.94, 0.94, 0.22), size)
        if canvas_alpha <= 0:
            return (0, 0, 0, 0)

    scale = 0.78 if maskable else 0.92
    cx, cy = nx / scale, ny / scale
    color = _mix(BLUE_TOP, BLUE_BOTTOM, _clamp((cy + 1) / 2))
    alpha = canvas_alpha

    # AI 星标：右上角四角星（astroid），先画在气泡下面，避免压住文字线条
    sdx, sdy = (cx - 0.40) / 0.20, (cy + 0.46) / 0.20
    star_v = abs(sdx) ** 0.55 + abs(sdy) ** 0.55
    star_a = _clamp((1.0 - star_v) * 8.0)
    if star_a > 0:
        color = tuple(c + (s - c) * star_a for c, s in zip(color, STAR))

    # 通知气泡：白色圆角矩形 + 左下角小尾巴
    bubble = _coverage(_sd_round_rect(cx, cy + 0.02, 0.54, 0.40, 0.13), size)
    tail = _coverage(_sd_round_rect(cx + 0.22, cy - 0.40, 0.12, 0.12, 0.04), size)
    white_a = max(bubble, tail)
    if white_a > 0:
        color = tuple(c + (w - c) * white_a for c, w in zip(color, WHITE))
        # 气泡内三条横线：用白色区域裁剪，保证线条只在气泡里
        line_alpha = 0.0
        for line_y, width in ((-0.18, 0.34), (-0.02, 0.34), (0.14, 0.22)):
            rect = _coverage(_sd_round_rect(cx, cy - line_y, width, 0.024, 0.024), size)
            line_alpha = max(line_alpha, rect)
        if line_alpha > 0:
            color = tuple(c + (b - c) * (line_alpha * white_a) for c, b in zip(color, LINE_BLUE))

    return (int(round(color[0])), int(round(color[1])), int(round(color[2])),
            int(round(_clamp(alpha) * 255)))


def render(size: int, maskable: bool = False) -> bytes:
    rows = []
    for y in range(size):
        ny = (y + 0.5) / size * 2 - 1
        row = bytearray([0])
        for x in range(size):
            nx = (x + 0.5) / size * 2 - 1
            row.extend(_pixel(nx, ny, size, maskable))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    targets = [
        ("icon-192.png", 192, False),
        ("icon-512.png", 512, False),
        ("icon-maskable-512.png", 512, True),
    ]
    for name, size, maskable in targets:
        path = OUT / name
        path.write_bytes(render(size, maskable))
        print(f"generated {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())