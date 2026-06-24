from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyopenms as oms


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALIDATION_ROOT = ROOT / "outputs"
DEFAULT_RESULT_ROOT = DEFAULT_VALIDATION_ROOT / "openms_feature_all20"
EXTRA_TOOL_PATHS = [
    Path(item)
    for item in os.environ.get("TRACKCODEC_EXTRA_TOOL_PATHS", "").split(os.pathsep)
    if item
]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


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
        writer.writerows([{key: _json_safe(row.get(key, "")) for key in fields} for row in rows])


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _ensure_path_for_tools() -> None:
    extra = [str(path) for path in EXTRA_TOOL_PATHS if path.exists()]
    old = os.environ.get("PATH", "")
    parts = old.split(os.pathsep) if old else []
    prefix = [p for p in extra if p not in parts]
    if prefix:
        os.environ["PATH"] = os.pathsep.join(prefix + parts)


def _which(name: str) -> str:
    _ensure_path_for_tools()
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Required tool not found in PATH: {name}")
    return path


def _run_command(cmd: list[str], log_path: Path) -> float:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.flush()
        subprocess.run(cmd, stdout=handle, stderr=subprocess.STDOUT, check=True)
    return float(time.perf_counter() - start)


def _infer_modality(file_name: str) -> str:
    upper = file_name.upper()
    if "ETD" in upper:
        return "ETD"
    if "DIA" in upper or "AIF" in upper:
        return "DIA"
    if "DDA" in upper:
        return "DDA"
    if "NEGATIVE" in upper:
        return "NEGATIVE"
    return "unknown"


def _slug(name: str) -> str:
    return name.replace(".mzML", "").replace(".reconstructed", "")


def _read_pairs(validation_root: Path) -> list[dict[str, Any]]:
    manifest = _read_csv_rows(validation_root / "tables" / "pair_manifest.csv")
    decoded = _read_csv_rows(validation_root / "tables" / "decode_status.csv")
    decoded_by_index = {int(row["index"]): row for row in decoded if str(row.get("index", "")).strip()}
    pairs: list[dict[str, Any]] = []
    for row in manifest:
        idx = int(row["index"])
        dec = decoded_by_index[idx]
        pairs.append(
            {
                "index": idx,
                "file_name": row["file_name"],
                "dataset": row.get("dataset", ""),
                "modality": _infer_modality(row["file_name"]),
                "original_path": row["original_path"],
                "reconstructed_path": dec["reconstructed_path"],
            }
        )
    return pairs


def _tmp_output_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")


def _ensure_ms1_only(input_path: str, output_path: Path, *, threads: int, log_path: Path) -> tuple[bool, float]:
    if output_path.exists() and output_path.stat().st_size > 0:
        return True, 0.0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_output_path(output_path)
    tmp_path.unlink(missing_ok=True)
    elapsed = _run_command(
        [
            _which("FileFilter"),
            "-in",
            input_path,
            "-out",
            str(tmp_path),
            "-peak_options:level",
            "1",
            "-peak_options:remove_chromatograms",
            "-peak_options:remove_empty",
            "-peak_options:indexed_file",
            "false",
            "-peak_options:zlib_compression",
            "false",
            "-threads",
            str(threads),
        ],
        log_path,
    )
    tmp_path.replace(output_path)
    return False, elapsed


def _ensure_peak_picked(input_path: str, output_path: Path, *, threads: int, log_path: Path) -> tuple[bool, float]:
    if output_path.exists() and output_path.stat().st_size > 0:
        return True, 0.0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_output_path(output_path)
    tmp_path.unlink(missing_ok=True)
    elapsed = _run_command(
        [
            _which("PeakPickerHiRes"),
            "-in",
            input_path,
            "-out",
            str(tmp_path),
            "-processOption",
            "lowmemory",
            "-threads",
            str(threads),
            "-algorithm:ms_levels",
            "1",
        ],
        log_path,
    )
    tmp_path.replace(output_path)
    return False, elapsed


def _ensure_feature_xml(input_path: Path, output_path: Path, *, threads: int, log_path: Path) -> tuple[bool, float]:
    if output_path.exists() and output_path.stat().st_size > 0:
        try:
            fmap = oms.FeatureMap()
            oms.FeatureXMLFile().load(str(output_path), fmap)
            return True, 0.0
        except Exception:
            output_path.unlink(missing_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = _tmp_output_path(output_path)
    tmp_path.unlink(missing_ok=True)
    elapsed = _run_command(
        [
            _which("FeatureFinderCentroided"),
            "-in",
            str(input_path),
            "-out",
            str(tmp_path),
            "-threads",
            str(threads),
        ],
        log_path,
    )
    tmp_path.replace(output_path)
    return False, elapsed


def _valid_feature_xml(path: Path) -> bool:
    if not path.exists() or path.stat().st_size <= 0:
        return False
    try:
        fmap = oms.FeatureMap()
        oms.FeatureXMLFile().load(str(path), fmap)
    except Exception:
        return False
    return True


def _load_feature_table(path: Path) -> pd.DataFrame:
    fmap = oms.FeatureMap()
    oms.FeatureXMLFile().load(str(path), fmap)
    rows: list[dict[str, Any]] = []
    for idx, feature in enumerate(fmap):
        rows.append(
            {
                "feature_idx": int(idx),
                "mz": float(feature.getMZ()),
                "rt_sec": float(feature.getRT()),
                "intensity": float(feature.getIntensity()),
                "charge": int(feature.getCharge()),
                "quality": float(feature.getOverallQuality()),
            }
        )
    return pd.DataFrame(rows)


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 2 or y.size < 2:
        return float("nan")
    xs = [float(v) for v in x]
    ys = [float(v) for v in y]
    n = min(len(xs), len(ys))
    if n < 2:
        return float("nan")
    mean_x = math.fsum(xs[:n]) / n
    mean_y = math.fsum(ys[:n]) / n
    x_ss = 0.0
    y_ss = 0.0
    xy_ss = 0.0
    for xv, yv in zip(xs[:n], ys[:n]):
        dx = xv - mean_x
        dy = yv - mean_y
        x_ss += dx * dx
        y_ss += dy * dy
        xy_ss += dx * dy
    if x_ss == 0.0 and y_ss == 0.0:
        return 1.0
    if x_ss == 0.0 or y_ss == 0.0:
        return 0.0
    return float(xy_ss / math.sqrt(x_ss * y_ss))


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if x.size < 2 or y.size < 2:
        return float("nan")
    x_rank = pd.Series(x).rank(method="average").to_numpy(dtype=np.float64)
    y_rank = pd.Series(y).rank(method="average").to_numpy(dtype=np.float64)
    return _pearson(x_rank, y_rank)


def _safe_stat(arr: np.ndarray, fn: str) -> float:
    if arr.size == 0:
        return float("nan")
    if fn == "median":
        return float(np.median(arr))
    if fn == "p95":
        return float(np.percentile(arr, 95))
    if fn == "max":
        return float(np.max(arr))
    raise ValueError(fn)


def _match_features(orig_df: pd.DataFrame, recon_df: pd.DataFrame, *, mz_ppm: float, rt_sec: float) -> pd.DataFrame:
    if orig_df.empty or recon_df.empty:
        return pd.DataFrame()
    recon_sorted = recon_df.sort_values(["mz", "rt_sec"], ascending=[True, True]).reset_index(drop=True)
    recon_mz = recon_sorted["mz"].to_numpy(dtype=np.float64)
    recon_rt = recon_sorted["rt_sec"].to_numpy(dtype=np.float64)
    recon_int = recon_sorted["intensity"].to_numpy(dtype=np.float64)
    recon_id = recon_sorted["feature_idx"].to_numpy(dtype=np.int64)
    used = np.zeros(len(recon_sorted), dtype=bool)
    matches: list[dict[str, Any]] = []
    orig_sorted = orig_df.sort_values(["intensity", "mz"], ascending=[False, True]).reset_index(drop=True)
    for row in orig_sorted.itertuples(index=False):
        mz_tol = float(row.mz) * mz_ppm * 1e-6
        left = int(np.searchsorted(recon_mz, float(row.mz) - mz_tol, side="left"))
        right = int(np.searchsorted(recon_mz, float(row.mz) + mz_tol, side="right"))
        if right <= left:
            continue
        candidate_idx = np.arange(left, right, dtype=np.int64)
        candidate_idx = candidate_idx[~used[candidate_idx]]
        if candidate_idx.size == 0:
            continue
        rt_diff = np.abs(recon_rt[candidate_idx] - float(row.rt_sec))
        candidate_idx = candidate_idx[rt_diff <= rt_sec]
        if candidate_idx.size == 0:
            continue
        ppm_diff = (recon_mz[candidate_idx] - float(row.mz)) / max(float(row.mz), 1e-12) * 1e6
        rt_diff = recon_rt[candidate_idx] - float(row.rt_sec)
        score = (np.abs(ppm_diff) / max(mz_ppm, 1e-12)) ** 2 + (np.abs(rt_diff) / max(rt_sec, 1e-12)) ** 2
        best_local = int(np.argmin(score))
        best_idx = int(candidate_idx[best_local])
        used[best_idx] = True
        orig_log2 = math.log2(float(row.intensity) + 1.0)
        recon_log2 = math.log2(float(recon_int[best_idx]) + 1.0)
        matches.append(
            {
                "orig_feature_idx": int(row.feature_idx),
                "recon_feature_idx": int(recon_id[best_idx]),
                "orig_mz": float(row.mz),
                "recon_mz": float(recon_mz[best_idx]),
                "orig_rt_sec": float(row.rt_sec),
                "recon_rt_sec": float(recon_rt[best_idx]),
                "orig_intensity": float(row.intensity),
                "recon_intensity": float(recon_int[best_idx]),
                "abs_mz_diff_ppm": float(abs(ppm_diff[best_local])),
                "abs_rt_diff_sec": float(abs(rt_diff[best_local])),
                "orig_log2_intensity": float(orig_log2),
                "recon_log2_intensity": float(recon_log2),
                "abs_log2_fc": float(abs(recon_log2 - orig_log2)),
            }
        )
    return pd.DataFrame(matches)


def _plot_pair_scatter(file_name: str, matches_df: pd.DataFrame, plot_path: Path) -> None:
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(5.2, 4.8))
    if matches_df.empty:
        ax.text(0.5, 0.5, "No matched features", ha="center", va="center", transform=ax.transAxes)
    else:
        sample = matches_df
        if len(sample) > 12000:
            sample = sample.sample(n=12000, random_state=42)
        x = sample["orig_log2_intensity"].to_numpy(dtype=np.float64)
        y = sample["recon_log2_intensity"].to_numpy(dtype=np.float64)
        ax.scatter(x, y, s=6, alpha=0.18, linewidths=0)
        lo = float(min(np.min(x), np.min(y)))
        hi = float(max(np.max(x), np.max(y)))
        ax.plot([lo, hi], [lo, hi], color="black", linestyle="--", linewidth=1.0)
    ax.set_title(file_name.replace(".mzML", ""), fontsize=9)
    ax.set_xlabel("Original log2(feature intensity + 1)")
    ax.set_ylabel("Reconstructed log2(feature intensity + 1)")
    fig.tight_layout()
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)


def _plot_summary(summary_df: pd.DataFrame, plot_path: Path) -> None:
    plot_path.parent.mkdir(parents=True, exist_ok=True)
    df = summary_df.copy()
    df["file_label"] = df["file"].str.replace(".mzML", "", regex=False)
    x = np.arange(len(df), dtype=np.float64)
    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    axes[0].plot(x, df["recall_vs_original"], marker="o", linewidth=1.0, label="Recall vs original")
    axes[0].plot(x, df["precision_vs_reconstructed"], marker="s", linewidth=1.0, label="Precision vs reconstructed")
    axes[0].plot(x, df["f1_score"], color="black", marker="^", linewidth=1.0, label="F1")
    axes[0].set_ylim(0.0, 1.05)
    axes[0].set_ylabel("Feature overlap")
    axes[0].legend(loc="lower right")

    axes[1].plot(x, df["matched_log2_pearson"], marker="o", linewidth=1.0, label="Matched log2 Pearson")
    axes[1].plot(x, df["matched_log2_spearman"], marker="s", linewidth=1.0, label="Matched log2 Spearman")
    axes[1].set_ylim(0.0, 1.05)
    axes[1].set_ylabel("Quant correlation")
    axes[1].legend(loc="lower right")
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(df["file_label"], rotation=45, ha="right", fontsize=8)
    fig.tight_layout()
    fig.savefig(plot_path, dpi=180)
    plt.close(fig)


def _summary_from_matches(
    cfg: dict[str, Any],
    *,
    orig_df: pd.DataFrame,
    recon_df: pd.DataFrame,
    matches_df: pd.DataFrame,
    status: str,
    error: str = "",
) -> dict[str, Any]:
    matched = int(len(matches_df))
    orig_count = int(len(orig_df))
    recon_count = int(len(recon_df))
    recall = float(matched / orig_count) if orig_count else float("nan")
    precision = float(matched / recon_count) if recon_count else float("nan")
    f1 = float(2 * precision * recall / (precision + recall)) if precision + recall > 0 else float("nan")

    orig_log = matches_df["orig_log2_intensity"].to_numpy(dtype=np.float64) if matched else np.asarray([], dtype=np.float64)
    recon_log = matches_df["recon_log2_intensity"].to_numpy(dtype=np.float64) if matched else np.asarray([], dtype=np.float64)
    abs_log2_fc = matches_df["abs_log2_fc"].to_numpy(dtype=np.float64) if matched else np.asarray([], dtype=np.float64)
    abs_rt_diff = matches_df["abs_rt_diff_sec"].to_numpy(dtype=np.float64) if matched else np.asarray([], dtype=np.float64)
    abs_mz_diff = matches_df["abs_mz_diff_ppm"].to_numpy(dtype=np.float64) if matched else np.asarray([], dtype=np.float64)

    return {
        "index": cfg["index"],
        "file": cfg["file_name"],
        "dataset": cfg["dataset"],
        "modality": cfg["modality"],
        "status": status,
        "error": error,
        "original_feature_count": orig_count,
        "reconstructed_feature_count": recon_count,
        "matched_feature_count": matched,
        "recall_vs_original": recall,
        "precision_vs_reconstructed": precision,
        "f1_score": f1,
        "matched_log2_pearson": _pearson(orig_log, recon_log),
        "matched_log2_spearman": _spearman(orig_log, recon_log),
        "abs_log2_fc_median": _safe_stat(abs_log2_fc, "median"),
        "abs_log2_fc_p95": _safe_stat(abs_log2_fc, "p95"),
        "abs_log2_fc_max": _safe_stat(abs_log2_fc, "max"),
        "abs_rt_diff_sec_median": _safe_stat(abs_rt_diff, "median"),
        "abs_rt_diff_sec_p95": _safe_stat(abs_rt_diff, "p95"),
        "abs_mz_diff_ppm_median": _safe_stat(abs_mz_diff, "median"),
        "abs_mz_diff_ppm_p95": _safe_stat(abs_mz_diff, "p95"),
    }


def _summarize_file(
    cfg: dict[str, Any],
    *,
    result_root: Path,
    tool_threads: int,
    mz_ppm: float,
    rt_sec: float,
    plot_pairs: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    name = cfg["file_name"]
    slug = f"{int(cfg['index']):02d}_{_slug(name)}"
    logs_dir = result_root / "logs" / slug
    filtered_dir = result_root / "ms1_only"
    picked_dir = result_root / "peak_picked"
    feature_dir = result_root / "featurexml"
    table_dir = result_root / "tables" / "matched_pairs"
    plot_dir = result_root / "plots" / "feature_scatter"

    orig_ms1 = filtered_dir / "original" / f"{slug}.ms1_only.mzML"
    recon_ms1 = filtered_dir / "reconstructed" / f"{slug}.ms1_only.mzML"
    orig_picked = picked_dir / "original" / f"{slug}.picked.mzML"
    recon_picked = picked_dir / "reconstructed" / f"{slug}.picked.mzML"
    orig_feature = feature_dir / "original" / f"{slug}.featureXML"
    recon_feature = feature_dir / "reconstructed" / f"{slug}.featureXML"
    matched_table = table_dir / f"{slug}.matched_features.csv"
    scatter_plot = plot_dir / f"{slug}.matched_feature_scatter.png"

    if matched_table.exists() and matched_table.stat().st_size > 0 and _valid_feature_xml(orig_feature) and _valid_feature_xml(recon_feature):
        orig_df = _load_feature_table(orig_feature)
        recon_df = _load_feature_table(recon_feature)
        matches_df = pd.read_csv(matched_table)
        summary_row = _summary_from_matches(cfg, orig_df=orig_df, recon_df=recon_df, matches_df=matches_df, status="cached")
        run_rows = [
            {"index": cfg["index"], "file": name, "step": "original_ms1_filter", "cached": 1, "elapsed_s": 0.0, "output_path": str(orig_ms1)},
            {"index": cfg["index"], "file": name, "step": "reconstructed_ms1_filter", "cached": 1, "elapsed_s": 0.0, "output_path": str(recon_ms1)},
            {"index": cfg["index"], "file": name, "step": "original_peak_pick", "cached": 1, "elapsed_s": 0.0, "output_path": str(orig_picked)},
            {"index": cfg["index"], "file": name, "step": "reconstructed_peak_pick", "cached": 1, "elapsed_s": 0.0, "output_path": str(recon_picked)},
            {"index": cfg["index"], "file": name, "step": "original_feature_finder", "cached": 1, "elapsed_s": 0.0, "output_path": str(orig_feature)},
            {"index": cfg["index"], "file": name, "step": "reconstructed_feature_finder", "cached": 1, "elapsed_s": 0.0, "output_path": str(recon_feature)},
        ]
        return summary_row, [], run_rows

    orig_filter_cached, orig_filter_elapsed = _ensure_ms1_only(cfg["original_path"], orig_ms1, threads=tool_threads, log_path=logs_dir / "original_filefilter.log")
    recon_filter_cached, recon_filter_elapsed = _ensure_ms1_only(cfg["reconstructed_path"], recon_ms1, threads=tool_threads, log_path=logs_dir / "reconstructed_filefilter.log")
    orig_pick_cached, orig_pick_elapsed = _ensure_peak_picked(str(orig_ms1), orig_picked, threads=tool_threads, log_path=logs_dir / "original_peakpicker.log")
    recon_pick_cached, recon_pick_elapsed = _ensure_peak_picked(str(recon_ms1), recon_picked, threads=tool_threads, log_path=logs_dir / "reconstructed_peakpicker.log")
    orig_ff_cached, orig_ff_elapsed = _ensure_feature_xml(orig_picked, orig_feature, threads=tool_threads, log_path=logs_dir / "original_featurefinder.log")
    recon_ff_cached, recon_ff_elapsed = _ensure_feature_xml(recon_picked, recon_feature, threads=tool_threads, log_path=logs_dir / "reconstructed_featurefinder.log")

    orig_df = _load_feature_table(orig_feature)
    recon_df = _load_feature_table(recon_feature)
    matches_df = _match_features(orig_df, recon_df, mz_ppm=mz_ppm, rt_sec=rt_sec)

    pair_rows: list[dict[str, Any]] = []
    if not matches_df.empty:
        for row in matches_df.to_dict(orient="records"):
            pair_rows.append({"index": cfg["index"], "file": name, "dataset": cfg["dataset"], "modality": cfg["modality"], **row})
        _write_csv(pair_rows, matched_table)

    if plot_pairs:
        _plot_pair_scatter(name, matches_df, scatter_plot)
    summary_row = _summary_from_matches(cfg, orig_df=orig_df, recon_df=recon_df, matches_df=matches_df, status="ok")
    run_rows = [
        {"index": cfg["index"], "file": name, "step": "original_ms1_filter", "cached": int(orig_filter_cached), "elapsed_s": float(orig_filter_elapsed), "output_path": str(orig_ms1)},
        {"index": cfg["index"], "file": name, "step": "reconstructed_ms1_filter", "cached": int(recon_filter_cached), "elapsed_s": float(recon_filter_elapsed), "output_path": str(recon_ms1)},
        {"index": cfg["index"], "file": name, "step": "original_peak_pick", "cached": int(orig_pick_cached), "elapsed_s": float(orig_pick_elapsed), "output_path": str(orig_picked)},
        {"index": cfg["index"], "file": name, "step": "reconstructed_peak_pick", "cached": int(recon_pick_cached), "elapsed_s": float(recon_pick_elapsed), "output_path": str(recon_picked)},
        {"index": cfg["index"], "file": name, "step": "original_feature_finder", "cached": int(orig_ff_cached), "elapsed_s": float(orig_ff_elapsed), "output_path": str(orig_feature)},
        {"index": cfg["index"], "file": name, "step": "reconstructed_feature_finder", "cached": int(recon_ff_cached), "elapsed_s": float(recon_ff_elapsed), "output_path": str(recon_feature)},
    ]
    return summary_row, pair_rows, run_rows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenMS feature validation for all 20 TrackCodec files.")
    parser.add_argument("--validation-root", type=Path, default=DEFAULT_VALIDATION_ROOT)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--tool-threads", type=int, default=8)
    parser.add_argument("--mz-ppm", type=float, default=10.0)
    parser.add_argument("--rt-sec", type=float, default=10.0)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--write-combined-pairs", action="store_true")
    parser.add_argument("--plot-pairs", action="store_true")
    parser.add_argument("--plot-summary", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pairs = _read_pairs(args.validation_root)
    if args.only:
        terms = [term.lower() for term in args.only]
        pairs = [row for row in pairs if any(term in row["file_name"].lower() for term in terms)]

    result_root = args.result_root
    (result_root / "tables").mkdir(parents=True, exist_ok=True)
    (result_root / "plots").mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    def collect_result(cfg: dict[str, Any], result: tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]] | Exception) -> None:
        file_name = cfg["file_name"]
        if isinstance(result, Exception):
            summary_row = _summary_from_matches(
                cfg,
                orig_df=pd.DataFrame(),
                recon_df=pd.DataFrame(),
                matches_df=pd.DataFrame(),
                status="failed",
                error=repr(result),
            )
            file_pair_rows: list[dict[str, Any]] = []
            file_run_rows: list[dict[str, Any]] = []
            print(f"[openms-all20] failed {file_name} | error={result!r}", flush=True)
        else:
            summary_row, file_pair_rows, file_run_rows = result
            print(
                f"[openms-all20] done {file_name} | status={summary_row['status']} | matched={summary_row['matched_feature_count']} | f1={summary_row['f1_score']:.4f}",
                flush=True,
            )
        summary_rows.append(summary_row)
        if args.write_combined_pairs:
            pair_rows.extend(file_pair_rows)
        run_rows.extend(file_run_rows)

    if int(args.jobs) <= 1:
        for cfg in pairs:
            file_name = cfg["file_name"]
            try:
                result = _summarize_file(
                    cfg,
                    result_root=result_root,
                    tool_threads=int(args.tool_threads),
                    mz_ppm=float(args.mz_ppm),
                    rt_sec=float(args.rt_sec),
                    plot_pairs=bool(args.plot_pairs),
                )
            except Exception as exc:
                collect_result(cfg, exc)
            else:
                collect_result(cfg, result)
    else:
        with ThreadPoolExecutor(max_workers=max(int(args.jobs), 1)) as executor:
            future_to_cfg = {
                executor.submit(
                    _summarize_file,
                    cfg,
                    result_root=result_root,
                    tool_threads=int(args.tool_threads),
                    mz_ppm=float(args.mz_ppm),
                    rt_sec=float(args.rt_sec),
                    plot_pairs=bool(args.plot_pairs),
                ): cfg
                for cfg in pairs
            }
            for future in as_completed(future_to_cfg):
                cfg = future_to_cfg[future]
                try:
                    collect_result(cfg, future.result())
                except Exception as exc:
                    collect_result(cfg, exc)

    summary_rows.sort(key=lambda row: int(row["index"]))
    pair_rows.sort(key=lambda row: (int(row["index"]), int(row["orig_feature_idx"])))
    run_rows.sort(key=lambda row: (int(row["index"]), str(row["step"])))

    tables_dir = result_root / "tables"
    _write_csv(summary_rows, tables_dir / "trackcodec_openms_feature_all20_summary.csv")
    _write_csv(run_rows, tables_dir / "trackcodec_openms_feature_all20_run_log.csv")
    if args.write_combined_pairs and pair_rows:
        _write_csv(pair_rows, tables_dir / "trackcodec_openms_feature_all20_matched_pairs_all.csv")

    summary_df = pd.DataFrame(summary_rows)
    if args.plot_summary and not summary_df.empty:
        _plot_summary(summary_df, result_root / "plots" / "trackcodec_openms_feature_all20_summary.png")

    aggregate = {
        "file_count": int(len(summary_rows)),
        "elapsed_s": float(time.perf_counter() - t0),
        "mean_recall_vs_original": float(summary_df["recall_vs_original"].mean()) if not summary_df.empty else float("nan"),
        "mean_precision_vs_reconstructed": float(summary_df["precision_vs_reconstructed"].mean()) if not summary_df.empty else float("nan"),
        "mean_f1_score": float(summary_df["f1_score"].mean()) if not summary_df.empty else float("nan"),
        "median_matched_log2_pearson": float(summary_df["matched_log2_pearson"].median()) if not summary_df.empty else float("nan"),
        "median_abs_log2_fc_p95": float(summary_df["abs_log2_fc_p95"].median()) if not summary_df.empty else float("nan"),
    }
    _write_json(result_root / "summary.json", aggregate)
    print(json.dumps(_json_safe(aggregate), indent=2), flush=True)


if __name__ == "__main__":
    main()
