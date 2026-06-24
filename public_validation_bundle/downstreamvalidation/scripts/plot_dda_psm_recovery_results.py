from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "outputs" / "search_validation_figures" / "tables" / "dda_psm_ion_recovery_summary.csv"
DEFAULT_OUT_ROOT = ROOT / "outputs" / "search_validation_figures"
DEFAULT_FILE_SET_TABLE = ROOT / "outputs" / "search_validation_figures" / "tables" / "dda_ionquant_pairwise_summary_input.csv"

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
    "gray": "#6F7782",
    "light_gray": "#E8ECEF",
    "text": "#202124",
}
RECOVERY_YMAX = 1.08


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


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["paper_order"] = df["file_name"].map(_order)
    df["file_label"] = df["file_name"].map(_label)
    numeric_cols = [
        "original_psm_count",
        "reconstructed_psm_count",
        "shared_psm_identity_count",
        "psm_recovery_rate",
        "psm_gain_count",
        "psm_loss_count",
        "original_peptide_ion_count",
        "reconstructed_peptide_ion_count",
        "shared_peptide_ion_count",
        "peptide_ion_recovery_rate",
        "peptide_ion_gain_count",
        "peptide_ion_loss_count",
        "hyperscore_pearson",
        "hyperscore_median_abs_delta",
        "hyperscore_p95_abs_delta",
        "peptideprophet_probability_pearson",
        "peptideprophet_probability_median_abs_delta",
        "peptideprophet_probability_p95_abs_delta",
        "expectation_pearson",
        "expectation_median_abs_delta",
        "expectation_p95_abs_delta",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = _as_float(df[col])
    df["psm_gain_rate"] = df["psm_gain_count"] / df["original_psm_count"]
    df["psm_loss_rate"] = df["psm_loss_count"] / df["original_psm_count"]
    df["peptide_ion_gain_rate"] = df["peptide_ion_gain_count"] / df["original_peptide_ion_count"]
    df["peptide_ion_loss_rate"] = df["peptide_ion_loss_count"] / df["original_peptide_ion_count"]
    return df.sort_values(["paper_order", "file_name"]).reset_index(drop=True)


def _filter_to_file_set(df: pd.DataFrame, file_set_table: Path | None, file_set_column: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    if file_set_table is None or not file_set_table.exists():
        audit = df[["file_name", "status"]].copy()
        audit["file_set_status"] = "unfiltered"
        return df.copy(), audit

    file_set_df = pd.read_csv(file_set_table)
    if file_set_column not in file_set_df.columns:
        raise ValueError(f"file-set column {file_set_column!r} not found in {file_set_table}")

    wanted = [str(x) for x in file_set_df[file_set_column].dropna().astype(str).drop_duplicates().tolist()]
    source_by_file = {str(row["file_name"]): row.to_dict() for _, row in df.iterrows()}
    rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    template_cols = list(df.columns)
    for file_name in wanted:
        if file_name in source_by_file:
            row = source_by_file[file_name]
            rows.append(row)
            audit_rows.append({"file_name": file_name, "in_psm_summary": True, "psm_status": row.get("status", ""), "file_set_status": "kept"})
        else:
            row = {col: math.nan for col in template_cols}
            row["file_name"] = file_name
            row["workflow"] = "dda_msfragger_philosopher_ionquant"
            row["status"] = "missing_psm"
            rows.append(row)
            audit_rows.append({"file_name": file_name, "in_psm_summary": False, "psm_status": "missing_psm", "file_set_status": "missing_from_psm_summary"})

    excluded = sorted(set(df["file_name"].astype(str)) - set(wanted), key=lambda x: (_order(x), x))
    for file_name in excluded:
        row = source_by_file[file_name]
        audit_rows.append({"file_name": file_name, "in_psm_summary": True, "psm_status": row.get("status", ""), "file_set_status": "excluded_not_in_dda_ionquant"})
    return pd.DataFrame(rows, columns=template_cols), pd.DataFrame(audit_rows)


def _style_axes(ax: plt.Axes) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#333333")
    ax.spines["bottom"].set_color("#333333")
    ax.tick_params(colors=COLORS["text"], labelsize=8)
    ax.grid(axis="y", color="#D7DCE0", linewidth=0.6, alpha=0.75)


def _set_log_x_padding(ax: plt.Axes, values: np.ndarray, left_factor: float = 1.45, right_factor: float = 1.85) -> None:
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite) & (finite > 0)]
    if finite.size:
        ax.set_xlim(float(finite.min()) / left_factor, float(finite.max()) * right_factor)


def _plot_recovery_heatmap(df: pd.DataFrame, out_dir: Path) -> list[str]:
    metrics = [
        ("psm_recovery_rate", "PSM recovery"),
        ("peptide_ion_recovery_rate", "Peptide-ion recovery"),
    ]
    matrix = np.vstack([df[col].to_numpy(dtype=float) for col, _ in metrics])
    labels = [label for _, label in metrics]
    cmap = plt.get_cmap("YlGn").copy()
    cmap.set_bad(color="#F0F2F4")
    fig, ax = plt.subplots(figsize=(max(7.5, 0.48 * len(df) + 2.2), 2.45))
    im = ax.imshow(np.ma.masked_invalid(matrix), aspect="auto", cmap=cmap, vmin=0.98, vmax=1.0)
    ax.set_xticks(np.arange(len(df)))
    ax.set_xticklabels(df["file_label"], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(labels)))
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.set_xticks(np.arange(-0.5, len(df), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(labels), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.75)
    ax.tick_params(axis="both", which="major", length=4, width=0.8, color="#111111")
    ax.tick_params(axis="both", which="minor", length=0)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            value = matrix[i, j]
            if np.isfinite(value):
                ax.text(j, i, f"{value:.4f}", ha="center", va="center", fontsize=6.7, color="#111111")
            elif str(df.loc[j, "status"]) != "ok":
                ax.text(j, i, "NA", ha="center", va="center", fontsize=6.5, color="#777777")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.8)
        spine.set_color("#111111")
    cbar = fig.colorbar(im, ax=ax, fraction=0.030, pad=0.012)
    cbar.set_label("Recovery rate", fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    ax.set_title("DDA PSM and peptide-ion recovery", fontsize=11.5, weight="bold", pad=8)
    fig.subplots_adjust(left=0.14, right=0.96, top=0.82, bottom=0.33)
    return _save(fig, out_dir / "dda_psm_peptide_ion_recovery_heatmap")


def _plot_gain_loss(df: pd.DataFrame, out_dir: Path) -> list[str]:
    ok = df[df["status"].astype(str) == "ok"].copy()
    x = np.arange(ok.shape[0])
    gain = ok["psm_gain_count"].fillna(0).to_numpy(dtype=float)
    loss = ok["psm_loss_count"].fillna(0).to_numpy(dtype=float)
    fig, ax = plt.subplots(figsize=(max(7.5, 0.44 * len(ok) + 2.2), 3.35))
    ax.bar(x, gain, width=0.68, color=COLORS["orange"], label="Gain")
    ax.bar(x, -loss, width=0.68, color=COLORS["blue"], label="Loss")
    ax.axhline(0, color="#222222", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(ok["file_label"], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("PSM count")
    ax.set_title("DDA PSM gain/loss after reconstruction", fontsize=11.5, weight="bold", pad=8)
    ax.legend(frameon=False, ncol=2, loc="upper right")
    _style_axes(ax)
    fig.subplots_adjust(left=0.10, right=0.98, top=0.86, bottom=0.28)
    return _save(fig, out_dir / "dda_psm_gain_loss_barplot")


def _row_normalized(values: np.ndarray) -> np.ndarray:
    out = values.copy().astype(float)
    for i in range(out.shape[0]):
        row = out[i, :]
        finite = np.isfinite(row)
        if not finite.any():
            continue
        lo = float(np.nanmin(row[finite]))
        hi = float(np.nanmax(row[finite]))
        if math.isclose(lo, hi):
            out[i, finite] = 0.5
        else:
            out[i, finite] = (row[finite] - lo) / (hi - lo)
    return out


def _plot_score_agreement(df: pd.DataFrame, out_dir: Path) -> list[str]:
    metrics = [
        ("hyperscore_pearson", "Hyperscore r", "{:.5f}"),
        ("peptideprophet_probability_pearson", "PeptideProphet r", "{:.5f}"),
        ("expectation_pearson", "Expectation r", "{:.5f}"),
        ("hyperscore_p95_abs_delta", "Hyperscore p95 delta", "{:.2g}"),
        ("peptideprophet_probability_p95_abs_delta", "PeptideProphet p95 delta", "{:.2g}"),
        ("expectation_p95_abs_delta", "Expectation p95 delta", "{:.2g}"),
    ]
    raw = np.vstack([df[col].to_numpy(dtype=float) for col, _, _ in metrics])
    color_values = _row_normalized(raw)
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad(color="#F0F2F4")
    fig, ax = plt.subplots(figsize=(max(8.2, 0.50 * len(df) + 2.6), 4.15))
    im = ax.imshow(np.ma.masked_invalid(color_values), aspect="auto", cmap=cmap, vmin=0.0, vmax=1.0)
    ax.set_xticks(np.arange(len(df)))
    ax.set_xticklabels(df["file_label"], rotation=45, ha="right", fontsize=8)
    ax.set_yticks(np.arange(len(metrics)))
    ax.set_yticklabels([label for _, label, _ in metrics], fontsize=8)
    for i, (_, _, fmt) in enumerate(metrics):
        for j in range(raw.shape[1]):
            value = raw[i, j]
            if np.isfinite(value):
                ax.text(j, i, fmt.format(value), ha="center", va="center", fontsize=6.2, color="white" if color_values[i, j] < 0.45 else "#111111")
            elif str(df.loc[j, "status"]) != "ok":
                ax.text(j, i, "NA", ha="center", va="center", fontsize=6.1, color="#777777")
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.8)
    cbar = fig.colorbar(im, ax=ax, fraction=0.026, pad=0.012)
    cbar.set_label("Row-normalized value", fontsize=8)
    cbar.ax.tick_params(labelsize=7)
    ax.set_title("DDA PSM score agreement", fontsize=11.5, weight="bold", pad=8)
    fig.subplots_adjust(left=0.20, right=0.97, top=0.88, bottom=0.30)
    return _save(fig, out_dir / "dda_psm_score_agreement_heatmap")


def _plot_recovery_scatter(df: pd.DataFrame, out_dir: Path) -> list[str]:
    ok = df[df["status"].astype(str) == "ok"].copy()
    x = ok["original_psm_count"].to_numpy(dtype=float)
    y = ok["psm_recovery_rate"].to_numpy(dtype=float)
    sizes = np.clip(np.sqrt(ok["original_peptide_ion_count"].fillna(0).to_numpy(dtype=float)) * 1.6, 28, 210)
    fig, ax = plt.subplots(figsize=(5.4, 4.2))
    ax.scatter(x, y, s=sizes, color=COLORS["green"], edgecolor="#1D3A2B", linewidth=0.45, alpha=0.82)
    xmax = float(np.nanmax(x)) if np.isfinite(x).any() else math.nan
    for _, row in ok.iterrows():
        if float(row["psm_recovery_rate"]) < 0.999 or float(row["original_psm_count"]) < 100:
            xpos = float(row["original_psm_count"])
            align_right = np.isfinite(xmax) and xpos > xmax / 3.0
            ax.annotate(
                row["file_label"],
                xy=(xpos, float(row["psm_recovery_rate"])),
                xytext=(-8 if align_right else 5, 0),
                textcoords="offset points",
                ha="right" if align_right else "left",
                va="center",
                fontsize=7.2,
                clip_on=False,
            )
    ax.set_xscale("log")
    _set_log_x_padding(ax, x)
    ax.set_ylim(0.0, RECOVERY_YMAX)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_xlabel("Original PSM count")
    ax.set_ylabel("PSM recovery rate")
    ax.set_title("PSM recovery versus original PSM count", fontsize=11.5, weight="bold", pad=8)
    _style_axes(ax)
    fig.subplots_adjust(left=0.13, right=0.97, top=0.87, bottom=0.16)
    return _save(fig, out_dir / "dda_psm_recovery_vs_original_psm_count_scatter")


def _plot_overview(df: pd.DataFrame, out_dir: Path) -> list[str]:
    ok = df[df["status"].astype(str) == "ok"].copy()
    fig = plt.figure(figsize=(11.2, 7.2))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.0], width_ratios=[1.15, 1.0], hspace=0.42, wspace=0.28)
    ax1 = fig.add_subplot(gs[0, 0])
    x = np.arange(ok.shape[0])
    ax1.plot(x, ok["psm_recovery_rate"], marker="o", color=COLORS["green"], linewidth=1.5, label="PSM")
    ax1.plot(x, ok["peptide_ion_recovery_rate"], marker="s", color=COLORS["blue"], linewidth=1.5, label="Peptide-ion")
    ax1.set_xticks(x)
    ax1.set_xticklabels(ok["file_label"], rotation=45, ha="right", fontsize=7)
    ax1.set_ylim(0.0, RECOVERY_YMAX)
    ax1.set_yticks(np.linspace(0.0, 1.0, 6))
    ax1.set_ylabel("Recovery rate")
    ax1.legend(frameon=False, fontsize=8)
    ax1.set_title("a  Recovery", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax1)

    ax2 = fig.add_subplot(gs[0, 1])
    ax2.bar(x, ok["psm_gain_count"].fillna(0), width=0.68, color=COLORS["orange"], label="Gain")
    ax2.bar(x, -ok["psm_loss_count"].fillna(0), width=0.68, color=COLORS["blue"], label="Loss")
    ax2.axhline(0, color="#222222", linewidth=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(ok["file_label"], rotation=45, ha="right", fontsize=7)
    ax2.set_ylabel("PSM count")
    ax2.legend(frameon=False, fontsize=8)
    ax2.set_title("b  Gain/loss", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax2)

    ax3 = fig.add_subplot(gs[1, 0])
    ax3.scatter(ok["original_psm_count"], ok["psm_recovery_rate"], s=55, color=COLORS["green"], edgecolor="#1D3A2B", linewidth=0.45)
    ax3.set_xscale("log")
    _set_log_x_padding(ax3, ok["original_psm_count"].to_numpy(dtype=float))
    ax3.set_ylim(0.0, RECOVERY_YMAX)
    ax3.set_yticks(np.linspace(0.0, 1.0, 6))
    ax3.set_xlabel("Original PSM count")
    ax3.set_ylabel("PSM recovery rate")
    ax3.set_title("c  Count dependence", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax3)

    ax4 = fig.add_subplot(gs[1, 1])
    score_cols = ["hyperscore_pearson", "peptideprophet_probability_pearson", "expectation_pearson"]
    score_labels = ["Hyperscore", "PeptideProphet", "Expectation"]
    vals = [ok[col].dropna().to_numpy(dtype=float) for col in score_cols]
    box = ax4.boxplot(vals, patch_artist=True, showfliers=False)
    for patch, color in zip(box["boxes"], [COLORS["blue"], COLORS["green"], COLORS["orange"]]):
        patch.set_facecolor(color)
        patch.set_alpha(0.34)
        patch.set_edgecolor(color)
    for median in box["medians"]:
        median.set_color("#111111")
    ax4.set_xticklabels(score_labels, rotation=20, ha="right", fontsize=8)
    ax4.set_ylabel("Pearson r")
    ax4.set_ylim(0.91, 1.001)
    ax4.set_title("d  Score agreement", loc="left", fontsize=10.5, weight="bold")
    _style_axes(ax4)
    fig.suptitle("DDA PSM recovery and score consistency", fontsize=12.5, weight="bold")
    fig.subplots_adjust(top=0.91)
    return _save(fig, out_dir / "dda_psm_recovery_nmi_overview")


def _write_summary(df: pd.DataFrame, out_root: Path) -> list[str]:
    tables = out_root / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    plot_input = tables / "dda_psm_recovery_plot_input.csv"
    df.to_csv(plot_input, index=False)
    ok = df[df["status"].astype(str) == "ok"].copy()
    rows: list[dict[str, Any]] = []
    for metric in [
        "psm_recovery_rate",
        "peptide_ion_recovery_rate",
        "hyperscore_pearson",
        "peptideprophet_probability_pearson",
        "expectation_pearson",
    ]:
        values = ok[metric].dropna()
        rows.append(
            {
                "metric": metric,
                "n": int(values.shape[0]),
                "median": float(values.median()) if not values.empty else math.nan,
                "min": float(values.min()) if not values.empty else math.nan,
                "p05": float(values.quantile(0.05)) if not values.empty else math.nan,
                "p95": float(values.quantile(0.95)) if not values.empty else math.nan,
                "max": float(values.max()) if not values.empty else math.nan,
                "worst_file": str(ok.loc[ok[metric].idxmin(), "file_name"]) if not values.empty else "",
                "worst_file_label": str(ok.loc[ok[metric].idxmin(), "file_label"]) if not values.empty else "",
            }
        )
    summary = tables / "dda_psm_recovery_plot_summary.csv"
    pd.DataFrame(rows).to_csv(summary, index=False)
    return [str(plot_input), str(summary)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot DDA PSM/peptide-ion recovery from existing summary CSV.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--file-set-table", type=Path, default=DEFAULT_FILE_SET_TABLE, help="Optional table whose unique files define the plotted DDA set.")
    parser.add_argument("--file-set-column", default="file", help="Column in --file-set-table containing file names.")
    parser.add_argument("--no-file-set-filter", action="store_true", help="Plot every row in the PSM summary instead of matching the DDA IonQuant set.")
    args = parser.parse_args()

    raw = pd.read_csv(args.input)
    filtered, audit = _filter_to_file_set(raw, None if args.no_file_set_filter else args.file_set_table, args.file_set_column)
    df = _prepare(filtered)
    out_dir = args.out_root / "plots" / "dda_psm_recovery"
    artifacts = []
    audit_path = args.out_root / "tables" / "dda_psm_recovery_file_set_audit.csv"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit.to_csv(audit_path, index=False)
    artifacts.append(str(audit_path))
    artifacts.extend(_write_summary(df, args.out_root))
    artifacts.extend(_plot_recovery_heatmap(df, out_dir))
    artifacts.extend(_plot_gain_loss(df, out_dir))
    artifacts.extend(_plot_score_agreement(df, out_dir))
    artifacts.extend(_plot_recovery_scatter(df, out_dir))
    artifacts.extend(_plot_overview(df, out_dir))
    print("\n".join(artifacts), flush=True)


if __name__ == "__main__":
    main()
