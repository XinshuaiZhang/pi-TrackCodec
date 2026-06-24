from __future__ import annotations

import html
import math
import os
from pathlib import Path


def _svg_text(
    x: float,
    y: float,
    text: object,
    *,
    size: float = 12,
    weight: str = "400",
    anchor: str = "start",
    color: str = "#111827",
    rotate: float | None = None,
    baseline: str | None = None,
) -> str:
    value = html.escape("" if text is None else str(text))
    attrs = [
        f'x="{x:.2f}"',
        f'y="{y:.2f}"',
        f'font-size="{size:.2f}"',
        'font-family="Arial, DejaVu Sans, sans-serif"',
        f'font-weight="{weight}"',
        f'text-anchor="{anchor}"',
        f'fill="{color}"',
    ]
    if baseline:
        attrs.append(f'dominant-baseline="{baseline}"')
    if rotate is not None:
        attrs.append(f'transform="rotate({rotate:.2f} {x:.2f} {y:.2f})"')
    return f"<text {' '.join(attrs)}>{value}</text>"


def _svg_line(x1: float, y1: float, x2: float, y2: float, *, color: str = "#AEB7C2", width: float = 1.0, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width:.2f}"{dash_attr}/>'


def _svg_rect(x: float, y: float, w: float, h: float, *, fill: str, stroke: str = "none", opacity: float = 1.0, rx: float = 0.0) -> str:
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w, 0):.2f}" height="{max(h, 0):.2f}" '
        f'fill="{fill}" stroke="{stroke}" opacity="{opacity:.3f}" rx="{rx:.2f}"/>'
    )


def _svg_circle(cx: float, cy: float, r: float, *, fill: str, stroke: str = "none", opacity: float = 1.0) -> str:
    return f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}" stroke="{stroke}" opacity="{opacity:.3f}"/>'


def _svg_polyline(points: list[tuple[float, float]], *, color: str = "#111827", width: float = 1.0, dash: str | None = None, fill: str = "none", opacity: float = 1.0) -> str:
    pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<polyline points="{pts}" fill="{fill}" stroke="{color}" stroke-width="{width:.2f}" opacity="{opacity:.3f}"{dash_attr}/>'


def _write_svg(path: Path, width: int, height: int, elements: list[str], *, background: str = "#FFFFFF") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">\n'
        f'<rect width="100%" height="100%" fill="{background}"/>\n'
        + "\n".join(elements)
        + "\n</svg>\n",
        encoding="utf-8",
    )


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(v))):02X}" for v in rgb)


def _blend_with_white(hex_color: str, opacity: float) -> str:
    rgb = _hex_to_rgb(hex_color)
    return _rgb_to_hex(tuple(round(v * opacity + 255 * (1.0 - opacity)) for v in rgb))


class _PngCanvas:
    def __init__(self, width: int, height: int, *, scale: int = 3):
        from PIL import Image, ImageDraw

        self.width = width
        self.height = height
        self.scale = scale
        self.image = Image.new("RGB", (width * scale, height * scale), "white")
        self.draw = ImageDraw.Draw(self.image)

    def _font(self, size: float, weight: str = "400"):
        from PIL import ImageFont

        font_dir = Path(os.environ.get("WINDIR", "")) / "Fonts"
        names = ["arialbd.ttf", "Arialbd.ttf"] if weight in {"700", "bold"} else ["arial.ttf", "Arial.ttf"]
        for name in names:
            path = font_dir / name
            if path.exists():
                return ImageFont.truetype(str(path), max(1, round(size * self.scale)))
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf" if weight in {"700", "bold"} else "DejaVuSans.ttf", max(1, round(size * self.scale)))
        except Exception:
            return ImageFont.load_default(size=max(1, round(size * self.scale)))

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: str = "#AEB7C2", width: float = 1.0, dash: str | None = None) -> None:
        s = self.scale
        if not dash:
            self.draw.line((x1 * s, y1 * s, x2 * s, y2 * s), fill=color, width=max(1, round(width * s)))
            return
        parts = [float(part) for part in dash.replace(" ", "").split(",") if part]
        dash_len = (parts[0] if parts else 4.0) * s
        gap_len = (parts[1] if len(parts) > 1 else dash_len) * s
        x1s, y1s, x2s, y2s = x1 * s, y1 * s, x2 * s, y2 * s
        dx, dy = x2s - x1s, y2s - y1s
        length = math.hypot(dx, dy)
        if length <= 0:
            return
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            end = min(length, pos + dash_len)
            self.draw.line(
                (x1s + ux * pos, y1s + uy * pos, x1s + ux * end, y1s + uy * end),
                fill=color,
                width=max(1, round(width * s)),
            )
            pos = end + gap_len

    def rect(self, x: float, y: float, w: float, h: float, *, fill: str, stroke: str = "none", opacity: float = 1.0) -> None:
        s = self.scale
        fill_color = None if fill == "none" else _blend_with_white(fill, opacity) if opacity < 1.0 else fill
        outline = None if stroke == "none" else stroke
        self.draw.rectangle((x * s, y * s, (x + max(w, 0)) * s, (y + max(h, 0)) * s), fill=fill_color, outline=outline, width=max(1, round(0.35 * s)))

    def circle(self, cx: float, cy: float, r: float, *, fill: str, stroke: str = "none", opacity: float = 1.0) -> None:
        s = self.scale
        fill_color = _blend_with_white(fill, opacity) if opacity < 1.0 else fill
        outline = None if stroke == "none" else stroke
        self.draw.ellipse(((cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s), fill=fill_color, outline=outline, width=max(1, round(0.35 * s)))

    def text(
        self,
        x: float,
        y: float,
        text: object,
        *,
        size: float = 12,
        weight: str = "400",
        anchor: str = "start",
        color: str = "#111827",
        rotate: float | None = None,
    ) -> None:
        from PIL import Image, ImageDraw

        value = "" if text is None else str(text)
        s = self.scale
        font = self._font(size, weight)
        bbox = self.draw.textbbox((0, 0), value, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x_s = x * s
        y_s = y * s
        if rotate is None:
            left = x_s - text_w / 2 if anchor == "middle" else x_s - text_w if anchor == "end" else x_s
            self.draw.text((left, y_s - text_h), value, fill=color, font=font)
            return
        pad = max(12, round(size * s * 0.7))
        image_w = text_w + pad * 2
        image_h = text_h + pad * 2
        text_image = Image.new("RGBA", (image_w, image_h), (255, 255, 255, 0))
        text_draw = ImageDraw.Draw(text_image)
        cx = image_w / 2
        cy = image_h / 2
        draw_x = pad - bbox[0]
        draw_y = pad - bbox[1]
        if anchor == "middle":
            anchor_x = pad + text_w / 2
            anchor_y = pad + text_h / 2
        elif anchor == "end":
            anchor_x = pad + text_w
            anchor_y = pad + text_h
        else:
            anchor_x = pad
            anchor_y = pad + text_h
        text_draw.text((draw_x, draw_y), value, fill=color, font=font)
        angle = -float(rotate)
        theta = math.radians(angle)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        def rotate_point(px: float, py: float) -> tuple[float, float]:
            rx = px - cx
            ry = py - cy
            return (cos_t * rx - sin_t * ry + cx, sin_t * rx + cos_t * ry + cy)

        corners = [
            rotate_point(0.0, 0.0),
            rotate_point(float(image_w), 0.0),
            rotate_point(0.0, float(image_h)),
            rotate_point(float(image_w), float(image_h)),
        ]
        min_x = min(point[0] for point in corners)
        min_y = min(point[1] for point in corners)
        anchor_rot_x, anchor_rot_y = rotate_point(anchor_x, anchor_y)
        rotated_anchor_x = anchor_rot_x - min_x
        rotated_anchor_y = anchor_rot_y - min_y
        rotated = text_image.rotate(angle, center=(cx, cy), expand=True, resample=Image.Resampling.BICUBIC)
        self.image.paste(rotated, (round(x_s - rotated_anchor_x), round(y_s - rotated_anchor_y)), rotated)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, format="PNG")


class SvgFigure:
    def __init__(self, width: int, height: int, *, background: str = "#FFFFFF"):
        self.width = width
        self.height = height
        self.background = background
        self.elements: list[str] = []
        self.canvas = _PngCanvas(width, height)

    def text(
        self,
        x: float,
        y: float,
        text: object,
        *,
        size: float = 12,
        weight: str = "400",
        anchor: str = "start",
        color: str = "#111827",
        rotate: float | None = None,
        baseline: str | None = None,
    ) -> None:
        self.elements.append(_svg_text(x, y, text, size=size, weight=weight, anchor=anchor, color=color, rotate=rotate, baseline=baseline))
        self.canvas.text(x, y, text, size=size, weight=weight, anchor=anchor, color=color, rotate=rotate)

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: str = "#AEB7C2", width: float = 1.0, dash: str | None = None) -> None:
        self.elements.append(_svg_line(x1, y1, x2, y2, color=color, width=width, dash=dash))
        self.canvas.line(x1, y1, x2, y2, color=color, width=width, dash=dash)

    def rect(self, x: float, y: float, w: float, h: float, *, fill: str, stroke: str = "none", opacity: float = 1.0, rx: float = 0.0) -> None:
        self.elements.append(_svg_rect(x, y, w, h, fill=fill, stroke=stroke, opacity=opacity, rx=rx))
        self.canvas.rect(x, y, w, h, fill=fill, stroke=stroke, opacity=opacity)

    def circle(self, cx: float, cy: float, r: float, *, fill: str, stroke: str = "none", opacity: float = 1.0) -> None:
        self.elements.append(_svg_circle(cx, cy, r, fill=fill, stroke=stroke, opacity=opacity))
        self.canvas.circle(cx, cy, r, fill=fill, stroke=stroke, opacity=opacity)

    def polyline(self, points: list[tuple[float, float]], *, color: str = "#111827", width: float = 1.0, dash: str | None = None, fill: str = "none", opacity: float = 1.0) -> None:
        self.elements.append(_svg_polyline(points, color=color, width=width, dash=dash, fill=fill, opacity=opacity))

    def save(self, out_base: Path) -> None:
        _write_svg(out_base.with_suffix(".svg"), self.width, self.height, self.elements, background=self.background)
        png_path = out_base.with_suffix(".png")
        pdf_path = out_base.with_suffix(".pdf")
        self.canvas.save(png_path)
        self.canvas.image.save(pdf_path, "PDF", resolution=300.0)
