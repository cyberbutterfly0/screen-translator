"""把 ``assets/icons/src/*.svg`` 渲染成程序用的 PNG，并生成应用图标 ``icon.ico``。

为什么自己渲染：lucide 图标是 stroke-only 的 24x24 SVG，只用到
path / circle / rect / line 四种元素和少数几种路径命令，自己解析即可。
换成 cairosvg 之类在 Windows 上还要另装 cairo 运行库，得不偿失。

用法::

    python build_icons.py

想换图标：把新的 SVG 丢进 ``assets/icons/src/``，重新跑一次这个脚本。
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "assets" / "icons" / "src"
OUT_DIR = ROOT / "assets" / "icons"
APP_ICON = ROOT / "icon.ico"

VIEWBOX = 24.0
OUT_SIZE = 96
SUPERSAMPLE = 4
STROKE_WIDTH = 2.0
#: 图标统一渲染成白色 + alpha，运行时按主题着色
STROKE_COLOR = (255, 255, 255, 255)

#: 应用图标底色（取自 DeepSeek Harness 的深色基色）
APP_BG = (21, 21, 23, 255)

ARGC = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}
NUMBER_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")
TAG_RE = re.compile(r"<(path|circle|rect|line)\b([^>]*?)/?>", re.S)
ATTR_RE = re.compile(r'([a-zA-Z][a-zA-Z0-9-]*)="([^"]*)"')


# ---------------------------------------------------------------- path 解析


def parse_path(d: str) -> list[tuple[str, list[float]]]:
    """把 ``d`` 属性拆成 (命令, 参数) 序列，处理命令的隐式重复。"""
    tokens = NUMBER_RE.findall(d)
    segments: list[tuple[str, list[float]]] = []
    command: str | None = None
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command.upper() == "Z":
                segments.append((command, []))
                continue
        if command is None:
            break
        count = ARGC[command.upper()]
        if index + count > len(tokens):
            break
        args = [float(value) for value in tokens[index : index + count]]
        index += count
        segments.append((command, args))
        # M 之后跟着的参数组等价于 L
        if command == "M":
            command = "L"
        elif command == "m":
            command = "l"

    return segments


def _arc_points(
    x1: float, y1: float, rx: float, ry: float, phi_deg: float,
    large_arc: int, sweep: int, x2: float, y2: float, steps: int = 48,
) -> list[tuple[float, float]]:
    """按 SVG 规范的端点参数化把椭圆弧离散成折线。"""
    if rx == 0 or ry == 0:
        return [(x2, y2)]

    phi = math.radians(phi_deg % 360)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)

    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2

    rx, ry = abs(rx), abs(ry)
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large_arc == sweep:
        coef = -coef

    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        if norm == 0:
            return 0.0
        value = math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / norm)))
        return -value if (ux * vy - uy * vx) < 0 else value

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    points = []
    for step in range(1, steps + 1):
        t = theta1 + delta * step / steps
        xt, yt = rx * math.cos(t), ry * math.sin(t)
        points.append((cos_phi * xt - sin_phi * yt + cx, sin_phi * xt + cos_phi * yt + cy))
    return points


def flatten_path(segments: list[tuple[str, list[float]]], steps: int = 24) -> list[list[tuple[float, float]]]:
    """把 path 转成若干条折线（每个子路径一条）。"""
    strokes: list[list[tuple[float, float]]] = []
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    stroke: list[tuple[float, float]] = []
    prev_cubic: tuple[float, float] | None = None
    prev_quad: tuple[float, float] | None = None

    def flush() -> None:
        nonlocal stroke
        if len(stroke) >= 2:
            strokes.append(stroke)
        stroke = []

    for command, args in segments:
        upper = command.upper()
        rel = command.islower()
        cx, cy = current

        if upper == "M":
            flush()
            x, y = args
            current = (cx + x, cy + y) if rel else (x, y)
            start = current
            stroke = [current]
            prev_cubic = prev_quad = None
        elif upper == "L":
            x, y = args
            current = (cx + x, cy + y) if rel else (x, y)
            stroke.append(current)
            prev_cubic = prev_quad = None
        elif upper == "H":
            x = args[0]
            current = (cx + x if rel else x, cy)
            stroke.append(current)
            prev_cubic = prev_quad = None
        elif upper == "V":
            y = args[0]
            current = (cx, cy + y if rel else y)
            stroke.append(current)
            prev_cubic = prev_quad = None
        elif upper in ("C", "S"):
            if upper == "C":
                x1, y1, x2, y2, x, y = args
                if rel:
                    x1, y1, x2, y2, x, y = cx + x1, cy + y1, cx + x2, cy + y2, cx + x, cy + y
            else:
                x2, y2, x, y = args
                if rel:
                    x2, y2, x, y = cx + x2, cy + y2, cx + x, cy + y
                if prev_cubic is not None:
                    x1, y1 = 2 * cx - prev_cubic[0], 2 * cy - prev_cubic[1]
                else:
                    x1, y1 = cx, cy
            for step in range(1, steps + 1):
                t = step / steps
                mt = 1 - t
                px = mt**3 * cx + 3 * mt * mt * t * x1 + 3 * mt * t * t * x2 + t**3 * x
                py = mt**3 * cy + 3 * mt * mt * t * y1 + 3 * mt * t * t * y2 + t**3 * y
                stroke.append((px, py))
            prev_cubic = (x2, y2)
            prev_quad = None
            current = (x, y)
        elif upper in ("Q", "T"):
            if upper == "Q":
                x1, y1, x, y = args
                if rel:
                    x1, y1, x, y = cx + x1, cy + y1, cx + x, cy + y
            else:
                x, y = args
                if rel:
                    x, y = cx + x, cy + y
                if prev_quad is not None:
                    x1, y1 = 2 * cx - prev_quad[0], 2 * cy - prev_quad[1]
                else:
                    x1, y1 = cx, cy
            for step in range(1, steps + 1):
                t = step / steps
                mt = 1 - t
                px = mt * mt * cx + 2 * mt * t * x1 + t * t * x
                py = mt * mt * cy + 2 * mt * t * y1 + t * t * y
                stroke.append((px, py))
            prev_quad = (x1, y1)
            prev_cubic = None
            current = (x, y)
        elif upper == "A":
            rx, ry, rot, large, sweep, x, y = args
            if rel:
                x, y = cx + x, cy + y
            stroke.extend(_arc_points(cx, cy, rx, ry, rot, int(large), int(sweep), x, y))
            current = (x, y)
            prev_cubic = prev_quad = None
        elif upper == "Z":
            stroke.append(start)
            flush()
            current = start
            prev_cubic = prev_quad = None

    flush()
    return strokes


# ---------------------------------------------------------------- 基础图形


def circle_points(cx: float, cy: float, r: float, steps: int = 72) -> list[tuple[float, float]]:
    points = [
        (cx + r * math.cos(2 * math.pi * i / steps), cy + r * math.sin(2 * math.pi * i / steps))
        for i in range(steps + 1)
    ]
    return points


def rounded_rect_points(
    x: float, y: float, w: float, h: float, r: float, steps: int = 10
) -> list[tuple[float, float]]:
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if r == 0:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    corners = [
        (x + w - r, y + r, -90.0, 0.0),
        (x + w - r, y + h - r, 0.0, 90.0),
        (x + r, y + h - r, 90.0, 180.0),
        (x + r, y + r, 180.0, 270.0),
    ]
    points: list[tuple[float, float]] = []
    for cx, cy, a0, a1 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    points.append(points[0])
    return points


def parse_svg(text: str) -> list[list[tuple[float, float]]]:
    strokes: list[list[tuple[float, float]]] = []
    for tag, attr_text in TAG_RE.findall(text):
        attrs = dict(ATTR_RE.findall(attr_text))
        try:
            if tag == "path":
                strokes.extend(flatten_path(parse_path(attrs.get("d", ""))))
            elif tag == "circle":
                strokes.append(
                    circle_points(float(attrs["cx"]), float(attrs["cy"]), float(attrs["r"]))
                )
            elif tag == "rect":
                strokes.append(
                    rounded_rect_points(
                        float(attrs.get("x", 0)),
                        float(attrs.get("y", 0)),
                        float(attrs["width"]),
                        float(attrs["height"]),
                        float(attrs.get("rx", 0)),
                    )
                )
            elif tag == "line":
                strokes.append(
                    [
                        (float(attrs["x1"]), float(attrs["y1"])),
                        (float(attrs["x2"]), float(attrs["y2"])),
                    ]
                )
        except (KeyError, ValueError) as exc:
            print(f"  ! 跳过无法解析的 <{tag}>: {exc}", file=sys.stderr)
    return strokes


def draw_strokes(
    size: int,
    strokes: list[list[tuple[float, float]]],
    color: tuple[int, int, int, int] = STROKE_COLOR,
    stroke_width: float = STROKE_WIDTH,
    background: tuple[int, int, int, int] | None = None,
    padding: float = 0.0,
) -> Image.Image:
    """按 viewBox 24x24 的比例把折线画出来，超采样后缩小以获得抗锯齿。"""
    canvas = int(round(size * SUPERSAMPLE))
    scale = canvas / (VIEWBOX + padding * 2)
    offset = padding * scale

    image = Image.new("RGBA", (canvas, canvas), background or (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    width_px = max(1, int(round(stroke_width * scale)))
    radius = width_px / 2.0

    for stroke in strokes:
        points = [(x * scale + offset, y * scale + offset) for x, y in stroke]
        if len(points) >= 2:
            draw.line(points, fill=color, width=width_px, joint="curve")
        # stroke-linecap: round —— 每条折线的两端补圆头
        for px, py in (points[0], points[-1]):
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=color)

    return image.resize((size, size), Image.LANCZOS)


def tint(image: Image.Image, color: str) -> Image.Image:
    """用图标的 alpha 当遮罩，重新着色。"""
    rgba = color.lstrip("#")
    if len(rgba) == 8:
        rgba = rgba[:6]
    rgb = tuple(int(rgba[i : i + 2], 16) for i in (0, 2, 4))
    tinted = Image.new("RGBA", image.size, rgb + (255,))
    tinted.putalpha(image.getchannel("A"))
    return tinted


# ---------------------------------------------------------------- 生成


def build_icons() -> int:
    if not SRC_DIR.is_dir():
        print(f"找不到图标源目录：{SRC_DIR}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_files = sorted(SRC_DIR.glob("*.svg"))
    if not svg_files:
        print(f"{SRC_DIR} 里没有 SVG 文件")
        return 1

    for svg_path in svg_files:
        strokes = parse_svg(svg_path.read_text(encoding="utf-8"))
        if not strokes:
            print(f"  ! {svg_path.name} 没有解析出任何图形")
            continue
        image = draw_strokes(OUT_SIZE, strokes)
        image.save(OUT_DIR / f"{svg_path.stem}.png")
        print(f"  {svg_path.stem:16s} -> {OUT_SIZE}x{OUT_SIZE}  {len(strokes)} 条路径")

    build_app_icon()
    return 0


def build_app_icon() -> None:
    """应用图标：深色圆角底 + 白色 scan-text。"""
    source = SRC_DIR / "scan-text.svg"
    if not source.exists():
        print("  ! 缺少 scan-text.svg，跳过应用图标")
        return

    strokes = parse_svg(source.read_text(encoding="utf-8"))
    size = 256
    # 内缩留白，让图标在圆角底上不顶边
    canvas = int(round(size * SUPERSAMPLE))
    scale = canvas / VIEWBOX
    plate = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    ImageDraw.Draw(plate).rounded_rectangle(
        [0, 0, canvas - 1, canvas - 1], radius=int(canvas * 0.22), fill=APP_BG
    )

    inner = size * 0.56
    glyph = draw_strokes(int(round(inner * SUPERSAMPLE)), strokes, stroke_width=2.1)
    offset = (canvas - glyph.width) // 2
    plate.alpha_composite(glyph, (offset, offset))

    icon = plate.resize((size, size), Image.LANCZOS)
    icon.save(
        APP_ICON,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"  {'icon.ico':16s} -> {size}x{size} 多尺寸")


if __name__ == "__main__":
    import applog

    applog.ensure_utf8_stdio()
    raise SystemExit(build_icons())
