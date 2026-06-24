from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
import numpy as np
import pandas as pd

from plot_diann_recovery_results import _label, _order, _save


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TABLE_ROOT = ROOT / "outputs" / "search_validation_figures" / "tables"
DEFAULT_OUT_ROOT = ROOT / "outputs" / "search_validation_figures"

LEVEL_COLORS = {
    "precursor": "#2F5D7C",
    "peptide": "#4E9A66",
    "protein_group": "#C47A3C",
}
MODALITY_SIZES = {
    "DDA": 95,
    "DIA": 210,
}
LEVEL_LABELS = {
    "psm": "PSM recovery",
    "peptide_ion": "Peptide-ion recovery",
    "precursor": "Precursor",
    "peptide": "Peptide",
    "protein_group": "Protein group",
}
LEVEL_ORDER = ["protein_group", "peptide", "precursor"]
SCATTER_LEVEL_ORDER = ["precursor", "peptide", "protein_group"]
HEATMAP_LEVEL_ORDER = ["psm", "peptide_ion", "precursor", "peptide", "protein_group"]
RECOVERY_YMAX = 1.08
RECOVERY_CMAP = LinearSegmentedColormap.from_list("recovery_gray", ["#FFFFFF", "#CBD5E1", "#64748B"])


def _as_float(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _style_axes(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#333333")
    ax.spines["bottom"].set_color("#333333")
    ax.tick_params(colors="#202124", labelsize=8)
    ax.grid(axis="y", color="#D7DCE0", linewidth=0.6, alpha=0.75)


def _set_log_x_padding(ax: plt.Axes, values: pd.Series | np.ndarray, left_factor: float = 1.45, right_factor: float = 1.75) -> None:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite) & (finite > 0)]
    if finite.size:
        ax.set_xlim(float(finite.min()) / left_factor, float(finite.max()) * right_factor)


def _load_dda_ionquant(table_root: Path) -> pd.DataFrame:
    path = table_root / "dda_ionquant_enriched_pairwise_summary.csv"
    if not path.exists():
        path = table_root / "dda_ionquant_pairwise_summary_input.csv"
    df = pd.read_csv(path)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        level = str(row.get("level", ""))
        if level == "protein":
            level = "protein_group"
        if level not in LEVEL_LABELS:
            continue
        original = float(row.get("original_nonzero_count", math.nan))
        shared = float(row.get("shared_nonzero_count", math.nan))
        reconstructed = float(row.get("reconstructed_nonzero_count", math.nan))
        rows.append(
            {
                "modality": "DDA",
                "workflow": "MSFragger-Philosopher-IonQuant",
                "file_name": row.get("file"),
                "level": level,
                "original_count": original,
                "reconstructed_count": reconstructed,
                "shared_count": shared,
                "recovery_rate": shared / original if original > 0 else math.nan,
                "jaccard": row.get("jaccard"),
                "quantity_pearson": row.get("shared_log2_pearson"),
                "p95_abs_log2_delta": row.get("p95_abs_log2_delta"),
                "status": row.get("status", "ok"),
            }
        )
    return pd.DataFrame(rows)


def _load_dia_diann(table_root: Path) -> pd.DataFrame:
    path = table_root / "diann_recovery_plot_input.csv"
    df = pd.read_csv(path)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        level = str(row.get("level", ""))
        if level not in LEVEL_LABELS:
            continue
        rows.append(
            {
                "modality": "DIA",
                "workflow": "DIA-NN",
                "file_name": row.get("file_name"),
                "level": level,
                "original_count": row.get("original_count"),
                "reconstructed_count": row.get("reconstructed_count"),
                "shared_count": row.get("shared_count"),
                "recovery_rate": row.get("recovery_rate"),
                "jaccard": row.get("jaccard"),
                "quantity_pearson": row.get("quantity_pearson"),
                "p95_abs_log2_delta": row.get("p95_abs_log2_delta"),
                "status": row.get("status", "ok"),
            }
        )
    return pd.DataFrame(rows)


def _load_dda_psm_recovery(table_root: Path) -> pd.DataFrame:
    path = table_root / "dda_psm_recovery_plot_input.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        for level, column in [("psm", "psm_recovery_rate"), ("peptide_ion", "peptide_ion_recovery_rate")]:
            rows.append(
                {
                    "modality": "DDA",
                    "workflow": "MSFragger-Philosopher-IonQuant",
                    "file_name": row.get("file_name"),
                    "level": level,
                    "original_count": row.get("original_psm_count") if level == "psm" else row.get("original_peptide_ion_count"),
                    "reconstructed_count": row.get("reconstructed_psm_count") if level == "psm" else row.get("reconstructed_peptide_ion_count"),
                    "shared_count": row.get("shared_psm_identity_count") if level == "psm" else row.get("shared_peptide_ion_count"),
                    "recovery_rate": row.get(column),
                    "jaccard": math.nan,
                    "quantity_pearson": math.nan,
                    "p95_abs_log2_delta": math.nan,
                    "status": row.get("status", "ok"),
                }
            )
    return pd.DataFrame(rows)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in [
        "original_count",
        "reconstructed_count",
        "shared_count",
        "recovery_rate",
        "jaccard",
        "quantity_pearson",
        "p95_abs_log2_delta",
    ]:
        out[col] = _as_float(out[col])
    out["paper_order"] = out["file_name"].map(_order)
    out["file_label"] = out["file_name"].map(_label)
    out["level_label"] = out["level"].map(LEVEL_LABELS)
    out["level_order"] = out["level"].map({level: i for i, level in enumerate(LEVEL_ORDER)})
    return out.sort_values(["modality", "paper_order", "level_order"]).reset_index(drop=True)


def _plot_combined_scatter(df: pd.DataFrame, out_dir: Path) -> list[str]:
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    for modality in ["DDA", "DIA"]:
        for level in LEVEL_ORDER:
            sub = df[(df["modality"] == modality) & (df["level"] == level)]
            if sub.empty:
                continue
            ax.scatter(
                sub["original_count"],
                sub["recovery_rate"],
                s=MODALITY_SIZES[modality],
                color=LEVEL_COLORS[level],
                edgecolor="#222222",
                linewidth=0.45,
                alpha=0.52 if modality == "DIA" else 0.58,
                label=f"{modality} {LEVEL_LABELS[level]}",
            )

    ax.set_xscale("log")
    _set_log_x_padding(ax, df["original_count"])
    ax.set_ylim(0.0, RECOVERY_YMAX)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_xlabel("Original identified / quantified count")
    ax.set_ylabel("Recovery rate")
    ax.set_title("DDA and DIA molecular-level recovery", fontsize=12.5, weight="bold", pad=8)
    _style_axes(ax)

    level_handles = [
        Line2D([0], [0], marker="o", linestyle="None", markerfacecolor=LEVEL_COLORS[level], markeredgecolor="#222222", markersize=8, label=LEVEL_LABELS[level])
        for level in ["precursor", "peptide", "protein_group"]
    ]
    modality_handles = [
        Line2D([0], [0], marker="o", linestyle="None", markerfacecolor="#A8A8A8", markeredgecolor="#222222", markersize=math.sqrt(MODALITY_SIZES[modality]) / 1.25, label=modality)
        for modality in ["DDA", "DIA"]
    ]
    first = ax.legend(
        handles=level_handles,
        title="Level",
        frameon=False,
        loc="lower left",
        bbox_to_anchor=(0.01, 0.18),
        fontsize=8,
        title_fontsize=8,
    )
    ax.add_artist(first)
    ax.legend(
        handles=modality_handles,
        title="Acquisition mode",
        frameon=False,
        loc="lower left",
        bbox_to_anchor=(0.01, 0.02),
        fontsize=8,
        title_fontsize=8,
    )
    fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.15)
    return _save(fig, out_dir / "dda_dia_molecular_recovery_vs_count")


def _plot_dda_ionquant_count_dependence(df: pd.DataFrame, out_root: Path) -> list[str]:
    dda = df[
        (df["modality"] == "DDA")
        & (df["level"].isin(SCATTER_LEVEL_ORDER))
        & (df["original_count"] > 0)
        & (df["recovery_rate"].notna())
    ].copy()
    if dda.empty:
        return []

    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    for level in SCATTER_LEVEL_ORDER:
        sub = dda[dda["level"] == level]
        if sub.empty:
            continue
        ax.scatter(
            sub["original_count"],
            sub["recovery_rate"],
            s=125,
            color=LEVEL_COLORS[level],
            edgecolor="#222222",
            linewidth=0.45,
            alpha=0.76,
            label=LEVEL_LABELS[level],
        )
        worst = sub.loc[sub["recovery_rate"].idxmin()]
        ax.annotate(
            str(worst["file_label"]),
            xy=(float(worst["original_count"]), float(worst["recovery_rate"])),
            xytext=(5, -7),
            textcoords="offset points",
            fontsize=8,
            color=LEVEL_COLORS[level],
        )

    ax.set_xscale("log")
    _set_log_x_padding(ax, dda["original_count"])
    ax.set_ylim(0.0, RECOVERY_YMAX)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_xlabel("Original quantified count")
    ax.set_ylabel("Recovery rate")
    ax.set_title("DDA IonQuant molecular-level recovery versus original count", fontsize=12.5, weight="bold", pad=8)
    _style_axes(ax)

    handles = [
        Line2D(
            [0],
            [0],
            marker="o",
            linestyle="None",
            markerfacecolor=LEVEL_COLORS[level],
            markeredgecolor="#222222",
            markersize=8,
            label=LEVEL_LABELS[level],
        )
        for level in SCATTER_LEVEL_ORDER
    ]
    ax.legend(handles=handles, frameon=False, loc="lower left", fontsize=9)
    fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.15)
    return _save_aliases(
        fig,
        [
            out_root / "plots" / "search_recovery_combined" / "dda_ionquant_molecular_recovery_vs_original_count",
            out_root / "plots" / "dda_ionquant" / "summary" / "dda_ionquant_molecular_recovery_vs_original_count",
        ],
    )


def _save_aliases(fig: plt.Figure, stems: list[Path]) -> list[str]:
    paths: list[str] = []
    for stem in stems:
        stem.parent.mkdir(parents=True, exist_ok=True)
        for suffix in [".png", ".pdf"]:
            path = stem.parent / f"{stem.name}{suffix}"
            fig.savefig(path, dpi=240, bbox_inches="tight")
            paths.append(str(path))
    plt.close(fig)
    return paths


def _plot_dda_search_recovery_heatmap(molecular_df: pd.DataFrame, psm_df: pd.DataFrame, out_dir: Path) -> list[str]:
    df = pd.concat([psm_df, molecular_df], ignore_index=True)
    dda = df[df["modality"] == "DDA"].copy()
    if dda.empty:
        return []
    files = (
        dda[["file_name", "paper_order"]]
        .drop_duplicates()
        .sort_values(["paper_order", "file_name"])["file_name"]
        .astype(str)
        .tolist()
    )
    matrix = []
    for level in HEATMAP_LEVEL_ORDER:
        sub = dda[dda["level"] == level].set_index("file_name")
        matrix.append([float(sub.loc[file, "recovery_rate"]) if file in sub.index else math.nan for file in files])
    values = np.array(matrix, dtype=float)
    labels = [_label(file) for file in files]
    fig, ax = plt.subplots(figsize=(max(7.6, 0.54 * len(files) + 2.1), 3.2))
    cmap = RECOVERY_CMAP.copy()
    cmap.set_bad(color="#F0F2F4")
    im = ax.imshow(np.ma.masked_invalid(values), aspect="auto", cmap=cmap, vmin=0.0, vmax=1.0)
    ax.set_xticks(np.arange(len(files)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(HEATMAP_LEVEL_ORDER)))
    ax.set_yticklabels([LEVEL_LABELS[level] for level in HEATMAP_LEVEL_ORDER], fontsize=9)
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
    ax.set_title("DDA search recovery: PSM, peptide-ion and IonQuant molecular levels", fontsize=11.5, weight="bold", pad=8)
    fig.subplots_adjust(left=0.20, right=0.96, top=0.84, bottom=0.30)
    return _save_aliases(
        fig,
        [
            out_dir / "dda_search_psm_ionquant_recovery_heatmap",
            out_dir / "dda_ionquant_precursor_peptide_proteingroup_recovery_heatmap",
        ],
    )


def _write_tables(df: pd.DataFrame, out_root: Path) -> list[str]:
    tables = out_root / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    input_path = tables / "combined_search_molecular_recovery_plot_input.csv"
    df.to_csv(input_path, index=False)
    rows: list[dict[str, Any]] = []
    for (modality, level), sub in df.groupby(["modality", "level"], sort=False):
        values = sub["recovery_rate"].dropna()
        if values.empty:
            continue
        worst_idx = values.idxmin()
        rows.append(
            {
                "modality": modality,
                "level": level,
                "level_label": LEVEL_LABELS[level],
                "n": int(values.shape[0]),
                "median_recovery": float(values.median()),
                "min_recovery": float(values.min()),
                "p05_recovery": float(values.quantile(0.05)),
                "max_recovery": float(values.max()),
                "worst_file": str(df.loc[worst_idx, "file_name"]),
                "worst_file_label": str(df.loc[worst_idx, "file_label"]),
            }
        )
    summary_path = tables / "combined_search_molecular_recovery_summary.csv"
    pd.DataFrame(rows).to_csv(summary_path, index=False)
    return [str(input_path), str(summary_path)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot combined DDA/DIA molecular-level search recovery.")
    parser.add_argument("--table-root", type=Path, default=DEFAULT_TABLE_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args()

    molecular_df = _prepare(pd.concat([_load_dda_ionquant(args.table_root), _load_dia_diann(args.table_root)], ignore_index=True))
    psm_df = _prepare(_load_dda_psm_recovery(args.table_root))
    out_dir = args.out_root / "plots" / "search_recovery_combined"
    artifacts: list[str] = []
    artifacts.extend(_write_tables(molecular_df, args.out_root))
    artifacts.extend(_plot_combined_scatter(molecular_df, out_dir))
    artifacts.extend(_plot_dda_ionquant_count_dependence(molecular_df, args.out_root))
    artifacts.extend(_plot_dda_search_recovery_heatmap(molecular_df, psm_df, out_dir))
    print("\n".join(artifacts), flush=True)


if __name__ == "__main__":
    main()
