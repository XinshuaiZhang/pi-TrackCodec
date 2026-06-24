from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["font.family"] = "Times New Roman"
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42
matplotlib.rcParams["ps.fonttype"] = 42
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, FancyBboxPatch, Patch

try:
    from scipy import stats
except Exception:  # pragma: no cover
    stats = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FULL_ROOT = ROOT / "outputs" / "search_validation_figures" / "full_downstream"
DEFAULT_OUT_ROOT = ROOT / "outputs" / "search_validation_figures"
DEFAULT_CURRENT_MANIFEST = ROOT / "outputs" / "search_validation_figures" / "tables" / "pair_manifest.csv"

LEVEL_ORDER = ["protein", "peptide", "precursor"]
LEVEL_LABEL = {"protein": "Protein", "peptide": "Peptide", "precursor": "Precursor"}
SCATTER_LEVEL_ORDER = ["precursor", "peptide", "protein"]
SCATTER_LEVEL_LABEL = {"protein": "Protein group", "peptide": "Peptide", "precursor": "Precursor"}
SCATTER_LEVEL_COLORS = {
    "precursor": "#2F5D7C",
    "peptide": "#4E9A66",
    "protein": "#C47A3C",
}
COLORS = {
    "original_only": "#376795",
    "shared": "#4E9A66",
    "reconstructed_only": "#D2872C",
    "protein": "#2F5D62",
    "peptide": "#B86B25",
    "precursor": "#526D9D",
}
AGREEMENT_HEATMAP_CMAP = LinearSegmentedColormap.from_list(
    "nmi_stability",
    ["#FFFFFF", "#E5E7EB", "#CBD5E1", "#94A3B8", "#64748B"],
)

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


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text.replace(".mzML", "")).strip("._") or "item"


def _short_label(file_name: str, limit: int = 30) -> str:
    paper_order = PAPER_FILE_ORDER.get(str(file_name))
    if paper_order is not None:
        return f"File{paper_order}"
    stem = file_name.replace(".mzML", "")
    replacements = [
        ("01625b_GA1-TUM_first_pool_1_01_01-", "TUM "),
        ("true_uncompressed", "true"),
        ("uncompressed", "uncomp"),
        ("QC_", ""),
        ("_293T_", " "),
        ("_Human_01", ""),
        ("File", "F"),
    ]
    for old, new in replacements:
        stem = stem.replace(old, new)
    return stem[: limit - 3] + "..." if len(stem) > limit else stem


def _paper_file_slug(file_name: str) -> str:
    label = _short_label(file_name, 30)
    return f"{label}_{_safe_name(file_name)}"


def _paper_sort_key(name: str) -> tuple[int, str]:
    return (PAPER_FILE_ORDER.get(str(name), 10_000), str(name))


def _ordered_unique_file_names(values: pd.Series | list[str]) -> list[str]:
    return sorted({str(v) for v in values}, key=_paper_sort_key)


def _row_index(row: pd.Series) -> int | None:
    try:
        value = row.get("index", "")
        if pd.isna(value) or str(value).strip() == "":
            return None
        return int(float(str(value)))
    except Exception:
        return None


def _index_from_pipeline_name(name: str) -> int | None:
    try:
        return int(str(name).split("_", 1)[0])
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def _save(fig: plt.Figure, stem: Path) -> list[str]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in [".png", ".pdf"]:
        path = stem.parent / f"{stem.name}{suffix}"
        fig.savefig(path, dpi=220)
        paths.append(str(path))
    plt.close(fig)
    return paths


def _unique_quality_legend_handles() -> list[Patch]:
    return [
        Patch(facecolor=COLORS["shared"], edgecolor=COLORS["shared"], alpha=0.36, label="Shared"),
        Patch(facecolor=COLORS["original_only"], edgecolor=COLORS["original_only"], alpha=0.36, label="Original only"),
        Patch(facecolor=COLORS["reconstructed_only"], edgecolor=COLORS["reconstructed_only"], alpha=0.36, label="Reconstructed only"),
    ]


def _add_block_label(fig: plt.Figure, axes: np.ndarray, row_idx: int, start_col: int, span: int, label: str, *, dy: float = 0.014, fontsize: float = 8.6) -> None:
    left = axes[row_idx, start_col].get_position()
    right = axes[row_idx, start_col + span - 1].get_position()
    x = (left.x0 + right.x1) / 2.0
    y = min(0.98, left.y1 + dy)
    fig.text(x, y, label, ha="center", va="bottom", fontsize=fontsize, fontweight="bold")


def _quantity_column(df: pd.DataFrame) -> str:
    for col in reversed(df.columns):
        values = pd.to_numeric(df[col], errors="coerce")
        if values.notna().any():
            return str(col)
    raise ValueError("No numeric quantity column found")


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()


def _summary_stats(series: pd.Series) -> dict[str, float | int]:
    values = _to_numeric(series)
    if values.empty:
        return {"n": 0, "median": math.nan, "p10": math.nan, "p25": math.nan, "p75": math.nan, "p90": math.nan, "p95": math.nan}
    return {
        "n": int(values.shape[0]),
        "median": float(values.median()),
        "p10": float(values.quantile(0.10)),
        "p25": float(values.quantile(0.25)),
        "p75": float(values.quantile(0.75)),
        "p90": float(values.quantile(0.90)),
        "p95": float(values.quantile(0.95)),
    }


def _pearson(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan"), float("nan")
    x = x[mask]
    y = y[mask]
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return float("nan"), float("nan")
    if stats is not None:
        res = stats.pearsonr(x, y)
        return float(res.statistic), float(res.pvalue)
    return float(np.corrcoef(x, y)[0, 1]), float("nan")


def _circle_overlap_area(r1: float, r2: float, distance: float) -> float:
    if distance >= r1 + r2:
        return 0.0
    if distance <= abs(r1 - r2):
        return math.pi * min(r1, r2) ** 2
    x1 = (distance**2 + r1**2 - r2**2) / (2.0 * distance * r1)
    x2 = (distance**2 + r2**2 - r1**2) / (2.0 * distance * r2)
    x1 = min(1.0, max(-1.0, x1))
    x2 = min(1.0, max(-1.0, x2))
    term = (-distance + r1 + r2) * (distance + r1 - r2) * (distance - r1 + r2) * (distance + r1 + r2)
    return r1**2 * math.acos(x1) + r2**2 * math.acos(x2) - 0.5 * math.sqrt(max(0.0, term))


def _solve_circle_distance(r1: float, r2: float, overlap_area: float) -> float:
    max_overlap = math.pi * min(r1, r2) ** 2
    if overlap_area <= 0:
        return r1 + r2
    if overlap_area >= max_overlap:
        return abs(r1 - r2)
    lo = abs(r1 - r2)
    hi = r1 + r2
    for _ in range(80):
        mid = (lo + hi) / 2.0
        area = _circle_overlap_area(r1, r2, mid)
        if area > overlap_area:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _fmt_count(value: int | float) -> str:
    return f"{int(value):,}"


def _pct(value: int | float, denom: int | float) -> float:
    return 100.0 * float(value) / float(denom) if float(denom) else float("nan")


def _draw_count_venn(
    ax: plt.Axes,
    *,
    title: str,
    original_count: int,
    reconstructed_count: int,
    shared_count: int,
    original_only_count: int,
    reconstructed_only_count: int,
    jaccard: float,
) -> None:
    union_count = shared_count + original_only_count + reconstructed_only_count
    max_radius = 0.78
    left_raw = math.sqrt(max(1, original_count) / math.pi)
    right_raw = math.sqrt(max(1, reconstructed_count) / math.pi)
    scale = max_radius / max(left_raw, right_raw)
    left_radius = left_raw * scale
    right_radius = right_raw * scale
    distance = _solve_circle_distance(left_radius, right_radius, shared_count * scale**2)
    left_center = -distance / 2.0
    right_center = distance / 2.0

    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Circle((left_center, 0), left_radius, color=COLORS["original_only"], alpha=0.24, linewidth=0))
    ax.add_patch(Circle((right_center, 0), right_radius, color=COLORS["reconstructed_only"], alpha=0.24, linewidth=0))
    shared_patch = Circle((left_center, 0), left_radius, color=COLORS["shared"], alpha=0.58, linewidth=0)
    shared_patch.set_clip_path(Circle((right_center, 0), right_radius, transform=ax.transData))
    ax.add_patch(shared_patch)
    ax.add_patch(Circle((left_center, 0), left_radius, fill=False, linewidth=1.6, ec=COLORS["original_only"], alpha=0.82))
    ax.add_patch(Circle((right_center, 0), right_radius, fill=False, linewidth=1.6, ec=COLORS["reconstructed_only"], alpha=0.82))

    x_extent = max(abs(left_center) + left_radius, abs(right_center) + right_radius, 1.05)
    y_extent = max(left_radius, right_radius, 0.78)
    ax.set_xlim(-x_extent - 0.12, x_extent + 0.12)
    ax.set_ylim(-y_extent - 0.35, y_extent + 0.33)
    ax.text(left_center - left_radius * 0.82, y_extent + 0.09, "Original", ha="center", va="center", fontsize=8.2, color=COLORS["original_only"], weight="bold")
    ax.text(right_center + right_radius * 0.82, y_extent + 0.09, "Reconstructed", ha="center", va="center", fontsize=8.2, color=COLORS["reconstructed_only"], weight="bold")
    ax.text(
        (left_center + right_center) / 2.0,
        0,
        f"Shared: {_fmt_count(shared_count)}\n{_pct(shared_count, union_count):.2f}% union\nJaccard: {jaccard:.4f}",
        ha="center",
        va="center",
        fontsize=7.0,
        color="#214F35",
        weight="bold",
        bbox=dict(boxstyle="round,pad=0.14", fc="white", ec="none", alpha=0.76),
    )
    stats_box = FancyBboxPatch(
        (0.03, -0.02),
        0.94,
        0.16,
        boxstyle="round,pad=0.01",
        transform=ax.transAxes,
        fc="white",
        ec="#777777",
        lw=0.8,
        alpha=0.95,
        clip_on=False,
    )
    ax.add_patch(stats_box)
    ax.text(
        0.06,
        0.095,
        f"Original only {_fmt_count(original_only_count)} ({_pct(original_only_count, union_count):.2f}%)",
        transform=ax.transAxes,
        fontsize=6.6,
        color=COLORS["original_only"],
        weight="bold",
    )
    ax.text(
        0.06,
        0.035,
        f"Reconstructed only {_fmt_count(reconstructed_only_count)} ({_pct(reconstructed_only_count, union_count):.2f}%)",
        transform=ax.transAxes,
        fontsize=6.6,
        color=COLORS["reconstructed_only"],
        weight="bold",
    )
    ax.set_title(title, fontsize=9.5, pad=6)


def _agreement_score(row_values: np.ndarray) -> np.ndarray:
    out = np.full(row_values.shape, np.nan, dtype=float)
    finite = np.isfinite(row_values)
    if not finite.any():
        return out
    values = row_values.astype(float)
    out[finite] = np.clip(values[finite], 0.0, 1.0)
    return out


def _plot_heatmap(matrix: pd.DataFrame, out_stem: Path, *, title: str, cmap: str = "viridis") -> list[str]:
    if matrix.empty:
        return []
    values = matrix.to_numpy(dtype=float)
    color_values = np.full(values.shape, np.nan, dtype=float)
    for i, label in enumerate(matrix.index):
        if str(label).strip():
            color_values[i, :] = _agreement_score(values[i, :])
    gap_rows = np.array([(str(label).strip() == "" and not np.isfinite(values[i]).any()) for i, label in enumerate(matrix.index)])
    groups: list[tuple[int, int]] = []
    start = 0
    for i, is_gap in enumerate(gap_rows):
        if is_gap:
            if start < i:
                groups.append((start, i))
            start = i + 1
    if start < values.shape[0]:
        groups.append((start, values.shape[0]))
    if not groups:
        groups = [(0, values.shape[0])]

    fig_w = max(8.0, 0.58 * matrix.shape[1] + 2.7)
    fig_h = max(5.2, 0.62 * sum(end - start for start, end in groups) + 1.55 + 0.75 * (len(groups) - 1))
    fig, axes = plt.subplots(
        len(groups),
        1,
        figsize=(fig_w, fig_h),
        sharex=True,
        gridspec_kw={"height_ratios": [end - start for start, end in groups], "hspace": 0.32},
        squeeze=False,
    )
    axes_flat = axes.ravel()
    cmap_obj = AGREEMENT_HEATMAP_CMAP.copy()
    cmap_obj.set_bad(color="#FFFFFF")
    im = None
    for ax, (start_row, end_row) in zip(axes_flat, groups):
        sub_values = values[start_row:end_row, :]
        sub_colors = color_values[start_row:end_row, :]
        row_labels = [str(label) for label in matrix.index[start_row:end_row]]
        group_title = row_labels[0].split(" ", 1)[0].replace("-", " ").title() if row_labels else ""
        metric_labels = [label.split(" ", 1)[1] if " " in label else label for label in row_labels]
        im = ax.imshow(np.ma.masked_invalid(sub_colors), aspect="auto", cmap=cmap_obj, vmin=0.0, vmax=1.0)
        ax.set_yticks(np.arange(len(metric_labels)))
        ax.set_yticklabels(metric_labels, fontsize=8.2)
        ax.set_title(group_title, loc="left", fontsize=9.6, fontweight="bold", pad=8, color="#111827")
        ax.set_xticks(np.arange(matrix.shape[1]))
        ax.set_xticklabels(matrix.columns, rotation=35, ha="right", fontsize=7.1)
        ax.set_xticks(np.arange(-0.5, matrix.shape[1], 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(metric_labels), 1), minor=True)
        ax.grid(which="minor", color="#F3F4F6", linewidth=0.45)
        ax.tick_params(which="minor", bottom=False, left=False)
        ax.tick_params(axis="both", which="major", length=4, width=0.8, color="#111111")
        for spine in ax.spines.values():
            spine.set_visible(True)
            spine.set_linewidth(0.72)
            spine.set_color("#1F2937")
        for i in range(sub_values.shape[0]):
            for j in range(sub_values.shape[1]):
                value = sub_values[i, j]
                if np.isfinite(value):
                    abs_value = abs(float(value))
                    if abs_value == 0:
                        text = "0"
                    elif abs_value < 1e-4:
                        text = f"{value:.1e}"
                    elif abs_value < 10:
                        text = f"{value:.4f}"
                    else:
                        text = f"{value:.2g}"
                    text_color = "#FFFFFF"
                    ax.text(j, i, text, ha="center", va="center", fontsize=6.4, color=text_color)
    axes_flat[-1].tick_params(axis="x", labelbottom=True)
    fig.suptitle(title, fontsize=12, fontweight="bold", y=0.965)
    if im is None:
        return []
    legend_ax = fig.add_axes([0.43, 0.054, 0.22, 0.024])
    gradient = np.linspace(0.0, 1.0, 128).reshape(1, -1)
    legend_ax.imshow(gradient, aspect="auto", cmap=cmap_obj, vmin=0.0, vmax=1.0)
    legend_ax.set_yticks([])
    legend_ax.set_xticks([0, 64, 127])
    legend_ax.set_xticklabels(["0", "0.5", "1.0"], fontsize=7)
    legend_ax.tick_params(axis="x", length=2.5, pad=1)
    for spine in legend_ax.spines.values():
        spine.set_linewidth(0.6)
        spine.set_color("#1F2937")
    fig.text(0.42, 0.066, "agreement score", ha="right", va="center", fontsize=7.6, color="#111827")
    fig.subplots_adjust(left=0.18, right=0.97, top=0.89, bottom=0.14)
    return _save(fig, out_stem)


def _plot_agreement_vs_count_scatter(rows: pd.DataFrame, out_stem: Path, *, title: str) -> list[str]:
    if rows.empty:
        return []
    df = rows.copy()
    for col in ["original_count", "jaccard", "pearson"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").replace([np.inf, -np.inf], np.nan)
    df = df[(df["original_count"] > 0) & (df[["jaccard", "pearson"]].notna().any(axis=1))]
    if df.empty:
        return []

    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    marker_style = {
        "jaccard": {"marker": "o", "label": "Jaccard"},
        "pearson": {"marker": "^", "label": "Pearson r"},
    }
    for level in SCATTER_LEVEL_ORDER:
        sub = df[df["level"].astype(str) == level]
        if sub.empty:
            continue
        for metric, style in marker_style.items():
            metric_sub = sub[sub[metric].notna()]
            if metric_sub.empty:
                continue
            ax.scatter(
                metric_sub["original_count"],
                metric_sub[metric],
                s=120,
                marker=style["marker"],
                color=SCATTER_LEVEL_COLORS[level],
                edgecolor="#222222",
                linewidth=0.45,
                alpha=0.76 if metric == "jaccard" else 0.58,
            )
            worst = metric_sub.loc[metric_sub[metric].idxmin()]
            ax.annotate(
                _short_label(str(worst["file"]), 18),
                xy=(float(worst["original_count"]), float(worst[metric])),
                xytext=(5, -7),
                textcoords="offset points",
                fontsize=7.4,
                color=SCATTER_LEVEL_COLORS[level],
            )

    values = df["original_count"].to_numpy(dtype=float)
    values = values[np.isfinite(values) & (values > 0)]
    ax.set_xscale("log")
    if values.size:
        ax.set_xlim(float(values.min()) / 1.45, float(values.max()) * 1.75)
    ax.set_ylim(0.0, 1.08)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_xlabel("Original identified / quantified count")
    ax.set_ylabel("Agreement score")
    ax.set_title(title, fontsize=12.5, fontweight="bold", pad=8)
    ax.grid(axis="y", color="#D7DCE0", linewidth=0.6, alpha=0.75)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#333333")
    ax.spines["bottom"].set_color("#333333")
    ax.tick_params(axis="both", which="major", length=4, width=0.8, color="#111111")
    ax.tick_params(axis="x", which="minor", length=2.8, width=0.65, color="#111111")

    level_handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="None",
            markerfacecolor=SCATTER_LEVEL_COLORS[level],
            markeredgecolor="#222222",
            markersize=8,
            label=SCATTER_LEVEL_LABEL[level],
        )
        for level in SCATTER_LEVEL_ORDER
    ]
    metric_handles = [
        Line2D(
            [0],
            [0],
            marker=style["marker"],
            linestyle="None",
            markerfacecolor="#A8A8A8",
            markeredgecolor="#222222",
            markersize=8,
            label=style["label"],
        )
        for style in marker_style.values()
    ]
    first = ax.legend(handles=level_handles, title="Level", frameon=False, loc="lower left", bbox_to_anchor=(0.01, 0.18), fontsize=8.8, title_fontsize=8.8)
    ax.add_artist(first)
    ax.legend(handles=metric_handles, title="Metric", frameon=False, loc="lower left", bbox_to_anchor=(0.01, 0.02), fontsize=8.8, title_fontsize=8.8)
    fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.15)
    return _save(fig, out_stem)


def _plot_public_agreement_from_combined_table(out_root: Path) -> list[str]:
    table = out_root / "tables" / "combined_search_molecular_recovery_plot_input.csv"
    if not table.exists():
        return []
    df = pd.read_csv(table)
    required = {"modality", "file_name", "level", "original_count", "jaccard", "quantity_pearson"}
    if not required.issubset(set(df.columns)):
        return []
    artifacts: list[str] = []
    for modality, plot_subdir, stem, title in [
        (
            "DIA",
            "diann",
            "diann_search_metric_heatmap",
            "DIA-NN molecular-level agreement versus original count",
        ),
        (
            "DDA",
            "dda_ionquant",
            "dda_ionquant_metric_heatmap",
            "MSFragger-Philosopher-IonQuant molecular-level agreement versus original count",
        ),
    ]:
        sub = df[df["modality"].astype(str) == modality].copy()
        if sub.empty:
            continue
        sub["level"] = sub["level"].astype(str).replace({"protein_group": "protein"})
        agreement = sub.rename(columns={"file_name": "file", "quantity_pearson": "pearson"})[
            ["file", "level", "original_count", "jaccard", "pearson"]
        ].copy()
        artifacts.extend(_plot_agreement_vs_count_scatter(agreement, out_root / "plots" / plot_subdir / "summary" / stem, title=title))
    return artifacts


def _coerce_search_roots(search_roots: list[Path] | tuple[Path, ...] | Path | None) -> list[Path]:
    if search_roots is None:
        return []
    if isinstance(search_roots, Path):
        return [search_roots]
    return list(search_roots)


def _find_diann_summary_roots(full_root: Path, search_roots: list[Path] | tuple[Path, ...] | Path | None) -> list[Path]:
    roots = [full_root / "diann_single_run"]
    for search_root in _coerce_search_roots(search_roots):
        if search_root and search_root.exists():
            roots.extend(sorted(search_root.glob("pipeline_runs/*/diann_single_run")))
    seen: set[str] = set()
    out = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key not in seen and (root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv").exists():
            out.append(root)
            seen.add(key)
    return out


def _read_diann_summary(full_root: Path, search_roots: list[Path] | tuple[Path, ...] | Path | None) -> pd.DataFrame:
    frames = []
    for root in _find_diann_summary_roots(full_root, search_roots):
        path = root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv"
        df = pd.read_csv(path)
        expected_index = _index_from_pipeline_name(root.parent.name) if root.parent.name != "full_downstream" else None
        if expected_index is not None and "index" in df.columns:
            df = df[df.apply(lambda row: _row_index(row) == expected_index, axis=1)]
        if df.empty:
            continue
        df["source_root"] = str(root)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["index", "file"], keep="last").sort_values("index").reset_index(drop=True)
    return df


def _apply_current_manifest(summary: pd.DataFrame, manifest: Path | None) -> pd.DataFrame:
    if summary.empty or manifest is None or not manifest.exists():
        return summary
    manifest_df = pd.read_csv(manifest)
    if "file_name" not in manifest_df.columns or "index" not in manifest_df.columns:
        return summary
    index_map = {
        str(row["file_name"]): int(row["index"])
        for _, row in manifest_df.iterrows()
        if str(row.get("file_name", "")).strip()
    }
    df = summary.copy()
    if "source_index" not in df.columns and "index" in df.columns:
        df["source_index"] = df["index"]
    df["current_index"] = df["file"].map(index_map)
    df = df[df["current_index"].notna()].copy()
    if df.empty:
        return df
    df["index"] = df["current_index"].astype(int)
    df = df.drop(columns=["current_index"])
    return df.sort_values(["index", "file"]).reset_index(drop=True)


def _load_diann_matrix(run_dir: Path, side: str, matrix_name: str, key_col: str) -> tuple[pd.DataFrame, set[str], str]:
    path = run_dir / side / matrix_name
    df = pd.read_csv(path, sep="\t", low_memory=False)
    if key_col not in df.columns:
        raise KeyError(f"Missing {key_col} in {path}")
    qty_col = _quantity_column(df)
    qty = pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0)
    ids = set(df.loc[qty > 0, key_col].astype(str))
    return df, ids, qty_col


def _diann_positive_set_from_matrix(df: pd.DataFrame, key_col: str, qty_col: str) -> set[str]:
    if key_col not in df.columns:
        return set()
    qty = pd.to_numeric(df[qty_col], errors="coerce").fillna(0.0)
    return set(df.loc[qty > 0, key_col].astype(str))


def _diann_grouped_quantities(df: pd.DataFrame, key_col: str, qty_col: str) -> pd.Series:
    if key_col not in df.columns:
        return pd.Series(dtype=float)
    work = df[[key_col, qty_col]].copy()
    work[key_col] = work[key_col].astype(str)
    work["quantity"] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0.0)
    grouped = work.groupby(key_col, sort=False)["quantity"].sum()
    return grouped[grouped > 0]


def _diann_peptide_quality_from_report(report: pd.DataFrame, peptide_key: str, ids: set[str]) -> pd.DataFrame:
    if peptide_key not in report.columns:
        return pd.DataFrame()
    df = report.loc[report[peptide_key].astype(str).isin(ids)].copy()
    if df.empty:
        return df
    df[peptide_key] = df[peptide_key].astype(str)
    agg: dict[str, tuple[str, str]] = {}
    if "Precursor.Quantity" in df.columns:
        df["peptide_quantity"] = pd.to_numeric(df["Precursor.Quantity"], errors="coerce").fillna(0.0)
        agg["peptide_quantity"] = ("peptide_quantity", "sum")
    for source_col, out_col, func in [
        ("Q.Value", "Q.Value", "min"),
        ("PEP", "PEP", "min"),
        ("Quantity.Quality", "Quantity.Quality", "max"),
        ("Evidence", "Evidence", "max"),
    ]:
        if source_col in df.columns:
            df[out_col] = pd.to_numeric(df[source_col], errors="coerce")
            agg[out_col] = (out_col, func)
    grouped = df.groupby(peptide_key, sort=False).agg(**agg).reset_index() if agg else df[[peptide_key]].drop_duplicates()
    grouped["precursor_row_count"] = df.groupby(peptide_key, sort=False).size().reindex(grouped[peptide_key]).to_numpy(dtype=int)
    if "peptide_quantity" in grouped.columns:
        grouped["log10_quantity"] = np.log10(pd.to_numeric(grouped["peptide_quantity"], errors="coerce").fillna(0.0) + 1.0)
    return grouped


def _quantified_set_metrics(original: pd.Series, reconstructed: pd.Series) -> dict[str, Any]:
    original_set = set(original.index.astype(str))
    reconstructed_set = set(reconstructed.index.astype(str))
    shared_set = original_set & reconstructed_set
    union_set = original_set | reconstructed_set
    out: dict[str, Any] = {
        "original_count": len(original_set),
        "reconstructed_count": len(reconstructed_set),
        "shared_count": len(shared_set),
        "original_only_count": len(original_set - reconstructed_set),
        "reconstructed_only_count": len(reconstructed_set - original_set),
        "jaccard": len(shared_set) / len(union_set) if union_set else float("nan"),
        "pearson": float("nan"),
        "median_abs_log2_fc": float("nan"),
        "p95_abs_log2_fc": float("nan"),
    }
    if not shared_set:
        return out
    shared = sorted(shared_set)
    x = np.log2(pd.to_numeric(original.reindex(shared), errors="coerce").fillna(0.0).to_numpy(dtype=float) + 1.0)
    y = np.log2(pd.to_numeric(reconstructed.reindex(shared), errors="coerce").fillna(0.0).to_numpy(dtype=float) + 1.0)
    pearson, _ = _pearson(x, y)
    delta = np.abs(y - x)
    out["pearson"] = pearson
    out["median_abs_log2_fc"] = float(np.nanmedian(delta)) if len(delta) else float("nan")
    out["p95_abs_log2_fc"] = float(np.nanpercentile(delta, 95)) if len(delta) else float("nan")
    return out


def _pairwise_from_quantities(original: pd.Series, reconstructed: pd.Series, *, index: int, file_name: str, level: str) -> pd.DataFrame:
    original = pd.to_numeric(original, errors="coerce").fillna(0.0)
    reconstructed = pd.to_numeric(reconstructed, errors="coerce").fillna(0.0)
    keys = sorted(set(original.index.astype(str)) | set(reconstructed.index.astype(str)))
    out = pd.DataFrame({"id": keys})
    out["original_quantity"] = pd.to_numeric(original.reindex(keys), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    out["reconstructed_quantity"] = pd.to_numeric(reconstructed.reindex(keys), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    out["file"] = file_name
    out["index"] = int(index)
    out["level"] = level
    out["log2_original"] = np.log2(out["original_quantity"].to_numpy(dtype=float) + 1.0)
    out["log2_reconstructed"] = np.log2(out["reconstructed_quantity"].to_numpy(dtype=float) + 1.0)
    out["abs_log2_delta"] = np.abs(out["log2_reconstructed"] - out["log2_original"])
    out["set_class"] = np.select(
        [
            (out["original_quantity"] > 0) & (out["reconstructed_quantity"] > 0),
            (out["original_quantity"] > 0) & (out["reconstructed_quantity"] <= 0),
            (out["original_quantity"] <= 0) & (out["reconstructed_quantity"] > 0),
        ],
        ["shared", "original_only", "reconstructed_only"],
        default="zero",
    )
    return out


def _shared_feature_summary_rows(pairs: list[pd.DataFrame]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pair in pairs:
        if pair.empty:
            continue
        shared = pair[pair["set_class"] == "shared"]
        x = shared["log2_original"].to_numpy(dtype=float)
        y = shared["log2_reconstructed"].to_numpy(dtype=float)
        r, _ = _pearson(x, y)
        union = pair[pair["set_class"].isin(["shared", "original_only", "reconstructed_only"])]
        delta = shared["abs_log2_delta"].to_numpy(dtype=float)
        rows.append(
            {
                "index": int(pair["index"].iloc[0]),
                "file": str(pair["file"].iloc[0]),
                "level": str(pair["level"].iloc[0]),
                "original_count": int(((pair["original_quantity"] > 0)).sum()),
                "reconstructed_count": int(((pair["reconstructed_quantity"] > 0)).sum()),
                "shared_count": int(shared.shape[0]),
                "original_only_count": int((pair["set_class"] == "original_only").sum()),
                "reconstructed_only_count": int((pair["set_class"] == "reconstructed_only").sum()),
                "jaccard": float(shared.shape[0] / union.shape[0]) if union.shape[0] else float("nan"),
                "shared_log2_pearson": r,
                "median_abs_log2_delta": float(np.nanmedian(delta)) if len(delta) else float("nan"),
                "p95_abs_log2_delta": float(np.nanpercentile(delta, 95)) if len(delta) else float("nan"),
            }
        )
    return rows


def _diann_run_dir(row: pd.Series) -> Path:
    source_root = Path(str(row["source_root"]))
    source_index = int(row.get("source_index", row["index"]))
    slug = f"{source_index:02d}_{_safe_name(str(row['file']))}"
    return source_root / "runs" / slug


def plot_diann_figures(summary: pd.DataFrame, out_root: Path) -> dict[str, Any]:
    artifacts: list[str] = []
    tables = out_root / "tables"
    plots = out_root / "plots" / "diann"
    if summary.empty:
        return {"diann_file_count": 0, "artifacts": artifacts}

    summary = summary.copy()
    summary["paper_order"] = summary["file"].map(lambda x: PAPER_FILE_ORDER.get(str(x), 10_000))
    summary = summary.sort_values(["paper_order", "index"]).reset_index(drop=True)
    labels = [_short_label(str(v), 22) for v in summary["file"]]
    peptide_metric_rows: list[dict[str, Any]] = []
    peptide_metric_by_index: dict[int, dict[str, Any]] = {}
    scatter_pairs: list[pd.DataFrame] = []
    for _, row in summary.iterrows():
        file_name = str(row["file"])
        try:
            run_dir = _diann_run_dir(row)
            orig_pr, _, orig_pr_qty = _load_diann_matrix(run_dir, "original", "report.pr_matrix.tsv", "Precursor.Id")
            recon_pr, _, recon_pr_qty = _load_diann_matrix(run_dir, "reconstructed", "report.pr_matrix.tsv", "Precursor.Id")
            peptide_key = "Modified.Sequence" if "Modified.Sequence" in orig_pr.columns and "Modified.Sequence" in recon_pr.columns else "Stripped.Sequence"
            precursor_original = _diann_grouped_quantities(orig_pr, "Precursor.Id", orig_pr_qty)
            precursor_reconstructed = _diann_grouped_quantities(recon_pr, "Precursor.Id", recon_pr_qty)
            peptide_original = _diann_grouped_quantities(orig_pr, peptide_key, orig_pr_qty)
            peptide_reconstructed = _diann_grouped_quantities(recon_pr, peptide_key, recon_pr_qty)
            metrics = _quantified_set_metrics(
                peptide_original,
                peptide_reconstructed,
            )
            orig_pg, _, orig_pg_qty = _load_diann_matrix(run_dir, "original", "report.pg_matrix.tsv", "Protein.Group")
            recon_pg, _, recon_pg_qty = _load_diann_matrix(run_dir, "reconstructed", "report.pg_matrix.tsv", "Protein.Group")
            protein_original = _diann_grouped_quantities(orig_pg, "Protein.Group", orig_pg_qty)
            protein_reconstructed = _diann_grouped_quantities(recon_pg, "Protein.Group", recon_pg_qty)
            scatter_pairs.extend(
                [
                    _pairwise_from_quantities(protein_original, protein_reconstructed, index=int(row["index"]), file_name=file_name, level="protein"),
                    _pairwise_from_quantities(peptide_original, peptide_reconstructed, index=int(row["index"]), file_name=file_name, level="peptide"),
                    _pairwise_from_quantities(precursor_original, precursor_reconstructed, index=int(row["index"]), file_name=file_name, level="precursor"),
                ]
            )
            metrics.update({"index": row["index"], "file": file_name, "peptide_key": peptide_key, "status": "ok"})
        except Exception as exc:
            metrics = {"index": row["index"], "file": file_name, "status": "error", "error": str(exc)}
        peptide_metric_rows.append(metrics)
        try:
            peptide_metric_by_index[int(row["index"])] = metrics
        except Exception:
            pass

    agreement_rows: list[dict[str, Any]] = []
    for _, row in summary.iterrows():
        index = int(row["index"])
        file_name = str(row["file"])
        peptide_metrics = peptide_metric_by_index.get(index, {})
        agreement_rows.extend(
            [
                {
                    "index": index,
                    "file": file_name,
                    "level": "precursor",
                    "original_count": row.get("original_quantified_precursors"),
                    "jaccard": row.get("quantified_precursor_jaccard"),
                    "pearson": row.get("precursor_quant_log2_pearson"),
                },
                {
                    "index": index,
                    "file": file_name,
                    "level": "peptide",
                    "original_count": peptide_metrics.get("original_count", math.nan),
                    "jaccard": peptide_metrics.get("jaccard", math.nan),
                    "pearson": peptide_metrics.get("pearson", math.nan),
                },
                {
                    "index": index,
                    "file": file_name,
                    "level": "protein",
                    "original_count": row.get("original_quantified_protein_groups"),
                    "jaccard": row.get("quantified_protein_group_jaccard"),
                    "pearson": row.get("protein_quant_log2_pearson"),
                },
            ]
        )
    agreement_df = pd.DataFrame(agreement_rows)
    artifacts.extend(_plot_agreement_vs_count_scatter(agreement_df, plots / "summary" / "diann_search_metric_heatmap", title="DIA-NN molecular-level agreement versus original count"))
    _write_csv(peptide_metric_rows, tables / "diann_peptide_quant_summary.csv")
    artifacts.append(str(tables / "diann_peptide_quant_summary.csv"))
    if scatter_pairs:
        scatter_summary_rows = _shared_feature_summary_rows(scatter_pairs)
        _write_csv(scatter_summary_rows, tables / "diann_shared_feature_scatter_summary.csv")
        artifacts.append(str(tables / "diann_shared_feature_scatter_summary.csv"))
        artifacts.extend(
            _plot_shared_feature_scatter_overview(
                scatter_pairs,
                plots / "scatter",
                title="DIA-NN shared-feature scatter overview",
                stem="diann_shared_feature_scatter_overview",
            )
        )
        artifacts.extend(
            _plot_combined_shared_feature_scatter(
                scatter_pairs,
                plots / "scatter",
                title="Combined shared-feature DIA-NN correlation",
                stem="diann_combined_shared_feature_scatter",
            )
        )
        scatter_summary = pd.DataFrame(scatter_summary_rows)
        worst_file = ""
        if not scatter_summary.empty and "shared_log2_pearson" in scatter_summary.columns:
            protein_rows = scatter_summary[scatter_summary["level"].astype(str) == "protein"].copy()
            protein_rows["shared_log2_pearson"] = pd.to_numeric(protein_rows["shared_log2_pearson"], errors="coerce")
            protein_rows = protein_rows[np.isfinite(protein_rows["shared_log2_pearson"])]
            if not protein_rows.empty:
                worst_file = str(protein_rows.sort_values("shared_log2_pearson").iloc[0]["file"])
        if worst_file:
            artifacts.extend(
                _plot_combined_shared_feature_scatter(
                    [p for p in scatter_pairs if str(p["file"].iloc[0]) != worst_file],
                    plots / "scatter",
                    title="Combined shared-feature DIA-NN correlation (excluding worst file)",
                    stem="diann_combined_shared_feature_scatter_excluding_worst",
                )
            )
            artifacts.extend(
                _plot_combined_shared_feature_scatter(
                    [p for p in scatter_pairs if str(p["file"].iloc[0]) == worst_file],
                    plots / "scatter",
                    title="Combined shared-feature DIA-NN correlation (worst file only)",
                    stem="diann_combined_shared_feature_scatter_worst_only",
                )
            )

    venn_rows: list[dict[str, Any]] = []
    precursor_quality_rows: list[dict[str, Any]] = []
    peptide_quality_rows: list[dict[str, Any]] = []
    protein_quality_rows: list[dict[str, Any]] = []
    precursor_examples: list[dict[str, Any]] = []
    peptide_examples: list[dict[str, Any]] = []
    protein_examples: list[dict[str, Any]] = []
    for _, row in summary.iterrows():
        run_dir = _diann_run_dir(row)
        file_name = str(row["file"])
        slug = _paper_file_slug(file_name)
        precursor_plot_df: pd.DataFrame | None = None
        protein_plot_df: pd.DataFrame | None = None
        try:
            orig_pr, orig_pr_set, _ = _load_diann_matrix(run_dir, "original", "report.pr_matrix.tsv", "Precursor.Id")
            recon_pr, recon_pr_set, _ = _load_diann_matrix(run_dir, "reconstructed", "report.pr_matrix.tsv", "Precursor.Id")
            orig_pg, orig_pg_set, orig_pg_qty = _load_diann_matrix(run_dir, "original", "report.pg_matrix.tsv", "Protein.Group")
            recon_pg, recon_pg_set, recon_pg_qty = _load_diann_matrix(run_dir, "reconstructed", "report.pg_matrix.tsv", "Protein.Group")
        except Exception as exc:
            venn_rows.append({"index": row["index"], "file": file_name, "status": "missing_diann_outputs", "error": str(exc)})
            continue

        peptide_key = "Modified.Sequence" if "Modified.Sequence" in orig_pr.columns and "Modified.Sequence" in recon_pr.columns else "Stripped.Sequence"
        orig_pr_qty = _quantity_column(orig_pr)
        recon_pr_qty = _quantity_column(recon_pr)
        orig_pep_set = _diann_positive_set_from_matrix(orig_pr, peptide_key, orig_pr_qty)
        recon_pep_set = _diann_positive_set_from_matrix(recon_pr, peptide_key, recon_pr_qty)
        level_sets = [
            ("precursor", orig_pr_set, recon_pr_set),
            ("peptide", orig_pep_set, recon_pep_set),
            ("protein_group", orig_pg_set, recon_pg_set),
        ]
        fig, axes = plt.subplots(1, 3, figsize=(18.0, 5.0))
        for ax, (level, left, right) in zip(axes, level_sets):
            shared = left & right
            left_only = left - right
            right_only = right - left
            union = left | right
            jaccard = len(shared) / len(union) if union else float("nan")
            _draw_count_venn(
                ax,
                title=f"{level.replace('_', ' ').title()} quantified set",
                original_count=len(left),
                reconstructed_count=len(right),
                shared_count=len(shared),
                original_only_count=len(left_only),
                reconstructed_only_count=len(right_only),
                jaccard=jaccard,
            )
            venn_rows.append(
                {
                    "index": row["index"],
                    "file": file_name,
                    "level": level,
                    "original_count": len(left),
                    "reconstructed_count": len(right),
                    "shared_count": len(shared),
                    "original_only_count": len(left_only),
                    "reconstructed_only_count": len(right_only),
                    "jaccard": jaccard,
                    "status": "ok",
                }
            )
        fig.suptitle(f"DIA-NN quantified-set overlap: {_short_label(file_name, 54)}", fontsize=12, fontweight="bold")
        fig.subplots_adjust(left=0.04, right=0.99, top=0.84, bottom=0.10, wspace=0.16)
        artifacts.extend(_save(fig, plots / "venn" / f"{slug}.diann_quantified_set_venn"))

        try:
            reports = {
                "original": pd.read_parquet(run_dir / "original" / "report.parquet"),
                "reconstructed": pd.read_parquet(run_dir / "reconstructed" / "report.parquet"),
            }
        except Exception as exc:
            precursor_quality_rows.append({"index": row["index"], "file": file_name, "status": "missing_report_parquet", "error": str(exc)})
            continue

        for side in ["original", "reconstructed"]:
            reports[side]["Precursor.Id"] = reports[side]["Precursor.Id"].astype(str)
            reports[side][peptide_key] = reports[side][peptide_key].astype(str)
            reports[side]["Protein.Group"] = reports[side]["Protein.Group"].astype(str)

        precursor_categories = {
            "shared_original": ("original", orig_pr_set & recon_pr_set),
            "original_only": ("original", orig_pr_set - recon_pr_set),
            "shared_reconstructed": ("reconstructed", orig_pr_set & recon_pr_set),
            "reconstructed_only": ("reconstructed", recon_pr_set - orig_pr_set),
        }
        precursor_frames = []
        for category, (side, ids) in precursor_categories.items():
            df = reports[side].loc[reports[side]["Precursor.Id"].isin(ids)].copy()
            if "Precursor.Quantity" in df.columns:
                df["log10_quantity"] = np.log10(pd.to_numeric(df["Precursor.Quantity"], errors="coerce").fillna(0.0) + 1.0)
            df["category"] = category
            precursor_frames.append(df)
            qrow: dict[str, Any] = {"index": row["index"], "file": file_name, "category": category, "side": side, "set_id_count": len(ids), "report_row_count": int(df.shape[0]), "status": "ok"}
            for metric in ["log10_quantity", "Q.Value", "PEP", "Quantity.Quality", "Evidence", "Ms1.Profile.Corr"]:
                if metric in df.columns:
                    for stat_name, value in _summary_stats(df[metric]).items():
                        qrow[f"{metric}_{stat_name}"] = value
            precursor_quality_rows.append(qrow)
            if category in {"original_only", "reconstructed_only"} and not df.empty:
                sort_cols = [col for col in ["Q.Value", "Precursor.Quantity"] if col in df.columns]
                examples = df.sort_values(sort_cols, ascending=[False, True][: len(sort_cols)]).head(80) if sort_cols else df.head(80)
                keep_cols = [c for c in ["Precursor.Id", "Protein.Group", "Genes", "Stripped.Sequence", "Precursor.Charge", "Precursor.Mz", "RT", "Precursor.Quantity", "Q.Value", "PEP", "Quantity.Quality", "Evidence", "Ms1.Profile.Corr"] if c in examples.columns]
                for _, rec in examples[keep_cols].iterrows():
                    precursor_examples.append({"index": row["index"], "file": file_name, "category": category, "side": side, **rec.to_dict()})
        if precursor_frames:
            precursor_plot_df = pd.concat(precursor_frames, ignore_index=True)
            artifacts.extend(_plot_category_boxplots(precursor_plot_df, plots / "unique_quality" / f"{slug}.diann_unique_precursor_quality_boxplot", title=f"{_short_label(file_name, 48)} DIA-NN unique precursor quality", metrics=[("log10_quantity", "log10(quantity + 1)"), ("Q.Value", "Q.Value"), ("PEP", "PEP"), ("Quantity.Quality", "Quantity.Quality"), ("Evidence", "Evidence")]))

        peptide_categories = {
            "shared_original": ("original", orig_pep_set & recon_pep_set),
            "original_only": ("original", orig_pep_set - recon_pep_set),
            "shared_reconstructed": ("reconstructed", orig_pep_set & recon_pep_set),
            "reconstructed_only": ("reconstructed", recon_pep_set - orig_pep_set),
        }
        peptide_frames = []
        for category, (side, ids) in peptide_categories.items():
            df = _diann_peptide_quality_from_report(reports[side], peptide_key, ids)
            if df.empty:
                continue
            df["category"] = category
            peptide_frames.append(df)
            qrow = {"index": row["index"], "file": file_name, "category": category, "side": side, "set_id_count": len(ids), "peptide_row_count": int(df.shape[0]), "peptide_key": peptide_key, "status": "ok"}
            for metric in ["log10_quantity", "peptide_quantity", "Q.Value", "PEP", "Quantity.Quality", "Evidence", "precursor_row_count"]:
                if metric in df.columns:
                    for stat_name, value in _summary_stats(df[metric]).items():
                        qrow[f"{metric}_{stat_name}"] = value
            peptide_quality_rows.append(qrow)
            if category in {"original_only", "reconstructed_only"} and not df.empty:
                sort_cols = [col for col in ["Q.Value", "peptide_quantity"] if col in df.columns]
                examples = df.sort_values(sort_cols, ascending=[False, True][: len(sort_cols)]).head(80) if sort_cols else df.head(80)
                keep_cols = [c for c in [peptide_key, "peptide_quantity", "log10_quantity", "Q.Value", "PEP", "Quantity.Quality", "Evidence", "precursor_row_count"] if c in examples.columns]
                for _, rec in examples[keep_cols].iterrows():
                    peptide_examples.append({"index": row["index"], "file": file_name, "category": category, "side": side, **rec.to_dict()})
        peptide_plot_df: pd.DataFrame | None = None
        if peptide_frames:
            peptide_plot_df = pd.concat(peptide_frames, ignore_index=True)
            artifacts.extend(_plot_category_boxplots(peptide_plot_df, plots / "unique_quality" / f"{slug}.diann_unique_peptide_quality_boxplot", title=f"{_short_label(file_name, 48)} DIA-NN unique peptide quality", metrics=[("log10_quantity", "log10(quantity + 1)"), ("Q.Value", "Q.Value"), ("PEP", "PEP"), ("Quantity.Quality", "Quantity.Quality"), ("Evidence", "Evidence")]))

        protein_categories = {
            "shared_original": ("original", orig_pg_set & recon_pg_set, orig_pg, orig_pg_qty),
            "original_only": ("original", orig_pg_set - recon_pg_set, orig_pg, orig_pg_qty),
            "shared_reconstructed": ("reconstructed", orig_pg_set & recon_pg_set, recon_pg, recon_pg_qty),
            "reconstructed_only": ("reconstructed", recon_pg_set - orig_pg_set, recon_pg, recon_pg_qty),
        }
        protein_frames = []
        for category, (side, ids, matrix, qty_col) in protein_categories.items():
            df = matrix.loc[matrix["Protein.Group"].astype(str).isin(ids)].copy()
            df["protein_quantity"] = pd.to_numeric(df[qty_col], errors="coerce")
            df["log10_quantity"] = np.log10(df["protein_quantity"].fillna(0.0) + 1.0)
            df["category"] = category
            protein_frames.append(df)
            qrow = {"index": row["index"], "file": file_name, "category": category, "side": side, "set_id_count": len(ids), "matrix_row_count": int(df.shape[0]), "quantity_column": qty_col, "status": "ok"}
            for metric in ["log10_quantity", "protein_quantity", "N.Sequences", "N.Proteotypic.Sequences"]:
                if metric in df.columns:
                    for stat_name, value in _summary_stats(df[metric]).items():
                        qrow[f"{metric}_{stat_name}"] = value
            protein_quality_rows.append(qrow)
            if category in {"original_only", "reconstructed_only"} and not df.empty:
                sort_cols = [col for col in ["N.Sequences", "protein_quantity"] if col in df.columns]
                examples = df.sort_values(sort_cols, ascending=True).head(80) if sort_cols else df.head(80)
                keep_cols = [c for c in ["Protein.Group", "Protein.Names", "Genes", "First.Protein.Description", "N.Sequences", "N.Proteotypic.Sequences", "protein_quantity"] if c in examples.columns]
                for _, rec in examples[keep_cols].iterrows():
                    protein_examples.append({"index": row["index"], "file": file_name, "category": category, "side": side, **rec.to_dict()})
        if protein_frames:
            protein_plot_df = pd.concat(protein_frames, ignore_index=True)
            artifacts.extend(_plot_category_boxplots(protein_plot_df, plots / "unique_quality" / f"{slug}.diann_unique_protein_support_boxplot", title=f"{_short_label(file_name, 48)} DIA-NN unique protein support", metrics=[("log10_quantity", "log10(quantity + 1)"), ("N.Sequences", "N.Sequences"), ("N.Proteotypic.Sequences", "N.Proteotypic.Sequences")]))
        if precursor_plot_df is not None and protein_plot_df is not None:
            artifacts.extend(_plot_diann_combined_unique_quality(precursor_plot_df, peptide_plot_df, protein_plot_df, plots / "unique_quality" / f"{slug}.diann_unique_precursor_peptide_protein_quality_boxplot", title=f"{_short_label(file_name, 54)} DIA-NN unique precursor/peptide/protein quality"))

    artifacts.extend(_plot_diann_venn_overview(venn_rows, plots / "venn"))
    _write_csv(venn_rows, tables / "diann_quantified_set_venn_summary.csv")
    _write_csv(precursor_quality_rows, tables / "diann_unique_precursor_quality_summary.csv")
    _write_csv(peptide_quality_rows, tables / "diann_unique_peptide_quality_summary.csv")
    _write_csv(protein_quality_rows, tables / "diann_unique_protein_quality_summary.csv")
    _write_csv(precursor_examples, tables / "diann_unique_precursor_examples_near_cutoff.csv")
    _write_csv(peptide_examples, tables / "diann_unique_peptide_examples_near_cutoff.csv")
    _write_csv(protein_examples, tables / "diann_unique_protein_examples_low_support.csv")
    artifacts.extend([str(tables / "diann_quantified_set_venn_summary.csv"), str(tables / "diann_unique_precursor_quality_summary.csv"), str(tables / "diann_unique_peptide_quality_summary.csv"), str(tables / "diann_unique_protein_quality_summary.csv")])
    return {"diann_file_count": int(summary["file"].nunique()), "artifacts": artifacts}


def plot_diann_shared_feature_scatter_only(summary: pd.DataFrame, out_root: Path) -> dict[str, Any]:
    artifacts: list[str] = []
    tables = out_root / "tables"
    plots = out_root / "plots" / "diann"
    if summary.empty:
        return {"diann_file_count": 0, "artifacts": artifacts}
    summary = summary.copy()
    summary["paper_order"] = summary["file"].map(lambda x: PAPER_FILE_ORDER.get(str(x), 10_000))
    summary = summary.sort_values(["paper_order", "index"]).reset_index(drop=True)
    scatter_pairs: list[pd.DataFrame] = []
    error_rows: list[dict[str, Any]] = []
    for _, row in summary.iterrows():
        file_name = str(row["file"])
        try:
            run_dir = _diann_run_dir(row)
            orig_pr, _, orig_pr_qty = _load_diann_matrix(run_dir, "original", "report.pr_matrix.tsv", "Precursor.Id")
            recon_pr, _, recon_pr_qty = _load_diann_matrix(run_dir, "reconstructed", "report.pr_matrix.tsv", "Precursor.Id")
            peptide_key = "Modified.Sequence" if "Modified.Sequence" in orig_pr.columns and "Modified.Sequence" in recon_pr.columns else "Stripped.Sequence"
            orig_pg, _, orig_pg_qty = _load_diann_matrix(run_dir, "original", "report.pg_matrix.tsv", "Protein.Group")
            recon_pg, _, recon_pg_qty = _load_diann_matrix(run_dir, "reconstructed", "report.pg_matrix.tsv", "Protein.Group")
            scatter_pairs.extend(
                [
                    _pairwise_from_quantities(_diann_grouped_quantities(orig_pg, "Protein.Group", orig_pg_qty), _diann_grouped_quantities(recon_pg, "Protein.Group", recon_pg_qty), index=int(row["index"]), file_name=file_name, level="protein"),
                    _pairwise_from_quantities(_diann_grouped_quantities(orig_pr, peptide_key, orig_pr_qty), _diann_grouped_quantities(recon_pr, peptide_key, recon_pr_qty), index=int(row["index"]), file_name=file_name, level="peptide"),
                    _pairwise_from_quantities(_diann_grouped_quantities(orig_pr, "Precursor.Id", orig_pr_qty), _diann_grouped_quantities(recon_pr, "Precursor.Id", recon_pr_qty), index=int(row["index"]), file_name=file_name, level="precursor"),
                ]
            )
        except Exception as exc:
            error_rows.append({"index": row.get("index"), "file": file_name, "error": str(exc)})
    if error_rows:
        _write_csv(error_rows, tables / "diann_shared_feature_scatter_errors.csv")
        artifacts.append(str(tables / "diann_shared_feature_scatter_errors.csv"))
    if not scatter_pairs:
        return {"diann_file_count": int(summary["file"].nunique()), "artifacts": artifacts}
    scatter_summary_rows = _shared_feature_summary_rows(scatter_pairs)
    _write_csv(scatter_summary_rows, tables / "diann_shared_feature_scatter_summary.csv")
    artifacts.append(str(tables / "diann_shared_feature_scatter_summary.csv"))
    artifacts.extend(_plot_shared_feature_scatter_overview(scatter_pairs, plots / "scatter", title="DIA-NN shared-feature scatter overview", stem="diann_shared_feature_scatter_overview"))
    artifacts.extend(_plot_combined_shared_feature_scatter(scatter_pairs, plots / "scatter", title="Combined shared-feature DIA-NN correlation", stem="diann_combined_shared_feature_scatter"))
    scatter_summary = pd.DataFrame(scatter_summary_rows)
    protein_rows = scatter_summary[scatter_summary["level"].astype(str) == "protein"].copy()
    protein_rows["shared_log2_pearson"] = pd.to_numeric(protein_rows["shared_log2_pearson"], errors="coerce")
    protein_rows = protein_rows[np.isfinite(protein_rows["shared_log2_pearson"])]
    if not protein_rows.empty:
        worst_file = str(protein_rows.sort_values("shared_log2_pearson").iloc[0]["file"])
        artifacts.extend(_plot_combined_shared_feature_scatter([p for p in scatter_pairs if str(p["file"].iloc[0]) != worst_file], plots / "scatter", title="Combined shared-feature DIA-NN correlation (excluding worst file)", stem="diann_combined_shared_feature_scatter_excluding_worst"))
        artifacts.extend(_plot_combined_shared_feature_scatter([p for p in scatter_pairs if str(p["file"].iloc[0]) == worst_file], plots / "scatter", title="Combined shared-feature DIA-NN correlation (worst file only)", stem="diann_combined_shared_feature_scatter_worst_only"))
    return {"diann_file_count": int(summary["file"].nunique()), "artifacts": artifacts}


def _plot_category_boxplots(df: pd.DataFrame, stem: Path, *, title: str, metrics: list[tuple[str, str]]) -> list[str]:
    categories = ["shared_original", "original_only", "shared_reconstructed", "reconstructed_only"]
    colors = [COLORS["shared"], COLORS["original_only"], COLORS["shared"], COLORS["reconstructed_only"]]
    metrics = [(col, label) for col, label in metrics if col in df.columns]
    if not metrics:
        return []
    cols = min(3, len(metrics))
    rows = int(math.ceil(len(metrics) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 3.9 * rows), squeeze=False)
    for ax, (metric, ylabel) in zip(axes.ravel(), metrics):
        values = []
        patch_colors = []
        for category, color in zip(categories, colors):
            vals = _to_numeric(df.loc[df["category"] == category, metric])
            if vals.empty:
                continue
            values.append(vals.to_numpy(dtype=float))
            patch_colors.append(color)
        if not values:
            ax.axis("off")
            continue
        box = ax.boxplot(values, patch_artist=True, showfliers=False)
        for patch, color in zip(box["boxes"], patch_colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.36)
            patch.set_edgecolor(color)
        for median in box["medians"]:
            median.set_color("#111111")
        ax.set_ylabel(ylabel)
        ax.tick_params(axis="x", bottom=False, labelbottom=False)
        ax.grid(axis="y", alpha=0.25)
    for ax in axes.ravel()[len(metrics) :]:
        ax.axis("off")
    fig.suptitle(title, fontsize=12, fontweight="bold", y=0.98)
    fig.legend(handles=_unique_quality_legend_handles(), loc="upper center", bbox_to_anchor=(0.5, 0.935), ncol=3, frameon=False, fontsize=8.6)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.80, bottom=0.11, wspace=0.28, hspace=0.38)
    return _save(fig, stem)


def _draw_category_boxplot_panel(ax: plt.Axes, df: pd.DataFrame, metric: str, ylabel: str) -> None:
    categories = ["shared_original", "original_only", "shared_reconstructed", "reconstructed_only"]
    colors = [COLORS["shared"], COLORS["original_only"], COLORS["shared"], COLORS["reconstructed_only"]]
    values = []
    patch_colors = []
    for category, color in zip(categories, colors):
        vals = _to_numeric(df.loc[df["category"] == category, metric]) if metric in df.columns else pd.Series(dtype=float)
        if vals.empty:
            continue
        values.append(vals.to_numpy(dtype=float))
        patch_colors.append(color)
    if not values:
        ax.axis("off")
        return
    box = ax.boxplot(values, patch_artist=True, showfliers=False)
    for patch, color in zip(box["boxes"], patch_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.36)
        patch.set_edgecolor(color)
    for median in box["medians"]:
        median.set_color("#111111")
    ax.set_ylabel(ylabel, fontsize=8.0)
    ax.tick_params(axis="x", bottom=False, labelbottom=False)
    ax.tick_params(axis="y", labelsize=7.2)
    ax.grid(axis="y", alpha=0.25)


def _plot_diann_combined_unique_quality(precursor_df: pd.DataFrame, peptide_df: pd.DataFrame | None, protein_df: pd.DataFrame, stem: Path, *, title: str) -> list[str]:
    precursor_metrics = [
        ("log10_quantity", "Precursor\nlog10 qty"),
        ("Q.Value", "Precursor\nQ.Value"),
        ("PEP", "Precursor\nPEP"),
        ("Quantity.Quality", "Precursor\nQty quality"),
        ("Evidence", "Precursor\nEvidence"),
    ]
    peptide_metrics = [
        ("log10_quantity", "Peptide\nlog10 qty"),
        ("Q.Value", "Peptide\nQ.Value"),
        ("PEP", "Peptide\nPEP"),
        ("Quantity.Quality", "Peptide\nQty quality"),
        ("Evidence", "Peptide\nEvidence"),
    ]
    protein_metrics = [
        ("log10_quantity", "Protein\nlog10 qty"),
        ("N.Sequences", "Protein\nN.Sequences"),
        ("N.Proteotypic.Sequences", "Protein\nProteotypic seq"),
    ]
    precursor_metrics = [(col, label) for col, label in precursor_metrics if col in precursor_df.columns]
    peptide_metrics = [(col, label) for col, label in peptide_metrics if peptide_df is not None and col in peptide_df.columns]
    protein_metrics = [(col, label) for col, label in protein_metrics if col in protein_df.columns]
    if not precursor_metrics and not peptide_metrics and not protein_metrics:
        return []
    cols = max(len(precursor_metrics), len(peptide_metrics), len(protein_metrics), 1)
    fig, axes = plt.subplots(3, cols, figsize=(3.4 * cols, 10.4), squeeze=False)
    for col_idx, (metric, ylabel) in enumerate(precursor_metrics):
        _draw_category_boxplot_panel(axes[0, col_idx], precursor_df, metric, ylabel)
    for col_idx in range(len(precursor_metrics), cols):
        axes[0, col_idx].axis("off")
    for col_idx, (metric, ylabel) in enumerate(peptide_metrics):
        _draw_category_boxplot_panel(axes[1, col_idx], peptide_df if peptide_df is not None else pd.DataFrame(), metric, ylabel)
    for col_idx in range(len(peptide_metrics), cols):
        axes[1, col_idx].axis("off")
    for col_idx, (metric, ylabel) in enumerate(protein_metrics):
        _draw_category_boxplot_panel(axes[2, col_idx], protein_df, metric, ylabel)
    for col_idx in range(len(protein_metrics), cols):
        axes[2, col_idx].axis("off")
    fig.suptitle(title, fontsize=12, fontweight="bold", y=0.985)
    fig.legend(handles=_unique_quality_legend_handles(), loc="upper center", bbox_to_anchor=(0.5, 0.955), ncol=3, frameon=False, fontsize=8.8)
    fig.subplots_adjust(left=0.08, right=0.99, top=0.90, bottom=0.07, wspace=0.36, hspace=0.44)
    return _save(fig, stem)


def _plot_diann_venn_overview(venn_rows: list[dict[str, Any]], out_dir: Path) -> list[str]:
    df = pd.DataFrame([row for row in venn_rows if row.get("status") == "ok"])
    if df.empty:
        return []
    files = _ordered_unique_file_names(df["file"].astype(str).tolist())
    levels = ["precursor", "peptide", "protein_group"]
    level_labels = {"precursor": "Precursor", "peptide": "Peptide", "protein_group": "Protein group"}
    files_per_row = 2
    file_rows = int(math.ceil(len(files) / files_per_row))
    cols = files_per_row * len(levels)
    fig_h = max(5.2, 3.1 * file_rows)
    fig, axes = plt.subplots(file_rows, cols, figsize=(20.0, fig_h), squeeze=False)
    fig.subplots_adjust(left=0.045, right=0.99, top=0.88, bottom=0.075, wspace=0.16, hspace=0.44)
    for file_idx, file_name in enumerate(files):
        row_idx = file_idx // files_per_row
        block_idx = file_idx % files_per_row
        start_col = block_idx * len(levels)
        _add_block_label(fig, axes, row_idx, start_col, len(levels), _short_label(file_name, 42), dy=0.020, fontsize=8.8)
        for col_idx, level in enumerate(levels):
            ax = axes[row_idx, start_col + col_idx]
            match = df[(df["file"].astype(str) == file_name) & (df["level"].astype(str) == level)]
            if match.empty:
                ax.axis("off")
                continue
            r = match.iloc[0]
            _draw_count_venn(
                ax,
                title=level_labels[level],
                original_count=int(r["original_count"]),
                reconstructed_count=int(r["reconstructed_count"]),
                shared_count=int(r["shared_count"]),
                original_only_count=int(r["original_only_count"]),
                reconstructed_only_count=int(r["reconstructed_only_count"]),
                jaccard=float(r["jaccard"]),
            )
    for empty_idx in range(len(files), file_rows * files_per_row):
        row_idx = empty_idx // files_per_row
        block_idx = empty_idx % files_per_row
        start_col = block_idx * len(levels)
        for col_idx in range(len(levels)):
            axes[row_idx, start_col + col_idx].axis("off")
    fig.suptitle("DIA-NN original/reconstructed quantified-set overlap", fontsize=13, fontweight="bold")
    return _save(fig, out_dir / "diann_quantified_set_venn_overview")


def _plot_shared_feature_scatter_overview(pairs: list[pd.DataFrame], out_dir: Path, *, title: str, stem: str) -> list[str]:
    files = _ordered_unique_file_names([str(p["file"].iloc[0]) for p in pairs])
    files_per_row = 2
    file_rows = int(math.ceil(len(files) / files_per_row))
    cols = files_per_row * len(LEVEL_ORDER)
    fig_h = max(5.0, 2.55 * file_rows)
    fig, axes = plt.subplots(file_rows, cols, figsize=(21.0, fig_h), squeeze=False)
    fig.subplots_adjust(left=0.08, right=0.975, top=0.93, bottom=0.055, wspace=0.34, hspace=0.34)
    last_hb = None
    for file_idx, file_name in enumerate(files):
        row_idx = file_idx // files_per_row
        block_idx = file_idx % files_per_row
        start_col = block_idx * len(LEVEL_ORDER)
        label_dy = 0.026 if row_idx == 0 else 0.006
        _add_block_label(fig, axes, row_idx, start_col, len(LEVEL_ORDER), _short_label(file_name, 44), dy=label_dy, fontsize=8.0)
        for col_idx, level in enumerate(LEVEL_ORDER):
            ax = axes[row_idx, start_col + col_idx]
            matches = [p for p in pairs if str(p["file"].iloc[0]) == file_name and str(p["level"].iloc[0]) == level]
            if not matches:
                ax.axis("off")
                continue
            df = matches[0]
            shared = df[df["set_class"] == "shared"].copy()
            if shared.empty:
                ax.text(0.5, 0.5, "No shared entries", ha="center", va="center", transform=ax.transAxes)
                ax.axis("off")
                continue
            if len(shared) > 60000:
                shared = shared.sample(n=60000, random_state=42)
            x = shared["log2_original"].to_numpy(dtype=float)
            y = shared["log2_reconstructed"].to_numpy(dtype=float)
            r, _ = _pearson(x, y)
            lo = float(np.nanmin([np.nanmin(x), np.nanmin(y)]))
            hi = float(np.nanmax([np.nanmax(x), np.nanmax(y)]))
            pad = max((hi - lo) * 0.045, 0.2)
            last_hb = ax.hexbin(x, y, gridsize=45, mincnt=1, bins="log", cmap="viridis", linewidths=0)
            ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color="#222222", linestyle="--", linewidth=0.75)
            ax.set_xlim(lo - pad, hi + pad)
            ax.set_ylim(lo - pad, hi + pad)
            ax.tick_params(axis="both", labelsize=6.2, length=2.5, pad=1.5)
            if row_idx == 0:
                ax.set_title(LEVEL_LABEL[level], fontsize=10.0, fontweight="bold")
            if row_idx == file_rows - 1:
                ax.set_xlabel("Original log2(quantity + 1)", fontsize=7.3)
            if col_idx == 0:
                ax.set_ylabel("Reconstructed\nlog2(quantity + 1)", fontsize=7.3)
            ax.text(0.035, 0.955, f"n={len(shared):,}\nr={r:.6f}", transform=ax.transAxes, ha="left", va="top", fontsize=6.4, bbox={"boxstyle": "round,pad=0.14", "facecolor": "white", "alpha": 0.84, "linewidth": 0.45})
    for empty_idx in range(len(files), file_rows * files_per_row):
        row_idx = empty_idx // files_per_row
        block_idx = empty_idx % files_per_row
        for col_idx in range(len(LEVEL_ORDER)):
            axes[row_idx, block_idx * len(LEVEL_ORDER) + col_idx].axis("off")
    if last_hb is not None:
        cbar = fig.colorbar(last_hb, ax=axes.ravel().tolist(), fraction=0.018, pad=0.012)
        cbar.ax.tick_params(labelsize=7)
        cbar.set_label("log10 count", fontsize=8)
    fig.suptitle(title, fontsize=13, fontweight="bold")
    return _save(fig, out_dir / stem)


def _plot_combined_shared_feature_scatter(
    pairs: list[pd.DataFrame],
    out_dir: Path,
    *,
    title: str,
    stem: str,
    sample_n: int = 90000,
) -> list[str]:
    if not pairs:
        return []
    fig, axes = plt.subplots(1, len(LEVEL_ORDER), figsize=(15.2, 4.8))
    if len(LEVEL_ORDER) == 1:
        axes = np.array([axes])
    artifacts: list[str] = []
    for ax, level in zip(axes, LEVEL_ORDER):
        level_pairs = [p for p in pairs if str(p["level"].iloc[0]) == level]
        if not level_pairs:
            ax.axis("off")
            continue
        df = pd.concat(level_pairs, ignore_index=True)
        shared = df[df["set_class"] == "shared"].copy()
        if shared.empty:
            ax.axis("off")
            continue
        if len(shared) > sample_n:
            shared = shared.sample(n=sample_n, random_state=42)
        x = shared["log2_original"].to_numpy(dtype=float)
        y = shared["log2_reconstructed"].to_numpy(dtype=float)
        r, _ = _pearson(x, y)
        lo = float(np.nanmin([np.nanmin(x), np.nanmin(y)]))
        hi = float(np.nanmax([np.nanmax(x), np.nanmax(y)]))
        pad = max((hi - lo) * 0.04, 0.2)
        hb = ax.hexbin(x, y, gridsize=70, mincnt=1, bins="log", cmap="viridis")
        ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color="black", linestyle="--", linewidth=1.0)
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_ylim(lo - pad, hi + pad)
        ax.set_title(LEVEL_LABEL[level], fontsize=11)
        ax.set_xlabel("Original log2(quantity + 1)")
        ax.text(0.04, 0.96, f"n={len(shared):,}\nPearson r={r:.6f}", transform=ax.transAxes, ha="left", va="top", fontsize=8, bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.88, "linewidth": 0.5})
        if ax is axes[0]:
            ax.set_ylabel("Reconstructed log2(quantity + 1)")
        fig.colorbar(hb, ax=ax, fraction=0.046, pad=0.02, label="log10 count")
    fig.suptitle(title, fontsize=13, fontweight="bold")
    fig.subplots_adjust(left=0.07, right=0.96, top=0.84, bottom=0.14, wspace=0.32)
    artifacts.extend(_save(fig, out_dir / stem))
    return artifacts


def _find_dda_summary_roots(full_root: Path, search_roots: list[Path] | tuple[Path, ...] | Path | None) -> list[Path]:
    roots = [full_root / "dda_msfragger_ionquant"]
    for search_root in _coerce_search_roots(search_roots):
        if search_root and search_root.exists():
            roots.extend(sorted(search_root.glob("pipeline_runs/*/dda_msfragger_ionquant")))
    seen: set[str] = set()
    out = []
    for root in roots:
        key = str(root.resolve()) if root.exists() else str(root)
        if key not in seen and (root / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv").exists():
            out.append(root)
            seen.add(key)
    return out


def _read_dda_summary(full_root: Path, search_roots: list[Path] | tuple[Path, ...] | Path | None) -> pd.DataFrame:
    frames = []
    for root in _find_dda_summary_roots(full_root, search_roots):
        path = root / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv"
        if path.stat().st_size <= 0:
            continue
        try:
            df = pd.read_csv(path)
        except pd.errors.EmptyDataError:
            continue
        expected_index = _index_from_pipeline_name(root.parent.name) if root.parent.name != "full_downstream" else None
        if expected_index is not None and "index" in df.columns:
            df = df[df.apply(lambda row: _row_index(row) == expected_index, axis=1)]
        if df.empty:
            continue
        df["source_root"] = str(root)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["index", "file", "level"], keep="last").reset_index(drop=True)
    for col in ["original_nonzero_count", "reconstructed_nonzero_count", "shared_nonzero_count"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def _load_dda_pair(row: pd.Series) -> pd.DataFrame:
    pairwise_text = str(row.get("pairwise_table", "") or "").strip()
    path = Path(pairwise_text) if pairwise_text else Path("__missing_pairwise_table__")
    if not path.is_file():
        source_index = int(row.get("source_index", row["index"]))
        slug = f"{source_index:02d}_{_safe_name(str(row['file']))}"
        path = Path(str(row["source_root"])) / "paired_tables" / f"{slug}.{row['level']}.ionquant_pairwise.tsv"
    df = pd.read_csv(path, sep="\t", low_memory=False)
    df["file"] = row["file"]
    df["index"] = int(row["index"])
    df["level"] = row["level"]
    df["log2_original"] = np.log2(pd.to_numeric(df["original_quantity"], errors="coerce").fillna(0.0) + 1.0)
    df["log2_reconstructed"] = np.log2(pd.to_numeric(df["reconstructed_quantity"], errors="coerce").fillna(0.0) + 1.0)
    df["abs_log2_delta"] = np.abs(df["log2_reconstructed"] - df["log2_original"])
    df["set_class"] = np.select(
        [
            (df["original_quantity"] > 0) & (df["reconstructed_quantity"] > 0),
            (df["original_quantity"] > 0) & (df["reconstructed_quantity"] <= 0),
            (df["original_quantity"] <= 0) & (df["reconstructed_quantity"] > 0),
        ],
        ["shared", "original_only", "reconstructed_only"],
        default="zero",
    )
    return df


def plot_dda_figures(summary: pd.DataFrame, out_root: Path) -> dict[str, Any]:
    artifacts: list[str] = []
    tables = out_root / "tables"
    plots = out_root / "plots" / "dda_ionquant"
    if summary.empty:
        return {"dda_file_count": 0, "artifacts": artifacts}

    summary = summary.copy()
    empty_mask = (summary.get("original_nonzero_count", 0) <= 0) & (summary.get("reconstructed_nonzero_count", 0) <= 0)
    if bool(empty_mask.any()):
        excluded = summary.loc[empty_mask].copy()
        excluded["exclusion_reason"] = "zero_ionquant_quantification"
        excluded_path = tables / "dda_ionquant_zero_or_empty_rows_excluded_from_plots.csv"
        _write_csv(excluded.to_dict("records"), excluded_path)
        artifacts.append(str(excluded_path))
        summary = summary.loc[~empty_mask].copy()
    if summary.empty:
        return {"dda_file_count": 0, "dda_pairwise_rows": 0, "artifacts": artifacts}
    summary["level"] = pd.Categorical(summary["level"], categories=LEVEL_ORDER, ordered=True)
    summary["paper_order"] = summary["file"].map(lambda x: PAPER_FILE_ORDER.get(str(x), 10_000))
    summary = summary.sort_values(["paper_order", "index", "level"]).reset_index(drop=True)
    files = _ordered_unique_file_names(summary["file"].astype(str).tolist())
    labels = [_short_label(file, 22) for file in files]

    agreement_df = summary.rename(
        columns={
            "original_nonzero_count": "original_count",
            "shared_log2_pearson": "pearson",
        }
    )[["index", "file", "level", "original_count", "jaccard", "pearson"]].copy()
    artifacts.extend(
        _plot_agreement_vs_count_scatter(
            agreement_df,
            plots / "summary" / "dda_ionquant_metric_heatmap",
            title="MSFragger-Philosopher-IonQuant molecular-level agreement versus original count",
        )
    )

    enriched_rows: list[dict[str, Any]] = []
    top_delta_rows: list[dict[str, Any]] = []
    pairs: list[pd.DataFrame] = []
    for _, row in summary.iterrows():
        try:
            pair = _load_dda_pair(row)
        except Exception as exc:
            enriched = row.to_dict()
            enriched["status"] = "missing_pairwise_table"
            enriched["error"] = str(exc)
            enriched_rows.append(enriched)
            continue
        pairs.append(pair)
        shared = pair[pair["set_class"] == "shared"].copy()
        abs_delta = shared["abs_log2_delta"].to_numpy(dtype=float)
        enriched = row.to_dict()
        enriched["status"] = "ok"
        enriched["p99_abs_log2_delta"] = float(np.nanpercentile(abs_delta, 99)) if len(abs_delta) else float("nan")
        enriched["p999_abs_log2_delta"] = float(np.nanpercentile(abs_delta, 99.9)) if len(abs_delta) else float("nan")
        enriched["nonzero_delta_count"] = int(np.sum(abs_delta > 1e-9))
        enriched["nonzero_delta_fraction"] = float(np.mean(abs_delta > 1e-9)) if len(abs_delta) else float("nan")
        union = float(row["shared_nonzero_count"] + row["original_only_count"] + row["reconstructed_only_count"])
        enriched["original_only_rate"] = float(row["original_only_count"] / union) if union else float("nan")
        enriched["reconstructed_only_rate"] = float(row["reconstructed_only_count"] / union) if union else float("nan")
        enriched_rows.append(enriched)
        for _, outlier in shared.nlargest(min(25, len(shared)), "abs_log2_delta").iterrows():
            top_delta_rows.append(
                {
                    "index": row["index"],
                    "file": row["file"],
                    "level": row["level"],
                    "id": outlier["id"],
                    "original_quantity": float(outlier["original_quantity"]),
                    "reconstructed_quantity": float(outlier["reconstructed_quantity"]),
                    "log2_original": float(outlier["log2_original"]),
                    "log2_reconstructed": float(outlier["log2_reconstructed"]),
                    "abs_log2_delta": float(outlier["abs_log2_delta"]),
                }
            )
    _write_csv(enriched_rows, tables / "dda_ionquant_enriched_pairwise_summary.csv")
    _write_csv(top_delta_rows, tables / "dda_ionquant_top_abs_log2_delta_rows.csv")
    artifacts.extend([str(tables / "dda_ionquant_enriched_pairwise_summary.csv"), str(tables / "dda_ionquant_top_abs_log2_delta_rows.csv")])

    artifacts.extend(_plot_dda_venn_overview(summary, plots / "venn"))
    if pairs:
        artifacts.extend(_plot_dda_scatter_overview(pairs, plots / "scatter"))
        artifacts.extend(_plot_dda_combined_scatter(pairs, plots / "scatter"))
        artifacts.extend(_plot_dda_delta_tail(pd.DataFrame(enriched_rows), plots / "summary"))
    return {"dda_file_count": int(summary["file"].nunique()), "dda_pairwise_rows": int(len(summary)), "artifacts": artifacts}


def _plot_dda_venn_overview(summary: pd.DataFrame, out_dir: Path) -> list[str]:
    files = _ordered_unique_file_names(summary["file"].astype(str).tolist())
    if not files:
        return []
    files_per_row = 2
    file_rows = int(math.ceil(len(files) / files_per_row))
    cols = files_per_row * len(LEVEL_ORDER)
    fig_h = max(5.8, 3.0 * file_rows)
    fig, axes = plt.subplots(file_rows, cols, figsize=(22.0, fig_h), squeeze=False)
    fig.subplots_adjust(left=0.045, right=0.99, top=0.90, bottom=0.055, wspace=0.14, hspace=0.46)
    for file_idx, file_name in enumerate(files):
        row_idx = file_idx // files_per_row
        block_idx = file_idx % files_per_row
        start_col = block_idx * len(LEVEL_ORDER)
        _add_block_label(fig, axes, row_idx, start_col, len(LEVEL_ORDER), _short_label(file_name, 44), dy=0.020, fontsize=8.8)
        for col_idx, level in enumerate(LEVEL_ORDER):
            ax = axes[row_idx, start_col + col_idx]
            row = summary[(summary["file"].astype(str) == file_name) & (summary["level"].astype(str) == level)]
            if row.empty:
                ax.axis("off")
                continue
            r = row.iloc[0]
            _draw_count_venn(
                ax,
                title=LEVEL_LABEL[level] if row_idx == 0 else "",
                original_count=int(r["original_nonzero_count"]),
                reconstructed_count=int(r["reconstructed_nonzero_count"]),
                shared_count=int(r["shared_nonzero_count"]),
                original_only_count=int(r["original_only_count"]),
                reconstructed_only_count=int(r["reconstructed_only_count"]),
                jaccard=float(r["jaccard"]),
            )
    for empty_idx in range(len(files), file_rows * files_per_row):
        row_idx = empty_idx // files_per_row
        block_idx = empty_idx % files_per_row
        start_col = block_idx * len(LEVEL_ORDER)
        for col_idx in range(len(LEVEL_ORDER)):
            axes[row_idx, start_col + col_idx].axis("off")
    fig.suptitle("DDA IonQuant original/reconstructed quantified-set overlap", fontsize=13, fontweight="bold")
    return _save(fig, out_dir / "dda_ionquant_quantified_set_venn_overview")


def _plot_dda_scatter_overview(pairs: list[pd.DataFrame], out_dir: Path) -> list[str]:
    return _plot_shared_feature_scatter_overview(
        pairs,
        out_dir,
        title="DDA IonQuant shared-feature scatter overview",
        stem="dda_ionquant_shared_feature_scatter_overview",
    )


def _plot_dda_combined_scatter(pairs: list[pd.DataFrame], out_dir: Path) -> list[str]:
    worst_file = "QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML"
    configs = [
        ("excluding_worst", [p for p in pairs if str(p["file"].iloc[0]) != worst_file], "Combined shared-feature IonQuant correlation (excluding worst file)", "dda_ionquant_combined_shared_feature_scatter_excluding_worst"),
        ("worst_only", [p for p in pairs if str(p["file"].iloc[0]) == worst_file], "Combined shared-feature IonQuant correlation (worst file only)", "dda_ionquant_combined_shared_feature_scatter_worst_only"),
    ]
    artifacts: list[str] = []
    for _, subset_pairs, title, stem in configs:
        artifacts.extend(_plot_combined_shared_feature_scatter(subset_pairs, out_dir, title=title, stem=stem))
    return artifacts


def _plot_dda_delta_tail(enriched: pd.DataFrame, out_dir: Path) -> list[str]:
    if enriched.empty or "p999_abs_log2_delta" not in enriched.columns:
        return []
    files = _ordered_unique_file_names(enriched["file"].astype(str).tolist())
    labels = [_short_label(f, 20) for f in files]
    fig, axes = plt.subplots(len(LEVEL_ORDER), 1, figsize=(max(10.0, 0.75 * len(files) + 4.0), 8.8), sharex=True)
    for ax, level in zip(axes, LEVEL_ORDER):
        sub = enriched[enriched["level"].astype(str) == level].set_index("file").reindex(files)
        x = np.arange(len(files))
        for col, label, marker in [("p95_abs_log2_delta", "p95", "o"), ("p99_abs_log2_delta", "p99", "s"), ("p999_abs_log2_delta", "p99.9", "^"), ("max_abs_log2_delta", "max", "D")]:
            if col in sub.columns:
                ax.plot(x, pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=float), marker=marker, linewidth=1.5, label=label)
        ax.set_yscale("symlog", linthresh=1e-6)
        ax.set_ylabel(f"{LEVEL_LABEL[level]}\nabs log2 delta")
        ax.grid(axis="y", alpha=0.28)
    axes[0].legend(ncol=4, loc="upper center", bbox_to_anchor=(0.5, 1.35), frameon=False)
    axes[-1].set_xticks(np.arange(len(files)))
    axes[-1].set_xticklabels(labels, rotation=35, ha="right", fontsize=8)
    fig.suptitle("DDA shared-feature quantitative error tail", fontsize=13, fontweight="bold")
    fig.subplots_adjust(left=0.08, right=0.98, top=0.88, bottom=0.18, hspace=0.10)
    return _save(fig, out_dir / "dda_ionquant_abs_log2_delta_tail_quantiles")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate downstream search-validation figures for TrackCodec outputs.")
    parser.add_argument("--full-root", type=Path, default=DEFAULT_FULL_ROOT)
    parser.add_argument("--all20-root", type=Path, default=ROOT / "results" / "search_validation_all20")
    parser.add_argument("--search-root", type=Path, action="append", default=[], help="Additional search-validation root containing pipeline_runs; can be repeated.")
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--current-manifest", type=Path, default=DEFAULT_CURRENT_MANIFEST, help="Optional pair manifest used to remap output indices to the current validation file order.")
    parser.add_argument("--diann-scatter-only", action="store_true", help="Regenerate only DIA-NN shared-feature scatter plots from the published input table.")
    parser.add_argument("--diann-only", action="store_true", help="Regenerate only DIA-NN plots from the published or discovered DIA-NN summary table.")
    args = parser.parse_args()

    args.out_root.mkdir(parents=True, exist_ok=True)
    if args.diann_scatter_only:
        diann_input = args.out_root / "tables" / "diann_search_summary_input.csv"
        diann_summary = pd.read_csv(diann_input) if diann_input.exists() else pd.DataFrame()
        results = {
            "out_root": str(args.out_root),
            "diann_scatter_only": plot_diann_shared_feature_scatter_only(diann_summary, args.out_root),
        }
        print(json.dumps(_json_safe(results), indent=2), flush=True)
        return

    search_roots = [args.all20_root, *args.search_root]
    diann_summary = _read_diann_summary(args.full_root, search_roots)
    dda_summary = _read_dda_summary(args.full_root, search_roots)
    diann_summary = _apply_current_manifest(diann_summary, args.current_manifest)
    dda_summary = _apply_current_manifest(dda_summary, args.current_manifest)
    if diann_summary.empty:
        diann_input = args.out_root / "tables" / "diann_search_summary_input.csv"
        if diann_input.exists():
            diann_summary = pd.read_csv(diann_input)
    if dda_summary.empty:
        dda_input = args.out_root / "tables" / "dda_ionquant_pairwise_summary_input.csv"
        if dda_input.exists():
            dda_summary = pd.read_csv(dda_input)
    if not diann_summary.empty:
        _write_csv(diann_summary.to_dict("records"), args.out_root / "tables" / "diann_search_summary_input.csv")
    if not dda_summary.empty:
        _write_csv(dda_summary.to_dict("records"), args.out_root / "tables" / "dda_ionquant_pairwise_summary_input.csv")

    if args.diann_only:
        results = {
            "out_root": str(args.out_root),
            "search_roots": [str(path) for path in search_roots],
            "diann": plot_diann_figures(diann_summary, args.out_root),
        }
        print(json.dumps(_json_safe(results), indent=2), flush=True)
        return

    fallback_artifacts: list[str] = []
    if diann_summary.empty and dda_summary.empty:
        fallback_artifacts = _plot_public_agreement_from_combined_table(args.out_root)

    results = {
        "out_root": str(args.out_root),
        "search_roots": [str(path) for path in search_roots],
        "diann": plot_diann_figures(diann_summary, args.out_root),
        "dda_ionquant": plot_dda_figures(dda_summary, args.out_root),
        "public_summary_fallback_artifacts": fallback_artifacts,
    }
    manifest_path = args.out_root / "plot_manifest.json"
    manifest_path.write_text(json.dumps(_json_safe(results), indent=2), encoding="utf-8")
    print(json.dumps(_json_safe(results), indent=2), flush=True)


if __name__ == "__main__":
    main()
