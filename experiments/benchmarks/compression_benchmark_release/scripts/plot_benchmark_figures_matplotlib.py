from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

try:
    from scipy import stats
except Exception:  # pragma: no cover
    stats = None

from benchmark_release_paths import default_combined_output_dir

DEFAULT_COMBINED_DIR = default_combined_output_dir(Path(__file__))
GIB = 1024**3

WHOLE_METHOD_ORDER = [
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
WHOLE_DISTRIBUTION_EXCLUDE = {"stackzdpd"}

WHOLE_DISPLAY = {
    "original_mzml": "Original mzML",
    "msconvert_zlib": "zlib level 6",
    "msconvert_gzip": "gzip level 6",
    "msconvert_numpress": "NumpressAll + zlib L6",
    "mspack": "mspack",
    "masscomp": "MassComp",
    "airdpro": "AirdPro",
    "zdpd": "ZDPD",
    "stackzdpd": "Stack-ZDPD",
    "trackcodec": "TrackCodec",
}

WHOLE_TICK_LABEL = {
    "original_mzml": "mzML",
    "msconvert_zlib": "zlib\nlevel 6",
    "msconvert_gzip": "gzip\nlevel 6",
    "msconvert_numpress": "NumpressAll\n+ zlib L6",
    "mspack": "mspack",
    "masscomp": "MassComp",
    "airdpro": "AirdPro",
    "zdpd": "ZDPD",
    "stackzdpd": "Stack-ZDPD",
    "trackcodec": "TrackCodec",
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

PRECISION_GROUPS = {
    "original_mzml": "strict_lossless",
    "msconvert_zlib": "strict_lossless",
    "msconvert_gzip": "strict_lossless",
    "mspack": "strict_lossless",
    "masscomp": "strict_lossless",
    "msconvert_numpress": "near_lossless",
    "airdpro": "near_lossless",
    "zdpd": "near_lossless",
    "stackzdpd": "near_lossless",
    "trackcodec": "near_lossless",
}

PRECISION_GROUP_LABELS = {
    "strict_lossless": "Strict lossless",
    "near_lossless": "Near-lossless / controlled precision",
}

PRECISION_GROUP_COLORS = {
    "strict_lossless": "#4E79A7",
    "near_lossless": "#59A14F",
}

WHOLE_COLORS = {
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

HEATMAP_COLORS = [
    (0.0, "#ECF7FB"),
    (0.5, "#7BCCC4"),
    (1.0, "#0868AC"),
]
HEATMAP_MISSING_COLOR = "#F2F2F2"

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

SECTION_MS1_ORDER = [
    "gzip",
    "zlib",
    "zstd-9",
    "airdpro_default",
    "zdpd_baseline",
    "ours_strict_q6",
    "ours_archive_fidelity",
]
SECTION_MS2_ORDER = [
    "gzip",
    "zlib",
    "zstd-9",
    "airdpro_default",
    "zdpd_baseline",
    "ours_eqfidelity",
]
SECTION_DISPLAY = {
    "gzip": "gzip\nlevel 6\nraw float64",
    "zlib": "zlib\nlevel 6\nraw float64",
    "zstd-9": "zstd\nlevel 9\nraw float64",
    "airdpro_default": "AirdPro\nDefault",
    "zdpd_baseline": "ZDPD",
    "ours_eqfidelity": "TrackCodec\neq-fidelity",
    "ours_strict_q6": "TrackCodec\nstrict q6",
    "ours_archive_fidelity": "TrackCodec\narchive fidelity",
}
SECTION_COLORS = {
    "gzip": "#C7E4F2",
    "zlib": "#9ECAE1",
    "zstd-9": "#6BAED6",
    "airdpro_default": "#00A087",
    "zdpd_baseline": "#72B7B2",
    "ours_eqfidelity": "#F58518",
    "ours_strict_q6": "#3C5488",
    "ours_archive_fidelity": "#E45756",
}


def _finite_float(value: object) -> float:
    try:
        value_f = float(value)
    except Exception:
        return math.nan
    return value_f if math.isfinite(value_f) else math.nan


def _normal_two_sided_p_from_z(z: float) -> float:
    if not math.isfinite(z):
        return math.nan
    return math.erfc(abs(z) / math.sqrt(2.0))


def _method_order(rows: pd.DataFrame, include_error: bool = True) -> list[str]:
    seen = set(rows["method"].astype(str))
    methods = [method for method in WHOLE_METHOD_ORDER if method in seen]
    if include_error:
        return methods
    ok = set(rows.loc[rows["status"].astype(str) == "ok", "method"].astype(str))
    return [method for method in methods if method in ok]


def _median_order(rows: pd.DataFrame, *, exclude: set[str] | None = None) -> list[str]:
    exclude = exclude or set()
    order = []
    for method in _method_order(rows, include_error=False):
        if method in exclude:
            continue
        vals = pd.to_numeric(
            rows.loc[(rows["method"] == method) & (rows["status"] == "ok"), "compression_ratio"],
            errors="coerce",
        ).dropna()
        if not vals.empty:
            order.append((method, float(vals.median())))
    return [method for method, _ in sorted(order, key=lambda item: item[1])]


def _aggregate(rows: pd.DataFrame) -> pd.DataFrame:
    out: list[dict[str, object]] = []
    for method in _method_order(rows, include_error=False):
        sub = rows[(rows["method"] == method) & (rows["status"] == "ok")].copy()
        if sub.empty:
            continue
        raw = pd.to_numeric(sub["raw_bytes"], errors="coerce").fillna(0).sum()
        compressed = pd.to_numeric(sub["compressed_bytes"], errors="coerce").fillna(0).sum()
        ratios = pd.to_numeric(sub["compression_ratio"], errors="coerce").dropna()
        elapsed = pd.to_numeric(sub["elapsed_seconds"], errors="coerce").dropna()
        out.append(
            {
                "method": method,
                "n_files": int(sub["sample_id"].nunique()),
                "raw_bytes": float(raw),
                "compressed_bytes": float(compressed),
                "compressed_gib": float(compressed) / GIB,
                "mean_compression_ratio": float(ratios.mean()) if len(ratios) else math.nan,
                "median_compression_ratio": float(ratios.median()) if len(ratios) else math.nan,
                "aggregate_compression_ratio": float(raw / compressed) if compressed else math.nan,
                "mean_elapsed_seconds": float(elapsed.mean()) if len(elapsed) else math.nan,
            }
        )
    return pd.DataFrame(out)


def _paired_wilcoxon_summary(rows: pd.DataFrame, base_method: str = "airdpro", target_method: str = "trackcodec") -> dict[str, float | int | str]:
    subset = rows[(rows["method"].isin([base_method, target_method])) & (rows["status"] == "ok")].copy()
    if subset.empty:
        return {}
    pivot = subset.pivot_table(index="sample_id", columns="method", values="compression_ratio", aggfunc="first", observed=False)
    pivot = pivot[[col for col in [base_method, target_method] if col in pivot.columns]].dropna()
    if pivot.empty or base_method not in pivot.columns or target_method not in pivot.columns:
        return {}
    base = pivot[base_method].to_numpy(dtype=float)
    cand = pivot[target_method].to_numpy(dtype=float)
    delta_pct = (cand / base - 1.0) * 100.0
    finite = np.isfinite(base) & np.isfinite(cand) & (base > 0)
    base = base[finite]
    cand = cand[finite]
    delta_pct = delta_pct[finite]
    if base.size == 0:
        return {}

    p_value = math.nan
    statistic = math.nan
    if stats is not None and base.size >= 2:
        try:
            res = stats.wilcoxon(cand, base, zero_method="wilcox", alternative="two-sided", method="auto")
            statistic = float(res.statistic)
            p_value = float(res.pvalue)
        except Exception:
            pass

    if not math.isfinite(p_value) and base.size >= 2:
        diff = cand - base
        nonzero = diff != 0
        diff = diff[nonzero]
        if diff.size:
            abs_diff = np.abs(diff)
            ranks = pd.Series(abs_diff).rank(method="average").to_numpy(dtype=float)
            w_plus = float(ranks[diff > 0].sum())
            w_minus = float(ranks[diff < 0].sum())
            statistic = min(w_plus, w_minus)
            n_eff = int(diff.size)
            mean_w = n_eff * (n_eff + 1) / 4.0
            tie_counts = pd.Series(abs_diff).value_counts().to_numpy(dtype=float)
            tie_correction = float(np.sum(tie_counts * (tie_counts + 1.0) * (2.0 * tie_counts + 1.0)))
            var_w = n_eff * (n_eff + 1) * (2.0 * n_eff + 1.0) / 24.0 - tie_correction / 48.0
            if var_w > 0:
                z = (abs(statistic - mean_w) - 0.5) / math.sqrt(var_w)
                p_value = _normal_two_sided_p_from_z(z)

    diff = cand - base
    ranks = pd.Series(np.abs(diff)).rank(method="average").to_numpy(dtype=float)
    pos = float(ranks[diff > 0].sum())
    neg = float(ranks[diff < 0].sum())
    total_rank = pos + neg
    rank_biserial = float((pos - neg) / total_rank) if total_rank else math.nan

    base_ok = rows[(rows["method"] == base_method) & (rows["status"] == "ok")]
    target_ok = rows[(rows["method"] == target_method) & (rows["status"] == "ok")]
    base_raw = pd.to_numeric(base_ok["raw_bytes"], errors="coerce").sum()
    base_compressed = pd.to_numeric(base_ok["compressed_bytes"], errors="coerce").sum()
    target_raw = pd.to_numeric(target_ok["raw_bytes"], errors="coerce").sum()
    target_compressed = pd.to_numeric(target_ok["compressed_bytes"], errors="coerce").sum()

    return {
        "n": int(base.size),
        "airdpro_median_cr": float(np.median(base)),
        "trackcodec_median_cr": float(np.median(cand)),
        "airdpro_mean_cr": float(np.mean(base)),
        "trackcodec_mean_cr": float(np.mean(cand)),
        "airdpro_aggregate_cr": float(base_raw / base_compressed) if base_compressed else math.nan,
        "trackcodec_aggregate_cr": float(target_raw / target_compressed) if target_compressed else math.nan,
        "paired_gain_mean_pct": float(np.mean(delta_pct)),
        "paired_gain_median_pct": float(np.median(delta_pct)),
        "paired_gain_min_pct": float(np.min(delta_pct)),
        "paired_gain_max_pct": float(np.max(delta_pct)),
        "wilcoxon_statistic": statistic,
        "wilcoxon_p_value": p_value,
        "rank_biserial": rank_biserial,
    }


def _save_all(fig: plt.Figure, out_base: Path, dpi: int = 300, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for fmt in formats:
        suffix = "." + fmt.lstrip(".").lower()
        target = out_base.with_suffix(suffix)
        if suffix == ".png":
            fig.savefig(target, dpi=dpi, bbox_inches="tight")
        else:
            fig.savefig(target, bbox_inches="tight")
        written.append(str(target))
    plt.close(fig)
    return written


def _style_axes(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", which="major", direction="out", length=4.0, width=0.9)
    ax.grid(axis="y", color="#d9e2ec", linewidth=0.75, alpha=0.75)
    ax.set_axisbelow(True)


def plot_whole_distribution(rows: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    methods = _median_order(rows, exclude=WHOLE_DISTRIBUTION_EXCLUDE)
    data = []
    for method in methods:
        vals = pd.to_numeric(
            rows.loc[(rows["method"] == method) & (rows["status"] == "ok"), "compression_ratio"],
            errors="coerce",
        ).dropna()
        data.append(vals.to_numpy(float))

    fig, ax = plt.subplots(figsize=(14.2, 6.8))
    x = np.arange(1, len(methods) + 1)
    wide = (
        rows[(rows["method"].isin(methods)) & (rows["status"] == "ok")]
        .pivot_table(index="sample_id", columns="method", values="compression_ratio", aggfunc="first", observed=False)
        .reindex(columns=methods)
    )
    for sample_idx, (_, row) in enumerate(wide.iterrows()):
        y = row.to_numpy(dtype=float)
        valid = np.isfinite(y)
        if valid.sum() >= 2:
            ax.plot(
                x[valid],
                y[valid],
                linestyle=(0, (3, 3)),
                linewidth=0.9,
                alpha=0.30,
                color=FILE_LINE_COLORS[sample_idx % len(FILE_LINE_COLORS)],
                zorder=1,
            )
        ax.scatter(
            x[valid],
            y[valid],
            s=24,
            color="#111111",
            edgecolor="white",
            linewidth=0.35,
            alpha=0.76,
            zorder=3,
        )

    bp = ax.boxplot(
        data,
        positions=x,
        widths=0.48,
        patch_artist=True,
        showfliers=False,
        medianprops={"color": "#111111", "linewidth": 1.5},
        boxprops={"linewidth": 1.1, "edgecolor": "#222222"},
        whiskerprops={"linewidth": 1.0, "color": "#222222"},
        capprops={"linewidth": 1.0, "color": "#222222"},
        zorder=2,
    )
    for patch, method in zip(bp["boxes"], methods):
        group = PRECISION_GROUPS.get(method, "near_lossless")
        patch.set_facecolor(PRECISION_GROUP_COLORS[group])
        patch.set_alpha(0.78)

    ax.set_title("Compression Ratio Distribution by Method", fontsize=18, weight="bold", pad=16)
    ax.set_ylabel("Compression ratio (x)", fontsize=14)
    ax.set_xlabel("mzML and compression method", fontsize=14, labelpad=14)
    ax.set_xticks(x)
    ax.set_xticklabels([WHOLE_TICK_LABEL[method] for method in methods], fontsize=13)
    ax.tick_params(axis="y", labelsize=13)
    ax.set_ylim(0.7, max(2.0, float(np.nanmax([np.nanmax(d) if len(d) else np.nan for d in data])) * 1.10))
    _style_axes(ax)
    stats_summary = _paired_wilcoxon_summary(rows)
    if stats_summary:
        annotation = "\n".join(
            [
                f"TC vs AirdPro paired per-file CR (n={stats_summary['n']})",
                f"Median: AirdPro {stats_summary['airdpro_median_cr']:.2f}x -> TC {stats_summary['trackcodec_median_cr']:.2f}x ({(stats_summary['trackcodec_median_cr'] / stats_summary['airdpro_median_cr'] - 1.0) * 100.0:+.1f}%)",
                f"Aggregate: AirdPro {stats_summary['airdpro_aggregate_cr']:.2f}x -> TC {stats_summary['trackcodec_aggregate_cr']:.2f}x ({(stats_summary['trackcodec_aggregate_cr'] / stats_summary['airdpro_aggregate_cr'] - 1.0) * 100.0:+.1f}%)",
                f"Per-file gain: mean {stats_summary['paired_gain_mean_pct']:+.1f}%, median {stats_summary['paired_gain_median_pct']:+.1f}%, range [{stats_summary['paired_gain_min_pct']:+.1f}%, {stats_summary['paired_gain_max_pct']:+.1f}%]",
                f"Wilcoxon p={stats_summary['wilcoxon_p_value']:.2e}, rank-biserial r={stats_summary['rank_biserial']:.3f}",
            ]
        )
        ax.text(
            0.015,
            0.985,
            annotation,
            transform=ax.transAxes,
            ha="left",
            va="top",
            fontsize=12,
            color="#111111",
            bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#C7D0D9", alpha=0.90),
        )
    legend_handles = [
        Patch(
            facecolor=PRECISION_GROUP_COLORS[group],
            edgecolor="#222222",
            alpha=0.78,
            label=label,
        )
        for group, label in PRECISION_GROUP_LABELS.items()
    ]
    ax.legend(
        handles=legend_handles,
        loc="upper right",
        frameon=True,
        facecolor="white",
        edgecolor="#C7D0D9",
        framealpha=0.92,
        fontsize=11,
        handlelength=1.2,
        handletextpad=0.45,
        borderpad=0.2,
    )
    summary_path = plots_dir.parent / "tables" / "combined_compression_ratio_distribution_wilcoxon_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if stats_summary:
        summary_path.write_text(json.dumps(stats_summary, indent=2), encoding="utf-8")
    fig.subplots_adjust(left=0.075, right=0.995, top=0.87, bottom=0.25)
    return _save_all(fig, plots_dir / "combined_compression_ratio_distribution_matplotlib", formats=formats)


def plot_whole_mean_ratio(agg: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    methods = [m for m in _median_order_from_agg(agg) if m != "stackzdpd"]
    sub = agg.set_index("method").loc[methods].reset_index()
    fig, ax = plt.subplots(figsize=(12.0, 6.0))
    x = np.arange(len(sub))
    vals = sub["median_compression_ratio"].to_numpy(float)
    bars = ax.bar(x, vals, color=[WHOLE_COLORS[m] for m in sub["method"]], width=0.68)
    ymax = max(2.0, float(np.nanmax(vals)) * 1.22)
    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            float(row["median_compression_ratio"]) + ymax * 0.018,
            f"{float(row['median_compression_ratio']):.2f}x\nn={int(row['n_files'])}",
            ha="center",
            va="bottom",
            fontsize=8.0,
        )
    ax.set_title("Median Compression Ratio by Method", fontsize=16, weight="bold", pad=14)
    ax.set_ylabel("Median compression ratio (x)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([WHOLE_TICK_LABEL[m] for m in sub["method"]], fontsize=8.6)
    ax.set_ylim(0, ymax)
    _style_axes(ax)
    fig.subplots_adjust(left=0.075, right=0.995, top=0.88, bottom=0.22)
    return _save_all(fig, plots_dir / "combined_median_compression_ratio_matplotlib", formats=formats)


def _median_order_from_agg(agg: pd.DataFrame) -> list[str]:
    sub = agg.dropna(subset=["median_compression_ratio"]).copy()
    sub = sub.sort_values("median_compression_ratio")
    return [m for m in sub["method"].astype(str).tolist() if m in WHOLE_METHOD_ORDER]


def plot_whole_total_size(agg: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    methods = [m for m in WHOLE_METHOD_ORDER if m in set(agg["method"].astype(str))]
    sub = agg.set_index("method").loc[methods].reset_index()
    fig, ax = plt.subplots(figsize=(12.2, 6.0))
    x = np.arange(len(sub))
    vals = sub["compressed_gib"].to_numpy(float)
    bars = ax.bar(x, vals, color=[WHOLE_COLORS[m] for m in sub["method"]], width=0.68)
    ymax = max(1.0, float(np.nanmax(vals)) * 1.18)
    for bar, (_, row) in zip(bars, sub.iterrows()):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            float(row["compressed_gib"]) + ymax * 0.018,
            f"{float(row['compressed_gib']):.1f}\n{float(row['aggregate_compression_ratio']):.2f}x",
            ha="center",
            va="bottom",
            fontsize=7.8,
        )
    ax.set_title("Total Compressed Size by Method", fontsize=16, weight="bold", pad=14)
    ax.set_ylabel("Total compressed size (GiB)", fontsize=11)
    ax.set_xticks(x)
    ax.set_xticklabels([WHOLE_TICK_LABEL[m] for m in sub["method"]], fontsize=8.2)
    ax.set_ylim(0, ymax)
    _style_axes(ax)
    fig.subplots_adjust(left=0.075, right=0.995, top=0.88, bottom=0.23)
    return _save_all(fig, plots_dir / "combined_total_compressed_size_matplotlib", formats=formats)


def plot_ratio_vs_time(agg: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    sub = agg[(agg["method"] != "original_mzml") & (agg["method"] != "stackzdpd")].copy()
    sub = sub[np.isfinite(sub["mean_elapsed_seconds"]) & np.isfinite(sub["median_compression_ratio"])]
    fig, ax = plt.subplots(figsize=(8.2, 5.8))
    for _, row in sub.iterrows():
        method = str(row["method"])
        ax.scatter(
            float(row["mean_elapsed_seconds"]),
            float(row["median_compression_ratio"]),
            s=74,
            color=WHOLE_COLORS.get(method, "#777777"),
            edgecolor="white",
            linewidth=0.8,
            zorder=3,
        )
        ax.text(
            float(row["mean_elapsed_seconds"]),
            float(row["median_compression_ratio"]),
            "  " + WHOLE_DISPLAY[method].replace(" level 6", " L6"),
            va="center",
            fontsize=8.6,
        )
    ax.set_title("Compression Ratio vs Elapsed Time", fontsize=15, weight="bold", pad=12)
    ax.set_xlabel("Mean elapsed time per completed file (s)", fontsize=11)
    ax.set_ylabel("Median compression ratio (x)", fontsize=11)
    ax.set_ylim(0, 8)
    _style_axes(ax)
    fig.subplots_adjust(left=0.11, right=0.98, top=0.88, bottom=0.13)
    return _save_all(fig, plots_dir / "combined_ratio_vs_elapsed_time_matplotlib", formats=formats)


def plot_heatmap(rows: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    methods = [m for m in WHOLE_METHOD_ORDER if m in set(rows["method"].astype(str))]
    file_labels = (
        rows[["dataset_key", "combined_file_label", "sample_id"]]
        .drop_duplicates("sample_id")
        .sort_values(["dataset_key", "combined_file_label"])
    )
    labels = file_labels["combined_file_label"].astype(str).tolist()
    matrix = (
        rows.pivot_table(
            index="method",
            columns="combined_file_label",
            values="compression_ratio",
            aggfunc="first",
            observed=False,
        )
        .reindex(index=methods, columns=labels)
    )
    values = matrix.to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(max(13.0, len(labels) * 0.38 + 5.0), 6.2))
    masked = np.ma.masked_invalid(values)
    cmap = mcolors.LinearSegmentedColormap.from_list("svg_ratio_heatmap", HEATMAP_COLORS, N=256).copy()
    cmap.set_bad(HEATMAP_MISSING_COLOR)
    finite = values[np.isfinite(values)]
    vmin = float(np.nanmin(finite)) if finite.size else 0.0
    vmax = float(np.nanmax(finite)) if finite.size else 1.0
    image = ax.imshow(masked, aspect="auto", cmap=cmap, vmin=vmin, vmax=vmax)
    text_threshold = (vmin + vmax) / 2.0
    for i in range(len(methods)):
        for j in range(len(labels)):
            value = values[i, j]
            is_value = math.isfinite(value)
            ax.text(
                j,
                i,
                f"{value:.1f}x" if is_value else "N/A",
                ha="center",
                va="center",
                fontsize=5.4,
                color=("#ffffff" if is_value and value > text_threshold else ("#111111" if is_value else "#666666")),
                zorder=4,
            )
    ax.set_title("Method x File Compression Ratio Heatmap", fontsize=16, weight="bold", pad=14)
    ax.set_xlabel("File", fontsize=11)
    ax.set_ylabel("Method", fontsize=11)
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=7.2)
    ax.set_yticks(np.arange(len(methods)))
    ax.set_yticklabels([WHOLE_DISPLAY[m] for m in methods], fontsize=8.6)
    ax.set_xticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(methods), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.7)
    ax.tick_params(which="minor", bottom=False, left=False)
    cbar = fig.colorbar(image, ax=ax, fraction=0.022, pad=0.012)
    cbar.set_label("Compression ratio (x)", fontsize=10)
    fig.subplots_adjust(left=0.15, right=0.965, top=0.88, bottom=0.31)
    return _save_all(fig, plots_dir / "combined_method_file_ratio_heatmap_matplotlib", formats=formats)


def plot_status_matrix(rows: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    methods = [m for m in WHOLE_METHOD_ORDER if m in set(rows["method"].astype(str))]
    file_labels = (
        rows[["dataset_key", "combined_file_label", "sample_id"]]
        .drop_duplicates("sample_id")
        .sort_values(["dataset_key", "combined_file_label"])
    )
    labels = file_labels["combined_file_label"].astype(str).tolist()
    status = (
        rows.pivot_table(index="method", columns="combined_file_label", values="status", aggfunc="first", observed=False)
        .reindex(index=methods, columns=labels)
    )
    numeric = status.map(lambda value: 1 if str(value) == "ok" else (0 if str(value) == "error" else np.nan)).to_numpy(float)
    cmap = mcolors.ListedColormap(["#E45756", "#59A14F"]).copy()
    cmap.set_bad("#F2F2F2")
    fig, ax = plt.subplots(figsize=(max(13.0, len(labels) * 0.38 + 5.0), 5.8))
    ax.imshow(np.ma.masked_invalid(numeric), aspect="auto", cmap=cmap, vmin=0, vmax=1)
    ax.set_title("Method x File Status Matrix", fontsize=16, weight="bold", pad=14)
    ax.set_xlabel("File", fontsize=11)
    ax.set_ylabel("Method", fontsize=11)
    ax.set_xticks(np.arange(len(labels)))
    ax.set_xticklabels(labels, rotation=55, ha="right", fontsize=7.2)
    ax.set_yticks(np.arange(len(methods)))
    ax.set_yticklabels([WHOLE_DISPLAY[m] for m in methods], fontsize=8.6)
    ax.set_xticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(methods), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.7)
    ax.tick_params(which="minor", bottom=False, left=False)
    ax.legend(
        handles=[
            Patch(facecolor="#59A14F", edgecolor="none", label="OK"),
            Patch(facecolor="#E45756", edgecolor="none", label="ERROR"),
            Patch(facecolor="#F2F2F2", edgecolor="#cbd5df", label="N/A"),
        ],
        loc="upper right",
        bbox_to_anchor=(1.0, 1.115),
        ncol=3,
        frameon=False,
        fontsize=9.0,
        handlelength=1.2,
        columnspacing=1.2,
    )
    fig.subplots_adjust(left=0.15, right=0.985, top=0.85, bottom=0.31)
    return _save_all(fig, plots_dir / "combined_method_file_status_matrix_matplotlib", formats=formats)


def plot_multipanel_size(rows: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    methods = [m for m in WHOLE_METHOD_ORDER if m in set(rows["method"].astype(str))]
    file_labels = (
        rows[["dataset_key", "combined_file_label", "sample_id", "file"]]
        .drop_duplicates("sample_id")
        .sort_values(["dataset_key", "combined_file_label"])
        .reset_index(drop=True)
    )
    ncols = 7
    nrows = math.ceil(len(file_labels) / ncols)
    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 2.35, nrows * 2.25), squeeze=False)
    for idx, file_row in file_labels.iterrows():
        ax = axes[idx // ncols][idx % ncols]
        sample_id = str(file_row["sample_id"])
        sub = rows[rows["sample_id"] == sample_id].set_index("method")
        vals = [float(sub.loc[m, "compressed_gib"]) if m in sub.index and str(sub.loc[m, "status"]) == "ok" else math.nan for m in methods]
        raw_gib = _finite_float(sub["raw_gib"].dropna().iloc[0]) if "raw_gib" in sub and sub["raw_gib"].notna().any() else math.nan
        vendor_gib = (
            _finite_float(sub["vendor_raw_gib"].dropna().iloc[0])
            if "vendor_raw_gib" in sub and sub["vendor_raw_gib"].notna().any()
            else math.nan
        )
        draw_vendor = math.isfinite(vendor_gib) and vendor_gib > 0 and (not math.isfinite(raw_gib) or vendor_gib <= raw_gib)
        x = np.arange(len(methods))
        ok = np.isfinite(vals)
        ax.bar(x[ok], np.asarray(vals)[ok], color=[WHOLE_COLORS[m] for m, keep in zip(methods, ok) if keep], width=0.72, zorder=2)
        ax.set_title(str(file_row["combined_file_label"]), fontsize=7.6, pad=3)
        ax.set_xticks([])
        ax.tick_params(axis="y", labelsize=6.4, length=2.5)
        ax.grid(axis="y", alpha=0.22, linewidth=0.5)
        refs = [float(np.nanmax(vals))] if np.isfinite(vals).any() else [1.0]
        if draw_vendor:
            refs.append(vendor_gib)
        ymax = max(refs)
        ylim_top = ymax * 1.22 if ymax else 1.0
        ax.set_ylim(0, ylim_top)
        ax.set_xlim(-0.6, len(methods) - 0.4)
        if draw_vendor:
            ax.axhline(vendor_gib, color="#2E2E2E", linewidth=0.85, linestyle=(0, (5, 4)), zorder=5)
            ax.text(
                -0.48,
                min(vendor_gib + ylim_top * 0.022, ylim_top * 0.965),
                f"raw {vendor_gib:.2f} GiB",
                ha="left",
                va="bottom",
                fontsize=5.7,
                color="#2E2E2E",
                zorder=6,
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.75, "pad": 0.45},
            )
    for idx in range(len(file_labels), nrows * ncols):
        axes[idx // ncols][idx % ncols].axis("off")
    handles = [plt.Rectangle((0, 0), 1, 1, color=WHOLE_COLORS[m]) for m in methods]
    labels = [WHOLE_TICK_LABEL[m].replace("\n", " ") for m in methods]
    handles.append(Line2D([0], [0], color="#2E2E2E", linewidth=1.0, linestyle=(0, (5, 4))))
    labels.append("Vendor raw size")
    fig.legend(handles, labels, ncol=6, loc="upper center", frameon=False, fontsize=8.0, bbox_to_anchor=(0.5, 0.995))
    fig.suptitle("Compressed Size by Method for Each File", fontsize=16, weight="bold", y=1.012)
    fig.text(0.01, 0.5, "Compressed size (GiB)", rotation=90, va="center", fontsize=10)
    fig.subplots_adjust(left=0.035, right=0.995, top=0.88, bottom=0.04, hspace=0.42, wspace=0.24)
    return _save_all(fig, plots_dir / "combined_multipanel_compressed_size_matplotlib", dpi=240, formats=formats)


def _section_order(section: str) -> list[str]:
    return SECTION_MS1_ORDER if section == "MS1" else SECTION_MS2_ORDER


def _format_pct(value: float) -> str:
    return "n/a" if not math.isfinite(value) else f"{value:+.1f}%"


def _ratio_gain_pct(reference: float, candidate: float) -> float:
    if not math.isfinite(reference) or not math.isfinite(candidate) or reference <= 0:
        return math.nan
    return (candidate / reference - 1.0) * 100.0


def _aggregate_ratio(section_rows: pd.DataFrame, section: str, label: str) -> float:
    sub = section_rows[(section_rows["section"] == section) & (section_rows["label"] == label)].copy()
    if "include_in_aggregate" in sub:
        sub = sub[sub["include_in_aggregate"].astype(str).str.lower().eq("yes")]
    sub["raw_bytes"] = pd.to_numeric(sub["raw_bytes"], errors="coerce")
    sub["compressed_bytes"] = pd.to_numeric(sub["compressed_bytes"], errors="coerce")
    raw = float(sub["raw_bytes"].dropna().sum())
    compressed = float(sub["compressed_bytes"].dropna().sum())
    return raw / compressed if raw > 0 and compressed > 0 else math.nan


def _section_advantage_text(section_rows: pd.DataFrame, section: str, order: list[str]) -> str:
    baseline = "airdpro_default"
    target = "ours_archive_fidelity" if "ours_archive_fidelity" in order else "ours_eqfidelity"
    if baseline not in order or target not in order:
        return ""

    sub = section_rows[(section_rows["section"] == section) & (section_rows["label"].isin([baseline, target]))].copy()
    if "include_in_aggregate" in sub:
        sub = sub[sub["include_in_aggregate"].astype(str).str.lower().eq("yes")]
    sub["compression_ratio"] = pd.to_numeric(sub["compression_ratio"], errors="coerce")
    sub = sub[np.isfinite(sub["compression_ratio"]) & (sub["compression_ratio"] > 0)]
    wide = sub.pivot_table(index="file", columns="label", values="compression_ratio", aggfunc="first", observed=False)
    if baseline not in wide.columns or target not in wide.columns:
        return ""

    base = pd.to_numeric(wide[baseline], errors="coerce").replace(0, np.nan).dropna()
    cand = pd.to_numeric(wide[target], errors="coerce").replace(0, np.nan).dropna()
    paired = wide[[baseline, target]].apply(pd.to_numeric, errors="coerce").replace(0, np.nan).dropna()
    paired_gain = (paired[target] / paired[baseline] - 1.0) * 100.0 if not paired.empty else pd.Series(dtype=float)

    base_median = float(base.median()) if len(base) else math.nan
    cand_median = float(cand.median()) if len(cand) else math.nan
    base_mean = float(base.mean()) if len(base) else math.nan
    cand_mean = float(cand.mean()) if len(cand) else math.nan
    base_agg = _aggregate_ratio(section_rows, section, baseline)
    cand_agg = _aggregate_ratio(section_rows, section, target)
    target_name = SECTION_DISPLAY[target].replace("\n", " ")

    return "\n".join(
        [
            f"{target_name} vs AirdPro Default",
            f"Median CR: {base_median:.2f}x -> {cand_median:.2f}x ({_format_pct(_ratio_gain_pct(base_median, cand_median))})",
            f"Mean CR: {base_mean:.2f}x -> {cand_mean:.2f}x ({_format_pct(_ratio_gain_pct(base_mean, cand_mean))})",
            f"Aggregate CR: {base_agg:.2f}x -> {cand_agg:.2f}x ({_format_pct(_ratio_gain_pct(base_agg, cand_agg))})",
            (
                "Paired gain: "
                f"mean {_format_pct(float(paired_gain.mean()) if len(paired_gain) else math.nan)}, "
                f"median {_format_pct(float(paired_gain.median()) if len(paired_gain) else math.nan)}"
            ),
            (
                "Range: "
                f"{_format_pct(float(paired_gain.min()) if len(paired_gain) else math.nan)} to "
                f"{_format_pct(float(paired_gain.max()) if len(paired_gain) else math.nan)} "
                f"(n={len(paired_gain)})"
            ),
        ]
    )


def plot_section_boxplot(section_rows: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.6), sharex=False)
    for ax, section in zip(axes, ["MS1", "MS2"]):
        order = [label for label in _section_order(section) if label in set(section_rows.loc[section_rows["section"] == section, "label"])]
        sub = section_rows[(section_rows["section"] == section) & (section_rows["label"].isin(order))].copy()
        data = [
            pd.to_numeric(sub.loc[sub["label"] == label, "compression_ratio"], errors="coerce").replace(0, np.nan).dropna().to_numpy(float)
            for label in order
        ]
        x = np.arange(1, len(order) + 1)
        wide = sub.pivot_table(index="file", columns="label", values="compression_ratio", aggfunc="first", observed=False).reindex(columns=order)
        for sample_idx, (_, row) in enumerate(wide.iterrows()):
            y = row.to_numpy(dtype=float).copy()
            y[y <= 0] = np.nan
            valid = np.isfinite(y)
            if valid.sum() >= 2:
                ax.plot(x[valid], y[valid], linestyle=(0, (3, 3)), color="#9aa5b1", alpha=0.22, linewidth=0.75, zorder=1)
            ax.scatter(x[valid], y[valid], s=18, color="#222222", alpha=0.72, edgecolor="white", linewidth=0.3, zorder=3)
        bp = ax.boxplot(
            data,
            positions=x,
            widths=0.50,
            showfliers=False,
            patch_artist=True,
            medianprops={"color": "#111111", "linewidth": 1.4},
            boxprops={"linewidth": 1.0, "edgecolor": "#222222"},
            whiskerprops={"linewidth": 1.0, "color": "#222222"},
            capprops={"linewidth": 1.0, "color": "#222222"},
            zorder=2,
        )
        for patch, label in zip(bp["boxes"], order):
            patch.set_facecolor(SECTION_COLORS[label])
            patch.set_alpha(0.78)
        ax.set_title(f"{section} compression ratio distribution", fontsize=13.2, weight="bold", pad=10)
        ax.set_ylabel("Compression ratio (x)", fontsize=10.5)
        ax.set_xticks(x)
        ax.set_xticklabels([SECTION_DISPLAY[label] for label in order], fontsize=8.0)
        ymax = max(2.0, float(np.nanmax([np.nanmax(d) if len(d) else np.nan for d in data])) * 1.08)
        ax.set_ylim(0, ymax)
        advantage_text = _section_advantage_text(section_rows, section, order)
        if advantage_text:
            ax.text(
                0.018,
                0.968,
                advantage_text,
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=6.7,
                color="#1f2933",
                linespacing=1.17,
                bbox={"facecolor": "white", "edgecolor": "#cbd5df", "linewidth": 0.55, "alpha": 0.88, "pad": 3.0},
                zorder=6,
            )
        _style_axes(ax)
    fig.suptitle("Section-level Compression Ratio Distribution", fontsize=16, weight="bold", y=0.99)
    fig.subplots_adjust(left=0.065, right=0.995, top=0.86, bottom=0.24, wspace=0.18)
    return _save_all(fig, plots_dir / "trackcodec_section_advantage_compression_boxplot_matplotlib", formats=formats)


def plot_section_mean_bar(section_agg: pd.DataFrame, plots_dir: Path, formats: tuple[str, ...] = ("png", "pdf", "svg")) -> list[str]:
    fig, axes = plt.subplots(1, 2, figsize=(13.2, 5.3), sharey=False)
    for ax, section in zip(axes, ["MS1", "MS2"]):
        order = [label for label in _section_order(section) if label in set(section_agg.loc[section_agg["section"] == section, "label"])]
        sub = section_agg[(section_agg["section"] == section) & (section_agg["label"].isin(order))].set_index("label").loc[order].reset_index()
        x = np.arange(len(sub))
        vals = pd.to_numeric(sub["median_cr"], errors="coerce").to_numpy(float)
        bars = ax.bar(x, vals, color=[SECTION_COLORS[label] for label in sub["label"]], width=0.68)
        ymax = max(2.0, float(np.nanmax(vals)) * 1.22)
        for bar, (_, row) in zip(bars, sub.iterrows()):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                float(row["median_cr"]) + ymax * 0.018,
                f"{float(row['median_cr']):.2f}x\nn={int(row['n_files'])}",
                ha="center",
                va="bottom",
                fontsize=7.6,
            )
        ax.set_title(f"{section} median section ratio", fontsize=13.2, weight="bold", pad=10)
        ax.set_ylabel("Median compression ratio (x)", fontsize=10.5)
        ax.set_xticks(x)
        ax.set_xticklabels([SECTION_DISPLAY[label] for label in sub["label"]], fontsize=8.0)
        ax.set_ylim(0, ymax)
        _style_axes(ax)
    fig.suptitle("Section-level Median Compression Ratio", fontsize=16, weight="bold", y=0.99)
    fig.subplots_adjust(left=0.065, right=0.995, top=0.86, bottom=0.24, wspace=0.18)
    return _save_all(fig, plots_dir / "trackcodec_section_advantage_mean_bar_matplotlib", formats=formats)


def build(combined_dir: Path, only_plot: str = "", formats: tuple[str, ...] = ("png", "pdf", "svg")) -> dict[str, object]:
    tables_dir = combined_dir / "tables"
    plots_dir = combined_dir / "plots"
    plot_data_dir = combined_dir / "plot_data"
    plots_dir.mkdir(parents=True, exist_ok=True)
    plot_data_dir.mkdir(parents=True, exist_ok=True)

    whole_csv = tables_dir / "combined_per_file_methods.csv"
    section_csv = tables_dir / "trackcodec_section_advantage_per_file_methods.csv"
    section_agg_csv = tables_dir / "trackcodec_section_advantage_aggregate.csv"
    whole = pd.read_csv(whole_csv)
    section = pd.read_csv(section_csv) if section_csv.exists() else pd.DataFrame()
    section_agg = pd.read_csv(section_agg_csv) if section_agg_csv.exists() else pd.DataFrame()
    whole["compression_ratio"] = pd.to_numeric(whole["compression_ratio"], errors="coerce")
    whole["compressed_gib"] = pd.to_numeric(whole["compressed_gib"], errors="coerce")
    whole["raw_bytes"] = pd.to_numeric(whole["raw_bytes"], errors="coerce")
    whole["compressed_bytes"] = pd.to_numeric(whole["compressed_bytes"], errors="coerce")
    whole["elapsed_seconds"] = pd.to_numeric(whole["elapsed_seconds"], errors="coerce")
    agg = _aggregate(whole)
    agg.to_csv(plot_data_dir / "matplotlib_whole_file_aggregate.csv", index=False)

    jobs = {
        "combined_compression_ratio_distribution": lambda: plot_whole_distribution(whole, plots_dir, formats=formats),
        "combined_median_compression_ratio": lambda: plot_whole_mean_ratio(agg, plots_dir, formats=formats),
        "combined_total_compressed_size": lambda: plot_whole_total_size(agg, plots_dir, formats=formats),
        "combined_ratio_vs_elapsed_time": lambda: plot_ratio_vs_time(agg, plots_dir, formats=formats),
        "combined_method_file_ratio_heatmap": lambda: plot_heatmap(whole, plots_dir, formats=formats),
        "combined_method_file_status_matrix": lambda: plot_status_matrix(whole, plots_dir, formats=formats),
        "combined_multipanel_compressed_size": lambda: plot_multipanel_size(whole, plots_dir, formats=formats),
    }
    if not section.empty:
        section["compression_ratio"] = pd.to_numeric(section["compression_ratio"], errors="coerce")
        jobs["trackcodec_section_advantage_compression_boxplot"] = lambda: plot_section_boxplot(section, plots_dir, formats=formats)
    if not section_agg.empty:
        section_agg["median_cr"] = pd.to_numeric(section_agg["median_cr"], errors="coerce")
        section_agg["n_files"] = pd.to_numeric(section_agg["n_files"], errors="coerce")
        jobs["trackcodec_section_advantage_mean_bar"] = lambda: plot_section_mean_bar(section_agg, plots_dir, formats=formats)

    if only_plot:
        if only_plot not in jobs:
            raise ValueError(f"Unknown --only-plot value: {only_plot}. Available: {', '.join(jobs)}")
        selected_jobs = {only_plot: jobs[only_plot]}
    else:
        selected_jobs = jobs

    outputs: dict[str, list[str]] = {}
    for name, fn in selected_jobs.items():
        outputs[name] = fn()

    summary = {
        "combined_dir": str(combined_dir),
        "script": str(Path(__file__).resolve()),
        "whole_file_csv": str(whole_csv),
        "section_csv": str(section_csv),
        "section_aggregate_csv": str(section_agg_csv),
        "only_plot": only_plot,
        "formats": list(formats),
        "n_whole_rows": int(len(whole)),
        "n_files": int(whole["sample_id"].nunique()),
        "outputs": outputs,
    }
    (tables_dir / "matplotlib_plot_outputs_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate Matplotlib versions of the combined compression benchmark figures. "
            "Existing hand-written SVG plotting scripts are not modified or required."
        )
    )
    parser.add_argument("--combined-dir", type=Path, default=DEFAULT_COMBINED_DIR)
    parser.add_argument(
        "--only-plot",
        default="",
        help=(
            "Optional plot key to regenerate only one Matplotlib figure. "
            "Example: combined_compression_ratio_distribution."
        ),
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        default=["png", "pdf", "svg"],
        choices=["png", "pdf", "svg"],
        help="Output formats to write. Use '--formats svg' to regenerate only SVG files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build(args.combined_dir.resolve(), args.only_plot, tuple(args.formats))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
