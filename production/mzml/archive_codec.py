"""Archive a full mzML file and reconstruct it back to valid mzML."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import multiprocessing as mp
import os
import shutil
import struct
import tempfile
import time
import traceback
from pathlib import Path
from typing import Callable, Dict, List, Sequence, Tuple

import numpy as np

from ..common.compression_backends import compress, compress_many, decompress
from ..common.extensions import TRACKCODEC_ARCHIVE_EXTENSION
from ..common.io import detect_file_type
from ..common.islands import identify_island_features_compact, identify_islands
from ..common.memory import release_memory, recommend_parallel_section_processes
from ..common.scan_store import create_memmap_scan_store_from_mzml, create_memmap_scan_store_with_metadata_from_mzml, open_memmap_scan_store
from ..common.tracking import CompactIslandTracks, HAVE_TRACK_LINK_SPEEDUPS, build_island_tracks
from ..common.track_store import open_compact_track_store, write_compact_track_store
from ..metadata.metadata_codec import MzMLMetadataCodec
from ..ms1.unified_codec import MS1Codec
from ..ms1.cross_scan_codec import (
    DELTA_LENGTH_MODE_MAX_REF_CURR,
    EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT,
    HAVE_CROSS_SCAN_SPEEDUPS,
    _cross_scan_speedups,
    _unpack_segments as _unpack_ms1_segments,
)
from ..ms2.codec import DIAWindowMS2Codec, MS2ModeConfig
from .auxiliary_codec import decode_auxiliary_records, encode_auxiliary_records
from .reconstruction import (
    _parse_root,
    extract_binary_stripped_metadata_and_auxiliary_records,
    extract_ms1_scan_templates_from_root,
    rebuild_mzml_from_root,
    write_rebuilt_mzml_from_root,
)


MAGIC = b"TCARCH\0\0"
MS2_MAGIC = b"TCMS2\0\0\0"
DEFAULT_MS1_ISLAND_WORKERS = max(1, min(4, os.cpu_count() or 1))
DEFAULT_MS1_SPARSE_PROFILE_BYPASS = True
DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS = 1
DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION = 0.35
DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN = 5000.0
DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION = 0.50


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _read_section_header_from_blob(blob: bytes, magic: bytes, context: str) -> tuple[list[dict], int]:
    min_header_size = len(magic) + 4
    if len(blob) < min_header_size:
        raise ValueError(f"Corrupt {context}: missing header length")
    if not blob.startswith(magic):
        raise ValueError(f"Invalid {context} magic")
    header_len = struct.unpack("<I", blob[len(magic):len(magic) + 4])[0]
    header_start = len(magic) + 4
    header_end = header_start + header_len
    if header_end > len(blob):
        raise ValueError(
            f"Corrupt {context}: header length {header_len} exceeds payload size {len(blob)}"
        )
    header = json.loads(blob[header_start:header_end].decode("utf-8"))
    if not isinstance(header, list):
        raise ValueError(f"Corrupt {context}: section header must be a list")
    return header, header_end


def _validate_section_header(header: list[dict], payload_len: int, context: str) -> list[dict]:
    normalized: list[dict] = []
    expected_offset = 0
    seen_names: set[str] = set()
    for idx, item in enumerate(header):
        if not isinstance(item, dict):
            raise ValueError(f"Corrupt {context}: section header item {idx} is not an object")
        if "name" not in item or "offset" not in item or "size" not in item:
            raise ValueError(f"Corrupt {context}: section header item {idx} is missing name/offset/size")
        name = str(item["name"])
        if name in seen_names:
            raise ValueError(f"Corrupt {context}: duplicate section name {name!r}")
        seen_names.add(name)
        offset = int(item["offset"])
        size = int(item["size"])
        if offset < 0 or size < 0:
            raise ValueError(f"Corrupt {context}: negative offset/size for section {name!r}")
        if offset != expected_offset:
            raise ValueError(
                f"Corrupt {context}: section {name!r} starts at {offset}, expected {expected_offset}"
            )
        end = offset + size
        if end > payload_len:
            raise ValueError(
                f"Corrupt {context}: section {name!r} ends at {end}, payload has {payload_len} bytes"
            )
        normalized.append({"name": name, "offset": offset, "size": size})
        expected_offset = end
    if expected_offset != payload_len:
        raise ValueError(
            f"Corrupt {context}: section payload length mismatch; header covers {expected_offset}, payload has {payload_len} bytes"
        )
    return normalized


def _merge_prefixed_timings(
    stage_timings: Dict[str, float],
    nested: Dict | None,
    *,
    prefix: str,
) -> None:
    if not isinstance(nested, dict):
        return
    for key, value in nested.items():
        if not isinstance(value, (int, float, np.integer, np.floating)):
            continue
        out_key = str(key)
        if not out_key.startswith(prefix):
            out_key = f"{prefix}{out_key}"
        stage_timings[out_key] = float(value)


def _pop_runtime_stage_timings(meta: Dict | None) -> Dict:
    if not isinstance(meta, dict):
        return {}
    timings = meta.pop("stage_timings_s", {})
    return timings if isinstance(timings, dict) else {}


_ARCHIVE_RUNTIME_METADATA_KEYS = {
    "cache_hit",
    "encode_time_s",
    "centroid_probe_s",
    "archive_track_probe_s",
    "parallel_decision",
    "parallel_ms1_ms2_processes",
}


def _strip_archive_runtime_metadata(obj):
    if isinstance(obj, dict):
        return {
            key: _strip_archive_runtime_metadata(value)
            for key, value in obj.items()
            if key not in _ARCHIVE_RUNTIME_METADATA_KEYS
        }
    if isinstance(obj, list):
        return [_strip_archive_runtime_metadata(item) for item in obj]
    return obj


def _write_section_meta_atomic(path: Path, meta: Dict) -> None:
    _write_text_atomic(path, json.dumps(_strip_archive_runtime_metadata(meta), ensure_ascii=False))


_STAGE_TIMING_NON_DURATION_SUFFIXES = (
    "_enabled",
    "_triggered",
    "_workers",
    "_units",
    "_count",
    "_counts",
    "_bytes",
)
_STAGE_TIMING_NON_DURATION_EXACT = {
    "ms1_sparse_profile_bypass_original_islands",
    "ms1_sparse_profile_bypass_trackable_islands",
    "ms2_internal_ms2_parallel_workers",
    "ms2_internal_ms2_total_units",
    "parallel_process_mode_suppressed",
}


def _is_stage_duration_key(key: str) -> bool:
    key = str(key)
    if key in _STAGE_TIMING_NON_DURATION_EXACT:
        return False
    if key.endswith("_s"):
        return True
    if any(key.endswith(suffix) for suffix in _STAGE_TIMING_NON_DURATION_SUFFIXES):
        return False
    return False


def _duration_stage_timings(stage_timings: Dict[str, float]) -> Dict[str, float]:
    return {
        str(key): float(value)
        for key, value in stage_timings.items()
        if isinstance(value, (int, float, np.integer, np.floating)) and _is_stage_duration_key(str(key))
    }


def _make_archive_ms1_mode(ms1_mode: Dict, *, external_full_mz_sidecar: bool) -> Dict:
    mode = {
        "mz_precision": ms1_mode["mz_precision"],
        "intensity_mode": ms1_mode["intensity_mode"],
        "backend": ms1_mode["backend"],
        "kwargs": dict(ms1_mode.get("kwargs", {})),
    }
    if external_full_mz_sidecar:
        # Archive mode stores the complete MS1 m/z grid once in the external
        # full-sidecar, so island m/z must be omitted from ms1_payload.
        mode["kwargs"]["preserve_full_scan"] = False
        mode["kwargs"]["retain_zero_intensity_mz"] = False
        mode["kwargs"]["omit_array_starts"] = False
        mode["kwargs"]["omit_island_mz"] = True
    return mode


def _disabled_uint32_sidecar_meta(note: str) -> Dict:
    return {
        "enabled": False,
        "backend": "",
        "count": 0,
        "dtype": "uint32",
        "raw_bytes": 0,
        "compressed_bytes": 0,
        "note": str(note),
    }


def _disabled_orphan_sidecar_meta(note: str, backend: str = "zstd-9") -> Dict:
    return {
        "enabled": False,
        "format": "ms1_orphan_intensity_sidecar",
        "mode": "disabled",
        "backend": backend,
        "raw_bytes": 0,
        "compressed_bytes": 0,
        "orphan_point_count": 0,
        "note": str(note),
    }


def _disabled_full_mz_sidecar_meta(note: str, backend: str = "zstd-9") -> Dict:
    return {
        "enabled": False,
        "backend": backend,
        "raw_bytes": 0,
        "compressed_bytes": 0,
        "compression_ratio": 0.0,
        "note": str(note),
    }


def _raw_ms1_bytes_for_scans(ms1_scans: Sequence[Dict]) -> int:
    total = 0
    for scan in ms1_scans:
        total += int(np.asarray(scan["mz_array"]).nbytes)
        total += int(np.asarray(scan["intensity_array"]).nbytes)
    return int(total)


def _scan_to_islands(scan: Dict, strategy_b_valley_ratio: float = 0.01) -> tuple[int, float, list]:
    islands = identify_islands(
        scan["mz_array"],
        scan["intensity_array"],
        scan["scan_idx"],
        strategy_b_valley_ratio=float(strategy_b_valley_ratio),
    )
    return int(scan["scan_idx"]), float(scan["rt"]), islands


def _scan_to_compact_island_features(scan: Dict, strategy_b_valley_ratio: float = 0.01):
    centers, n_points, starts, scan_idx = identify_island_features_compact(
        scan["mz_array"],
        scan["intensity_array"],
        scan["scan_idx"],
        strategy_b_valley_ratio=float(strategy_b_valley_ratio),
    )
    return centers, n_points, starts, int(scan_idx)


def _scan_to_compact_island_features_with_zero(scan: Dict, strategy_b_valley_ratio: float = 0.01):
    intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
    zero_frac = float(np.mean(intensity == 0.0)) if intensity.size else 0.0
    centers, n_points, starts, scan_idx = identify_island_features_compact(
        scan["mz_array"],
        intensity,
        scan["scan_idx"],
        strategy_b_valley_ratio=float(strategy_b_valley_ratio),
    )
    return centers, n_points, starts, int(scan_idx), zero_frac, int(intensity.size)


def _build_ms1_tracks_compact_features_fast(
    ms1_scans,
    *,
    island_workers: int,
    strategy_b_valley_ratio: float,
    ppm_tol: float = 15.0,
) -> CompactIslandTracks:
    from ..ms1 import _cross_scan_speedups

    def _iter_features():
        if island_workers <= 1:
            for scan in ms1_scans:
                yield _scan_to_compact_island_features(
                    scan,
                    strategy_b_valley_ratio=float(strategy_b_valley_ratio),
                )
        else:
            with ThreadPoolExecutor(max_workers=island_workers) as executor:
                yield from executor.map(
                    lambda scan: _scan_to_compact_island_features(
                        scan,
                        strategy_b_valley_ratio=float(strategy_b_valley_ratio),
                    ),
                    ms1_scans,
                )

    center_parts: List[np.ndarray] = []
    npoint_parts: List[np.ndarray] = []
    start_parts: List[np.ndarray] = []
    scan_idx_parts: List[np.ndarray] = []
    scan_offsets = [0]
    for centers, n_points, starts, scan_idx in _iter_features():
        centers = np.asarray(centers, dtype=np.float64)
        n_points = np.asarray(n_points, dtype=np.uint32)
        starts = np.asarray(starts, dtype=np.uint32)
        count = int(centers.size)
        if count:
            center_parts.append(centers)
            npoint_parts.append(n_points)
            start_parts.append(starts)
            scan_idx_parts.append(np.full(count, int(scan_idx), dtype=np.uint32))
        scan_offsets.append(scan_offsets[-1] + count)

    total_islands = int(scan_offsets[-1])
    if total_islands == 0:
        return CompactIslandTracks(
            track_lengths=np.array([], dtype=np.uint32),
            track_offsets=np.array([0], dtype=np.uint32),
            islands=[],
            scan_indices=np.array([], dtype=np.uint32),
            array_start_indices=np.array([], dtype=np.uint32),
            n_points=np.array([], dtype=np.uint32),
            point_count=0,
        )

    center_mz = np.concatenate(center_parts).astype(np.float64, copy=False)
    n_points = np.concatenate(npoint_parts).astype(np.uint32, copy=False)
    array_start_indices_flat = np.concatenate(start_parts).astype(np.uint32, copy=False)
    scan_indices_flat = np.concatenate(scan_idx_parts).astype(np.uint32, copy=False)
    scan_offsets_arr = np.asarray(scan_offsets, dtype=np.uint32)
    track_lengths, island_order = _cross_scan_speedups.build_compact_tracks_baseline(
        center_mz,
        np.empty(0, dtype=np.float64),
        np.empty(0, dtype=np.float64),
        n_points,
        np.empty(0, dtype=np.float64),
        scan_offsets_arr,
        float(ppm_tol),
    )
    track_lengths = np.asarray(track_lengths, dtype=np.uint32)
    island_order = np.asarray(island_order, dtype=np.uint32)
    track_offsets = np.zeros(len(track_lengths) + 1, dtype=np.uint32)
    if len(track_lengths):
        track_offsets[1:] = np.cumsum(track_lengths, dtype=np.uint32)
    n_points_reordered = np.take(n_points, island_order)
    return CompactIslandTracks(
        track_lengths=track_lengths,
        track_offsets=track_offsets,
        islands=[],
        scan_indices=np.take(scan_indices_flat, island_order),
        array_start_indices=np.take(array_start_indices_flat, island_order),
        n_points=n_points_reordered,
        point_count=int(n_points_reordered.astype(np.uint64, copy=False).sum()) if n_points_reordered.size else 0,
    )


def _empty_compact_island_tracks() -> CompactIslandTracks:
    return CompactIslandTracks(
        track_lengths=np.array([], dtype=np.uint32),
        track_offsets=np.array([0], dtype=np.uint32),
        islands=[],
        scan_indices=np.array([], dtype=np.uint32),
        array_start_indices=np.array([], dtype=np.uint32),
        n_points=np.array([], dtype=np.uint32),
        point_count=0,
    )


def _link_compact_island_features(
    center_parts: Sequence[np.ndarray],
    npoint_parts: Sequence[np.ndarray],
    start_parts: Sequence[np.ndarray],
    scan_idx_parts: Sequence[np.ndarray],
    *,
    ppm_tol: float = 15.0,
) -> CompactIslandTracks:
    from ..ms1 import _cross_scan_speedups

    scan_offsets = [0]
    total_islands = 0
    for centers in center_parts:
        total_islands += int(np.asarray(centers).size)
        scan_offsets.append(total_islands)
    if total_islands == 0:
        return _empty_compact_island_tracks()

    center_mz = np.concatenate(center_parts).astype(np.float64, copy=False)
    n_points = np.concatenate(npoint_parts).astype(np.uint32, copy=False)
    array_start_indices_flat = np.concatenate(start_parts).astype(np.uint32, copy=False)
    scan_indices_flat = np.concatenate(scan_idx_parts).astype(np.uint32, copy=False)
    scan_offsets_arr = np.asarray(scan_offsets, dtype=np.uint32)
    track_lengths, island_order = _cross_scan_speedups.build_compact_tracks_baseline(
        center_mz,
        np.empty(0, dtype=np.float64),
        np.empty(0, dtype=np.float64),
        n_points,
        np.empty(0, dtype=np.float64),
        scan_offsets_arr,
        float(ppm_tol),
    )
    track_lengths = np.asarray(track_lengths, dtype=np.uint32)
    island_order = np.asarray(island_order, dtype=np.uint32)
    track_offsets = np.zeros(len(track_lengths) + 1, dtype=np.uint32)
    if len(track_lengths):
        track_offsets[1:] = np.cumsum(track_lengths, dtype=np.uint32)
    n_points_reordered = np.take(n_points, island_order)
    return CompactIslandTracks(
        track_lengths=track_lengths,
        track_offsets=track_offsets,
        islands=[],
        scan_indices=np.take(scan_indices_flat, island_order),
        array_start_indices=np.take(array_start_indices_flat, island_order),
        n_points=n_points_reordered,
        point_count=int(n_points_reordered.astype(np.uint64, copy=False).sum()) if n_points_reordered.size else 0,
    )


def _archive_sparse_profile_bypass_config(
    *,
    enabled: bool,
    min_zero_fraction: float,
    min_islands_per_scan: float,
    min_short_fraction: float,
    short_max_points: int,
) -> Dict:
    return {
        "enabled": bool(enabled),
        "mode": "sparse_profile_short_island_bypass",
        "min_zero_fraction": float(min_zero_fraction),
        "min_islands_per_scan": float(min_islands_per_scan),
        "min_short_fraction": float(min_short_fraction),
        "short_max_points": int(short_max_points),
    }


def _archive_track_store_path(track_store_path: Path | None, sparse_bypass_config: Dict | None) -> Path | None:
    if track_store_path is None:
        return None
    cfg = sparse_bypass_config or {}
    if not bool(cfg.get("enabled", False)):
        return track_store_path
    digest = _stable_cache_digest({"track_store_kind": "archive_ms1_tracks", "sparse_bypass": cfg})[:16]
    return track_store_path / f"archive_sparse_{digest}"


def build_ms1_tracks_for_archive(
    ms1_scans,
    *,
    island_workers: int | None = None,
    compact: bool = True,
    strategy_b_valley_ratio: float = 0.01,
    drop_islands_in_compact: bool = True,
    retain_zero_intensity_mz: bool = True,
    sparse_profile_bypass: bool = DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
    sparse_min_zero_fraction: float = DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
    sparse_min_islands_per_scan: float = DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
    sparse_min_short_fraction: float = DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
    sparse_short_max_points: int = DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
    ppm_tol: float = 15.0,
) -> tuple[CompactIslandTracks | list, Dict]:
    """Build archive MS1 tracks, optionally bypassing singleton sparse-profile runs.

    Archive mode with a full m/z sidecar can reconstruct scan-local nonzero
    intensities through the orphan sidecar. For zero-filled sparse profile
    files, routing singleton islands to that sidecar avoids creating millions
    of low-value one-point tracks without changing mzML roundtrip fidelity.
    """
    if island_workers is None:
        island_workers = DEFAULT_MS1_ISLAND_WORKERS
    island_workers = max(1, int(island_workers))
    sparse_short_max_points = max(0, int(sparse_short_max_points))
    stats: Dict[str, float | int | bool | str] = {
        "enabled": bool(sparse_profile_bypass),
        "triggered": False,
        "reason": "not_evaluated",
        "short_max_points": int(sparse_short_max_points),
    }
    if (
        not sparse_profile_bypass
        or not retain_zero_intensity_mz
        or not compact
        or not drop_islands_in_compact
        or not HAVE_TRACK_LINK_SPEEDUPS
    ):
        tracks = build_ms1_tracks(
            ms1_scans,
            island_workers=island_workers,
            compact=compact,
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
            drop_islands_in_compact=drop_islands_in_compact,
        )
        stats["reason"] = "disabled_or_ineligible"
        stats["track_count"] = int(len(tracks)) if hasattr(tracks, "__len__") else 0
        return tracks, stats

    def _iter_features():
        if island_workers <= 1:
            for scan in ms1_scans:
                yield _scan_to_compact_island_features_with_zero(
                    scan,
                    strategy_b_valley_ratio=float(strategy_b_valley_ratio),
                )
        else:
            with ThreadPoolExecutor(max_workers=island_workers) as executor:
                yield from executor.map(
                    lambda scan: _scan_to_compact_island_features_with_zero(
                        scan,
                        strategy_b_valley_ratio=float(strategy_b_valley_ratio),
                    ),
                    ms1_scans,
                )

    center_parts: List[np.ndarray] = []
    npoint_parts: List[np.ndarray] = []
    start_parts: List[np.ndarray] = []
    scan_idx_parts: List[np.ndarray] = []
    total_islands = 0
    total_points = 0
    total_array_points = 0
    zero_weighted_sum = 0.0
    short_islands = 0
    short_points = 0
    scan_count = 0
    for centers, n_points, starts, scan_idx, zero_frac, array_points in _iter_features():
        centers = np.asarray(centers, dtype=np.float64)
        n_points = np.asarray(n_points, dtype=np.uint32)
        starts = np.asarray(starts, dtype=np.uint32)
        count = int(centers.size)
        scan_count += 1
        total_array_points += int(array_points)
        zero_weighted_sum += float(zero_frac) * float(array_points)
        center_parts.append(centers)
        npoint_parts.append(n_points)
        start_parts.append(starts)
        scan_idx_parts.append(np.full(count, int(scan_idx), dtype=np.uint32))
        if count:
            total_islands += count
            point_sum = int(n_points.astype(np.uint64, copy=False).sum())
            total_points += point_sum
            short_mask = n_points <= sparse_short_max_points
            if np.any(short_mask):
                short_islands += int(np.count_nonzero(short_mask))
                short_points += int(n_points[short_mask].astype(np.uint64, copy=False).sum())

    avg_islands_per_scan = float(total_islands) / float(max(1, scan_count))
    avg_zero_fraction = float(zero_weighted_sum) / float(max(1, total_array_points))
    short_fraction = float(short_islands) / float(max(1, total_islands))
    avg_island_points = float(total_points) / float(max(1, total_islands))
    triggered = (
        total_islands > 0
        and avg_zero_fraction >= float(sparse_min_zero_fraction)
        and avg_islands_per_scan >= float(sparse_min_islands_per_scan)
        and short_fraction >= float(sparse_min_short_fraction)
        and sparse_short_max_points > 0
    )
    stats.update(
        {
            "scan_count": int(scan_count),
            "original_island_count": int(total_islands),
            "original_island_point_count": int(total_points),
            "avg_islands_per_scan": float(avg_islands_per_scan),
            "avg_zero_fraction": float(avg_zero_fraction),
            "avg_island_points": float(avg_island_points),
            "short_island_count": int(short_islands),
            "short_island_point_count": int(short_points),
            "short_island_fraction": float(short_fraction),
            "min_zero_fraction": float(sparse_min_zero_fraction),
            "min_islands_per_scan": float(sparse_min_islands_per_scan),
            "min_short_fraction": float(sparse_min_short_fraction),
            "triggered": bool(triggered),
        }
    )
    if not triggered:
        tracks = _link_compact_island_features(
            center_parts,
            npoint_parts,
            start_parts,
            scan_idx_parts,
            ppm_tol=float(ppm_tol),
        )
        stats["reason"] = "sparse_profile_threshold_not_met"
        stats["trackable_island_count"] = int(total_islands)
        stats["omitted_short_island_count"] = 0
        stats["omitted_short_point_count"] = 0
        stats["track_count"] = int(len(tracks))
        return tracks, stats

    filtered_center_parts: List[np.ndarray] = []
    filtered_npoint_parts: List[np.ndarray] = []
    filtered_start_parts: List[np.ndarray] = []
    filtered_scan_idx_parts: List[np.ndarray] = []
    trackable_islands = 0
    trackable_points = 0
    for centers, n_points, starts, scan_indices in zip(center_parts, npoint_parts, start_parts, scan_idx_parts):
        keep = np.asarray(n_points, dtype=np.uint32) > np.uint32(sparse_short_max_points)
        keep_count = int(np.count_nonzero(keep))
        if keep_count == 0:
            filtered_center_parts.append(np.empty(0, dtype=np.float64))
            filtered_npoint_parts.append(np.empty(0, dtype=np.uint32))
            filtered_start_parts.append(np.empty(0, dtype=np.uint32))
            filtered_scan_idx_parts.append(np.empty(0, dtype=np.uint32))
            continue
        kept_n = np.asarray(n_points, dtype=np.uint32)[keep]
        filtered_center_parts.append(np.asarray(centers, dtype=np.float64)[keep])
        filtered_npoint_parts.append(kept_n)
        filtered_start_parts.append(np.asarray(starts, dtype=np.uint32)[keep])
        filtered_scan_idx_parts.append(np.asarray(scan_indices, dtype=np.uint32)[keep])
        trackable_islands += keep_count
        trackable_points += int(kept_n.astype(np.uint64, copy=False).sum())

    tracks = _link_compact_island_features(
        filtered_center_parts,
        filtered_npoint_parts,
        filtered_start_parts,
        filtered_scan_idx_parts,
        ppm_tol=float(ppm_tol),
    )
    stats["reason"] = "sparse_profile_short_island_bypass"
    stats["trackable_island_count"] = int(trackable_islands)
    stats["trackable_island_point_count"] = int(trackable_points)
    stats["omitted_short_island_count"] = int(total_islands - trackable_islands)
    stats["omitted_short_point_count"] = int(total_points - trackable_points)
    stats["track_count"] = int(len(tracks))
    return tracks, stats


def build_ms1_tracks(
    ms1_scans,
    island_workers: int | None = None,
    *,
    compact: bool = False,
    strategy_b_valley_ratio: float = 0.01,
    drop_islands_in_compact: bool = False,
):
    if island_workers is None:
        island_workers = DEFAULT_MS1_ISLAND_WORKERS
    island_workers = max(1, int(island_workers))
    if compact and drop_islands_in_compact and HAVE_TRACK_LINK_SPEEDUPS:
        return _build_ms1_tracks_compact_features_fast(
            ms1_scans,
            island_workers=island_workers,
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
        )
    try:
        n_scans = len(ms1_scans)
    except TypeError:
        n_scans = None
    # ThreadPool-based per-scan island extraction is negative on current real-data
    # workloads up to at least 256 MS1 scans; keep it available only for much larger
    # batches where scheduling overhead has a chance to amortize.
    if island_workers <= 1 or (n_scans is not None and n_scans < 1024):
        scan_island_iter = (
            _scan_to_islands(scan, strategy_b_valley_ratio=float(strategy_b_valley_ratio))
            for scan in ms1_scans
        )
    else:
        with ThreadPoolExecutor(max_workers=island_workers) as executor:
            scan_island_iter = executor.map(
                lambda scan: _scan_to_islands(scan, strategy_b_valley_ratio=float(strategy_b_valley_ratio)),
                ms1_scans,
            )
            return build_island_tracks(
                scan_island_iter,
                compact=compact,
                drop_islands_when_compact=drop_islands_in_compact,
            )
    return build_island_tracks(
        scan_island_iter,
        compact=compact,
        drop_islands_when_compact=drop_islands_in_compact,
    )


def _flatten_ms1_array_starts(tracks) -> List[int] | np.ndarray:
    if isinstance(tracks, CompactIslandTracks):
        return np.asarray(tracks.array_start_indices, dtype=np.uint32).astype(np.uint32, copy=False)
    return [int(isl.array_start_idx) for track in tracks for isl in track.islands]


def _compact_covered_runs_index(tracks: CompactIslandTracks) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    scan_indices = np.asarray(tracks.scan_indices, dtype=np.uint32)
    starts = np.asarray(tracks.array_start_indices, dtype=np.uint32)
    counts = np.asarray(tracks.n_points, dtype=np.uint32)
    if scan_indices.size == 0:
        empty = np.empty(0, dtype=np.uint32)
        return empty, empty, empty, empty, empty
    order = np.argsort(scan_indices, kind="stable").astype(np.uint32, copy=False)
    sorted_scan_indices = scan_indices[order]
    change_pos = np.flatnonzero(sorted_scan_indices[1:] != sorted_scan_indices[:-1]).astype(np.int64) + 1
    group_starts = np.concatenate((np.array([0], dtype=np.int64), change_pos))
    group_ends = np.concatenate((change_pos, np.array([sorted_scan_indices.size], dtype=np.int64)))
    unique_scan_indices = sorted_scan_indices[group_starts].astype(np.uint32, copy=False)
    return (
        unique_scan_indices,
        group_starts.astype(np.uint32, copy=False),
        group_ends.astype(np.uint32, copy=False),
        order,
        starts,
        counts,
    )


def _covered_runs_index(tracks) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return scan-grouped covered island runs without per-scan dict/list state."""
    if isinstance(tracks, CompactIslandTracks):
        return _compact_covered_runs_index(tracks)

    scan_parts: List[np.ndarray] = []
    start_parts: List[np.ndarray] = []
    count_parts: List[np.ndarray] = []
    for track in tracks:
        islands = getattr(track, "islands", [])
        if not islands:
            continue
        scan_parts.append(np.fromiter((int(isl.scan_idx) for isl in islands), dtype=np.uint32, count=len(islands)))
        start_parts.append(np.fromiter((int(isl.array_start_idx) for isl in islands), dtype=np.uint32, count=len(islands)))
        count_parts.append(np.fromiter((int(isl.n_points) for isl in islands), dtype=np.uint32, count=len(islands)))
    if not scan_parts:
        empty = np.empty(0, dtype=np.uint32)
        return empty, empty, empty, empty, empty, empty
    compact = CompactIslandTracks(
        track_lengths=np.empty(0, dtype=np.uint32),
        track_offsets=np.empty(0, dtype=np.uint32),
        islands=[],
        scan_indices=np.concatenate(scan_parts).astype(np.uint32, copy=False),
        array_start_indices=np.concatenate(start_parts).astype(np.uint32, copy=False),
        n_points=np.concatenate(count_parts).astype(np.uint32, copy=False),
        point_count=0,
    )
    return _compact_covered_runs_index(compact)


def _compact_runs_for_scan(
    compact_index: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    scan_idx: int,
) -> tuple[np.ndarray, np.ndarray]:
    unique_scan_indices, group_starts, group_ends, order, starts, counts = compact_index
    if unique_scan_indices.size == 0:
        return np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.uint32)
    pos = int(np.searchsorted(unique_scan_indices, np.uint32(scan_idx)))
    if pos >= int(unique_scan_indices.size) or int(unique_scan_indices[pos]) != int(scan_idx):
        return np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.uint32)
    s = int(group_starts[pos])
    e = int(group_ends[pos])
    idx = order[s:e]
    return starts[idx], counts[idx]


def _iter_compact_runs_for_scan(
    compact_index: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    scan_idx: int,
):
    starts, counts = _compact_runs_for_scan(compact_index, scan_idx)
    for idx in range(int(starts.size)):
        yield int(starts[idx]), int(counts[idx])


def _pack_named_sections(named_sections: Dict[str, bytes]) -> bytes:
    header = []
    payload = bytearray()
    offset = 0
    for name, blob in named_sections.items():
        size = len(blob)
        header.append({"name": name, "offset": offset, "size": size})
        payload.extend(blob)
        offset += size
    header_blob = _json_bytes(header)
    return MAGIC + struct.pack("<I", len(header_blob)) + header_blob + bytes(payload)


def _pack_named_section_files(named_section_paths: List[Tuple[str, Path]], output_path: Path) -> int:
    header = []
    offset = 0
    for name, path in named_section_paths:
        size = int(path.stat().st_size)
        header.append({"name": name, "offset": offset, "size": size})
        offset += size
    header_blob = _json_bytes(header)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as dst:
        dst.write(MAGIC)
        dst.write(struct.pack("<I", len(header_blob)))
        dst.write(header_blob)
        for _, path in named_section_paths:
            with path.open("rb") as src:
                shutil.copyfileobj(src, dst, length=1024 * 1024)
    return int(output_path.stat().st_size)


def _unpack_named_sections(blob: bytes) -> Dict[str, bytes]:
    header, header_end = _read_section_header_from_blob(blob, MAGIC, "TrackCodec archive")
    payload = blob[header_end:]
    header = _validate_section_header(header, len(payload), "TrackCodec archive")
    out: Dict[str, bytes] = {}
    for item in header:
        out[item["name"]] = payload[item["offset"]:item["offset"] + item["size"]]
    return out


def _read_named_section_index(path: str | Path, magic: bytes = MAGIC) -> tuple[list[dict], int]:
    path = Path(path)
    file_size = int(path.stat().st_size)
    with path.open("rb") as handle:
        prefix = handle.read(len(magic))
        if prefix != magic:
            raise ValueError("Invalid TrackCodec archive magic")
        header_len_raw = handle.read(4)
        if len(header_len_raw) != 4:
            raise ValueError("Corrupt TrackCodec archive header")
        header_len = struct.unpack("<I", header_len_raw)[0]
        payload_start = len(magic) + 4 + header_len
        if payload_start > file_size:
            raise ValueError(
                f"Corrupt TrackCodec archive header: header ends at {payload_start}, file has {file_size} bytes"
            )
        header_blob = handle.read(header_len)
        if len(header_blob) != header_len:
            raise ValueError("Corrupt TrackCodec archive header: truncated header JSON")
    header = json.loads(header_blob.decode("utf-8"))
    header = _validate_section_header(header, file_size - payload_start, "TrackCodec archive file")
    return header, payload_start


def _read_named_sections_from_file(
    path: str | Path,
    names: Sequence[str] | None = None,
    magic: bytes = MAGIC,
) -> Dict[str, bytes]:
    header, payload_start = _read_named_section_index(path, magic=magic)
    wanted = None if names is None else set(names)
    selected = [item for item in header if wanted is None or item["name"] in wanted]
    out: Dict[str, bytes] = {}
    with Path(path).open("rb") as handle:
        for item in selected:
            handle.seek(payload_start + int(item["offset"]))
            size = int(item["size"])
            blob = handle.read(size)
            if len(blob) != size:
                raise ValueError(
                    f"Corrupt TrackCodec archive file: section {item['name']!r} expected {size} bytes, read {len(blob)}"
                )
            out[item["name"]] = blob
    return out


def _pack_ms2_sections(named_sections: Dict[str, bytes]) -> bytes:
    header = []
    payload = bytearray()
    offset = 0
    for name, blob in named_sections.items():
        size = len(blob)
        header.append({"name": name, "offset": offset, "size": size})
        payload.extend(blob)
        offset += size
    header_blob = _json_bytes(header)
    return MS2_MAGIC + struct.pack("<I", len(header_blob)) + header_blob + bytes(payload)


def _pack_ms2_sections_to_file(named_sections: Dict[str, bytes], output_path: str | Path) -> int:
    header = []
    offset = 0
    for name, blob in named_sections.items():
        size = len(blob)
        header.append({"name": name, "offset": offset, "size": size})
        offset += size
    header_blob = _json_bytes(header)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        handle.write(MS2_MAGIC)
        handle.write(struct.pack("<I", len(header_blob)))
        handle.write(header_blob)
        for _, blob in named_sections.items():
            handle.write(blob)
    return int(output_path.stat().st_size)


def _unpack_ms2_sections(blob: bytes) -> Dict[str, bytes]:
    header, header_end = _read_section_header_from_blob(blob, MS2_MAGIC, "TrackCodec MS2 payload")
    payload = blob[header_end:]
    header = _validate_section_header(header, len(payload), "TrackCodec MS2 payload")
    out: Dict[str, bytes] = {}
    for item in header:
        out[item["name"]] = payload[item["offset"]:item["offset"] + item["size"]]
    return out


def _default_ms2_config_for_type(
    file_type: str,
    ms2_section_workers: int | None = None,
    ms2_segment_workers: int | None = None,
) -> MS2ModeConfig:
    kwargs = {}
    if ms2_section_workers is not None:
        kwargs["dia_section_workers"] = max(1, int(ms2_section_workers))
    if ms2_segment_workers is not None and int(ms2_segment_workers) > 0:
        kwargs["section_segment_workers"] = max(1, int(ms2_segment_workers))
    elif ms2_section_workers is not None and int(ms2_section_workers) > 1:
        kwargs["section_segment_workers"] = max(1, int(ms2_section_workers))
    if file_type == "DIA":
        return MS2ModeConfig.exact_track_dia_current(**kwargs)
    return MS2ModeConfig.exact_track_dda_current(dia_exact_track_mode="prefer_section", **kwargs)


def _encode_uint32_sidecar(values: List[int] | np.ndarray, backend: str = "zstd-9") -> Tuple[bytes, Dict]:
    arr = np.asarray(values, dtype=np.uint32)
    raw = arr.tobytes()
    payload = compress(raw, backend)
    return payload, {
        "backend": backend,
        "count": int(arr.size),
        "dtype": "uint32",
        "raw_bytes": len(raw),
        "compressed_bytes": len(payload),
    }


def _decode_uint32_sidecar(payload: bytes, meta: Dict) -> np.ndarray:
    raw = decompress(payload, meta["backend"])
    out = np.frombuffer(raw, dtype=np.uint32, count=int(meta["count"]))
    return out.copy()


def _encode_int64_sidecar(values: List[int] | np.ndarray, backend: str = "zstd-9") -> Tuple[bytes, Dict]:
    arr = np.asarray(values, dtype=np.int64)
    raw = arr.tobytes()
    payload = compress(raw, backend)
    return payload, {
        "backend": backend,
        "count": int(arr.size),
        "dtype": "int64",
        "raw_bytes": len(raw),
        "compressed_bytes": len(payload),
    }


def _decode_int64_sidecar(payload: bytes, meta: Dict) -> np.ndarray:
    raw = decompress(payload, meta["backend"])
    out = np.frombuffer(raw, dtype=np.int64, count=int(meta["count"]))
    return out.copy()


def _encode_float64_sidecar(values: List[float] | np.ndarray, backend: str = "zstd-9") -> Tuple[bytes, Dict]:
    arr = np.asarray(values, dtype=np.float64)
    raw = arr.tobytes()
    payload = compress(raw, backend)
    return payload, {
        "backend": backend,
        "count": int(arr.size),
        "dtype": "float64",
        "raw_bytes": len(raw),
        "compressed_bytes": len(payload),
    }


def _decode_float64_sidecar(payload: bytes, meta: Dict) -> np.ndarray:
    raw = decompress(payload, meta["backend"])
    out = np.frombuffer(raw, dtype=np.float64, count=int(meta["count"]))
    return out.copy()


def _quantize_mz_array(mz_array: np.ndarray, mz_precision: int) -> np.ndarray:
    scale = 10 ** int(mz_precision)
    return np.round(np.asarray(mz_array, dtype=np.float64) * scale).astype(np.int64)


def _ms1_scan_store_view(ms1_scans):
    store = getattr(ms1_scans, "store", None)
    global_indices = getattr(ms1_scans, "global_indices", None)
    if store is None or global_indices is None:
        return None
    if not all(hasattr(store, name) for name in ("mz_data", "offsets", "lengths")):
        return None
    return store, np.asarray(global_indices, dtype=np.uint32)


def _pack_ms1_full_mz_qdelta_parts(
    lengths: np.ndarray,
    first_values: np.ndarray,
    delta_values_i64: np.ndarray,
    *,
    mz_precision: int,
    backend: str,
    compression_workers: int,
) -> Tuple[bytes, Dict]:
    lengths = np.asarray(lengths, dtype=np.uint32)
    first_arr = np.asarray(first_values, dtype=np.int64)
    delta_values_i64 = np.asarray(delta_values_i64, dtype=np.int64)
    uint32_max = int(np.iinfo(np.uint32).max)
    use_signed_delta = bool(
        delta_values_i64.size
        and (np.any(delta_values_i64 < 0) or np.any(delta_values_i64 > uint32_max))
    )
    if use_signed_delta:
        delta_values = delta_values_i64
        delta_mode = "signed_int64_delta"
        delta_dtype = "int64"
    else:
        delta_values = delta_values_i64.astype(np.uint32, copy=False)
        delta_mode = "uint32_delta"
        delta_dtype = "uint32"

    lengths_raw = lengths.tobytes()
    first_raw = first_arr.tobytes()
    delta_raw = delta_values.tobytes()
    lengths_payload, first_payload, delta_payload = compress_many(
        [lengths_raw, first_raw, delta_raw],
        backend,
        max_workers=int(compression_workers),
    )
    lengths_meta = {
        "backend": backend,
        "count": int(lengths.size),
        "dtype": "uint32",
        "raw_bytes": len(lengths_raw),
        "compressed_bytes": len(lengths_payload),
    }
    first_meta = {
        "backend": backend,
        "count": int(first_arr.size),
        "dtype": "int64",
        "raw_bytes": len(first_raw),
        "compressed_bytes": len(first_payload),
    }
    delta_meta = {
        "backend": backend,
        "count": int(delta_values.size),
        "dtype": delta_dtype,
        "raw_bytes": len(delta_raw),
        "compressed_bytes": len(delta_payload),
    }
    packed = _pack_named_sections(
        {
            "lengths_payload": lengths_payload,
            "lengths_meta": _json_bytes(lengths_meta),
            "first_payload": first_payload,
            "first_meta": _json_bytes(first_meta),
            "delta_payload": delta_payload,
            "delta_meta": _json_bytes(delta_meta),
        }
    )
    raw_bytes = int(lengths.nbytes + first_arr.nbytes + delta_values.nbytes)
    return packed, {
        "format": "ms1_full_mz_qdelta",
        "backend": backend,
        "mz_precision": int(mz_precision),
        "scan_count": int(lengths.size),
        "delta_mode": delta_mode,
        "sidecar_scope": "full_scan_mz",
        "raw_bytes": raw_bytes,
        "compressed_bytes": len(packed),
    }


def _collect_ms1_full_mz_qdelta_from_scan_store(ms1_scans, mz_precision: int):
    view = _ms1_scan_store_view(ms1_scans)
    if view is None:
        return None
    store, global_indices = view
    indices = np.asarray(global_indices, dtype=np.int64)
    lengths = np.asarray(store.lengths[indices], dtype=np.uint32)
    offsets = np.asarray(store.offsets[indices], dtype=np.uint64)
    if (
        HAVE_CROSS_SCAN_SPEEDUPS
        and _cross_scan_speedups is not None
        and hasattr(_cross_scan_speedups, "ms1_collect_full_mz_qdelta")
    ):
        try:
            native_lengths, native_firsts, native_deltas = _cross_scan_speedups.ms1_collect_full_mz_qdelta(
                np.asarray(store.mz_data, dtype=np.float64),
                offsets,
                lengths,
                float(10 ** int(mz_precision)),
            )
            return (
                np.asarray(native_lengths, dtype=np.uint32),
                np.asarray(native_firsts, dtype=np.int64),
                np.asarray(native_deltas, dtype=np.int64),
            )
        except Exception:
            pass
    first_values = np.zeros(int(lengths.size), dtype=np.int64)
    total_delta_count = int(np.maximum(lengths.astype(np.int64, copy=False) - 1, 0).sum())
    delta_values = np.empty(total_delta_count, dtype=np.int64)
    delta_pos = 0
    for idx, (offset, length) in enumerate(zip(offsets, lengths)):
        n = int(length)
        if n == 0:
            continue
        start = int(offset)
        mz_q = _quantize_mz_array(store.mz_data[start:start + n], mz_precision)
        first_values[idx] = int(mz_q[0])
        if n > 1:
            next_pos = delta_pos + n - 1
            delta_values[delta_pos:next_pos] = np.diff(mz_q)
            delta_pos = next_pos
    if delta_pos != total_delta_count:
        raise ValueError("MS1 full m/z sidecar delta collection length mismatch")
    return lengths, first_values, delta_values


def _collect_ms1_full_mz_qdelta_from_sequence(ms1_scans: Sequence[Dict], mz_precision: int):
    lengths = np.empty(len(ms1_scans), dtype=np.uint32)
    total_delta_count = 0
    for idx, scan in enumerate(ms1_scans):
        n = int(len(scan["mz_array"]))
        lengths[idx] = np.uint32(n)
        if n > 1:
            total_delta_count += n - 1

    first_values = np.zeros(int(lengths.size), dtype=np.int64)
    delta_values = np.empty(int(total_delta_count), dtype=np.int64)
    delta_pos = 0
    for idx, scan in enumerate(ms1_scans):
        n = int(lengths[idx])
        if n == 0:
            continue
        mz_q = _quantize_mz_array(scan["mz_array"], mz_precision)
        first_values[idx] = int(mz_q[0])
        if n > 1:
            next_pos = delta_pos + n - 1
            delta_values[delta_pos:next_pos] = np.diff(mz_q)
            delta_pos = next_pos
    if delta_pos != total_delta_count:
        raise ValueError("MS1 full m/z sidecar delta collection length mismatch")
    return lengths, first_values, delta_values


def _encode_ms1_full_mz_sidecar(
    ms1_scans: Sequence[Dict],
    *,
    mz_precision: int,
    backend: str = "zstd-9",
    compression_workers: int = 1,
) -> Tuple[bytes, Dict]:
    parts = _collect_ms1_full_mz_qdelta_from_scan_store(ms1_scans, int(mz_precision))
    if parts is None:
        parts = _collect_ms1_full_mz_qdelta_from_sequence(ms1_scans, int(mz_precision))
    lengths, first_values, delta_values_i64 = parts
    return _pack_ms1_full_mz_qdelta_parts(
        lengths,
        first_values,
        delta_values_i64,
        mz_precision=int(mz_precision),
        backend=backend,
        compression_workers=int(compression_workers),
    )


def _decode_ms1_full_mz_sidecar(payload: bytes, meta: Dict) -> List[np.ndarray]:
    sections = _unpack_named_sections(payload)
    lengths_meta = json.loads(sections["lengths_meta"].decode("utf-8"))
    first_meta = json.loads(sections["first_meta"].decode("utf-8"))
    delta_meta = json.loads(sections["delta_meta"].decode("utf-8"))
    lengths = _decode_uint32_sidecar(sections["lengths_payload"], lengths_meta).astype(np.int64)
    first_values = _decode_int64_sidecar(sections["first_payload"], first_meta)
    if str(meta.get("format")) != "ms1_full_mz_qdelta":
        raise ValueError(f"Unsupported MS1 full m/z sidecar format: {meta.get('format')}")
    delta_mode = str(meta.get("delta_mode", "uint32_delta"))
    if delta_mode == "signed_int64_delta":
        delta_values = _decode_int64_sidecar(sections["delta_payload"], delta_meta)
    elif delta_mode == "uint32_delta":
        delta_values = _decode_uint32_sidecar(sections["delta_payload"], delta_meta).astype(np.int64)
    else:
        raise ValueError(f"Unsupported MS1 full m/z sidecar delta mode: {delta_mode}")
    if len(lengths) != int(meta.get("scan_count", len(lengths))):
        raise ValueError(f"MS1 full m/z sidecar scan count mismatch: {len(lengths)} vs {meta.get('scan_count')}")
    scale = float(10 ** int(meta["mz_precision"]))
    scans = []
    delta_pos = 0
    for scan_idx, length in enumerate(lengths):
        n = int(length)
        if n == 0:
            scans.append(np.array([], dtype=np.float64))
            continue
        mz_q = np.empty(n, dtype=np.int64)
        mz_q[0] = int(first_values[scan_idx])
        if n > 1:
            local_delta = delta_values[delta_pos:delta_pos + n - 1]
            if len(local_delta) != n - 1:
                raise ValueError(f"MS1 full m/z sidecar delta length mismatch for scan {scan_idx}")
            delta_pos += n - 1
            mz_q[1:] = mz_q[0] + np.cumsum(local_delta, dtype=np.int64)
        scans.append(mz_q.astype(np.float64) / scale)
    if delta_pos != len(delta_values):
        raise ValueError(f"MS1 full m/z sidecar unused deltas: {len(delta_values) - delta_pos}")
    return scans


def _encode_ms1_full_mz_sidecar_timed(
    ms1_scans: Sequence[Dict],
    *,
    mz_precision: int,
    backend: str = "zstd-9",
    compression_workers: int = 1,
) -> Tuple[bytes, Dict, float]:
    t0 = time.perf_counter()
    payload, meta = _encode_ms1_full_mz_sidecar(
        ms1_scans,
        mz_precision=mz_precision,
        backend=backend,
        compression_workers=compression_workers,
    )
    return payload, meta, time.perf_counter() - t0


def _ms1_full_mz_sidecar_cache_key(ms1_scans, *, mz_precision: int, backend: str) -> str | None:
    store = getattr(ms1_scans, "store", None)
    global_indices = getattr(ms1_scans, "global_indices", None)
    if store is None or global_indices is None:
        return None
    try:
        root = Path(store.root).resolve()
        manifest = getattr(store, "manifest", {})
        mz_path = root / str(manifest.get("mz_data_file", "mz_data.bin"))
        stat = mz_path.stat()
        indices = np.asarray(global_indices, dtype=np.uint32)
        h = hashlib.sha256()
        h.update(b"trackcodec-archive-ms1-full-mz-sidecar")
        h.update(str(root).encode("utf-8", errors="surrogatepass"))
        h.update(b"\0")
        h.update(json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))
        h.update(b"\0")
        h.update(indices.tobytes())
        h.update(b"\0")
        h.update(str(int(stat.st_size)).encode("ascii"))
        h.update(b"\0")
        h.update(str(int(stat.st_mtime_ns)).encode("ascii"))
        h.update(b"\0")
        h.update(str(int(mz_precision)).encode("ascii"))
        h.update(b"\0")
        h.update(str(backend).encode("utf-8"))
        return h.hexdigest()
    except Exception:
        return None


def _encode_ms1_full_mz_sidecar_timed_cached(
    ms1_scans: Sequence[Dict],
    *,
    mz_precision: int,
    backend: str = "zstd-9",
    cache_dir: str | Path | None = None,
    compression_workers: int = 1,
) -> Tuple[bytes, Dict, float]:
    if cache_dir is None:
        return _encode_ms1_full_mz_sidecar_timed(
            ms1_scans,
            mz_precision=mz_precision,
            backend=backend,
            compression_workers=compression_workers,
        )
    cache_key = _ms1_full_mz_sidecar_cache_key(ms1_scans, mz_precision=mz_precision, backend=backend)
    if cache_key is None:
        return _encode_ms1_full_mz_sidecar_timed(
            ms1_scans,
            mz_precision=mz_precision,
            backend=backend,
            compression_workers=compression_workers,
        )
    cache_dir = Path(cache_dir)
    payload_path = cache_dir / f"{cache_key}.payload"
    meta_path = cache_dir / f"{cache_key}.meta.json"
    if payload_path.exists() and meta_path.exists():
        t0 = time.perf_counter()
        payload = payload_path.read_bytes()
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta = dict(meta)
        meta["cache_hit"] = True
        return payload, meta, time.perf_counter() - t0

    payload, meta, elapsed = _encode_ms1_full_mz_sidecar_timed(
        ms1_scans,
        mz_precision=mz_precision,
        backend=backend,
        compression_workers=compression_workers,
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_payload = payload_path.with_suffix(payload_path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
        tmp_meta = meta_path.with_suffix(meta_path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
        tmp_payload.write_bytes(payload)
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp_payload.replace(payload_path)
        tmp_meta.replace(meta_path)
    except Exception:
        pass
    meta = dict(meta)
    meta["cache_hit"] = False
    return payload, meta, elapsed


def _normalized_complement_runs(n: int, covered_runs: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    normalized = []
    for start, count in covered_runs:
        start_i = max(0, int(start))
        end_i = min(int(n), start_i + max(0, int(count)))
        if start_i < end_i:
            normalized.append((start_i, end_i))
    if not normalized:
        return [(0, int(n))] if n > 0 else []
    normalized.sort()
    merged = []
    for start, end in normalized:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)

    complement = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            complement.append((cursor, start - cursor))
        cursor = max(cursor, end)
    if cursor < n:
        complement.append((cursor, n - cursor))
    return complement


def _encode_ms1_complement_mz_sidecar(
    ms1_scans: Sequence[Dict],
    tracks,
    *,
    mz_precision: int,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict]:
    covered_index = _covered_runs_index(tracks)
    scan_lengths = np.asarray([len(scan["mz_array"]) for scan in ms1_scans], dtype=np.uint32)
    run_scan_indices: List[int] = []
    run_starts: List[int] = []
    run_lengths: List[int] = []
    first_values = []
    delta_parts = []
    full_scan_point_count = 0
    complement_point_count = 0

    for scan in ms1_scans:
        scan_idx = int(scan["scan_idx"])
        n = int(len(scan["mz_array"]))
        full_scan_point_count += n
        if n == 0:
            continue
        mz_q = _quantize_mz_array(scan["mz_array"], mz_precision)
        covered_runs = _iter_compact_runs_for_scan(covered_index, scan_idx)
        for start, count in _normalized_complement_runs(n, covered_runs):
            end = start + count
            if count <= 0:
                continue
            local_q = mz_q[start:end]
            run_scan_indices.append(scan_idx)
            run_starts.append(start)
            run_lengths.append(count)
            first_values.append(int(local_q[0]))
            complement_point_count += count
            if count > 1:
                deltas = np.diff(local_q)
                if np.any(deltas < 0) or np.any(deltas > np.iinfo(np.uint32).max):
                    raise ValueError("MS1 complement m/z sidecar requires non-negative uint32 deltas")
                delta_parts.append(deltas.astype(np.uint32, copy=False))

    scan_lengths_payload, scan_lengths_meta = _encode_uint32_sidecar(scan_lengths, backend=backend)
    scan_payload, scan_meta = _encode_uint32_sidecar(run_scan_indices, backend=backend)
    start_payload, start_meta = _encode_uint32_sidecar(run_starts, backend=backend)
    length_payload, length_meta = _encode_uint32_sidecar(run_lengths, backend=backend)
    first_payload, first_meta = _encode_int64_sidecar(first_values, backend=backend)
    delta_values = np.concatenate(delta_parts) if delta_parts else np.array([], dtype=np.uint32)
    delta_payload, delta_meta = _encode_uint32_sidecar(delta_values, backend=backend)
    packed = _pack_named_sections(
        {
            "scan_lengths_payload": scan_lengths_payload,
            "scan_lengths_meta": _json_bytes(scan_lengths_meta),
            "scan_payload": scan_payload,
            "scan_meta": _json_bytes(scan_meta),
            "start_payload": start_payload,
            "start_meta": _json_bytes(start_meta),
            "length_payload": length_payload,
            "length_meta": _json_bytes(length_meta),
            "first_payload": first_payload,
            "first_meta": _json_bytes(first_meta),
            "delta_payload": delta_payload,
            "delta_meta": _json_bytes(delta_meta),
        }
    )
    raw_bytes = int(
        scan_lengths.nbytes
        + np.asarray(run_scan_indices, dtype=np.uint32).nbytes
        + np.asarray(run_starts, dtype=np.uint32).nbytes
        + np.asarray(run_lengths, dtype=np.uint32).nbytes
        + np.asarray(first_values, dtype=np.int64).nbytes
        + delta_values.nbytes
    )
    return packed, {
        "format": "ms1_complement_mz_qdelta",
        "backend": backend,
        "mz_precision": int(mz_precision),
        "scan_count": int(len(ms1_scans)),
        "sidecar_scope": "non_island_complement_mz",
        "scan_lengths_count": int(len(scan_lengths)),
        "complement_run_count": int(len(run_lengths)),
        "complement_point_count": int(complement_point_count),
        "full_scan_point_count": int(full_scan_point_count),
        "island_or_covered_point_count": int(max(0, full_scan_point_count - complement_point_count)),
        "raw_bytes": raw_bytes,
        "compressed_bytes": len(packed),
    }


def _decode_ms1_complement_mz_sidecar(payload: bytes, meta: Dict) -> Dict[str, Sequence]:
    sections = _unpack_named_sections(payload)
    scan_lengths_meta = json.loads(sections["scan_lengths_meta"].decode("utf-8"))
    scan_meta = json.loads(sections["scan_meta"].decode("utf-8"))
    start_meta = json.loads(sections["start_meta"].decode("utf-8"))
    length_meta = json.loads(sections["length_meta"].decode("utf-8"))
    first_meta = json.loads(sections["first_meta"].decode("utf-8"))
    delta_meta = json.loads(sections["delta_meta"].decode("utf-8"))
    scan_lengths = _decode_uint32_sidecar(sections["scan_lengths_payload"], scan_lengths_meta).astype(np.uint32, copy=False)
    run_scan_indices = _decode_uint32_sidecar(sections["scan_payload"], scan_meta).astype(np.uint32, copy=False)
    run_starts = _decode_uint32_sidecar(sections["start_payload"], start_meta).astype(np.uint32, copy=False)
    run_lengths = _decode_uint32_sidecar(sections["length_payload"], length_meta).astype(np.uint32, copy=False)
    first_values = _decode_int64_sidecar(sections["first_payload"], first_meta)
    delta_values = _decode_uint32_sidecar(sections["delta_payload"], delta_meta).astype(np.int64)
    if str(meta.get("format")) != "ms1_complement_mz_qdelta":
        raise ValueError(f"Unsupported MS1 m/z sidecar format: {meta.get('format')}")
    if len(scan_lengths) != int(meta.get("scan_count", len(scan_lengths))):
        raise ValueError(f"MS1 complement m/z sidecar scan count mismatch: {len(scan_lengths)} vs {meta.get('scan_count')}")
    if not (len(run_scan_indices) == len(run_starts) == len(run_lengths) == len(first_values)):
        raise ValueError("MS1 complement m/z sidecar run metadata length mismatch")
    scale = float(10 ** int(meta["mz_precision"]))
    delta_pos = 0
    mz_runs = []
    for run_idx, length in enumerate(run_lengths):
        n = int(length)
        if n == 0:
            mz_runs.append(np.array([], dtype=np.float64))
            continue
        mz_q = np.empty(n, dtype=np.int64)
        mz_q[0] = int(first_values[run_idx])
        if n > 1:
            local_delta = delta_values[delta_pos:delta_pos + n - 1]
            if len(local_delta) != n - 1:
                raise ValueError(f"MS1 complement m/z sidecar delta length mismatch for run {run_idx}")
            delta_pos += n - 1
            mz_q[1:] = mz_q[0] + np.cumsum(local_delta, dtype=np.int64)
        mz_runs.append(mz_q.astype(np.float64) / scale)
    if delta_pos != len(delta_values):
        raise ValueError(f"MS1 complement m/z sidecar unused deltas: {len(delta_values) - delta_pos}")
    return {
        "scan_lengths": scan_lengths,
        "scan_indices": run_scan_indices,
        "run_starts": run_starts,
        "run_lengths": run_lengths,
        "mz_runs": mz_runs,
    }


def _encode_ms1_complement_mz_sidecar_timed(
    ms1_scans: Sequence[Dict],
    tracks,
    *,
    mz_precision: int,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict, float]:
    t0 = time.perf_counter()
    payload, meta = _encode_ms1_complement_mz_sidecar(
        ms1_scans,
        tracks,
        mz_precision=mz_precision,
        backend=backend,
    )
    return payload, meta, time.perf_counter() - t0


def _extract_ms1_orphan_intensity_arrays(
    ms1_scans: Sequence[Dict],
    tracks,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    covered_index = _covered_runs_index(tracks)
    view = _ms1_scan_store_view(ms1_scans)
    if (
        view is not None
        and isinstance(tracks, CompactIslandTracks)
        and HAVE_CROSS_SCAN_SPEEDUPS
        and _cross_scan_speedups is not None
        and hasattr(_cross_scan_speedups, "ms1_collect_orphan_intensity_sidecar")
    ):
        try:
            store, global_indices = view
            indices_i64 = np.asarray(global_indices, dtype=np.int64)
            unique_scan_indices, group_starts, group_ends, order, starts, counts = covered_index
            native = _cross_scan_speedups.ms1_collect_orphan_intensity_sidecar(
                np.asarray(store.intensity_data, dtype=np.float64),
                np.asarray(store.offsets[indices_i64], dtype=np.uint64),
                np.asarray(store.lengths[indices_i64], dtype=np.uint32),
                np.asarray(store.local_scan_idx[indices_i64], dtype=np.uint32),
                np.asarray(unique_scan_indices, dtype=np.uint32),
                np.asarray(group_starts, dtype=np.uint32),
                np.asarray(group_ends, dtype=np.uint32),
                np.asarray(order, dtype=np.uint32),
                np.asarray(starts, dtype=np.uint32),
                np.asarray(counts, dtype=np.uint32),
            )
            return (
                np.asarray(native[0], dtype=np.uint32),
                np.asarray(native[1], dtype=np.uint32),
                np.asarray(native[2], dtype=np.float64),
            )
        except Exception:
            pass

    def _covered_mask_for_scan(scan: Dict, n: int) -> np.ndarray:
        scan_idx = int(scan["scan_idx"])
        covered = np.zeros(n, dtype=bool)
        for start, count in _iter_compact_runs_for_scan(covered_index, scan_idx):
            if count <= 0:
                continue
            start = max(0, int(start))
            end = min(n, start + int(count))
            if start < end:
                covered[start:end] = True
        return covered

    def _orphan_count_for_scan(scan: Dict) -> int:
        intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
        n = int(intensity.size)
        if n == 0:
            return 0
        covered = _covered_mask_for_scan(scan, n)
        return int(np.count_nonzero((intensity != 0.0) & (~covered)))

    def _orphan_positions_for_scan(scan: Dict) -> np.ndarray:
        intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
        n = int(intensity.size)
        if n == 0:
            return np.empty(0, dtype=np.uint32)
        covered = _covered_mask_for_scan(scan, n)
        return np.flatnonzero((intensity != 0.0) & (~covered)).astype(np.uint32, copy=False)

    orphan_counts = np.zeros(len(ms1_scans), dtype=np.uint32)
    for idx, scan in enumerate(ms1_scans):
        orphan_counts[idx] = np.uint32(_orphan_count_for_scan(scan))

    total_orphans = int(orphan_counts.sum())
    if total_orphans == 0:
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.float64),
        )

    orphan_scan_indices = np.empty(total_orphans, dtype=np.uint32)
    orphan_array_indices = np.empty(total_orphans, dtype=np.uint32)
    orphan_intensities = np.empty(total_orphans, dtype=np.float64)
    out_pos = 0
    for scan in ms1_scans:
        positions = _orphan_positions_for_scan(scan)
        count = int(positions.size)
        if count == 0:
            continue
        end_pos = out_pos + count
        orphan_scan_indices[out_pos:end_pos] = np.uint32(int(scan["scan_idx"]))
        orphan_array_indices[out_pos:end_pos] = positions
        intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
        orphan_intensities[out_pos:end_pos] = intensity[positions.astype(np.int64, copy=False)]
        out_pos = end_pos

    return (
        orphan_scan_indices,
        orphan_array_indices,
        orphan_intensities,
    )


def _pack_ms1_orphan_intensity_points(
    orphan_scan_indices: np.ndarray,
    orphan_array_indices: np.ndarray,
    orphan_intensities: np.ndarray,
    *,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict]:
    scan_payload, scan_meta = _encode_uint32_sidecar(orphan_scan_indices, backend=backend)
    array_payload, array_meta = _encode_uint32_sidecar(orphan_array_indices, backend=backend)
    intensity_payload, intensity_meta = _encode_float64_sidecar(orphan_intensities, backend=backend)
    packed = _pack_named_sections(
        {
            "scan_payload": scan_payload,
            "scan_meta": _json_bytes(scan_meta),
            "array_payload": array_payload,
            "array_meta": _json_bytes(array_meta),
            "intensity_payload": intensity_payload,
            "intensity_meta": _json_bytes(intensity_meta),
        }
    )
    raw_bytes = int(
        np.asarray(orphan_scan_indices, dtype=np.uint32).nbytes
        + np.asarray(orphan_array_indices, dtype=np.uint32).nbytes
        + np.asarray(orphan_intensities, dtype=np.float64).nbytes
    )
    return packed, {
        "format": "ms1_orphan_intensity_points",
        "backend": backend,
        "orphan_point_count": int(len(orphan_intensities)),
        "raw_bytes": raw_bytes,
        "compressed_bytes": len(packed),
    }


def _encode_ms1_orphan_intensity_sidecar_points(
    ms1_scans: Sequence[Dict],
    tracks,
    *,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict]:
    orphan_scan_indices, orphan_array_indices, orphan_intensities = _extract_ms1_orphan_intensity_arrays(ms1_scans, tracks)
    return _pack_ms1_orphan_intensity_points(
        orphan_scan_indices,
        orphan_array_indices,
        orphan_intensities,
        backend=backend,
    )


def _pack_ms1_orphan_intensity_runs(
    orphan_scan_indices: np.ndarray,
    orphan_array_indices: np.ndarray,
    orphan_intensities: np.ndarray,
    *,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict]:
    orphan_scan_indices = np.asarray(orphan_scan_indices, dtype=np.uint32)
    orphan_array_indices = np.asarray(orphan_array_indices, dtype=np.uint32)
    orphan_intensities = np.asarray(orphan_intensities, dtype=np.float64)
    n_points = int(orphan_array_indices.size)
    if n_points == 0:
        orphan_run_scan_deltas = np.empty(0, dtype=np.uint32)
        orphan_run_starts = np.empty(0, dtype=np.uint32)
        orphan_run_lengths = np.empty(0, dtype=np.uint32)
    else:
        run_breaks = np.empty(n_points, dtype=bool)
        run_breaks[0] = True
        run_breaks[1:] = (
            (orphan_scan_indices[1:] != orphan_scan_indices[:-1])
            | (orphan_array_indices[1:] != (orphan_array_indices[:-1] + np.uint32(1)))
        )
        run_positions = np.flatnonzero(run_breaks).astype(np.int64, copy=False)
        run_ends = np.empty_like(run_positions)
        if int(run_positions.size) > 1:
            run_ends[:-1] = run_positions[1:]
        run_ends[-1] = n_points

        run_scan_indices = orphan_scan_indices[run_positions].astype(np.uint32, copy=False)
        orphan_run_starts = orphan_array_indices[run_positions].astype(np.uint32, copy=False)
        orphan_run_lengths = (run_ends - run_positions).astype(np.uint32, copy=False)
        orphan_run_scan_deltas = np.empty_like(run_scan_indices, dtype=np.uint32)
        orphan_run_scan_deltas[0] = run_scan_indices[0]
        if int(run_scan_indices.size) > 1:
            orphan_run_scan_deltas[1:] = (run_scan_indices[1:] - run_scan_indices[:-1]).astype(np.uint32, copy=False)

    scan_payload, scan_meta = _encode_uint32_sidecar(orphan_run_scan_deltas, backend=backend)
    start_payload, start_meta = _encode_uint32_sidecar(orphan_run_starts, backend=backend)
    length_payload, length_meta = _encode_uint32_sidecar(orphan_run_lengths, backend=backend)
    intensity_payload, intensity_meta = _encode_float64_sidecar(orphan_intensities, backend=backend)
    packed = _pack_named_sections(
        {
            "scan_payload": scan_payload,
            "scan_meta": _json_bytes(scan_meta),
            "start_payload": start_payload,
            "start_meta": _json_bytes(start_meta),
            "length_payload": length_payload,
            "length_meta": _json_bytes(length_meta),
            "intensity_payload": intensity_payload,
            "intensity_meta": _json_bytes(intensity_meta),
        }
    )
    raw_bytes = int(
        np.asarray(orphan_run_scan_deltas, dtype=np.uint32).nbytes
        + np.asarray(orphan_run_starts, dtype=np.uint32).nbytes
        + np.asarray(orphan_run_lengths, dtype=np.uint32).nbytes
        + orphan_intensities.nbytes
    )
    return packed, {
        "format": "ms1_orphan_intensity_runs",
        "backend": backend,
        "orphan_point_count": int(n_points),
        "orphan_run_count": int(orphan_run_lengths.size),
        "raw_bytes": raw_bytes,
        "compressed_bytes": len(packed),
    }


def _encode_ms1_orphan_intensity_sidecar_runs(
    ms1_scans: Sequence[Dict],
    tracks,
    *,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict]:
    orphan_scan_indices, orphan_array_indices, orphan_intensities = _extract_ms1_orphan_intensity_arrays(ms1_scans, tracks)
    return _pack_ms1_orphan_intensity_runs(
        orphan_scan_indices,
        orphan_array_indices,
        orphan_intensities,
        backend=backend,
    )


def _encode_ms1_orphan_intensity_sidecar(
    ms1_scans: Sequence[Dict],
    tracks,
    *,
    backend: str = "zstd-9",
) -> Tuple[bytes, Dict]:
    orphan_scan_indices, orphan_array_indices, orphan_intensities = _extract_ms1_orphan_intensity_arrays(ms1_scans, tracks)

    def _pack_points():
        return _pack_ms1_orphan_intensity_points(
            orphan_scan_indices,
            orphan_array_indices,
            orphan_intensities,
            backend=backend,
        )

    def _pack_runs():
        return _pack_ms1_orphan_intensity_runs(
            orphan_scan_indices,
            orphan_array_indices,
            orphan_intensities,
            backend=backend,
        )

    if int(orphan_intensities.size) >= 4096:
        with ThreadPoolExecutor(max_workers=2) as executor:
            points_future = executor.submit(_pack_points)
            runs_future = executor.submit(_pack_runs)
            points_payload, points_meta = points_future.result()
            runs_payload, runs_meta = runs_future.result()
    else:
        points_payload, points_meta = _pack_points()
        runs_payload, runs_meta = _pack_runs()
    if int(runs_meta.get("compressed_bytes", len(runs_payload))) < int(points_meta.get("compressed_bytes", len(points_payload))):
        runs_meta = {
            **runs_meta,
            "adaptive_orphan_sidecar": True,
            "candidate_points_compressed_bytes": int(points_meta.get("compressed_bytes", len(points_payload))),
            "candidate_runs_compressed_bytes": int(runs_meta.get("compressed_bytes", len(runs_payload))),
        }
        return runs_payload, runs_meta
    points_meta = {
        **points_meta,
        "adaptive_orphan_sidecar": True,
        "candidate_points_compressed_bytes": int(points_meta.get("compressed_bytes", len(points_payload))),
        "candidate_runs_compressed_bytes": int(runs_meta.get("compressed_bytes", len(runs_payload))),
    }
    return points_payload, points_meta


def _decode_ms1_orphan_intensity_sidecar(payload: bytes, meta: Dict) -> Dict[str, np.ndarray]:
    sections = _unpack_named_sections(payload)
    fmt = str(meta["format"])
    intensity_meta = json.loads(sections["intensity_meta"].decode("utf-8"))
    intensity_values = _decode_float64_sidecar(sections["intensity_payload"], intensity_meta)
    expected = int(meta.get("orphan_point_count", len(intensity_values)))
    if len(intensity_values) != expected:
        raise ValueError(f"MS1 orphan intensity sidecar count mismatch: {len(intensity_values)} vs {expected}")

    if fmt == "ms1_orphan_intensity_runs":
        scan_meta = json.loads(sections["scan_meta"].decode("utf-8"))
        start_meta = json.loads(sections["start_meta"].decode("utf-8"))
        length_meta = json.loads(sections["length_meta"].decode("utf-8"))
        scan_deltas = _decode_uint32_sidecar(sections["scan_payload"], scan_meta)
        run_starts = _decode_uint32_sidecar(sections["start_payload"], start_meta)
        run_lengths = _decode_uint32_sidecar(sections["length_payload"], length_meta)
        expected_runs = int(meta.get("orphan_run_count", len(run_lengths)))
        if len(scan_deltas) != len(run_starts) or len(scan_deltas) != len(run_lengths):
            raise ValueError("MS1 orphan intensity run sidecar length mismatch")
        if len(run_lengths) != expected_runs:
            raise ValueError(f"MS1 orphan intensity run sidecar count mismatch: {len(run_lengths)} vs {expected_runs}")
        if int(run_lengths.astype(np.uint64, copy=False).sum()) != len(intensity_values):
            raise ValueError("MS1 orphan intensity run sidecar point count mismatch")

        run_scan_indices = np.zeros(len(scan_deltas), dtype=np.uint32)
        prev_scan_idx = 0
        for idx, delta in enumerate(scan_deltas.astype(np.uint64, copy=False)):
            if idx == 0:
                curr_scan = int(delta)
            else:
                curr_scan = prev_scan_idx + int(delta)
            run_scan_indices[idx] = curr_scan
            prev_scan_idx = curr_scan

        point_count = int(run_lengths.astype(np.uint64, copy=False).sum())
        scan_indices = np.empty(point_count, dtype=np.uint32)
        array_indices = np.empty(point_count, dtype=np.uint32)
        pos = 0
        for scan_idx, start, length in zip(run_scan_indices, run_starts, run_lengths):
            length_i = int(length)
            if length_i <= 0:
                continue
            end = pos + length_i
            scan_indices[pos:end] = int(scan_idx)
            array_indices[pos:end] = np.arange(int(start), int(start) + length_i, dtype=np.uint32)
            pos = end
        if pos != point_count:
            raise ValueError("MS1 orphan intensity run sidecar expansion mismatch")
        return {
            "scan_indices": scan_indices,
            "array_indices": array_indices,
            "intensity_values": intensity_values.astype(np.float64, copy=False),
        }

    scan_meta = json.loads(sections["scan_meta"].decode("utf-8"))
    array_meta = json.loads(sections["array_meta"].decode("utf-8"))
    scan_indices = _decode_uint32_sidecar(sections["scan_payload"], scan_meta)
    array_indices = _decode_uint32_sidecar(sections["array_payload"], array_meta)
    if len(scan_indices) != len(array_indices) or len(scan_indices) != len(intensity_values):
        raise ValueError("MS1 orphan intensity sidecar length mismatch")
    return {
        "scan_indices": scan_indices.astype(np.uint32, copy=False),
        "array_indices": array_indices.astype(np.uint32, copy=False),
        "intensity_values": intensity_values.astype(np.float64, copy=False),
    }


def _apply_ms1_orphan_intensities(
    reconstructed: List[Dict],
    orphan_intensity_entries: Dict[str, np.ndarray],
) -> None:
    scan_indices = np.asarray(orphan_intensity_entries["scan_indices"], dtype=np.uint32)
    array_indices = np.asarray(orphan_intensity_entries["array_indices"], dtype=np.uint32)
    intensity_values = np.asarray(orphan_intensity_entries["intensity_values"], dtype=np.float64)
    if scan_indices.size == 0:
        return
    if scan_indices.size != array_indices.size or scan_indices.size != intensity_values.size:
        raise ValueError("MS1 orphan intensity sidecar length mismatch")
    if int(scan_indices.max()) >= len(reconstructed):
        raise ValueError(f"MS1 orphan intensity sidecar scan index out of range: {int(scan_indices.max())}")

    order = np.argsort(scan_indices, kind="stable")
    sorted_scan_indices = scan_indices[order]
    change_pos = np.flatnonzero(sorted_scan_indices[1:] != sorted_scan_indices[:-1]).astype(np.int64) + 1
    group_starts = np.concatenate((np.array([0], dtype=np.int64), change_pos))
    group_ends = np.concatenate((change_pos, np.array([sorted_scan_indices.size], dtype=np.int64)))
    for start, end in zip(group_starts, group_ends):
        group_order = order[start:end]
        scan_idx_i = int(sorted_scan_indices[start])
        intensity_array = reconstructed[scan_idx_i]["intensity_array"]
        group_array_indices = array_indices[group_order].astype(np.int64, copy=False)
        if group_array_indices.size == 0:
            continue
        if int(group_array_indices.max()) >= len(intensity_array):
            raise ValueError(
                f"MS1 orphan intensity sidecar array index out of range: scan {scan_idx_i}, "
                f"index {int(group_array_indices.max())}"
            )
        values = intensity_values[group_order]
        if np.unique(group_array_indices).size != group_array_indices.size:
            for array_idx_i, intensity in zip(group_array_indices, values):
                intensity_array[int(array_idx_i)] = float(intensity)
        else:
            intensity_array[group_array_indices] = values


class _MS1OrphanIntensityLookup:
    def __init__(self, orphan_intensity_entries: Dict[str, np.ndarray] | None):
        self._groups: Dict[int, tuple[np.ndarray, np.ndarray]] = {}
        if orphan_intensity_entries is None:
            return
        scan_indices = np.asarray(orphan_intensity_entries["scan_indices"], dtype=np.uint32)
        array_indices = np.asarray(orphan_intensity_entries["array_indices"], dtype=np.uint32)
        intensity_values = np.asarray(orphan_intensity_entries["intensity_values"], dtype=np.float64)
        if scan_indices.size == 0:
            return
        if scan_indices.size != array_indices.size or scan_indices.size != intensity_values.size:
            raise ValueError("MS1 orphan intensity sidecar length mismatch")
        order = np.argsort(scan_indices, kind="stable")
        sorted_scan_indices = scan_indices[order]
        change_pos = np.flatnonzero(sorted_scan_indices[1:] != sorted_scan_indices[:-1]).astype(np.int64) + 1
        group_starts = np.concatenate((np.array([0], dtype=np.int64), change_pos))
        group_ends = np.concatenate((change_pos, np.array([sorted_scan_indices.size], dtype=np.int64)))
        for start, end in zip(group_starts, group_ends):
            group_order = order[start:end]
            self._groups[int(sorted_scan_indices[start])] = (
                array_indices[group_order].astype(np.int64, copy=False),
                intensity_values[group_order],
            )

    def apply(self, scan_idx: int, intensity_array: np.ndarray) -> None:
        entry = self._groups.get(int(scan_idx))
        if entry is None:
            return
        group_array_indices, values = entry
        if group_array_indices.size == 0:
            return
        if int(group_array_indices.max()) >= len(intensity_array):
            raise ValueError(
                f"MS1 orphan intensity sidecar array index out of range: scan {int(scan_idx)}, "
                f"index {int(group_array_indices.max())}"
            )
        if np.unique(group_array_indices).size != group_array_indices.size:
            for array_idx_i, intensity in zip(group_array_indices, values):
                intensity_array[int(array_idx_i)] = float(intensity)
        else:
            intensity_array[group_array_indices] = values


def _encode_json_section(obj: Dict | List, backend: str = "zstd-9") -> Tuple[bytes, Dict]:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = compress(raw, backend)
    return payload, {
        "backend": backend,
        "format": "json",
        "raw_bytes": len(raw),
        "compressed_bytes": len(payload),
    }


def _decode_json_section(payload: bytes, meta: Dict):
    raw = decompress(payload, meta["backend"])
    return json.loads(raw.decode("utf-8"))


def _extract_and_encode_metadata_auxiliary_timed(
    mzml_path: str | Path,
    metadata_codec: MzMLMetadataCodec,
    auxiliary_backend: str = "zstd-9",
    cache_root: str | Path | None = None,
    metadata_xml_bytes: bytes | None = None,
    auxiliary_records: List[Dict] | None = None,
) -> Tuple[bytes, Dict, bytes, Dict, Dict]:
    t0 = time.perf_counter()
    if metadata_xml_bytes is not None and auxiliary_records is not None:
        metadata_xml_bytes = bytes(metadata_xml_bytes)
        auxiliary_records = list(auxiliary_records)
    elif cache_root is not None:
        cache_dir = Path(cache_root) / "metadata_aux_cache"
        metadata_xml_path = cache_dir / "metadata_xml.bin"
        auxiliary_records_path = cache_dir / "auxiliary_records.json"
        if metadata_xml_path.exists() and auxiliary_records_path.exists():
            metadata_xml_bytes = metadata_xml_path.read_bytes()
            auxiliary_records = json.loads(auxiliary_records_path.read_text(encoding="utf-8"))
        else:
            metadata_xml_bytes, auxiliary_records = extract_binary_stripped_metadata_and_auxiliary_records(str(mzml_path))
            _write_bytes_atomic(metadata_xml_path, metadata_xml_bytes)
            _write_text_atomic(
                auxiliary_records_path,
                json.dumps(auxiliary_records, ensure_ascii=False, separators=(",", ":")),
            )
    else:
        metadata_xml_bytes, auxiliary_records = extract_binary_stripped_metadata_and_auxiliary_records(str(mzml_path))
    extract_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    metadata_payload, metadata_meta = metadata_codec.encode_bytes(metadata_xml_bytes)
    metadata_encode_s = time.perf_counter() - t1

    t2 = time.perf_counter()
    auxiliary_payload, auxiliary_meta = encode_auxiliary_records(
        metadata_xml_bytes,
        auxiliary_records,
        backend=auxiliary_backend,
    )
    auxiliary_encode_s = time.perf_counter() - t2

    return (
        metadata_payload,
        metadata_meta,
        auxiliary_payload,
        auxiliary_meta,
        {
            "extract_metadata_auxiliary_s": extract_s,
            "encode_metadata_s": metadata_encode_s,
            "encode_auxiliary_s": auxiliary_encode_s,
            "metadata_auxiliary_total_s": extract_s + metadata_encode_s + auxiliary_encode_s,
        },
    )


def _metadata_auxiliary_payload_cache_key(
    mzml_path: str | Path,
    *,
    metadata_backend: str,
    auxiliary_backend: str,
) -> str | None:
    try:
        path = Path(mzml_path).resolve()
        stat = path.stat()
        h = hashlib.sha256()
        h.update(b"trackcodec-archive-metadata-aux-payload")
        h.update(str(path).encode("utf-8", errors="surrogatepass"))
        h.update(b"\0")
        h.update(str(int(stat.st_size)).encode("ascii"))
        h.update(b"\0")
        h.update(str(int(stat.st_mtime_ns)).encode("ascii"))
        h.update(b"\0")
        h.update(str(metadata_backend).encode("utf-8"))
        h.update(b"\0")
        h.update(str(auxiliary_backend).encode("utf-8"))
        return h.hexdigest()
    except Exception:
        return None


def _extract_and_encode_metadata_auxiliary_payloads_cached(
    mzml_path: str | Path,
    metadata_codec: MzMLMetadataCodec,
    auxiliary_backend: str = "zstd-9",
    cache_root: str | Path | None = None,
    metadata_xml_bytes: bytes | None = None,
    auxiliary_records: List[Dict] | None = None,
) -> Tuple[Path, Dict, Path, Dict, Dict]:
    if cache_root is None:
        payload, meta, aux_payload, aux_meta, timing = _extract_and_encode_metadata_auxiliary_timed(
            mzml_path,
            metadata_codec,
            auxiliary_backend=auxiliary_backend,
            cache_root=None,
            metadata_xml_bytes=metadata_xml_bytes,
            auxiliary_records=auxiliary_records,
        )
        tmp_dir = Path(tempfile.mkdtemp(prefix="trackcodec_metadata_aux_payloads_"))
        metadata_path = tmp_dir / "metadata_payload"
        auxiliary_path = tmp_dir / "auxiliary_payload"
        metadata_path.write_bytes(payload)
        auxiliary_path.write_bytes(aux_payload)
        return metadata_path, meta, auxiliary_path, aux_meta, timing

    key = _metadata_auxiliary_payload_cache_key(
        mzml_path,
        metadata_backend=str(metadata_codec.backend),
        auxiliary_backend=str(auxiliary_backend),
    )
    if key is None:
        return _extract_and_encode_metadata_auxiliary_payloads_cached(
            mzml_path,
            metadata_codec,
            auxiliary_backend=auxiliary_backend,
            cache_root=None,
        )

    cache_dir = Path(cache_root) / "metadata_aux_payload_cache" / key
    metadata_path = cache_dir / "metadata_payload"
    metadata_meta_path = cache_dir / "metadata_meta.json"
    auxiliary_path = cache_dir / "auxiliary_payload"
    auxiliary_meta_path = cache_dir / "auxiliary_meta.json"
    if (
        metadata_path.exists()
        and metadata_meta_path.exists()
        and auxiliary_path.exists()
        and auxiliary_meta_path.exists()
    ):
        t0 = time.perf_counter()
        metadata_meta = json.loads(metadata_meta_path.read_text(encoding="utf-8"))
        auxiliary_meta = json.loads(auxiliary_meta_path.read_text(encoding="utf-8"))
        return (
            metadata_path,
            metadata_meta,
            auxiliary_path,
            auxiliary_meta,
            {
                "metadata_auxiliary_payload_cache_hit": 1.0,
                "metadata_auxiliary_total_s": time.perf_counter() - t0,
            },
        )

    payload, meta, aux_payload, aux_meta, timing = _extract_and_encode_metadata_auxiliary_timed(
        mzml_path,
        metadata_codec,
        auxiliary_backend=auxiliary_backend,
        cache_root=cache_root,
        metadata_xml_bytes=metadata_xml_bytes,
        auxiliary_records=auxiliary_records,
    )
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_bytes_atomic(metadata_path, payload)
        _write_text_atomic(metadata_meta_path, json.dumps(meta, ensure_ascii=False, separators=(",", ":")))
        _write_bytes_atomic(auxiliary_path, aux_payload)
        _write_text_atomic(auxiliary_meta_path, json.dumps(aux_meta, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        tmp_dir = Path(tempfile.mkdtemp(prefix="trackcodec_metadata_aux_payloads_"))
        metadata_path = tmp_dir / "metadata_payload"
        auxiliary_path = tmp_dir / "auxiliary_payload"
        metadata_path.write_bytes(payload)
        auxiliary_path.write_bytes(aux_payload)
    return metadata_path, meta, auxiliary_path, aux_meta, timing


def _serialize_ms2_encoded(encoded: Dict, file_type: str) -> bytes:
    body = {
        "v": 2,
        "t": file_type,
        "rb": int(encoded["raw_bytes"]),
        "cb": int(encoded["compressed_bytes"]),
        "cr": float(encoded["compression_ratio"]),
        "bk": encoded.get("container_backend"),
        "em": encoded.get("exact_track_container_mode"),
        "ep": encoded.get("exact_track_parallel_workers"),
    }
    sections: Dict[str, bytes] = {}
    section_blob = encoded.get("section_blob")
    container_blob = encoded.get("container_blob")
    if section_blob is not None:
        body["m"] = "section_blob"
        sections["s"] = section_blob
    elif container_blob is not None:
        body["m"] = "container_blob"
        sections["c"] = container_blob
    elif file_type == "DIA":
        body["m"] = "windows"
        body["w"] = [list(item["window_key"]) for item in encoded.get("windows", [])]
        for idx, item in enumerate(encoded.get("windows", [])):
            sections[f"p{idx}"] = item["payload"]
    else:
        body["m"] = "blocks"
        body["b"] = [int(item["block_start"]) for item in encoded.get("blocks", [])]
        for idx, item in enumerate(encoded.get("blocks", [])):
            sections[f"p{idx}"] = item["payload"]
    sections = {"h": _json_bytes(body), **sections}
    return _pack_ms2_sections(sections)


def _serialize_ms2_encoded_to_path(encoded: Dict, file_type: str, output_path: str | Path) -> int:
    body = {
        "v": 2,
        "t": file_type,
        "rb": int(encoded["raw_bytes"]),
        "cb": int(encoded["compressed_bytes"]),
        "cr": float(encoded["compression_ratio"]),
        "bk": encoded.get("container_backend"),
        "em": encoded.get("exact_track_container_mode"),
        "ep": encoded.get("exact_track_parallel_workers"),
    }
    sections: Dict[str, bytes] = {}
    section_blob = encoded.get("section_blob")
    container_blob = encoded.get("container_blob")
    if section_blob is not None:
        body["m"] = "section_blob"
        sections["s"] = section_blob
    elif container_blob is not None:
        body["m"] = "container_blob"
        sections["c"] = container_blob
    elif file_type == "DIA":
        body["m"] = "windows"
        body["w"] = [list(item["window_key"]) for item in encoded.get("windows", [])]
        for idx, item in enumerate(encoded.get("windows", [])):
            sections[f"p{idx}"] = item["payload"]
    else:
        body["m"] = "blocks"
        body["b"] = [int(item["block_start"]) for item in encoded.get("blocks", [])]
        for idx, item in enumerate(encoded.get("blocks", [])):
            sections[f"p{idx}"] = item["payload"]
    sections = {"h": _json_bytes(body), **sections}
    return _pack_ms2_sections_to_file(sections, output_path)


def _write_json_file(path: str | Path, payload: Dict) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_json_file(path: str | Path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_bytes_atomic(path: str | Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_bytes(data)
    tmp.replace(path)


def _write_text_atomic(path: str | Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _copy_file_atomic(src: str | Path, dst: str | Path) -> None:
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
    shutil.copyfile(src, tmp)
    tmp.replace(dst)


def _stage_file_complete(path: str | Path, meta_path: str | Path | None = None) -> bool:
    path = Path(path)
    if not path.exists() or path.stat().st_size < 0:
        return False
    if meta_path is not None and not Path(meta_path).exists():
        return False
    return True


def _path_file_size(path: str | Path) -> int:
    return int(Path(path).stat().st_size)


def _stable_cache_digest(payload: Dict | List | Tuple | str) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _safe_cache_name(name: str, max_len: int = 96) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(name))
    cleaned = cleaned.strip("._")
    if not cleaned:
        cleaned = "mzml"
    return cleaned[: max(8, int(max_len))]


def _archive_source_cache_root(cache_dir: str | Path, mzml_path: Path, file_type: str) -> Path:
    stat = mzml_path.stat()
    digest = _stable_cache_digest(
        {
            "cache_kind": "archive_source_cache",
            "source_file": str(mzml_path.resolve()),
            "file_type": str(file_type),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    )[:20]
    return Path(cache_dir) / f"{_safe_cache_name(mzml_path.name)}.{digest}"


def _archive_section_cache_base(scan_store_root: str | Path, ms1_track_store_root: str | Path | None = None) -> Path:
    if ms1_track_store_root:
        return Path(ms1_track_store_root).parent / "whole_archive_section_cache"
    return Path(scan_store_root) / "whole_archive_section_cache"


def _archive_ms1_section_cache_dir(
    base: str | Path,
    *,
    ms1_mode: Dict,
    ms1_sidecar_backend: str,
    ms1_full_mz_backend: str,
    retain_zero_intensity_mz: bool,
    strategy_b_valley_ratio: float,
    sparse_bypass_config: Dict | None = None,
) -> Path:
    digest = _stable_cache_digest(
        {
            "cache_kind": "archive_ms1_section",
            "ms1_mode": ms1_mode,
            "ms1_sidecar_backend": ms1_sidecar_backend,
            "ms1_full_mz_backend": ms1_full_mz_backend,
            "retain_zero_intensity_mz": bool(retain_zero_intensity_mz),
            "strategy_b_valley_ratio": float(strategy_b_valley_ratio),
            "sparse_profile_bypass": sparse_bypass_config or {"enabled": False},
        }
    )
    return Path(base) / "ms1" / digest


def _archive_ms1_payload_cache_dir(
    base: str | Path,
    *,
    ms1_mode: Dict,
    strategy_b_valley_ratio: float,
    sparse_bypass_config: Dict | None = None,
) -> Path:
    digest = _stable_cache_digest(
        {
            "cache_kind": "archive_ms1_payload",
            "ms1_mode": ms1_mode,
            "strategy_b_valley_ratio": float(strategy_b_valley_ratio),
            "sparse_profile_bypass": sparse_bypass_config or {"enabled": False},
        }
    )
    return Path(base) / "ms1_payload" / digest


def _archive_ms1_sidecar_cache_dir(
    base: str | Path,
    *,
    mz_precision: int,
    ms1_sidecar_backend: str,
    ms1_full_mz_backend: str,
    retain_zero_intensity_mz: bool,
    strategy_b_valley_ratio: float,
    sparse_bypass_config: Dict | None = None,
) -> Path:
    digest = _stable_cache_digest(
        {
            "cache_kind": "archive_ms1_sidecars",
            "mz_precision": int(mz_precision),
            "ms1_sidecar_backend": str(ms1_sidecar_backend),
            "ms1_full_mz_backend": str(ms1_full_mz_backend),
            "retain_zero_intensity_mz": bool(retain_zero_intensity_mz),
            "strategy_b_valley_ratio": float(strategy_b_valley_ratio),
            "sparse_profile_bypass": sparse_bypass_config or {"enabled": False},
        }
    )
    return Path(base) / "ms1_sidecars" / digest


def _archive_ms2_section_cache_dir(
    base: str | Path,
    *,
    file_type: str,
    ms2_config_dict: Dict,
) -> Path:
    digest = _stable_cache_digest(
        {
            "cache_kind": "archive_ms2_section",
            "file_type": str(file_type),
            "ms2_config": ms2_config_dict,
        }
    )
    return Path(base) / "ms2" / digest


def _append_progress_jsonl(path: str | Path | None, payload: Dict) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _drain_progress_jsonl(path: str | Path, offset: int) -> tuple[list[Dict], int]:
    target = Path(path)
    if not target.exists():
        return [], offset
    items: list[Dict] = []
    with target.open("r", encoding="utf-8") as handle:
        handle.seek(offset)
        for line in handle:
            line = line.strip()
            if not line:
                continue
            items.append(json.loads(line))
        offset = handle.tell()
    return items, offset


def _clamp_pct(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(100.0, float(value)))


def _make_progress_reporter(
    progress_callback: Callable[[Dict], None] | None,
    *,
    scope: str,
) -> Callable[[str, Dict[str, float | int | str | None]], None]:
    state = {
        "ms1_pct": 0.0,
        "ms2_pct": 0.0,
        "metadata_pct": 0.0,
    }

    def _report(stage: str, updates: Dict[str, float | int | str | None]) -> None:
        if progress_callback is None:
            return
        if "ms1_pct" in updates:
            state["ms1_pct"] = _clamp_pct(updates["ms1_pct"])
        if "ms2_pct" in updates:
            state["ms2_pct"] = _clamp_pct(updates["ms2_pct"])
        if "metadata_pct" in updates:
            state["metadata_pct"] = _clamp_pct(updates["metadata_pct"])
        payload = {
            "scope": scope,
            "stage": stage,
            "ms1_pct": float(state["ms1_pct"]),
            "ms2_pct": float(state["ms2_pct"]),
            "metadata_pct": float(state["metadata_pct"]),
            "overall_pct": float((state["ms1_pct"] + state["ms2_pct"] + state["metadata_pct"]) / 3.0),
        }
        for key, value in updates.items():
            if key not in {"ms1_pct", "ms2_pct", "metadata_pct"} and value is not None:
                payload[key] = value
        try:
            progress_callback(payload)
        except Exception:
            pass

    return _report


def _can_use_fork_process_parallelism() -> bool:
    if os.name != "posix":
        return False
    try:
        mp.get_context("fork")
    except ValueError:
        return False
    return True


def _archive_ms1_worker_process(
    section_root: str,
    status_path: str,
    error_path: str,
    progress_path: str,
    scan_store_root: str,
    ms1_track_store_root: str | None,
    ms1_mode: Dict,
    ms1_island_workers: int | None,
    ms1_encode_section_workers: int,
    ms1_sidecar_backend: str,
    ms1_full_mz_backend: str,
    retain_zero_intensity_mz: bool,
    strategy_b_valley_ratio: float,
    sparse_profile_bypass: bool,
    sparse_profile_short_max_points: int,
    sparse_profile_min_zero_fraction: float,
    sparse_profile_min_islands_per_scan: float,
    sparse_profile_min_short_fraction: float,
) -> None:
    try:
        ms1_mode = _make_archive_ms1_mode(
            ms1_mode,
            external_full_mz_sidecar=bool(retain_zero_intensity_mz),
        )
        section_dir = Path(section_root)
        section_dir.mkdir(parents=True, exist_ok=True)
        stage_timings: Dict[str, float] = {}
        total_t0 = time.perf_counter()
        _append_progress_jsonl(progress_path, {"section": "ms1", "stage": "worker_boot", "pct": 0.0})
        cache_base = _archive_section_cache_base(scan_store_root, ms1_track_store_root)
        sparse_bypass_config = _archive_sparse_profile_bypass_config(
            enabled=bool(sparse_profile_bypass) and bool(retain_zero_intensity_mz),
            min_zero_fraction=float(sparse_profile_min_zero_fraction),
            min_islands_per_scan=float(sparse_profile_min_islands_per_scan),
            min_short_fraction=float(sparse_profile_min_short_fraction),
            short_max_points=int(sparse_profile_short_max_points),
        )
        cache_section_dir = _archive_ms1_section_cache_dir(
            cache_base,
            ms1_mode=ms1_mode,
            ms1_sidecar_backend=ms1_sidecar_backend,
            ms1_full_mz_backend=ms1_full_mz_backend,
            retain_zero_intensity_mz=bool(retain_zero_intensity_mz),
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
            sparse_bypass_config=sparse_bypass_config,
        )
        cache_section_dir.mkdir(parents=True, exist_ok=True)
        cache_payload_dir = _archive_ms1_payload_cache_dir(
            cache_base,
            ms1_mode=ms1_mode,
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
            sparse_bypass_config=sparse_bypass_config,
        )
        cache_sidecar_dir = _archive_ms1_sidecar_cache_dir(
            cache_base,
            mz_precision=int(ms1_mode["mz_precision"]),
            ms1_sidecar_backend=ms1_sidecar_backend,
            ms1_full_mz_backend=ms1_full_mz_backend,
            retain_zero_intensity_mz=bool(retain_zero_intensity_mz),
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
            sparse_bypass_config=sparse_bypass_config,
        )
        cache_payload_dir.mkdir(parents=True, exist_ok=True)
        cache_sidecar_dir.mkdir(parents=True, exist_ok=True)
        ms1_payload_path = section_dir / "ms1_payload"
        ms1_meta_path = section_dir / "ms1_meta"
        ms1_array_starts_payload_path = section_dir / "ms1_array_starts_payload"
        ms1_array_starts_meta_path = section_dir / "ms1_array_starts_meta"
        ms1_orphan_payload_path = section_dir / "ms1_orphan_intensity_payload"
        ms1_orphan_meta_path = section_dir / "ms1_orphan_intensity_meta"
        ms1_full_mz_payload_path = section_dir / "ms1_full_mz_payload"
        ms1_full_mz_meta_path = section_dir / "ms1_full_mz_meta"
        cache_ms1_payload_path = cache_payload_dir / "ms1_payload"
        cache_ms1_meta_path = cache_payload_dir / "ms1_meta"
        cache_array_payload_path = cache_sidecar_dir / "ms1_array_starts_payload"
        cache_array_meta_path = cache_sidecar_dir / "ms1_array_starts_meta"
        cache_orphan_payload_path = cache_sidecar_dir / "ms1_orphan_intensity_payload"
        cache_orphan_meta_path = cache_sidecar_dir / "ms1_orphan_intensity_meta"
        cache_full_mz_payload_path = cache_sidecar_dir / "ms1_full_mz_payload"
        cache_full_mz_meta_path = cache_sidecar_dir / "ms1_full_mz_meta"
        t_open = time.perf_counter()
        scan_store = open_memmap_scan_store(scan_store_root)
        ms1_scans = scan_store.ms1_scans()
        stage_timings["open_ms1_scan_store_s"] = time.perf_counter() - t_open
        _append_progress_jsonl(progress_path, {"section": "ms1", "stage": "start", "pct": 0.0})
        track_store_path = _archive_track_store_path(Path(ms1_track_store_root), sparse_bypass_config) if ms1_track_store_root else None

        with ThreadPoolExecutor(max_workers=1) as side_executor:
            if track_store_path is not None and (track_store_path / "manifest.json").exists():
                t0 = time.perf_counter()
                tracks = open_compact_track_store(track_store_path)
                stage_timings["open_ms1_tracks_store_s"] = time.perf_counter() - t0
                sparse_bypass_stats = {"cache_hit": True, **sparse_bypass_config}
            else:
                t0 = time.perf_counter()
                tracks, sparse_bypass_stats = build_ms1_tracks_for_archive(
                    ms1_scans,
                    island_workers=ms1_island_workers,
                    compact=True,
                    retain_zero_intensity_mz=bool(retain_zero_intensity_mz),
                    sparse_profile_bypass=bool(sparse_profile_bypass),
                    sparse_short_max_points=int(sparse_profile_short_max_points),
                    sparse_min_zero_fraction=float(sparse_profile_min_zero_fraction),
                    sparse_min_islands_per_scan=float(sparse_profile_min_islands_per_scan),
                    sparse_min_short_fraction=float(sparse_profile_min_short_fraction),
                    strategy_b_valley_ratio=float(strategy_b_valley_ratio),
                    drop_islands_in_compact=True,
                )
                stage_timings["build_ms1_tracks_s"] = time.perf_counter() - t0
                if isinstance(tracks, CompactIslandTracks) and len(tracks.islands) == 0:
                    t0 = time.perf_counter()
                    spill_root = track_store_path if track_store_path is not None else (section_dir / "ms1_tracks_store")
                    tracks = write_compact_track_store(spill_root, tracks)
                    stage_timings["spill_ms1_tracks_store_s"] = time.perf_counter() - t0
            stage_timings["ms1_sparse_profile_bypass_triggered"] = 1.0 if sparse_bypass_stats.get("triggered") else 0.0
            stage_timings["ms1_sparse_profile_bypass_original_islands"] = float(sparse_bypass_stats.get("original_island_count", 0) or 0)
            stage_timings["ms1_sparse_profile_bypass_trackable_islands"] = float(sparse_bypass_stats.get("trackable_island_count", 0) or 0)
            _append_progress_jsonl(progress_path, {"section": "ms1", "stage": "build_ms1_tracks_done", "pct": 30.0})

            full_mz_cache_ready = bool(retain_zero_intensity_mz) and _stage_file_complete(
                cache_full_mz_payload_path,
                cache_full_mz_meta_path,
            )
            ms1_full_mz_future = (
                side_executor.submit(
                    _encode_ms1_full_mz_sidecar_timed_cached,
                    ms1_scans,
                    mz_precision=ms1_mode["mz_precision"],
                    backend=ms1_full_mz_backend,
                    cache_dir=section_dir / "ms1_full_mz_sidecar_cache",
                    compression_workers=max(1, int(ms1_encode_section_workers)),
                )
                if retain_zero_intensity_mz and not full_mz_cache_ready
                else None
            )

            if _stage_file_complete(cache_ms1_payload_path, cache_ms1_meta_path):
                t0 = time.perf_counter()
                _copy_file_atomic(cache_ms1_payload_path, ms1_payload_path)
                _copy_file_atomic(cache_ms1_meta_path, ms1_meta_path)
                ms1_meta = json.loads(ms1_meta_path.read_text(encoding="utf-8"))
                stage_timings["ms1_encode_s"] = 0.0
                stage_timings["ms1_payload_cache_hit_s"] = time.perf_counter() - t0
            else:
                ms1_codec = MS1Codec(
                    mz_precision=ms1_mode["mz_precision"],
                    intensity_mode=ms1_mode["intensity_mode"],
                    backend=ms1_mode["backend"],
                    encode_section_workers=ms1_encode_section_workers,
                    **ms1_mode["kwargs"],
                )
                def _ms1_progress(item: Dict) -> None:
                    payload = {
                        "section": "ms1",
                        "stage": str(item.get("stage", "ms1_progress")),
                        "pct": float(30.0 + 48.0 * _clamp_pct(item.get("pct")) / 100.0),
                    }
                    for key in ("done_tracks", "total_tracks", "done_islands", "total_islands", "done_sections", "total_sections", "track_pct"):
                        if item.get(key) is not None:
                            payload[key] = item.get(key)
                    _append_progress_jsonl(progress_path, payload)
                t0 = time.perf_counter()
                _, ms1_meta = ms1_codec.encode_to_path(
                    tracks,
                    ms1_scans,
                    output_path=ms1_payload_path,
                    progress_callback=_ms1_progress,
                )
                stage_timings["ms1_encode_s"] = time.perf_counter() - t0
                ms1_runtime_stage_timings = _pop_runtime_stage_timings(ms1_meta)
                _merge_prefixed_timings(
                    stage_timings,
                    ms1_runtime_stage_timings,
                    prefix="ms1_internal_",
                )
                _write_section_meta_atomic(ms1_meta_path, ms1_meta)
                _copy_file_atomic(ms1_payload_path, cache_ms1_payload_path)
                _copy_file_atomic(ms1_meta_path, cache_ms1_meta_path)
            _append_progress_jsonl(progress_path, {"section": "ms1", "stage": "ms1_payload_done", "pct": 78.0})

            if _stage_file_complete(cache_array_payload_path, cache_array_meta_path):
                t0 = time.perf_counter()
                _copy_file_atomic(cache_array_payload_path, ms1_array_starts_payload_path)
                _copy_file_atomic(cache_array_meta_path, ms1_array_starts_meta_path)
                ms1_array_starts_meta = json.loads(ms1_array_starts_meta_path.read_text(encoding="utf-8"))
                stage_timings["encode_ms1_array_starts_sidecar_s"] = 0.0
                stage_timings["ms1_array_starts_cache_hit_s"] = time.perf_counter() - t0
            else:
                ms1_array_starts = _flatten_ms1_array_starts(tracks)
                t0 = time.perf_counter()
                ms1_array_starts_payload, ms1_array_starts_meta = _encode_uint32_sidecar(
                    ms1_array_starts,
                    backend=ms1_sidecar_backend,
                )
                stage_timings["encode_ms1_array_starts_sidecar_s"] = time.perf_counter() - t0
                _write_bytes_atomic(ms1_array_starts_payload_path, ms1_array_starts_payload)
                _write_text_atomic(ms1_array_starts_meta_path, json.dumps(ms1_array_starts_meta, ensure_ascii=False))
                _copy_file_atomic(ms1_array_starts_payload_path, cache_array_payload_path)
                _copy_file_atomic(ms1_array_starts_meta_path, cache_array_meta_path)

            if _stage_file_complete(cache_orphan_payload_path, cache_orphan_meta_path):
                t0 = time.perf_counter()
                _copy_file_atomic(cache_orphan_payload_path, ms1_orphan_payload_path)
                _copy_file_atomic(cache_orphan_meta_path, ms1_orphan_meta_path)
                ms1_orphan_meta = json.loads(ms1_orphan_meta_path.read_text(encoding="utf-8"))
                stage_timings["encode_ms1_orphan_intensity_sidecar_s"] = 0.0
                stage_timings["ms1_orphan_intensity_cache_hit_s"] = time.perf_counter() - t0
            else:
                t0 = time.perf_counter()
                ms1_orphan_payload, ms1_orphan_meta = _encode_ms1_orphan_intensity_sidecar(
                    ms1_scans,
                    tracks,
                    backend=ms1_sidecar_backend,
                )
                stage_timings["encode_ms1_orphan_intensity_sidecar_s"] = time.perf_counter() - t0
                _write_bytes_atomic(ms1_orphan_payload_path, ms1_orphan_payload)
                _write_text_atomic(ms1_orphan_meta_path, json.dumps(ms1_orphan_meta, ensure_ascii=False))
                _copy_file_atomic(ms1_orphan_payload_path, cache_orphan_payload_path)
                _copy_file_atomic(ms1_orphan_meta_path, cache_orphan_meta_path)
            _append_progress_jsonl(progress_path, {"section": "ms1", "stage": "ms1_sidecars_done", "pct": 92.0})

            if retain_zero_intensity_mz and _stage_file_complete(cache_full_mz_payload_path, cache_full_mz_meta_path):
                t0 = time.perf_counter()
                _copy_file_atomic(cache_full_mz_payload_path, ms1_full_mz_payload_path)
                _copy_file_atomic(cache_full_mz_meta_path, ms1_full_mz_meta_path)
                ms1_full_mz_meta = json.loads(ms1_full_mz_meta_path.read_text(encoding="utf-8"))
                stage_timings["encode_ms1_full_mz_sidecar_s"] = 0.0
                stage_timings["ms1_full_mz_cache_hit_s"] = time.perf_counter() - t0
            elif ms1_full_mz_future is not None:
                ms1_full_mz_payload, ms1_full_mz_meta, full_mz_s = ms1_full_mz_future.result()
                stage_timings["encode_ms1_full_mz_sidecar_s"] = full_mz_s
                _write_bytes_atomic(ms1_full_mz_payload_path, ms1_full_mz_payload)
                _write_text_atomic(ms1_full_mz_meta_path, json.dumps(ms1_full_mz_meta, ensure_ascii=False))
                _copy_file_atomic(ms1_full_mz_payload_path, cache_full_mz_payload_path)
                _copy_file_atomic(ms1_full_mz_meta_path, cache_full_mz_meta_path)
            else:
                ms1_full_mz_meta = {
                    "enabled": False,
                    "backend": ms1_full_mz_backend,
                    "raw_bytes": 0,
                    "compressed_bytes": 0,
                    "compression_ratio": 0.0,
                    "note": "Full-scan MS1 m/z sidecar omitted",
                }
                stage_timings["encode_ms1_full_mz_sidecar_s"] = 0.0
            _append_progress_jsonl(progress_path, {"section": "ms1", "stage": "ms1_full_mz_done", "pct": 100.0})
            del tracks
            del ms1_scans

        _write_section_meta_atomic(section_dir / "ms1_meta", ms1_meta)
        (section_dir / "ms1_array_starts_meta").write_text(json.dumps(ms1_array_starts_meta), encoding="utf-8")
        (section_dir / "ms1_orphan_intensity_meta").write_text(json.dumps(ms1_orphan_meta), encoding="utf-8")
        if retain_zero_intensity_mz:
            (section_dir / "ms1_full_mz_meta").write_text(json.dumps(ms1_full_mz_meta), encoding="utf-8")

        _write_json_file(
            status_path,
            {
                "ms1_meta": ms1_meta,
                "ms1_array_starts_meta": ms1_array_starts_meta,
                "ms1_orphan_meta": ms1_orphan_meta,
                "ms1_full_mz_meta": ms1_full_mz_meta,
                "ms1_sparse_profile_bypass": sparse_bypass_stats,
                "stage_timings_s": stage_timings,
                "elapsed_s": float(time.perf_counter() - total_t0),
            },
        )
    except Exception:
        _write_json_file(error_path, {"traceback": traceback.format_exc()})
        raise


def _archive_ms2_worker_process(
    section_root: str,
    status_path: str,
    error_path: str,
    progress_path: str,
    scan_store_root: str,
    file_type: str,
    ms2_config_dict: Dict,
) -> None:
    try:
        section_dir = Path(section_root)
        section_dir.mkdir(parents=True, exist_ok=True)
        stage_timings: Dict[str, float] = {}
        total_t0 = time.perf_counter()
        _append_progress_jsonl(progress_path, {"section": "ms2", "stage": "worker_boot", "pct": 0.0})
        cache_base = _archive_section_cache_base(scan_store_root)
        cache_section_dir = _archive_ms2_section_cache_dir(
            cache_base,
            file_type=file_type,
            ms2_config_dict=ms2_config_dict,
        )
        cache_section_dir.mkdir(parents=True, exist_ok=True)
        ms2_payload_path = section_dir / "ms2_payload"
        ms2_meta_path = section_dir / "ms2_meta"
        cache_payload_path = cache_section_dir / "ms2_payload"
        cache_meta_path = cache_section_dir / "ms2_meta"
        t_open = time.perf_counter()
        scan_store = open_memmap_scan_store(scan_store_root)
        stage_timings["open_ms2_scan_store_s"] = time.perf_counter() - t_open
        _append_progress_jsonl(progress_path, {"section": "ms2", "stage": "start", "pct": 0.0})

        if _stage_file_complete(cache_payload_path, cache_meta_path):
            t0 = time.perf_counter()
            _copy_file_atomic(cache_payload_path, ms2_payload_path)
            _copy_file_atomic(cache_meta_path, ms2_meta_path)
            ms2_meta = json.loads(ms2_meta_path.read_text(encoding="utf-8"))
            stage_timings["ms2_encode_s"] = 0.0
            stage_timings["serialize_ms2_payload_s"] = 0.0
            stage_timings["ms2_payload_cache_hit_s"] = time.perf_counter() - t0
            _append_progress_jsonl(progress_path, {"section": "ms2", "stage": "ms2_payload_cache_hit", "pct": 100.0})
        else:
            ms2_codec = DIAWindowMS2Codec(MS2ModeConfig(**ms2_config_dict))
            def _ms2_progress(item: Dict) -> None:
                payload = {
                    "section": "ms2",
                    "stage": str(item.get("stage", "ms2_progress")),
                    "pct": float(85.0 * _clamp_pct(item.get("pct")) / 100.0),
                }
                for key in ("done_units", "total_units", "unit_kind", "done_tracks", "total_tracks", "track_kind"):
                    if item.get(key) is not None:
                        payload[key] = item.get(key)
                _append_progress_jsonl(progress_path, payload)
            t0 = time.perf_counter()
            if file_type == "DIA":
                ms2_by_window = scan_store.dia_ms2_window_items()
                ms2_encoded = ms2_codec.encode_dia_windows(ms2_by_window, progress_callback=_ms2_progress)
            else:
                dda_ms2_scans = scan_store.dda_ms2_scans()
                ms2_encoded = ms2_codec.encode_dda_blocks(dda_ms2_scans, progress_callback=_ms2_progress)
            stage_timings["ms2_encode_s"] = time.perf_counter() - t0
            _merge_prefixed_timings(
                stage_timings,
                ms2_encoded.get("stage_timings_s"),
                prefix="ms2_internal_",
            )
            _append_progress_jsonl(progress_path, {"section": "ms2", "stage": "ms2_payload_done", "pct": 85.0})

            t0 = time.perf_counter()
            ms2_payload_size = _serialize_ms2_encoded_to_path(ms2_encoded, file_type, ms2_payload_path)
            stage_timings["serialize_ms2_payload_s"] = time.perf_counter() - t0
            _append_progress_jsonl(progress_path, {"section": "ms2", "stage": "ms2_serialized", "pct": 100.0})

            ms2_meta = {
                "type": file_type,
                "raw_bytes": int(scan_store.raw_ms2_bytes),
                "compressed_bytes": int(ms2_payload_size),
                "compression_ratio": float(scan_store.raw_ms2_bytes) / float(ms2_payload_size) if ms2_payload_size else 0.0,
                "config": ms2_config_dict,
            }

            _write_text_atomic(ms2_meta_path, json.dumps(ms2_meta, ensure_ascii=False))
            _copy_file_atomic(ms2_payload_path, cache_payload_path)
            _copy_file_atomic(ms2_meta_path, cache_meta_path)

        _write_json_file(
            status_path,
            {
                "ms2_meta": ms2_meta,
                "stage_timings_s": stage_timings,
                "elapsed_s": float(time.perf_counter() - total_t0),
            },
        )
    except Exception:
        _write_json_file(error_path, {"traceback": traceback.format_exc()})
        raise


def _deserialize_ms2_encoded(payload: bytes) -> Dict:
    if not payload.startswith(MS2_MAGIC):
        raise ValueError("Invalid TrackCodec MS2 payload magic")

    sections = _unpack_ms2_sections(payload)
    header_key = "h" if "h" in sections else "ms2_header"
    body = json.loads(sections[header_key].decode("utf-8"))
    if int(body.get("v", 1)) >= 2:
        file_type = body.get("t") or body.get("type")
        out = {
            "type": file_type,
            "raw_bytes": int(body.get("rb", body.get("raw_bytes", 0))),
            "compressed_bytes": int(body.get("cb", body.get("compressed_bytes", 0))),
            "compression_ratio": float(body.get("cr", body.get("compression_ratio", 0.0))),
            "container_backend": body.get("bk", body.get("container_backend")),
            "container_blob": None,
            "section_blob": None,
            "exact_track_container_mode": body.get("em", body.get("exact_track_container_mode")),
            "exact_track_parallel_workers": body.get("ep", body.get("exact_track_parallel_workers")),
        }
        mode = body.get("m")
        if mode == "section_blob":
            out["section_blob"] = sections["s"]
            out["windows"] = []
            out["blocks"] = []
        elif mode == "container_blob":
            out["container_blob"] = sections["c"]
            out["windows"] = []
            out["blocks"] = []
        elif file_type == "DIA":
            out["windows"] = [
                {
                    "window_key": tuple(window_key),
                    "payload": sections[f"p{idx}"],
                    "meta": {},
                    "n_scans": 0,
                }
                for idx, window_key in enumerate(body.get("w", []))
            ]
            out["blocks"] = []
        else:
            out["blocks"] = [
                {
                    "block_start": int(block_start),
                    "payload": sections[f"p{idx}"],
                    "meta": {},
                    "n_scans": 0,
                }
                for idx, block_start in enumerate(body.get("b", []))
            ]
            out["windows"] = []
        return out

    out = {
        "raw_bytes": int(body["raw_bytes"]),
        "compressed_bytes": int(body["compressed_bytes"]),
        "compression_ratio": float(body["compression_ratio"]),
        "container_backend": body.get("container_backend"),
        "container_blob": sections[body["container_blob_section"]] if body.get("container_blob_section") else None,
        "section_blob": sections[body["section_blob_section"]] if body.get("section_blob_section") else None,
        "exact_track_container_mode": body.get("exact_track_container_mode"),
        "exact_track_parallel_workers": body.get("exact_track_parallel_workers"),
    }
    if body["type"] == "DIA":
        out["windows"] = [
            {
                "window_key": tuple(item["window_key"]),
                "payload": sections[item["payload_section"]],
                "meta": item["meta"],
                "n_scans": int(item["n_scans"]),
            }
            for item in body.get("windows", [])
        ]
    else:
        out["blocks"] = [
            {
                "block_start": int(item["block_start"]),
                "payload": sections[item["payload_section"]],
                "meta": item["meta"],
                "n_scans": int(item["n_scans"]),
            }
            for item in body.get("blocks", [])
        ]
    out["type"] = body["type"]
    return out


class MzMLSectionArchiveCodec:
    def __init__(
        self,
        *,
        ms1_mode: Dict | None = None,
        ms1_island_workers: int | None = DEFAULT_MS1_ISLAND_WORKERS,
        ms1_encode_section_workers: int | None = None,
        parallel_ms1_ms2_processes: bool | None = None,
        memory_aware_parallel: bool = True,
        parallel_min_available_bytes: int = 24 * (1 << 30),
        parallel_working_set_multiplier: float = 6.0,
        parallel_fixed_overhead_bytes: int = 2 * (1 << 30),
        parallel_headroom_ratio: float = 1.10,
        ms2_config: MS2ModeConfig | None = None,
        ms2_section_workers: int | None = None,
        ms2_segment_workers: int | None = None,
        decode_segment_workers: int | None = None,
        metadata_backend: str = "zstd-9",
        auxiliary_backend: str = "zstd-9",
        ms1_sidecar_backend: str = "zstd-9",
        ms1_full_mz_backend: str = "zstd-9",
        retain_zero_intensity_mz: bool = True,
        ms1_strategy_b_valley_ratio: float = 0.01,
        ms1_sparse_profile_bypass: bool = DEFAULT_MS1_SPARSE_PROFILE_BYPASS,
        ms1_sparse_profile_short_max_points: int = DEFAULT_MS1_SPARSE_PROFILE_SHORT_MAX_POINTS,
        ms1_sparse_profile_min_zero_fraction: float = DEFAULT_MS1_SPARSE_PROFILE_MIN_ZERO_FRACTION,
        ms1_sparse_profile_min_islands_per_scan: float = DEFAULT_MS1_SPARSE_PROFILE_MIN_ISLANDS_PER_SCAN,
        ms1_sparse_profile_min_short_fraction: float = DEFAULT_MS1_SPARSE_PROFILE_MIN_SHORT_FRACTION,
        cache_dir: str | Path | None = None,
    ):
        self.ms1_mode = ms1_mode or {
            "mz_precision": 6,
            "intensity_mode": "szdpd_xdelta_equalfidelity",
            "backend": "brotli",
            "kwargs": {
                "use_pfor": True,
                "mz_dedup": "residual_offset_model",
                "zero_rle": True,
                "metadata_transform": True,
                "adaptive_uint32": True,
                "adaptive_intensity_search": False,
                "adaptive_search_mode": "converged",
                "adaptive_search_sample_count": 16384,
                "delta_ref_window": 4,
                "padded_delta_max_diff": 1,
                "adaptive_padded_delta_accounting": True,
                "enable_byteaware_delta_ref_selection": True,
                "enable_cross_track_full_prediction": True,
                "cross_track_candidate_limit": 4,
                "cross_track_min_gain_bytes": 32,
                "equal_fidelity_overflow_mode": EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT,
                "omit_array_starts": True,
                "omit_n_points": True,
                "track_prepare_workers": 2,
                "track_prepare_lookahead": 4,
            },
        }
        self.ms2_config = ms2_config
        self.ms1_island_workers = None if ms1_island_workers is None else max(1, int(ms1_island_workers))
        self.ms2_section_workers = None if ms2_section_workers is None else max(1, int(ms2_section_workers))
        self.ms2_segment_workers = None if ms2_segment_workers is None else max(1, int(ms2_segment_workers))
        self.decode_segment_workers = None if decode_segment_workers is None else max(1, int(decode_segment_workers))
        if ms1_encode_section_workers is not None:
            self.ms1_encode_section_workers = max(1, int(ms1_encode_section_workers))
        elif self.ms2_section_workers is not None:
            self.ms1_encode_section_workers = int(self.ms2_section_workers)
        else:
            self.ms1_encode_section_workers = 1
        if parallel_ms1_ms2_processes is None:
            self.parallel_ms1_ms2_processes = _can_use_fork_process_parallelism()
        else:
            self.parallel_ms1_ms2_processes = bool(parallel_ms1_ms2_processes)
        self.memory_aware_parallel = bool(memory_aware_parallel)
        self.parallel_min_available_bytes = max(0, int(parallel_min_available_bytes))
        self.parallel_working_set_multiplier = max(0.0, float(parallel_working_set_multiplier))
        self.parallel_fixed_overhead_bytes = max(0, int(parallel_fixed_overhead_bytes))
        self.parallel_headroom_ratio = max(1.0, float(parallel_headroom_ratio))
        self.metadata_codec = MzMLMetadataCodec(metadata_backend)
        self.auxiliary_backend = auxiliary_backend
        self.ms1_sidecar_backend = ms1_sidecar_backend
        self.ms1_full_mz_backend = ms1_full_mz_backend
        self.retain_zero_intensity_mz = bool(retain_zero_intensity_mz)
        self.ms1_strategy_b_valley_ratio = max(0.0, float(ms1_strategy_b_valley_ratio))
        self.ms1_sparse_profile_bypass = bool(ms1_sparse_profile_bypass)
        self.ms1_sparse_profile_short_max_points = max(0, int(ms1_sparse_profile_short_max_points))
        self.ms1_sparse_profile_min_zero_fraction = max(0.0, float(ms1_sparse_profile_min_zero_fraction))
        self.ms1_sparse_profile_min_islands_per_scan = max(0.0, float(ms1_sparse_profile_min_islands_per_scan))
        self.ms1_sparse_profile_min_short_fraction = max(0.0, float(ms1_sparse_profile_min_short_fraction))
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None

    def _ms1_sparse_bypass_config(self) -> Dict:
        return _archive_sparse_profile_bypass_config(
            enabled=bool(self.ms1_sparse_profile_bypass) and bool(self.retain_zero_intensity_mz),
            min_zero_fraction=float(self.ms1_sparse_profile_min_zero_fraction),
            min_islands_per_scan=float(self.ms1_sparse_profile_min_islands_per_scan),
            min_short_fraction=float(self.ms1_sparse_profile_min_short_fraction),
            short_max_points=int(self.ms1_sparse_profile_short_max_points),
        )

    def _build_archive_ms1_tracks(self, ms1_scans) -> tuple[CompactIslandTracks | list, Dict]:
        return build_ms1_tracks_for_archive(
            ms1_scans,
            island_workers=self.ms1_island_workers,
            compact=True,
            strategy_b_valley_ratio=self.ms1_strategy_b_valley_ratio,
            drop_islands_in_compact=True,
            retain_zero_intensity_mz=bool(self.retain_zero_intensity_mz),
            sparse_profile_bypass=bool(self.ms1_sparse_profile_bypass),
            sparse_short_max_points=int(self.ms1_sparse_profile_short_max_points),
            sparse_min_zero_fraction=float(self.ms1_sparse_profile_min_zero_fraction),
            sparse_min_islands_per_scan=float(self.ms1_sparse_profile_min_islands_per_scan),
            sparse_min_short_fraction=float(self.ms1_sparse_profile_min_short_fraction),
        )

    def _decide_parallel_process_mode(
        self,
        *,
        raw_ms1_bytes: int,
        raw_ms2_bytes: int,
        input_file_bytes: int,
    ) -> Dict:
        if not self.parallel_ms1_ms2_processes:
            return {
                "enabled": False,
                "reason": "disabled_by_config",
                "memory_aware_parallel": bool(self.memory_aware_parallel),
            }
        if not _can_use_fork_process_parallelism():
            return {
                "enabled": False,
                "reason": "fork_unavailable",
                "memory_aware_parallel": bool(self.memory_aware_parallel),
            }
        if not self.memory_aware_parallel:
            return {
                "enabled": True,
                "reason": "enabled_without_memory_guard",
                "memory_aware_parallel": False,
            }
        raw_ms1_bytes = max(0, int(raw_ms1_bytes))
        raw_ms2_bytes = max(0, int(raw_ms2_bytes))
        if raw_ms1_bytes <= 0 or raw_ms2_bytes <= 0:
            return {
                "enabled": False,
                "reason": "disabled_missing_ms1_or_ms2_payload",
                "memory_aware_parallel": True,
                "raw_ms1_bytes": int(raw_ms1_bytes),
                "raw_ms2_bytes": int(raw_ms2_bytes),
            }
        smaller = min(raw_ms1_bytes, raw_ms2_bytes)
        larger = max(raw_ms1_bytes, raw_ms2_bytes)
        if smaller < (64 << 20):
            return {
                "enabled": False,
                "reason": "disabled_small_secondary_payload",
                "memory_aware_parallel": True,
                "raw_ms1_bytes": int(raw_ms1_bytes),
                "raw_ms2_bytes": int(raw_ms2_bytes),
                "secondary_payload_min_bytes": int(64 << 20),
            }
        if larger >= 8 * max(1, smaller):
            return {
                "enabled": False,
                "reason": "disabled_unbalanced_ms1_ms2_payload",
                "memory_aware_parallel": True,
                "raw_ms1_bytes": int(raw_ms1_bytes),
                "raw_ms2_bytes": int(raw_ms2_bytes),
                "largest_to_smallest_payload_ratio": float(larger) / float(max(1, smaller)),
            }
        decision = recommend_parallel_section_processes(
            raw_payload_bytes=int(raw_ms1_bytes) + int(raw_ms2_bytes),
            input_file_bytes=int(input_file_bytes),
            min_available_bytes=self.parallel_min_available_bytes,
            working_set_multiplier=self.parallel_working_set_multiplier,
            fixed_overhead_bytes=self.parallel_fixed_overhead_bytes,
            headroom_ratio=self.parallel_headroom_ratio,
        )
        decision["memory_aware_parallel"] = True
        return decision

    def _new_ms2_codec(self, file_type: str) -> DIAWindowMS2Codec:
        cfg = self.ms2_config or _default_ms2_config_for_type(
            file_type,
            self.ms2_section_workers,
            self.ms2_segment_workers,
        )
        return DIAWindowMS2Codec(cfg)

    def _encode_ms1_archive_sections_to_paths(
        self,
        *,
        mzml_path: Path,
        scan_store,
        ms1_scans,
        tracks,
        archive_ms1_mode: Dict,
        section_dir: Path,
        cache_base: Path,
        stage_timings: Dict[str, float],
        progress_callback: Callable[[Dict], None] | None,
    ) -> tuple[Path, Dict, Path, Dict, Path, Dict, Path, Dict]:
        section_dir.mkdir(parents=True, exist_ok=True)
        sparse_bypass_config = self._ms1_sparse_bypass_config()
        cache_section_dir = _archive_ms1_section_cache_dir(
            cache_base,
            ms1_mode=archive_ms1_mode,
            ms1_sidecar_backend=self.ms1_sidecar_backend,
            ms1_full_mz_backend=self.ms1_full_mz_backend,
            retain_zero_intensity_mz=bool(self.retain_zero_intensity_mz),
            strategy_b_valley_ratio=float(self.ms1_strategy_b_valley_ratio),
            sparse_bypass_config=sparse_bypass_config,
        )
        cache_section_dir.mkdir(parents=True, exist_ok=True)
        cache_payload_dir = _archive_ms1_payload_cache_dir(
            cache_base,
            ms1_mode=archive_ms1_mode,
            strategy_b_valley_ratio=float(self.ms1_strategy_b_valley_ratio),
            sparse_bypass_config=sparse_bypass_config,
        )
        cache_sidecar_dir = _archive_ms1_sidecar_cache_dir(
            cache_base,
            mz_precision=int(archive_ms1_mode["mz_precision"]),
            ms1_sidecar_backend=self.ms1_sidecar_backend,
            ms1_full_mz_backend=self.ms1_full_mz_backend,
            retain_zero_intensity_mz=bool(self.retain_zero_intensity_mz),
            strategy_b_valley_ratio=float(self.ms1_strategy_b_valley_ratio),
            sparse_bypass_config=sparse_bypass_config,
        )
        cache_payload_dir.mkdir(parents=True, exist_ok=True)
        cache_sidecar_dir.mkdir(parents=True, exist_ok=True)

        ms1_payload_path = section_dir / "ms1_payload"
        ms1_meta_path = section_dir / "ms1_meta"
        array_payload_path = section_dir / "ms1_array_starts_payload"
        array_meta_path = section_dir / "ms1_array_starts_meta"
        orphan_payload_path = section_dir / "ms1_orphan_intensity_payload"
        orphan_meta_path = section_dir / "ms1_orphan_intensity_meta"
        full_mz_payload_path = section_dir / "ms1_full_mz_payload"
        full_mz_meta_path = section_dir / "ms1_full_mz_meta"

        cache_ms1_payload_path = cache_payload_dir / "ms1_payload"
        cache_ms1_meta_path = cache_payload_dir / "ms1_meta"
        cache_array_payload_path = cache_sidecar_dir / "ms1_array_starts_payload"
        cache_array_meta_path = cache_sidecar_dir / "ms1_array_starts_meta"
        cache_orphan_payload_path = cache_sidecar_dir / "ms1_orphan_intensity_payload"
        cache_orphan_meta_path = cache_sidecar_dir / "ms1_orphan_intensity_meta"
        cache_full_mz_payload_path = cache_sidecar_dir / "ms1_full_mz_payload"
        cache_full_mz_meta_path = cache_sidecar_dir / "ms1_full_mz_meta"

        if _stage_file_complete(cache_ms1_payload_path, cache_ms1_meta_path):
            t0 = time.perf_counter()
            _copy_file_atomic(cache_ms1_payload_path, ms1_payload_path)
            _copy_file_atomic(cache_ms1_meta_path, ms1_meta_path)
            ms1_meta = json.loads(ms1_meta_path.read_text(encoding="utf-8"))
            stage_timings["ms1_encode_s"] = 0.0
            stage_timings["ms1_payload_cache_hit_s"] = time.perf_counter() - t0
        else:
            ms1_codec = MS1Codec(
                mz_precision=archive_ms1_mode["mz_precision"],
                intensity_mode=archive_ms1_mode["intensity_mode"],
                backend=archive_ms1_mode["backend"],
                encode_section_workers=self.ms1_encode_section_workers,
                **archive_ms1_mode["kwargs"],
            )

            def _forward_ms1_progress(item: Dict) -> None:
                if progress_callback is None:
                    return
                updates = {"ms1_pct": float(30.0 + 48.0 * _clamp_pct(item.get("pct")) / 100.0)}
                for key in ("done_tracks", "total_tracks", "done_islands", "total_islands", "done_sections", "total_sections", "track_pct"):
                    if item.get(key) is not None:
                        updates[key] = item.get(key)
                progress_callback(str(item.get("stage", "ms1_progress")), updates)

            t0 = time.perf_counter()
            _, ms1_meta = ms1_codec.encode_to_path(
                tracks,
                ms1_scans,
                output_path=ms1_payload_path,
                progress_callback=_forward_ms1_progress,
            )
            stage_timings["ms1_encode_s"] = time.perf_counter() - t0
            ms1_runtime_stage_timings = _pop_runtime_stage_timings(ms1_meta)
            _merge_prefixed_timings(
                stage_timings,
                ms1_runtime_stage_timings,
                prefix="ms1_internal_",
            )
            _write_section_meta_atomic(ms1_meta_path, ms1_meta)
            _copy_file_atomic(ms1_payload_path, cache_ms1_payload_path)
            _copy_file_atomic(ms1_meta_path, cache_ms1_meta_path)

        if _stage_file_complete(cache_array_payload_path, cache_array_meta_path):
            t0 = time.perf_counter()
            _copy_file_atomic(cache_array_payload_path, array_payload_path)
            _copy_file_atomic(cache_array_meta_path, array_meta_path)
            array_meta = json.loads(array_meta_path.read_text(encoding="utf-8"))
            stage_timings["encode_ms1_array_starts_sidecar_s"] = 0.0
            stage_timings["ms1_array_starts_cache_hit_s"] = time.perf_counter() - t0
        else:
            t0 = time.perf_counter()
            array_payload, array_meta = _encode_uint32_sidecar(
                _flatten_ms1_array_starts(tracks),
                backend=self.ms1_sidecar_backend,
            )
            stage_timings["encode_ms1_array_starts_sidecar_s"] = time.perf_counter() - t0
            _write_bytes_atomic(array_payload_path, array_payload)
            _write_text_atomic(array_meta_path, json.dumps(array_meta, ensure_ascii=False))
            _copy_file_atomic(array_payload_path, cache_array_payload_path)
            _copy_file_atomic(array_meta_path, cache_array_meta_path)

        if _stage_file_complete(cache_orphan_payload_path, cache_orphan_meta_path):
            t0 = time.perf_counter()
            _copy_file_atomic(cache_orphan_payload_path, orphan_payload_path)
            _copy_file_atomic(cache_orphan_meta_path, orphan_meta_path)
            orphan_meta = json.loads(orphan_meta_path.read_text(encoding="utf-8"))
            stage_timings["encode_ms1_orphan_intensity_sidecar_s"] = 0.0
            stage_timings["ms1_orphan_intensity_cache_hit_s"] = time.perf_counter() - t0
        else:
            t0 = time.perf_counter()
            orphan_payload, orphan_meta = _encode_ms1_orphan_intensity_sidecar(
                ms1_scans,
                tracks,
                backend=self.ms1_sidecar_backend,
            )
            stage_timings["encode_ms1_orphan_intensity_sidecar_s"] = time.perf_counter() - t0
            _write_bytes_atomic(orphan_payload_path, orphan_payload)
            _write_text_atomic(orphan_meta_path, json.dumps(orphan_meta, ensure_ascii=False))
            _copy_file_atomic(orphan_payload_path, cache_orphan_payload_path)
            _copy_file_atomic(orphan_meta_path, cache_orphan_meta_path)

        if self.retain_zero_intensity_mz and _stage_file_complete(cache_full_mz_payload_path, cache_full_mz_meta_path):
            t0 = time.perf_counter()
            _copy_file_atomic(cache_full_mz_payload_path, full_mz_payload_path)
            _copy_file_atomic(cache_full_mz_meta_path, full_mz_meta_path)
            full_mz_meta = json.loads(full_mz_meta_path.read_text(encoding="utf-8"))
            stage_timings["encode_ms1_full_mz_sidecar_s"] = 0.0
            stage_timings["ms1_full_mz_cache_hit_s"] = time.perf_counter() - t0
        elif self.retain_zero_intensity_mz:
            t0 = time.perf_counter()
            full_mz_payload, full_mz_meta, full_mz_s = _encode_ms1_full_mz_sidecar_timed_cached(
                ms1_scans,
                mz_precision=archive_ms1_mode["mz_precision"],
                backend=self.ms1_full_mz_backend,
                cache_dir=cache_base / "ms1_full_mz_sidecar_cache",
                compression_workers=max(1, int(self.ms1_encode_section_workers)),
            )
            stage_timings["encode_ms1_full_mz_sidecar_s"] = full_mz_s
            _write_bytes_atomic(full_mz_payload_path, full_mz_payload)
            _write_text_atomic(full_mz_meta_path, json.dumps(full_mz_meta, ensure_ascii=False))
            _copy_file_atomic(full_mz_payload_path, cache_full_mz_payload_path)
            _copy_file_atomic(full_mz_meta_path, cache_full_mz_meta_path)
        else:
            full_mz_meta = {
                "enabled": False,
                "backend": self.ms1_full_mz_backend,
                "raw_bytes": 0,
                "compressed_bytes": 0,
                "compression_ratio": 0.0,
                "note": "Full-scan MS1 m/z sidecar omitted",
            }
            stage_timings["encode_ms1_full_mz_sidecar_s"] = 0.0

        return (
            ms1_payload_path,
            ms1_meta,
            array_payload_path,
            array_meta,
            orphan_payload_path,
            orphan_meta,
            full_mz_payload_path,
            full_mz_meta,
        )

    def _encode_ms2_archive_section_to_path(
        self,
        *,
        scan_store,
        file_type: str,
        ms2_config_dict: Dict,
        section_dir: Path,
        cache_base: Path,
        stage_timings: Dict[str, float],
        progress_callback: Callable[[Dict], None] | None,
    ) -> tuple[Path, Dict]:
        section_dir.mkdir(parents=True, exist_ok=True)
        cache_section_dir = _archive_ms2_section_cache_dir(
            cache_base,
            file_type=file_type,
            ms2_config_dict=ms2_config_dict,
        )
        cache_section_dir.mkdir(parents=True, exist_ok=True)
        payload_path = section_dir / "ms2_payload"
        meta_path = section_dir / "ms2_meta"
        cache_payload_path = cache_section_dir / "ms2_payload"
        cache_meta_path = cache_section_dir / "ms2_meta"
        if _stage_file_complete(cache_payload_path, cache_meta_path):
            t0 = time.perf_counter()
            _copy_file_atomic(cache_payload_path, payload_path)
            _copy_file_atomic(cache_meta_path, meta_path)
            ms2_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            stage_timings["ms2_encode_s"] = 0.0
            stage_timings["serialize_ms2_payload_s"] = 0.0
            stage_timings["ms2_payload_cache_hit_s"] = time.perf_counter() - t0
            return payload_path, ms2_meta

        ms2_codec = DIAWindowMS2Codec(MS2ModeConfig(**ms2_config_dict))

        def _forward_ms2_progress(item: Dict) -> None:
            if progress_callback is None:
                return
            updates = {"ms2_pct": float(85.0 * _clamp_pct(item.get("pct")) / 100.0)}
            for key in ("done_units", "total_units", "unit_kind", "done_tracks", "total_tracks", "track_kind"):
                if item.get(key) is not None:
                    updates[key] = item.get(key)
            progress_callback(str(item.get("stage", "ms2_progress")), updates)

        t0 = time.perf_counter()
        if file_type == "DIA":
            ms2_encoded = ms2_codec.encode_dia_windows(
                scan_store.dia_ms2_window_items(),
                progress_callback=_forward_ms2_progress,
            )
        else:
            ms2_encoded = ms2_codec.encode_dda_blocks(
                scan_store.dda_ms2_scans(),
                progress_callback=_forward_ms2_progress,
            )
            stage_timings["ms2_encode_s"] = time.perf_counter() - t0
            _merge_prefixed_timings(
                stage_timings,
                ms2_encoded.get("stage_timings_s"),
                prefix="ms2_internal_",
            )

            t0 = time.perf_counter()
        ms2_payload_size = _serialize_ms2_encoded_to_path(ms2_encoded, file_type, payload_path)
        stage_timings["serialize_ms2_payload_s"] = time.perf_counter() - t0
        del ms2_encoded
        release_memory()

        ms2_meta = {
            "type": file_type,
            "raw_bytes": int(scan_store.raw_ms2_bytes),
            "compressed_bytes": int(ms2_payload_size),
            "compression_ratio": float(scan_store.raw_ms2_bytes) / float(ms2_payload_size) if ms2_payload_size else 0.0,
            "config": ms2_config_dict,
        }
        _write_text_atomic(meta_path, json.dumps(ms2_meta, ensure_ascii=False))
        _copy_file_atomic(payload_path, cache_payload_path)
        _copy_file_atomic(meta_path, cache_meta_path)
        return payload_path, ms2_meta

    def _encode_file_to_path_serial_from_scan_store(
        self,
        *,
        mzml_path: Path,
        output_path: Path,
        scan_store,
        track_store_path: Path | None,
        file_type: str,
        raw_ms1_bytes: int,
        raw_ms2_bytes: int,
        total_t0: float,
        stage_timings: Dict[str, float],
        parallel_decision: Dict,
        report_progress: Callable[[str, Dict], None],
        metadata_cache_root: Path | None = None,
        metadata_xml_bytes: bytes | None = None,
        auxiliary_records: List[Dict] | None = None,
    ):
        ms1_scans = scan_store.ms1_scans()
        archive_ms1_mode = _make_archive_ms1_mode(
            self.ms1_mode,
            external_full_mz_sidecar=bool(self.retain_zero_intensity_mz),
        )
        sparse_bypass_config = self._ms1_sparse_bypass_config()
        tracks = None
        sparse_bypass_stats: Dict = {"enabled": False, "triggered": False, "reason": "not_run"}
        effective_track_store_path = _archive_track_store_path(track_store_path, sparse_bypass_config)
        if effective_track_store_path is not None and (effective_track_store_path / "manifest.json").exists():
            t0 = time.perf_counter()
            tracks = open_compact_track_store(effective_track_store_path)
            stage_timings["open_ms1_tracks_store_s"] = time.perf_counter() - t0
            sparse_bypass_stats = {"cache_hit": True, **sparse_bypass_config}
        else:
            t0 = time.perf_counter()
            tracks, sparse_bypass_stats = self._build_archive_ms1_tracks(
                ms1_scans,
            )
            stage_timings["build_ms1_tracks_s"] = time.perf_counter() - t0
            if (
                effective_track_store_path is not None
                and isinstance(tracks, CompactIslandTracks)
                and len(tracks.islands) == 0
            ):
                t0 = time.perf_counter()
                tracks = write_compact_track_store(effective_track_store_path, tracks)
                stage_timings["spill_ms1_tracks_store_s"] = time.perf_counter() - t0
        stage_timings["ms1_sparse_profile_bypass_triggered"] = 1.0 if sparse_bypass_stats.get("triggered") else 0.0
        stage_timings["ms1_sparse_profile_bypass_original_islands"] = float(sparse_bypass_stats.get("original_island_count", 0) or 0)
        stage_timings["ms1_sparse_profile_bypass_trackable_islands"] = float(sparse_bypass_stats.get("trackable_island_count", 0) or 0)
        report_progress("build_ms1_tracks_done", {"ms1_pct": 30.0})

        ms2_codec = self._new_ms2_codec(file_type)
        ms2_config_dict = asdict(ms2_codec.config)
        del ms2_codec

        cache_base = _archive_section_cache_base(scan_store.root, track_store_path)
        with tempfile.TemporaryDirectory(prefix="trackcodec_archive_serial_sections_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            section_dir = tmp_dir / "sections"
            meta_dir = tmp_dir / "meta"
            meta_dir.mkdir(parents=True, exist_ok=True)

            (
                ms1_payload_path,
                ms1_meta,
                array_payload_path,
                ms1_array_starts_meta,
                orphan_payload_path,
                ms1_orphan_meta,
                full_mz_payload_path,
                ms1_full_mz_meta,
            ) = self._encode_ms1_archive_sections_to_paths(
                mzml_path=mzml_path,
                scan_store=scan_store,
                ms1_scans=ms1_scans,
                tracks=tracks,
                archive_ms1_mode=archive_ms1_mode,
                section_dir=section_dir,
                cache_base=cache_base,
                stage_timings=stage_timings,
                progress_callback=report_progress,
            )
            report_progress("ms1_payload_done", {"ms1_pct": 78.0})
            report_progress("ms1_sidecars_done", {"ms1_pct": 92.0})
            report_progress("ms1_full_mz_done", {"ms1_pct": 100.0})
            if tracks is not None:
                del tracks
            del ms1_scans
            release_memory()

            report_progress("metadata_start", {"metadata_pct": 0.0})
            (
                metadata_payload_path,
                metadata_meta,
                auxiliary_payload_path,
                auxiliary_meta,
                metadata_aux_timing,
            ) = _extract_and_encode_metadata_auxiliary_payloads_cached(
                mzml_path,
                self.metadata_codec,
                self.auxiliary_backend,
                metadata_cache_root,
                metadata_xml_bytes=metadata_xml_bytes,
                auxiliary_records=auxiliary_records,
            )
            stage_timings.update(metadata_aux_timing)
            report_progress("metadata_done", {"metadata_pct": 100.0})

            ms2_payload_path, ms2_meta = self._encode_ms2_archive_section_to_path(
                scan_store=scan_store,
                file_type=file_type,
                ms2_config_dict=ms2_config_dict,
                section_dir=section_dir,
                cache_base=cache_base,
                stage_timings=stage_timings,
                progress_callback=report_progress,
            )
            report_progress("ms2_serialized", {"ms2_pct": 100.0})

            archive_meta = {
                "source_file": str(mzml_path),
                "source_file_name": mzml_path.name,
                "file_type": file_type,
                "input_file_bytes": int(mzml_path.stat().st_size),
                "ms1_mode": self.ms1_mode,
                "ms1_payload_mode": archive_ms1_mode,
                "ms2_config": ms2_config_dict,
                "reconstructable": True,
                "ms1_full_mz_sidecar": bool(self.retain_zero_intensity_mz),
                "ms1_mz_sidecar_scope": "full_scan_mz",
                "ms1_orphan_intensity_sidecar": True,
                "ms1_sparse_profile_bypass": _strip_archive_runtime_metadata(sparse_bypass_stats),
                "ms1_island_workers": self.ms1_island_workers,
                "ms1_encode_section_workers": self.ms1_encode_section_workers,
                "retain_zero_intensity_mz": bool(self.retain_zero_intensity_mz),
            }

            archive_meta_path = meta_dir / "archive_meta"
            metadata_meta_path = meta_dir / "metadata_meta"
            auxiliary_meta_path = meta_dir / "auxiliary_meta"
            _write_bytes_atomic(archive_meta_path, _json_bytes(_strip_archive_runtime_metadata(archive_meta)))
            _write_bytes_atomic(metadata_meta_path, _json_bytes(_strip_archive_runtime_metadata(metadata_meta)))
            _write_bytes_atomic(auxiliary_meta_path, _json_bytes(_strip_archive_runtime_metadata(auxiliary_meta)))

            section_paths: List[Tuple[str, Path]] = [
                ("archive_meta", archive_meta_path),
                ("ms1_payload", ms1_payload_path),
                ("ms1_meta", section_dir / "ms1_meta"),
                ("ms1_array_starts_payload", array_payload_path),
                ("ms1_array_starts_meta", section_dir / "ms1_array_starts_meta"),
                ("ms1_orphan_intensity_payload", orphan_payload_path),
                ("ms1_orphan_intensity_meta", section_dir / "ms1_orphan_intensity_meta"),
                ("ms2_payload", ms2_payload_path),
                ("ms2_meta", section_dir / "ms2_meta"),
                ("metadata_payload", Path(metadata_payload_path)),
                ("metadata_meta", metadata_meta_path),
                ("auxiliary_payload", Path(auxiliary_payload_path)),
                ("auxiliary_meta", auxiliary_meta_path),
            ]
            if self.retain_zero_intensity_mz:
                section_paths.extend(
                    [
                        ("ms1_full_mz_payload", full_mz_payload_path),
                        ("ms1_full_mz_meta", section_dir / "ms1_full_mz_meta"),
                    ]
                )

            t0 = time.perf_counter()
            archive_size = _pack_named_section_files(section_paths, output_path)
            stage_timings["pack_archive_s"] = time.perf_counter() - t0
            report_progress("pack_archive_done", {"ms1_pct": 100.0, "ms2_pct": 100.0, "metadata_pct": 100.0})

        stage_timings["total_encode_file_s"] = time.perf_counter() - total_t0
        meta = {
            "source_file": str(mzml_path),
            "file_type": file_type,
            "ms1": ms1_meta,
            "ms2": ms2_meta,
            "metadata": metadata_meta,
            "auxiliary": auxiliary_meta,
            "ms1_full_mz": ms1_full_mz_meta,
            "ms1_orphan_intensity": ms1_orphan_meta,
            "ms1_sparse_profile_bypass": sparse_bypass_stats,
            "raw_bytes": raw_ms1_bytes + raw_ms2_bytes + metadata_meta["raw_bytes"] + auxiliary_meta["raw_bytes"],
            "input_file_bytes": int(mzml_path.stat().st_size),
            "compressed_bytes": archive_size,
            "compression_ratio": (raw_ms1_bytes + raw_ms2_bytes + metadata_meta["raw_bytes"] + auxiliary_meta["raw_bytes"]) / archive_size if archive_size else 0.0,
            "compression_ratio_vs_input_file": int(mzml_path.stat().st_size) / archive_size if archive_size else 0.0,
            "stage_timings_s": stage_timings,
            "stage_duration_timings_s": _duration_stage_timings(stage_timings),
            "parallel_decision": parallel_decision,
        }
        return output_path, meta

    def _iter_decode_ms1_scans(
        self,
        ms1_payload: bytes,
        ms1_meta: Dict,
        templates: Sequence[Dict],
        array_starts: np.ndarray | None,
        full_mz_arrays: Sequence[np.ndarray] | None,
        complement_mz_entries: Dict[str, Sequence] | None,
        orphan_intensity_entries: Dict[str, np.ndarray] | None,
    ):
        header = ms1_meta.get("header", {})
        intensity_mode = header.get("intensity_mode") or header.get("int_mode") or self.ms1_mode["intensity_mode"]
        mz_precision = int(header.get("mz_precision", self.ms1_mode["mz_precision"]))
        backend = header.get("backend", self.ms1_mode["backend"])
        ms1_codec = MS1Codec(mz_precision=mz_precision, intensity_mode=intensity_mode, backend=backend)

        if (
            full_mz_arrays is not None
            and array_starts is not None
            and HAVE_CROSS_SCAN_SPEEDUPS
            and _cross_scan_speedups is not None
            and bool(header.get("omit_island_mz", False))
        ):
            native_scans = self._iter_decode_ms1_scans_native_intensity_grid(
                ms1_payload,
                ms1_meta,
                ms1_codec,
                templates,
                array_starts,
                full_mz_arrays,
                orphan_intensity_entries,
            )
            if native_scans is not None:
                yield from native_scans
                return

        by_scan: Dict[int, List[tuple[int, int, np.ndarray, np.ndarray]]] = {}
        island_count = 0
        for island in ms1_codec.iter_decoded_islands(ms1_payload, ms1_meta):
            start = int(island.get("array_start_idx", -1))
            if array_starts is not None:
                if island_count >= len(array_starts):
                    raise ValueError(f"MS1 array-start sidecar shorter than decoded islands: {len(array_starts)}")
                start = int(array_starts[island_count])
            count = int(island["n_points"])
            island_mz = np.asarray(island.get("mz_array", []), dtype=np.float64)
            island_intensity = np.asarray(island["intensity_array"], dtype=np.float64)
            island_count += 1
            by_scan.setdefault(int(island["scan_idx"]), []).append((start, count, island_mz, island_intensity))
        if array_starts is not None and island_count != len(array_starts):
            raise ValueError(f"MS1 array-start sidecar mismatch: {len(array_starts)} vs {island_count}")

        complement_by_scan: Dict[int, List[tuple[int, int, np.ndarray]]] = {}
        if full_mz_arrays is None:
            if complement_mz_entries is None:
                raise ValueError("Missing MS1 complement m/z sidecar")
            scan_indices = np.asarray(complement_mz_entries.get("scan_indices", []), dtype=np.uint32)
            run_starts = np.asarray(complement_mz_entries.get("run_starts", []), dtype=np.uint32)
            run_lengths = np.asarray(complement_mz_entries.get("run_lengths", []), dtype=np.uint32)
            mz_runs = complement_mz_entries.get("mz_runs", [])
            if not (len(scan_indices) == len(run_starts) == len(run_lengths) == len(mz_runs)):
                raise ValueError("MS1 complement m/z sidecar length mismatch")
            for scan_idx, start, length, mz_run in zip(scan_indices, run_starts, run_lengths, mz_runs):
                complement_by_scan.setdefault(int(scan_idx), []).append(
                    (int(start), int(length), np.asarray(mz_run, dtype=np.float64))
                )

        orphan_lookup = _MS1OrphanIntensityLookup(orphan_intensity_entries)

        for template in templates:
            scan_idx = int(template["scan_idx"])
            n = int(template["array_length"])
            if full_mz_arrays is not None:
                if scan_idx >= len(full_mz_arrays):
                    raise ValueError(f"Missing full MS1 m/z sidecar for scan {scan_idx}")
                mz_array = np.asarray(full_mz_arrays[scan_idx], dtype=np.float64).copy()
                if len(mz_array) != n:
                    raise ValueError(f"MS1 full m/z sidecar length mismatch for scan {scan_idx}: {len(mz_array)} vs {n}")
            else:
                mz_array = np.zeros(n, dtype=np.float64)
                for start, count, mz_run in complement_by_scan.get(scan_idx, []):
                    end = start + count
                    if start < 0 or end > n:
                        raise ValueError(f"MS1 complement m/z run overruns scan length: scan {scan_idx}, {start}:{end} > {n}")
                    if len(mz_run) != count:
                        raise ValueError(f"MS1 complement m/z run length mismatch for scan {scan_idx}: {len(mz_run)} vs {count}")
                    mz_array[start:end] = mz_run
            intensity_array = np.zeros(n, dtype=np.float64)
            for start, count, island_mz, island_intensity in by_scan.pop(scan_idx, []):
                if start < 0:
                    raise ValueError("Missing array_start_idx for MS1 reconstruction")
                end = start + count
                if end > n:
                    raise ValueError(f"MS1 island overruns scan length: {end} > {n}")
                if full_mz_arrays is None:
                    if len(island_mz) != count:
                        raise ValueError(f"Missing MS1 island m/z values without full m/z sidecar: {len(island_mz)} vs {count}")
                    mz_array[start:end] = island_mz
                elif len(island_mz) not in (0, count):
                    raise ValueError(f"MS1 island m/z length mismatch: {len(island_mz)} vs {count}")
                intensity_array[start:end] = island_intensity
            orphan_lookup.apply(scan_idx, intensity_array)
            yield mz_array, intensity_array

    def _iter_decode_ms1_scans_native_intensity_grid(
        self,
        ms1_payload: bytes,
        ms1_meta: Dict,
        ms1_codec: MS1Codec,
        templates: Sequence[Dict],
        array_starts: np.ndarray,
        full_mz_arrays: Sequence[np.ndarray],
        orphan_intensity_entries: Dict[str, np.ndarray] | None,
    ):
        if not hasattr(_cross_scan_speedups, "ms1_expand_equal_fidelity_island_intensity_f64"):
            return None
        header, segments = _unpack_ms1_segments(ms1_payload)
        segment_meta = header["segment_meta"]
        decoded_meta = ms1_codec._decode_metadata_segments(segments, segment_meta)
        n_points = decoded_meta.get("n_points")
        if n_points is None:
            return None
        scan_indices = np.asarray(decoded_meta["scan_indices"], dtype=np.uint32)
        if len(array_starts) != len(scan_indices):
            raise ValueError(f"MS1 array-start sidecar mismatch: {len(array_starts)} vs {len(scan_indices)}")
        scan_lengths = np.asarray([int(template["array_length"]) for template in templates], dtype=np.uint32)
        if len(full_mz_arrays) != len(scan_lengths):
            raise ValueError(f"MS1 full m/z sidecar scan count mismatch: {len(full_mz_arrays)} vs {len(scan_lengths)}")
        full_concat, delta_concat = ms1_codec._decode_intensity_segments(segments, segment_meta)
        delta_length_mode = ms1_codec._resolve_decode_delta_length_mode(header, decoded_meta, segment_meta)
        use_max_ref_curr = bool(delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR)
        enable_second_order_delta = bool(header.get("enable_second_order_delta", ms1_codec.enable_second_order_delta))
        delta_ref_window = int(header.get("delta_ref_window", ms1_codec.delta_ref_window))
        track_lengths = np.asarray(decoded_meta["track_lengths"], dtype=np.uint32)
        n_points = np.asarray(n_points, dtype=np.uint32)
        int_kinds = np.asarray(decoded_meta["int_kinds"], dtype=np.uint8)
        delta_ref_offsets = np.asarray(decoded_meta.get("delta_ref_offsets", np.array([], dtype=np.uint8)), dtype=np.uint8)
        cross_track_ref_indices = np.asarray(
            decoded_meta.get("cross_track_ref_indices", np.array([], dtype=np.uint32)),
            dtype=np.uint32,
        )

        intensity_mode = str(header.get("intensity_mode") or header.get("int_mode") or ms1_codec.intensity_mode)
        if intensity_mode == "szdpd_xdelta_equalfidelity":
            precision = int(segment_meta["full_intensity"].get("precision", 1))
            overflow_mode = header.get(
                "equal_fidelity_overflow_mode",
                segment_meta.get("full_intensity", {}).get(
                    "overflow_policy",
                    ms1_codec.equal_fidelity_overflow_mode,
                ),
            )
            native = _cross_scan_speedups.ms1_expand_equal_fidelity_island_intensity_f64(
                track_lengths,
                n_points,
                int_kinds,
                delta_ref_offsets,
                cross_track_ref_indices,
                np.asarray(full_concat, dtype=np.int64),
                np.asarray(delta_concat, dtype=np.int64),
                precision,
                bool(overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT),
                use_max_ref_curr,
                enable_second_order_delta,
                delta_ref_window,
            )
        elif intensity_mode in {"strict_lossless_xdelta", "szdpd_xdelta"}:
            if not hasattr(_cross_scan_speedups, "ms1_expand_float64_island_intensity_f64"):
                return None
            native = _cross_scan_speedups.ms1_expand_float64_island_intensity_f64(
                track_lengths,
                n_points,
                int_kinds,
                delta_ref_offsets,
                cross_track_ref_indices,
                np.asarray(full_concat, dtype=np.float64),
                np.asarray(delta_concat, dtype=np.float64),
                use_max_ref_curr,
                enable_second_order_delta,
                delta_ref_window,
            )
        else:
            return None

        island_offsets = np.asarray(native[0], dtype=np.uint64)
        flat_island_intensity = np.asarray(native[1], dtype=np.float64)
        orphan_lookup = _MS1OrphanIntensityLookup(orphan_intensity_entries)
        if len(scan_indices):
            island_order = np.argsort(scan_indices, kind="stable")
            sorted_scan_indices = scan_indices[island_order]
        else:
            island_order = np.array([], dtype=np.uint32)
            sorted_scan_indices = np.array([], dtype=np.uint32)

        def _iter():
            island_pos = 0
            island_total = int(len(island_order))
            for template in templates:
                scan_idx = int(template["scan_idx"])
                n = int(scan_lengths[scan_idx])
                if scan_idx >= len(full_mz_arrays):
                    raise ValueError(f"Missing full MS1 m/z sidecar for scan {scan_idx}")
                mz_array = np.asarray(full_mz_arrays[scan_idx], dtype=np.float64)
                if len(mz_array) != n:
                    raise ValueError(f"MS1 full m/z sidecar length mismatch for scan {scan_idx}: {len(mz_array)} vs {n}")
                intensity_array = np.zeros(n, dtype=np.float64)
                while island_pos < island_total and int(sorted_scan_indices[island_pos]) < scan_idx:
                    raise ValueError(
                        "MS1 native decoded island scan index is not present in templates: "
                        f"{int(sorted_scan_indices[island_pos])}"
                    )
                while island_pos < island_total and int(sorted_scan_indices[island_pos]) == scan_idx:
                    island_idx = int(island_order[island_pos])
                    start = int(array_starts[island_idx])
                    count = int(n_points[island_idx])
                    seg_start = int(island_offsets[island_idx])
                    seg_end = int(island_offsets[island_idx + 1])
                    if seg_end - seg_start != count:
                        raise ValueError(
                            "MS1 native island intensity length mismatch: "
                            f"island {island_idx}, {seg_end - seg_start} vs {count}"
                        )
                    end = start + count
                    if start < 0 or end > n:
                        raise ValueError(f"MS1 native island overruns scan length: {end} > {n}")
                    intensity_array[start:end] = flat_island_intensity[seg_start:seg_end]
                    island_pos += 1
                orphan_lookup.apply(scan_idx, intensity_array)
                yield mz_array, intensity_array
            if island_pos != island_total:
                raise ValueError(f"MS1 native decoded islands left after template iteration: {island_total - island_pos}")

        return _iter()

    def _decode_ms2_scans(self, ms2_payload_json: Dict, archive_meta: Dict):
        file_type = archive_meta["file_type"]
        codec = DIAWindowMS2Codec(MS2ModeConfig(**archive_meta["ms2_config"]))
        if file_type == "DIA":
            return codec.decode_dia_windows(ms2_payload_json)
        return codec.decode_dda_blocks(ms2_payload_json)

    def _decode_ms2_scans_for_reconstruction(self, ms2_payload_json: Dict, archive_meta: Dict):
        """Decode MS2 in the form preferred by mzML reconstruction.

        The DDA path returns an iterator so reconstruction no longer needs to
        materialize every decoded MS2 scan before writing.  DIA keeps the window
        lookup contract but decodes each window on first use and releases it
        after the last scan in that window is consumed.
        """
        file_type = archive_meta["file_type"]
        codec = DIAWindowMS2Codec(MS2ModeConfig(**archive_meta["ms2_config"]))
        if file_type == "DIA":
            return codec.lazy_decode_dia_windows(ms2_payload_json)
        return codec.iter_decode_dda_blocks(ms2_payload_json)

    def encode_file(self, mzml_path: str | Path):
        with tempfile.TemporaryDirectory(prefix="trackcodec_archive_mem_") as tmp_dir:
            tmp_archive = Path(tmp_dir) / f"{Path(mzml_path).name}{TRACKCODEC_ARCHIVE_EXTENSION}"
            _, meta = self.encode_file_to_path(mzml_path, tmp_archive)
            return tmp_archive.read_bytes(), meta

    def _encode_file_to_path_parallel(
        self,
        mzml_path: Path,
        output_path: Path,
        *,
        total_t0: float,
        stage_timings: Dict[str, float],
        file_type: str,
        scan_store_root: str,
        ms1_track_store_root: str | None,
        raw_ms1_bytes: int,
        raw_ms2_bytes: int,
        parallel_decision: Dict,
        metadata_cache_root: Path | None = None,
        metadata_xml_bytes: bytes | None = None,
        auxiliary_records: List[Dict] | None = None,
        progress_callback: Callable[[Dict], None] | None = None,
    ):
        ctx = mp.get_context("fork")
        ms2_config_dict = asdict(self._new_ms2_codec(file_type).config)
        report_progress = _make_progress_reporter(progress_callback, scope="archive")

        with tempfile.TemporaryDirectory(prefix="trackcodec_archive_parallel_") as tmp_dir_str:
            tmp_dir = Path(tmp_dir_str)
            section_dir = tmp_dir / "sections"
            section_dir.mkdir(parents=True, exist_ok=True)
            ms1_status_path = tmp_dir / "ms1_status.json"
            ms2_status_path = tmp_dir / "ms2_status.json"
            ms1_error_path = tmp_dir / "ms1_error.json"
            ms2_error_path = tmp_dir / "ms2_error.json"
            ms1_progress_path = tmp_dir / "ms1_progress.jsonl"
            ms2_progress_path = tmp_dir / "ms2_progress.jsonl"

            ms1_proc = ctx.Process(
                target=_archive_ms1_worker_process,
                args=(
                    str(section_dir),
                    str(ms1_status_path),
                    str(ms1_error_path),
                    str(ms1_progress_path),
                    str(scan_store_root),
                    str(ms1_track_store_root) if ms1_track_store_root else None,
                    _make_archive_ms1_mode(
                        self.ms1_mode,
                        external_full_mz_sidecar=bool(self.retain_zero_intensity_mz),
                    ),
                    self.ms1_island_workers,
                    self.ms1_encode_section_workers,
                    self.ms1_sidecar_backend,
                    self.ms1_full_mz_backend,
                    self.retain_zero_intensity_mz,
                    self.ms1_strategy_b_valley_ratio,
                    self.ms1_sparse_profile_bypass,
                    self.ms1_sparse_profile_short_max_points,
                    self.ms1_sparse_profile_min_zero_fraction,
                    self.ms1_sparse_profile_min_islands_per_scan,
                    self.ms1_sparse_profile_min_short_fraction,
                ),
            )
            ms2_proc = ctx.Process(
                target=_archive_ms2_worker_process,
                args=(
                    str(section_dir),
                    str(ms2_status_path),
                    str(ms2_error_path),
                    str(ms2_progress_path),
                    str(scan_store_root),
                    file_type,
                    ms2_config_dict,
                ),
            )

            procs = [ms1_proc, ms2_proc]
            for proc in procs:
                proc.start()

            try:
                report_progress("metadata_start", {"metadata_pct": 0.0})
                with ThreadPoolExecutor(max_workers=1) as side_executor:
                    metadata_future = side_executor.submit(
                        _extract_and_encode_metadata_auxiliary_payloads_cached,
                        mzml_path,
                        self.metadata_codec,
                        self.auxiliary_backend,
                        metadata_cache_root,
                        metadata_xml_bytes=metadata_xml_bytes,
                        auxiliary_records=auxiliary_records,
                    )
                    ms1_offset = 0
                    ms2_offset = 0
                    metadata_done = False
                    while True:
                        items, ms1_offset = _drain_progress_jsonl(ms1_progress_path, ms1_offset)
                        for item in items:
                            report_progress(str(item.get("stage", "ms1_progress")), {"ms1_pct": item.get("pct"), "section": "ms1"})
                        items, ms2_offset = _drain_progress_jsonl(ms2_progress_path, ms2_offset)
                        for item in items:
                            report_progress(str(item.get("stage", "ms2_progress")), {"ms2_pct": item.get("pct"), "section": "ms2"})
                        if (not metadata_done) and metadata_future.done():
                            (
                                metadata_payload_path,
                                metadata_meta,
                                auxiliary_payload_path,
                                auxiliary_meta,
                                metadata_aux_timing,
                            ) = metadata_future.result()
                            stage_timings.update(metadata_aux_timing)
                            metadata_done = True
                            report_progress("metadata_done", {"metadata_pct": 100.0})
                        alive = False
                        for proc in procs:
                            proc.join(timeout=0.2)
                            alive = alive or proc.is_alive()
                        if metadata_done and (not alive):
                            break
                        time.sleep(0.2)
                    items, ms1_offset = _drain_progress_jsonl(ms1_progress_path, ms1_offset)
                    for item in items:
                        report_progress(str(item.get("stage", "ms1_progress")), {"ms1_pct": item.get("pct"), "section": "ms1"})
                    items, ms2_offset = _drain_progress_jsonl(ms2_progress_path, ms2_offset)
                    for item in items:
                        report_progress(str(item.get("stage", "ms2_progress")), {"ms2_pct": item.get("pct"), "section": "ms2"})
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.terminate()
                    proc.join(timeout=5)

            release_memory()

            if ms1_proc.exitcode != 0:
                detail = _read_json_file(ms1_error_path)["traceback"] if ms1_error_path.exists() else "unknown MS1 worker error"
                raise RuntimeError(f"Archive MS1 worker failed for {mzml_path.name}:\n{detail}")
            if ms2_proc.exitcode != 0:
                detail = _read_json_file(ms2_error_path)["traceback"] if ms2_error_path.exists() else "unknown MS2 worker error"
                raise RuntimeError(f"Archive MS2 worker failed for {mzml_path.name}:\n{detail}")

            ms1_status = _read_json_file(ms1_status_path)
            ms2_status = _read_json_file(ms2_status_path)
            ms1_meta = ms1_status["ms1_meta"]
            ms1_array_starts_meta = ms1_status["ms1_array_starts_meta"]
            ms1_orphan_meta = ms1_status["ms1_orphan_meta"]
            ms1_full_mz_meta = ms1_status["ms1_full_mz_meta"]
            ms1_sparse_profile_bypass_stats = ms1_status.get("ms1_sparse_profile_bypass", self._ms1_sparse_bypass_config())
            ms2_meta = ms2_status["ms2_meta"]
            stage_timings.update(ms1_status.get("stage_timings_s", {}))
            stage_timings.update(ms2_status.get("stage_timings_s", {}))
            stage_timings["parallel_ms1_child_s"] = float(ms1_status.get("elapsed_s", 0.0))
            stage_timings["parallel_ms2_child_s"] = float(ms2_status.get("elapsed_s", 0.0))

            archive_meta = {
                "source_file": str(mzml_path),
                "source_file_name": mzml_path.name,
                "file_type": file_type,
                "input_file_bytes": int(mzml_path.stat().st_size),
                "ms1_mode": self.ms1_mode,
                "ms1_payload_mode": _make_archive_ms1_mode(
                    self.ms1_mode,
                    external_full_mz_sidecar=bool(self.retain_zero_intensity_mz),
                ),
                "ms2_config": ms2_config_dict,
                "reconstructable": True,
                "ms1_full_mz_sidecar": bool(self.retain_zero_intensity_mz),
                "ms1_mz_sidecar_scope": "full_scan_mz",
                "ms1_orphan_intensity_sidecar": True,
                "ms1_sparse_profile_bypass": _strip_archive_runtime_metadata(ms1_sparse_profile_bypass_stats),
                "ms1_island_workers": self.ms1_island_workers,
                "ms1_encode_section_workers": self.ms1_encode_section_workers,
                "retain_zero_intensity_mz": bool(self.retain_zero_intensity_mz),
            }

            section_paths: List[Tuple[str, Path]] = [
                ("archive_meta", tmp_dir / "archive_meta"),
                ("ms1_payload", section_dir / "ms1_payload"),
                ("ms1_meta", section_dir / "ms1_meta"),
                ("ms1_array_starts_payload", section_dir / "ms1_array_starts_payload"),
                ("ms1_array_starts_meta", section_dir / "ms1_array_starts_meta"),
                ("ms1_orphan_intensity_payload", section_dir / "ms1_orphan_intensity_payload"),
                ("ms1_orphan_intensity_meta", section_dir / "ms1_orphan_intensity_meta"),
                ("ms2_payload", section_dir / "ms2_payload"),
                ("ms2_meta", section_dir / "ms2_meta"),
                ("metadata_payload", Path(metadata_payload_path)),
                ("metadata_meta", tmp_dir / "metadata_meta"),
                ("auxiliary_payload", Path(auxiliary_payload_path)),
                ("auxiliary_meta", tmp_dir / "auxiliary_meta"),
            ]
            if self.retain_zero_intensity_mz:
                section_paths.extend(
                    [
                        ("ms1_full_mz_payload", section_dir / "ms1_full_mz_payload"),
                        ("ms1_full_mz_meta", section_dir / "ms1_full_mz_meta"),
                    ]
                )

            (tmp_dir / "archive_meta").write_bytes(_json_bytes(_strip_archive_runtime_metadata(archive_meta)))
            (tmp_dir / "metadata_meta").write_bytes(_json_bytes(_strip_archive_runtime_metadata(metadata_meta)))
            (tmp_dir / "auxiliary_meta").write_bytes(_json_bytes(_strip_archive_runtime_metadata(auxiliary_meta)))

            t0 = time.perf_counter()
            archive_size = _pack_named_section_files(section_paths, output_path)
            stage_timings["pack_archive_s"] = time.perf_counter() - t0
            report_progress("pack_archive_done", {"ms1_pct": 100.0, "ms2_pct": 100.0, "metadata_pct": 100.0})

        stage_timings["total_encode_file_s"] = time.perf_counter() - total_t0
        meta = {
            "source_file": str(mzml_path),
            "file_type": file_type,
            "ms1": ms1_meta,
            "ms2": ms2_meta,
            "metadata": metadata_meta,
            "auxiliary": auxiliary_meta,
                "ms1_full_mz": ms1_full_mz_meta,
                "ms1_orphan_intensity": ms1_orphan_meta,
                "ms1_sparse_profile_bypass": ms1_sparse_profile_bypass_stats,
                "raw_bytes": raw_ms1_bytes + raw_ms2_bytes + metadata_meta["raw_bytes"] + auxiliary_meta["raw_bytes"],
            "input_file_bytes": int(mzml_path.stat().st_size),
            "compressed_bytes": archive_size,
            "compression_ratio": (raw_ms1_bytes + raw_ms2_bytes + metadata_meta["raw_bytes"] + auxiliary_meta["raw_bytes"]) / archive_size if archive_size else 0.0,
            "compression_ratio_vs_input_file": int(mzml_path.stat().st_size) / archive_size if archive_size else 0.0,
            "stage_timings_s": stage_timings,
            "stage_duration_timings_s": _duration_stage_timings(stage_timings),
            "parallel_decision": parallel_decision,
        }
        return output_path, meta

    def encode_file_to_path(
        self,
        mzml_path: str | Path,
        output_path: str | Path,
        *,
        progress_callback: Callable[[Dict], None] | None = None,
    ):
        mzml_path = Path(mzml_path)
        output_path = Path(output_path)
        total_t0 = time.perf_counter()
        stage_timings: Dict[str, float] = {}
        report_progress = _make_progress_reporter(progress_callback, scope="archive")

        t0 = time.perf_counter()
        file_type = detect_file_type(str(mzml_path))
        stage_timings["detect_file_type_s"] = time.perf_counter() - t0

        if self.cache_dir is not None:
            source_cache_root = _archive_source_cache_root(self.cache_dir, mzml_path, file_type)
            scan_store_root = source_cache_root / "scan_store"
            track_store_root = source_cache_root / "tracks"
            source_cache_root.mkdir(parents=True, exist_ok=True)
            t0 = time.perf_counter()
            scan_store, metadata_xml_bytes, auxiliary_records = create_memmap_scan_store_with_metadata_from_mzml(
                mzml_path,
                file_type,
                scan_store_root,
            )
            stage_timings["load_scans_s"] = time.perf_counter() - t0
            raw_ms1_bytes = int(scan_store.raw_ms1_bytes)
            raw_ms2_bytes = int(scan_store.raw_ms2_bytes)
            report_progress("load_scans_done", {"ms1_pct": 0.0, "ms2_pct": 0.0, "metadata_pct": 0.0})
            parallel_decision = self._decide_parallel_process_mode(
                raw_ms1_bytes=raw_ms1_bytes,
                raw_ms2_bytes=raw_ms2_bytes,
                input_file_bytes=int(mzml_path.stat().st_size),
            )
            if not parallel_decision.get("enabled", False):
                stage_timings["parallel_process_mode_suppressed"] = 1.0
            if parallel_decision.get("enabled", False):
                return self._encode_file_to_path_parallel(
                    mzml_path,
                    output_path,
                    total_t0=total_t0,
                    stage_timings=stage_timings,
                    file_type=file_type,
                    scan_store_root=str(scan_store.root),
                    ms1_track_store_root=str(track_store_root),
                    raw_ms1_bytes=raw_ms1_bytes,
                    raw_ms2_bytes=raw_ms2_bytes,
                    parallel_decision=parallel_decision,
                    metadata_cache_root=source_cache_root,
                    metadata_xml_bytes=metadata_xml_bytes,
                    auxiliary_records=auxiliary_records,
                    progress_callback=progress_callback,
                )
            return self._encode_file_to_path_serial_from_scan_store(
                mzml_path=mzml_path,
                output_path=output_path,
                scan_store=scan_store,
                track_store_path=track_store_root,
                file_type=file_type,
                raw_ms1_bytes=raw_ms1_bytes,
                raw_ms2_bytes=raw_ms2_bytes,
                total_t0=total_t0,
                stage_timings=stage_timings,
                parallel_decision=parallel_decision,
                report_progress=report_progress,
                metadata_cache_root=source_cache_root,
                metadata_xml_bytes=metadata_xml_bytes,
                auxiliary_records=auxiliary_records,
            )

        with tempfile.TemporaryDirectory(prefix="trackcodec_scan_store_") as scan_store_tmp:
            t0 = time.perf_counter()
            scan_store, metadata_xml_bytes, auxiliary_records = create_memmap_scan_store_with_metadata_from_mzml(
                mzml_path,
                file_type,
                Path(scan_store_tmp) / "store",
            )
            stage_timings["load_scans_s"] = time.perf_counter() - t0
            report_progress("load_scans_done", {"ms1_pct": 0.0, "ms2_pct": 0.0, "metadata_pct": 0.0})
            raw_ms1_bytes = int(scan_store.raw_ms1_bytes)
            raw_ms2_bytes = int(scan_store.raw_ms2_bytes)
            parallel_decision = self._decide_parallel_process_mode(
                raw_ms1_bytes=raw_ms1_bytes,
                raw_ms2_bytes=raw_ms2_bytes,
                input_file_bytes=int(mzml_path.stat().st_size),
            )
            if not parallel_decision.get("enabled", False):
                stage_timings["parallel_process_mode_suppressed"] = 1.0

            if parallel_decision.get("enabled", False):
                return self._encode_file_to_path_parallel(
                    mzml_path,
                    output_path,
                    total_t0=total_t0,
                    stage_timings=stage_timings,
                    file_type=file_type,
                    scan_store_root=str(scan_store.root),
                    ms1_track_store_root=str(Path(scan_store_tmp) / "tracks"),
                    raw_ms1_bytes=raw_ms1_bytes,
                    raw_ms2_bytes=raw_ms2_bytes,
                    parallel_decision=parallel_decision,
                    metadata_cache_root=None,
                    metadata_xml_bytes=metadata_xml_bytes,
                    auxiliary_records=auxiliary_records,
                    progress_callback=progress_callback,
                )

            return self._encode_file_to_path_serial_from_scan_store(
                mzml_path=mzml_path,
                output_path=output_path,
                scan_store=scan_store,
                track_store_path=Path(scan_store_tmp) / "tracks",
                file_type=file_type,
                raw_ms1_bytes=raw_ms1_bytes,
                raw_ms2_bytes=raw_ms2_bytes,
                total_t0=total_t0,
                stage_timings=stage_timings,
                parallel_decision=parallel_decision,
                report_progress=report_progress,
                metadata_cache_root=None,
                metadata_xml_bytes=metadata_xml_bytes,
                auxiliary_records=auxiliary_records,
            )

    def encode_file_to_path_from_cache(
        self,
        mzml_path: str | Path,
        output_path: str | Path,
        *,
        scan_store_root: str | Path,
        ms1_track_store_root: str | Path | None = None,
        progress_callback: Callable[[Dict], None] | None = None,
    ):
        mzml_path = Path(mzml_path)
        output_path = Path(output_path)
        total_t0 = time.perf_counter()
        stage_timings: Dict[str, float] = {}
        report_progress = _make_progress_reporter(progress_callback, scope="archive")
        track_store_path = Path(ms1_track_store_root) if ms1_track_store_root else None

        t0 = time.perf_counter()
        scan_store = open_memmap_scan_store(scan_store_root)
        stage_timings["open_scan_store_s"] = time.perf_counter() - t0
        file_type = str(scan_store.file_type)
        metadata_xml_bytes = None
        auxiliary_records = None
        metadata_xml_path = Path(scan_store_root) / "metadata_xml.bin"
        auxiliary_records_path = Path(scan_store_root) / "auxiliary_records.json"
        if metadata_xml_path.exists() and auxiliary_records_path.exists():
            try:
                metadata_xml_bytes = metadata_xml_path.read_bytes()
                auxiliary_records = json.loads(auxiliary_records_path.read_text(encoding="utf-8"))
                stage_timings["fused_metadata_cache_available"] = 1.0
            except Exception:
                metadata_xml_bytes = None
                auxiliary_records = None
        report_progress("load_scans_done", {"ms1_pct": 0.0, "ms2_pct": 0.0, "metadata_pct": 0.0})

        raw_ms1_bytes = int(scan_store.raw_ms1_bytes)
        raw_ms2_bytes = int(scan_store.raw_ms2_bytes)
        parallel_decision = self._decide_parallel_process_mode(
            raw_ms1_bytes=raw_ms1_bytes,
            raw_ms2_bytes=raw_ms2_bytes,
            input_file_bytes=int(mzml_path.stat().st_size),
        )
        if not parallel_decision.get("enabled", False):
            stage_timings["parallel_process_mode_suppressed"] = 1.0

        if parallel_decision.get("enabled", False):
            return self._encode_file_to_path_parallel(
                mzml_path,
                output_path,
                total_t0=total_t0,
                stage_timings=stage_timings,
                file_type=file_type,
                scan_store_root=str(Path(scan_store_root)),
                ms1_track_store_root=str(track_store_path) if track_store_path is not None else None,
                raw_ms1_bytes=raw_ms1_bytes,
                raw_ms2_bytes=raw_ms2_bytes,
                parallel_decision=parallel_decision,
                metadata_cache_root=track_store_path.parent if track_store_path is not None else Path(scan_store_root),
                metadata_xml_bytes=metadata_xml_bytes,
                auxiliary_records=auxiliary_records,
                progress_callback=progress_callback,
            )

        return self._encode_file_to_path_serial_from_scan_store(
            mzml_path=mzml_path,
            output_path=output_path,
            scan_store=scan_store,
            track_store_path=track_store_path,
            file_type=file_type,
            raw_ms1_bytes=raw_ms1_bytes,
            raw_ms2_bytes=raw_ms2_bytes,
            total_t0=total_t0,
            stage_timings=stage_timings,
            parallel_decision=parallel_decision,
            report_progress=report_progress,
            metadata_cache_root=track_store_path.parent if track_store_path is not None else Path(scan_store_root),
            metadata_xml_bytes=metadata_xml_bytes,
            auxiliary_records=auxiliary_records,
        )

    def decode_sections(self, archive_blob: bytes):
        sections = _unpack_named_sections(archive_blob)
        archive_meta = json.loads(sections["archive_meta"].decode("utf-8")) if "archive_meta" in sections else {}
        ms1_payload = sections["ms1_payload"]
        ms1_meta = json.loads(sections["ms1_meta"].decode("utf-8"))
        ms1_array_starts_meta = json.loads(sections["ms1_array_starts_meta"].decode("utf-8")) if "ms1_array_starts_meta" in sections else None
        ms1_orphan_intensity_meta = (
            json.loads(sections["ms1_orphan_intensity_meta"].decode("utf-8"))
            if "ms1_orphan_intensity_meta" in sections
            else None
        )
        ms1_full_mz_meta = json.loads(sections["ms1_full_mz_meta"].decode("utf-8")) if "ms1_full_mz_meta" in sections else None
        ms2_meta = json.loads(sections["ms2_meta"].decode("utf-8"))
        metadata_meta = json.loads(sections["metadata_meta"].decode("utf-8"))
        metadata_xml = self.metadata_codec.decode_to_bytes(sections["metadata_payload"], metadata_meta)
        auxiliary_meta = json.loads(sections["auxiliary_meta"].decode("utf-8")) if "auxiliary_meta" in sections else None
        ms1_full_mz_arrays = None
        ms1_complement_mz_entries = None
        if ms1_full_mz_meta and bool(ms1_full_mz_meta.get("enabled", True)) and "ms1_full_mz_payload" in sections:
            sidecar_format = str(ms1_full_mz_meta.get("format", ""))
            if sidecar_format == "ms1_full_mz_qdelta":
                ms1_full_mz_arrays = _decode_ms1_full_mz_sidecar(sections["ms1_full_mz_payload"], ms1_full_mz_meta)
            elif sidecar_format == "ms1_complement_mz_qdelta":
                ms1_complement_mz_entries = _decode_ms1_complement_mz_sidecar(sections["ms1_full_mz_payload"], ms1_full_mz_meta)
            else:
                raise ValueError(f"Unsupported MS1 m/z sidecar format: {ms1_full_mz_meta.get('format')}")
        return {
            "archive_meta": archive_meta,
            "ms1_payload": ms1_payload,
            "ms1_meta": ms1_meta,
            "ms1_array_starts": (
                _decode_uint32_sidecar(sections["ms1_array_starts_payload"], ms1_array_starts_meta)
                if ms1_array_starts_meta
                and bool(ms1_array_starts_meta.get("enabled", True))
                and "ms1_array_starts_payload" in sections
                else None
            ),
            "ms1_array_starts_meta": ms1_array_starts_meta,
            "ms1_orphan_intensity_entries": _decode_ms1_orphan_intensity_sidecar(
                sections["ms1_orphan_intensity_payload"],
                ms1_orphan_intensity_meta,
            )
            if ms1_orphan_intensity_meta
            and bool(ms1_orphan_intensity_meta.get("enabled", True))
            and "ms1_orphan_intensity_payload" in sections
            else None,
            "ms1_orphan_intensity_meta": ms1_orphan_intensity_meta,
            "ms1_full_mz_arrays": ms1_full_mz_arrays,
            "ms1_complement_mz_entries": ms1_complement_mz_entries,
            "ms1_full_mz_meta": ms1_full_mz_meta,
            "ms2_payload_json": _deserialize_ms2_encoded(sections["ms2_payload"]),
            "ms2_meta": ms2_meta,
            "metadata_xml": metadata_xml,
            "metadata_meta": metadata_meta,
            "auxiliary_binary_records": decode_auxiliary_records(sections["auxiliary_payload"], auxiliary_meta) if auxiliary_meta else [],
            "auxiliary_meta": auxiliary_meta,
        }

    def decode_archive_file(
        self,
        archive_path: str | Path,
        *,
        decode_ms1_sidecars: bool = True,
        decode_ms2_payload_json: bool = True,
        decode_auxiliary_records_flag: bool = True,
        decode_metadata_xml: bool = True,
    ):
        wanted_sections = [
            "archive_meta",
            "ms1_payload",
            "ms1_meta",
            "ms1_array_starts_meta",
            "ms1_orphan_intensity_meta",
            "ms1_full_mz_meta",
            "ms2_meta",
            "metadata_meta",
            "auxiliary_meta",
        ]
        if decode_metadata_xml:
            wanted_sections.append("metadata_payload")
        if decode_ms1_sidecars:
            wanted_sections.extend(
                [
                    "ms1_array_starts_payload",
                    "ms1_orphan_intensity_payload",
                    "ms1_full_mz_payload",
                ]
            )
        if decode_ms2_payload_json:
            wanted_sections.append("ms2_payload")
        if decode_auxiliary_records_flag:
            wanted_sections.append("auxiliary_payload")

        sections = _read_named_sections_from_file(archive_path, names=wanted_sections)
        archive_meta = json.loads(sections["archive_meta"].decode("utf-8")) if "archive_meta" in sections else {}
        ms1_meta = json.loads(sections["ms1_meta"].decode("utf-8"))
        ms1_array_starts_meta = json.loads(sections["ms1_array_starts_meta"].decode("utf-8")) if "ms1_array_starts_meta" in sections else None
        ms1_orphan_intensity_meta = (
            json.loads(sections["ms1_orphan_intensity_meta"].decode("utf-8"))
            if "ms1_orphan_intensity_meta" in sections
            else None
        )
        ms1_full_mz_meta = json.loads(sections["ms1_full_mz_meta"].decode("utf-8")) if "ms1_full_mz_meta" in sections else None
        ms2_meta = json.loads(sections["ms2_meta"].decode("utf-8"))
        metadata_meta = json.loads(sections["metadata_meta"].decode("utf-8"))
        metadata_xml = (
            self.metadata_codec.decode_to_bytes(sections["metadata_payload"], metadata_meta)
            if decode_metadata_xml and "metadata_payload" in sections
            else None
        )
        auxiliary_meta = json.loads(sections["auxiliary_meta"].decode("utf-8")) if "auxiliary_meta" in sections else None
        out = {
            "archive_meta": archive_meta,
            "ms1_payload": sections["ms1_payload"],
            "ms1_meta": ms1_meta,
            "ms1_array_starts_meta": ms1_array_starts_meta,
            "ms1_orphan_intensity_meta": ms1_orphan_intensity_meta,
            "ms1_full_mz_meta": ms1_full_mz_meta,
            "ms2_meta": ms2_meta,
            "metadata_xml": metadata_xml,
            "metadata_meta": metadata_meta,
            "auxiliary_meta": auxiliary_meta,
        }
        if (
            decode_ms1_sidecars
            and ms1_array_starts_meta
            and bool(ms1_array_starts_meta.get("enabled", True))
            and "ms1_array_starts_payload" in sections
        ):
            out["ms1_array_starts"] = _decode_uint32_sidecar(sections["ms1_array_starts_payload"], ms1_array_starts_meta)
        else:
            out["ms1_array_starts"] = None
        if (
            decode_ms1_sidecars
            and ms1_orphan_intensity_meta
            and bool(ms1_orphan_intensity_meta.get("enabled", True))
            and "ms1_orphan_intensity_payload" in sections
        ):
            out["ms1_orphan_intensity_entries"] = _decode_ms1_orphan_intensity_sidecar(
                sections["ms1_orphan_intensity_payload"],
                ms1_orphan_intensity_meta,
            )
        else:
            out["ms1_orphan_intensity_entries"] = None
        out["ms1_full_mz_arrays"] = None
        out["ms1_complement_mz_entries"] = None
        if (
            decode_ms1_sidecars
            and ms1_full_mz_meta
            and bool(ms1_full_mz_meta.get("enabled", True))
            and "ms1_full_mz_payload" in sections
        ):
            sidecar_format = str(ms1_full_mz_meta.get("format", ""))
            if sidecar_format == "ms1_full_mz_qdelta":
                out["ms1_full_mz_arrays"] = _decode_ms1_full_mz_sidecar(sections["ms1_full_mz_payload"], ms1_full_mz_meta)
            elif sidecar_format == "ms1_complement_mz_qdelta":
                out["ms1_complement_mz_entries"] = _decode_ms1_complement_mz_sidecar(sections["ms1_full_mz_payload"], ms1_full_mz_meta)
            else:
                raise ValueError(f"Unsupported MS1 m/z sidecar format: {ms1_full_mz_meta.get('format')}")
        if decode_ms2_payload_json and "ms2_payload" in sections:
            out["ms2_payload_json"] = _deserialize_ms2_encoded(sections["ms2_payload"])
        else:
            out["ms2_payload_json"] = None
        if decode_auxiliary_records_flag and auxiliary_meta and "auxiliary_payload" in sections:
            out["auxiliary_binary_records"] = decode_auxiliary_records(sections["auxiliary_payload"], auxiliary_meta)
        else:
            out["auxiliary_binary_records"] = []
        return out

    def _sections_to_mzml_bytes(
        self,
        sections: Dict,
        *,
        binary_compression: str = "preserve_template",
    ) -> bytes:
        root = _parse_root(sections["metadata_xml"])
        templates = extract_ms1_scan_templates_from_root(root)
        archive_meta = sections["archive_meta"]
        if not archive_meta.get("retain_zero_intensity_mz", archive_meta.get("ms1_full_mz_sidecar", True)):
            raise ValueError(
                "This TrackCodec-Archive was created with retain_zero_intensity_mz=False; "
                "exact MS1 full-scan mzML reconstruction is unavailable."
            )
        ms1_scans = self._iter_decode_ms1_scans(
            sections["ms1_payload"],
            sections["ms1_meta"],
            templates,
            sections.get("ms1_array_starts"),
            sections.get("ms1_full_mz_arrays"),
            sections.get("ms1_complement_mz_entries"),
            sections.get("ms1_orphan_intensity_entries"),
        )
        ms2_decoded = self._decode_ms2_scans(sections["ms2_payload_json"], archive_meta)
        return rebuild_mzml_from_root(
            root,
            ms1_scans,
            ms2_decoded,
            file_type=archive_meta["file_type"],
            auxiliary_binary_records=sections.get("auxiliary_binary_records") or [],
            binary_compression=binary_compression,
        )

    def _sections_to_mzml_file(
        self,
        sections: Dict,
        output_path: str | Path,
        *,
        binary_compression: str = "preserve_template",
    ) -> Path:
        root = _parse_root(sections["metadata_xml"])
        templates = extract_ms1_scan_templates_from_root(root)
        archive_meta = sections["archive_meta"]
        if not archive_meta.get("retain_zero_intensity_mz", archive_meta.get("ms1_full_mz_sidecar", True)):
            raise ValueError(
                "This TrackCodec-Archive was created with retain_zero_intensity_mz=False; "
                "exact MS1 full-scan mzML reconstruction is unavailable."
            )
        ms1_scans = self._iter_decode_ms1_scans(
            sections["ms1_payload"],
            sections["ms1_meta"],
            templates,
            sections.get("ms1_array_starts"),
            sections.get("ms1_full_mz_arrays"),
            sections.get("ms1_complement_mz_entries"),
            sections.get("ms1_orphan_intensity_entries"),
        )
        ms2_decoded = self._decode_ms2_scans_for_reconstruction(sections["ms2_payload_json"], archive_meta)
        return write_rebuilt_mzml_from_root(
            root,
            ms1_scans,
            ms2_decoded,
            output_path,
            file_type=archive_meta["file_type"],
            auxiliary_binary_records=sections.get("auxiliary_binary_records") or [],
            binary_compression=binary_compression,
        )

    def decode_to_mzml_bytes(
        self,
        archive_blob: bytes | str | Path,
        *,
        binary_compression: str = "preserve_template",
    ) -> bytes:
        if isinstance(archive_blob, (str, Path)):
            sections = self.decode_archive_file(
                archive_blob,
                decode_ms1_sidecars=True,
                decode_ms2_payload_json=True,
                decode_auxiliary_records_flag=True,
            )
        else:
            sections = self.decode_sections(archive_blob)
        return self._sections_to_mzml_bytes(
            sections,
            binary_compression=binary_compression,
        )

    def decode_to_mzml_file(
        self,
        archive_blob: bytes | str | Path,
        output_path: str | Path,
        *,
        binary_compression: str = "preserve_template",
    ) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(archive_blob, (str, Path)):
            sections = self.decode_archive_file(
                archive_blob,
                decode_ms1_sidecars=True,
                decode_ms2_payload_json=True,
                decode_auxiliary_records_flag=True,
            )
        else:
            sections = self.decode_sections(archive_blob)
        self._sections_to_mzml_file(
            sections,
            output_path,
            binary_compression=binary_compression,
        )
        release_memory()
        return output_path

    def export_to_mzml_file(
        self,
        archive_blob: bytes | str | Path,
        output_path: str | Path,
        *,
        binary_compression: str = "preserve_template",
    ) -> Path:
        return self.decode_to_mzml_file(
            archive_blob,
            output_path,
            binary_compression=binary_compression,
        )
