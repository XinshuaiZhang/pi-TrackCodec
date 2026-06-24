#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil


DRIVER_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = DRIVER_DIR.parent
ROOT = RELEASE_ROOT.parents[2]
sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.runtime_env import (
    assert_python_native_speedups_available,
    resolve_trackcodec_python,
)

STACKZDPD_VALIDATION_ENV = "TRACKCODEC_STACKZDPD_VALIDATION_DIR"
DEFAULT_RESULT_ROOT = ROOT.parent / "benchmark_results" / "stackzdpd_validation_lt5g_supervised_20260417"
PYTHON_BIN = resolve_trackcodec_python()
BENCHMARK_SCRIPT = DRIVER_DIR / "stackzdpd_validation_benchmark.py"
WAIT_PGREP_PATTERN = "full8_section_benchmark.py --dataset-scope full8_extra3"
GB = 1024 ** 3


@dataclass(frozen=True)
class SelectedFile:
    path: Path
    size_bytes: int

    @property
    def size_gb(self) -> float:
        return self.size_bytes / GB


@dataclass
class ActiveBatch:
    batch_index: int
    batch: list[SelectedFile]
    jobs: int
    batch_root: Path
    logs_root: Path
    stage_dir: Path
    result_root: Path
    stdout_log: Path
    stderr_log: Path
    process: subprocess.Popen | None
    pid: int | None
    pgid: int | None
    external: bool
    retry_mode: bool = False


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, SelectedFile):
        return {
            "file_name": value.path.name,
            "file_path": str(value.path),
            "size_bytes": int(value.size_bytes),
            "size_gb": round(value.size_gb, 6),
        }
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(row), ensure_ascii=False) + "\n")


def _append_md(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip() + "\n")


def _read_tail_line(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle.readlines()[-20:] if line.strip()]
    except OSError:
        return ""
    return lines[-1] if lines else ""


def _read_meminfo_bytes() -> dict[str, int]:
    meminfo: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            parts = value.strip().split()
            if not parts:
                continue
            meminfo[key.strip()] = int(parts[0]) * 1024
    except OSError:
        return {}
    return meminfo


def _read_system_memory_snapshot() -> dict[str, float | int]:
    meminfo = _read_meminfo_bytes()
    total_bytes = int(meminfo.get("MemTotal", 0))
    available_bytes = int(meminfo.get("MemAvailable", 0))
    used_bytes = max(0, total_bytes - available_bytes) if total_bytes > 0 else 0
    swap_total_bytes = int(meminfo.get("SwapTotal", 0))
    swap_free_bytes = int(meminfo.get("SwapFree", 0))
    swap_used_bytes = max(0, swap_total_bytes - swap_free_bytes)
    return {
        "total_bytes": total_bytes,
        "available_bytes": available_bytes,
        "used_bytes": used_bytes,
        "used_gb": float(used_bytes) / GB if used_bytes else 0.0,
        "available_gb": float(available_bytes) / GB if available_bytes else 0.0,
        "swap_total_bytes": swap_total_bytes,
        "swap_used_bytes": swap_used_bytes,
        "swap_used_gb": float(swap_used_bytes) / GB if swap_used_bytes else 0.0,
    }


def _pid_alive(pid: int | None) -> bool:
    return bool(isinstance(pid, int) and pid > 0 and psutil.pid_exists(pid))


def _background_process_kwargs() -> dict:
    if os.name == "nt":
        return {
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        }
    return {"start_new_session": True}


def _safe_getpgid(pid: int | None) -> int | None:
    if pid is None or pid <= 0:
        return None
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _discover_files_in_size_range(
    input_dir: Path,
    *,
    min_file_size_gb: float,
    max_file_size_gb: float,
) -> list[SelectedFile]:
    min_bytes = int(max(0.0, min_file_size_gb) * GB)
    limit_bytes = int(max_file_size_gb * GB) if max_file_size_gb > 0 else None
    selected = []
    for path in sorted(input_dir.iterdir(), key=lambda item: item.name.lower()):
        if path.suffix.lower() != ".mzml":
            continue
        size_bytes = path.stat().st_size
        if size_bytes < min_bytes:
            continue
        if limit_bytes is not None and size_bytes >= limit_bytes:
            continue
        selected.append(SelectedFile(path=path, size_bytes=size_bytes))
    return sorted(selected, key=lambda item: (-item.size_bytes, item.path.name.lower()))


def _size_scope_label(min_file_size_gb: float, max_file_size_gb: float) -> str:
    min_gb = max(0.0, float(min_file_size_gb))
    max_gb = float(max_file_size_gb)
    if min_gb <= 0.0 and max_gb > 0.0:
        return f"<{max_gb:g}G"
    if min_gb > 0.0 and max_gb <= 0.0:
        return f">={min_gb:g}G"
    if min_gb > 0.0 and max_gb > 0.0:
        return f">={min_gb:g}G_< {max_gb:g}G"
    return "all_sizes"


def _balanced_batches(files: list[SelectedFile], batch_width: int) -> list[list[SelectedFile]]:
    if batch_width <= 1:
        return [[item] for item in files]
    working = list(files)
    batches: list[list[SelectedFile]] = []
    while working:
        if len(working) == 1:
            batches.append([working.pop(0)])
            continue
        batch = [working.pop(0)]
        while len(batch) < batch_width and working:
            batch.append(working.pop(-1))
        batches.append(batch)
    return batches


def _pgrep_matches(pattern: str) -> list[str]:
    if not pattern.strip():
        return []
    proc = subprocess.run(
        ["pgrep", "-af", pattern],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return [f"pgrep_error_rc={proc.returncode}: {proc.stderr.strip()}"]
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _wait_for_pattern(pattern: str, poll_seconds: int, progress_jsonl: Path, artifact_log_md: Path) -> None:
    if not pattern.strip():
        return
    first = True
    while True:
        matches = _pgrep_matches(pattern)
        if not matches:
            _append_jsonl(
                progress_jsonl,
                {
                    "event": "wait_target_absent",
                    "timestamp": _now_iso(),
                    "pattern": pattern,
                },
            )
            _append_md(artifact_log_md, f"- `{_now_iso()}` wait target absent: `{pattern}`")
            return
        if first:
            _append_jsonl(
                progress_jsonl,
                {
                    "event": "wait_target_present",
                    "timestamp": _now_iso(),
                    "pattern": pattern,
                    "matches": matches,
                },
            )
            _append_md(artifact_log_md, f"- `{_now_iso()}` waiting for existing task: `{pattern}`")
            first = False
        else:
            _append_jsonl(
                progress_jsonl,
                {
                    "event": "wait_heartbeat",
                    "timestamp": _now_iso(),
                    "pattern": pattern,
                    "matches": matches,
                },
            )
        time.sleep(max(5, poll_seconds))


def _write_selection_files(
    *,
    selected_files: list[SelectedFile],
    batches: list[list[SelectedFile]],
    manifest_json: Path,
    manifest_csv: Path,
    readme_md: Path,
    primary_jobs: int,
    retry_jobs: int,
    max_concurrent_batches: int,
    input_dir: Path,
    size_scope_label: str,
) -> None:
    manifest_json.parent.mkdir(parents=True, exist_ok=True)
    manifest = {
        "generated_at": _now_iso(),
        "n_files": len(selected_files),
        "selected_files": _json_safe(selected_files),
        "batches": [
            {
                "batch_index": idx,
                "batch_label": f"batch_{idx:02d}",
                "files": _json_safe(batch),
            }
            for idx, batch in enumerate(batches, start=1)
        ],
    }
    manifest_json.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_lines = ["batch_index,batch_label,file_name,size_bytes,size_gb,file_path"]
    for idx, batch in enumerate(batches, start=1):
        batch_label = f"batch_{idx:02d}"
        for item in batch:
            csv_lines.append(
                f"{idx},{batch_label},\"{item.path.name}\",{item.size_bytes},{item.size_gb:.6f},\"{item.path}\""
            )
    manifest_csv.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    lines = [
        f"# TrackCodec StackZDPD Validation {size_scope_label} Supervised Run",
        "",
        f"- Generated at: `{_now_iso()}`",
        f"- Input directory: `{input_dir}`",
        f"- Files selected (`{size_scope_label}`): `{len(selected_files)}`",
        f"- Supervisor behavior: `jobs={primary_jobs}`, retry same batch with `jobs={retry_jobs}` on non-zero exit.",
        f"- Max concurrent batches: `{max_concurrent_batches}`",
        "",
        "## Selected Files",
        "",
    ]
    for item in selected_files:
        lines.append(f"- `{item.path.name}`: `{item.size_gb:.3f} GB`")
    lines.extend(
        [
            "",
            "## Planned Batches",
            "",
        ]
    )
    for idx, batch in enumerate(batches, start=1):
        batch_desc = ", ".join(f"`{item.path.name}` ({item.size_gb:.3f} GB)" for item in batch)
        lines.append(f"- `batch_{idx:02d}`: {batch_desc}")
    readme_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _prepare_stage_dir(stage_dir: Path, batch: list[SelectedFile]) -> None:
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True, exist_ok=True)
    for item in batch:
        link_path = stage_dir / item.path.name
        os.symlink(item.path, link_path)


def _batch_result_done(result_root: Path) -> bool:
    return (result_root / "README.json").exists()


def _find_running_batch_matches(result_root: Path) -> list[str]:
    proc = subprocess.run(
        ["pgrep", "-af", BENCHMARK_SCRIPT.name],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode not in (0, 1):
        return []
    result_root_text = str(result_root)
    benchmark_script_text = str(BENCHMARK_SCRIPT)
    matches: list[str] = []
    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if result_root_text in line and benchmark_script_text in line:
            matches.append(line)
    return matches


def _active_batch_still_running(active_batch: ActiveBatch) -> bool:
    if active_batch.process is not None:
        return active_batch.process.poll() is None
    if active_batch.pid is not None and _pid_alive(active_batch.pid):
        return True
    return bool(_find_running_batch_matches(active_batch.result_root))


def _terminate_active_batch(
    *,
    active_batch: ActiveBatch,
    progress_jsonl: Path,
    artifact_log_md: Path,
    event: str,
    reason: str,
    grace_seconds: float,
) -> None:
    last_stdout = _read_tail_line(active_batch.stdout_log)
    last_stderr = _read_tail_line(active_batch.stderr_log)
    _append_jsonl(
        progress_jsonl,
        {
            "event": event,
            "timestamp": _now_iso(),
            "batch_index": active_batch.batch_index,
            "jobs": active_batch.jobs,
            "pid": active_batch.pid,
            "pgid": active_batch.pgid,
            "external": bool(active_batch.external),
            "reason": reason,
            "result_root": str(active_batch.result_root),
            "last_stdout": last_stdout,
            "last_stderr": last_stderr,
        },
    )
    _append_md(
        artifact_log_md,
        f"- `{_now_iso()}` {event} batch `{active_batch.batch_index:02d}` jobs=`{active_batch.jobs}` "
        f"pid=`{active_batch.pid}` pgid=`{active_batch.pgid}` reason=`{reason}` "
        f"last_stdout=`{last_stdout}` last_stderr=`{last_stderr}`",
    )

    term_sent = False
    if active_batch.pgid is not None and active_batch.pgid > 0:
        try:
            os.killpg(active_batch.pgid, signal.SIGTERM)
            term_sent = True
        except ProcessLookupError:
            pass
    if not term_sent and active_batch.process is not None and active_batch.process.poll() is None:
        try:
            active_batch.process.terminate()
            term_sent = True
        except Exception:
            pass
    if not term_sent and active_batch.pid is not None and _pid_alive(active_batch.pid):
        try:
            os.kill(active_batch.pid, signal.SIGTERM)
            term_sent = True
        except ProcessLookupError:
            pass

    deadline = time.time() + max(1.0, float(grace_seconds))
    while time.time() < deadline:
        if not _active_batch_still_running(active_batch):
            break
        time.sleep(1.0)

    if _active_batch_still_running(active_batch):
        if active_batch.pgid is not None and active_batch.pgid > 0:
            try:
                os.killpg(active_batch.pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif active_batch.process is not None and active_batch.process.poll() is None:
            try:
                active_batch.process.kill()
            except Exception:
                pass
        elif active_batch.pid is not None and _pid_alive(active_batch.pid):
            try:
                os.kill(active_batch.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _append_jsonl(
            progress_jsonl,
            {
                "event": f"{event}_sigkill",
                "timestamp": _now_iso(),
                "batch_index": active_batch.batch_index,
                "jobs": active_batch.jobs,
                "pid": active_batch.pid,
                "pgid": active_batch.pgid,
                "reason": reason,
            },
        )


def _parse_pids(matches: list[str]) -> list[int]:
    pids: list[int] = []
    for line in matches:
        parts = line.split(maxsplit=1)
        if not parts:
            continue
        try:
            pids.append(int(parts[0]))
        except ValueError:
            continue
    return pids


def _build_command(
    *,
    input_dir: Path,
    result_root: Path,
    jobs: int,
    baseline_threads: int,
    whole_workers: int,
    whole_ms2_section_workers: int,
    whole_ms2_segment_workers: int,
    compare_baseline_errors: bool,
) -> list[str]:
    cmd = [
        str(PYTHON_BIN),
        str(BENCHMARK_SCRIPT),
        "--input-dir",
        str(input_dir),
        "--result-root",
        str(result_root),
        "--jobs",
        str(jobs),
        "--baseline-threads",
        str(baseline_threads),
        "--whole-workers",
        str(whole_workers),
        "--whole-ms2-section-workers",
        str(whole_ms2_section_workers),
    ]
    if int(whole_ms2_segment_workers) > 0:
        cmd.extend(["--whole-ms2-segment-workers", str(int(whole_ms2_segment_workers))])
    if compare_baseline_errors:
        cmd.append("--compare-baseline-errors")
    return cmd


def _build_active_batch(
    *,
    batch_index: int,
    batch: list[SelectedFile],
    jobs: int,
    batch_root: Path,
    logs_root: Path,
    progress_jsonl: Path,
    artifact_log_md: Path,
    baseline_threads: int,
    whole_workers: int,
    whole_ms2_section_workers: int,
    whole_ms2_segment_workers: int,
    compare_baseline_errors: bool,
    retry_mode: bool,
) -> ActiveBatch | None:
    stage_dir = batch_root / "staging_input"
    result_root = batch_root / f"benchmark_jobs{jobs}"
    stdout_log = logs_root / f"batch_{batch_index:02d}.jobs{jobs}.stdout.log"
    stderr_log = logs_root / f"batch_{batch_index:02d}.jobs{jobs}.stderr.log"

    if _batch_result_done(result_root):
        _append_jsonl(
            progress_jsonl,
            {
                "event": "batch_skip_completed",
                "timestamp": _now_iso(),
                "batch_index": batch_index,
                "jobs": jobs,
                "result_root": str(result_root),
                "files": _json_safe(batch),
            },
        )
        _append_md(
            artifact_log_md,
            f"- `{_now_iso()}` batch `{batch_index:02d}` skip completed for `jobs={jobs}`: `{result_root}`",
        )
        return None

    running_matches = _find_running_batch_matches(result_root)
    if running_matches:
        attached_pids = _parse_pids(running_matches)
        _append_jsonl(
            progress_jsonl,
            {
                "event": "batch_attach_running",
                "timestamp": _now_iso(),
                "batch_index": batch_index,
                "jobs": jobs,
                "result_root": str(result_root),
                "pids": attached_pids,
                "matches": running_matches,
                "files": _json_safe(batch),
            },
        )
        _append_md(
            artifact_log_md,
            f"- `{_now_iso()}` batch `{batch_index:02d}` attach running `jobs={jobs}` "
            f"pid(s)=`{','.join(str(pid) for pid in attached_pids) or 'unknown'}`: `{result_root}`",
        )
        return ActiveBatch(
            batch_index=batch_index,
            batch=batch,
            jobs=jobs,
            batch_root=batch_root,
            logs_root=logs_root,
            stage_dir=stage_dir,
            result_root=result_root,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            process=None,
            pid=attached_pids[0] if attached_pids else None,
            pgid=_safe_getpgid(attached_pids[0] if attached_pids else None),
            external=True,
            retry_mode=retry_mode,
        )

    _prepare_stage_dir(stage_dir, batch)
    cmd = _build_command(
        input_dir=stage_dir,
        result_root=result_root,
        jobs=jobs,
        baseline_threads=baseline_threads,
        whole_workers=whole_workers,
        whole_ms2_section_workers=whole_ms2_section_workers,
        whole_ms2_segment_workers=whole_ms2_segment_workers,
        compare_baseline_errors=compare_baseline_errors,
    )

    _append_jsonl(
        progress_jsonl,
        {
            "event": "batch_start",
            "timestamp": _now_iso(),
            "batch_index": batch_index,
            "jobs": jobs,
            "result_root": str(result_root),
            "stage_dir": str(stage_dir),
            "files": _json_safe(batch),
            "cmd": cmd,
        },
    )
    _append_md(
        artifact_log_md,
        f"- `{_now_iso()}` batch `{batch_index:02d}` start `jobs={jobs}` with "
        + ", ".join(f"`{item.path.name}`" for item in batch),
    )

    stdout_log.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TRACKCODEC_PYTHON"] = str(PYTHON_BIN)
    env["PYTHONPATH"] = str(ROOT.parent) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    with stdout_log.open("a", encoding="utf-8") as stdout_handle, stderr_log.open("a", encoding="utf-8") as stderr_handle:
        process = subprocess.Popen(
            cmd,
            stdout=stdout_handle,
            stderr=stderr_handle,
            cwd=str(ROOT.parent),
            env=env,
            **_background_process_kwargs(),
        )
        _append_jsonl(
            progress_jsonl,
            {
                "event": "batch_subprocess_started",
                "timestamp": _now_iso(),
                "batch_index": batch_index,
                "jobs": jobs,
                "pid": process.pid,
            },
        )
    return ActiveBatch(
        batch_index=batch_index,
        batch=batch,
        jobs=jobs,
        batch_root=batch_root,
        logs_root=logs_root,
        stage_dir=stage_dir,
        result_root=result_root,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        process=process,
        pid=process.pid,
        pgid=_safe_getpgid(process.pid),
        external=False,
        retry_mode=retry_mode,
    )


def _poll_active_batch(
    *,
    active_batch: ActiveBatch,
    progress_jsonl: Path,
    artifact_log_md: Path,
) -> int | None:
    last_stdout = _read_tail_line(active_batch.stdout_log)
    last_stderr = _read_tail_line(active_batch.stderr_log)

    if active_batch.external:
        running_matches = _find_running_batch_matches(active_batch.result_root)
        if running_matches:
            attached_pids = _parse_pids(running_matches)
            _append_jsonl(
                progress_jsonl,
                {
                    "event": "batch_heartbeat",
                    "timestamp": _now_iso(),
                    "batch_index": active_batch.batch_index,
                    "jobs": active_batch.jobs,
                    "pid": attached_pids[0] if attached_pids else active_batch.pid,
                    "pids": attached_pids,
                    "result_root": str(active_batch.result_root),
                    "external": True,
                    "last_stdout": last_stdout,
                    "last_stderr": last_stderr,
                },
            )
            return None
        rc = 0 if _batch_result_done(active_batch.result_root) else 999
    else:
        assert active_batch.process is not None
        rc = active_batch.process.poll()
        if rc is None:
            _append_jsonl(
                progress_jsonl,
                {
                    "event": "batch_heartbeat",
                    "timestamp": _now_iso(),
                    "batch_index": active_batch.batch_index,
                    "jobs": active_batch.jobs,
                    "pid": active_batch.pid,
                    "result_root": str(active_batch.result_root),
                    "external": False,
                    "last_stdout": last_stdout,
                    "last_stderr": last_stderr,
                },
            )
            return None

    event_name = "batch_done" if rc == 0 else "batch_failed"
    _append_jsonl(
        progress_jsonl,
        {
            "event": event_name,
            "timestamp": _now_iso(),
            "batch_index": active_batch.batch_index,
            "jobs": active_batch.jobs,
            "pid": active_batch.pid,
            "returncode": int(rc),
            "result_root": str(active_batch.result_root),
            "external": bool(active_batch.external),
            "last_stdout": last_stdout,
            "last_stderr": last_stderr,
        },
    )
    if rc == 0:
        _append_md(
            artifact_log_md,
            f"- `{_now_iso()}` batch `{active_batch.batch_index:02d}` succeeded with `jobs={active_batch.jobs}`: "
            f"`{active_batch.result_root}`",
        )
    else:
        _append_md(
            artifact_log_md,
            f"- `{_now_iso()}` batch `{active_batch.batch_index:02d}` failed with `jobs={active_batch.jobs}` "
            f"rc=`{rc}`. last_stdout=`{last_stdout}` last_stderr=`{last_stderr}`",
        )
    return int(rc)


def main() -> int:
    assert_python_native_speedups_available(str(PYTHON_BIN), cwd=ROOT)
    parser = argparse.ArgumentParser(description="Supervised StackZDPD validation benchmark with automatic fallback.")
    parser.add_argument("--input-dir", type=Path, default=os.environ.get(STACKZDPD_VALIDATION_ENV))
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--min-file-size-gb", type=float, default=0.0)
    parser.add_argument("--max-file-size-gb", type=float, default=5.0)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--retry-jobs", type=int, default=1)
    parser.add_argument("--baseline-threads", type=int, default=1)
    parser.add_argument("--whole-workers", type=int, default=1)
    parser.add_argument("--whole-ms2-section-workers", type=int, default=8)
    parser.add_argument("--whole-ms2-segment-workers", type=int, default=0)
    parser.add_argument("--max-concurrent-batches", type=int, default=2)
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--max-total-memory-gb", type=float, default=100.0)
    parser.add_argument("--memory-guard-cooldown-seconds", type=int, default=120)
    parser.add_argument("--memory-guard-grace-seconds", type=int, default=15)
    parser.add_argument("--wait-pgrep-pattern", default=WAIT_PGREP_PATTERN)
    parser.add_argument("--compare-baseline-errors", action="store_true")
    args = parser.parse_args()
    if args.input_dir is None:
        raise ValueError(f"--input-dir or {STACKZDPD_VALIDATION_ENV} is required for the StackZDPD validation dataset.")

    result_root = Path(args.result_root)
    size_scope_label = _size_scope_label(float(args.min_file_size_gb), float(args.max_file_size_gb))
    result_root.mkdir(parents=True, exist_ok=True)
    log_dir = result_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_jsonl = log_dir / "supervisor.progress.jsonl"
    artifact_log_md = result_root / "stackzdpd_validation_supervisor_artifact_log_20260425.md"
    report_md = result_root / "stackzdpd_validation_supervisor_report_20260425.md"

    _append_jsonl(
        progress_jsonl,
        {
            "event": "supervisor_start",
            "timestamp": _now_iso(),
            "input_dir": str(args.input_dir),
            "result_root": str(result_root),
            "jobs": int(args.jobs),
            "retry_jobs": int(args.retry_jobs),
            "max_concurrent_batches": int(args.max_concurrent_batches),
            "min_file_size_gb": float(args.min_file_size_gb),
            "max_file_size_gb": float(args.max_file_size_gb),
            "max_total_memory_gb": float(args.max_total_memory_gb),
            "memory_guard_cooldown_seconds": int(args.memory_guard_cooldown_seconds),
            "memory_guard_grace_seconds": int(args.memory_guard_grace_seconds),
        },
    )

    if not artifact_log_md.exists():
        artifact_log_md.write_text(
            "\n".join(
                [
                    f"# StackZDPD Validation {size_scope_label} Supervisor Artifact Log",
                    "",
                    f"- Start time: `{_now_iso()}`",
                    f"- Input directory: `{args.input_dir}`",
                    f"- Result root: `{result_root}`",
                    f"- Primary jobs: `{args.jobs}`",
                    f"- Retry jobs: `{args.retry_jobs}`",
                    f"- Max concurrent batches: `{args.max_concurrent_batches}`",
                    f"- Min file size selector: `{args.min_file_size_gb} GB`",
                    f"- Max total memory guard: `{args.max_total_memory_gb} GB`",
                    f"- Memory guard cooldown: `{args.memory_guard_cooldown_seconds} s`",
                    f"- Memory guard grace kill: `{args.memory_guard_grace_seconds} s`",
                    "",
                    "## Chronological Log",
                    "",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

    selected_files = _discover_files_in_size_range(
        Path(args.input_dir),
        min_file_size_gb=float(args.min_file_size_gb),
        max_file_size_gb=float(args.max_file_size_gb),
    )
    if not selected_files:
        raise SystemExit(
            "No mzML files matched the requested size range: "
            f"input_dir={args.input_dir} min_file_size_gb={args.min_file_size_gb} max_file_size_gb={args.max_file_size_gb}"
        )

    batches = _balanced_batches(selected_files, max(1, int(args.jobs)))
    _write_selection_files(
        selected_files=selected_files,
        batches=batches,
        manifest_json=result_root / "selected_files_manifest.json",
        manifest_csv=result_root / "selected_files_manifest.csv",
        readme_md=result_root / "README.md",
        primary_jobs=max(1, int(args.jobs)),
        retry_jobs=max(1, int(args.retry_jobs)),
        max_concurrent_batches=max(1, int(args.max_concurrent_batches)),
        input_dir=Path(args.input_dir),
        size_scope_label=size_scope_label,
    )

    report_lines = [
        f"# StackZDPD Validation {size_scope_label} Supervisor Report",
        "",
        f"- Generated at: `{_now_iso()}`",
        f"- Input directory: `{args.input_dir}`",
        f"- Result root: `{result_root}`",
        f"- Primary jobs: `{args.jobs}`",
        f"- Retry jobs: `{args.retry_jobs}`",
        f"- Max concurrent batches: `{args.max_concurrent_batches}`",
        f"- Max total memory guard: `{args.max_total_memory_gb} GB`",
        f"- Memory guard cooldown: `{args.memory_guard_cooldown_seconds} s`",
        f"- Memory guard grace kill: `{args.memory_guard_grace_seconds} s`",
        f"- Min file size: `{args.min_file_size_gb} GB`",
        f"- Max file size: `{args.max_file_size_gb} GB`",
        "",
        "## Scope",
        "",
        f"- Select mzML files within `{size_scope_label}`.",
        f"- Each batch runs up to `{max(1, int(args.jobs))}` file(s) in parallel.",
        f"- Supervisor keeps up to `{max(1, int(args.max_concurrent_batches))}` batch(es) active concurrently.",
        "- If a batch exits non-zero, append the error to supervisor logs and retry the same batch with one file in parallel.",
        "- If total system used memory exceeds the configured ceiling, terminate active benchmark process groups immediately and requeue them.",
        "",
        "## Selected Files",
        "",
    ]
    for item in selected_files:
        report_lines.append(f"- `{item.path.name}`: `{item.size_gb:.3f} GB`")
    report_lines.extend(["", "## Planned Batches", ""])
    for idx, batch in enumerate(batches, start=1):
        report_lines.append(
            f"- `batch_{idx:02d}`: " + ", ".join(f"`{item.path.name}` ({item.size_gb:.3f} GB)" for item in batch)
        )
    report_md.write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    _wait_for_pattern(str(args.wait_pgrep_pattern), int(args.poll_seconds), progress_jsonl, artifact_log_md)

    runs_root = result_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)
    pending_batches: list[dict] = [
        {
            "batch_index": batch_index,
            "batch": batch,
            "jobs": max(1, int(args.jobs)),
            "retry_mode": False,
        }
        for batch_index, batch in enumerate(batches, start=1)
    ]
    active_batches: dict[int, ActiveBatch] = {}
    max_concurrent_batches = max(1, int(args.max_concurrent_batches))
    poll_seconds = max(10, int(args.poll_seconds))
    max_total_memory_bytes = int(max(0.0, float(args.max_total_memory_gb)) * GB)
    memory_guard_cooldown_seconds = max(1, int(args.memory_guard_cooldown_seconds))
    memory_guard_grace_seconds = max(1.0, float(args.memory_guard_grace_seconds))

    while pending_batches or active_batches:
        memory_state = _read_system_memory_snapshot()
        _append_jsonl(
            progress_jsonl,
            {
                "event": "supervisor_heartbeat",
                "timestamp": _now_iso(),
                "pending_batches": len(pending_batches),
                "active_batches": sorted(active_batches.keys()),
                "memory_used_gb": float(memory_state.get("used_gb", 0.0)),
                "memory_available_gb": float(memory_state.get("available_gb", 0.0)),
                "swap_used_gb": float(memory_state.get("swap_used_gb", 0.0)),
            },
        )

        if max_total_memory_bytes > 0 and int(memory_state.get("used_bytes", 0)) > max_total_memory_bytes:
            if active_batches:
                reason = (
                    f"system_used_gb={float(memory_state.get('used_gb', 0.0)):.2f} "
                    f"> max_total_memory_gb={float(args.max_total_memory_gb):.2f}"
                )
                requeue_specs = []
                for batch_index, active in list(active_batches.items()):
                    _terminate_active_batch(
                        active_batch=active,
                        progress_jsonl=progress_jsonl,
                        artifact_log_md=artifact_log_md,
                        event="memory_guard_triggered",
                        reason=reason,
                        grace_seconds=memory_guard_grace_seconds,
                    )
                    requeue_specs.append(
                        {
                            "batch_index": batch_index,
                            "batch": active.batch,
                            "jobs": max(1, int(args.retry_jobs)) if int(active.jobs) != int(args.retry_jobs) else int(active.jobs),
                            "retry_mode": True,
                        }
                    )
                active_batches.clear()
                pending_batches = requeue_specs + pending_batches
                _append_md(
                    artifact_log_md,
                    f"- `{_now_iso()}` memory guard cooldown `{memory_guard_cooldown_seconds}s` after forced termination",
                )
                time.sleep(memory_guard_cooldown_seconds)
                continue
            _append_md(
                artifact_log_md,
                f"- `{_now_iso()}` launch delayed by memory guard: "
                f"used=`{float(memory_state.get('used_gb', 0.0)):.2f} GB` "
                f"> limit=`{float(args.max_total_memory_gb):.2f} GB`",
            )
            time.sleep(poll_seconds)
            continue

        while pending_batches and len(active_batches) < max_concurrent_batches:
            spec = pending_batches.pop(0)
            batch_index = int(spec["batch_index"])
            batch = spec["batch"]
            batch_root = runs_root / f"batch_{batch_index:02d}"
            batch_logs_root = log_dir / f"batch_{batch_index:02d}"
            batch_root.mkdir(parents=True, exist_ok=True)
            batch_logs_root.mkdir(parents=True, exist_ok=True)
            active = _build_active_batch(
                batch_index=batch_index,
                batch=batch,
                jobs=max(1, int(spec["jobs"])),
                batch_root=batch_root,
                logs_root=batch_logs_root,
                progress_jsonl=progress_jsonl,
                artifact_log_md=artifact_log_md,
                baseline_threads=max(1, int(args.baseline_threads)),
                whole_workers=max(1, int(args.whole_workers)),
                whole_ms2_section_workers=max(1, int(args.whole_ms2_section_workers)),
                whole_ms2_segment_workers=max(0, int(args.whole_ms2_segment_workers)),
                compare_baseline_errors=bool(args.compare_baseline_errors),
                retry_mode=bool(spec["retry_mode"]),
            )
            if active is not None:
                active_batches[batch_index] = active

        if not active_batches:
            continue

        time.sleep(poll_seconds)

        finished_indexes: list[int] = []
        for batch_index, active in list(active_batches.items()):
            rc = _poll_active_batch(
                active_batch=active,
                progress_jsonl=progress_jsonl,
                artifact_log_md=artifact_log_md,
            )
            if rc is None:
                continue
            finished_indexes.append(batch_index)
            if rc != 0 and int(args.retry_jobs) != int(active.jobs):
                pending_batches.insert(
                    0,
                    {
                        "batch_index": batch_index,
                        "batch": active.batch,
                        "jobs": max(1, int(args.retry_jobs)),
                        "retry_mode": True,
                    },
                )
                _append_jsonl(
                    progress_jsonl,
                    {
                        "event": "batch_retry_scheduled",
                        "timestamp": _now_iso(),
                        "batch_index": batch_index,
                        "retry_jobs": int(args.retry_jobs),
                        "returncode": rc,
                    },
                )
                _append_md(
                    artifact_log_md,
                    f"- `{_now_iso()}` batch `{batch_index:02d}` scheduled retry with `jobs={args.retry_jobs}` "
                    f"after rc=`{rc}`",
                )
                continue
            if rc != 0:
                stop_event = "supervisor_stop_after_retry_failure" if active.retry_mode else "supervisor_stop_after_primary_failure"
                _append_jsonl(
                    progress_jsonl,
                    {
                        "event": stop_event,
                        "timestamp": _now_iso(),
                        "batch_index": batch_index,
                        "jobs": int(active.jobs),
                        "returncode": rc,
                    },
                )
                _append_md(
                    artifact_log_md,
                    f"- `{_now_iso()}` stop after {'retry' if active.retry_mode else 'primary'} failure "
                    f"on batch `{batch_index:02d}` rc=`{rc}`",
                )
                return int(rc)

        for batch_index in finished_indexes:
            active_batches.pop(batch_index, None)

    _append_jsonl(
        progress_jsonl,
        {
            "event": "supervisor_done",
            "timestamp": _now_iso(),
            "n_batches": len(batches),
            "n_files": len(selected_files),
        },
    )
    _append_md(
        artifact_log_md,
        f"- `{_now_iso()}` supervisor finished successfully for `{len(selected_files)}` files across `{len(batches)}` batches",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
