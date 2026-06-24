from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VALIDATION_RESULTS = ROOT / "outputs" / "stream_loss_downstream24_top2500"
DEFAULT_OUT_ROOT = ROOT / "outputs" / "search_validation_all20"
DEFAULT_WORK_ROOT = Path(
    os.environ.get("TRACKCODEC_SEARCH_WORK_ROOT", str(ROOT / "outputs" / "search_work"))
)
DEFAULT_FULL_ROOT = ROOT / "outputs" / "full_downstream"
DEFAULT_PREDICTED_LIB = DEFAULT_FULL_ROOT / "diann_single_run" / "assets" / "human_reviewed_20260425.predicted.speclib"
DEFAULT_ASSETS_ROOT = ROOT / "inputs" / "assets"
DEFAULT_FASTA = DEFAULT_ASSETS_ROOT / "human_reviewed_UP000005640_uniprot_20260425.fasta"
DEFAULT_TARGET_DECOY_FASTA = DEFAULT_ASSETS_ROOT / "human_reviewed_UP000005640_uniprot_20260425.target_decoy.fasta"
DEFAULT_MSFRAGGER_PARAMS = DEFAULT_ASSETS_ROOT / "closed_dda_target_decoy_fragger.params"
PYTHON = Path(sys.executable)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
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
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key, "")) for key in fields})


def _csv_value(value: Any) -> Any:
    value = _json_safe(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _load_full_module():
    module_path = ROOT / "scripts" / "run_full_downstream_validation.py"
    spec = importlib.util.spec_from_file_location("trackcodec_full_downstream", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cmd: list[str], log_path: Path, *, cwd: Path | None = None) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.flush()
        proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=handle, stderr=subprocess.STDOUT, check=False)
        code = int(proc.returncode)
        handle.write(f"\nexit_code={code}\n")
    return code, float(time.perf_counter() - start)


def _infer_modality(name: str, ms2_count: int) -> str:
    upper = name.upper()
    if "ETD" in upper:
        return "ETD"
    if "AIF" in upper:
        return "AIF"
    if "DIA" in upper:
        return "DIA"
    if "DDA" in upper:
        return "DDA"
    if ms2_count <= 0:
        return "MS1-only"
    return "DDA-like"


def _is_human_like(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ["293T", "HUMAN", "GA1-TUM", "TUM"])


def _assignment(name: str, modality: str) -> tuple[str, str, str]:
    upper = name.upper()
    if modality == "MS1-only":
        return "not_applicable", "not_applicable", "MS1-only file has no MS2 spectra for peptide/protein search"
    if modality == "ETD":
        return "dda", "exploratory", "ETD file is routed through open DDA search settings; interpret separately"
    if "NEGATIVE" in upper:
        return "dda", "exploratory", "negative-mode file is routed through DDA search only as an attempted compatibility check"
    if modality in {"DIA", "AIF"}:
        confidence = "primary" if modality == "DIA" else "exploratory"
        note = "DIA file uses DIA-NN predicted human library" if modality == "DIA" else "AIF file is DIA-like; DIA-NN result is exploratory"
        return "diann", confidence, note
    if modality == "DDA":
        confidence = "primary" if _is_human_like(name) else "exploratory"
        note = "DDA file uses human target-decoy FASTA" if confidence == "primary" else "DDA file lacks human naming evidence; human FASTA result is exploratory"
        return "dda", confidence, note
    return "dda", "exploratory", "unknown MS2 acquisition; routed through DDA search as attempted compatibility check"


def build_plan(validation_results: Path, out_root: Path) -> list[dict[str, Any]]:
    manifest = _read_csv(validation_results / "tables" / "pair_manifest.csv")
    decoded = {int(row["index"]): row for row in _read_csv(validation_results / "tables" / "decode_status.csv")}
    roundtrip_dir = validation_results / "roundtrip_json"
    rows: list[dict[str, Any]] = []
    for row in manifest:
        idx = int(row["index"])
        rt_matches = sorted(roundtrip_dir.glob(f"{idx:02d}_*.json"))
        ms1 = ms2 = spectra = None
        if rt_matches:
            data = json.loads(rt_matches[0].read_text(encoding="utf-8"))
            rt = data.get("roundtrip", {})
            ms1 = int(rt.get("ms1_count", 0) or 0)
            ms2 = int(rt.get("ms2_count", 0) or 0)
            spectra = int(rt.get("spectrum_count", 0) or 0)
        name = row["file_name"]
        modality = _infer_modality(name, int(ms2 or 0))
        workflow, confidence, note = _assignment(name, modality)
        decoded_row = decoded.get(idx, {})
        rows.append(
            {
                "index": idx,
                "dataset": row.get("dataset", ""),
                "file_name": name,
                "modality": modality,
                "search_workflow": workflow,
                "assignment_confidence": confidence,
                "assignment_note": note,
                "ms1_count": ms1,
                "ms2_count": ms2,
                "spectrum_count": spectra,
                "original_path": row.get("original_path", ""),
                "reconstructed_path": decoded_row.get("reconstructed_path", ""),
                "original_exists": Path(row.get("original_path", "")).exists(),
                "reconstructed_exists": Path(decoded_row.get("reconstructed_path", "")).exists(),
            }
        )
    _write_csv(rows, out_root / "tables" / "search_validation_all20_plan.csv")
    return rows


def _single_file_result_root(work_root: Path, row: dict[str, Any], full) -> Path:
    slug = f"{int(row['index']):02d}_{full._safe_name(str(row['file_name']))}"
    short_slug = f"{int(row['index']):02d}_{full._safe_name(str(row['file_name']))[:36]}"
    return work_root / "pipeline_runs" / short_slug


def _copy_if_exists(source: Path, target: Path) -> None:
    if not source.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _row_index(row: dict[str, Any]) -> int | None:
    value = row.get("index", "")
    try:
        if str(value).strip() == "":
            return None
        return int(float(str(value)))
    except Exception:
        return None


def _index_from_pipeline_name(name: str) -> int | None:
    prefix = str(name).split("_", 1)[0]
    try:
        return int(prefix)
    except Exception:
        return None


def _filtered_csv_rows(source: Path, index: int) -> list[dict[str, str]]:
    if not source.exists():
        return []
    return [row for row in _read_csv(source) if _row_index(row) == int(index)]


def _write_filtered_csv(source: Path, target: Path, index: int) -> int:
    rows = _filtered_csv_rows(source, index)
    _write_csv(rows, target)
    return len(rows)


def _workflow_summary_rows(run_root: Path, row: dict[str, Any]) -> int:
    workflow = str(row.get("search_workflow", ""))
    index = int(row["index"])
    if workflow == "diann":
        path = run_root / "diann_single_run" / "tables" / "trackcodec_diann_single_run_summary_per_file.csv"
        return len(_filtered_csv_rows(path, index)) if path.exists() else 0
    if workflow == "dda":
        path = run_root / "dda_msfragger_ionquant" / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv"
        return len(_filtered_csv_rows(path, index)) if path.exists() else 0
    return 0


def _float_value(value: Any) -> float:
    try:
        if str(value).strip() == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _dda_quantification_status(run_root: Path, row: dict[str, Any]) -> tuple[int, float, float, float]:
    path = run_root / "dda_msfragger_ionquant" / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv"
    if not path.exists():
        return 0, 0.0, 0.0, 0.0
    rows = _filtered_csv_rows(path, int(row["index"]))
    original_total = sum(_float_value(item.get("original_nonzero_count", 0)) for item in rows)
    reconstructed_total = sum(_float_value(item.get("reconstructed_nonzero_count", 0)) for item in rows)
    shared_total = sum(_float_value(item.get("shared_nonzero_count", 0)) for item in rows)
    return len(rows), original_total, reconstructed_total, shared_total


def _validation_status(row: dict[str, Any], run_result: dict[str, Any] | None) -> tuple[str, int]:
    workflow = str(row.get("search_workflow", ""))
    if workflow == "not_applicable":
        return "not_applicable", 0
    if run_result is None:
        return "pending", 0
    raw_status = str(run_result.get("run_status", "")).strip()
    run_root = Path(str(run_result.get("run_root", "")))
    summary_rows = _workflow_summary_rows(run_root, row) if run_root else 0
    if raw_status not in {"ok", "ok_cached"}:
        if workflow == "diann" and summary_rows >= 1:
            return "ok_recovered", summary_rows
        if workflow == "dda" and summary_rows >= 3:
            _, original_total, reconstructed_total, shared_total = _dda_quantification_status(run_root, row)
            if original_total <= 0 and reconstructed_total <= 0:
                return "zero_ionquant_quantification", summary_rows
            if shared_total <= 0:
                return "no_shared_ionquant_features", summary_rows
            return "ok_recovered", summary_rows
        return raw_status or "pending", summary_rows
    if workflow == "diann" and summary_rows < 1:
        return "no_diann_summary", summary_rows
    if workflow == "dda" and summary_rows < 3:
        return "no_ionquant_pairwise", summary_rows
    if workflow == "dda":
        _, original_total, reconstructed_total, shared_total = _dda_quantification_status(run_root, row)
        if original_total <= 0 and reconstructed_total <= 0:
            return "zero_ionquant_quantification", summary_rows
        if shared_total <= 0:
            return "no_shared_ionquant_features", summary_rows
    return raw_status, summary_rows


def _reuse_existing(full_root: Path, run_root: Path, row: dict[str, Any], full) -> bool:
    index = int(row["index"])
    slug = f"{int(row['index']):02d}_{full._safe_name(str(row['file_name']))}"
    workflow = row["search_workflow"]
    if workflow == "diann":
        src = full_root / "diann_single_run" / "runs" / slug
        summary = full_root / "diann_single_run" / "tables" / "trackcodec_diann_single_run_summary_per_file.csv"
        summary_rows = _filtered_csv_rows(summary, index)
        if src.exists() and summary_rows:
            _copy_if_exists(src, run_root / "diann_single_run" / "runs" / slug)
            tables = run_root / "diann_single_run" / "tables"
            tables.mkdir(parents=True, exist_ok=True)
            _write_csv(summary_rows, tables / "trackcodec_diann_single_run_summary_per_file.csv")
            run_log = full_root / "diann_single_run" / "tables" / "trackcodec_diann_single_run_run_log.csv"
            _write_filtered_csv(run_log, tables / "trackcodec_diann_single_run_run_log.csv", index)
            pairs = full_root / "diann_single_run" / "paired_tables"
            dst_pairs = run_root / "diann_single_run" / "paired_tables"
            dst_pairs.mkdir(parents=True, exist_ok=True)
            for path in pairs.glob(f"{slug}.*.diann_pairwise.tsv"):
                shutil.copy2(path, dst_pairs / path.name)
            return True
    if workflow == "dda":
        src = full_root / "dda_msfragger_ionquant" / "runs" / slug
        summary = full_root / "dda_msfragger_ionquant" / "tables" / "trackcodec_dda_ionquant_pairwise_summary.csv"
        summary_rows = _filtered_csv_rows(summary, index)
        if src.exists() and summary_rows:
            _copy_if_exists(src, run_root / "dda_msfragger_ionquant" / "runs" / slug)
            tables = run_root / "dda_msfragger_ionquant" / "tables"
            tables.mkdir(parents=True, exist_ok=True)
            _write_csv(summary_rows, tables / "trackcodec_dda_ionquant_pairwise_summary.csv")
            for name in [
                "trackcodec_dda_msfragger_run_log.csv",
                "trackcodec_dda_philosopher_run_log.csv",
                "trackcodec_dda_ionquant_run_log.csv",
            ]:
                _write_filtered_csv(full_root / "dda_msfragger_ionquant" / "tables" / name, tables / name, index)
            src_pairs = full_root / "dda_msfragger_ionquant" / "paired_tables"
            dst_pairs = run_root / "dda_msfragger_ionquant" / "paired_tables"
            dst_pairs.mkdir(parents=True, exist_ok=True)
            for path in src_pairs.glob(f"{slug}.*.ionquant_pairwise.tsv"):
                shutil.copy2(path, dst_pairs / path.name)
            return True
    return False


def run_one(row: dict[str, Any], args_dict: dict[str, Any]) -> dict[str, Any]:
    full = _load_full_module()
    out_root = Path(args_dict["out_root"])
    work_root = Path(args_dict["work_root"])
    full_root = Path(args_dict["full_root"])
    run_root = _single_file_result_root(work_root, row, full)
    run_root.mkdir(parents=True, exist_ok=True)
    _write_json(run_root / "input_row.json", row)

    workflow = str(row["search_workflow"])
    if workflow == "not_applicable":
        result = {**row, "run_status": "not_applicable", "run_root": str(run_root), "exit_code": "", "elapsed_s": 0.0}
        _write_json(run_root / "run_result.json", result)
        return result

    if not bool(row.get("original_exists")) or not bool(row.get("reconstructed_exists")):
        result = {**row, "run_status": "missing_input", "run_root": str(run_root), "exit_code": 1, "elapsed_s": 0.0}
        _write_json(run_root / "run_result.json", result)
        return result

    reused = False
    if not args_dict.get("force"):
        reused = _reuse_existing(full_root, run_root, row, full)
    if reused:
        summary_rows = _workflow_summary_rows(run_root, row)
        result = {
            **row,
            "run_status": "ok_cached",
            "reused_existing_seed": True,
            "run_root": str(run_root),
            "exit_code": 0,
            "elapsed_s": 0.0,
            "search_summary_rows": summary_rows,
            "pipeline_log": "",
        }
        _write_json(run_root / "run_result.json", result)
        return result

    cmd = [
        str(PYTHON),
        "-B",
        str(ROOT / "scripts" / "run_full_downstream_validation.py"),
        "--validation-results",
        str(Path(args_dict["validation_results"])),
        "--result-root",
        str(run_root),
        "--scope",
        "all",
        "--include-aif-diann",
        "--only",
        str(row["file_name"]),
        "--threads",
        str(int(args_dict["tool_threads"])),
        "--skip-plots",
    ]
    if workflow == "diann":
        cmd.extend(
            [
                "--stages",
                "manifest",
                "diann",
                "--force-diann-selected",
                "--predicted-lib",
                str(Path(args_dict["predicted_lib"])),
                "--fasta",
                str(Path(args_dict["fasta"])),
                "--rewrite-original-diann-input",
                "--rewrite-reconstructed-diann-input",
            ]
        )
    elif workflow == "dda":
        cmd.extend(
            [
                "--stages",
                "manifest",
                "dda",
                "--force-dda-selected",
                "--target-decoy-fasta",
                str(Path(args_dict["target_decoy_fasta"])),
                "--msfragger-params",
                str(Path(args_dict["msfragger_params"])),
            ]
        )
    else:
        result = {**row, "run_status": "unknown_workflow", "run_root": str(run_root), "exit_code": 1, "elapsed_s": 0.0}
        _write_json(run_root / "run_result.json", result)
        return result

    if args_dict.get("force"):
        cmd.append("--force")

    log_path = run_root / "logs" / "single_file_pipeline.log"
    code, elapsed = _run(cmd, log_path, cwd=ROOT)
    summary_rows = _workflow_summary_rows(run_root, row)
    if code == 0:
        if workflow == "diann" and summary_rows < 1:
            status = "no_diann_summary"
        elif workflow == "dda" and summary_rows < 3:
            status = "no_ionquant_pairwise"
        elif workflow == "dda":
            _, original_total, reconstructed_total, shared_total = _dda_quantification_status(run_root, row)
            if original_total <= 0 and reconstructed_total <= 0:
                status = "zero_ionquant_quantification"
            elif shared_total <= 0:
                status = "no_shared_ionquant_features"
            else:
                status = "ok"
        else:
            status = "ok"
    else:
        status = "failed"
    result = {
        **row,
        "run_status": status,
        "reused_existing_seed": reused,
        "run_root": str(run_root),
        "exit_code": code,
        "elapsed_s": elapsed,
        "search_summary_rows": summary_rows,
        "pipeline_log": str(log_path),
    }
    _write_json(run_root / "run_result.json", result)
    return result


def _collect_diann_summaries(out_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(out_root.glob("pipeline_runs/*/diann_single_run/tables/trackcodec_diann_single_run_summary_per_file.csv")):
        workflow_root = path.parents[1]
        expected_index = _index_from_pipeline_name(path.parents[2].name)
        for row in _read_csv(path):
            if expected_index is not None and _row_index(row) != expected_index:
                continue
            row["source_root"] = str(workflow_root)
            rows.append(row)
    rows = _dedupe(rows, ["index", "file"])
    _write_csv(rows, out_root / "tables" / "search_validation_all20_diann_summary.csv")
    return rows


def _collect_dda_summaries(out_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(out_root.glob("pipeline_runs/*/dda_msfragger_ionquant/tables/trackcodec_dda_ionquant_pairwise_summary.csv")):
        workflow_root = path.parents[1]
        expected_index = _index_from_pipeline_name(path.parents[2].name)
        for row in _read_csv(path):
            if expected_index is not None and _row_index(row) != expected_index:
                continue
            row["source_root"] = str(workflow_root)
            rows.append(row)
    rows = _dedupe(rows, ["index", "file", "level"])
    _write_csv(rows, out_root / "tables" / "search_validation_all20_dda_ionquant_summary.csv")
    return rows


def _dedupe(rows: list[dict[str, Any]], keys: list[str]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(str(row.get(k, "")) for k in keys)
        by_key[key] = row
    def sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
        try:
            idx = int(item.get("index", 0))
        except Exception:
            idx = 0
        return idx, str(item.get("file", "")), str(item.get("level", ""))
    return sorted(by_key.values(), key=sort_key)


def aggregate(out_root: Path, work_root: Path, plan_rows: list[dict[str, Any]]) -> dict[str, Any]:
    run_rows: list[dict[str, Any]] = []
    for path in sorted(work_root.glob("pipeline_runs/*/run_result.json")):
        run_rows.append(json.loads(path.read_text(encoding="utf-8")))
    run_by_index = {int(row["index"]): row for row in run_rows if str(row.get("index", "")).strip()}
    status_rows = []
    for row in plan_rows:
        result = run_by_index.get(int(row["index"]))
        validation_status, summary_rows = _validation_status(row, result)
        status_rows.append(
            {
                **row,
                **({} if result is None else {k: result.get(k, "") for k in ["run_status", "reused_existing_seed", "run_root", "exit_code", "elapsed_s", "pipeline_log"]}),
                "validation_status": validation_status,
                "search_summary_rows": summary_rows,
            }
        )
    _write_csv(status_rows, out_root / "tables" / "search_validation_all20_run_status.csv")
    diann = _collect_diann_summaries(work_root)
    dda = _collect_dda_summaries(work_root)
    _write_csv(diann, out_root / "tables" / "search_validation_all20_diann_summary.csv")
    _write_csv(dda, out_root / "tables" / "search_validation_all20_dda_ionquant_summary.csv")
    summary = {
        "out_root": str(out_root),
        "work_root": str(work_root),
        "plan_count": len(plan_rows),
        "run_status_count": len(status_rows),
        "diann_plan_count": sum(1 for row in plan_rows if row["search_workflow"] == "diann"),
        "dda_plan_count": sum(1 for row in plan_rows if row["search_workflow"] == "dda"),
        "not_applicable_count": sum(1 for row in plan_rows if row["search_workflow"] == "not_applicable"),
        "ok_count": sum(1 for row in status_rows if row.get("validation_status") in {"ok", "ok_cached", "ok_recovered"}),
        "cached_count": sum(1 for row in status_rows if row.get("run_status") == "ok_cached"),
        "failed_count": sum(
            1
            for row in status_rows
            if row.get("validation_status")
            in {
                "failed",
                "exception",
                "missing_input",
                "unknown_workflow",
                "no_diann_summary",
                "no_ionquant_pairwise",
                "zero_ionquant_quantification",
                "no_shared_ionquant_features",
            }
        ),
        "pending_count": sum(1 for row in status_rows if row.get("validation_status") == "pending"),
        "diann_summary_count": len(diann),
        "dda_pairwise_summary_count": len(dda),
    }
    _write_json(out_root / "summary.json", summary)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run all-20 search/identification validation with per-file isolated outputs.")
    parser.add_argument("--validation-results", type=Path, default=DEFAULT_VALIDATION_RESULTS)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    parser.add_argument("--full-root", type=Path, default=DEFAULT_FULL_ROOT)
    parser.add_argument("--predicted-lib", type=Path, default=DEFAULT_PREDICTED_LIB)
    parser.add_argument("--fasta", type=Path, default=DEFAULT_FASTA)
    parser.add_argument("--target-decoy-fasta", type=Path, default=DEFAULT_TARGET_DECOY_FASTA)
    parser.add_argument("--msfragger-params", type=Path, default=DEFAULT_MSFRAGGER_PARAMS)
    parser.add_argument("--jobs", type=int, default=7)
    parser.add_argument("--tool-threads", type=int, default=2)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.out_root.mkdir(parents=True, exist_ok=True)
    args.work_root.mkdir(parents=True, exist_ok=True)
    (args.out_root / "tables").mkdir(parents=True, exist_ok=True)
    plan_rows = build_plan(args.validation_results, args.out_root)
    if args.only:
        terms = [term.lower() for term in args.only]
        plan_rows = [row for row in plan_rows if any(term in str(row["file_name"]).lower() for term in terms)]
    if args.dry_run:
        summary = aggregate(args.out_root, args.work_root, plan_rows)
        print(json.dumps(_json_safe({"dry_run": True, **summary}), indent=2), flush=True)
        return

    args_dict = {
        "validation_results": str(args.validation_results),
        "out_root": str(args.out_root),
        "work_root": str(args.work_root),
        "full_root": str(args.full_root),
        "predicted_lib": str(args.predicted_lib),
        "fasta": str(args.fasta),
        "target_decoy_fasta": str(args.target_decoy_fasta),
        "msfragger_params": str(args.msfragger_params),
        "tool_threads": int(args.tool_threads),
        "force": bool(args.force),
    }
    runnable = [row for row in plan_rows if row["search_workflow"] != "not_applicable"]
    non_runnable = [row for row in plan_rows if row["search_workflow"] == "not_applicable"]
    for row in non_runnable:
        run_one(row, args_dict)
    if args.jobs <= 1:
        for row in runnable:
            print(f"[run] {row['index']:02d} {row['search_workflow']} {row['file_name']}", flush=True)
            result = run_one(row, args_dict)
            print(f"[done] {row['index']:02d} {result['run_status']} exit={result.get('exit_code')}", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=max(1, int(args.jobs))) as pool:
            futures = {pool.submit(run_one, row, args_dict): row for row in runnable}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    result = future.result()
                    print(f"[done] {row['index']:02d} {row['search_workflow']} {result['run_status']} exit={result.get('exit_code')}", flush=True)
                except Exception as exc:
                    fail = {**row, "run_status": "exception", "error": str(exc)}
                    run_root = _single_file_result_root(args.work_root, row, _load_full_module())
                    _write_json(run_root / "run_result.json", fail)
                    print(f"[exception] {row['index']:02d} {exc}", flush=True)
    summary = aggregate(args.out_root, args.work_root, plan_rows)
    print(json.dumps(_json_safe(summary), indent=2), flush=True)


if __name__ == "__main__":
    main()
