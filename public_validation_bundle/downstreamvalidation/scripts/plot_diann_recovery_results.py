from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE_ROOT = ROOT / "outputs" / "search_validation_figures" / "tables"
DEFAULT_OUT_ROOT = ROOT / "outputs" / "search_validation_figures"

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

COLORS = {
    "blue": "#2F5D7C",
    "green": "#4E9A66",
    "orange": "#C47A3C",
    "red": "#B84A4A",
    "purple": "#6F5C8F",
    "gray": "#6F7782",
    "light_gray": "#E8ECEF",
    "text": "#202124",
}
RECOVERY_YMAX = 1.02

LEVELS = [
    ("precursor", "Precursor", COLORS["blue"]),
    ("peptide", "Peptide", COLORS["green"]),
    ("protein_group", "Protein group", COLORS["orange"]),
]
RECOVERY_CMAP = LinearSegmentedColormap.from_list("recovery_gray", ["#FFFFFF", "#CBD5E1", "#64748B"])


def _save(fig: plt.Figure, stem: Path) -> list[str]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in [".png", ".pdf"]:
        path = stem.parent / f"{stem.name}{suffix}"
        fig.savefig(path, dpi=240, bbox_inches="tight")
        paths.append(str(path))
    plt.close(fig)
    return paths


def _as_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _label(file_name: str) -> str:
    order = PAPER_FILE_ORDER.get(str(file_name))
    if order is not None:
        return f"File{order}"
    return str(file_name).replace(".mzML", "")[:18]


def _order(file_name: str) -> int:
    return PAPER_FILE_ORDER.get(str(file_name), 10_000)


def _style_axes(ax: plt.Axes, grid_axis: str = "y") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#333333")
    ax.spines["bottom"].set_color("#333333")
    ax.tick_params(colors=COLORS["text"], labelsize=8)
    ax.grid(axis=grid_axis, color="#D7DCE0", linewidth=0.6, alpha=0.75)


def _load_recovery_table(table_root: Path) -> pd.DataFrame:
    precursor = pd.read_csv(table_root / "diann_precursor_recovery_summary.csv")
    peptide = pd.read_csv(table_root / "diann_peptide_quant_summary.csv")
    protein = pd.read_csv(table_root / "diann_search_summary_input.csv")

    rows: list[dict[str, Any]] = []
    for _, row in precursor.iterrows():
        original = float(row.get("original_precursor_count", math.nan))
        shared = float(row.get("shared_precursor_count", math.nan))
        reconstructed = float(row.get("reconstructed_precursor_count", math.nan))
        rows.append(
            {
                "index": row.get("index"),
                "source_index": row.get("source_index"),
                "file_name": row.get("file_name"),
                "level": "precursor",
                "status": row.get("status", "ok"),
                "original_count": original,
                "reconstructed_count": reconstructed,
                "shared_count": shared,
                "gain_count": row.get("precursor_gain_count"),
                "loss_count": row.get("precursor_loss_count"),
                "recovery_rate": row.get("precursor_recovery_rate"),
                "jaccard": shared / (original + reconstructed - shared) if original + reconstructed - shared > 0 else math.nan,
                "quantity_pearson": row.get("precursor_quantity_pearson"),
                "p95_abs_log2_delta": row.get("precursor_quantity_p95_abs_delta"),
                "q_value_pearson": row.get("q_value_pearson"),
                "rt_pearson": row.get("rt_pearson"),
            }
        )
    for _, row in peptide.iterrows():
        original = float(row.get("original_count", math.nan))
        shared = float(row.get("shared_count", math.nan))
        reconstructed = float(row.get("reconstructed_count", math.nan))
        rows.append(
            {
                "index": row.get("index"),
                "source_index": row.get("source_index", math.nan),
                "file_name": row.get("file"),
                "level": "peptide",
                "status": row.get("status", "ok"),
                "original_count": original,
                "reconstructed_count": reconstructed,
                "shared_count": shared,
                "gain_count": row.get("reconstructed_only_count"),
                "loss_count": row.get("original_only_count"),
                "recovery_rate": shared / original if original > 0 else math.nan,
                "jaccard": row.get("jaccard"),
                "quantity_pearson": row.get("pearson"),
                "p95_abs_log2_delta": row.get("p95_abs_log2_fc"),
                "q_value_pearson": math.nan,
                "rt_pearson": math.nan,
            }
        )
    for _, row in protein.iterrows():
        original = float(row.get("original_quantified_protein_groups", math.nan))
        shared = float(row.get("shared_quantified_protein_groups", math.nan))
        reconstructed = float(row.get("reconstructed_quantified_protein_groups", math.nan))
        rows.append(
            {
                "index": row.get("index"),
                "source_index": row.get("source_index", math.nan),
                "file_name": row.get("file"),
                "level": "protein_group",
                "status": "ok",
                "original_count": original,
                "reconstructed_count": reconstructed,
                "shared_count": shared,
                "gain_count": reconstructed - shared if np.isfinite(reconstructed) and np.isfinite(shared) else math.nan,
                "loss_count": original - shared if np.isfinite(original) and np.isfinite(shared) else math.nan,
                "recovery_rate": shared / original if original > 0 else math.nan,
                "jaccard": row.get("quantified_protein_group_jaccard"),
                "quantity_pearson": row.get("protein_quant_log2_pearson"),
                "p95_abs_log2_delta": row.get("protein_abs_log2_fc_p95"),
                "q_value_pearson": math.nan,
                "rt_pearson": math.nan,
            }
        )

    out = pd.DataFrame(rows)
    for col in [
        "original_count",
        "reconstructed_count",
        "shared_count",
        "gain_count",
        "loss_count",
        "recovery_rate",
        "jaccard",
        "quantity_pearson",
        "p95_abs_log2_delta",
        "q_value_pearson",
        "rt_pearson",
    ]:
        out[col] = _as_float(out[col])
    out["paper_order"] = out["file_name"].map(_order)
    out["file_label"] = out["file_name"].map(_label)
    return out.sort_values(["paper_order", "level"]).reset_index(drop=True)


def _wide(df: pd.DataFrame, value_col: str) -> tuple[pd.DataFrame, list[str]]:
    files = (
        df[["file_name", "paper_order"]]
        .drop_duplicates()
        .sort_values(["paper_order", "file_name"])["file_name"]
        .astype(str)
        .tolist()
    )
    labels = [_label(file) for file in files]
    rows = []
    for level, _, _ in LEVELS:
        sub = df[df["level"] == level].set_index("file_name")
        rows.append([float(sub.loc[file, value_col]) if file in sub.index else math.nan for file in files])
    return pd.DataFrame(rows, index=[label for _, label, _ in LEVELS], columns=labels), files


def _plot_recovery_heatmap(df: pd.DataFrame, out_dir: Path) -> list[str]:
    heat, _ = _wide(df, "recovery_rate")
    fig, ax = plt.subplots(figsize=(max(7.8, 0.54 * heat.shape[1] + 2.0), 2.85))
    cmap = RECOVERY_CMAP.copy()
    cmap.set_bad(color="#F0F2F4")
    im = ax.imshow(np.ma.masked_invalid(heat.to_numpy(dtype=float)), aspect="auto", cmap=cmap, vmin=0.0, vmax=1.0)
    ax.set_xticks(np.arange(heat.shape[1]))
    ax.set_xticklabels(heat.columns, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(heat.shape[0]))
    ax.set_yticklabels(heat.index, fontsize=9)
    values = heat.to_numpy(dtype=float)
    for i in range(values.shape[0]):
        for j in range(values.shape[1]):
            value = values[i, j]
            if np.isfinite(value):
                ax.text(j, i, f"{value:.4f}", ha="center", va="center", fontsize=6.8, color="white" if value >= 0.55 else "#111111")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.8)
    cbar = fig.colorbar(im, ax=ax, fraction=0.030, pad=0.012)
    cbar.set_label("Recovery rate", fontsize=8)
    cbar.set_ticks(np.linspace(0.0, 1.0, 6))
    cbar.ax.tick_params(labelsize=7)
    ax.set_title(
        f"DIA-NN precursor, peptide and protein-group recovery ({heat.shape[1]} files)",
        fontsize=11.5,
        weight="bold",
        pad=8,
    )
    fig.subplots_adjust(left=0.14, right=0.96, top=0.82, bottom=0.34)
    return _save(fig, out_dir / "diann_precursor_peptide_proteingroup_recovery_heatmap")


def _plot_gain_loss(df: pd.DataFrame, out_dir: Path) -> list[str]:
    files = (
        df[["file_name", "paper_order"]]
        .drop_duplicates()
        .sort_values(["paper_order", "file_name"])["file_name"]
        .astype(str)
        .tolist()
    )
    labels = [_label(file) for file in files]
    fig, axes = plt.subplots(3, 1, figsize=(max(8.2, 0.52 * len(files) + 2.2), 7.0), sharex=True)
    for ax, (level, label, color) in zip(axes, LEVELS):
        sub = df[df["level"] == level].set_index("file_name")
        gain = np.array([float(sub.loc[file, "gain_count"]) if file in sub.index else math.nan for file in files])
        loss = np.array([float(sub.loc[file, "loss_count"]) if file in sub.index else math.nan for file in files])
        x = np.arange(len(files))
        ax.bar(x, gain, width=0.68, color=COLORS["orange"], label="Gain")
        ax.bar(x, -loss, width=0.68, color=color, label="Loss")
        ax.axhline(0, color="#222222", linewidth=0.8)
        ax.set_ylabel(label)
        ax.set_title(label, loc="left", fontsize=9.8, weight="bold")
        _style_axes(ax)
    axes[0].legend(frameon=False, ncol=2, loc="upper right")
    axes[-1].set_xticks(np.arange(len(files)))
    axes[-1].set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    fig.suptitle("DIA-NN gain/loss by molecular level", fontsize=12.0, weight="bold")
    fig.subplots_adjust(left=0.12, right=0.98, top=0.92, bottom=0.16, hspace=0.38)
    return _save(fig, out_dir / "diann_precursor_peptide_proteingroup_gain_loss_barplot")


def _plot_recovery_vs_count(df: pd.DataFrame, out_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(6.2, 4.35))
    for level, label, color in LEVELS:
        sub = df[df["level"] == level].copy()
        ax.scatter(
            sub["original_count"],
            sub["recovery_rate"],
            s=np.clip(np.sqrt(sub["original_count"].fillna(0).to_numpy(dtype=float)) * 0.45, 34, 170),
            color=color,
            edgecolor="#222222",
            linewidth=0.35,
            alpha=0.80,
            label=label,
        )
        worst = sub.dropna(subset=["recovery_rate"]).nsmallest(1, "recovery_rate")
        for _, row in worst.iterrows():
            ax.text(float(row["original_count"]) * 1.04, float(row["recovery_rate"]), row["file_label"], fontsize=7.3, va="center", color=color)
    ax.set_xscale("log")
    ax.set_ylim(0.0, RECOVERY_YMAX)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_xlabel("Original identified / quantified count")
    ax.set_ylabel("Recovery rate")
    ax.set_title("DIA-NN recovery versus original count", fontsize=11.5, weight="bold", pad=8)
    ax.legend(frameon=False, fontsize=8)
    _style_axes(ax)
    fig.subplots_adjust(left=0.12, right=0.97, top=0.87, bottom=0.16)
    return _save(fig, out_dir / "diann_recovery_vs_original_count_scatter")


def _row_normalized(values: np.ndarray) -> np.ndarray:
    out = values.copy().astype(float)
    for i in range(out.shape[0]):
        finite = np.isfinite(out[i, :])
        if not finite.any():
            continue
        lo = float(np.nanmin(out[i, finite]))
        hi = float(np.nanmax(out[i, finite]))
        if math.isclose(lo, hi):
            out[i, finite] = 0.5
        else:
            out[i, finite] = (out[i, finite] - lo) / (hi - lo)
    return out


def _plot_agreement_heatmap(df: pd.DataFrame, out_dir: Path) -> list[str]:
    files = (
        df[["file_name", "paper_order"]]
        .drop_duplicates()
        .sort_values(["paper_order", "file_name"])["file_name"]
        .astype(str)
        .tolist()
    )
    labels = [_label(file) for file in files]
    metric_specs = [
        ("precursor", "quantity_pearson", "Precursor quantity r", "{:.4f}"),
        ("peptide", "quantity_pearson", "Peptide quantity r", "{:.4f}"),
        ("protein_group", "quantity_pearson", "Protein-group quantity r", "{:.4f}"),
        ("precursor", "p95_abs_log2_delta", "Precursor p95 abs log2FC", "{:.3g}"),
        ("peptide", "p95_abs_log2_delta", "Peptide p95 abs log2FC", "{:.3g}"),
        ("protein_group", "p95_abs_log2_delta", "Protein-group p95 abs log2FC", "{:.3g}"),
        ("precursor", "q_value_pearson", "Precursor Q.Value r", "{:.4f}"),
        ("precursor", "rt_pearson", "Precursor RT r", "{:.4f}"),
    ]
    raw_rows = []
    for level, metric, _, _ in metric_specs:
        sub = df[df["level"] == level].set_index("file_name")
        raw_rows.append([float(sub.loc[file, metric]) if file in sub.index else math.nan for file in files])
    raw = np.array(raw_rows, dtype=float)
    color_values = _row_normalized(raw)
    fig, ax = plt.subplots(figsize=(max(8.4, 0.58 * len(files) + 2.8), 5.3))
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad(color="#F0F2F4")
    im = ax.imshow(np.ma.masked_invalid(color_values), aspect="auto", cmap=cmap, vmin=0.0, vmax=1.0)
    ax.set_xticks(np.arange(len(files)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(metric_specs)))
    ax.set_yticklabels([label for _, _, label, _ in metric_specs], fontsize=8)
    for i, (_, _, _, fmt) in enumerate(metric_specs):
        for j in range(raw.shape[1]):
            value = raw[i, j]
            if np.isfinite(value):
                ax.text(j, i, fmt.format(value), ha="center", va="center", fontsize=6.1, color="white" if color_values[i, j] < 0.42 else "#111111")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.8)
    cbar = fig.colorbar(im, ax=ax, fraction=0.026, pad=0.012)
    cbar.set_label("Row-normalized value", fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    ax.set_title("DIA-NN recovery-associated agreement metrics", fontsize=11.5, weight="bold", pad=8)
    fig.subplots_adjust(left=0.22, right=0.97, top=0.88, bottom=0.28)
    return _save(fig, out_dir / "diann_recovery_agreement_heatmap")


def _plot_overview(df: pd.DataFrame, out_dir: Path) -> list[str]:
    files = (
        df[["file_name", "paper_order"]]
        .drop_duplicates()
        .sort_values(["paper_order", "file_name"])["file_name"]
        .astype(str)
        .tolist()
    )
    labels = [_label(file) for file in files]
    fig = plt.figure(figsize=(11.5, 7.6))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.2, 1.0], hspace=0.42, wspace=0.30)
    ax1 = fig.add_subplot(gs[0, :])
    x = np.arange(len(files))
    for level, label, color in LEVELS:
        sub = df[df["level"] == level].set_index("file_name")
        y = [float(sub.loc[file, "recovery_rate"]) if file in sub.index else math.nan for file in files]
        ax1.plot(x, y, marker="o", linewidth=1.5, color=color, label=label)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax1.set_ylim(0.0, RECOVERY_YMAX)
    ax1.set_yticks(np.linspace(0.0, 1.0, 6))
    ax1.set_ylabel("Recovery rate")
    ax1.legend(frameon=False, ncol=3, loc="lower left")
    ax1.set_title("a  Recovery by file", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax1)

    ax2 = fig.add_subplot(gs[1, 0])
    for level, label, color in LEVELS:
        sub = df[df["level"] == level]
        ax2.scatter(sub["original_count"], sub["recovery_rate"], s=56, color=color, edgecolor="#222222", linewidth=0.35, alpha=0.82, label=label)
    ax2.set_xscale("log")
    ax2.set_ylim(0.0, RECOVERY_YMAX)
    ax2.set_yticks(np.linspace(0.0, 1.0, 6))
    ax2.set_xlabel("Original identified / quantified count")
    ax2.set_ylabel("Recovery rate")
    ax2.set_title("b  Count dependence", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax2)

    ax3 = fig.add_subplot(gs[1, 1])
    vals = [df[df["level"] == level]["quantity_pearson"].dropna().to_numpy(dtype=float) for level, _, _ in LEVELS]
    box = ax3.boxplot(vals, patch_artist=True, showfliers=False)
    for patch, (_, _, color) in zip(box["boxes"], LEVELS):
        patch.set_facecolor(color)
        patch.set_alpha(0.34)
        patch.set_edgecolor(color)
    for median in box["medians"]:
        median.set_color("#111111")
    ax3.set_xticklabels([label for _, label, _ in LEVELS], rotation=20, ha="right", fontsize=8)
    ax3.set_ylabel("Quantity Pearson r")
    ax3.set_ylim(0.95, 1.001)
    ax3.set_title("c  Shared-feature quantification", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax3)
    fig.suptitle("DIA-NN precursor / peptide / protein-group recovery", fontsize=12.5, weight="bold")
    fig.subplots_adjust(top=0.91)
    return _save(fig, out_dir / "diann_recovery_nmi_overview")


def _plot_overview_panels(df: pd.DataFrame, out_dir: Path) -> list[str]:
    files = (
        df[["file_name", "paper_order"]]
        .drop_duplicates()
        .sort_values(["paper_order", "file_name"])["file_name"]
        .astype(str)
        .tolist()
    )
    labels = [_label(file) for file in files]
    x = np.arange(len(files))
    artifacts: list[str] = []

    fig, ax = plt.subplots(figsize=(11.5, 3.6))
    for level, label, color in LEVELS:
        sub = df[df["level"] == level].set_index("file_name")
        y = [float(sub.loc[file, "recovery_rate"]) if file in sub.index else math.nan for file in files]
        ax.plot(x, y, marker="o", linewidth=1.5, color=color, label=label)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylim(0.0, RECOVERY_YMAX)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_ylabel("Recovery rate")
    ax.legend(frameon=False, ncol=3, loc="lower left")
    ax.set_title("DIA-NN recovery by file", fontsize=11.5, weight="bold", pad=8)
    _style_axes(ax)
    fig.subplots_adjust(left=0.08, right=0.99, top=0.84, bottom=0.31)
    artifacts.extend(_save(fig, out_dir / "diann_recovery_overview_panel_a_recovery_by_file"))

    fig, ax = plt.subplots(figsize=(6.2, 4.15))
    for level, label, color in LEVELS:
        sub = df[df["level"] == level]
        ax.scatter(
            sub["original_count"],
            sub["recovery_rate"],
            s=56,
            color=color,
            edgecolor="#222222",
            linewidth=0.35,
            alpha=0.82,
            label=label,
        )
    ax.set_xscale("log")
    ax.set_ylim(0.0, RECOVERY_YMAX)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_xlabel("Original identified / quantified count")
    ax.set_ylabel("Recovery rate")
    ax.set_title("DIA-NN count dependence", fontsize=11.5, weight="bold", pad=8)
    _style_axes(ax)
    fig.subplots_adjust(left=0.13, right=0.98, top=0.86, bottom=0.17)
    artifacts.extend(_save(fig, out_dir / "diann_recovery_overview_panel_b_count_dependence"))

    fig, ax = plt.subplots(figsize=(4.7, 4.15))
    vals = [df[df["level"] == level]["quantity_pearson"].dropna().to_numpy(dtype=float) for level, _, _ in LEVELS]
    box = ax.boxplot(vals, patch_artist=True, showfliers=False)
    for patch, (_, _, color) in zip(box["boxes"], LEVELS):
        patch.set_facecolor(color)
        patch.set_alpha(0.34)
        patch.set_edgecolor(color)
    for median in box["medians"]:
        median.set_color("#111111")
    ax.set_xticklabels([label for _, label, _ in LEVELS], rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("Quantity Pearson r")
    ax.set_ylim(0.95, 1.001)
    ax.set_title("DIA-NN shared-feature quantification", fontsize=11.5, weight="bold", pad=8)
    _style_axes(ax)
    fig.subplots_adjust(left=0.18, right=0.98, top=0.86, bottom=0.18)
    artifacts.extend(_save(fig, out_dir / "diann_recovery_overview_panel_c_shared_feature_quantification"))
    return artifacts


def _write_summary(df: pd.DataFrame, out_root: Path) -> list[str]:
    tables = out_root / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    plot_input = tables / "diann_recovery_plot_input.csv"
    df.to_csv(plot_input, index=False)
    rows: list[dict[str, Any]] = []
    for level, label, _ in LEVELS:
        sub = df[df["level"] == level].copy()
        for metric in ["recovery_rate", "jaccard", "quantity_pearson", "p95_abs_log2_delta"]:
            values = sub[metric].dropna()
            if values.empty:
                rows.append({"level": level, "metric": metric, "n": 0})
                continue
            worst_idx = values.idxmin() if metric in {"recovery_rate", "jaccard", "quantity_pearson"} else values.idxmax()
            rows.append(
                {
                    "level": level,
                    "level_label": label,
                    "metric": metric,
                    "n": int(values.shape[0]),
                    "median": float(values.median()),
                    "min": float(values.min()),
                    "p05": float(values.quantile(0.05)),
                    "p95": float(values.quantile(0.95)),
                    "max": float(values.max()),
                    "worst_file": str(df.loc[worst_idx, "file_name"]),
                    "worst_file_label": str(df.loc[worst_idx, "file_label"]),
                }
            )
    summary = tables / "diann_recovery_plot_summary.csv"
    pd.DataFrame(rows).to_csv(summary, index=False)
    return [str(plot_input), str(summary)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot DIA-NN precursor/peptide/protein-group recovery from existing result tables.")
    parser.add_argument("--table-root", type=Path, default=DEFAULT_TABLE_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args()

    df = _load_recovery_table(args.table_root)
    out_dir = args.out_root / "plots" / "diann_recovery"
    artifacts: list[str] = []
    artifacts.extend(_write_summary(df, args.out_root))
    artifacts.extend(_plot_recovery_heatmap(df, out_dir))
    artifacts.extend(_plot_gain_loss(df, out_dir))
    artifacts.extend(_plot_recovery_vs_count(df, out_dir))
    artifacts.extend(_plot_agreement_heatmap(df, out_dir))
    artifacts.extend(_plot_overview(df, out_dir))
    artifacts.extend(_plot_overview_panels(df, out_dir))
    print("\n".join(artifacts), flush=True)


if __name__ == "__main__":
    main()
