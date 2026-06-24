from __future__ import annotations

import argparse
import csv
import math
import os
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK_ROOTS = [
    Path(item)
    for item in os.environ.get(
        "TRACKCODEC_SEARCH_WORK_ROOTS",
        str(ROOT / "outputs" / "search_work"),
    ).split(os.pathsep)
    if item
]
DEFAULT_CURRENT_MANIFEST = ROOT / "outputs" / "stream_loss_current36" / "tables" / "pair_manifest.csv"
DEFAULT_OUT_ROOT = ROOT / "outputs" / "search_validation_figures"


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text.replace(".mzML", "")).strip("._") or "item"


def _index_from_pipeline_name(name: str) -> int | None:
    try:
        return int(str(name).split("_", 1)[0])
    except Exception:
        return None


def _pearson(x: pd.Series, y: pd.Series) -> float:
    xv = pd.to_numeric(x, errors="coerce").to_numpy(float)
    yv = pd.to_numeric(y, errors="coerce").to_numpy(float)
    mask = np.isfinite(xv) & np.isfinite(yv)
    if int(mask.sum()) < 3:
        return float("nan")
    xv = xv[mask]
    yv = yv[mask]
    xc = xv - float(np.mean(xv))
    yc = yv - float(np.mean(yv))
    xss = float(np.sum(xc * xc))
    yss = float(np.sum(yc * yc))
    if xss == 0.0 or yss == 0.0:
        return float("nan")
    return float(np.sum(xc * yc) / math.sqrt(xss * yss))


def _median_abs_delta(x: pd.Series, y: pd.Series, *, log2: bool = False) -> float:
    xv = pd.to_numeric(x, errors="coerce")
    yv = pd.to_numeric(y, errors="coerce")
    if log2:
        xv = np.log2(xv.fillna(0.0) + 1.0)
        yv = np.log2(yv.fillna(0.0) + 1.0)
    delta = (yv - xv).abs().replace([np.inf, -np.inf], np.nan).dropna()
    return float(delta.median()) if not delta.empty else float("nan")


def _p95_abs_delta(x: pd.Series, y: pd.Series, *, log2: bool = False) -> float:
    xv = pd.to_numeric(x, errors="coerce")
    yv = pd.to_numeric(y, errors="coerce")
    if log2:
        xv = np.log2(xv.fillna(0.0) + 1.0)
        yv = np.log2(yv.fillna(0.0) + 1.0)
    delta = (yv - xv).abs().replace([np.inf, -np.inf], np.nan).dropna()
    return float(np.percentile(delta.to_numpy(float), 95)) if not delta.empty else float("nan")


def _parse_spectrum_scan(spectrum: str) -> tuple[int | None, int | None]:
    parts = str(spectrum).split(".")
    if len(parts) >= 4:
        try:
            return int(parts[-3]), int(parts[-1])
        except Exception:
            return None, None
    return None, None


def _load_psm(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", low_memory=False)
    if "Spectrum" not in df.columns or "Charge" not in df.columns:
        raise KeyError(f"Missing Spectrum/Charge in {path}")
    parsed = df["Spectrum"].map(_parse_spectrum_scan)
    df["scan"] = [item[0] for item in parsed]
    df["spectrum_charge"] = [item[1] for item in parsed]
    df["charge_key"] = pd.to_numeric(df["Charge"], errors="coerce").fillna(df["spectrum_charge"])
    mod = df["Modified Peptide"] if "Modified Peptide" in df.columns else df.get("Peptide", pd.Series([""] * len(df)))
    pep = df["Peptide"] if "Peptide" in df.columns else mod
    df["peptide_key"] = mod.fillna("").astype(str)
    df.loc[df["peptide_key"].isin(["", "nan", "NaN"]), "peptide_key"] = pep.fillna("").astype(str)
    df["psm_identity_key"] = df["scan"].astype("Int64").astype(str) + "|" + df["charge_key"].astype("Int64").astype(str) + "|" + df["peptide_key"]
    df["scan_charge_key"] = df["scan"].astype("Int64").astype(str) + "|" + df["charge_key"].astype("Int64").astype(str)
    return df.dropna(subset=["scan"])


def _load_ion(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", low_memory=False)
    seq_col = "Modified Sequence" if "Modified Sequence" in df.columns else "Peptide Sequence"
    df["ion_key"] = df[seq_col].fillna("").astype(str) + "|" + pd.to_numeric(df["Charge"], errors="coerce").astype("Int64").astype(str)
    return df


def _load_diann_report(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    required = ["Precursor.Id"]
    for col in required:
        if col not in df.columns:
            raise KeyError(f"Missing {col} in {path}")
    q_mask = pd.to_numeric(df.get("Q.Value", 0.0), errors="coerce").fillna(1.0) <= 0.01
    for optional in ["Lib.Q.Value", "PG.Q.Value"]:
        if optional in df.columns:
            q_mask &= pd.to_numeric(df[optional], errors="coerce").fillna(1.0) <= 0.01
    df = df.loc[q_mask].copy()
    keep = ["Precursor.Id"]
    for col in ["Q.Value", "Lib.Q.Value", "PG.Q.Value", "RT", "Predicted.RT", "Precursor.Quantity", "Quantity.Quality", "PEP"]:
        if col in df.columns:
            keep.append(col)
    return df[keep].drop_duplicates(subset=["Precursor.Id"], keep="best" if False else "first")


def _current_index_map(manifest: Path) -> dict[str, int]:
    df = pd.read_csv(manifest)
    return {str(row["file_name"]): int(row["index"]) for _, row in df.iterrows()}


def _iter_pipeline_roots(work_roots: list[Path]) -> list[Path]:
    roots: list[Path] = []
    for work_root in work_roots:
        roots.extend(sorted((work_root / "pipeline_runs").glob("*")))
    return roots


def _run_file_name_from_root(run_root: Path) -> str:
    name = run_root.name
    idx = _index_from_pipeline_name(name)
    if idx is not None:
        name = name.split("_", 1)[1]
    if not name.endswith(".mzML"):
        name = f"{name}.mzML"
    return name


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("workflow", "")), str(row.get("index", "")), str(row.get("file_name", "")))
        current = by_key.get(key)
        if current is None:
            by_key[key] = row
            continue
        current_ok = str(current.get("status", "")) == "ok"
        row_ok = str(row.get("status", "")) == "ok"
        if row_ok and not current_ok:
            by_key[key] = row
        elif row_ok == current_ok:
            current_shared = int(float(current.get("shared_psm_identity_count", current.get("shared_precursor_count", -1)) or -1))
            row_shared = int(float(row.get("shared_psm_identity_count", row.get("shared_precursor_count", -1)) or -1))
            if row_shared >= current_shared:
                by_key[key] = row
    return sorted(
        by_key.values(),
        key=lambda row: (
            str(row.get("workflow", "")),
            int(row.get("index", 9999)) if str(row.get("index", "")).isdigit() else 9999,
            str(row.get("file_name", "")),
        ),
    )


def _find_single_run_dir(workflow_root: Path, workflow_name: str) -> Path | None:
    runs_root = workflow_root / workflow_name / "runs"
    if not runs_root.exists():
        return None
    dirs = [path for path in sorted(runs_root.iterdir()) if path.is_dir()]
    return dirs[0] if dirs else None


def _summarize_dda(pipeline_root: Path, current_map: dict[str, int]) -> dict[str, Any] | None:
    run_dir = _find_single_run_dir(pipeline_root, "dda_msfragger_ionquant")
    if run_dir is None:
        return None
    source_index = _index_from_pipeline_name(pipeline_root.name)
    file_name = _run_file_name_from_root(run_dir)
    if file_name not in current_map:
        return None
    orig_psm = run_dir / "original" / "philosopher" / "psm.tsv"
    recon_psm = run_dir / "reconstructed" / "philosopher" / "psm.tsv"
    orig_ion = run_dir / "original" / "philosopher" / "ion.tsv"
    recon_ion = run_dir / "reconstructed" / "philosopher" / "ion.tsv"
    if not orig_psm.exists() or not recon_psm.exists():
        return {
            "index": current_map[file_name],
            "source_index": source_index,
            "file_name": file_name,
            "workflow": "dda_msfragger_philosopher_ionquant",
            "status": "missing_psm",
        }
    left = _load_psm(orig_psm)
    right = _load_psm(recon_psm)
    left_best = left.drop_duplicates(subset=["scan_charge_key", "peptide_key"])
    right_best = right.drop_duplicates(subset=["scan_charge_key", "peptide_key"])
    shared_identity = left_best.merge(right_best, on=["scan_charge_key", "peptide_key"], how="inner", suffixes=("_orig", "_recon"))
    scan_join = left.drop_duplicates(subset=["scan_charge_key"]).merge(right.drop_duplicates(subset=["scan_charge_key"]), on="scan_charge_key", how="inner", suffixes=("_orig", "_recon"))
    orig_ids = set(left_best["scan_charge_key"].astype(str) + "|" + left_best["peptide_key"].astype(str))
    recon_ids = set(right_best["scan_charge_key"].astype(str) + "|" + right_best["peptide_key"].astype(str))
    row: dict[str, Any] = {
        "index": current_map[file_name],
        "source_index": source_index,
        "file_name": file_name,
        "workflow": "dda_msfragger_philosopher_ionquant",
        "status": "ok",
        "original_psm_count": int(len(orig_ids)),
        "reconstructed_psm_count": int(len(recon_ids)),
        "shared_psm_identity_count": int(len(orig_ids & recon_ids)),
        "psm_recovery_rate": float(len(orig_ids & recon_ids) / len(orig_ids)) if orig_ids else float("nan"),
        "psm_gain_count": int(len(recon_ids - orig_ids)),
        "psm_loss_count": int(len(orig_ids - recon_ids)),
        "shared_scan_charge_count": int(scan_join.shape[0]),
    }
    if not shared_identity.empty:
        for col, out_name in [
            ("Hyperscore", "hyperscore"),
            ("PeptideProphet Probability", "peptideprophet_probability"),
            ("Expectation", "expectation"),
        ]:
            lo = f"{col}_orig"
            rr = f"{col}_recon"
            if lo in shared_identity.columns and rr in shared_identity.columns:
                row[f"{out_name}_pearson"] = _pearson(shared_identity[lo], shared_identity[rr])
                row[f"{out_name}_median_abs_delta"] = _median_abs_delta(shared_identity[lo], shared_identity[rr])
                row[f"{out_name}_p95_abs_delta"] = _p95_abs_delta(shared_identity[lo], shared_identity[rr])
    if orig_ion.exists() and recon_ion.exists():
        oi = _load_ion(orig_ion)
        ri = _load_ion(recon_ion)
        orig_ions = set(oi["ion_key"].astype(str))
        recon_ions = set(ri["ion_key"].astype(str))
        row.update(
            {
                "original_peptide_ion_count": int(len(orig_ions)),
                "reconstructed_peptide_ion_count": int(len(recon_ions)),
                "shared_peptide_ion_count": int(len(orig_ions & recon_ions)),
                "peptide_ion_recovery_rate": float(len(orig_ions & recon_ions) / len(orig_ions)) if orig_ions else float("nan"),
                "peptide_ion_gain_count": int(len(recon_ions - orig_ions)),
                "peptide_ion_loss_count": int(len(orig_ions - recon_ions)),
            }
        )
    return row


def _summarize_diann(pipeline_root: Path, current_map: dict[str, int]) -> dict[str, Any] | None:
    run_dir = _find_single_run_dir(pipeline_root, "diann_single_run")
    if run_dir is None:
        return None
    source_index = _index_from_pipeline_name(pipeline_root.name)
    file_name = _run_file_name_from_root(run_dir)
    if file_name not in current_map:
        return None
    orig_report = run_dir / "original" / "report.parquet"
    recon_report = run_dir / "reconstructed" / "report.parquet"
    if not orig_report.exists() or not recon_report.exists():
        return {
            "index": current_map[file_name],
            "source_index": source_index,
            "file_name": file_name,
            "workflow": "diann",
            "status": "missing_report",
        }
    left = _load_diann_report(orig_report)
    right = _load_diann_report(recon_report)
    orig_ids = set(left["Precursor.Id"].astype(str))
    recon_ids = set(right["Precursor.Id"].astype(str))
    shared = left.merge(right, on="Precursor.Id", how="inner", suffixes=("_orig", "_recon"))
    row: dict[str, Any] = {
        "index": current_map[file_name],
        "source_index": source_index,
        "file_name": file_name,
        "workflow": "diann",
        "status": "ok",
        "original_precursor_count": int(len(orig_ids)),
        "reconstructed_precursor_count": int(len(recon_ids)),
        "shared_precursor_count": int(len(orig_ids & recon_ids)),
        "precursor_recovery_rate": float(len(orig_ids & recon_ids) / len(orig_ids)) if orig_ids else float("nan"),
        "precursor_gain_count": int(len(recon_ids - orig_ids)),
        "precursor_loss_count": int(len(orig_ids - recon_ids)),
    }
    for col, out_name in [
        ("Q.Value", "q_value"),
        ("Lib.Q.Value", "lib_q_value"),
        ("PG.Q.Value", "pg_q_value"),
        ("RT", "rt"),
        ("Precursor.Quantity", "precursor_quantity"),
    ]:
        lo = f"{col}_orig"
        rr = f"{col}_recon"
        if lo in shared.columns and rr in shared.columns:
            row[f"{out_name}_pearson"] = _pearson(shared[lo], shared[rr])
            row[f"{out_name}_median_abs_delta"] = _median_abs_delta(shared[lo], shared[rr], log2=(col == "Precursor.Quantity"))
            row[f"{out_name}_p95_abs_delta"] = _p95_abs_delta(shared[lo], shared[rr], log2=(col == "Precursor.Quantity"))
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize DDA PSM/ion and DIA-NN precursor recovery from existing search outputs.")
    parser.add_argument("--work-root", type=Path, action="append", default=[])
    parser.add_argument("--current-manifest", type=Path, default=DEFAULT_CURRENT_MANIFEST)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    args = parser.parse_args()

    work_roots = args.work_root or DEFAULT_WORK_ROOTS
    current_map = _current_index_map(args.current_manifest)
    dda_rows: list[dict[str, Any]] = []
    dia_rows: list[dict[str, Any]] = []
    for pipeline_root in _iter_pipeline_roots(work_roots):
        try:
            dda = _summarize_dda(pipeline_root, current_map)
            if dda is not None:
                dda_rows.append(dda)
        except Exception as exc:
            dda_rows.append({"pipeline_root": str(pipeline_root), "workflow": "dda_msfragger_philosopher_ionquant", "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        try:
            dia = _summarize_diann(pipeline_root, current_map)
            if dia is not None:
                dia_rows.append(dia)
        except Exception as exc:
            dia_rows.append({"pipeline_root": str(pipeline_root), "workflow": "diann", "status": "error", "error": f"{type(exc).__name__}: {exc}"})

    dda_rows = _dedupe_rows(dda_rows)
    dia_rows = _dedupe_rows(dia_rows)
    tables = args.out_root / "tables"
    _write_csv(dda_rows, tables / "dda_psm_ion_recovery_summary.csv")
    _write_csv(dia_rows, tables / "diann_precursor_recovery_summary.csv")
    _write_csv(dda_rows + dia_rows, tables / "psm_precursor_recovery_summary.csv")
    print(
        {
            "dda_rows": len(dda_rows),
            "dia_rows": len(dia_rows),
            "out_root": str(args.out_root),
        }
    )


if __name__ == "__main__":
    main()
