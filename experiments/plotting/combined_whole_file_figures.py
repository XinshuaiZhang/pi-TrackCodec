from __future__ import annotations

import argparse
import html
import json
import math
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


GIB = 1024**3

METHOD_MAP = {
    "raw_file": "original_mzml",
    "zlib_file": "zlib_mzml",
    "gzip_file": "gzip_mzml",
    "zstd-9_file": "zstd_mzml",
    "zdpd_container": "zdpd_container",
    "stack_zdpd_container": "stack_zdpd_container",
    "ours_strict_q6_whole_archive": "trackcodec_strict_q6",
    "ours_whole_archive": "trackcodec_equal_fidelity",
}

COMBINED_METHOD_MAP = {
    "original_mzml": "original_mzml",
    "msconvert_zlib": "zlib_mzml",
    "zlib_mzml": "zlib_mzml",
    "msconvert_gzip": "gzip_mzml",
    "gzip_mzml": "gzip_mzml",
    "msconvert_zstd": "zstd_mzml",
    "zstd_mzml": "zstd_mzml",
    "masscomp": "masscomp",
    "msconvert_numpress": "numpress_zlib",
    "numpress_zlib": "numpress_zlib",
    "zdpd": "zdpd_container",
    "zdpd_container": "zdpd_container",
    "stackzdpd": "stack_zdpd_container",
    "stack_zdpd_container": "stack_zdpd_container",
    "airdpro": "airdpro",
    "mspack": "mspack",
    "trackcodec": "trackcodec_equal_fidelity",
    "trackcodec_equal_fidelity": "trackcodec_equal_fidelity",
    "trackcodec_strict_q6": "trackcodec_strict_q6",
}

METHOD_ORDER = [
    "original_mzml",
    "zlib_mzml",
    "gzip_mzml",
    "zstd_mzml",
    "numpress_zlib",
    "masscomp",
    "zdpd_container",
    "stack_zdpd_container",
    "airdpro",
    "mspack",
    "trackcodec_strict_q6",
    "trackcodec_equal_fidelity",
]

COMMON_METHOD_ORDER = [
    "original_mzml",
    "zlib_mzml",
    "gzip_mzml",
    "zstd_mzml",
    "numpress_zlib",
    "masscomp",
    "zdpd_container",
    "stack_zdpd_container",
    "airdpro",
    "mspack",
    "trackcodec_equal_fidelity",
]

DISPLAY = {
    "original_mzml": "Original mzML",
    "zlib_mzml": "zlib mzML",
    "gzip_mzml": "gzip mzML",
    "zstd_mzml": "zstd mzML",
    "numpress_zlib": "Numpress+zlib",
    "masscomp": "MassComp",
    "zdpd_container": "ZDPD",
    "stack_zdpd_container": "Stack-ZDPD",
    "airdpro": "AirdPro",
    "mspack": "MSPack",
    "trackcodec_strict_q6": "TrackCodec strict-q6",
    "trackcodec_equal_fidelity": "TrackCodec equal-fidelity",
}

SHORT_DISPLAY = {
    "original_mzml": "Original\nmzML",
    "zlib_mzml": "zlib\nmzML",
    "gzip_mzml": "gzip\nmzML",
    "zstd_mzml": "zstd\nmzML",
    "numpress_zlib": "Numpress\n+zlib",
    "masscomp": "MassComp",
    "zdpd_container": "ZDPD",
    "stack_zdpd_container": "Stack-ZDPD",
    "airdpro": "AirdPro",
    "mspack": "MSPack",
    "trackcodec_equal_fidelity": "TrackCodec\nequal-fidelity",
}

MULTIPANEL_TICKS = {
    "original_mzml": "mzML",
    "zlib_mzml": "zlib",
    "gzip_mzml": "gzip",
    "zstd_mzml": "zstd",
    "numpress_zlib": "Numpress",
    "masscomp": "MassComp",
    "zdpd_container": "ZDPD",
    "stack_zdpd_container": "Stack\nZDPD",
    "airdpro": "AirdPro",
    "mspack": "MSPack",
    "trackcodec_strict_q6": "TC\nstrict",
    "trackcodec_equal_fidelity": "TC\nequal",
}

COLORS = {
    "original_mzml": "#BDBDBD",
    "zlib_mzml": "#8FBBD9",
    "gzip_mzml": "#D8E8F2",
    "zstd_mzml": "#3F7FB6",
    "numpress_zlib": "#7AA6DC",
    "masscomp": "#C6A15B",
    "zdpd_container": "#67A991",
    "stack_zdpd_container": "#8A85B9",
    "airdpro": "#E2A34A",
    "mspack": "#8C6D31",
    "trackcodec_strict_q6": "#4C78A8",
    "trackcodec_equal_fidelity": "#D95F02",
}

FILE_LINE_COLORS = [
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#B279A2",
    "#9D755D",
    "#72B7B2",
    "#E45756",
    "#EECA3B",
    "#6B6ECF",
    "#8CD17D",
    "#B6992D",
    "#499894",
    "#D37295",
    "#A0CBE8",
    "#FFBE7D",
    "#59A14F",
    "#AF7AA1",
    "#BAB0AC",
    "#86BCB6",
]


def _apply_figure_style(figure_style: str) -> None:
    if figure_style != "nmi":
        return
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans", "Liberation Sans"],
            "font.size": 8.0,
            "axes.titlesize": 9.0,
            "axes.labelsize": 8.2,
            "axes.linewidth": 0.75,
            "xtick.labelsize": 7.2,
            "ytick.labelsize": 7.2,
            "xtick.major.width": 0.65,
            "ytick.major.width": 0.65,
            "legend.fontsize": 7.2,
            "legend.frameon": False,
            "figure.dpi": 180,
            "savefig.dpi": 300,
            "svg.fonttype": "none",
        }
    )


def _format_n(value: int) -> str:
    return f"n={int(value)}"


def _style_nmi_axis(ax: plt.Axes, *, grid_axis: str | None = "y") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(0.75)
    ax.spines["bottom"].set_linewidth(0.75)
    if grid_axis:
        ax.grid(axis=grid_axis, color="#D7DCE2", linewidth=0.55, alpha=0.75)
        ax.set_axisbelow(True)


def _ratio(raw: float, compressed: float) -> float:
    return raw / compressed if raw > 0 and compressed > 0 else math.nan


def _rate_pct(raw: float, compressed: float) -> float:
    return compressed / raw * 100.0 if raw > 0 and compressed >= 0 else math.nan


def _reduction_pct(raw: float, compressed: float) -> float:
    return (1.0 - compressed / raw) * 100.0 if raw > 0 and compressed >= 0 else math.nan


def _safe_float(value: object) -> float:
    try:
        if value is None or pd.isna(value):
            return math.nan
        return float(value)
    except Exception:
        return math.nan


def _soften_color(hex_color: str, mix: float = 0.22) -> str:
    rgb = np.asarray(mcolors.to_rgb(hex_color))
    softened = rgb * (1.0 - mix) + np.ones(3) * mix
    return mcolors.to_hex(softened)


def _dataset_display(dataset_key: str) -> str:
    mapping = {
        "full8": "Full8",
        "data_stackzdpd": "data_StackZDPD",
        "stackzdpd_validation": "data_StackZDPD",
        "proposal18": "Proposal18",
    }
    return mapping.get(dataset_key, dataset_key)


def _short_stack_label(file_name: str) -> str:
    base = file_name.replace(".uncompressed.mzML", "").replace(".mzML", "")
    if base == "Set 1_F2":
        return "SZ-Set_1_F2"
    if base.startswith("File"):
        head = base.split("_", 1)[0]
        if head[4:].isdigit():
            return f"SZ-{head}"
    return f"SZ-{base[:18]}"


def _short_full8_label(file_name: str, rank: int) -> str:
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
    if "ETD-1h" in file_name:
        return "F8-ETD"
    if "true_uncompressed" in file_name:
        return "F8-DDA-true"
    if "DDA-1h" in file_name:
        return "F8-DDA"
    return f"F8-{rank:02d}"


def _short_label(dataset_key: str, file_name: str, rank: int) -> str:
    if dataset_key == "full8":
        return _short_full8_label(file_name, rank)
    if dataset_key == "stackzdpd_validation":
        return _short_stack_label(file_name)
    base = file_name.replace(".uncompressed.mzML", "").replace(".mzML", "")
    prefix = "P18" if dataset_key == "proposal18" else dataset_key[:3].upper()
    return f"{prefix}-{rank:02d}-{base[:16]}"


def _find_per_file_whole_csvs(result_root: Path) -> list[tuple[str, Path]]:
    result_root = Path(result_root)
    hits: list[tuple[str, Path]] = []
    for csv_path in sorted(result_root.glob("*/per_file/*/whole/compression_results/whole_archive_per_file_methods.csv")):
        rel = csv_path.relative_to(result_root)
        hits.append((rel.parts[0], csv_path))
    for csv_path in sorted(result_root.glob("per_file/*/whole/compression_results/whole_archive_per_file_methods.csv")):
        hits.append((result_root.name, csv_path))
    return hits


def _load_vendor_reference(path: Path | None) -> dict[str, int]:
    if path is None or not Path(path).exists():
        return {}
    df = pd.read_csv(path)
    if "file" not in df.columns:
        raise ValueError(f"vendor reference is missing 'file' column: {path}")
    value_col = None
    for candidate in ("vendor_raw_bytes", "raw_file_bytes", "raw_bytes", "bytes"):
        if candidate in df.columns:
            value_col = candidate
            break
    if value_col is None:
        raise ValueError(f"vendor reference has no byte column: {path}")
    out: dict[str, int] = {}
    for _, row in df.iterrows():
        try:
            value = int(row[value_col])
        except Exception:
            continue
        if value > 0:
            out[str(row["file"])] = value
    return out


def _load_vendor_raw_dir(path: Path | None) -> dict[str, int]:
    if path is None or not Path(path).exists():
        return {}
    out: dict[str, int] = {}
    for raw_path in Path(path).rglob("*.raw"):
        out[f"{raw_path.stem}.mzML"] = int(raw_path.stat().st_size)
        out[f"{raw_path.stem}.uncompressed.mzML"] = int(raw_path.stat().st_size)
    return out


def _vendor_bytes_for_file(file_name: str, references: dict[str, int]) -> int:
    if file_name in references:
        return int(references[file_name])
    stripped = file_name.replace(".uncompressed.mzML", ".mzML")
    if stripped in references:
        return int(references[stripped])
    return 0


def _load_rows(
    result_root: Path,
    *,
    vendor_raw_reference: Path | None = None,
    vendor_raw_dir: Path | None = None,
) -> tuple[pd.DataFrame, dict[str, object]]:
    csvs = _find_per_file_whole_csvs(result_root)
    vendor = _load_vendor_reference(vendor_raw_reference)
    vendor.update(_load_vendor_raw_dir(vendor_raw_dir))
    rows: list[dict[str, object]] = []
    dataset_ranks: dict[str, int] = {}
    skipped_methods: set[str] = set()

    for dataset_key, csv_path in csvs:
        src = pd.read_csv(csv_path)
        if src.empty or "file" not in src.columns:
            continue
        dataset_ranks[dataset_key] = dataset_ranks.get(dataset_key, 0) + 1
        rank = dataset_ranks[dataset_key]
        file_name = str(src["file"].iloc[0])
        label = _short_label(dataset_key, file_name, rank)
        vendor_raw_bytes = _vendor_bytes_for_file(file_name, vendor)
        for _, row in src.iterrows():
            method = METHOD_MAP.get(str(row["method"]))
            if method is None:
                skipped_methods.add(str(row["method"]))
                continue
            raw = int(row["raw_bytes"])
            compressed = int(row["compressed_bytes"])
            rows.append(
                {
                    "dataset_key": dataset_key,
                    "dataset_source": _dataset_display(dataset_key),
                    "file": file_name,
                    "file_alias": str(row.get("file_alias") or file_name.replace(".uncompressed.mzML", "").replace(".mzML", "")),
                    "combined_file_label": label,
                    "method": method,
                    "display_name": DISPLAY[method],
                    "raw_bytes": raw,
                    "compressed_bytes": compressed,
                    "raw_gib": raw / GIB,
                    "compressed_gib": compressed / GIB,
                    "compression_ratio": _ratio(raw, compressed),
                    "compression_rate_pct": _rate_pct(raw, compressed),
                    "compression_reduction_pct": _reduction_pct(raw, compressed),
                    "encode_time_s": _safe_float(row.get("encode_time_s")),
                    "decode_time_s": _safe_float(row.get("decode_time_s")),
                    "max_abs_mz_error": _safe_float(row.get("max_abs_mz_error")),
                    "max_abs_intensity_error": _safe_float(row.get("max_abs_intensity_error")),
                    "mz_within_ceiling": row.get("mz_within_ceiling"),
                    "intensity_within_ceiling": row.get("intensity_within_ceiling"),
                    "compare_completed": row.get("compare_completed"),
                    "vendor_raw_bytes": int(vendor_raw_bytes),
                    "source_csv": str(csv_path),
                }
            )

    if not rows:
        return pd.DataFrame(), {
            "n_input_csvs": len(csvs),
            "skipped_methods": sorted(skipped_methods),
            "vendor_reference_files": len(vendor),
        }

    df = pd.DataFrame(rows)
    df["method"] = pd.Categorical(df["method"], METHOD_ORDER, ordered=True)
    df = df.sort_values(["dataset_key", "combined_file_label", "method"]).reset_index(drop=True)
    summary = {
        "n_input_csvs": len(csvs),
        "skipped_methods": sorted(skipped_methods),
        "vendor_reference_files": len(vendor),
        "n_vendor_raw_lines": int((df.drop_duplicates("file")["vendor_raw_bytes"] > 0).sum()),
    }
    return df, summary


def _canonical_combined_method(method: object) -> str | None:
    key = str(method).strip()
    return COMBINED_METHOD_MAP.get(key)


def _load_combined_per_file_rows(combined_csv: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    combined_csv = Path(combined_csv)
    src = pd.read_csv(combined_csv)
    required = {"file", "method", "raw_bytes", "compressed_bytes"}
    missing = sorted(required - set(src.columns))
    if missing:
        raise ValueError(f"combined per-file CSV is missing columns {missing}: {combined_csv}")

    rows: list[dict[str, object]] = []
    skipped_methods: set[str] = set()
    status_counts = src["status"].fillna("ok").astype(str).str.lower().value_counts().to_dict() if "status" in src.columns else {"ok": len(src)}
    skipped_non_ok = 0
    skipped_empty_size = 0

    for row_idx, row in src.iterrows():
        method = _canonical_combined_method(row.get("method"))
        if method is None:
            skipped_methods.add(str(row.get("method")))
            continue
        status = str(row.get("status", "ok")).strip().lower()
        if status not in {"ok", "completed", "success", "true"}:
            skipped_non_ok += 1
            continue
        raw = _safe_float(row.get("raw_bytes"))
        compressed = _safe_float(row.get("compressed_bytes"))
        if not math.isfinite(raw) or not math.isfinite(compressed) or raw <= 0 or compressed <= 0:
            skipped_empty_size += 1
            continue

        dataset_key = str(row.get("dataset_key") or "combined")
        file_name = str(row.get("file"))
        combined_label = str(row.get("combined_file_label") or _short_label(dataset_key, file_name, int(row_idx) + 1))
        vendor_raw_bytes = _safe_float(row.get("vendor_raw_bytes"))
        elapsed_seconds = _safe_float(row.get("elapsed_seconds"))
        compression_ratio = _safe_float(row.get("compression_ratio"))
        compression_rate_pct = _safe_float(row.get("compression_rate_pct"))
        if not math.isfinite(compression_ratio):
            compression_ratio = _ratio(raw, compressed)
        if not math.isfinite(compression_rate_pct):
            compression_rate_pct = _rate_pct(raw, compressed)

        rows.append(
            {
                "dataset_key": dataset_key,
                "dataset_source": str(row.get("dataset") or _dataset_display(dataset_key)),
                "file": file_name,
                "sample_id": str(row.get("sample_id") or f"{dataset_key}::{file_name}"),
                "input_path": str(row.get("input_path") or ""),
                "file_alias": file_name.replace(".uncompressed.mzML", "").replace(".mzML", ""),
                "combined_file_label": combined_label,
                "method": method,
                "display_name": DISPLAY[method],
                "status": status,
                "raw_bytes": int(raw),
                "compressed_bytes": int(compressed),
                "raw_gib": raw / GIB,
                "compressed_gib": compressed / GIB,
                "compression_ratio": compression_ratio,
                "compression_rate_pct": compression_rate_pct,
                "compression_reduction_pct": _reduction_pct(raw, compressed),
                "encode_time_s": elapsed_seconds,
                "decode_time_s": math.nan,
                "max_abs_mz_error": math.nan,
                "max_abs_intensity_error": math.nan,
                "mz_within_ceiling": math.nan,
                "intensity_within_ceiling": math.nan,
                "compare_completed": math.nan,
                "vendor_raw_bytes": int(vendor_raw_bytes) if math.isfinite(vendor_raw_bytes) and vendor_raw_bytes > 0 else 0,
                "source_csv": str(row.get("source_csv") or combined_csv),
            }
        )

    if not rows:
        return pd.DataFrame(), {
            "source_csv": str(combined_csv),
            "n_source_rows": int(len(src)),
            "status_counts": {str(k): int(v) for k, v in status_counts.items()},
            "skipped_methods": sorted(skipped_methods),
            "skipped_non_ok_rows": int(skipped_non_ok),
            "skipped_empty_size_rows": int(skipped_empty_size),
        }

    df = pd.DataFrame(rows)
    df["method"] = pd.Categorical(df["method"], METHOD_ORDER, ordered=True)
    df = df.sort_values(["dataset_key", "combined_file_label", "method"]).reset_index(drop=True)
    summary = {
        "source_csv": str(combined_csv),
        "n_source_rows": int(len(src)),
        "n_loaded_ok_rows": int(len(df)),
        "status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "skipped_methods": sorted(skipped_methods),
        "skipped_non_ok_rows": int(skipped_non_ok),
        "skipped_empty_size_rows": int(skipped_empty_size),
        "n_vendor_raw_lines": int((df.drop_duplicates("file")["vendor_raw_bytes"] > 0).sum()),
    }
    return df, summary


def _aggregate(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for method in METHOD_ORDER:
        sub = df[df["method"] == method].copy()
        if sub.empty:
            continue
        raw_sum = int(sub["raw_bytes"].sum())
        compressed_sum = int(sub["compressed_bytes"].sum())
        rows.append(
            {
                "method": method,
                "display_name": DISPLAY[method],
                "n_files": int(sub["file"].nunique()),
                "total_raw_bytes": raw_sum,
                "compressed_bytes": compressed_sum,
                "raw_gib": raw_sum / GIB,
                "compressed_gib": compressed_sum / GIB,
                "compression_ratio": _ratio(raw_sum, compressed_sum),
                "mean_compression_ratio": float(sub["compression_ratio"].mean()),
                "median_compression_ratio": float(sub["compression_ratio"].median()),
                "compression_rate_pct": _rate_pct(raw_sum, compressed_sum),
                "compression_reduction_pct": _reduction_pct(raw_sum, compressed_sum),
                "mean_encode_time_s": float(sub["encode_time_s"].dropna().mean()) if sub["encode_time_s"].notna().any() else math.nan,
                "mean_decode_time_s": float(sub["decode_time_s"].dropna().mean()) if sub["decode_time_s"].notna().any() else math.nan,
            }
        )
    out = pd.DataFrame(rows)
    out["method"] = pd.Categorical(out["method"], METHOD_ORDER, ordered=True)
    return out.sort_values("method").reset_index(drop=True)


def _common_methods(df: pd.DataFrame) -> list[str]:
    total_files = int(df["file"].nunique())
    counts = df.groupby("method", observed=False)["file"].nunique().to_dict()
    return [method for method in COMMON_METHOD_ORDER if int(counts.get(method, 0)) == total_files]


def _present_methods(df: pd.DataFrame) -> list[str]:
    present = set(df["method"].astype(str))
    return [method for method in METHOD_ORDER if method in present]


def _validation_summary(df: pd.DataFrame) -> pd.DataFrame:
    def _all_true_or_missing(series: pd.Series) -> bool:
        values = series.dropna().tolist()
        if not values:
            return True
        normalized = []
        for value in values:
            if isinstance(value, str):
                normalized.append(value.strip().lower() not in {"false", "0", "no", "nan", ""})
            else:
                normalized.append(bool(value))
        return bool(all(normalized))

    rows = []
    for method in ("trackcodec_equal_fidelity", "trackcodec_strict_q6"):
        sub = df[df["method"] == method]
        if sub.empty:
            continue
        metric_cols = ["compare_completed", "mz_within_ceiling", "intensity_within_ceiling", "max_abs_mz_error", "max_abs_intensity_error"]
        if all((col not in sub.columns) or (not sub[col].notna().any()) for col in metric_cols):
            continue
        rows.append(
            {
                "method": method,
                "display_name": DISPLAY[method],
                "n_files": int(sub["file"].nunique()),
                "compare_completed_all": _all_true_or_missing(sub["compare_completed"]),
                "mz_within_ceiling_all": _all_true_or_missing(sub["mz_within_ceiling"]),
                "intensity_within_ceiling_all": _all_true_or_missing(sub["intensity_within_ceiling"]),
                "max_abs_mz_error": float(sub["max_abs_mz_error"].dropna().max()) if sub["max_abs_mz_error"].notna().any() else math.nan,
                "max_abs_intensity_error": float(sub["max_abs_intensity_error"].dropna().max()) if sub["max_abs_intensity_error"].notna().any() else math.nan,
            }
        )
    return pd.DataFrame(rows)


def _savefig(fig: plt.Figure, out_base: Path, *, dpi: int = 180, bbox_inches: str | None = None) -> None:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_base.with_suffix(".png"), dpi=dpi, bbox_inches=bbox_inches)
    fig.savefig(out_base.with_suffix(".svg"), bbox_inches=bbox_inches)
    plt.close(fig)


def _plot_total_size(agg: pd.DataFrame, methods: list[str], out_base: Path, title_prefix: str) -> None:
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    x = np.arange(len(sub))
    fig, ax = plt.subplots(figsize=(max(11.5, len(sub) * 1.4), 7.2))
    bars = ax.bar(x, sub["compressed_gib"], width=0.68, color=[COLORS[m] for m in sub["method"]])
    ax.set_title(f"{title_prefix} whole-file compressed size by method", fontsize=18)
    ax.set_ylabel("Compressed size (GiB)", fontsize=13)
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY[m].replace(" ", "\n", 1) for m in sub["method"]], fontsize=10)
    ax.grid(axis="y", alpha=0.26, linestyle=":")
    ymax = float(sub["compressed_gib"].max() or 1.0)
    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + ymax * 0.018,
            f"{row['compressed_gib']:.2f} GiB\n{row['compression_ratio']:.2f}x",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    ax.set_ylim(0, ymax * 1.20)
    fig.tight_layout()
    _savefig(fig, out_base)


def _plot_ratio_rate(agg: pd.DataFrame, methods: list[str], out_base: Path, title_prefix: str) -> None:
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    x = np.arange(len(sub))
    fig, ax1 = plt.subplots(figsize=(max(11.5, len(sub) * 1.4), 7.2))
    ax2 = ax1.twinx()
    bars = ax1.bar(x, sub["compression_ratio"], width=0.58, color=[COLORS[m] for m in sub["method"]])
    ax2.plot(x, sub["compression_rate_pct"], color="#222222", marker="o", linewidth=2.0)
    ax1.set_title(f"{title_prefix} whole-file compression ratio and rate", fontsize=18)
    ax1.set_ylabel("Compression ratio (x)", fontsize=13)
    ax2.set_ylabel("Compression rate (%)", fontsize=13)
    ax1.set_xticks(x)
    ax1.set_xticklabels([DISPLAY[m].replace(" ", "\n", 1) for m in sub["method"]], fontsize=10)
    ax1.grid(axis="y", alpha=0.26, linestyle=":")
    ymax = float(sub["compression_ratio"].max() or 1.0)
    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            row["compression_ratio"] + ymax * 0.018,
            f"{row['compression_ratio']:.2f}x",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    for xi, (_, row) in zip(x, sub.iterrows()):
        ax2.text(xi, row["compression_rate_pct"], f"{row['compression_rate_pct']:.1f}%", ha="center", va="bottom", fontsize=9)
    ax1.set_ylim(0, ymax * 1.20)
    fig.tight_layout()
    _savefig(fig, out_base)


def _plot_distribution(df: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    plot_df = df[df["method"].isin(methods)].copy()
    plot_df["method"] = pd.Categorical(plot_df["method"], methods, ordered=True)
    plot_df = plot_df.sort_values(["combined_file_label", "method"]).reset_index(drop=True)
    plot_df.to_csv(data_dir / "combined_whole_file_common_methods_compression_ratio_distribution_data.csv", index=False)
    wide = plot_df.pivot_table(
        index=["dataset_source", "file", "combined_file_label"],
        columns="method",
        values="compression_ratio",
        aggfunc="first",
        observed=False,
    ).reset_index()
    wide.to_csv(data_dir / "combined_whole_file_common_methods_compression_ratio_distribution_wide.csv", index=False)

    data = [plot_df[plot_df["method"] == method]["compression_ratio"].to_numpy(float) for method in methods]
    x = np.arange(1, len(methods) + 1)
    fig, ax = plt.subplots(figsize=(10.3, 5.35))
    jitter_offsets = np.linspace(-0.075, 0.075, max(len(wide), 1))
    for idx, (_, row) in enumerate(wide.iterrows()):
        y = [row.get(method, np.nan) for method in methods]
        xj = x + jitter_offsets[idx]
        ax.plot(
            xj,
            y,
            color=_soften_color(FILE_LINE_COLORS[idx % len(FILE_LINE_COLORS)]),
            alpha=0.42,
            linewidth=1.05,
            linestyle="--",
            zorder=1,
        )
        ax.scatter(xj, y, s=20, color="#111111", alpha=0.62, edgecolor="#111111", linewidth=0.25, zorder=4)

    boxes = ax.boxplot(
        data,
        positions=x,
        widths=0.45,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": "#FF7F0E", "linewidth": 1.4},
        boxprops={"linewidth": 1.0, "edgecolor": "#222222"},
        whiskerprops={"linewidth": 1.0, "color": "#222222"},
        capprops={"linewidth": 1.0, "color": "#222222"},
        zorder=3,
    )
    for patch, method in zip(boxes["boxes"], methods):
        patch.set_facecolor(COLORS[method])
        patch.set_alpha(0.82)
        patch.set_zorder(3)

    n_files = int(plot_df["file"].nunique())
    ax.set_title(f"{title_prefix} whole-file compression ratio distribution (n={n_files})", fontsize=15)
    ax.set_ylabel("Compression ratio (x)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([SHORT_DISPLAY[m] for m in methods], fontsize=8)
    ax.grid(axis="y", alpha=0.35, linestyle="-")
    ax.set_axisbelow(True)
    ymax = max(float(np.nanmax(plot_df["compression_ratio"])) * 1.08, 2.0)
    ax.set_ylim(0.7, ymax)
    fig.text(
        0.5,
        0.012,
        "Each colored line connects the same file across methods; compression ratio = input mzML bytes / compressed bytes.",
        ha="center",
        fontsize=7.5,
        color="#303846",
    )
    fig.tight_layout(rect=[0, 0.035, 1, 1])
    _savefig(fig, out_base)


def _plot_mean(df: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    rows = []
    for method in methods:
        sub = df[df["method"] == method]
        rows.append(
            {
                "method": method,
                "display_name": DISPLAY[method],
                "n_files": int(sub["file"].nunique()),
                "mean_compression_ratio": float(sub["compression_ratio"].mean()),
                "median_compression_ratio": float(sub["compression_ratio"].median()),
                "total_raw_bytes": int(sub["raw_bytes"].sum()),
                "total_compressed_bytes": int(sub["compressed_bytes"].sum()),
                "aggregate_compression_ratio": float(sub["raw_bytes"].sum() / sub["compressed_bytes"].sum()),
            }
        )
    mean_df = pd.DataFrame(rows)
    mean_df.to_csv(data_dir / "combined_whole_file_mean_compression_ratio.csv", index=False)
    x = np.arange(len(mean_df))
    vals = mean_df["mean_compression_ratio"].to_numpy(float)
    fig, ax = plt.subplots(figsize=(14.5, 7.6))
    bars = ax.bar(x, vals, color=[COLORS[m] for m in mean_df["method"]], width=0.72)
    n_files = int(df["file"].nunique())
    ax.set_title(f"{title_prefix} whole-file mean compression ratio (n={n_files})", fontsize=20)
    ax.set_ylabel("Compression ratio (x)", fontsize=16)
    ax.set_xticks(x)
    ax.set_xticklabels(mean_df["display_name"], rotation=62, ha="right", fontsize=12)
    ax.grid(axis="y", alpha=0.35, linestyle="-")
    ax.set_axisbelow(True)
    ymax = max(float(vals.max()) * 1.22, 2.0)
    stack_values = mean_df.loc[mean_df["method"] == "stack_zdpd_container", "mean_compression_ratio"]
    stack_n_values = mean_df.loc[mean_df["method"] == "stack_zdpd_container", "n_files"]
    stack_mean = float(stack_values.iloc[0]) if not stack_values.empty else math.nan
    stack_n = int(stack_n_values.iloc[0]) if not stack_n_values.empty else 0
    for bar, (_, row) in zip(bars, mean_df.iterrows()):
        row_n = int(row["n_files"])
        text = f"{row['mean_compression_ratio']:.2f}x\n{_format_n(row_n)}"
        if row["method"] == "stack_zdpd_container":
            text += "\npartial" if row_n != n_files else "\nbaseline"
        if row["method"] == "trackcodec_equal_fidelity" and math.isfinite(stack_mean) and stack_n == row_n:
            text += f"\nvs Stack +{row['mean_compression_ratio'] - stack_mean:.2f}x"
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + ymax * 0.018, text, ha="center", va="bottom", fontsize=11)
    ax.set_ylim(0, ymax)
    fig.text(
        0.5,
        0.012,
        f"Bars show arithmetic mean of per-file compression ratios over {n_files} completed files.",
        ha="center",
        fontsize=10,
        color="#303846",
    )
    fig.tight_layout(rect=[0, 0.035, 1, 1])
    _savefig(fig, out_base)


def _plot_multipanel(df: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    plot_df = df[df["method"].isin(methods)].copy()
    plot_df["method"] = pd.Categorical(plot_df["method"], methods, ordered=True)
    plot_df = plot_df.sort_values(["dataset_key", "combined_file_label", "method"]).reset_index(drop=True)
    plot_df.to_csv(data_dir / "combined_whole_file_multipanel_available_formats_with_mzml_trackcodec.csv", index=False)

    files = (
        plot_df[["file", "combined_file_label", "raw_bytes", "vendor_raw_bytes"]]
        .drop_duplicates("file")
        .reset_index(drop=True)
    )
    ncols = 4
    nrows = math.ceil(len(files) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 7.2, nrows * 4.9), squeeze=False)
    for idx, file_row in files.iterrows():
        ax = axes[idx // ncols][idx % ncols]
        file_name = str(file_row["file"])
        sub = plot_df[plot_df["file"] == file_name].copy()
        method_frame = pd.DataFrame({"method": methods})
        merged = method_frame.merge(sub, on="method", how="left")
        x = np.arange(len(methods))
        vals = merged["compressed_gib"].to_numpy(float)
        present = np.isfinite(vals)
        bars = ax.bar(x[present], vals[present], width=0.70, color=[COLORS[m] for m in merged.loc[present, "method"]])
        ax.set_title(str(file_row["combined_file_label"]), fontsize=11.5)
        ax.set_ylabel("GiB", fontsize=9)
        ax.set_xticks(x)
        ax.set_xticklabels([MULTIPANEL_TICKS[m] for m in methods], fontsize=8)
        ax.grid(axis="y", alpha=0.24, linestyle=":")
        ymax = float(np.nanmax(vals)) if np.isfinite(vals).any() else 1.0
        vendor_raw = int(file_row.get("vendor_raw_bytes", 0) or 0)
        if vendor_raw > 0:
            vendor_gib = vendor_raw / GIB
            ax.axhline(vendor_gib, color="#303846", linestyle="--", linewidth=1.25)
            ax.text(
                len(methods) - 0.05,
                vendor_gib + max(ymax, vendor_gib) * 0.018,
                f"raw {vendor_gib:.2f} GiB",
                ha="right",
                va="bottom",
                fontsize=7.5,
                color="#303846",
            )
            ymax = max(ymax, vendor_gib)
        label_y_pad = ymax * 0.018
        for xpos, _ in merged.loc[~present].iterrows():
            ax.text(xpos, ymax * 0.035, "N/A", ha="center", va="bottom", fontsize=7, color="#666666", rotation=90)
        for bar, (_, row) in zip(bars, merged.loc[present].iterrows()):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + label_y_pad,
                f"{row['compressed_gib']:.2f}\n{row['compression_ratio']:.1f}x\n{row['compression_rate_pct']:.1f}%",
                ha="center",
                va="bottom",
                fontsize=6.4,
                linespacing=0.92,
            )
        ax.set_ylim(0, ymax * 1.30 if ymax else 1.0)

    for idx in range(len(files), nrows * ncols):
        axes[idx // ncols][idx % ncols].axis("off")
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[m]) for m in methods]
    labels = [DISPLAY[m] for m in methods]
    fig.legend(handles, labels, loc="upper center", ncol=4, fontsize=10, frameon=False, bbox_to_anchor=(0.5, 0.982))
    fig.suptitle(f"{title_prefix} whole-file compressed size by method (n={len(files)})", fontsize=20, y=0.999)
    fig.text(
        0.5,
        0.012,
        "First bar is the input uncompressed mzML. Dashed reference line is the vendor raw container size only when that source file is available.",
        ha="center",
        va="bottom",
        fontsize=10,
        color="#303846",
    )
    fig.tight_layout(rect=[0, 0.025, 1, 0.94])
    _savefig(fig, out_base, bbox_inches=None)


def _plot_speed_pareto(df: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    rows = []
    for method in methods:
        sub = df[df["method"] == method]
        if sub.empty:
            continue
        rows.append(
            {
                "method": method,
                "display_name": DISPLAY[method],
                "mean_encode_time_s": float(sub["encode_time_s"].dropna().mean()) if sub["encode_time_s"].notna().any() else 0.0,
                "mean_compression_ratio": float(sub["compression_ratio"].mean()),
            }
        )
    speed = pd.DataFrame(rows)
    speed.to_csv(data_dir / "combined_whole_file_compression_ratio_vs_encode_time.csv", index=False)
    fig, ax = plt.subplots(figsize=(10.5, 7.0))
    for _, row in speed.iterrows():
        ax.scatter(
            row["mean_encode_time_s"],
            row["mean_compression_ratio"],
            s=150,
            color=COLORS.get(row["method"], "#777777"),
            edgecolor="white",
            linewidth=0.8,
        )
        ax.text(row["mean_encode_time_s"], row["mean_compression_ratio"], "  " + row["display_name"], va="center", fontsize=9)
    ax.set_title(f"{title_prefix} whole-file compression ratio vs encode time", fontsize=18)
    ax.set_xlabel("Mean encode time (s)", fontsize=13)
    ax.set_ylabel("Mean compression ratio (x)", fontsize=13)
    ax.grid(alpha=0.25, linestyle=":")
    fig.tight_layout()
    _savefig(fig, out_base)


def _plot_method_file_heatmap(df: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    plot_df = df[df["method"].isin(methods)].copy()
    plot_df["method"] = pd.Categorical(plot_df["method"], methods, ordered=True)
    file_order = (
        plot_df[["dataset_key", "combined_file_label", "file"]]
        .drop_duplicates("file")
        .sort_values(["dataset_key", "combined_file_label"])
    )
    labels = file_order["combined_file_label"].astype(str).tolist()
    matrix = (
        plot_df.pivot_table(
            index="method",
            columns="combined_file_label",
            values="compression_ratio",
            aggfunc="first",
            observed=False,
        )
        .reindex(index=methods, columns=labels)
    )
    matrix_out = matrix.copy()
    matrix_out.insert(0, "display_name", [DISPLAY[method] for method in matrix_out.index])
    matrix_out.to_csv(data_dir / "combined_whole_file_method_file_compression_ratio_heatmap_matrix.csv")

    values = matrix.to_numpy(dtype=float)
    n_methods, n_files = values.shape
    fig_w = max(11.0, n_files * 0.68 + 3.0)
    fig_h = max(4.8, n_methods * 0.58 + 1.8)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    masked = np.ma.masked_invalid(values)
    cmap = plt.cm.YlGnBu.copy()
    cmap.set_bad("#F2F2F2")
    finite = values[np.isfinite(values)]
    if finite.size:
        vmin = max(0.0, float(np.nanpercentile(finite, 5)) * 0.92)
        vmax = float(np.nanpercentile(finite, 95)) * 1.08
        if vmax <= vmin:
            vmax = float(np.nanmax(finite)) * 1.08
    else:
        vmin, vmax = 0.0, 1.0
    image = ax.imshow(masked, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
    ax.set_title(f"{title_prefix} whole-file compression ratio heatmap", fontsize=17)
    ax.set_xlabel("File", fontsize=12)
    ax.set_ylabel("Method", fontsize=12)
    ax.set_xticks(np.arange(n_files))
    ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=8.5)
    ax.set_yticks(np.arange(n_methods))
    ax.set_yticklabels([DISPLAY[method] for method in methods], fontsize=10)
    ax.set_xticks(np.arange(-0.5, n_files, 1), minor=True)
    ax.set_yticks(np.arange(-0.5, n_methods, 1), minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=0.8)
    ax.tick_params(which="minor", bottom=False, left=False)

    for i in range(n_methods):
        for j in range(n_files):
            value = values[i, j]
            text = "N/A" if not np.isfinite(value) else f"{value:.2f}x"
            color = "#666666" if not np.isfinite(value) else ("white" if value > (vmin + vmax) / 2 else "#111111")
            ax.text(j, i, text, ha="center", va="center", fontsize=7.4, color=color)

    cbar = fig.colorbar(image, ax=ax, fraction=0.025, pad=0.018)
    cbar.set_label("Compression ratio (x)", fontsize=10)
    fig.tight_layout()
    _savefig(fig, out_base)


def _plot_nmi_fullset_total_size(agg: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    sub.to_csv(data_dir / "nmi_fullset_total_compressed_size_data.csv", index=False)

    fig, ax = plt.subplots(figsize=(7.2, 3.25))
    x = np.arange(len(sub))
    bars = ax.bar(x, sub["compressed_gib"], width=0.64, color=[COLORS[m] for m in sub["method"]], edgecolor="#2B2B2B", linewidth=0.35)
    ymax = max(float(sub["compressed_gib"].max()) * 1.18, 1.0)
    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + ymax * 0.018,
            f"{row['compression_ratio']:.2f}x",
            ha="center",
            va="bottom",
            fontsize=6.8,
        )
    ax.set_ylabel("Compressed size (GiB)")
    ax.set_title(f"{title_prefix}: complete-method whole-file size")
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY[str(m)].replace("TrackCodec ", "TC ") for m in sub["method"]], rotation=34, ha="right")
    ax.set_ylim(0, ymax)
    _style_nmi_axis(ax)
    fig.tight_layout()
    _savefig(fig, out_base, dpi=300)


def _plot_nmi_available_mean_ratio(agg: pd.DataFrame, methods: list[str], total_files: int, out_base: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    sub.to_csv(data_dir / "nmi_available_mean_compression_ratio_data.csv", index=False)

    fig, ax = plt.subplots(figsize=(7.5, 3.35))
    x = np.arange(len(sub))
    colors = [COLORS[str(m)] for m in sub["method"]]
    alpha = [1.0 if int(n) == total_files else 0.55 for n in sub["n_files"]]
    bars = ax.bar(x, sub["mean_compression_ratio"], width=0.64, color=colors, edgecolor="#2B2B2B", linewidth=0.35)
    for bar, a in zip(bars, alpha):
        bar.set_alpha(a)
    ymax = max(float(sub["mean_compression_ratio"].max()) * 1.28, 2.0)
    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + ymax * 0.018,
            f"{row['mean_compression_ratio']:.2f}x\n{_format_n(int(row['n_files']))}",
            ha="center",
            va="bottom",
            fontsize=6.5,
            linespacing=0.92,
        )
    ax.set_ylabel("Mean compression ratio (x)")
    ax.set_title(f"{title_prefix}: available-file compression ratio")
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY[str(m)].replace("TrackCodec ", "TC ") for m in sub["method"]], rotation=34, ha="right")
    ax.set_ylim(0, ymax)
    _style_nmi_axis(ax)
    fig.text(
        0.995,
        0.01,
        f"Faded bars denote methods that did not complete all {total_files} files.",
        ha="right",
        va="bottom",
        fontsize=6.4,
        color="#4B5563",
    )
    fig.tight_layout(rect=[0, 0.035, 1, 1])
    _savefig(fig, out_base, dpi=300)


def _matched_pair_table(df: pd.DataFrame, focal_method: str, baseline_method: str) -> pd.DataFrame:
    base_cols = ["dataset_key", "dataset_source", "file", "combined_file_label", "raw_bytes", "raw_gib"]
    metric_cols = ["compressed_bytes", "compressed_gib", "compression_ratio", "compression_rate_pct", "encode_time_s"]
    focal = df[df["method"] == focal_method][base_cols + metric_cols].copy()
    baseline = df[df["method"] == baseline_method][["file"] + metric_cols].copy()
    focal = focal.rename(columns={col: f"focal_{col}" for col in metric_cols})
    baseline = baseline.rename(columns={col: f"baseline_{col}" for col in metric_cols})
    matched = focal.merge(baseline, on="file", how="inner")
    if matched.empty:
        return matched
    matched["focal_method"] = focal_method
    matched["baseline_method"] = baseline_method
    matched["focal_smaller_pct"] = (1.0 - matched["focal_compressed_bytes"] / matched["baseline_compressed_bytes"]) * 100.0
    matched["ratio_gain_x"] = matched["focal_compression_ratio"] - matched["baseline_compression_ratio"]
    return matched.sort_values(["dataset_key", "raw_bytes"], ascending=[True, False]).reset_index(drop=True)


def _plot_nmi_matched_stackzdpd(df: pd.DataFrame, out_base: Path, data_dir: Path, title_prefix: str) -> dict[str, object]:
    focal = "trackcodec_equal_fidelity"
    baseline = "stack_zdpd_container"
    matched = _matched_pair_table(df, focal, baseline)
    matched.to_csv(data_dir / "nmi_trackcodec_vs_stackzdpd_matched_files.csv", index=False)
    if matched.empty:
        return {"matched_files": 0}

    raw_sum = float(matched["raw_bytes"].sum())
    focal_sum = float(matched["focal_compressed_bytes"].sum())
    baseline_sum = float(matched["baseline_compressed_bytes"].sum())
    smaller_pct = (1.0 - focal_sum / baseline_sum) * 100.0 if baseline_sum > 0 else math.nan
    focal_cr = _ratio(raw_sum, focal_sum)
    baseline_cr = _ratio(raw_sum, baseline_sum)

    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(7.4, 3.2), gridspec_kw={"width_ratios": [0.82, 1.35]})
    totals = np.asarray([baseline_sum / GIB, focal_sum / GIB], dtype=float)
    bars = ax0.bar(
        [0, 1],
        totals,
        width=0.58,
        color=[COLORS[baseline], COLORS[focal]],
        edgecolor="#2B2B2B",
        linewidth=0.35,
    )
    ymax0 = max(float(totals.max()) * 1.24, 1.0)
    for bar, cr in zip(bars, [baseline_cr, focal_cr]):
        ax0.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + ymax0 * 0.025, f"{cr:.2f}x", ha="center", va="bottom", fontsize=6.8)
    ax0.set_xticks([0, 1])
    ax0.set_xticklabels(["Stack-\nZDPD", "Track\nCodec"])
    ax0.set_ylabel("Compressed size (GiB)")
    ax0.set_title("a  Matched total")
    ax0.set_ylim(0, ymax0)
    _style_nmi_axis(ax0)

    labels = matched["combined_file_label"].astype(str).tolist()
    x = np.arange(len(matched))
    vals = matched["focal_smaller_pct"].to_numpy(float)
    colors = np.where(vals >= 0, COLORS[focal], "#B33A3A")
    ax1.bar(x, vals, width=0.68, color=colors, edgecolor="#2B2B2B", linewidth=0.25)
    ax1.axhline(0, color="#2B2B2B", linewidth=0.75)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=48, ha="right")
    ax1.set_ylabel("TrackCodec size reduction vs Stack-ZDPD (%)")
    ax1.set_title("b  Per-file matched reduction")
    _style_nmi_axis(ax1)
    pad = max(float(np.nanmax(np.abs(vals))) * 0.18, 2.0)
    ax1.set_ylim(float(np.nanmin(vals)) - pad, float(np.nanmax(vals)) + pad)
    fig.suptitle(f"{title_prefix}: Stack-ZDPD matched comparison ({_format_n(len(matched))})", y=1.03, fontsize=9.5)
    fig.text(
        0.985,
        0.01,
        f"Aggregate matched reduction: {smaller_pct:.1f}% smaller; CR {focal_cr:.2f}x vs {baseline_cr:.2f}x.",
        ha="right",
        va="bottom",
        fontsize=6.4,
        color="#4B5563",
    )
    fig.tight_layout(rect=[0, 0.04, 1, 0.98])
    _savefig(fig, out_base, dpi=300)
    return {
        "matched_files": int(len(matched)),
        "trackcodec_smaller_pct": float(smaller_pct),
        "trackcodec_matched_cr": float(focal_cr),
        "stackzdpd_matched_cr": float(baseline_cr),
    }


def _plot_nmi_method_presence(df: pd.DataFrame, methods: list[str], out_base: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    file_order = (
        df[["dataset_key", "combined_file_label", "file", "raw_bytes"]]
        .drop_duplicates("file")
        .sort_values(["dataset_key", "raw_bytes"], ascending=[True, False])
    )
    labels = file_order["combined_file_label"].astype(str).tolist()
    presence = (
        df.pivot_table(index="method", columns="combined_file_label", values="compressed_bytes", aggfunc="count", observed=False)
        .reindex(index=methods, columns=labels)
        .fillna(0)
        .astype(int)
    )
    presence.insert(0, "display_name", [DISPLAY[method] for method in presence.index])
    presence.to_csv(data_dir / "nmi_method_file_completion_presence_matrix.csv")

    values = presence.drop(columns=["display_name"]).to_numpy(dtype=float)
    fig_w = max(7.2, len(labels) * 0.26 + 2.6)
    fig_h = max(3.2, len(methods) * 0.24 + 1.2)
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    cmap = mcolors.ListedColormap(["#F2F3F5", "#324E68"])
    ax.imshow(values, aspect="auto", interpolation="nearest", cmap=cmap, vmin=0, vmax=1)
    ax.set_title(f"{title_prefix}: completed method-file cells")
    ax.set_xlabel("File")
    ax.set_ylabel("Method")
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=50, ha="right")
    ax.set_yticks(np.arange(len(methods)))
    ax.set_yticklabels([DISPLAY[method].replace("TrackCodec ", "TC ") for method in methods])
    ax.set_xticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(methods), 1), minor=True)
    ax.grid(which="minor", color="white", linestyle="-", linewidth=0.65)
    ax.tick_params(which="minor", bottom=False, left=False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fig.tight_layout()
    _savefig(fig, out_base, dpi=300)


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
    body = "\n".join(elements)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">\n'
        '<rect width="100%" height="100%" fill="#FFFFFF"/>\n'
        f"{body}\n"
        "</svg>\n"
    )
    path.write_text(svg, encoding="utf-8")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{max(0, min(255, int(v))):02X}" for v in rgb)


def _blend_with_white(hex_color: str, opacity: float) -> str:
    rgb = _hex_to_rgb(hex_color)
    out = tuple(round(v * opacity + 255 * (1.0 - opacity)) for v in rgb)
    return _rgb_to_hex(out)


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
        font_names = ["arialbd.ttf", "Arialbd.ttf"] if weight in {"700", "bold"} else ["arial.ttf", "Arial.ttf"]
        for name in font_names:
            path = font_dir / name
            if path.exists():
                return ImageFont.truetype(str(path), max(1, round(size * self.scale)))
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf" if weight in {"700", "bold"} else "DejaVuSans.ttf", max(1, round(size * self.scale)))
        except Exception:
            return ImageFont.load_default(size=max(1, round(size * self.scale)))

    def line(self, x1: float, y1: float, x2: float, y2: float, *, color: str = "#AEB7C2", width: float = 1.0) -> None:
        s = self.scale
        self.draw.line((x1 * s, y1 * s, x2 * s, y2 * s), fill=color, width=max(1, round(width * s)))

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
    ) -> None:
        s = self.scale
        fill_color = _blend_with_white(fill, opacity) if opacity < 1.0 else fill
        xy = (x * s, y * s, (x + max(w, 0)) * s, (y + max(h, 0)) * s)
        outline = None if stroke == "none" else stroke
        self.draw.rectangle(xy, fill=fill_color, outline=outline, width=max(1, round(0.35 * s)))

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
            if anchor == "middle":
                left = x_s - text_w / 2
            elif anchor == "end":
                left = x_s - text_w
            else:
                left = x_s
            top = y_s - text_h
            self.draw.text((left, top), value, fill=color, font=font)
            return

        pad = max(6, round(size * s * 0.35))
        text_image = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (255, 255, 255, 0))
        text_draw = ImageDraw.Draw(text_image)
        text_draw.text((pad, pad), value, fill=color, font=font)
        rotated = text_image.rotate(rotate, expand=True, resample=Image.Resampling.BICUBIC)
        if anchor == "middle":
            left = x_s - rotated.width / 2
            top = y_s - rotated.height / 2
        elif anchor == "end":
            left = x_s - rotated.width + 2 * s
            top = y_s - rotated.height * 0.35
        else:
            left = x_s
            top = y_s - rotated.height * 0.35
        self.image.paste(rotated, (round(left), round(top)), rotated)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(path, format="PNG", optimize=True)


def _nice_ticks(max_value: float, n: int = 4) -> list[float]:
    if not math.isfinite(max_value) or max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / max(n, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = min((1, 2, 5, 10), key=lambda m: abs(raw - m * power)) * power
    top = math.ceil(max_value / step) * step
    return [i * step for i in range(int(round(top / step)) + 1)]


def _write_nmi_fullset_total_size_svg(agg: pd.DataFrame, methods: list[str], out_svg: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    sub.to_csv(data_dir / "nmi_fullset_total_compressed_size_data.csv", index=False)

    width, height = 920, 430
    left, top, right, bottom = 76, 48, 24, 112
    plot_w, plot_h = width - left - right, height - top - bottom
    max_y = float(sub["compressed_gib"].max()) * 1.16
    ticks = _nice_ticks(max_y, 4)
    max_tick = max(ticks) if ticks else max_y
    elements = [
        _svg_text(left, 24, f"{title_prefix}: complete-method whole-file size", size=14, weight="700"),
        _svg_text(14, top + plot_h / 2, "Compressed size (GiB)", size=12, anchor="middle", rotate=-90),
    ]
    for tick in ticks:
        y = top + plot_h - (tick / max_tick) * plot_h
        elements.append(_svg_line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8))
        elements.append(_svg_text(left - 8, y + 4, f"{tick:.0f}", size=10, anchor="end", color="#4B5563"))
    n = len(sub)
    gap = 13
    bar_w = (plot_w - gap * (n - 1)) / n
    for idx, (_, row) in enumerate(sub.iterrows()):
        method = str(row["method"])
        value = float(row["compressed_gib"])
        x = left + idx * (bar_w + gap)
        bar_h = (value / max_tick) * plot_h
        y = top + plot_h - bar_h
        elements.append(_svg_rect(x, y, bar_w, bar_h, fill=COLORS[method], stroke="#1F2937"))
        elements.append(_svg_text(x + bar_w / 2, y - 8, f"{row['compression_ratio']:.2f}x", size=9, anchor="middle"))
        label = DISPLAY[method].replace("TrackCodec ", "TC ")
        elements.append(_svg_text(x + bar_w / 2, top + plot_h + 18, label, size=9, anchor="end", rotate=-36))
    elements.append(_svg_line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0))
    elements.append(_svg_line(left, top, left, top + plot_h, color="#111827", width=1.0))
    _write_svg(out_svg, width, height, elements)


def _write_nmi_fullset_total_size_png(agg: pd.DataFrame, methods: list[str], out_png: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    sub.to_csv(data_dir / "nmi_fullset_total_compressed_size_data.csv", index=False)

    width, height = 920, 430
    left, top, right, bottom = 76, 48, 24, 112
    plot_w, plot_h = width - left - right, height - top - bottom
    max_y = float(sub["compressed_gib"].max()) * 1.16
    ticks = _nice_ticks(max_y, 4)
    max_tick = max(ticks) if ticks else max_y
    canvas = _PngCanvas(width, height)
    canvas.text(left, 24, f"{title_prefix}: complete-method whole-file size", size=14, weight="700")
    canvas.text(14, top + plot_h / 2, "Compressed size (GiB)", size=12, anchor="middle", rotate=-90)
    for tick in ticks:
        y = top + plot_h - (tick / max_tick) * plot_h
        canvas.line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8)
        canvas.text(left - 8, y + 4, f"{tick:.0f}", size=10, anchor="end", color="#4B5563")
    n = len(sub)
    gap = 13
    bar_w = (plot_w - gap * (n - 1)) / n
    for idx, (_, row) in enumerate(sub.iterrows()):
        method = str(row["method"])
        value = float(row["compressed_gib"])
        x = left + idx * (bar_w + gap)
        bar_h = (value / max_tick) * plot_h
        y = top + plot_h - bar_h
        canvas.rect(x, y, bar_w, bar_h, fill=COLORS[method], stroke="#1F2937")
        canvas.text(x + bar_w / 2, y - 8, f"{row['compression_ratio']:.2f}x", size=9, anchor="middle")
        canvas.text(x + bar_w / 2, top + plot_h + 18, DISPLAY[method].replace("TrackCodec ", "TC "), size=9, anchor="end", rotate=-36)
    canvas.line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0)
    canvas.line(left, top, left, top + plot_h, color="#111827", width=1.0)
    canvas.save(out_png)


def _write_nmi_available_mean_ratio_svg(
    agg: pd.DataFrame,
    methods: list[str],
    total_files: int,
    out_svg: Path,
    data_dir: Path,
    title_prefix: str,
) -> None:
    if not methods:
        return
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    sub.to_csv(data_dir / "nmi_available_mean_compression_ratio_data.csv", index=False)

    width, height = 980, 450
    left, top, right, bottom = 72, 48, 24, 124
    plot_w, plot_h = width - left - right, height - top - bottom
    max_y = float(sub["mean_compression_ratio"].max()) * 1.25
    ticks = _nice_ticks(max_y, 4)
    max_tick = max(ticks) if ticks else max_y
    elements = [
        _svg_text(left, 24, f"{title_prefix}: available-file compression ratio", size=14, weight="700"),
        _svg_text(16, top + plot_h / 2, "Mean compression ratio (x)", size=12, anchor="middle", rotate=-90),
        _svg_text(width - 16, height - 10, f"Faded bars did not complete all {total_files} files.", size=9, anchor="end", color="#4B5563"),
    ]
    for tick in ticks:
        y = top + plot_h - (tick / max_tick) * plot_h
        elements.append(_svg_line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8))
        elements.append(_svg_text(left - 8, y + 4, f"{tick:.0f}", size=10, anchor="end", color="#4B5563"))
    n = len(sub)
    gap = 11
    bar_w = (plot_w - gap * (n - 1)) / n
    for idx, (_, row) in enumerate(sub.iterrows()):
        method = str(row["method"])
        value = float(row["mean_compression_ratio"])
        x = left + idx * (bar_w + gap)
        bar_h = (value / max_tick) * plot_h
        y = top + plot_h - bar_h
        opacity = 1.0 if int(row["n_files"]) == total_files else 0.52
        elements.append(_svg_rect(x, y, bar_w, bar_h, fill=COLORS[method], stroke="#1F2937", opacity=opacity))
        elements.append(_svg_text(x + bar_w / 2, y - 18, f"{value:.2f}x", size=8.5, anchor="middle"))
        elements.append(_svg_text(x + bar_w / 2, y - 6, _format_n(int(row["n_files"])), size=8.0, anchor="middle", color="#4B5563"))
        label = DISPLAY[method].replace("TrackCodec ", "TC ")
        elements.append(_svg_text(x + bar_w / 2, top + plot_h + 18, label, size=8.5, anchor="end", rotate=-36))
    elements.append(_svg_line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0))
    elements.append(_svg_line(left, top, left, top + plot_h, color="#111827", width=1.0))
    _write_svg(out_svg, width, height, elements)


def _write_nmi_available_mean_ratio_png(
    agg: pd.DataFrame,
    methods: list[str],
    total_files: int,
    out_png: Path,
    data_dir: Path,
    title_prefix: str,
) -> None:
    if not methods:
        return
    sub = agg[agg["method"].isin(methods)].copy()
    sub["method"] = pd.Categorical(sub["method"], methods, ordered=True)
    sub = sub.sort_values("method")
    sub.to_csv(data_dir / "nmi_available_mean_compression_ratio_data.csv", index=False)

    width, height = 980, 450
    left, top, right, bottom = 72, 48, 24, 124
    plot_w, plot_h = width - left - right, height - top - bottom
    max_y = float(sub["mean_compression_ratio"].max()) * 1.25
    ticks = _nice_ticks(max_y, 4)
    max_tick = max(ticks) if ticks else max_y
    canvas = _PngCanvas(width, height)
    canvas.text(left, 24, f"{title_prefix}: available-file compression ratio", size=14, weight="700")
    canvas.text(16, top + plot_h / 2, "Mean compression ratio (x)", size=12, anchor="middle", rotate=-90)
    canvas.text(width - 16, height - 10, f"Faded bars did not complete all {total_files} files.", size=9, anchor="end", color="#4B5563")
    for tick in ticks:
        y = top + plot_h - (tick / max_tick) * plot_h
        canvas.line(left, y, left + plot_w, y, color="#E1E5EA", width=0.8)
        canvas.text(left - 8, y + 4, f"{tick:.0f}", size=10, anchor="end", color="#4B5563")
    n = len(sub)
    gap = 11
    bar_w = (plot_w - gap * (n - 1)) / n
    for idx, (_, row) in enumerate(sub.iterrows()):
        method = str(row["method"])
        value = float(row["mean_compression_ratio"])
        x = left + idx * (bar_w + gap)
        bar_h = (value / max_tick) * plot_h
        y = top + plot_h - bar_h
        opacity = 1.0 if int(row["n_files"]) == total_files else 0.52
        canvas.rect(x, y, bar_w, bar_h, fill=COLORS[method], stroke="#1F2937", opacity=opacity)
        canvas.text(x + bar_w / 2, y - 18, f"{value:.2f}x", size=8.5, anchor="middle")
        canvas.text(x + bar_w / 2, y - 6, _format_n(int(row["n_files"])), size=8.0, anchor="middle", color="#4B5563")
        canvas.text(x + bar_w / 2, top + plot_h + 18, DISPLAY[method].replace("TrackCodec ", "TC "), size=8.5, anchor="end", rotate=-36)
    canvas.line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0)
    canvas.line(left, top, left, top + plot_h, color="#111827", width=1.0)
    canvas.save(out_png)


def _write_nmi_matched_stackzdpd_svg(df: pd.DataFrame, out_svg: Path, data_dir: Path, title_prefix: str) -> dict[str, object]:
    focal = "trackcodec_equal_fidelity"
    baseline = "stack_zdpd_container"
    matched = _matched_pair_table(df, focal, baseline)
    matched.to_csv(data_dir / "nmi_trackcodec_vs_stackzdpd_matched_files.csv", index=False)
    if matched.empty:
        return {"matched_files": 0}

    raw_sum = float(matched["raw_bytes"].sum())
    focal_sum = float(matched["focal_compressed_bytes"].sum())
    baseline_sum = float(matched["baseline_compressed_bytes"].sum())
    smaller_pct = (1.0 - focal_sum / baseline_sum) * 100.0
    focal_cr = _ratio(raw_sum, focal_sum)
    baseline_cr = _ratio(raw_sum, baseline_sum)

    width, height = 960, 430
    elements = [
        _svg_text(42, 24, f"{title_prefix}: Stack-ZDPD matched comparison ({_format_n(len(matched))})", size=14, weight="700"),
        _svg_text(width - 24, height - 10, f"Aggregate matched reduction: {smaller_pct:.1f}% smaller; CR {focal_cr:.2f}x vs {baseline_cr:.2f}x.", size=9, anchor="end", color="#4B5563"),
    ]

    left, top, plot_w, plot_h = 70, 70, 210, 240
    totals = [baseline_sum / GIB, focal_sum / GIB]
    max_total = max(totals) * 1.25
    elements.append(_svg_text(left, 50, "a  Matched total", size=12, weight="700"))
    elements.append(_svg_text(18, top + plot_h / 2, "Compressed size (GiB)", size=11, anchor="middle", rotate=-90))
    for tick in _nice_ticks(max_total, 4):
        y = top + plot_h - (tick / max(_nice_ticks(max_total, 4))) * plot_h
        elements.append(_svg_line(left, y, left + plot_w, y, color="#E1E5EA", width=0.75))
        elements.append(_svg_text(left - 8, y + 4, f"{tick:.0f}", size=9, anchor="end", color="#4B5563"))
    bar_w = 60
    for idx, (label, value, color, cr) in enumerate(
        [
            ("Stack-ZDPD", totals[0], COLORS[baseline], baseline_cr),
            ("TrackCodec", totals[1], COLORS[focal], focal_cr),
        ]
    ):
        x = left + 32 + idx * 92
        bar_h = (value / max_total) * plot_h
        y = top + plot_h - bar_h
        elements.append(_svg_rect(x, y, bar_w, bar_h, fill=color, stroke="#1F2937"))
        elements.append(_svg_text(x + bar_w / 2, y - 8, f"{cr:.2f}x", size=9, anchor="middle"))
        elements.append(_svg_text(x + bar_w / 2, top + plot_h + 18, label, size=9, anchor="middle"))
    elements.append(_svg_line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0))
    elements.append(_svg_line(left, top, left, top + plot_h, color="#111827", width=1.0))

    right_left, right_top, right_w, right_h = 372, 70, 540, 240
    vals = matched["focal_smaller_pct"].to_numpy(float)
    ymin = min(0.0, float(np.nanmin(vals)))
    ymax = max(0.0, float(np.nanmax(vals)))
    pad = max((ymax - ymin) * 0.12, 2.0)
    ymin -= pad
    ymax += pad
    elements.append(_svg_text(right_left, 50, "b  Per-file matched reduction", size=12, weight="700"))
    elements.append(_svg_text(326, right_top + right_h / 2, "TrackCodec size reduction vs Stack-ZDPD (%)", size=11, anchor="middle", rotate=-90))
    for tick in np.linspace(math.floor(ymin / 10) * 10, math.ceil(ymax / 10) * 10, 5):
        y = right_top + right_h - ((float(tick) - ymin) / (ymax - ymin)) * right_h
        elements.append(_svg_line(right_left, y, right_left + right_w, y, color="#E1E5EA", width=0.75))
        elements.append(_svg_text(right_left - 8, y + 4, f"{tick:.0f}", size=9, anchor="end", color="#4B5563"))
    zero_y = right_top + right_h - ((0.0 - ymin) / (ymax - ymin)) * right_h
    elements.append(_svg_line(right_left, zero_y, right_left + right_w, zero_y, color="#111827", width=0.85))
    labels = matched["combined_file_label"].astype(str).tolist()
    gap = 8
    bar_w = (right_w - gap * (len(vals) - 1)) / len(vals)
    for idx, (value, label) in enumerate(zip(vals, labels)):
        x = right_left + idx * (bar_w + gap)
        y_val = right_top + right_h - ((value - ymin) / (ymax - ymin)) * right_h
        y = min(y_val, zero_y)
        h = abs(zero_y - y_val)
        fill = COLORS[focal] if value >= 0 else "#B33A3A"
        elements.append(_svg_rect(x, y, bar_w, h, fill=fill, stroke="#1F2937"))
        elements.append(_svg_text(x + bar_w / 2, right_top + right_h + 18, label, size=8, anchor="end", rotate=-42))
    elements.append(_svg_line(right_left, right_top + right_h, right_left + right_w, right_top + right_h, color="#111827", width=1.0))
    elements.append(_svg_line(right_left, right_top, right_left, right_top + right_h, color="#111827", width=1.0))
    _write_svg(out_svg, width, height, elements)
    return {
        "matched_files": int(len(matched)),
        "trackcodec_smaller_pct": float(smaller_pct),
        "trackcodec_matched_cr": float(focal_cr),
        "stackzdpd_matched_cr": float(baseline_cr),
    }


def _write_nmi_matched_stackzdpd_png(df: pd.DataFrame, out_png: Path, data_dir: Path, title_prefix: str) -> dict[str, object]:
    focal = "trackcodec_equal_fidelity"
    baseline = "stack_zdpd_container"
    matched = _matched_pair_table(df, focal, baseline)
    matched.to_csv(data_dir / "nmi_trackcodec_vs_stackzdpd_matched_files.csv", index=False)
    if matched.empty:
        return {"matched_files": 0}

    raw_sum = float(matched["raw_bytes"].sum())
    focal_sum = float(matched["focal_compressed_bytes"].sum())
    baseline_sum = float(matched["baseline_compressed_bytes"].sum())
    smaller_pct = (1.0 - focal_sum / baseline_sum) * 100.0
    focal_cr = _ratio(raw_sum, focal_sum)
    baseline_cr = _ratio(raw_sum, baseline_sum)

    width, height = 960, 430
    canvas = _PngCanvas(width, height)
    canvas.text(42, 24, f"{title_prefix}: Stack-ZDPD matched comparison ({_format_n(len(matched))})", size=14, weight="700")
    canvas.text(width - 24, height - 10, f"Aggregate matched reduction: {smaller_pct:.1f}% smaller; CR {focal_cr:.2f}x vs {baseline_cr:.2f}x.", size=9, anchor="end", color="#4B5563")

    left, top, plot_w, plot_h = 70, 70, 210, 240
    totals = [baseline_sum / GIB, focal_sum / GIB]
    max_total = max(totals) * 1.25
    total_ticks = _nice_ticks(max_total, 4)
    max_total_tick = max(total_ticks) if total_ticks else max_total
    canvas.text(left, 50, "a  Matched total", size=12, weight="700")
    canvas.text(18, top + plot_h / 2, "Compressed size (GiB)", size=11, anchor="middle", rotate=-90)
    for tick in total_ticks:
        y = top + plot_h - (tick / max_total_tick) * plot_h
        canvas.line(left, y, left + plot_w, y, color="#E1E5EA", width=0.75)
        canvas.text(left - 8, y + 4, f"{tick:.0f}", size=9, anchor="end", color="#4B5563")
    bar_w = 60
    for idx, (label, value, color, cr) in enumerate(
        [
            ("Stack-ZDPD", totals[0], COLORS[baseline], baseline_cr),
            ("TrackCodec", totals[1], COLORS[focal], focal_cr),
        ]
    ):
        x = left + 32 + idx * 92
        bar_h = (value / max_total_tick) * plot_h
        y = top + plot_h - bar_h
        canvas.rect(x, y, bar_w, bar_h, fill=color, stroke="#1F2937")
        canvas.text(x + bar_w / 2, y - 8, f"{cr:.2f}x", size=9, anchor="middle")
        canvas.text(x + bar_w / 2, top + plot_h + 18, label, size=9, anchor="middle")
    canvas.line(left, top + plot_h, left + plot_w, top + plot_h, color="#111827", width=1.0)
    canvas.line(left, top, left, top + plot_h, color="#111827", width=1.0)

    right_left, right_top, right_w, right_h = 372, 70, 540, 240
    vals = matched["focal_smaller_pct"].to_numpy(float)
    ymin = min(0.0, float(np.nanmin(vals)))
    ymax = max(0.0, float(np.nanmax(vals)))
    pad = max((ymax - ymin) * 0.12, 2.0)
    ymin -= pad
    ymax += pad
    canvas.text(right_left, 50, "b  Per-file matched reduction", size=12, weight="700")
    canvas.text(326, right_top + right_h / 2, "TrackCodec size reduction vs Stack-ZDPD (%)", size=11, anchor="middle", rotate=-90)
    for tick in np.linspace(math.floor(ymin / 10) * 10, math.ceil(ymax / 10) * 10, 5):
        y = right_top + right_h - ((float(tick) - ymin) / (ymax - ymin)) * right_h
        canvas.line(right_left, y, right_left + right_w, y, color="#E1E5EA", width=0.75)
        canvas.text(right_left - 8, y + 4, f"{tick:.0f}", size=9, anchor="end", color="#4B5563")
    zero_y = right_top + right_h - ((0.0 - ymin) / (ymax - ymin)) * right_h
    canvas.line(right_left, zero_y, right_left + right_w, zero_y, color="#111827", width=0.85)
    labels = matched["combined_file_label"].astype(str).tolist()
    gap = 8
    bar_w = (right_w - gap * (len(vals) - 1)) / len(vals)
    for idx, (value, label) in enumerate(zip(vals, labels)):
        x = right_left + idx * (bar_w + gap)
        y_val = right_top + right_h - ((value - ymin) / (ymax - ymin)) * right_h
        y = min(y_val, zero_y)
        h = abs(zero_y - y_val)
        fill = COLORS[focal] if value >= 0 else "#B33A3A"
        canvas.rect(x, y, bar_w, h, fill=fill, stroke="#1F2937")
        canvas.text(x + bar_w / 2, right_top + right_h + 18, label, size=8, anchor="end", rotate=-42)
    canvas.line(right_left, right_top + right_h, right_left + right_w, right_top + right_h, color="#111827", width=1.0)
    canvas.line(right_left, right_top, right_left, right_top + right_h, color="#111827", width=1.0)
    canvas.save(out_png)
    return {
        "matched_files": int(len(matched)),
        "trackcodec_smaller_pct": float(smaller_pct),
        "trackcodec_matched_cr": float(focal_cr),
        "stackzdpd_matched_cr": float(baseline_cr),
    }


def _write_nmi_method_presence_svg(df: pd.DataFrame, methods: list[str], out_svg: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    file_order = (
        df[["dataset_key", "combined_file_label", "file", "raw_bytes"]]
        .drop_duplicates("file")
        .sort_values(["dataset_key", "raw_bytes"], ascending=[True, False])
    )
    labels = file_order["combined_file_label"].astype(str).tolist()
    presence = (
        df.pivot_table(index="method", columns="combined_file_label", values="compressed_bytes", aggfunc="count", observed=False)
        .reindex(index=methods, columns=labels)
        .fillna(0)
        .astype(int)
    )
    presence.insert(0, "display_name", [DISPLAY[method] for method in presence.index])
    presence.to_csv(data_dir / "nmi_method_file_completion_presence_matrix.csv")

    cell_w, cell_h = 22, 18
    left, top = 180, 48
    width = max(760, left + len(labels) * cell_w + 28)
    height = top + len(methods) * cell_h + 118
    elements = [_svg_text(24, 24, f"{title_prefix}: completed method-file cells", size=14, weight="700")]
    matrix = presence.drop(columns=["display_name"]).to_numpy(dtype=int)
    for i, method in enumerate(methods):
        y = top + i * cell_h
        elements.append(_svg_text(left - 8, y + 13, DISPLAY[method].replace("TrackCodec ", "TC "), size=9, anchor="end"))
        for j, value in enumerate(matrix[i]):
            x = left + j * cell_w
            fill = "#324E68" if value else "#F1F3F5"
            elements.append(_svg_rect(x, y, cell_w - 1, cell_h - 1, fill=fill, stroke="#FFFFFF"))
    for j, label in enumerate(labels):
        x = left + j * cell_w + cell_w / 2
        elements.append(_svg_text(x, top + len(methods) * cell_h + 18, label, size=7.5, anchor="end", rotate=-55))
    elements.append(_svg_rect(width - 174, 18, 13, 13, fill="#324E68"))
    elements.append(_svg_text(width - 154, 29, "completed", size=9))
    elements.append(_svg_rect(width - 86, 18, 13, 13, fill="#F1F3F5", stroke="#D1D5DB"))
    elements.append(_svg_text(width - 66, 29, "not run/failed", size=9))
    _write_svg(out_svg, width, height, elements)


def _write_nmi_method_presence_png(df: pd.DataFrame, methods: list[str], out_png: Path, data_dir: Path, title_prefix: str) -> None:
    if not methods:
        return
    file_order = (
        df[["dataset_key", "combined_file_label", "file", "raw_bytes"]]
        .drop_duplicates("file")
        .sort_values(["dataset_key", "raw_bytes"], ascending=[True, False])
    )
    labels = file_order["combined_file_label"].astype(str).tolist()
    presence = (
        df.pivot_table(index="method", columns="combined_file_label", values="compressed_bytes", aggfunc="count", observed=False)
        .reindex(index=methods, columns=labels)
        .fillna(0)
        .astype(int)
    )
    presence.insert(0, "display_name", [DISPLAY[method] for method in presence.index])
    presence.to_csv(data_dir / "nmi_method_file_completion_presence_matrix.csv")

    cell_w, cell_h = 22, 18
    left, top = 180, 48
    width = max(760, left + len(labels) * cell_w + 28)
    height = top + len(methods) * cell_h + 118
    canvas = _PngCanvas(width, height)
    canvas.text(24, 24, f"{title_prefix}: completed method-file cells", size=14, weight="700")
    matrix = presence.drop(columns=["display_name"]).to_numpy(dtype=int)
    for i, method in enumerate(methods):
        y = top + i * cell_h
        canvas.text(left - 8, y + 13, DISPLAY[method].replace("TrackCodec ", "TC "), size=9, anchor="end")
        for j, value in enumerate(matrix[i]):
            x = left + j * cell_w
            fill = "#324E68" if value else "#F1F3F5"
            canvas.rect(x, y, cell_w - 1, cell_h - 1, fill=fill, stroke="#FFFFFF")
    for j, label in enumerate(labels):
        x = left + j * cell_w + cell_w / 2
        canvas.text(x, top + len(methods) * cell_h + 18, label, size=7.5, anchor="end", rotate=-55)
    canvas.rect(width - 174, 18, 13, 13, fill="#324E68")
    canvas.text(width - 154, 29, "completed", size=9)
    canvas.rect(width - 86, 18, 13, 13, fill="#F1F3F5", stroke="#D1D5DB")
    canvas.text(width - 66, 29, "not run/failed", size=9)
    canvas.save(out_png)


def _plot_nmi_figure_set(
    df: pd.DataFrame,
    agg: pd.DataFrame,
    *,
    complete_methods: list[str],
    all_methods: list[str],
    plot_dir: Path,
    data_dir: Path,
    title_prefix: str,
) -> dict[str, object]:
    nmi_dir = plot_dir / "nmi"
    nmi_data_dir = data_dir / "nmi"
    nmi_dir.mkdir(parents=True, exist_ok=True)
    nmi_data_dir.mkdir(parents=True, exist_ok=True)
    total_files = int(df["file"].nunique())

    _write_nmi_fullset_total_size_svg(
        agg,
        complete_methods,
        nmi_dir / "nmi_fullset_total_compressed_size.svg",
        nmi_data_dir,
        title_prefix,
    )
    _write_nmi_fullset_total_size_png(
        agg,
        complete_methods,
        nmi_dir / "nmi_fullset_total_compressed_size.png",
        nmi_data_dir,
        title_prefix,
    )
    _write_nmi_available_mean_ratio_svg(
        agg,
        all_methods,
        total_files,
        nmi_dir / "nmi_available_mean_compression_ratio.svg",
        nmi_data_dir,
        title_prefix,
    )
    _write_nmi_available_mean_ratio_png(
        agg,
        all_methods,
        total_files,
        nmi_dir / "nmi_available_mean_compression_ratio.png",
        nmi_data_dir,
        title_prefix,
    )
    matched_summary = _write_nmi_matched_stackzdpd_svg(
        df,
        nmi_dir / "nmi_trackcodec_vs_stackzdpd_matched.svg",
        nmi_data_dir,
        title_prefix,
    )
    _write_nmi_matched_stackzdpd_png(
        df,
        nmi_dir / "nmi_trackcodec_vs_stackzdpd_matched.png",
        nmi_data_dir,
        title_prefix,
    )
    _write_nmi_method_presence_svg(
        df,
        all_methods,
        nmi_dir / "nmi_method_file_completion_presence.svg",
        nmi_data_dir,
        title_prefix,
    )
    _write_nmi_method_presence_png(
        df,
        all_methods,
        nmi_dir / "nmi_method_file_completion_presence.png",
        nmi_data_dir,
        title_prefix,
    )
    return {
        "nmi_plot_dir": str(nmi_dir),
        "nmi_plot_data_dir": str(nmi_data_dir),
        "plots": {
            "fullset_total_size_svg": str(nmi_dir / "nmi_fullset_total_compressed_size.svg"),
            "fullset_total_size_png": str(nmi_dir / "nmi_fullset_total_compressed_size.png"),
            "available_mean_ratio_svg": str(nmi_dir / "nmi_available_mean_compression_ratio.svg"),
            "available_mean_ratio_png": str(nmi_dir / "nmi_available_mean_compression_ratio.png"),
            "trackcodec_vs_stackzdpd_matched_svg": str(nmi_dir / "nmi_trackcodec_vs_stackzdpd_matched.svg"),
            "trackcodec_vs_stackzdpd_matched_png": str(nmi_dir / "nmi_trackcodec_vs_stackzdpd_matched.png"),
            "method_file_presence_svg": str(nmi_dir / "nmi_method_file_completion_presence.svg"),
            "method_file_presence_png": str(nmi_dir / "nmi_method_file_completion_presence.png"),
        },
        "matched_stackzdpd": matched_summary,
    }


def _write_report(output_dir: Path, df: pd.DataFrame, agg: pd.DataFrame, validation: pd.DataFrame, methods: list[str]) -> None:
    docs = output_dir / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    tc = agg[agg["method"] == "trackcodec_equal_fidelity"]
    stack = agg[agg["method"] == "stack_zdpd_container"]
    zdpd = agg[agg["method"] == "zdpd_container"]
    lines = [
        "# TrackCodec Combined Whole-File Benchmark Figures",
        "",
        f"- completed_files: `{int(df['file'].nunique())}`",
        f"- common_methods: `{', '.join(methods)}`",
        "- compression_ratio: `input mzML bytes / compressed bytes`",
        "- compression_rate: `compressed bytes / input mzML bytes`",
        "",
        "## Aggregate",
        "",
        "| Method | Files | Total size GiB | CR | Compression rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for _, row in agg.iterrows():
        lines.append(
            f"| {row['display_name']} | {int(row['n_files'])} | {row['compressed_gib']:.2f} | "
            f"{row['compression_ratio']:.3f}x | {row['compression_rate_pct']:.2f}% |"
        )
    if not tc.empty:
        tc_row = tc.iloc[0]
        lines.extend(["", "## Main Comparison", ""])
        lines.append(
            f"TrackCodec equal-fidelity completed {int(tc_row['n_files'])} files with total compressed size "
            f"{tc_row['compressed_gib']:.2f} GiB and aggregate CR {tc_row['compression_ratio']:.3f}x."
        )
        if not zdpd.empty:
            zdpd_row = zdpd.iloc[0]
            if int(zdpd_row["n_files"]) == int(tc_row["n_files"]):
                lines.append(
                    f"On the same {int(tc_row['n_files'])} files, TrackCodec is "
                    f"{(1.0 - tc_row['compressed_bytes'] / zdpd_row['compressed_bytes']) * 100.0:.2f}% smaller than ZDPD."
                )
        if not stack.empty:
            stack_row = stack.iloc[0]
            if int(stack_row["n_files"]) == int(tc_row["n_files"]):
                lines.append(
                    f"On the same {int(tc_row['n_files'])} files, TrackCodec is "
                    f"{(1.0 - tc_row['compressed_bytes'] / stack_row['compressed_bytes']) * 100.0:.2f}% smaller than Stack-ZDPD."
                )
            else:
                matched = _matched_pair_table(df, "trackcodec_equal_fidelity", "stack_zdpd_container")
                if not matched.empty:
                    tc_sum = float(matched["focal_compressed_bytes"].sum())
                    stack_sum = float(matched["baseline_compressed_bytes"].sum())
                    raw_sum = float(matched["raw_bytes"].sum())
                    lines.append(
                        f"Stack-ZDPD completed {int(stack_row['n_files'])} files, so it is reported as a matched-file comparison: "
                        f"on {len(matched)} common files TrackCodec is {(1.0 - tc_sum / stack_sum) * 100.0:.2f}% smaller "
                        f"(CR {_ratio(raw_sum, tc_sum):.3f}x vs {_ratio(raw_sum, stack_sum):.3f}x)."
                    )
    lines.extend(["", "## Roundtrip Validation", ""])
    if validation.empty:
        lines.append("No TrackCodec validation rows were found.")
    else:
        lines.extend(["| Method | Files | mz OK | intensity OK | Max abs m/z error | Max abs intensity error |", "|---|---:|---:|---:|---:|---:|"])
        for _, row in validation.iterrows():
            lines.append(
                f"| {row['display_name']} | {int(row['n_files'])} | {row['mz_within_ceiling_all']} | "
                f"{row['intensity_within_ceiling_all']} | {row['max_abs_mz_error']:.6g} | {row['max_abs_intensity_error']:.6g} |"
            )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- ZDPD and Stack-ZDPD rows are numeric container baselines, not full mzML archive implementations.",
            "- Vendor raw reference lines are drawn only for files whose vendor raw size is available.",
        ]
    )
    (docs / "combined_whole_file_figures_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_combined_whole_file_figures_from_df(
    df: pd.DataFrame,
    load_summary: dict[str, object],
    *,
    output_dir: str | Path,
    title_prefix: str,
    figure_style: str,
    input_label: str,
) -> dict[str, object]:
    _apply_figure_style(figure_style)
    output_dir = Path(output_dir).resolve()
    table_dir = output_dir / "tables"
    whole_table_dir = output_dir / "whole_file" / "tables"
    plot_data_dir = output_dir / "whole_file" / "plot_data"
    plot_dir = output_dir / "whole_file" / "plots"
    for path in (table_dir, whole_table_dir, plot_data_dir, plot_dir):
        path.mkdir(parents=True, exist_ok=True)

    if df.empty:
        raise FileNotFoundError(f"No completed per-file whole-file rows found for {input_label}")

    agg = _aggregate(df)
    validation = _validation_summary(df)
    methods = _common_methods(df)
    if not methods:
        methods = [method for method in COMMON_METHOD_ORDER if method in set(df["method"].astype(str))]
    all_methods = _present_methods(df)

    df.to_csv(table_dir / "combined_whole_file_per_file_methods.csv", index=False)
    agg.to_csv(table_dir / "combined_whole_file_aggregate_by_method.csv", index=False)
    validation.to_csv(table_dir / "combined_whole_file_validation_summary.csv", index=False)
    df.to_csv(whole_table_dir / "combined_whole_file_per_file_methods.csv", index=False)
    agg.to_csv(whole_table_dir / "combined_whole_file_aggregate_by_method.csv", index=False)

    _write_report(output_dir, df, agg, validation, methods)
    nmi_summary: dict[str, object] = {}
    if figure_style == "nmi":
        nmi_summary = _plot_nmi_figure_set(
            df,
            agg,
            complete_methods=methods,
            all_methods=all_methods,
            plot_dir=plot_dir,
            data_dir=plot_data_dir,
            title_prefix=title_prefix,
        )
    else:
        _plot_total_size(agg, methods, plot_dir / "combined_whole_file_total_compressed_size_by_method", title_prefix)
        _plot_ratio_rate(agg, methods, plot_dir / "combined_whole_file_compression_ratio_and_rate_by_method", title_prefix)
        _plot_distribution(df, methods, plot_dir / "combined_whole_file_common_methods_compression_ratio_distribution", plot_data_dir, title_prefix)
        _plot_mean(df, methods, plot_dir / "combined_whole_file_mean_compression_ratio", plot_data_dir, title_prefix)
        _plot_multipanel(df, methods, plot_dir / "combined_whole_file_multipanel_available_formats_with_mzml_trackcodec", plot_data_dir, title_prefix)
        _plot_method_file_heatmap(df, methods, plot_dir / "combined_whole_file_method_file_compression_ratio_heatmap", plot_data_dir, title_prefix)
        _plot_speed_pareto(df, methods, plot_dir / "combined_whole_file_compression_ratio_vs_encode_time", plot_data_dir, title_prefix)
    standard_plots = {}
    if figure_style != "nmi":
        standard_plots = {
            "total_size_svg": str(plot_dir / "combined_whole_file_total_compressed_size_by_method.svg"),
            "ratio_rate_svg": str(plot_dir / "combined_whole_file_compression_ratio_and_rate_by_method.svg"),
            "distribution_svg": str(plot_dir / "combined_whole_file_common_methods_compression_ratio_distribution.svg"),
            "mean_ratio_svg": str(plot_dir / "combined_whole_file_mean_compression_ratio.svg"),
            "multipanel_svg": str(plot_dir / "combined_whole_file_multipanel_available_formats_with_mzml_trackcodec.svg"),
            "method_file_heatmap_svg": str(plot_dir / "combined_whole_file_method_file_compression_ratio_heatmap.svg"),
            "speed_pareto_svg": str(plot_dir / "combined_whole_file_compression_ratio_vs_encode_time.svg"),
        }

    summary = {
        "input": input_label,
        "output_dir": str(output_dir),
        "figure_style": figure_style,
        "n_files": int(df["file"].nunique()),
        "n_rows": int(len(df)),
        "complete_methods": methods,
        "all_methods": all_methods,
        "per_file_csv": str(table_dir / "combined_whole_file_per_file_methods.csv"),
        "aggregate_csv": str(table_dir / "combined_whole_file_aggregate_by_method.csv"),
        "validation_csv": str(table_dir / "combined_whole_file_validation_summary.csv"),
        "plots": standard_plots,
        "nmi": nmi_summary,
        **load_summary,
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def build_combined_whole_file_figures(
    result_root: str | Path,
    *,
    output_dir: str | Path | None = None,
    vendor_raw_reference: str | Path | None = None,
    vendor_raw_dir: str | Path | None = None,
    title_prefix: str = "TrackCodec completed",
    figure_style: str = "default",
) -> dict[str, object]:
    result_root = Path(result_root).resolve()
    output_dir = Path(output_dir).resolve() if output_dir is not None else result_root / "combined_whole_file"
    vendor_raw_reference_path = Path(vendor_raw_reference).resolve() if vendor_raw_reference else None
    vendor_raw_dir_path = Path(vendor_raw_dir).resolve() if vendor_raw_dir else None
    df, load_summary = _load_rows(
        result_root,
        vendor_raw_reference=vendor_raw_reference_path,
        vendor_raw_dir=vendor_raw_dir_path,
    )
    return _build_combined_whole_file_figures_from_df(
        df,
        load_summary,
        output_dir=output_dir,
        title_prefix=title_prefix,
        figure_style=figure_style,
        input_label=str(result_root),
    )


def build_combined_whole_file_figures_from_csv(
    combined_per_file_csv: str | Path,
    *,
    output_dir: str | Path,
    title_prefix: str = "TrackCodec completed benchmark",
    figure_style: str = "nmi",
) -> dict[str, object]:
    combined_per_file_csv = Path(combined_per_file_csv).resolve()
    df, load_summary = _load_combined_per_file_rows(combined_per_file_csv)
    return _build_combined_whole_file_figures_from_df(
        df,
        load_summary,
        output_dir=output_dir,
        title_prefix=title_prefix,
        figure_style=figure_style,
        input_label=str(combined_per_file_csv),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build combined whole-file benchmark tables and plots from TrackCodec supervisor outputs.")
    parser.add_argument("--result-root", type=Path)
    parser.add_argument("--combined-per-file-csv", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--vendor-raw-reference", type=Path, default=os.environ.get("TRACKCODEC_VENDOR_RAW_REFERENCE"))
    parser.add_argument("--vendor-raw-dir", type=Path, default=os.environ.get("TRACKCODEC_FULL8_RAW_DIR"))
    parser.add_argument("--title-prefix", default="TrackCodec completed")
    parser.add_argument("--figure-style", choices=("default", "nmi"), default="default")
    args = parser.parse_args()
    if args.combined_per_file_csv is None and args.result_root is None:
        parser.error("provide either --result-root or --combined-per-file-csv")
    if args.combined_per_file_csv is not None and args.output_dir is None:
        parser.error("--output-dir is required with --combined-per-file-csv")
    return args


def main() -> int:
    args = _parse_args()
    if args.combined_per_file_csv is not None:
        summary = build_combined_whole_file_figures_from_csv(
            args.combined_per_file_csv,
            output_dir=args.output_dir,
            title_prefix=args.title_prefix,
            figure_style=args.figure_style,
        )
    else:
        summary = build_combined_whole_file_figures(
            args.result_root,
            output_dir=args.output_dir,
            vendor_raw_reference=args.vendor_raw_reference,
            vendor_raw_dir=args.vendor_raw_dir,
            title_prefix=args.title_prefix,
            figure_style=args.figure_style,
        )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
