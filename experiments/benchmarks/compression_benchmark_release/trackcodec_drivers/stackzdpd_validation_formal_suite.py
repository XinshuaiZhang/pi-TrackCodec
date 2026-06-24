from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DRIVER_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = DRIVER_DIR.parent
ROOT = RELEASE_ROOT.parents[2]
sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.runtime_env import (
    assert_python_native_speedups_available,
    resolve_trackcodec_python,
)

BENCHMARK_SCRIPT = DRIVER_DIR / "stackzdpd_validation_benchmark.py"
STACKZDPD_VALIDATION_ENV = "TRACKCODEC_STACKZDPD_VALIDATION_DIR"
DEFAULT_RESULT_ROOT = ROOT.parent / "benchmark_results" / "trackcodec_stackzdpd_validation_formal_20260414"

DEFAULT_SECTION_JOBS = 5
DEFAULT_BASELINE_THREADS = 1
DEFAULT_WHOLE_WORKERS = 5
DEFAULT_WHOLE_MS2_SECTION_WORKERS = 16
WHOLE_RUN_MODE_CHOICES = ("all", "encode_only", "validate_only")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Formal orchestration wrapper for StackZDPD validation benchmark.")
    parser.add_argument("--input-dir", type=Path, default=os.environ.get(STACKZDPD_VALIDATION_ENV))
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--section-jobs", type=int, default=DEFAULT_SECTION_JOBS)
    parser.add_argument("--baseline-threads", type=int, default=DEFAULT_BASELINE_THREADS)
    parser.add_argument("--whole-workers", type=int, default=DEFAULT_WHOLE_WORKERS)
    parser.add_argument("--whole-ms2-section-workers", type=int, default=DEFAULT_WHOLE_MS2_SECTION_WORKERS)
    parser.add_argument("--whole-ms2-segment-workers", type=int, default=0)
    parser.add_argument("--whole-run-mode", choices=WHOLE_RUN_MODE_CHOICES, default="all")
    parser.add_argument("--limit-files", type=int, default=0)
    parser.add_argument("--compare-baseline-errors", action="store_true")
    parser.add_argument("--skip-whole-file", action="store_true")
    parser.add_argument("--sync-only", action="store_true")
    return parser.parse_args()


def _load_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    _remove_tree(dst)
    shutil.copytree(src, dst)


def _copy_file(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _raw_run_root(result_root: Path) -> Path:
    return result_root / "_raw_benchmark"


def _run_raw_benchmark(args: argparse.Namespace, raw_root: Path) -> None:
    cmd = [
        str(resolve_trackcodec_python()),
        str(BENCHMARK_SCRIPT),
        "--input-dir",
        str(args.input_dir),
        "--result-root",
        str(raw_root),
        "--jobs",
        str(int(args.section_jobs)),
        "--baseline-threads",
        str(int(args.baseline_threads)),
        "--whole-workers",
        str(int(args.whole_workers)),
        "--whole-ms2-section-workers",
        str(int(args.whole_ms2_section_workers)),
    ]
    if int(args.whole_ms2_segment_workers) > 0:
        cmd.extend(["--whole-ms2-segment-workers", str(int(args.whole_ms2_segment_workers))])
    if int(args.limit_files) > 0:
        cmd.extend(["--limit-files", str(int(args.limit_files))])
    if args.compare_baseline_errors:
        cmd.append("--compare-baseline-errors")
    if args.skip_whole_file:
        cmd.append("--skip-whole-file")
    else:
        cmd.extend(["--whole-run-mode", str(args.whole_run_mode)])
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Raw benchmark failed with exit code {exc.returncode}: {' '.join(str(part) for part in cmd)}"
        ) from exc


def _required_section_paths(raw_root: Path) -> dict[str, Path]:
    base = raw_root / "section_benchmark" / "compression_results"
    return {
        "ms1": base / "ms1_per_file_methods.csv",
        "metadata": base / "metadata_per_file_methods.csv",
        "ms2": base / "ms2_per_file_methods.csv",
    }


def _required_whole_paths(raw_root: Path, whole_run_mode: str) -> dict[str, Path]:
    base_comp = raw_root / "whole_archive" / "compression_results"
    base_roundtrip = raw_root / "whole_archive" / "roundtrip_validation"
    required = {
        "whole_compare_table": base_comp / "stackzdpd_validation_whole_archive_aggregate.csv",
        "whole_per_file": base_comp / "stackzdpd_validation_whole_archive_per_file_methods.csv",
        "component_breakdown": base_comp / "stackzdpd_validation_whole_archive_component_breakdown.csv",
        "roundtrip_summary": base_roundtrip / "stackzdpd_validation_whole_archive_roundtrip_summary.json",
    }
    if whole_run_mode != "encode_only":
        required["roundtrip_csv"] = base_roundtrip / "stackzdpd_validation_whole_archive_roundtrip_validation.csv"
    return required


def _validate_raw_outputs(raw_root: Path, *, skip_whole_file: bool, whole_run_mode: str) -> dict:
    missing: list[str] = []
    present: dict[str, str] = {}
    for label, path in _required_section_paths(raw_root).items():
        if path.exists():
            present[label] = str(path)
        else:
            missing.append(label)
    if not skip_whole_file:
        for label, path in _required_whole_paths(raw_root, whole_run_mode).items():
            if path.exists():
                present[label] = str(path)
            else:
                missing.append(label)
    return {
        "ok": not missing,
        "missing": missing,
        "present": present,
    }


def _sync_section_exports(raw_root: Path, result_root: Path) -> dict:
    section_src = raw_root / "section_benchmark"
    section_dst = result_root / "section_results"
    out = {"source": str(section_src), "target": str(section_dst), "groups": {}}
    for label in ("ms1", "ms2", "metadata", "overall_container"):
        src = section_src / label
        dst = section_dst / label
        if src.exists():
            _copy_tree(src, dst)
            out["groups"][label] = str(dst)
    alias_src = section_src / "comparison_tables" / "file_alias_mapping.csv"
    if alias_src.exists():
        _copy_file(alias_src, result_root / "comparison_tables" / "file_alias_mapping.csv")
        out["alias_csv"] = str(result_root / "comparison_tables" / "file_alias_mapping.csv")
    return out


def _sync_whole_exports(raw_root: Path, result_root: Path) -> dict:
    whole_src = raw_root / "whole_archive"
    out = {"source": str(whole_src), "target": str(result_root), "dirs": {}}
    for label in (
        "compressed_archives",
        "compression_results",
        "comparison_tables",
        "plot_data",
        "plots",
        "roundtrip_validation",
        "reconstructed_mzml",
    ):
        src = whole_src / label
        dst = result_root / label
        if src.exists():
            _copy_tree(src, dst)
            out["dirs"][label] = str(dst)
    return out


def _export_paper_bundle(result_root: Path) -> dict:
    bundle_root = result_root / "paper_figures_bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    out = {"bundle_root": str(bundle_root), "groups": {}}
    for label in ("ms1", "ms2", "metadata"):
        src = result_root / "section_results" / label
        dst = bundle_root / label
        if src.exists():
            _copy_tree(src, dst)
            out["groups"][label] = str(dst)

    combined = bundle_root / "combined"
    combined.mkdir(parents=True, exist_ok=True)
    for label in ("comparison_tables", "plot_data", "plots", "roundtrip_validation", "compression_results"):
        src = result_root / label
        dst = combined / label
        if src.exists():
            _copy_tree(src, dst)
            out["groups"][f"combined_{label}"] = str(dst)
    return out


def _write_formal_readmes(result_root: Path, summary: dict) -> None:
    readme_json = result_root / "README.json"
    readme_md = result_root / "README.md"
    readme_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    alias_rows = _load_csv_rows(result_root / "comparison_tables" / "file_alias_mapping.csv")
    lines = [
        "# TrackCodec StackZDPD Validation Formal Suite",
        "",
        f"- Updated: `{_now_iso()}`",
        f"- Input directory: `{summary['input_dir']}`",
        f"- Formal result root: `{summary['result_root']}`",
        f"- Raw benchmark root: `{summary['raw_benchmark_root']}`",
        f"- section jobs: `{summary['parallelism']['section_jobs']}`",
        f"- baseline threads: `{summary['parallelism']['baseline_threads']}`",
        f"- whole workers: `{summary['parallelism']['whole_workers']}`",
        f"- whole ms2 section workers: `{summary['parallelism']['whole_ms2_section_workers']}`",
        f"- whole ms2 segment workers: `{summary['parallelism']['whole_ms2_segment_workers']}`",
        f"- whole-file run mode: `{summary['parallelism']['whole_run_mode']}`",
        f"- whole-file skipped: `{summary['skip_whole_file']}`",
        f"- raw output validation ok: `{summary['raw_output_validation']['ok']}`",
        "",
        "## Formal Result Tree",
        "",
        "- `section_results/`",
        "- `compressed_archives/`",
        "- `compression_results/`",
        "- `comparison_tables/`",
        "- `plot_data/`",
        "- `plots/`",
        "- `roundtrip_validation/`",
        "- `paper_figures_bundle/`",
        "",
        "## File Alias Mapping",
        "",
    ]
    if alias_rows:
        for row in alias_rows:
            lines.append(f"- `{row['file_alias']}` = `{row['file_name']}`")
    else:
        lines.append("- Pending")
    if not summary["raw_output_validation"]["ok"]:
        lines.extend(
            [
                "",
                "## Raw Output Validation Failure",
                "",
            ]
        )
        for item in summary["raw_output_validation"]["missing"]:
            lines.append(f"- Missing required raw output: `{item}`")
    readme_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    assert_python_native_speedups_available(str(resolve_trackcodec_python()), cwd=ROOT)
    args = _parse_args()
    if args.input_dir is None:
        raise ValueError(f"--input-dir or {STACKZDPD_VALIDATION_ENV} is required for the StackZDPD validation dataset.")
    result_root = Path(args.result_root)
    raw_root = _raw_run_root(result_root)
    result_root.mkdir(parents=True, exist_ok=True)

    if not args.sync_only:
        _run_raw_benchmark(args, raw_root)

    raw_output_validation = _validate_raw_outputs(
        raw_root,
        skip_whole_file=bool(args.skip_whole_file),
        whole_run_mode=str(args.whole_run_mode),
    )

    raw_summary = _load_json(raw_root / "README.json")
    summary = {
        "updated_at": _now_iso(),
        "input_dir": str(args.input_dir),
        "result_root": str(result_root),
        "raw_benchmark_root": str(raw_root),
        "parallelism": {
            "section_jobs": int(args.section_jobs),
            "baseline_threads": int(args.baseline_threads),
            "whole_workers": int(args.whole_workers),
            "whole_ms2_section_workers": int(args.whole_ms2_section_workers),
            "whole_ms2_segment_workers": int(args.whole_ms2_segment_workers),
            "whole_run_mode": str(args.whole_run_mode),
        },
        "limit_files": int(args.limit_files),
        "compare_baseline_errors": bool(args.compare_baseline_errors),
        "skip_whole_file": bool(args.skip_whole_file),
        "raw_output_validation": raw_output_validation,
        "raw_summary": raw_summary,
    }

    if not raw_output_validation["ok"]:
        summary.update(
            {
                "status": "failed_before_sync",
                "failure_reason": "missing_required_raw_outputs",
                "section_sync": {},
                "whole_sync": {},
                "paper_bundle": {},
            }
        )
        _write_formal_readmes(result_root, summary)
        raise RuntimeError(
            "Raw benchmark outputs incomplete; refusing to sync formal suite. "
            f"Missing: {', '.join(raw_output_validation['missing'])}"
        )

    section_sync = _sync_section_exports(raw_root, result_root)
    whole_sync = {} if args.skip_whole_file else _sync_whole_exports(raw_root, result_root)
    paper_bundle = _export_paper_bundle(result_root)
    summary.update(
        {
            "status": "ok",
            "section_sync": section_sync,
            "whole_sync": whole_sync,
            "paper_bundle": paper_bundle,
        }
    )
    _write_formal_readmes(result_root, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
