from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT_ROOT = ROOT / "outputs" / "search_validation_figures"

COLORS = {
    "full8": "#2F5D62",
    "data_stackzdpd": "#B86B25",
    "all": "#526D9D",
    "density": "#1F5A63",
}

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


def short_label(name: str) -> str:
    paper_order = PAPER_FILE_ORDER.get(str(name))
    if paper_order is not None:
        return f"File{paper_order}"
    stem = str(name).replace(".mzML", "")
    replacements = [
        ("01625b_GA1-TUM_first_pool_1_01_01-", "TUM "),
        ("QC_", ""),
        ("File", "F"),
        ("uncompressed", "uncomp"),
        ("true_uncompressed", "true"),
        ("_293T_", " "),
        ("_Human_01", ""),
    ]
    for old, new in replacements:
        stem = stem.replace(old, new)
    if len(stem) > 28:
        stem = stem[:25] + "..."
    return stem


def _paper_sort_key(name: str) -> tuple[int, str]:
    return (PAPER_FILE_ORDER.get(str(name), 10_000), str(name))


def _sorted_file_names(names: list[str] | np.ndarray | pd.Series) -> list[str]:
    return sorted((str(name) for name in names), key=_paper_sort_key)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _save(fig: plt.Figure, path: Path) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for suffix in (".png", ".pdf"):
        p = path.with_suffix(suffix)
        fig.savefig(p, dpi=240, bbox_inches="tight", pad_inches=0.18)
        out.append(str(p))
    plt.close(fig)
    return out


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 2:
        return float("nan")
    x = x[mask]
    y = y[mask]
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    x_centered = x - x_mean
    y_centered = y - y_mean
    x_ss = float(np.sum(x_centered * x_centered))
    y_ss = float(np.sum(y_centered * y_centered))
    if x_ss == 0.0 or y_ss == 0.0:
        return float("nan")
    return float(np.sum(x_centered * y_centered) / math.sqrt(x_ss * y_ss))


def _finite_xy(df: pd.DataFrame, x_col: str, y_col: str) -> pd.DataFrame:
    out = df.replace([np.inf, -np.inf], np.nan).dropna(subset=[x_col, y_col]).copy()
    return out[np.isfinite(out[x_col]) & np.isfinite(out[y_col])]


def plot_xic_area_scatter(target_df: pd.DataFrame, out: Path) -> list[str]:
    df = target_df.copy()
    df["x"] = np.log10(_num(df, "area_original").clip(lower=0) + 1.0)
    df["y"] = np.log10(_num(df, "area_reconstructed").clip(lower=0) + 1.0)
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["x", "y"])
    if len(df) > 30000:
        df = df.sample(n=30000, random_state=42)

    fig, ax = plt.subplots(figsize=(6.4, 5.8))
    ax.scatter(df["x"], df["y"], s=7, alpha=0.30, linewidths=0, color=COLORS["all"], label=f"all files (n={len(df):,})")
    lo = float(min(df["x"].min(), df["y"].min()))
    hi = float(max(df["x"].max(), df["y"].max()))
    pad = max((hi - lo) * 0.04, 0.2)
    lo -= pad
    hi += pad
    ax.plot([lo, hi], [lo, hi], color="black", linestyle="--", linewidth=1.0)
    r = _pearson(df["x"].to_numpy(float), df["y"].to_numpy(float))
    ax.text(
        0.04,
        0.96,
        f"n = {len(df):,}\nPearson r = {r:.9f}",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.28", fc="white", ec="#777777", alpha=0.90),
    )
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Original log10(XIC area + 1)")
    ax.set_ylabel("Reconstructed log10(XIC area + 1)")
    ax.set_title("Top-500 XIC Area Agreement")
    ax.legend(loc="lower right", frameon=True, fontsize=8)
    fig.tight_layout()
    artifacts = _save(fig, out / "top500_xic_area_scatter_all_files")
    artifacts.extend(_save_legacy_placeholder(out / "full20_top500_xic_area_scatter", out / "top500_xic_area_scatter_all_files"))
    return artifacts


def plot_xic_error_ecdf(target_df: pd.DataFrame, out: Path) -> list[str]:
    df = target_df.copy()
    df["err"] = _num(df, "area_abs_rel_error").clip(lower=0)
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["err"])

    fig, ax = plt.subplots(figsize=(6.8, 5.2))
    values = np.sort(df["err"].to_numpy(float))
    y = np.arange(1, values.size + 1, dtype=float) / values.size
    ax.step(values + 1e-12, y, where="post", linewidth=2.0, color=COLORS["all"], label=f"all files p95={np.percentile(values, 95):.2e}")
    ax.set_xscale("log")
    ax.set_xlabel("XIC area absolute relative error")
    ax.set_ylabel("Cumulative fraction")
    ax.set_title("Top-500 XIC Area Error ECDF")
    ax.grid(alpha=0.25, which="both")
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    artifacts = _save(fig, out / "top500_xic_area_error_ecdf_all_files")
    artifacts.extend(_save_legacy_placeholder(out / "full20_top500_xic_area_error_ecdf", out / "top500_xic_area_error_ecdf_all_files"))
    return artifacts


def plot_xic_error_ecdf_by_file(target_df: pd.DataFrame, out: Path, tables: Path) -> list[str]:
    df = target_df.copy()
    df["err"] = _num(df, "area_abs_rel_error").clip(lower=0)
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["err"])
    summary_rows = []

    files = _sorted_file_names(df["file_name"].astype(str).unique())
    cmap = plt.get_cmap("tab20")
    fig, ax = plt.subplots(figsize=(9.5, 6.4))
    for i, file_name in enumerate(files):
        sub = df[df["file_name"].astype(str) == file_name]
        values = np.sort(sub["err"].to_numpy(float))
        if values.size == 0:
            continue
        p95 = float(np.percentile(values, 95))
        summary_rows.append(
            {
                "file_name": file_name,
                "short_label": short_label(file_name),
                "target_count": int(values.size),
                "area_abs_rel_error_p50": float(np.percentile(values, 50)),
                "area_abs_rel_error_p95": p95,
                "area_abs_rel_error_p99": float(np.percentile(values, 99)),
                "area_abs_rel_error_max": float(np.max(values)),
            }
        )
        y = np.arange(1, values.size + 1, dtype=float) / values.size
        label = f"{short_label(file_name)} p95={p95:.1e}" if len(files) <= 18 else short_label(file_name)
        ax.step(values + 1e-12, y, where="post", linewidth=1.1, alpha=0.85, color=cmap(i % 20), label=label)

    ax.set_xscale("log")
    ax.set_xlabel("XIC area absolute relative error")
    ax.set_ylabel("Cumulative fraction")
    ax.set_title("Top-500 XIC Area Error ECDF by File")
    ax.grid(alpha=0.25, which="both")
    if len(files) <= 18:
        ax.legend(loc="lower right", fontsize=6.6, ncol=1)
    fig.tight_layout()
    tables.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(summary_rows).to_csv(tables / "xic_per_file_ecdf_p95_summary.csv", index=False)
    artifacts = _save(fig, out / "top500_xic_area_error_ecdf_by_file")
    artifacts.append(str(tables / "xic_per_file_ecdf_p95_summary.csv"))
    return artifacts


def _save_legacy_placeholder(legacy_stem: Path, canonical_stem: Path) -> list[str]:
    out = []
    for suffix in (".png", ".pdf"):
        src = canonical_stem.with_suffix(suffix)
        dst = legacy_stem.with_suffix(suffix)
        if src.exists() and src != dst:
            dst.write_bytes(src.read_bytes())
            out.append(str(dst))
    return out


def plot_xic_pearson_box(target_df: pd.DataFrame, out: Path) -> list[str]:
    df = target_df.copy()
    df["pearson"] = _num(df, "pearson")
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=["pearson"])
    df["label"] = df["file_name"].map(short_label)
    labels = [short_label(x) for x in _sorted_file_names(df["file_name"].unique())]
    groups = [df.loc[df["label"] == label, "pearson"].to_numpy(float) for label in labels]

    fig, ax = plt.subplots(figsize=(13.8, 5.8))
    box = ax.boxplot(groups, patch_artist=True, showfliers=False)
    for patch in box["boxes"]:
        patch.set_facecolor("#E8EEF3")
        patch.set_edgecolor("#526D9D")
    for median in box["medians"]:
        median.set_color("#B86B25")
        median.set_linewidth(1.4)
    ax.set_ylim(max(0.99995, float(df["pearson"].min()) - 2e-6), 1.000001)
    ax.set_xticks(np.arange(1, len(labels) + 1))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Pearson r")
    ax.set_title("Top-500 XIC Pearson Distribution")
    ax.grid(alpha=0.22, axis="y")
    fig.tight_layout()
    artifacts = _save(fig, out / "top500_xic_pearson_box_by_file")
    artifacts.extend(_save_legacy_placeholder(out / "full20_top500_xic_pearson_box", out / "top500_xic_pearson_box_by_file"))
    return artifacts


def plot_xic_error_vs_signal(target_df: pd.DataFrame, out: Path) -> list[str]:
    df = target_df.copy()
    df["signal"] = _num(df, "seed_intensity_sum").clip(lower=0)
    df["err"] = _num(df, "area_abs_rel_error").clip(lower=0)
    df = _finite_xy(df, "signal", "err")
    df["log_signal"] = np.log10(df["signal"] + 1.0)
    df["log_err"] = np.log10(df["err"] + 1e-12)
    df = _finite_xy(df, "log_signal", "log_err")
    plot_df = df.sample(n=30000, random_state=42) if len(df) > 30000 else df

    fig, ax = plt.subplots(figsize=(7.4, 5.8))
    counts, xedges, yedges = np.histogram2d(df["log_signal"], df["log_err"], bins=72)
    positive = counts[counts > 0]
    if positive.size:
        levels = np.quantile(positive, [0.50, 0.75, 0.90, 0.97])
        levels = np.unique(levels[levels > 0])
        if levels.size:
            xc = (xedges[:-1] + xedges[1:]) / 2.0
            yc = (yedges[:-1] + yedges[1:]) / 2.0
            ax.contour(10**xc, 10**yc, counts.T, levels=levels, colors=COLORS["density"], linewidths=0.9, alpha=0.85)
    ax.scatter(
        plot_df["signal"] + 1.0,
        plot_df["err"] + 1e-12,
        s=7,
        alpha=0.24,
        linewidths=0,
        color=COLORS["all"],
        label=f"all files (n={len(df):,})",
    )
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Seed signal score + 1")
    ax.set_ylabel("XIC area absolute relative error")
    ax.set_title("Top-500 XIC Error vs Signal")
    ax.grid(alpha=0.22, which="both")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    artifacts = _save(fig, out / "top500_xic_area_error_vs_signal_all_files")
    artifacts.extend(_save_legacy_placeholder(out / "full20_top500_xic_area_error_vs_signal", out / "top500_xic_area_error_vs_signal_all_files"))
    return artifacts


def plot_roundtrip_error_summary(roundtrip_df: pd.DataFrame, out: Path) -> list[str]:
    df = roundtrip_df.copy()
    df["paper_order"] = df["file_name"].map(lambda x: PAPER_FILE_ORDER.get(str(x), 10_000))
    df = df.sort_values(["paper_order", "index"])
    df["label"] = df["file_name"].map(short_label)
    df["mz"] = _num(df, "max_abs_mz_error")
    df["inten"] = _num(df, "max_abs_intensity_error")
    x = np.arange(len(df))

    fig, axes = plt.subplots(2, 1, figsize=(13.8, 7.8), sharex=True)
    axes[0].bar(x, df["mz"], color="#526D9D", width=0.76)
    axes[0].axhline(5e-7, color="black", linestyle="--", linewidth=1.0, label="5e-7 ceiling")
    axes[0].set_ylabel("max abs m/z error")
    axes[0].legend(loc="upper right", fontsize=8)
    axes[0].grid(alpha=0.2, axis="y")

    axes[1].bar(x, df["inten"], color="#B86B25", width=0.76)
    axes[1].axhline(0.1, color="black", linestyle="--", linewidth=1.0, label="0.1 float32 ceiling")
    axes[1].axhline(0.05, color="#555555", linestyle=":", linewidth=1.0, label="0.05 float64 ceiling")
    axes[1].set_ylabel("max abs intensity error")
    axes[1].legend(loc="upper right", fontsize=8)
    axes[1].grid(alpha=0.2, axis="y")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(df["label"], rotation=45, ha="right", fontsize=8)
    fig.suptitle("Array-Level Roundtrip Error Summary", fontsize=13)
    fig.tight_layout()
    return _save(fig, out / "trackcodec_roundtrip_error_summary")


def plot_roundtrip_tic_bpi(roundtrip_df: pd.DataFrame, out: Path) -> list[str]:
    df = roundtrip_df.copy()
    df["paper_order"] = df["file_name"].map(lambda x: PAPER_FILE_ORDER.get(str(x), 10_000))
    df = df.sort_values(["paper_order", "index"])
    df["label"] = df["file_name"].map(short_label)
    df["tic"] = _num(df, "spectrum_tic_rel_error_p95").clip(lower=0)
    df["bpi"] = _num(df, "spectrum_bpi_rel_error_p95").clip(lower=0)
    x = np.arange(len(df))

    fig, ax = plt.subplots(figsize=(13.8, 5.6))
    ax.plot(x, df["tic"] + 1e-12, marker="o", linewidth=1.5, label="p95 TIC relative error", color="#2F5D62")
    ax.plot(x, df["bpi"] + 1e-12, marker="s", linewidth=1.5, label="p95 BPI relative error", color="#B86B25")
    ax.set_yscale("log")
    ax.set_ylabel("relative error + 1e-12")
    ax.set_title("Per-Spectrum TIC/BPI Error p95")
    ax.set_xticks(x)
    ax.set_xticklabels(df["label"], rotation=45, ha="right", fontsize=8)
    ax.grid(alpha=0.22, axis="y", which="both")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    return _save(fig, out / "trackcodec_roundtrip_tic_bpi_error")


def _heatmap_text(col: str, value: float) -> str:
    if col == "pearson_p05":
        return f"{value:.6f}"
    abs_value = abs(float(value))
    if abs_value == 0:
        return "0"
    if abs_value < 1e-4:
        return f"{value:.1e}"
    if abs_value < 10:
        return f"{value:.4f}"
    return f"{value:.2g}"


def _heatmap_quantized_value(col: str, value: float) -> float:
    if col == "pearson_p05":
        return max(0.0, 1.0 - float(f"{value:.6f}"))
    return float(_heatmap_text(col, value))


def plot_validation_heatmap(roundtrip_df: pd.DataFrame, xic_df: pd.DataFrame, out: Path) -> list[str]:
    rt = roundtrip_df.copy()
    xic = xic_df.copy()
    merged = rt[["index", "file_name", "max_abs_mz_error", "max_abs_intensity_error", "spectrum_tic_rel_error_p95"]].merge(
        xic[["index", "area_abs_rel_error_p95", "pearson_p05", "apex_shift_p95"]],
        on="index",
        how="inner",
    )
    merged["paper_order"] = merged["file_name"].map(lambda x: PAPER_FILE_ORDER.get(str(x), 10_000))
    merged = merged.sort_values(["paper_order", "index"])
    merged["label"] = merged["file_name"].map(short_label)
    cols = [
        "max_abs_mz_error",
        "max_abs_intensity_error",
        "spectrum_tic_rel_error_p95",
        "area_abs_rel_error_p95",
        "pearson_p05",
        "apex_shift_p95",
    ]
    mat = merged[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    display = np.zeros_like(mat, dtype=float)
    for j, col in enumerate(cols):
        values = np.array([_heatmap_quantized_value(col, value) if np.isfinite(value) else np.nan for value in mat[:, j]], dtype=float)
        values = np.log10(np.clip(values, 1e-12, None))
        lo = np.nanmin(values)
        hi = np.nanmax(values)
        display[:, j] = 0.0 if hi <= lo else (values - lo) / (hi - lo)

    fig, ax = plt.subplots(figsize=(9.8, 8.6))
    im = ax.imshow(display, aspect="auto", cmap="viridis")
    ax.set_yticks(np.arange(len(merged)))
    ax.set_yticklabels(merged["label"], fontsize=8)
    ax.set_xticks(np.arange(len(cols)))
    ax.set_xticklabels(
        ["m/z max", "Intensity max", "TIC p95", "XIC area p95", "1 - Pearson p05", "Apex p95"],
        rotation=35,
        ha="right",
        fontsize=9,
    )
    ax.set_xticks(np.arange(-0.5, len(cols), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(merged), 1), minor=True)
    ax.grid(which="minor", color="white", linewidth=0.75)
    ax.tick_params(axis="both", which="major", length=4, width=0.8, color="#111111")
    ax.tick_params(axis="both", which="minor", length=0)
    for i in range(len(merged)):
        for j, col in enumerate(cols):
            value = mat[i, j]
            text = _heatmap_text(col, value)
            text_color = "black" if display[i, j] > 0.62 else "white"
            ax.text(j, i, text, ha="center", va="center", fontsize=6.5, color=text_color)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.8)
        spine.set_color("#111111")
    fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02, label="Per-metric normalized log error")
    ax.set_title("TrackCodec Validation Metric Heatmap")
    fig.tight_layout()
    return _save(fig, out / "trackcodec_validation_metric_heatmap")


def write_key_table(roundtrip_df: pd.DataFrame, xic_df: pd.DataFrame, out: Path) -> str:
    merged = roundtrip_df.merge(
        xic_df[["index", "area_abs_rel_error_p95", "area_abs_rel_error_max", "pearson_median", "pearson_p05", "apex_shift_p95"]],
        on="index",
        how="left",
        suffixes=("", "_xic"),
    )
    cols = [
        "index",
        "dataset",
        "file_name",
        "roundtrip_pass",
        "max_abs_mz_error",
        "max_abs_intensity_error",
        "spectrum_tic_rel_error_p95",
        "area_abs_rel_error_p95",
        "area_abs_rel_error_max",
        "pearson_median",
        "pearson_p05",
        "apex_shift_p95",
        "roundtrip_engine",
    ]
    out_path = out / "tables" / "validation_key_metrics_table.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    merged[cols].to_csv(out_path, index=False)
    return str(out_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate TrackCodec downstream validation figures.")
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result_root: Path = args.result_root
    tables = result_root / "tables"
    plots = result_root / "plots"

    roundtrip_df = pd.read_csv(tables / "roundtrip_summary.csv")
    xic_summary_df = pd.read_csv(tables / "xic_summary.csv")
    xic_target_df = pd.read_csv(tables / "xic_target_metrics.csv")

    artifacts: list[str] = []
    artifacts.extend(plot_xic_area_scatter(xic_target_df, plots / "xic"))
    artifacts.extend(plot_xic_error_ecdf(xic_target_df, plots / "xic"))
    artifacts.extend(plot_xic_error_ecdf_by_file(xic_target_df, plots / "xic", tables))
    artifacts.extend(plot_xic_pearson_box(xic_target_df, plots / "xic"))
    artifacts.extend(plot_xic_error_vs_signal(xic_target_df, plots / "xic"))
    artifacts.extend(plot_roundtrip_error_summary(roundtrip_df, plots / "roundtrip"))
    artifacts.extend(plot_roundtrip_tic_bpi(roundtrip_df, plots / "roundtrip"))
    artifacts.extend(plot_validation_heatmap(roundtrip_df, xic_summary_df, plots / "summary"))
    artifacts.append(write_key_table(roundtrip_df, xic_summary_df, result_root))

    manifest_path = result_root / "plots" / "plot_manifest.json"
    manifest_path.write_text(json.dumps({"artifacts": artifacts}, indent=2), encoding="utf-8")
    print(json.dumps({"n_artifacts": len(artifacts), "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
