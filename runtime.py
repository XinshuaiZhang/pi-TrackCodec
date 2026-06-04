from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np


def _apply_runtime_thread_caps() -> None:
    # The current hot paths are Python-heavy; capping BLAS/OpenMP pools avoids
    # dozens of mostly idle helper threads and reduces scheduler overhead.
    for name in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "BLIS_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        os.environ.setdefault(name, "1")


_apply_runtime_thread_caps()


PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parent


def _ensure_trackcodec_package() -> None:
    if importlib.util.find_spec("TrackCodec") is not None:
        return
    init_py = PACKAGE_ROOT / "__init__.py"
    if not init_py.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "TrackCodec",
        init_py,
        submodule_search_locations=[str(PACKAGE_ROOT)],
    )
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("TrackCodec", module)
    spec.loader.exec_module(module)


_ensure_trackcodec_package()

from TrackCodec.production.common.runtime_env import (
    assert_native_speedups_available,
    resolve_trackcodec_python,
)


DEFAULT_ARCHIVE_SECTION_WORKERS = 4
DEFAULT_MS1_ISLAND_WORKERS = 4
DEFAULT_MS2_SEGMENT_WORKERS = 0


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


def _write_json_if_needed(payload: dict, output_json: str | None) -> None:
    if output_json:
        Path(output_json).write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


def _print_json(payload) -> None:
    print(json.dumps(_json_safe(payload), indent=2))


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value


def _resolve_section_workers(container_kind: str, requested: int | None) -> int:
    if requested is not None:
        return max(1, int(requested))
    env_override = os.environ.get("TRACKCODEC_DEFAULT_SECTION_WORKERS")
    if env_override:
        return max(1, int(env_override))
    return DEFAULT_ARCHIVE_SECTION_WORKERS


def _get_trackcodec_classes():
    from TrackCodec.production.trackcodec import TrackCodecArchive

    return TrackCodecArchive


def _get_compare_files():
    from TrackCodec.production.validation.roundtrip import compare_files

    return compare_files


def _resolve_ms1_island_workers(requested: int | None) -> int:
    if requested is not None:
        return max(1, int(requested))
    env_override = os.environ.get("TRACKCODEC_MS1_ISLAND_WORKERS")
    if env_override:
        return max(1, int(env_override))
    return DEFAULT_MS1_ISLAND_WORKERS


def _resolve_ms2_segment_workers(requested: int | None) -> int:
    if requested is not None:
        return max(0, int(requested))
    env_override = os.environ.get("TRACKCODEC_MS2_SEGMENT_WORKERS")
    if env_override:
        return max(0, int(env_override))
    return DEFAULT_MS2_SEGMENT_WORKERS


def _build_archive_codec(args) -> TrackCodecArchive:
    TrackCodecArchive = _get_trackcodec_classes()
    workers = _resolve_section_workers("archive", getattr(args, "section_workers", None))
    return TrackCodecArchive(
        ms2_section_workers=workers,
        ms2_segment_workers=_resolve_ms2_segment_workers(getattr(args, "ms2_segment_workers", None)),
        ms1_encode_section_workers=workers,
        ms1_island_workers=_resolve_ms1_island_workers(getattr(args, "ms1_island_workers", None)),
        retain_zero_intensity_mz=bool(getattr(args, "retain_zero_intensity_mz", True)),
        cache_dir=getattr(args, "cache_dir", None),
    )


def _build_codec(args):
    return _build_archive_codec(args)


def _cmd_compress(args) -> None:
    codec = _build_codec(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    _, meta = codec.encode_file_to_path(args.input, output)
    meta["runtime_section_workers"] = _resolve_section_workers("archive", getattr(args, "section_workers", None))
    meta["runtime_ms1_island_workers"] = _resolve_ms1_island_workers(getattr(args, "ms1_island_workers", None))
    meta["runtime_ms2_segment_workers"] = _resolve_ms2_segment_workers(getattr(args, "ms2_segment_workers", None))
    _write_json_if_needed(meta, args.stats_json)
    _print_json(meta)


def _cmd_decompress(args) -> None:
    codec = _build_archive_codec(args)
    out = {}
    if args.output_mzml:
        output_mzml = codec.decode_to_mzml_file(
            args.input,
            args.output_mzml,
            binary_compression=args.binary_compression,
        )
        out["output_mzml"] = str(output_mzml)
    if args.output_dir:
        sections = codec.decode_archive_file(args.input, decode_ms1_sidecars=False, decode_ms2_payload_json=False, decode_auxiliary_records_flag=False)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "archive_meta.json").write_text(json.dumps(sections["archive_meta"], indent=2), encoding="utf-8")
        (output_dir / "ms1_meta.json").write_text(json.dumps(sections["ms1_meta"], indent=2), encoding="utf-8")
        (output_dir / "ms2_meta.json").write_text(json.dumps(sections["ms2_meta"], indent=2), encoding="utf-8")
        (output_dir / "metadata_meta.json").write_text(json.dumps(sections["metadata_meta"], indent=2), encoding="utf-8")
        metadata_xml = sections["metadata_xml"]
        if isinstance(metadata_xml, bytes):
            (output_dir / "metadata_stripped.xml").write_bytes(metadata_xml)
        else:
            (output_dir / "metadata_stripped.xml").write_text(str(metadata_xml), encoding="utf-8")
        out["output_dir"] = str(output_dir)
    if not out:
        raise SystemExit("At least one of --output-mzml or --output-dir is required")
    _print_json(out)


def _cmd_export_mzml(args) -> None:
    codec = _build_archive_codec(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    codec.export_to_mzml_file(
        args.archive,
        output,
        binary_compression=args.binary_compression,
    )
    payload = {
        "archive": str(args.archive),
        "output_mzml": str(output),
        "binary_compression": args.binary_compression,
    }
    _write_json_if_needed(payload, args.summary_json)
    _print_json(payload)


def _cmd_validate(args) -> None:
    codec = _build_archive_codec(args)
    sections = codec.decode_archive_file(
        args.archive,
        decode_ms1_sidecars=False,
        decode_ms2_payload_json=False,
        decode_auxiliary_records_flag=False,
        decode_metadata_xml=False,
    )
    payload = {
        "archive": str(args.archive),
        "level": args.level,
        "archive_meta": sections["archive_meta"],
        "ms1_meta": sections["ms1_meta"],
        "ms2_meta": sections["ms2_meta"],
        "metadata_meta": sections["metadata_meta"],
    }

    if args.level == "quick":
        if args.output_mzml:
            reconstructed = codec.decode_to_mzml_file(args.archive, args.output_mzml)
            payload["output_mzml"] = str(reconstructed)
        _write_json_if_needed(payload, args.summary_json)
        _print_json(payload)
        return

    if not args.original_mzml:
        raise SystemExit("--original-mzml is required for --level full")

    if args.output_mzml:
        reconstructed_path = Path(args.output_mzml)
        reconstructed_path.parent.mkdir(parents=True, exist_ok=True)
        codec.decode_to_mzml_file(
            args.archive,
            reconstructed_path,
            binary_compression=args.binary_compression,
        )
    else:
        tmp_dir = tempfile.TemporaryDirectory(prefix="trackcodec_validate_")
        reconstructed_path = Path(tmp_dir.name) / f"{Path(args.archive).stem}.reconstructed.mzML"
        codec.decode_to_mzml_file(
            args.archive,
            reconstructed_path,
            binary_compression=args.binary_compression,
        )

    compare_files = _get_compare_files()
    roundtrip = compare_files(
        Path(args.original_mzml),
        reconstructed_path,
        per_spectrum_csv=Path(args.per_spectrum_csv) if args.per_spectrum_csv else None,
    )
    payload["output_mzml"] = str(reconstructed_path)
    payload["roundtrip"] = roundtrip
    _write_json_if_needed(payload, args.summary_json)
    _print_json(payload)


def _job_paths(job_root: Path) -> dict[str, Path]:
    log_dir = job_root / "logs"
    return {
        "job_root": job_root,
        "log_dir": log_dir,
        "status_json": job_root / "status.json",
        "stdout_log": log_dir / "job.stdout.log",
        "stderr_log": log_dir / "job.stderr.log",
    }


def _normalize_job_args(job_args: list[str]) -> list[str]:
    args = list(job_args)
    if args and args[0] == "--":
        args = args[1:]
    return args


def _build_job_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["TRACKCODEC_PYTHON"] = str(resolve_trackcodec_python())
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        env.setdefault(key, "1")
    return env


def _cmd_launch(args) -> None:
    job_root = Path(args.job_root)
    paths = _job_paths(job_root)
    paths["log_dir"].mkdir(parents=True, exist_ok=True)
    job_args = _normalize_job_args(list(args.job_args))
    if not job_args:
        raise SystemExit("launch requires a runtime subcommand after '--'")
    if job_args[0] in {"launch", "status", "_managed-run"}:
        raise SystemExit("launch cannot recursively wrap launch/status/_managed-run")

    runtime_path = Path(__file__).resolve()
    python_bin = str(resolve_trackcodec_python())
    command = [
        python_bin,
        str(runtime_path),
        "_managed-run",
        "--job-root",
        str(job_root),
        "--",
        *job_args,
    ]
    status_payload = {
        "state": "launched",
        "launch_ts": _now_iso(),
        "job_root": str(job_root),
        "command": command,
        "job_args": job_args,
        "stdout_log": str(paths["stdout_log"]),
        "stderr_log": str(paths["stderr_log"]),
        "default_archive_section_workers": DEFAULT_ARCHIVE_SECTION_WORKERS,
        "python_bin": python_bin,
    }
    _write_json(paths["status_json"], status_payload)

    with paths["stdout_log"].open("ab") as stdout_handle, paths["stderr_log"].open("ab") as stderr_handle:
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=_build_job_env(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
        )

    status_payload.update(
        {
            "state": "started",
            "pid": int(proc.pid),
            "started_ts": _now_iso(),
        }
    )
    _write_json(paths["status_json"], status_payload)
    _print_json(status_payload)


def _cmd_status(args) -> None:
    job_root = Path(args.job_root)
    status_path = _job_paths(job_root)["status_json"]
    if not status_path.exists():
        raise SystemExit(f"job status file not found: {status_path}")
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    pid = payload.get("pid")
    pid_alive = False
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            pid_alive = True
        except OSError:
            pid_alive = False
    payload["pid_alive"] = pid_alive
    payload["status_checked_ts"] = _now_iso()
    _write_json_if_needed(payload, args.output_json)
    _print_json(payload)


def _cmd_managed_run(args) -> None:
    job_root = Path(args.job_root)
    paths = _job_paths(job_root)
    paths["log_dir"].mkdir(parents=True, exist_ok=True)
    status_payload = {}
    if paths["status_json"].exists():
        status_payload = json.loads(paths["status_json"].read_text(encoding="utf-8"))
    job_args = _normalize_job_args(list(args.job_args))
    if not job_args:
        raise SystemExit("_managed-run requires a runtime subcommand after '--'")

    status_payload.update(
        {
            "state": "running",
            "running_ts": _now_iso(),
            "pid": os.getpid(),
            "job_args": job_args,
        }
    )
    _write_json(paths["status_json"], status_payload)

    parser = build_parser(include_job_control=False)
    child_args = parser.parse_args(job_args)
    started = time.perf_counter()
    try:
        child_args.func(child_args)
    except BaseException as exc:
        status_payload.update(
            {
                "state": "failed",
                "end_ts": _now_iso(),
                "duration_s": time.perf_counter() - started,
                "exception_type": type(exc).__name__,
                "exception": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        _write_json(paths["status_json"], status_payload)
        raise
    status_payload.update(
        {
            "state": "succeeded",
            "end_ts": _now_iso(),
            "duration_s": time.perf_counter() - started,
        }
    )
    _write_json(paths["status_json"], status_payload)


def build_parser(*, include_job_control: bool = True) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified runtime entry for the TrackCodec whole-file archive workflow.")
    sub = parser.add_subparsers(dest="command", required=True)

    compress = sub.add_parser("compress", help="Compress mzML into a .tcarchive file")
    compress.add_argument("--input", required=True)
    compress.add_argument("--output", required=True)
    compress.add_argument("--stats-json", required=False)
    compress.add_argument("--section-workers", type=int, default=None)
    compress.add_argument("--ms2-segment-workers", type=int, default=None)
    compress.add_argument("--ms1-island-workers", type=int, default=None)
    compress.add_argument("--cache-dir", default=None, help="Optional persistent cache for scan-store, MS1 tracks, and section payloads")
    compress.add_argument("--retain-zero-intensity-mz", dest="retain_zero_intensity_mz", action="store_true", default=True)
    compress.add_argument("--drop-zero-intensity-mz", dest="retain_zero_intensity_mz", action="store_false")
    compress.set_defaults(func=_cmd_compress)

    decompress = sub.add_parser("decompress", help="Decompress a .tcarchive archive")
    decompress.add_argument("--archive", dest="input", required=True)
    decompress.add_argument("--output-mzml", required=False)
    decompress.add_argument("--output-dir", required=False)
    decompress.add_argument("--binary-compression", choices=("preserve_template", "none", "zlib"), default="preserve_template")
    decompress.add_argument("--section-workers", type=int, default=None)
    decompress.add_argument("--ms2-segment-workers", type=int, default=None)
    decompress.add_argument("--ms1-island-workers", type=int, default=None)
    decompress.set_defaults(func=_cmd_decompress)

    export_mzml = sub.add_parser("export-mzml", help="Export a TrackCodec-Archive file as standard mzML")
    export_mzml.add_argument("--archive", required=True)
    export_mzml.add_argument("--output", required=True)
    export_mzml.add_argument("--binary-compression", choices=("preserve_template", "none", "zlib"), default="preserve_template")
    export_mzml.add_argument("--summary-json", required=False)
    export_mzml.add_argument("--section-workers", type=int, default=None)
    export_mzml.add_argument("--ms2-segment-workers", type=int, default=None)
    export_mzml.add_argument("--ms1-island-workers", type=int, default=None)
    export_mzml.set_defaults(func=_cmd_export_mzml)

    validate = sub.add_parser("validate", help="Validate a .tcarchive archive")
    validate.add_argument("--archive", required=True)
    validate.add_argument("--level", choices=("quick", "full"), default="quick")
    validate.add_argument("--original-mzml", required=False)
    validate.add_argument("--output-mzml", required=False)
    validate.add_argument("--binary-compression", choices=("preserve_template", "none", "zlib"), default="preserve_template")
    validate.add_argument("--summary-json", required=False)
    validate.add_argument("--per-spectrum-csv", required=False)
    validate.add_argument("--section-workers", type=int, default=None)
    validate.add_argument("--ms2-segment-workers", type=int, default=None)
    validate.add_argument("--ms1-island-workers", type=int, default=None)
    validate.set_defaults(func=_cmd_validate)

    if include_job_control:
        launch = sub.add_parser("launch", help="Launch a long-running runtime job with PID/log/status tracking")
        launch.add_argument("--job-root", required=True)
        launch.add_argument("job_args", nargs=argparse.REMAINDER)
        launch.set_defaults(func=_cmd_launch)

        status = sub.add_parser("status", help="Inspect a launched runtime job")
        status.add_argument("--job-root", required=True)
        status.add_argument("--output-json", required=False)
        status.set_defaults(func=_cmd_status)

        managed = sub.add_parser("_managed-run", help=argparse.SUPPRESS)
        managed.add_argument("--job-root", required=True)
        managed.add_argument("job_args", nargs=argparse.REMAINDER)
        managed.set_defaults(func=_cmd_managed_run)

    return parser


def main() -> None:
    resolved_python = resolve_trackcodec_python()
    current_python = Path(sys.executable).resolve()
    auto_reexec = os.environ.get("TRACKCODEC_AUTO_REEXEC", "").strip().lower() in {"1", "true", "yes", "on"}
    if resolved_python != current_python and auto_reexec and os.environ.get("TRACKCODEC_NO_AUTO_REEXEC") != "1":
        env = os.environ.copy()
        env["TRACKCODEC_PYTHON"] = str(resolved_python)
        env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        os.execve(str(resolved_python), [str(resolved_python), str(Path(__file__).resolve()), *sys.argv[1:]], env)
    assert_native_speedups_available(required=True)
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
