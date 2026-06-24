#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_text(command: list[str]) -> dict:
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    return {
        "cmd": command,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def pgrep(pattern: str) -> list[dict]:
    proc = subprocess.run(
        ["pgrep", "-af", pattern],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return [{"error": f"pgrep rc={proc.returncode}", "stderr": proc.stderr.strip()}]
    matches = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(" ", 1)
        try:
            pid = int(parts[0])
        except ValueError:
            pid = None
        cmd = parts[1] if len(parts) > 1 else ""
        matches.append({"pid": pid, "cmd": cmd})
    return matches


def filter_matches_by_substring(matches: list[dict], needle: str) -> list[dict]:
    if not needle:
        return matches
    return [item for item in matches if needle in item.get("cmd", "")]


def tail_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = [line.rstrip("\n") for line in handle.readlines()[-n:]]
    except OSError:
        return []
    return lines


def latest_batch_logs(log_root: Path) -> tuple[Path | None, Path | None]:
    stdout_logs = sorted(log_root.glob("batch_*/*.stdout.log"))
    stderr_logs = sorted(log_root.glob("batch_*/*.stderr.log"))
    return (stdout_logs[-1] if stdout_logs else None, stderr_logs[-1] if stderr_logs else None)


def read_last_nonempty(path: Path | None, n: int = 20) -> str:
    if path is None:
        return ""
    lines = [line for line in tail_lines(path, n) if line.strip()]
    return lines[-1] if lines else ""


def file_mtime(path: Path | None) -> float | None:
    if path is None or not path.exists():
        return None
    return path.stat().st_mtime


def tail_diagnostics(result_root: Path) -> dict:
    log_root = result_root / "logs"
    supervisor_progress = log_root / "supervisor.progress.jsonl"
    batch_stdout, batch_stderr = latest_batch_logs(log_root)
    diagnostics = {
        "timestamp": now_iso(),
        "supervisor_progress_tail": tail_lines(supervisor_progress, 20),
        "batch_stdout_tail": tail_lines(batch_stdout, 40) if batch_stdout else [],
        "batch_stderr_tail": tail_lines(batch_stderr, 40) if batch_stderr else [],
        "ps_snapshot": run_text(
            [
                "ps",
                "-eo",
                "pid,ppid,etime,%cpu,%mem,rss,stat,cmd",
            ]
        ),
        "free_h": run_text(["free", "-h"]),
        "vmstat": run_text(["vmstat", "-s"]),
        "swapon": run_text(["swapon", "--show"]),
    }
    journalctl = run_text(["journalctl", "-k", "-n", "80", "--no-pager"])
    diagnostics["journalctl_k"] = journalctl
    return diagnostics


def flatten_pids(entries: Iterable[dict]) -> list[int]:
    pids = []
    for item in entries:
        pid = item.get("pid")
        if isinstance(pid, int):
            pids.append(pid)
    return sorted(set(pids))


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch stackzdpd validation supervisor/benchmark processes.")
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--stale-seconds", type=int, default=900)
    parser.add_argument("--watchdog-jsonl", type=Path, default=None)
    parser.add_argument("--watchdog-md", type=Path, default=None)
    args = parser.parse_args()

    result_root = args.result_root.resolve()
    watchdog_jsonl = args.watchdog_jsonl or (result_root / "logs" / "process_watchdog.jsonl")
    watchdog_md = args.watchdog_md or (result_root / "process_watchdog_report.md")

    supervisor_pattern = f"stackzdpd_validation_lt5g_supervised.py --result-root {result_root}"
    benchmark_pattern = "stackzdpd_validation_benchmark.py"
    supervisor_progress = result_root / "logs" / "supervisor.progress.jsonl"

    last_state: dict[str, list[int]] = {"supervisor": [], "benchmark": []}
    last_seen_progress_mtime = file_mtime(supervisor_progress)
    last_stale_report_ts = 0.0

    append_jsonl(
        watchdog_jsonl,
        {
            "event": "watchdog_start",
            "timestamp": now_iso(),
            "result_root": str(result_root),
            "poll_seconds": args.poll_seconds,
            "stale_seconds": args.stale_seconds,
        },
    )
    watchdog_md.parent.mkdir(parents=True, exist_ok=True)
    with watchdog_md.open("a", encoding="utf-8") as handle:
        handle.write(f"- `{now_iso()}` watchdog started for `{result_root}`\n")

    while True:
        supervisor_matches = pgrep(supervisor_pattern)
        benchmark_matches = filter_matches_by_substring(pgrep(benchmark_pattern), str(result_root))
        supervisor_pids = flatten_pids(supervisor_matches)
        benchmark_pids = flatten_pids(benchmark_matches)
        progress_mtime = file_mtime(supervisor_progress)

        event = {
            "event": "heartbeat",
            "timestamp": now_iso(),
            "supervisor_matches": supervisor_matches,
            "benchmark_matches": benchmark_matches,
            "last_supervisor_progress_line": read_last_nonempty(supervisor_progress, 20),
        }
        append_jsonl(watchdog_jsonl, event)

        for name, current_pids in (("supervisor", supervisor_pids), ("benchmark", benchmark_pids)):
            previous_pids = last_state[name]
            if previous_pids and not current_pids:
                diagnostics = tail_diagnostics(result_root)
                append_jsonl(
                    watchdog_jsonl,
                    {
                        "event": f"{name}_exit_detected",
                        "timestamp": now_iso(),
                        "previous_pids": previous_pids,
                        "diagnostics": diagnostics,
                    },
                )
                with watchdog_md.open("a", encoding="utf-8") as handle:
                    handle.write(
                        f"- `{now_iso()}` `{name}` process exited; last progress line: "
                        f"`{read_last_nonempty(supervisor_progress, 20)}`\n"
                    )
            last_state[name] = current_pids

        if progress_mtime is not None:
            if last_seen_progress_mtime is None or progress_mtime > last_seen_progress_mtime:
                last_seen_progress_mtime = progress_mtime
            else:
                stale_for = time.time() - progress_mtime
                if stale_for >= args.stale_seconds and (time.time() - last_stale_report_ts) >= args.stale_seconds:
                    diagnostics = tail_diagnostics(result_root)
                    append_jsonl(
                        watchdog_jsonl,
                        {
                            "event": "progress_stalled",
                            "timestamp": now_iso(),
                            "stale_seconds": stale_for,
                            "diagnostics": diagnostics,
                        },
                    )
                    with watchdog_md.open("a", encoding="utf-8") as handle:
                        handle.write(
                            f"- `{now_iso()}` progress stalled for `{stale_for:.0f}s`; "
                            f"last progress line: `{read_last_nonempty(supervisor_progress, 20)}`\n"
                        )
                    last_stale_report_ts = time.time()

        if not supervisor_pids and not benchmark_pids:
            append_jsonl(
                watchdog_jsonl,
                {
                    "event": "watchdog_stop",
                    "timestamp": now_iso(),
                    "reason": "all_target_processes_gone",
                },
            )
            with watchdog_md.open("a", encoding="utf-8") as handle:
                handle.write(f"- `{now_iso()}` watchdog stopped: all target processes gone\n")
            return 0

        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
