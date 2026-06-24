from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import time
from pathlib import Path


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(payload), ensure_ascii=False) + "\n")


def _write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _parse_option(cmdline: list[str], option: str) -> str | None:
    for idx, token in enumerate(cmdline):
        if token == option and idx + 1 < len(cmdline):
            return cmdline[idx + 1]
    return None


def _cmd_matches_result_root(cmdline: list[str], result_root: Path) -> bool:
    resolved = str(result_root.resolve())
    value = _parse_option(cmdline, "--result-root")
    if value is not None:
        try:
            return str(Path(value).resolve()).startswith(resolved)
        except Exception:
            return False
    return any(resolved in token for token in cmdline)


def _ps_snapshot() -> list[dict]:
    output = subprocess.check_output(
        ["ps", "-eo", "pid=,ppid=,rss=,args="],
        text=True,
        encoding="utf-8",
    )
    rows = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            rss_kb = int(parts[2])
        except ValueError:
            continue
        rows.append(
            {
                "pid": pid,
                "ppid": ppid,
                "rss_bytes": int(rss_kb) * 1024,
                "cmd": parts[3],
                "cmdline": shlex.split(parts[3]),
            }
        )
    return rows


def _iter_supervisors_and_workers(result_root: Path):
    snapshot = _ps_snapshot()
    supervisors = []
    workers = []
    for proc in snapshot:
        cmdline = proc["cmdline"]
        joined = proc["cmd"]
        if "multi_dataset_supervisor.py" in joined and _cmd_matches_result_root(cmdline, result_root):
            supervisors.append(proc)
            continue
        if "single_file_benchmark.py" in joined and _cmd_matches_result_root(cmdline, result_root):
            workers.append(proc)
    return supervisors, workers, snapshot


def _read_tree_rss_bytes(proc_row: dict, snapshot: list[dict]) -> int:
    by_ppid: dict[int, list[dict]] = {}
    for row in snapshot:
        by_ppid.setdefault(int(row["ppid"]), []).append(row)
    total = 0
    stack = [int(proc_row["pid"])]
    seen = set()
    by_pid = {int(row["pid"]): row for row in snapshot}
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        row = by_pid.get(pid)
        if row is None:
            continue
        total += int(row["rss_bytes"])
        for child in by_ppid.get(pid, []):
            stack.append(int(child["pid"]))
    return int(total)


def _infer_worker_stage(worker_root: Path, phase: str) -> str:
    progress_path = worker_root / "logs" / "worker.progress.jsonl"
    if not progress_path.exists():
        return phase
    last_stage = phase
    try:
        with progress_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = str(row.get("event", ""))
                if event in {"rss_stage_start", "rss_stage_done"} and row.get("stage"):
                    last_stage = str(row["stage"])
                elif event == "section_start" and row.get("section"):
                    last_stage = f"section_{row['section']}"
                elif event == "whole_variant_start" and row.get("archive_variant"):
                    last_stage = f"whole_variant_{row['archive_variant']}"
    except OSError:
        return phase
    return last_stage


def _worker_identity(proc_row: dict) -> dict | None:
    cmdline = list(proc_row["cmdline"])
    file_path = _parse_option(cmdline, "--file")
    result_root = _parse_option(cmdline, "--result-root")
    phase = _parse_option(cmdline, "--phase") or "unknown"
    dataset_label = _parse_option(cmdline, "--dataset-label") or ""
    if file_path is None or result_root is None:
        return None
    file_obj = Path(file_path).resolve()
    worker_root = Path(result_root).resolve()
    return {
        "pid": int(proc_row["pid"]),
        "file": file_obj.name,
        "file_path": str(file_obj),
        "worker_root": worker_root,
        "phase": phase,
        "dataset_label": dataset_label,
        "stage": _infer_worker_stage(worker_root, phase),
        "raw_file_bytes": int(file_obj.stat().st_size) if file_obj.exists() else 0,
    }


def _build_outputs(state: dict, output_root: Path) -> None:
    stage_rows = []
    file_rows = []
    per_file_acc: dict[str, dict] = {}
    for key, item in sorted(state.items(), key=lambda kv: (kv[1]["dataset_label"], kv[1]["file"], kv[1]["phase"], kv[1]["stage"])):
        stage_row = {
            "dataset_label": item["dataset_label"],
            "file": item["file"],
            "file_path": item["file_path"],
            "phase": item["phase"],
            "stage": item["stage"],
            "raw_file_bytes": int(item["raw_file_bytes"]),
            "raw_file_gb": float(item["raw_file_bytes"]) / (1024.0 ** 3),
            "peak_rss_bytes": int(item["peak_rss_bytes"]),
            "peak_rss_gb": float(item["peak_rss_bytes"]) / (1024.0 ** 3),
            "last_seen_pid": int(item["last_seen_pid"]),
            "first_seen_at": item["first_seen_at"],
            "last_seen_at": item["last_seen_at"],
        }
        stage_rows.append(stage_row)
        bucket = per_file_acc.setdefault(
            item["file_path"],
            {
                "dataset_label": item["dataset_label"],
                "file": item["file"],
                "file_path": item["file_path"],
                "raw_file_bytes": int(item["raw_file_bytes"]),
                "max_peak_rss_bytes": 0,
                "max_peak_rss_stage": "",
                "phase_count": set(),
                "stage_count": 0,
            },
        )
        bucket["phase_count"].add(item["phase"])
        bucket["stage_count"] += 1
        if int(item["peak_rss_bytes"]) >= int(bucket["max_peak_rss_bytes"]):
            bucket["max_peak_rss_bytes"] = int(item["peak_rss_bytes"])
            bucket["max_peak_rss_stage"] = str(item["stage"])
    for bucket in per_file_acc.values():
        file_rows.append(
            {
                "dataset_label": bucket["dataset_label"],
                "file": bucket["file"],
                "file_path": bucket["file_path"],
                "raw_file_bytes": int(bucket["raw_file_bytes"]),
                "raw_file_gb": float(bucket["raw_file_bytes"]) / (1024.0 ** 3),
                "max_peak_rss_bytes": int(bucket["max_peak_rss_bytes"]),
                "max_peak_rss_gb": float(bucket["max_peak_rss_bytes"]) / (1024.0 ** 3),
                "max_peak_rss_stage": bucket["max_peak_rss_stage"],
                "phase_count": len(bucket["phase_count"]),
                "stage_count": int(bucket["stage_count"]),
            }
        )

    summary = {
        "updated_at": _now_iso(),
        "n_stage_rows": len(stage_rows),
        "n_files": len(file_rows),
        "stage_rows": stage_rows,
        "file_rows": file_rows,
    }
    _write_json(output_root / "rss_peak_summary.json", summary)
    _write_csv(stage_rows, output_root / "rss_peak_by_stage.csv")
    _write_csv(file_rows, output_root / "rss_peak_by_file.csv")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch TrackCodec worker RSS peaks for an active multi-dataset run.")
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--idle-exit-seconds", type=float, default=600.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result_root = Path(args.result_root).resolve()
    output_root = result_root / "live_rss_monitor"
    output_root.mkdir(parents=True, exist_ok=True)
    progress_log = output_root / "rss_monitor.progress.jsonl"

    _append_jsonl(
        progress_log,
        {
            "event": "rss_monitor_start",
            "timestamp": _now_iso(),
            "result_root": str(result_root),
            "poll_seconds": float(args.poll_seconds),
            "idle_exit_seconds": float(args.idle_exit_seconds),
        },
    )

    state: dict[tuple[str, str, str], dict] = {}
    idle_started = None
    while True:
        supervisors, workers, snapshot = _iter_supervisors_and_workers(result_root)
        now = _now_iso()
        active_count = 0
        for proc_row in workers:
            identity = _worker_identity(proc_row)
            if identity is None:
                continue
            active_count += 1
            key = (identity["file_path"], identity["phase"], identity["stage"])
            rss_bytes = _read_tree_rss_bytes(proc_row, snapshot)
            item = state.get(key)
            if item is None:
                item = {
                    **identity,
                    "peak_rss_bytes": int(rss_bytes),
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "last_seen_pid": int(proc.pid),
                }
                state[key] = item
            else:
                item["peak_rss_bytes"] = max(int(item["peak_rss_bytes"]), int(rss_bytes))
                item["last_seen_at"] = now
                item["last_seen_pid"] = int(proc.pid)
                item["stage"] = identity["stage"]
            _append_jsonl(
                progress_log,
                {
                    "event": "rss_sample",
                    "timestamp": now,
                    "dataset_label": identity["dataset_label"],
                    "file": identity["file"],
                    "phase": identity["phase"],
                    "stage": identity["stage"],
                    "pid": int(proc_row["pid"]),
                    "rss_bytes": int(rss_bytes),
                    "rss_gb": float(rss_bytes) / (1024.0 ** 3),
                    "peak_rss_bytes": int(state[key]["peak_rss_bytes"]),
                    "peak_rss_gb": float(state[key]["peak_rss_bytes"]) / (1024.0 ** 3),
                },
            )

        _build_outputs(state, output_root)

        if supervisors or active_count:
            idle_started = None
        else:
            if idle_started is None:
                idle_started = time.time()
            elif (time.time() - idle_started) >= float(args.idle_exit_seconds):
                break
        time.sleep(max(1.0, float(args.poll_seconds)))

    _append_jsonl(
        progress_log,
        {
            "event": "rss_monitor_done",
            "timestamp": _now_iso(),
            "result_root": str(result_root),
            "n_tracked_stage_rows": len(state),
        },
    )
    _build_outputs(state, output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
