from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_WORK_ROOT = Path(
    os.environ.get("TRACKCODEC_SEARCH_WORK_ROOT", str(Path(__file__).resolve().parents[1] / "outputs" / "search_work"))
)


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text.replace(".mzML", "")).strip("._") or "item"


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
        writer.writerows(rows)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _pearson(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan"), float("nan")
    x = x[mask]
    y = y[mask]
    x_centered = x - float(np.mean(x))
    y_centered = y - float(np.mean(y))
    x_ss = float(np.sum(x_centered * x_centered))
    y_ss = float(np.sum(y_centered * y_centered))
    if x_ss == 0.0 or y_ss == 0.0:
        return float("nan"), float("nan")
    return float(np.sum(x_centered * y_centered) / math.sqrt(x_ss * y_ss)), float("nan")


def _spearman(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan"), float("nan")
    x_rank = pd.Series(x[mask]).rank(method="average").to_numpy(dtype=float)
    y_rank = pd.Series(y[mask]).rank(method="average").to_numpy(dtype=float)
    rho, _ = _pearson(x_rank, y_rank)
    return rho, float("nan")


def _resolve_matrix_run_column(df: pd.DataFrame, run_name: str) -> str:
    if run_name in df.columns:
        return run_name
    normalized = run_name.replace("\\", "/").lower()
    candidates: list[str] = []
    for col in df.columns:
        text = str(col).replace("\\", "/").lower()
        if text.endswith("/" + normalized) or text.endswith(normalized):
            candidates.append(str(col))
    if candidates:
        candidates.sort(key=lambda item: (len(item), item))
        return candidates[0]
    numeric_cols = [str(col) for col in df.columns if pd.api.types.is_numeric_dtype(df[col])]
    if len(numeric_cols) == 1:
        return numeric_cols[0]
    raise KeyError(f"Could not resolve run column {run_name!r} from {list(df.columns)}")


def _detect_merge_key(df: pd.DataFrame, kind: str) -> str:
    if kind == "precursor":
        candidates = ["Precursor.Id", "PrecursorID", "Precursor"]
    else:
        candidates = ["Protein.Group", "Protein.Groups", "Protein.Ids", "Protein.Ids.Group", "Protein.Names", "Genes"]
    for col in candidates:
        if col in df.columns:
            return col
    return str(df.columns[0])


def _load_diann_quant_single(matrix_path: Path, *, run_name: str, kind: str) -> tuple[pd.DataFrame, str, str]:
    df = pd.read_csv(matrix_path, sep="\t", low_memory=False)
    qty_col = _resolve_matrix_run_column(df, run_name)
    key_col = _detect_merge_key(df, kind)
    out = df[[key_col, qty_col]].copy()
    out.columns = ["key", "qty"]
    out["qty"] = pd.to_numeric(out["qty"], errors="coerce")
    out = out.replace({0: np.nan}).dropna(subset=["qty"])
    return out, key_col, qty_col


def _load_diann_stats(path: Path) -> dict[str, float]:
    df = pd.read_csv(path, sep="\t", low_memory=False)
    if df.empty:
        return {}
    return {str(key): value for key, value in df.iloc[0].to_dict().items()}


def _detect_output_file(run_dir: Path, suffix: str) -> Path:
    direct = run_dir / suffix
    if direct.exists() and direct.stat().st_size > 0:
        return direct
    matches = sorted(path for path in run_dir.glob(f"*{suffix}") if path.is_file() and path.stat().st_size > 0)
    if matches:
        return matches[0]
    raise FileNotFoundError(f"Missing DIA-NN output *{suffix} under {run_dir}")


def _index_from_name(name: str) -> int | None:
    try:
        return int(name.split("_", 1)[0])
    except Exception:
        return None


def _file_from_run_dir(run_dir: Path) -> str:
    name = run_dir.name
    if re.match(r"^\d{2}_", name):
        name = name.split("_", 1)[1]
    if not name.lower().endswith(".mzml"):
        name = f"{name}.mzML"
    return name


def recover_one(pipeline_root: Path) -> dict[str, Any]:
    result_root = pipeline_root / "diann_single_run"
    runs_root = result_root / "runs"
    index = _index_from_name(pipeline_root.name)
    if index is None or not runs_root.exists():
        return {"pipeline_root": str(pipeline_root), "status": "skipped"}
    run_dirs = [path for path in sorted(runs_root.iterdir()) if path.is_dir()]
    if not run_dirs:
        return {"pipeline_root": str(pipeline_root), "status": "no_runs"}
    run_dir = run_dirs[0]
    file_name = _file_from_run_dir(run_dir)
    original_dir = run_dir / "original"
    reconstructed_dir = run_dir / "reconstructed"
    errors: list[str] = []
    try:
        orig_stats_path = _detect_output_file(original_dir, "report.stats.tsv")
        recon_stats_path = _detect_output_file(reconstructed_dir, "report.stats.tsv")
        orig_pr_path = _detect_output_file(original_dir, "pr_matrix.tsv")
        recon_pr_path = _detect_output_file(reconstructed_dir, "pr_matrix.tsv")
        orig_pg_path = _detect_output_file(original_dir, "pg_matrix.tsv")
        recon_pg_path = _detect_output_file(reconstructed_dir, "pg_matrix.tsv")
        orig_stats = _load_diann_stats(orig_stats_path)
        recon_stats = _load_diann_stats(recon_stats_path)
        orig_run = str(orig_stats.get("File.Name") or orig_pr_path.name)
        recon_run = str(recon_stats.get("File.Name") or recon_pr_path.name)
        orig_pr, _, orig_pr_col = _load_diann_quant_single(orig_pr_path, run_name=orig_run, kind="precursor")
        recon_pr, _, recon_pr_col = _load_diann_quant_single(recon_pr_path, run_name=recon_run, kind="precursor")
        orig_pg, _, orig_pg_col = _load_diann_quant_single(orig_pg_path, run_name=orig_run, kind="protein")
        recon_pg, _, recon_pg_col = _load_diann_quant_single(recon_pg_path, run_name=recon_run, kind="protein")
    except Exception as exc:
        errors.append(str(exc))
        _write_csv([], result_root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv")
        return {"pipeline_root": str(pipeline_root), "index": index, "file": file_name, "status": "failed", "errors": "; ".join(errors)}

    pr = orig_pr.merge(recon_pr, on="key", how="inner", suffixes=("_orig", "_recon"))
    pr.columns = ["key", "orig_qty", "recon_qty"]
    pg = orig_pg.merge(recon_pg, on="key", how="inner", suffixes=("_orig", "_recon"))
    pg.columns = ["key", "orig_qty", "recon_qty"]
    slug = f"{int(index):02d}_{_safe_name(file_name)}"
    pair_dir = result_root / "paired_tables"
    pair_dir.mkdir(parents=True, exist_ok=True)
    pr.to_csv(pair_dir / f"{slug}.precursor.diann_pairwise.tsv", sep="\t", index=False)
    pg.to_csv(pair_dir / f"{slug}.protein_group.diann_pairwise.tsv", sep="\t", index=False)

    orig_pr_keys = set(orig_pr["key"].astype(str))
    recon_pr_keys = set(recon_pr["key"].astype(str))
    orig_pg_keys = set(orig_pg["key"].astype(str))
    recon_pg_keys = set(recon_pg["key"].astype(str))
    pr_x = np.log2(pr["orig_qty"].to_numpy(dtype=float) + 1.0) if not pr.empty else np.asarray([])
    pr_y = np.log2(pr["recon_qty"].to_numpy(dtype=float) + 1.0) if not pr.empty else np.asarray([])
    pg_x = np.log2(pg["orig_qty"].to_numpy(dtype=float) + 1.0) if not pg.empty else np.asarray([])
    pg_y = np.log2(pg["recon_qty"].to_numpy(dtype=float) + 1.0) if not pg.empty else np.asarray([])
    pr_pearson, pr_pearson_p = _pearson(pr_x, pr_y)
    pr_spearman, pr_spearman_p = _spearman(pr_x, pr_y)
    pg_pearson, pg_pearson_p = _pearson(pg_x, pg_y)
    pg_spearman, pg_spearman_p = _spearman(pg_x, pg_y)
    pr_delta = np.abs(pr_y - pr_x)
    pg_delta = np.abs(pg_y - pg_x)
    row = {
        "index": index,
        "file": file_name,
        "original_precursors_identified": int(float(orig_stats.get("Precursors.Identified", 0) or 0)),
        "reconstructed_precursors_identified": int(float(recon_stats.get("Precursors.Identified", 0) or 0)),
        "original_proteins_identified": int(float(orig_stats.get("Proteins.Identified", 0) or 0)),
        "reconstructed_proteins_identified": int(float(recon_stats.get("Proteins.Identified", 0) or 0)),
        "original_quantified_precursors": len(orig_pr_keys),
        "reconstructed_quantified_precursors": len(recon_pr_keys),
        "shared_quantified_precursors": len(orig_pr_keys & recon_pr_keys),
        "quantified_precursor_jaccard": _jaccard(orig_pr_keys, recon_pr_keys),
        "original_quantified_protein_groups": len(orig_pg_keys),
        "reconstructed_quantified_protein_groups": len(recon_pg_keys),
        "shared_quantified_protein_groups": len(orig_pg_keys & recon_pg_keys),
        "quantified_protein_group_jaccard": _jaccard(orig_pg_keys, recon_pg_keys),
        "shared_precursor_quant_entries": len(pr),
        "precursor_quant_log2_pearson": pr_pearson,
        "precursor_quant_log2_pearson_p": pr_pearson_p,
        "precursor_quant_log2_spearman": pr_spearman,
        "precursor_quant_log2_spearman_p": pr_spearman_p,
        "precursor_abs_log2_fc_median": float(np.nanmedian(pr_delta)) if len(pr_delta) else float("nan"),
        "precursor_abs_log2_fc_p95": float(np.nanpercentile(pr_delta, 95)) if len(pr_delta) else float("nan"),
        "shared_protein_quant_entries": len(pg),
        "protein_quant_log2_pearson": pg_pearson,
        "protein_quant_log2_pearson_p": pg_pearson_p,
        "protein_quant_log2_spearman": pg_spearman,
        "protein_quant_log2_spearman_p": pg_spearman_p,
        "protein_abs_log2_fc_median": float(np.nanmedian(pg_delta)) if len(pg_delta) else float("nan"),
        "protein_abs_log2_fc_p95": float(np.nanpercentile(pg_delta, 95)) if len(pg_delta) else float("nan"),
        "original_pr_matrix_column": orig_pr_col,
        "reconstructed_pr_matrix_column": recon_pr_col,
        "original_pg_matrix_column": orig_pg_col,
        "reconstructed_pg_matrix_column": recon_pg_col,
    }
    _write_csv([row], result_root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv")
    summary = {
        "stage": "diann",
        "recovered": True,
        "file_count": 1,
        "completed_summary_count": 1,
        "summary_table": str(result_root / "tables" / "trackcodec_diann_single_run_summary_per_file.csv"),
    }
    (result_root / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return {"pipeline_root": str(pipeline_root), "index": index, "file": file_name, "status": "ok", "pairwise_rows": 2}


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover DIA-NN single-run summaries from existing report matrices.")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--only-index", type=int, action="append", default=[])
    args = parser.parse_args()
    roots = sorted((args.work_root / "pipeline_runs").glob("*/diann_single_run"))
    pipeline_roots = [root.parent for root in roots]
    if args.only_index:
        wanted = set(int(v) for v in args.only_index)
        pipeline_roots = [root for root in pipeline_roots if _index_from_name(root.name) in wanted]
    results = [recover_one(root) for root in pipeline_roots]
    out = args.work_root / "tables" / "recover_diann_single_run_summary_status.csv"
    _write_csv(results, out)
    print(json.dumps({"status_table": str(out), "results": results}, indent=2), flush=True)


if __name__ == "__main__":
    main()
