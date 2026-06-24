from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams.update({"pdf.fonttype": 42, "ps.fonttype": 42})
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_TOP_K = (500, 1000, 1500, 2000, 2500)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _stat(series: pd.Series, q: float | None = None, *, name: str | None = None) -> float:
    arr = _numeric(series).dropna().to_numpy(dtype=float)
    if arr.size == 0:
        return float("nan")
    if q is not None:
        return float(np.percentile(arr, q))
    if name == "median":
        return float(np.median(arr))
    if name == "min":
        return float(np.min(arr))
    if name == "max":
        return float(np.max(arr))
    raise ValueError(name or str(q))


def _short_label(name: str, limit: int = 34) -> str:
    stem = str(name).replace(".mzML", "")
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


def _file_columns(df: pd.DataFrame) -> tuple[str, str]:
    index_col = "index" if "index" in df.columns else "file_index"
    file_col = "file_name" if "file_name" in df.columns else "file"
    return index_col, file_col


def _rank_column(df: pd.DataFrame) -> str:
    if "target_rank" in df.columns:
        return "target_rank"
    if "rank" in df.columns:
        return "rank"
    raise KeyError("target table needs target_rank or rank")


def summarize_topk(target_df: pd.DataFrame, top_k_values: tuple[int, ...]) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = target_df.copy()
    index_col, file_col = _file_columns(df)
    rank_col = _rank_column(df)
    df[rank_col] = _numeric(df[rank_col])
    rows: list[dict[str, Any]] = []
    for (index, file_name), file_df in df.groupby([index_col, file_col], sort=True):
        file_df = file_df.sort_values(rank_col)
        available = int(file_df[rank_col].max()) if file_df[rank_col].notna().any() else 0
        for top_k in top_k_values:
            sub = file_df[file_df[rank_col] <= top_k]
            rows.append(
                {
                    "index": int(index),
                    "file_name": file_name,
                    "short_label": _short_label(file_name),
                    "top_k": int(top_k),
                    "requested_top_k": int(top_k),
                    "target_count": int(len(sub)),
                    "available_target_count": available,
                    "target_count_lt_requested": bool(len(sub) < top_k),
                    "has_nan": bool(sub[["area_abs_rel_error", "pearson", "apex_shift"]].isna().any().any()) if not sub.empty else True,
                    "area_abs_rel_error_median": _stat(sub["area_abs_rel_error"], name="median"),
                    "area_abs_rel_error_p95": _stat(sub["area_abs_rel_error"], 95),
                    "area_abs_rel_error_p99": _stat(sub["area_abs_rel_error"], 99),
                    "area_abs_rel_error_max": _stat(sub["area_abs_rel_error"], name="max"),
                    "pearson_median": _stat(sub["pearson"], name="median"),
                    "pearson_p05": _stat(sub["pearson"], 5),
                    "pearson_min": _stat(sub["pearson"], name="min"),
                    "apex_shift_p95": _stat(sub["apex_shift"], 95),
                    "apex_shift_max": _stat(sub["apex_shift"], name="max"),
                }
            )
    per_file = pd.DataFrame(rows)
    all_rows: list[dict[str, Any]] = []
    for top_k, sub in per_file.groupby("top_k", sort=True):
        worst_area = sub.loc[_numeric(sub["area_abs_rel_error_p95"]).idxmax()] if sub["area_abs_rel_error_p95"].notna().any() else None
        worst_pearson = sub.loc[_numeric(sub["pearson_p05"]).idxmin()] if sub["pearson_p05"].notna().any() else None
        all_rows.append(
            {
                "top_k": int(top_k),
                "file_count": int(sub["file_name"].nunique()),
                "files_with_target_count_lt_requested": int(sub["target_count_lt_requested"].sum()),
                "files_with_nan": int(sub["has_nan"].sum()),
                "area_abs_rel_error_p95_median": _stat(sub["area_abs_rel_error_p95"], name="median"),
                "area_abs_rel_error_p95_p95": _stat(sub["area_abs_rel_error_p95"], 95),
                "area_abs_rel_error_p95_max": _stat(sub["area_abs_rel_error_p95"], name="max"),
                "worst_area_file": "" if worst_area is None else str(worst_area["file_name"]),
                "worst_area_value": float("nan") if worst_area is None else float(worst_area["area_abs_rel_error_p95"]),
                "pearson_p05_median": _stat(sub["pearson_p05"], name="median"),
                "pearson_p05_p05": _stat(sub["pearson_p05"], 5),
                "pearson_p05_min": _stat(sub["pearson_p05"], name="min"),
                "worst_pearson_file": "" if worst_pearson is None else str(worst_pearson["file_name"]),
                "worst_pearson_value": float("nan") if worst_pearson is None else float(worst_pearson["pearson_p05"]),
                "apex_shift_p95_median": _stat(sub["apex_shift_p95"], name="median"),
                "apex_shift_p95_max": _stat(sub["apex_shift_p95"], name="max"),
            }
        )
    return per_file, pd.DataFrame(all_rows)


def _save(fig: plt.Figure, stem: Path) -> list[str]:
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in [".png", ".pdf"]:
        path = stem.with_suffix(suffix)
        fig.savefig(path, dpi=240, bbox_inches="tight", pad_inches=0.18)
        paths.append(str(path))
    plt.close(fig)
    return paths


def _plot_metric(summary: pd.DataFrame, y_col: str, title: str, ylabel: str, out: Path, *, log_y: bool = False, pearson_axis: bool = False) -> list[str]:
    fig, ax = plt.subplots(figsize=(8.2, 5.6))
    topks = sorted(int(v) for v in summary["top_k"].dropna().unique())
    for _, sub in summary.groupby("index", sort=True):
        sub = sub.sort_values("top_k")
        ax.plot(sub["top_k"], sub[y_col], color="#8FA1AA", linewidth=0.9, alpha=0.34)
    med = summary.groupby("top_k")[y_col].median().reindex(topks)
    ax.plot(topks, med, marker="o", linewidth=2.5, color="#1F5D58", label="median across files")
    if y_col == "area_abs_rel_error_p95":
        worst_idx = _numeric(summary[y_col]).idxmax()
        worst = summary.loc[worst_idx]
        sub = summary[summary["file_name"] == worst["file_name"]].sort_values("top_k")
        ax.plot(sub["top_k"], sub[y_col], marker="o", linewidth=1.8, color="#B44E2A", label=f"worst: {_short_label(str(worst['file_name']), 24)}")
    if y_col == "pearson_p05":
        worst_idx = _numeric(summary[y_col]).idxmin()
        worst = summary.loc[worst_idx]
        sub = summary[summary["file_name"] == worst["file_name"]].sort_values("top_k")
        ax.plot(sub["top_k"], sub[y_col], marker="o", linewidth=1.8, color="#B44E2A", label=f"worst: {_short_label(str(worst['file_name']), 24)}")
    if y_col == "apex_shift_p95":
        ax.axhline(0.0, color="#333333", linewidth=0.8, alpha=0.55)
        nonzero = summary[_numeric(summary[y_col]) > 0]
        for _, sub in nonzero.groupby("index", sort=True):
            sub = sub.sort_values("top_k")
            ax.plot(sub["top_k"], sub[y_col], color="#B44E2A", linewidth=1.0, alpha=0.45)
    ax.set_xticks(topks)
    if log_y:
        ax.set_yscale("log")
    if pearson_axis:
        vals = _numeric(summary[y_col]).dropna()
        if not vals.empty:
            low = max(0.0, min(0.99, float(vals.min()) - 0.0005))
            ax.set_ylim(low, 1.00005)
    ax.set_xlabel("Top-K XIC targets per file")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(alpha=0.25, which="both")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    return _save(fig, out)


def _plot_error_vs_signal(target_df: pd.DataFrame, out: Path) -> list[str]:
    score_col = "seed_signal_score" if "seed_signal_score" in target_df.columns else "seed_intensity_sum"
    x = _numeric(target_df[score_col]).to_numpy(float) + 1.0
    y = _numeric(target_df["area_abs_rel_error"]).clip(lower=1e-15).to_numpy(float)
    mask = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
    x = x[mask]
    y = y[mask]
    if x.size == 0:
        return []
    fig, ax = plt.subplots(figsize=(7.6, 6.0))
    hb = ax.hexbin(x, y, gridsize=75, xscale="log", yscale="log", mincnt=1, cmap="viridis", bins="log", alpha=0.92)
    ax.scatter(x[:: max(1, x.size // 6000)], y[:: max(1, y.size // 6000)], s=4, c="white", alpha=0.12, linewidths=0)
    cbar = fig.colorbar(hb, ax=ax)
    cbar.set_label("log10(point density)")
    ax.set_xlabel("log10(Seed signal score + 1)")
    ax.set_ylabel("log10(XIC area absolute relative error)")
    ax.set_title("Top-2500 XIC Error vs Signal")
    ax.grid(alpha=0.22, which="both")
    fig.tight_layout()
    return _save(fig, out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate and plot Top-K XIC gradients from one max-K target table.")
    parser.add_argument("--target-table", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    parser.add_argument("--top-k", type=int, nargs="+", default=list(DEFAULT_TOP_K))
    parser.add_argument("--plot-error-vs-signal", action="store_true")
    args = parser.parse_args()

    target_df = pd.read_csv(args.target_table)
    top_k_values = tuple(sorted(set(int(v) for v in args.top_k)))
    rank_col = _rank_column(target_df)
    observed_max = int(_numeric(target_df[rank_col]).max())
    target_df = target_df[_numeric(target_df[rank_col]) <= max(top_k_values)].copy()
    per_file, all_file = summarize_topk(target_df, top_k_values)

    tables = args.out_root / "tables"
    plots = args.out_root / "plots" / "xic"
    tables.mkdir(parents=True, exist_ok=True)
    per_file_path = tables / "xic_topk_gradient_summary.csv"
    all_file_path = tables / "xic_topk_gradient_all_file_summary.csv"
    per_file.to_csv(per_file_path, index=False)
    all_file.to_csv(all_file_path, index=False)

    artifacts = [str(per_file_path), str(all_file_path)]
    artifacts.extend(_plot_metric(per_file, "area_abs_rel_error_p95", "Top-K XIC p95 Area Error Sensitivity", "per-file p95 area absolute relative error", plots / "xic_topk_gradient_p95_area_error", log_y=True))
    artifacts.extend(_plot_metric(per_file, "pearson_p05", "Top-K XIC Pearson p05 Sensitivity", "per-file p05 Pearson r", plots / "xic_topk_gradient_pearson_p05", pearson_axis=True))
    artifacts.extend(_plot_metric(per_file, "apex_shift_p95", "Top-K XIC Apex Shift p95 Sensitivity", "per-file p95 apex RT shift", plots / "xic_topk_gradient_apex_shift_p95"))
    if args.plot_error_vs_signal:
        artifacts.extend(_plot_error_vs_signal(target_df, plots / "top500_2500_xic_area_error_vs_signal_all_files"))

    manifest = {
        "target_table": str(args.target_table),
        "out_root": str(args.out_root),
        "requested_top_k": top_k_values,
        "observed_max_rank": observed_max,
        "file_count": int(per_file["file_name"].nunique()) if not per_file.empty else 0,
        "artifacts": artifacts,
    }
    manifest_path = plots / "xic_topk_gradient_from_maxk_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
