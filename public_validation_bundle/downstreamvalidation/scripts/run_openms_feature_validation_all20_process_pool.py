from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_ROOT = ROOT / "results"
RESULT_ROOT = VALIDATION_ROOT / "openms_feature_all20"
WORKER_SCRIPT = ROOT / "scripts" / "run_openms_feature_validation_all20.py"
DEFAULT_PYTHON = Path(os.environ.get("TRACKCODEC_PYTHON", sys.executable))


def read_manifest() -> list[dict[str, str]]:
    path = VALIDATION_ROOT / "tables" / "pair_manifest.csv"
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def slug(row: dict[str, str]) -> str:
    name = row["file_name"].replace(".mzML", "").replace(".reconstructed", "")
    return f"{int(row['index']):02d}_{name}"


def matched_path(row: dict[str, str]) -> Path:
    return RESULT_ROOT / "tables" / "matched_pairs" / f"{slug(row)}.matched_features.csv"


def feature_paths(row: dict[str, str]) -> tuple[Path, Path]:
    stem = slug(row)
    return (
        RESULT_ROOT / "featurexml" / "original" / f"{stem}.featureXML",
        RESULT_ROOT / "featurexml" / "reconstructed" / f"{stem}.featureXML",
    )


def pending_rows(rows: list[dict[str, str]], include_completed: bool) -> list[dict[str, str]]:
    if include_completed:
        return rows
    pending: list[dict[str, str]] = []
    for row in rows:
        table = matched_path(row)
        orig_feature, recon_feature = feature_paths(row)
        if not (table.exists() and table.stat().st_size > 0 and orig_feature.exists() and recon_feature.exists()):
            pending.append(row)
    return pending


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenMS all20 validation as one Python process per file.")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--max-procs", type=int, default=7)
    parser.add_argument("--tool-threads", type=int, default=2)
    parser.add_argument("--include-completed", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = pending_rows(read_manifest(), bool(args.include_completed))
    logs_dir = RESULT_ROOT / "process_pool_logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    active: list[tuple[subprocess.Popen[bytes], dict[str, str], object, object]] = []
    finished: list[tuple[int, str, int, float]] = []
    queue = list(rows)

    def launch(row: dict[str, str]) -> None:
        stem = slug(row)
        stdout_path = logs_dir / f"{stem}.stdout.log"
        stderr_path = logs_dir / f"{stem}.stderr.log"
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        cmd = [
            str(args.python),
            "-X",
            "faulthandler",
            "-B",
            str(WORKER_SCRIPT),
            "--only",
            row["file_name"],
            "--jobs",
            "1",
            "--tool-threads",
            str(int(args.tool_threads)),
        ]
        started = time.time()
        proc = subprocess.Popen(cmd, cwd=str(ROOT.parent), stdout=stdout, stderr=stderr)
        proc._trackcodec_started = started  # type: ignore[attr-defined]
        active.append((proc, row, stdout, stderr))
        print(f"[pool] launch {row['index']} {row['file_name']} pid={proc.pid}", flush=True)

    while queue or active:
        while queue and len(active) < max(int(args.max_procs), 1):
            launch(queue.pop(0))
        time.sleep(5)
        still_active: list[tuple[subprocess.Popen[bytes], dict[str, str], object, object]] = []
        for proc, row, stdout, stderr in active:
            code = proc.poll()
            if code is None:
                still_active.append((proc, row, stdout, stderr))
                continue
            stdout.close()
            stderr.close()
            elapsed = time.time() - float(getattr(proc, "_trackcodec_started", time.time()))
            finished.append((int(row["index"]), row["file_name"], int(code), elapsed))
            print(f"[pool] done {row['index']} {row['file_name']} exit={code} elapsed_s={elapsed:.1f}", flush=True)
        active = still_active

    failed = [item for item in finished if item[2] != 0]
    print(f"[pool] finished={len(finished)} failed={len(failed)}", flush=True)
    for idx, name, code, elapsed in failed:
        print(f"[pool] failed index={idx} exit={code} elapsed_s={elapsed:.1f} file={name}", flush=True)
    if failed:
        sys.exit(1)

    if args.finalize:
        cmd = [
            str(args.python),
            "-B",
            str(WORKER_SCRIPT),
            "--jobs",
            "1",
            "--tool-threads",
            "1",
        ]
        subprocess.run(cmd, cwd=str(ROOT.parent), check=True)


if __name__ == "__main__":
    main()
