from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ms1_ablation_fresh_common import aggregate_rows, load_manifest, write_csv


SCRIPT_DIR = Path(__file__).resolve().parent
SINGLE_SCRIPT = SCRIPT_DIR / "run_ms1_intensity_isolation_single.py"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the MS1 intensity-codec isolation ablation from a manifest. "
            "Input files are sorted by size descending before scheduling."
        )
    )
    parser.add_argument("--manifest", type=Path, required=True, help="CSV or JSON manifest with path/file_path/mzml_path entries.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory for per-file runs and merged tables.")
    parser.add_argument("--workers", type=int, default=1, help="Number of files to run concurrently.")
    parser.add_argument("--max-files", type=int, default=0, help="Limit number of manifest rows; 0 means all rows.")
    parser.add_argument("--max-ms1-scans", type=int, default=0, help="Limit MS1 scans for smoke tests; 0 means all MS1 scans.")
    parser.add_argument("--resume", action="store_true", help="Skip files with existing per-file summary JSON.")
    parser.add_argument("--temp-root", type=Path, default=None, help="Optional worker-local temporary directory root.")
    return parser.parse_args()


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _run_one(row: dict, args: argparse.Namespace, output_dir: Path) -> list[dict]:
    file_name = str(row["file"])
    index = int(row.get("index") or 0)
    file_dir = output_dir / "per_file" / f"{index:03d}_{file_name}"
    summary_path = file_dir / "ms1_intensity_isolation_summary.json"
    if args.resume and summary_path.exists():
        return _read_csv(file_dir / "ms1_intensity_isolation_per_file.csv")

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
    return _read_csv(file_dir / "ms1_intensity_isolation_per_file.csv")


def _write_summary(rows: list[dict], path: Path) -> None:
    by_variant: dict[str, list[dict]] = {}
    for row in rows:
        by_variant.setdefault(str(row.get("variant_id")), []).append(row)
    summary_rows: list[dict] = []
    for variant_id in sorted(by_variant):
        group = by_variant[variant_id]
        agg = aggregate_rows(group)
        agg["variant_id"] = variant_id
        agg["variant_name"] = group[0].get("variant_name", variant_id)
        summary_rows.append(agg)

    a0 = next((row for row in summary_rows if row["variant_id"] == "A0"), None)
    a7 = next((row for row in summary_rows if row["variant_id"] == "A7"), None)
    if a0 and a7 and a0.get("mean_ms1_cr"):
        a7["mean_delta_vs_a0_pct"] = (
            (float(a7["mean_ms1_cr"]) - float(a0["mean_ms1_cr"])) / float(a0["mean_ms1_cr"]) * 100.0
        )
    if a0 and a7 and a0.get("median_ms1_cr"):
        a7["median_delta_vs_a0_pct"] = (
            (float(a7["median_ms1_cr"]) - float(a0["median_ms1_cr"])) / float(a0["median_ms1_cr"]) * 100.0
        )

    delta_values = []
    by_file: dict[str, dict[str, float]] = {}
    for row in rows:
        file_name = str(row["file"])
        by_file.setdefault(file_name, {})[str(row["variant_id"])] = float(row["ms1_cr"])
    for values in by_file.values():
        if "A0" in values and "A7" in values and values["A0"] > 0:
            delta_values.append((values["A7"] - values["A0"]) / values["A0"] * 100.0)
    if a7 is not None and delta_values:
        a7["paired_delta_vs_a0_mean_pct"] = statistics.mean(delta_values)
        a7["paired_delta_vs_a0_median_pct"] = statistics.median(delta_values)
        a7["paired_delta_vs_a0_min_pct"] = min(delta_values)
        a7["paired_delta_vs_a0_max_pct"] = max(delta_values)

    write_csv(summary_rows, path)


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_manifest(args.manifest, max_files=int(args.max_files))
    manifest_path = output_dir / "resolved_manifest_size_desc.json"
    manifest_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    run_log = output_dir / "RUN_LOG.md"
    run_log.write_text(
        "\n".join(
            [
                "# MS1 Intensity Isolation Manifest Run",
                "",
                f"- started_at: `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
                f"- manifest: `{args.manifest.resolve()}`",
                f"- output_dir: `{output_dir}`",
                f"- workers: `{int(args.workers)}`",
                f"- max_files: `{int(args.max_files)}`",
                f"- max_ms1_scans: `{int(args.max_ms1_scans)}`",
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
        future_map = {executor.submit(_run_one, row, args, output_dir): row for row in rows}
        for future in as_completed(future_map):
            row = future_map[future]
            try:
                completed_rows.extend(future.result())
            except Exception as exc:
                errors.append({"file": row.get("file"), "path": row.get("resolved_path"), "error": str(exc)})
                with run_log.open("a", encoding="utf-8") as handle:
                    handle.write(f"- ERROR `{row.get('file')}`: {exc}\n")

    write_csv(completed_rows, output_dir / "ms1_intensity_isolation_per_file.csv")
    _write_summary(completed_rows, output_dir / "ms1_intensity_isolation_summary.csv")
    write_csv(errors, output_dir / "ms1_intensity_isolation_errors.csv")
    if errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
