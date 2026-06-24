from __future__ import annotations

import argparse
import csv
import html
import io
import json
import math
import re
from pathlib import Path
from statistics import mean, median

from benchmark_release_paths import default_combined_output_dir, find_workspace_root, inputs_root

GIB = 1024**3
ROOT = find_workspace_root(Path(__file__))

METHOD_ORDER = [
    "original_mzml",
    "msconvert_zlib",
    "msconvert_gzip",
    "msconvert_numpress",
    "mspack",
    "masscomp",
    "airdpro",
    "zdpd",
    "stackzdpd",
    "trackcodec",
]
COMPRESSION_DISTRIBUTION_EXCLUDE = {"stackzdpd"}

DISPLAY = {
    "original_mzml": "Original mzML",
    "msconvert_zlib": "zlib mzML",
    "msconvert_gzip": "gzip mzML",
    "msconvert_numpress": "Numpress+zlib",
    "mspack": "mspack",
    "masscomp": "MassComp",
    "airdpro": "AirdPro",
    "zdpd": "ZDPD",
    "stackzdpd": "Stack-ZDPD",
    "trackcodec": "TrackCodec",
}

SHORT_DISPLAY = {
    "original_mzml": "mzML",
    "msconvert_zlib": "zlib(def)",
    "msconvert_gzip": "gzip(def)",
    "msconvert_numpress": "Numpress+zlib",
    "mspack": "mspack",
    "masscomp": "MassComp",
    "airdpro": "AirdPro",
    "zdpd": "ZDPD",
    "stackzdpd": "Stack-ZDPD",
    "trackcodec": "TrackCodec",
}

DISTRIBUTION_LABEL_LINES = {
    "original_mzml": ["mzML"],
    "msconvert_zlib": ["zlib", "level 6"],
    "msconvert_gzip": ["gzip", "level 6"],
    "msconvert_numpress": ["NumpressAll", "+ zlib L6"],
    "mspack": ["mspack"],
    "masscomp": ["MassComp"],
    "airdpro": ["AirdPro"],
    "zdpd": ["ZDPD"],
    "stackzdpd": ["Stack-ZDPD"],
    "trackcodec": ["TrackCodec"],
}

METHOD_GROUPS = {
    "original_mzml": "original",
    "msconvert_zlib": "msconvert",
    "msconvert_gzip": "msconvert",
    "msconvert_numpress": "msconvert",
    "mspack": "external",
    "masscomp": "external",
    "airdpro": "airdpro",
    "zdpd": "airdpro",
    "stackzdpd": "airdpro",
    "trackcodec": "trackcodec",
}

GROUP_COLORS = {
    "original": "#B09C85",
    "msconvert": "#3C5488",
    "external": "#00A087",
    "airdpro": "#E64B35",
    "trackcodec": "#DC0000",
}

METHOD_COLORS = {
    "original_mzml": "#B09C85",
    "msconvert_zlib": "#4DBBD5",
    "msconvert_gzip": "#91D1C2",
    "msconvert_numpress": "#3C5488",
    "mspack": "#00A087",
    "masscomp": "#8491B4",
    "airdpro": "#F39B7F",
    "zdpd": "#E64B35",
    "stackzdpd": "#7E6148",
    "trackcodec": "#DC0000",
}

FILE_LINE_COLORS = [
    "#E64B35",
    "#4DBBD5",
    "#00A087",
    "#3C5488",
    "#F39B7F",
    "#8491B4",
    "#91D1C2",
    "#DC0000",
    "#7E6148",
    "#B09C85",
    "#A73030",
    "#2D6A8E",
    "#3B8C6E",
    "#6C6B9D",
    "#C97B63",
    "#5B6B8C",
    "#6FAF9F",
    "#8C3B3B",
    "#5C4638",
    "#8F806B",
]

DATASET_LABELS = {
    "full8": "Full8",
    "data_stackzdpd": "Data_StackZDPD",
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def is_finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def safe_float(value: object) -> float:
    try:
        if value is None:
            return math.nan
        text = str(value).strip()
        if not text:
            return math.nan
        return float(text)
    except Exception:
        return math.nan


def safe_int(value: object) -> int:
    try:
        if value is None:
            return 0
        text = str(value).strip()
        if not text:
            return 0
        return int(float(text))
    except Exception:
        return 0


def quantile(values: list[float], q: float) -> float:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return math.nan
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def nice_max(value: float) -> float:
    if not math.isfinite(value) or value <= 0:
        return 1.0
    raw = value * 1.12
    magnitude = 10 ** math.floor(math.log10(raw))
    fraction = raw / magnitude
    if fraction <= 1.5:
        nice = 1.5
    elif fraction <= 2:
        nice = 2
    elif fraction <= 3:
        nice = 3
    elif fraction <= 5:
        nice = 5
    else:
        nice = 10
    return nice * magnitude


def nice_linear_ticks(max_value: float, target_intervals: int = 5) -> list[float]:
    if not math.isfinite(max_value) or max_value <= 0:
        return [0.0, 1.0]
    raw_step = max_value / max(1, target_intervals)
    magnitude = 10 ** math.floor(math.log10(raw_step))
    fraction = raw_step / magnitude
    if fraction <= 1:
        step = 1.0
    elif fraction <= 2:
        step = 2.0
    elif fraction <= 2.5:
        step = 2.5
    elif fraction <= 5:
        step = 5.0
    else:
        step = 10.0
    step *= magnitude
    upper = math.ceil(max_value / step) * step
    count = int(round(upper / step))
    return [i * step for i in range(count + 1)]


def format_axis_number(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:,.0f}"
    if abs(value) >= 10:
        return f"{value:.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def short_full8_label(file_name: str, rank: int) -> str:
    if file_name.startswith("QC_E4802"):
        return "F8-E4802"
    if file_name.startswith("QC_E4804_240320") and "centroided" in file_name:
        return "F8-E4804-cent"
    if file_name.startswith("QC_E4804_240320"):
        return "F8-E4804-R1"
    if file_name.startswith("QC_E4804_240403"):
        return "F8-E4804-R2"
    if file_name.startswith("QC_E4805_240328"):
        return "F8-E4805-R2"
    if file_name.startswith("QC_E4805_240709"):
        return "F8-E4805-DIA"
    if "ETD-1h" in file_name:
        return "F8-ETD"
    if "true_uncompressed" in file_name:
        return "F8-DDA-true"
    if "DDA-1h" in file_name:
        return "F8-DDA"
    return f"F8-{rank:02d}"


def short_stack_label(file_name: str) -> str:
    base = file_name.replace(".uncompressed.mzML", "").replace(".mzML", "")
    if base == "Set 1_F2":
        return "SZ-Set_1_F2"
    if base.startswith("File"):
        head = base.split("_", 1)[0]
        if head[4:].isdigit():
            return f"SZ-{head}"
    return f"SZ-{base[:18]}"


def short_label(dataset_key: str, file_name: str, rank: int) -> str:
    if dataset_key == "full8":
        return short_full8_label(file_name, rank)
    if dataset_key == "data_stackzdpd":
        return short_stack_label(file_name)
    return f"{dataset_key[:3].upper()}-{rank:02d}"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_vendor_key(file_name: str) -> str:
    key = Path(file_name).stem
    key = key.replace(".uncompressed", "")
    if key.endswith("_centroided"):
        key = key[: -len("_centroided")]
    return key


def load_vendor_raw_dirs(paths: list[Path]) -> dict[str, int]:
    vendor: dict[str, int] = {}
    for root in paths:
        if not root.exists():
            continue
        for raw_path in root.rglob("*"):
            if not raw_path.is_file():
                continue
            if raw_path.suffix.lower() not in {".raw", ".wiff", ".wiff2"}:
                continue
            vendor[normalize_vendor_key(raw_path.name)] = int(raw_path.stat().st_size)
    return vendor


def parse_size_to_bytes(text: str) -> int:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B?|[KMGT])?", text, re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    multipliers = {
        "b": 1,
        "": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": GIB,
        "gb": GIB,
        "gib": GIB,
        "t": 1024**4,
        "tb": 1024**4,
        "tib": 1024**4,
    }
    return int(round(value * multipliers.get(unit, 1)))


def load_vendor_size_markdown(paths: list[Path]) -> dict[str, int]:
    vendor: dict[str, int] = {}
    for path in paths:
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        in_raw_size_section = False
        for line in lines:
            if line.startswith("## "):
                in_raw_size_section = "Raw Source Size Versus Converted mzML Size" in line
                continue
            if not in_raw_size_section or not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            file_name, size_text = cells[0], cells[1]
            if not file_name.lower().endswith(".mzml"):
                continue
            size_bytes = parse_size_to_bytes(size_text)
            if size_bytes > 0:
                vendor[normalize_vendor_key(file_name)] = size_bytes
    return vendor


def vendor_size_for_file(file_name: str, vendor: dict[str, int]) -> int:
    key = normalize_vendor_key(file_name)
    if key in vendor:
        return vendor[key]
    return 0


def load_summary(
    dataset_key: str,
    csv_path: Path,
    expected_methods: int,
    vendor_raw_sizes: dict[str, int],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    src = read_csv_rows(csv_path)
    files: dict[str, list[dict[str, str]]] = {}
    file_order: list[str] = []
    for row in src:
        file_name = row.get("file_name", "")
        if file_name not in files:
            files[file_name] = []
            file_order.append(file_name)
        files[file_name].append(row)

    counts: list[dict[str, object]] = []
    rows: list[dict[str, object]] = []
    complete_rank = 0
    for file_name in file_order:
        file_rows = files[file_name]
        is_complete = len(file_rows) >= expected_methods
        counts.append(
            {
                "dataset_key": dataset_key,
                "dataset": DATASET_LABELS.get(dataset_key, dataset_key),
                "file": file_name,
                "method_rows": len(file_rows),
                "is_complete": is_complete,
            }
        )
        if not is_complete:
            continue

        complete_rank += 1
        first = file_rows[0]
        raw = safe_int(first.get("original_size_bytes"))
        vendor_raw = vendor_size_for_file(file_name, vendor_raw_sizes)
        input_path = first.get("input_path", "")
        sample_id = f"{dataset_key}::{file_name}"
        label = short_label(dataset_key, file_name, complete_rank)
        common = {
            "dataset_key": dataset_key,
            "dataset": DATASET_LABELS.get(dataset_key, dataset_key),
            "file": file_name,
            "sample_id": sample_id,
            "input_path": input_path,
            "combined_file_label": label,
            "raw_bytes": raw,
            "raw_gib": raw / GIB,
            "vendor_raw_bytes": vendor_raw,
            "vendor_raw_gib": vendor_raw / GIB if vendor_raw > 0 else math.nan,
            "source_csv": str(csv_path),
        }
        rows.append(
            {
                **common,
                "method": "original_mzml",
                "display_name": DISPLAY["original_mzml"],
                "status": "ok",
                "compressed_bytes": raw,
                "compressed_gib": raw / GIB,
                "compression_ratio": 1.0,
                "compression_rate_pct": 100.0,
                "elapsed_seconds": math.nan,
                "note": "",
            }
        )
        for row in file_rows:
            method = row.get("method", "")
            if method not in DISPLAY:
                continue
            status = row.get("status", "")
            compressed = safe_int(row.get("compressed_size_bytes"))
            ratio = safe_float(row.get("compression_ratio"))
            if not math.isfinite(ratio) and raw > 0 and compressed > 0:
                ratio = raw / compressed
            ok = status == "ok" and compressed > 0 and math.isfinite(ratio)
            rows.append(
                {
                    **common,
                    "method": method,
                    "display_name": DISPLAY[method],
                    "status": status,
                    "compressed_bytes": compressed if ok else 0,
                    "compressed_gib": compressed / GIB if ok else math.nan,
                    "compression_ratio": ratio if ok else math.nan,
                    "compression_rate_pct": (compressed / raw * 100.0) if ok and raw > 0 else math.nan,
                    "elapsed_seconds": safe_float(row.get("elapsed_seconds")),
                    "note": row.get("note", ""),
                }
            )
    return rows, counts


def parse_inputs(values: list[str]) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"Expected dataset_key=summary.csv, got {value}")
        key, path = value.split("=", 1)
        out.append((key.strip(), Path(path).resolve()))
    return out


def load_all(
    inputs: list[tuple[str, Path]],
    expected_methods: int,
    vendor_raw_sizes: dict[str, int],
    exclude_files: set[str] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    exclude_files = exclude_files or set()
    all_rows: list[dict[str, object]] = []
    all_counts: list[dict[str, object]] = []
    for dataset_key, csv_path in inputs:
        rows, counts = load_summary(dataset_key, csv_path, expected_methods, vendor_raw_sizes)
        if exclude_files:
            rows = [r for r in rows if str(r["file"]) not in exclude_files]
            counts = [r for r in counts if str(r["file"]) not in exclude_files]
        all_rows.extend(rows)
        all_counts.extend(counts)
    order = {method: idx for idx, method in enumerate(METHOD_ORDER)}
    all_rows.sort(key=lambda r: (str(r["dataset_key"]), str(r["combined_file_label"]), order.get(str(r["method"]), 999)))
    return all_rows, all_counts


def aggregate(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for method in METHOD_ORDER:
        sub = [r for r in rows if r["method"] == method and r["status"] == "ok" and is_finite(r["compression_ratio"])]
        if not sub:
            continue
        total_raw = sum(int(r["raw_bytes"]) for r in sub)
        total_compressed = sum(int(r["compressed_bytes"]) for r in sub)
        ratios = [float(r["compression_ratio"]) for r in sub]
        times = [float(r["elapsed_seconds"]) for r in sub if is_finite(r["elapsed_seconds"])]
        out.append(
            {
                "method": method,
                "display_name": DISPLAY[method],
                "group": METHOD_GROUPS[method],
                "n_files": len({str(r["sample_id"]) for r in sub}),
                "total_raw_bytes": total_raw,
                "total_compressed_bytes": total_compressed,
                "compressed_gib": total_compressed / GIB,
                "aggregate_compression_ratio": total_raw / total_compressed if total_compressed > 0 else math.nan,
                "mean_compression_ratio": mean(ratios),
                "median_compression_ratio": median(ratios),
                "mean_elapsed_seconds": mean(times) if times else math.nan,
                "compression_rate_pct": total_compressed / total_raw * 100.0 if total_raw > 0 else math.nan,
            }
        )
    return out


def median_ordered_methods(rows: list[dict[str, object]]) -> list[str]:
    medians: dict[str, float] = {}
    default_order = {method: idx for idx, method in enumerate(METHOD_ORDER)}
    for method in METHOD_ORDER:
        values = [
            float(r["compression_ratio"])
            for r in rows
            if r["method"] == method and r["status"] == "ok" and is_finite(r["compression_ratio"])
        ]
        if values:
            medians[method] = median(values)
    methods = sorted(medians, key=lambda method: (medians[method], default_order.get(method, 999)))
    if "stackzdpd" in methods and "zdpd" in methods:
        methods.remove("stackzdpd")
        methods.insert(methods.index("zdpd") + 1, "stackzdpd")
    return methods


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        if not path.exists() or path.read_text(encoding="utf-8") != "":
            path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    content = buffer.getvalue()
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    try:
        path.write_text(content, encoding="utf-8")
    except PermissionError:
        if path.exists():
            return
        raise


def svg_doc(width: int, height: int, body: list[str]) -> str:
    style = """
<style>
  .title{font:700 23px Arial,sans-serif;fill:#1f2933}
  .subtitle{font:12px Arial,sans-serif;fill:#52606d}
  .axis{font:12px Arial,sans-serif;fill:#374151}
  .small{font:10px Arial,sans-serif;fill:#374151}
  .tiny{font:8.5px Arial,sans-serif;fill:#374151}
  .grid{stroke:#d9e2ec;stroke-width:1;stroke-dasharray:2 5}
  .axisline{stroke:#7b8794;stroke-width:1.2}
</style>
"""
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
            style,
            f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>',
            *body,
            "</svg>",
        ]
    )


def draw_axes(
    width: int,
    height: int,
    left: int,
    right: int,
    top: int,
    bottom: int,
    y_max: float,
    y_label: str,
    *,
    y_min: float = 0.0,
    x_padding_slots: float = 0.0,
) -> tuple[list[str], callable, callable]:
    plot_w = width - left - right
    plot_h = height - top - bottom

    def x_at(index: float, n: int) -> float:
        if n <= 1:
            return left + plot_w / 2
        if x_padding_slots > 0:
            return left + (index + x_padding_slots) * plot_w / (n - 1 + x_padding_slots * 2)
        return left + index * plot_w / (n - 1)

    def y_at(value: float) -> float:
        span = y_max - y_min
        if span <= 0:
            return top + plot_h
        return top + plot_h - ((value - y_min) / span) * plot_h

    parts: list[str] = []
    ticks = 6
    for i in range(ticks + 1):
        value = y_min + (y_max - y_min) * i / ticks
        y = y_at(value)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        parts.append(f'<line class="axisline" x1="{left-5}" y1="{y:.2f}" x2="{left}" y2="{y:.2f}"/>')
        parts.append(f'<text class="axis" x="{left-10}" y="{y+4:.2f}" text-anchor="end">{value:.1f}</text>')
    parts.append(f'<line class="axisline" x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}"/>')
    parts.append(f'<line class="axisline" x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}"/>')
    parts.append(
        f'<text class="axis" x="24" y="{top + plot_h/2:.2f}" text-anchor="middle" transform="rotate(-90 24 {top + plot_h/2:.2f})">{esc(y_label)}</text>'
    )
    return parts, x_at, y_at


def plot_distribution(rows: list[dict[str, object]], methods: list[str], path: Path) -> None:
    plot_rows = [r for r in rows if r["method"] in methods and r["status"] == "ok" and is_finite(r["compression_ratio"])]
    samples = list(dict.fromkeys(str(r["sample_id"]) for r in rows))
    labels_by_sample = {str(r["sample_id"]): str(r["combined_file_label"]) for r in rows}
    width, height = 1660, 820
    left, right, top, bottom = 90, 38, 62, 148
    max_ratio = max([float(r["compression_ratio"]) for r in plot_rows] or [1.0])
    y_max = nice_max(max_ratio)
    y_min = 0.75
    body: list[str] = [
        f'<text class="title" x="{width/2:.2f}" y="40" text-anchor="middle">Compression Ratio Distribution by Method</text>',
    ]
    axes, x_at, y_at = draw_axes(
        width,
        height,
        left,
        right,
        top,
        bottom,
        y_max,
        "Compression ratio (x)",
        y_min=y_min,
        x_padding_slots=0.55,
    )
    body.extend(axes)
    by_method = {m: [float(r["compression_ratio"]) for r in plot_rows if r["method"] == m] for m in methods}
    box_w = min(72, (width - left - right) / max(len(methods), 1) * 0.55)

    for idx, method in enumerate(methods):
        vals = by_method.get(method, [])
        if not vals:
            continue
        x = x_at(idx, len(methods))
        q1, med, q3 = quantile(vals, 0.25), quantile(vals, 0.5), quantile(vals, 0.75)
        low, high = min(vals), max(vals)
        y_low, y_high = y_at(low), y_at(high)
        y_q1, y_q3, y_med = y_at(q1), y_at(q3), y_at(med)
        body.append(f'<line x1="{x:.2f}" y1="{y_high:.2f}" x2="{x:.2f}" y2="{y_low:.2f}" stroke="#374151" stroke-width="1.1"/>')
        body.append(f'<line x1="{x-box_w*0.25:.2f}" y1="{y_high:.2f}" x2="{x+box_w*0.25:.2f}" y2="{y_high:.2f}" stroke="#374151" stroke-width="1.1"/>')
        body.append(f'<line x1="{x-box_w*0.25:.2f}" y1="{y_low:.2f}" x2="{x+box_w*0.25:.2f}" y2="{y_low:.2f}" stroke="#374151" stroke-width="1.1"/>')
        body.append(
            f'<rect x="{x-box_w/2:.2f}" y="{y_q3:.2f}" width="{box_w:.2f}" height="{max(1, y_q1-y_q3):.2f}" fill="{METHOD_COLORS[method]}" fill-opacity="0.78" stroke="#222222" stroke-width="1.1"/>'
        )
        body.append(f'<line x1="{x-box_w/2:.2f}" y1="{y_med:.2f}" x2="{x+box_w/2:.2f}" y2="{y_med:.2f}" stroke="#111111" stroke-width="2"/>')

    for sample_idx, sample_id in enumerate(samples):
        points: list[tuple[float, float]] = []
        for method_idx, method in enumerate(methods):
            vals = [
                float(r["compression_ratio"])
                for r in plot_rows
                if r["sample_id"] == sample_id and r["method"] == method
            ]
            if vals:
                points.append((x_at(method_idx, len(methods)), y_at(vals[0])))
        if len(points) >= 2:
            text_points = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
            color = FILE_LINE_COLORS[sample_idx % len(FILE_LINE_COLORS)]
            body.append(f'<polyline points="{text_points}" fill="none" stroke="{color}" stroke-width="1.25" stroke-opacity="0.32" stroke-dasharray="5 5"/>')
        for x, y in points:
            body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4.0" fill="#111111" stroke="#ffffff" stroke-width="0.8"><title>{esc(labels_by_sample.get(sample_id, sample_id))}</title></circle>')

    for idx, method in enumerate(methods):
        x = x_at(idx, len(methods))
        axis_y = height - bottom
        body.append(f'<line class="axisline" x1="{x:.2f}" y1="{axis_y:.2f}" x2="{x:.2f}" y2="{axis_y+6:.2f}"/>')
        label_lines = DISTRIBUTION_LABEL_LINES.get(method, [SHORT_DISPLAY[method]])
        label_y = axis_y + 24
        body.append(f'<text class="axis" x="{x:.2f}" y="{label_y:.2f}" text-anchor="middle">')
        for line_idx, label_line in enumerate(label_lines):
            dy = 0 if line_idx == 0 else 14
            body.append(f'<tspan x="{x:.2f}" dy="{dy}">{esc(label_line)}</tspan>')
        body.append("</text>")
    body.append(
        f'<text class="axis" x="{width/2:.2f}" y="{height-18}" '
        f'text-anchor="middle" style="font-size:18px;">'
        f'mzML and compression method</text>'
        )
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def plot_bar(
    agg: list[dict[str, object]],
    methods: list[str],
    path: Path,
    value_key: str,
    y_label: str,
    title: str,
    label_key: str = "mean_compression_ratio",
) -> None:
    method_rank = {method: idx for idx, method in enumerate(methods)}
    rows = [r for r in agg if str(r["method"]) in method_rank and is_finite(r.get(value_key))]
    rows.sort(key=lambda row: method_rank.get(str(row["method"]), 999))
    width, height = 1500, 720
    left, right, top, bottom = 95, 35, 86, 170
    max_value = max([float(r[value_key]) for r in rows] or [1.0])
    y_max = nice_max(max_value)
    body = [
        f'<text class="title" x="{left}" y="38">{esc(title)}</text>',
        '<text class="subtitle" x="95" y="62">Labels show value and number of files contributing to each method.</text>',
    ]
    axes, x_at, y_at = draw_axes(width, height, left, right, top, bottom, y_max, y_label)
    body.extend(axes)
    plot_h = height - top - bottom
    n = len(rows)
    step = (width - left - right) / max(n, 1)
    bar_w = min(82, step * 0.68)
    for idx, row in enumerate(rows):
        method = str(row["method"])
        x = left + step * (idx + 0.5)
        value = float(row[value_key])
        y = y_at(value)
        body.append(f'<rect x="{x-bar_w/2:.2f}" y="{y:.2f}" width="{bar_w:.2f}" height="{top+plot_h-y:.2f}" fill="{METHOD_COLORS[method]}" fill-opacity="0.86"/>')
        if label_key == "compressed_gib":
            label = f"{value:.1f} GiB"
        else:
            label = f"{value:.2f}x"
        body.append(f'<text class="tiny" x="{x:.2f}" y="{max(top+12, y-8):.2f}" text-anchor="middle">{esc(label)}</text>')
        body.append(f'<text class="tiny" x="{x:.2f}" y="{max(top+24, y+5):.2f}" text-anchor="middle">n={int(row["n_files"])}</text>')
        y_lab = height - bottom + top + 30
        body.append(f'<text class="axis" x="{x:.2f}" y="{y_lab:.2f}" text-anchor="end" transform="rotate(-38 {x:.2f} {y_lab:.2f})">{esc(DISPLAY[method])}</text>')
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def color_scale(value: float, vmin: float, vmax: float) -> str:
    if not math.isfinite(value):
        return "#F2F2F2"
    t = 0.0 if vmax <= vmin else max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    stops = [(0xEC, 0xF7, 0xFB), (0x7B, 0xCC, 0xC4), (0x08, 0x68, 0xAC)]
    if t < 0.5:
        a, b, local = stops[0], stops[1], t / 0.5
    else:
        a, b, local = stops[1], stops[2], (t - 0.5) / 0.5
    rgb = tuple(round(a[i] + (b[i] - a[i]) * local) for i in range(3))
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def plot_heatmap(rows: list[dict[str, object]], methods: list[str], path: Path) -> None:
    files = list(dict.fromkeys((str(r["sample_id"]), str(r["combined_file_label"])) for r in rows))
    values = {
        (str(r["method"]), str(r["sample_id"])): float(r["compression_ratio"])
        for r in rows
        if r["method"] in methods and r["status"] == "ok" and is_finite(r["compression_ratio"])
    }
    finite = list(values.values())
    vmin, vmax = (min(finite), max(finite)) if finite else (0.0, 1.0)
    cell_w, cell_h = 58, 31
    left, top = 165, 82
    width = left + len(files) * cell_w + 40
    height = top + len(methods) * cell_h + 128
    body = [
        '<text class="title" x="90" y="38">Method x File Compression Ratio Heatmap</text>',
        '<text class="subtitle" x="90" y="62">Failed runs are shown as N/A; values are compression ratio (input mzML bytes / compressed bytes).</text>',
    ]
    for i, method in enumerate(methods):
        y = top + i * cell_h
        body.append(f'<text class="axis" x="{left-10}" y="{y+20}" text-anchor="end">{esc(DISPLAY[method])}</text>')
        for j, (sample_id, label) in enumerate(files):
            x = left + j * cell_w
            value = values.get((method, sample_id), math.nan)
            fill = color_scale(value, vmin, vmax)
            body.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{fill}" stroke="#ffffff" stroke-width="1"/>')
            text = "N/A" if not math.isfinite(value) else f"{value:.1f}x"
            text_color = "#666666" if not math.isfinite(value) else ("#ffffff" if value > (vmin + vmax) / 2 else "#111111")
            body.append(f'<text class="tiny" x="{x+cell_w/2:.2f}" y="{y+20}" text-anchor="middle" fill="{text_color}">{esc(text)}</text>')
    for j, (_, label) in enumerate(files):
        x = left + j * cell_w + cell_w / 2
        y = top + len(methods) * cell_h + 18
        body.append(f'<text class="tiny" x="{x:.2f}" y="{y:.2f}" text-anchor="end" transform="rotate(-55 {x:.2f} {y:.2f})">{esc(label)}</text>')
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def plot_status_matrix(rows: list[dict[str, object]], methods: list[str], path: Path) -> None:
    files = list(dict.fromkeys((str(r["sample_id"]), str(r["combined_file_label"])) for r in rows))
    status = {(str(r["method"]), str(r["sample_id"])): str(r["status"]) for r in rows if r["method"] in methods}
    cell_w, cell_h = 58, 30
    left, top = 165, 82
    width = left + len(files) * cell_w + 40
    height = top + len(methods) * cell_h + 128
    body = [
        '<text class="title" x="90" y="38">Method x File Status Matrix</text>',
        '<text class="subtitle" x="90" y="62">Green indicates an OK result; red indicates an error row recorded in the benchmark summary.</text>',
    ]
    for i, method in enumerate(methods):
        y = top + i * cell_h
        body.append(f'<text class="axis" x="{left-10}" y="{y+19}" text-anchor="end">{esc(DISPLAY[method])}</text>')
        for j, (sample_id, _) in enumerate(files):
            x = left + j * cell_w
            value = status.get((method, sample_id), "")
            if value == "ok":
                fill, text = "#59A14F", "OK"
            elif value:
                fill, text = "#E15759", "ERR"
            else:
                fill, text = "#F2F2F2", "N/A"
            body.append(f'<rect x="{x}" y="{y}" width="{cell_w}" height="{cell_h}" fill="{fill}" stroke="#ffffff" stroke-width="1"/>')
            body.append(f'<text class="tiny" x="{x+cell_w/2:.2f}" y="{y+19}" text-anchor="middle" fill="{"#ffffff" if text != "N/A" else "#666666"}">{text}</text>')
    for j, (_, label) in enumerate(files):
        x = left + j * cell_w + cell_w / 2
        y = top + len(methods) * cell_h + 18
        body.append(f'<text class="tiny" x="{x:.2f}" y="{y:.2f}" text-anchor="end" transform="rotate(-55 {x:.2f} {y:.2f})">{esc(label)}</text>')
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def plot_ratio_time(agg: list[dict[str, object]], path: Path) -> None:
    rows = [
        r
        for r in agg
        if r["method"] not in {"original_mzml", "stackzdpd"}
        and is_finite(r["mean_elapsed_seconds"])
        and is_finite(r["median_compression_ratio"])
    ]
    width, height = 1120, 680
    left, right, top, bottom = 95, 45, 88, 82
    max_x_raw = max([float(r["mean_elapsed_seconds"]) for r in rows] or [1.0])
    x_ticks = nice_linear_ticks(max_x_raw, target_intervals=5)
    max_x = max(x_ticks[-1], 1.0)
    max_y = 8.0
    plot_w, plot_h = width - left - right, height - top - bottom
    body = [
        '<text class="title" x="95" y="38">Median Compression Ratio vs Mean Elapsed Time</text>',
        '<text class="subtitle" x="95" y="62">Y-axis uses the median per-file compression ratio; X-axis uses linear seconds. Stack-ZDPD is excluded.</text>',
    ]
    for i in range(7):
        yv = max_y * i / 6
        y = top + plot_h - yv / max_y * plot_h
        body.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        body.append(f'<text class="axis" x="{left-10}" y="{y+4:.2f}" text-anchor="end">{yv:.1f}</text>')
    for xv in x_ticks:
        x = left + xv / max_x * plot_w if max_x > 0 else left
        body.append(f'<line class="grid" x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top+plot_h}"/>')
        body.append(f'<text class="axis" x="{x:.2f}" y="{top+plot_h+20}" text-anchor="middle">{format_axis_number(xv)}</text>')
    body.append(f'<line class="axisline" x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}"/>')
    body.append(f'<line class="axisline" x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}"/>')
    for row in rows:
        method = str(row["method"])
        xval = float(row["mean_elapsed_seconds"])
        yval = float(row["median_compression_ratio"])
        x = left + xval / max_x * plot_w if max_x > 0 else left
        y = top + plot_h - yval / max_y * plot_h
        r = 8 + min(12, int(row["n_files"]) * 0.6)
        body.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r}" fill="{METHOD_COLORS[method]}" fill-opacity="0.90" stroke="#ffffff" stroke-width="1.2"/>')
        body.append(f'<text class="small" x="{x+r+5:.2f}" y="{y+4:.2f}">{esc(DISPLAY[method])}</text>')
    body.append(f'<text class="axis" x="{width/2:.2f}" y="{height-20}" text-anchor="middle">Mean elapsed time per successful file (s)</text>')
    body.append(f'<text class="axis" x="24" y="{top+plot_h/2:.2f}" text-anchor="middle" transform="rotate(-90 24 {top+plot_h/2:.2f})">Median compression ratio (x)</text>')
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def plot_multipanel(rows: list[dict[str, object]], methods: list[str], path: Path) -> None:
    files = list(dict.fromkeys((str(r["sample_id"]), str(r["combined_file_label"])) for r in rows))
    values = {(str(r["sample_id"]), str(r["method"])): r for r in rows if r["method"] in methods and r["status"] == "ok" and is_finite(r["compressed_gib"])}
    vendor_by_sample = {
        str(r["sample_id"]): float(r["vendor_raw_gib"])
        for r in rows
        if is_finite(r.get("vendor_raw_gib"))
    }
    raw_by_sample = {
        str(r["sample_id"]): float(r["raw_gib"])
        for r in rows
        if r["method"] == "original_mzml" and is_finite(r.get("raw_gib"))
    }
    ncols = 7
    panel_w, panel_h = 330, 268
    left, top = 48, 92
    width = left * 2 + ncols * panel_w
    nrows = math.ceil(len(files) / ncols)
    height = top + nrows * panel_h + 54
    body = [
        '<text class="title" x="48" y="38">Compressed Size by Method for Each Completed File</text>',
        '<text class="subtitle" x="48" y="62">Bar labels show size, ratio, and rate; vendor raw dashed lines are shown only when not larger than mzML input. zlib/gzip use ProteoWizard default level; Numpress+zlib uses numpressAll.</text>',
    ]
    for idx, (sample_id, label) in enumerate(files):
        col, row = idx % ncols, idx // ncols
        x0, y0 = left + col * panel_w, top + row * panel_h
        vals = [float(values[(sample_id, m)]["compressed_gib"]) for m in methods if (sample_id, m) in values]
        vendor_gib = vendor_by_sample.get(sample_id, math.nan)
        raw_gib = raw_by_sample.get(sample_id, math.nan)
        draw_vendor = math.isfinite(vendor_gib) and vendor_gib > 0 and (not math.isfinite(raw_gib) or vendor_gib <= raw_gib)
        ymax = max(vals + ([vendor_gib] if draw_vendor else []) or [1.0]) * 1.38
        body.append(f'<text class="small" x="{x0 + panel_w / 2:.2f}" y="{y0-10}" text-anchor="middle">{esc(label)}</text>')
        body.append(f'<line class="axisline" x1="{x0+32}" y1="{y0+170}" x2="{x0+panel_w-12}" y2="{y0+170}"/>')
        body.append(f'<line class="axisline" x1="{x0+32}" y1="{y0+18}" x2="{x0+32}" y2="{y0+170}"/>')
        inner_w = panel_w - 50
        bar_w = inner_w / len(methods) * 0.70
        vendor_label = ""
        if draw_vendor and ymax > 0:
            y_vendor = y0 + 170 - vendor_gib / ymax * 150
            body.append(
                f'<line x1="{x0+32}" y1="{y_vendor:.2f}" x2="{x0+panel_w-12}" y2="{y_vendor:.2f}" '
                'stroke="#2E2E2E" stroke-width="1.2" stroke-dasharray="5 4"/>'
            )
            vendor_label = (
                f'<text class="tiny" x="{x0+36}" y="{y_vendor-3:.2f}" text-anchor="start" fill="#2E2E2E">raw {vendor_gib:.2f} GiB</text>'
            )
        for m_idx, method in enumerate(methods):
            x = x0 + 38 + (m_idx + 0.5) * inner_w / len(methods)
            row_value = values.get((sample_id, method))
            if row_value is None:
                body.append(f'<text class="tiny" x="{x:.2f}" y="{y0+160}" text-anchor="middle" fill="#777777" transform="rotate(-90 {x:.2f} {y0+160})">N/A</text>')
            else:
                gib = float(row_value["compressed_gib"])
                ratio = float(row_value["compression_ratio"])
                rate = float(row_value["compression_rate_pct"]) if is_finite(row_value.get("compression_rate_pct")) else math.nan
                h = gib / ymax * 150 if ymax > 0 else 0
                body.append(f'<rect x="{x-bar_w/2:.2f}" y="{y0+170-h:.2f}" width="{bar_w:.2f}" height="{h:.2f}" fill="{METHOD_COLORS[method]}" fill-opacity="0.86"/>')
                bar_top = y0 + 170 - h
                label_y = max(y0 + 24, bar_top - 22)
                body.append(
                    f'<text class="tiny" x="{x:.2f}" y="{label_y:.2f}" text-anchor="middle">'
                    f'<tspan x="{x:.2f}" dy="0">{gib:.2f}G</tspan>'
                    f'<tspan x="{x:.2f}" dy="9">{ratio:.1f}x</tspan>'
                    f'<tspan x="{x:.2f}" dy="9">{rate:.1f}%</tspan>'
                    '</text>'
                )
            body.append(f'<text class="tiny" x="{x:.2f}" y="{y0+188}" text-anchor="end" transform="rotate(-55 {x:.2f} {y0+188})">{esc(SHORT_DISPLAY[method])}</text>')
        if vendor_label:
            body.append(vendor_label)
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def write_html(output_dir: Path, summary: dict[str, object], plot_paths: list[Path]) -> None:
    cards = []
    section_paths = sorted((output_dir / "plots").glob("trackcodec_section_advantage_*.svg"))
    display_paths = list(plot_paths) + section_paths
    for path in plot_paths:
        rel = path.relative_to(output_dir).as_posix()
        title = path.stem.replace("_", " ").title()
        cards.append(
            f"""
      <section class="figure">
        <h2>{esc(title)}</h2>
        <a href="{esc(rel)}"><img src="{esc(rel)}" alt="{esc(title)}"></a>
      </section>
"""
        )
    if section_paths:
        cards.append(
            """
      <section class="note">
        <h2>TrackCodec Section Advantage</h2>
        <p>Section-level MS1/MS2 figures are generated from the 8-file Stack-ZDPD validation section benchmark table. MS2 aggregates exclude File13_SA1 because it has no MS2 spectra in the inventory.</p>
      </section>
"""
        )
    for path in section_paths:
        rel = path.relative_to(output_dir).as_posix()
        title = path.stem.replace("_", " ").title()
        png_rel = path.with_suffix(".png").relative_to(output_dir).as_posix()
        cards.append(
            f"""
      <section class="figure">
        <h2>{esc(title)}</h2>
        <p class="links"><a href="{esc(png_rel)}">PNG</a> · <a href="{esc(rel)}">SVG</a></p>
        <a href="{esc(rel)}"><img src="{esc(rel)}" alt="{esc(title)}"></a>
      </section>
"""
        )
    doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Combined Compression Benchmark Figures</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f7f9fc; color: #1f2933; }}
    main {{ max-width: 1320px; margin: 0 auto; padding: 26px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta {{ color: #52606d; margin-bottom: 20px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 18px 0 24px; }}
    .tile {{ background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 12px; }}
    .tile b {{ display: block; font-size: 22px; }}
    .figure {{ background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 14px; margin: 16px 0; overflow-x: auto; }}
    .figure h2 {{ margin: 0 0 12px; font-size: 18px; }}
    .note {{ background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 14px; margin: 20px 0 12px; }}
    .note h2 {{ margin: 0 0 8px; font-size: 18px; }}
    .note p {{ margin: 0; color: #52606d; line-height: 1.45; }}
    .links {{ margin: -6px 0 10px; color: #52606d; font-size: 13px; }}
    .links a {{ color: #1f5fbf; text-decoration: none; }}
    img {{ width: 100%; min-width: 960px; height: auto; display: block; }}
    code {{ background: #eef2f7; padding: 1px 4px; border-radius: 3px; }}
  </style>
</head>
<body>
<main>
  <h1>Combined Compression Benchmark Figures</h1>
  <p class="meta">Generated from full8 and Data_StackZDPD benchmark summaries. Only files with all expected method rows are counted as completed files. Vendor raw dashed lines use matching local instrument raw files or paper inventory sizes, and are shown only when the vendor raw reference is not larger than the mzML input. zlib/gzip use ProteoWizard default compression level.</p>
  <div class="summary">
    <div class="tile"><span>Completed files</span><b>{summary["n_files"]}</b></div>
    <div class="tile"><span>Rows plotted</span><b>{summary["n_rows"]}</b></div>
    <div class="tile"><span>OK rows</span><b>{summary["n_ok_rows"]}</b></div>
    <div class="tile"><span>Error rows</span><b>{summary["n_error_rows"]}</b></div>
    <div class="tile"><span>Vendor raw refs</span><b>{summary["n_completed_files_with_vendor_raw"]}</b></div>
    <div class="tile"><span>Figure panels</span><b>{len(display_paths)}</b></div>
  </div>
  {''.join(cards)}
</main>
</body>
</html>
"""
    (output_dir / "combined_benchmark_figures.html").write_text(doc, encoding="utf-8")


def build(
    inputs: list[tuple[str, Path]],
    output_dir: Path,
    expected_methods: int,
    vendor_raw_dirs: list[Path],
    vendor_size_tables: list[Path],
    exclude_files: set[str] | None = None,
    only_plot: str = "",
) -> dict[str, object]:
    table_dir = output_dir / "tables"
    plot_dir = output_dir / "plots"
    plot_data_dir = output_dir / "plot_data"
    for folder in (table_dir, plot_dir, plot_data_dir):
        folder.mkdir(parents=True, exist_ok=True)
    vendor_size_table_values = load_vendor_size_markdown(vendor_size_tables)
    vendor_raw_sizes = vendor_size_table_values | load_vendor_raw_dirs(vendor_raw_dirs)
    rows, counts = load_all(inputs, expected_methods, vendor_raw_sizes, exclude_files)
    agg = aggregate(rows)
    write_csv(table_dir / "combined_per_file_methods.csv", rows)
    write_csv(table_dir / "input_file_completion_counts.csv", counts)
    write_csv(table_dir / "combined_aggregate_by_method.csv", agg)

    complete_files = sorted({str(r["sample_id"]) for r in rows})
    all_methods = median_ordered_methods(rows)
    distribution_methods = [method for method in all_methods if method not in COMPRESSION_DISTRIBUTION_EXCLUDE]
    plot_paths = [
        plot_dir / "combined_compression_ratio_distribution.svg",
        plot_dir / "combined_mean_compression_ratio.svg",
        plot_dir / "combined_total_compressed_size.svg",
        plot_dir / "combined_ratio_vs_elapsed_time.svg",
        plot_dir / "combined_method_file_ratio_heatmap.svg",
        plot_dir / "combined_method_file_status_matrix.svg",
        plot_dir / "combined_multipanel_compressed_size.svg",
    ]
    plot_jobs = {
        "combined_compression_ratio_distribution": lambda: plot_distribution(rows, distribution_methods, plot_paths[0]),
        "combined_mean_compression_ratio": lambda: plot_bar(
            agg, all_methods, plot_paths[1], "mean_compression_ratio", "Mean compression ratio (x)", "Mean Compression Ratio by Method"
        ),
        "combined_total_compressed_size": lambda: plot_bar(
            agg, all_methods, plot_paths[2], "compressed_gib", "Total compressed size (GiB)", "Total Compressed Size by Method", label_key="compressed_gib"
        ),
        "combined_ratio_vs_elapsed_time": lambda: plot_ratio_time(agg, plot_paths[3]),
        "combined_method_file_ratio_heatmap": lambda: plot_heatmap(rows, all_methods, plot_paths[4]),
        "combined_method_file_status_matrix": lambda: plot_status_matrix(rows, all_methods, plot_paths[5]),
        "combined_multipanel_compressed_size": lambda: plot_multipanel(rows, all_methods, plot_paths[6]),
    }
    if only_plot:
        if only_plot not in plot_jobs:
            raise ValueError(f"Unknown --only-plot value: {only_plot}. Available: {', '.join(plot_jobs)}")
        plot_jobs[only_plot]()
    else:
        for job in plot_jobs.values():
            job()
    section_plot_paths = sorted(plot_dir.glob("trackcodec_section_advantage_*.svg"))

    summary = {
        "output_dir": str(output_dir),
        "inputs": [{"dataset_key": key, "summary_csv": str(path)} for key, path in inputs],
        "vendor_raw_dirs": [str(path) for path in vendor_raw_dirs],
        "vendor_size_tables": [str(path) for path in vendor_size_tables],
        "exclude_files": sorted(exclude_files or []),
        "n_vendor_size_table_refs_indexed": len(vendor_size_table_values),
        "n_vendor_raw_files_indexed": len(vendor_raw_sizes),
        "n_completed_files_with_vendor_raw": len({str(r["sample_id"]) for r in rows if is_finite(r.get("vendor_raw_gib"))}),
        "n_files": len(complete_files),
        "n_rows": len(rows),
        "n_ok_rows": sum(1 for r in rows if r["status"] == "ok"),
        "n_error_rows": sum(1 for r in rows if r["status"] == "error"),
        "methods": all_methods,
        "only_plot": only_plot,
        "tables": {
            "per_file_methods": str(table_dir / "combined_per_file_methods.csv"),
            "aggregate_by_method": str(table_dir / "combined_aggregate_by_method.csv"),
            "file_completion_counts": str(table_dir / "input_file_completion_counts.csv"),
        },
        "plots": {path.stem: str(path) for path in plot_paths},
        "section_advantage_plots": {
            path.stem: {
                "svg": str(path),
                "png": str(path.with_suffix(".png")),
            }
            for path in section_plot_paths
        },
        "html": str(output_dir / "combined_benchmark_figures.html"),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_html(output_dir, summary, plot_paths)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build pure-SVG combined benchmark plots from summary.csv files.")
    parser.add_argument("--input", action="append", required=True, help="dataset_key=summary.csv; repeatable")
    parser.add_argument("--output-dir", default=default_combined_output_dir(Path(__file__)), type=Path)
    parser.add_argument("--expected-methods", type=int, default=9)
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        help="Input file name to exclude from combined plots; repeatable.",
    )
    parser.add_argument(
        "--vendor-raw-dir",
        action="append",
        type=Path,
        default=[ROOT / "benchmark" / "data_full8_raw"],
        help="Directory to scan for vendor instrument raw files; repeatable.",
    )
    parser.add_argument(
        "--vendor-size-table",
        action="append",
        type=Path,
        default=[inputs_root(Path(__file__)) / "refs" / "dataset_header_inventory_report.md"],
        help="Markdown inventory table with original vendor file sizes; repeatable.",
    )
    parser.add_argument(
        "--only-plot",
        default="",
        choices=[
            "",
            "combined_compression_ratio_distribution",
            "combined_mean_compression_ratio",
            "combined_total_compressed_size",
            "combined_ratio_vs_elapsed_time",
            "combined_method_file_ratio_heatmap",
            "combined_method_file_status_matrix",
            "combined_multipanel_compressed_size",
        ],
        help="Regenerate only one plot. By default all whole-file plots are regenerated.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build(
        parse_inputs(args.input),
        args.output_dir.resolve(),
        args.expected_methods,
        [path.resolve() for path in args.vendor_raw_dir],
        [path.resolve() for path in args.vendor_size_table],
        set(args.exclude_file),
        args.only_plot,
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
