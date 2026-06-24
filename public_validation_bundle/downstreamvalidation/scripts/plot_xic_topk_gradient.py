from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_TOP_K = (100, 500, 1000, 2000)


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _short_label(name: str, limit: int = 30) -> str:
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


def _stat(values: pd.Series, q: float | None = None, *, name: str | None = None) -> float:
    arr = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
    if arr.size == 0:
        return float("nan")
    if q is not None:
        return float(np.percentile(arr, q))
    if name == "median":
        return float(np.median(arr))
    if name == "max":
        return float(np.max(arr))
    if name == "min":
        return float(np.min(arr))
    raise ValueError(name or str(q))


def summarize_topk(target_df: pd.DataFrame, top_k_values: tuple[int, ...]) -> pd.DataFrame:
    rows = []
    target_df = target_df.copy()
    target_df["rank"] = _num(target_df, "rank")
    for (index, file_name), file_df in target_df.groupby(["index", "file_name"], sort=True):
        file_df = file_df.sort_values("rank")
        for top_k in top_k_values:
            sub = file_df[file_df["rank"] <= top_k]
            if sub.empty:
                continue
            rows.append(
                {
                    "index": int(index),
                    "file_name": file_name,
                    "short_label": _short_label(file_name),
                    "top_k": int(top_k),
                    "target_count": int(len(sub)),
                    "area_abs_rel_error_median": _stat(sub["area_abs_rel_error"], name="median"),
                    "area_abs_rel_error_p95": _stat(sub["area_abs_rel_error"], 95),
                    "area_abs_rel_error_max": _stat(sub["area_abs_rel_error"], name="max"),
                    "pearson_median": _stat(sub["pearson"], name="median"),
                    "pearson_p05": _stat(sub["pearson"], 5),
                    "apex_shift_p95": _stat(sub["apex_shift"], 95),
                }
            )
    return pd.DataFrame(rows)


def _save(fig: plt.Figure, path: Path) -> list[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = []
    for suffix in (".png", ".pdf"):
        p = path.with_suffix(suffix)
        fig.savefig(p, dpi=240, bbox_inches="tight", pad_inches=0.18)
        out.append(str(p))
    plt.close(fig)
    return out


def _plot_metric(summary: pd.DataFrame, y_col: str, title: str, ylabel: str, out: Path, *, log_y: bool = False) -> list[str]:
    fig, ax = plt.subplots(figsize=(7.4, 5.2))
    topks = sorted(summary["top_k"].unique())
    for _, sub in summary.groupby("index", sort=True):
        sub = sub.sort_values("top_k")
        ax.plot(sub["top_k"], sub[y_col], color="#9AA7B0", linewidth=0.8, alpha=0.35)
    agg = summary.groupby("top_k")[y_col].agg(["median", "min", "max"]).reindex(topks)
    ax.plot(topks, agg["median"], marker="o", linewidth=2.2, color="#2F5D62", label="median across files")
    ax.fill_between(topks, agg["min"], agg["max"], color="#2F5D62", alpha=0.15, label="min-max across files")
    ax.set_xticks(topks)
    if log_y:
        ax.set_yscale("log")
    ax.set_xlabel("Top-K XIC targets per file")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(alpha=0.25, which="both")
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    return _save(fig, out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot Top-K XIC sensitivity from a target-level XIC metrics table.")
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--top-k", type=int, action="append", default=list(DEFAULT_TOP_K))
    args = parser.parse_args()

    tables = args.result_root / "tables"
    plots = args.result_root / "plots" / "xic"
    target_path = tables / "xic_target_metrics.csv"
    target_df = pd.read_csv(target_path)
    top_k_values = tuple(sorted(set(int(v) for v in args.top_k)))
    observed_max = int(pd.to_numeric(target_df["rank"], errors="coerce").max())
    usable_top_k = tuple(k for k in top_k_values if k <= observed_max)
    if not usable_top_k:
        raise ValueError(f"No requested Top-K values <= observed max rank {observed_max}")

    summary = summarize_topk(target_df, usable_top_k)
    tables.mkdir(parents=True, exist_ok=True)
    summary_path = tables / "xic_topk_gradient_summary.csv"
    summary.to_csv(summary_path, index=False)

    artifacts = [str(summary_path)]
    artifacts.extend(
        _plot_metric(
            summary,
            "area_abs_rel_error_p95",
            "Top-K XIC p95 Area Error Sensitivity",
            "p95 XIC area absolute relative error",
            plots / "xic_topk_gradient_p95_area_error",
            log_y=True,
        )
    )
    artifacts.extend(
        _plot_metric(
            summary,
            "pearson_p05",
            "Top-K XIC Pearson p05 Sensitivity",
            "p05 Pearson r",
            plots / "xic_topk_gradient_pearson_p05",
            log_y=False,
        )
    )
    artifacts.extend(
        _plot_metric(
            summary,
            "apex_shift_p95",
            "Top-K XIC Apex Shift Sensitivity",
            "p95 apex RT shift",
            plots / "xic_topk_gradient_apex_shift_p95",
            log_y=False,
        )
    )
    manifest = {
        "result_root": str(args.result_root),
        "requested_top_k": top_k_values,
        "usable_top_k": usable_top_k,
        "observed_max_rank": observed_max,
        "artifacts": artifacts,
    }
    manifest_path = plots / "xic_topk_gradient_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == "__main__":
    main()
