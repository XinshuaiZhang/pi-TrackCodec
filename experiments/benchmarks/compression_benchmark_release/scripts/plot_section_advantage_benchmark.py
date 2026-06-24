#!/usr/bin/env python3
from __future__ import annotations

import csv
import html
import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from benchmark_release_paths import default_combined_output_dir, find_workspace_root, inputs_root

ROOT = find_workspace_root(Path(__file__))
SOURCE_MD = inputs_root(Path(__file__)) / "refs" / "compression_ratio_tables_with_ms_format.md"
OUTPUT_ROOT = default_combined_output_dir(Path(__file__))
PLOTS_DIR = OUTPUT_ROOT / "plots"
TABLES_DIR = OUTPUT_ROOT / "tables"
RAW_BACKEND_CSV = TABLES_DIR / "section_raw_backend_baselines.csv"
TRACKCODEC_SECTION_CSV = TABLES_DIR / "trackcodec_ms1_ms2_section_benchmark.csv"
ZDPD_AIRD_SECTION_CSV = TABLES_DIR / "zdpd_aird_section_benchmark.csv"
AIRDPRO_AIRD_SECTION_CSV = TABLES_DIR / "airdpro_default_aird_section_benchmark.csv"

MS1_ORDER = [
    "gzip",
    "zlib",
    "zstd-9",
    "airdpro_default",
    "zdpd_baseline",
    "ours_eqfidelity",
    "ours_strict_q6",
    "ours_archive_fidelity",
]
MS2_ORDER = [
    "gzip",
    "zlib",
    "zstd-9",
    "zdpd_baseline",
    "airdpro_default",
    "ours_eqfidelity",
]
MS1_ADVANTAGE_PLOT_ORDER = [
    "gzip",
    "zlib",
    "zstd-9",
    "airdpro_default",
    "zdpd_baseline",
    "ours_strict_q6",
    "ours_archive_fidelity",
]
MS2_ADVANTAGE_PLOT_ORDER = MS2_ORDER
REFERENCE_LABEL = "zdpd_baseline"

# NPG/Nature-style palette. Baselines are cool colors, TrackCodec methods are warm/deep colors.
COLOR = {
    "gzip": "#C7E4F2",
    "zlib": "#9ECAE1",
    "zstd-9": "#6BAED6",
    "airdpro_default": "#00A087",
    "zdpd_baseline": "#72B7B2",
    "stack_zdpd_baseline": "#9E9AC8",
    "ours_eqfidelity": "#F58518",
    "ours_strict_q6": "#3C5488",
    "ours_archive_fidelity": "#E45756",
}
DISPLAY = {
    "gzip": "gzip level 6 raw float64",
    "zlib": "zlib level 6 raw float64",
    "zstd-9": "zstd level 9 raw float64",
    "airdpro_default": "AirdPro Default",
    "zdpd_baseline": "ZDPD",
    "stack_zdpd_baseline": "Stack-ZDPD",
    "ours_eqfidelity": "TrackCodec eq-fidelity",
    "ours_strict_q6": "TrackCodec strict q6",
    "ours_archive_fidelity": "TrackCodec archive fidelity",
}
DISPLAY_LINES = {
    "gzip": ["gzip", "level 6", "raw float64"],
    "zlib": ["zlib", "level 6", "raw float64"],
    "zstd-9": ["zstd", "level 9", "raw float64"],
    "airdpro_default": ["AirdPro", "Default"],
    "zdpd_baseline": ["ZDPD"],
    "stack_zdpd_baseline": ["Stack-ZDPD"],
    "ours_eqfidelity": ["TrackCodec", "eq-fidelity"],
    "ours_strict_q6": ["TrackCodec", "strict q6"],
    "ours_archive_fidelity": ["TrackCodec", "archive fidelity"],
}
FAMILY = {
    "gzip": "raw_backend",
    "zlib": "raw_backend",
    "zstd-9": "raw_backend",
    "airdpro_default": "baseline",
    "zdpd_baseline": "baseline",
    "stack_zdpd_baseline": "baseline",
    "ours_eqfidelity": "trackcodec",
    "ours_strict_q6": "trackcodec",
    "ours_archive_fidelity": "trackcodec",
}
SECTION_COLUMNS = {
    "MS1": {
        "ZDPD baseline": "zdpd_baseline",
        "Stack-ZDPD baseline": "stack_zdpd_baseline",
        "Ours eqfidelity": "ours_eqfidelity",
        "Ours strict_q6": "ours_strict_q6",
        "Ours archive_fidelity": "ours_archive_fidelity",
    },
    "MS2": {
        "ZDPD baseline": "zdpd_baseline",
        "Stack-ZDPD baseline": "stack_zdpd_baseline",
        "Ours eqfidelity": "ours_eqfidelity",
    },
}

INK = "#222222"
MUTED = "#666666"
GRID = "#D8DEE4"
PANEL_EDGE = "#BFC5CC"

FONT_REGULAR = Path(r"C:\Windows\Fonts\arial.ttf")
FONT_BOLD = Path(r"C:\Windows\Fonts\arialbd.ttf")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    return ImageFont.truetype(str(path), size=size)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _blend(hex_color: str, alpha: float, bg: str = "#FFFFFF") -> str:
    fg = _hex_to_rgb(hex_color)
    bg_rgb = _hex_to_rgb(bg)
    mixed = tuple(round(f * alpha + b * (1.0 - alpha)) for f, b in zip(fg, bg_rgb))
    return "#" + "".join(f"{v:02X}" for v in mixed)


def _fmt_num(value: float) -> str:
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}"


class SvgCanvas:
    def __init__(self, width: int, height: int):
        self.width = width
        self.height = height
        self.parts: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#FFFFFF"/>',
            "<style>text{font-family:Arial,Helvetica,sans-serif;dominant-baseline:auto}.title{font-weight:700}.tick{fill:#333333}.muted{fill:#666666}</style>",
        ]

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke: str = INK,
        width: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{stroke}" stroke-width="{width:.2f}" opacity="{opacity:.3f}"{dash_attr}/>'
        )

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        stroke: str | None = None,
        width: float = 1.0,
        opacity: float = 1.0,
        rx: float = 0.0,
    ) -> None:
        stroke_attr = f' stroke="{stroke}" stroke-width="{width:.2f}"' if stroke else ""
        rx_attr = f' rx="{rx:.2f}"' if rx else ""
        self.parts.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'fill="{fill}" opacity="{opacity:.3f}"{stroke_attr}{rx_attr}/>'
        )

    def circle(
        self,
        x: float,
        y: float,
        r: float,
        fill: str,
        stroke: str | None = None,
        width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        stroke_attr = f' stroke="{stroke}" stroke-width="{width:.2f}"' if stroke else ""
        self.parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{fill}" '
            f'opacity="{opacity:.3f}"{stroke_attr}/>'
        )

    def polyline(
        self,
        points: list[tuple[float, float]],
        stroke: str,
        width: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        if len(points) < 2:
            return
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        self.parts.append(
            f'<polyline points="{pts}" fill="none" stroke="{stroke}" stroke-width="{width:.2f}" '
            f'opacity="{opacity:.3f}"{dash_attr}/>'
        )

    def polygon(
        self,
        points: list[tuple[float, float]],
        fill: str,
        stroke: str | None = None,
        width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
        stroke_attr = f' stroke="{stroke}" stroke-width="{width:.2f}"' if stroke else ""
        self.parts.append(
            f'<polygon points="{pts}" fill="{fill}" opacity="{opacity:.3f}"{stroke_attr}/>'
        )

    def text(
        self,
        x: float,
        y: float,
        text: str,
        size: int = 12,
        fill: str = INK,
        anchor: str = "start",
        weight: str = "400",
        rotate: float | None = None,
        opacity: float = 1.0,
        cls: str | None = None,
    ) -> None:
        escaped = html.escape(text)
        transform = f' transform="rotate({rotate:.1f} {x:.2f} {y:.2f})"' if rotate else ""
        class_attr = f' class="{cls}"' if cls else ""
        self.parts.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" font-weight="{weight}" opacity="{opacity:.3f}"{transform}{class_attr}>{escaped}</text>'
        )

    def save(self, path: Path) -> None:
        path.write_text("\n".join(self.parts + ["</svg>"]) + "\n", encoding="utf-8")


class PngCanvas:
    def __init__(self, width: int, height: int, scale: int = 2):
        self.width = width
        self.height = height
        self.scale = scale
        self.image = Image.new("RGB", (width * scale, height * scale), "white")
        self.draw = ImageDraw.Draw(self.image, "RGBA")

    def _xy(self, *vals: float) -> tuple[float, ...]:
        return tuple(v * self.scale for v in vals)

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        stroke: str = INK,
        width: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        fill = (*_hex_to_rgb(stroke), round(255 * opacity))
        if dash:
            dash_parts = [float(part) for part in re.split(r"[,\s]+", dash.strip()) if part]
            if len(dash_parts) < 2:
                dash_parts = [4.0, 3.0]
            total = math.hypot(x2 - x1, y2 - y1)
            if total <= 0:
                return
            ux, uy = (x2 - x1) / total, (y2 - y1) / total
            dist = 0.0
            draw_segment = True
            idx = 0
            while dist < total:
                seg = min(dash_parts[idx % len(dash_parts)], total - dist)
                if draw_segment:
                    sx, sy = x1 + ux * dist, y1 + uy * dist
                    ex, ey = x1 + ux * (dist + seg), y1 + uy * (dist + seg)
                    self.draw.line(self._xy(sx, sy, ex, ey), fill=fill, width=max(1, round(width * self.scale)))
                dist += seg
                draw_segment = not draw_segment
                idx += 1
            return
        self.draw.line(self._xy(x1, y1, x2, y2), fill=fill, width=max(1, round(width * self.scale)))

    def rect(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        stroke: str | None = None,
        width: float = 1.0,
        opacity: float = 1.0,
        rx: float = 0.0,
    ) -> None:
        fill_rgba = (*_hex_to_rgb(fill), round(255 * opacity))
        box = self._xy(x, y, x + w, y + h)
        if rx:
            self.draw.rounded_rectangle(box, radius=rx * self.scale, fill=fill_rgba)
            if stroke:
                self.draw.rounded_rectangle(
                    box,
                    radius=rx * self.scale,
                    outline=(*_hex_to_rgb(stroke), 255),
                    width=max(1, round(width * self.scale)),
                )
        else:
            self.draw.rectangle(box, fill=fill_rgba)
            if stroke:
                self.draw.rectangle(
                    box,
                    outline=(*_hex_to_rgb(stroke), 255),
                    width=max(1, round(width * self.scale)),
                )

    def circle(
        self,
        x: float,
        y: float,
        r: float,
        fill: str,
        stroke: str | None = None,
        width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        box = self._xy(x - r, y - r, x + r, y + r)
        self.draw.ellipse(box, fill=(*_hex_to_rgb(fill), round(255 * opacity)))
        if stroke:
            self.draw.ellipse(
                box,
                outline=(*_hex_to_rgb(stroke), 255),
                width=max(1, round(width * self.scale)),
            )

    def polyline(
        self,
        points: list[tuple[float, float]],
        stroke: str,
        width: float = 1.0,
        dash: str | None = None,
        opacity: float = 1.0,
    ) -> None:
        if len(points) < 2:
            return
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            self.line(x1, y1, x2, y2, stroke=stroke, width=width, dash=dash, opacity=opacity)

    def polygon(
        self,
        points: list[tuple[float, float]],
        fill: str,
        stroke: str | None = None,
        width: float = 1.0,
        opacity: float = 1.0,
    ) -> None:
        pts = [(x * self.scale, y * self.scale) for x, y in points]
        self.draw.polygon(pts, fill=(*_hex_to_rgb(fill), round(255 * opacity)))
        if stroke:
            self.draw.line(
                pts + [pts[0]],
                fill=(*_hex_to_rgb(stroke), 255),
                width=max(1, round(width * self.scale)),
            )

    def text(
        self,
        x: float,
        y: float,
        text: str,
        size: int = 12,
        fill: str = INK,
        anchor: str = "start",
        weight: str = "400",
        rotate: float | None = None,
        opacity: float = 1.0,
        cls: str | None = None,
    ) -> None:
        font = _font(size * self.scale, bold=weight in {"700", "bold"})
        fill_rgba = (*_hex_to_rgb(fill), round(255 * opacity))
        if rotate:
            probe = ImageDraw.Draw(Image.new("RGBA", (1, 1), (255, 255, 255, 0)))
            bbox = probe.textbbox((0, 0), text, font=font)
            pad = max(8, size * self.scale // 2)
            tw = max(1, bbox[2] - bbox[0])
            th = max(1, bbox[3] - bbox[1])
            temp = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (255, 255, 255, 0))
            temp_draw = ImageDraw.Draw(temp)
            temp_draw.text((pad - bbox[0], pad - bbox[1]), text, fill=fill_rgba, font=font)
            rotated = temp.rotate(float(rotate), expand=True, resample=Image.Resampling.BICUBIC)
            cx, cy = x * self.scale, y * self.scale
            if anchor == "middle":
                paste = (round(cx - rotated.width / 2), round(cy - rotated.height / 2))
            elif anchor == "end":
                paste = (round(cx - rotated.width), round(cy - rotated.height / 2))
            else:
                paste = (round(cx), round(cy - rotated.height / 2))
            self.image.paste(rotated, paste, rotated)
            return
        xy = (x * self.scale, y * self.scale)
        pil_anchor = {"start": "la", "middle": "ma", "end": "ra"}.get(anchor, "la")
        self.draw.text(
            xy,
            text,
            fill=fill_rgba,
            font=font,
            anchor=pil_anchor,
        )

    def save(self, path: Path) -> None:
        self.image.save(path)


def _make_canvases(width: int, height: int) -> tuple[SvgCanvas, PngCanvas]:
    return SvgCanvas(width, height), PngCanvas(width, height, scale=2)


def _call_both(canvases: tuple[SvgCanvas, PngCanvas], method: str, *args, **kwargs) -> None:
    getattr(canvases[0], method)(*args, **kwargs)
    getattr(canvases[1], method)(*args, **kwargs)


def _save_both(canvases: tuple[SvgCanvas, PngCanvas], base_path: Path) -> None:
    base_path.parent.mkdir(parents=True, exist_ok=True)
    canvases[0].save(base_path.with_suffix(".svg"))
    canvases[1].save(base_path.with_suffix(".png"))


def _split_markdown_row(line: str) -> list[str]:
    return [part.strip() for part in line.strip().strip("|").split("|")]


def _short_file_label(file_name: str) -> str:
    match = re.match(r"(File\d+|Set_1_F2)", file_name)
    return match.group(1) if match else file_name


def _strip_mzml_suffix(file_name: str) -> str:
    file_name = file_name.replace("Set 1_F2", "Set_1_F2")
    for suffix in (".uncompressed.mzML", ".mzML"):
        if file_name.endswith(suffix):
            return file_name[: -len(suffix)]
    return file_name


def _current_combined_file_keys() -> set[str]:
    combined_csv = TABLES_DIR / "combined_per_file_methods.csv"
    if not combined_csv.exists():
        return set()
    keys: set[str] = set()
    with combined_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            file_name = row.get("file", "")
            if file_name:
                keys.add(_strip_mzml_suffix(file_name))
    return keys


def _filter_to_current_combined(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    allowed = _current_combined_file_keys()
    if not allowed:
        return rows
    return [row for row in rows if _strip_mzml_suffix(row.get("file", "")) in allowed]


def _parse_section_tables(path: Path) -> list[dict[str, str]]:
    current_section: str | None = None
    markdown_rows: dict[str, list[str]] = {"MS1": [], "MS2": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "## MS1 CR":
            current_section = "MS1"
            continue
        if stripped == "## MS2 CR":
            current_section = "MS2"
            continue
        if stripped.startswith("## ") and current_section is not None:
            current_section = None
            continue
        if current_section in markdown_rows and stripped.startswith("|"):
            markdown_rows[current_section].append(stripped)

    out: list[dict[str, str]] = []
    for section, lines in markdown_rows.items():
        if len(lines) < 3:
            continue
        header = _split_markdown_row(lines[0])
        for line in lines[2:]:
            values = _split_markdown_row(line)
            if len(values) != len(header):
                continue
            source_row = dict(zip(header, values))
            file_name = source_row["File"]
            if file_name.startswith("Mean"):
                continue
            file_label = _short_file_label(file_name)
            ms_format = source_row["MS1/MS2 format"]
            for source_col, label in SECTION_COLUMNS[section].items():
                compression_ratio = float(source_row[source_col])
                has_section_data = not (section == "MS2" and "MS2 none" in ms_format)
                include_in_aggregate = has_section_data and compression_ratio > 0.0
                out.append(
                    {
                        "section": section,
                        "file": file_name,
                        "file_label": file_label,
                        "ms_format": ms_format,
                        "label": label,
                        "display_name": DISPLAY[label],
                        "family": FAMILY[label],
                        "color_hex": COLOR[label],
                        "compression_ratio": f"{compression_ratio:.6f}",
                        "include_in_aggregate": "yes" if include_in_aggregate else "no",
                    }
                )
    return out


def _read_raw_backend_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for source_row in csv.DictReader(handle):
            label = source_row["label"]
            if label not in {"gzip", "zlib", "zstd-9"}:
                continue
            rows.append(
                {
                    "section": source_row["section"],
                    "file": source_row["file"],
                    "file_label": source_row["file_label"],
                    "ms_format": source_row.get("ms_format", ""),
                    "label": label,
                    "display_name": DISPLAY[label],
                    "family": FAMILY[label],
                    "color_hex": COLOR[label],
                    "compression_ratio": f"{float(source_row['compression_ratio']):.6f}",
                    "include_in_aggregate": source_row["include_in_aggregate"],
                    "raw_bytes": source_row.get("raw_bytes", ""),
                    "compressed_bytes": source_row.get("compressed_bytes", ""),
                    "ms_scans": source_row.get("ms_scans", ""),
                }
            )
    return rows


def _read_aird_index_rows(path: Path, expected_label: str) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for source_row in csv.DictReader(handle):
            label = source_row["label"]
            if label != expected_label:
                continue
            rows.append(
                {
                    "section": source_row["section"],
                    "file": _strip_mzml_suffix(source_row["file"]),
                    "file_label": source_row.get("file_label") or _short_file_label(source_row["file"]),
                    "ms_format": source_row.get("ms_format", ""),
                    "label": label,
                    "display_name": DISPLAY[label],
                    "family": FAMILY[label],
                    "color_hex": COLOR[label],
                    "compression_ratio": f"{float(source_row['compression_ratio']):.6f}",
                    "include_in_aggregate": source_row["include_in_aggregate"],
                    "raw_bytes": source_row.get("raw_bytes", ""),
                    "compressed_bytes": source_row.get("compressed_bytes", ""),
                    "ms_scans": source_row.get("ms_scans", ""),
                }
            )
    return rows


def _read_trackcodec_section_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for source_row in csv.DictReader(handle):
            section = source_row["section"]
            if section == "MS1":
                label = "ours_archive_fidelity"
            elif section == "MS2":
                label = "ours_eqfidelity"
            else:
                continue
            raw = float(source_row.get("raw_bytes") or 0)
            compressed = float(source_row.get("compressed_bytes") or 0)
            ratio = float(source_row.get("compression_ratio") or 0)
            file_name = _strip_mzml_suffix(source_row["file"])
            include = raw > 0 and compressed > 0 and ratio > 0.0
            rows.append(
                {
                    "section": section,
                    "file": file_name,
                    "file_label": source_row.get("label") or _short_file_label(file_name),
                    "ms_format": "",
                    "label": label,
                    "display_name": DISPLAY[label],
                    "family": FAMILY[label],
                    "color_hex": COLOR[label],
                    "compression_ratio": f"{ratio:.6f}",
                    "include_in_aggregate": "yes" if include else "no",
                    "raw_bytes": source_row.get("raw_bytes", ""),
                    "compressed_bytes": source_row.get("compressed_bytes", ""),
                    "ms_scans": "",
                }
            )
    return rows


def _section_method_rows_with_trackcodec_stats() -> list[dict[str, str]]:
    rows = _parse_section_tables(SOURCE_MD)
    rows = [
        row
        for row in rows
        if not (
            row["label"] == "ours_archive_fidelity"
            or row["label"] == "zdpd_baseline"
            or row["label"] == "stack_zdpd_baseline"
            or (row["section"] == "MS2" and row["label"] == "ours_eqfidelity")
        )
    ]
    rows.extend(_read_trackcodec_section_rows(TRACKCODEC_SECTION_CSV))
    rows.extend(_read_aird_index_rows(AIRDPRO_AIRD_SECTION_CSV, "airdpro_default"))
    rows.extend(_read_aird_index_rows(ZDPD_AIRD_SECTION_CSV, "zdpd_baseline"))
    return rows


def _combined_section_rows() -> list[dict[str, str]]:
    method_rows = _section_method_rows_with_trackcodec_stats()
    raw_backend_rows = _read_raw_backend_rows(RAW_BACKEND_CSV)
    return _filter_to_current_combined(raw_backend_rows + method_rows)


def _method_only_section_rows() -> list[dict[str, str]]:
    return _filter_to_current_combined(_section_method_rows_with_trackcodec_stats())


def _rows_for_section(rows: list[dict[str, str]], section: str) -> list[dict[str, str]]:
    return [
        row
        for row in rows
        if row["section"] == section and row["include_in_aggregate"] == "yes"
    ]


def _aggregate_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for section, order in (("MS1", MS1_ORDER), ("MS2", MS2_ORDER)):
        section_rows = _rows_for_section(rows, section)
        values_by_label: dict[str, list[float]] = defaultdict(list)
        for row in section_rows:
            values_by_label[row["label"]].append(float(row["compression_ratio"]))
        reference_mean = statistics.mean(values_by_label[REFERENCE_LABEL])
        reference_display = DISPLAY[REFERENCE_LABEL]
        for label in order:
            values = values_by_label.get(label, [])
            if not values:
                continue
            mean_cr = statistics.mean(values)
            median_cr = statistics.median(values)
            min_cr = min(values)
            max_cr = max(values)
            relative_to_reference = (mean_cr / reference_mean - 1.0) * 100.0
            annotation = f"{mean_cr:.2f}x"
            if FAMILY[label] == "trackcodec":
                sign = "+" if relative_to_reference >= 0 else ""
                annotation = f"{mean_cr:.2f}x; {sign}{relative_to_reference:.1f}% vs {reference_display}"
            output.append(
                {
                    "section": section,
                    "label": label,
                    "display_name": DISPLAY[label],
                    "family": FAMILY[label],
                    "color_hex": COLOR[label],
                    "n_files": str(len(values)),
                    "mean_cr": f"{mean_cr:.6f}",
                    "median_cr": f"{median_cr:.6f}",
                    "min_cr": f"{min_cr:.6f}",
                    "max_cr": f"{max_cr:.6f}",
                    "relative_to_reference_mean_pct": f"{relative_to_reference:.6f}",
                    "bar_annotation": annotation,
                }
            )
    return output


def _add_relative_to_reference(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_file_section: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        by_file_section[(row["section"], row["file"])][row["label"]] = float(row["compression_ratio"])

    out: list[dict[str, str]] = []
    for row in rows:
        new_row = dict(row)
        ref = by_file_section[(row["section"], row["file"])].get(REFERENCE_LABEL, 0.0)
        value = float(row["compression_ratio"])
        if row["include_in_aggregate"] == "yes" and ref > 0.0:
            rel = (value / ref - 1.0) * 100.0
            new_row["relative_to_reference_pct"] = f"{rel:.6f}"
        else:
            new_row["relative_to_reference_pct"] = ""
        out.append(new_row)
    return out


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _section_order(section: str) -> list[str]:
    return MS1_ORDER if section == "MS1" else MS2_ORDER


def _section_advantage_plot_order(section: str) -> list[str]:
    return MS1_ADVANTAGE_PLOT_ORDER if section == "MS1" else MS2_ADVANTAGE_PLOT_ORDER


def _nice_ticks(max_value: float, n: int = 5) -> list[float]:
    if max_value <= 0:
        return [0.0, 1.0]
    raw_step = max_value / n
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    if residual <= 1:
        step = magnitude
    elif residual <= 2:
        step = 2 * magnitude
    elif residual <= 5:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    upper = math.ceil(max_value / step) * step
    ticks = []
    value = 0.0
    while value <= upper + step * 0.5:
        ticks.append(round(value, 10))
        value += step
    return ticks


def _draw_multiline_label(canvases, x: float, y: float, lines: list[str], size: int = 12) -> None:
    line_gap = size + 3
    start_y = y - (len(lines) - 1) * line_gap / 2
    for idx, line in enumerate(lines):
        _call_both(canvases, "text", x, start_y + idx * line_gap, line, size=size, anchor="middle")


def _draw_panel_frame(canvases, x: float, y: float, w: float, h: float) -> None:
    _call_both(canvases, "line", x, y + h, x + w, y + h, stroke=PANEL_EDGE, width=1.0)
    _call_both(canvases, "line", x, y, x, y + h, stroke=PANEL_EDGE, width=1.0)


def _plot_summary_bar(aggregate_rows: list[dict[str, str]], output_base: Path) -> None:
    width, height = 1280, 560
    canvases = _make_canvases(width, height)
    _call_both(canvases, "text", width / 2, 34, "Section-level compression advantage", size=21, anchor="middle", weight="700")
    by_section = defaultdict(list)
    for row in aggregate_rows:
        by_section[row["section"]].append(row)

    panels = [("MS1", 70, 78, 540, 392), ("MS2", 690, 78, 520, 392)]
    for section, px, py, pw, ph in panels:
        order = _section_order(section)
        row_by_label = {row["label"]: row for row in by_section[section]}
        rows = [row_by_label[label] for label in order if label in row_by_label]
        values = [float(row["mean_cr"]) for row in rows]
        ticks = _nice_ticks(max(values) * 1.16, n=5)
        ymax = max(ticks)
        left, right, top, bottom = px + 58, px + pw - 18, py + 44, py + ph - 72
        plot_w, plot_h = right - left, bottom - top
        _call_both(canvases, "text", px + pw / 2, py + 20, f"{section} mean compression ratio", size=15, anchor="middle", weight="700")
        for tick in ticks:
            yy = bottom - tick / ymax * plot_h
            _call_both(canvases, "line", left, yy, right, yy, stroke=GRID, width=0.8, opacity=0.8)
            _call_both(canvases, "text", left - 10, yy + 4, _fmt_num(tick), size=10, fill=MUTED, anchor="end")
        _draw_panel_frame(canvases, left, top, plot_w, plot_h)
        _call_both(canvases, "text", left - 42, top + plot_h / 2, "Compression ratio (x)", size=11, fill=MUTED, anchor="middle", rotate=-90)
        n = len(rows)
        slot = plot_w / n
        bar_w = slot * 0.58
        reference_value = float(row_by_label[REFERENCE_LABEL]["mean_cr"])
        ref_y = bottom - reference_value / ymax * plot_h
        _call_both(canvases, "line", left, ref_y, right, ref_y, stroke=COLOR[REFERENCE_LABEL], width=1.2, dash="5 4", opacity=0.88)
        _call_both(canvases, "text", right - 4, ref_y - 7, f"{DISPLAY[REFERENCE_LABEL]} mean", size=9, fill=COLOR[REFERENCE_LABEL], anchor="end")
        for idx, row in enumerate(rows):
            cx = left + slot * (idx + 0.5)
            value = float(row["mean_cr"])
            bar_h = value / ymax * plot_h
            x = cx - bar_w / 2
            y = bottom - bar_h
            _call_both(canvases, "rect", x, y, bar_w, bar_h, fill=COLOR[row["label"]])
            label_lines = DISPLAY_LINES[row["label"]]
            _draw_multiline_label(canvases, cx, bottom + 25, label_lines, size=9)
            _call_both(canvases, "text", cx, y - 8, f"{value:.2f}x", size=10, anchor="middle", weight="700")
            if FAMILY[row["label"]] == "trackcodec":
                gain = float(row["relative_to_reference_mean_pct"])
                sign = "+" if gain >= 0 else ""
                _call_both(canvases, "text", cx, y - 24, f"{sign}{gain:.1f}%", size=9, fill=COLOR[row["label"]], anchor="middle")
    _save_both(canvases, output_base)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    pos = (len(data) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return data[lo]
    return data[lo] * (hi - pos) + data[hi] * (pos - lo)


def _plot_boxplot(rows: list[dict[str, str]], output_base: Path) -> None:
    width, height = 1500, 620
    canvases = _make_canvases(width, height)
    _call_both(canvases, "text", width / 2, 32, "Section-level compression ratio distribution", size=21, anchor="middle", weight="700")
    panels = [("MS1", 92, 68, 650, 470), ("MS2", 840, 68, 560, 470)]
    for section, px, py, pw, ph in panels:
        section_rows = _rows_for_section(rows, section)
        order = _section_advantage_plot_order(section)
        grouped: dict[str, list[float]] = defaultdict(list)
        by_file: dict[str, dict[str, float]] = defaultdict(dict)
        for row in section_rows:
            grouped[row["label"]].append(float(row["compression_ratio"]))
            by_file[row["file"]][row["label"]] = float(row["compression_ratio"])
        plot_order = [label for label in order if label in grouped]
        max_val = max(max(vals) for vals in grouped.values())
        ticks = _nice_ticks(max_val * 1.08, n=5)
        xmax = max(ticks)
        left, right, top, bottom = px + 166, px + pw - 22, py + 54, py + ph - 58
        plot_w, plot_h = right - left, bottom - top
        row_top = top + 22
        row_bottom = bottom - 28
        row_gap = (row_bottom - row_top) / max(1, len(plot_order) - 1)
        _call_both(canvases, "text", px + pw / 2, py + 14, f"{section} compression ratio distribution", size=15, anchor="middle", weight="700")
        for tick in ticks:
            xx = left + tick / xmax * plot_w
            _call_both(canvases, "line", xx, top, xx, bottom, stroke=GRID, width=0.8, opacity=0.8)
            _call_both(canvases, "text", xx, bottom + 18, _fmt_num(tick), size=10, fill=MUTED, anchor="middle")
        _draw_panel_frame(canvases, left, top, plot_w, plot_h)
        _call_both(canvases, "text", left + plot_w / 2, bottom + 42, "Compression ratio (x)", size=11, fill=MUTED, anchor="middle")
        for file_idx, (file_label, values_by_label) in enumerate(sorted(by_file.items())):
            points = []
            for idx, label in enumerate(plot_order):
                if label not in values_by_label:
                    continue
                yy = row_top + idx * row_gap
                xx = left + values_by_label[label] / xmax * plot_w
                points.append((xx, yy))
            _call_both(canvases, "polyline", points, stroke="#B8B8B8", width=1.0, dash="2.5 3.2", opacity=0.55)
        for idx, label in enumerate(plot_order):
            vals = grouped[label]
            q1, med, q3 = _quantile(vals, 0.25), _quantile(vals, 0.5), _quantile(vals, 0.75)
            vmin, vmax = min(vals), max(vals)
            yy = row_top + idx * row_gap
            x_min = left + vmin / xmax * plot_w
            x_q1 = left + q1 / xmax * plot_w
            x_med = left + med / xmax * plot_w
            x_q3 = left + q3 / xmax * plot_w
            x_max = left + vmax / xmax * plot_w
            _call_both(canvases, "text", left - 12, yy + 4, DISPLAY[label], size=10, anchor="end")
            _call_both(canvases, "line", x_min, yy, x_q1, yy, stroke="#111111", width=1.2)
            _call_both(canvases, "line", x_q3, yy, x_max, yy, stroke="#111111", width=1.2)
            _call_both(canvases, "line", x_min, yy - 8, x_min, yy + 8, stroke="#111111", width=1.2)
            _call_both(canvases, "line", x_max, yy - 8, x_max, yy + 8, stroke="#111111", width=1.2)
            _call_both(canvases, "rect", x_q1, yy - 17, max(1, x_q3 - x_q1), 34, fill=_blend(COLOR[label], 0.42), stroke=COLOR[label], width=1.2)
            _call_both(canvases, "line", x_med, yy - 18, x_med, yy + 18, stroke=COLOR[label], width=1.8)
            for value in vals:
                xx = left + value / xmax * plot_w
                _call_both(canvases, "circle", xx, yy, 4.4, fill=COLOR[label], opacity=0.78)
            _call_both(canvases, "text", x_med, yy - 23, f"{med:.2f}x", size=8, anchor="middle", fill=MUTED)
    _save_both(canvases, output_base)


def _write_readme(aggregate_rows: list[dict[str, str]]) -> None:
    source_scope = (
        "AirdPro Default and ZDPD section CR are computed from existing AirdPro "
        "`.aird/.json` outputs for the current de-duplicated combined benchmark "
        "using `indexList` byte ranges; strict-q6/old eq-fidelity baselines remain "
        "available for the original Stack-ZDPD validation section source table; "
        "TrackCodec archive-fidelity section stats are read from the current "
        "de-duplicated combined benchmark; gzip level 6, zlib level 6, and zstd "
        "level 9 raw float64 baselines cover the retained section-baseline subset."
    )
    lines = [
        "# TrackCodec Section Advantage Benchmark",
        "",
        f"- Source table: `{SOURCE_MD}`",
        f"- Source scope: {source_scope}",
        "- MS2 figures and aggregates exclude files with zero MS2 raw bytes; the per-file CSV keeps those rows with `include_in_aggregate=no`.",
        f"- Reference for relative gain: `{DISPLAY[REFERENCE_LABEL]}`.",
        "- Figure structure is adapted from `<TRACKCODEC_ROOT>/experiments/benchmarks/full8_section_benchmark.py`: aggregate bar and method distribution boxplot.",
        "- Rendering uses pure SVG plus Pillow PNG output because Matplotlib crashes in this Windows environment at `Axes.bar()` with `0xc06d007f`.",
        "",
        "## Aggregate Summary",
        "",
        f"| Section | Method | n | Mean CR | Median CR | Mean gain vs {DISPLAY[REFERENCE_LABEL]} |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in aggregate_rows:
        gain = float(row["relative_to_reference_mean_pct"])
        lines.append(
            f"| {row['section']} | {row['display_name']} | {row['n_files']} | "
            f"{float(row['mean_cr']):.3f} | {float(row['median_cr']):.3f} | {gain:+.1f}% |"
        )
    lines.extend(
        [
            "",
            "## Figures",
            "",
            "| Figure | PNG | SVG |",
            "|---|---|---|",
        ]
    )
    figure_names = [
        "trackcodec_section_advantage_mean_bar",
        "trackcodec_section_advantage_compression_boxplot",
    ]
    for name in figure_names:
        lines.append(f"| {name} | `../plots/{name}.png` | `../plots/{name}.svg` |")
    (TABLES_DIR / "trackcodec_section_advantage_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> None:
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    rows = _combined_section_rows()
    rows = _add_relative_to_reference(rows)
    aggregate_rows = _aggregate_rows(rows)
    method_only_rows = _add_relative_to_reference(_method_only_section_rows())
    method_only_aggregate_rows = _aggregate_rows(method_only_rows)
    raw_backend_files = sorted({row["file"] for row in rows if row["family"] == "raw_backend"})
    trackcodec_files = sorted({row["file"] for row in rows if row["label"].startswith("ours_")})
    aird_files = sorted({row["file"] for row in rows if row["label"] in {"airdpro_default", "zdpd_baseline"}})

    _write_csv(TABLES_DIR / "trackcodec_section_advantage_per_file_methods.csv", rows)
    _write_csv(TABLES_DIR / "trackcodec_section_advantage_aggregate.csv", aggregate_rows)
    (TABLES_DIR / "trackcodec_section_advantage_summary.json").write_text(
        json.dumps(
            {
                "source_md": str(SOURCE_MD),
                "output_root": str(OUTPUT_ROOT),
                "reference": REFERENCE_LABEL,
                "aggregate_rows": aggregate_rows,
                "raw_backend_csv": str(RAW_BACKEND_CSV),
                "trackcodec_section_csv": str(TRACKCODEC_SECTION_CSV),
                "zdpd_aird_section_csv": str(ZDPD_AIRD_SECTION_CSV),
                "n_raw_backend_files": len(raw_backend_files),
                "n_trackcodec_files": len(trackcodec_files),
                "n_aird_family_files": len(aird_files),
                "note": (
                    f"gzip level 6/zlib level 6/zstd level 9 raw float64 section baselines cover {len(raw_backend_files)} files; "
                    f"TrackCodec archive-fidelity section stats cover {len(trackcodec_files)} files in the current de-duplicated combined benchmark; "
                    f"AirdPro Default and ZDPD section stats cover {len(aird_files)} files via AirdPro indexList byte ranges; "
                    "strict-q6/old eq-fidelity baselines remain limited to the 8-file section source table."
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_readme(aggregate_rows)

    _plot_summary_bar(method_only_aggregate_rows, PLOTS_DIR / "trackcodec_section_advantage_mean_bar")
    _plot_boxplot(rows, PLOTS_DIR / "trackcodec_section_advantage_compression_boxplot")

    print(f"Wrote section advantage tables to {TABLES_DIR}")
    print(f"Wrote section advantage plots to {PLOTS_DIR}")


if __name__ == "__main__":
    main()
