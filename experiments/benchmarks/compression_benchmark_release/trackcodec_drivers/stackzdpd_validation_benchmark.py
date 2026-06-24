from __future__ import annotations

import argparse
from array import array
import copy
import csv
import gc
import hashlib
from datetime import datetime
import gzip
import io
import json
import multiprocessing as mp
import os
import re
import shutil
import statistics
import tempfile
import time
import traceback
import zlib
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import zstandard as zstd
from pyteomics import mzml

try:
    import psutil
except Exception:
    psutil = None


DRIVER_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = DRIVER_DIR.parent
ROOT = RELEASE_ROOT.parents[2]
STACKZDPD_VALIDATION_ENV = "TRACKCODEC_STACKZDPD_VALIDATION_DIR"
DEFAULT_RESULT_ROOT = ROOT.parent / "benchmark_results" / "trackcodec_stackzdpd_validation_20260407"
os.sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.compression_backends import compress as backend_compress
from TrackCodec.production.common.compression_backends import decompress as backend_decompress
from TrackCodec.production.common.extensions import archive_name
from TrackCodec.production.common.runtime_env import assert_native_speedups_available
from TrackCodec.production.common.io import (
    detect_file_type,
    detect_file_type_by_name,
    load_all_ms2_scans,
    load_scans,
)
from TrackCodec.production.common.islands import identify_islands
from TrackCodec.production.common.scan_store import (
    create_memmap_scan_store_from_mzml,
    is_complete_memmap_scan_store,
    open_memmap_scan_store,
)
from TrackCodec.production.common.tracking import CompactIslandTracks, build_island_tracks
from TrackCodec.production.common.track_store import open_compact_track_store, write_compact_track_store
from TrackCodec.production.metadata.metadata_codec import MzMLMetadataCodec, extract_binary_stripped_metadata_xml
from TrackCodec.production.ms1.unified_codec import MS1Codec
from TrackCodec.production.ms1.cross_scan_codec import _unpack_segments
from TrackCodec.production.ms2.codec import DIAWindowMS2Codec, MS2ModeConfig
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
    build_ms1_tracks_for_archive,
)
from TrackCodec.production.mzml.archive_codec import build_ms1_tracks as archive_build_ms1_tracks
from TrackCodec.production.mzml.reconstruction import extract_auxiliary_binary_records
from TrackCodec.production.mzml.reconstruction import reconstruct_ms1_scans_from_islands_with_full_mz
from TrackCodec.experiments.baselines.stack_zdpd_baseline import (
    compare_roundtrip_scans,
    float64_payload,
    stack_zdpd_baseline_compress,
    zdpd_baseline_compress,
)
sys.path.insert(0, str(DRIVER_DIR))
from full8_section_benchmark import (
    MS1_INT_SEGMENTS,
    MS1_META_SEGMENTS,
    MS1_MZ_SEGMENTS,
    MS2_INT_SEGMENTS,
    MS2_MZ_SEGMENTS,
    _categorize_segment_sizes,
    _collect_ms2_segment_sizes,
    _ms2_window_key,
    compare_centroid_scans,
)
from TrackCodec.experiments.plotting.benchmark_from_csv import (
    plot_compression_bar_custom,
    plot_compression_line_custom,
    plot_speed_bar,
)
from TrackCodec.production.validation.roundtrip import compare_files


COLOR_MAP = {
    "raw": "#B0B0B0",
    "raw_file": "#B0B0B0",
    "gzip": "#BFD7EA",
    "zlib": "#9ECAE1",
    "zstd-9": "#6BAED6",
    "zdpd_baseline": "#72B7B2",
    "stack_zdpd_baseline": "#9E9AC8",
    "ours_eqfidelity": "#F58518",
    "ours_archive_fidelity": "#D67236",
    "ours_strict_q6": "#4C78A8",
    "ours_metadata": "#E45756",
    "gzip_file": "#BFD7EA",
    "zlib_file": "#9ECAE1",
    "zstd-9_file": "#6BAED6",
    "zdpd_container": "#72B7B2",
    "stack_zdpd_container": "#9E9AC8",
    "ours_whole_archive": "#F58518",
    "ours_strict_q6_whole_archive": "#4C78A8",
}

DISPLAY_NAME = {
    "raw": "Raw float64 payload",
    "gzip": "gzip on raw float64",
    "zlib": "zlib on raw float64",
    "zstd-9": "zstd-9 on raw float64",
    "zdpd_baseline": "ZDPD baseline",
    "stack_zdpd_baseline": "Stack-ZDPD baseline",
    "ours_eqfidelity": "TrackCodec equal-fidelity",
    "ours_archive_fidelity": "TrackCodec archive-fidelity MS1",
    "ours_strict_q6": "Our strict-q6 path",
    "ours_metadata": "Our metadata codec",
    "raw_file": "Raw mzML file",
    "gzip_file": "gzip whole mzML",
    "zlib_file": "zlib whole mzML",
    "zstd-9_file": "zstd-9 whole mzML",
    "zdpd_container": "ZDPD container baseline",
    "stack_zdpd_container": "Stack-ZDPD container baseline",
    "ours_whole_archive": "TrackCodec whole mzML archive (equal-fidelity)",
    "ours_strict_q6_whole_archive": "TrackCodec whole mzML archive (strict-q6)",
}

MS1_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "zdpd_baseline",
    "stack_zdpd_baseline",
    "ours_eqfidelity",
    "ours_archive_fidelity",
    "ours_strict_q6",
]
MS2_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "zdpd_baseline",
    "stack_zdpd_baseline",
    "ours_eqfidelity",
]
META_ORDER = [
    "raw",
    "gzip",
    "zlib",
    "zstd-9",
    "ours_metadata",
]
WHOLE_ORDER = [
    "raw_file",
    "gzip_file",
    "zlib_file",
    "zstd-9_file",
    "zdpd_container",
    "stack_zdpd_container",
    "ours_whole_archive",
    "ours_strict_q6_whole_archive",
]

SECTION_FAMILY_MAP = {
    "ours_eqfidelity": "near",
    "ours_archive_fidelity": "near",
    "ours_strict_q6": "strict_q6",
}
WHOLE_FAMILY_MAP = {
    "ours_whole_archive": "near",
    "ours_strict_q6_whole_archive": "strict_q6",
}

EQ_MS1_MODE = dict(
    mz_precision=6,
    intensity_mode="szdpd_xdelta_equalfidelity",
    backend="brotli",
    use_pfor=True,
    mz_dedup="residual_offset_model",
    zero_rle=True,
    metadata_transform=True,
    adaptive_uint32=True,
    adaptive_intensity_search=False,
    adaptive_search_mode="converged",
    adaptive_search_sample_count=16384,
    delta_ref_window=4,
    padded_delta_max_diff=1,
    adaptive_padded_delta_accounting=True,
    enable_byteaware_delta_ref_selection=True,
    omit_array_starts=True,
    omit_n_points=True,
    omit_island_mz=True,
    preserve_full_scan=True,
)
STRICT_MS1_MODE = dict(EQ_MS1_MODE)
STRICT_MS1_MODE["intensity_mode"] = "strict_lossless_xdelta"


def _ms1_mode_fingerprint(label: str, mode_cfg: dict) -> str:
    payload = {
        "label": str(label),
        "mode": _json_safe(mode_cfg),
        "cache_schema": "ms1_section_full_sidecar_mz_omit_island_mz",
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]

EQ_ARCHIVE_MS1_MODE = {
    "mz_precision": int(EQ_MS1_MODE["mz_precision"]),
    "intensity_mode": str(EQ_MS1_MODE["intensity_mode"]),
    "backend": str(EQ_MS1_MODE["backend"]),
    "kwargs": {key: value for key, value in EQ_MS1_MODE.items() if key not in {"mz_precision", "intensity_mode", "backend"}},
}
STRICT_ARCHIVE_MS1_MODE = {
    "mz_precision": int(STRICT_MS1_MODE["mz_precision"]),
    "intensity_mode": str(STRICT_MS1_MODE["intensity_mode"]),
    "backend": str(STRICT_MS1_MODE["backend"]),
    "kwargs": {key: value for key, value in STRICT_MS1_MODE.items() if key not in {"mz_precision", "intensity_mode", "backend"}},
}

WHOLE_METHOD_SCOPE_MAP = {
    "raw_file": "file_stream_baseline",
    "gzip_file": "file_stream_baseline",
    "zlib_file": "file_stream_baseline",
    "zstd-9_file": "file_stream_baseline",
    "zdpd_container": "synthetic_container",
    "stack_zdpd_container": "synthetic_container",
    "ours_whole_archive": "true_whole_file",
    "ours_strict_q6_whole_archive": "true_whole_file",
}
WHOLE_VALIDATED_ROUNDTRIP_MAP = {
    "raw_file": False,
    "gzip_file": False,
    "zlib_file": False,
    "zstd-9_file": False,
    "zdpd_container": False,
    "stack_zdpd_container": False,
    "ours_whole_archive": True,
    "ours_strict_q6_whole_archive": True,
}
WHOLE_RUN_MODE_CHOICES = ("all", "encode_only", "validate_only")

MZ_ERROR_CEILING = 5e-7
INT_ERROR_CEILING = 0.05
CEILING_EPS = 1e-10
CHUNK_SIZE = 16 * 1024 * 1024
BENCHMARK_TIME_SCOPE_VERSION = "whole_file_io_timing_v2"
FILE_STREAM_ENCODE_TIME_SCOPE = "source_mzml_file_read_to_compressed_file_write"
FILE_STREAM_DECODE_TIME_SCOPE = "compressed_file_read_to_mzml_file_write"
RAW_FILE_ENCODE_TIME_SCOPE = "no_encode_raw_file_reference"
RAW_FILE_DECODE_TIME_SCOPE = "no_decode_raw_file_reference"
WHOLE_ARCHIVE_ENCODE_TIME_SCOPE = "source_mzml_file_read_to_trackcodec_archive_file_write"
WHOLE_ARCHIVE_DECODE_TIME_SCOPE = "trackcodec_archive_file_read_to_reconstructed_mzml_file_write"
NO_WHOLE_FILE_MZML_DECODER_SCOPE = "not_available_no_whole_file_mzml_decoder"
SECTION_NUMERIC_ENCODE_TIME_SCOPE = "section_numeric_payload_encode_only_not_source_mzml_to_compressed_file"
SECTION_NUMERIC_DECODE_TIME_SCOPE = "section_numeric_payload_decode_only_not_compressed_file_to_mzml_file"
WHOLE_FILE_BASELINE_METHODS = ("raw_file", "gzip_file", "zlib_file", "zstd-9_file")


def _optional_float(value) -> float | None:
    if value is None:
        return None
    if value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_optional_times(*values) -> float | None:
    parsed = [_optional_float(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return float(sum(parsed))


def _sum_numeric_times(*stats: dict, key: str) -> float | None:
    parsed = []
    for item in stats:
        value = item.get(key)
        if value is None:
            value = item.get("encode_time_s" if key == "numeric_encode_time_s" else "decode_time_s")
        if value is None:
            return None
        parsed.append(float(value))
    return float(sum(parsed))


def _promote_section_numeric_timing_fields(stats: dict | None) -> dict | None:
    """Keep section numeric timings separate from file-scope timing columns."""
    if not isinstance(stats, dict):
        return stats
    out = dict(stats)
    has_section_scope = (
        out.get("encode_time_scope") == SECTION_NUMERIC_ENCODE_TIME_SCOPE
        or out.get("decode_time_scope") == SECTION_NUMERIC_DECODE_TIME_SCOPE
    )
    has_numeric_timing = (
        out.get("numeric_encode_time_s") is not None
        or out.get("numeric_decode_time_s") is not None
    )
    if not has_section_scope and not has_numeric_timing:
        return out

    numeric_encode = _optional_float(out.get("numeric_encode_time_s"))
    if numeric_encode is None:
        numeric_encode = _optional_float(out.get("encode_time_s"))
    numeric_decode = _optional_float(out.get("numeric_decode_time_s"))
    if numeric_decode is None:
        numeric_decode = _optional_float(out.get("decode_time_s"))

    if numeric_encode is not None:
        out["numeric_encode_time_s"] = float(numeric_encode)
    if numeric_decode is not None:
        out["numeric_decode_time_s"] = float(numeric_decode)
    out["encode_time_s"] = None
    out["decode_time_s"] = None
    out.setdefault("encode_time_scope", SECTION_NUMERIC_ENCODE_TIME_SCOPE)
    out.setdefault("decode_time_scope", SECTION_NUMERIC_DECODE_TIME_SCOPE)
    out.setdefault("timing_scope_version", BENCHMARK_TIME_SCOPE_VERSION)
    return out


def _normalise_section_methods_for_reporting(methods: dict) -> dict:
    return {
        method: _promote_section_numeric_timing_fields(values)
        for method, values in methods.items()
    }


def _roundtrip_pass_flags(roundtrip: dict, *, mz_ok: bool, int_ok: bool) -> tuple[bool, bool]:
    structure_ok = bool(roundtrip.get("structure_within_ceiling", True))
    if "structure_within_ceiling" not in roundtrip:
        structure_ok = (
            int(roundtrip.get("missing_original_spectra", 0)) == 0
            and int(roundtrip.get("missing_reconstructed_spectra", 0)) == 0
            and int(roundtrip.get("spectrum_id_mismatch_count", 0)) == 0
            and int(roundtrip.get("ms_level_mismatch_count", 0)) == 0
            and int(roundtrip.get("rt_mismatch_count", 0)) == 0
            and int(roundtrip.get("array_length_mismatch_count", 0)) == 0
        )
    roundtrip_passed = bool(structure_ok and mz_ok and int_ok and roundtrip.get("aux_counts_match"))
    xml_metadata_warning = not (
        bool(roundtrip.get("binary_stripped_xml_equal")) and bool(roundtrip.get("xml_without_binary_arrays_equal"))
    )
    return roundtrip_passed, xml_metadata_warning


def _whole_file_baseline_cache_valid(stats: dict | None, input_path: Path) -> bool:
    if not isinstance(stats, dict):
        return False
    raw_bytes = int(input_path.stat().st_size)
    for method in WHOLE_FILE_BASELINE_METHODS:
        item = stats.get(method)
        if not isinstance(item, dict):
            return False
        if item.get("timing_scope_version") != BENCHMARK_TIME_SCOPE_VERSION:
            return False
        if method == "raw_file":
            if item.get("encode_time_scope") != RAW_FILE_ENCODE_TIME_SCOPE:
                return False
            if item.get("decode_time_scope") != RAW_FILE_DECODE_TIME_SCOPE:
                return False
        else:
            if item.get("encode_time_scope") != FILE_STREAM_ENCODE_TIME_SCOPE:
                return False
            if item.get("decode_time_scope") != FILE_STREAM_DECODE_TIME_SCOPE:
                return False
        if int(item.get("raw_bytes", -1)) != raw_bytes:
            return False
        if int(item.get("decoded_bytes_checked", -1)) != raw_bytes:
            return False
    return True


def _whole_archive_timing_cache_valid(stats: dict | None) -> bool:
    return bool(
        isinstance(stats, dict)
        and stats.get("timing_scope_version") == BENCHMARK_TIME_SCOPE_VERSION
        and stats.get("encode_time_scope") == WHOLE_ARCHIVE_ENCODE_TIME_SCOPE
        and stats.get("decode_time_scope") == WHOLE_ARCHIVE_DECODE_TIME_SCOPE
    )


def _whole_archive_decode_checkpoint_valid(stats: dict | None, reconstructed_path: Path) -> bool:
    return bool(
        _whole_archive_timing_cache_valid(stats)
        and stats
        and stats.get("encode_completed")
        and stats.get("decode_completed")
        and _optional_float(stats.get("decode_time_s")) is not None
        and reconstructed_path.exists()
        and reconstructed_path.stat().st_size > 0
    )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _current_rss_gb() -> float:
    try:
        with open("/proc/self/statm", "r", encoding="utf-8") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 ** 3)
    except Exception:
        return 0.0


def _available_memory_gb() -> float:
    if psutil is not None:
        try:
            return float(psutil.virtual_memory().available) / (1024.0 ** 3)
        except Exception:
            pass
    try:
        mem_available_kb = 0
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])
                break
        if mem_available_kb > 0:
            return float(mem_available_kb) / (1024.0 ** 2)
    except OSError:
        pass
    return 0.0


def _resolve_ms1_section_workers(
    *,
    file_path: Path,
    cpu_budget: int,
    track_prepare_workers: int,
    encode_section_workers: int,
    min_available_mem_gb: float,
) -> dict:
    raw_file_gb = float(file_path.stat().st_size) / (1024.0 ** 3)
    available_mem_gb = _available_memory_gb()
    cpu_budget = max(1, int(cpu_budget) if int(cpu_budget) > 0 else int(os.cpu_count() or 1))
    headroom_gb = max(0.0, float(available_mem_gb) - float(min_available_mem_gb))
    tight_threshold_gb = max(6.0, raw_file_gb * 1.25)
    guarded_threshold_gb = max(12.0, raw_file_gb * 2.0)
    auto_track_prepare = 1
    auto_encode_workers = 1

    if cpu_budget <= 1:
        reason = "cpu_budget_1"
    elif headroom_gb < tight_threshold_gb:
        reason = "memory_tight"
    elif headroom_gb < guarded_threshold_gb:
        auto_encode_workers = min(2, cpu_budget)
        reason = "memory_guarded"
    else:
        auto_track_prepare = 2 if cpu_budget >= 4 else 1
        if auto_track_prepare == 1:
            auto_encode_workers = min(4, cpu_budget)
        else:
            auto_encode_workers = min(4, max(2, cpu_budget - 1))
        if raw_file_gb >= 3.5:
            auto_encode_workers = min(auto_encode_workers, 3)
        reason = "multicore"

    resolved_track_prepare = max(1, int(track_prepare_workers) if int(track_prepare_workers) > 0 else int(auto_track_prepare))
    resolved_encode_workers = max(1, int(encode_section_workers) if int(encode_section_workers) > 0 else int(auto_encode_workers))
    if resolved_encode_workers == 1 and resolved_track_prepare > 1:
        resolved_track_prepare = 1

    return {
        "cpu_budget": int(cpu_budget),
        "track_prepare_workers": int(resolved_track_prepare),
        "encode_section_workers": int(resolved_encode_workers),
        "available_mem_gb": float(available_mem_gb),
        "min_available_mem_gb": float(min_available_mem_gb),
        "headroom_gb": float(headroom_gb),
        "raw_file_gb": float(raw_file_gb),
        "reason": reason,
    }


def _log_worker_stage(section: str, file_name: str, stage: str) -> None:
    print(
        f"[STACKZDPD][{section}] {stage} {file_name} rss_gb={_current_rss_gb():.3f}",
        flush=True,
    )


def _raw_bytes_from_scans(scans) -> int:
    total = 0
    for scan in scans:
        total += int(len(scan["mz_array"])) * 8
        total += int(len(scan["intensity_array"])) * 8
    return total


def _backend_stats_from_payload(raw_payload: bytes, raw_bytes: int, backend: str) -> dict:
    t0 = time.perf_counter()
    if backend == "gzip":
        comp = gzip.compress(raw_payload)
    else:
        comp = backend_compress(raw_payload, backend)
    encode_time = time.perf_counter() - t0

    dt0 = time.perf_counter()
    if backend == "gzip":
        decoded = gzip.decompress(comp)
    else:
        decoded = backend_decompress(comp, backend)
    decode_time = time.perf_counter() - dt0
    if len(decoded) != raw_bytes:
        raise RuntimeError(f"{backend} raw payload decode length mismatch: {len(decoded)} != {raw_bytes}")
    compressed_bytes = len(comp)
    del decoded
    del comp
    gc.collect()
    return {
        "raw_bytes": raw_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": raw_bytes / compressed_bytes if compressed_bytes else 0.0,
        "encode_time_s": encode_time,
        "decode_time_s": decode_time,
    }


def _benchmark_raw_backends(scans, *, section: str, file_name: str) -> tuple[int, dict]:
    _log_worker_stage(section, file_name, "raw_payload_streaming_prepare_start")
    raw_bytes = _raw_bytes_from_scans(scans)
    _log_worker_stage(section, file_name, f"raw_payload_streaming_ready raw_bytes={raw_bytes}")
    stats = {}
    for backend in ("gzip", "zlib", "zstd-9"):
        _log_worker_stage(section, file_name, f"{backend}_start")
        stats[backend] = _stream_backend_stats_from_scans(scans, raw_bytes, backend)
        _log_worker_stage(section, file_name, f"{backend}_done")
    gc.collect()
    _log_worker_stage(section, file_name, "raw_payload_streaming_done")
    return raw_bytes, stats


def _extract_file_number(path_or_name: str | Path) -> int:
    match = re.search(r"File\s*([0-9]+)", Path(path_or_name).name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 10**9


def _discover_files(input_dir: Path) -> list[Path]:
    files = [path for path in input_dir.iterdir() if path.suffix.lower() == ".mzml"]
    return sorted(files, key=lambda path: (_extract_file_number(path), path.name.lower()))


def _make_alias_map(files: list[Path]) -> dict[str, str]:
    out = {}
    for idx, path in enumerate(files, start=1):
        number = _extract_file_number(path)
        alias = f"File{number}" if number < 10**9 else f"File{idx}"
        out[path.name] = alias
    return out


def _detect_kind(file_path: Path) -> str:
    by_name = detect_file_type_by_name(str(file_path))
    if by_name != "UNKNOWN":
        return by_name
    detected = detect_file_type(str(file_path))
    return detected if detected != "UNKNOWN" else "DDA"


def _json_safe(value):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _archive_stage_timings_from_meta(archive_meta: dict | None) -> dict:
    if not isinstance(archive_meta, dict):
        return {}
    timings = archive_meta.get("stage_timings_s")
    return timings if isinstance(timings, dict) else {}


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _append_jsonl(path: Path, row: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(_json_safe(row), ensure_ascii=False) + "\n")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _stage_cache_dir(
    artifact_root: str | Path | None,
    section: str,
    file_name: str,
    *,
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
) -> Path | None:
    if artifact_root is None:
        return None
    root = Path(artifact_root)
    mode_tag = f"thr{int(baseline_threads)}_cmp{int(bool(compare_baseline_errors))}"
    return root / "_stage_cache" / section / mode_tag / file_name


def _load_stage_stats(cache_dir: Path | None, stem: str) -> dict | None:
    if cache_dir is None:
        return None
    return _load_json(cache_dir / f"{stem}.json")


def _load_ms1_variant_stage_stats(cache_dir: Path | None, stem: str, mode_cfg: dict) -> dict | None:
    cached = _load_stage_stats(cache_dir, stem)
    if cached is None:
        return None
    expected = _ms1_mode_fingerprint(stem, mode_cfg)
    if str(cached.get("ms1_mode_fingerprint", "")) != expected:
        return None
    return cached


def _save_stage_stats(cache_dir: Path | None, stem: str, stats: dict) -> None:
    if cache_dir is None:
        return
    _write_json(cache_dir / f"{stem}.json", stats)


def _mark_section_numeric_only_timing(stats: dict | None) -> dict | None:
    """Mark section numeric timing scope without hiding the measured time."""
    if not isinstance(stats, dict):
        return stats
    out = dict(stats)
    if "numeric_encode_time_s" not in out and out.get("encode_time_s") is not None:
        out["numeric_encode_time_s"] = float(out.get("encode_time_s", 0.0))
    if "numeric_decode_time_s" not in out and out.get("decode_time_s") is not None:
        out["numeric_decode_time_s"] = float(out.get("decode_time_s", 0.0))
    out["encode_time_scope"] = SECTION_NUMERIC_ENCODE_TIME_SCOPE
    out["decode_time_scope"] = SECTION_NUMERIC_DECODE_TIME_SCOPE
    out["timing_scope_version"] = BENCHMARK_TIME_SCOPE_VERSION
    return _promote_section_numeric_timing_fields(out)


def _normalise_strict_q6_ms1_timing_against_eqfidelity(
    strict_stats: dict | None,
    eq_stats: dict | None,
) -> tuple[dict | None, bool]:
    """Report strict-q6 with the same cold shared MS1 costs as eqfidelity.

    The MS1 section runs eqfidelity before strict-q6 and intentionally shares
    the expensive full-scan m/z sidecar cache. That is useful for throughput,
    but it makes strict-q6 encode timing look artificially small. For benchmark
    reporting, replace strict-q6's cache-hit build/full-scan timings with the
    eqfidelity cold timings while preserving the measured cache-hit fields.
    """
    if not isinstance(strict_stats, dict) or not isinstance(eq_stats, dict):
        return strict_stats, False
    if strict_stats.get("method") not in (None, "ours_strict_q6"):
        return strict_stats, False
    strict_stage = strict_stats.get("stage_timings_s")
    eq_stage = eq_stats.get("stage_timings_s")
    if not isinstance(strict_stage, dict) or not isinstance(eq_stage, dict):
        return strict_stats, False

    keys = ("ms1_build_representation_s", "ms1_section_full_scan_s")
    if not all(key in strict_stage and key in eq_stage for key in keys):
        return strict_stats, False

    out = copy.deepcopy(strict_stats)
    out_stage = copy.deepcopy(strict_stage)
    if "measured_encode_time_s" not in out:
        out["measured_encode_time_s"] = float(strict_stats.get("encode_time_s", 0.0))
    if "measured_stage_timings_s" not in out:
        out["measured_stage_timings_s"] = copy.deepcopy(strict_stage)
    measured_encode = float(out["measured_encode_time_s"])

    adjustment_s = 0.0
    replacement = {}
    for key in keys:
        measured_value = float(strict_stage.get(key, 0.0))
        eq_value = float(eq_stage.get(key, measured_value))
        out_stage[key] = eq_value
        adjustment_s += eq_value - measured_value
        replacement[key] = {
            "measured_s": measured_value,
            "reported_s": eq_value,
            "source_method": "ours_eqfidelity",
        }

    out["encode_time_s"] = max(0.0, measured_encode + adjustment_s)
    out["stage_timings_s"] = _json_safe(out_stage)
    out["timing_normalization"] = {
        "scheme": "strict_q6_ms1_cold_shared_cost_from_eqfidelity",
        "version": 1,
        "adjustment_s": float(adjustment_s),
        "replacement": replacement,
        "measured_encode_time_s": measured_encode,
        "reported_encode_time_s": float(out["encode_time_s"]),
    }
    section_sum = sum(
        float(value)
        for key, value in out_stage.items()
        if key.startswith("ms1_section_")
        and key.endswith("_s")
        and key not in {"ms1_section_encode_wall_s", "ms1_section_encode_stage_sum_s"}
    )
    out_stage["ms1_section_encode_stage_sum_s"] = float(section_sum)
    # Keep the historical key populated for old plotting code, but record that
    # it is a section-stage sum rather than a true wall time.
    out_stage["ms1_section_encode_wall_s"] = float(section_sum)
    out["stage_timings_s"] = _json_safe(out_stage)
    return out, True


def _normalise_ms1_method_timings(methods: dict, cache_dir: Path | None) -> None:
    eq_stats = methods.get("ours_eqfidelity")
    if eq_stats is None:
        eq_stats = _load_stage_stats(cache_dir, "ours_eqfidelity")
    strict_stats = methods.get("ours_strict_q6")
    if strict_stats is None:
        strict_stats = _load_stage_stats(cache_dir, "ours_strict_q6")
    normalised, changed = _normalise_strict_q6_ms1_timing_against_eqfidelity(strict_stats, eq_stats)
    if changed and isinstance(normalised, dict):
        methods["ours_strict_q6"] = normalised
        _save_stage_stats(cache_dir, "ours_strict_q6", normalised)


def _iter_scan_payload_chunks(scans):
    for scan in scans:
        mz_arr = np.asarray(scan["mz_array"], dtype=np.float64)
        int_arr = np.asarray(scan["intensity_array"], dtype=np.float64)
        if mz_arr.size:
            yield mz_arr.tobytes(order="C")
        if int_arr.size:
            yield int_arr.tobytes(order="C")


def _stream_backend_stats_from_scans(scans, raw_bytes: int, backend: str) -> dict:
    t0 = time.perf_counter()
    if backend == "gzip":
        out = io.BytesIO()
        with gzip.GzipFile(fileobj=out, mode="wb", mtime=0) as handle:
            for chunk in _iter_scan_payload_chunks(scans):
                handle.write(chunk)
        comp = out.getvalue()
    elif backend == "zlib":
        compobj = zlib.compressobj(level=6)
        parts = []
        for chunk in _iter_scan_payload_chunks(scans):
            piece = compobj.compress(chunk)
            if piece:
                parts.append(piece)
        tail = compobj.flush()
        if tail:
            parts.append(tail)
        comp = b"".join(parts)
    elif backend == "zstd-9":
        out = io.BytesIO()
        cctx = zstd.ZstdCompressor(level=9)
        with cctx.stream_writer(out, closefd=False) as handle:
            for chunk in _iter_scan_payload_chunks(scans):
                handle.write(chunk)
        comp = out.getvalue()
    else:
        raise ValueError(f"Unsupported streaming backend: {backend}")
    encode_time = time.perf_counter() - t0

    dt0 = time.perf_counter()
    if backend == "gzip":
        decoded = gzip.decompress(comp)
    elif backend == "zlib":
        decoded = zlib.decompress(comp)
    else:
        decoded = zstd.ZstdDecompressor().decompress(comp, max_output_size=int(raw_bytes))
    decode_time = time.perf_counter() - dt0
    if len(decoded) != int(raw_bytes):
        raise RuntimeError(f"{backend} raw payload decode length mismatch: {len(decoded)} != {raw_bytes}")
    compressed_bytes = len(comp)
    del decoded
    del comp
    gc.collect()
    return {
        "raw_bytes": int(raw_bytes),
        "compressed_bytes": int(compressed_bytes),
        "compression_ratio": float(raw_bytes / compressed_bytes) if compressed_bytes else 0.0,
        "encode_time_s": None,
        "decode_time_s": None,
        "numeric_encode_time_s": float(encode_time),
        "numeric_decode_time_s": float(decode_time),
        "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
        "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
        "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
    }


def _sorted_dia_window_items(ms2_by_window):
    if isinstance(ms2_by_window, dict):
        return sorted(ms2_by_window.items(), key=lambda item: item[0])
    return sorted(list(ms2_by_window), key=lambda item: item[0])


def _ensure_scan_store(
    file_path: Path,
    artifact_root: str | Path | None,
    *,
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
    store_kind: str = "full",
    include_ms1: bool = True,
    include_ms2: bool = True,
) -> tuple[object, Path]:
    cache_dir = _stage_cache_dir(
        artifact_root,
        f"shared_scan_store_{store_kind}",
        file_path.name,
        baseline_threads=baseline_threads,
        compare_baseline_errors=compare_baseline_errors,
    )
    if cache_dir is not None:
        if is_complete_memmap_scan_store(cache_dir):
            return open_memmap_scan_store(cache_dir), cache_dir
        file_type = _detect_kind(file_path)
        return (
            create_memmap_scan_store_from_mzml(
                file_path,
                file_type=file_type,
                store_root=cache_dir,
                max_ms1_scans=None,
                include_ms1=include_ms1,
                include_ms2=include_ms2,
                show_progress=False,
            ),
            cache_dir,
        )
    temp_root = Path(tempfile.mkdtemp(prefix="trackcodec_scan_store_"))
    file_type = _detect_kind(file_path)
    return (
        create_memmap_scan_store_from_mzml(
            file_path,
            file_type=file_type,
            store_root=temp_root,
            max_ms1_scans=None,
            include_ms1=include_ms1,
            include_ms2=include_ms2,
            show_progress=False,
        ),
        temp_root,
    )


def _resolve_whole_file_cache_roots(
    artifact_root: str | Path | None,
    file_name: str,
) -> tuple[Path | None, Path | None]:
    if artifact_root is None:
        return None, None
    root = Path(artifact_root) / "_stage_cache"
    scan_candidates = []
    # Whole-file requires both MS1 and MS2, so never reuse an MS1-only
    # shared_scan_store_ms1 as the archive input. Track cache is resolved
    # independently below and can still be reused with a full scan-store.
    for store_name in ("shared_scan_store_full", "shared_scan_store"):
        scan_candidates.extend((root / store_name).glob(f"*/{file_name}/manifest.json"))
    track_candidates = list((root / "ms1").glob(f"*/{file_name}/tracks/manifest.json"))

    scan_store_root = None
    ms1_track_store_root = None
    if scan_candidates:
        scan_manifest = max(scan_candidates, key=lambda path: path.stat().st_mtime)
        scan_store_root = scan_manifest.parent
    if track_candidates:
        track_manifest = max(track_candidates, key=lambda path: path.stat().st_mtime)
        ms1_track_store_root = track_manifest.parent
    return scan_store_root, ms1_track_store_root


def _flatten_method_rows(per_file_map: dict[str, dict]) -> list[dict]:
    rows = []
    for file_name, methods in sorted(per_file_map.items(), key=lambda item: _extract_file_number(item[0])):
        for method, values in _normalise_section_methods_for_reporting(methods).items():
            rows.append({"file": file_name, "method": method, **_json_safe(values)})
    return rows


def _write_section_sidecar(sidecar_dir: Path, file_name: str, section_label: str, result_row: dict):
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    path = sidecar_dir / f"{file_name}.{section_label}_methods.json"
    row = dict(result_row)
    if isinstance(row.get("methods"), dict):
        row["methods"] = _normalise_section_methods_for_reporting(row["methods"])
    path.write_text(json.dumps(_json_safe(row), indent=2, ensure_ascii=False), encoding="utf-8")


def _run_incremental_section_jobs(
    *,
    files: list[Path],
    jobs: int,
    worker,
    task_builder,
    flat_csv_path: Path,
    progress_jsonl_path: Path,
    sidecar_dir: Path,
    section_label: str,
) -> tuple[list[dict], dict[str, dict]]:
    results: list[dict] = []
    per_file_map: dict[str, dict] = {}
    partial_csv_path = flat_csv_path.with_name(flat_csv_path.stem + ".partial.csv")
    if flat_csv_path.exists():
        flat_csv_path.unlink()
    if partial_csv_path.exists():
        partial_csv_path.unlink()
    if progress_jsonl_path.exists():
        progress_jsonl_path.unlink()

    _append_jsonl(
        progress_jsonl_path,
        {
            "event": "section_start",
            "section": section_label,
            "timestamp": _now_iso(),
            "n_files": int(len(files)),
            "jobs": int(max(1, min(jobs, len(files)))),
            "worker_start_method": "spawn",
        },
    )
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=max(1, min(jobs, len(files))), mp_context=ctx) as executor:
        futures = {
            executor.submit(worker, *task_builder(path)): path
            for path in files
        }
        for path in files:
            _append_jsonl(
                progress_jsonl_path,
                {
                    "event": "file_submitted",
                    "section": section_label,
                    "timestamp": _now_iso(),
                    "file": path.name,
                    "file_path": str(path),
                },
            )
        failures = []
        for future in as_completed(futures):
            file_path = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                failures.append((file_path, exc))
                _append_jsonl(
                    progress_jsonl_path,
                    {
                        "event": "file_failed",
                        "section": section_label,
                        "timestamp": _now_iso(),
                        "file": file_path.name,
                        "file_path": str(file_path),
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                    },
                )
                continue
            results.append(row)
            per_file_map[row["file"]] = row["methods"]
            _write_csv(_flatten_method_rows(per_file_map), flat_csv_path)
            _write_csv(_flatten_method_rows(per_file_map), partial_csv_path)
            _write_section_sidecar(sidecar_dir, row["file"], section_label, row)
            method_ratios = {
                method: float(values.get("compression_ratio", 0.0))
                for method, values in row["methods"].items()
            }
            _append_jsonl(
                progress_jsonl_path,
                {
                    "event": "file_done",
                    "section": section_label,
                    "timestamp": _now_iso(),
                    "file": row["file"],
                    "file_path": str(file_path),
                    "n_completed": int(len(per_file_map)),
                    "n_total": int(len(files)),
                    "method_compression_ratio": method_ratios,
                },
            )
    _append_jsonl(
        progress_jsonl_path,
        {
            "event": "section_done",
            "section": section_label,
            "timestamp": _now_iso(),
            "n_files": int(len(files)),
        },
    )
    if failures:
        raise RuntimeError(
            f"{section_label} section failed for {len(failures)} file(s): "
            + ", ".join(path.name for path, _ in failures)
        )
    return results, per_file_map


def _load_dia_ms2_by_window(path: Path):
    grouped = defaultdict(list)
    raw_ms2_bytes = 0
    with mzml.MzML(str(path), read_schema=False, iterative=True, use_index=False, huge_tree=True, decode_binary=True) as reader:
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


def _build_tracks_from_scans(ms1_scans):
    return archive_build_ms1_tracks(
        ms1_scans,
        compact=True,
        drop_islands_in_compact=True,
    )


def _archive_ms1_section_cache_token() -> str:
    payload = {
        "kind": "archive_fidelity_ms1_section",
        "eq_archive_ms1_mode": EQ_ARCHIVE_MS1_MODE,
        "retain_zero_intensity_mz": True,
        "sparse_profile_bypass": DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
        "short_max_points": DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
        "min_zero_fraction": DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
        "min_islands_per_scan": DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
        "min_short_fraction": DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


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
        scan_indices = orphan_entries["scan_indices"]
        array_indices = orphan_entries["array_indices"]
        intensity_values = orphan_entries["intensity_values"]
        for scan_idx, array_idx, intensity in zip(scan_indices, array_indices, intensity_values):
            reconstructed[int(scan_idx)]["intensity_array"][int(array_idx)] = float(intensity)
    return compare_roundtrip_scans(ms1_scans, reconstructed)


def _encode_compare_ms1_archive_fidelity_variant(
    *,
    file_path: Path,
    worker_cfg: dict,
    ms1_scans,
    cache_dir: Path | None,
) -> dict:
    section_cache_dir = cache_dir / "archive_fidelity_ms1_section" if cache_dir is not None else None
    token = _archive_ms1_section_cache_token()
    stats_cache_path = section_cache_dir / f"{token}.json" if section_cache_dir is not None else None
    if stats_cache_path is not None and stats_cache_path.exists():
        cached = _load_json(stats_cache_path)
        if cached is not None:
            _log_worker_stage("MS1", file_path.name, "ours_archive_fidelity_cache_hit")
            return cached

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
    scan_store = getattr(ms1_scans, "store", None)

    _log_worker_stage("MS1", file_path.name, "ours_archive_fidelity_tracks_build_start")
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
    _log_worker_stage("MS1", file_path.name, f"ours_archive_fidelity_tracks_build_done n_tracks={len(tracks)}")

    with tempfile.TemporaryDirectory(prefix="trackcodec_archive_ms1_section_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        section_dir = tmp_dir / "section"
        cache_base = tmp_dir / "cache_base"
        cache_base.mkdir(parents=True, exist_ok=True)
        stage_timings: dict[str, float] = {}
        _log_worker_stage("MS1", file_path.name, "ours_archive_fidelity_encode_start")
        t0 = time.perf_counter()
        (
            ms1_payload_path,
            ms1_meta,
            array_payload_path,
            array_meta,
            orphan_payload_path,
            orphan_meta,
            full_mz_payload_path,
            full_mz_meta,
        ) = codec._encode_ms1_archive_sections_to_paths(
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
        _log_worker_stage("MS1", file_path.name, "ours_archive_fidelity_encode_done")

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
            + (int(full_mz_payload_path.stat().st_size) if bool(full_mz_meta.get("enabled", True)) and full_mz_payload_path.exists() else 0)
            + (int(full_mz_meta_path.stat().st_size) if bool(full_mz_meta.get("enabled", True)) and full_mz_meta_path.exists() else 0)
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
            if bool(full_mz_meta.get("enabled", True)) and full_mz_payload_path.exists()
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
        _log_worker_stage("MS1", file_path.name, "ours_archive_fidelity_decode_done")

        stats = {
            "raw_bytes": int(raw_bytes),
            "compressed_bytes": int(compressed_bytes),
            "compression_ratio": float(raw_bytes / compressed_bytes) if compressed_bytes else 0.0,
            "encode_time_s": float(encode_time),
            "decode_time_s": float(decode_time),
            "max_abs_mz_error": float(errors["max_abs_mz_error"]),
            "max_abs_intensity_error": float(errors["max_abs_intensity_error"]),
            "median_mz_ppm_p95": float(errors["median_mz_ppm_p95"]),
            "median_int_rel_p95": float(errors["median_int_rel_p95"]),
            "segment_sizes": {
                "ms1_payload": int(ms1_payload_path.stat().st_size),
                "ms1_meta": int(ms1_meta_path.stat().st_size),
                "ms1_array_starts_payload": int(array_payload_path.stat().st_size),
                "ms1_array_starts_meta": int(array_meta_path.stat().st_size),
                "ms1_orphan_intensity_payload": int(orphan_payload_path.stat().st_size),
                "ms1_orphan_intensity_meta": int(orphan_meta_path.stat().st_size),
                "ms1_full_mz_payload": int(full_mz_payload_path.stat().st_size) if full_mz_payload_path.exists() else 0,
                "ms1_full_mz_meta": int(full_mz_meta_path.stat().st_size) if full_mz_meta_path.exists() else 0,
            },
            "validation_mode": "archive_fidelity_ms1_section_full_scan_roundtrip",
            "decode_time_scope": "ms1_payload_plus_ms1_sidecars_full_scan_roundtrip",
            "track_storage": type(tracks).__name__,
            "track_prepare_workers": int(worker_cfg["track_prepare_workers"]),
            "encode_section_workers": int(worker_cfg["encode_section_workers"]),
            "ms1_cpu_budget": int(worker_cfg["cpu_budget"]),
            "worker_memory_reason": str(worker_cfg["reason"]),
            "archive_ms1_section": True,
            "ms1_sparse_profile_bypass": _json_safe(sparse_stats),
            "build_ms1_tracks_s": float(build_tracks_time),
            "ms1_payload_mode": archive_ms1_mode,
            "ms1_full_mz_meta": _json_safe(full_mz_meta),
            "ms1_representation": "track_island_archive",
            "ms1_full_mz_compressed_bytes": int(full_mz_meta.get("compressed_bytes", 0) or 0),
            "ms1_array_starts_meta": _json_safe(array_meta),
            "ms1_orphan_intensity_meta": _json_safe(orphan_meta),
            "stage_timings_s": _json_safe(stage_timings),
        }

    if stats_cache_path is not None:
        stats_cache_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(stats_cache_path, stats)
    gc.collect()
    return stats


def _compare_track_domain_roundtrip(tracks, decoded_islands, ms1_scans=None):
    if isinstance(tracks, CompactIslandTracks) and len(tracks.islands) == 0:
        original_count = int(len(tracks.scan_indices))
    else:
        original_count = 0
        if hasattr(tracks, "iter_track_islands"):
            for track_islands in tracks.iter_track_islands():
                original_count += len(track_islands)
        else:
            for track in tracks:
                original_count += len(track.islands)
    if original_count != len(decoded_islands):
        raise ValueError(f"Island count mismatch: {original_count} vs {len(decoded_islands)}")
    max_abs_mz = 0.0
    max_abs_int = 0.0
    ppm_p95 = array("d")
    int_rel_p95 = array("d")

    if isinstance(tracks, CompactIslandTracks) and len(tracks.islands) == 0:
        if ms1_scans is None:
            raise ValueError("CompactIslandTracks without islands requires ms1_scans for roundtrip compare")
        orig_iter = range(int(len(tracks.scan_indices)))
    else:
        original_islands = []
        if hasattr(tracks, "iter_track_islands"):
            for track_islands in tracks.iter_track_islands():
                original_islands.extend(track_islands)
        else:
            for track in tracks:
                original_islands.extend(track.islands)
        orig_iter = original_islands

    for orig_ref, dec_island in zip(orig_iter, decoded_islands):
        if isinstance(tracks, CompactIslandTracks) and len(tracks.islands) == 0:
            island_idx = int(orig_ref)
            scan_idx = int(tracks.scan_indices[island_idx])
            start = int(tracks.array_start_indices[island_idx])
            n_points = int(tracks.n_points[island_idx])
            scan = ms1_scans[scan_idx]
            end = start + n_points
            mz = np.asarray(scan["mz_array"], dtype=np.float64)[start:end]
            intensity = np.asarray(scan["intensity_array"], dtype=np.float64)[start:end]
        else:
            mz = np.asarray(orig_ref.mz_array, dtype=np.float64)
            intensity = np.asarray(orig_ref.intensity_array, dtype=np.float64)
        dec_mz = np.asarray(dec_island["mz_array"], dtype=np.float64)
        dec_int = np.asarray(dec_island["intensity_array"], dtype=np.float64)
        if len(mz) != len(dec_mz) or len(intensity) != len(dec_int):
            scan_label = int(dec_island.get("scan_idx", -1))
            raise ValueError(f"Island length mismatch at scan {scan_label}: {len(mz)}/{len(intensity)} vs {len(dec_mz)}/{len(dec_int)}")
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


def _compare_track_domain_roundtrip_streaming(tracks, decoded_islands_iter, ms1_scans=None, *, validate_mz: bool = True):
    if isinstance(tracks, CompactIslandTracks) and len(tracks.islands) == 0:
        original_count = int(len(tracks.scan_indices))
        if ms1_scans is None:
            raise ValueError("CompactIslandTracks without islands requires ms1_scans for roundtrip compare")
        orig_iter = iter(range(original_count))
    else:
        original_islands = []
        if hasattr(tracks, "iter_track_islands"):
            for track_islands in tracks.iter_track_islands():
                original_islands.extend(track_islands)
        else:
            for track in tracks:
                original_islands.extend(track.islands)
        original_count = int(len(original_islands))
        orig_iter = iter(original_islands)

    decoded_iter = iter(decoded_islands_iter)
    max_abs_mz = 0.0
    max_abs_int = 0.0
    ppm_p95 = array("d")
    int_rel_p95 = array("d")
    decoded_count = 0

    for orig_ref in orig_iter:
        try:
            dec_island = next(decoded_iter)
        except StopIteration as exc:
            raise ValueError(f"Island count mismatch: decoded ended at {decoded_count}, expected {original_count}") from exc
        if isinstance(tracks, CompactIslandTracks) and len(tracks.islands) == 0:
            island_idx = int(orig_ref)
            scan_idx = int(tracks.scan_indices[island_idx])
            start = int(tracks.array_start_indices[island_idx])
            n_points = int(tracks.n_points[island_idx])
            scan = ms1_scans[scan_idx]
            end = start + n_points
            mz = np.asarray(scan["mz_array"], dtype=np.float64)[start:end]
            intensity = np.asarray(scan["intensity_array"], dtype=np.float64)[start:end]
        else:
            mz = np.asarray(orig_ref.mz_array, dtype=np.float64)
            intensity = np.asarray(orig_ref.intensity_array, dtype=np.float64)
        dec_mz = np.asarray(dec_island["mz_array"], dtype=np.float64)
        dec_int = np.asarray(dec_island["intensity_array"], dtype=np.float64)
        if (validate_mz and len(mz) != len(dec_mz)) or len(intensity) != len(dec_int):
            scan_label = int(dec_island.get("scan_idx", -1))
            raise ValueError(
                f"Island length mismatch at scan {scan_label}: "
                f"{len(mz)}/{len(intensity)} vs {len(dec_mz)}/{len(dec_int)}"
            )
        if not validate_mz and int(dec_island.get("n_points", len(dec_int))) != len(intensity):
            scan_label = int(dec_island.get("scan_idx", -1))
            raise ValueError(
                f"Island n_points mismatch at scan {scan_label}: "
                f"{len(intensity)} vs {dec_island.get('n_points')}"
            )
        int_diff = np.abs(intensity - dec_int)
        max_abs_int = max(max_abs_int, float(int_diff.max(initial=0.0)))
        if validate_mz:
            mz_diff = np.abs(mz - dec_mz)
            max_abs_mz = max(max_abs_mz, float(mz_diff.max(initial=0.0)))
            if len(mz):
                ppm_p95.append(float(np.percentile(mz_diff / np.maximum(mz, 1e-12) * 1e6, 95)))
        if len(intensity):
            int_rel_p95.append(float(np.percentile(int_diff / np.maximum(np.abs(intensity), 1.0), 95)))
        decoded_count += 1
        if decoded_count % 500000 == 0:
            gc.collect()

    try:
        next(decoded_iter)
    except StopIteration:
        pass
    else:
        raise ValueError(f"Island count mismatch: decoded has more than expected {original_count}")

    return {
        "max_abs_mz_error": max_abs_mz,
        "max_abs_intensity_error": max_abs_int,
        "median_mz_ppm_p95": float(np.median(ppm_p95)) if ppm_p95 else 0.0,
        "median_int_rel_p95": float(np.median(int_rel_p95)) if int_rel_p95 else 0.0,
        "decoded_island_count": int(decoded_count),
    }


def _compare_ms1_codec_roundtrip_low_memory(codec, payload, metadata, tracks, ms1_scans):
    """Validate reconstructable MS1 without materializing dense decoded scans.

    The full dense reconstruction path is correct but memory-heavy for files with
    millions of short tracks. This checker validates the two pieces that define
    the same reconstruction: full-scan m/z sidecar and decoded non-zero islands.
    """
    t0 = time.perf_counter()
    sidecar_t0 = time.perf_counter()
    max_full_scan_mz = 0.0
    full_scan_ppm_p95 = array("d")
    header, segments = _unpack_segments(payload)
    impl = getattr(codec, "impl", codec)
    omit_island_mz = bool(header.get("omit_island_mz", False))
    has_full_scan_sidecar = bool(header.get("retain_zero_intensity_mz", header.get("preserve_full_scan", False)))
    if has_full_scan_sidecar:
        scans_by_idx = {int(scan["scan_idx"]): scan for scan in ms1_scans}
        sidecar_iter = impl.iter_full_scan_sidecar_arrays(segments, header["segment_meta"])
        for scan_idx, decoded_mz in sidecar_iter:
            scan_idx = int(scan_idx)
            orig_scan = scans_by_idx.get(scan_idx)
            if orig_scan is None:
                raise ValueError(f"Decoded full-scan sidecar references missing scan_idx={scan_idx}")
            orig_mz = np.asarray(orig_scan["mz_array"], dtype=np.float64)
            decoded_mz = np.asarray(decoded_mz, dtype=np.float64)
            if len(orig_mz) != len(decoded_mz):
                raise ValueError(f"Full-scan m/z length mismatch at scan {scan_idx}: {len(orig_mz)} vs {len(decoded_mz)}")
            if len(orig_mz):
                mz_diff = np.abs(orig_mz - decoded_mz)
                max_full_scan_mz = max(max_full_scan_mz, float(np.max(mz_diff)))
                full_scan_ppm_p95.append(float(np.percentile(mz_diff / np.maximum(orig_mz, 1e-12) * 1e6, 95)))
        del scans_by_idx
        gc.collect()
    elif omit_island_mz:
        raise ValueError("omit_island_mz=True requires full-scan m/z sidecar for strict MS1 validation")
    sidecar_compare_time_s = time.perf_counter() - sidecar_t0
    del header
    del segments
    gc.collect()

    track_t0 = time.perf_counter()
    if hasattr(impl, "iter_decoded_islands"):
        errors = _compare_track_domain_roundtrip_streaming(
            tracks,
            impl.iter_decoded_islands(payload, metadata),
            ms1_scans=ms1_scans,
            validate_mz=not omit_island_mz,
        )
        decoded_islands_materialized = False
    else:
        decoded_islands, _, _ = codec.decode(payload, metadata)
        errors = _compare_track_domain_roundtrip(tracks, decoded_islands, ms1_scans=ms1_scans)
        del decoded_islands
        decoded_islands_materialized = True
    decode_time = time.perf_counter() - track_t0
    errors["max_abs_mz_error"] = max(float(errors["max_abs_mz_error"]), float(max_full_scan_mz))
    if omit_island_mz:
        errors["median_mz_ppm_p95"] = float(np.median(full_scan_ppm_p95)) if full_scan_ppm_p95 else 0.0
    errors["decode_time_s"] = float(time.perf_counter() - t0)
    errors["track_decode_time_s"] = float(decode_time)
    errors["full_scan_mz_sidecar_compare_time_s"] = float(sidecar_compare_time_s)
    errors["full_scan_mz_sidecar_max_abs_error"] = float(max_full_scan_mz)
    errors["full_scan_mz_sidecar_median_mz_ppm_p95"] = float(np.median(full_scan_ppm_p95)) if full_scan_ppm_p95 else 0.0
    errors["validation_mode"] = (
        "streaming_full_scan_mz_sidecar_plus_track_intensity_domain"
        if omit_island_mz
        else "streaming_full_scan_mz_sidecar_plus_track_domain"
    )
    errors["decode_time_scope"] = "full_scan_mz_sidecar_decode_compare_plus_track_domain_decode_compare"
    errors["dense_reconstruction_materialized"] = False
    errors["decoded_islands_materialized"] = bool(decoded_islands_materialized)
    gc.collect()
    return errors


def _encode_compare_ms1_variant(
    *,
    file_path: Path,
    label: str,
    mode_cfg: dict,
    worker_cfg: dict,
    tracks,
    ms1_scans,
    cache_dir: Path | None,
    isolated_process: bool,
) -> dict:
    runtime_mode_cfg = dict(mode_cfg)
    runtime_mode_cfg["track_prepare_workers"] = int(worker_cfg["track_prepare_workers"])
    runtime_mode_cfg["encode_section_workers"] = int(worker_cfg["encode_section_workers"])
    if cache_dir is not None and runtime_mode_cfg.get("preserve_full_scan", False):
        runtime_mode_cfg["full_scan_sidecar_cache_dir"] = str(cache_dir / "ms1_full_scan_sidecar_cache")
    codec = MS1Codec(**runtime_mode_cfg)
    _log_worker_stage("MS1", file_path.name, f"{label}_encode_start")
    t0 = time.perf_counter()
    payload, meta = codec.encode(tracks, ms1_scans)
    encode_time = time.perf_counter() - t0
    _log_worker_stage("MS1", file_path.name, f"{label}_encode_done")
    errors = _compare_ms1_codec_roundtrip_low_memory(codec, payload, meta, tracks, ms1_scans)
    decode_time = float(errors.pop("decode_time_s"))
    _log_worker_stage("MS1", file_path.name, f"{label}_decode_done")
    stats = {
        "raw_bytes": int(meta["raw_bytes"]),
        "compressed_bytes": int(meta["compressed_bytes"]),
        "compression_ratio": float(meta["compression_ratio"]),
        "encode_time_s": float(encode_time),
        "decode_time_s": float(decode_time),
        "max_abs_mz_error": float(errors["max_abs_mz_error"]),
        "max_abs_intensity_error": float(errors["max_abs_intensity_error"]),
        "median_mz_ppm_p95": float(errors["median_mz_ppm_p95"]),
        "median_int_rel_p95": float(errors["median_int_rel_p95"]),
        "segment_sizes": dict(meta.get("segment_sizes", {})),
        "track_decode_time_s": float(errors.get("track_decode_time_s", decode_time)),
        "full_scan_mz_sidecar_compare_time_s": float(errors.get("full_scan_mz_sidecar_compare_time_s", 0.0)),
        "full_scan_mz_sidecar_max_abs_error": float(errors.get("full_scan_mz_sidecar_max_abs_error", 0.0)),
        "validation_mode": str(errors.get("validation_mode", "")),
        "decode_time_scope": str(errors.get("decode_time_scope", "")),
        "dense_reconstruction_materialized": bool(errors.get("dense_reconstruction_materialized", False)),
        "decoded_islands_materialized": bool(errors.get("decoded_islands_materialized", False)),
        "decoded_island_count": int(errors.get("decoded_island_count", 0)),
        "track_storage": type(tracks).__name__,
        "track_prepare_workers": int(worker_cfg["track_prepare_workers"]),
        "encode_section_workers": int(worker_cfg["encode_section_workers"]),
        "ms1_cpu_budget": int(worker_cfg["cpu_budget"]),
        "worker_memory_reason": str(worker_cfg["reason"]),
        "variant_process_isolated": bool(isolated_process),
        "variant_worker_pid": int(os.getpid()),
        "ms1_mode_fingerprint": _ms1_mode_fingerprint(label, runtime_mode_cfg),
        "ms1_mode": _json_safe(runtime_mode_cfg),
        "stage_timings_s": _json_safe(meta.get("stage_timings_s", {})),
    }
    _save_stage_stats(cache_dir, label, stats)
    del payload
    del meta
    del codec
    gc.collect()
    _log_worker_stage("MS1", file_path.name, f"{label}_released")
    return stats


def _run_ms1_ours_variant_isolated_worker(
    file_path_str: str,
    artifact_root_str: str,
    baseline_threads: int,
    compare_baseline_errors: bool,
    label: str,
    mode_cfg: dict,
    worker_cfg: dict,
) -> dict:
    file_path = Path(file_path_str)
    artifact_root = Path(artifact_root_str)
    cache_dir = _stage_cache_dir(
        artifact_root,
        "ms1",
        file_path.name,
        baseline_threads=baseline_threads,
        compare_baseline_errors=compare_baseline_errors,
    )
    cached_stats = _load_ms1_variant_stage_stats(cache_dir, label, mode_cfg)
    if cached_stats is not None:
        _log_worker_stage("MS1", file_path.name, f"{label}_isolated_cache_hit")
        return cached_stats

    scan_store, _ = _ensure_scan_store(
        file_path,
        artifact_root,
        baseline_threads=baseline_threads,
        compare_baseline_errors=compare_baseline_errors,
        store_kind="ms1",
        include_ms1=True,
        include_ms2=False,
    )
    ms1_scans = scan_store.ms1_scans()
    track_store_dir = cache_dir / "tracks" if cache_dir is not None else None
    track_manifest = track_store_dir / "manifest.json" if track_store_dir is not None else None
    if track_manifest is not None and track_manifest.exists():
        tracks = open_compact_track_store(track_store_dir)
        _log_worker_stage("MS1", file_path.name, f"{label}_isolated_tracks_cache_hit n_tracks={len(tracks)}")
    else:
        _log_worker_stage("MS1", file_path.name, f"{label}_isolated_tracks_build_start")
        tracks = _build_tracks_from_scans(ms1_scans)
        _log_worker_stage("MS1", file_path.name, f"{label}_isolated_tracks_build_done n_tracks={len(tracks)}")
        if (
            track_store_dir is not None
            and isinstance(tracks, CompactIslandTracks)
            and len(tracks.islands) == 0
        ):
            write_compact_track_store(track_store_dir, tracks)

    try:
        return _encode_compare_ms1_variant(
            file_path=file_path,
            label=label,
            mode_cfg=mode_cfg,
            worker_cfg=worker_cfg,
            tracks=tracks,
            ms1_scans=ms1_scans,
            cache_dir=cache_dir,
            isolated_process=True,
        )
    finally:
        try:
            del tracks
            del ms1_scans
            del scan_store
        except Exception:
            pass
        gc.collect()


def _run_ms1_ours_variant_isolated(
    *,
    file_path: Path,
    artifact_root: str | Path,
    baseline_threads: int,
    compare_baseline_errors: bool,
    label: str,
    mode_cfg: dict,
    worker_cfg: dict,
) -> dict:
    ctx = mp.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as executor:
        future = executor.submit(
            _run_ms1_ours_variant_isolated_worker,
            str(file_path),
            str(artifact_root),
            int(baseline_threads),
            bool(compare_baseline_errors),
            label,
            dict(mode_cfg),
            dict(worker_cfg),
        )
        return future.result()


def benchmark_ms1_validation_file(
    file_path_str: str,
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
    artifact_root: str | Path | None = None,
    ms1_cpu_budget: int = 0,
    ms1_track_prepare_workers: int = 0,
    ms1_encode_section_workers: int = 0,
    min_available_mem_gb: float = 20.0,
):
    file_path = Path(file_path_str)
    print(f"[STACKZDPD][MS1] start {file_path.name}", flush=True)
    worker_cfg = _resolve_ms1_section_workers(
        file_path=file_path,
        cpu_budget=ms1_cpu_budget,
        track_prepare_workers=ms1_track_prepare_workers,
        encode_section_workers=ms1_encode_section_workers,
        min_available_mem_gb=min_available_mem_gb,
    )
    _log_worker_stage(
        "MS1",
        file_path.name,
        (
            "worker_config "
            f"cpu_budget={worker_cfg['cpu_budget']} "
            f"track_prepare_workers={worker_cfg['track_prepare_workers']} "
            f"encode_section_workers={worker_cfg['encode_section_workers']} "
            f"available_mem_gb={worker_cfg['available_mem_gb']:.2f} "
            f"headroom_gb={worker_cfg['headroom_gb']:.2f} "
            f"raw_file_gb={worker_cfg['raw_file_gb']:.3f} "
            f"reason={worker_cfg['reason']}"
        ),
    )
    cache_dir = _stage_cache_dir(
        artifact_root,
        "ms1",
        file_path.name,
        baseline_threads=baseline_threads,
        compare_baseline_errors=compare_baseline_errors,
    )
    temp_scan_store_root = None
    if artifact_root is None:
        ms1_scans, _, _, _ = load_scans(str(file_path), max_ms1_scans=None)
        _log_worker_stage("MS1", file_path.name, f"load_scans_done n_scans={len(ms1_scans)}")
        raw_bytes, raw_backend_stats = _benchmark_raw_backends(ms1_scans, section="MS1", file_name=file_path.name)

        _log_worker_stage("MS1", file_path.name, "zdpd_start")
        zdpd_stats = zdpd_baseline_compress(
            ms1_scans,
            mz_precision=6,
            num_threads=baseline_threads,
            compare_errors=compare_baseline_errors,
        )
        gc.collect()
        _log_worker_stage("MS1", file_path.name, "zdpd_done")

        _log_worker_stage("MS1", file_path.name, "stack_zdpd_start")
        stack_stats = stack_zdpd_baseline_compress(
            ms1_scans,
            mz_precision=6,
            stack_size=256,
            num_threads=baseline_threads,
            compare_errors=compare_baseline_errors,
        )
        gc.collect()
        _log_worker_stage("MS1", file_path.name, "stack_zdpd_done")

        _log_worker_stage("MS1", file_path.name, "tracks_build_start")
        tracks = _build_tracks_from_scans(ms1_scans)
        _log_worker_stage("MS1", file_path.name, f"tracks_build_done n_tracks={len(tracks)}")
    else:
        scan_store, scan_store_root = _ensure_scan_store(
            file_path,
            artifact_root,
            baseline_threads=baseline_threads,
            compare_baseline_errors=compare_baseline_errors,
            store_kind="ms1",
            include_ms1=True,
            include_ms2=False,
        )
        if str(scan_store_root).startswith("/tmp/trackcodec_scan_store_"):
            temp_scan_store_root = scan_store_root
        ms1_scans = scan_store.ms1_scans()
        _log_worker_stage("MS1", file_path.name, f"load_scans_done n_scans={scan_store.ms1_count}")

        raw_cache = _load_stage_stats(cache_dir, "raw_backends")
        if raw_cache is not None and int(raw_cache.get("raw_bytes", -1)) == int(scan_store.raw_ms1_bytes):
            raw_bytes = int(raw_cache["raw_bytes"])
            raw_backend_stats = dict(raw_cache["methods"])
            _log_worker_stage("MS1", file_path.name, "raw_backends_cache_hit")
        else:
            raw_bytes, raw_backend_stats = _benchmark_raw_backends(ms1_scans, section="MS1", file_name=file_path.name)
            _save_stage_stats(cache_dir, "raw_backends", {"raw_bytes": int(raw_bytes), "methods": raw_backend_stats})

        zdpd_stats = _load_stage_stats(cache_dir, "zdpd_baseline")
        if zdpd_stats is None:
            _log_worker_stage("MS1", file_path.name, "zdpd_start")
            zdpd_stats = zdpd_baseline_compress(
                ms1_scans,
                mz_precision=6,
                num_threads=baseline_threads,
                compare_errors=compare_baseline_errors,
            )
            gc.collect()
            _save_stage_stats(cache_dir, "zdpd_baseline", zdpd_stats)
            _log_worker_stage("MS1", file_path.name, "zdpd_done")
        else:
            _log_worker_stage("MS1", file_path.name, "zdpd_cache_hit")
        zdpd_stats = _mark_section_numeric_only_timing(zdpd_stats)
        _save_stage_stats(cache_dir, "zdpd_baseline", zdpd_stats)

        stack_stats = _load_stage_stats(cache_dir, "stack_zdpd_baseline")
        if stack_stats is None:
            _log_worker_stage("MS1", file_path.name, "stack_zdpd_start")
            stack_stats = stack_zdpd_baseline_compress(
                ms1_scans,
                mz_precision=6,
                stack_size=256,
                num_threads=baseline_threads,
                compare_errors=compare_baseline_errors,
            )
            gc.collect()
            _save_stage_stats(cache_dir, "stack_zdpd_baseline", stack_stats)
            _log_worker_stage("MS1", file_path.name, "stack_zdpd_done")
        else:
            _log_worker_stage("MS1", file_path.name, "stack_zdpd_cache_hit")
        stack_stats = _mark_section_numeric_only_timing(stack_stats)
        _save_stage_stats(cache_dir, "stack_zdpd_baseline", stack_stats)

        track_store_dir = cache_dir / "tracks" if cache_dir is not None else None
        track_manifest = track_store_dir / "manifest.json" if track_store_dir is not None else None
        if track_manifest is not None and track_manifest.exists():
            tracks = open_compact_track_store(track_store_dir)
            _log_worker_stage("MS1", file_path.name, f"tracks_cache_hit n_tracks={len(tracks)}")
        else:
            _log_worker_stage("MS1", file_path.name, "tracks_build_start")
            tracks = _build_tracks_from_scans(ms1_scans)
            _log_worker_stage("MS1", file_path.name, f"tracks_build_done n_tracks={len(tracks)}")
            if (
                track_store_dir is not None
                and isinstance(tracks, CompactIslandTracks)
                and len(tracks.islands) == 0
            ):
                write_compact_track_store(track_store_dir, tracks)

    out = {
        "file": file_path.name,
        "raw_bytes": raw_bytes,
        "methods": {
            "raw": {
                "raw_bytes": raw_bytes,
                "compressed_bytes": raw_bytes,
                "compression_ratio": 1.0,
                "encode_time_s": 0.0,
                "decode_time_s": 0.0,
                "max_abs_mz_error": 0.0,
                "max_abs_intensity_error": 0.0,
                "median_mz_ppm_p95": 0.0,
                "median_int_rel_p95": 0.0,
                "segment_sizes": {},
            },
            "gzip": {**raw_backend_stats["gzip"], "segment_sizes": {}},
            "zlib": {**raw_backend_stats["zlib"], "segment_sizes": {}},
            "zstd-9": {**raw_backend_stats["zstd-9"], "segment_sizes": {}},
            "zdpd_baseline": zdpd_stats,
            "stack_zdpd_baseline": stack_stats,
        },
    }

    isolated_variants = artifact_root is not None
    if isolated_variants:
        try:
            del tracks
            del ms1_scans
            del scan_store
        except Exception:
            pass
        gc.collect()
        _log_worker_stage("MS1", file_path.name, "ours_variant_parent_released_before_isolated_workers")

    for label, mode_cfg in (("ours_eqfidelity", EQ_MS1_MODE), ("ours_strict_q6", STRICT_MS1_MODE)):
        cached_stats = _load_ms1_variant_stage_stats(cache_dir, label, mode_cfg)
        if cached_stats is not None:
            out["methods"][label] = cached_stats
            _log_worker_stage("MS1", file_path.name, f"{label}_cache_hit")
            continue
        if isolated_variants:
            _log_worker_stage("MS1", file_path.name, f"{label}_isolated_process_start")
            out["methods"][label] = _run_ms1_ours_variant_isolated(
                file_path=file_path,
                artifact_root=artifact_root,
                baseline_threads=baseline_threads,
                compare_baseline_errors=compare_baseline_errors,
                label=label,
                mode_cfg=mode_cfg,
                worker_cfg=worker_cfg,
            )
            _log_worker_stage("MS1", file_path.name, f"{label}_isolated_process_done")
        else:
            out["methods"][label] = _encode_compare_ms1_variant(
                file_path=file_path,
                label=label,
                mode_cfg=mode_cfg,
                worker_cfg=worker_cfg,
                tracks=tracks,
                ms1_scans=ms1_scans,
                cache_dir=cache_dir,
                isolated_process=False,
            )
    if isolated_variants:
        archive_scan_store, _ = _ensure_scan_store(
            file_path,
            artifact_root,
            baseline_threads=baseline_threads,
            compare_baseline_errors=compare_baseline_errors,
            store_kind="ms1",
            include_ms1=True,
            include_ms2=False,
        )
        archive_ms1_scans = archive_scan_store.ms1_scans()
        try:
            archive_fidelity_stats = _encode_compare_ms1_archive_fidelity_variant(
                file_path=file_path,
                worker_cfg=worker_cfg,
                ms1_scans=archive_ms1_scans,
                cache_dir=cache_dir,
            )
        finally:
            try:
                del archive_ms1_scans
                del archive_scan_store
            except Exception:
                pass
            gc.collect()
    else:
        archive_fidelity_stats = _encode_compare_ms1_archive_fidelity_variant(
            file_path=file_path,
            worker_cfg=worker_cfg,
            ms1_scans=ms1_scans,
            cache_dir=cache_dir,
        )
    out["methods"]["ours_archive_fidelity"] = archive_fidelity_stats
    _normalise_ms1_method_timings(out["methods"], cache_dir)
    if not isolated_variants:
        del tracks
        del ms1_scans
    if temp_scan_store_root is not None:
        try:
            shutil.rmtree(temp_scan_store_root, ignore_errors=True)
        except Exception:
            pass
    gc.collect()
    print(f"[STACKZDPD][MS1] done {file_path.name}", flush=True)
    return out


def benchmark_metadata_validation_file(file_path_str: str):
    file_path = Path(file_path_str)
    print(f"[STACKZDPD][META] start {file_path.name}", flush=True)
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
    ours_payload, ours_meta = metadata_codec.encode_bytes(raw_blob)
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
                "raw_bytes": int(ours_meta["raw_bytes"]),
                "compressed_bytes": int(ours_meta["compressed_bytes"]),
                "compression_ratio": float(ours_meta["compression_ratio"]),
                "encode_time_s": float(ours_encode),
                "decode_time_s": float(ours_decode),
            },
        },
    }
    print(f"[STACKZDPD][META] done {file_path.name}", flush=True)
    return out


def benchmark_ms2_validation_file(
    file_path_str: str,
    baseline_threads: int = 0,
    compare_baseline_errors: bool = False,
    artifact_root: str | Path | None = None,
    ms2_section_workers: int = 0,
    ms2_segment_workers: int = 0,
):
    file_path = Path(file_path_str)
    temp_scan_store_root = None
    scan_store = None
    if artifact_root is None:
        file_type = _detect_kind(file_path)
    else:
        scan_store, scan_store_root = _ensure_scan_store(
            file_path,
            artifact_root,
            baseline_threads=baseline_threads,
            compare_baseline_errors=compare_baseline_errors,
            store_kind="ms2",
            include_ms1=False,
            include_ms2=True,
        )
        if str(scan_store_root).startswith("/tmp/trackcodec_scan_store_"):
            temp_scan_store_root = scan_store_root
        file_type = str(scan_store.file_type)
    cache_dir = _stage_cache_dir(
        artifact_root,
        "ms2",
        file_path.name,
        baseline_threads=baseline_threads,
        compare_baseline_errors=compare_baseline_errors,
    )
    print(f"[STACKZDPD][MS2] start {file_path.name} type={file_type}", flush=True)
    if file_type == "DIA":
        if scan_store is None:
            ms2_by_window, raw_ms2_bytes = _load_dia_ms2_by_window(file_path)
            window_items = _sorted_dia_window_items(ms2_by_window)
        else:
            ms2_by_window = None
            window_items = scan_store.dia_ms2_window_items()
            raw_ms2_bytes = int(scan_store.raw_ms2_bytes)
        flat_scans = []
        for _key, scans in window_items:
            flat_scans.extend(scans)
        raw_cache = _load_stage_stats(cache_dir, "raw_backends")
        if raw_cache is not None and int(raw_cache.get("raw_bytes", -1)) == int(raw_ms2_bytes):
            raw_bytes = int(raw_cache["raw_bytes"])
            raw_backend_stats = dict(raw_cache["methods"])
            _log_worker_stage("MS2", file_path.name, "raw_backends_cache_hit")
        else:
            raw_bytes, raw_backend_stats = _benchmark_raw_backends(flat_scans, section="MS2", file_name=file_path.name)
            _save_stage_stats(cache_dir, "raw_backends", {"raw_bytes": int(raw_bytes), "methods": raw_backend_stats})
        raw = {"raw_bytes": raw_bytes, "compressed_bytes": raw_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}

        zdpd_stats = _load_stage_stats(cache_dir, "zdpd_baseline")
        if zdpd_stats is None:
            _log_worker_stage("MS2", file_path.name, "zdpd_start")
            zdpd_stats = zdpd_baseline_compress(
                flat_scans,
                mz_precision=6,
                num_threads=baseline_threads,
                compare_errors=compare_baseline_errors,
            )
            gc.collect()
            _save_stage_stats(cache_dir, "zdpd_baseline", zdpd_stats)
            _log_worker_stage("MS2", file_path.name, "zdpd_done")
        else:
            _log_worker_stage("MS2", file_path.name, "zdpd_cache_hit")
        zdpd_stats = _mark_section_numeric_only_timing(zdpd_stats)
        _save_stage_stats(cache_dir, "zdpd_baseline", zdpd_stats)
        stack_stats = _load_stage_stats(cache_dir, "stack_zdpd_baseline")
        if stack_stats is None:
            _log_worker_stage("MS2", file_path.name, "stack_zdpd_start")
            stack_stats = stack_zdpd_baseline_compress(
                flat_scans,
                mz_precision=6,
                stack_size=256,
                num_threads=baseline_threads,
                compare_errors=compare_baseline_errors,
            )
            gc.collect()
            _save_stage_stats(cache_dir, "stack_zdpd_baseline", stack_stats)
            _log_worker_stage("MS2", file_path.name, "stack_zdpd_done")
        else:
            _log_worker_stage("MS2", file_path.name, "stack_zdpd_cache_hit")
        stack_stats = _mark_section_numeric_only_timing(stack_stats)
        _save_stage_stats(cache_dir, "stack_zdpd_baseline", stack_stats)

        ours_stats = _load_stage_stats(cache_dir, "ours_eqfidelity")
        if ours_stats is None:
            cfg_kwargs = {}
            if int(ms2_section_workers) > 0:
                cfg_kwargs["dia_section_workers"] = max(1, int(ms2_section_workers))
            if int(ms2_segment_workers) > 0:
                cfg_kwargs["section_segment_workers"] = max(1, int(ms2_segment_workers))
            codec = DIAWindowMS2Codec(MS2ModeConfig.exact_track_dia_current(**cfg_kwargs))
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_encode_start")
            t0 = time.perf_counter()
            ours = codec.encode_dia_windows(window_items)
            ours_encode = time.perf_counter() - t0
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_encode_done")
            dt0 = time.perf_counter()
            decoded = codec.decode_dia_windows(ours)
            ours_decode = time.perf_counter() - dt0
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_decode_done")
            compare_rows = []
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
                "segment_sizes": dict(_collect_ms2_segment_sizes(ours)),
                "file_type": "DIA",
            }
            _save_stage_stats(cache_dir, "ours_eqfidelity", ours_stats)
            del decoded
            del ours
            del codec
            del compare_rows
            gc.collect()
        else:
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_cache_hit")
    else:
        if scan_store is None:
            flat_scans, raw_ms2_bytes = load_all_ms2_scans(str(file_path), max_ms2_scans=0)
        else:
            flat_scans = scan_store.dda_ms2_scans()
            raw_ms2_bytes = int(scan_store.raw_ms2_bytes)
        raw_cache = _load_stage_stats(cache_dir, "raw_backends")
        if raw_cache is not None and int(raw_cache.get("raw_bytes", -1)) == int(raw_ms2_bytes):
            raw_bytes = int(raw_cache["raw_bytes"])
            raw_backend_stats = dict(raw_cache["methods"])
            _log_worker_stage("MS2", file_path.name, "raw_backends_cache_hit")
        else:
            raw_bytes, raw_backend_stats = _benchmark_raw_backends(flat_scans, section="MS2", file_name=file_path.name)
            _save_stage_stats(cache_dir, "raw_backends", {"raw_bytes": int(raw_bytes), "methods": raw_backend_stats})
        raw = {"raw_bytes": raw_bytes, "compressed_bytes": raw_bytes, "compression_ratio": 1.0, "encode_time_s": 0.0, "decode_time_s": 0.0}

        zdpd_stats = _load_stage_stats(cache_dir, "zdpd_baseline")
        if zdpd_stats is None:
            _log_worker_stage("MS2", file_path.name, "zdpd_start")
            zdpd_stats = zdpd_baseline_compress(
                flat_scans,
                mz_precision=6,
                num_threads=baseline_threads,
                compare_errors=compare_baseline_errors,
            )
            gc.collect()
            _save_stage_stats(cache_dir, "zdpd_baseline", zdpd_stats)
            _log_worker_stage("MS2", file_path.name, "zdpd_done")
        else:
            _log_worker_stage("MS2", file_path.name, "zdpd_cache_hit")
        zdpd_stats = _mark_section_numeric_only_timing(zdpd_stats)
        _save_stage_stats(cache_dir, "zdpd_baseline", zdpd_stats)
        stack_stats = _load_stage_stats(cache_dir, "stack_zdpd_baseline")
        if stack_stats is None:
            _log_worker_stage("MS2", file_path.name, "stack_zdpd_start")
            stack_stats = stack_zdpd_baseline_compress(
                flat_scans,
                mz_precision=6,
                stack_size=256,
                num_threads=baseline_threads,
                compare_errors=compare_baseline_errors,
            )
            gc.collect()
            _save_stage_stats(cache_dir, "stack_zdpd_baseline", stack_stats)
            _log_worker_stage("MS2", file_path.name, "stack_zdpd_done")
        else:
            _log_worker_stage("MS2", file_path.name, "stack_zdpd_cache_hit")
        stack_stats = _mark_section_numeric_only_timing(stack_stats)
        _save_stage_stats(cache_dir, "stack_zdpd_baseline", stack_stats)

        ours_stats = _load_stage_stats(cache_dir, "ours_eqfidelity")
        if ours_stats is None:
            cfg_kwargs = {}
            if int(ms2_section_workers) > 0:
                cfg_kwargs["dia_section_workers"] = max(1, int(ms2_section_workers))
            if int(ms2_segment_workers) > 0:
                cfg_kwargs["section_segment_workers"] = max(1, int(ms2_segment_workers))
            codec = DIAWindowMS2Codec(MS2ModeConfig.exact_track_dda_current(**cfg_kwargs))
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_encode_start")
            t0 = time.perf_counter()
            ours = codec.encode_dda_blocks(flat_scans)
            ours_encode = time.perf_counter() - t0
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_encode_done")
            dt0 = time.perf_counter()
            decoded = codec.decode_dda_blocks(ours)
            ours_decode = time.perf_counter() - dt0
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_decode_done")
            block_compare = compare_centroid_scans(flat_scans, decoded)
            ours_stats = {
                "raw_bytes": ours["raw_bytes"],
                "compressed_bytes": ours["compressed_bytes"],
                "compression_ratio": ours["compression_ratio"],
                "encode_time_s": ours_encode,
                "decode_time_s": ours_decode,
                "max_abs_intensity_error": block_compare["max_abs_intensity_error"],
                "max_abs_mz_error": block_compare["max_abs_mz_error"],
                "median_mz_ppm_p95": block_compare["median_mz_ppm_p95"],
                "median_int_rel_p95": block_compare["median_int_rel_p95"],
                "segment_sizes": dict(_collect_ms2_segment_sizes(ours)),
                "file_type": "DDA",
            }
            _save_stage_stats(cache_dir, "ours_eqfidelity", ours_stats)
            del decoded
            del ours
            del codec
            del block_compare
            gc.collect()
        else:
            _log_worker_stage("MS2", file_path.name, "ours_eqfidelity_cache_hit")

    out = {
        "file": file_path.name,
        "raw_bytes": raw["raw_bytes"],
        "methods": {
            "raw": raw,
            "gzip": raw_backend_stats["gzip"],
            "zlib": raw_backend_stats["zlib"],
            "zstd-9": raw_backend_stats["zstd-9"],
            "zdpd_baseline": zdpd_stats,
            "stack_zdpd_baseline": stack_stats,
            "ours_eqfidelity": ours_stats,
        },
    }
    del flat_scans
    if file_type == "DIA":
        del ms2_by_window
    if scan_store is not None:
        del scan_store
    if temp_scan_store_root is not None:
        try:
            shutil.rmtree(temp_scan_store_root, ignore_errors=True)
        except Exception:
            pass
    gc.collect()
    print(f"[STACKZDPD][MS2] done {file_path.name}", flush=True)
    return out


def _aggregate_section(per_file_map, order):
    out = {}
    for label in order:
        vals = [method_map[label]["compression_ratio"] for method_map in per_file_map.values() if label in method_map]
        enc = [
            _optional_float(_promote_section_numeric_timing_fields(method_map[label]).get("encode_time_s"))
            for method_map in per_file_map.values()
            if label in method_map
        ]
        dec = [
            _optional_float(_promote_section_numeric_timing_fields(method_map[label]).get("decode_time_s"))
            for method_map in per_file_map.values()
            if label in method_map
        ]
        enc = [value for value in enc if value is not None]
        dec = [value for value in dec if value is not None]
        out[label] = {
            "mean_cr": statistics.mean(vals) if vals else 0.0,
            "median_cr": statistics.median(vals) if vals else 0.0,
            "mean_encode_time_s": statistics.mean(enc) if enc else None,
            "mean_decode_time_s": statistics.mean(dec) if dec else None,
            "time_scope_available": bool(enc and dec),
        }
    return out


def _make_bar_rows(order, aggregate_map, family_map=None):
    rows = []
    for label in order:
        item = aggregate_map[label]
        rows.append(
            {
                "label": label,
                "display_name": DISPLAY_NAME[label],
                "mean_cr": item["mean_cr"],
                "median_cr": item["median_cr"],
                "mean_encode_time_s": item["mean_encode_time_s"],
                "mean_decode_time_s": item["mean_decode_time_s"],
                "time_scope_available": bool(item.get("time_scope_available", True)),
                "color_hex": COLOR_MAP[label],
                "family": family_map.get(label, "near") if family_map else "near",
                "bar_annotation": item.get("bar_annotation", f"{item['mean_cr']:.2f}x"),
            }
        )
    return rows


def _make_line_rows(order, per_file_map, alias_map: dict[str, str]):
    rows = []
    for file_name, method_map in sorted(per_file_map.items(), key=lambda item: _extract_file_number(item[0])):
        for label in order:
            if label not in method_map:
                continue
            rows.append(
                {
                    "file": file_name,
                    "file_label": alias_map[file_name],
                    "label": label,
                    "display_name": DISPLAY_NAME[label],
                    "compression_ratio": method_map[label]["compression_ratio"],
                    "color_hex": COLOR_MAP[label],
                    "family": SECTION_FAMILY_MAP.get(label, "near"),
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


def _plot_method_boxplot(line_rows, order, output_png: Path, title: str):
    if not line_rows:
        return
    fig, ax = plt.subplots(figsize=(11.2, 7.8))
    grouped = defaultdict(list)
    for row in line_rows:
        grouped[row["label"]].append(float(row["compression_ratio"]))
    plot_order = [label for label in order if label in grouped]
    positions = np.arange(len(plot_order))
    data = [grouped[label] for label in plot_order]
    box = ax.boxplot(data, vert=False, positions=positions, widths=0.6, patch_artist=True, showfliers=False)
    for patch, label in zip(box["boxes"], plot_order):
        patch.set_facecolor(COLOR_MAP[label])
        patch.set_alpha(0.35)
        patch.set_edgecolor(COLOR_MAP[label])
    rng = np.random.default_rng(20260407)
    for pos, label in zip(positions, plot_order):
        vals = grouped[label]
        jitter = rng.uniform(-0.12, 0.12, size=len(vals))
        ax.scatter(vals, np.full(len(vals), pos) + jitter, color=COLOR_MAP[label], s=36, alpha=0.85, zorder=3)
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
    palette = plt.cm.tab20(np.linspace(0, 1, max(3, len(by_file))))
    for color, (file_label, vals) in zip(palette, sorted(by_file.items(), key=lambda item: _extract_file_number(item[0]))):
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


def _summarize_section(section_name: str, per_file_map: dict, order: list[str], out_root: Path, alias_map: dict[str, str], reference_label: str | None):
    comp_dir = out_root / "compression_results"
    plot_data_dir = out_root / "plot_data"
    table_dir = out_root / "comparison_tables"
    plots_dir = out_root / "plots"
    for path in (comp_dir, plot_data_dir, table_dir, plots_dir):
        path.mkdir(parents=True, exist_ok=True)

    aggregate_map = _aggregate_section(per_file_map, order)
    bar_rows = _make_bar_rows(order, aggregate_map, family_map=SECTION_FAMILY_MAP)
    line_rows = _make_line_rows(order, per_file_map, alias_map)
    compare_rows = _make_compare_table_rows(section_name, order, aggregate_map, reference_label)

    speed_rows = []
    for row in bar_rows:
        speed_rows.append(
            {
                "label": row["label"],
                "display_name": row["display_name"],
                "mean_encode_time_s": row["mean_encode_time_s"],
                "mean_decode_time_s": row["mean_decode_time_s"],
                "time_scope_available": row.get("time_scope_available", True),
                "color_hex": row["color_hex"],
                "family": row["family"],
            }
        )

    _write_csv(bar_rows, plot_data_dir / f"{section_name}_compression_bar_data.csv")
    _write_csv(line_rows, plot_data_dir / f"{section_name}_compression_line_data.csv")
    _write_csv(speed_rows, plot_data_dir / f"{section_name}_speed_bar_data.csv")
    _write_csv(bar_rows, comp_dir / f"{section_name}_aggregate.csv")
    _write_csv(compare_rows, table_dir / f"{section_name}_compare_table.csv")

    plot_compression_bar_custom(
        bar_rows,
        plots_dir / f"{section_name}_compression_comparison.png",
        title=f"{section_name.replace('_', ' ').upper()} Mean Compression Ratio By Method",
        figsize=(11.5, 8.5),
        annotation_fontsize=14,
    )
    plot_compression_line_custom(
        line_rows,
        plots_dir / f"{section_name}_compression_line.png",
        title=f"{section_name.replace('_', ' ').upper()} Per-file Compression Ratio By Method",
        figsize=(11.0, 7.8),
        legend_loc="center left",
        legend_bbox=(1.01, 0.5),
        annotation_fontsize=10,
    )
    plot_speed_bar(
        speed_rows,
        plots_dir / f"{section_name}_speed_bar.png",
        title_encode=f"{section_name.replace('_', ' ').upper()} Mean Encode Time By Method",
        title_decode=f"{section_name.replace('_', ' ').upper()} Mean Decode Time By Method",
    )
    _plot_method_boxplot(
        line_rows,
        order,
        plots_dir / f"{section_name}_compression_boxplot.png",
        title=f"{section_name.replace('_', ' ').upper()} Compression Ratio Distribution By Method",
    )
    _plot_file_lines_by_method(
        line_rows,
        order,
        plots_dir / f"{section_name}_per_file_method_line.png",
        title=f"{section_name.replace('_', ' ').upper()} Per-file Compression Ratio Across Methods",
    )
    return bar_rows


def _compress_file_streaming(input_path: Path, method: str, tmp_dir: Path) -> dict:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    output_path = tmp_dir / f"{input_path.name}.{method}.tmp"
    decoded_path = tmp_dir / f"{input_path.name}.{method}.decoded.mzML"
    raw_bytes = int(input_path.stat().st_size)

    if method == "raw_file":
        return {
            "raw_bytes": raw_bytes,
            "compressed_bytes": raw_bytes,
            "compression_ratio": 1.0,
            "encode_time_s": 0.0,
            "decode_time_s": 0.0,
            "encode_time_scope": RAW_FILE_ENCODE_TIME_SCOPE,
            "decode_time_scope": RAW_FILE_DECODE_TIME_SCOPE,
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            "decoded_bytes_checked": raw_bytes,
            "validated_roundtrip": False,
        }

    if method == "gzip_file":
        t0 = time.perf_counter()
        with input_path.open("rb") as src, gzip.open(output_path, "wb", compresslevel=6) as dst:
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                dst.write(chunk)
        encode_time_s = time.perf_counter() - t0
        dt0 = time.perf_counter()
        with gzip.open(output_path, "rb") as src, decoded_path.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=CHUNK_SIZE)
        decode_time_s = time.perf_counter() - dt0
    elif method == "zlib_file":
        t0 = time.perf_counter()
        compressor = zlib.compressobj(level=6)
        with input_path.open("rb") as src, output_path.open("wb") as dst:
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                comp = compressor.compress(chunk)
                if comp:
                    dst.write(comp)
            tail = compressor.flush()
            if tail:
                dst.write(tail)
        encode_time_s = time.perf_counter() - t0
        dt0 = time.perf_counter()
        decompressor = zlib.decompressobj()
        with output_path.open("rb") as src, decoded_path.open("wb") as dst:
            while True:
                chunk = src.read(CHUNK_SIZE)
                if not chunk:
                    break
                raw_piece = decompressor.decompress(chunk)
                if raw_piece:
                    dst.write(raw_piece)
            tail = decompressor.flush()
            if tail:
                dst.write(tail)
        decode_time_s = time.perf_counter() - dt0
    elif method == "zstd-9_file":
        t0 = time.perf_counter()
        cctx = zstd.ZstdCompressor(level=9)
        with input_path.open("rb") as src, output_path.open("wb") as dst:
            with cctx.stream_writer(dst) as writer:
                while True:
                    chunk = src.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    writer.write(chunk)
        encode_time_s = time.perf_counter() - t0
        dt0 = time.perf_counter()
        dctx = zstd.ZstdDecompressor()
        with output_path.open("rb") as src, decoded_path.open("wb") as dst:
            with dctx.stream_reader(src) as reader:
                shutil.copyfileobj(reader, dst, length=CHUNK_SIZE)
        decode_time_s = time.perf_counter() - dt0
    else:
        raise ValueError(f"Unsupported method: {method}")

    compressed_bytes = int(output_path.stat().st_size)
    decoded_bytes = int(decoded_path.stat().st_size)
    output_path.unlink(missing_ok=True)
    decoded_path.unlink(missing_ok=True)
    if decoded_bytes != raw_bytes:
        raise RuntimeError(f"{method} decoded byte count mismatch: {decoded_bytes} != {raw_bytes}")
    return {
        "raw_bytes": raw_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": raw_bytes / compressed_bytes if compressed_bytes else 0.0,
        "encode_time_s": encode_time_s,
        "decode_time_s": decode_time_s,
        "encode_time_scope": FILE_STREAM_ENCODE_TIME_SCOPE,
        "decode_time_scope": FILE_STREAM_DECODE_TIME_SCOPE,
        "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        "decoded_bytes_checked": int(decoded_bytes),
        "validated_roundtrip": False,
    }


def _auxiliary_zlib_stats(file_path: Path) -> dict:
    records = extract_auxiliary_binary_records(str(file_path))
    raw = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    t0 = time.perf_counter()
    payload = zlib.compress(raw)
    encode_time_s = time.perf_counter() - t0
    dt0 = time.perf_counter()
    zlib.decompress(payload)
    decode_time_s = time.perf_counter() - dt0
    return {
        "raw_bytes": len(raw),
        "compressed_bytes": len(payload),
        "encode_time_s": encode_time_s,
        "decode_time_s": decode_time_s,
    }


def _roundtrip_within(summary: dict) -> tuple[bool, bool]:
    structure_ok = bool(summary.get("structure_within_ceiling", True))
    if "structure_within_ceiling" not in summary:
        structure_ok = (
            int(summary.get("missing_original_spectra", 0)) == 0
            and int(summary.get("missing_reconstructed_spectra", 0)) == 0
            and int(summary.get("spectrum_id_mismatch_count", 0)) == 0
            and int(summary.get("ms_level_mismatch_count", 0)) == 0
            and int(summary.get("rt_mismatch_count", 0)) == 0
            and int(summary.get("array_length_mismatch_count", 0)) == 0
        )
    mz_ok = structure_ok and float(summary["max_abs_mz_error"]) <= (MZ_ERROR_CEILING + CEILING_EPS)
    effective_int_ceiling = float(summary.get("effective_intensity_error_ceiling", INT_ERROR_CEILING))
    int_ok = structure_ok and float(summary["max_abs_intensity_error"]) <= (effective_int_ceiling + CEILING_EPS)
    return mz_ok, int_ok


def benchmark_whole_archive_file(
    file_path_str: str,
    archive_dir_str: str,
    recon_dir_str: str,
    roundtrip_dir_str: str,
    comp_dir_str: str,
    ms2_section_workers: int = 8,
    ms2_segment_workers: int = 0,
    archive_variant: str = "eqfidelity",
    run_mode: str = "all",
    progress_log_str: str | None = None,
    artifact_root: str | Path | None = None,
) -> dict:
    file_path = Path(file_path_str)
    archive_dir = Path(archive_dir_str)
    recon_dir = Path(recon_dir_str)
    roundtrip_dir = Path(roundtrip_dir_str)
    comp_dir = Path(comp_dir_str)
    archive_dir.mkdir(parents=True, exist_ok=True)
    recon_dir.mkdir(parents=True, exist_ok=True)
    roundtrip_dir.mkdir(parents=True, exist_ok=True)
    comp_dir.mkdir(parents=True, exist_ok=True)
    if run_mode not in WHOLE_RUN_MODE_CHOICES:
        raise ValueError(f"Unsupported run_mode: {run_mode}")
    progress_log_path = Path(progress_log_str) if progress_log_str else None

    if archive_variant == "eqfidelity":
        archive_label = "ours_whole_archive"
        archive_path = archive_dir / archive_name(file_path.name)
        reconstructed_path = recon_dir / f"{file_path.name}.reconstructed.mzML"
        summary_path = roundtrip_dir / f"{file_path.name}.whole_archive_roundtrip.json"
        stats_path = comp_dir / f"{file_path.name}.whole_archive_stats.json"
        per_spectrum_csv = roundtrip_dir / f"{file_path.name}.whole_archive_per_spectrum.csv"
        ms1_mode = EQ_ARCHIVE_MS1_MODE
    elif archive_variant == "strict_q6":
        archive_label = "ours_strict_q6_whole_archive"
        archive_path = archive_dir / archive_name(file_path.name, strict_q6=True)
        reconstructed_path = recon_dir / f"{file_path.name}.strict_q6.reconstructed.mzML"
        summary_path = roundtrip_dir / f"{file_path.name}.strict_q6.whole_archive_roundtrip.json"
        stats_path = comp_dir / f"{file_path.name}.strict_q6.whole_archive_stats.json"
        per_spectrum_csv = roundtrip_dir / f"{file_path.name}.strict_q6.whole_archive_per_spectrum.csv"
        ms1_mode = STRICT_ARCHIVE_MS1_MODE
    else:
        raise ValueError(f"Unsupported archive_variant: {archive_variant}")

    existing_summary = _load_json(summary_path)
    existing_stats = _load_json(stats_path)
    timing_cache_valid = _whole_archive_timing_cache_valid(existing_stats)
    encode_complete = bool(timing_cache_valid and existing_stats and existing_stats.get("encode_completed") and archive_path.exists())
    decode_complete = _whole_archive_decode_checkpoint_valid(existing_stats, reconstructed_path)
    compare_complete = bool(
        timing_cache_valid
        and existing_stats
        and existing_stats.get("compare_completed")
        and existing_stats.get("decode_completed")
        and reconstructed_path.exists()
        and summary_path.exists()
        and per_spectrum_csv.exists()
    )

    def _emit_progress(event: str, **extra):
        if progress_log_path is None:
            return
        _append_jsonl(
            progress_log_path,
            {
                "event": event,
                "timestamp": _now_iso(),
                "file": file_path.name,
                "method": archive_label,
                "archive_variant": archive_variant,
                "run_mode": run_mode,
                **extra,
            },
        )

    progress_state = {
        "stage": None,
        "overall_bucket": -1,
    }

    def _emit_encode_inner_progress(item: dict) -> None:
        stage = str(item.get("stage", "unknown"))
        overall_pct = float(item.get("overall_pct", 0.0) or 0.0)
        ms1_pct = float(item.get("ms1_pct", 0.0) or 0.0)
        ms2_pct = float(item.get("ms2_pct", 0.0) or 0.0)
        metadata_pct = float(item.get("metadata_pct", 0.0) or 0.0)
        _emit_progress(
            "encode_progress",
            scope=item.get("scope"),
            stage=stage,
            overall_pct=overall_pct,
            ms1_pct=ms1_pct,
            ms2_pct=ms2_pct,
            metadata_pct=metadata_pct,
            section=item.get("section"),
            done_tracks=item.get("done_tracks"),
            total_tracks=item.get("total_tracks"),
            done_sections=item.get("done_sections"),
            total_sections=item.get("total_sections"),
            done_units=item.get("done_units"),
            total_units=item.get("total_units"),
            unit_kind=item.get("unit_kind"),
        )
        overall_bucket = int(overall_pct // 5.0)
        if stage != progress_state["stage"] or overall_bucket > progress_state["overall_bucket"]:
            print(
                (
                    f"[STACKZDPD][WHOLE] encode-progress {file_path.name} variant={archive_variant} "
                    f"stage={stage} overall={overall_pct:.1f}% "
                    f"ms1={ms1_pct:.1f}% ms2={ms2_pct:.1f}% metadata={metadata_pct:.1f}%"
                ),
                flush=True,
            )
            progress_state["stage"] = stage
            progress_state["overall_bucket"] = overall_bucket

    if run_mode == "validate_only" and not encode_complete:
        raise FileNotFoundError(f"validate_only requires existing encoded archive + stats: {file_path.name} [{archive_variant}]")
    if run_mode == "encode_only" and encode_complete:
        _emit_progress("encode_cache_hit")
        return {
            "file": file_path.name,
            "method": archive_label,
            "archive_variant": archive_variant,
            "archive_path": str(archive_path),
            "reconstructed_path": str(reconstructed_path),
            "summary": existing_summary,
            "stats": existing_stats,
        }
    if run_mode == "all" and compare_complete:
        _emit_progress("all_cache_hit")
        return {
            "file": file_path.name,
            "method": archive_label,
            "archive_variant": archive_variant,
            "archive_path": str(archive_path),
            "reconstructed_path": str(reconstructed_path),
            "summary": existing_summary,
            "stats": existing_stats,
        }

    codec = MzMLSectionArchiveCodec(
        ms1_mode=ms1_mode,
        ms2_section_workers=ms2_section_workers,
        ms2_segment_workers=ms2_segment_workers if int(ms2_segment_workers) > 0 else None,
    )
    archive_meta = existing_summary.get("archive_meta") if existing_summary else None
    if archive_meta is None and existing_stats is not None:
        archive_meta = existing_stats.get("archive_meta")
    encode_time_s = float(existing_stats.get("encode_time_s", 0.0)) if existing_stats else 0.0
    decode_time_s = _optional_float(existing_stats.get("decode_time_s")) if existing_stats else None

    if run_mode in ("all", "encode_only") and not encode_complete:
        print(f"[STACKZDPD][WHOLE] encode-start {file_path.name} variant={archive_variant}", flush=True)
        _emit_progress("encode_start")
        t0 = time.perf_counter()
        _, archive_meta = codec.encode_file_to_path(
            file_path,
            archive_path,
            progress_callback=_emit_encode_inner_progress,
        )
        encode_time_s = time.perf_counter() - t0
        print(f"[STACKZDPD][WHOLE] encode-done {file_path.name} variant={archive_variant} seconds={encode_time_s:.3f}", flush=True)
        _emit_progress("encode_done", encode_time_s=float(encode_time_s), archive_bytes=int(archive_path.stat().st_size))

        stats = {
            "file": file_path.name,
            "method": archive_label,
            "archive_variant": archive_variant,
            "archive_meta": _json_safe(archive_meta),
            "raw_bytes": int(file_path.stat().st_size),
            "compressed_bytes": int(archive_path.stat().st_size),
            "compression_ratio": float(file_path.stat().st_size) / float(archive_path.stat().st_size),
            "encode_time_s": encode_time_s,
            "decode_time_s": None,
            "encode_time_scope": WHOLE_ARCHIVE_ENCODE_TIME_SCOPE,
            "decode_time_scope": WHOLE_ARCHIVE_DECODE_TIME_SCOPE,
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            "file_type": archive_meta["file_type"],
            "ms1_compressed_bytes": int(archive_meta["ms1"]["compressed_bytes"]),
            "ms2_compressed_bytes": int(archive_meta["ms2"]["compressed_bytes"]),
            "metadata_compressed_bytes": int(archive_meta["metadata"]["compressed_bytes"]),
            "auxiliary_compressed_bytes": int(archive_meta["auxiliary"]["compressed_bytes"]),
            "ms1_full_mz_compressed_bytes": int(archive_meta["ms1_full_mz"]["compressed_bytes"]),
            "stage_timings_s": _json_safe(_archive_stage_timings_from_meta(archive_meta)),
            "encode_completed": True,
            "decode_completed": False,
            "compare_completed": False,
            "run_mode_last": run_mode,
        }
        stats_path.write_text(json.dumps(stats, indent=2))
        existing_stats = stats
        encode_complete = True

    if run_mode == "encode_only":
        return {
            "file": file_path.name,
            "method": archive_label,
            "archive_variant": archive_variant,
            "archive_path": str(archive_path),
            "reconstructed_path": str(reconstructed_path),
            "summary": existing_summary,
            "stats": existing_stats,
        }

    if compare_complete:
        _emit_progress("validate_cache_hit")
        return {
            "file": file_path.name,
            "method": archive_label,
            "archive_variant": archive_variant,
            "archive_path": str(archive_path),
            "reconstructed_path": str(reconstructed_path),
            "summary": existing_summary,
            "stats": existing_stats,
        }

    if decode_complete:
        _emit_progress("decode_cache_hit")
        decode_time_s = float(existing_stats.get("decode_time_s", 0.0)) if existing_stats else float(decode_time_s or 0.0)
    else:
        print(f"[STACKZDPD][WHOLE] decode-start {file_path.name} variant={archive_variant}", flush=True)
        _emit_progress("decode_start")
        dt0 = time.perf_counter()
        codec.decode_to_mzml_file(archive_path, reconstructed_path, binary_compression="preserve_template")
        decode_time_s = time.perf_counter() - dt0
        print(f"[STACKZDPD][WHOLE] decode-done {file_path.name} variant={archive_variant} seconds={decode_time_s:.3f}", flush=True)
        _emit_progress("decode_done", decode_time_s=float(decode_time_s))
        if existing_stats is not None:
            checkpoint_stats = dict(existing_stats)
        else:
            checkpoint_stats = {
                "file": file_path.name,
                "method": archive_label,
                "archive_variant": archive_variant,
                "archive_meta": _json_safe(archive_meta),
                "raw_bytes": int(file_path.stat().st_size),
                "compressed_bytes": int(archive_path.stat().st_size),
                "compression_ratio": float(file_path.stat().st_size) / float(archive_path.stat().st_size),
                "encode_time_s": encode_time_s,
                "encode_completed": True,
            }
        checkpoint_stats.update(
            {
                "decode_time_s": float(decode_time_s),
                "encode_time_scope": WHOLE_ARCHIVE_ENCODE_TIME_SCOPE,
                "decode_time_scope": WHOLE_ARCHIVE_DECODE_TIME_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
                "decode_completed": True,
                "compare_completed": False,
                "run_mode_last": run_mode,
            }
        )
        stats_path.write_text(json.dumps(_json_safe(checkpoint_stats), indent=2))
        existing_stats = checkpoint_stats

    print(f"[STACKZDPD][WHOLE] compare-start {file_path.name} variant={archive_variant}", flush=True)
    _emit_progress("compare_start")
    roundtrip = compare_files(file_path, reconstructed_path, per_spectrum_csv=per_spectrum_csv)
    print(f"[STACKZDPD][WHOLE] compare-done {file_path.name} variant={archive_variant}", flush=True)
    _emit_progress("compare_done")
    mz_ok, int_ok = _roundtrip_within(roundtrip)
    roundtrip_passed, xml_metadata_warning = _roundtrip_pass_flags(roundtrip, mz_ok=mz_ok, int_ok=int_ok)

    summary = {
        "file": file_path.name,
        "method": archive_label,
        "archive_variant": archive_variant,
        "input_file_bytes": int(file_path.stat().st_size),
        "archive_bytes": int(archive_path.stat().st_size),
        "archive_path": str(archive_path),
        "reconstructed_mzml_path": str(reconstructed_path),
        "compression_ratio_vs_input_file": float(file_path.stat().st_size) / float(archive_path.stat().st_size),
        "encode_time_s": encode_time_s,
        "decode_time_s": decode_time_s,
        "encode_time_scope": WHOLE_ARCHIVE_ENCODE_TIME_SCOPE,
        "decode_time_scope": WHOLE_ARCHIVE_DECODE_TIME_SCOPE,
        "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        "archive_meta": _json_safe(archive_meta),
        "stage_timings_s": _json_safe(_archive_stage_timings_from_meta(archive_meta)),
        "roundtrip": _json_safe(roundtrip),
        "mz_error_ceiling": MZ_ERROR_CEILING,
        "intensity_error_ceiling": float(roundtrip.get("effective_intensity_error_ceiling", INT_ERROR_CEILING)),
        "intensity_error_ceiling_float64": INT_ERROR_CEILING,
        "intensity_error_ceiling_float32": 0.1,
        "intensity_error_ceiling_policy": roundtrip.get("intensity_error_ceiling_policy"),
        "effective_intensity_error_ceiling": roundtrip.get("effective_intensity_error_ceiling", INT_ERROR_CEILING),
        "effective_intensity_error_ceiling_basis": roundtrip.get("effective_intensity_error_ceiling_basis"),
        "effective_intensity_error_ceiling_dtype": roundtrip.get("effective_intensity_error_ceiling_dtype"),
        "effective_intensity_error_ceiling_reason": roundtrip.get("effective_intensity_error_ceiling_reason"),
        "original_intensity_dtype_summary": roundtrip.get("original_intensity_dtype_summary"),
        "reconstructed_intensity_dtype_summary": roundtrip.get("reconstructed_intensity_dtype_summary"),
        "intensity_dtype_match": roundtrip.get("intensity_dtype_match"),
        "ceiling_eps": CEILING_EPS,
        "mz_within_ceiling": mz_ok,
        "intensity_within_ceiling": int_ok,
        "roundtrip_passed": roundtrip_passed,
        "xml_metadata_warning": xml_metadata_warning,
        "compare_completed": True,
        "run_mode_last": run_mode,
    }
    summary_path.write_text(json.dumps(summary, indent=2))

    stats = {
        "file": file_path.name,
        "method": archive_label,
        "archive_variant": archive_variant,
        "archive_meta": _json_safe(archive_meta),
        "raw_bytes": int(file_path.stat().st_size),
        "compressed_bytes": int(archive_path.stat().st_size),
        "compression_ratio": float(file_path.stat().st_size) / float(archive_path.stat().st_size),
        "encode_time_s": encode_time_s,
        "decode_time_s": decode_time_s,
        "encode_time_scope": WHOLE_ARCHIVE_ENCODE_TIME_SCOPE,
        "decode_time_scope": WHOLE_ARCHIVE_DECODE_TIME_SCOPE,
        "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        "file_type": archive_meta["file_type"],
        "ms1_compressed_bytes": int(archive_meta["ms1"]["compressed_bytes"]),
        "ms2_compressed_bytes": int(archive_meta["ms2"]["compressed_bytes"]),
        "metadata_compressed_bytes": int(archive_meta["metadata"]["compressed_bytes"]),
        "auxiliary_compressed_bytes": int(archive_meta["auxiliary"]["compressed_bytes"]),
        "ms1_full_mz_compressed_bytes": int(archive_meta["ms1_full_mz"]["compressed_bytes"]),
        "stage_timings_s": _json_safe(_archive_stage_timings_from_meta(archive_meta)),
        "max_abs_mz_error": float(roundtrip["max_abs_mz_error"]),
        "max_abs_intensity_error": float(roundtrip["max_abs_intensity_error"]),
        "p95_spectrum_p95_abs_mz_error": float(roundtrip["spectrum_p95_abs_mz_error"]["p95"]),
        "p95_spectrum_p95_abs_intensity_error": float(roundtrip["spectrum_p95_abs_intensity_error"]["p95"]),
        "p95_spectrum_tic_rel_error": float(roundtrip["spectrum_tic_rel_error"]["p95"]),
        "p95_spectrum_bpi_rel_error": float(roundtrip["spectrum_bpi_rel_error"]["p95"]),
        "effective_intensity_error_ceiling": float(roundtrip.get("effective_intensity_error_ceiling", INT_ERROR_CEILING)),
        "effective_intensity_error_ceiling_basis": roundtrip.get("effective_intensity_error_ceiling_basis"),
        "effective_intensity_error_ceiling_dtype": roundtrip.get("effective_intensity_error_ceiling_dtype"),
        "intensity_dtype_match": roundtrip.get("intensity_dtype_match"),
        "mz_within_ceiling": mz_ok,
        "intensity_within_ceiling": int_ok,
        "roundtrip_passed": roundtrip_passed,
        "xml_metadata_warning": xml_metadata_warning,
        "aux_counts_match": bool(roundtrip["aux_counts_match"]),
        "binary_stripped_xml_equal": bool(roundtrip["binary_stripped_xml_equal"]),
        "xml_without_binary_arrays_equal": bool(roundtrip["xml_without_binary_arrays_equal"]),
        "spectrum_count": int(roundtrip["spectrum_count"]),
        "ms1_count": int(roundtrip["ms1_count"]),
        "ms2_count": int(roundtrip["ms2_count"]),
        "encode_completed": True,
        "decode_completed": True,
        "compare_completed": True,
        "run_mode_last": run_mode,
    }
    stats_path.write_text(json.dumps(stats, indent=2))
    return {
        "file": file_path.name,
        "method": archive_label,
        "archive_variant": archive_variant,
        "archive_path": str(archive_path),
        "reconstructed_path": str(reconstructed_path),
        "summary": summary,
        "stats": stats,
    }


def _aggregate_whole_rows(per_file_rows: list[dict], order: list[str]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in per_file_rows:
        grouped.setdefault(row["method"], []).append(row)

    out = []
    for label in order:
        rows = grouped.get(label, [])
        if not rows:
            continue
        values = [float(row["compression_ratio"]) for row in rows]
        encode_values = [_optional_float(row.get("encode_time_s")) for row in rows]
        decode_values = [_optional_float(row.get("decode_time_s")) for row in rows]
        encode_values = [value for value in encode_values if value is not None]
        decode_values = [value for value in decode_values if value is not None]
        out.append(
            {
                "label": label,
                "display_name": DISPLAY_NAME[label],
                "method_scope": WHOLE_METHOD_SCOPE_MAP.get(label, ""),
                "validated_roundtrip": bool(all(bool(row.get("validated_roundtrip", False)) for row in rows)),
                "mean_cr": statistics.mean(values),
                "median_cr": statistics.median(values),
                "std_cr": statistics.pstdev(values) if len(values) > 1 else 0.0,
                "min_cr": min(values),
                "max_cr": max(values),
                "mean_encode_time_s": statistics.mean(encode_values) if encode_values else None,
                "mean_decode_time_s": statistics.mean(decode_values) if decode_values else None,
                "time_scope_available": bool(encode_values and decode_values),
                "color_hex": COLOR_MAP[label],
                "family": WHOLE_FAMILY_MAP.get(label, "near"),
            }
        )
    stack_row = next((row for row in out if row["label"] == "stack_zdpd_container"), None)
    stack_cr = stack_row["mean_cr"] if stack_row is not None else None
    for row in out:
        if stack_cr is None:
            row["delta_cr_vs_stack"] = 0.0
        else:
            row["delta_cr_vs_stack"] = float(row["mean_cr"]) - float(stack_cr)
        if row["label"] == "stack_zdpd_container":
            row["bar_annotation"] = f"{row['mean_cr']:.2f}x\nbaseline"
        elif row["label"] in {"ours_whole_archive", "ours_strict_q6_whole_archive"}:
            row["bar_annotation"] = f"{row['mean_cr']:.2f}x\nvs Stack {row['delta_cr_vs_stack']:+.2f}x"
        else:
            row["bar_annotation"] = f"{row['mean_cr']:.2f}x"
    return out


def _plot_whole_boxplot(per_file_rows: list[dict], output_png: Path):
    order = [label for label in WHOLE_ORDER if any(row["method"] == label for row in per_file_rows)]
    data = [[float(row["compression_ratio"]) for row in per_file_rows if row["method"] == label] for label in order]
    fig, ax = plt.subplots(figsize=(13, 9))
    box = ax.boxplot(data, labels=[DISPLAY_NAME[label] for label in order], patch_artist=True, showfliers=False)
    for patch, label in zip(box["boxes"], order):
        patch.set_facecolor(COLOR_MAP[label])
        patch.set_alpha(0.75)
    for idx, label in enumerate(order, start=1):
        ys = [float(row["compression_ratio"]) for row in per_file_rows if row["method"] == label]
        xs = np.linspace(idx - 0.10, idx + 0.10, len(ys)) if len(ys) > 1 else [idx]
        ax.scatter(xs, ys, color="black", s=22, alpha=0.8, zorder=3)
    ax.set_title("StackZDPD Validation Whole-file Compression Ratio Distribution", fontsize=18)
    ax.set_ylabel("Compression Ratio", fontsize=15)
    ax.grid(axis="y", alpha=0.3)
    ax.tick_params(axis="x", labelrotation=38, labelsize=11)
    ax.tick_params(axis="y", labelsize=12)
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def _plot_whole_line(per_file_rows: list[dict], output_png: Path, files: list[Path], alias_map: dict[str, str]):
    order = [label for label in WHOLE_ORDER if any(row["method"] == label for row in per_file_rows)]
    file_order = [alias_map[path.name] for path in files]
    fig, ax = plt.subplots(figsize=(11.2, 6.4))
    x = np.arange(len(file_order))
    for label in order:
        rows = [row for row in per_file_rows if row["method"] == label]
        mapping = {alias_map[row["file"]]: float(row["compression_ratio"]) for row in rows}
        y = [mapping[name] for name in file_order]
        highlight = label in {"ours_whole_archive", "ours_strict_q6_whole_archive"}
        lw = 2.8 if label == "ours_whole_archive" else (2.5 if label == "ours_strict_q6_whole_archive" else 1.8)
        alpha = 1.0 if highlight else 0.82
        ax.plot(x, y, marker="o", linewidth=lw, color=COLOR_MAP[label], alpha=alpha, label=DISPLAY_NAME[label])
        if highlight:
            for xi, yi in zip(x, y):
                ax.text(xi, yi + max(0.015 * max(y), 0.02), f"{yi:.2f}x", ha="center", va="bottom", fontsize=11, color=COLOR_MAP[label])
    ax.set_title("StackZDPD Validation Whole-file Compression Ratio Per File", fontsize=18)
    ax.set_ylabel("Compression Ratio", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(file_order, rotation=0, ha="center", fontsize=11)
    ax.tick_params(axis="y", labelsize=12)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=10, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    plt.tight_layout()
    plt.savefig(output_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _run_section_suite(
    files: list[Path],
    result_root: Path,
    jobs: int,
    baseline_threads: int,
    compare_baseline_errors: bool,
    ms1_cpu_budget: int = 0,
    ms1_track_prepare_workers: int = 0,
    ms1_encode_section_workers: int = 0,
    min_available_mem_gb: float = 20.0,
):
    comp_dir = result_root / "compression_results"
    table_dir = result_root / "comparison_tables"
    plot_data_dir = result_root / "plot_data"
    plots_dir = result_root / "plots"
    for path in (comp_dir, table_dir, plot_data_dir, plots_dir):
        path.mkdir(parents=True, exist_ok=True)

    alias_map = _make_alias_map(files)

    ms1_rows, ms1_per_file = _run_incremental_section_jobs(
        files=files,
        jobs=jobs,
        worker=benchmark_ms1_validation_file,
        task_builder=lambda path: (
            str(path),
            baseline_threads,
            compare_baseline_errors,
            result_root,
            ms1_cpu_budget,
            ms1_track_prepare_workers,
            ms1_encode_section_workers,
            min_available_mem_gb,
        ),
        flat_csv_path=comp_dir / "ms1_per_file_methods.csv",
        progress_jsonl_path=comp_dir / "ms1.progress.jsonl",
        sidecar_dir=comp_dir / "ms1_sidecars",
        section_label="ms1",
    )

    meta_rows, meta_per_file = _run_incremental_section_jobs(
        files=files,
        jobs=jobs,
        worker=benchmark_metadata_validation_file,
        task_builder=lambda path: (str(path),),
        flat_csv_path=comp_dir / "metadata_per_file_methods.csv",
        progress_jsonl_path=comp_dir / "metadata.progress.jsonl",
        sidecar_dir=comp_dir / "metadata_sidecars",
        section_label="metadata",
    )

    ms2_rows, ms2_per_file = _run_incremental_section_jobs(
        files=files,
        jobs=jobs,
        worker=benchmark_ms2_validation_file,
        task_builder=lambda path: (str(path), baseline_threads, compare_baseline_errors),
        flat_csv_path=comp_dir / "ms2_per_file_methods.csv",
        progress_jsonl_path=comp_dir / "ms2.progress.jsonl",
        sidecar_dir=comp_dir / "ms2_sidecars",
        section_label="ms2",
    )

    overall_per_file = {}
    for file_path in files:
        file_name = file_path.name
        ms1 = _normalise_section_methods_for_reporting(ms1_per_file[file_name])
        ms2 = _normalise_section_methods_for_reporting(ms2_per_file[file_name])
        meta = _normalise_section_methods_for_reporting(meta_per_file[file_name])
        zdpd_encode = _sum_numeric_times(ms1["zdpd_baseline"], ms2["zdpd_baseline"], meta["zlib"], key="numeric_encode_time_s")
        zdpd_decode = _sum_numeric_times(ms1["zdpd_baseline"], ms2["zdpd_baseline"], meta["zlib"], key="numeric_decode_time_s")
        stack_encode = _sum_numeric_times(ms1["stack_zdpd_baseline"], ms2["stack_zdpd_baseline"], meta["zlib"], key="numeric_encode_time_s")
        stack_decode = _sum_numeric_times(ms1["stack_zdpd_baseline"], ms2["stack_zdpd_baseline"], meta["zlib"], key="numeric_decode_time_s")
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
                "encode_time_s": zdpd_encode,
                "decode_time_s": zdpd_decode,
                "numeric_encode_time_s": zdpd_encode,
                "numeric_decode_time_s": zdpd_decode,
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            },
            "stack_zdpd_container": {
                "raw_bytes": ms1["stack_zdpd_baseline"]["raw_bytes"] + ms2["stack_zdpd_baseline"]["raw_bytes"] + meta["zlib"]["raw_bytes"],
                "compressed_bytes": ms1["stack_zdpd_baseline"]["compressed_bytes"] + ms2["stack_zdpd_baseline"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"],
                "compression_ratio": (ms1["stack_zdpd_baseline"]["raw_bytes"] + ms2["stack_zdpd_baseline"]["raw_bytes"] + meta["zlib"]["raw_bytes"]) / (ms1["stack_zdpd_baseline"]["compressed_bytes"] + ms2["stack_zdpd_baseline"]["compressed_bytes"] + meta["zlib"]["compressed_bytes"]),
                "encode_time_s": stack_encode,
                "decode_time_s": stack_decode,
                "numeric_encode_time_s": stack_encode,
                "numeric_decode_time_s": stack_decode,
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            },
            "ours_eqfidelity": {
                "raw_bytes": ms1["ours_eqfidelity"]["raw_bytes"] + ms2["ours_eqfidelity"]["raw_bytes"] + meta["ours_metadata"]["raw_bytes"],
                "compressed_bytes": ms1["ours_eqfidelity"]["compressed_bytes"] + ms2["ours_eqfidelity"]["compressed_bytes"] + meta["ours_metadata"]["compressed_bytes"],
                "compression_ratio": (ms1["ours_eqfidelity"]["raw_bytes"] + ms2["ours_eqfidelity"]["raw_bytes"] + meta["ours_metadata"]["raw_bytes"]) / (ms1["ours_eqfidelity"]["compressed_bytes"] + ms2["ours_eqfidelity"]["compressed_bytes"] + meta["ours_metadata"]["compressed_bytes"]),
                "encode_time_s": ms1["ours_eqfidelity"]["encode_time_s"] + ms2["ours_eqfidelity"]["encode_time_s"] + meta["ours_metadata"]["encode_time_s"],
                "decode_time_s": ms1["ours_eqfidelity"]["decode_time_s"] + ms2["ours_eqfidelity"]["decode_time_s"] + meta["ours_metadata"]["decode_time_s"],
            },
        }
    _write_csv(
        [{"file": file_name, "method": method, **vals} for file_name, methods in sorted(overall_per_file.items(), key=lambda item: _extract_file_number(item[0])) for method, vals in methods.items()],
        comp_dir / "overall_per_file_methods.csv",
    )

    ms1_bar = _summarize_section("stackzdpd_validation_ms1", ms1_per_file, MS1_ORDER, result_root / "ms1", alias_map, "stack_zdpd_baseline")
    ms2_bar = _summarize_section("stackzdpd_validation_ms2", ms2_per_file, MS2_ORDER, result_root / "ms2", alias_map, "stack_zdpd_baseline")
    meta_bar = _summarize_section("stackzdpd_validation_metadata", meta_per_file, META_ORDER, result_root / "metadata", alias_map, None)
    overall_bar = _summarize_section("stackzdpd_validation_overall_container", overall_per_file, ["raw", "gzip", "zlib", "zstd-9", "zdpd_container", "stack_zdpd_container", "ours_eqfidelity"], result_root / "overall_container", alias_map, "stack_zdpd_container")

    summary = {
        "n_files": len(files),
        "files": [str(path) for path in files],
        "file_alias_map": alias_map,
        "ms1_best_ours_eqfidelity": next(row["mean_cr"] for row in ms1_bar if row["label"] == "ours_eqfidelity"),
        "ms1_best_ours_strict_q6": next(row["mean_cr"] for row in ms1_bar if row["label"] == "ours_strict_q6"),
        "ms2_best_ours_eqfidelity": next(row["mean_cr"] for row in ms2_bar if row["label"] == "ours_eqfidelity"),
        "metadata_best_ours": next(row["mean_cr"] for row in meta_bar if row["label"] == "ours_metadata"),
        "overall_container_best_ours": next(row["mean_cr"] for row in overall_bar if row["label"] == "ours_eqfidelity"),
    }
    (result_root / "section_benchmark_summary.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")

    alias_rows = [{"file_alias": alias_map[path.name], "file_name": path.name, "file_type": _detect_kind(path), "file_path": str(path)} for path in files]
    _write_csv(alias_rows, result_root / "comparison_tables" / "file_alias_mapping.csv")
    return ms1_per_file, ms2_per_file, meta_per_file, alias_map


def _run_whole_suite(
    files: list[Path],
    section_ms1_map: dict,
    section_ms2_map: dict,
    section_meta_map: dict,
    alias_map: dict[str, str],
    result_root: Path,
    workers: int,
    ms2_section_workers: int,
    ms2_segment_workers: int,
    run_mode: str,
):
    archive_dir = result_root / "compressed_archives"
    recon_dir = result_root / "reconstructed_mzml"
    roundtrip_dir = result_root / "roundtrip_validation"
    comp_dir = result_root / "compression_results"
    table_dir = result_root / "comparison_tables"
    plot_data_dir = result_root / "plot_data"
    plots_dir = result_root / "plots"
    for path in (archive_dir, recon_dir, roundtrip_dir, comp_dir, table_dir, plot_data_dir, plots_dir):
        path.mkdir(parents=True, exist_ok=True)
    progress_log_path = comp_dir / "whole_archive.progress.jsonl"
    _append_jsonl(
        progress_log_path,
        {
            "event": "whole_suite_start",
            "timestamp": _now_iso(),
            "run_mode": run_mode,
            "n_files": int(len(files)),
            "workers": int(max(1, workers)),
            "ms2_section_workers": int(max(1, ms2_section_workers)),
            "ms2_segment_workers": int(max(0, ms2_segment_workers)),
        },
    )

    alias_rows = [{"file_alias": alias_map[path.name], "file_name": path.name, "file_type": _detect_kind(path), "file_path": str(path)} for path in files]
    _write_csv(alias_rows, table_dir / "stackzdpd_validation_whole_archive_file_alias_mapping.csv")

    aux_zlib_map = {}
    aux_rows = []
    for file_path in files:
        stats = _auxiliary_zlib_stats(file_path)
        aux_zlib_map[file_path.name] = stats
        aux_rows.append({"file": file_path.name, **stats})
    _write_csv(aux_rows, comp_dir / "stackzdpd_validation_auxiliary_zlib_stats.csv")

    whole_file_baselines = {}
    with tempfile.TemporaryDirectory(prefix="stackzdpd_whole_") as tmp_root:
        tmp_dir = Path(tmp_root)
        for file_path in files:
            cache_path = comp_dir / f"{file_path.name}.whole_file_baselines.json"
            baseline_stats = None
            if cache_path.exists():
                baseline_stats = json.loads(cache_path.read_text())
            if not _whole_file_baseline_cache_valid(baseline_stats, file_path):
                baseline_stats = {}
                for method in WHOLE_FILE_BASELINE_METHODS:
                    baseline_stats[method] = _compress_file_streaming(file_path, method, tmp_dir)
                cache_path.write_text(json.dumps(_json_safe(baseline_stats), indent=2))
            whole_file_baselines[file_path.name] = baseline_stats

    ours_archive_map = defaultdict(dict)
    archive_variants = ("eqfidelity", "strict_q6")
    if workers > 1:
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(max_workers=int(workers), mp_context=ctx) as ex:
            futures = {
                ex.submit(
                    benchmark_whole_archive_file,
                    str(file_path),
                    str(archive_dir),
                    str(recon_dir),
                    str(roundtrip_dir),
                    str(comp_dir),
                    int(ms2_section_workers),
                    int(ms2_segment_workers),
                    variant,
                    run_mode,
                    str(progress_log_path),
                    str(result_root),
                ): (file_path.name, variant)
                for file_path in files
                for variant in archive_variants
            }
            for future in as_completed(futures):
                result = future.result()
                ours_archive_map[result["file"]][result["method"]] = result
    else:
        for file_path in files:
            for variant in archive_variants:
                result = benchmark_whole_archive_file(
                    str(file_path),
                    str(archive_dir),
                    str(recon_dir),
                    str(roundtrip_dir),
                    str(comp_dir),
                    int(ms2_section_workers),
                    int(ms2_segment_workers),
                    variant,
                    run_mode,
                    str(progress_log_path),
                    str(result_root),
                )
                ours_archive_map[result["file"]][result["method"]] = result

    overall_rows = []
    for file_path in files:
        file_name = file_path.name
        input_bytes = int(file_path.stat().st_size)
        for method in WHOLE_FILE_BASELINE_METHODS:
            stats = whole_file_baselines[file_name][method]
            overall_rows.append(
                {
                    "file": file_name,
                    "file_alias": alias_map[file_name],
                    "method": method,
                    "method_scope": WHOLE_METHOD_SCOPE_MAP[method],
                    "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP[method],
                    "raw_bytes": input_bytes,
                    "compressed_bytes": int(stats["compressed_bytes"]),
                    "compression_ratio": float(stats["compression_ratio"]),
                    "encode_time_s": float(stats["encode_time_s"]),
                    "decode_time_s": float(stats["decode_time_s"]),
                    "encode_time_scope": str(stats.get("encode_time_scope", "")),
                    "decode_time_scope": str(stats.get("decode_time_scope", "")),
                    "timing_scope_version": str(stats.get("timing_scope_version", "")),
                    "decoded_bytes_checked": int(stats.get("decoded_bytes_checked", 0)),
                }
            )

        aux_stats = aux_zlib_map[file_name]
        zdpd_comp = int(section_ms1_map[file_name]["zdpd_baseline"]["compressed_bytes"]) + int(section_ms2_map[file_name]["zdpd_baseline"]["compressed_bytes"]) + int(section_meta_map[file_name]["zlib"]["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
        zdpd_encode = (
            float(section_ms1_map[file_name]["zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(section_ms2_map[file_name]["zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(section_meta_map[file_name]["zlib"].get("numeric_encode_time_s", section_meta_map[file_name]["zlib"].get("encode_time_s", 0.0)))
            + float(aux_stats["encode_time_s"])
        )
        zdpd_decode = (
            float(section_ms1_map[file_name]["zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(section_ms2_map[file_name]["zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(section_meta_map[file_name]["zlib"].get("numeric_decode_time_s", section_meta_map[file_name]["zlib"].get("decode_time_s", 0.0)))
            + float(aux_stats["decode_time_s"])
        )
        overall_rows.append(
            {
                "file": file_name,
                "file_alias": alias_map[file_name],
                "method": "zdpd_container",
                "method_scope": WHOLE_METHOD_SCOPE_MAP["zdpd_container"],
                "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP["zdpd_container"],
                "raw_bytes": input_bytes,
            "compressed_bytes": zdpd_comp,
            "compression_ratio": input_bytes / zdpd_comp if zdpd_comp else 0.0,
            "encode_time_s": None,
            "decode_time_s": None,
            "numeric_encode_time_s": zdpd_encode,
            "numeric_decode_time_s": zdpd_decode,
            "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
            "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        }
    )

        stack_comp = int(section_ms1_map[file_name]["stack_zdpd_baseline"]["compressed_bytes"]) + int(section_ms2_map[file_name]["stack_zdpd_baseline"]["compressed_bytes"]) + int(section_meta_map[file_name]["zlib"]["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
        stack_encode = (
            float(section_ms1_map[file_name]["stack_zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(section_ms2_map[file_name]["stack_zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(section_meta_map[file_name]["zlib"].get("numeric_encode_time_s", section_meta_map[file_name]["zlib"].get("encode_time_s", 0.0)))
            + float(aux_stats["encode_time_s"])
        )
        stack_decode = (
            float(section_ms1_map[file_name]["stack_zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(section_ms2_map[file_name]["stack_zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(section_meta_map[file_name]["zlib"].get("numeric_decode_time_s", section_meta_map[file_name]["zlib"].get("decode_time_s", 0.0)))
            + float(aux_stats["decode_time_s"])
        )
        overall_rows.append(
            {
                "file": file_name,
                "file_alias": alias_map[file_name],
                "method": "stack_zdpd_container",
                "method_scope": WHOLE_METHOD_SCOPE_MAP["stack_zdpd_container"],
                "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP["stack_zdpd_container"],
                "raw_bytes": input_bytes,
            "compressed_bytes": stack_comp,
            "compression_ratio": input_bytes / stack_comp if stack_comp else 0.0,
            "encode_time_s": None,
            "decode_time_s": None,
            "numeric_encode_time_s": stack_encode,
            "numeric_decode_time_s": stack_decode,
            "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
            "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        }
    )

        for ours_method in ("ours_whole_archive", "ours_strict_q6_whole_archive"):
            ours = ours_archive_map[file_name][ours_method]["stats"]
            overall_rows.append(
                {
                    "file": file_name,
                    "file_alias": alias_map[file_name],
                    "method": ours_method,
                    "method_scope": WHOLE_METHOD_SCOPE_MAP[ours_method],
                    "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP[ours_method],
                    "raw_bytes": int(ours["raw_bytes"]),
                    "compressed_bytes": int(ours["compressed_bytes"]),
                    "compression_ratio": float(ours["compression_ratio"]),
                    "encode_time_s": float(ours["encode_time_s"]),
                    "decode_time_s": _optional_float(ours.get("decode_time_s")),
                    "encode_time_scope": str(ours.get("encode_time_scope", WHOLE_ARCHIVE_ENCODE_TIME_SCOPE)),
                    "decode_time_scope": str(ours.get("decode_time_scope", WHOLE_ARCHIVE_DECODE_TIME_SCOPE)),
                    "timing_scope_version": str(ours.get("timing_scope_version", "")),
                    "max_abs_mz_error": float(ours["max_abs_mz_error"]) if ours.get("max_abs_mz_error") is not None else None,
                    "max_abs_intensity_error": float(ours["max_abs_intensity_error"]) if ours.get("max_abs_intensity_error") is not None else None,
                    "mz_within_ceiling": bool(ours["mz_within_ceiling"]) if ours.get("mz_within_ceiling") is not None else None,
                    "intensity_within_ceiling": bool(ours["intensity_within_ceiling"]) if ours.get("intensity_within_ceiling") is not None else None,
                    "aux_counts_match": bool(ours["aux_counts_match"]) if ours.get("aux_counts_match") is not None else None,
                    "compare_completed": bool(ours.get("compare_completed", False)),
                }
            )

    aggregate_rows = _aggregate_whole_rows(overall_rows, WHOLE_ORDER)
    line_rows = []
    for method in WHOLE_ORDER:
        for row in overall_rows:
            if row["method"] != method:
                continue
            line_rows.append(
                {
                    "file": row["file"],
                    "file_label": row["file_alias"],
                    "label": method,
                    "display_name": DISPLAY_NAME[method],
                    "method_scope": row.get("method_scope", WHOLE_METHOD_SCOPE_MAP.get(method, "")),
                    "validated_roundtrip": row.get("validated_roundtrip", WHOLE_VALIDATED_ROUNDTRIP_MAP.get(method, False)),
                    "compression_ratio": row["compression_ratio"],
                    "color_hex": COLOR_MAP[method],
                    "family": WHOLE_FAMILY_MAP.get(method, "near"),
                }
            )
    speed_rows = [
        {
            "label": row["label"],
            "display_name": row["display_name"],
            "method_scope": row.get("method_scope", WHOLE_METHOD_SCOPE_MAP.get(row["label"], "")),
            "validated_roundtrip": row.get("validated_roundtrip", WHOLE_VALIDATED_ROUNDTRIP_MAP.get(row["label"], False)),
            "mean_encode_time_s": row["mean_encode_time_s"],
            "mean_decode_time_s": row["mean_decode_time_s"],
            "time_scope_available": row.get("time_scope_available", True),
            "color_hex": row["color_hex"],
            "family": row["family"],
        }
        for row in aggregate_rows
    ]

    _write_csv(overall_rows, comp_dir / "stackzdpd_validation_whole_archive_per_file_methods.csv")
    _write_csv(aggregate_rows, comp_dir / "stackzdpd_validation_whole_archive_aggregate.csv")
    _write_csv(aggregate_rows, table_dir / "stackzdpd_validation_whole_archive_compare_table.csv")
    _write_csv(aggregate_rows, plot_data_dir / "stackzdpd_validation_whole_archive_compression_bar_data.csv")
    _write_csv(line_rows, plot_data_dir / "stackzdpd_validation_whole_archive_compression_line_data.csv")
    _write_csv(speed_rows, plot_data_dir / "stackzdpd_validation_whole_archive_speed_bar_data.csv")

    plot_compression_bar_custom(
        aggregate_rows,
        plots_dir / "stackzdpd_validation_whole_archive_compression_comparison.png",
        title="StackZDPD Validation Whole-file Mean Compression Ratio",
        figsize=(12.2, 8.6),
        legend_loc="upper right",
        annotation_fontsize=12,
    )
    _plot_whole_boxplot(overall_rows, plots_dir / "stackzdpd_validation_whole_archive_compression_boxplot.png")
    _plot_whole_line(overall_rows, plots_dir / "stackzdpd_validation_whole_archive_per_file_line.png", files, alias_map)
    plot_speed_bar(
        speed_rows,
        plots_dir / "stackzdpd_validation_whole_archive_speed_bar.png",
        title_encode="StackZDPD Validation Whole-file Mean Encode Time",
        title_decode="StackZDPD Validation Whole-file Mean Decode Time",
    )

    component_rows = []
    roundtrip_rows = []
    for file_path in files:
        for ours_method in ("ours_whole_archive", "ours_strict_q6_whole_archive"):
            stats = ours_archive_map[file_path.name][ours_method]["stats"]
            summary = ours_archive_map[file_path.name][ours_method]["summary"]
            component_rows.append(
                {
                    "file": file_path.name,
                    "file_alias": alias_map[file_path.name],
                    "method": ours_method,
                    "archive_variant": stats.get("archive_variant"),
                    "file_type": stats["file_type"],
                    "archive_bytes": stats["compressed_bytes"],
                    "ms1_compressed_bytes": stats["ms1_compressed_bytes"],
                    "ms2_compressed_bytes": stats["ms2_compressed_bytes"],
                    "metadata_compressed_bytes": stats["metadata_compressed_bytes"],
                    "auxiliary_compressed_bytes": stats["auxiliary_compressed_bytes"],
                    "ms1_full_mz_compressed_bytes": stats["ms1_full_mz_compressed_bytes"],
                    "ms1_fraction": stats["ms1_compressed_bytes"] / stats["compressed_bytes"] if stats["compressed_bytes"] else 0.0,
                    "ms2_fraction": stats["ms2_compressed_bytes"] / stats["compressed_bytes"] if stats["compressed_bytes"] else 0.0,
                    "metadata_fraction": stats["metadata_compressed_bytes"] / stats["compressed_bytes"] if stats["compressed_bytes"] else 0.0,
                    "auxiliary_fraction": stats["auxiliary_compressed_bytes"] / stats["compressed_bytes"] if stats["compressed_bytes"] else 0.0,
                    "ms1_full_mz_fraction": stats["ms1_full_mz_compressed_bytes"] / stats["compressed_bytes"] if stats["compressed_bytes"] else 0.0,
                    "compare_completed": bool(stats.get("compare_completed", False)),
                }
            )
            if summary is None:
                continue
            roundtrip = summary["roundtrip"]
            roundtrip_rows.append(
                {
                    "file": file_path.name,
                    "file_alias": alias_map[file_path.name],
                    "method": ours_method,
                    "archive_variant": summary.get("archive_variant"),
                    "archive_path": summary["archive_path"],
                    "reconstructed_mzml_path": summary["reconstructed_mzml_path"],
                    "compression_ratio_vs_input_file": summary["compression_ratio_vs_input_file"],
                    "encode_time_s": summary["encode_time_s"],
                    "decode_time_s": summary["decode_time_s"],
                    "spectrum_count": roundtrip["spectrum_count"],
                    "ms1_count": roundtrip["ms1_count"],
                    "ms2_count": roundtrip["ms2_count"],
                    "compared_point_count": roundtrip["compared_point_count"],
                    "max_abs_mz_error": roundtrip["max_abs_mz_error"],
                    "max_abs_intensity_error": roundtrip["max_abs_intensity_error"],
                    "p95_spectrum_p95_abs_mz_error": roundtrip["spectrum_p95_abs_mz_error"]["p95"],
                    "p95_spectrum_p95_abs_intensity_error": roundtrip["spectrum_p95_abs_intensity_error"]["p95"],
                    "p95_spectrum_p95_rel_intensity_error": roundtrip["spectrum_p95_rel_intensity_error"]["p95"],
                    "p95_spectrum_tic_rel_error": roundtrip["spectrum_tic_rel_error"]["p95"],
                    "p95_spectrum_bpi_rel_error": roundtrip["spectrum_bpi_rel_error"]["p95"],
                    "effective_intensity_error_ceiling": roundtrip.get("effective_intensity_error_ceiling", INT_ERROR_CEILING),
                    "effective_intensity_error_ceiling_basis": roundtrip.get("effective_intensity_error_ceiling_basis"),
                    "effective_intensity_error_ceiling_dtype": roundtrip.get("effective_intensity_error_ceiling_dtype"),
                    "intensity_dtype_match": roundtrip.get("intensity_dtype_match"),
                    "mz_within_ceiling": summary["mz_within_ceiling"],
                    "intensity_within_ceiling": summary["intensity_within_ceiling"],
                    "roundtrip_passed": summary.get("roundtrip_passed"),
                    "xml_metadata_warning": summary.get("xml_metadata_warning"),
                    "aux_counts_match": roundtrip["aux_counts_match"],
                    "binary_stripped_xml_equal": roundtrip["binary_stripped_xml_equal"],
                    "xml_without_binary_arrays_equal": roundtrip["xml_without_binary_arrays_equal"],
                }
            )
    _write_csv(component_rows, comp_dir / "stackzdpd_validation_whole_archive_component_breakdown.csv")
    _write_csv(roundtrip_rows, roundtrip_dir / "stackzdpd_validation_whole_archive_roundtrip_validation.csv")

    if roundtrip_rows:
        roundtrip_summary = {
            "n_files": len(roundtrip_rows),
            "max_abs_mz_error": max(float(row["max_abs_mz_error"]) for row in roundtrip_rows),
            "max_abs_intensity_error": max(float(row["max_abs_intensity_error"]) for row in roundtrip_rows),
            "all_roundtrip_passed": all(bool(row["roundtrip_passed"]) for row in roundtrip_rows),
            "all_mz_within_ceiling": all(bool(row["mz_within_ceiling"]) for row in roundtrip_rows),
            "all_intensity_within_ceiling": all(bool(row["intensity_within_ceiling"]) for row in roundtrip_rows),
            "all_aux_counts_match": all(bool(row["aux_counts_match"]) for row in roundtrip_rows),
            "xml_metadata_warning_count": sum(1 for row in roundtrip_rows if bool(row.get("xml_metadata_warning"))),
            "all_xml_without_binary_arrays_equal": all(bool(row["xml_without_binary_arrays_equal"]) for row in roundtrip_rows),
            "all_binary_stripped_xml_equal": all(bool(row["binary_stripped_xml_equal"]) for row in roundtrip_rows),
            "effective_intensity_error_ceiling_counts": {
                str(value): sum(1 for row in roundtrip_rows if str(row.get("effective_intensity_error_ceiling")) == str(value))
                for value in sorted({row.get("effective_intensity_error_ceiling", INT_ERROR_CEILING) for row in roundtrip_rows})
            },
            "effective_intensity_error_ceiling_dtype_counts": {
                str(value): sum(1 for row in roundtrip_rows if str(row.get("effective_intensity_error_ceiling_dtype")) == str(value))
                for value in sorted({row.get("effective_intensity_error_ceiling_dtype", "unknown") for row in roundtrip_rows})
            },
            "per_method_counts": {
                method: sum(1 for row in roundtrip_rows if row.get("method") == method)
                for method in sorted({row.get("method", "") for row in roundtrip_rows})
            },
        }
        (roundtrip_dir / "stackzdpd_validation_whole_archive_roundtrip_summary.json").write_text(json.dumps(_json_safe(roundtrip_summary), indent=2))
    else:
        roundtrip_summary = {
            "n_files": 0,
            "run_mode": run_mode,
            "note": "Roundtrip validation not executed in encode_only mode or no validated rows were produced.",
        }
        (roundtrip_dir / "stackzdpd_validation_whole_archive_roundtrip_summary.json").write_text(json.dumps(_json_safe(roundtrip_summary), indent=2))
    _append_jsonl(
        progress_log_path,
        {
            "event": "whole_suite_done",
            "timestamp": _now_iso(),
            "run_mode": run_mode,
            "n_files": int(len(files)),
            "n_roundtrip_rows": int(len(roundtrip_rows)),
        },
    )
    return roundtrip_summary


def main():
    assert_native_speedups_available(required=True)
    parser = argparse.ArgumentParser(description="TrackCodec benchmark for the StackZDPD validation mzML dataset.")
    parser.add_argument("--input-dir", type=Path, default=os.environ.get(STACKZDPD_VALIDATION_ENV))
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--baseline-threads", type=int, default=1)
    parser.add_argument("--ms1-cpu-budget", type=int, default=0)
    parser.add_argument("--ms1-track-prepare-workers", type=int, default=0)
    parser.add_argument("--ms1-encode-section-workers", type=int, default=0)
    parser.add_argument("--min-available-mem-gb", type=float, default=20.0)
    parser.add_argument("--whole-workers", type=int, default=1)
    parser.add_argument("--whole-ms2-section-workers", type=int, default=8)
    parser.add_argument("--whole-ms2-segment-workers", type=int, default=0)
    parser.add_argument("--whole-run-mode", choices=WHOLE_RUN_MODE_CHOICES, default="all")
    parser.add_argument("--limit-files", type=int, default=0)
    parser.add_argument("--compare-baseline-errors", action="store_true")
    parser.add_argument("--skip-whole-file", action="store_true")
    args = parser.parse_args()

    if args.input_dir is None:
        raise ValueError(f"--input-dir or {STACKZDPD_VALIDATION_ENV} is required for the StackZDPD validation dataset.")
    files = _discover_files(Path(args.input_dir).expanduser())
    if int(args.limit_files) > 0:
        files = files[: int(args.limit_files)]
    if not files:
        raise ValueError(f"No mzML files found in {args.input_dir}")

    result_root = Path(args.result_root)
    result_root.mkdir(parents=True, exist_ok=True)

    section_root = result_root / "section_benchmark"
    ms1_map, ms2_map, meta_map, alias_map = _run_section_suite(
        files,
        section_root,
        jobs=max(1, int(args.jobs)),
        baseline_threads=max(0, int(args.baseline_threads)),
        compare_baseline_errors=bool(args.compare_baseline_errors),
        ms1_cpu_budget=max(0, int(args.ms1_cpu_budget)),
        ms1_track_prepare_workers=max(0, int(args.ms1_track_prepare_workers)),
        ms1_encode_section_workers=max(0, int(args.ms1_encode_section_workers)),
        min_available_mem_gb=float(args.min_available_mem_gb),
    )

    summary = {
        "dataset_dir": str(args.input_dir),
        "result_root": str(result_root),
        "n_files": len(files),
        "file_alias_map": [{"file_alias": alias_map[path.name], "file_name": path.name, "file_type": _detect_kind(path), "file_path": str(path)} for path in files],
        "section_root": str(section_root),
    }

    if not args.skip_whole_file:
        whole_root = result_root / "whole_archive"
        roundtrip_summary = _run_whole_suite(
            files,
            ms1_map,
            ms2_map,
            meta_map,
            alias_map,
            whole_root,
            workers=max(1, int(args.whole_workers)),
            ms2_section_workers=max(1, int(args.whole_ms2_section_workers)),
            ms2_segment_workers=max(0, int(args.whole_ms2_segment_workers)),
            run_mode=str(args.whole_run_mode),
        )
        summary["whole_archive_root"] = str(whole_root)
        summary["whole_run_mode"] = str(args.whole_run_mode)
        summary["whole_archive_roundtrip_summary"] = roundtrip_summary

    (result_root / "README.json").write_text(json.dumps(_json_safe(summary), indent=2), encoding="utf-8")
    readme_lines = [
        "# TrackCodec StackZDPD Validation Benchmark",
        "",
        f"- Input directory: `{args.input_dir}`",
        f"- Number of files: `{len(files)}`",
        f"- Section benchmark root: `{section_root}`",
    ]
    if not args.skip_whole_file:
        readme_lines.append(f"- Whole-file benchmark root: `{result_root / 'whole_archive'}`")
        readme_lines.append(f"- Whole-file run mode: `{args.whole_run_mode}`")
    readme_lines.extend(
        [
            "",
            "## File Alias Mapping",
            "",
        ]
    )
    for path in files:
        readme_lines.append(f"- `{alias_map[path.name]}` = `{path.name}`")
    (result_root / "README.md").write_text("\n".join(readme_lines), encoding="utf-8")
    print(json.dumps(_json_safe(summary), indent=2))


if __name__ == "__main__":
    main()
