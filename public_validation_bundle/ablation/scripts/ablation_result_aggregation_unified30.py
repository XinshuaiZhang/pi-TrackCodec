from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = "Times New Roman"
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
import matplotlib.pyplot as plt

from ablation_io_helpers import aggregate_rows, fmt_num, read_csv, write_csv


VARIANT_ORDER = ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "F1"]
COMPONENT_ORDER = ["A0", "A1", "A2", "A3", "A4", "A5", "A6"]
VARIANT_LABELS = {
    "A0": "A0 Full",
    "A1": "A1 no cross-scan",
    "A2": "A2 no equal-fid",
    "A3": "A3 lossless",
    "A4": "A4 no sidecar",
    "A5": "A5 zlib",
    "A6": "A6 zstd-3",
    "F1": "A7 no intensity-delta",
}
VARIANT_SHORT = {
    "A0": "A0",
    "A1": "A1",
    "A2": "A2",
    "A3": "A3",
    "A4": "A4",
    "A5": "A5",
    "A6": "A6",
    "F1": "A7",
}
VARIANT_COLORS = {
    "A0": "#D95F02",
    "A1": "#6BAED6",
    "A2": "#3182BD",
    "A3": "#756BB1",
    "A4": "#9E9AC8",
    "A5": "#74A66A",
    "A6": "#A1D99B",
    "F1": "#B84C4C",
}
SAMPLE_LINE_COLORS = [
    "#4DBBD5",
    "#00A087",
    "#3C5488",
    "#E64B35",
    "#8491B4",
    "#91D1C2",
    "#F39B7F",
    "#7E6148",
    "#B09C85",
    "#5F6F52",
    "#8F6A9E",
    "#6B8E9C",
    "#1B9E77",
    "#D95F02",
    "#7570B3",
    "#E7298A",
    "#66A61E",
    "#A6761D",
    "#A6CEE3",
    "#1F78B4",
    "#B2DF8A",
    "#33A02C",
    "#FB9A99",
    "#A6A6A6",
    "#CAB2D6",
    "#6A3D9A",
    "#B15928",
    "#8DD3C7",
    "#80B1D3",
    "#BC80BD",
]
PAPER_BLUE = "#355c7d"
PAPER_PINK = "#f67280"
BOXPLOT_EDGE_COLOR = "#1F1F1F"
BOXPLOT_EXCLUDE_FILE_LABELS = {"SZ-File14", "SZ-Set1F2"}

PAPER_ORDER_FILES = [
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML",
    "Set 1_F2.uncompressed.mzML",
    "File2_20180722_L929_test_DDA_1.uncompressed.mzML",
    "File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML",
    "File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML",
    "QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML",
    "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML",
    "QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML",
    "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML",
    "QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML",
    "QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML",
    "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML",
    "File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML",
    "QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML",
    "QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML",
    "QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML",
    "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML",
    "File5_S8184TPST_01.uncompressed.mzML",
    "File6_Negative_000333.uncompressed.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML",
    "File13_SA1.uncompressed.mzML",
]
PAPER_FILE_ORDER = {name: i + 1 for i, name in enumerate(PAPER_ORDER_FILES)}
PAPER_FILE_ALIASES: dict[str, int] = {}
FORMAL_EXCLUDE_FILES = {
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML",
    "File13_SA1.uncompressed.mzML",
}
UNIFIED30_FILES = {
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML",
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML",
    "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML",
    "File5_S8184TPST_01.uncompressed.mzML",
    "File6_Negative_000333.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML",
    "QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML",
    "QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML",
    "QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML",
    "QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML",
    "QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML",
    "QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML",
    "QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML",
    "Set 1_F2.uncompressed.mzML",
}


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
) -> str:
    value = html.escape("" if text is None else str(text))
    transform = f' transform="rotate({rotate:.2f} {x:.2f} {y:.2f})"' if rotate is not None else ""
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.2f}" font-family="Arial, DejaVu Sans, sans-serif" '
        f'font-weight="{weight}" text-anchor="{anchor}" fill="{color}"{transform}>{value}</text>'
    )


def _svg_line(x1: float, y1: float, x2: float, y2: float, *, color: str = "#AEB7C2", width: float = 1.0, dash: str | None = None) -> str:
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{color}" stroke-width="{width:.2f}"{dash_attr}/>'


def _svg_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    stroke: str = "none",
    opacity: float = 1.0,
    rx: float = 0.0,
) -> str:
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{max(w, 0):.2f}" height="{max(h, 0):.2f}" '
        f'fill="{fill}" stroke="{stroke}" opacity="{opacity:.3f}" rx="{rx:.2f}"/>'
    )


def _write_svg(path: Path, width: int, height: int, elements: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">\n'
        '<rect width="100%" height="100%" fill="#FFFFFF"/>\n'
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


def _sample_line_color(index: int) -> str:
    return SAMPLE_LINE_COLORS[index % len(SAMPLE_LINE_COLORS)]


def _nice_ticks(max_value: float, n: int = 4) -> list[float]:
    if not math.isfinite(max_value) or max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / max(n, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = min((1, 2, 5, 10), key=lambda m: abs(raw - m * power)) * power
    top = math.ceil(max_value / step) * step
    return [i * step for i in range(int(round(top / step)) + 1)]


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

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str = "#AEB7C2",
        width: float = 1.0,
        dash: str | None = None,
    ) -> None:
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

        angle = float(rotate)
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

        # PIL's visual rotation direction is opposite to SVG's y-down rotate transform.
        rotated = text_image.rotate(-angle, center=(cx, cy), expand=True, resample=Image.Resampling.BICUBIC)
        self.image.paste(rotated, (round(x_s - rotated_anchor_x), round(y_s - rotated_anchor_y)), rotated)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, format="PNG")


class _DualFigure:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
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
    ) -> None:
        self.elements.append(_svg_text(x, y, text, size=size, weight=weight, anchor=anchor, color=color, rotate=rotate))
        self.canvas.text(x, y, text, size=size, weight=weight, anchor=anchor, color=color, rotate=rotate)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        *,
        color: str = "#AEB7C2",
        width: float = 1.0,
        dash: str | None = None,
    ) -> None:
        self.elements.append(_svg_line(x1, y1, x2, y2, color=color, width=width, dash=dash))
        self.canvas.line(x1, y1, x2, y2, color=color, width=width, dash=dash)

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str,
        stroke: str = "none",
        opacity: float = 1.0,
        rx: float = 0.0,
    ) -> None:
        self.elements.append(_svg_rect(x, y, w, h, fill=fill, stroke=stroke, opacity=opacity, rx=rx))
        self.canvas.rect(x, y, w, h, fill=fill, stroke=stroke, opacity=opacity)

    def save(self, out_base: Path) -> None:
        out_base.parent.mkdir(parents=True, exist_ok=True)
        _write_svg(out_base.with_suffix(".svg"), self.width, self.height, self.elements)
        png_path = out_base.with_suffix(".png")
        pdf_path = out_base.with_suffix(".pdf")
        self.canvas.save(png_path)
        self.canvas.image.save(pdf_path, "PDF", resolution=300.0)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate TrackCodec ablation sweep outputs.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--nmi-ablation", action="store_true", help="Build NMI-style SVG/PNG ablation figures from component/F1 per-file CSVs.")
    parser.add_argument("--component-csv", type=Path, help="A0-A6 component ablation per-file CSV.")
    parser.add_argument("--f1-csv", type=Path, nargs="*", default=[], help="One or more F1 per-file CSVs.")
    args = parser.parse_args()
    if args.nmi_ablation:
        if args.component_csv is None:
            parser.error("--component-csv is required with --nmi-ablation")
        if not args.f1_csv:
            parser.error("--f1-csv is required with --nmi-ablation")
    return args


def _write_component_tex(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "\\begin{tabular}{lccccc}",
        "\\toprule",
        "Variant & MS1 CR & $\\Delta$vsFull & MS2 CR & $\\Delta$vsFull & Notes \\\\",
        "\\midrule",
    ]
    notes = {
        "A0": "equal-fid",
        "A1": "= ZDPD",
        "A2": "",
        "A3": "strict",
        "A4": "no full-scan sidecar",
        "A5": "zlib",
        "A6": "zstd-3",
    }
    for row in rows:
        vid = row["variant_id"]
        if not str(vid).startswith("A"):
            continue
        lines.append(
            f"{vid} {row['variant_name']} & "
            f"{fmt_num(row.get('mean_ms1_cr'))} & "
            f"{fmt_num(row.get('delta_vs_full_ms1_pct'), 1)}\\% & "
            f"{fmt_num(row.get('mean_ms2_cr'))} & "
            f"{fmt_num(row.get('delta_vs_full_ms2_pct'), 1)}\\% & "
            f"{notes.get(vid, '')} \\\\"
        )
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_mz_tex(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "Variant & m/z precision & MS1 CR & MS2 CR \\\\",
        "\\midrule",
    ]
    precision = {"B1": "4", "B2": "5", "B3": "6"}
    for row in rows:
        vid = row["variant_id"]
        if not str(vid).startswith("B"):
            continue
        lines.append(
            f"{vid} & {precision.get(vid, '')} & {fmt_num(row.get('mean_ms1_cr'))} & {fmt_num(row.get('mean_ms2_cr'))} \\\\"
        )
    lines.extend(["\\bottomrule", "\\end{tabular}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def _plot_components(rows: list[dict], path: Path) -> None:
    a_rows = [row for row in rows if str(row["variant_id"]).startswith("A")]
    if not a_rows:
        return
    labels = [row["variant_id"] for row in a_rows]
    ms1 = [float(row["mean_ms1_cr"]) if row.get("mean_ms1_cr") not in (None, "") else float("nan") for row in a_rows]
    ms2 = [float(row["mean_ms2_cr"] or 0.0) for row in a_rows]
    x = range(len(labels))
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(9.0, 4.6))
    plt.bar([i - 0.18 for i in x], ms1, width=0.36, label="MS1")
    plt.bar([i + 0.18 for i in x], ms2, width=0.36, label="MS2")
    for i, value in enumerate(ms1):
        if value != value:  # NaN: CR is invalid because the full-scan sidecar is disabled.
            plt.text(i - 0.18, max(ms2) * 0.05 if ms2 else 0.1, "N/A", ha="center", va="bottom", fontsize=8, rotation=90)
    plt.xticks(list(x), labels)
    plt.ylabel("Mean compression ratio (x)")
    plt.title("TrackCodec component ablation")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _plot_mz_precision(rows: list[dict], path: Path) -> None:
    b_rows = [row for row in rows if str(row["variant_id"]).startswith("B")]
    if not b_rows:
        return
    precision = [int(str(row["variant_name"]).rsplit("_", 1)[-1]) for row in b_rows]
    ms1 = [float(row["mean_ms1_cr"] or 0.0) for row in b_rows]
    ms2 = [float(row["mean_ms2_cr"] or 0.0) for row in b_rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(6.2, 4.2))
    plt.plot(precision, ms1, marker="o", linewidth=2.2, label="MS1")
    plt.plot(precision, ms2, marker="s", linewidth=2.2, label="MS2")
    plt.xticks([4, 5, 6])
    plt.xlabel("m/z precision decimals")
    plt.ylabel("Mean compression ratio (x)")
    plt.title("m/z precision sensitivity")
    plt.legend()
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


def _to_float(value, default: float = math.nan) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _to_int(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _short_file_label(file_name: str, index: int) -> str:
    if file_name.startswith("QC_E4802"):
        return "F8-E4802"
    if file_name.startswith("QC_E4804_240320"):
        return "F8-E4804-R1"
    if file_name.startswith("QC_E4804_240403"):
        return "F8-E4804-R2"
    if file_name.startswith("QC_E4805_240328"):
        return "F8-E4805-R2"
    if file_name.startswith("QC_E4805_240709"):
        return "F8-E4805-DIA"
    if "true_uncompressed" in file_name:
        return "F8-DDA-true"
    if "DDA-1h" in file_name:
        return "F8-DDA"
    if "ETD-1h" in file_name:
        return "F8-ETD"
    if file_name == "Set 1_F2.uncompressed.mzML":
        return "SZ-Set1F2"
    if file_name.startswith("File"):
        return "SZ-" + file_name.split("_", 1)[0]
    return f"F{index:02d}"


def _paper_file_order(file_name: str) -> int:
    if file_name in PAPER_FILE_ORDER:
        return PAPER_FILE_ORDER[file_name]
    if file_name in PAPER_FILE_ALIASES:
        return PAPER_FILE_ALIASES[file_name]
    return 10_000


def _paper_file_label(file_name: str) -> str:
    order = _paper_file_order(file_name)
    if order < 10_000:
        return f"F{order}"
    return _short_file_label(file_name, order)


def _paper_file_labels_for_plot(file_names: list[str]) -> dict[str, str]:
    base_labels = {file_name: _paper_file_label(file_name) for file_name in file_names}
    counts: dict[str, int] = {}
    for label in base_labels.values():
        counts[label] = counts.get(label, 0) + 1

    seen: dict[str, int] = {}
    labels: dict[str, str] = {}
    for file_name in file_names:
        label = base_labels[file_name]
        if counts[label] > 1:
            seen[label] = seen.get(label, 0) + 1
            labels[file_name] = f"{label}{chr(ord('a') + seen[label] - 1)}"
        else:
            labels[file_name] = label
    return labels


def _load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        if path and path.exists() and path.stat().st_size > 0:
            rows.extend(read_csv(path))
    return rows


def _prepare_ablation_rows(component_csv: Path, f1_csvs: list[Path]) -> tuple[list[dict], list[dict], list[dict]]:
    component_rows = _load_rows([component_csv])
    f1_rows = _load_rows(f1_csvs)
    for row in f1_rows:
        row["variant_id"] = "F1"
        row["variant_name"] = "intensity codec isolation"
    component_rows = [row for row in component_rows if str(row.get("file", "")) in UNIFIED30_FILES]
    f1_rows = [row for row in f1_rows if str(row.get("file", "")) in UNIFIED30_FILES]
    rows = component_rows + f1_rows
    for idx, file_name in enumerate(sorted({str(row.get("file", "")) for row in rows}), start=1):
        label = _short_file_label(file_name, idx)
        for row in rows:
            if str(row.get("file", "")) == file_name:
                row["file_label"] = label
    return rows, component_rows, f1_rows


def _aggregate_ms1_vs_a0(rows: list[dict]) -> list[dict]:
    by_variant: dict[str, list[dict]] = {}
    for row in rows:
        variant = str(row.get("variant_id", ""))
        by_variant.setdefault(variant, []).append(row)
    a0_by_file = {str(row["file"]): row for row in by_variant.get("A0", [])}
    out: list[dict] = []
    for variant in VARIANT_ORDER:
        group = by_variant.get(variant, [])
        if not group:
            continue
        common_files = sorted({str(row["file"]) for row in group} & set(a0_by_file))
        common_rows = [next(row for row in group if str(row["file"]) == file_name) for file_name in common_files]
        raw_sum = sum(_to_float(row.get("ms1_raw_bytes"), 0.0) for row in common_rows)
        comp_sum = sum(_to_float(row.get("ms1_compressed_bytes"), 0.0) for row in common_rows)
        a0_comp_sum = sum(_to_float(a0_by_file[file_name].get("ms1_compressed_bytes"), 0.0) for file_name in common_files)
        cr_values = [_to_float(row.get("ms1_cr")) for row in common_rows if math.isfinite(_to_float(row.get("ms1_cr")))]
        payload_values = [_to_float(row.get("ms1_payload_only_cr")) for row in common_rows if math.isfinite(_to_float(row.get("ms1_payload_only_cr")))]
        enc_values = [_to_float(row.get("encode_time_s")) for row in common_rows if math.isfinite(_to_float(row.get("encode_time_s")))]
        out.append(
            {
                "variant_id": variant,
                "variant_name": VARIANT_LABELS.get(variant, str(group[0].get("variant_name", variant))),
                "n_files": len(common_files),
                "aggregate_ms1_cr": raw_sum / comp_sum if raw_sum > 0 and comp_sum > 0 else math.nan,
                "mean_ms1_cr": sum(cr_values) / len(cr_values) if cr_values else math.nan,
                "median_ms1_cr": sorted(cr_values)[len(cr_values) // 2] if cr_values else math.nan,
                "mean_ms1_payload_only_cr": sum(payload_values) / len(payload_values) if payload_values else math.nan,
                "total_ms1_raw_bytes": int(raw_sum),
                "total_ms1_compressed_bytes": int(comp_sum),
                "a0_total_ms1_compressed_bytes": int(a0_comp_sum),
                "size_delta_vs_a0_pct": (comp_sum / a0_comp_sum - 1.0) * 100.0 if comp_sum > 0 and a0_comp_sum > 0 else math.nan,
                "mean_encode_time_s": sum(enc_values) / len(enc_values) if enc_values else math.nan,
                "max_abs_delta_intensity": max((_to_float(row.get("ms1_max_abs_delta_intensity"), 0.0) for row in common_rows), default=0.0),
                "max_abs_delta_mz": max((_to_float(row.get("ms1_max_abs_delta_mz"), 0.0) for row in common_rows), default=0.0),
            }
        )
    return out


def _paired_delta_rows(rows: list[dict]) -> list[dict]:
    by_file_variant: dict[tuple[str, str], dict] = {}
    for row in rows:
        by_file_variant[(str(row.get("file")), str(row.get("variant_id")))] = row
    files = sorted({file_name for file_name, variant in by_file_variant if variant == "A0"})
    out: list[dict] = []
    for idx, file_name in enumerate(files, start=1):
        a0 = by_file_variant.get((file_name, "A0"))
        if not a0:
            continue
        a0_comp = _to_float(a0.get("ms1_compressed_bytes"), 0.0)
        for variant in VARIANT_ORDER:
            row = by_file_variant.get((file_name, variant))
            if not row:
                continue
            comp = _to_float(row.get("ms1_compressed_bytes"), 0.0)
            out.append(
                {
                    "file": file_name,
                    "file_label": _short_file_label(file_name, idx),
                    "variant_id": variant,
                    "ms1_cr": _to_float(row.get("ms1_cr")),
                    "ms2_cr": _to_float(row.get("ms2_cr")),
                    "ms1_compressed_bytes": int(comp),
                    "a0_ms1_compressed_bytes": int(a0_comp),
                    "size_delta_vs_a0_pct": (comp / a0_comp - 1.0) * 100.0 if comp > 0 and a0_comp > 0 else math.nan,
                }
            )
    return out


def _write_summary_csv(rows: list[dict], path: Path) -> None:
    write_csv(rows, path)


def _draw_axes(fig: _DualFigure, left: float, top: float, plot_w: float, plot_h: float, ticks: list[float], max_tick: float, label: str) -> None:
    fig.text(18, top + plot_h / 2, label, size=13, anchor="middle", rotate=-90)
    for tick in ticks:
        y = top + plot_h - (tick / max_tick) * plot_h
        fig.line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8)
        fig.text(left - 8, y + 4, f"{tick:.0f}", size=11, anchor="end", color="#4B5563")
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0)
    fig.line(left, top, left, top + plot_h, color="#111827", width=1.0)


def _signed_axis_bounds(values: list[float], *, pad_fraction: float = 0.16, min_pad: float = 2.0) -> tuple[float, float]:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        return 0.0, 1.0
    data_min = min(finite)
    data_max = max(finite)
    if data_min >= 0.0:
        return 0.0, data_max + max(data_max * pad_fraction, min_pad)
    if data_max <= 0.0:
        return data_min - max(abs(data_min) * pad_fraction, min_pad), 0.0
    pad = max((data_max - data_min) * pad_fraction, min_pad)
    return data_min - pad, data_max + pad


def _draw_signed_axis(
    fig: _DualFigure,
    left: float,
    top: float,
    plot_w: float,
    plot_h: float,
    ymin: float,
    ymax: float,
    *,
    label: str,
    step: float = 10.0,
) -> float:
    fig.text(18, top + plot_h / 2, label, size=13, anchor="middle", rotate=-90)
    tick_start = math.ceil(ymin / step) * step
    tick_end = math.floor(ymax / step) * step
    tick = tick_start
    while tick <= tick_end + 1e-9:
        y = top + plot_h - ((tick - ymin) / (ymax - ymin)) * plot_h
        fig.line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8)
        fig.text(left - 8, y + 4, f"{tick:.0f}", size=11, anchor="end", color="#4B5563")
        tick += step
    zero_y = top + plot_h - ((0.0 - ymin) / (ymax - ymin)) * plot_h
    fig.line(left, top, left, top + plot_h, color="#111827", width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0)
    if ymin < 0.0 < ymax and abs(zero_y - (top + plot_h)) > 1e-6:
        fig.line(left, zero_y, left + plot_w, zero_y, color="#111827", width=1.0)
    return zero_y


def _plot_ablation_ms1_cr(summary: list[dict], out_base: Path) -> None:
    rows = [row for row in summary if row["variant_id"] in VARIANT_ORDER]
    width, height = 520, 450
    left, top, right, bottom = 86, 58, 18, 148
    plot_w, plot_h = width - left - right, height - top - bottom
    max_y = max(float(row["aggregate_ms1_cr"]) for row in rows if math.isfinite(float(row["aggregate_ms1_cr"]))) * 1.18
    ticks = _nice_ticks(max_y, 4)
    max_tick = max(ticks)
    fig = _DualFigure(width, height)
    fig.text(left, 30, "MS1 ablation: aggregate compression ratio", size=15, weight="700")
    _draw_axes(fig, left, top, plot_w, plot_h, ticks, max_tick, "Aggregate MS1 CR (x)")
    side_pad = 24
    gap = 8
    usable_w = plot_w - side_pad * 2
    bar_w = (usable_w - gap * (len(rows) - 1)) / len(rows)
    for idx, row in enumerate(rows):
        variant = row["variant_id"]
        value = float(row["aggregate_ms1_cr"])
        x = left + side_pad + idx * (bar_w + gap)
        y = top + plot_h - (value / max_tick) * plot_h
        fig.rect(x, y, bar_w, top + plot_h - y, fill=VARIANT_COLORS[variant], stroke="#1F2937", opacity=1.0 if row["n_files"] == 15 else 0.68)
        fig.text(x + bar_w / 2, y - 22, f"{value:.2f}x", size=11, anchor="middle")
        fig.text(x + bar_w / 2, y - 7, f"n={row['n_files']}", size=9.5, anchor="middle", color="#4B5563")
        fig.text(x + bar_w / 2, top + plot_h + 22, VARIANT_LABELS[variant], size=10.5, anchor="end", rotate=-45)
    fig.text(width - 20, height - 10, "Matched to A0 file set; faded bars indicate incomplete coverage.", size=10, anchor="end", color="#4B5563")
    fig.save(out_base)


def _plot_ablation_size_delta(summary: list[dict], out_base: Path) -> None:
    rows = [row for row in summary if row["variant_id"] in VARIANT_ORDER and row["variant_id"] != "A0"]
    width, height = 520, 450
    left, top, right, bottom = 90, 58, 18, 150
    plot_w, plot_h = width - left - right, height - top - bottom
    vals = [float(row["size_delta_vs_a0_pct"]) for row in rows if math.isfinite(float(row["size_delta_vs_a0_pct"]))]
    ymin, ymax = _signed_axis_bounds(vals, pad_fraction=0.14, min_pad=2.0)
    fig = _DualFigure(width, height)
    fig.text(left, 30, "MS1 ablation: compressed-size change vs A0", size=15, weight="700")
    zero_y = _draw_signed_axis(
        fig,
        left,
        top,
        plot_w,
        plot_h,
        ymin,
        ymax,
        label="MS1 compressed size change vs A0 (%)",
        step=10.0,
    )
    side_pad = 24
    gap = 9
    usable_w = plot_w - side_pad * 2
    bar_w = (usable_w - gap * (len(rows) - 1)) / len(rows)
    for idx, row in enumerate(rows):
        variant = row["variant_id"]
        value = float(row["size_delta_vs_a0_pct"])
        x = left + side_pad + idx * (bar_w + gap)
        y_val = top + plot_h - ((value - ymin) / (ymax - ymin)) * plot_h
        y = min(y_val, zero_y)
        h = abs(zero_y - y_val)
        fig.rect(x, y, bar_w, h, fill=VARIANT_COLORS[variant], stroke="#1F2937", opacity=1.0 if row["n_files"] == 15 else 0.68)
        fig.text(x + bar_w / 2, y - 10 if value >= 0 else y + h + 18, f"{value:+.1f}%", size=11, anchor="middle")
        fig.text(x + bar_w / 2, top + plot_h + 22, VARIANT_LABELS[variant], size=10.5, anchor="end", rotate=-45)
    fig.text(width - 20, height - 10, "Positive means worse than A0; negative means smaller.", size=10, anchor="end", color="#4B5563")
    fig.save(out_base)


def _plot_a0_f1_paired(deltas: list[dict], out_base: Path) -> None:
    rows = [row for row in deltas if row["variant_id"] == "F1"]
    if not rows:
        return
    width, height = 620, 450
    left, top, right, bottom = 88, 58, 18, 150
    plot_w, plot_h = width - left - right, height - top - bottom
    vals = [float(row["size_delta_vs_a0_pct"]) for row in rows]
    ymin, ymax = _signed_axis_bounds(vals, pad_fraction=0.10, min_pad=2.0)
    fig = _DualFigure(width, height)
    fig.text(left, 30, "A7 intensity isolation: per-file MS1 penalty", size=15, weight="700")
    zero_y = _draw_signed_axis(
        fig,
        left,
        top,
        plot_w,
        plot_h,
        ymin,
        ymax,
        label="A7 compressed size change vs A0 (%)",
        step=5.0,
    )
    side_pad = 16
    gap = 4
    usable_w = plot_w - side_pad * 2
    bar_w = (usable_w - gap * (len(rows) - 1)) / len(rows)
    for idx, row in enumerate(rows):
        value = float(row["size_delta_vs_a0_pct"])
        x = left + side_pad + idx * (bar_w + gap)
        y_val = top + plot_h - ((value - ymin) / (ymax - ymin)) * plot_h
        y = min(y_val, zero_y)
        h = abs(zero_y - y_val)
        fig.rect(x, y, bar_w, h, fill=PAPER_BLUE, stroke="#1F2937")
        fig.text(x + bar_w / 2, y - 7 if value >= 0 else y + h + 15, f"{value:+.1f}%", size=8.2, anchor="middle")
        fig.text(x + bar_w / 2, top + plot_h + 22, row["file_label"], size=8.8, anchor="end", rotate=-45)
    mean_delta = sum(vals) / len(vals)
    fig.text(width - 20, height - 10, f"Mean per-file penalty: {mean_delta:+.1f}%; n={len(rows)}.", size=10, anchor="end", color="#4B5563")
    fig.save(out_base)


def _plot_a0_vs_f1_ms1_cr_all_files(deltas: list[dict], out_base: Path) -> None:
    by_file_variant = {(row["file"], row["variant_id"]): row for row in deltas}
    files = []
    for row in deltas:
        file_name = row["file"]
        if row["variant_id"] == "A0" and (file_name, "F1") in by_file_variant:
            files.append((file_name, row["file_label"]))
    if not files:
        return
    width, height = 720, 470
    left, top, right, bottom = 82, 58, 22, 150
    plot_w, plot_h = width - left - right, height - top - bottom
    values = []
    for file_name, _ in files:
        values.append(float(by_file_variant[(file_name, "A0")]["ms1_cr"]))
        values.append(float(by_file_variant[(file_name, "F1")]["ms1_cr"]))
    ticks = _nice_ticks(max(values) * 1.08, 4)
    max_tick = max(ticks)
    fig = _DualFigure(width, height)
    fig.text(width / 2, 28, f"A0 vs A7 same-file MS1 compression ratio (n={len(files)})", size=15, weight="700", anchor="middle")
    fig.text(
        width / 2,
        45,
        "A7 replaces the A0 intensity codec with Stack-like passthrough while keeping tracks/mz/backend fixed.",
        size=10,
        anchor="middle",
        color="#4B5563",
    )
    _draw_axes(fig, left, top, plot_w, plot_h, ticks, max_tick, "MS1 CR (x)")
    group_w = plot_w / len(files)
    bar_w = min(14.0, group_w * 0.34)
    inner_gap = max(2.0, bar_w * 0.16)
    pair_colors = {"A0": PAPER_BLUE, "F1": PAPER_PINK}
    for idx, (file_name, label) in enumerate(files):
        center = left + idx * group_w + group_w / 2
        a0 = float(by_file_variant[(file_name, "A0")]["ms1_cr"])
        f1 = float(by_file_variant[(file_name, "F1")]["ms1_cr"])
        for variant, value, offset in (("A0", a0, -bar_w / 2 - inner_gap / 2), ("F1", f1, bar_w / 2 + inner_gap / 2)):
            x = center + offset - bar_w / 2
            y = top + plot_h - (value / max_tick) * plot_h
            fig.rect(x, y, bar_w, top + plot_h - y, fill=pair_colors[variant], stroke="#1F2937", opacity=0.92)
        fig.text(center, top + plot_h + 22, label, size=8.8, anchor="end", rotate=-45)
    legend_x = width - 252
    legend_y = 60
    fig.rect(legend_x, legend_y, 11, 11, fill=pair_colors["A0"], stroke="#1F2937")
    fig.text(legend_x + 17, legend_y + 10, "A0 Full: track-aware intensity delta", size=9.4)
    fig.rect(legend_x, legend_y + 18, 11, 11, fill=pair_colors["F1"], stroke="#1F2937")
    fig.text(legend_x + 17, legend_y + 28, "A7: Stack-like intensity passthrough", size=9.4)
    fig.text(width - 20, height - 10, "Higher is better.", size=10, anchor="end", color="#4B5563")
    fig.save(out_base)


def _heat_color(value: float, vmin: float, vmax: float, *, intensity_scale: float = 1.0) -> str:
    if not math.isfinite(value):
        return "#F1F3F5"
    if abs(value) < 1e-12:
        return "#F6F7F9"
    if value > 0:
        base = _hex_to_rgb("#355C7D")
        t = min(1.0, (value / max(vmax, 1e-9)) * intensity_scale)
    else:
        base = _hex_to_rgb("#F67280")
        t = min(1.0, (abs(value) / max(abs(vmin), 1e-9)) * intensity_scale)
    rgb = tuple(round(255 * (1.0 - t) + c * t) for c in base)
    return _rgb_to_hex(rgb)


def _plot_delta_heatmap(deltas: list[dict], out_base: Path) -> None:
    deltas = [row for row in deltas if row.get("file") not in FORMAL_EXCLUDE_FILES]
    variants = [variant for variant in VARIANT_ORDER if any(row["variant_id"] == variant for row in deltas)]
    seen = set()
    file_names = []
    for row in deltas:
        if row["file"] not in seen:
            seen.add(row["file"])
            file_names.append(row["file"])
    file_names.sort(key=lambda file_name: (_paper_file_order(file_name), file_name))
    file_labels = _paper_file_labels_for_plot(file_names)
    files = [(file_name, file_labels[file_name]) for file_name in file_names]
    values = {(row["variant_id"], row["file"]): float(row["size_delta_vs_a0_pct"]) for row in deltas}
    finite = [value for value in values.values() if math.isfinite(value)]
    vmin = min(finite)
    vmax = max(finite)
    cell_w, cell_h = 26, 24
    left, top = 106, 58
    width = max(548, left + len(files) * cell_w + 44)
    height = top + len(variants) * cell_h + 142
    fig = _DualFigure(width, height)
    fig.text(24, 30, "MS1 ablation: per-file size change vs A0", size=15, weight="700")
    for i, variant in enumerate(variants):
        y = top + i * cell_h
        fig.text(left - 10, y + 16, VARIANT_LABELS[variant], size=10, anchor="end")
        for j, (file_name, _) in enumerate(files):
            value = values.get((variant, file_name), math.nan)
            x = left + j * cell_w
            fig.rect(x, y, cell_w - 1, cell_h - 1, fill=_heat_color(value, vmin, vmax, intensity_scale=1.45 if variant == "F1" else 1.0), stroke="#FFFFFF")
            if math.isfinite(value):
                fig.text(x + cell_w / 2, y + 16, f"{value:+.0f}", size=7.4, anchor="middle", color="#111827")
    for j, (_, label) in enumerate(files):
        x = left + j * cell_w + cell_w / 2
        fig.text(x, top + len(variants) * cell_h + 22, label, size=8.2, anchor="end", rotate=-45)
    fig.rect(left, top, len(files) * cell_w, len(variants) * cell_h, fill="none", stroke="#111827")
    legend_y = top + len(variants) * cell_h + 92
    legend_x = left
    fig.rect(legend_x, legend_y, 16, 12, fill="#F67280", stroke="#111827")
    fig.text(legend_x + 22, legend_y + 11, "smaller than A0", size=9.5)
    fig.rect(legend_x + 142, legend_y, 16, 12, fill="#F6F7F9", stroke="#D1D5DB")
    fig.text(legend_x + 164, legend_y + 11, "A0 / no change", size=9.5)
    fig.rect(legend_x + 272, legend_y, 16, 12, fill="#355C7D", stroke="#111827")
    fig.text(legend_x + 294, legend_y + 11, "larger than A0", size=9.5)
    fig.text(width - 18, legend_y + 42, "Cell values are % MS1 size change vs A0; positive is worse. A7 row is visually emphasized.", size=10, anchor="end", color="#4B5563")
    fig.save(out_base)


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return math.nan
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    return sorted_values[lo] * (hi - pos) + sorted_values[hi] * (pos - lo)


def _plot_ms1_cr_boxplot(deltas: list[dict], out_base: Path) -> None:
    variants = [variant for variant in VARIANT_ORDER if any(row["variant_id"] == variant for row in deltas)]
    ms2_files = {
        str(row["file"])
        for row in deltas
        if row.get("variant_id") == "A0"
        and str(row.get("file", "")) not in FORMAL_EXCLUDE_FILES
        and math.isfinite(_to_float(row.get("ms2_cr")))
    }
    files = []
    seen = set()
    for row in deltas:
        file_name = str(row.get("file", ""))
        if file_name not in ms2_files or file_name in seen:
            continue
        seen.add(file_name)
        files.append((file_name, row["file_label"]))
    files.sort(key=lambda item: (_paper_file_order(item[0]), item[0]))
    value_map = {(row["variant_id"], row["file"]): float(row["ms1_cr"]) for row in deltas if math.isfinite(float(row["ms1_cr"]))}
    values_by_variant = {
        variant: [value_map[(variant, file_name)] for file_name, _ in files if (variant, file_name) in value_map]
        for variant in variants
    }
    finite = [value for values in values_by_variant.values() for value in values]
    if not finite:
        return
    width, height = 560, 460
    left, top, right, bottom = 82, 58, 20, 150
    plot_w, plot_h = width - left - right, height - top - bottom
    ymin = 1.0
    ymax = max(finite) + 0.8
    ticks = sorted({1.0, *[tick for tick in _nice_ticks(ymax, 4) if tick >= ymin]})
    max_tick = max(ticks)
    fig = _DualFigure(width, height)
    fig.text(left, 30, f"MS1 ablation: per-file compression ratio (n={len(files)})", size=16, weight="700")
    fig.text(16, top + plot_h / 2, "MS1 CR (x)", size=15, anchor="middle", rotate=-90)
    for tick in ticks:
        if tick < ymin:
            continue
        y = top + plot_h - ((tick - ymin) / (max_tick - ymin)) * plot_h
        fig.line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8)
        fig.line(left - 5, y, left, y, color="#111827", width=1.0)
        fig.text(left - 8, y + 4, f"{tick:.0f}", size=13, anchor="end", color="#4B5563")
    fig.line(left, top, left, top + plot_h, color="#111827", width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0)

    side_pad = 24
    usable_w = plot_w - side_pad * 2
    step = usable_w / max(len(variants) - 1, 1)
    xpos = {variant: left + side_pad + i * step for i, variant in enumerate(variants)}

    def y_of(value: float) -> float:
        return top + plot_h - ((value - ymin) / (max_tick - ymin)) * plot_h

    for file_index, (file_name, _) in enumerate(files):
        points = []
        for variant in variants:
            value = value_map.get((variant, file_name))
            if value is None:
                continue
            points.append((xpos[variant], y_of(value)))
        line_color = _sample_line_color(file_index)
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            fig.line(x1, y1, x2, y2, color=line_color, width=0.75, dash="3,3")
        for x, y in points:
            fig.rect(x - 1.8, y - 1.8, 3.6, 3.6, fill=BOXPLOT_EDGE_COLOR, stroke="none", opacity=1.0)

    box_w = min(26, step * 0.55)
    for variant in variants:
        vals = sorted(values_by_variant[variant])
        if not vals:
            continue
        q1 = _percentile(vals, 0.25)
        med = _percentile(vals, 0.50)
        q3 = _percentile(vals, 0.75)
        iqr = q3 - q1
        low_bound = q1 - 1.5 * iqr
        high_bound = q3 + 1.5 * iqr
        whisk_low = min([v for v in vals if v >= low_bound] or [vals[0]])
        whisk_high = max([v for v in vals if v <= high_bound] or [vals[-1]])
        x = xpos[variant]
        fig.line(x, y_of(whisk_low), x, y_of(q1), color=BOXPLOT_EDGE_COLOR, width=1.0)
        fig.line(x, y_of(q3), x, y_of(whisk_high), color=BOXPLOT_EDGE_COLOR, width=1.0)
        fig.line(x - box_w * 0.35, y_of(whisk_low), x + box_w * 0.35, y_of(whisk_low), color=BOXPLOT_EDGE_COLOR, width=1.0)
        fig.line(x - box_w * 0.35, y_of(whisk_high), x + box_w * 0.35, y_of(whisk_high), color=BOXPLOT_EDGE_COLOR, width=1.0)
        fig.rect(x - box_w / 2, y_of(q3), box_w, y_of(q1) - y_of(q3), fill="none", stroke=BOXPLOT_EDGE_COLOR, opacity=1.0)
        fig.line(x - box_w / 2, y_of(med), x + box_w / 2, y_of(med), color=BOXPLOT_EDGE_COLOR, width=1.3)
        fig.line(x, top + plot_h, x, top + plot_h + 5, color="#111827", width=1.0)
        fig.text(x, top + plot_h + 24, VARIANT_LABELS[variant], size=11.5, anchor="end", rotate=-45)
    fig.save(out_base)
    _plot_ms1_cr_boxplot_editable_pdf(files, variants, value_map, values_by_variant, out_base)


def _plot_ms1_cr_boxplot_editable_pdf(
    files: list[tuple[str, str]],
    variants: list[str],
    value_map: dict[tuple[str, str], float],
    values_by_variant: dict[str, list[float]],
    out_base: Path,
) -> None:
    finite = [value for values in values_by_variant.values() for value in values]
    if not finite:
        return
    fig_w, fig_h = 6.4, 4.45
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    x_positions = list(range(1, len(variants) + 1))
    xpos = {variant: x_positions[i] for i, variant in enumerate(variants)}

    for file_index, (file_name, _) in enumerate(files):
        xs = []
        ys = []
        for variant in variants:
            value = value_map.get((variant, file_name))
            if value is None:
                continue
            xs.append(xpos[variant])
            ys.append(value)
        if len(xs) >= 2:
            ax.plot(xs, ys, color=_sample_line_color(file_index), linewidth=0.55, linestyle=(0, (3, 3)), alpha=0.65, zorder=1)
        if xs:
            ax.scatter(xs, ys, s=10, marker="o", color=BOXPLOT_EDGE_COLOR, alpha=1.0, linewidths=0, zorder=3)

    box_values = [values_by_variant[variant] for variant in variants]
    parts = ax.boxplot(
        box_values,
        positions=x_positions,
        widths=0.48,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": BOXPLOT_EDGE_COLOR, "linewidth": 1.15},
        whiskerprops={"color": BOXPLOT_EDGE_COLOR, "linewidth": 0.85},
        capprops={"color": BOXPLOT_EDGE_COLOR, "linewidth": 0.85},
        boxprops={"edgecolor": BOXPLOT_EDGE_COLOR, "linewidth": 0.85},
    )
    for patch in parts["boxes"]:
        patch.set_facecolor("none")
        patch.set_fill(False)
        patch.set_alpha(1.0)

    ymax = max(finite) + 0.8
    ax.set_ylim(1.0, ymax)
    ax.set_xticks(x_positions)
    ax.set_xticklabels([VARIANT_LABELS[variant] for variant in variants], rotation=45, ha="right", fontsize=9.5)
    ax.set_ylabel("MS1 CR (x)", fontsize=12.0)
    ax.set_title(f"MS1 ablation: per-file compression ratio (n={len(files)})", loc="left", fontsize=12.2, fontweight="bold")
    ax.grid(False)
    ax.tick_params(axis="both", which="major", length=3.8, width=0.85, labelsize=10.0)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#111827")
    ax.spines["bottom"].set_color("#111827")
    fig.subplots_adjust(left=0.12, right=0.98, top=0.87, bottom=0.38)
    for path in [out_base.with_suffix(".pdf"), out_base.parent / f"{out_base.name}_editable.pdf"]:
        fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def build_nmi_ablation_figures(component_csv: Path, f1_csvs: list[Path], output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    table_dir = output_dir / "tables"
    plot_dir = output_dir / "plots"
    rows, component_rows, f1_rows = _prepare_ablation_rows(component_csv, f1_csvs)
    summary = _aggregate_ms1_vs_a0(rows)
    deltas = _paired_delta_rows(rows)
    _write_summary_csv(summary, table_dir / "ablation_ms1_summary_vs_a0.csv")
    _write_summary_csv(deltas, table_dir / "ablation_ms1_per_file_delta_vs_a0.csv")
    _write_summary_csv(component_rows, table_dir / "component_A0_A6_per_file_input.csv")
    _write_summary_csv(f1_rows, table_dir / "f1_per_file_input.csv")

    _plot_ablation_ms1_cr(summary, plot_dir / "nmi_ablation_ms1_cr_A0_A6_F1")
    _plot_ablation_size_delta(summary, plot_dir / "nmi_ablation_ms1_size_delta_vs_A0")
    _plot_a0_f1_paired(deltas, plot_dir / "nmi_ablation_F1_per_file_size_penalty_vs_A0")
    _plot_a0_vs_f1_ms1_cr_all_files(deltas, plot_dir / "nmi_ablation_a0_vs_f1_ms1_cr_all_files")
    _plot_delta_heatmap(deltas, plot_dir / "nmi_ablation_per_file_delta_heatmap")
    _plot_ms1_cr_boxplot(deltas, plot_dir / "nmi_ablation_ms1_cr_boxplot_linked_files")

    out = {
        "output_dir": str(output_dir),
        "component_csv": str(component_csv),
        "f1_csvs": [str(path) for path in f1_csvs],
        "n_component_rows": len(component_rows),
        "n_f1_rows": len(f1_rows),
        "n_files": len({row["file"] for row in rows}),
        "summary_csv": str(table_dir / "ablation_ms1_summary_vs_a0.csv"),
        "per_file_delta_csv": str(table_dir / "ablation_ms1_per_file_delta_vs_a0.csv"),
        "plots": {
            "ms1_cr_svg": str(plot_dir / "nmi_ablation_ms1_cr_A0_A6_F1.svg"),
            "ms1_cr_png": str(plot_dir / "nmi_ablation_ms1_cr_A0_A6_F1.png"),
            "ms1_cr_pdf": str(plot_dir / "nmi_ablation_ms1_cr_A0_A6_F1.pdf"),
            "size_delta_svg": str(plot_dir / "nmi_ablation_ms1_size_delta_vs_A0.svg"),
            "size_delta_png": str(plot_dir / "nmi_ablation_ms1_size_delta_vs_A0.png"),
            "size_delta_pdf": str(plot_dir / "nmi_ablation_ms1_size_delta_vs_A0.pdf"),
            "f1_per_file_svg": str(plot_dir / "nmi_ablation_F1_per_file_size_penalty_vs_A0.svg"),
            "f1_per_file_png": str(plot_dir / "nmi_ablation_F1_per_file_size_penalty_vs_A0.png"),
            "f1_per_file_pdf": str(plot_dir / "nmi_ablation_F1_per_file_size_penalty_vs_A0.pdf"),
            "a0_vs_f1_all_files_svg": str(plot_dir / "nmi_ablation_a0_vs_f1_ms1_cr_all_files.svg"),
            "a0_vs_f1_all_files_png": str(plot_dir / "nmi_ablation_a0_vs_f1_ms1_cr_all_files.png"),
            "a0_vs_f1_all_files_pdf": str(plot_dir / "nmi_ablation_a0_vs_f1_ms1_cr_all_files.pdf"),
            "heatmap_svg": str(plot_dir / "nmi_ablation_per_file_delta_heatmap.svg"),
            "heatmap_png": str(plot_dir / "nmi_ablation_per_file_delta_heatmap.png"),
            "heatmap_pdf": str(plot_dir / "nmi_ablation_per_file_delta_heatmap.pdf"),
            "boxplot_svg": str(plot_dir / "nmi_ablation_ms1_cr_boxplot_linked_files.svg"),
            "boxplot_png": str(plot_dir / "nmi_ablation_ms1_cr_boxplot_linked_files.png"),
            "boxplot_pdf": str(plot_dir / "nmi_ablation_ms1_cr_boxplot_linked_files.pdf"),
        },
        "summary": summary,
    }
    (output_dir / "summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> int:
    args = _parse_args()
    if getattr(args, "nmi_ablation", False):
        summary = build_nmi_ablation_figures(
            args.component_csv,
            args.f1_csv,
            args.output_dir.resolve(),
        )
        print(json.dumps(summary, indent=2))
        return 0

    output_dir = args.output_dir.resolve()
    component_per_file = output_dir / "component_sweep" / "ablation_per_file.csv"
    precision_per_file = output_dir / "mz_precision_sweep" / "mz_precision_per_file.csv"

    component_agg = []
    precision_agg = []
    if component_per_file.exists() and component_per_file.stat().st_size > 0:
        component_agg = aggregate_rows(read_csv(component_per_file))
        write_csv(component_agg, output_dir / "component_sweep" / "ablation_aggregate.csv")
    if precision_per_file.exists() and precision_per_file.stat().st_size > 0:
        precision_agg = aggregate_rows(read_csv(precision_per_file))
        write_csv(precision_agg, output_dir / "mz_precision_sweep" / "mz_precision_aggregate.csv")

    tables_dir = output_dir / "tables"
    figures_dir = output_dir / "figures"
    _write_component_tex(component_agg, tables_dir / "ablation_table_a.tex")
    _write_mz_tex(precision_agg, tables_dir / "ablation_table_b_mz.tex")
    _plot_components(component_agg, figures_dir / "ablation_components_bar.pdf")
    _plot_mz_precision(precision_agg, figures_dir / "ablation_mz_precision.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
