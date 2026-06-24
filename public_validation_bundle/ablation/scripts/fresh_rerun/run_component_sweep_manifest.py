from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ms1_ablation_fresh_common import COMPONENT_VARIANTS, load_manifest, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent
SINGLE_SCRIPT = SCRIPT_DIR / "run_component_sweep_single.py"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fresh-run TrackCodec component ablation A0-A6 from a manifest. "
            "Input files are sorted by byte size descending before scheduling."
        )
    )
    parser.add_argument("--manifest", type=Path, required=True, help="CSV or JSON manifest with path/file_path/mzml_path entries.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory for per-file runs and merged tables.")
    parser.add_argument("--workers", type=int, default=1, help="Number of files to run concurrently.")
    parser.add_argument("--max-files", type=int, default=0, help="Limit manifest rows; 0 means all rows.")
    parser.add_argument("--max-ms1-scans", type=int, default=0, help="Limit MS1 scans for smoke tests; 0 means all MS1 scans.")
    parser.add_argument("--max-ms2-scans", type=int, default=0, help="Limit MS2 scans for smoke tests; 0 means all MS2 scans; -1 skips MS2.")
    parser.add_argument("--variants", nargs="*", default=None, help="Optional subset, e.g. A0 A1 A3.")
    parser.add_argument("--resume", action="store_true", help="Skip files with existing per-file component_ablation_summary.json.")
    parser.add_argument("--temp-root", type=Path, default=None, help="Optional worker-local temporary directory root.")
    return parser.parse_args()


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _selected_variant_ids(names: list[str] | None) -> list[str]:
    if not names:
        return [variant.variant_id for variant in COMPONENT_VARIANTS]
    wanted = {name.upper() for name in names}
    selected = [variant.variant_id for variant in COMPONENT_VARIANTS if variant.variant_id.upper() in wanted]
    if not selected:
        raise ValueError("No component variants selected")
    return selected


def _run_one(row: dict, args: argparse.Namespace, output_dir: Path, selected_variants: list[str]) -> list[dict]:
    file_name = str(row["file"])
    index = int(row.get("index") or 0)
    file_dir = output_dir / "per_file" / f"{index:03d}_{file_name}"
    summary_path = file_dir / "component_ablation_summary.json"
    if args.resume and summary_path.exists():
        return _read_csv(file_dir / "component_ablation_per_file.csv")

    file_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = file_dir / "stdout.log"
    stderr_path = file_dir / "stderr.log"
    cmd = [
        sys.executable,
        "-B",
        str(SINGLE_SCRIPT),
        "--input",
        str(row["resolved_path"]),
        "--output-dir",
        str(file_dir),
        "--index",
        str(index),
        "--dataset",
        str(row.get("dataset", "")),
        "--vendor",
        str(row.get("vendor", "")),
        "--max-ms1-scans",
        str(args.max_ms1_scans),
        "--max-ms2-scans",
        str(args.max_ms2_scans),
        "--variants",
        *selected_variants,
    ]
    env = dict(os.environ)
    if args.temp_root is not None:
        temp_dir = Path(args.temp_root) / f"{index:03d}"
        temp_dir.mkdir(parents=True, exist_ok=True)
        env["TEMP"] = str(temp_dir)
        env["TMP"] = str(temp_dir)
        env["TMPDIR"] = str(temp_dir)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.run(cmd, cwd=str(SCRIPT_DIR), env=env, stdout=stdout, stderr=stderr, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{file_name} failed with return code {proc.returncode}; see {stderr_path}")
    return _read_csv(file_dir / "component_ablation_per_file.csv")


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    component_dir = output_dir / "component_sweep"
    component_dir.mkdir(parents=True, exist_ok=True)
    rows = load_manifest(args.manifest, max_files=int(args.max_files))
    selected_variants = _selected_variant_ids(args.variants)
    (output_dir / "resolved_manifest_size_desc.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    run_log = output_dir / "RUN_LOG.md"
    run_log.write_text(
        "\n".join(
            [
                "# TrackCodec Component Ablation Manifest Run",
                "",
                f"- started_at: `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
                f"- manifest: `{args.manifest.resolve()}`",
                f"- output_dir: `{output_dir}`",
                f"- workers: `{int(args.workers)}`",
                f"- max_files: `{int(args.max_files)}`",
                f"- max_ms1_scans: `{int(args.max_ms1_scans)}`",
                f"- max_ms2_scans: `{int(args.max_ms2_scans)}`",
                f"- variants: `{','.join(selected_variants)}`",
                "- schedule_policy: `file_size_descending`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    completed_rows: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        future_map = {executor.submit(_run_one, row, args, output_dir, selected_variants): row for row in rows}
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                completed_rows.extend(future.result())
                write_csv(completed_rows, component_dir / "ablation_per_file.csv")
            except Exception as exc:
                errors.append({"file": row.get("file"), "path": row.get("resolved_path"), "error": str(exc)})
                with run_log.open("a", encoding="utf-8") as handle:
                    handle.write(f"- ERROR `{row.get('file')}`: {exc}\n")

    write_csv(completed_rows, component_dir / "ablation_per_file.csv")
    write_csv(errors, output_dir / "component_ablation_errors.csv")
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
