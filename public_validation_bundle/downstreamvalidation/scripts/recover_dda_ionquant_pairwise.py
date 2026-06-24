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

try:
    from scipy import stats
except Exception:  # pragma: no cover
    stats = None


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
    if float(np.std(x)) == 0.0 or float(np.std(y)) == 0.0:
        return float("nan"), float("nan")
    if stats is not None:
        res = stats.pearsonr(x, y)
        return float(res.statistic), float(res.pvalue)
    return float(np.corrcoef(x, y)[0, 1]), float("nan")


def _spearman(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    mask = np.isfinite(x) & np.isfinite(y)
    if int(mask.sum()) < 3:
        return float("nan"), float("nan")
    if stats is not None:
        res = stats.spearmanr(x[mask], y[mask])
        return float(res.statistic), float(res.pvalue)
    return float(pd.Series(x[mask]).rank().corr(pd.Series(y[mask]).rank())), float("nan")


def _pick_intensity_col(df: pd.DataFrame) -> str:
    for col in ["philosopher Intensity", "Intensity"]:
        if col in df.columns:
            return col
    matches = [col for col in df.columns if "intensity" in col.lower()]
    if matches:
        return str(matches[0])
    raise ValueError(f"No intensity column found in {list(df.columns)}")


def _load_ionquant(path: Path, level: str, condition: str) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", low_memory=False)
    intensity_col = _pick_intensity_col(df)
    if level == "protein":
        key_cols = [col for col in ["Protein ID", "Protein"] if col in df.columns]
    elif level == "peptide":
        key_cols = [col for col in ["Protein ID", "Peptide", "Peptide Sequence", "Modified Sequence", "Charges", "Charge"] if col in df.columns]
    else:
        key_cols = [col for col in ["Protein ID", "Modified Sequence", "Peptide Sequence", "Charge", "M/Z"] if col in df.columns]
    if not key_cols:
        key_cols = [str(df.columns[0])]
    out = pd.DataFrame(
        {
            "id": df[key_cols].astype(str).agg("|".join, axis=1),
            f"{condition}_quantity": pd.to_numeric(df[intensity_col], errors="coerce").fillna(0.0),
        }
    )
    return out.groupby("id", as_index=False)[f"{condition}_quantity"].sum()


def _collect_quant_tables(output_dir: Path) -> dict[str, Path]:
    tables: dict[str, Path] = {}
    for search_dir in [output_dir, output_dir / "combined"]:
        if not search_dir.exists():
            continue
        for path in sorted(search_dir.glob("*.tsv")):
            if path.stat().st_size <= 0:
                continue
            name = path.name.lower()
            if name == "protein.tsv" or name.startswith("combined_protein"):
                tables["protein"] = path
            elif name == "peptide.tsv" or name.startswith("combined_peptide"):
                tables["peptide"] = path
            elif name == "ion.tsv" or name.startswith("combined_ion"):
                tables["precursor"] = path
    return tables


def _compare(original_table: Path, reconstructed_table: Path, file_name: str, index: int, level: str, result_root: Path) -> dict[str, Any]:
    original = _load_ionquant(original_table, level, "original")
    reconstructed = _load_ionquant(reconstructed_table, level, "reconstructed")
    merged = original.merge(reconstructed, on="id", how="outer").fillna(0.0)
    original_set = set(merged.loc[merged["original_quantity"] > 0, "id"].astype(str))
    reconstructed_set = set(merged.loc[merged["reconstructed_quantity"] > 0, "id"].astype(str))
    shared = merged[(merged["original_quantity"] > 0) & (merged["reconstructed_quantity"] > 0)].copy()
    x = np.log2(shared["original_quantity"].to_numpy(dtype=float) + 1.0)
    y = np.log2(shared["reconstructed_quantity"].to_numpy(dtype=float) + 1.0)
    abs_delta = np.abs(y - x)
    pearson_r, pearson_p = _pearson(x, y)
    spearman_r, spearman_p = _spearman(x, y)
    slug = f"{int(index):02d}_{_safe_name(file_name)}"
    paired_dir = result_root / "paired_tables"
    paired_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(paired_dir / f"{slug}.{level}.ionquant_pairwise.tsv", sep="\t", index=False)
    return {
        "index": int(index),
        "file": file_name,
        "level": level,
        "original_table": str(original_table),
        "reconstructed_table": str(reconstructed_table),
        "original_nonzero_count": len(original_set),
        "reconstructed_nonzero_count": len(reconstructed_set),
        "shared_nonzero_count": len(original_set & reconstructed_set),
        "original_only_count": len(original_set - reconstructed_set),
        "reconstructed_only_count": len(reconstructed_set - original_set),
        "jaccard": _jaccard(original_set, reconstructed_set),
        "shared_log2_pearson": pearson_r,
        "shared_log2_pearson_p": pearson_p,
        "shared_log2_spearman": spearman_r,
        "shared_log2_spearman_p": spearman_p,
        "median_abs_log2_delta": float(np.nanmedian(abs_delta)) if len(abs_delta) else float("nan"),
        "p95_abs_log2_delta": float(np.nanpercentile(abs_delta, 95)) if len(abs_delta) else float("nan"),
        "max_abs_log2_delta": float(np.nanmax(abs_delta)) if len(abs_delta) else float("nan"),
    }


def _index_from_name(name: str) -> int | None:
    try:
        return int(name.split("_", 1)[0])
    except Exception:
        return None


def recover_one(pipeline_root: Path) -> dict[str, Any]:
    result_root = pipeline_root / "dda_msfragger_ionquant"
    runs_root = result_root / "runs"
    index = _index_from_name(pipeline_root.name)
    if index is None or not runs_root.exists():
        return {"pipeline_root": str(pipeline_root), "status": "skipped"}
    run_dirs = [path for path in sorted(runs_root.iterdir()) if path.is_dir()]
    if not run_dirs:
        return {"pipeline_root": str(pipeline_root), "status": "no_runs"}
    run_dir = run_dirs[0]
    file_name = run_dir.name
    if not file_name.lower().endswith(".mzml"):
        file_name = f"{file_name}.mzML"
    original_tables = _collect_quant_tables(run_dir / "original" / "ionquant_workspace")
    reconstructed_tables = _collect_quant_tables(run_dir / "reconstructed" / "ionquant_workspace")
    rows = []
    errors = []
    for level in ["protein", "peptide", "precursor"]:
        if level not in original_tables or level not in reconstructed_tables:
            errors.append(f"missing {level}")
            continue
        try:
            rows.append(_compare(original_tables[level], reconstructed_tables[level], file_name, index, level, result_root))
        except Exception as exc:
            errors.append(f"{level}: {exc}")
    _write_csv(rows, result_root / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv")
    return {
        "pipeline_root": str(pipeline_root),
        "index": index,
        "file": file_name,
        "status": "ok" if rows else "no_pairwise",
        "pairwise_rows": len(rows),
        "errors": "; ".join(errors),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover DDA IonQuant pairwise summaries from existing per-file IonQuant outputs.")
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--only-index", type=int, action="append", default=[])
    args = parser.parse_args()
    roots = sorted((args.work_root / "pipeline_runs").glob("*/dda_msfragger_ionquant"))
    pipeline_roots = [root.parent for root in roots]
    if args.only_index:
        wanted = set(int(v) for v in args.only_index)
        pipeline_roots = [root for root in pipeline_roots if _index_from_name(root.name) in wanted]
    results = [recover_one(root) for root in pipeline_roots]
    out = args.work_root / "tables" / "recover_dda_ionquant_pairwise_status.csv"
    _write_csv(results, out)
    print(json.dumps({"status_table": str(out), "results": results}, indent=2), flush=True)


if __name__ == "__main__":
    main()
