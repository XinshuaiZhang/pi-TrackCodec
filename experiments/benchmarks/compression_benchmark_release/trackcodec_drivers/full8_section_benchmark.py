from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import gzip
import json
import multiprocessing as mp
import os
import pickle
import statistics
import sys
import tempfile
import threading
import time
import traceback
import zlib
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pyteomics import mzml

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.datasets import (
    DIA_FILES,
    DDA_FILES,
    FULL8_EXTRA3_PROFILE_FILES,
    FULL8_PROFILE_FILES,
)
from TrackCodec.production.common.runtime_env import assert_native_speedups_available
from TrackCodec.production.common.memory import recommend_parallel_section_processes
from TrackCodec.production.common.scan_store import (
    create_memmap_scan_store_from_mzml,
    is_complete_memmap_scan_store,
    open_memmap_scan_store,
)
from TrackCodec.production.common.tracking import CompactIslandTracks
from TrackCodec.production.common.compression_backends import compress as backend_compress
from TrackCodec.production.common.compression_backends import decompress as backend_decompress
from TrackCodec.production.common.io import load_all_ms2_scans, load_scans
from TrackCodec.production.metadata.metadata_codec import MzMLMetadataCodec, extract_binary_stripped_metadata_xml
from TrackCodec.production.ms1.unified_codec import MS1Codec
from TrackCodec.production.ms1.cross_scan_codec import _unpack_segments
from TrackCodec.production.ms2.codec import DIAWindowMS2Codec, MS2ModeConfig
from TrackCodec.production.ms2.window_dictionary_codec import _unpack_exact_track_segments
from TrackCodec.production.mzml.archive_codec import (
    DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
    DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
    DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
    DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
    DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
    MzMLSectionArchiveCodec,
    _decode_ms1_full_mz_sidecar,
    _decode_ms1_orphan_intensity_sidecar,
    _decode_uint32_sidecar,
    _make_archive_ms1_mode,
    build_ms1_tracks as archive_build_ms1_tracks,
    build_ms1_tracks_for_archive,
)
from TrackCodec.production.mzml.reconstruction import reconstruct_ms1_scans_from_islands_with_full_mz
from TrackCodec.experiments.baselines.stack_zdpd_baseline import (
    compare_roundtrip_scans,
    float64_payload,
    gzip_raw_payload,
    raw_backend_payload,
    stack_zdpd_baseline_decode,
    stack_zdpd_baseline_encode,
    stack_zdpd_baseline_compress,
    zdpd_baseline_decode,
    zdpd_baseline_encode,
    zdpd_baseline_compress,
)
from TrackCodec.experiments.presets.ms1 import OUR_MODE
from TrackCodec.experiments.plotting.benchmark_from_csv import (
    plot_compression_bar_custom,
    plot_compression_line_custom,
    plot_speed_bar,
)


RESULT_ROOT = ROOT.parent / "benchmark_results" / "trackcodec_full8_sections_20260404"
PLOTS_DIR = RESULT_ROOT / "plots"
PLOT_DATA_DIR = RESULT_ROOT / "plot_data"
COMP_DIR = RESULT_ROOT / "compression_results"
TABLE_DIR = RESULT_ROOT / "comparison_tables"
LOG_DIR = RESULT_ROOT / "logs"
ARTIFACTS_DIR = RESULT_ROOT / "artifacts"

DOCS_DIR = ROOT.parent / "benchmark_results" / "docs"

DATASET_CONFIG = {
    "full8": {
        "files": FULL8_PROFILE_FILES,
        "result_dir_name": "trackcodec_full8_sections_20260404",
    },
    "full8_extra3": {
        "files": FULL8_EXTRA3_PROFILE_FILES,
        "result_dir_name": "trackcodec_full8_extra3_sections_20260411",
    },
}

CURRENT_DATASET_SCOPE = "full8"
CURRENT_FILES = FULL8_PROFILE_FILES
MS1_ISLAND_WORKERS = max(1, min(4, os.cpu_count() or 1))
MS1_ENCODE_SECTION_WORKERS = max(1, min(4, os.cpu_count() or 1))
MS1_TRACK_CACHE_KEY = "ms1_tracks_compact"
SECTION_FILE_JOBS = 1
BUNDLE_PARALLEL_SECTIONS = True
BUNDLE_PARALLEL_MIN_AVAILABLE_BYTES = 14 * (1 << 30)
BUNDLE_PARALLEL_WORKING_SET_MULTIPLIER = 6.0
BUNDLE_PARALLEL_FIXED_OVERHEAD_BYTES = 2 * (1 << 30)
BUNDLE_PARALLEL_HEADROOM_RATIO = 1.10

MS1_SECTION_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "zdpd_baseline",
    "stack_zdpd_baseline",
    "ours_eqfidelity",
    "ours_archive_fidelity_auto",
    "ours_strict_q6",
]
MS2_SECTION_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "zdpd_baseline",
    "stack_zdpd_baseline",
    "ours_eqfidelity",
]
METADATA_SECTION_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "ours_metadata",
]
OVERALL_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "zdpd_container",
    "stack_zdpd_container",
    "ours_container",
    "ours_archive_auto_container",
]

COLOR_MAP = {
    "raw": "#B0B0B0",
    "gzip": "#BFD7EA",
    "zlib": "#9ECAE1",
    "zstd-9": "#6BAED6",
    "zdpd_baseline": "#72B7B2",
    "stack_zdpd_baseline": "#9E9AC8",
    "ours_eqfidelity": "#F58518",
    "ours_archive_fidelity_auto": "#D67236",
    "ours_strict_q6": "#4C78A8",
    "ours_metadata": "#E45756",
    "zdpd_container": "#72B7B2",
    "stack_zdpd_container": "#9E9AC8",
    "ours_container": "#F58518",
    "ours_archive_auto_container": "#D67236",
}

DISPLAY_NAME = {
    "raw": "Raw float64 payload",
    "gzip": "gzip on raw float64",
    "zlib": "zlib on raw float64",
    "zstd-9": "zstd-9 on raw float64",
    "zdpd_baseline": "ZDPD baseline",
    "stack_zdpd_baseline": "Stack-ZDPD baseline",
    "ours_eqfidelity": "TrackCodec track/island component",
    "ours_archive_fidelity_auto": "TrackCodec Archive track/island",
    "ours_strict_q6": "Our current strict-q6 path",
    "ours_metadata": "Our metadata codec",
    "zdpd_container": "ZDPD + zlib metadata",
    "stack_zdpd_container": "Stack-ZDPD + zlib metadata",
    "ours_container": "TrackCodec combined container",
    "ours_archive_auto_container": "TrackCodec Archive auto container",
}

MS1_MZ_SEGMENTS = {
    "mz_lengths",
    "mz_first_values",
    "mz_delta_values",
    "mz_model_offsets",
    "mz_model_steps",
    "mz_model_residual_lengths",
    "mz_residual_values",
}
MS1_INT_SEGMENTS = {
    "full_intensity",
    "delta_intensity",
    "delta_overflow_idx",
    "delta_overflow_vals",
}
MS1_META_SEGMENTS = {
    "track_lengths",
    "scan_indices",
    "mz_ref_indices",
    "delta_ref_offsets",
    "mz_kinds",
    "int_kinds",
    "n_points",
    "array_starts",
}
FULL_SCAN_SIDECAR_SEGMENTS = {
    "full_scan_scan_indices",
    "full_scan_lengths",
    "full_scan_first_values",
    "full_scan_delta_values",
    "full_scan_delta_same_prev_flags",
    "full_scan_delta2_first_deltas",
    "full_scan_delta2_second_diffs",
}

MS2_MZ_SEGMENTS = {
    "dict_mz_q",
    "top_mz_residuals",
    "tail_mz_residuals",
}
MS2_INT_SEGMENTS = {
    "top_first_codes",
    "top_delta_main",
    "top_delta_overflow_idx",
    "top_delta_overflow_vals",
    "tail_codes",
    "int_first_codes",
    "int_delta_main",
    "int_delta_overflow_idx",
    "int_delta_overflow_vals",
    "mid_int_first_codes",
    "mid_int_delta_main",
    "mid_int_delta_overflow_idx",
    "mid_int_delta_overflow_vals",
}

STRICT_MS1_MODE = dict(OUR_MODE)
STRICT_MS1_MODE["intensity_mode"] = "strict_lossless_xdelta"
EQ_ARCHIVE_MS1_MODE = {
    "mz_precision": int(OUR_MODE["mz_precision"]),
    "intensity_mode": str(OUR_MODE["intensity_mode"]),
    "backend": str(OUR_MODE["backend"]),
    "kwargs": {key: value for key, value in OUR_MODE.items() if key not in {"mz_precision", "intensity_mode", "backend"}},
}

SECTION_PROGRESS_LOG = LOG_DIR / "section_benchmark.progress.jsonl"
SECTION_SUMMARY_JSON = LOG_DIR / "section_benchmark_summary.json"
SECTION_SUMMARY_MD = LOG_DIR / "section_benchmark_summary.md"
SECTION_SUMMARY_CSV = LOG_DIR / "section_benchmark_summary_by_file.csv"
FILE_LOG_ROOT = LOG_DIR / "by_file"


class MemoryMonitor:
    def __init__(self, interval_s: float = 0.2):
        self.interval_s = float(interval_s)
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._root = psutil.Process(os.getpid()) if psutil is not None else None
        self.system_total_bytes = self._read_total_memory_bytes()

    def _read_total_memory_bytes(self) -> int:
        if psutil is not None:
            return int(psutil.virtual_memory().total)
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    def _read_self_rss_bytes(self) -> int:
        if psutil is not None:
            rss = 0
            procs = [self._root]
            try:
                procs.extend(self._root.children(recursive=True))
            except psutil.Error:
                pass
            seen = set()
            for proc in procs:
                if proc is None or proc.pid in seen:
                    continue
                seen.add(proc.pid)
                try:
                    rss += int(proc.memory_info().rss)
                except psutil.Error:
                    continue
            return rss
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    def _sample_once(self) -> None:
        self.peak_rss_bytes = max(self.peak_rss_bytes, self._read_self_rss_bytes())

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval_s)
        self._sample_once()

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_s * 4))

    @property
    def peak_memory_pct(self) -> float:
        if self.system_total_bytes <= 0:
            return 0.0
        return 100.0 * float(self.peak_rss_bytes) / float(self.system_total_bytes)


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _file_log_dir(file_name: str) -> Path:
    return FILE_LOG_ROOT / _artifact_file_dirname(file_name)


def _file_stdout_log_path(file_name: str) -> Path:
    return _file_log_dir(file_name) / "stdout.log"


def _file_progress_log_path(file_name: str) -> Path:
    return _file_log_dir(file_name) / "progress.jsonl"


def _file_summary_json_path(file_name: str) -> Path:
    return _file_log_dir(file_name) / "summary.json"


def _file_summary_md_path(file_name: str) -> Path:
    return _file_log_dir(file_name) / "summary.md"


def _emit_log(file_name: str | None, message: str) -> None:
    text = str(message)
    print(text, flush=True)
    if not file_name:
        return
    log_path = _file_stdout_log_path(file_name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_now_text()}] {text}\n")


def _append_progress_row(file_name: str, payload: dict) -> None:
    _append_jsonl(SECTION_PROGRESS_LOG, payload)
    _append_jsonl(_file_progress_log_path(file_name), payload)


def _safe_pct(value) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _can_use_fork_process_parallelism() -> bool:
    if os.name != "posix":
        return False
    try:
        mp.get_context("fork")
    except ValueError:
        return False
    return True


def _make_ms1_codec_progress_callback(file_name: str, mode_name: str):
    last_signature = {"value": ()}

    def _callback(payload: dict) -> None:
        stage = str(payload.get("stage", "progress"))
        pct = _safe_pct(payload.get("pct"))
        done_tracks = int(payload.get("done_tracks") or 0)
        total_tracks = payload.get("total_tracks")
        total_tracks = None if total_tracks is None else int(total_tracks)
        done_islands = int(payload.get("done_islands") or 0)
        total_islands = payload.get("total_islands")
        total_islands = None if total_islands is None else int(total_islands)
        signature = (
            stage,
            round(pct, 1),
            done_tracks,
            total_tracks,
            done_islands,
            total_islands,
        )
        if signature == last_signature["value"]:
            return
        last_signature["value"] = signature
        row = {
            "file": file_name,
            "section": "ms1",
            "stage": f"{mode_name}_progress",
            "progress_stage": stage,
            "pct": float(pct),
            "done_tracks": int(done_tracks),
            "total_tracks": total_tracks,
            "done_islands": int(done_islands),
            "total_islands": total_islands,
        }
        _append_progress_row(file_name, row)
        msg = f"[FULL8][MS1][PROGRESS] {mode_name} {file_name} stage={stage} pct={pct:.1f}%"
        if total_tracks is not None:
            msg += f" tracks={done_tracks}/{total_tracks}"
        else:
            msg += f" tracks_done={done_tracks}"
        if total_islands is not None:
            msg += f" islands={done_islands}/{total_islands}"
        elif done_islands:
            msg += f" islands_done={done_islands}"
        _emit_log(file_name, msg)

    return _callback


def _make_ms2_codec_progress_callback(file_name: str, mode_name: str, file_type: str):
    last_signature = {"value": ()}

    def _callback(payload: dict) -> None:
        stage = str(payload.get("stage", "progress"))
        pct = _safe_pct(payload.get("pct"))
        done_units = int(payload.get("done_units") or 0)
        total_units = int(payload.get("total_units") or 0)
        unit_kind = str(payload.get("unit_kind", "units"))
        done_tracks = payload.get("done_tracks")
        done_tracks = None if done_tracks is None else int(done_tracks)
        total_tracks = payload.get("total_tracks")
        total_tracks = None if total_tracks is None else int(total_tracks)
        signature = (
            stage,
            round(pct, 1),
            done_units,
            total_units,
            done_tracks,
            total_tracks,
            unit_kind,
        )
        if signature == last_signature["value"]:
            return
        last_signature["value"] = signature
        row = {
            "file": file_name,
            "section": "ms2",
            "stage": f"{mode_name}_progress",
            "progress_stage": stage,
            "file_type": file_type,
            "pct": float(pct),
            "done_units": int(done_units),
            "total_units": int(total_units),
            "unit_kind": unit_kind,
            "done_tracks": done_tracks,
            "total_tracks": total_tracks,
            "track_kind": str(payload.get("track_kind", "dictionary_entries")),
        }
        _append_progress_row(file_name, row)
        msg = (
            f"[FULL8][MS2][PROGRESS] {mode_name} {file_name} type={file_type} "
            f"stage={stage} pct={pct:.1f}% {unit_kind}={done_units}/{total_units}"
        )
        if total_tracks is not None:
            msg += f" track_units={int(done_tracks or 0)}/{total_tracks}"
        elif done_tracks is not None:
            msg += f" track_units_done={done_tracks}"
        _emit_log(file_name, msg)

    return _callback


def _write_file_bundle_summary(bundle: dict) -> None:
    file_name = bundle["file"]
    stage_rows = bundle.get("stage_rows", [])
    stage_totals = defaultdict(float)
    section_totals = defaultdict(float)
    for row in stage_rows:
        elapsed_s = float(row.get("elapsed_s", 0.0) or 0.0)
        stage_totals[f"{row.get('section','unknown')}/{row.get('stage','unknown')}"] += elapsed_s
        section_totals[row.get("section", "unknown")] += elapsed_s
    summary = {
        "file": file_name,
        "sections_present": {
            "ms1": bundle.get("ms1") is not None,
            "ms2": bundle.get("ms2") is not None,
            "metadata": bundle.get("metadata") is not None,
        },
        "bundle_parallel_enabled": bool(bundle.get("bundle_parallel_enabled", False)),
        "bundle_parallel_decision": bundle.get("bundle_parallel_decision"),
        "wall_clock_elapsed_s": float(bundle.get("wall_clock_elapsed_s", sum(section_totals.values()))),
        "sum_stage_elapsed_s": float(sum(section_totals.values())),
        "section_elapsed_s": {key: float(val) for key, val in sorted(section_totals.items())},
        "stage_elapsed_s": {key: float(val) for key, val in sorted(stage_totals.items())},
        "ms1_ours_eqfidelity_cr": (
            float(bundle["ms1"]["methods"]["ours_eqfidelity"]["compression_ratio"])
            if bundle.get("ms1") is not None
            else None
        ),
        "ms1_ours_strict_q6_cr": (
            float(bundle["ms1"]["methods"]["ours_strict_q6"]["compression_ratio"])
            if bundle.get("ms1") is not None
            else None
        ),
        "ms2_ours_eqfidelity_cr": (
            float(bundle["ms2"]["methods"]["ours_eqfidelity"]["compression_ratio"])
            if bundle.get("ms2") is not None
            else None
        ),
        "metadata_ours_cr": (
            float(bundle["metadata"]["methods"]["ours_metadata"]["compression_ratio"])
            if bundle.get("metadata") is not None
            else None
        ),
        "stage_rows": stage_rows,
    }
    _write_json(_file_summary_json_path(file_name), summary)
    md_lines = [
        f"# File Benchmark Summary: {file_name}",
        "",
        f"- Generated: `{_now_text()}`",
        "",
        "## Compression Ratios",
        "",
        "| Item | Value |",
        "|---|---:|",
        f"| MS1 ours eq-fidelity CR | {summary['ms1_ours_eqfidelity_cr'] if summary['ms1_ours_eqfidelity_cr'] is not None else 'NA'} |",
        f"| MS1 ours strict-q6 CR | {summary['ms1_ours_strict_q6_cr'] if summary['ms1_ours_strict_q6_cr'] is not None else 'NA'} |",
        f"| MS2 ours eq-fidelity CR | {summary['ms2_ours_eqfidelity_cr'] if summary['ms2_ours_eqfidelity_cr'] is not None else 'NA'} |",
        f"| Metadata ours CR | {summary['metadata_ours_cr'] if summary['metadata_ours_cr'] is not None else 'NA'} |",
        "",
        "## Section Elapsed",
        "",
        f"- Bundle parallel enabled: `{summary['bundle_parallel_enabled']}`",
        f"- Wall-clock elapsed (s): `{summary['wall_clock_elapsed_s']:.3f}`",
        f"- Sum stage elapsed (s): `{summary['sum_stage_elapsed_s']:.3f}`",
        "",
        "| Section | Elapsed (s) |",
        "|---|---:|",
    ]
    for key, value in sorted(section_totals.items()):
        md_lines.append(f"| `{key}` | {value:.3f} |")
    md_lines.extend(["", "## Stage Elapsed", "", "| Stage | Elapsed (s) |", "|---|---:|"])
    for key, value in sorted(stage_totals.items()):
        md_lines.append(f"| `{key}` | {value:.3f} |")
    _file_summary_md_path(file_name).write_text("\n".join(md_lines), encoding="utf-8")


def _write_aggregate_stage_summary(bundle_rows: list[dict], stage_metric_rows: list[dict]) -> None:
    file_rows = []
    section_keys = ("shared_parse", "ms1", "ms2", "metadata")
    for bundle in sorted(bundle_rows, key=lambda item: item["file"]):
        file_name = bundle["file"]
        stage_rows = bundle.get("stage_rows", [])
        row = {
            "file": file_name,
            "n_stage_rows": len(stage_rows),
            "total_elapsed_s": float(bundle.get("wall_clock_elapsed_s", 0.0)),
            "sum_stage_elapsed_s": 0.0,
            "bundle_parallel_enabled": bool(bundle.get("bundle_parallel_enabled", False)),
        }
        for key in section_keys:
            row[f"{key}_elapsed_s"] = 0.0
        row["ms1_ours_eqfidelity_cr"] = None
        row["ms1_ours_strict_q6_cr"] = None
        row["ms2_ours_eqfidelity_cr"] = None
        row["metadata_ours_cr"] = None
        for stage_row in stage_rows:
            elapsed_s = float(stage_row.get("elapsed_s", 0.0) or 0.0)
            row["sum_stage_elapsed_s"] += elapsed_s
            section = stage_row.get("section", "")
            if section in section_keys:
                row[f"{section}_elapsed_s"] += elapsed_s
        if row["total_elapsed_s"] <= 0.0:
            row["total_elapsed_s"] = float(row["sum_stage_elapsed_s"])
        if bundle.get("ms1") is not None:
            row["ms1_ours_eqfidelity_cr"] = float(bundle["ms1"]["methods"]["ours_eqfidelity"]["compression_ratio"])
            row["ms1_ours_strict_q6_cr"] = float(bundle["ms1"]["methods"]["ours_strict_q6"]["compression_ratio"])
        if bundle.get("ms2") is not None:
            row["ms2_ours_eqfidelity_cr"] = float(bundle["ms2"]["methods"]["ours_eqfidelity"]["compression_ratio"])
        if bundle.get("metadata") is not None:
            row["metadata_ours_cr"] = float(bundle["metadata"]["methods"]["ours_metadata"]["compression_ratio"])
        file_rows.append(row)
    _write_csv(file_rows, SECTION_SUMMARY_CSV)

    stage_group = defaultdict(list)
    for row in stage_metric_rows:
        key = f"{row.get('section','unknown')}/{row.get('stage','unknown')}"
        stage_group[key].append(float(row.get("elapsed_s", 0.0) or 0.0))
    summary = {
        "generated": _now_text(),
        "n_files": len(file_rows),
        "files": file_rows,
        "stage_mean_elapsed_s": {
            key: (statistics.mean(vals) if vals else 0.0)
            for key, vals in sorted(stage_group.items())
        },
    }
    _write_json(SECTION_SUMMARY_JSON, summary)

    md_lines = [
        "# Section Benchmark Summary",
        "",
        f"- Generated: `{summary['generated']}`",
        f"- Files: `{summary['n_files']}`",
        "",
        "## Per-file Summary",
        "",
        "| File | Wall-clock elapsed (s) | Sum stage elapsed (s) | Parallel | Shared parse (s) | MS1 (s) | MS2 (s) | Metadata (s) | MS1 eq CR | MS1 strict CR | MS2 eq CR | Metadata CR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in file_rows:
        md_lines.append(
            f"| `{row['file']}` | {row['total_elapsed_s']:.3f} | {row['sum_stage_elapsed_s']:.3f} | "
            f"{'Y' if row['bundle_parallel_enabled'] else 'N'} | {row['shared_parse_elapsed_s']:.3f} | "
            f"{row['ms1_elapsed_s']:.3f} | {row['ms2_elapsed_s']:.3f} | {row['metadata_elapsed_s']:.3f} | "
            f"{row['ms1_ours_eqfidelity_cr'] if row['ms1_ours_eqfidelity_cr'] is not None else 'NA'} | "
            f"{row['ms1_ours_strict_q6_cr'] if row['ms1_ours_strict_q6_cr'] is not None else 'NA'} | "
            f"{row['ms2_ours_eqfidelity_cr'] if row['ms2_ours_eqfidelity_cr'] is not None else 'NA'} | "
            f"{row['metadata_ours_cr'] if row['metadata_ours_cr'] is not None else 'NA'} |"
        )
    md_lines.extend(["", "## Mean Stage Elapsed", "", "| Stage | Mean elapsed (s) |", "|---|---:|"])
    for key, value in summary["stage_mean_elapsed_s"].items():
        md_lines.append(f"| `{key}` | {value:.3f} |")
    SECTION_SUMMARY_MD.write_text("\n".join(md_lines), encoding="utf-8")


def _current_rss_bytes() -> int:
    if psutil is not None:
        try:
            return int(psutil.Process(os.getpid()).memory_info().rss)
        except psutil.Error:
            return 0
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


def _read_system_memory_state() -> dict:
    total_bytes = 0
    available_bytes = 0
    swap_used_pct = 0.0
    if psutil is not None:
        try:
            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            return {
                "total_bytes": int(vm.total),
                "available_bytes": int(vm.available),
                "swap_used_pct": float(sm.percent),
            }
        except psutil.Error:
            pass
    try:
        meminfo = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            meminfo[key.strip()] = value.strip()
        total_bytes = int(meminfo.get("MemTotal", "0 kB").split()[0]) * 1024
        available_bytes = int(meminfo.get("MemAvailable", "0 kB").split()[0]) * 1024
        swap_total = int(meminfo.get("SwapTotal", "0 kB").split()[0]) * 1024
        swap_free = int(meminfo.get("SwapFree", "0 kB").split()[0]) * 1024
        if swap_total > 0:
            swap_used_pct = 100.0 * float(max(0, swap_total - swap_free)) / float(swap_total)
    except OSError:
        pass
    return {
        "total_bytes": int(total_bytes),
        "available_bytes": int(available_bytes),
        "swap_used_pct": float(swap_used_pct),
    }


def _resolve_effective_jobs(requested_jobs: int, n_files: int, min_free_mem_per_job_gb: float, max_swap_pct: float) -> tuple[int, dict]:
    jobs = max(1, min(int(requested_jobs), int(max(1, n_files))))
    mem_state = _read_system_memory_state()
    available_bytes = int(mem_state.get("available_bytes", 0))
    mem_cap = jobs
    required_free_bytes = 0
    if float(min_free_mem_per_job_gb) > 0:
        required_free_bytes = int(float(min_free_mem_per_job_gb) * (1024.0 ** 3))
    if min_free_mem_per_job_gb > 0 and available_bytes > 0:
        mem_cap = max(1, int(available_bytes // required_free_bytes))
        jobs = max(1, min(jobs, mem_cap))
    swap_used_pct = float(mem_state.get("swap_used_pct", 0.0))
    blocked_by_swap_guard = max_swap_pct >= 0 and swap_used_pct >= float(max_swap_pct)
    blocked_by_memory_guard = required_free_bytes > 0 and available_bytes > 0 and available_bytes < required_free_bytes
    guard_blocked = bool(blocked_by_swap_guard or blocked_by_memory_guard)
    if guard_blocked:
        jobs = 1
    guard_reasons = []
    if blocked_by_swap_guard:
        guard_reasons.append(
            f"swap_used_pct={swap_used_pct:.2f} >= max_swap_pct={float(max_swap_pct):.2f}"
        )
    if blocked_by_memory_guard:
        guard_reasons.append(
            f"available_gb={float(available_bytes) / (1024.0 ** 3):.2f} < min_free_mem_per_job_gb={float(min_free_mem_per_job_gb):.2f}"
        )
    info = {
        "requested_jobs": int(requested_jobs),
        "effective_jobs": int(jobs),
        "n_files": int(n_files),
        "min_free_mem_per_job_gb": float(min_free_mem_per_job_gb),
        "max_swap_pct": float(max_swap_pct),
        "available_gb": float(available_bytes) / (1024.0 ** 3) if available_bytes else 0.0,
        "total_gb": float(mem_state.get("total_bytes", 0)) / (1024.0 ** 3) if mem_state.get("total_bytes", 0) else 0.0,
        "swap_used_pct": swap_used_pct,
        "mem_cap_jobs": int(mem_cap),
        "guard_blocked": bool(guard_blocked),
        "blocked_by_swap_guard": bool(blocked_by_swap_guard),
        "blocked_by_memory_guard": bool(blocked_by_memory_guard),
        "guard_reason": "; ".join(guard_reasons),
    }
    return jobs, info


def _record_stage_metric(stage_rows: list[dict], *, file_name: str, section: str, stage: str, elapsed_s: float, peak_rss_bytes: int, peak_memory_pct: float, **extra) -> dict:
    row = {
        "file": file_name,
        "section": section,
        "stage": stage,
        "elapsed_s": float(elapsed_s),
        "peak_rss_bytes": int(peak_rss_bytes),
        "peak_rss_gb": float(peak_rss_bytes) / (1024.0 ** 3),
        "peak_memory_pct": float(peak_memory_pct),
    }
    row.update(extra)
    stage_rows.append(row)
    _append_progress_row(file_name, row)
    return row


def _run_stage(stage_rows: list[dict], *, file_name: str, section: str, stage: str, fn, log_prefix: str | None = None, extra_log: str = "", **extra):
    t0 = time.perf_counter()
    with MemoryMonitor() as monitor:
        result = fn()
    elapsed_s = time.perf_counter() - t0
    row = _record_stage_metric(
        stage_rows,
        file_name=file_name,
        section=section,
        stage=stage,
        elapsed_s=elapsed_s,
        peak_rss_bytes=monitor.peak_rss_bytes,
        peak_memory_pct=monitor.peak_memory_pct,
        **extra,
    )
    if log_prefix:
        suffix = f" {extra_log}" if extra_log else ""
        _emit_log(
            file_name,
            f"{log_prefix} elapsed_s={elapsed_s:.3f} peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f}{suffix}",
        )
    return result, row


def _write_csv(rows, path: Path):
    if not rows:
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _canonicalize_method_stats(stats: dict | None, *, raw_bytes: int, file_type: str | None = None) -> dict | None:
    if stats is None:
        return None
    out = dict(stats)
    out["raw_bytes"] = int(raw_bytes)
    out.setdefault("compressed_bytes", 0)
    comp = int(out["compressed_bytes"]) if out.get("compressed_bytes") else 0
    out["compression_ratio"] = float(raw_bytes) / float(comp) if comp else 0.0
    out.setdefault("encode_time_s", 0.0)
    out.setdefault("decode_time_s", 0.0)
    out.setdefault("max_abs_mz_error", None)
    out.setdefault("max_abs_intensity_error", None)
    out.setdefault("median_mz_ppm_p95", None)
    out.setdefault("median_int_rel_p95", None)
    if file_type is not None:
        out["file_type"] = file_type
    if out.get("segment_sizes") is None:
        out["segment_sizes"] = {}
    return out


def _raw_bytes_from_scans(scans) -> int:
    total = 0
    for scan in scans:
        total += int(len(scan["mz_array"])) * 8
        total += int(len(scan["intensity_array"])) * 8
    return total


def _norm_file_label(path_or_name: str) -> str:
    return Path(path_or_name).name.replace(".mzML", "").replace(".mzml", "")


def _artifact_file_dirname(path_or_name: str) -> str:
    stem = Path(path_or_name).name.replace(".mzML", "").replace(".mzml", "")
    return stem.replace(" ", "_")


def _artifact_method_dir(section: str, file_name: str, method: str) -> Path:
    return ARTIFACTS_DIR / section / _artifact_file_dirname(file_name) / method


def _artifact_manifest_path(section: str, file_name: str, method: str) -> Path:
    return _artifact_method_dir(section, file_name, method) / "manifest.json"


def _artifact_meta_path(section: str, file_name: str, method: str) -> Path:
    return _artifact_method_dir(section, file_name, method) / "meta.json"


def _artifact_stats_path(section: str, file_name: str, method: str) -> Path:
    return _artifact_method_dir(section, file_name, method) / "stats.json"


def _artifact_manifest(section: str, file_name: str, method: str) -> dict | None:
    return _read_json(_artifact_manifest_path(section, file_name, method))


def _artifact_stats(section: str, file_name: str, method: str) -> dict | None:
    manifest = _artifact_manifest(section, file_name, method)
    if manifest is None:
        return None
    meta = _read_json(_artifact_meta_path(section, file_name, method)) or {}
    stats = _read_json(_artifact_stats_path(section, file_name, method)) or {}
    merged = {}
    merged.update(meta)
    merged.update(stats)
    payload_size = manifest.get("payload_size_bytes")
    if payload_size is not None and "compressed_bytes" not in merged:
        merged["compressed_bytes"] = int(payload_size)
    if "raw_bytes" in merged and "compressed_bytes" in merged and "compression_ratio" not in merged:
        comp = int(merged["compressed_bytes"])
        merged["compression_ratio"] = float(merged["raw_bytes"]) / float(comp) if comp else 0.0
    if "segment_sizes" in merged and merged["segment_sizes"] is None:
        merged["segment_sizes"] = {}
    return merged


def _artifact_payload(section: str, file_name: str, method: str):
    manifest = _artifact_manifest(section, file_name, method)
    if manifest is None:
        return None
    payload_path = Path(manifest["payload_path"])
    if not payload_path.exists():
        return None
    if manifest.get("payload_format") == "bytes":
        return payload_path.read_bytes()
    with payload_path.open("rb") as handle:
        return pickle.load(handle)


def _cache_dir(file_name: str) -> Path:
    return ARTIFACTS_DIR / "_cache" / _artifact_file_dirname(file_name)


def _cache_pickle_path(file_name: str, cache_key: str) -> Path:
    return _cache_dir(file_name) / f"{cache_key}.pkl"


def _cache_meta_path(file_name: str, cache_key: str) -> Path:
    return _cache_dir(file_name) / f"{cache_key}.meta.json"


def _cache_scan_store_root(file_name: str) -> Path:
    return _cache_dir(file_name) / "scan_store"


def _cache_scan_store_meta_path(file_name: str) -> Path:
    return _cache_dir(file_name) / "scan_store.meta.json"


def _cache_scan_store_manifest_path(file_name: str) -> Path:
    return _cache_scan_store_root(file_name) / "manifest.json"


def _save_scan_store_meta(file_name: str, meta: dict) -> None:
    cache_root = _cache_dir(file_name)
    cache_root.mkdir(parents=True, exist_ok=True)
    _write_json(_cache_scan_store_meta_path(file_name), meta)


def _load_scan_store_meta(file_name: str) -> dict:
    return _read_json(_cache_scan_store_meta_path(file_name)) or {}


def _scan_store_exists(file_name: str) -> bool:
    return _cache_scan_store_manifest_path(file_name).exists()


def _save_cache_pickle(file_name: str, cache_key: str, payload_obj, *, meta: dict | None = None) -> dict:
    cache_root = _cache_dir(file_name)
    cache_root.mkdir(parents=True, exist_ok=True)
    payload_path = _cache_pickle_path(file_name, cache_key)
    with payload_path.open("wb") as handle:
        pickle.dump(payload_obj, handle, protocol=pickle.HIGHEST_PROTOCOL)
    manifest = {
        "file": file_name,
        "cache_key": cache_key,
        "payload_path": str(payload_path),
        "payload_size_bytes": int(payload_path.stat().st_size),
    }
    if meta is not None:
        meta_path = _cache_meta_path(file_name, cache_key)
        _write_json(meta_path, meta)
        manifest["meta_path"] = str(meta_path)
    _write_json(cache_root / f"{cache_key}.manifest.json", manifest)
    return manifest


def _load_cache_pickle(file_name: str, cache_key: str):
    payload_path = _cache_pickle_path(file_name, cache_key)
    if not payload_path.exists():
        return None
    with payload_path.open("rb") as handle:
        return pickle.load(handle)


def _save_split_section_caches(file_name: str, loaded: dict) -> None:
    ms1_scans = loaded.get("ms1_scans")
    if ms1_scans is not None and _cache_pickle_path(file_name, "ms1_scans").exists() is False:
        _save_cache_pickle(
            file_name,
            "ms1_scans",
            ms1_scans,
            meta={
                "file_type": loaded.get("file_type"),
                "n_scans": len(ms1_scans),
                "raw_ms1_bytes": int(loaded.get("raw_ms1_bytes", 0)),
            },
        )
    ms2_by_window = loaded.get("ms2_by_window")
    if ms2_by_window is not None and _cache_pickle_path(file_name, "ms2_by_window").exists() is False:
        _save_cache_pickle(
            file_name,
            "ms2_by_window",
            ms2_by_window,
            meta={
                "file_type": loaded.get("file_type"),
                "n_scans": int(sum(len(v) for v in ms2_by_window.values())),
                "window_count": int(len(ms2_by_window)),
                "raw_ms2_bytes": int(loaded.get("raw_ms2_bytes", 0)),
            },
        )
    ms2_scans = loaded.get("ms2_scans")
    if ms2_scans is not None and _cache_pickle_path(file_name, "ms2_scans").exists() is False:
        _save_cache_pickle(
            file_name,
            "ms2_scans",
            ms2_scans,
            meta={
                "file_type": loaded.get("file_type"),
                "n_scans": len(ms2_scans),
                "raw_ms2_bytes": int(loaded.get("raw_ms2_bytes", 0)),
            },
        )


def _load_split_section_cache(file_path: Path, *, need_ms1: bool, need_ms2: bool):
    file_name = file_path.name
    is_dia = file_path in DIA_FILES
    if need_ms1 and not need_ms2:
        ms1_scans = _load_cache_pickle(file_name, "ms1_scans")
        if ms1_scans is None:
            return None
        meta = _read_json(_cache_meta_path(file_name, "ms1_scans")) or {}
        return {
            "file_type": str(meta.get("file_type", "DIA" if is_dia else "DDA")),
            "ms1_scans": ms1_scans,
            "raw_ms1_bytes": int(meta.get("raw_ms1_bytes", 0)),
            "ms2_by_window": None,
            "ms2_scans": None,
            "raw_ms2_bytes": 0,
            "total_specs": int(meta.get("n_scans", len(ms1_scans))),
            "_cache_key": "ms1_scans",
        }
    if need_ms2 and not need_ms1:
        cache_key = "ms2_by_window" if is_dia else "ms2_scans"
        payload = _load_cache_pickle(file_name, cache_key)
        if payload is None:
            return None
        meta = _read_json(_cache_meta_path(file_name, cache_key)) or {}
        out = {
            "file_type": str(meta.get("file_type", "DIA" if is_dia else "DDA")),
            "ms1_scans": None,
            "raw_ms1_bytes": 0,
            "ms2_by_window": payload if cache_key == "ms2_by_window" else None,
            "ms2_scans": payload if cache_key == "ms2_scans" else None,
            "raw_ms2_bytes": int(meta.get("raw_ms2_bytes", 0)),
            "total_specs": int(meta.get("n_scans", 0)),
            "_cache_key": cache_key,
        }
        return out
    return None


def _materialize_loaded_from_scan_store(scan_store, *, need_ms1: bool, need_ms2: bool) -> dict:
    file_type = str(getattr(scan_store, "file_type", "DDA"))
    is_dia = file_type.upper() == "DIA"
    return {
        "file_type": file_type,
        "ms1_scans": scan_store.ms1_scans() if need_ms1 else None,
        "raw_ms1_bytes": int(getattr(scan_store, "raw_ms1_bytes", 0)) if need_ms1 else 0,
        "ms2_window_items": scan_store.dia_ms2_window_items() if (need_ms2 and is_dia) else None,
        "ms2_by_window": None,
        "ms2_scans": scan_store.dda_ms2_scans() if (need_ms2 and not is_dia) else None,
        "raw_ms2_bytes": int(getattr(scan_store, "raw_ms2_bytes", 0)) if need_ms2 else 0,
        "total_specs": int(getattr(scan_store, "ms1_count", 0)) + int(getattr(scan_store, "ms2_count", 0)),
        "_cache_key": "scan_store",
    }


def _save_bytes_artifact(
    section: str,
    file_name: str,
    method: str,
    payload: bytes,
    *,
    meta: dict | None = None,
    manifest_extra: dict | None = None,
    stats: dict | None = None,
) -> dict:
    method_dir = _artifact_method_dir(section, file_name, method)
    method_dir.mkdir(parents=True, exist_ok=True)
    payload_path = method_dir / "payload.bin"
    payload_path.write_bytes(payload)
    if meta is not None:
        _write_json(method_dir / "meta.json", meta)
    if stats is not None:
        _write_json(method_dir / "stats.json", stats)
    manifest = {
        "file": file_name,
        "section": section,
        "method": method,
        "payload_format": "bytes",
        "payload_path": str(payload_path),
        "payload_size_bytes": int(stats.get("compressed_bytes", len(payload))) if isinstance(stats, dict) else int(len(payload)),
    }
    if meta is not None:
        manifest["meta_path"] = str(method_dir / "meta.json")
    if stats is not None:
        manifest["stats_path"] = str(method_dir / "stats.json")
    if manifest_extra:
        manifest.update(manifest_extra)
    _write_json(method_dir / "manifest.json", manifest)
    return manifest


def _save_pickle_artifact(
    section: str,
    file_name: str,
    method: str,
    payload_obj,
    *,
    meta: dict | None = None,
    manifest_extra: dict | None = None,
    stats: dict | None = None,
) -> dict:
    method_dir = _artifact_method_dir(section, file_name, method)
    method_dir.mkdir(parents=True, exist_ok=True)
    payload_path = method_dir / "payload.pkl"
    with payload_path.open("wb") as handle:
        pickle.dump(payload_obj, handle, protocol=pickle.HIGHEST_PROTOCOL)
    if meta is not None:
        _write_json(method_dir / "meta.json", meta)
    if stats is not None:
        _write_json(method_dir / "stats.json", stats)
    manifest = {
        "file": file_name,
        "section": section,
        "method": method,
        "payload_format": "pickle",
        "payload_path": str(payload_path),
        "payload_size_bytes": int(payload_path.stat().st_size),
    }
    if meta is not None:
        manifest["meta_path"] = str(method_dir / "meta.json")
    if stats is not None:
        manifest["stats_path"] = str(method_dir / "stats.json")
    if manifest_extra:
        manifest.update(manifest_extra)
    _write_json(method_dir / "manifest.json", manifest)
    return manifest


def _make_bar_rows(order, aggregate_map, family_map=None):
    rows = []
    for label in order:
        item = aggregate_map[label]
        row = {
            "label": label,
            "display_name": DISPLAY_NAME[label],
            "mean_cr": item["mean_cr"],
            "median_cr": item["median_cr"],
            "mean_encode_time_s": item.get("mean_encode_time_s", 0.0),
            "mean_decode_time_s": item.get("mean_decode_time_s", 0.0),
            "color_hex": COLOR_MAP[label],
            "family": family_map.get(label, "near") if family_map else "near",
            "bar_annotation": item.get("bar_annotation", f"{item['mean_cr']:.2f}x"),
        }
        rows.append(row)
    return rows


def _make_line_rows(order, per_file_map):
    rows = []
    for file_name, method_map in sorted(per_file_map.items()):
        for label in order:
            if label not in method_map:
                continue
            rows.append(
                {
                    "file": file_name,
                    "file_label": _norm_file_label(file_name),
                    "label": label,
                    "display_name": DISPLAY_NAME[label],
                    "compression_ratio": method_map[label]["compression_ratio"],
                }
            )
    return rows


def _make_compare_table_rows(section_name: str, order: list[str], aggregate_map: dict, reference_label: str | None):
    ref_cr = float(aggregate_map[reference_label]["mean_cr"]) if reference_label and reference_label in aggregate_map else 0.0
    rows = []
    for label in order:
        item = aggregate_map[label]
        mean_cr = float(item["mean_cr"])
        rows.append(
            {
                "section": section_name,
                "label": label,
                "display_name": DISPLAY_NAME[label],
                "mean_cr": mean_cr,
                "median_cr": float(item["median_cr"]),
                "delta_cr_vs_reference": mean_cr - ref_cr if ref_cr else 0.0,
                "delta_cr_vs_reference_pct": 100.0 * (mean_cr / ref_cr - 1.0) if ref_cr else 0.0,
                "reference_label": reference_label or "",
                "mean_encode_time_s": float(item.get("mean_encode_time_s", 0.0)),
                "mean_decode_time_s": float(item.get("mean_decode_time_s", 0.0)),
            }
        )
    return rows


def _full_scan_sidecar_bytes(method_stats: dict) -> int:
    segment_sizes = method_stats.get("segment_sizes", {}) or {}
    return sum(int(segment_sizes.get(key, 0) or 0) for key in FULL_SCAN_SIDECAR_SEGMENTS)


def _cr_without_full_scan_sidecar(method_stats: dict) -> float:
    raw_bytes = int(method_stats.get("raw_bytes", 0) or 0)
    compressed_bytes = int(method_stats.get("compressed_bytes", 0) or 0)
    sidecar_bytes = _full_scan_sidecar_bytes(method_stats)
    reduced_bytes = max(1, compressed_bytes - sidecar_bytes)
    return float(raw_bytes) / float(reduced_bytes)


def _augment_ms1_bar_rows_with_sidecar(bar_rows, ms1_per_file):
    per_method_values = defaultdict(list)
    for methods in ms1_per_file.values():
        for label in ("ours_eqfidelity", "ours_strict_q6", "ours_archive_fidelity_auto"):
            if label in methods:
                per_method_values[label].append(_cr_without_full_scan_sidecar(methods[label]))
    mean_without = {
        label: (sum(vals) / len(vals) if vals else 0.0)
        for label, vals in per_method_values.items()
    }
    for row in bar_rows:
        label = row.get("label")
        if label in mean_without:
            base = float(row["mean_cr"])
            without = float(mean_without[label])
            row["mean_cr_without_full_scan_sidecar"] = without
            row["sidecar_removed_gain_cr"] = max(0.0, without - base)
            row["bar_annotation"] = f"{base:.2f}x\nw/o sidecar {without:.2f}x"
        else:
            row["mean_cr_without_full_scan_sidecar"] = ""
            row["sidecar_removed_gain_cr"] = 0.0
    return bar_rows


def _plot_ms1_compression_bar_with_full_scan_gain(bar_rows, output_png: Path, title: str):
    labels = [row["display_name"] for row in bar_rows]
    values = [float(row["mean_cr"]) for row in bar_rows]
    colors = [row.get("color_hex") or COLOR_MAP.get(row["label"], "#999999") for row in bar_rows]
    sidecar_free = [
        float(row["mean_cr_without_full_scan_sidecar"]) if row.get("mean_cr_without_full_scan_sidecar") not in ("", None) else None
        for row in bar_rows
    ]
    annotations = [row.get("bar_annotation", f"{val:.2f}x") for row, val in zip(bar_rows, values)]

    fig, ax = plt.subplots(figsize=(11.5, 8.8))
    x = np.arange(len(labels))
    bars = ax.bar(x, values, color=colors, width=0.78)
    for idx, (base, without) in enumerate(zip(values, sidecar_free)):
        if without is None or without <= base:
            continue
        ax.bar(
            x[idx],
            without - base,
            bottom=base,
            width=0.78,
            color="#FF9DA6",
            edgecolor="#B33A3A",
            linewidth=1.2,
        )
    ax.set_title(title, fontsize=20)
    ax.set_ylabel("Compression Ratio", fontsize=16)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=68, ha="right", fontsize=13)
    ax.tick_params(axis="y", labelsize=13)
    ax.grid(axis="y", alpha=0.3)
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=COLOR_MAP["ours_strict_q6"], label="Exact intensity + q6 m/z"),
        plt.Rectangle((0, 0), 1, 1, color=COLOR_MAP["ours_eqfidelity"], label="Near-lossless"),
        plt.Rectangle((0, 0), 1, 1, color="#FF9DA6", ec="#B33A3A", label="CR gain if full-scan sidecar removed"),
    ]
    ax.legend(handles=handles, fontsize=11, loc="upper right")
    max_candidates = [v for v in values]
    max_candidates.extend(v for v in sidecar_free if v is not None)
    max_val = max(max_candidates) if max_candidates else 1.0
    ax.set_ylim(0, max_val * 1.22 if max_val > 0 else 1.0)
    for bar, text in zip(bars, annotations):
        ax.text(
            bar.get_x() + bar.get_width() / 2.0,
            bar.get_height() + max_val * 0.015,
            text,
            ha="center",
            va="bottom",
            fontsize=13,
        )
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def _plot_method_boxplot(line_rows, order, output_png: Path, title: str):
    if not line_rows:
        return
    fig, ax = plt.subplots(figsize=(11.2, 7.8))
    grouped = defaultdict(list)
    by_file = defaultdict(dict)
    for row in line_rows:
        label = row["label"]
        compression_ratio = float(row["compression_ratio"])
        grouped[label].append(compression_ratio)
        by_file[row["file"]][label] = compression_ratio
    plot_order = [label for label in order if label in grouped]
    positions = np.arange(len(plot_order))
    label_to_pos = {label: pos for label, pos in zip(plot_order, positions)}
    data = [grouped[label] for label in plot_order]
    box = ax.boxplot(
        data,
        vert=False,
        positions=positions,
        widths=0.6,
        patch_artist=True,
        showfliers=False,
    )
    for patch, label in zip(box["boxes"], plot_order):
        patch.set_facecolor(COLOR_MAP[label])
        patch.set_alpha(0.35)
        patch.set_edgecolor(COLOR_MAP[label])
    rng = np.random.default_rng(20260406)
    point_y_by_file_label = {
        (row["file"], row["label"]): label_to_pos[row["label"]] + float(rng.uniform(-0.12, 0.12))
        for row in line_rows
        if row["label"] in label_to_pos
    }
    for file_name, values_by_label in by_file.items():
        xs = []
        ys = []
        for label in plot_order:
            if label not in values_by_label:
                continue
            xs.append(values_by_label[label])
            ys.append(point_y_by_file_label[(file_name, label)])
        if len(xs) >= 2:
            ax.plot(
                xs,
                ys,
                color="#8A8A8A",
                linestyle=(0, (2.0, 2.8)),
                linewidth=0.75,
                alpha=0.38,
                zorder=1,
            )
    for pos, label in zip(positions, plot_order):
        rows_for_label = [item for item in line_rows if item["label"] == label]
        vals = [float(row["compression_ratio"]) for row in rows_for_label]
        ys = [point_y_by_file_label[(row["file"], label)] for row in rows_for_label]
        ax.scatter(vals, ys, color=COLOR_MAP[label], s=36, alpha=0.85, zorder=3)
    ax.set_yticks(positions)
    ax.set_yticklabels([DISPLAY_NAME[label] for label in plot_order], fontsize=11)
    ax.set_xlabel("Compression Ratio", fontsize=13)
    ax.set_title(title, fontsize=16)
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def _plot_file_lines_by_method(line_rows, order, output_png: Path, title: str):
    if not line_rows:
        return
    fig, ax = plt.subplots(figsize=(11.6, 7.6))
    by_file = defaultdict(dict)
    for row in line_rows:
        by_file[row["file_label"]][row["label"]] = float(row["compression_ratio"])
    x_labels = [label for label in order if any(label in vals for vals in by_file.values())]
    x = np.arange(len(x_labels))
    palette = plt.cm.tab10(np.linspace(0, 1, max(3, len(by_file))))
    for color, (file_label, vals) in zip(palette, sorted(by_file.items())):
        y = [vals.get(label, np.nan) for label in x_labels]
        ax.plot(x, y, marker="o", linewidth=2.0, markersize=5.5, label=file_label, color=color, alpha=0.95)
        for xi, yi in zip(x, y):
            if not np.isnan(yi):
                ax.text(xi, yi, f"{yi:.2f}", fontsize=8.5, ha="center", va="bottom", color=color)
    ax.set_xticks(x)
    ax.set_xticklabels([DISPLAY_NAME[label] for label in x_labels], rotation=25, ha="right", fontsize=11)
    ax.set_ylabel("Compression Ratio", fontsize=13)
    ax.set_title(title, fontsize=16)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=10, frameon=False)
    fig.tight_layout()
    fig.savefig(output_png, dpi=180)
    plt.close(fig)


def _aggregate_section(per_file_map, order):
    out = {}
    for label in order:
        vals = [method_map[label]["compression_ratio"] for method_map in per_file_map.values() if label in method_map]
        enc = [method_map[label].get("encode_time_s", 0.0) for method_map in per_file_map.values() if label in method_map]
        dec = [method_map[label].get("decode_time_s", 0.0) for method_map in per_file_map.values() if label in method_map]
        out[label] = {
            "mean_cr": statistics.mean(vals) if vals else 0.0,
            "median_cr": statistics.median(vals) if vals else 0.0,
            "mean_encode_time_s": statistics.mean(enc) if enc else 0.0,
            "mean_decode_time_s": statistics.mean(dec) if dec else 0.0,
        }
    return out


def _load_existing_ms1_methods_csv(per_file_csv: Path):
    return _load_existing_methods_csv(per_file_csv)


def _load_existing_methods_csv(per_file_csv: Path):
    with open(per_file_csv, newline="") as handle:
        rows = list(csv.DictReader(handle))
    by_file = defaultdict(dict)
    for row in rows:
        file_name = Path(row["file"]).name
        method = row["method"]
        parsed = {
            "raw_bytes": int(float(row["raw_bytes"])) if row.get("raw_bytes") else 0,
            "compressed_bytes": int(float(row["compressed_bytes"])) if row.get("compressed_bytes") else 0,
            "compression_ratio": float(row["compression_ratio"]) if row.get("compression_ratio") else 0.0,
            "encode_time_s": float(row["encode_time_s"]) if row.get("encode_time_s") else 0.0,
            "decode_time_s": float(row["decode_time_s"]) if row.get("decode_time_s") else 0.0,
            "precision": int(float(row["precision"])) if row.get("precision") else 0,
            "max_abs_mz_error": float(row["max_abs_mz_error"]) if row.get("max_abs_mz_error") else 0.0,
            "max_abs_intensity_error": float(row["max_abs_intensity_error"]) if row.get("max_abs_intensity_error") else 0.0,
            "median_mz_ppm_p95": float(row["median_mz_ppm_p95"]) if row.get("median_mz_ppm_p95") else 0.0,
            "median_int_rel_p95": float(row["median_int_rel_p95"]) if row.get("median_int_rel_p95") else 0.0,
        }
        seg = row.get("segment_sizes", "")
        parsed["segment_sizes"] = ast.literal_eval(seg) if seg else {}
        by_file[file_name][method] = parsed
    return {file_name: dict(methods) for file_name, methods in sorted(by_file.items())}


def _ms2_window_key(spec):
    iso = spec.get("precursorList", {}).get("precursor", [{}])[0].get("isolationWindow", {})
    target = float(iso.get("isolation window target m/z", 0.0))
    lower = float(iso.get("isolation window lower offset", 0.0))
    upper = float(iso.get("isolation window upper offset", 0.0))
    return (round(target, 4), round(lower, 4), round(upper, 4))


def _sorted_dia_window_items(ms2_source):
    if ms2_source is None:
        return []
    if isinstance(ms2_source, dict):
        return sorted(ms2_source.items(), key=lambda item: item[0])
    return sorted(list(ms2_source), key=lambda item: item[0])


def _count_dia_window_scans(ms2_source) -> int:
    if ms2_source is None:
        return 0
    if isinstance(ms2_source, dict):
        return int(sum(len(v) for v in ms2_source.values()))
    return int(sum(len(scans) for _, scans in ms2_source))


def _loaded_ms2_scan_count(loaded: dict) -> int:
    ms2_window_items = loaded.get("ms2_window_items")
    if ms2_window_items is not None:
        return _count_dia_window_scans(ms2_window_items)
    ms2_by_window = loaded.get("ms2_by_window")
    if ms2_by_window is not None:
        return _count_dia_window_scans(ms2_by_window)
    ms2_scans = loaded.get("ms2_scans")
    if ms2_scans is not None:
        return int(len(ms2_scans))
    return 0


def load_dia_ms2_by_window(path: Path):
    grouped = defaultdict(list)
    raw_ms2_bytes = 0
    with mzml.MzML(str(path)) as reader:
        for spec in reader:
            if spec.get("ms level", 0) != 2:
                continue
            key = _ms2_window_key(spec)
            rt = float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))
            mz_array = np.asarray(spec.get("m/z array", []), dtype=np.float64)
            int_array = np.asarray(spec.get("intensity array", []), dtype=np.float64)
            grouped[key].append(
                {
                    "scan_idx": len(grouped[key]),
                    "rt": rt,
                    "mz_array": mz_array,
                    "intensity_array": int_array,
                    "original_id": spec.get("id"),
                }
            )
            raw_ms2_bytes += len(mz_array) * 16
    return grouped, raw_ms2_bytes


def _ours_ms2_codec_for_file(file_path: Path) -> DIAWindowMS2Codec:
    if file_path in DIA_FILES:
        return DIAWindowMS2Codec(MS2ModeConfig.exact_track_dia_current())
    return DIAWindowMS2Codec(MS2ModeConfig.exact_track_dda_current())


def _collect_ms2_segment_sizes(encoded: dict) -> dict[str, int]:
    segment_sizes = defaultdict(int)
    if encoded.get("section_blob") is not None:
        _, segments = _unpack_exact_track_segments(encoded["section_blob"])
        for name, blob in segments.items():
            segment_sizes[name] += len(blob)
        return dict(segment_sizes)
    if encoded.get("container_blob") is not None and not encoded.get("windows") and not encoded.get("blocks"):
        return {}
    for item in encoded.get("windows", []):
        payload = item["payload"]
        if payload.startswith(b"DMS2ET6\0"):
            _, segments = _unpack_exact_track_segments(payload)
        else:
            _, segments = _unpack_segments(payload)
        for name, blob in segments.items():
            segment_sizes[name] += len(blob)
    for item in encoded.get("blocks", []):
        payload = item["payload"]
        if payload.startswith(b"DMS2ET6\0"):
            _, segments = _unpack_exact_track_segments(payload)
        else:
            _, segments = _unpack_segments(payload)
        for name, blob in segments.items():
            segment_sizes[name] += len(blob)
    return dict(segment_sizes)


def compare_centroid_scans(original_scans, decoded_scans):
    max_abs_mz = 0.0
    max_abs_int = 0.0
    ppm_p95 = []
    int_rel_p95 = []
    total = min(len(original_scans), len(decoded_scans))
    for orig, dec in zip(original_scans[:total], decoded_scans[:total]):
        mz = np.asarray(orig["mz_array"], dtype=np.float64)
        intensity = np.asarray(orig["intensity_array"], dtype=np.float64)
        dec_mz = np.asarray(dec["mz_array"], dtype=np.float64)
        dec_int = np.asarray(dec["intensity_array"], dtype=np.float64)
        if len(mz) != len(dec_mz):
            continue
        order_o = np.argsort(mz)
        order_d = np.argsort(dec_mz)
        mz = mz[order_o]
        intensity = intensity[order_o]
        dec_mz = dec_mz[order_d]
        dec_int = dec_int[order_d]
        mz_diff = np.abs(mz - dec_mz)
        int_diff = np.abs(intensity - dec_int)
        max_abs_mz = max(max_abs_mz, float(mz_diff.max(initial=0.0)))
        max_abs_int = max(max_abs_int, float(int_diff.max(initial=0.0)))
        if len(mz):
            ppm_p95.append(float(np.percentile(mz_diff / np.maximum(mz, 1e-12) * 1e6, 95)))
        if len(intensity):
            int_rel_p95.append(float(np.percentile(int_diff / np.maximum(np.abs(intensity), 1.0), 95)))
    return {
        "max_abs_mz_error": max_abs_mz,
        "max_abs_intensity_error": max_abs_int,
        "median_mz_ppm_p95": float(np.median(ppm_p95)) if ppm_p95 else 0.0,
        "median_int_rel_p95": float(np.median(int_rel_p95)) if int_rel_p95 else 0.0,
    }


def _categorize_segment_sizes(segment_sizes: dict[str, int], mz_names: set[str], int_names: set[str], meta_names: set[str] | None = None):
    mz_bytes = 0
    int_bytes = 0
    meta_bytes = 0
    other_bytes = 0
    for name, size in segment_sizes.items():
        size = int(size)
        if name in mz_names:
            mz_bytes += size
        elif name in int_names:
            int_bytes += size
        elif meta_names is None or name in meta_names:
            meta_bytes += size
        else:
            other_bytes += size
    return mz_bytes, int_bytes, meta_bytes, other_bytes


def _build_tracks_from_scans(ms1_scans):
    return archive_build_ms1_tracks(
        ms1_scans,
        island_workers=MS1_ISLAND_WORKERS,
        compact=True,
        drop_islands_in_compact=True,
    )


def _archive_ms1_section_cache_token() -> str:
    payload = {
        "kind": "archive_fidelity_track_island_ms1_section",
        "eq_archive_ms1_mode": EQ_ARCHIVE_MS1_MODE,
        "retain_zero_intensity_mz": True,
        "sparse_profile_bypass": DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
        "short_max_points": DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
        "min_zero_fraction": DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
        "min_islands_per_scan": DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
        "min_short_fraction": DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
    }
    return hashlib.sha256(json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _archive_ms1_section_templates(ms1_scans) -> list[dict]:
    return [
        {
            "scan_idx": int(scan["scan_idx"]),
            "rt": float(scan["rt"]),
            "array_length": int(len(scan["mz_array"])),
        }
        for scan in ms1_scans
    ]


def _compare_archive_fidelity_ms1_roundtrip(
    ms1_scans,
    decoded_islands,
    array_starts: np.ndarray,
    full_mz_arrays,
    orphan_entries,
) -> dict:
    if len(decoded_islands) != int(len(array_starts)):
        raise ValueError(f"MS1 array-start sidecar mismatch: {len(array_starts)} vs {len(decoded_islands)}")
    for island, start in zip(decoded_islands, array_starts):
        island["array_start_idx"] = int(start)
    templates = _archive_ms1_section_templates(ms1_scans)
    reconstructed = reconstruct_ms1_scans_from_islands_with_full_mz(decoded_islands, templates, full_mz_arrays)
    if orphan_entries is not None:
        for scan_idx, array_idx, intensity in zip(
            orphan_entries["scan_indices"],
            orphan_entries["array_indices"],
            orphan_entries["intensity_values"],
        ):
            reconstructed[int(scan_idx)]["intensity_array"][int(array_idx)] = float(intensity)
    return compare_roundtrip_scans(ms1_scans, reconstructed)


def _encode_compare_ms1_archive_fidelity_auto_variant(
    *,
    file_path: Path,
    ms1_scans,
    worker_cfg: dict,
) -> dict:
    cache_dir = _cache_dir(file_path.name) / "archive_fidelity_auto_ms1_section"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stats_cache_path = cache_dir / f"{_archive_ms1_section_cache_token()}.json"
    cached = _read_json(stats_cache_path)
    if cached is not None:
        _emit_log(file_path.name, f"[FULL8][MS1] archive-auto cache-hit {file_path.name}")
        return _canonicalize_method_stats(cached, raw_bytes=int(_raw_bytes_from_scans(ms1_scans)))

    codec = MzMLSectionArchiveCodec(
        ms1_mode=EQ_ARCHIVE_MS1_MODE,
        ms1_island_workers=int(worker_cfg["track_prepare_workers"]),
        ms1_encode_section_workers=int(worker_cfg["encode_section_workers"]),
        retain_zero_intensity_mz=True,
        ms1_sparse_profile_bypass=DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
        ms1_sparse_profile_short_max_points=DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
        ms1_sparse_profile_min_zero_fraction=DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
        ms1_sparse_profile_min_islands_per_scan=DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
        ms1_sparse_profile_min_short_fraction=DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
    )
    archive_ms1_mode = _make_archive_ms1_mode(EQ_ARCHIVE_MS1_MODE, external_full_mz_sidecar=True)
    raw_bytes = int(_raw_bytes_from_scans(ms1_scans))

    _emit_log(file_path.name, f"[FULL8][MS1] archive-track-island tracks build start {file_path.name}")
    t0 = time.perf_counter()
    tracks, sparse_stats = build_ms1_tracks_for_archive(
        ms1_scans,
        island_workers=int(worker_cfg["track_prepare_workers"]),
        compact=True,
        strategy_b_valley_ratio=float(codec.ms1_strategy_b_valley_ratio),
        drop_islands_in_compact=True,
        retain_zero_intensity_mz=True,
        sparse_profile_bypass=DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
        sparse_short_max_points=DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
        sparse_min_zero_fraction=DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
        sparse_min_islands_per_scan=DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
        sparse_min_short_fraction=DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
    )
    build_tracks_time = time.perf_counter() - t0
    _emit_log(file_path.name, f"[FULL8][MS1] archive-track-island tracks built {file_path.name} n_tracks={len(tracks)}")

    with tempfile.TemporaryDirectory(prefix="trackcodec_full8_archive_ms1_section_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        section_dir = tmp_dir / "section"
        cache_base = tmp_dir / "cache_base"
        cache_base.mkdir(parents=True, exist_ok=True)
        stage_timings: dict[str, float] = {}
        t0 = time.perf_counter()
        scan_store = getattr(ms1_scans, "store", None)
        paths = codec._encode_ms1_archive_sections_to_paths(
            mzml_path=file_path,
            scan_store=scan_store,
            ms1_scans=ms1_scans,
            tracks=tracks,
            archive_ms1_mode=archive_ms1_mode,
            section_dir=section_dir,
            cache_base=cache_base,
            stage_timings=stage_timings,
            progress_callback=None,
        )
        encode_time = time.perf_counter() - t0
        (
            ms1_payload_path,
            ms1_meta,
            array_payload_path,
            array_meta,
            orphan_payload_path,
            orphan_meta,
            full_mz_payload_path,
            full_mz_meta,
        ) = paths

        ms1_meta_path = section_dir / "ms1_meta"
        array_meta_path = section_dir / "ms1_array_starts_meta"
        orphan_meta_path = section_dir / "ms1_orphan_intensity_meta"
        full_mz_meta_path = section_dir / "ms1_full_mz_meta"
        compressed_bytes = (
            int(ms1_payload_path.stat().st_size)
            + int(ms1_meta_path.stat().st_size)
            + int(array_payload_path.stat().st_size)
            + int(array_meta_path.stat().st_size)
            + int(orphan_payload_path.stat().st_size)
            + int(orphan_meta_path.stat().st_size)
            + (int(full_mz_payload_path.stat().st_size) if full_mz_payload_path.exists() else 0)
            + (int(full_mz_meta_path.stat().st_size) if full_mz_meta_path.exists() else 0)
        )

        dt0 = time.perf_counter()
        payload = ms1_payload_path.read_bytes()
        ms1_codec = MS1Codec(
            mz_precision=archive_ms1_mode["mz_precision"],
            intensity_mode=archive_ms1_mode["intensity_mode"],
            backend=archive_ms1_mode["backend"],
            **archive_ms1_mode["kwargs"],
        )
        decoded_islands, _, _ = ms1_codec.decode(payload, ms1_meta)
        array_starts = _decode_uint32_sidecar(array_payload_path.read_bytes(), array_meta)
        orphan_entries = _decode_ms1_orphan_intensity_sidecar(orphan_payload_path.read_bytes(), orphan_meta)
        full_mz_arrays = (
            _decode_ms1_full_mz_sidecar(full_mz_payload_path.read_bytes(), full_mz_meta)
            if full_mz_payload_path.exists() and bool(full_mz_meta.get("enabled", True))
            else None
        )
        errors = _compare_archive_fidelity_ms1_roundtrip(
            ms1_scans,
            decoded_islands,
            array_starts,
            full_mz_arrays,
            orphan_entries,
        )
        decode_time = time.perf_counter() - dt0

        segment_sizes = {
            "ms1_payload": int(ms1_payload_path.stat().st_size),
            "ms1_meta": int(ms1_meta_path.stat().st_size),
            "ms1_array_starts_payload": int(array_payload_path.stat().st_size),
            "ms1_array_starts_meta": int(array_meta_path.stat().st_size),
            "ms1_orphan_intensity_payload": int(orphan_payload_path.stat().st_size),
            "ms1_orphan_intensity_meta": int(orphan_meta_path.stat().st_size),
            "ms1_full_mz_payload": int(full_mz_payload_path.stat().st_size) if full_mz_payload_path.exists() else 0,
            "ms1_full_mz_meta": int(full_mz_meta_path.stat().st_size) if full_mz_meta_path.exists() else 0,
        }
        stats = {
            "raw_bytes": int(raw_bytes),
            "compressed_bytes": int(compressed_bytes),
            "compression_ratio": float(raw_bytes) / float(compressed_bytes) if compressed_bytes else 0.0,
            "encode_time_s": float(encode_time),
            "decode_time_s": float(decode_time),
            "max_abs_mz_error": float(errors["max_abs_mz_error"]),
            "max_abs_intensity_error": float(errors["max_abs_intensity_error"]),
            "median_mz_ppm_p95": float(errors["median_mz_ppm_p95"]),
            "median_int_rel_p95": float(errors["median_int_rel_p95"]),
            "segment_sizes": segment_sizes,
            "validation_mode": "archive_fidelity_auto_ms1_section_full_scan_roundtrip",
            "archive_ms1_section": True,
            "track_storage": type(tracks).__name__,
            "track_prepare_workers": int(worker_cfg["track_prepare_workers"]),
            "encode_section_workers": int(worker_cfg["encode_section_workers"]),
            "ms1_cpu_budget": int(worker_cfg["cpu_budget"]),
            "worker_memory_reason": str(worker_cfg["reason"]),
            "build_ms1_tracks_s": float(build_tracks_time),
            "ms1_sparse_profile_bypass": _json_safe(sparse_stats),
            "ms1_payload_mode": _json_safe(archive_ms1_mode),
            "ms1_full_mz_meta": _json_safe(full_mz_meta),
            "ms1_full_mz_compressed_bytes": int(full_mz_meta.get("compressed_bytes", segment_sizes["ms1_full_mz_payload"]) or 0),
            "stage_timings_s": _json_safe(stage_timings),
            "ms1_representation": "track_island_archive",
        }
    _write_json(stats_cache_path, stats)
    return stats


def _load_ms1_only_scans(mzml_path: str, progress_label: str | None = None, progress_every: int = 5000):
    ms1_scans = []
    raw_ms1_bytes = 0
    total_specs = 0
    with mzml.MzML(
        str(mzml_path),
        read_schema=False,
        iterative=True,
        use_index=False,
        huge_tree=True,
        decode_binary=False,
    ) as reader:
        for spec in reader:
            total_specs += 1
            if progress_label and progress_every > 0 and total_specs % progress_every == 0:
                print(
                    f"[FULL8][MS1] scan-progress {progress_label} total_specs={total_specs} ms1={len(ms1_scans)}",
                    flush=True,
                )
            if spec.get("ms level", 0) != 1:
                continue
            rt = float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))
            mz_arr = np.asarray(spec["m/z array"].decode(), dtype=np.float64)
            int_arr = np.asarray(spec["intensity array"].decode(), dtype=np.float64)
            ms1_scans.append(
                {
                    "scan_idx": len(ms1_scans),
                    "rt": rt,
                    "mz_array": mz_arr,
                    "intensity_array": int_arr,
                }
            )
            raw_ms1_bytes += len(mz_arr) * 16
    if progress_label:
        print(
            f"[FULL8][MS1] scan-progress {progress_label} total_specs={total_specs} ms1={len(ms1_scans)} final=true",
            flush=True,
        )
    return ms1_scans, raw_ms1_bytes


def _benchmark_ms1_ours(
    ms1_scans,
    mode_cfg: dict,
    tracks=None,
    return_artifact: bool = False,
    validate_roundtrip: bool = True,
    file_name: str | None = None,
    mode_name: str = "ours",
):
    if tracks is None:
        tracks = _build_tracks_from_scans(ms1_scans)
    local_mode_cfg = dict(mode_cfg)
    local_mode_cfg.setdefault("encode_section_workers", int(MS1_ENCODE_SECTION_WORKERS))
    if local_mode_cfg.get("preserve_full_scan", False):
        cache_name = _artifact_file_dirname(file_name or "__anonymous_ms1__")
        local_mode_cfg.setdefault(
            "full_scan_sidecar_cache_dir",
            str(ARTIFACTS_DIR / "_cache" / cache_name / "ms1_full_scan_sidecar_cache"),
        )
    codec = MS1Codec(**local_mode_cfg)
    progress_callback = _make_ms1_codec_progress_callback(file_name, mode_name) if file_name else None
    t0 = time.perf_counter()
    payload, meta = codec.encode(tracks, ms1_scans, progress_callback=progress_callback)
    encode_time_s = time.perf_counter() - t0
    if validate_roundtrip:
        decoded_scans, decode_time_s = codec.decode_full_scans(
            payload,
            meta,
            original_rts=[float(scan["rt"]) for scan in ms1_scans],
        )
        errors = compare_roundtrip_scans(ms1_scans, decoded_scans)
    else:
        decode_time_s = 0.0
        errors = {
            "max_abs_mz_error": None,
            "max_abs_intensity_error": None,
            "median_mz_ppm_p95": None,
            "median_int_rel_p95": None,
        }
    result = {
        "raw_bytes": int(meta["raw_bytes"]),
        "compressed_bytes": int(meta["compressed_bytes"]),
        "compression_ratio": float(meta["compression_ratio"]),
        "encode_time_s": float(encode_time_s),
        "decode_time_s": float(decode_time_s),
        "max_abs_mz_error": (float(errors["max_abs_mz_error"]) if errors["max_abs_mz_error"] is not None else None),
        "max_abs_intensity_error": (
            float(errors["max_abs_intensity_error"]) if errors["max_abs_intensity_error"] is not None else None
        ),
        "median_mz_ppm_p95": (float(errors["median_mz_ppm_p95"]) if errors["median_mz_ppm_p95"] is not None else None),
        "median_int_rel_p95": (float(errors["median_int_rel_p95"]) if errors["median_int_rel_p95"] is not None else None),
        "segment_sizes": dict(meta.get("segment_sizes", {})),
        "roundtrip_validated": bool(validate_roundtrip),
    }
    if mode_name == "ours_eqfidelity":
        result["ms1_representation"] = "track_island_component"
    elif mode_name == "ours_strict_q6":
        result["ms1_representation"] = "track_island_strict"
    if return_artifact:
        return result, payload, meta
    return result


def benchmark_ms1_file(file_path_str: str, baseline_threads: int = 0, compare_baseline_errors: bool = False):
    file_path = Path(file_path_str)
    _emit_log(file_path.name, f"[FULL8][MS1] start {file_path.name}")
    ms1_scans, raw_ms1_bytes = _load_ms1_only_scans(str(file_path), progress_label=file_path.name)
    _emit_log(file_path.name, f"[FULL8][MS1] loaded {file_path.name} scans={len(ms1_scans)}")
    raw = {"raw_bytes": int(raw_ms1_bytes), "compressed_bytes": 0, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}
    raw_payload = float64_payload(ms1_scans)[0]
    t0 = time.perf_counter()
    gzip_stats = gzip_raw_payload(ms1_scans)
    gzip_encode = time.perf_counter() - t0
    gzip_payload = gzip.compress(raw_payload)
    dt0 = time.perf_counter()
    gzip.decompress(gzip_payload)
    gzip_decode = time.perf_counter() - dt0
    zlib_t0 = time.perf_counter()
    zlib_stats = raw_backend_payload(ms1_scans, "zlib")
    zlib_encode = time.perf_counter() - zlib_t0
    zlib_payload = backend_compress(raw_payload, "zlib")
    dt0 = time.perf_counter()
    backend_decompress(zlib_payload, "zlib")
    zlib_decode = time.perf_counter() - dt0
    zstd_t0 = time.perf_counter()
    zstd_stats = raw_backend_payload(ms1_scans, "zstd-9")
    zstd_encode = time.perf_counter() - zstd_t0
    zstd_payload = backend_compress(raw_payload, "zstd-9")
    dt0 = time.perf_counter()
    backend_decompress(zstd_payload, "zstd-9")
    zstd_decode = time.perf_counter() - dt0
    zdpd_stats = zdpd_baseline_compress(
        ms1_scans,
        mz_precision=6,
        num_threads=baseline_threads,
        compare_errors=compare_baseline_errors,
    )
    _emit_log(file_path.name, f"[FULL8][MS1] zdpd done {file_path.name}")
    stack_stats = stack_zdpd_baseline_compress(
        ms1_scans,
        mz_precision=6,
        stack_size=256,
        num_threads=baseline_threads,
        compare_errors=compare_baseline_errors,
    )
    _emit_log(file_path.name, f"[FULL8][MS1] stack-zdpd done {file_path.name}")
    tracks = _build_tracks_from_scans(ms1_scans)
    _emit_log(file_path.name, f"[FULL8][MS1] tracks built {file_path.name} n_tracks={len(tracks)}")
    ours_eq_stats = _benchmark_ms1_ours(ms1_scans, OUR_MODE, tracks=tracks, file_name=file_path.name, mode_name="ours_eqfidelity")
    _emit_log(file_path.name, f"[FULL8][MS1] ours-eq done {file_path.name}")
    ours_strict_stats = _benchmark_ms1_ours(ms1_scans, STRICT_MS1_MODE, tracks=tracks, file_name=file_path.name, mode_name="ours_strict_q6")
    _emit_log(file_path.name, f"[FULL8][MS1] ours-strict done {file_path.name}")
    out = {
        "file": file_path.name,
        "raw_bytes": raw["raw_bytes"],
        "methods": {
            "raw": {**raw},
            "gzip": {**gzip_stats, "encode_time_s": gzip_encode, "decode_time_s": gzip_decode},
            "zlib": {**zlib_stats, "encode_time_s": zlib_encode, "decode_time_s": zlib_decode},
            "zstd-9": {**zstd_stats, "encode_time_s": zstd_encode, "decode_time_s": zstd_decode},
            "zdpd_baseline": zdpd_stats,
            "stack_zdpd_baseline": stack_stats,
            "ours_eqfidelity": ours_eq_stats,
            "ours_strict_q6": ours_strict_stats,
        },
    }
    _emit_log(file_path.name, f"[FULL8][MS1] done {file_path.name}")
    return out


def benchmark_metadata_file(file_path_str: str):
    file_path = Path(file_path_str)
    _emit_log(file_path.name, f"[FULL8][META] start {file_path.name}")
    raw_blob = extract_binary_stripped_metadata_xml(file_path)
    metadata_codec = MzMLMetadataCodec("zstd-9")
    raw_bytes = len(raw_blob)

    def _backend_stats(backend_name: str):
        t0 = time.perf_counter()
        if backend_name == "gzip":
            comp = gzip.compress(raw_blob)
            encode_time = time.perf_counter() - t0
            dt0 = time.perf_counter()
            gzip.decompress(comp)
            decode_time = time.perf_counter() - dt0
        else:
            comp = backend_compress(raw_blob, backend_name)
            encode_time = time.perf_counter() - t0
            dt0 = time.perf_counter()
            backend_decompress(comp, backend_name)
            decode_time = time.perf_counter() - dt0
        return {
            "raw_bytes": raw_bytes,
            "compressed_bytes": len(comp),
            "compression_ratio": raw_bytes / len(comp) if len(comp) else 0.0,
            "encode_time_s": encode_time,
            "decode_time_s": decode_time,
        }

    ours_t0 = time.perf_counter()
    ours_payload, ours_meta = metadata_codec.encode_file(file_path)
    ours_encode = time.perf_counter() - ours_t0
    dt0 = time.perf_counter()
    metadata_codec.decode_to_bytes(ours_payload, ours_meta)
    ours_decode = time.perf_counter() - dt0
    out = {
        "file": file_path.name,
        "raw_bytes": raw_bytes,
        "methods": {
            "raw": {"raw_bytes": raw_bytes, "compressed_bytes": raw_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0},
            "gzip": _backend_stats("gzip"),
            "zlib": _backend_stats("zlib"),
            "zstd-9": _backend_stats("zstd-9"),
            "ours_metadata": {
                "raw_bytes": ours_meta["raw_bytes"],
                "compressed_bytes": ours_meta["compressed_bytes"],
                "compression_ratio": ours_meta["compression_ratio"],
                "encode_time_s": ours_encode,
                "decode_time_s": ours_decode,
            },
        },
    }
    _emit_log(file_path.name, f"[FULL8][META] done {file_path.name}")
    return out


def benchmark_ms2_file(file_path_str: str, baseline_threads: int = 0, compare_baseline_errors: bool = False):
    file_path = Path(file_path_str)
    _emit_log(file_path.name, f"[FULL8][MS2] start {file_path.name}")
    if file_path in DIA_FILES:
        ms2_by_window, raw_ms2_bytes = load_dia_ms2_by_window(file_path)
        window_items = _sorted_dia_window_items(ms2_by_window)
        flat_scans = []
        for _, scans in window_items:
            flat_scans.extend(scans)
        raw_payload = float64_payload(flat_scans)[0]
        raw = {"raw_bytes": raw_ms2_bytes, "compressed_bytes": raw_ms2_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}
        gzip_t0 = time.perf_counter()
        gzip_stats = gzip_raw_payload(flat_scans)
        gzip_encode = time.perf_counter() - gzip_t0
        gzip_payload = gzip.compress(raw_payload)
        dt0 = time.perf_counter()
        gzip.decompress(gzip_payload)
        gzip_decode = time.perf_counter() - dt0
        zlib_t0 = time.perf_counter()
        zlib_stats = raw_backend_payload(flat_scans, "zlib")
        zlib_encode = time.perf_counter() - zlib_t0
        zlib_payload = backend_compress(raw_payload, "zlib")
        dt0 = time.perf_counter()
        backend_decompress(zlib_payload, "zlib")
        zlib_decode = time.perf_counter() - dt0
        zstd_t0 = time.perf_counter()
        zstd_stats = raw_backend_payload(flat_scans, "zstd-9")
        zstd_encode = time.perf_counter() - zstd_t0
        zstd_payload = backend_compress(raw_payload, "zstd-9")
        dt0 = time.perf_counter()
        backend_decompress(zstd_payload, "zstd-9")
        zstd_decode = time.perf_counter() - dt0
        zdpd_stats = zdpd_baseline_compress(
            flat_scans,
            mz_precision=6,
            num_threads=baseline_threads,
            compare_errors=compare_baseline_errors,
        )
        stack_stats = stack_zdpd_baseline_compress(
            flat_scans,
            mz_precision=6,
            stack_size=256,
            num_threads=baseline_threads,
            compare_errors=compare_baseline_errors,
        )

        codec = _ours_ms2_codec_for_file(file_path)
        t0 = time.perf_counter()
        ours = codec.encode_dia_windows(
            window_items,
            progress_callback=_make_ms2_codec_progress_callback(file_path.name, "ours_eqfidelity", "DIA"),
        )
        ours_encode = time.perf_counter() - t0
        dt0 = time.perf_counter()
        decoded = codec.decode_dia_windows(ours)
        ours_decode = time.perf_counter() - dt0
        compare_rows = []
        segment_sizes = _collect_ms2_segment_sizes(ours)
        for key, scans in window_items:
            compare_rows.append(compare_centroid_scans(scans, decoded[key]))
        max_abs_int = max((row["max_abs_intensity_error"] for row in compare_rows), default=0.0)
        max_abs_mz = max((row["max_abs_mz_error"] for row in compare_rows), default=0.0)
        median_mz_ppm_p95 = statistics.median([row["median_mz_ppm_p95"] for row in compare_rows]) if compare_rows else 0.0
        median_int_rel_p95 = statistics.median([row["median_int_rel_p95"] for row in compare_rows]) if compare_rows else 0.0
        ours_stats = {
            "raw_bytes": ours["raw_bytes"],
            "compressed_bytes": ours["compressed_bytes"],
            "compression_ratio": ours["compression_ratio"],
            "encode_time_s": ours_encode,
            "decode_time_s": ours_decode,
            "max_abs_intensity_error": max_abs_int,
            "max_abs_mz_error": max_abs_mz,
            "median_mz_ppm_p95": median_mz_ppm_p95,
            "median_int_rel_p95": median_int_rel_p95,
            "segment_sizes": dict(segment_sizes),
            "file_type": "DIA",
        }
    else:
        flat_scans, raw_ms2_bytes = load_all_ms2_scans(str(file_path), max_ms2_scans=0)
        raw_payload = float64_payload(flat_scans)[0]
        raw = {"raw_bytes": raw_ms2_bytes, "compressed_bytes": raw_ms2_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}
        gzip_t0 = time.perf_counter()
        gzip_stats = gzip_raw_payload(flat_scans)
        gzip_encode = time.perf_counter() - gzip_t0
        gzip_payload = gzip.compress(raw_payload)
        dt0 = time.perf_counter()
        gzip.decompress(gzip_payload)
        gzip_decode = time.perf_counter() - dt0
        zlib_t0 = time.perf_counter()
        zlib_stats = raw_backend_payload(flat_scans, "zlib")
        zlib_encode = time.perf_counter() - zlib_t0
        zlib_payload = backend_compress(raw_payload, "zlib")
        dt0 = time.perf_counter()
        backend_decompress(zlib_payload, "zlib")
        zlib_decode = time.perf_counter() - dt0
        zstd_t0 = time.perf_counter()
        zstd_stats = raw_backend_payload(flat_scans, "zstd-9")
        zstd_encode = time.perf_counter() - zstd_t0
        zstd_payload = backend_compress(raw_payload, "zstd-9")
        dt0 = time.perf_counter()
        backend_decompress(zstd_payload, "zstd-9")
        zstd_decode = time.perf_counter() - dt0
        zdpd_stats = zdpd_baseline_compress(
            flat_scans,
            mz_precision=6,
            num_threads=baseline_threads,
            compare_errors=compare_baseline_errors,
        )
        stack_stats = stack_zdpd_baseline_compress(
            flat_scans,
            mz_precision=6,
            stack_size=256,
            num_threads=baseline_threads,
            compare_errors=compare_baseline_errors,
        )

        codec = _ours_ms2_codec_for_file(file_path)
        t0 = time.perf_counter()
        ours = codec.encode_dda_blocks(
            flat_scans,
            progress_callback=_make_ms2_codec_progress_callback(file_path.name, "ours_eqfidelity", "DDA"),
        )
        ours_encode = time.perf_counter() - t0
        dt0 = time.perf_counter()
        decoded = codec.decode_dda_blocks(ours)
        ours_decode = time.perf_counter() - dt0
        block_compares = [compare_centroid_scans(flat_scans, decoded)]
        segment_sizes = _collect_ms2_segment_sizes(ours)
        max_abs_int = max((row["max_abs_intensity_error"] for row in block_compares), default=0.0)
        max_abs_mz = max((row["max_abs_mz_error"] for row in block_compares), default=0.0)
        median_mz_ppm_p95 = statistics.median([row["median_mz_ppm_p95"] for row in block_compares]) if block_compares else 0.0
        median_int_rel_p95 = statistics.median([row["median_int_rel_p95"] for row in block_compares]) if block_compares else 0.0
        ours_stats = {
            "raw_bytes": ours["raw_bytes"],
            "compressed_bytes": ours["compressed_bytes"],
            "compression_ratio": ours["compression_ratio"],
            "encode_time_s": ours_encode,
            "decode_time_s": ours_decode,
            "max_abs_intensity_error": max_abs_int,
            "max_abs_mz_error": max_abs_mz,
            "median_mz_ppm_p95": median_mz_ppm_p95,
            "median_int_rel_p95": median_int_rel_p95,
            "segment_sizes": dict(segment_sizes),
            "file_type": "DDA",
        }
    out = {
        "file": file_path.name,
        "raw_bytes": raw["raw_bytes"],
        "methods": {
            "raw": raw,
            "gzip": {**gzip_stats, "encode_time_s": gzip_encode, "decode_time_s": gzip_decode},
            "zlib": {**zlib_stats, "encode_time_s": zlib_encode, "decode_time_s": zlib_decode},
            "zstd-9": {**zstd_stats, "encode_time_s": zstd_encode, "decode_time_s": zstd_decode},
            "zdpd_baseline": zdpd_stats,
            "stack_zdpd_baseline": stack_stats,
            "ours_eqfidelity": ours_stats,
        },
    }
    _emit_log(file_path.name, f"[FULL8][MS2] done {file_path.name}")
    return out


def _load_sections_single_pass(
    file_path: Path,
    *,
    need_ms1: bool,
    need_ms2: bool,
    stage_rows: list[dict],
    progress_every: int = 5000,
):
    file_name = file_path.name
    is_dia = file_path in DIA_FILES
    if _scan_store_exists(file_name) and is_complete_memmap_scan_store(_cache_scan_store_root(file_name)):
        try:
            t0 = time.perf_counter()
            with MemoryMonitor() as monitor:
                scan_store = open_memmap_scan_store(_cache_scan_store_root(file_name))
                loaded = _materialize_loaded_from_scan_store(
                    scan_store,
                    need_ms1=need_ms1,
                    need_ms2=need_ms2,
                )
            elapsed_s = time.perf_counter() - t0
            ms2_count = _loaded_ms2_scan_count(loaded)
            row = _record_stage_metric(
                stage_rows,
                file_name=file_name,
                section="shared_parse",
                stage="scan_store_cache_load",
                elapsed_s=elapsed_s,
                peak_rss_bytes=monitor.peak_rss_bytes,
                peak_memory_pct=monitor.peak_memory_pct,
                total_specs=int(loaded.get("total_specs", 0)),
                ms1_scans=len(loaded.get("ms1_scans") or []),
                ms2_scans=ms2_count,
                file_type=str(loaded.get("file_type", "DIA" if is_dia else "DDA")),
                reused_cache=True,
                cache_key="scan_store",
            )
            _emit_log(
                file_name,
                f"[FULL8][PARSE] scan-store-hit {file_name} total_specs={row.get('total_specs', 0)} "
                f"ms1={row.get('ms1_scans', 0)} ms2={row.get('ms2_scans', 0)} "
                f"elapsed_s={elapsed_s:.3f} peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f}",
            )
            return loaded
        except Exception as exc:
            _emit_log(
                file_name,
                f"[FULL8][PARSE] scan-store-hit fallback {file_name} reason={type(exc).__name__}: {exc}",
            )
    split_cached = _load_split_section_cache(file_path, need_ms1=need_ms1, need_ms2=need_ms2)
    if split_cached is not None:
        t0 = time.perf_counter()
        with MemoryMonitor() as monitor:
            loaded = split_cached
        elapsed_s = time.perf_counter() - t0
        ms2_count = _loaded_ms2_scan_count(loaded)
        row = _record_stage_metric(
            stage_rows,
            file_name=file_name,
            section="shared_parse",
            stage="scan_parse_cache_load",
            elapsed_s=elapsed_s,
            peak_rss_bytes=monitor.peak_rss_bytes,
            peak_memory_pct=monitor.peak_memory_pct,
            total_specs=int(loaded.get("total_specs", 0)),
            ms1_scans=len(loaded.get("ms1_scans") or []),
            ms2_scans=ms2_count,
            file_type=str(loaded.get("file_type", "DIA" if is_dia else "DDA")),
            reused_cache=True,
            cache_key=str(loaded.get("_cache_key", "")),
        )
        _emit_log(
            file_name,
            f"[FULL8][PARSE] split-cache-hit {file_name} key={row.get('cache_key','')} total_specs={row.get('total_specs', 0)} "
            f"ms1={row.get('ms1_scans', 0)} ms2={row.get('ms2_scans', 0)} "
            f"elapsed_s={elapsed_s:.3f} peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f}",
        )
        return loaded
    cached = _load_cache_pickle(file_name, "shared_sections")
    if cached is not None:
        has_ms1 = bool(cached.get("ms1_scans") is not None)
        has_ms2 = bool(cached.get("ms2_by_window") is not None or cached.get("ms2_scans") is not None)
        if (not need_ms1 or has_ms1) and (not need_ms2 or has_ms2):
            t0 = time.perf_counter()
            with MemoryMonitor() as monitor:
                loaded = {
                    "file_type": cached.get("file_type", "DIA" if is_dia else "DDA"),
                    "ms1_scans": cached.get("ms1_scans") if need_ms1 else None,
                    "raw_ms1_bytes": int(cached.get("raw_ms1_bytes", 0)) if need_ms1 else 0,
                    "ms2_by_window": cached.get("ms2_by_window") if need_ms2 else None,
                    "ms2_scans": cached.get("ms2_scans") if need_ms2 else None,
                    "raw_ms2_bytes": int(cached.get("raw_ms2_bytes", 0)) if need_ms2 else 0,
                    "total_specs": int(cached.get("total_specs", 0)),
                    "_cache_key": "shared_sections",
                }
            elapsed_s = time.perf_counter() - t0
            _save_split_section_caches(file_name, cached)
            ms2_count = _loaded_ms2_scan_count(loaded)
            row = _record_stage_metric(
                stage_rows,
                file_name=file_name,
                section="shared_parse",
                stage="scan_parse_cache_load",
                elapsed_s=elapsed_s,
                peak_rss_bytes=monitor.peak_rss_bytes,
                peak_memory_pct=monitor.peak_memory_pct,
                total_specs=int(loaded.get("total_specs", 0)),
                ms1_scans=len(loaded.get("ms1_scans") or []),
                ms2_scans=ms2_count,
                file_type=str(loaded.get("file_type", "DIA" if is_dia else "DDA")),
                reused_cache=True,
                cache_key="shared_sections",
            )
            _emit_log(
                file_name,
            f"[FULL8][PARSE] cache-hit {file_name} total_specs={row.get('total_specs', 0)} "
            f"ms1={row.get('ms1_scans', 0)} ms2={row.get('ms2_scans', 0)} "
            f"elapsed_s={elapsed_s:.3f} peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f}",
        )
        return loaded
    try:
        _emit_log(file_name, f"[FULL8][PARSE] scan-store-build start {file_name} need_ms1={need_ms1} need_ms2={need_ms2}")
        parse_t0 = time.perf_counter()
        with MemoryMonitor() as monitor:
            scan_store = create_memmap_scan_store_from_mzml(
                file_path,
                "DIA" if is_dia else "DDA",
                _cache_scan_store_root(file_name),
                show_progress=False,
            )
            loaded = _materialize_loaded_from_scan_store(
                scan_store,
                need_ms1=need_ms1,
                need_ms2=need_ms2,
            )
        elapsed_s = time.perf_counter() - parse_t0
        ms2_count = _loaded_ms2_scan_count(loaded)
        row = _record_stage_metric(
            stage_rows,
            file_name=file_name,
            section="shared_parse",
            stage="scan_store_build",
            elapsed_s=elapsed_s,
            peak_rss_bytes=monitor.peak_rss_bytes,
            peak_memory_pct=monitor.peak_memory_pct,
            total_specs=int(loaded.get("total_specs", 0)),
            ms1_scans=len(loaded.get("ms1_scans") or []),
            ms2_scans=ms2_count,
            file_type=str(loaded.get("file_type", "DIA" if is_dia else "DDA")),
            reused_cache=False,
            cache_key="scan_store",
        )
        _save_scan_store_meta(
            file_name,
            {
                "file_type": str(loaded.get("file_type", "DIA" if is_dia else "DDA")),
                "total_specs": int(loaded.get("total_specs", 0)),
                "ms1_scans": len(loaded.get("ms1_scans") or []),
                "ms2_scans": ms2_count,
                "raw_ms1_bytes": int(loaded.get("raw_ms1_bytes", 0)),
                "raw_ms2_bytes": int(loaded.get("raw_ms2_bytes", 0)),
                "cache_key": "scan_store",
            },
        )
        _emit_log(
            file_name,
            f"[FULL8][PARSE] scan-store-build done {file_name} total_specs={row.get('total_specs', 0)} "
            f"ms1={row.get('ms1_scans', 0)} ms2={row.get('ms2_scans', 0)} "
            f"elapsed_s={elapsed_s:.3f} peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f}",
        )
        return loaded
    except Exception as exc:
        _emit_log(
            file_name,
            f"[FULL8][PARSE] scan-store-build fallback {file_name} reason={type(exc).__name__}: {exc}",
        )
    ms1_scans = [] if need_ms1 else None
    ms2_by_window = defaultdict(list) if (need_ms2 and is_dia) else None
    ms2_scans = [] if (need_ms2 and not is_dia) else None
    raw_ms1_bytes = 0
    raw_ms2_bytes = 0
    total_specs = 0
    ms2_count = 0
    parse_t0 = time.perf_counter()
    _emit_log(file_name, f"[FULL8][PARSE] start {file_name} need_ms1={need_ms1} need_ms2={need_ms2}")
    with MemoryMonitor() as monitor:
        with mzml.MzML(
            str(file_path),
            read_schema=False,
            iterative=True,
            use_index=False,
            huge_tree=True,
            decode_binary=False,
        ) as reader:
            for spec in reader:
                total_specs += 1
                ms_level = spec.get("ms level", 0)
                if ms_level == 1 and need_ms1:
                    rt = float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))
                    mz_arr = np.asarray(spec["m/z array"].decode(), dtype=np.float64)
                    int_arr = np.asarray(spec["intensity array"].decode(), dtype=np.float64)
                    ms1_scans.append(
                        {
                            "scan_idx": len(ms1_scans),
                            "rt": rt,
                            "mz_array": mz_arr,
                            "intensity_array": int_arr,
                        }
                    )
                    raw_ms1_bytes += len(mz_arr) * 16
                elif ms_level == 2 and need_ms2:
                    rt = float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))
                    mz_arr = np.asarray(spec["m/z array"].decode(), dtype=np.float64)
                    int_arr = np.asarray(spec["intensity array"].decode(), dtype=np.float64)
                    raw_ms2_bytes += len(mz_arr) * 16
                    if is_dia:
                        key = _ms2_window_key(spec)
                        ms2_by_window[key].append(
                            {
                                "scan_idx": len(ms2_by_window[key]),
                                "rt": rt,
                                "mz_array": mz_arr,
                                "intensity_array": int_arr,
                                "original_id": spec.get("id"),
                            }
                        )
                    else:
                        iw = spec.get("precursorList", {}).get("precursor", [{}])[0].get("isolationWindow", {})
                        try:
                            tmz = round(float(iw.get("isolation window target m/z", 0.0)), 4)
                        except (TypeError, ValueError):
                            tmz = 0.0
                        ms2_scans.append(
                            {
                                "scan_idx": len(ms2_scans),
                                "rt": rt,
                                "mz_array": mz_arr,
                                "intensity_array": int_arr,
                                "target_mz": tmz,
                                "original_id": spec.get("id"),
                            }
                        )
                    ms2_count += 1
                if progress_every > 0 and total_specs % progress_every == 0:
                    elapsed_s = time.perf_counter() - parse_t0
                    rss_gb = float(_current_rss_bytes()) / (1024.0 ** 3)
                    msg = (
                        f"[FULL8][PARSE] scan-progress {file_name} total_specs={total_specs} "
                        f"ms1={len(ms1_scans) if ms1_scans is not None else 0} ms2={ms2_count} "
                        f"elapsed_s={elapsed_s:.3f} rss_gb={rss_gb:.3f}"
                    )
                    _emit_log(file_name, msg)
                    _append_progress_row(
                        file_name,
                        {
                            "file": file_name,
                            "section": "shared_parse",
                            "stage": "scan_progress",
                            "total_specs": total_specs,
                            "ms1_scans": len(ms1_scans) if ms1_scans is not None else 0,
                            "ms2_scans": ms2_count,
                            "elapsed_s": elapsed_s,
                            "current_rss_bytes": int(_current_rss_bytes()),
                            "current_rss_gb": rss_gb,
                        },
                    )
    elapsed_s = time.perf_counter() - parse_t0
    if ms2_by_window is not None:
        for key in ms2_by_window:
            ms2_by_window[key].sort(key=lambda x: x["rt"])
    row = _record_stage_metric(
        stage_rows,
        file_name=file_name,
        section="shared_parse",
        stage="scan_parse",
        elapsed_s=elapsed_s,
        peak_rss_bytes=monitor.peak_rss_bytes,
        peak_memory_pct=monitor.peak_memory_pct,
        total_specs=total_specs,
        ms1_scans=len(ms1_scans) if ms1_scans is not None else 0,
        ms2_scans=ms2_count,
        file_type="DIA" if is_dia else "DDA",
    )
    _emit_log(
        file_name,
        f"[FULL8][PARSE] done {file_name} total_specs={total_specs} "
        f"ms1={len(ms1_scans) if ms1_scans is not None else 0} ms2={ms2_count} "
        f"elapsed_s={elapsed_s:.3f} peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f}",
    )
    loaded = {
        "file_type": "DIA" if is_dia else "DDA",
        "ms1_scans": ms1_scans,
        "raw_ms1_bytes": int(raw_ms1_bytes),
        "ms2_by_window": ms2_by_window,
        "ms2_scans": ms2_scans,
        "raw_ms2_bytes": int(raw_ms2_bytes),
        "total_specs": int(total_specs),
    }
    _save_cache_pickle(
        file_name,
        "shared_sections",
        loaded,
        meta={
            "file_type": loaded["file_type"],
            "ms1_scans": len(ms1_scans) if ms1_scans is not None else 0,
            "ms2_scans": ms2_count,
            "total_specs": int(total_specs),
            "raw_ms1_bytes": int(raw_ms1_bytes),
            "raw_ms2_bytes": int(raw_ms2_bytes),
        },
    )
    _save_split_section_caches(file_name, loaded)
    return loaded


def _benchmark_ms1_from_loaded(
    file_path: Path,
    loaded: dict,
    stage_rows: list[dict],
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
    validate_ours: bool = True,
):
    file_name = file_path.name
    ms1_scans = loaded["ms1_scans"] or []
    raw_ms1_bytes = int(loaded["raw_ms1_bytes"])
    _emit_log(file_name, f"[FULL8][MS1] loaded {file_name} scans={len(ms1_scans)}")
    raw = {"raw_bytes": raw_ms1_bytes, "compressed_bytes": raw_ms1_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}
    raw_payload_holder = {"payload": None}

    def _raw_payload() -> bytes:
        if raw_payload_holder["payload"] is None:
            raw_payload_holder["payload"] = float64_payload(ms1_scans)[0]
        return raw_payload_holder["payload"]

    def _normalize_stats(stats: dict | None) -> dict | None:
        return _canonicalize_method_stats(stats, raw_bytes=raw_ms1_bytes)

    gzip_stats = _normalize_stats(_artifact_stats("ms1", file_name, "gzip"))
    if gzip_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="gzip_reuse",
            fn=lambda: gzip_stats,
            log_prefix=f"[FULL8][MS1] gzip reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
        )
    else:
        gzip_t0 = time.perf_counter()
        gzip_stats = gzip_raw_payload(ms1_scans)
        gzip_encode = time.perf_counter() - gzip_t0
        gzip_payload = gzip.compress(_raw_payload())
        dt0 = time.perf_counter()
        gzip.decompress(gzip_payload)
        gzip_decode = time.perf_counter() - dt0
        gzip_stats = {
            **gzip_stats,
            "encode_time_s": gzip_encode,
            "decode_time_s": gzip_decode,
            "raw_bytes": raw_ms1_bytes,
            "compressed_bytes": len(gzip_payload),
        }
        _save_bytes_artifact(
            "ms1",
            file_name,
            "gzip",
            gzip_payload,
            meta={"raw_bytes": raw_ms1_bytes, "compressed_bytes": len(gzip_payload), "compression_ratio": gzip_stats["compression_ratio"]},
            stats=gzip_stats,
        )

    zlib_stats = _normalize_stats(_artifact_stats("ms1", file_name, "zlib"))
    if zlib_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="zlib_reuse",
            fn=lambda: zlib_stats,
            log_prefix=f"[FULL8][MS1] zlib reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
        )
    else:
        zlib_t0 = time.perf_counter()
        zlib_stats = raw_backend_payload(ms1_scans, "zlib")
        zlib_encode = time.perf_counter() - zlib_t0
        zlib_payload = backend_compress(_raw_payload(), "zlib")
        dt0 = time.perf_counter()
        backend_decompress(zlib_payload, "zlib")
        zlib_decode = time.perf_counter() - dt0
        zlib_stats = {
            **zlib_stats,
            "encode_time_s": zlib_encode,
            "decode_time_s": zlib_decode,
            "raw_bytes": raw_ms1_bytes,
            "compressed_bytes": len(zlib_payload),
        }
        _save_bytes_artifact(
            "ms1",
            file_name,
            "zlib",
            zlib_payload,
            meta={"raw_bytes": raw_ms1_bytes, "compressed_bytes": len(zlib_payload), "compression_ratio": zlib_stats["compression_ratio"]},
            stats=zlib_stats,
        )

    zstd_stats = _normalize_stats(_artifact_stats("ms1", file_name, "zstd-9"))
    if zstd_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="zstd_reuse",
            fn=lambda: zstd_stats,
            log_prefix=f"[FULL8][MS1] zstd reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
        )
    else:
        zstd_t0 = time.perf_counter()
        zstd_stats = raw_backend_payload(ms1_scans, "zstd-9")
        zstd_encode = time.perf_counter() - zstd_t0
        zstd_payload = backend_compress(_raw_payload(), "zstd-9")
        dt0 = time.perf_counter()
        backend_decompress(zstd_payload, "zstd-9")
        zstd_decode = time.perf_counter() - dt0
        zstd_stats = {
            **zstd_stats,
            "encode_time_s": zstd_encode,
            "decode_time_s": zstd_decode,
            "raw_bytes": raw_ms1_bytes,
            "compressed_bytes": len(zstd_payload),
        }
        _save_bytes_artifact(
            "ms1",
            file_name,
            "zstd-9",
            zstd_payload,
            meta={"raw_bytes": raw_ms1_bytes, "compressed_bytes": len(zstd_payload), "compression_ratio": zstd_stats["compression_ratio"]},
            stats=zstd_stats,
        )

    def _encode_decode_zdpd():
        payload, meta = zdpd_baseline_encode(
            ms1_scans,
            mz_precision=6,
            num_threads=baseline_threads,
        )
        decoded_scans, decode_time_s = zdpd_baseline_decode(payload)
        errors = compare_roundtrip_scans(ms1_scans, decoded_scans) if compare_baseline_errors else {
            "max_abs_mz_error": None,
            "max_abs_intensity_error": None,
            "median_mz_ppm_p95": None,
            "median_int_rel_p95": None,
        }
        stats = {**meta, **errors, "decode_time_s": decode_time_s}
        return stats, payload, meta

    zdpd_stats = _normalize_stats(_artifact_stats("ms1", file_name, "zdpd_baseline"))
    if zdpd_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="zdpd_baseline_reuse",
            fn=lambda: zdpd_stats,
            log_prefix=f"[FULL8][MS1] zdpd reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
        )
    else:
        (zdpd_stats, zdpd_payload, zdpd_meta), _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="zdpd_baseline",
            fn=_encode_decode_zdpd,
            log_prefix=f"[FULL8][MS1] zdpd done {file_name}",
            n_scans=len(ms1_scans),
        )
        _save_pickle_artifact("ms1", file_name, "zdpd_baseline", zdpd_payload, meta=zdpd_meta, stats=zdpd_stats)

    def _encode_decode_stack():
        payload, meta = stack_zdpd_baseline_encode(
            ms1_scans,
            mz_precision=6,
            stack_size=256,
            num_threads=baseline_threads,
        )
        decoded_scans, decode_time_s = stack_zdpd_baseline_decode(payload)
        errors = compare_roundtrip_scans(ms1_scans, decoded_scans) if compare_baseline_errors else {
            "max_abs_mz_error": None,
            "max_abs_intensity_error": None,
            "median_mz_ppm_p95": None,
            "median_int_rel_p95": None,
        }
        stats = {**meta, **errors, "decode_time_s": decode_time_s}
        return stats, payload, meta

    stack_stats = _normalize_stats(_artifact_stats("ms1", file_name, "stack_zdpd_baseline"))
    if stack_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="stack_zdpd_baseline_reuse",
            fn=lambda: stack_stats,
            log_prefix=f"[FULL8][MS1] stack-zdpd reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
        )
    else:
        (stack_stats, stack_payload, stack_meta), _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="stack_zdpd_baseline",
            fn=_encode_decode_stack,
            log_prefix=f"[FULL8][MS1] stack-zdpd done {file_name}",
            n_scans=len(ms1_scans),
        )
        _save_pickle_artifact("ms1", file_name, "stack_zdpd_baseline", stack_payload, meta=stack_meta, stats=stack_stats)

    ours_eq_stats = _normalize_stats(_artifact_stats("ms1", file_name, "ours_eqfidelity"))
    ours_archive_auto_stats = _normalize_stats(_artifact_stats("ms1", file_name, "ours_archive_fidelity_auto"))
    ours_strict_stats = _normalize_stats(_artifact_stats("ms1", file_name, "ours_strict_q6"))
    need_tracks = ours_eq_stats is None or ours_strict_stats is None
    tracks = None
    if need_tracks:
        tracks = _load_cache_pickle(file_name, MS1_TRACK_CACHE_KEY)
        if tracks is not None and not isinstance(tracks, CompactIslandTracks):
            _emit_log(
                file_name,
                f"[FULL8][MS1] ignoring incompatible non-compact tracks cache for {file_name}",
            )
            tracks = None
        if tracks is not None:
            t0 = time.perf_counter()
            with MemoryMonitor() as monitor:
                _ = len(tracks)
            elapsed_s = time.perf_counter() - t0
            row = _record_stage_metric(
                stage_rows,
                file_name=file_name,
                section="ms1",
                stage="tracks_cache_load",
                elapsed_s=elapsed_s,
                peak_rss_bytes=monitor.peak_rss_bytes,
                peak_memory_pct=monitor.peak_memory_pct,
                n_scans=len(ms1_scans),
                n_tracks=len(tracks),
                n_islands=int(tracks.island_count),
                reused_cache=True,
                track_repr="compact",
            )
            _emit_log(
                file_name,
                f"[FULL8][MS1] tracks cache-hit {file_name} elapsed_s={elapsed_s:.3f} "
                f"peak_rss_gb={row['peak_rss_gb']:.3f} peak_mem_pct={row['peak_memory_pct']:.2f} "
                f"n_tracks={len(tracks)} n_islands={tracks.island_count}",
            )
        else:
            tracks, tracks_row = _run_stage(
                stage_rows,
                file_name=file_name,
                section="ms1",
                stage="tracks_built",
                fn=lambda: _build_tracks_from_scans(ms1_scans),
                log_prefix=f"[FULL8][MS1] tracks built {file_name}",
                n_scans=len(ms1_scans),
            )
            _save_cache_pickle(
                file_name,
                MS1_TRACK_CACHE_KEY,
                tracks,
                meta={
                    "n_tracks": len(tracks),
                    "n_islands": int(tracks.island_count),
                    "n_scans": len(ms1_scans),
                    "raw_ms1_bytes": raw_ms1_bytes,
                    "track_repr": "compact",
                },
            )
            _emit_log(
                file_name,
                f"[FULL8][MS1] tracks detail {file_name} n_tracks={len(tracks)} "
                f"n_islands={tracks.island_count} track_repr=compact",
            )
            tracks_row["n_tracks"] = len(tracks)
            tracks_row["n_islands"] = int(tracks.island_count)
            tracks_row["track_repr"] = "compact"

    def _encode_decode_ours_eq():
        return _benchmark_ms1_ours(
            ms1_scans,
            OUR_MODE,
            tracks=tracks,
            return_artifact=True,
            validate_roundtrip=validate_ours,
            file_name=file_name,
            mode_name="ours_eqfidelity",
        )

    if ours_eq_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="ours_eqfidelity_reuse",
            fn=lambda: ours_eq_stats,
            log_prefix=f"[FULL8][MS1] ours-eq reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
            n_tracks=len(tracks) if tracks is not None else None,
        )
    else:
        (ours_eq_stats, ours_eq_payload, ours_eq_meta), _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="ours_eqfidelity",
            fn=_encode_decode_ours_eq,
            log_prefix=f"[FULL8][MS1] ours-eq done {file_name}",
            n_scans=len(ms1_scans),
            n_tracks=len(tracks),
        )
        ours_eq_stats = _canonicalize_method_stats(ours_eq_stats, raw_bytes=raw_ms1_bytes)
        _save_bytes_artifact("ms1", file_name, "ours_eqfidelity", ours_eq_payload, meta=ours_eq_meta, stats=ours_eq_stats)

    worker_cfg = {
        "cpu_budget": int(max(MS1_ISLAND_WORKERS, MS1_ENCODE_SECTION_WORKERS)),
        "track_prepare_workers": int(MS1_ISLAND_WORKERS),
        "encode_section_workers": int(MS1_ENCODE_SECTION_WORKERS),
        "reason": "full8_section_defaults",
    }
    if ours_archive_auto_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="ours_archive_fidelity_auto_reuse",
            fn=lambda: ours_archive_auto_stats,
            log_prefix=f"[FULL8][MS1] archive-auto reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
        )
    else:
        ours_archive_auto_stats, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="ours_archive_fidelity_auto",
            fn=lambda: _encode_compare_ms1_archive_fidelity_auto_variant(
                file_path=file_path,
                ms1_scans=ms1_scans,
                worker_cfg=worker_cfg,
            ),
            log_prefix=f"[FULL8][MS1] archive-auto done {file_name}",
            n_scans=len(ms1_scans),
        )
        ours_archive_auto_stats = _canonicalize_method_stats(ours_archive_auto_stats, raw_bytes=raw_ms1_bytes)
        _save_bytes_artifact(
            "ms1",
            file_name,
            "ours_archive_fidelity_auto",
            b"",
            meta=ours_archive_auto_stats.get("ms1_payload_mode", {}),
            stats=ours_archive_auto_stats,
        )

    def _encode_decode_ours_strict():
        return _benchmark_ms1_ours(
            ms1_scans,
            STRICT_MS1_MODE,
            tracks=tracks,
            return_artifact=True,
            validate_roundtrip=validate_ours,
            file_name=file_name,
            mode_name="ours_strict_q6",
        )

    if ours_strict_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="ours_strict_q6_reuse",
            fn=lambda: ours_strict_stats,
            log_prefix=f"[FULL8][MS1] ours-strict reuse {file_name}",
            reused_artifact=True,
            n_scans=len(ms1_scans),
            n_tracks=len(tracks) if tracks is not None else None,
        )
    else:
        (ours_strict_stats, ours_strict_payload, ours_strict_meta), _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms1",
            stage="ours_strict_q6",
            fn=_encode_decode_ours_strict,
            log_prefix=f"[FULL8][MS1] ours-strict done {file_name}",
            n_scans=len(ms1_scans),
            n_tracks=len(tracks),
        )
        ours_strict_stats = _canonicalize_method_stats(ours_strict_stats, raw_bytes=raw_ms1_bytes)
        _save_bytes_artifact("ms1", file_name, "ours_strict_q6", ours_strict_payload, meta=ours_strict_meta, stats=ours_strict_stats)
    _emit_log(file_name, f"[FULL8][MS1] done {file_name}")
    return {
        "file": file_name,
        "raw_bytes": raw["raw_bytes"],
        "methods": {
            "raw": {**raw},
            "gzip": gzip_stats,
            "zlib": zlib_stats,
            "zstd-9": zstd_stats,
            "zdpd_baseline": zdpd_stats,
            "stack_zdpd_baseline": stack_stats,
            "ours_eqfidelity": ours_eq_stats,
            "ours_archive_fidelity_auto": ours_archive_auto_stats,
            "ours_strict_q6": ours_strict_stats,
        },
    }


def _benchmark_ms2_from_loaded(
    file_path: Path,
    loaded: dict,
    stage_rows: list[dict],
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
    validate_ours: bool = True,
):
    file_name = file_path.name
    _emit_log(file_name, f"[FULL8][MS2] start {file_name}")
    if loaded["file_type"] == "DIA":
        ms2_source = loaded.get("ms2_window_items")
        if ms2_source is None:
            ms2_source = loaded.get("ms2_by_window") or {}
        window_items = _sorted_dia_window_items(ms2_source)
        flat_scans = []
        for _, scans in window_items:
            flat_scans.extend(scans)
        raw_ms2_bytes = int(loaded["raw_ms2_bytes"])
        file_type = "DIA"
    else:
        window_items = []
        flat_scans = loaded["ms2_scans"] or []
        raw_ms2_bytes = int(loaded["raw_ms2_bytes"])
        file_type = "DDA"
    raw = {"raw_bytes": raw_ms2_bytes, "compressed_bytes": raw_ms2_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}
    raw_payload_holder = {"payload": None}

    def _raw_payload() -> bytes:
        if raw_payload_holder["payload"] is None:
            raw_payload_holder["payload"] = float64_payload(flat_scans)[0]
        return raw_payload_holder["payload"]

    def _normalize_stats(stats: dict | None) -> dict | None:
        return _canonicalize_method_stats(stats, raw_bytes=raw_ms2_bytes, file_type=file_type)

    gzip_stats = _normalize_stats(_artifact_stats("ms2", file_name, "gzip"))
    if gzip_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="gzip_reuse",
            fn=lambda: gzip_stats,
            log_prefix=f"[FULL8][MS2] gzip reuse {file_name}",
            reused_artifact=True,
            n_scans=len(flat_scans),
            file_type=file_type,
        )
    else:
        gzip_t0 = time.perf_counter()
        gzip_stats = gzip_raw_payload(flat_scans)
        gzip_encode = time.perf_counter() - gzip_t0
        gzip_payload = gzip.compress(_raw_payload())
        dt0 = time.perf_counter()
        gzip.decompress(gzip_payload)
        gzip_decode = time.perf_counter() - dt0
        gzip_stats = {
            **gzip_stats,
            "encode_time_s": gzip_encode,
            "decode_time_s": gzip_decode,
            "raw_bytes": raw_ms2_bytes,
            "compressed_bytes": len(gzip_payload),
            "file_type": file_type,
        }
        _save_bytes_artifact(
            "ms2",
            file_name,
            "gzip",
            gzip_payload,
            meta={"raw_bytes": raw_ms2_bytes, "compressed_bytes": len(gzip_payload), "compression_ratio": gzip_stats["compression_ratio"], "file_type": file_type},
            stats=gzip_stats,
        )

    zlib_stats = _normalize_stats(_artifact_stats("ms2", file_name, "zlib"))
    if zlib_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="zlib_reuse",
            fn=lambda: zlib_stats,
            log_prefix=f"[FULL8][MS2] zlib reuse {file_name}",
            reused_artifact=True,
            n_scans=len(flat_scans),
            file_type=file_type,
        )
    else:
        zlib_t0 = time.perf_counter()
        zlib_stats = raw_backend_payload(flat_scans, "zlib")
        zlib_encode = time.perf_counter() - zlib_t0
        zlib_payload = backend_compress(_raw_payload(), "zlib")
        dt0 = time.perf_counter()
        backend_decompress(zlib_payload, "zlib")
        zlib_decode = time.perf_counter() - dt0
        zlib_stats = {
            **zlib_stats,
            "encode_time_s": zlib_encode,
            "decode_time_s": zlib_decode,
            "raw_bytes": raw_ms2_bytes,
            "compressed_bytes": len(zlib_payload),
            "file_type": file_type,
        }
        _save_bytes_artifact(
            "ms2",
            file_name,
            "zlib",
            zlib_payload,
            meta={"raw_bytes": raw_ms2_bytes, "compressed_bytes": len(zlib_payload), "compression_ratio": zlib_stats["compression_ratio"], "file_type": file_type},
            stats=zlib_stats,
        )

    zstd_stats = _normalize_stats(_artifact_stats("ms2", file_name, "zstd-9"))
    if zstd_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="zstd_reuse",
            fn=lambda: zstd_stats,
            log_prefix=f"[FULL8][MS2] zstd reuse {file_name}",
            reused_artifact=True,
            n_scans=len(flat_scans),
            file_type=file_type,
        )
    else:
        zstd_t0 = time.perf_counter()
        zstd_stats = raw_backend_payload(flat_scans, "zstd-9")
        zstd_encode = time.perf_counter() - zstd_t0
        zstd_payload = backend_compress(_raw_payload(), "zstd-9")
        dt0 = time.perf_counter()
        backend_decompress(zstd_payload, "zstd-9")
        zstd_decode = time.perf_counter() - dt0
        zstd_stats = {
            **zstd_stats,
            "encode_time_s": zstd_encode,
            "decode_time_s": zstd_decode,
            "raw_bytes": raw_ms2_bytes,
            "compressed_bytes": len(zstd_payload),
            "file_type": file_type,
        }
        _save_bytes_artifact(
            "ms2",
            file_name,
            "zstd-9",
            zstd_payload,
            meta={"raw_bytes": raw_ms2_bytes, "compressed_bytes": len(zstd_payload), "compression_ratio": zstd_stats["compression_ratio"], "file_type": file_type},
            stats=zstd_stats,
        )

    def _encode_decode_zdpd():
        payload, meta = zdpd_baseline_encode(
            flat_scans,
            mz_precision=6,
            num_threads=baseline_threads,
        )
        decoded_scans, decode_time_s = zdpd_baseline_decode(payload)
        errors = compare_centroid_scans(flat_scans, decoded_scans) if compare_baseline_errors else {
            "max_abs_mz_error": None,
            "max_abs_intensity_error": None,
            "median_mz_ppm_p95": None,
            "median_int_rel_p95": None,
        }
        stats = {**meta, **errors, "decode_time_s": decode_time_s}
        return stats, payload, meta

    zdpd_stats = _normalize_stats(_artifact_stats("ms2", file_name, "zdpd_baseline"))
    if zdpd_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="zdpd_baseline_reuse",
            fn=lambda: zdpd_stats,
            log_prefix=f"[FULL8][MS2] zdpd reuse {file_name}",
            reused_artifact=True,
            n_scans=len(flat_scans),
            file_type=file_type,
        )
    else:
        (zdpd_stats, zdpd_payload, zdpd_meta), _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="zdpd_baseline",
            fn=_encode_decode_zdpd,
            log_prefix=f"[FULL8][MS2] zdpd done {file_name}",
            n_scans=len(flat_scans),
            file_type=file_type,
        )
        _save_pickle_artifact("ms2", file_name, "zdpd_baseline", zdpd_payload, meta=zdpd_meta, manifest_extra={"file_type": file_type}, stats=zdpd_stats)

    def _encode_decode_stack():
        payload, meta = stack_zdpd_baseline_encode(
            flat_scans,
            mz_precision=6,
            stack_size=256,
            num_threads=baseline_threads,
        )
        decoded_scans, decode_time_s = stack_zdpd_baseline_decode(payload)
        errors = compare_centroid_scans(flat_scans, decoded_scans) if compare_baseline_errors else {
            "max_abs_mz_error": None,
            "max_abs_intensity_error": None,
            "median_mz_ppm_p95": None,
            "median_int_rel_p95": None,
        }
        stats = {**meta, **errors, "decode_time_s": decode_time_s}
        return stats, payload, meta

    stack_stats = _normalize_stats(_artifact_stats("ms2", file_name, "stack_zdpd_baseline"))
    if stack_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="stack_zdpd_baseline_reuse",
            fn=lambda: stack_stats,
            log_prefix=f"[FULL8][MS2] stack-zdpd reuse {file_name}",
            reused_artifact=True,
            n_scans=len(flat_scans),
            file_type=file_type,
        )
    else:
        (stack_stats, stack_payload, stack_meta), _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="stack_zdpd_baseline",
            fn=_encode_decode_stack,
            log_prefix=f"[FULL8][MS2] stack-zdpd done {file_name}",
            n_scans=len(flat_scans),
            file_type=file_type,
        )
        _save_pickle_artifact("ms2", file_name, "stack_zdpd_baseline", stack_payload, meta=stack_meta, manifest_extra={"file_type": file_type}, stats=stack_stats)

    ours_stats = _normalize_stats(_artifact_stats("ms2", file_name, "ours_eqfidelity"))
    if ours_stats is not None:
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="ms2",
            stage="ours_eqfidelity_reuse",
            fn=lambda: ours_stats,
            log_prefix=f"[FULL8][MS2] ours reuse {file_name}",
            reused_artifact=True,
            n_scans=len(flat_scans),
            file_type=file_type,
        )
    else:
        if file_type == "DIA":
            codec = _ours_ms2_codec_for_file(file_path)
            ours, ours_encode_row = _run_stage(
                stage_rows,
                file_name=file_name,
                section="ms2",
                stage="ours_eqfidelity",
                fn=lambda: codec.encode_dia_windows(
                    window_items,
                    progress_callback=_make_ms2_codec_progress_callback(file_name, "ours_eqfidelity", file_type),
                ),
                log_prefix=f"[FULL8][MS2] ours-encode done {file_name}",
                n_scans=len(flat_scans),
                window_count=len(window_items),
                file_type=file_type,
            )
            segment_sizes = _collect_ms2_segment_sizes(ours)
            if validate_ours:
                decoded, ours_decode_row = _run_stage(
                    stage_rows,
                    file_name=file_name,
                    section="ms2",
                    stage="ours_decode",
                    fn=lambda: codec.decode_dia_windows(ours),
                    log_prefix=f"[FULL8][MS2] ours-decode done {file_name}",
                    n_scans=len(flat_scans),
                    window_count=len(window_items),
                    file_type=file_type,
                )
                compare_rows = []
                for key, scans in window_items:
                    compare_rows.append(compare_centroid_scans(scans, decoded[key]))
            else:
                ours_decode_row = {"elapsed_s": 0.0}
                compare_rows = []
        else:
            codec = _ours_ms2_codec_for_file(file_path)
            ours, ours_encode_row = _run_stage(
                stage_rows,
                file_name=file_name,
                section="ms2",
                stage="ours_eqfidelity",
                fn=lambda: codec.encode_dda_blocks(
                    flat_scans,
                    progress_callback=_make_ms2_codec_progress_callback(file_name, "ours_eqfidelity", file_type),
                ),
                log_prefix=f"[FULL8][MS2] ours-encode done {file_name}",
                n_scans=len(flat_scans),
                file_type=file_type,
            )
            segment_sizes = _collect_ms2_segment_sizes(ours)
            if validate_ours:
                decoded, ours_decode_row = _run_stage(
                    stage_rows,
                    file_name=file_name,
                    section="ms2",
                    stage="ours_decode",
                    fn=lambda: codec.decode_dda_blocks(ours),
                    log_prefix=f"[FULL8][MS2] ours-decode done {file_name}",
                    n_scans=len(flat_scans),
                    file_type=file_type,
                )
                compare_rows = [compare_centroid_scans(flat_scans, decoded)]
            else:
                ours_decode_row = {"elapsed_s": 0.0}
                compare_rows = []
        max_abs_int = max((row["max_abs_intensity_error"] for row in compare_rows), default=None)
        max_abs_mz = max((row["max_abs_mz_error"] for row in compare_rows), default=None)
        median_mz_ppm_p95 = statistics.median([row["median_mz_ppm_p95"] for row in compare_rows]) if compare_rows else None
        median_int_rel_p95 = statistics.median([row["median_int_rel_p95"] for row in compare_rows]) if compare_rows else None
        ours_stats = {
            "raw_bytes": ours["raw_bytes"],
            "compressed_bytes": ours["compressed_bytes"],
            "compression_ratio": ours["compression_ratio"],
            "encode_time_s": ours_encode_row["elapsed_s"],
            "decode_time_s": ours_decode_row["elapsed_s"],
            "max_abs_intensity_error": max_abs_int,
            "max_abs_mz_error": max_abs_mz,
            "median_mz_ppm_p95": median_mz_ppm_p95,
            "median_int_rel_p95": median_int_rel_p95,
            "segment_sizes": dict(segment_sizes),
            "file_type": file_type,
            "roundtrip_validated": bool(validate_ours),
        }
        ours_stats = _canonicalize_method_stats(ours_stats, raw_bytes=raw_ms2_bytes, file_type=file_type)
        _save_pickle_artifact("ms2", file_name, "ours_eqfidelity", ours, manifest_extra={"file_type": file_type}, stats=ours_stats)
    _emit_log(file_name, f"[FULL8][MS2] done {file_name}")
    return {
        "file": file_name,
        "raw_bytes": raw["raw_bytes"],
        "methods": {
            "raw": raw,
            "gzip": gzip_stats,
            "zlib": zlib_stats,
            "zstd-9": zstd_stats,
            "zdpd_baseline": zdpd_stats,
            "stack_zdpd_baseline": stack_stats,
            "ours_eqfidelity": ours_stats,
        },
    }


def benchmark_metadata_file_with_metrics(file_path_str: str, stage_rows: list[dict] | None = None):
    file_path = Path(file_path_str)
    file_name = file_path.name
    stage_rows = stage_rows if stage_rows is not None else []
    _emit_log(file_name, f"[FULL8][META] start {file_name}")
    metadata_codec = MzMLMetadataCodec("zstd-9")
    cached_gzip = _artifact_stats("metadata", file_name, "gzip")
    cached_zlib = _artifact_stats("metadata", file_name, "zlib")
    cached_zstd = _artifact_stats("metadata", file_name, "zstd-9")
    cached_ours = _artifact_stats("metadata", file_name, "ours_metadata")
    raw_blob = None
    raw_bytes = None
    if all(item is not None for item in (cached_gzip, cached_zlib, cached_zstd, cached_ours)):
        raw_bytes = int(cached_ours.get("raw_bytes") or cached_gzip.get("raw_bytes") or 0)
        _, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="metadata",
            stage="artifact_reuse",
            fn=lambda: {"raw_bytes": raw_bytes},
            log_prefix=f"[FULL8][META] artifact reuse {file_name}",
            reused_artifact=True,
        )
    else:
        raw_blob, _ = _run_stage(
            stage_rows,
            file_name=file_name,
            section="metadata",
            stage="extract_binary_stripped_xml",
            fn=lambda: extract_binary_stripped_metadata_xml(file_path),
            log_prefix=f"[FULL8][META] extract done {file_name}",
        )
        raw_bytes = len(raw_blob)

    def _backend_stats(backend_name: str):
        t0 = time.perf_counter()
        if backend_name == "gzip":
            comp = gzip.compress(raw_blob)
            encode_time = time.perf_counter() - t0
            dt0 = time.perf_counter()
            gzip.decompress(comp)
            decode_time = time.perf_counter() - dt0
        else:
            comp = backend_compress(raw_blob, backend_name)
            encode_time = time.perf_counter() - t0
            dt0 = time.perf_counter()
            backend_decompress(comp, backend_name)
            decode_time = time.perf_counter() - dt0
        return {
            "raw_bytes": raw_bytes,
            "compressed_bytes": len(comp),
            "compression_ratio": raw_bytes / len(comp) if len(comp) else 0.0,
            "encode_time_s": encode_time,
            "decode_time_s": decode_time,
        }

    def _encode_decode_metadata():
        ours_t0 = time.perf_counter()
        payload, meta = metadata_codec.encode_file(file_path)
        ours_encode = time.perf_counter() - ours_t0
        dt0 = time.perf_counter()
        metadata_codec.decode_to_bytes(payload, meta)
        ours_decode = time.perf_counter() - dt0
        return payload, meta, ours_encode, ours_decode

    if cached_gzip is not None:
        gzip_stats = dict(cached_gzip)
    else:
        gzip_stats = _backend_stats("gzip")
        _save_bytes_artifact(
            "metadata",
            file_name,
            "gzip",
            gzip.compress(raw_blob),
            meta={"raw_bytes": raw_bytes, "compressed_bytes": gzip_stats["compressed_bytes"], "compression_ratio": gzip_stats["compression_ratio"]},
            stats=gzip_stats,
        )
    if cached_zlib is not None:
        zlib_stats = dict(cached_zlib)
    else:
        zlib_payload = backend_compress(raw_blob, "zlib")
        zlib_stats = _backend_stats("zlib")
        _save_bytes_artifact(
            "metadata",
            file_name,
            "zlib",
            zlib_payload,
            meta={"raw_bytes": raw_bytes, "compressed_bytes": len(zlib_payload), "compression_ratio": zlib_stats["compression_ratio"]},
            stats=zlib_stats,
        )
    if cached_zstd is not None:
        zstd_stats = dict(cached_zstd)
    else:
        zstd_payload = backend_compress(raw_blob, "zstd-9")
        zstd_stats = _backend_stats("zstd-9")
        _save_bytes_artifact(
            "metadata",
            file_name,
            "zstd-9",
            zstd_payload,
            meta={"raw_bytes": raw_bytes, "compressed_bytes": len(zstd_payload), "compression_ratio": zstd_stats["compression_ratio"]},
            stats=zstd_stats,
        )
    if cached_ours is not None:
        ours_method_stats = dict(cached_ours)
    else:
        (ours_result, meta_row) = _run_stage(
            stage_rows,
            file_name=file_name,
            section="metadata",
            stage="ours_metadata",
            fn=_encode_decode_metadata,
            log_prefix=f"[FULL8][META] ours done {file_name}",
        )
        ours_payload, ours_meta, ours_encode, ours_decode = ours_result
        meta_row["encode_time_s"] = ours_encode
        meta_row["decode_time_s"] = ours_decode
        ours_method_stats = {
            "raw_bytes": ours_meta["raw_bytes"],
            "compressed_bytes": ours_meta["compressed_bytes"],
            "compression_ratio": ours_meta["compression_ratio"],
            "encode_time_s": ours_encode,
            "decode_time_s": ours_decode,
        }
        _save_bytes_artifact("metadata", file_name, "ours_metadata", ours_payload, meta=ours_meta, stats=ours_method_stats)
    out = {
        "file": file_name,
        "raw_bytes": raw_bytes,
        "methods": {
            "raw": {"raw_bytes": raw_bytes, "compressed_bytes": raw_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0},
            "gzip": gzip_stats,
            "zlib": zlib_stats,
            "zstd-9": zstd_stats,
            "ours_metadata": ours_method_stats,
        },
    }
    _emit_log(file_name, f"[FULL8][META] done {file_name}")
    return out


def _decide_bundle_parallel_mode(
    file_path: Path,
    loaded: dict | None,
    *,
    need_ms1: bool,
    need_ms2: bool,
) -> dict:
    if not BUNDLE_PARALLEL_SECTIONS:
        return {
            "enabled": False,
            "reason": "disabled_by_config",
            "file_jobs": int(SECTION_FILE_JOBS),
        }
    if int(SECTION_FILE_JOBS) > 1:
        return {
            "enabled": False,
            "reason": "disabled_when_file_jobs_gt_1",
            "file_jobs": int(SECTION_FILE_JOBS),
        }
    if not need_ms1 or not need_ms2:
        return {
            "enabled": False,
            "reason": "requires_ms1_and_ms2",
            "file_jobs": int(SECTION_FILE_JOBS),
        }
    if loaded is None:
        return {
            "enabled": False,
            "reason": "missing_loaded_sections",
            "file_jobs": int(SECTION_FILE_JOBS),
        }
    if not _can_use_fork_process_parallelism():
        return {
            "enabled": False,
            "reason": "fork_unavailable",
            "file_jobs": int(SECTION_FILE_JOBS),
        }
    decision = recommend_parallel_section_processes(
        raw_payload_bytes=int(loaded.get("raw_ms1_bytes", 0)) + int(loaded.get("raw_ms2_bytes", 0)),
        input_file_bytes=int(file_path.stat().st_size),
        min_available_bytes=int(BUNDLE_PARALLEL_MIN_AVAILABLE_BYTES),
        working_set_multiplier=float(BUNDLE_PARALLEL_WORKING_SET_MULTIPLIER),
        fixed_overhead_bytes=int(BUNDLE_PARALLEL_FIXED_OVERHEAD_BYTES),
        headroom_ratio=float(BUNDLE_PARALLEL_HEADROOM_RATIO),
    )
    decision["file_jobs"] = int(SECTION_FILE_JOBS)
    return decision


def _bundle_section_worker(
    section_name: str,
    file_path_str: str,
    loaded: dict | None,
    baseline_threads: int,
    compare_baseline_errors: bool,
    validate_ours_roundtrip: bool,
    result_path_str: str,
    error_path_str: str,
) -> None:
    file_path = Path(file_path_str)
    stage_rows: list[dict] = []
    try:
        if section_name == "ms1":
            result = _benchmark_ms1_from_loaded(
                file_path,
                loaded,
                stage_rows,
                baseline_threads,
                compare_baseline_errors,
                validate_ours_roundtrip,
            )
        elif section_name == "ms2":
            result = _benchmark_ms2_from_loaded(
                file_path,
                loaded,
                stage_rows,
                baseline_threads,
                compare_baseline_errors,
                validate_ours_roundtrip,
            )
        elif section_name == "metadata":
            result = benchmark_metadata_file_with_metrics(file_path_str, stage_rows=stage_rows)
        else:
            raise ValueError(f"Unsupported section worker: {section_name}")
        _write_json(
            Path(result_path_str),
            {
                "section": section_name,
                "result": result,
                "stage_rows": stage_rows,
            },
        )
    except Exception:
        _write_json(
            Path(error_path_str),
            {
                "section": section_name,
                "traceback": traceback.format_exc(),
            },
        )
        raise


def _benchmark_file_bundle_parallel(
    file_path: Path,
    loaded: dict,
    stage_rows: list[dict],
    *,
    need_ms1: bool,
    need_ms2: bool,
    need_metadata: bool,
    baseline_threads: int,
    compare_baseline_errors: bool,
    validate_ours_roundtrip: bool,
    parallel_decision: dict,
) -> dict:
    ctx = mp.get_context("fork")
    bundle_t0 = time.perf_counter()
    _emit_log(
        file_path.name,
        "[FULL8][BUNDLE] parallel enabled "
        f"{file_path.name} available_gb={float(parallel_decision.get('available_memory_bytes', 0)) / (1024.0 ** 3):.2f} "
        f"required_gb={float(parallel_decision.get('required_available_bytes', 0)) / (1024.0 ** 3):.2f} "
        f"est_peak_gb={float(parallel_decision.get('estimated_parallel_peak_bytes', 0)) / (1024.0 ** 3):.2f}",
    )
    workers: list[tuple[str, mp.Process, Path, Path]] = []
    metadata_thread = None
    metadata_box: dict[str, object] = {"result": None, "stage_rows": [], "error": None}

    def _metadata_runner() -> None:
        local_stage_rows: list[dict] = []
        try:
            result = benchmark_metadata_file_with_metrics(str(file_path), stage_rows=local_stage_rows)
            metadata_box["result"] = result
            metadata_box["stage_rows"] = local_stage_rows
        except Exception:
            metadata_box["error"] = traceback.format_exc()

    with tempfile.TemporaryDirectory(prefix="trackcodec_section_bundle_parallel_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        if need_ms1:
            result_path = tmp_dir / "ms1_result.json"
            error_path = tmp_dir / "ms1_error.json"
            proc = ctx.Process(
                target=_bundle_section_worker,
                args=(
                    "ms1",
                    str(file_path),
                    loaded,
                    int(baseline_threads),
                    bool(compare_baseline_errors),
                    bool(validate_ours_roundtrip),
                    str(result_path),
                    str(error_path),
                ),
            )
            proc.start()
            workers.append(("ms1", proc, result_path, error_path))
        if need_ms2:
            result_path = tmp_dir / "ms2_result.json"
            error_path = tmp_dir / "ms2_error.json"
            proc = ctx.Process(
                target=_bundle_section_worker,
                args=(
                    "ms2",
                    str(file_path),
                    loaded,
                    int(baseline_threads),
                    bool(compare_baseline_errors),
                    bool(validate_ours_roundtrip),
                    str(result_path),
                    str(error_path),
                ),
            )
            proc.start()
            workers.append(("ms2", proc, result_path, error_path))
        if need_metadata:
            metadata_thread = threading.Thread(
                target=_metadata_runner,
                name=f"metadata-{file_path.name}",
                daemon=True,
            )
            metadata_thread.start()

        try:
            for _, proc, _, _ in workers:
                proc.join()
            if metadata_thread is not None:
                metadata_thread.join()
        finally:
            for _, proc, _, _ in workers:
                if proc.is_alive():
                    proc.terminate()
                proc.join(timeout=5)

        bundle = {
            "file": file_path.name,
            "ms1": None,
            "ms2": None,
            "metadata": None,
            "stage_rows": list(stage_rows),
            "bundle_parallel_enabled": True,
            "bundle_parallel_decision": dict(parallel_decision),
            "wall_clock_elapsed_s": float(time.perf_counter() - bundle_t0),
        }
        for section_name, proc, result_path, error_path in workers:
            if proc.exitcode != 0:
                detail = _read_json(error_path) or {}
                trace = detail.get("traceback", f"unknown {section_name} worker error")
                raise RuntimeError(f"[FULL8][BUNDLE] {section_name} worker failed for {file_path.name}:\n{trace}")
            payload = _read_json(result_path)
            if payload is None:
                raise RuntimeError(f"[FULL8][BUNDLE] missing {section_name} worker result for {file_path.name}")
            bundle["stage_rows"].extend(payload.get("stage_rows", []))
            bundle[section_name] = payload.get("result")
        if need_metadata:
            if metadata_box["error"] is not None:
                raise RuntimeError(f"[FULL8][BUNDLE] metadata worker failed for {file_path.name}:\n{metadata_box['error']}")
            bundle["stage_rows"].extend(metadata_box.get("stage_rows", []))
            bundle["metadata"] = metadata_box.get("result")
    _emit_log(
        file_path.name,
        f"[FULL8][BUNDLE] parallel done {file_path.name} wall_clock_s={bundle['wall_clock_elapsed_s']:.3f}",
    )
    return bundle


def benchmark_file_bundle(
    file_path_str: str,
    need_ms1: bool,
    need_ms2: bool,
    need_metadata: bool,
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
    validate_ours_roundtrip: bool = True,
):
    file_path = Path(file_path_str)
    bundle_t0 = time.perf_counter()
    _emit_log(file_path.name, f"[FULL8][BUNDLE] start {file_path.name} need_ms1={need_ms1} need_ms2={need_ms2} need_metadata={need_metadata}")
    stage_rows: list[dict] = []
    loaded = None
    if need_ms1 or need_ms2:
        loaded = _load_sections_single_pass(
            file_path,
            need_ms1=need_ms1,
            need_ms2=need_ms2,
            stage_rows=stage_rows,
        )
    parallel_decision = _decide_bundle_parallel_mode(
        file_path,
        loaded,
        need_ms1=need_ms1,
        need_ms2=need_ms2,
    )
    _append_progress_row(
        file_path.name,
        {
            "file": file_path.name,
            "section": "bundle",
            "stage": "parallel_decision",
            "enabled": bool(parallel_decision.get("enabled", False)),
            "reason": str(parallel_decision.get("reason", "")),
            "available_memory_bytes": int(parallel_decision.get("available_memory_bytes", 0) or 0),
            "required_available_bytes": int(parallel_decision.get("required_available_bytes", 0) or 0),
            "estimated_parallel_peak_bytes": int(parallel_decision.get("estimated_parallel_peak_bytes", 0) or 0),
            "file_jobs": int(parallel_decision.get("file_jobs", SECTION_FILE_JOBS)),
        },
    )
    if parallel_decision.get("enabled", False):
        bundle = _benchmark_file_bundle_parallel(
            file_path,
            loaded,
            stage_rows,
            need_ms1=need_ms1,
            need_ms2=need_ms2,
            need_metadata=need_metadata,
            baseline_threads=baseline_threads,
            compare_baseline_errors=compare_baseline_errors,
            validate_ours_roundtrip=validate_ours_roundtrip,
            parallel_decision=parallel_decision,
        )
    else:
        _emit_log(
            file_path.name,
            f"[FULL8][BUNDLE] parallel disabled {file_path.name} reason={parallel_decision.get('reason','')}",
        )
        bundle = {
            "file": file_path.name,
            "ms1": _benchmark_ms1_from_loaded(
                file_path,
                loaded,
                stage_rows,
                baseline_threads,
                compare_baseline_errors,
                validate_ours_roundtrip,
            )
            if need_ms1
            else None,
            "ms2": _benchmark_ms2_from_loaded(
                file_path,
                loaded,
                stage_rows,
                baseline_threads,
                compare_baseline_errors,
                validate_ours_roundtrip,
            )
            if need_ms2
            else None,
            "metadata": benchmark_metadata_file_with_metrics(str(file_path), stage_rows=stage_rows) if need_metadata else None,
            "stage_rows": stage_rows,
            "bundle_parallel_enabled": False,
            "bundle_parallel_decision": dict(parallel_decision),
            "wall_clock_elapsed_s": float(time.perf_counter() - bundle_t0),
        }
    if "wall_clock_elapsed_s" not in bundle:
        bundle["wall_clock_elapsed_s"] = float(time.perf_counter() - bundle_t0)
    _write_file_bundle_summary(bundle)
    _emit_log(file_path.name, f"[FULL8][BUNDLE] done {file_path.name} n_stage_rows={len(bundle.get('stage_rows', []))}")
    return bundle


def _summarize_rows(section_name: str, per_file_map: dict, order: list[str]):
    family_map = {
        "ours_strict_q6": "strict_q6",
        "ours_eqfidelity": "near",
        "ours_archive_fidelity_auto": "production",
    }
    reference_map = {
        "ms1": "stack_zdpd_baseline",
        "ms2": "stack_zdpd_baseline",
        "metadata": None,
        "overall": "stack_zdpd_container",
    }
    section_kind = section_name.split("_")[-1]
    aggregate_map = _aggregate_section(per_file_map, order)
    for label, item in aggregate_map.items():
        item["bar_annotation"] = f"{item['mean_cr']:.2f}x"
    bar_rows = _make_bar_rows(order, aggregate_map, family_map=family_map)
    if section_kind == "ms1":
        bar_rows = _augment_ms1_bar_rows_with_sidecar(bar_rows, per_file_map)
    line_rows = _make_line_rows(order, per_file_map)
    compare_rows = _make_compare_table_rows(section_name, order, aggregate_map, reference_map.get(section_kind))

    _write_csv(bar_rows, PLOT_DATA_DIR / f"{section_name}_compression_bar_data.csv")
    _write_csv(line_rows, PLOT_DATA_DIR / f"{section_name}_compression_line_data.csv")
    _write_csv(bar_rows, COMP_DIR / f"{section_name}_aggregate.csv")
    _write_csv(compare_rows, TABLE_DIR / f"{section_name}_compare_table.csv")
    speed_rows = []
    for row in bar_rows:
        speed_rows.append(
            {
                "label": row["label"],
                "display_name": row["display_name"],
                "mean_encode_time_s": row["mean_encode_time_s"],
                "mean_decode_time_s": row["mean_decode_time_s"],
                "color_hex": row["color_hex"],
                "family": row["family"],
            }
        )
    _write_csv(speed_rows, PLOT_DATA_DIR / f"{section_name}_speed_bar_data.csv")

    if section_kind == "ms1":
        _plot_ms1_compression_bar_with_full_scan_gain(
            bar_rows,
            PLOTS_DIR / f"{section_name}_compression_comparison.png",
            title=f"{section_name.replace('_', ' ').upper()} Mean Compression Ratio By Method",
        )
    else:
        plot_compression_bar_custom(
            bar_rows,
            PLOTS_DIR / f"{section_name}_compression_comparison.png",
            title=f"{section_name.replace('_', ' ').upper()} Mean Compression Ratio By Method",
            figsize=(11.5, 8.5),
            annotation_fontsize=14,
        )
    plot_compression_line_custom(
        line_rows,
        PLOTS_DIR / f"{section_name}_compression_line.png",
        title=f"{section_name.replace('_', ' ').upper()} Per-file Compression Ratio By Method",
        figsize=(11.0, 7.8),
        legend_loc="center left",
        legend_bbox=(1.01, 0.5),
        annotation_fontsize=10,
    )
    plot_speed_bar(
        speed_rows,
        PLOTS_DIR / f"{section_name}_speed_bar.png",
        title_encode=f"{section_name.replace('_', ' ').upper()} Mean Encode Time By Method",
        title_decode=f"{section_name.replace('_', ' ').upper()} Mean Decode Time By Method",
    )
    _plot_method_boxplot(
        line_rows,
        order,
        PLOTS_DIR / f"{section_name}_compression_boxplot.png",
        title=f"{section_name.replace('_', ' ').upper()} Compression Ratio Distribution By Method",
    )
    _plot_file_lines_by_method(
        line_rows,
        order,
        PLOTS_DIR / f"{section_name}_per_file_method_line.png",
        title=f"{section_name.replace('_', ' ').upper()} Per-file Compression Ratio Across Methods",
    )
    return bar_rows, line_rows, speed_rows


def _build_ms1_payload_composition(ms1_per_file):
    per_file = []
    total = defaultdict(int)
    for file_name, methods in sorted(ms1_per_file.items()):
        mode = methods["ours_strict_q6"]
        segment_sizes = mode["segment_sizes"]
        compressed_bytes = int(mode["compressed_bytes"])
        mz_bytes = 0
        int_bytes = 0
        sidecar_bytes = 0
        meta_bytes = 0
        other_bytes = 0
        for name, size in segment_sizes.items():
            size = int(size)
            if name in MS1_MZ_SEGMENTS:
                mz_bytes += size
            elif name in MS1_INT_SEGMENTS:
                int_bytes += size
            elif name in FULL_SCAN_SIDECAR_SEGMENTS:
                sidecar_bytes += size
            elif name in MS1_META_SEGMENTS:
                meta_bytes += size
            else:
                other_bytes += size
        meta_total = meta_bytes + other_bytes + max(0, compressed_bytes - (mz_bytes + int_bytes + sidecar_bytes + meta_bytes + other_bytes))
        per_file.append(
            {
                "file": file_name,
                "file_label": _norm_file_label(file_name),
                "compression_ratio": mode["compression_ratio"],
                "compression_ratio_without_full_scan_sidecar": _cr_without_full_scan_sidecar(mode),
                "mz_bytes": mz_bytes,
                "intensity_bytes": int_bytes,
                "full_scan_sidecar_bytes": sidecar_bytes,
                "metadata_bytes": meta_total,
                "compressed_bytes": compressed_bytes,
            }
        )
        total["mz_bytes"] += mz_bytes
        total["intensity_bytes"] += int_bytes
        total["full_scan_sidecar_bytes"] += sidecar_bytes
        total["metadata_bytes"] += meta_total
        total["compressed_bytes"] += compressed_bytes
    return per_file, total


def _build_ms2_payload_composition(ms2_per_file):
    per_file = []
    total = defaultdict(int)
    for file_name, methods in sorted(ms2_per_file.items()):
        ours = methods["ours_eqfidelity"]
        segment_sizes = ours.get("segment_sizes", {})
        mz_bytes, int_bytes, meta_bytes, other_bytes = _categorize_segment_sizes(
            segment_sizes,
            mz_names=MS2_MZ_SEGMENTS,
            int_names=MS2_INT_SEGMENTS,
            meta_names=None,
        )
        compressed_bytes = int(ours["compressed_bytes"])
        meta_total = meta_bytes + other_bytes + max(0, compressed_bytes - (mz_bytes + int_bytes + meta_bytes + other_bytes))
        per_file.append(
            {
                "file": file_name,
                "file_label": _norm_file_label(file_name),
                "compression_ratio": ours["compression_ratio"],
                "mz_bytes": mz_bytes,
                "intensity_bytes": int_bytes,
                "metadata_bytes": meta_total,
                "compressed_bytes": compressed_bytes,
            }
        )
        total["mz_bytes"] += mz_bytes
        total["intensity_bytes"] += int_bytes
        total["metadata_bytes"] += meta_total
        total["compressed_bytes"] += compressed_bytes
    return per_file, total


def _plot_payload_mix(ms1_totals, ms2_totals):
    rows = [
        {
            "section": "MS1",
            "mz_pct": 100.0 * ms1_totals["mz_bytes"] / ms1_totals["compressed_bytes"],
            "intensity_pct": 100.0 * ms1_totals["intensity_bytes"] / ms1_totals["compressed_bytes"],
            "full_scan_sidecar_pct": 100.0 * ms1_totals["full_scan_sidecar_bytes"] / ms1_totals["compressed_bytes"],
            "metadata_pct": 100.0 * ms1_totals["metadata_bytes"] / ms1_totals["compressed_bytes"],
            "mz_bytes": ms1_totals["mz_bytes"],
            "intensity_bytes": ms1_totals["intensity_bytes"],
            "full_scan_sidecar_bytes": ms1_totals["full_scan_sidecar_bytes"],
            "metadata_bytes": ms1_totals["metadata_bytes"],
        },
        {
            "section": "MS2",
            "mz_pct": 100.0 * ms2_totals["mz_bytes"] / ms2_totals["compressed_bytes"],
            "intensity_pct": 100.0 * ms2_totals["intensity_bytes"] / ms2_totals["compressed_bytes"],
            "full_scan_sidecar_pct": 0.0,
            "metadata_pct": 100.0 * ms2_totals["metadata_bytes"] / ms2_totals["compressed_bytes"],
            "mz_bytes": ms2_totals["mz_bytes"],
            "intensity_bytes": ms2_totals["intensity_bytes"],
            "full_scan_sidecar_bytes": 0,
            "metadata_bytes": ms2_totals["metadata_bytes"],
        },
    ]
    _write_csv(rows, PLOT_DATA_DIR / "ms1_ms2_payload_mix_stacked_bar_data.csv")
    fig, ax = plt.subplots(figsize=(7.6, 6.0))
    x = np.arange(len(rows))
    mz_vals = [row["mz_pct"] for row in rows]
    int_vals = [row["intensity_pct"] for row in rows]
    sidecar_vals = [row["full_scan_sidecar_pct"] for row in rows]
    meta_vals = [row["metadata_pct"] for row in rows]
    ax.bar(x, mz_vals, color="#4C78A8", label="m/z")
    ax.bar(x, int_vals, bottom=mz_vals, color="#F58518", label="Intensity")
    bottoms_sidecar = [m + i for m, i in zip(mz_vals, int_vals)]
    ax.bar(x, sidecar_vals, bottom=bottoms_sidecar, color="#E45756", label="Full-scan sidecar")
    bottoms = [m + i + s for m, i, s in zip(mz_vals, int_vals, sidecar_vals)]
    ax.bar(x, meta_vals, bottom=bottoms, color="#54A24B", label="Metadata")
    ax.set_xticks(x)
    ax.set_xticklabels([row["section"] for row in rows], fontsize=12)
    ax.set_ylabel("Share Of Compressed Bytes (%)", fontsize=13)
    ax.set_title("MS1/MS2 Payload Composition", fontsize=16)
    ax.legend(fontsize=10, loc="upper right")
    ax.grid(axis="y", alpha=0.25)
    for idx, row in enumerate(rows):
        if row["section"] == "MS1" and row["full_scan_sidecar_pct"] > 0:
            sidecar_mb = float(row["full_scan_sidecar_bytes"]) / (1024.0 ** 2)
            ax.text(
                idx,
                row["mz_pct"] + row["intensity_pct"] + row["full_scan_sidecar_pct"] / 2.0,
                f"{row['full_scan_sidecar_pct']:.1f}%\n{sidecar_mb:.1f} MB",
                ha="center",
                va="center",
                fontsize=9.5,
                color="white",
                fontweight="bold",
            )
    fig.tight_layout()
    fig.savefig(PLOTS_DIR / "ms1_ms2_payload_mix_stacked_bar.png", dpi=180)
    plt.close(fig)


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-scope", choices=sorted(DATASET_CONFIG.keys()), default="full8")
    parser.add_argument("--file-path", action="append", type=Path, default=[])
    parser.add_argument("--jobs", type=int, default=min(3, len(FULL8_PROFILE_FILES)))
    parser.add_argument("--baseline-threads", type=int, default=2)
    parser.add_argument("--compare-baseline-errors", action="store_true")
    parser.add_argument("--skip-ours-roundtrip", action="store_true")
    parser.add_argument("--ms1-island-workers", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--ms1-encode-section-workers", type=int, default=max(1, min(5, os.cpu_count() or 1)))
    parser.add_argument("--min-free-mem-per-job-gb", type=float, default=14.0)
    parser.add_argument("--max-swap-pct", type=float, default=80.0)
    parser.add_argument("--ignore-swap-guard", action="store_true")
    parser.add_argument("--disable-bundle-parallel-sections", action="store_true")
    parser.add_argument("--reuse-ms1-methods-csv", type=Path, default=None)
    parser.add_argument("--reuse-ms2-methods-csv", type=Path, default=None)
    parser.add_argument("--reuse-metadata-methods-csv", type=Path, default=None)
    parser.add_argument("--result-dir-name", type=str, default="")
    parser.add_argument("--result-root", type=Path, default=None)
    parser.add_argument(
        "--sections",
        nargs="+",
        choices=["ms1", "ms2", "metadata", "overall"],
        default=["ms1", "ms2", "metadata", "overall"],
    )
    return parser.parse_args()


def _configure_output_dirs(
    dataset_scope: str,
    result_dir_name: str = "",
    result_root: Path | None = None,
    selected_files: list[Path] | None = None,
):
    global RESULT_ROOT, PLOTS_DIR, PLOT_DATA_DIR, COMP_DIR, TABLE_DIR, LOG_DIR, ARTIFACTS_DIR
    global SECTION_PROGRESS_LOG, SECTION_SUMMARY_JSON, SECTION_SUMMARY_MD, SECTION_SUMMARY_CSV, FILE_LOG_ROOT
    global CURRENT_DATASET_SCOPE, CURRENT_FILES
    CURRENT_DATASET_SCOPE = dataset_scope
    CURRENT_FILES = list(selected_files) if selected_files is not None else DATASET_CONFIG[dataset_scope]["files"]
    if result_root is not None:
        RESULT_ROOT = Path(result_root)
    else:
        final_dir_name = result_dir_name or DATASET_CONFIG[dataset_scope]["result_dir_name"]
        RESULT_ROOT = ROOT.parent / "benchmark_results" / final_dir_name
    PLOTS_DIR = RESULT_ROOT / "plots"
    PLOT_DATA_DIR = RESULT_ROOT / "plot_data"
    COMP_DIR = RESULT_ROOT / "compression_results"
    TABLE_DIR = RESULT_ROOT / "comparison_tables"
    LOG_DIR = RESULT_ROOT / "logs"
    ARTIFACTS_DIR = RESULT_ROOT / "artifacts"
    SECTION_PROGRESS_LOG = LOG_DIR / "section_benchmark.progress.jsonl"
    SECTION_SUMMARY_JSON = LOG_DIR / "section_benchmark_summary.json"
    SECTION_SUMMARY_MD = LOG_DIR / "section_benchmark_summary.md"
    SECTION_SUMMARY_CSV = LOG_DIR / "section_benchmark_summary_by_file.csv"
    FILE_LOG_ROOT = LOG_DIR / "by_file"
    for path in (PLOTS_DIR, PLOT_DATA_DIR, COMP_DIR, TABLE_DIR, LOG_DIR, ARTIFACTS_DIR, FILE_LOG_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def main():
    args = _parse_args()
    assert_native_speedups_available(required=True)
    global MS1_ISLAND_WORKERS, MS1_ENCODE_SECTION_WORKERS
    global SECTION_FILE_JOBS, BUNDLE_PARALLEL_SECTIONS, BUNDLE_PARALLEL_MIN_AVAILABLE_BYTES
    MS1_ISLAND_WORKERS = max(1, int(args.ms1_island_workers))
    MS1_ENCODE_SECTION_WORKERS = max(1, int(args.ms1_encode_section_workers))
    SECTION_FILE_JOBS = max(1, int(args.jobs))
    BUNDLE_PARALLEL_SECTIONS = not bool(args.disable_bundle_parallel_sections)
    BUNDLE_PARALLEL_MIN_AVAILABLE_BYTES = max(0, int(float(args.min_free_mem_per_job_gb) * (1 << 30)))
    selected_files = [Path(path) for path in args.file_path] if args.file_path else None
    _configure_output_dirs(args.dataset_scope, args.result_dir_name, args.result_root, selected_files)
    _append_jsonl(
        SECTION_PROGRESS_LOG,
        {
            "file": "__run__",
            "section": "run",
            "stage": "start",
            "time": _now_text(),
            "args": _json_safe(vars(args)),
        },
    )
    print(f"[FULL8] args={vars(args)}", flush=True)
    need_ms1 = "ms1" in args.sections or "overall" in args.sections
    need_ms2 = "ms2" in args.sections or "overall" in args.sections
    need_metadata = "metadata" in args.sections or "overall" in args.sections

    ms1_per_file = None
    if need_ms1 and args.reuse_ms1_methods_csv is not None:
        print(f"[FULL8] reusing ms1 methods csv={args.reuse_ms1_methods_csv}", flush=True)
        ms1_per_file = _load_existing_ms1_methods_csv(args.reuse_ms1_methods_csv)

    metadata_per_file = None
    if need_metadata and args.reuse_metadata_methods_csv is not None:
        print(f"[FULL8] reusing metadata methods csv={args.reuse_metadata_methods_csv}", flush=True)
        metadata_per_file = _load_existing_methods_csv(args.reuse_metadata_methods_csv)

    ms2_per_file = None
    if need_ms2 and args.reuse_ms2_methods_csv is not None:
        print(f"[FULL8] reusing ms2 methods csv={args.reuse_ms2_methods_csv}", flush=True)
        ms2_per_file = _load_existing_methods_csv(args.reuse_ms2_methods_csv)

    fresh_ms1 = need_ms1 and ms1_per_file is None
    fresh_ms2 = need_ms2 and ms2_per_file is None
    fresh_metadata = need_metadata and metadata_per_file is None
    if fresh_ms1 or fresh_ms2 or fresh_metadata:
        effective_jobs, scheduler_info = _resolve_effective_jobs(
            requested_jobs=int(args.jobs),
            n_files=len(CURRENT_FILES),
            min_free_mem_per_job_gb=float(args.min_free_mem_per_job_gb),
            max_swap_pct=float(args.max_swap_pct),
        )
        _append_jsonl(
            SECTION_PROGRESS_LOG,
            {
                "file": "__run__",
                "section": "run",
                "stage": "scheduler",
                "scheduler_info": scheduler_info,
                "ms1_island_workers": MS1_ISLAND_WORKERS,
                "ms1_encode_section_workers": MS1_ENCODE_SECTION_WORKERS,
                "skip_ours_roundtrip": bool(args.skip_ours_roundtrip),
                "bundle_parallel_sections": bool(BUNDLE_PARALLEL_SECTIONS),
            },
        )
        print(
            f"[FULL8] scheduler requested_jobs={scheduler_info['requested_jobs']} "
            f"effective_jobs={scheduler_info['effective_jobs']} "
            f"available_gb={scheduler_info['available_gb']:.2f} "
            f"swap_used_pct={scheduler_info['swap_used_pct']:.2f} "
            f"ms1_island_workers={MS1_ISLAND_WORKERS} "
            f"ms1_encode_section_workers={MS1_ENCODE_SECTION_WORKERS} "
            f"bundle_parallel_sections={bool(BUNDLE_PARALLEL_SECTIONS)} "
            f"skip_ours_roundtrip={bool(args.skip_ours_roundtrip)}",
            flush=True,
        )
        if bool(scheduler_info.get("guard_blocked")):
            guard_message = (
                f"[FULL8] scheduler guard blocked start: {scheduler_info.get('guard_reason') or 'unknown reason'}"
            )
            _append_jsonl(
                SECTION_PROGRESS_LOG,
                {
                    "file": "__run__",
                    "section": "run",
                    "stage": "scheduler_guard",
                    "guard_blocked": True,
                    "guard_reason": scheduler_info.get("guard_reason", ""),
                    "ignore_swap_guard": bool(args.ignore_swap_guard),
                },
            )
            print(guard_message, flush=True)
            if not bool(args.ignore_swap_guard):
                raise SystemExit(
                    guard_message
                    + " (pass --ignore-swap-guard to override intentionally)"
                )
            print("[FULL8] scheduler guard override enabled, continuing anyway", flush=True)
        print(
            f"[FULL8] stage=file_bundle fresh start shared_parse ms1={fresh_ms1} ms2={fresh_ms2} metadata={fresh_metadata}",
            flush=True,
        )
        bundle_args = [
            (
                str(p),
                fresh_ms1,
                fresh_ms2,
                fresh_metadata,
                args.baseline_threads,
                args.compare_baseline_errors,
                not bool(args.skip_ours_roundtrip),
            )
            for p in CURRENT_FILES
        ]
        if int(effective_jobs) <= 1:
            bundle_rows = [benchmark_file_bundle(*bundle_arg) for bundle_arg in bundle_args]
        else:
            with mp.Pool(processes=max(1, min(effective_jobs, len(CURRENT_FILES)))) as pool:
                bundle_rows = pool.starmap(benchmark_file_bundle, bundle_args)
        print("[FULL8] stage=file_bundle fresh done", flush=True)
        stage_metric_rows = []
        for bundle in bundle_rows:
            stage_metric_rows.extend(bundle.get("stage_rows", []))
            if fresh_ms1 and bundle.get("ms1") is not None:
                if ms1_per_file is None:
                    ms1_per_file = {}
                ms1_per_file[bundle["file"]] = dict(bundle["ms1"]["methods"])
            if fresh_ms2 and bundle.get("ms2") is not None:
                if ms2_per_file is None:
                    ms2_per_file = {}
                ms2_per_file[bundle["file"]] = dict(bundle["ms2"]["methods"])
            if fresh_metadata and bundle.get("metadata") is not None:
                if metadata_per_file is None:
                    metadata_per_file = {}
                metadata_per_file[bundle["file"]] = dict(bundle["metadata"]["methods"])
        _write_csv(stage_metric_rows, COMP_DIR / f"{args.dataset_scope}_section_stage_metrics.csv")
        _write_aggregate_stage_summary(bundle_rows, stage_metric_rows)

    if ms1_per_file is not None:
        _write_csv(
            [
                {"file": file_name, "method": method, **vals}
                for file_name, methods in sorted(ms1_per_file.items())
                for method, vals in methods.items()
            ],
            COMP_DIR / "ms1_per_file_methods.csv",
        )
    if metadata_per_file is not None:
        _write_csv(
            [
                {"file": file_name, "method": method, **vals}
                for file_name, methods in sorted(metadata_per_file.items())
                for method, vals in methods.items()
            ],
            COMP_DIR / "metadata_per_file_methods.csv",
        )
    if ms2_per_file is not None:
        _write_csv(
            [
                {"file": file_name, "method": method, **vals}
                for file_name, methods in sorted(ms2_per_file.items())
                for method, vals in methods.items()
            ],
            COMP_DIR / "ms2_per_file_methods.csv",
        )

    overall_per_file = None
    if "overall" in args.sections:
        if ms1_per_file is None or ms2_per_file is None or metadata_per_file is None:
            raise ValueError("overall requires ms1, ms2, and metadata inputs")
        overall_per_file = {}
        for file_path in CURRENT_FILES:
            file_name = file_path.name
            ms1 = ms1_per_file[file_name]
            ms2 = ms2_per_file[file_name]
            meta = metadata_per_file[file_name]
            overall_per_file[file_name] = {
            "raw": {
                "raw_bytes": ms1["raw"]["raw_bytes"] + ms2["raw"]["raw_bytes"] + meta["raw"]["raw_bytes"],
                "compressed_bytes": ms1["raw"]["raw_bytes"] + ms2["raw"]["raw_bytes"] + meta["raw"]["raw_bytes"],
                "compression_ratio": 1.0,
                "encode_time_s": 0.0,
                "decode_time_s": 0.0,
            },
            "gzip": {
                "raw_bytes": ms1["gzip"]["raw_bytes"] + ms2["gzip"]["raw_bytes"] + meta["gzip"]["raw_bytes"],
                "compressed_bytes": ms1["gzip"]["compressed_bytes"] + ms2["gzip"]["compressed_bytes"] + meta["gzip"]["compressed_bytes"],
                "compression_ratio": (ms1["gzip"]["raw_bytes"] + ms2["gzip"]["raw_bytes"] + meta["gzip"]["raw_bytes"]) / (ms1["gzip"]["compressed_bytes"] + ms2["gzip"]["compressed_bytes"] + meta["gzip"]["compressed_bytes"]),
                "encode_time_s": ms1["gzip"]["encode_time_s"] + ms2["gzip"]["encode_time_s"] + meta["gzip"]["encode_time_s"],
                "decode_time_s": ms1["gzip"]["decode_time_s"] + ms2["gzip"]["decode_time_s"] + meta["gzip"]["decode_time_s"],
            },
            "zlib": {
                "raw_bytes": ms1["zlib"]["raw_bytes"] + ms2["zlib"]["raw_bytes"] + meta["zlib"]["raw_bytes"],
                "compressed_bytes": ms1["zlib"]["compressed_bytes"] + ms2["zlib"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"],
                "compression_ratio": (ms1["zlib"]["raw_bytes"] + ms2["zlib"]["raw_bytes"] + meta["zlib"]["raw_bytes"]) / (ms1["zlib"]["compressed_bytes"] + ms2["zlib"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"]),
                "encode_time_s": ms1["zlib"]["encode_time_s"] + ms2["zlib"]["encode_time_s"] + meta["zlib"]["encode_time_s"],
                "decode_time_s": ms1["zlib"]["decode_time_s"] + ms2["zlib"]["decode_time_s"] + meta["zlib"]["decode_time_s"],
            },
            "zstd-9": {
                "raw_bytes": ms1["zstd-9"]["raw_bytes"] + ms2["zstd-9"]["raw_bytes"] + meta["zstd-9"]["raw_bytes"],
                "compressed_bytes": ms1["zstd-9"]["compressed_bytes"] + ms2["zstd-9"]["compressed_bytes"] + meta["zstd-9"]["compressed_bytes"],
                "compression_ratio": (ms1["zstd-9"]["raw_bytes"] + ms2["zstd-9"]["raw_bytes"] + meta["zstd-9"]["raw_bytes"]) / (ms1["zstd-9"]["compressed_bytes"] + ms2["zstd-9"]["compressed_bytes"] + meta["zstd-9"]["compressed_bytes"]),
                "encode_time_s": ms1["zstd-9"]["encode_time_s"] + ms2["zstd-9"]["encode_time_s"] + meta["zstd-9"]["encode_time_s"],
                "decode_time_s": ms1["zstd-9"]["decode_time_s"] + ms2["zstd-9"]["decode_time_s"] + meta["zstd-9"]["decode_time_s"],
            },
            "zdpd_container": {
                "raw_bytes": ms1["zdpd_baseline"]["raw_bytes"] + ms2["zdpd_baseline"]["raw_bytes"] + meta["zlib"]["raw_bytes"],
                "compressed_bytes": ms1["zdpd_baseline"]["compressed_bytes"] + ms2["zdpd_baseline"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"],
                "compression_ratio": (ms1["zdpd_baseline"]["raw_bytes"] + ms2["zdpd_baseline"]["raw_bytes"] + meta["zlib"]["raw_bytes"]) / (ms1["zdpd_baseline"]["compressed_bytes"] + ms2["zdpd_baseline"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"]),
                "encode_time_s": None,
                "decode_time_s": None,
                "numeric_encode_time_s": ms1["zdpd_baseline"]["encode_time_s"] + ms2["zdpd_baseline"]["encode_time_s"] + meta["zlib"]["encode_time_s"],
                "numeric_decode_time_s": ms1["zdpd_baseline"]["decode_time_s"] + ms2["zdpd_baseline"]["decode_time_s"] + meta["zlib"]["decode_time_s"],
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            },
            "stack_zdpd_container": {
                "raw_bytes": ms1["stack_zdpd_baseline"]["raw_bytes"] + ms2["stack_zdpd_baseline"]["raw_bytes"] + meta["zlib"]["raw_bytes"],
                "compressed_bytes": ms1["stack_zdpd_baseline"]["compressed_bytes"] + ms2["stack_zdpd_baseline"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"],
                "compression_ratio": (ms1["stack_zdpd_baseline"]["raw_bytes"] + ms2["stack_zdpd_baseline"]["raw_bytes"] + meta["zlib"]["raw_bytes"]) / (ms1["stack_zdpd_baseline"]["compressed_bytes"] + ms2["stack_zdpd_baseline"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"]),
                "encode_time_s": None,
                "decode_time_s": None,
                "numeric_encode_time_s": ms1["stack_zdpd_baseline"]["encode_time_s"] + ms2["stack_zdpd_baseline"]["encode_time_s"] + meta["zlib"]["encode_time_s"],
                "numeric_decode_time_s": ms1["stack_zdpd_baseline"]["decode_time_s"] + ms2["stack_zdpd_baseline"]["decode_time_s"] + meta["zlib"]["decode_time_s"],
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            },
            "ours_container": {
                "raw_bytes": ms1["ours_eqfidelity"]["raw_bytes"] + ms2["ours_eqfidelity"]["raw_bytes"] + meta["ours_metadata"]["raw_bytes"],
                "compressed_bytes": ms1["ours_eqfidelity"]["compressed_bytes"] + ms2["ours_eqfidelity"]["compressed_bytes"] + meta["ours_metadata"]["compressed_bytes"],
                "compression_ratio": (ms1["ours_eqfidelity"]["raw_bytes"] + ms2["ours_eqfidelity"]["raw_bytes"] + meta["ours_metadata"]["raw_bytes"]) / (ms1["ours_eqfidelity"]["compressed_bytes"] + ms2["ours_eqfidelity"]["compressed_bytes"] + meta["ours_metadata"]["compressed_bytes"]),
                "encode_time_s": ms1["ours_eqfidelity"]["encode_time_s"] + ms2["ours_eqfidelity"]["encode_time_s"] + meta["ours_metadata"]["encode_time_s"],
                "decode_time_s": ms1["ours_eqfidelity"]["decode_time_s"] + ms2["ours_eqfidelity"]["decode_time_s"] + meta["ours_metadata"]["decode_time_s"],
            },
            }
            if "ours_archive_fidelity_auto" in ms1:
                archive_auto = ms1["ours_archive_fidelity_auto"]
                auto_comp = archive_auto["compressed_bytes"] + ms2["ours_eqfidelity"]["compressed_bytes"] + meta["ours_metadata"]["compressed_bytes"]
                overall_per_file[file_name]["ours_archive_auto_container"] = {
                    "raw_bytes": archive_auto["raw_bytes"] + ms2["ours_eqfidelity"]["raw_bytes"] + meta["ours_metadata"]["raw_bytes"],
                    "compressed_bytes": auto_comp,
                    "compression_ratio": (archive_auto["raw_bytes"] + ms2["ours_eqfidelity"]["raw_bytes"] + meta["ours_metadata"]["raw_bytes"]) / auto_comp if auto_comp else 0.0,
                    "encode_time_s": archive_auto["encode_time_s"] + ms2["ours_eqfidelity"]["encode_time_s"] + meta["ours_metadata"]["encode_time_s"],
                    "decode_time_s": archive_auto["decode_time_s"] + ms2["ours_eqfidelity"]["decode_time_s"] + meta["ours_metadata"]["decode_time_s"],
                    "ms1_representation": archive_auto.get("ms1_representation", "track_island_archive"),
                    "ms1_full_mz_compressed_bytes": archive_auto.get("ms1_full_mz_compressed_bytes", 0),
                }

    ms1_bar = ms1_line = metadata_bar = metadata_line = ms2_bar = ms2_line = overall_bar = overall_line = []
    if "ms1" in args.sections:
        ms1_bar, ms1_line, _ = _summarize_rows(f"{args.dataset_scope}_ms1", ms1_per_file, MS1_SECTION_ORDER)
    if "ms2" in args.sections:
        ms2_bar, ms2_line, _ = _summarize_rows(f"{args.dataset_scope}_ms2", ms2_per_file, MS2_SECTION_ORDER)
    if "metadata" in args.sections:
        metadata_bar, metadata_line, _ = _summarize_rows(f"{args.dataset_scope}_metadata", metadata_per_file, METADATA_SECTION_ORDER)
    if "overall" in args.sections:
        overall_bar, overall_line, _ = _summarize_rows(f"{args.dataset_scope}_overall", overall_per_file, OVERALL_ORDER)

    if ms1_per_file is not None and ms2_per_file is not None:
        our_ms1_payload, ms1_totals = _build_ms1_payload_composition(ms1_per_file)
        our_ms2_payload, ms2_totals = _build_ms2_payload_composition(ms2_per_file)
        _write_csv(our_ms1_payload, COMP_DIR / f"{args.dataset_scope}_ms1_payload_mix_by_file.csv")
        _write_csv(our_ms2_payload, COMP_DIR / f"{args.dataset_scope}_ms2_payload_mix_by_file.csv")
        _write_csv([{"section": "MS1", **ms1_totals}, {"section": "MS2", **ms2_totals}], COMP_DIR / f"{args.dataset_scope}_ms1_ms2_payload_mix_aggregate.csv")
        _plot_payload_mix(ms1_totals, ms2_totals)

    summary = {
        "dataset_scope": args.dataset_scope,
        "files": [str(path) for path in CURRENT_FILES],
        "ms1_methods": ms1_bar,
        "ms2_methods": ms2_bar,
        "metadata_methods": metadata_bar,
        "overall_methods": overall_bar,
        "args": _json_safe(vars(args)),
    }
    (COMP_DIR / f"{args.dataset_scope}_summary.json").write_text(json.dumps(summary, indent=2))
    readme_lines = [
        f"# TrackCodec {args.dataset_scope.upper()} Multi-section Benchmark",
        "",
        "## Structure",
        "",
        "- `plots/`: PNG figures",
        "- `plot_data/`: CSV used to draw the figures",
        "- `compression_results/`: per-file/aggregate CSV and summary JSON",
        "",
        "## Headline",
        "",
    ]
    if ms1_bar:
        archive_row = next((row for row in ms1_bar if row["label"] == "ours_archive_fidelity_auto"), None)
        eq_row = next((row for row in ms1_bar if row["label"] == "ours_eqfidelity"), None)
        if archive_row is not None:
            readme_lines.append(f"- MS1 production Archive auto CR: `{archive_row['mean_cr']:.3f}x`")
        if eq_row is not None:
            readme_lines.append(f"- MS1 component track/island CR: `{eq_row['mean_cr']:.3f}x`")
    if ms2_bar:
        readme_lines.append(f"- MS2 best ours (equal-fidelity): `{next(row['mean_cr'] for row in ms2_bar if row['label']=='ours_eqfidelity'):.3f}x`")
    if overall_bar:
        overall_archive = next((row for row in overall_bar if row["label"] == "ours_archive_auto_container"), None)
        overall_component = next((row for row in overall_bar if row["label"] == "ours_container"), None)
        if overall_archive is not None:
            readme_lines.append(f"- Overall production Archive auto container CR: `{overall_archive['mean_cr']:.3f}x`")
        if overall_component is not None:
            readme_lines.append(f"- Overall component container CR: `{overall_component['mean_cr']:.3f}x`")
    readme_lines.extend(
        [
            "",
            "## Caveat",
            "",
            "- `zdpd_container` and `stack_zdpd_container` use section-wise ZDPD/Stack-ZDPD baselines for MS1/MS2 plus `zlib` on non-binary metadata.",
        ]
    )
    (RESULT_ROOT / "README.md").write_text("\n".join(readme_lines))

    print("[FULL8] done", flush=True)
    print(json.dumps({"result_root": str(RESULT_ROOT)}, indent=2))


if __name__ == "__main__":
    main()
