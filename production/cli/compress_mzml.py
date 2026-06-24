from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

for _thread_var in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_thread_var, "1")

from ..mzml.archive_codec import MzMLSectionArchiveCodec
from ..common.runtime_env import assert_native_speedups_available


def _bool_arg(value: str) -> bool:
    value_norm = str(value).strip().lower()
    if value_norm in {"1", "true", "yes", "y", "on"}:
        return True
    if value_norm in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"Expected boolean value, got {value!r}")


def _format_pct(value) -> str:
    try:
        return f"{float(value):6.2f}%"
    except (TypeError, ValueError):
        return "  0.00%"


def _make_progress_callback(mode: str, interval_s: float):
    mode = str(mode or "none").lower()
    if mode == "none":
        return None
    min_interval = max(0.0, float(interval_s))
    state = {"last_emit": 0.0, "last_stage": None}

    def _callback(payload: dict) -> None:
        now = time.monotonic()
        stage = str(payload.get("stage", "progress"))
        force = stage != state["last_stage"] or float(payload.get("overall_pct", 0.0)) >= 100.0
        if not force and min_interval > 0.0 and now - float(state["last_emit"]) < min_interval:
            return
        state["last_emit"] = now
        state["last_stage"] = stage
        if mode == "jsonl":
            print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
            return
        timestamp = time.strftime("%H:%M:%S")
        parts = [
            f"[{timestamp}]",
            str(payload.get("scope", "archive")),
            stage,
            f"overall={_format_pct(payload.get('overall_pct'))}",
            f"ms1={_format_pct(payload.get('ms1_pct'))}",
            f"ms2={_format_pct(payload.get('ms2_pct'))}",
            f"metadata={_format_pct(payload.get('metadata_pct'))}",
        ]
        if "done_units" in payload and "total_units" in payload:
            parts.append(f"units={payload.get('done_units')}/{payload.get('total_units')}")
        if "done_tracks" in payload and "total_tracks" in payload:
            parts.append(f"tracks={payload.get('done_tracks')}/{payload.get('total_tracks')}")
        print(" ".join(parts), file=sys.stderr, flush=True)

    return _callback


def main():
    assert_native_speedups_available(required=True)
    parser = argparse.ArgumentParser(description="Compress an mzML file into a TrackCodec section archive.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--stats-json", required=False)
    parser.add_argument("--section-workers", type=int, default=16)
    parser.add_argument("--ms2-segment-workers", type=int, default=0, help="0 reuses --section-workers for MS2 section segment compression.")
    parser.add_argument(
        "--ms1-island-workers",
        type=int,
        default=1 if os.name == "nt" else None,
        help="MS1 island extraction workers. Defaults to 1 on Windows for stable source-tree execution.",
    )
    parser.add_argument("--parallel-ms1-ms2-processes", type=_bool_arg, default=None)
    parser.add_argument("--memory-aware-parallel", type=_bool_arg, default=True)
    parser.add_argument("--parallel-min-available-gb", type=float, default=24.0)
    parser.add_argument("--cache-dir", default=None)
    parser.add_argument(
        "--progress",
        choices=("none", "text", "jsonl"),
        default="none",
        help="Emit encode progress to stderr. Use 'text' for CMD-visible progress or 'jsonl' for machine-readable lines.",
    )
    parser.add_argument(
        "--progress-interval-s",
        type=float,
        default=5.0,
        help="Minimum seconds between repeated progress updates for the same stage.",
    )
    args = parser.parse_args()

    codec = MzMLSectionArchiveCodec(
        ms2_section_workers=args.section_workers,
        ms2_segment_workers=args.ms2_segment_workers if int(args.ms2_segment_workers) > 0 else None,
        ms1_island_workers=args.ms1_island_workers,
        parallel_ms1_ms2_processes=args.parallel_ms1_ms2_processes,
        memory_aware_parallel=args.memory_aware_parallel,
        parallel_min_available_bytes=int(float(args.parallel_min_available_gb) * (1 << 30)),
        cache_dir=args.cache_dir,
    )
    output = Path(args.output)
    _, meta = codec.encode_file_to_path(
        args.input,
        output,
        progress_callback=_make_progress_callback(args.progress, args.progress_interval_s),
    )
    if args.stats_json:
        Path(args.stats_json).write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
