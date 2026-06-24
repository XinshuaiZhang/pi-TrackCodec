"""TrackCodec cross-scan codec for MS1 tracks.

Implements cross-scan prediction on top of the track/island representation:
- S1: cross-scan delta for same-length islands within a track
- S2: SZDPD coding for delta residuals in near-lossless mode
- S3: selective FastPFor integer coding on long intensity streams
- S4: previous-grid exact-offset m/z sharing within a track
- S5: optional zero-RLE on selected integer streams before backend compression
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import gc
import hashlib
import json
from collections import OrderedDict
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Tuple

import numpy as np

from ..common.bitshuffle import byteplane_merge, byteplane_split
from ..common.compression_backends import available_backends, compress, compress_many, decompress, decompress_many
from ..common.tracking import CompactIslandTracks
from . import baseline_ms1_codec
from . import _enable_windows_native_dll_dirs

_enable_windows_native_dll_dirs()

try:
    from . import _cross_scan_speedups as _cross_scan_speedups
    HAVE_CROSS_SCAN_SPEEDUPS = True
except ImportError:  # pragma: no cover - optional native extension
    _cross_scan_speedups = None
    HAVE_CROSS_SCAN_SPEEDUPS = False

try:
    import pyfastpfor
    HAVE_PYFASTPFOR = True
except ImportError:  # pragma: no cover - optional dependency
    pyfastpfor = None
    HAVE_PYFASTPFOR = False


MS1_CROSS_SCAN_MAGIC = b"TCMS1CS\0"
CROSS_SCAN_INTENSITY_MODES = [
    "strict_lossless_xdelta",
    "szdpd_xdelta",
    "szdpd_xdelta_equalfidelity",
    "stackzdpd_passthrough",
]

MZ_KIND_RAW = 0
MZ_KIND_PREV_OFFSET = 1
MZ_KIND_PREV_OFFSET_RESIDUAL = 2
INT_KIND_FULL = 0
INT_KIND_DELTA = 1
INT_KIND_DELTA2 = 2
FULL_MODE_NONE = 0
FULL_MODE_NO_REF = 1
FULL_MODE_REJECT = 2

DELTA_LENGTH_MODE_CURR_LEN = "curr_len"
DELTA_LENGTH_MODE_MAX_REF_CURR = "max_ref_curr"
SUPPORTED_DELTA_LENGTH_MODES = {
    DELTA_LENGTH_MODE_CURR_LEN,
    DELTA_LENGTH_MODE_MAX_REF_CURR,
}

EQUAL_FIDELITY_OVERFLOW_MODE_LOG2_NEGATIVE_CODE = "log2_negative_code"
EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT = "strict_lossless_full_segment"
SUPPORTED_EQUAL_FIDELITY_OVERFLOW_MODES = {
    EQUAL_FIDELITY_OVERFLOW_MODE_LOG2_NEGATIVE_CODE,
    EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT,
}

EMPTY_FLOAT64 = np.array([], dtype=np.float64)
EMPTY_UINT32 = np.array([], dtype=np.uint32)
EMPTY_UINT64 = np.array([], dtype=np.uint64)
EMPTY_INT64 = np.array([], dtype=np.int64)
EMPTY_REF_INFO = (None, None, None)


def _release_temp_memmap(arr: np.ndarray) -> None:
    if not isinstance(arr, np.memmap):
        return
    try:
        arr.flush()
    except Exception:
        pass
    mmap_obj = getattr(arr, "_mmap", None)
    if mmap_obj is None:
        return
    try:
        mmap_obj.close()
    except Exception:
        pass

UINT32_TOP2_FULL_SEARCH_MIN_COUNT = 1_000_000
UINT32_TOP2_METADATA_STREAMS = {
    "array_starts",
    "scan_indices",
    "track_lengths",
    "n_points",
    "mz_ref_indices",
    "int_indices",
    "full_lengths",
    "delta_lengths",
    "mz_lengths",
    "mz_first_values",
    "mz_first_overflow_idx",
    "mz_delta_values",
    "mz_model_residual_lengths",
    "mz_residual_values",
    "cross_track_ref_indices",
    "delta_ref_offsets",
    "full_scan_scan_indices",
    "full_scan_lengths",
    "full_scan_first_values",
    "full_scan_first_overflow_idx",
    "full_scan_delta_values",
}

UINT32_MAX = np.uint64(np.iinfo(np.uint32).max)
UINT32_MAX_INT = int(np.iinfo(np.uint32).max)
INT64_MAX = np.uint64(np.iinfo(np.int64).max)
BITPACK_VECTOR_CHUNK = 1 << 18
SPARSE_L0_DELTA_STORAGE_KIND = "sparse_l0_delta_int32"
SPARSE_L0_DELTA_MAGIC = b"TCL0D6\0\0"
SPARSE_L0_DELTA_HEADER = struct.Struct("<8sQQ")


CrossTrackHistoryEntry = Dict[int, List[int]]


def _append_cross_track_bucket(
    history_entry: CrossTrackHistoryEntry,
    segment_len: int,
    island_idx: int,
    *,
    per_length_limit: int | None,
) -> None:
    seg_len = int(segment_len)
    bucket = history_entry.get(seg_len)
    if bucket is None:
        bucket = []
        history_entry[seg_len] = bucket
    bucket.append(int(island_idx))
    if per_length_limit is not None:
        overflow = len(bucket) - int(per_length_limit)
        if overflow > 0:
            del bucket[:overflow]


def _collect_cross_track_candidates(
    history_entry: CrossTrackHistoryEntry,
    curr_len: int,
    *,
    padded_delta_max_diff: int,
) -> List[int]:
    candidate_indices: List[int] = []
    for seg_len in range(int(curr_len) - int(padded_delta_max_diff), int(curr_len) + int(padded_delta_max_diff) + 1):
        if seg_len < 0:
            continue
        bucket = history_entry.get(int(seg_len))
        if bucket is None:
            continue
        candidate_indices.extend(bucket)
    return candidate_indices


@dataclass(slots=True)
class _PreparedTrackRepresentation:
    track_index: int
    track_start: int
    track_end: int
    scan_indices: np.ndarray
    array_starts: np.ndarray | None
    n_points: np.ndarray
    island_indices: np.ndarray
    quantized_mz: List[np.ndarray] | None
    native_int_kinds: np.ndarray | None
    native_ref_offsets: np.ndarray | None
    native_encoded_lens: np.ndarray | None
    native_full_estimates: np.ndarray | None

    @property
    def track_island_count(self) -> int:
        return int(self.track_end - self.track_start)


@dataclass(slots=True)
class _SourceContext:
    tracks: object
    compact_tracks: CompactIslandTracks | None
    all_islands: object
    ms1_scans: object
    flat_mz_data: np.ndarray | None
    flat_intensity_data: np.ndarray | None
    ms1_scan_base_offsets: np.ndarray | None
    tempdir: str | None


def _pad_float64(arr: np.ndarray, length: int) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.float64)
    if len(arr) >= length:
        return arr
    out = np.zeros(length, dtype=np.float64)
    out[: len(arr)] = arr
    return out


def _pad_int64(arr: np.ndarray, length: int) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.int64)
    if len(arr) >= length:
        return arr
    out = np.zeros(length, dtype=np.int64)
    out[: len(arr)] = arr
    return out


def _as_float64_view(arr: np.ndarray) -> np.ndarray:
    if isinstance(arr, np.ndarray) and arr.dtype == np.float64:
        return arr
    return np.asarray(arr, dtype=np.float64)


def _concat_segments_as_dtype(segments: List[np.ndarray], dtype: np.dtype) -> np.ndarray:
    if not segments:
        return np.array([], dtype=dtype)
    total = sum(int(len(seg)) for seg in segments)
    out = np.empty(total, dtype=dtype)
    pos = 0
    for segment in segments:
        arr = np.asarray(segment, dtype=dtype)
        next_pos = pos + len(arr)
        out[pos:next_pos] = arr
        pos = next_pos
    return out


def _iter_track_island_ranges(tracks):
    if isinstance(tracks, CompactIslandTracks):
        yield from tracks.iter_track_ranges()
        return
    offset = 0
    for track in tracks:
        next_offset = offset + len(track.islands)
        yield offset, next_offset
        offset = next_offset


def _compact_track_scan_slice(
    compact_tracks: CompactIslandTracks,
    ms1_scans,
    island_idx: int,
) -> tuple[int, int, int, np.ndarray, np.ndarray]:
    scan_idx = int(compact_tracks.scan_indices[island_idx])
    start = int(compact_tracks.array_start_indices[island_idx])
    count = int(compact_tracks.n_points[island_idx])
    if scan_idx < 0 or scan_idx >= len(ms1_scans):
        raise IndexError(f"scan_idx out of range for compact track slice: {scan_idx}")
    scan = ms1_scans[scan_idx]
    mz_full = _as_float64_view(scan["mz_array"])
    int_full = _as_float64_view(scan["intensity_array"])
    if mz_full.size != int_full.size:
        raise ValueError(
            "Compact track slice source scan has mismatched mz/intensity lengths: "
            f"island_idx={island_idx} scan_idx={scan_idx} mz_len={mz_full.size} int_len={int_full.size}"
        )
    if start < 0 or count < 0:
        raise IndexError(
            "Negative compact track slice bounds: "
            f"island_idx={island_idx} scan_idx={scan_idx} start={start} count={count}"
        )
    end = start + count
    if end > mz_full.size:
        raise IndexError(
            "Compact track slice exceeds source scan length: "
            f"island_idx={island_idx} scan_idx={scan_idx} start={start} end={end} scan_len={mz_full.size}"
        )
    return scan_idx, start, count, mz_full[start:end], int_full[start:end]


def _source_segment_view(
    tracks,
    compact_tracks: CompactIslandTracks | None,
    all_islands,
    ms1_scans,
    island_idx: int,
) -> np.ndarray:
    if compact_tracks is None:
        offset = 0
        for track in tracks:
            next_offset = offset + len(track.islands)
            if island_idx < next_offset:
                return _as_float64_view(track.islands[int(island_idx - offset)].intensity_array)
            offset = next_offset
        raise IndexError(f"island_idx out of range: {island_idx}")
    if all_islands is not None:
        return _as_float64_view(all_islands[int(island_idx)].intensity_array)
    _, _, _, _, int_view = _compact_track_scan_slice(compact_tracks, ms1_scans, int(island_idx))
    return _as_float64_view(int_view)


def _source_mz_view(
    tracks,
    compact_tracks: CompactIslandTracks | None,
    all_islands,
    ms1_scans,
    island_idx: int,
) -> np.ndarray:
    if compact_tracks is None:
        offset = 0
        for track in tracks:
            next_offset = offset + len(track.islands)
            if island_idx < next_offset:
                return _as_float64_view(track.islands[int(island_idx - offset)].mz_array)
            offset = next_offset
        raise IndexError(f"island_idx out of range: {island_idx}")
    if all_islands is not None:
        return _as_float64_view(all_islands[int(island_idx)].mz_array)
    _, _, _, mz_view, _ = _compact_track_scan_slice(compact_tracks, ms1_scans, int(island_idx))
    return _as_float64_view(mz_view)


def _make_source_context(
    tracks,
    compact_tracks: CompactIslandTracks | None,
    all_islands,
    ms1_scans,
    *,
    tempdir: str | None = None,
) -> _SourceContext:
    flat_mz_data = None
    flat_intensity_data = None
    ms1_scan_base_offsets = None
    local_tempdir = tempdir

    def _ensure_tempdir() -> str:
        nonlocal local_tempdir
        if local_tempdir is None:
            local_tempdir = tempfile.mkdtemp(prefix="trackcodec_source_")
        return local_tempdir

    def _materialize_ms1_scans(scans) -> None:
        nonlocal flat_mz_data, flat_intensity_data, ms1_scan_base_offsets
        scans_list = list(scans)
        scan_lengths = np.asarray([len(scan["mz_array"]) for scan in scans_list], dtype=np.uint64)
        base_offsets = np.zeros(len(scan_lengths), dtype=np.uint64)
        if len(scan_lengths):
            base_offsets[1:] = np.cumsum(scan_lengths[:-1], dtype=np.uint64)
        total_points = int(scan_lengths.astype(np.uint64, copy=False).sum()) if len(scan_lengths) else 0
        root = Path(_ensure_tempdir())
        mz_path = root / "source_flat_mz.bin"
        int_path = root / "source_flat_intensity.bin"
        flat_mz_data = np.memmap(mz_path, dtype=np.float64, mode="w+", shape=(total_points,))
        flat_intensity_data = np.memmap(int_path, dtype=np.float64, mode="w+", shape=(total_points,))
        pos = 0
        for scan in scans_list:
            mz_arr = _as_float64_view(scan["mz_array"])
            int_arr = _as_float64_view(scan["intensity_array"])
            next_pos = pos + len(mz_arr)
            flat_mz_data[pos:next_pos] = mz_arr
            flat_intensity_data[pos:next_pos] = int_arr
            pos = next_pos
        ms1_scan_base_offsets = base_offsets

    def _iter_islands():
        if compact_tracks is not None and all_islands is not None:
            yield from all_islands
            return
        if compact_tracks is None:
            for track in tracks:
                yield from track.islands

    def _materialize_from_islands() -> None:
        nonlocal flat_mz_data, flat_intensity_data, ms1_scan_base_offsets
        islands = list(_iter_islands())
        if not islands:
            root = Path(_ensure_tempdir())
            flat_mz_data = np.memmap(root / "source_flat_mz.bin", dtype=np.float64, mode="w+", shape=(0,))
            flat_intensity_data = np.memmap(root / "source_flat_intensity.bin", dtype=np.float64, mode="w+", shape=(0,))
            ms1_scan_base_offsets = np.zeros(0, dtype=np.uint64)
            return
        scan_count = max(int(island.scan_idx) for island in islands) + 1
        scan_lengths = np.zeros(scan_count, dtype=np.uint64)
        for island in islands:
            scan_idx = int(island.scan_idx)
            end = int(island.array_start_idx) + int(island.n_points)
            if end > scan_lengths[scan_idx]:
                scan_lengths[scan_idx] = end
        base_offsets = np.zeros(scan_count, dtype=np.uint64)
        if scan_count > 1:
            base_offsets[1:] = np.cumsum(scan_lengths[:-1], dtype=np.uint64)
        total_points = int(scan_lengths.astype(np.uint64, copy=False).sum())
        root = Path(_ensure_tempdir())
        mz_path = root / "source_flat_mz.bin"
        int_path = root / "source_flat_intensity.bin"
        flat_mz_data = np.memmap(mz_path, dtype=np.float64, mode="w+", shape=(total_points,))
        flat_intensity_data = np.memmap(int_path, dtype=np.float64, mode="w+", shape=(total_points,))
        occupied_by_scan: list[set[tuple[int, int]] | None] = [set() for _ in range(scan_count)]
        for island in islands:
            scan_idx = int(island.scan_idx)
            local_start = int(island.array_start_idx)
            local_end = local_start + int(island.n_points)
            occupied = occupied_by_scan[scan_idx]
            if occupied is not None:
                for prev_start, prev_end in occupied:
                    if local_start < prev_end and local_end > prev_start:
                        raise ValueError(
                            "Overlapping MS1 island array ranges while materializing source data: "
                            f"scan_idx={scan_idx}, range=({local_start}, {local_end}), "
                            f"previous=({prev_start}, {prev_end})"
                        )
                occupied.add((local_start, local_end))
                if len(occupied) > 4096:
                    occupied_by_scan[scan_idx] = None
            start = int(base_offsets[scan_idx]) + local_start
            end = int(base_offsets[scan_idx]) + local_end
            flat_mz_data[start:end] = _as_float64_view(island.mz_array)
            flat_intensity_data[start:end] = _as_float64_view(island.intensity_array)
        ms1_scan_base_offsets = base_offsets

    if compact_tracks is not None and all_islands is None and ms1_scans is not None:
        store = getattr(ms1_scans, "store", None)
        global_indices = getattr(ms1_scans, "global_indices", None)
        if store is not None and global_indices is not None:
            try:
                flat_mz_data = store.mz_data
                flat_intensity_data = store.intensity_data
                ms1_scan_base_offsets = np.asarray(store.offsets[global_indices], dtype=np.uint64)
            except Exception:
                flat_mz_data = None
                flat_intensity_data = None
                ms1_scan_base_offsets = None
    if flat_mz_data is None and ms1_scans is not None:
        _materialize_ms1_scans(ms1_scans)
    if flat_mz_data is None:
        _materialize_from_islands()
    return _SourceContext(
        tracks=tracks,
        compact_tracks=compact_tracks,
        all_islands=all_islands,
        ms1_scans=ms1_scans,
        flat_mz_data=flat_mz_data,
        flat_intensity_data=flat_intensity_data,
        ms1_scan_base_offsets=ms1_scan_base_offsets,
        tempdir=local_tempdir,
    )


def _source_segment_view_ctx(ctx: _SourceContext, island_idx: int) -> np.ndarray:
    compact_tracks = ctx.compact_tracks
    if (
        compact_tracks is not None
        and ctx.all_islands is None
        and ctx.flat_intensity_data is not None
        and ctx.ms1_scan_base_offsets is not None
    ):
        scan_idx = int(compact_tracks.scan_indices[island_idx])
        start = int(compact_tracks.array_start_indices[island_idx])
        count = int(compact_tracks.n_points[island_idx])
        base = int(ctx.ms1_scan_base_offsets[scan_idx])
        abs_start = base + start
        abs_end = abs_start + count
        return _as_float64_view(ctx.flat_intensity_data[abs_start:abs_end])
    return _source_segment_view(ctx.tracks, compact_tracks, ctx.all_islands, ctx.ms1_scans, island_idx)


def _source_mz_view_ctx(ctx: _SourceContext, island_idx: int) -> np.ndarray:
    compact_tracks = ctx.compact_tracks
    if (
        compact_tracks is not None
        and ctx.all_islands is None
        and ctx.flat_mz_data is not None
        and ctx.ms1_scan_base_offsets is not None
    ):
        scan_idx = int(compact_tracks.scan_indices[island_idx])
        start = int(compact_tracks.array_start_indices[island_idx])
        count = int(compact_tracks.n_points[island_idx])
        base = int(ctx.ms1_scan_base_offsets[scan_idx])
        abs_start = base + start
        abs_end = abs_start + count
        return _as_float64_view(ctx.flat_mz_data[abs_start:abs_end])
    return _source_mz_view(ctx.tracks, compact_tracks, ctx.all_islands, ctx.ms1_scans, island_idx)


def _can_use_native_equal_fidelity_track_planner(codec, equal_fidelity_precision) -> bool:
    if codec.intensity_mode == "stackzdpd_passthrough":
        return False
    return (
        HAVE_CROSS_SCAN_SPEEDUPS
        and equal_fidelity_precision is not None
        and codec.intensity_mode == "szdpd_xdelta_equalfidelity"
        and codec.equal_fidelity_overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT
    )


def _runtime_cache_get(runtime_cache: Dict | None, cache_name: str, key):
    if runtime_cache is None:
        return None
    lock = runtime_cache.get("_lock")
    if lock is None:
        caches = runtime_cache.get("_caches")
        if not isinstance(caches, dict):
            return None
        cache = caches.get(cache_name)
        if cache is None:
            return None
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value
    with lock:
        caches = runtime_cache.get("_caches")
        if not isinstance(caches, dict):
            return None
        cache = caches.get(cache_name)
        if cache is None:
            return None
        value = cache.get(key)
        if value is not None:
            cache.move_to_end(key)
        return value


def _runtime_cache_put(runtime_cache: Dict | None, cache_name: str, key, value) -> None:
    if runtime_cache is None:
        return
    lock = runtime_cache.get("_lock")
    if lock is None:
        caches = runtime_cache.setdefault("_caches", {})
        cache = caches.get(cache_name)
        if cache is None:
            cache = OrderedDict()
            caches[cache_name] = cache
        elif key in cache:
            cache.move_to_end(key)
        cache[key] = value
        limits = runtime_cache.get("_limits", {})
        limit = int(limits.get(cache_name, 0))
        if limit > 0:
            while len(cache) > limit:
                cache.popitem(last=False)
        return
    with lock:
        caches = runtime_cache.setdefault("_caches", {})
        cache = caches.get(cache_name)
        if cache is None:
            cache = OrderedDict()
            caches[cache_name] = cache
        elif key in cache:
            cache.move_to_end(key)
        cache[key] = value
        limits = runtime_cache.get("_limits", {})
        limit = int(limits.get(cache_name, 0))
        if limit > 0:
            while len(cache) > limit:
                cache.popitem(last=False)


def _has_fractional_values(values: np.ndarray) -> bool:
    arr = np.asarray(values, dtype=np.float64)
    if len(arr) == 0:
        return False
    return bool(np.any(np.abs(arr - np.round(arr)) > 1e-9))


def _detect_szdpd_precision_from_segment_sequence(
    segments: List[np.ndarray],
    sample_size: int = 1000,
) -> int:
    total_points = 0
    for segment in segments:
        total_points += int(len(segment))
    if total_points <= 0:
        return 1

    sample_count = min(int(sample_size), total_points)
    target_positions = np.linspace(0, total_points - 1, sample_count, dtype=int)
    sample = np.empty(sample_count, dtype=np.float64)
    sample_pos = 0
    offset = 0
    for segment in segments:
        arr = np.asarray(segment, dtype=np.float64)
        next_offset = offset + len(arr)
        while sample_pos < sample_count and target_positions[sample_pos] < next_offset:
            local_idx = int(target_positions[sample_pos] - offset)
            sample[sample_pos] = arr[local_idx]
            sample_pos += 1
        offset = next_offset
        if sample_pos >= sample_count:
            break
    if sample_pos != sample_count:
        sample = sample[:sample_pos]
    if _has_fractional_values(sample):
        return 10

    # Sampling is only a fast positive detector.  If it sees no fractional
    # values, scan every segment once so rare fractional intensities cannot be
    # silently encoded with integer precision.
    for segment in segments:
        if _has_fractional_values(segment):
            return 10
    return 1


def _require_pfor():
    if not HAVE_PYFASTPFOR:
        raise ImportError(
            "This stream was encoded with FastPFor and requires the optional "
            "pyfastpfor dependency. Install TrackCodec[pfor] or install "
            "requirements_pfor.txt in an environment with a supported binary "
            "wheel or C++ build toolchain."
        )


def _zigzag_encode_int32(arr: np.ndarray) -> np.ndarray:
    arr64 = np.asarray(arr, dtype=np.int64)
    if np.any((arr64 < np.iinfo(np.int32).min) | (arr64 > np.iinfo(np.int32).max)):
        raise OverflowError("zigzag int32 encode received values outside int32 range")
    encoded = (arr64 << 1) ^ (arr64 >> 63)
    return encoded.astype(np.uint32)


def _zigzag_decode_uint32(arr: np.ndarray) -> np.ndarray:
    arr64 = np.asarray(arr, dtype=np.uint64).astype(np.int64)
    decoded = (arr64 >> 1) ^ -(arr64 & 1)
    return decoded.astype(np.int32)


def _zero_rle_encode_uint32(arr: np.ndarray, min_run: int = 4) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.uint32)
    if len(arr) == 0:
        return arr
    if np.any(arr == UINT32_MAX_INT):
        raise OverflowError("zero-RLE uint32 cannot encode UINT32_MAX without ambiguity")
    if HAVE_CROSS_SCAN_SPEEDUPS and arr.ndim == 1:
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr, dtype=np.uint32)
        return np.asarray(_cross_scan_speedups.zero_rle_encode_uint32(arr, int(min_run)), dtype=np.uint32)

    out: List[int] = []
    i = 0
    while i < len(arr):
        if arr[i] == 0:
            j = i + 1
            while j < len(arr) and arr[j] == 0:
                j += 1
            run = j - i
            if run >= min_run:
                out.extend((0, run))
            else:
                out.extend([1] * run)
            i = j
            continue
        out.append(int(arr[i]) + 1)
        i += 1
    return np.asarray(out, dtype=np.uint32)


def _zero_rle_decode_uint32(arr: np.ndarray) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.uint32)
    if len(arr) == 0:
        return arr
    if HAVE_CROSS_SCAN_SPEEDUPS and arr.ndim == 1:
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr, dtype=np.uint32)
        return np.asarray(_cross_scan_speedups.zero_rle_decode_uint32(arr), dtype=np.uint32)

    out: List[int] = []
    i = 0
    while i < len(arr):
        token = int(arr[i])
        if token == 0:
            if i + 1 >= len(arr):
                raise ValueError("Corrupt zero-RLE stream: missing run length")
            run = int(arr[i + 1])
            out.extend([0] * run)
            i += 2
            continue
        out.append(token - 1)
        i += 1
    return np.asarray(out, dtype=np.uint32)


def _pack_small_uint(arr: np.ndarray, bits: int) -> bytes:
    arr = np.asarray(arr, dtype=np.uint8)
    if bits not in (1, 2, 4, 8):
        raise ValueError(f"Unsupported small-uint bit width: {bits}")
    if HAVE_CROSS_SCAN_SPEEDUPS and arr.ndim == 1:
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr, dtype=np.uint8)
        return bytes(_cross_scan_speedups.pack_small_uint(arr, int(bits)))
    if bits == 8:
        return arr.tobytes()
    mask = (1 << bits) - 1
    per_byte = 8 // bits
    out = np.zeros((len(arr) + per_byte - 1) // per_byte, dtype=np.uint8)
    for idx, value in enumerate(arr):
        out[idx // per_byte] |= (int(value) & mask) << ((idx % per_byte) * bits)
    return out.tobytes()


def _unpack_small_uint(payload: bytes, bits: int, count: int) -> np.ndarray:
    if bits not in (1, 2, 4, 8):
        raise ValueError(f"Unsupported small-uint bit width: {bits}")
    if count == 0:
        if len(payload) != 0:
            raise ValueError(f"Corrupt small-uint stream: expected 0 bytes, got {len(payload)}")
        return np.array([], dtype=np.uint8)
    expected_size = (int(count) * int(bits) + 7) // 8
    if len(payload) != expected_size:
        raise ValueError(f"Corrupt small-uint stream: expected {expected_size} bytes, got {len(payload)}")
    if HAVE_CROSS_SCAN_SPEEDUPS:
        out = _cross_scan_speedups.unpack_small_uint(payload, int(bits), int(count))
        return np.asarray(out, dtype=np.uint8)
    if bits == 8:
        arr = np.frombuffer(payload, dtype=np.uint8).copy()
        return arr[:count]
    mask = (1 << bits) - 1
    per_byte = 8 // bits
    raw = np.frombuffer(payload, dtype=np.uint8)
    out = np.empty(count, dtype=np.uint8)
    for idx in range(count):
        out[idx] = (raw[idx // per_byte] >> ((idx % per_byte) * bits)) & mask
    return out


def _pack_uint32_bits(arr: np.ndarray, bit_width: int) -> bytes:
    arr = np.asarray(arr, dtype=np.uint32)
    if bit_width < 1 or bit_width > 32:
        raise ValueError(f"Unsupported uint32 bit width: {bit_width}")
    if len(arr) == 0:
        return b""
    if HAVE_CROSS_SCAN_SPEEDUPS and arr.ndim == 1:
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr, dtype=np.uint32)
        return bytes(_cross_scan_speedups.pack_uint32_bits(arr, int(bit_width)))
    if bit_width == 32:
        return arr.astype(np.uint32, copy=False).tobytes()
    mask = np.uint64((1 << bit_width) - 1)
    arr64 = np.asarray(arr, dtype=np.uint64) & mask
    total_bytes = (len(arr64) * bit_width + 7) // 8
    word_count = ((total_bytes + 7) // 8) + 1
    words = np.zeros(word_count, dtype=np.uint64)
    bit_width_u64 = np.uint64(bit_width)

    for start in range(0, len(arr64), BITPACK_VECTOR_CHUNK):
        stop = min(len(arr64), start + BITPACK_VECTOR_CHUNK)
        chunk = arr64[start:stop]
        if len(chunk) == 0:
            continue
        bit_pos = (np.arange(len(chunk), dtype=np.uint64) + np.uint64(start)) * bit_width_u64
        word_idx = (bit_pos >> 6).astype(np.int64, copy=False)
        shift = bit_pos & np.uint64(63)
        np.bitwise_or.at(words, word_idx, chunk << shift)
        spill_mask = (shift + bit_width_u64) > np.uint64(64)
        if np.any(spill_mask):
            spill_idx = word_idx[spill_mask] + 1
            spill_vals = chunk[spill_mask] >> (np.uint64(64) - shift[spill_mask])
            np.bitwise_or.at(words, spill_idx, spill_vals)

    return words.view(np.uint8)[:total_bytes].tobytes()


def _unpack_uint32_bits(payload: bytes, bit_width: int, count: int) -> np.ndarray:
    if bit_width < 1 or bit_width > 32:
        raise ValueError(f"Unsupported uint32 bit width: {bit_width}")
    if count == 0:
        if len(payload) != 0:
            raise ValueError(f"Corrupt uint32 bitpack stream: expected 0 bytes, got {len(payload)}")
        return np.array([], dtype=np.uint32)
    expected_size = (int(count) * int(bit_width) + 7) // 8
    if len(payload) != expected_size:
        raise ValueError(f"Corrupt uint32 bitpack stream: expected {expected_size} bytes, got {len(payload)}")
    if HAVE_CROSS_SCAN_SPEEDUPS:
        out = _cross_scan_speedups.unpack_uint32_bits(payload, int(bit_width), int(count))
        return np.asarray(out, dtype=np.uint32)
    if bit_width == 32:
        return np.frombuffer(payload, dtype=np.uint32).copy()[:count]
    mask = np.uint64((1 << bit_width) - 1)
    raw = np.frombuffer(payload, dtype=np.uint8)
    padded = np.zeros((((len(raw) + 7) // 8) + 1) * 8, dtype=np.uint8)
    padded[: len(raw)] = raw
    words = padded.view(np.uint64)
    out = np.empty(count, dtype=np.uint32)
    bit_width_u64 = np.uint64(bit_width)

    for start in range(0, count, BITPACK_VECTOR_CHUNK):
        stop = min(count, start + BITPACK_VECTOR_CHUNK)
        local_count = stop - start
        bit_pos = (np.arange(local_count, dtype=np.uint64) + np.uint64(start)) * bit_width_u64
        word_idx = (bit_pos >> 6).astype(np.int64, copy=False)
        shift = bit_pos & np.uint64(63)
        values = words[word_idx] >> shift
        spill_mask = (shift + bit_width_u64) > np.uint64(64)
        if np.any(spill_mask):
            values = values.copy()
            values[spill_mask] |= words[word_idx[spill_mask] + 1] << (np.uint64(64) - shift[spill_mask])
        out[start:stop] = np.asarray(values & mask, dtype=np.uint32)

    return out


def _transform_uint32_for_compression(arr: np.ndarray, transform: str) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.uint32)
    if transform == "raw":
        return arr
    signed = arr.astype(np.int64)
    delta1 = np.diff(signed, prepend=0)
    if transform == "delta":
        return _zigzag_encode_int32(delta1)
    if transform == "delta2":
        delta2 = np.diff(delta1, prepend=0)
        return _zigzag_encode_int32(delta2)
    raise ValueError(f"Unsupported uint32 transform: {transform}")


def _inverse_uint32_transform(arr: np.ndarray, transform: str) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.uint32)
    if transform == "raw":
        return arr
    if transform == "delta":
        signed = _zigzag_decode_uint32(arr).astype(np.int64)
        return np.cumsum(signed, dtype=np.int64).astype(np.uint32)
    if transform == "delta2":
        signed = _zigzag_decode_uint32(arr).astype(np.int64)
        delta1 = np.cumsum(signed, dtype=np.int64)
        return np.cumsum(delta1, dtype=np.int64).astype(np.uint32)
    raise ValueError(f"Unsupported uint32 transform: {transform}")


def _estimate_uint32_bitpack_bytes(arr: np.ndarray) -> int:
    arr = np.asarray(arr, dtype=np.uint32)
    if len(arr) == 0:
        return 0
    if HAVE_CROSS_SCAN_SPEEDUPS and arr.ndim == 1:
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr, dtype=np.uint32)
        return int(_cross_scan_speedups.estimate_uint32_bitpack_bytes(arr))
    max_val = int(arr.max())
    bit_width = max(1, max_val.bit_length())
    return 8 + ((bit_width * len(arr) + 7) // 8)


def _estimate_signed_residual_bytes(arr: np.ndarray) -> int:
    arr = np.asarray(arr, dtype=np.int32)
    if len(arr) == 0:
        return 0
    if HAVE_CROSS_SCAN_SPEEDUPS and arr.ndim == 1:
        if not arr.flags.c_contiguous:
            arr = np.ascontiguousarray(arr, dtype=np.int32)
        return int(_cross_scan_speedups.estimate_signed_residual_bytes(arr))
    zigzag = _zigzag_encode_int32(arr)
    dense = _estimate_uint32_bitpack_bytes(zigzag)
    nonzero_idx = np.flatnonzero(arr).astype(np.uint32)
    if len(nonzero_idx) == 0:
        return 8
    nonzero_vals = _zigzag_encode_int32(arr[nonzero_idx].astype(np.int32))
    sparse = (
        12
        + _estimate_uint32_bitpack_bytes(nonzero_idx)
        + _estimate_uint32_bitpack_bytes(nonzero_vals)
    )
    return min(dense, sparse)


def _estimate_uint64_raw_bytes(arr: np.ndarray) -> int:
    arr = np.asarray(arr, dtype=np.uint64)
    if len(arr) == 0:
        return 0
    return 8 + 8 * len(arr)


def _split_uint64_anchor_overflow(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    arr64 = np.asarray(arr, dtype=np.uint64)
    if len(arr64) == 0:
        return EMPTY_UINT32, EMPTY_UINT32, EMPTY_UINT64
    overflow_mask = arr64 > UINT32_MAX
    main = arr64.astype(np.uint32, copy=True)
    if not np.any(overflow_mask):
        return main, EMPTY_UINT32, EMPTY_UINT64
    overflow_idx = np.flatnonzero(overflow_mask).astype(np.uint32)
    overflow_vals = arr64[overflow_mask].astype(np.uint64, copy=False)
    main[overflow_mask] = 0
    return main, overflow_idx, overflow_vals


def _restore_uint64_anchor_overflow(
    main: np.ndarray,
    overflow_idx: np.ndarray,
    overflow_vals: np.ndarray,
) -> np.ndarray:
    out = np.asarray(main, dtype=np.uint32).astype(np.uint64)
    if len(overflow_idx):
        out[np.asarray(overflow_idx, dtype=np.int64)] = np.asarray(overflow_vals, dtype=np.uint64)
    return out


def _validate_quantized_mz_deltas_uint32(mz_q: np.ndarray, *, context: str) -> np.ndarray:
    mz_u64 = np.asarray(mz_q, dtype=np.uint64)
    if len(mz_u64) <= 1:
        return EMPTY_UINT32
    mz_i64 = mz_u64.astype(np.int64, copy=False)
    delta_i64 = np.diff(mz_i64)
    if np.any(delta_i64 < 0):
        raise ValueError(f"{context}: quantized m/z must be nondecreasing within each spectrum")
    if np.any(delta_i64.astype(np.uint64) > UINT32_MAX):
        raise ValueError(
            f"{context}: raw m/z delta exceeds uint32 range; strict overflow sidecar only covers anchors"
        )
    return delta_i64.astype(np.uint32, copy=False)


def _estimate_uint64_anchor_bytes(arr: np.ndarray) -> int:
    main, overflow_idx, overflow_vals = _split_uint64_anchor_overflow(arr)
    total = _estimate_uint32_bitpack_bytes(main)
    if len(overflow_idx):
        total += _estimate_uint32_bitpack_bytes(overflow_idx)
    if len(overflow_vals):
        total += _estimate_uint64_raw_bytes(overflow_vals)
    return total


def _estimate_raw_mz_local_bytes(mz_q: np.ndarray) -> int:
    mz_q = np.asarray(mz_q, dtype=np.uint64)
    if len(mz_q) == 0:
        return 4
    if HAVE_CROSS_SCAN_SPEEDUPS and mz_q.ndim == 1 and int(np.max(mz_q, initial=0)) <= int(UINT32_MAX):
        mz_q32 = np.asarray(mz_q, dtype=np.uint32)
        if not mz_q32.flags.c_contiguous:
            mz_q32 = np.ascontiguousarray(mz_q32, dtype=np.uint32)
        return int(_cross_scan_speedups.estimate_raw_mz_local_bytes(mz_q32))
    first_cost = _estimate_uint64_anchor_bytes(mz_q[:1])
    if len(mz_q) == 1:
        return 4 + first_cost
    deltas = _validate_quantized_mz_deltas_uint32(mz_q, context="estimate_raw_mz_local_bytes")
    return 4 + first_cost + _estimate_uint32_bitpack_bytes(deltas)


def _estimate_int64_raw_bytes(arr: np.ndarray) -> int:
    arr = np.asarray(arr, dtype=np.int64)
    if len(arr) == 0:
        return 0
    return 8 + 8 * len(arr)


def _estimate_int64_main_bytes(arr: np.ndarray) -> int:
    arr64 = np.asarray(arr, dtype=np.int64)
    if len(arr64) == 0:
        return 0
    if HAVE_CROSS_SCAN_SPEEDUPS and arr64.ndim == 1:
        if not arr64.flags.c_contiguous:
            arr64 = np.ascontiguousarray(arr64, dtype=np.int64)
        return int(_cross_scan_speedups.estimate_int64_main_bytes(arr64))
    code_main, overflow_idx, overflow_vals = _encode_signed_int64_to_int32_main(arr64)
    return (
        _estimate_signed_residual_bytes(code_main)
        + _estimate_uint32_bitpack_bytes(overflow_idx)
        + _estimate_int64_raw_bytes(overflow_vals)
    )


def _encode_uint32_stream(
    arr: np.ndarray,
    backend: str,
    *,
    use_pfor: bool,
    pfor_codec: str,
    zero_rle: bool,
) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.uint32)
    if zero_rle and np.any(arr == UINT32_MAX_INT):
        zero_rle = False
    transformed = _zero_rle_encode_uint32(arr) if zero_rle else arr

    meta = {
        "count": int(len(arr)),
        "zero_rle": bool(zero_rle),
        "use_pfor": bool(use_pfor),
        "outer_backend": backend,
    }

    if len(transformed) == 0:
        meta["encoded_count"] = 0
        return b"", meta

    if use_pfor:
        _require_pfor()
        codec = pyfastpfor.getCodec(pfor_codec)
        out = np.zeros(len(transformed) * 2 + 1024, dtype=np.uint32)
        encoded_words = int(codec.encodeArray(transformed, len(transformed), out, len(out)))
        encoded = out[:encoded_words].copy()
        payload = compress(encoded.tobytes(), backend)
        meta.update(
            {
                "encoded_count": int(len(transformed)),
                "encoded_words": encoded_words,
                "pfor_codec": pfor_codec,
            }
        )
        return payload, meta

    payload = compress(transformed.tobytes(), backend)
    meta["encoded_count"] = int(len(transformed))
    return payload, meta


def _sample_uint32_for_search(arr: np.ndarray, sample_count: int) -> np.ndarray:
    arr = np.asarray(arr, dtype=np.uint32)
    if sample_count <= 0 or len(arr) <= sample_count:
        return arr
    window_count = min(4, max(1, sample_count // 2048))
    window_len = max(256, sample_count // window_count)
    if window_len >= len(arr):
        return arr
    starts = np.linspace(0, len(arr) - window_len, num=window_count, dtype=np.int64)
    pieces = [arr[int(start):int(start) + window_len] for start in starts]
    return np.concatenate(pieces)


def _encode_uint32_with_strategy(
    arr: np.ndarray,
    *,
    backend: str,
    use_pfor: bool,
    pfor_codec: str,
    zero_rle: bool,
    storage_kind: str,
) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.uint32)
    if zero_rle and np.any(arr == UINT32_MAX_INT):
        zero_rle = False
    if storage_kind == "plain":
        return _encode_uint32_stream(
            arr,
            backend,
            use_pfor=use_pfor,
            pfor_codec=pfor_codec,
            zero_rle=zero_rle,
        )
    if storage_kind != "bitpack32":
        raise ValueError(f"Unsupported uint32 storage_kind: {storage_kind}")

    transformed = _zero_rle_encode_uint32(arr) if zero_rle else arr
    meta = {
        "count": int(len(arr)),
        "encoded_count": int(len(transformed)),
        "zero_rle": bool(zero_rle),
        "use_pfor": False,
        "outer_backend": backend,
        "storage_kind": "bitpack32",
        "bit_width": 1,
    }
    if len(transformed) == 0:
        return b"", meta
    bit_width = max(1, int(int(transformed.max(initial=0)).bit_length()))
    packed = _pack_uint32_bits(transformed, bit_width)
    meta["bit_width"] = int(bit_width)
    return compress(packed, backend), meta


def _enumerate_uint32_strategies(
    *,
    backend_candidates: List[str],
    default_backend: str,
    use_pfor: bool,
    zero_rle: bool,
    try_pfor: bool,
    try_zero_rle: bool,
    allow_bitpack: bool,
) -> List[Dict]:
    effective_use_pfor = bool(use_pfor) and HAVE_PYFASTPFOR
    pfor_options = [effective_use_pfor]
    if try_pfor and HAVE_PYFASTPFOR:
        pfor_options = sorted(set([False, True]))

    zero_rle_options = [bool(zero_rle)]
    if try_zero_rle:
        zero_rle_options = sorted(set([False, True]))

    out = []
    seen = set()
    for candidate_backend in backend_candidates or [default_backend]:
        for candidate_pfor in pfor_options:
            if candidate_pfor and not HAVE_PYFASTPFOR:
                continue
            for candidate_zero_rle in zero_rle_options:
                key = (candidate_backend, candidate_pfor, candidate_zero_rle, "plain")
                if key not in seen:
                    seen.add(key)
                    out.append(
                        {
                            "backend": candidate_backend,
                            "use_pfor": candidate_pfor,
                            "zero_rle": candidate_zero_rle,
                            "storage_kind": "plain",
                        }
                    )
                if allow_bitpack and not candidate_pfor:
                    key = (candidate_backend, False, candidate_zero_rle, "bitpack32")
                    if key not in seen:
                        seen.add(key)
                        out.append(
                            {
                                "backend": candidate_backend,
                                "use_pfor": False,
                                "zero_rle": candidate_zero_rle,
                                "storage_kind": "bitpack32",
                            }
                        )
    return out


def _encode_uint32_stream_best(
    arr: np.ndarray,
    backend: str,
    *,
    use_pfor: bool,
    pfor_codec: str,
    zero_rle: bool,
    backend_candidates: List[str],
    try_pfor: bool,
    try_zero_rle: bool,
    allow_bitpack: bool = False,
    search_mode: str = "full",
    sample_count: int = 16384,
) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.uint32)
    if search_mode not in {"full", "converged"}:
        raise ValueError(f"Unsupported search_mode: {search_mode}")
    if np.any(arr == UINT32_MAX_INT):
        zero_rle = False
        try_zero_rle = False
    candidates = _enumerate_uint32_strategies(
        backend_candidates=backend_candidates,
        default_backend=backend,
        use_pfor=use_pfor,
        zero_rle=zero_rle,
        try_pfor=try_pfor,
        try_zero_rle=try_zero_rle,
        allow_bitpack=allow_bitpack,
    )
    if not candidates:
        return _encode_uint32_stream(
            arr,
            backend,
            use_pfor=False,
            pfor_codec=pfor_codec,
            zero_rle=False,
        )
    preferred_backend = backend_candidates[0] if backend_candidates else backend
    if len(candidates) == 1:
        candidate = candidates[0]
        return _encode_uint32_with_strategy(
            arr,
            backend=candidate["backend"],
            use_pfor=candidate["use_pfor"],
            pfor_codec=pfor_codec,
            zero_rle=candidate["zero_rle"],
            storage_kind=candidate["storage_kind"],
        )

    best_candidate = None
    best_score = None
    best_payload = None
    best_meta = None
    search_arr = arr
    if search_mode == "converged":
        search_arr = _sample_uint32_for_search(arr, sample_count)
    search_uses_full_input = len(search_arr) == len(arr)

    for candidate in candidates:
        payload, payload_meta = _encode_uint32_with_strategy(
            search_arr,
            backend=candidate["backend"],
            use_pfor=candidate["use_pfor"],
            pfor_codec=pfor_codec,
            zero_rle=candidate["zero_rle"],
            storage_kind=candidate["storage_kind"],
        )
        score = (
            len(payload),
            0 if candidate["backend"] == preferred_backend else 1,
            0 if candidate["use_pfor"] == use_pfor else 1,
            0 if candidate["zero_rle"] == zero_rle else 1,
            0 if candidate["storage_kind"] == "plain" else 1,
        )
        if best_score is None or score < best_score:
            best_score = score
            best_candidate = candidate
            if search_uses_full_input:
                best_payload = payload
                best_meta = payload_meta

    if search_uses_full_input and best_payload is not None and best_meta is not None:
        return best_payload, best_meta

    return _encode_uint32_with_strategy(
        arr,
        backend=best_candidate["backend"],
        use_pfor=best_candidate["use_pfor"],
        pfor_codec=pfor_codec,
        zero_rle=best_candidate["zero_rle"],
        storage_kind=best_candidate["storage_kind"],
    )


def _score_uint32_candidate(
    payload_len: int,
    candidate: Dict,
    *,
    preferred_backend: str,
    default_use_pfor: bool,
    default_zero_rle: bool,
) -> Tuple[int, int, int, int, int]:
    return (
        int(payload_len),
        0 if candidate["backend"] == preferred_backend else 1,
        0 if candidate["use_pfor"] == default_use_pfor else 1,
        0 if candidate["zero_rle"] == default_zero_rle else 1,
        0 if candidate["storage_kind"] == "plain" else 1,
    )


def _encode_uint32_stream_topk_from_sample(
    arr: np.ndarray,
    backend: str,
    *,
    use_pfor: bool,
    pfor_codec: str,
    zero_rle: bool,
    backend_candidates: List[str],
    try_pfor: bool,
    try_zero_rle: bool,
    allow_bitpack: bool = False,
    sample_count: int = 16384,
    top_k: int = 2,
) -> Tuple[bytes, Dict]:
    """Pick top-k strategies on a deterministic sample, then full-encode only those.

    This keeps the on-disk stream format identical to the existing uint32
    strategies. It only reduces sample-selection risk for large metadata/index
    streams where top-1 sampling can occasionally pick a slightly worse full
    strategy.
    """
    arr = np.asarray(arr, dtype=np.uint32)
    if np.any(arr == UINT32_MAX_INT):
        zero_rle = False
        try_zero_rle = False
    candidates = _enumerate_uint32_strategies(
        backend_candidates=backend_candidates,
        default_backend=backend,
        use_pfor=use_pfor,
        zero_rle=zero_rle,
        try_pfor=try_pfor,
        try_zero_rle=try_zero_rle,
        allow_bitpack=allow_bitpack,
    )
    if len(candidates) <= 1 or len(arr) <= sample_count:
        return _encode_uint32_stream_best(
            arr,
            backend,
            use_pfor=use_pfor,
            pfor_codec=pfor_codec,
            zero_rle=zero_rle,
            backend_candidates=backend_candidates,
            try_pfor=try_pfor,
            try_zero_rle=try_zero_rle,
            allow_bitpack=allow_bitpack,
            search_mode="full",
            sample_count=sample_count,
        )

    preferred_backend = backend_candidates[0] if backend_candidates else backend
    sample = _sample_uint32_for_search(arr, sample_count)
    ranked = []
    for candidate in candidates:
        payload, _ = _encode_uint32_with_strategy(
            sample,
            backend=candidate["backend"],
            use_pfor=candidate["use_pfor"],
            pfor_codec=pfor_codec,
            zero_rle=candidate["zero_rle"],
            storage_kind=candidate["storage_kind"],
        )
        ranked.append(
            (
                _score_uint32_candidate(
                    len(payload),
                    candidate,
                    preferred_backend=preferred_backend,
                    default_use_pfor=use_pfor,
                    default_zero_rle=zero_rle,
                ),
                candidate,
            )
        )
    ranked.sort(key=lambda item: item[0])

    best_payload = None
    best_meta = None
    best_score = None
    seen = set()
    for _, candidate in ranked[:max(1, int(top_k))]:
        key = (
            candidate["backend"],
            bool(candidate["use_pfor"]),
            bool(candidate["zero_rle"]),
            candidate["storage_kind"],
        )
        if key in seen:
            continue
        seen.add(key)
        payload, payload_meta = _encode_uint32_with_strategy(
            arr,
            backend=candidate["backend"],
            use_pfor=candidate["use_pfor"],
            pfor_codec=pfor_codec,
            zero_rle=candidate["zero_rle"],
            storage_kind=candidate["storage_kind"],
        )
        score = _score_uint32_candidate(
            len(payload),
            candidate,
            preferred_backend=preferred_backend,
            default_use_pfor=use_pfor,
            default_zero_rle=zero_rle,
        )
        if best_score is None or score < best_score:
            best_score = score
            best_payload = payload
            best_meta = payload_meta
    if best_payload is None or best_meta is None:
        raise ValueError("No uint32 strategy candidates were available for top-k search")
    return best_payload, best_meta


def _encode_uint32_stream_best_chunked(
    arr: np.ndarray,
    backend: str,
    *,
    chunk_items: int,
    max_workers: int,
    use_pfor: bool,
    pfor_codec: str,
    zero_rle: bool,
    backend_candidates: List[str],
    try_pfor: bool,
    try_zero_rle: bool,
    allow_bitpack: bool = False,
    search_mode: str = "full",
    sample_count: int = 16384,
) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.uint32)
    chunk_items = max(1, int(chunk_items))
    if len(arr) <= chunk_items:
        return _encode_uint32_stream_best(
            arr,
            backend,
            use_pfor=use_pfor,
            pfor_codec=pfor_codec,
            zero_rle=zero_rle,
            backend_candidates=backend_candidates,
            try_pfor=try_pfor,
            try_zero_rle=try_zero_rle,
            allow_bitpack=allow_bitpack,
            search_mode=search_mode,
            sample_count=sample_count,
        )

    ranges = [(start, min(start + chunk_items, len(arr))) for start in range(0, len(arr), chunk_items)]

    def _encode_range(item):
        start, stop = item
        payload, child_meta = _encode_uint32_stream_best(
            arr[start:stop],
            backend,
            use_pfor=use_pfor,
            pfor_codec=pfor_codec,
            zero_rle=zero_rle,
            backend_candidates=backend_candidates,
            try_pfor=try_pfor,
            try_zero_rle=try_zero_rle,
            allow_bitpack=allow_bitpack,
            search_mode=search_mode,
            sample_count=sample_count,
        )
        return payload, child_meta

    if max_workers > 1 and len(ranges) > 1:
        with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
            encoded = list(executor.map(_encode_range, ranges))
    else:
        encoded = [_encode_range(item) for item in ranges]

    payloads = [item[0] for item in encoded]
    child_metas = [item[1] for item in encoded]
    return b"".join(payloads), {
        "count": int(len(arr)),
        "storage_kind": "chunked_uint32",
        "chunk_items": int(chunk_items),
        "chunk_count": int(len(payloads)),
        "chunk_payload_sizes": [int(len(payload)) for payload in payloads],
        "chunk_metas": child_metas,
    }


def _decode_uint32_stream(payload: bytes, meta: Dict, max_workers: int = 1) -> np.ndarray:
    count = int(meta["count"])
    if count == 0:
        return np.array([], dtype=np.uint32)

    if meta.get("storage_kind") == "chunked_uint32":
        sizes = [int(x) for x in meta.get("chunk_payload_sizes", [])]
        child_metas = list(meta.get("chunk_metas", []))
        if len(sizes) != len(child_metas):
            raise ValueError("Chunked uint32 stream metadata size mismatch")
        parts = []
        offset = 0
        for size in sizes:
            parts.append(payload[offset:offset + size])
            offset += size
        if offset != len(payload):
            raise ValueError(f"Chunked uint32 payload size mismatch: {offset} vs {len(payload)}")

        def _decode_part(item):
            part, child_meta = item
            return _decode_uint32_stream(part, child_meta, max_workers=1)

        if max_workers > 1 and len(parts) > 1:
            with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
                arrays = list(executor.map(_decode_part, zip(parts, child_metas)))
        else:
            arrays = [_decode_part(item) for item in zip(parts, child_metas)]
        decoded = np.concatenate(arrays).astype(np.uint32, copy=False) if arrays else np.array([], dtype=np.uint32)
        if len(decoded) != count:
            raise ValueError(f"Decoded chunked uint32 stream length mismatch: {len(decoded)} vs {count}")
        return decoded

    raw = decompress(payload, meta["outer_backend"])
    if meta.get("storage_kind") == "bitpack32":
        decoded = _unpack_uint32_bits(raw, int(meta["bit_width"]), int(meta["encoded_count"]))
    elif meta.get("use_pfor"):
        _require_pfor()
        codec = pyfastpfor.getCodec(meta["pfor_codec"])
        encoded = np.frombuffer(raw, dtype=np.uint32)
        decoded = np.zeros(int(meta["encoded_count"]), dtype=np.uint32)
        codec.decodeArray(encoded, len(encoded), decoded, len(decoded))
    else:
        decoded = np.frombuffer(raw, dtype=np.uint32).copy()

    if meta.get("zero_rle"):
        decoded = _zero_rle_decode_uint32(decoded)

    if len(decoded) != count:
        raise ValueError(f"Decoded uint32 stream length mismatch: {len(decoded)} vs {count}")
    return decoded


def _pack_sparse_l0_delta_payload(position_payload: bytes, value_payload: bytes) -> bytes:
    return (
        SPARSE_L0_DELTA_HEADER.pack(
            SPARSE_L0_DELTA_MAGIC,
            int(len(position_payload)),
            int(len(value_payload)),
        )
        + position_payload
        + value_payload
    )


def _unpack_sparse_l0_delta_payload(payload: bytes) -> Tuple[bytes, bytes]:
    header_size = SPARSE_L0_DELTA_HEADER.size
    if len(payload) < header_size:
        raise ValueError("Sparse-L0 delta payload is shorter than its header")
    magic, position_size, value_size = SPARSE_L0_DELTA_HEADER.unpack(payload[:header_size])
    if magic != SPARSE_L0_DELTA_MAGIC:
        raise ValueError("Sparse-L0 delta payload magic mismatch")
    position_size = int(position_size)
    value_size = int(value_size)
    expected_size = header_size + position_size + value_size
    if expected_size != len(payload):
        raise ValueError(
            f"Sparse-L0 delta payload size mismatch: expected {expected_size}, got {len(payload)}"
        )
    position_start = header_size
    value_start = position_start + position_size
    return payload[position_start:value_start], payload[value_start:]


def _estimate_json_meta_bytes(meta: Dict) -> int:
    return len(json.dumps(meta, default=str).encode("utf-8"))


def _encode_small_uint_stream(
    arr: np.ndarray,
    backend: str,
    *,
    bits: int,
    backend_candidates: List[str],
) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.uint8)
    packed = _pack_small_uint(arr, bits)
    best_payload = None
    best_backend = backend
    best_len = None
    for candidate_backend in backend_candidates or [backend]:
        payload = compress(packed, candidate_backend)
        score = (len(payload), 0 if candidate_backend == backend else 1)
        if best_len is None or score < best_len:
            best_payload = payload
            best_backend = candidate_backend
            best_len = score
    return best_payload, {
        "count": int(len(arr)),
        "bits": int(bits),
        "outer_backend": best_backend,
    }


def _decode_small_uint_stream(payload: bytes, meta: Dict) -> np.ndarray:
    count = int(meta["count"])
    if count == 0:
        return np.array([], dtype=np.uint8)
    raw = decompress(payload, meta["outer_backend"])
    return _unpack_small_uint(raw, int(meta["bits"]), count)


def _encode_uint8_stream(arr: np.ndarray, backend: str) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.uint8)
    return compress(arr.tobytes(), backend), {"count": int(len(arr)), "outer_backend": backend}


def _decode_uint8_stream(payload: bytes, meta: Dict) -> np.ndarray:
    if int(meta["count"]) == 0:
        return np.array([], dtype=np.uint8)
    raw = decompress(payload, meta["outer_backend"])
    arr = np.frombuffer(raw, dtype=np.uint8).copy()
    if len(arr) != int(meta["count"]):
        raise ValueError(f"Decoded uint8 stream length mismatch: {len(arr)} vs {meta['count']}")
    return arr


def _encode_float64_planes(arr: np.ndarray, backend: str, max_workers: int = 1) -> Tuple[bytes, Dict]:
    arr = np.asarray(arr, dtype=np.float64)
    planes = byteplane_split(arr)
    compressed_planes = compress_many(planes, backend, max_workers=max_workers)
    return b"".join(compressed_planes), {
        "count": int(len(arr)),
        "outer_backend": backend,
        "plane_sizes": [len(x) for x in compressed_planes],
    }


def _decode_float64_planes(payload: bytes, meta: Dict, max_workers: int = 1) -> np.ndarray:
    if int(meta["count"]) == 0:
        return np.array([], dtype=np.float64)
    parts = []
    offset = 0
    for size in meta["plane_sizes"]:
        part = payload[offset:offset + size]
        parts.append(part)
        offset += size
    planes = decompress_many(parts, meta["outer_backend"], max_workers=max_workers)
    raw = byteplane_merge(planes, dtype=np.float64)
    arr = np.frombuffer(raw, dtype=np.float64).copy()
    if len(arr) != int(meta["count"]):
        raise ValueError(f"Decoded float64 stream length mismatch: {len(arr)} vs {meta['count']}")
    return arr


def _szdpd_encode_to_int32_with_precision(intensity_array: np.ndarray, precision: int) -> Tuple[np.ndarray, Dict]:
    arr = np.asarray(intensity_array, dtype=np.float64)
    if np.any(~np.isfinite(arr)):
        raise ValueError("Cannot SZDPD-encode intensity array with NaN or infinite values")
    if np.any(arr < 0):
        raise ValueError("Cannot SZDPD-encode negative intensity values")
    int32_max = np.iinfo(np.int32).max

    scaled = arr * precision
    encoded = np.zeros(len(arr), dtype=np.int32)
    normal_mask = scaled <= int32_max
    overflow_mask = ~normal_mask

    encoded[normal_mask] = np.round(scaled[normal_mask]).astype(np.int32)
    if np.any(overflow_mask):
        log_vals = np.log2(scaled[overflow_mask]) * 100000
        encoded[overflow_mask] = -np.round(log_vals).astype(np.int32)

    return encoded, {
        "count": int(len(arr)),
        "precision": int(precision),
        "overflow_count": int(np.sum(overflow_mask)),
    }


def _szdpd_encode_to_int32(intensity_array: np.ndarray) -> Tuple[np.ndarray, Dict]:
    arr = np.asarray(intensity_array, dtype=np.float64)
    precision = baseline_ms1_codec._szdpd_detect_precision(arr)
    return _szdpd_encode_to_int32_with_precision(arr, precision)


def _szdpd_decode_from_int32(encoded: np.ndarray, meta: Dict) -> np.ndarray:
    encoded = np.asarray(encoded, dtype=np.int32).astype(np.float64)
    precision = float(meta["precision"])

    out = np.zeros(len(encoded), dtype=np.float64)
    neg = encoded < 0
    pos = ~neg
    out[pos] = encoded[pos] / precision
    if np.any(neg):
        out[neg] = np.power(2.0, -encoded[neg] / 100000.0) / precision
    return out


def _equal_fidelity_exact_scaled_int64(intensity_array: np.ndarray, precision: int) -> np.ndarray:
    arr = np.asarray(intensity_array, dtype=np.float64)
    return np.round(arr * float(precision)).astype(np.int64)


def _decode_equal_fidelity_codes_to_float64(
    codes: np.ndarray,
    *,
    precision: int,
    overflow_mode: str,
) -> np.ndarray:
    codes64 = np.asarray(codes, dtype=np.int64)
    if overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT:
        return codes64.astype(np.float64) / float(precision)
    if overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_LOG2_NEGATIVE_CODE:
        return _szdpd_decode_from_int32(
            codes64.astype(np.int32),
            {"precision": int(precision)},
        )
    raise ValueError(f"Unsupported equal-fidelity overflow mode: {overflow_mode}")


def _signed_residual_encode_to_int32(intensity_array: np.ndarray) -> Tuple[np.ndarray, Dict, np.ndarray, np.ndarray]:
    arr = np.asarray(intensity_array, dtype=np.float64)
    precision = baseline_ms1_codec._szdpd_detect_precision(arr)
    scaled = np.round(arr * precision).astype(np.float64)
    int32_max = float(np.iinfo(np.int32).max)
    overflow_mask = np.abs(scaled) > int32_max

    encoded64 = scaled.astype(np.int64)
    overflow_idx = np.where(overflow_mask)[0].astype(np.uint32)
    overflow_vals = encoded64[overflow_mask].astype(np.int64)
    if np.any(overflow_mask):
        encoded64[overflow_mask] = 0

    return encoded64.astype(np.int32), {
        "count": int(len(arr)),
        "precision": int(precision),
        "overflow_count": int(np.sum(overflow_mask)),
    }, overflow_idx, overflow_vals


def _signed_residual_decode_from_int32(encoded: np.ndarray, meta: Dict, overflow_idx: np.ndarray, overflow_vals: np.ndarray) -> np.ndarray:
    encoded64 = np.asarray(encoded, dtype=np.int32).astype(np.int64)
    if len(overflow_idx):
        encoded64[np.asarray(overflow_idx, dtype=np.int64)] = np.asarray(overflow_vals, dtype=np.int64)
    return encoded64.astype(np.float64) / float(meta["precision"])


def _encode_signed_int64_to_int32_main(arr: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    arr64 = np.asarray(arr, dtype=np.int64)
    if len(arr64) == 0:
        return np.array([], dtype=np.int32), EMPTY_UINT32, EMPTY_INT64
    if HAVE_CROSS_SCAN_SPEEDUPS and arr64.ndim == 1:
        if not arr64.flags.c_contiguous:
            arr64 = np.ascontiguousarray(arr64, dtype=np.int64)
        main, overflow_idx, overflow_vals = _cross_scan_speedups.encode_signed_int64_to_int32_main(arr64)
        return (
            np.asarray(main, dtype=np.int32),
            np.asarray(overflow_idx, dtype=np.uint32),
            np.asarray(overflow_vals, dtype=np.int64),
        )
    int32_min = np.iinfo(np.int32).min
    int32_max = np.iinfo(np.int32).max
    overflow_mask = (arr64 < int32_min) | (arr64 > int32_max)
    if not np.any(overflow_mask):
        return arr64.astype(np.int32), EMPTY_UINT32, EMPTY_INT64

    main = arr64.astype(np.int32, copy=True)
    overflow_idx = np.flatnonzero(overflow_mask).astype(np.uint32)
    overflow_vals = arr64[overflow_mask].astype(np.int64)
    main[overflow_mask] = 0
    return main, overflow_idx, overflow_vals


def _decode_signed_int64_from_int32_main(main: np.ndarray, overflow_idx: np.ndarray, overflow_vals: np.ndarray) -> np.ndarray:
    arr64 = np.asarray(main, dtype=np.int32).astype(np.int64)
    if len(overflow_idx):
        arr64[np.asarray(overflow_idx, dtype=np.int64)] = np.asarray(overflow_vals, dtype=np.int64)
    return arr64


def _encode_uint64_values(
    arr: np.ndarray,
    *,
    preferred_backend: str,
    backend_candidates: List[str] | None = None,
) -> Tuple[bytes, Dict]:
    arr64 = np.asarray(arr, dtype=np.uint64)
    if len(arr64) == 0:
        return b"", {"count": 0, "outer_backend": preferred_backend}
    raw = arr64.tobytes()
    best_backend = preferred_backend
    best_payload = compress(raw, preferred_backend)
    for candidate in backend_candidates or []:
        if candidate == best_backend:
            continue
        payload = compress(raw, candidate)
        if len(payload) < len(best_payload):
            best_backend = candidate
            best_payload = payload
    return best_payload, {"count": int(len(arr64)), "outer_backend": best_backend}


def _decode_uint64_values(payload: bytes, meta: Dict) -> np.ndarray:
    count = int(meta.get("count", 0))
    if count == 0:
        return EMPTY_UINT64
    raw = decompress(payload, meta["outer_backend"])
    return np.frombuffer(raw, dtype=np.uint64).copy()


def _pack_segments(header: Dict, segments: Dict[str, bytes]) -> bytes:
    header_json = json.dumps(header, default=str).encode("utf-8")
    parts = [MS1_CROSS_SCAN_MAGIC, struct.pack("<I", len(header_json)), header_json, struct.pack("<I", len(segments))]
    for name, payload in segments.items():
        name_bytes = name.encode("utf-8")
        parts.extend((struct.pack("<H", len(name_bytes)), name_bytes, struct.pack("<Q", len(payload)), payload))
    return b"".join(parts)


def _pack_segments_to_path(header: Dict, segments: Dict[str, bytes], output_path: str | Path) -> int:
    header_json = json.dumps(header, default=str).encode("utf-8")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        handle.write(MS1_CROSS_SCAN_MAGIC)
        handle.write(struct.pack("<I", len(header_json)))
        handle.write(header_json)
        handle.write(struct.pack("<I", len(segments)))
        for name, payload in segments.items():
            name_bytes = name.encode("utf-8")
            handle.write(struct.pack("<H", len(name_bytes)))
            handle.write(name_bytes)
            handle.write(struct.pack("<Q", len(payload)))
            handle.write(payload)
    return int(output_path.stat().st_size)


def _unpack_segments(blob: bytes) -> Tuple[Dict, Dict[str, memoryview]]:
    offset = 0
    blob_len = len(blob)
    blob_view = memoryview(blob)
    magic = blob[offset:offset + len(MS1_CROSS_SCAN_MAGIC)]
    if magic != MS1_CROSS_SCAN_MAGIC:
        raise ValueError("Not a TrackCodec MS1 cross-scan payload")
    offset += len(MS1_CROSS_SCAN_MAGIC)

    if offset + 4 > blob_len:
        raise ValueError("Corrupt TrackCodec MS1 cross-scan payload: missing header length")
    header_len = struct.unpack("<I", blob[offset:offset + 4])[0]
    offset += 4
    if offset + header_len > blob_len:
        raise ValueError(
            f"Corrupt TrackCodec MS1 cross-scan payload: header length {header_len} exceeds payload size {blob_len}"
        )
    header = json.loads(blob[offset:offset + header_len].decode("utf-8"))
    offset += header_len

    if offset + 4 > blob_len:
        raise ValueError("Corrupt TrackCodec MS1 cross-scan payload: missing segment count")
    n_segments = struct.unpack("<I", blob[offset:offset + 4])[0]
    offset += 4
    segments = {}
    for idx in range(n_segments):
        if offset + 2 > blob_len:
            raise ValueError(f"Corrupt TrackCodec MS1 cross-scan payload: missing name length for segment {idx}")
        name_len = struct.unpack("<H", blob[offset:offset + 2])[0]
        offset += 2
        if offset + name_len > blob_len:
            raise ValueError(f"Corrupt TrackCodec MS1 cross-scan payload: truncated name for segment {idx}")
        name = blob[offset:offset + name_len].decode("utf-8")
        offset += name_len
        if offset + 8 > blob_len:
            raise ValueError(f"Corrupt TrackCodec MS1 cross-scan payload: missing data length for segment {name!r}")
        data_len = struct.unpack("<Q", blob[offset:offset + 8])[0]
        offset += 8
        if offset + data_len > blob_len:
            raise ValueError(
                f"Corrupt TrackCodec MS1 cross-scan payload: segment {name!r} ends at {offset + data_len}, payload has {blob_len} bytes"
            )
        segments[name] = blob_view[offset:offset + data_len]
        offset += data_len
    if offset != blob_len:
        raise ValueError(
            f"Corrupt TrackCodec MS1 cross-scan payload: trailing bytes after segments ({blob_len - offset})"
        )
    return header, segments


def _emit_progress(progress_callback: Callable[[Dict], None] | None, payload: Dict) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:
        pass


class CrossScanMS1Codec:
    def __init__(
        self,
        mz_precision: int = 6,
        intensity_mode: str = "szdpd_xdelta",
        backend: str = "zstd-9",
        *,
        use_pfor: bool = False,
        pfor_codec: str = "simdfastpfor256",
        pfor_min_count: int = 4096,
        mz_dedup: str = "none",
        zero_rle: bool = False,
        zero_rle_min_count: int = 4096,
        metadata_transform: bool = False,
        adaptive_uint32: bool = False,
        delta_ref_window: int = 1,
        adaptive_intensity_search: bool = True,
        compact_uint32: bool = False,
        enhanced_residual_model: bool = False,
        adaptive_search_mode: str = "full",
        adaptive_search_sample_count: int = 16384,
        padded_delta_max_diff: int = 0,
        adaptive_padded_delta_accounting: bool = False,
        enable_second_order_delta: bool = False,
        enable_cross_track_full_prediction: bool = False,
        cross_track_candidate_limit: int = 4,
        cross_track_min_gain_bytes: int = 0,
        enable_metadata_trim: bool = False,
        enable_stream_backend_tuning: bool = False,
        enable_delta_int16_pack: bool = False,
        extended_metadata_adaptive: bool = False,
        enable_byteaware_delta_ref_selection: bool = False,
        omit_array_starts: bool = False,
        omit_n_points: bool = False,
        omit_island_mz: bool = False,
        preserve_full_scan: bool = False,
        retain_zero_intensity_mz: bool | None = None,
        enable_full_scan_delta_same_prev: bool = False,
        enable_full_scan_delta2: bool = False,
        equal_fidelity_overflow_mode: str = EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT,
        encode_section_workers: int | None = None,
        intensity_stream_workers: int | None = None,
        intensity_decode_workers: int | None = None,
        enable_delta_intensity_chunked_stream: bool = False,
        delta_intensity_chunk_items: int = 16_777_216,
        enable_sparse_l0_delta_intensity: bool = True,
        sparse_l0_delta_min_count: int = 65_536,
        sparse_l0_delta_min_gain_bytes: int = 1,
        sparse_l0_delta_max_nonzero_fraction: float = 0.75,
        full_scan_sidecar_cache_dir: str | Path | None = None,
        enable_runtime_cache: bool = True,
        runtime_cache_padded_max_entries: int = 512,
        runtime_cache_eq_codes_max_entries: int = 2048,
        runtime_cache_eq_delta_est_max_entries: int = 4096,
        runtime_cache_eq_delta2_est_max_entries: int = 1024,
        runtime_cache_eq_full_est_max_entries: int = 2048,
        lazy_track_precompute: bool = True,
        native_track_plan_min_track_len: int = 1,
        disable_native_compact_views: bool = False,
        track_prepare_workers: int = 1,
        track_prepare_lookahead: int | None = None,
    ):
        if mz_precision not in (4, 5, 6):
            raise ValueError(f"mz_precision must be 4/5/6, got {mz_precision}")
        if intensity_mode not in CROSS_SCAN_INTENSITY_MODES:
            raise ValueError(f"Unsupported TrackCodec MS1 mode: {intensity_mode}")
        if mz_dedup not in ("none", "track_unique", "offset_model", "residual_offset_model"):
            raise ValueError(f"Unsupported mz_dedup mode: {mz_dedup}")
        if equal_fidelity_overflow_mode not in SUPPORTED_EQUAL_FIDELITY_OVERFLOW_MODES:
            raise ValueError(
                f"Unsupported equal_fidelity_overflow_mode: {equal_fidelity_overflow_mode}"
            )
        self.mz_precision = mz_precision
        self.intensity_mode = intensity_mode
        self.backend = backend
        self.use_pfor = bool(use_pfor)
        self.pfor_codec = pfor_codec
        self.pfor_min_count = int(pfor_min_count)
        self.mz_dedup = mz_dedup
        self.zero_rle = bool(zero_rle)
        self.zero_rle_min_count = int(zero_rle_min_count)
        self.metadata_transform = bool(metadata_transform)
        self.adaptive_uint32 = bool(adaptive_uint32)
        self.delta_ref_window = max(1, int(delta_ref_window))
        self.adaptive_intensity_search = bool(adaptive_intensity_search)
        self.compact_uint32 = bool(compact_uint32)
        self.enhanced_residual_model = bool(enhanced_residual_model)
        self.padded_delta_max_diff = max(0, int(padded_delta_max_diff))
        self.adaptive_padded_delta_accounting = bool(adaptive_padded_delta_accounting)
        self.enable_second_order_delta = bool(enable_second_order_delta)
        self.enable_cross_track_full_prediction = bool(enable_cross_track_full_prediction)
        self.cross_track_candidate_limit = max(0, int(cross_track_candidate_limit))
        self.cross_track_min_gain_bytes = max(0, int(cross_track_min_gain_bytes))
        self.enable_metadata_trim = bool(enable_metadata_trim)
        self.enable_stream_backend_tuning = bool(enable_stream_backend_tuning)
        self.enable_delta_int16_pack = bool(enable_delta_int16_pack)
        self.extended_metadata_adaptive = bool(extended_metadata_adaptive)
        self.enable_byteaware_delta_ref_selection = bool(enable_byteaware_delta_ref_selection)
        self.omit_array_starts = bool(omit_array_starts)
        self.omit_island_mz = bool(omit_island_mz)
        # Without island m/z, n_points is required to split intensity streams
        # and place islands back onto the full-sidecar grid.
        self.omit_n_points = bool(omit_n_points) and not self.omit_island_mz
        if retain_zero_intensity_mz is None:
            retain_zero_intensity_mz = preserve_full_scan
        self.retain_zero_intensity_mz = bool(retain_zero_intensity_mz)
        self.preserve_full_scan = self.retain_zero_intensity_mz
        self.enable_full_scan_delta_same_prev = bool(enable_full_scan_delta_same_prev)
        self.enable_full_scan_delta2 = bool(enable_full_scan_delta2)
        self.equal_fidelity_overflow_mode = str(equal_fidelity_overflow_mode)
        if encode_section_workers is None:
            # Full-scan m/z sidecar compression is independent from intensity
            # and metadata encoding. Running these sections in parallel keeps
            # the archive/section format and CR unchanged while hiding the
            # expensive brotli sidecar stream behind intensity encoding.
            self.encode_section_workers = 3 if self.preserve_full_scan else 1
        else:
            self.encode_section_workers = max(1, int(encode_section_workers))
        if intensity_stream_workers is None:
            intensity_stream_workers = self.encode_section_workers
        if intensity_decode_workers is None:
            intensity_decode_workers = intensity_stream_workers
        self.intensity_stream_workers = max(1, int(intensity_stream_workers))
        self.intensity_decode_workers = max(1, int(intensity_decode_workers))
        self.enable_delta_intensity_chunked_stream = bool(enable_delta_intensity_chunked_stream)
        self.delta_intensity_chunk_items = max(1024, int(delta_intensity_chunk_items))
        self.enable_sparse_l0_delta_intensity = bool(enable_sparse_l0_delta_intensity)
        self.sparse_l0_delta_min_count = max(1, int(sparse_l0_delta_min_count))
        self.sparse_l0_delta_min_gain_bytes = max(0, int(sparse_l0_delta_min_gain_bytes))
        self.sparse_l0_delta_max_nonzero_fraction = min(
            1.0,
            max(0.0, float(sparse_l0_delta_max_nonzero_fraction)),
        )
        self.full_scan_sidecar_cache_dir = (
            Path(full_scan_sidecar_cache_dir)
            if full_scan_sidecar_cache_dir is not None
            else None
        )
        self.enable_runtime_cache = bool(enable_runtime_cache)
        self.runtime_cache_limits = {
            "padded_float64": max(0, int(runtime_cache_padded_max_entries)),
            "eq_codes": max(0, int(runtime_cache_eq_codes_max_entries)),
            "eq_delta_est": max(0, int(runtime_cache_eq_delta_est_max_entries)),
            "eq_delta2_est": max(0, int(runtime_cache_eq_delta2_est_max_entries)),
            "eq_full_est": max(0, int(runtime_cache_eq_full_est_max_entries)),
        }
        self.lazy_track_precompute = bool(lazy_track_precompute)
        self.native_track_plan_min_track_len = max(1, int(native_track_plan_min_track_len))
        self.disable_native_compact_views = bool(disable_native_compact_views)
        self.track_prepare_workers = max(1, int(track_prepare_workers))
        if track_prepare_lookahead is None:
            track_prepare_lookahead = self.track_prepare_workers
        self.track_prepare_lookahead = max(1, int(track_prepare_lookahead))
        self.delta_length_mode = (
            DELTA_LENGTH_MODE_MAX_REF_CURR
            if self.padded_delta_max_diff > 0
            else DELTA_LENGTH_MODE_CURR_LEN
        )
        if adaptive_search_mode not in {"full", "converged"}:
            raise ValueError(f"Unsupported adaptive_search_mode: {adaptive_search_mode}")
        self.adaptive_search_mode = adaptive_search_mode
        self.adaptive_search_sample_count = max(1024, int(adaptive_search_sample_count))
        candidates = [backend]
        available = set(available_backends())
        if self.adaptive_uint32:
            for candidate in ["brotli", "zstd-19", "zstd-9", "zlib"]:
                if candidate in available and candidate not in candidates:
                    candidates.append(candidate)
        self.backend_candidates = candidates

    def _create_runtime_cache(self) -> Dict | None:
        if not self.enable_runtime_cache:
            return None
        return {
            "_limits": dict(self.runtime_cache_limits),
            "_caches": {},
            "_lock": threading.RLock(),
        }

    def _quantize_mz(self, mz_array: np.ndarray) -> np.ndarray:
        scale = baseline_ms1_codec.MZ_PRECISION_MAP[self.mz_precision]
        mz_int = np.round(np.asarray(mz_array, dtype=np.float64) * scale).astype(np.uint64)
        if len(mz_int) and np.max(mz_int, initial=0) > INT64_MAX:
            raise ValueError("Quantized m/z exceeds int64 working range for strict TrackCodec encoder")
        return mz_int

    def _detect_equal_fidelity_precision_from_tracks(self, tracks, ms1_scans=None, sample_size: int = 1000) -> int:
        if isinstance(tracks, CompactIslandTracks):
            total_points = int(tracks.point_count)
            if len(tracks.islands):
                island_source = tracks.islands
                sample_from_compact_views = False
            else:
                if ms1_scans is None:
                    raise ValueError("CompactIslandTracks without islands requires ms1_scans for precision detection")
                island_source = None
                sample_from_compact_views = True
        else:
            total_points = 0
            for track in tracks:
                for island in track.islands:
                    total_points += int(island.n_points)
            island_source = (
                island
                for track in tracks
                for island in track.islands
            )
            sample_from_compact_views = False
        if total_points <= 0:
            return 1

        sample_count = min(sample_size, total_points)
        target_positions = np.linspace(0, total_points - 1, sample_count, dtype=int)
        sample = np.empty(sample_count, dtype=np.float64)
        sample_pos = 0
        offset = 0
        if sample_from_compact_views:
            for island_idx in range(int(len(tracks.scan_indices))):
                _, _, _, _, arr = _compact_track_scan_slice(tracks, ms1_scans, island_idx)
                next_offset = offset + len(arr)
                while sample_pos < sample_count and target_positions[sample_pos] < next_offset:
                    local_idx = int(target_positions[sample_pos] - offset)
                    sample[sample_pos] = arr[local_idx]
                    sample_pos += 1
                offset = next_offset
                if sample_pos >= sample_count:
                    break
        else:
            for island in island_source:
                arr = np.asarray(island.intensity_array, dtype=np.float64)
                next_offset = offset + len(arr)
                while sample_pos < sample_count and target_positions[sample_pos] < next_offset:
                    local_idx = int(target_positions[sample_pos] - offset)
                    sample[sample_pos] = arr[local_idx]
                    sample_pos += 1
                offset = next_offset
                if sample_pos >= sample_count:
                    break

        if sample_pos != sample_count:
            sample = sample[:sample_pos]
        if baseline_ms1_codec._szdpd_detect_precision(sample) > 1:
            return 10

        # The sample is only a fast positive detector.  If it sees only integer
        # values, scan all source segments once to avoid losing rare fractional
        # intensities in equal-fidelity mode.
        if sample_from_compact_views:
            for island_idx in range(int(len(tracks.scan_indices))):
                _, _, _, _, arr = _compact_track_scan_slice(tracks, ms1_scans, island_idx)
                if _has_fractional_values(arr):
                    return 10
        elif isinstance(tracks, CompactIslandTracks):
            for island in tracks.islands:
                if _has_fractional_values(island.intensity_array):
                    return 10
        else:
            for track in tracks:
                for island in track.islands:
                    if _has_fractional_values(island.intensity_array):
                        return 10
        return 1

    def _prepare_track_representation(
        self,
        source_ctx: _SourceContext,
        *,
        track_index: int,
        track_start: int,
        track_end: int,
        include_array_starts: bool,
        equal_fidelity_precision: int | None,
    ) -> _PreparedTrackRepresentation:
        tracks = source_ctx.tracks
        compact_tracks = source_ctx.compact_tracks
        all_islands = source_ctx.all_islands
        ms1_scans = source_ctx.ms1_scans
        track_island_count = int(track_end - track_start)
        need_native_track_plan = _can_use_native_equal_fidelity_track_planner(
            self,
            equal_fidelity_precision,
        ) and track_island_count >= self.native_track_plan_min_track_len
        precompute_quantized_mz = (not self.lazy_track_precompute) and (not self.omit_island_mz)

        island_indices = np.arange(track_start, track_end, dtype=np.uint32)
        if compact_tracks is None:
            track_islands = tracks[track_index].islands
            scan_indices = np.fromiter(
                (int(island.scan_idx) for island in track_islands),
                dtype=np.uint32,
                count=track_island_count,
            )
            array_starts = (
                np.fromiter(
                    (int(island.array_start_idx) for island in track_islands),
                    dtype=np.uint32,
                    count=track_island_count,
                )
                if include_array_starts
                else None
            )
            n_points = np.fromiter(
                (int(island.n_points) for island in track_islands),
                dtype=np.uint32,
                count=track_island_count,
            )
        elif compact_tracks is not None:
            scan_indices = np.asarray(compact_tracks.scan_indices[track_start:track_end], dtype=np.uint32)
            array_starts = (
                np.asarray(compact_tracks.array_start_indices[track_start:track_end], dtype=np.uint32)
                if include_array_starts
                else None
            )
            n_points = np.asarray(compact_tracks.n_points[track_start:track_end], dtype=np.uint32)

        quantized_mz = None
        if precompute_quantized_mz:
            quantized_mz = [
                self._quantize_mz(
                    _source_mz_view_ctx(source_ctx, int(island_idx))
                )
                for island_idx in island_indices
            ]

        native_int_kinds = None
        native_ref_offsets = None
        native_encoded_lens = None
        native_full_estimates = None
        if need_native_track_plan:
            track_segments_native = [
                (
                    seg
                    if seg.flags.c_contiguous
                    else np.ascontiguousarray(seg, dtype=np.float64)
                )
                for seg in (
                    _source_segment_view_ctx(source_ctx, int(island_idx))
                    for island_idx in island_indices
                )
            ]
            native_track_plan = _cross_scan_speedups.plan_track_intensity_equal_fidelity(
                track_segments_native,
                int(equal_fidelity_precision),
                int(self.delta_ref_window),
                int(self.padded_delta_max_diff),
                bool(self.delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR),
                bool(self.adaptive_padded_delta_accounting),
                bool(self.enable_second_order_delta),
            )
            native_int_kinds = np.asarray(native_track_plan["int_kinds"], dtype=np.uint8)
            native_ref_offsets = np.asarray(native_track_plan["delta_ref_offsets"], dtype=np.uint8)
            native_encoded_lens = np.asarray(native_track_plan["delta_encoded_lengths"], dtype=np.uint32)
            native_full_estimates = np.asarray(native_track_plan["full_estimates"], dtype=np.uint32)

        return _PreparedTrackRepresentation(
            track_index=track_index,
            track_start=track_start,
            track_end=track_end,
            scan_indices=scan_indices,
            array_starts=array_starts,
            n_points=n_points,
            island_indices=island_indices,
            quantized_mz=quantized_mz,
            native_int_kinds=native_int_kinds,
            native_ref_offsets=native_ref_offsets,
            native_encoded_lens=native_encoded_lens,
            native_full_estimates=native_full_estimates,
        )

    def _iter_prepared_track_representations(
        self,
        tracks,
        compact_tracks: CompactIslandTracks | None,
        all_islands,
        ms1_scans,
        *,
        include_array_starts: bool,
        equal_fidelity_precision: int | None,
        source_ctx: _SourceContext | None = None,
    ):
        if source_ctx is None:
            source_ctx = _make_source_context(tracks, compact_tracks, all_islands, ms1_scans)
        n_tracks = int(len(compact_tracks) if compact_tracks is not None else len(tracks))
        if n_tracks <= 0:
            return

        max_workers = min(self.track_prepare_workers, n_tracks)
        if max_workers <= 1:
            for track_index, (track_start, track_end) in enumerate(_iter_track_island_ranges(tracks)):
                yield self._prepare_track_representation(
                    source_ctx,
                    track_index=track_index,
                    track_start=track_start,
                    track_end=track_end,
                    include_array_starts=include_array_starts,
                    equal_fidelity_precision=equal_fidelity_precision,
                )
            return

        track_ranges = list(_iter_track_island_ranges(tracks))
        prefetch_limit = max(max_workers, self.track_prepare_lookahead)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            pending = {}
            next_submit = 0
            next_consume = 0

            while next_consume < len(track_ranges):
                while next_submit < len(track_ranges) and len(pending) < prefetch_limit:
                    track_start, track_end = track_ranges[next_submit]
                    pending[next_submit] = executor.submit(
                        self._prepare_track_representation,
                        source_ctx,
                        track_index=next_submit,
                        track_start=track_start,
                        track_end=track_end,
                        include_array_starts=include_array_starts,
                        equal_fidelity_precision=equal_fidelity_precision,
                    )
                    next_submit += 1
                future = pending.pop(next_consume)
                yield future.result()
                next_consume += 1

    def _estimate_equal_fidelity_full_bytes(self, segment: np.ndarray, precision: int) -> int:
        codes64 = self._equal_fidelity_codes(segment, precision)
        return _estimate_int64_main_bytes(codes64)

    @staticmethod
    def _runtime_segment_key(segment: np.ndarray) -> Tuple[object, ...]:
        arr = np.asarray(segment)
        data_ptr = int(arr.__array_interface__.get("data", (0, False))[0])
        return (
            data_ptr,
            str(arr.dtype),
            int(arr.nbytes),
            tuple(int(dim) for dim in arr.shape),
            tuple(int(stride) for stride in arr.strides),
        )

    def _get_padded_float64_cached(
        self,
        segment: np.ndarray,
        length: int,
        runtime_cache: Dict | None = None,
    ) -> np.ndarray:
        arr = np.asarray(segment, dtype=np.float64)
        if len(arr) >= int(length):
            return arr
        if runtime_cache is None:
            return _pad_float64(arr, int(length))
        key = (*self._runtime_segment_key(arr), int(length))
        cached = _runtime_cache_get(runtime_cache, "padded_float64", key)
        if cached is None:
            cached = _pad_float64(arr, int(length))
            _runtime_cache_put(runtime_cache, "padded_float64", key, cached)
        return cached

    def _get_equal_fidelity_codes_cached(
        self,
        segment: np.ndarray,
        precision: int,
        *,
        encoded_len: int | None = None,
        runtime_cache: Dict | None = None,
    ) -> np.ndarray:
        arr = np.asarray(segment, dtype=np.float64)
        norm_len = int(len(arr) if encoded_len is None else encoded_len)
        if runtime_cache is None:
            return self._equal_fidelity_codes(arr, precision, encoded_len=None if norm_len == len(arr) else norm_len)
        key = (*self._runtime_segment_key(arr), int(norm_len), int(precision))
        cached = _runtime_cache_get(runtime_cache, "eq_codes", key)
        if cached is None:
            cached = self._equal_fidelity_codes(arr, precision, encoded_len=None if norm_len == len(arr) else norm_len)
            _runtime_cache_put(runtime_cache, "eq_codes", key, cached)
        return cached

    def _equal_fidelity_codes(
        self,
        segment: np.ndarray,
        precision: int,
        encoded_len: int | None = None,
    ) -> np.ndarray:
        if encoded_len is None:
            arr = np.asarray(segment, dtype=np.float64)
        else:
            arr = _pad_float64(segment, encoded_len)
        if self.equal_fidelity_overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT:
            return _equal_fidelity_exact_scaled_int64(arr, precision)
        codes, _ = _szdpd_encode_to_int32_with_precision(arr, precision)
        return codes.astype(np.int64)

    def _estimate_equal_fidelity_delta_bytes(
        self,
        curr_segment: np.ndarray,
        ref_segment: np.ndarray,
        encoded_len: int,
        precision: int,
        curr_codes: np.ndarray | None = None,
        ref_codes: np.ndarray | None = None,
        runtime_cache: Dict | None = None,
    ) -> int:
        if runtime_cache is not None:
            key = (
                *self._runtime_segment_key(curr_segment),
                *self._runtime_segment_key(ref_segment),
                int(encoded_len),
                int(precision),
            )
            cached = _runtime_cache_get(runtime_cache, "eq_delta_est", key)
            if cached is not None:
                return int(cached)
        if curr_codes is None:
            curr_codes = self._get_equal_fidelity_codes_cached(
                curr_segment,
                precision,
                encoded_len=encoded_len,
                runtime_cache=runtime_cache,
            )
        if ref_codes is None:
            ref_codes = self._get_equal_fidelity_codes_cached(
                ref_segment,
                precision,
                encoded_len=encoded_len,
                runtime_cache=runtime_cache,
            )
        if HAVE_CROSS_SCAN_SPEEDUPS and curr_codes.ndim == 1 and ref_codes.ndim == 1:
            curr_codes = np.ascontiguousarray(curr_codes, dtype=np.int64)
            ref_codes = np.ascontiguousarray(ref_codes, dtype=np.int64)
            est = int(_cross_scan_speedups.estimate_delta_int64_bytes(curr_codes, ref_codes))
        else:
            delta64 = curr_codes - ref_codes
            est = _estimate_int64_main_bytes(delta64)
        if runtime_cache is not None:
            _runtime_cache_put(runtime_cache, "eq_delta_est", key, int(est))
        return int(est)

    def _estimate_equal_fidelity_delta2_bytes(
        self,
        curr_segment: np.ndarray,
        prev_segment: np.ndarray,
        prev2_segment: np.ndarray,
        precision: int,
        runtime_cache: Dict | None = None,
    ) -> int:
        if runtime_cache is not None:
            key = (
                *self._runtime_segment_key(curr_segment),
                *self._runtime_segment_key(prev_segment),
                *self._runtime_segment_key(prev2_segment),
                int(precision),
            )
            cached = _runtime_cache_get(runtime_cache, "eq_delta2_est", key)
            if cached is not None:
                return int(cached)
        curr_codes = self._get_equal_fidelity_codes_cached(curr_segment, precision, runtime_cache=runtime_cache)
        prev_codes = self._get_equal_fidelity_codes_cached(prev_segment, precision, runtime_cache=runtime_cache)
        prev2_codes = self._get_equal_fidelity_codes_cached(prev2_segment, precision, runtime_cache=runtime_cache)
        if HAVE_CROSS_SCAN_SPEEDUPS and curr_codes.ndim == 1 and prev_codes.ndim == 1 and prev2_codes.ndim == 1:
            curr_codes = np.ascontiguousarray(curr_codes, dtype=np.int64)
            prev_codes = np.ascontiguousarray(prev_codes, dtype=np.int64)
            prev2_codes = np.ascontiguousarray(prev2_codes, dtype=np.int64)
            est = int(_cross_scan_speedups.estimate_delta2_int64_bytes(curr_codes, prev_codes, prev2_codes))
        else:
            delta64 = curr_codes - (2 * prev_codes) + prev2_codes
            est = _estimate_int64_main_bytes(delta64)
        if runtime_cache is not None:
            _runtime_cache_put(runtime_cache, "eq_delta2_est", key, int(est))
        return int(est)

    def _estimate_equal_fidelity_full_bytes_cached(
        self,
        segment: np.ndarray,
        precision: int,
        runtime_cache: Dict | None = None,
    ) -> int:
        if runtime_cache is not None:
            key = (*self._runtime_segment_key(segment), int(precision))
            cached = _runtime_cache_get(runtime_cache, "eq_full_est", key)
            if cached is not None:
                return int(cached)
        codes64 = self._get_equal_fidelity_codes_cached(segment, precision, runtime_cache=runtime_cache)
        est = _estimate_int64_main_bytes(codes64)
        if runtime_cache is not None:
            _runtime_cache_put(runtime_cache, "eq_full_est", key, int(est))
        return int(est)

    def _should_use_pfor(self, stream_name: str, count: int) -> bool:
        if not HAVE_PYFASTPFOR or not self.use_pfor or count < self.pfor_min_count:
            return False
        if self.backend.startswith("zstd"):
            return stream_name in {"delta_intensity", "full_intensity"}
        return stream_name in {"delta_intensity"}

    def _should_use_zero_rle(self, stream_name: str, count: int) -> bool:
        if not self.zero_rle or count < self.zero_rle_min_count:
            return False
        return stream_name in {"delta_intensity"}

    def _candidate_backends(self, stream_name: str) -> List[str]:
        if not self.adaptive_uint32:
            return [self.backend]
        if self.enable_stream_backend_tuning:
            if stream_name in {"mz_model_offsets", "mz_model_steps"}:
                tuned = [name for name in ["zstd-9", "zlib", "brotli"] if name in self.backend_candidates]
                return tuned or self.backend_candidates
            if stream_name in {"mz_residual_values", "mz_model_residual_lengths"}:
                tuned = [name for name in ["brotli", "zstd-9", "zlib"] if name in self.backend_candidates]
                return tuned or self.backend_candidates
        if stream_name in {
            "track_lengths",
            "scan_indices",
            "array_starts",
            "n_points",
            "mz_ref_indices",
            "int_indices",
            "full_lengths",
            "delta_lengths",
            "mz_model_offsets",
            "mz_model_steps",
            "mz_model_residual_lengths",
            "mz_residual_values",
            "mz_lengths",
            "mz_first_values",
            "mz_first_overflow_idx",
            "mz_delta_values",
            "int_ref_offsets",
            "mz_kinds",
            "int_kinds",
        }:
            return self.backend_candidates
        if self.extended_metadata_adaptive and stream_name in {
            "cross_track_ref_indices",
            "delta_ref_offsets",
            "full_scan_scan_indices",
            "full_scan_lengths",
            "full_scan_first_values",
            "full_scan_first_overflow_idx",
            "full_scan_delta_values",
        }:
            return self.backend_candidates
        if stream_name in {"mz_first_overflow_vals", "full_scan_first_overflow_vals"}:
            return self.backend_candidates
        if stream_name in {"full_scan_signed_delta_overflow_vals"}:
            return self.backend_candidates
        if stream_name in {
            "full_intensity",
            "delta_intensity",
            "delta_intensity_l0_positions",
            "delta_intensity_l0_values",
            "delta_overflow_idx",
        }:
            if not self.adaptive_intensity_search:
                return [self.backend]
            return self.backend_candidates[:2] if len(self.backend_candidates) > 1 else self.backend_candidates
        return [self.backend]

    def _candidate_transforms(self, stream_name: str) -> List[str]:
        if not self.metadata_transform:
            return ["raw"]
        if stream_name in {
            "scan_indices",
            "array_starts",
            "mz_ref_indices",
            "int_indices",
            "mz_first_values",
            "mz_first_overflow_idx",
        }:
            return ["raw", "delta", "delta2"]
        if stream_name in {
            "track_lengths",
            "n_points",
            "full_lengths",
            "delta_lengths",
            "mz_model_residual_lengths",
            "mz_lengths",
            "mz_delta_values",
        }:
            return ["raw", "delta", "delta2"]
        if self.extended_metadata_adaptive and stream_name in {
            "cross_track_ref_indices",
            "delta_ref_offsets",
            "full_scan_scan_indices",
            "full_scan_lengths",
            "full_scan_first_values",
            "full_scan_first_overflow_idx",
            "full_scan_delta_values",
        }:
            return ["raw", "delta", "delta2"]
        return ["raw"]

    def _force_full_uint32_search(self, stream_name: str, arr: np.ndarray) -> bool:
        return (
            stream_name in {"full_scan_delta_values"}
            and len(arr) > self.adaptive_search_sample_count
        )

    def _use_top2_uint32_full_search(self, stream_name: str, arr: np.ndarray, transforms: List[str]) -> bool:
        return (
            self.adaptive_search_mode == "converged"
            and self.adaptive_uint32
            and stream_name in UINT32_TOP2_METADATA_STREAMS
            and len(transforms) > 1
            and len(arr) >= UINT32_TOP2_FULL_SEARCH_MIN_COUNT
            and len(arr) > self.adaptive_search_sample_count
        )

    def _store_raw_mz(
        self,
        mz_q: np.ndarray,
        mz_library,
        local_mz_refs: Dict[bytes, int] | None,
    ) -> int:
        if local_mz_refs is not None:
            key = mz_q.tobytes()
            ref = local_mz_refs.get(key)
            if ref is not None:
                return ref
            if isinstance(mz_library, dict):
                ref = int(mz_library["count"])
            else:
                ref = len(mz_library)
            local_mz_refs[key] = ref
        else:
            if isinstance(mz_library, dict):
                ref = int(mz_library["count"])
            else:
                ref = len(mz_library)

        if isinstance(mz_library, dict):
            mz_library["count"] = int(ref) + 1
            mz_library["lengths"].append(int(len(mz_q)))
            if len(mz_q) > 0:
                mz_library["first_values"].append(int(mz_q[0]))
            if len(mz_q) > 1:
                delta = _validate_quantized_mz_deltas_uint32(mz_q, context="store_raw_mz_spill")
                delta.tofile(mz_library["delta_handle"])
                mz_library["delta_count"] += int(len(delta))
            return ref

        mz_library.append(mz_q)
        return ref

    @staticmethod
    def _append_mz_residual_segment(mz_residual_segments, residual: np.ndarray) -> None:
        if isinstance(mz_residual_segments, dict):
            arr = np.asarray(residual, dtype=np.int32)
            arr.tofile(mz_residual_segments["handle"])
            mz_residual_segments["count"] += int(len(arr))
            return
        mz_residual_segments.append(np.asarray(residual, dtype=np.int32))

    def _estimate_mz_model_bytes(self, offset: int, step: int, residual: np.ndarray) -> int:
        offset_cost = _estimate_signed_residual_bytes(np.asarray([offset], dtype=np.int32))
        step_cost = _estimate_signed_residual_bytes(np.asarray([step], dtype=np.int32))
        residual_cost = _estimate_signed_residual_bytes(np.asarray(residual, dtype=np.int32))
        return 12 + offset_cost + step_cost + residual_cost

    @staticmethod
    def _expand_small_int_candidates(candidates: List[int], radius: int = 1) -> List[int]:
        out: List[int] = []
        seen = set()
        for value in candidates:
            base = int(value)
            for delta in range(-radius, radius + 1):
                v = base + delta
                if v in seen:
                    continue
                seen.add(v)
                out.append(v)
        return out

    def _select_best_residual_mz_model(self, prev64: np.ndarray, curr64: np.ndarray) -> Tuple[int, int, np.ndarray, int] | None:
        if len(prev64) != len(curr64) or len(curr64) == 0:
            return None
        if HAVE_CROSS_SCAN_SPEEDUPS and prev64.ndim == 1 and curr64.ndim == 1:
            native = _cross_scan_speedups.best_residual_mz_model(
                np.ascontiguousarray(prev64, dtype=np.int64),
                np.ascontiguousarray(curr64, dtype=np.int64),
                bool(self.enhanced_residual_model),
            )
            if bool(native.get("found", False)):
                return (
                    int(native["offset"]),
                    int(native["step"]),
                    np.asarray(native["residual"], dtype=np.int32),
                    int(native["cost"]),
                )
        diff = curr64 - prev64
        idx = np.arange(len(diff), dtype=np.int64)
        best = None
        seen = set()

        scalar_candidates = [
            int(diff[0]),
            int(np.median(diff)),
            int(np.round(np.mean(diff))),
        ]
        if self.enhanced_residual_model:
            scalar_candidates = self._expand_small_int_candidates(scalar_candidates, radius=1)
        for offset in scalar_candidates:
            key = (offset, 0)
            if key in seen:
                continue
            seen.add(key)
            residual = (diff - offset).astype(np.int32)
            cost = self._estimate_mz_model_bytes(offset, 0, residual)
            candidate = (offset, 0, residual, cost)
            if best is None or candidate[3] < best[3]:
                best = candidate

        if self.enhanced_residual_model and len(diff) >= 3:
            diff_delta = np.diff(diff)
            step_candidates = [
                int(np.round((float(diff[-1]) - float(diff[0])) / max(1, len(diff) - 1))),
                int(np.median(diff_delta)),
                int(np.round(np.mean(diff_delta))),
            ]
            step_candidates = self._expand_small_int_candidates(step_candidates, radius=1)
            for step in step_candidates:
                base_candidates = [
                    int(np.round(np.median(diff - step * idx))),
                    int(np.round(np.mean(diff - step * idx))),
                    int(diff[0]),
                ]
                base_candidates = self._expand_small_int_candidates(base_candidates, radius=1)
                for base_offset in base_candidates:
                    key = (base_offset, step)
                    if key in seen:
                        continue
                    seen.add(key)
                    residual = (diff - (base_offset + step * idx)).astype(np.int32)
                    cost = self._estimate_mz_model_bytes(base_offset, step, residual)
                    candidate = (base_offset, step, residual, cost)
                    if best is None or candidate[3] < best[3]:
                        best = candidate
        return best

    def _select_best_delta_reference(
        self,
        history_segments: List[np.ndarray],
        curr_segment: np.ndarray,
        equal_fidelity_precision: int | None = None,
        runtime_cache: Dict | None = None,
    ) -> Tuple[int, np.ndarray | None, Tuple[int | None, int | None, int | None]]:
        best_offset = 0
        best_ref = None
        best_score = None
        best_info = EMPTY_REF_INFO
        lookback = min(self.delta_ref_window, len(history_segments))
        use_byteaware = (
            self.enable_byteaware_delta_ref_selection
            and self.intensity_mode == "szdpd_xdelta_equalfidelity"
            and equal_fidelity_precision is not None
        )
        full_est = None
        curr_code_cache: Dict[int, np.ndarray] = {}
        if use_byteaware:
            full_est = self._estimate_equal_fidelity_full_bytes_cached(
                curr_segment,
                int(equal_fidelity_precision),
                runtime_cache=runtime_cache,
            )
            if (
                HAVE_CROSS_SCAN_SPEEDUPS
                and self.equal_fidelity_overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT
            ):
                native = _cross_scan_speedups.best_delta_reference_equal_fidelity(
                    np.ascontiguousarray(curr_segment, dtype=np.float64),
                    history_segments,
                    int(equal_fidelity_precision),
                    int(self.delta_ref_window),
                    int(self.padded_delta_max_diff),
                    bool(self.delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR),
                    int(full_est),
                )
                best_offset = int(native["offset"])
                if best_offset > 0:
                    best_ref = history_segments[-best_offset]
                    best_info = (
                        int(native["full_est"]),
                        int(native["delta_est"]) if native["delta_est"] is not None else None,
                        int(native["encoded_len"]) if native["encoded_len"] is not None else None,
                    )
                else:
                    best_info = (int(native["full_est"]), None, None)
                return best_offset, best_ref, best_info
        for ref_offset in range(1, lookback + 1):
            ref_segment = history_segments[-ref_offset]
            length_diff = abs(len(ref_segment) - len(curr_segment))
            if length_diff > self.padded_delta_max_diff:
                continue
            encoded_len = self._delta_encoded_length(len(curr_segment), len(ref_segment))
            if use_byteaware:
                curr_codes = curr_code_cache.get(encoded_len)
                if curr_codes is None:
                    curr_codes = self._get_equal_fidelity_codes_cached(
                        curr_segment,
                        int(equal_fidelity_precision),
                        encoded_len=encoded_len,
                        runtime_cache=runtime_cache,
                    )
                    curr_code_cache[encoded_len] = curr_codes
                delta_est = self._estimate_equal_fidelity_delta_bytes(
                    curr_segment,
                    ref_segment,
                    encoded_len,
                    int(equal_fidelity_precision),
                    curr_codes=curr_codes,
                    runtime_cache=runtime_cache,
                )
                if full_est is not None and delta_est >= full_est:
                    continue
                delta = (
                    self._get_padded_float64_cached(curr_segment, encoded_len, runtime_cache=runtime_cache)
                    - self._get_padded_float64_cached(ref_segment, encoded_len, runtime_cache=runtime_cache)
                )
                score = (int(delta_est), float(np.mean(np.abs(delta))), length_diff, ref_offset)
            else:
                delta = self._get_padded_float64_cached(curr_segment, encoded_len, runtime_cache=runtime_cache) - self._get_padded_float64_cached(ref_segment, encoded_len, runtime_cache=runtime_cache)
                score = (float(np.mean(np.abs(delta))), length_diff, ref_offset)
            if best_score is None or score < best_score:
                best_score = score
                best_offset = ref_offset
                best_ref = ref_segment
                best_info = (
                    int(full_est) if full_est is not None else None,
                    int(delta_est) if use_byteaware else None,
                    int(encoded_len),
                )
        return best_offset, best_ref, best_info

    def _delta_encoded_length(
        self,
        curr_len: int,
        ref_len: int,
        *,
        mode: str | None = None,
        padded_delta_max_diff: int | None = None,
    ) -> int:
        if padded_delta_max_diff is None:
            padded_delta_max_diff = self.padded_delta_max_diff
        if mode is None:
            mode = self.delta_length_mode
        if mode not in SUPPORTED_DELTA_LENGTH_MODES:
            raise ValueError(f"Unsupported delta length mode: {mode}")
        if int(padded_delta_max_diff) > 0 and mode == DELTA_LENGTH_MODE_MAX_REF_CURR:
            return max(int(curr_len), int(ref_len))
        return int(curr_len)

    def _expected_delta_value_count(
        self,
        decoded_meta: Dict[str, np.ndarray],
        *,
        mode: str,
        padded_delta_max_diff: int,
    ) -> int:
        if mode not in SUPPORTED_DELTA_LENGTH_MODES:
            raise ValueError(f"Unsupported delta length mode: {mode}")
        track_lengths = decoded_meta["track_lengths"]
        int_kinds = decoded_meta["int_kinds"]
        n_points = decoded_meta["n_points"]
        delta_ref_offsets = decoded_meta.get("delta_ref_offsets", np.array([], dtype=np.uint8))
        cross_track_ref_indices = decoded_meta.get("cross_track_ref_indices", np.array([], dtype=np.uint32))

        island_pos = 0
        delta_ref_pos = 0
        cross_track_ref_pos = 0
        total = 0
        global_lengths: List[int] = []
        track_history_limit = max(1, int(self.delta_ref_window))

        for track_len in track_lengths:
            track_history: List[int] = []
            def append_track_history(length_value: int) -> None:
                track_history.append(int(length_value))
                overflow = len(track_history) - track_history_limit
                if overflow > 0:
                    del track_history[:overflow]
            for _ in range(int(track_len)):
                curr_len = int(n_points[island_pos])
                int_kind = int(int_kinds[island_pos])
                if int_kind == INT_KIND_DELTA2:
                    total += curr_len
                elif int_kind == INT_KIND_DELTA:
                    if delta_ref_pos < len(delta_ref_offsets):
                        ref_offset = int(delta_ref_offsets[delta_ref_pos])
                        delta_ref_pos += 1
                    else:
                        ref_offset = 1
                    if ref_offset == 0:
                        if cross_track_ref_pos >= len(cross_track_ref_indices):
                            raise ValueError("Corrupt TrackCodec MS1 stream: missing cross-track delta length reference")
                        ref_idx = int(cross_track_ref_indices[cross_track_ref_pos])
                        cross_track_ref_pos += 1
                        if ref_idx < 0 or ref_idx >= len(global_lengths):
                            raise ValueError("Corrupt TrackCodec MS1 stream: invalid cross-track delta length reference")
                        ref_len = int(global_lengths[ref_idx])
                    else:
                        if ref_offset > len(track_history):
                            raise ValueError("Corrupt TrackCodec MS1 stream: invalid in-track delta length reference")
                        ref_len = int(track_history[-ref_offset])
                    total += self._delta_encoded_length(
                        curr_len,
                        ref_len,
                        mode=mode,
                        padded_delta_max_diff=padded_delta_max_diff,
                    )
                append_track_history(curr_len)
                global_lengths.append(curr_len)
                island_pos += 1
        return total

    def _resolve_decode_delta_length_mode(self, header: Dict, decoded_meta: Dict[str, np.ndarray], segment_meta: Dict) -> str:
        explicit_mode = header.get("delta_length_mode")
        if explicit_mode is None:
            raise ValueError("Corrupt TrackCodec MS1 stream: missing delta_length_mode")
        if explicit_mode not in SUPPORTED_DELTA_LENGTH_MODES:
            raise ValueError(f"Unsupported delta length mode in header: {explicit_mode}")
        return explicit_mode

    def _select_best_cross_track_reference(
        self,
        candidate_segments: List[np.ndarray],
        candidate_indices_list: List[int],
        curr_segment: np.ndarray,
        equal_fidelity_precision: int | None,
        runtime_cache: Dict | None = None,
    ) -> Tuple[np.ndarray | None, int | None, Tuple[int | None, int | None, int | None]]:
        if not self.enable_cross_track_full_prediction:
            return None, None, EMPTY_REF_INFO
        if self.intensity_mode != "szdpd_xdelta_equalfidelity":
            return None, None, EMPTY_REF_INFO
        if equal_fidelity_precision is None:
            return None, None, EMPTY_REF_INFO
        if not candidate_segments:
            return None, None, EMPTY_REF_INFO
        full_est = self._estimate_equal_fidelity_full_bytes_cached(
            curr_segment,
            int(equal_fidelity_precision),
            runtime_cache=runtime_cache,
        )
        if (
            HAVE_CROSS_SCAN_SPEEDUPS
            and self.equal_fidelity_overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT
        ):
            candidate_indices = np.asarray(candidate_indices_list, dtype=np.uint32)
            native = _cross_scan_speedups.best_cross_track_reference_equal_fidelity(
                np.ascontiguousarray(curr_segment, dtype=np.float64),
                candidate_segments,
                candidate_indices,
                int(equal_fidelity_precision),
                int(self.padded_delta_max_diff),
                bool(self.delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR),
                int(self.cross_track_candidate_limit),
                int(self.cross_track_min_gain_bytes),
                int(full_est),
            )
            candidate_pos = native["candidate_pos"]
            if candidate_pos is not None:
                ref_segment = candidate_segments[int(candidate_pos)]
                ref_idx = candidate_indices_list[int(candidate_pos)]
                return ref_segment, int(ref_idx), (
                    int(native["full_est"]),
                    int(native["delta_est"]) if native["delta_est"] is not None else None,
                    int(native["encoded_len"]) if native["encoded_len"] is not None else None,
                )
            return None, None, (int(native["full_est"]), None, None)
        curr_len = len(curr_segment)
        best_ref = None
        best_ref_idx = None
        best_score = None
        best_info = (int(full_est), None, None)
        curr_code_cache: Dict[int, np.ndarray] = {}
        limit = int(self.cross_track_candidate_limit)
        filtered_candidates = []
        for ref_segment, ref_idx in zip(candidate_segments, candidate_indices_list):
            len_diff = abs(len(ref_segment) - curr_len)
            if len_diff > self.padded_delta_max_diff:
                continue
            if limit <= 0:
                filtered_candidates.append((len_diff, ref_segment, int(ref_idx)))
                continue
            score = (int(len_diff), -int(ref_idx))
            entry = (score, ref_segment, int(ref_idx))
            if len(filtered_candidates) < limit:
                filtered_candidates.append(entry)
                continue
            worst_pos = 0
            worst_score = filtered_candidates[0][0]
            for idx in range(1, len(filtered_candidates)):
                candidate_score = filtered_candidates[idx][0]
                if candidate_score > worst_score:
                    worst_pos = idx
                    worst_score = candidate_score
            if score < worst_score:
                filtered_candidates[worst_pos] = entry
        if not filtered_candidates:
            return None, None, {"full_est": int(full_est), "delta_est": None, "encoded_len": None}
        if limit > 0:
            filtered_candidates = [
                (score[0], ref_segment, ref_idx)
                for score, ref_segment, ref_idx in sorted(filtered_candidates, key=lambda item: item[0])
            ]

        for len_diff, ref_segment, ref_idx in filtered_candidates:
            encoded_len = self._delta_encoded_length(curr_len, len(ref_segment))
            curr_codes = curr_code_cache.get(encoded_len)
            if curr_codes is None:
                curr_codes = self._get_equal_fidelity_codes_cached(
                    curr_segment,
                    int(equal_fidelity_precision),
                    encoded_len=encoded_len,
                    runtime_cache=runtime_cache,
                )
                curr_code_cache[encoded_len] = curr_codes
            delta_est = self._estimate_equal_fidelity_delta_bytes(
                curr_segment,
                ref_segment,
                encoded_len,
                int(equal_fidelity_precision),
                curr_codes=curr_codes,
                runtime_cache=runtime_cache,
            )
            if delta_est + self.cross_track_min_gain_bytes >= full_est:
                continue
            score = (int(delta_est), int(len_diff), -int(ref_idx))
            if best_score is None or score < best_score:
                best_score = score
                best_ref = ref_segment
                best_ref_idx = int(ref_idx)
                best_info = (int(full_est), int(delta_est), int(encoded_len))
        return best_ref, best_ref_idx, best_info

    def _select_mz_representation(
        self,
        mz_q: np.ndarray,
        prev_mz_q: np.ndarray | None,
        mz_library,
        local_mz_refs: Dict[bytes, int],
        mz_model_offsets: List[int],
        mz_model_steps: List[int],
        mz_model_residual_lengths: List[int],
        mz_residual_segments,
    ) -> Tuple[int, int, bool, bool, bool]:
        if prev_mz_q is None:
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        if self.mz_dedup not in {"offset_model", "residual_offset_model"}:
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        if len(prev_mz_q) != len(mz_q):
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        if HAVE_CROSS_SCAN_SPEEDUPS and prev_mz_q.ndim == 1 and mz_q.ndim == 1:
            prev_native = prev_mz_q if prev_mz_q.flags.c_contiguous else np.ascontiguousarray(prev_mz_q, dtype=np.uint64)
            curr_native = mz_q if mz_q.flags.c_contiguous else np.ascontiguousarray(mz_q, dtype=np.uint64)
            native = _cross_scan_speedups.select_mz_representation_u64(
                prev_native,
                curr_native,
                bool(self.mz_dedup == "residual_offset_model"),
                bool(self.enhanced_residual_model),
            )
            native_kind = int(native.get("kind", MZ_KIND_RAW))
            if native_kind == MZ_KIND_PREV_OFFSET:
                model_idx = len(mz_model_offsets)
                mz_model_offsets.append(int(native["offset"]))
                mz_model_steps.append(0)
                mz_model_residual_lengths.append(0)
                return MZ_KIND_PREV_OFFSET, model_idx, True, False, False
            if native_kind == MZ_KIND_PREV_OFFSET_RESIDUAL:
                model_idx = len(mz_model_offsets)
                offset = int(native["offset"])
                step = int(native["step"])
                residual = np.asarray(native["residual"], dtype=np.int32)
                mz_model_offsets.append(offset)
                mz_model_steps.append(step)
                mz_model_residual_lengths.append(int(len(residual)))
                self._append_mz_residual_segment(mz_residual_segments, residual)
                return MZ_KIND_PREV_OFFSET_RESIDUAL, model_idx, False, True, bool(step != 0)
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        prev64 = prev_mz_q.astype(np.int64)
        curr64 = mz_q.astype(np.int64)
        diff = curr64 - prev64
        offset = int(diff[0]) if len(diff) else 0
        pred = prev64 + offset
        if np.array_equal(curr64, pred):
            model_idx = len(mz_model_offsets)
            mz_model_offsets.append(offset)
            mz_model_steps.append(0)
            mz_model_residual_lengths.append(0)
            return MZ_KIND_PREV_OFFSET, model_idx, True, False, False

        if self.mz_dedup != "residual_offset_model":
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        if len(diff) == 0:
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        raw_cost = _estimate_raw_mz_local_bytes(mz_q)
        best_model = self._select_best_residual_mz_model(prev64, curr64)
        if best_model is None:
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False
        offset, step, residual, model_cost = best_model
        if model_cost >= raw_cost:
            return MZ_KIND_RAW, self._store_raw_mz(mz_q, mz_library, local_mz_refs), False, False, False

        model_idx = len(mz_model_offsets)
        mz_model_offsets.append(offset)
        mz_model_steps.append(step)
        mz_model_residual_lengths.append(int(len(residual)))
        self._append_mz_residual_segment(mz_residual_segments, residual)
        return MZ_KIND_PREV_OFFSET_RESIDUAL, model_idx, False, True, bool(step != 0)

    def _build_representation(self, tracks, ms1_scans=None, progress_callback: Callable[[Dict], None] | None = None) -> Dict:
        tempdir: str | None = None
        scale = baseline_ms1_codec.MZ_PRECISION_MAP[self.mz_precision]
        equal_fidelity_precision = None
        runtime_cache = self._create_runtime_cache()
        if self.intensity_mode == "szdpd_xdelta_equalfidelity" and (
            self.adaptive_padded_delta_accounting
            or self.enable_cross_track_full_prediction
            or self.enable_byteaware_delta_ref_selection
        ):
            equal_fidelity_precision = self._detect_equal_fidelity_precision_from_tracks(tracks, ms1_scans=ms1_scans)
        include_array_starts = (not self.omit_array_starts) or self.preserve_full_scan
        compact_tracks = tracks if isinstance(tracks, CompactIslandTracks) else None
        compact_track_views_only = compact_tracks is not None and len(compact_tracks.islands) == 0
        if compact_track_views_only and ms1_scans is None:
            raise ValueError("CompactIslandTracks without islands requires ms1_scans for representation build")
        if compact_tracks is not None:
            n_tracks_total = int(len(compact_tracks))
            n_islands_total = int(compact_tracks.island_count)
            track_lengths = np.asarray(compact_tracks.track_lengths, dtype=np.uint32).copy()
            scan_indices = np.asarray(compact_tracks.scan_indices, dtype=np.uint32)
            source_array_starts = np.asarray(compact_tracks.array_start_indices, dtype=np.uint32)
            array_starts = source_array_starts if include_array_starts else None
            n_points = np.asarray(compact_tracks.n_points, dtype=np.uint32)
            all_islands = compact_tracks.islands if not compact_track_views_only else None
        else:
            n_tracks_total = int(len(tracks))
            n_islands_total = int(sum(len(track.islands) for track in tracks))
            track_lengths = np.asarray([len(track.islands) for track in tracks], dtype=np.uint32)
            scan_indices = np.empty(n_islands_total, dtype=np.uint32)
            source_array_starts = np.empty(n_islands_total, dtype=np.uint32)
            array_starts = np.empty(n_islands_total, dtype=np.uint32) if include_array_starts else None
            n_points = np.empty(n_islands_total, dtype=np.uint32)
            pos = 0
            for track in tracks:
                for island in track.islands:
                    scan_indices[pos] = int(island.scan_idx)
                    source_array_starts[pos] = int(island.array_start_idx)
                    if array_starts is not None:
                        array_starts[pos] = source_array_starts[pos]
                    n_points[pos] = int(island.n_points)
                    pos += 1
            all_islands = None
        source_ctx = _make_source_context(tracks, compact_tracks, all_islands, ms1_scans)

        if (
            HAVE_CROSS_SCAN_SPEEDUPS
            and not self.disable_native_compact_views
            and source_ctx.flat_mz_data is not None
            and source_ctx.flat_intensity_data is not None
            and source_ctx.ms1_scan_base_offsets is not None
        ):
            if compact_tracks is not None:
                track_offsets_arr = np.asarray(compact_tracks.track_offsets, dtype=np.uint32)
            else:
                track_offsets_arr = np.zeros(len(track_lengths) + 1, dtype=np.uint32)
                if len(track_lengths):
                    track_offsets_arr[1:] = np.cumsum(track_lengths, dtype=np.uint32)
            _emit_progress(
                progress_callback,
                {
                    "stage": "track_representation_start",
                    "pct": 0.0,
                    "done_tracks": 0,
                    "total_tracks": int(n_tracks_total),
                    "done_islands": 0,
                    "total_islands": int(n_islands_total),
                },
            )
            native = _cross_scan_speedups.build_representation_compact_views(
                np.asarray(source_ctx.flat_mz_data, dtype=np.float64),
                np.asarray(source_ctx.flat_intensity_data, dtype=np.float64),
                np.asarray(source_ctx.ms1_scan_base_offsets, dtype=np.uint64),
                track_offsets_arr,
                np.asarray(scan_indices, dtype=np.uint32),
                np.asarray(source_array_starts, dtype=np.uint32),
                np.asarray(n_points, dtype=np.uint32),
                bool(self.omit_island_mz),
                int(self.mz_precision),
                bool((not self.omit_island_mz) and self.mz_dedup in {"track_unique", "offset_model", "residual_offset_model"}),
                bool((not self.omit_island_mz) and self.mz_dedup in {"offset_model", "residual_offset_model"}),
                bool((not self.omit_island_mz) and self.mz_dedup == "residual_offset_model"),
                bool((not self.omit_island_mz) and self.enhanced_residual_model),
                bool(self.intensity_mode == "szdpd_xdelta_equalfidelity"),
                int(equal_fidelity_precision or 0),
                int(self.delta_ref_window),
                int(self.padded_delta_max_diff),
                bool(self.delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR),
                bool(self.adaptive_padded_delta_accounting),
                bool(self.enable_second_order_delta),
                bool(self.enable_cross_track_full_prediction),
                int(self.cross_track_candidate_limit),
                int(self.cross_track_min_gain_bytes),
                bool(self.enable_byteaware_delta_ref_selection),
            )
            native_stats = native["stats"]
            n_offset_arrays = int(native_stats["n_mz_offset_arrays"])
            n_residual_offset_arrays = int(native_stats["n_mz_residual_offset_arrays"])
            n_affine_residual_arrays = int(native_stats["n_mz_affine_residual_arrays"])
            n_delta_islands = int(native_stats["n_delta_islands"])
            n_delta2_islands = int(native_stats["n_delta2_islands"])
            n_padded_delta_islands = int(native_stats["n_padded_delta_islands"])
            n_byte_accounting_rejects = int(native_stats["n_byte_accounting_rejects"])
            n_same_length_pairs = int(native_stats["n_same_length_pairs"])
            delta_ref_offset_sum = int(native_stats["delta_ref_offset_sum"])
            n_cross_track_delta_islands = int(native_stats["n_cross_track_delta_islands"])
            n_singleton_track_fast_path = int(native_stats.get("n_singleton_track_fast_path", 0))
            total_mz_points = int(native_stats["total_mz_points"])
            offset_mz_points = int(native_stats["offset_mz_points"])
            residual_mz_points = int(native_stats["residual_mz_points"])
            native_int_kinds = np.asarray(native["int_kinds"], dtype=np.uint8)
            native_delta_ref_offsets = np.asarray(native["delta_ref_offsets"], dtype=np.uint8)
            native_cross_track_ref_indices = np.asarray(native["cross_track_ref_indices"], dtype=np.uint32)
            if self.intensity_mode == "stackzdpd_passthrough":
                native_int_kinds = np.full(int(n_islands_total), INT_KIND_FULL, dtype=np.uint8)
                native_delta_ref_offsets = np.array([], dtype=np.uint8)
                native_cross_track_ref_indices = np.array([], dtype=np.uint32)
                n_delta_islands = 0
                n_delta2_islands = 0
                n_padded_delta_islands = 0
                n_byte_accounting_rejects = 0
                n_same_length_pairs = 0
                delta_ref_offset_sum = 0
                n_cross_track_delta_islands = 0
                n_singleton_track_fast_path = 0
            rep = {
                "scale": scale,
                "track_lengths": track_lengths,
                "scan_indices": scan_indices,
                "mz_kinds": np.asarray(native["mz_kinds"], dtype=np.uint8),
                "mz_ref_indices": np.asarray(native["mz_ref_indices"], dtype=np.uint32),
                "int_kinds": native_int_kinds,
                "delta_ref_offsets": native_delta_ref_offsets,
                "cross_track_ref_indices": native_cross_track_ref_indices,
                "mz_library": None,
                "mz_library_lengths": np.asarray(native["mz_library_lengths"], dtype=np.uint32),
                "mz_library_first_values_u64": np.asarray(native["mz_library_first_values_u64"], dtype=np.uint64),
                "mz_library_delta_values_u32": np.asarray(native["mz_library_delta_values_u32"], dtype=np.uint32),
                "mz_model_offsets": np.asarray(native["mz_model_offsets"], dtype=np.int32),
                "mz_model_steps": np.asarray(native["mz_model_steps"], dtype=np.int32),
                "mz_model_residual_lengths": np.asarray(native["mz_model_residual_lengths"], dtype=np.uint32),
                "mz_residual_segments": None,
                "mz_residual_concat_i32": np.asarray(native["mz_residual_concat_i32"], dtype=np.int32),
                "equal_fidelity_precision": int(equal_fidelity_precision) if equal_fidelity_precision is not None else None,
                "omit_island_mz": bool(self.omit_island_mz),
                "_runtime_cache": runtime_cache,
                "_n_points_runtime": n_points,
                "_scan_indices_runtime": scan_indices,
                "_array_starts_runtime": array_starts,
                "_source_tracks": tracks,
                "_source_ms1_scans": ms1_scans,
                "_source_compact_tracks": compact_tracks,
                "_source_all_islands": all_islands,
                "_tempdir": source_ctx.tempdir,
                "_native_representation_used": True,
                "_source_context_materialized": source_ctx.tempdir is not None,
                "stats": {
                    "n_tracks": int(n_tracks_total),
                    "n_islands": int(n_islands_total),
                    "n_mz_arrays": int(native_stats["n_mz_arrays"]),
                    "n_mz_offset_arrays": n_offset_arrays,
                    "n_mz_residual_offset_arrays": n_residual_offset_arrays,
                    "n_mz_affine_residual_arrays": n_affine_residual_arrays,
                    "mz_offset_fraction": float(n_offset_arrays / n_islands_total) if n_islands_total else 0.0,
                    "mz_offset_point_fraction": float(offset_mz_points / total_mz_points) if total_mz_points else 0.0,
                    "mz_residual_offset_fraction": float(n_residual_offset_arrays / n_islands_total) if n_islands_total else 0.0,
                    "mz_residual_offset_point_fraction": float(residual_mz_points / total_mz_points) if total_mz_points else 0.0,
                    "mz_affine_residual_fraction": float(n_affine_residual_arrays / n_islands_total) if n_islands_total else 0.0,
                    "n_total_pairs": int(max(0, n_islands_total - n_tracks_total)),
                    "n_same_length_pairs": n_same_length_pairs,
                    "n_delta_islands": n_delta_islands,
                    "n_delta2_islands": n_delta2_islands,
                    "delta_island_fraction": float(n_delta_islands / n_islands_total) if n_islands_total else 0.0,
                    "delta2_island_fraction": float(n_delta2_islands / n_islands_total) if n_islands_total else 0.0,
                    "padded_delta_island_fraction": float(n_padded_delta_islands / n_islands_total) if n_islands_total else 0.0,
                    "n_byte_accounting_rejects": n_byte_accounting_rejects,
                    "byte_accounting_reject_fraction": float(n_byte_accounting_rejects / n_islands_total) if n_islands_total else 0.0,
                    "delta_ref_mean_offset": float(delta_ref_offset_sum / n_delta_islands) if n_delta_islands else 0.0,
                    "n_cross_track_delta_islands": n_cross_track_delta_islands,
                    "cross_track_delta_fraction": float(n_cross_track_delta_islands / n_islands_total) if n_islands_total else 0.0,
                    "n_singleton_track_fast_path": n_singleton_track_fast_path,
                    "singleton_track_fast_path_fraction": float(n_singleton_track_fast_path / n_tracks_total) if n_tracks_total else 0.0,
                    "omit_array_starts": bool(self.omit_array_starts),
                    "omit_n_points": bool(self.omit_n_points),
                    "adaptive_padded_delta_accounting": bool(self.adaptive_padded_delta_accounting),
                    "enable_second_order_delta": bool(self.enable_second_order_delta),
                    "enable_cross_track_full_prediction": bool(self.enable_cross_track_full_prediction),
                    "cross_track_min_gain_bytes": int(self.cross_track_min_gain_bytes),
                    "enable_byteaware_delta_ref_selection": bool(self.enable_byteaware_delta_ref_selection),
                    "track_prepare_workers": int(self.track_prepare_workers),
                    "track_prepare_lookahead": int(self.track_prepare_lookahead),
                    "omit_island_mz": bool(self.omit_island_mz),
                },
            }
            if include_array_starts:
                rep["array_starts"] = array_starts
            if not self.omit_n_points:
                rep["n_points"] = n_points
            rep["include_array_starts"] = bool(include_array_starts)
            _emit_progress(
                progress_callback,
                {
                    "stage": "track_representation_done",
                    "pct": 92.0,
                    "track_pct": 100.0,
                    "done_tracks": int(n_tracks_total),
                    "total_tracks": int(n_tracks_total),
                    "done_islands": int(n_islands_total),
                    "total_islands": int(n_islands_total),
                },
            )
            return rep
        mz_kinds = np.empty(n_islands_total, dtype=np.uint8)
        mz_ref_indices = np.empty(n_islands_total, dtype=np.uint32)
        int_kinds = np.empty(n_islands_total, dtype=np.uint8)
        delta_ref_offsets = bytearray()
        cross_track_ref_indices = array("I")

        tempdir = tempfile.mkdtemp(prefix="trackcodec_rep_build_")
        mz_library = {
            "count": 0,
            "lengths": [],
            "first_values": [],
            "delta_handle": (Path(tempdir) / "mz_library_delta_values.bin").open("wb"),
            "delta_count": 0,
        }
        mz_model_offsets = array("i")
        mz_model_steps = array("i")
        mz_model_residual_lengths = array("I")
        mz_residual_segments = {
            "handle": (Path(tempdir) / "mz_residual_concat.bin").open("wb"),
            "count": 0,
        }

        n_delta_islands = 0
        n_delta2_islands = 0
        n_padded_delta_islands = 0
        n_byte_accounting_rejects = 0
        n_same_length_pairs = 0
        n_offset_arrays = 0
        n_residual_offset_arrays = 0
        n_affine_residual_arrays = 0
        total_mz_points = 0
        offset_mz_points = 0
        residual_mz_points = 0
        delta_ref_offset_sum = 0
        n_cross_track_delta_islands = 0
        cross_track_scan_history: Dict[int, CrossTrackHistoryEntry] | None = (
            {} if self.enable_cross_track_full_prediction else None
        )
        cross_track_bucket_limit = (
            int(self.cross_track_candidate_limit)
            if int(self.cross_track_candidate_limit) > 0
            else None
        )
        global_island_idx = 0
        track_pos = 0
        track_progress_step = max(1, n_tracks_total // 100) if n_tracks_total > 0 else 1
        track_history_limit = max(int(self.delta_ref_window), 2 if self.enable_second_order_delta else 1)

        _emit_progress(
            progress_callback,
            {
                "stage": "track_representation_start",
                "pct": 0.0,
                "done_tracks": 0,
                "total_tracks": int(n_tracks_total),
                "done_islands": 0,
                "total_islands": int(n_islands_total),
            },
        )

        append_delta_ref_offset = delta_ref_offsets.append
        append_cross_track_ref_index = cross_track_ref_indices.append
        quantize_mz = self._quantize_mz
        select_mz_representation = self._select_mz_representation
        select_best_delta_reference = self._select_best_delta_reference
        select_best_cross_track_reference = self._select_best_cross_track_reference
        estimate_equal_fidelity_full_bytes_cached = self._estimate_equal_fidelity_full_bytes_cached
        estimate_equal_fidelity_delta_bytes = self._estimate_equal_fidelity_delta_bytes
        estimate_equal_fidelity_delta2_bytes = self._estimate_equal_fidelity_delta2_bytes
        delta_encoded_length = self._delta_encoded_length
        use_local_mz_refs = (not self.omit_island_mz) and self.mz_dedup in {"track_unique", "offset_model", "residual_offset_model"}
        write_scan_meta = compact_tracks is None
        source_ctx = _make_source_context(tracks, compact_tracks, all_islands, ms1_scans)
        source_get_segment = lambda idx: _source_segment_view_ctx(source_ctx, idx)
        source_get_mz = lambda idx: _source_mz_view_ctx(source_ctx, idx)

        def _append_cross_track_history(scan_idx_i: int, segment: np.ndarray, island_idx: int) -> None:
            if cross_track_scan_history is None:
                return
            history_entry = cross_track_scan_history.get(scan_idx_i)
            if history_entry is None:
                history_entry = {}
                cross_track_scan_history[scan_idx_i] = history_entry
            _append_cross_track_bucket(
                history_entry,
                len(segment),
                island_idx,
                per_length_limit=cross_track_bucket_limit,
            )

        for prepared_track in self._iter_prepared_track_representations(
            tracks,
            compact_tracks,
            all_islands,
            ms1_scans,
            include_array_starts=include_array_starts,
            equal_fidelity_precision=equal_fidelity_precision,
            source_ctx=source_ctx,
        ):
            track_start = prepared_track.track_start
            track_end = prepared_track.track_end
            track_island_count = prepared_track.track_island_count
            track_lengths[track_pos] = track_island_count
            track_pos += 1
            local_mz_refs: Dict[bytes, int] | None = {} if (use_local_mz_refs and track_island_count > 1) else None
            prev_mz_q = None
            history_segments: List[np.ndarray] = []
            def append_history_segment(segment: np.ndarray) -> None:
                history_segments.append(segment)
                overflow = len(history_segments) - track_history_limit
                if overflow > 0:
                    del history_segments[:overflow]
            track_mz_q = prepared_track.quantized_mz
            native_int_kinds = prepared_track.native_int_kinds
            native_ref_offsets = prepared_track.native_ref_offsets
            native_encoded_lens = prepared_track.native_encoded_lens
            native_full_estimates = prepared_track.native_full_estimates
            native_track_plan = native_int_kinds is not None
            for local_offset in range(track_island_count):
                island_idx = global_island_idx
                source_island_idx = int(prepared_track.island_indices[local_offset])
                island_scan_idx = int(prepared_track.scan_indices[local_offset])
                curr_segment = source_get_segment(source_island_idx)
                if self.omit_island_mz:
                    mz_q = None
                    mz_kinds[island_idx] = MZ_KIND_RAW
                    mz_ref_indices[island_idx] = 0
                else:
                    mz_arr_view = source_get_mz(source_island_idx)
                    mz_q = (
                        track_mz_q[local_offset]
                        if track_mz_q is not None
                        else quantize_mz(mz_arr_view)
                    )
                    total_mz_points += len(mz_q)

                    mz_kind, mz_ref_idx, used_exact_offset, used_residual_offset, used_affine_residual = select_mz_representation(
                        mz_q,
                        prev_mz_q,
                        mz_library,
                        local_mz_refs,
                        mz_model_offsets,
                        mz_model_steps,
                        mz_model_residual_lengths,
                        mz_residual_segments,
                    )
                    mz_kinds[island_idx] = mz_kind
                    mz_ref_indices[island_idx] = mz_ref_idx
                    if used_exact_offset:
                        n_offset_arrays += 1
                        offset_mz_points += len(mz_q)
                    if used_residual_offset:
                        n_residual_offset_arrays += 1
                        residual_mz_points += len(mz_q)
                    if used_affine_residual:
                        n_affine_residual_arrays += 1

                if write_scan_meta:
                    scan_indices[island_idx] = prepared_track.scan_indices[local_offset]
                    if array_starts is not None and prepared_track.array_starts is not None:
                        array_starts[island_idx] = prepared_track.array_starts[local_offset]
                    n_points[island_idx] = prepared_track.n_points[local_offset]

                if self.intensity_mode == "stackzdpd_passthrough":
                    int_kinds[island_idx] = INT_KIND_FULL
                    prev_mz_q = mz_q
                    global_island_idx += 1
                    continue

                cross_track_ref_idx = None
                selected_full_est, selected_delta_est, selected_encoded_len = EMPTY_REF_INFO
                scan_idx_i = int(island_scan_idx)
                scan_history = None
                if cross_track_scan_history is not None:
                    scan_history = cross_track_scan_history.get(scan_idx_i)
                scan_history_indices = None
                if scan_history is not None:
                    scan_history_indices = _collect_cross_track_candidates(
                        scan_history,
                        len(curr_segment),
                        padded_delta_max_diff=int(self.padded_delta_max_diff),
                    )
                scan_history_segments = (
                    [source_get_segment(int(idx)) for idx in scan_history_indices]
                    if scan_history_indices
                    else None
                )
                if native_track_plan:
                    native_kind = int(native_int_kinds[local_offset])
                    native_ref_offset = int(native_ref_offsets[local_offset])
                    native_encoded_len = int(native_encoded_lens[local_offset])
                    native_full_est = int(native_full_estimates[local_offset])
                    ref_offset = native_ref_offset
                    ref_segment = history_segments[-ref_offset] if native_ref_offset > 0 else None
                    if native_kind == INT_KIND_DELTA:
                        selected_full_est, selected_delta_est, selected_encoded_len = (
                            native_full_est,
                            None,
                            native_encoded_len,
                        )
                    elif native_kind == INT_KIND_DELTA2:
                        selected_full_est, selected_delta_est, selected_encoded_len = (
                            native_full_est,
                            None,
                            int(len(curr_segment)),
                        )
                    elif scan_history_segments:
                        cross_track_ref, cross_track_ref_idx, cross_track_info = select_best_cross_track_reference(
                            scan_history_segments,
                            scan_history_indices,
                            curr_segment,
                            equal_fidelity_precision,
                            runtime_cache=runtime_cache,
                        )
                        if cross_track_ref is not None:
                            ref_offset = 0
                            ref_segment = cross_track_ref
                            selected_full_est, selected_delta_est, selected_encoded_len = cross_track_info
                            native_kind = INT_KIND_DELTA
                    kind_override = native_kind
                else:
                    ref_offset, ref_segment, ref_info = select_best_delta_reference(
                        history_segments,
                        curr_segment,
                        equal_fidelity_precision=equal_fidelity_precision,
                        runtime_cache=runtime_cache,
                    )
                    selected_full_est, selected_delta_est, selected_encoded_len = ref_info
                    if ref_segment is None and scan_history_segments:
                        cross_track_ref, cross_track_ref_idx, cross_track_info = select_best_cross_track_reference(
                            scan_history_segments,
                            scan_history_indices,
                            curr_segment,
                            equal_fidelity_precision,
                            runtime_cache=runtime_cache,
                        )
                        if cross_track_ref is not None:
                            ref_offset = 0
                            ref_segment = cross_track_ref
                            selected_full_est, selected_delta_est, selected_encoded_len = cross_track_info
                    kind_override = None
                if kind_override == INT_KIND_DELTA2:
                    n_delta_islands += 1
                    n_delta2_islands += 1
                    int_kinds[island_idx] = INT_KIND_DELTA2
                    prev_mz_q = mz_q
                    append_history_segment(curr_segment)
                    _append_cross_track_history(scan_idx_i, curr_segment, island_idx)
                    global_island_idx += 1
                    continue

                if ref_segment is None:
                    int_kinds[island_idx] = INT_KIND_FULL
                else:
                    is_padded_delta = len(ref_segment) != len(curr_segment)
                    encoded_len = selected_encoded_len
                    if encoded_len is None:
                        encoded_len = delta_encoded_length(len(curr_segment), len(ref_segment))
                    delta1_est = selected_delta_est
                    if (
                        (not native_track_plan)
                        and
                        is_padded_delta
                        and self.adaptive_padded_delta_accounting
                        and self.intensity_mode == "szdpd_xdelta_equalfidelity"
                    ):
                        full_est = selected_full_est
                        if full_est is None:
                            full_est = estimate_equal_fidelity_full_bytes_cached(
                                curr_segment,
                                int(equal_fidelity_precision),
                                runtime_cache=runtime_cache,
                            )
                        delta1_est = estimate_equal_fidelity_delta_bytes(
                            curr_segment,
                            ref_segment,
                            encoded_len,
                            int(equal_fidelity_precision),
                            runtime_cache=runtime_cache,
                        )
                        if delta1_est >= full_est:
                            n_byte_accounting_rejects += 1
                            int_kinds[island_idx] = INT_KIND_FULL
                            prev_mz_q = mz_q
                            append_history_segment(curr_segment)
                            _append_cross_track_history(scan_idx_i, curr_segment, island_idx)
                            global_island_idx += 1
                            continue

                    if (
                        (not native_track_plan)
                        and
                        self.enable_second_order_delta
                        and self.intensity_mode == "szdpd_xdelta_equalfidelity"
                        and len(history_segments) >= 2
                    ):
                        prev_segment = history_segments[-1]
                        prev2_segment = history_segments[-2]
                        if (
                            len(prev_segment) == len(curr_segment)
                            and len(prev2_segment) == len(curr_segment)
                        ):
                            full_est = selected_full_est
                            if full_est is None:
                                full_est = estimate_equal_fidelity_full_bytes_cached(
                                    curr_segment,
                                    int(equal_fidelity_precision),
                                    runtime_cache=runtime_cache,
                                )
                            if delta1_est is None:
                                delta1_est = estimate_equal_fidelity_delta_bytes(
                                    curr_segment,
                                    ref_segment,
                                    encoded_len,
                                    int(equal_fidelity_precision),
                                    runtime_cache=runtime_cache,
                                )
                            delta2_est = estimate_equal_fidelity_delta2_bytes(
                                curr_segment,
                                prev_segment,
                                prev2_segment,
                                int(equal_fidelity_precision),
                                runtime_cache=runtime_cache,
                            )
                            if delta2_est < min(full_est, delta1_est):
                                n_delta_islands += 1
                                n_delta2_islands += 1
                                int_kinds[island_idx] = INT_KIND_DELTA2
                                prev_mz_q = mz_q
                                append_history_segment(curr_segment)
                                _append_cross_track_history(scan_idx_i, curr_segment, island_idx)
                                global_island_idx += 1
                                continue

                    if not is_padded_delta:
                        n_same_length_pairs += 1
                    else:
                        n_padded_delta_islands += 1
                    n_delta_islands += 1
                    if ref_offset > 0:
                        delta_ref_offset_sum += ref_offset
                    else:
                        n_cross_track_delta_islands += 1
                        if cross_track_ref_idx is None:
                            raise ValueError("Cross-track delta chosen without a reference island index")
                        append_cross_track_ref_index(int(cross_track_ref_idx))
                    int_kinds[island_idx] = INT_KIND_DELTA
                    append_delta_ref_offset(ref_offset)
                prev_mz_q = mz_q
                append_history_segment(curr_segment)
                _append_cross_track_history(scan_idx_i, curr_segment, island_idx)
                global_island_idx += 1

            if (
                track_pos == int(n_tracks_total)
                or track_pos == 1
                or (track_pos % track_progress_step) == 0
            ):
                track_pct = (100.0 * float(track_pos) / float(n_tracks_total)) if n_tracks_total else 100.0
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "track_representation",
                        "pct": float(92.0 * track_pct / 100.0),
                        "track_pct": float(track_pct),
                        "done_tracks": int(track_pos),
                        "total_tracks": int(n_tracks_total),
                        "done_islands": int(global_island_idx),
                        "total_islands": int(n_islands_total),
                    },
                )

        try:
            mz_library["delta_handle"].close()
            mz_residual_segments["handle"].close()
            rep = {
                "scale": scale,
                "track_lengths": track_lengths,
                "scan_indices": scan_indices,
                "mz_kinds": mz_kinds,
                "mz_ref_indices": mz_ref_indices,
                "int_kinds": int_kinds,
                "delta_ref_offsets": np.frombuffer(delta_ref_offsets, dtype=np.uint8).copy(),
                "cross_track_ref_indices": np.asarray(cross_track_ref_indices, dtype=np.uint32),
                "mz_library": None,
                "mz_library_lengths": np.asarray(mz_library["lengths"], dtype=np.uint32),
                "mz_library_first_values_u64": np.asarray(mz_library["first_values"], dtype=np.uint64),
                "mz_library_delta_values_u32": np.memmap(
                    Path(tempdir) / "mz_library_delta_values.bin",
                    dtype=np.uint32,
                    mode="r",
                    shape=(int(mz_library["delta_count"]),),
                ) if int(mz_library["delta_count"]) > 0 else EMPTY_UINT32,
                "mz_model_offsets": np.asarray(mz_model_offsets, dtype=np.int32),
                "mz_model_steps": np.asarray(mz_model_steps, dtype=np.int32),
                "mz_model_residual_lengths": np.asarray(mz_model_residual_lengths, dtype=np.uint32),
                "mz_residual_segments": None,
                "mz_residual_concat_i32": np.memmap(
                    Path(tempdir) / "mz_residual_concat.bin",
                    dtype=np.int32,
                    mode="r",
                    shape=(int(mz_residual_segments["count"]),),
                ) if int(mz_residual_segments["count"]) > 0 else np.array([], dtype=np.int32),
                "equal_fidelity_precision": int(equal_fidelity_precision) if equal_fidelity_precision is not None else None,
                "omit_island_mz": bool(self.omit_island_mz),
                "_runtime_cache": runtime_cache,
                "_n_points_runtime": n_points,
                "_scan_indices_runtime": scan_indices,
                "_array_starts_runtime": array_starts,
                "_source_tracks": tracks,
                "_source_ms1_scans": ms1_scans,
                "_source_compact_tracks": compact_tracks,
                "_source_all_islands": all_islands,
                "_tempdir": tempdir,
                "_native_representation_used": False,
                "_source_context_materialized": source_ctx.tempdir is not None,
                "stats": {
                "n_tracks": int(n_tracks_total),
                "n_islands": int(n_islands_total),
                "n_mz_arrays": int(mz_library["count"]),
                "n_mz_offset_arrays": int(n_offset_arrays),
                "n_mz_residual_offset_arrays": int(n_residual_offset_arrays),
                "n_mz_affine_residual_arrays": int(n_affine_residual_arrays),
                "mz_offset_fraction": float(n_offset_arrays / n_islands_total) if n_islands_total else 0.0,
                "mz_offset_point_fraction": float(offset_mz_points / total_mz_points) if total_mz_points else 0.0,
                "mz_residual_offset_fraction": float(n_residual_offset_arrays / n_islands_total) if n_islands_total else 0.0,
                "mz_residual_offset_point_fraction": float(residual_mz_points / total_mz_points) if total_mz_points else 0.0,
                "mz_affine_residual_fraction": float(n_affine_residual_arrays / n_islands_total) if n_islands_total else 0.0,
                "n_total_pairs": int(max(0, n_islands_total - n_tracks_total)),
                "n_same_length_pairs": int(n_same_length_pairs),
                "n_delta_islands": int(n_delta_islands),
                "n_delta2_islands": int(n_delta2_islands),
                "delta_island_fraction": float(n_delta_islands / n_islands_total) if n_islands_total else 0.0,
                "delta2_island_fraction": float(n_delta2_islands / n_islands_total) if n_islands_total else 0.0,
                "padded_delta_island_fraction": float(n_padded_delta_islands / n_islands_total) if n_islands_total else 0.0,
                "n_byte_accounting_rejects": int(n_byte_accounting_rejects),
                "byte_accounting_reject_fraction": float(n_byte_accounting_rejects / n_islands_total) if n_islands_total else 0.0,
                "delta_ref_mean_offset": float(delta_ref_offset_sum / n_delta_islands) if n_delta_islands else 0.0,
                "n_cross_track_delta_islands": int(n_cross_track_delta_islands),
                "cross_track_delta_fraction": float(n_cross_track_delta_islands / n_islands_total) if n_islands_total else 0.0,
                "omit_array_starts": bool(self.omit_array_starts),
                "omit_n_points": bool(self.omit_n_points),
                "adaptive_padded_delta_accounting": bool(self.adaptive_padded_delta_accounting),
                "enable_second_order_delta": bool(self.enable_second_order_delta),
                "enable_cross_track_full_prediction": bool(self.enable_cross_track_full_prediction),
                "cross_track_min_gain_bytes": int(self.cross_track_min_gain_bytes),
                "enable_byteaware_delta_ref_selection": bool(self.enable_byteaware_delta_ref_selection),
                "track_prepare_workers": int(self.track_prepare_workers),
                "track_prepare_lookahead": int(self.track_prepare_lookahead),
                "omit_island_mz": bool(self.omit_island_mz),
                },
            }
            if include_array_starts:
                rep["array_starts"] = array_starts
            if not self.omit_n_points:
                rep["n_points"] = n_points
            rep["include_array_starts"] = bool(include_array_starts)
            _emit_progress(
                progress_callback,
                {
                    "stage": "track_representation_done",
                    "pct": 92.0,
                    "track_pct": 100.0,
                    "done_tracks": int(n_tracks_total),
                    "total_tracks": int(n_tracks_total),
                    "done_islands": int(n_islands_total),
                    "total_islands": int(n_islands_total),
                },
            )
            return rep
        except Exception:
            try:
                mz_library["delta_handle"].close()
            except Exception:
                pass
            try:
                mz_residual_segments["handle"].close()
            except Exception:
                pass
            if tempdir is not None:
                shutil.rmtree(tempdir, ignore_errors=True)
            raise

    def _spill_representation_arrays(self, rep: Dict) -> None:
        mz_library = rep.get("mz_library")
        mz_residual_segments = rep.get("mz_residual_segments")
        if mz_library is None and mz_residual_segments is None:
            return
        tmp_dir = rep.get("_tempdir")
        if tmp_dir is None:
            tmp_dir = tempfile.mkdtemp(prefix="trackcodec_rep_")
            rep["_tempdir"] = tmp_dir

        if mz_library is not None:
            lengths = np.asarray([len(arr) for arr in mz_library], dtype=np.uint32)
            first_values_u64 = np.asarray([int(arr[0]) for arr in mz_library if len(arr) > 0], dtype=np.uint64)
            delta_total = int(sum(max(0, len(arr) - 1) for arr in mz_library))
            delta_values = self._make_temp_dense_array(tmp_dir, "mz_library_delta_values", np.uint32, delta_total)
            pos = 0
            for arr in mz_library:
                if len(arr) > 1:
                    delta = _validate_quantized_mz_deltas_uint32(arr, context="spill_mz_library")
                    next_pos = pos + len(delta)
                    delta_values[pos:next_pos] = delta
                    pos = next_pos
            rep["mz_library_lengths"] = lengths
            rep["mz_library_first_values_u64"] = first_values_u64
            rep["mz_library_delta_values_u32"] = np.asarray(delta_values, dtype=np.uint32)
            rep["mz_library"] = None

        if mz_residual_segments is not None:
            residual_total = int(sum(len(seg) for seg in mz_residual_segments))
            residual_concat = self._make_temp_dense_array(tmp_dir, "mz_residual_concat", np.int32, residual_total)
            pos = 0
            for seg in mz_residual_segments:
                arr = np.asarray(seg, dtype=np.int32)
                next_pos = pos + len(arr)
                residual_concat[pos:next_pos] = arr
                pos = next_pos
            rep["mz_residual_concat_i32"] = np.asarray(residual_concat, dtype=np.int32)
            rep["mz_residual_segments"] = None

    def _encode_mz_library(self, rep: Dict) -> Tuple[Dict[str, bytes], Dict]:
        if rep.get("mz_library_lengths") is not None:
            lengths = np.asarray(rep["mz_library_lengths"], dtype=np.uint32)
            first_values_u64 = np.asarray(rep["mz_library_first_values_u64"], dtype=np.uint64)
            delta_concat = np.asarray(rep["mz_library_delta_values_u32"], dtype=np.uint32)
        else:
            mz_library = rep["mz_library"]
            lengths = np.asarray([len(arr) for arr in mz_library], dtype=np.uint32)
            first_values_u64 = np.asarray([int(arr[0]) for arr in mz_library if len(arr) > 0], dtype=np.uint64)
            delta_values = []
            for arr in mz_library:
                if len(arr) > 1:
                    delta_values.append(_validate_quantized_mz_deltas_uint32(arr, context="encode_mz_library"))
            delta_concat = np.concatenate(delta_values) if delta_values else np.array([], dtype=np.uint32)
        first_values, first_overflow_idx, first_overflow_vals = _split_uint64_anchor_overflow(first_values_u64)

        seg_lengths, meta_lengths = self._encode_transformed_uint32_metadata(lengths, "mz_lengths")
        seg_first, meta_first = self._encode_transformed_uint32_metadata(first_values, "mz_first_values")
        seg_delta, meta_delta = self._encode_transformed_uint32_metadata(delta_concat, "mz_delta_values")
        segments = {
            "mz_lengths": seg_lengths,
            "mz_first_values": seg_first,
            "mz_delta_values": seg_delta,
        }
        metadata = {
            "mz_lengths": meta_lengths,
            "mz_first_values": meta_first,
            "mz_delta_values": meta_delta,
        }
        if len(first_overflow_idx):
            seg_idx, meta_idx = self._encode_transformed_uint32_metadata(
                first_overflow_idx,
                "mz_first_overflow_idx",
            )
            seg_vals, meta_vals = _encode_uint64_values(
                first_overflow_vals,
                preferred_backend=self.backend,
                backend_candidates=self._candidate_backends("mz_first_overflow_vals"),
            )
            segments["mz_first_overflow_idx"] = seg_idx
            segments["mz_first_overflow_vals"] = seg_vals
            metadata["mz_first_overflow_idx"] = meta_idx
            metadata["mz_first_overflow_vals"] = meta_vals
        return segments, metadata

    def _decode_mz_library(self, segments: Dict[str, bytes], meta: Dict) -> List[np.ndarray]:
        lengths = self._decode_transformed_uint32_metadata(segments["mz_lengths"], meta["mz_lengths"])
        first_main = self._decode_transformed_uint32_metadata(segments["mz_first_values"], meta["mz_first_values"])
        first_overflow_idx = (
            self._decode_transformed_uint32_metadata(
                segments["mz_first_overflow_idx"],
                meta["mz_first_overflow_idx"],
            )
            if "mz_first_overflow_idx" in segments and "mz_first_overflow_idx" in meta
            else EMPTY_UINT32
        )
        first_overflow_vals = (
            _decode_uint64_values(segments["mz_first_overflow_vals"], meta["mz_first_overflow_vals"])
            if "mz_first_overflow_vals" in segments and "mz_first_overflow_vals" in meta
            else EMPTY_UINT64
        )
        first_values = _restore_uint64_anchor_overflow(first_main, first_overflow_idx, first_overflow_vals)
        delta_values = self._decode_transformed_uint32_metadata(segments["mz_delta_values"], meta["mz_delta_values"])

        out: List[np.ndarray] = []
        delta_pos = 0
        first_pos = 0
        for n in lengths:
            n = int(n)
            if n == 0:
                out.append(EMPTY_UINT64)
                continue
            first = int(first_values[first_pos])
            first_pos += 1
            if n == 1:
                q = np.array([first], dtype=np.uint64)
            else:
                tail = delta_values[delta_pos:delta_pos + n - 1].astype(np.uint64)
                delta_pos += n - 1
                q = np.empty(n, dtype=np.uint64)
                q[0] = first
                q[1:] = tail
                q = np.cumsum(q, dtype=np.uint64)
            out.append(q)
        return out

    def _full_scan_sidecar_cache_key(self, ms1_scans) -> str | None:
        if self.full_scan_sidecar_cache_dir is None:
            return None
        mode_values = (
            int(self.mz_precision),
            self.backend,
            bool(self.adaptive_uint32),
            bool(self.metadata_transform),
            bool(self.compact_uint32),
            self.adaptive_search_mode,
            int(self.adaptive_search_sample_count),
            bool(self.enable_full_scan_delta_same_prev),
            bool(self.enable_full_scan_delta2),
            bool(self.extended_metadata_adaptive),
            bool(self.enable_stream_backend_tuning),
        )
        store = getattr(ms1_scans, "store", None)
        global_indices = getattr(ms1_scans, "global_indices", None)
        if store is not None and global_indices is not None:
            try:
                root = Path(store.root).resolve()
                manifest = getattr(store, "manifest", {})
                mz_path = root / str(manifest.get("mz_data_file", "mz_data.bin"))
                stat = mz_path.stat()
                indices = np.asarray(global_indices, dtype=np.uint32)
                h = hashlib.sha256()
                h.update(b"trackcodec-full-scan-sidecar:memmap")
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
                for value in mode_values:
                    h.update(str(value).encode("utf-8"))
                    h.update(b"\0")
                return h.hexdigest()
            except Exception:
                return None
        try:
            n_scans = len(ms1_scans)
        except Exception:
            return None
        try:
            h = hashlib.sha256()
            h.update(b"trackcodec-full-scan-sidecar:sequence")
            h.update(str(int(n_scans)).encode("ascii"))
            h.update(b"\0")
            for value in mode_values:
                h.update(str(value).encode("utf-8"))
                h.update(b"\0")
            for idx, scan in enumerate(ms1_scans):
                scan_idx = int(scan.get("scan_idx", idx))
                mz_arr = np.asarray(scan["mz_array"], dtype=np.float64)
                h.update(str(scan_idx).encode("ascii"))
                h.update(b":")
                h.update(str(int(mz_arr.size)).encode("ascii"))
                h.update(b":")
                if mz_arr.size:
                    if not mz_arr.flags.c_contiguous:
                        mz_arr = np.ascontiguousarray(mz_arr, dtype=np.float64)
                    h.update(mz_arr.view(np.uint8))
                h.update(b"\0")
            return h.hexdigest()
        except Exception:
            return None

    def _load_full_scan_sidecar_cache(self, cache_key: str) -> Tuple[Dict[str, bytes], Dict] | None:
        if self.full_scan_sidecar_cache_dir is None:
            return None
        cache_path = self.full_scan_sidecar_cache_dir / f"{cache_key}.ms1_full_scan_sidecar.bin"
        if not cache_path.exists():
            return None
        try:
            header, segments = _unpack_segments(cache_path.read_bytes())
            if header.get("cache_kind") != "ms1_full_scan_sidecar":
                return None
            return segments, dict(header.get("segment_meta", {}))
        except Exception:
            return None

    def _save_full_scan_sidecar_cache(self, cache_key: str, segments: Dict[str, bytes], meta: Dict) -> None:
        if self.full_scan_sidecar_cache_dir is None:
            return
        try:
            cache_dir = self.full_scan_sidecar_cache_dir
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / f"{cache_key}.ms1_full_scan_sidecar.bin"
            tmp_path = cache_path.with_suffix(
                cache_path.suffix + f".tmp.{threading.get_ident()}.{time.time_ns()}"
            )
            header = {
                "cache_kind": "ms1_full_scan_sidecar",
                "mz_precision_decimals": int(self.mz_precision),
                "backend": self.backend,
                "segment_meta": meta,
            }
            tmp_path.write_bytes(_pack_segments(header, segments))
            tmp_path.replace(cache_path)
        except Exception:
            return

    def _encode_full_scan_sidecar(self, ms1_scans) -> Tuple[Dict[str, bytes], Dict]:
        cache_key = self._full_scan_sidecar_cache_key(ms1_scans)
        if cache_key is not None:
            cached = self._load_full_scan_sidecar_cache(cache_key)
            if cached is not None:
                return cached
        lengths = np.asarray([len(scan["mz_array"]) for scan in ms1_scans], dtype=np.uint32)
        scan_indices = np.asarray([int(scan.get("scan_idx", idx)) for idx, scan in enumerate(ms1_scans)], dtype=np.uint32)
        first_values = []
        delta_values = []
        same_prev_flags = []
        delta_values_same_prev = []
        prev_delta = None
        signed_delta_values = []
        has_signed_delta = False
        for scan in ms1_scans:
            mz_q = self._quantize_mz(np.asarray(scan["mz_array"], dtype=np.float64))
            if len(mz_q) == 0:
                continue
            first_values.append(int(mz_q[0]))
            if len(mz_q) > 1:
                mz_i64 = mz_q.astype(np.int64, copy=False)
                curr_delta_i64 = np.diff(mz_i64).astype(np.int64, copy=False)
                if np.any(curr_delta_i64 < 0) or np.any(curr_delta_i64.astype(np.uint64, copy=False) > UINT32_MAX):
                    has_signed_delta = True
                signed_delta_values.append(curr_delta_i64)
                curr_delta = curr_delta_i64.astype(np.uint32, copy=False) if not has_signed_delta else None
                if curr_delta is not None:
                    delta_values.append(curr_delta)
                if (
                    curr_delta is not None
                    and self.enable_full_scan_delta_same_prev
                    and prev_delta is not None
                    and np.array_equal(curr_delta, prev_delta)
                ):
                    same_prev_flags.append(1)
                else:
                    same_prev_flags.append(0)
                    if curr_delta is not None:
                        delta_values_same_prev.append(curr_delta)
                        prev_delta = curr_delta
                    else:
                        prev_delta = None
            else:
                prev_delta = None

        first_arr_u64 = np.asarray(first_values, dtype=np.uint64)
        first_arr, first_overflow_idx, first_overflow_vals = _split_uint64_anchor_overflow(first_arr_u64)
        if has_signed_delta:
            signed_delta_arr64 = (
                np.concatenate(signed_delta_values).astype(np.int64, copy=False)
                if signed_delta_values
                else np.array([], dtype=np.int64)
            )
            signed_delta_main, signed_delta_overflow_idx, signed_delta_overflow_vals = _encode_signed_int64_to_int32_main(
                signed_delta_arr64
            )
            delta_arr = _zigzag_encode_int32(signed_delta_main)
        else:
            signed_delta_arr64 = np.array([], dtype=np.int64)
            signed_delta_overflow_idx = EMPTY_UINT32
            signed_delta_overflow_vals = EMPTY_INT64
            delta_arr = np.concatenate(delta_values) if delta_values else np.array([], dtype=np.uint32)
        delta_arr_same_prev = (
            np.concatenate(delta_values_same_prev) if delta_values_same_prev else np.array([], dtype=np.uint32)
        )
        seg_scan_idx, meta_scan_idx = self._encode_transformed_uint32_metadata(scan_indices, "full_scan_scan_indices")
        seg_lengths, meta_lengths = self._encode_transformed_uint32_metadata(lengths, "full_scan_lengths")
        seg_first, meta_first = self._encode_transformed_uint32_metadata(first_arr, "full_scan_first_values")
        seg_delta, meta_delta = self._encode_transformed_uint32_metadata(delta_arr, "full_scan_delta_values")
        payloads = {
            "full_scan_scan_indices": seg_scan_idx,
            "full_scan_lengths": seg_lengths,
            "full_scan_first_values": seg_first,
            "full_scan_delta_values": seg_delta,
        }
        payload_meta = {
            "full_scan_scan_indices": meta_scan_idx,
            "full_scan_lengths": meta_lengths,
            "full_scan_first_values": meta_first,
            "full_scan_delta_values": meta_delta,
            "full_scan_count": int(len(ms1_scans)),
            "full_scan_delta_mode": "signed_delta_stream" if has_signed_delta else "full_mz_delta_stream",
        }
        if has_signed_delta:
            payload_meta["full_scan_signed_delta_count"] = int(len(signed_delta_arr64))
            payload_meta["full_scan_signed_delta_overflow_count"] = int(len(signed_delta_overflow_idx))
            if len(signed_delta_overflow_idx):
                seg_signed_idx, meta_signed_idx = self._encode_transformed_uint32_metadata(
                    signed_delta_overflow_idx,
                    "full_scan_signed_delta_overflow_idx",
                )
                signed_vals_bytes = np.asarray(signed_delta_overflow_vals, dtype=np.int64).tobytes()
                best_backend = self.backend
                seg_signed_vals = compress(signed_vals_bytes, self.backend)
                for candidate_backend in self._candidate_backends("full_scan_signed_delta_overflow_vals"):
                    candidate = compress(signed_vals_bytes, candidate_backend)
                    if len(candidate) < len(seg_signed_vals):
                        seg_signed_vals = candidate
                        best_backend = candidate_backend
                payloads["full_scan_signed_delta_overflow_idx"] = seg_signed_idx
                payloads["full_scan_signed_delta_overflow_vals"] = seg_signed_vals
                payload_meta["full_scan_signed_delta_overflow_idx"] = meta_signed_idx
                payload_meta["full_scan_signed_delta_overflow_vals"] = {
                    "count": int(len(signed_delta_overflow_vals)),
                    "outer_backend": best_backend,
                }
        if len(first_overflow_idx):
            seg_first_idx, meta_first_idx = self._encode_transformed_uint32_metadata(
                first_overflow_idx,
                "full_scan_first_overflow_idx",
            )
            seg_first_vals, meta_first_vals = _encode_uint64_values(
                first_overflow_vals,
                preferred_backend=self.backend,
                backend_candidates=self._candidate_backends("full_scan_first_overflow_vals"),
            )
            payloads["full_scan_first_overflow_idx"] = seg_first_idx
            payloads["full_scan_first_overflow_vals"] = seg_first_vals
            payload_meta["full_scan_first_overflow_idx"] = meta_first_idx
            payload_meta["full_scan_first_overflow_vals"] = meta_first_vals
        best_delta_mode = "signed_delta_stream" if has_signed_delta else "full_mz_delta_stream"
        best_delta_total_bytes = len(seg_delta) + len(json.dumps(meta_delta, separators=(",", ":")))
        best_delta_payload_overrides = {
            "full_scan_delta_values": seg_delta,
        }
        best_delta_meta_overrides = {
            "full_scan_delta_values": meta_delta,
        }
        if (not has_signed_delta) and self.enable_full_scan_delta_same_prev and len(same_prev_flags) > 0:
            seg_same_prev, meta_same_prev = _encode_small_uint_stream(
                np.asarray(same_prev_flags, dtype=np.uint8),
                self.backend,
                bits=1,
                backend_candidates=self._candidate_backends("full_scan_delta_values"),
            )
            seg_delta_same_prev, meta_delta_same_prev = self._encode_transformed_uint32_metadata(
                delta_arr_same_prev,
                "full_scan_delta_values",
            )
            concat_meta_bytes = len(json.dumps(meta_delta, separators=(",", ":")))
            same_prev_meta_bytes = (
                len(json.dumps(meta_delta_same_prev, separators=(",", ":")))
                + len(json.dumps(meta_same_prev, separators=(",", ":")))
            )
            concat_total_bytes = len(seg_delta) + concat_meta_bytes
            same_prev_total_bytes = len(seg_same_prev) + len(seg_delta_same_prev) + same_prev_meta_bytes
            if same_prev_total_bytes < best_delta_total_bytes:
                best_delta_mode = "reuse_previous_delta_stream"
                best_delta_total_bytes = same_prev_total_bytes
                best_delta_payload_overrides = {
                    "full_scan_delta_values": seg_delta_same_prev,
                    "full_scan_delta_same_prev_flags": seg_same_prev,
                }
                best_delta_meta_overrides = {
                    "full_scan_delta_values": meta_delta_same_prev,
                    "full_scan_delta_same_prev_flags": meta_same_prev,
                }
        if (not has_signed_delta) and self.enable_full_scan_delta2 and len(delta_values) > 0:
            delta2_first_values_i64 = np.asarray([int(delta[0]) for delta in delta_values], dtype=np.int64)
            delta2_second_values_i64 = (
                np.concatenate(
                    [
                        np.diff(delta.astype(np.int64)).astype(np.int64)
                        for delta in delta_values
                        if len(delta) > 1
                    ]
                )
                if any(len(delta) > 1 for delta in delta_values)
                else np.array([], dtype=np.int64)
            )
            int32_min = np.iinfo(np.int32).min
            int32_max = np.iinfo(np.int32).max
            if (
                np.all((delta2_first_values_i64 >= int32_min) & (delta2_first_values_i64 <= int32_max))
                and np.all((delta2_second_values_i64 >= int32_min) & (delta2_second_values_i64 <= int32_max))
            ):
                delta2_first_values = delta2_first_values_i64.astype(np.int32, copy=False)
                delta2_second_values = delta2_second_values_i64.astype(np.int32, copy=False)
                seg_delta2_first, meta_delta2_first = self._encode_transformed_uint32_metadata(
                    _zigzag_encode_int32(delta2_first_values),
                    "full_scan_first_values",
                )
                seg_delta2_second, meta_delta2_second = self._encode_transformed_uint32_metadata(
                    _zigzag_encode_int32(delta2_second_values),
                    "full_scan_delta_values",
                )
                delta2_meta_bytes = (
                    len(json.dumps(meta_delta2_first, separators=(",", ":")))
                    + len(json.dumps(meta_delta2_second, separators=(",", ":")))
                )
                delta2_total_bytes = len(seg_delta2_first) + len(seg_delta2_second) + delta2_meta_bytes
                if delta2_total_bytes < best_delta_total_bytes:
                    best_delta_mode = "second_order_delta_stream"
                    best_delta_total_bytes = delta2_total_bytes
                    best_delta_payload_overrides = {
                        "full_scan_delta2_first_deltas": seg_delta2_first,
                        "full_scan_delta2_second_diffs": seg_delta2_second,
                    }
                    best_delta_meta_overrides = {
                        "full_scan_delta2_first_deltas": meta_delta2_first,
                        "full_scan_delta2_second_diffs": meta_delta2_second,
                    }
        payloads.pop("full_scan_delta_values", None)
        payload_meta.pop("full_scan_delta_values", None)
        payloads.update(best_delta_payload_overrides)
        payload_meta.update(best_delta_meta_overrides)
        payload_meta["full_scan_delta_mode"] = best_delta_mode
        out_segments = {
            **payloads,
        }
        out_meta = {
            **payload_meta,
        }
        if cache_key is not None:
            self._save_full_scan_sidecar_cache(cache_key, out_segments, out_meta)
        return out_segments, out_meta

    def iter_full_scan_sidecar_arrays(self, segments: Dict[str, bytes], meta: Dict):
        scan_indices = self._decode_transformed_uint32_metadata(
            segments["full_scan_scan_indices"],
            meta["full_scan_scan_indices"],
        )
        lengths = self._decode_transformed_uint32_metadata(segments["full_scan_lengths"], meta["full_scan_lengths"])
        first_main = self._decode_transformed_uint32_metadata(
            segments["full_scan_first_values"],
            meta["full_scan_first_values"],
        )
        first_overflow_idx = (
            self._decode_transformed_uint32_metadata(
                segments["full_scan_first_overflow_idx"],
                meta["full_scan_first_overflow_idx"],
            )
            if "full_scan_first_overflow_idx" in segments and "full_scan_first_overflow_idx" in meta
            else EMPTY_UINT32
        )
        first_overflow_vals = (
            _decode_uint64_values(
                segments["full_scan_first_overflow_vals"],
                meta["full_scan_first_overflow_vals"],
            )
            if "full_scan_first_overflow_vals" in segments and "full_scan_first_overflow_vals" in meta
            else EMPTY_UINT64
        )
        first_values = _restore_uint64_anchor_overflow(first_main, first_overflow_idx, first_overflow_vals)
        delta_mode = str(meta["full_scan_delta_mode"])
        delta_values = None
        same_prev_flags = None
        signed_delta_values_i64 = None
        if delta_mode == "reuse_previous_delta_stream":
            if "full_scan_delta_same_prev_flags" not in segments or "full_scan_delta_same_prev_flags" not in meta:
                raise ValueError("Corrupt TrackCodec MS1 stream: missing full_scan_delta_same_prev_flags")
            delta_values = self._decode_transformed_uint32_metadata(
                segments["full_scan_delta_values"],
                meta["full_scan_delta_values"],
            )
            same_prev_flags = _decode_small_uint_stream(
                segments["full_scan_delta_same_prev_flags"],
                meta["full_scan_delta_same_prev_flags"],
            )
        elif delta_mode == "signed_delta_stream":
            if "full_scan_delta_values" not in segments or "full_scan_delta_values" not in meta:
                raise ValueError("Corrupt TrackCodec MS1 stream: missing full_scan_delta_values")
            signed_delta_main = _zigzag_decode_uint32(
                self._decode_transformed_uint32_metadata(
                    segments["full_scan_delta_values"],
                    meta["full_scan_delta_values"],
                )
            ).astype(np.int32, copy=False)
            signed_delta_overflow_count = int(meta.get("full_scan_signed_delta_overflow_count", 0))
            if signed_delta_overflow_count > 0:
                if (
                    "full_scan_signed_delta_overflow_idx" not in segments
                    or "full_scan_signed_delta_overflow_idx" not in meta
                    or "full_scan_signed_delta_overflow_vals" not in segments
                    or "full_scan_signed_delta_overflow_vals" not in meta
                ):
                    raise ValueError("Corrupt TrackCodec MS1 stream: missing signed full-scan delta overflow payload")
                signed_delta_overflow_idx = self._decode_transformed_uint32_metadata(
                    segments["full_scan_signed_delta_overflow_idx"],
                    meta["full_scan_signed_delta_overflow_idx"],
                )
                signed_delta_overflow_meta = meta["full_scan_signed_delta_overflow_vals"]
                raw = decompress(
                    segments["full_scan_signed_delta_overflow_vals"],
                    signed_delta_overflow_meta["outer_backend"],
                )
                signed_delta_overflow_vals = np.frombuffer(raw, dtype=np.int64).copy()
                if len(signed_delta_overflow_vals) != signed_delta_overflow_count:
                    raise ValueError("Corrupt TrackCodec MS1 stream: signed full-scan delta overflow count mismatch")
            else:
                signed_delta_overflow_idx = EMPTY_UINT32
                signed_delta_overflow_vals = EMPTY_INT64
            signed_delta_values_i64 = _decode_signed_int64_from_int32_main(
                signed_delta_main,
                signed_delta_overflow_idx,
                signed_delta_overflow_vals,
            )
        elif delta_mode == "second_order_delta_stream":
            if "full_scan_delta2_first_deltas" not in segments or "full_scan_delta2_first_deltas" not in meta:
                raise ValueError("Corrupt TrackCodec MS1 stream: missing full_scan_delta2_first_deltas")
            if "full_scan_delta2_second_diffs" not in segments or "full_scan_delta2_second_diffs" not in meta:
                raise ValueError("Corrupt TrackCodec MS1 stream: missing full_scan_delta2_second_diffs")
            delta2_first_values = _zigzag_decode_uint32(
                self._decode_transformed_uint32_metadata(
                    segments["full_scan_delta2_first_deltas"],
                    meta["full_scan_delta2_first_deltas"],
                )
            ).astype(np.int32)
            delta2_second_values = _zigzag_decode_uint32(
                self._decode_transformed_uint32_metadata(
                    segments["full_scan_delta2_second_diffs"],
                    meta["full_scan_delta2_second_diffs"],
                )
            ).astype(np.int32)
        else:
            delta_values = self._decode_transformed_uint32_metadata(
                segments["full_scan_delta_values"],
                meta["full_scan_delta_values"],
            )
        delta_pos = 0
        first_pos = 0
        same_prev_pos = 0
        delta2_first_pos = 0
        delta2_second_pos = 0
        prev_delta = None
        scale = float(baseline_ms1_codec.MZ_PRECISION_MAP[self.mz_precision])
        for local_pos, n in enumerate(lengths):
            scan_idx = int(scan_indices[local_pos]) if local_pos < len(scan_indices) else int(local_pos)
            n = int(n)
            if n == 0:
                yield scan_idx, np.array([], dtype=np.float64)
                continue
            first = int(first_values[first_pos])
            first_pos += 1
            if n == 1:
                q = np.array([first], dtype=np.uint64)
                prev_delta = None
            else:
                if delta_mode == "reuse_previous_delta_stream":
                    if same_prev_flags is None or same_prev_pos >= len(same_prev_flags):
                        raise ValueError("Corrupt TrackCodec MS1 stream: missing same-prev full-scan delta flag")
                    use_same_prev = int(same_prev_flags[same_prev_pos]) == 1
                    same_prev_pos += 1
                    if use_same_prev:
                        if prev_delta is None or len(prev_delta) != n - 1:
                            raise ValueError("Corrupt TrackCodec MS1 stream: invalid same-prev full-scan delta reference")
                        tail = prev_delta.astype(np.uint64, copy=False)
                    else:
                        tail = delta_values[delta_pos:delta_pos + n - 1].astype(np.uint64)
                        delta_pos += n - 1
                        prev_delta = tail.astype(np.uint32, copy=True)
                elif delta_mode == "second_order_delta_stream":
                    if delta2_first_pos >= len(delta2_first_values):
                        raise ValueError("Corrupt TrackCodec MS1 stream: missing full-scan delta2 first delta")
                    first_delta = int(delta2_first_values[delta2_first_pos])
                    delta2_first_pos += 1
                    delta64 = np.empty(n - 1, dtype=np.int64)
                    delta64[0] = first_delta
                    if n - 1 > 1:
                        need = n - 2
                        if delta2_second_pos + need > len(delta2_second_values):
                            raise ValueError("Corrupt TrackCodec MS1 stream: missing full-scan delta2 second diffs")
                        second = delta2_second_values[delta2_second_pos:delta2_second_pos + need].astype(np.int64)
                        delta2_second_pos += need
                        delta64[1:] = np.cumsum(second, dtype=np.int64) + first_delta
                    tail = delta64.astype(np.uint64)
                elif delta_mode == "signed_delta_stream":
                    if signed_delta_values_i64 is None:
                        raise ValueError("Corrupt TrackCodec MS1 stream: missing signed full-scan deltas")
                    need = n - 1
                    if delta_pos + need > len(signed_delta_values_i64):
                        raise ValueError("Corrupt TrackCodec MS1 stream: signed full-scan delta count mismatch")
                    tail_i64 = signed_delta_values_i64[delta_pos:delta_pos + need]
                    delta_pos += need
                    q_i64 = np.empty(n, dtype=np.int64)
                    q_i64[0] = first
                    q_i64[1:] = np.cumsum(tail_i64, dtype=np.int64) + int(first)
                    if np.any(q_i64 < 0):
                        raise ValueError("Corrupt TrackCodec MS1 stream: negative quantized m/z in signed full-scan sidecar")
                    q = q_i64.astype(np.uint64, copy=False)
                else:
                    tail = delta_values[delta_pos:delta_pos + n - 1].astype(np.uint64)
                    delta_pos += n - 1
                    q = np.empty(n, dtype=np.uint64)
                    q[0] = first
                    q[1:] = tail
                    q = np.cumsum(q, dtype=np.uint64)
            yield scan_idx, q.astype(np.float64) / scale

    def _decode_full_scan_sidecar(self, segments: Dict[str, bytes], meta: Dict) -> Tuple[np.ndarray, List[np.ndarray]]:
        scan_indices: List[int] = []
        arrays: List[np.ndarray] = []
        for scan_idx, mz_array in self.iter_full_scan_sidecar_arrays(segments, meta):
            scan_indices.append(int(scan_idx))
            arrays.append(mz_array)
        return np.asarray(scan_indices, dtype=np.uint32), arrays

    def _encode_mz_model_segments(self, rep: Dict) -> Tuple[Dict[str, bytes], Dict]:
        segments = {}
        meta = {}

        offsets_payload, offsets_meta = _encode_uint32_stream_best(
            _zigzag_encode_int32(rep["mz_model_offsets"]),
            self.backend,
            use_pfor=False,
            pfor_codec=self.pfor_codec,
            zero_rle=False,
            backend_candidates=self._candidate_backends("mz_model_offsets"),
            try_pfor=self.adaptive_uint32 and len(rep["mz_model_offsets"]) >= 256,
            try_zero_rle=False,
            allow_bitpack=self.compact_uint32,
            search_mode=self.adaptive_search_mode,
            sample_count=self.adaptive_search_sample_count,
        )
        segments["mz_model_offsets"] = offsets_payload
        meta["mz_model_offsets"] = offsets_meta

        step_values = np.asarray(rep["mz_model_steps"], dtype=np.int32)
        if len(step_values) == 0 or np.max(np.abs(step_values), initial=0) == 0:
            meta["mz_model_steps"] = {
                "all_zero": True,
                "count": int(len(step_values)),
            }
        else:
            max_abs_step = int(np.max(np.abs(step_values), initial=0))
            if max_abs_step <= 1:
                encoded_steps = (step_values + 1).astype(np.uint8)
                steps_payload, steps_meta = _encode_small_uint_stream(
                    encoded_steps,
                    self.backend,
                    bits=2,
                    backend_candidates=self._candidate_backends("mz_model_offsets"),
                )
                steps_meta["small_uint"] = True
                steps_meta["step_shift"] = 1
                segments["mz_model_steps"] = steps_payload
                meta["mz_model_steps"] = steps_meta
            else:
                steps_payload, steps_meta = _encode_uint32_stream_best(
                    _zigzag_encode_int32(step_values),
                    self.backend,
                    use_pfor=False,
                    pfor_codec=self.pfor_codec,
                    zero_rle=False,
                    backend_candidates=self._candidate_backends("mz_model_offsets"),
                    try_pfor=self.adaptive_uint32 and len(step_values) >= 256,
                    try_zero_rle=False,
                    allow_bitpack=self.compact_uint32,
                    search_mode=self.adaptive_search_mode,
                    sample_count=self.adaptive_search_sample_count,
                )
                segments["mz_model_steps"] = steps_payload
                meta["mz_model_steps"] = steps_meta

        length_payload, length_meta = self._encode_transformed_uint32_metadata(
            rep["mz_model_residual_lengths"],
            "mz_model_residual_lengths",
        )
        segments["mz_model_residual_lengths"] = length_payload
        meta["mz_model_residual_lengths"] = length_meta

        if rep.get("mz_residual_concat_i32") is not None:
            residual_concat = np.asarray(rep["mz_residual_concat_i32"], dtype=np.int32)
        else:
            residual_concat = (
                np.concatenate(rep["mz_residual_segments"]).astype(np.int32)
                if rep["mz_residual_segments"]
                else np.array([], dtype=np.int32)
            )
        residual_payload, residual_meta = _encode_uint32_stream_best(
            _zigzag_encode_int32(residual_concat),
            self.backend,
            use_pfor=False,
            pfor_codec=self.pfor_codec,
            zero_rle=self._should_use_zero_rle("mz_residual_values", int(len(residual_concat))),
            backend_candidates=self._candidate_backends("mz_residual_values"),
            try_pfor=self.adaptive_uint32 and len(residual_concat) >= 512,
            try_zero_rle=self.adaptive_uint32,
            allow_bitpack=self.compact_uint32,
            search_mode=self.adaptive_search_mode,
            sample_count=self.adaptive_search_sample_count,
        )
        segments["mz_residual_values"] = residual_payload
        meta["mz_residual_values"] = residual_meta
        return segments, meta

    def _decode_mz_model_segments(self, segments: Dict[str, bytes], meta: Dict) -> Tuple[np.ndarray, np.ndarray, List[np.ndarray]]:
        if "mz_model_offsets" not in segments:
            return np.array([], dtype=np.int32), np.array([], dtype=np.int32), []
        offsets = _zigzag_decode_uint32(_decode_uint32_stream(segments["mz_model_offsets"], meta["mz_model_offsets"]))
        if meta.get("mz_model_steps", {}).get("all_zero", False):
            steps = np.zeros(int(meta["mz_model_steps"].get("count", len(offsets))), dtype=np.int32)
        elif "mz_model_steps" in segments and meta.get("mz_model_steps", {}).get("small_uint", False):
            enc = _decode_small_uint_stream(segments["mz_model_steps"], meta["mz_model_steps"]).astype(np.int32)
            steps = enc - int(meta["mz_model_steps"].get("step_shift", 0))
        else:
            steps = (
                _zigzag_decode_uint32(_decode_uint32_stream(segments["mz_model_steps"], meta["mz_model_steps"]))
                if "mz_model_steps" in segments
                else np.zeros(len(offsets), dtype=np.int32)
            )
        residual_lengths = self._decode_transformed_uint32_metadata(
            segments["mz_model_residual_lengths"],
            meta["mz_model_residual_lengths"],
        )
        residual_encoded = _decode_uint32_stream(segments["mz_residual_values"], meta["mz_residual_values"])
        residual_concat = _zigzag_decode_uint32(residual_encoded).astype(np.int32)
        residual_arrays: List[np.ndarray] = []
        pos = 0
        for n in residual_lengths:
            n = int(n)
            if n == 0:
                residual_arrays.append(np.array([], dtype=np.int32))
                continue
            residual_arrays.append(residual_concat[pos:pos + n].copy())
            pos += n
        return offsets, steps, residual_arrays

    def _encode_transformed_uint32_metadata(self, arr: np.ndarray, stream_name: str) -> Tuple[bytes, Dict]:
        transforms = self._candidate_transforms(stream_name)
        force_full_search = self._force_full_uint32_search(stream_name, arr)
        stream_search_mode = "full" if force_full_search else self.adaptive_search_mode
        use_top2_full_search = self._use_top2_uint32_full_search(stream_name, arr, transforms)
        if (
            stream_search_mode == "converged"
            and len(transforms) > 1
            and len(arr) > self.adaptive_search_sample_count
        ):
            ranked_transforms = []
            for transform in transforms:
                try:
                    transformed = _transform_uint32_for_compression(arr, transform)
                except OverflowError:
                    if transform == "raw":
                        raise
                    continue
                sample = _sample_uint32_for_search(transformed, self.adaptive_search_sample_count)
                sample_payload, sample_meta = _encode_uint32_stream_best(
                    sample,
                    self.backend,
                    use_pfor=self._should_use_pfor(stream_name, int(len(sample))),
                    pfor_codec=self.pfor_codec,
                    zero_rle=False,
                    backend_candidates=self._candidate_backends(stream_name),
                    try_pfor=self.adaptive_uint32 and len(sample) >= 256,
                    try_zero_rle=False,
                    allow_bitpack=self.compact_uint32,
                    search_mode="full",
                    sample_count=self.adaptive_search_sample_count,
                )
                score = (len(sample_payload), 0 if transform == "raw" else 1)
                ranked_transforms.append((score, transform, sample_meta))
            ranked_transforms.sort(key=lambda item: item[0])

            if not ranked_transforms:
                best_transform = "raw"
                transformed = _transform_uint32_for_compression(arr, best_transform)
                payload, payload_meta = _encode_uint32_stream_best(
                    transformed,
                    self.backend,
                    use_pfor=self._should_use_pfor(stream_name, int(len(transformed))),
                    pfor_codec=self.pfor_codec,
                    zero_rle=False,
                    backend_candidates=self._candidate_backends(stream_name),
                    try_pfor=self.adaptive_uint32 and len(transformed) >= 256,
                    try_zero_rle=False,
                    allow_bitpack=self.compact_uint32,
                    search_mode="full",
                    sample_count=self.adaptive_search_sample_count,
                )
                meta = dict(payload_meta)
                meta["transform"] = best_transform
                return payload, meta

            if use_top2_full_search:
                best_payload = None
                best_meta = None
                best_score = None
                for _, transform, _sample_meta in ranked_transforms[:2]:
                    transformed = _transform_uint32_for_compression(arr, transform)
                    payload, payload_meta = _encode_uint32_stream_topk_from_sample(
                        transformed,
                        self.backend,
                        use_pfor=self._should_use_pfor(stream_name, int(len(transformed))),
                        pfor_codec=self.pfor_codec,
                        zero_rle=False,
                        backend_candidates=self._candidate_backends(stream_name),
                        try_pfor=self.adaptive_uint32 and len(transformed) >= 256,
                        try_zero_rle=False,
                        allow_bitpack=self.compact_uint32,
                        sample_count=self.adaptive_search_sample_count,
                        top_k=2,
                    )
                    meta = dict(payload_meta)
                    meta["transform"] = transform
                    score = (len(payload), 0 if transform == "raw" else 1)
                    if best_score is None or score < best_score:
                        best_payload = payload
                        best_meta = meta
                        best_score = score
                if best_payload is None or best_meta is None:
                    raise ValueError(f"No uint32 metadata candidates were available for stream {stream_name!r}")
                return best_payload, best_meta

            _score, best_transform, best_strategy_meta = ranked_transforms[0]
            transformed = _transform_uint32_for_compression(arr, best_transform)
            payload, payload_meta = _encode_uint32_with_strategy(
                transformed,
                backend=best_strategy_meta["outer_backend"],
                use_pfor=bool(best_strategy_meta.get("use_pfor", False)),
                pfor_codec=self.pfor_codec,
                zero_rle=bool(best_strategy_meta.get("zero_rle", False)),
                storage_kind=best_strategy_meta.get("storage_kind", "plain"),
            )
            meta = dict(payload_meta)
            meta["transform"] = best_transform
            return payload, meta

        best_payload = None
        best_meta = None
        best_score = None
        for transform in transforms:
            try:
                transformed = _transform_uint32_for_compression(arr, transform)
            except OverflowError:
                if transform == "raw":
                    raise
                continue
            payload, payload_meta = _encode_uint32_stream_best(
                transformed,
                self.backend,
                use_pfor=self._should_use_pfor(stream_name, int(len(transformed))),
                pfor_codec=self.pfor_codec,
                zero_rle=False,
                backend_candidates=self._candidate_backends(stream_name),
                try_pfor=self.adaptive_uint32 and len(transformed) >= 256,
                try_zero_rle=False,
                allow_bitpack=self.compact_uint32,
                search_mode=stream_search_mode,
                sample_count=self.adaptive_search_sample_count,
            )
            meta = dict(payload_meta)
            meta["transform"] = transform
            score = (len(payload), 0 if transform == "raw" else 1)
            if best_score is None or score < best_score:
                best_payload = payload
                best_meta = meta
                best_score = score
        if best_payload is None or best_meta is None:
            transformed = _transform_uint32_for_compression(arr, "raw")
            best_payload, payload_meta = _encode_uint32_stream_best(
                transformed,
                self.backend,
                use_pfor=self._should_use_pfor(stream_name, int(len(transformed))),
                pfor_codec=self.pfor_codec,
                zero_rle=False,
                backend_candidates=self._candidate_backends(stream_name),
                try_pfor=self.adaptive_uint32 and len(transformed) >= 256,
                try_zero_rle=False,
                allow_bitpack=self.compact_uint32,
                search_mode=stream_search_mode,
                sample_count=self.adaptive_search_sample_count,
            )
            best_meta = dict(payload_meta)
            best_meta["transform"] = "raw"
        return best_payload, best_meta

    def _decode_transformed_uint32_metadata(self, payload: bytes, meta: Dict) -> np.ndarray:
        encoded = _decode_uint32_stream(payload, meta)
        return _inverse_uint32_transform(encoded, meta.get("transform", "raw"))

    def _encode_int16_stream_best(self, arr: np.ndarray, stream_name: str) -> Tuple[bytes, Dict]:
        raw = np.asarray(arr, dtype=np.int16).tobytes()
        best_backend = None
        best_payload = None
        for backend in self._candidate_backends(stream_name):
            candidate = compress(raw, backend)
            if best_payload is None or len(candidate) < len(best_payload):
                best_payload = candidate
                best_backend = backend
        return best_payload, {
            "storage_kind": "int16_raw",
            "outer_backend": best_backend,
            "count": int(len(arr)),
        }

    def _encode_sparse_l0_signed_int32_stream(self, arr: np.ndarray) -> Tuple[bytes, Dict]:
        values = np.asarray(arr, dtype=np.int32)
        count = int(len(values))
        if count == 0:
            return _pack_sparse_l0_delta_payload(b"", b""), {
                "storage_kind": SPARSE_L0_DELTA_STORAGE_KIND,
                "count": 0,
                "nnz_count": 0,
                "position_meta": {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0},
                "value_meta": {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0},
            }

        nonzero_positions = np.flatnonzero(values).astype(np.uint64)
        nnz_count = int(len(nonzero_positions))
        if nnz_count == 0:
            pos_deltas = np.array([], dtype=np.uint32)
            nonzero_uint32 = np.array([], dtype=np.uint32)
        else:
            pos_deltas64 = np.empty(nnz_count, dtype=np.uint64)
            pos_deltas64[0] = nonzero_positions[0]
            if nnz_count > 1:
                pos_deltas64[1:] = nonzero_positions[1:] - nonzero_positions[:-1]
            if np.max(pos_deltas64, initial=0) > UINT32_MAX:
                raise ValueError("Sparse-L0 position delta exceeds uint32 range")
            pos_deltas = pos_deltas64.astype(np.uint32, copy=False)
            nonzero_uint32 = _zigzag_encode_int32(values[nonzero_positions.astype(np.int64)])

        position_payload, position_meta = _encode_uint32_stream_best(
            pos_deltas,
            self.backend,
            use_pfor=self._should_use_pfor("delta_intensity_l0_positions", nnz_count),
            pfor_codec=self.pfor_codec,
            zero_rle=False,
            backend_candidates=self._candidate_backends("delta_intensity_l0_positions"),
            try_pfor=self.adaptive_uint32 and self.adaptive_intensity_search and nnz_count >= 512,
            try_zero_rle=False,
            search_mode=self.adaptive_search_mode if self.adaptive_intensity_search else "full",
            sample_count=self.adaptive_search_sample_count,
        )
        value_payload, value_meta = _encode_uint32_stream_best(
            nonzero_uint32,
            self.backend,
            use_pfor=self._should_use_pfor("delta_intensity_l0_values", nnz_count),
            pfor_codec=self.pfor_codec,
            zero_rle=False,
            backend_candidates=self._candidate_backends("delta_intensity_l0_values"),
            try_pfor=self.adaptive_uint32 and self.adaptive_intensity_search and nnz_count >= 512,
            try_zero_rle=False,
            search_mode=self.adaptive_search_mode if self.adaptive_intensity_search else "full",
            sample_count=self.adaptive_search_sample_count,
        )
        payload = _pack_sparse_l0_delta_payload(position_payload, value_payload)
        return payload, {
            "storage_kind": SPARSE_L0_DELTA_STORAGE_KIND,
            "count": count,
            "nnz_count": nnz_count,
            "position_meta": position_meta,
            "value_meta": value_meta,
        }

    def _decode_sparse_l0_signed_int32_stream(self, payload: bytes, meta: Dict) -> np.ndarray:
        count = int(meta["count"])
        nnz_count = int(meta.get("nnz_count", 0))
        position_payload, value_payload = _unpack_sparse_l0_delta_payload(payload)
        position_deltas = _decode_uint32_stream(
            position_payload,
            meta["position_meta"],
            max_workers=self.intensity_decode_workers,
        )
        value_uint32 = _decode_uint32_stream(
            value_payload,
            meta["value_meta"],
            max_workers=self.intensity_decode_workers,
        )
        if len(position_deltas) != nnz_count or len(value_uint32) != nnz_count:
            raise ValueError(
                "Sparse-L0 delta stream metadata mismatch: "
                f"nnz={nnz_count}, positions={len(position_deltas)}, values={len(value_uint32)}"
            )
        decoded = np.zeros(count, dtype=np.int32)
        if nnz_count == 0:
            return decoded
        positions = np.cumsum(position_deltas.astype(np.uint64, copy=False), dtype=np.uint64)
        if int(positions[-1]) >= count:
            raise ValueError("Sparse-L0 delta position exceeds decoded stream length")
        if nnz_count > 1 and np.any(positions[1:] <= positions[:-1]):
            raise ValueError("Sparse-L0 delta positions are not strictly increasing")
        decoded[positions.astype(np.int64, copy=False)] = _zigzag_decode_uint32(value_uint32).astype(np.int32, copy=False)
        return decoded

    def _encode_metadata_segments(self, rep: Dict) -> Tuple[Dict[str, bytes], Dict]:
        segments = {}
        meta = {}
        names = [
            "track_lengths",
            "scan_indices",
        ]
        if not rep.get("omit_island_mz", False):
            names.append("mz_ref_indices")
        for name in names:
            source = rep[name]
            if source.dtype == np.uint8:
                max_value = int(source.max(initial=0))
                bits = 1 if max_value <= 1 else 2 if max_value <= 3 else 4 if max_value <= 15 else 8
                payload, payload_meta = _encode_small_uint_stream(
                    source,
                    self.backend,
                    bits=bits,
                    backend_candidates=self._candidate_backends(name),
                )
                payload_meta["small_uint"] = True
            else:
                payload, payload_meta = self._encode_transformed_uint32_metadata(source, name)
            segments[name] = payload
            meta[name] = payload_meta

        if len(rep["cross_track_ref_indices"]):
            payload, payload_meta = self._encode_transformed_uint32_metadata(
                rep["cross_track_ref_indices"],
                "cross_track_ref_indices",
            )
            segments["cross_track_ref_indices"] = payload
            meta["cross_track_ref_indices"] = payload_meta

        if not self.omit_n_points:
            payload, payload_meta = self._encode_transformed_uint32_metadata(rep["n_points"], "n_points")
            segments["n_points"] = payload
            meta["n_points"] = payload_meta

        if not self.omit_array_starts:
            payload, payload_meta = self._encode_transformed_uint32_metadata(rep["array_starts"], "array_starts")
            segments["array_starts"] = payload
            meta["array_starts"] = payload_meta

        delta_offsets = rep["delta_ref_offsets"]
        if len(delta_offsets) > 0 and np.max(delta_offsets, initial=0) == 1 and self.enable_metadata_trim:
            meta["delta_ref_offsets"] = {
                "all_one": True,
                "count": int(len(delta_offsets)),
            }
        elif np.max(delta_offsets, initial=0) > 1 or self.delta_ref_window > 1:
            payload, payload_meta = _encode_small_uint_stream(
                delta_offsets,
                self.backend,
                bits=4 if int(np.max(delta_offsets, initial=0)) > 3 else 2 if int(np.max(delta_offsets, initial=0)) > 1 else 1,
                backend_candidates=self._candidate_backends("int_ref_offsets"),
            )
            payload_meta["small_uint"] = True
            segments["delta_ref_offsets"] = payload
            meta["delta_ref_offsets"] = payload_meta

        kind_names = ["int_kinds"]
        if not rep.get("omit_island_mz", False):
            kind_names.insert(0, "mz_kinds")
        for name in kind_names:
            source = rep[name]
            max_value = int(source.max(initial=0))
            bits = 1 if max_value <= 1 else 2 if max_value <= 3 else 4 if max_value <= 15 else 8
            payload, payload_meta = _encode_small_uint_stream(
                source,
                self.backend,
                bits=bits,
                backend_candidates=self._candidate_backends(name),
            )
            payload_meta["small_uint"] = True
            segments[name] = payload
            meta[name] = payload_meta
        return segments, meta

    def _decode_metadata_segments(self, segments: Dict[str, bytes], meta: Dict) -> Dict[str, np.ndarray]:
        out = {}
        for name in [
            "track_lengths",
            "scan_indices",
        ]:
            if meta[name].get("small_uint"):
                out[name] = _decode_small_uint_stream(segments[name], meta[name])
            else:
                out[name] = self._decode_transformed_uint32_metadata(segments[name], meta[name])
        if "mz_ref_indices" in segments and "mz_ref_indices" in meta:
            if meta["mz_ref_indices"].get("small_uint"):
                out["mz_ref_indices"] = _decode_small_uint_stream(segments["mz_ref_indices"], meta["mz_ref_indices"])
            else:
                out["mz_ref_indices"] = self._decode_transformed_uint32_metadata(segments["mz_ref_indices"], meta["mz_ref_indices"])
        else:
            out["mz_ref_indices"] = np.zeros(len(out["scan_indices"]), dtype=np.uint32)
        if "cross_track_ref_indices" in segments and "cross_track_ref_indices" in meta:
            out["cross_track_ref_indices"] = self._decode_transformed_uint32_metadata(
                segments["cross_track_ref_indices"],
                meta["cross_track_ref_indices"],
            )
        else:
            out["cross_track_ref_indices"] = np.array([], dtype=np.uint32)
        if "n_points" in segments and "n_points" in meta:
            if meta["n_points"].get("small_uint"):
                out["n_points"] = _decode_small_uint_stream(segments["n_points"], meta["n_points"])
            else:
                out["n_points"] = self._decode_transformed_uint32_metadata(segments["n_points"], meta["n_points"])
        if "array_starts" in segments and "array_starts" in meta:
            if meta["array_starts"].get("small_uint"):
                out["array_starts"] = _decode_small_uint_stream(segments["array_starts"], meta["array_starts"])
            else:
                out["array_starts"] = self._decode_transformed_uint32_metadata(segments["array_starts"], meta["array_starts"])
        if meta.get("delta_ref_offsets", {}).get("all_one", False):
            out["delta_ref_offsets"] = np.ones(int(meta["delta_ref_offsets"]["count"]), dtype=np.uint8)
        elif "delta_ref_offsets" in segments and "delta_ref_offsets" in meta:
            out["delta_ref_offsets"] = _decode_small_uint_stream(segments["delta_ref_offsets"], meta["delta_ref_offsets"])
        elif "int_ref_offsets" in segments and "int_ref_offsets" in meta:
            out["delta_ref_offsets"] = _decode_small_uint_stream(segments["int_ref_offsets"], meta["int_ref_offsets"])
        out["int_kinds"] = _decode_small_uint_stream(segments["int_kinds"], meta["int_kinds"])
        if "mz_kinds" in segments and "mz_kinds" in meta:
            out["mz_kinds"] = _decode_small_uint_stream(segments["mz_kinds"], meta["mz_kinds"])
        else:
            out["mz_kinds"] = np.zeros(len(out["mz_ref_indices"]), dtype=np.uint8)
        if "delta_ref_offsets" not in out:
            out["delta_ref_offsets"] = np.array([], dtype=np.uint8)
        return out

    def _derive_n_points_from_mz(
        self,
        track_lengths: np.ndarray,
        mz_kinds: np.ndarray,
        mz_refs: np.ndarray,
        mz_library_q: List[np.ndarray],
    ) -> np.ndarray:
        n_points = np.empty(len(mz_kinds), dtype=np.uint32)
        island_pos = 0
        for track_len in track_lengths:
            prev_len = 0
            for _ in range(int(track_len)):
                mz_kind = int(mz_kinds[island_pos]) if island_pos < len(mz_kinds) else MZ_KIND_RAW
                mz_idx = int(mz_refs[island_pos]) if island_pos < len(mz_refs) else 0
                if mz_kind == MZ_KIND_RAW:
                    curr_len = len(mz_library_q[mz_idx])
                elif mz_kind in {MZ_KIND_PREV_OFFSET, MZ_KIND_PREV_OFFSET_RESIDUAL}:
                    curr_len = prev_len
                else:
                    raise ValueError(f"Unsupported mz_kind {mz_kind} while deriving n_points")
                n_points[island_pos] = curr_len
                prev_len = curr_len
                island_pos += 1
        return n_points

    @staticmethod
    def _make_temp_dense_array(tmp_dir: str, name: str, dtype: np.dtype, count: int):
        if int(count) <= 0:
            return np.array([], dtype=dtype)
        path = Path(tmp_dir) / f"{name}.bin"
        return np.memmap(path, dtype=dtype, mode="w+", shape=(int(count),))

    def _iter_intensity_source_contexts(self, rep: Dict):
        tracks = rep["_source_tracks"]
        compact_tracks = rep.get("_source_compact_tracks")
        all_islands = rep.get("_source_all_islands")
        ms1_scans = rep.get("_source_ms1_scans")
        track_lengths = np.asarray(rep["track_lengths"], dtype=np.uint32)
        int_kinds = np.asarray(rep["int_kinds"], dtype=np.uint8)
        n_points = np.asarray(rep.get("n_points", rep["_n_points_runtime"]), dtype=np.uint32)
        delta_ref_offsets = np.asarray(rep.get("delta_ref_offsets", EMPTY_UINT32), dtype=np.uint8)
        cross_track_ref_indices = np.asarray(rep.get("cross_track_ref_indices", EMPTY_UINT32), dtype=np.uint32)
        source_get_segment = lambda idx: _source_segment_view(
            tracks,
            compact_tracks,
            all_islands,
            ms1_scans,
            int(idx),
        )
        delta_ref_pos = 0
        cross_track_ref_pos = 0
        island_pos = 0
        track_history_limit = max(int(self.delta_ref_window), 2 if self.enable_second_order_delta else 1)
        for track_len in track_lengths:
            track_history: List[np.ndarray] = []
            for _ in range(int(track_len)):
                curr_segment = source_get_segment(island_pos)
                int_kind = int(int_kinds[island_pos])
                ref_segment = None
                prev_segment = None
                prev2_segment = None
                encoded_len = None
                if int_kind == INT_KIND_DELTA2:
                    if len(track_history) < 2:
                        raise ValueError("Representation delta2 island missing source history")
                    prev_segment = track_history[-1]
                    prev2_segment = track_history[-2]
                    encoded_len = int(len(curr_segment))
                elif int_kind == INT_KIND_DELTA:
                    ref_offset = int(delta_ref_offsets[delta_ref_pos]) if delta_ref_pos < len(delta_ref_offsets) else 1
                    delta_ref_pos += 1
                    if ref_offset == 0:
                        if cross_track_ref_pos >= len(cross_track_ref_indices):
                            raise ValueError("Representation missing cross-track reference index")
                        ref_segment = source_get_segment(int(cross_track_ref_indices[cross_track_ref_pos]))
                        cross_track_ref_pos += 1
                    else:
                        if ref_offset > len(track_history):
                            raise ValueError("Representation delta ref offset exceeds track history")
                        ref_segment = track_history[-ref_offset]
                    encoded_len = int(self._delta_encoded_length(len(curr_segment), len(ref_segment)))
                yield island_pos, int_kind, curr_segment, ref_segment, prev_segment, prev2_segment, encoded_len
                track_history.append(curr_segment)
                overflow = len(track_history) - track_history_limit
                if overflow > 0:
                    del track_history[:overflow]
                island_pos += 1

    def _detect_equal_fidelity_precision_from_rep_source(self, rep: Dict, sample_size: int = 1000) -> int:
        tracks = rep["_source_tracks"]
        compact_tracks = rep.get("_source_compact_tracks")
        all_islands = rep.get("_source_all_islands")
        ms1_scans = rep.get("_source_ms1_scans")
        total_points = int(rep.get("_n_points_runtime", EMPTY_UINT32).astype(np.uint64, copy=False).sum()) if len(rep.get("_n_points_runtime", EMPTY_UINT32)) else 0
        if total_points <= 0:
            return 1
        sample_count = min(int(sample_size), total_points)
        target_positions = np.linspace(0, total_points - 1, sample_count, dtype=int)
        sample = np.empty(sample_count, dtype=np.float64)
        sample_pos = 0
        offset = 0
        island_count = int(rep.get("stats", {}).get("n_islands", 0))
        for island_idx in range(island_count):
            arr = _source_segment_view(tracks, compact_tracks, all_islands, ms1_scans, island_idx)
            next_offset = offset + len(arr)
            while sample_pos < sample_count and target_positions[sample_pos] < next_offset:
                local_idx = int(target_positions[sample_pos] - offset)
                sample[sample_pos] = arr[local_idx]
                sample_pos += 1
            offset = next_offset
            if sample_pos >= sample_count:
                break
        if sample_pos != sample_count:
            sample = sample[:sample_pos]
        if baseline_ms1_codec._szdpd_detect_precision(sample) > 1:
            return 10
        for island_idx in range(island_count):
            arr = _source_segment_view(tracks, compact_tracks, all_islands, ms1_scans, island_idx)
            if _has_fractional_values(arr):
                return 10
        return 1

    def _try_fill_equal_fidelity_intensity_native(
        self,
        rep: Dict,
        full_code64: np.ndarray,
        delta_code64: np.ndarray,
        precision: int,
    ) -> bool:
        if not (
            HAVE_CROSS_SCAN_SPEEDUPS
            and hasattr(_cross_scan_speedups, "fill_equal_fidelity_intensity_streams_compact_views")
            and self.intensity_mode == "szdpd_xdelta_equalfidelity"
            and self.equal_fidelity_overflow_mode == EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT
        ):
            return False
        compact_tracks = rep.get("_source_compact_tracks")
        ms1_scans = rep.get("_source_ms1_scans")
        if not isinstance(compact_tracks, CompactIslandTracks) or compact_tracks.islands:
            return False
        source_ctx = _make_source_context(
            rep["_source_tracks"],
            compact_tracks,
            rep.get("_source_all_islands"),
            ms1_scans,
            tempdir=rep.get("_tempdir"),
        )
        if (
            source_ctx.flat_intensity_data is None
            or source_ctx.ms1_scan_base_offsets is None
        ):
            return False
        try:
            _cross_scan_speedups.fill_equal_fidelity_intensity_streams_compact_views(
                np.asarray(source_ctx.flat_intensity_data, dtype=np.float64),
                np.asarray(source_ctx.ms1_scan_base_offsets, dtype=np.uint64),
                np.asarray(compact_tracks.track_offsets, dtype=np.uint32),
                np.asarray(compact_tracks.scan_indices, dtype=np.uint32),
                np.asarray(compact_tracks.array_start_indices, dtype=np.uint32),
                np.asarray(compact_tracks.n_points, dtype=np.uint32),
                np.asarray(rep["int_kinds"], dtype=np.uint8),
                np.asarray(rep.get("delta_ref_offsets", EMPTY_UINT32), dtype=np.uint8),
                np.asarray(rep.get("cross_track_ref_indices", EMPTY_UINT32), dtype=np.uint32),
                np.asarray(full_code64, dtype=np.int64),
                np.asarray(delta_code64, dtype=np.int64),
                int(precision),
                bool(self.delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR),
                bool(self.enable_second_order_delta),
                int(self.delta_ref_window),
            )
            return True
        except Exception:
            return False

    def _try_fill_strict_lossless_intensity_native(
        self,
        rep: Dict,
        full_float64: np.ndarray,
        delta_float64: np.ndarray,
    ) -> bool:
        if not (
            HAVE_CROSS_SCAN_SPEEDUPS
            and hasattr(_cross_scan_speedups, "fill_strict_lossless_intensity_streams_compact_views")
            and self.intensity_mode == "strict_lossless_xdelta"
        ):
            return False
        compact_tracks = rep.get("_source_compact_tracks")
        ms1_scans = rep.get("_source_ms1_scans")
        if not isinstance(compact_tracks, CompactIslandTracks) or compact_tracks.islands:
            return False
        source_ctx = _make_source_context(
            rep["_source_tracks"],
            compact_tracks,
            rep.get("_source_all_islands"),
            ms1_scans,
            tempdir=rep.get("_tempdir"),
        )
        if (
            source_ctx.flat_intensity_data is None
            or source_ctx.ms1_scan_base_offsets is None
        ):
            return False
        try:
            _cross_scan_speedups.fill_strict_lossless_intensity_streams_compact_views(
                np.asarray(source_ctx.flat_intensity_data, dtype=np.float64),
                np.asarray(source_ctx.ms1_scan_base_offsets, dtype=np.uint64),
                np.asarray(compact_tracks.track_offsets, dtype=np.uint32),
                np.asarray(compact_tracks.scan_indices, dtype=np.uint32),
                np.asarray(compact_tracks.array_start_indices, dtype=np.uint32),
                np.asarray(compact_tracks.n_points, dtype=np.uint32),
                np.asarray(rep["int_kinds"], dtype=np.uint8),
                np.asarray(rep.get("delta_ref_offsets", EMPTY_UINT32), dtype=np.uint8),
                np.asarray(rep.get("cross_track_ref_indices", EMPTY_UINT32), dtype=np.uint32),
                np.asarray(full_float64, dtype=np.float64),
                np.asarray(delta_float64, dtype=np.float64),
                bool(self.delta_length_mode == DELTA_LENGTH_MODE_MAX_REF_CURR),
                bool(self.enable_second_order_delta),
                int(self.delta_ref_window),
            )
            return True
        except Exception:
            return False

    def _encode_intensity_segments(self, rep: Dict) -> Tuple[Dict[str, bytes], Dict]:
        total_t0 = time.perf_counter()
        stage_timings: Dict[str, float] = {}
        runtime_cache = None
        int_kinds = np.asarray(rep["int_kinds"], dtype=np.uint8)
        n_points = np.asarray(rep.get("n_points", rep["_n_points_runtime"]), dtype=np.uint32)
        full_total = int(n_points[int_kinds == INT_KIND_FULL].astype(np.uint64, copy=False).sum()) if len(n_points) else 0
        delta_total = int(
            self._expected_delta_value_count(
                {
                    "track_lengths": np.asarray(rep["track_lengths"], dtype=np.uint32),
                    "int_kinds": int_kinds,
                    "n_points": n_points,
                    "delta_ref_offsets": np.asarray(rep.get("delta_ref_offsets", EMPTY_UINT32), dtype=np.uint8),
                    "cross_track_ref_indices": np.asarray(rep.get("cross_track_ref_indices", EMPTY_UINT32), dtype=np.uint32),
                },
                mode=self.delta_length_mode,
                padded_delta_max_diff=self.padded_delta_max_diff,
            )
        )

        with tempfile.TemporaryDirectory(prefix="trackcodec_ms1_intensity_") as tmp_dir:
            if self.intensity_mode == "stackzdpd_passthrough":
                fill_t0 = time.perf_counter()
                full_concat = self._make_temp_dense_array(tmp_dir, "stackzdpd_full_float64", np.float64, full_total)
                full_pos = 0
                for _, int_kind, curr_seg, _, _, _, _ in self._iter_intensity_source_contexts(rep):
                    if int_kind != INT_KIND_FULL:
                        raise ValueError("stackzdpd_passthrough requires all intensity islands to be FULL")
                    rounded = np.round(np.asarray(curr_seg, dtype=np.float64), 1)
                    next_pos = full_pos + len(rounded)
                    full_concat[full_pos:next_pos] = rounded
                    full_pos = next_pos
                if full_pos != full_total:
                    raise ValueError(f"stackzdpd passthrough fill mismatch: {full_pos} vs {full_total}")
                stage_timings["intensity_fill_s"] = time.perf_counter() - fill_t0
                stage_timings["intensity_native_fill_used"] = 0.0
                encode_t0 = time.perf_counter()
                raw_full = np.asarray(full_concat, dtype=np.float64).tobytes()
                _release_temp_memmap(full_concat)
                del full_concat
                payload = compress(raw_full, self.backend)
                stage_timings["intensity_stream_compress_s"] = time.perf_counter() - encode_t0
                stage_timings["intensity_stream_workers"] = 1.0
                stage_timings["intensity_total_s"] = time.perf_counter() - total_t0
                return {
                    "full_intensity": payload,
                    "full_overflow_idx": b"",
                    "full_overflow_vals": b"",
                    "delta_intensity": b"",
                    "delta_overflow_idx": b"",
                    "delta_overflow_vals": b"",
                }, {
                    "full_intensity": {
                        "count": int(full_total),
                        "outer_backend": self.backend,
                        "storage_kind": "stackzdpd_float64_round1",
                        "round_decimals": 1,
                    },
                    "full_overflow_idx": {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0},
                    "full_overflow_vals": {"count": 0, "outer_backend": self.backend},
                    "delta_intensity": {
                        "count": 0,
                        "outer_backend": self.backend,
                        "storage_kind": "empty",
                    },
                    "delta_overflow_idx": {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0},
                    "delta_overflow_vals": {"count": 0, "outer_backend": self.backend},
                    "__stage_timings_s": stage_timings,
                }

            if self.intensity_mode == "strict_lossless_xdelta":
                fill_t0 = time.perf_counter()
                full_concat = self._make_temp_dense_array(tmp_dir, "full_float64", np.float64, full_total)
                delta_concat = self._make_temp_dense_array(tmp_dir, "delta_float64", np.float64, delta_total)
                full_pos = 0
                delta_pos = 0
                native_filled = self._try_fill_strict_lossless_intensity_native(
                    rep,
                    full_concat,
                    delta_concat,
                )
                if not native_filled:
                    for _, int_kind, curr_seg, ref_seg, prev_seg, prev2_seg, encoded_len in self._iter_intensity_source_contexts(rep):
                        if int_kind == INT_KIND_FULL:
                            next_pos = full_pos + len(curr_seg)
                            full_concat[full_pos:next_pos] = np.asarray(curr_seg, dtype=np.float64)
                            full_pos = next_pos
                        elif int_kind == INT_KIND_DELTA2:
                            delta_chunk = np.asarray(curr_seg, dtype=np.float64) - (2.0 * np.asarray(prev_seg, dtype=np.float64)) + np.asarray(prev2_seg, dtype=np.float64)
                            next_pos = delta_pos + len(delta_chunk)
                            delta_concat[delta_pos:next_pos] = delta_chunk
                            delta_pos = next_pos
                        elif int_kind == INT_KIND_DELTA:
                            curr_padded = _pad_float64(curr_seg, int(encoded_len))
                            ref_padded = _pad_float64(ref_seg, int(encoded_len))
                            delta_chunk = curr_padded - ref_padded
                            next_pos = delta_pos + len(delta_chunk)
                            delta_concat[delta_pos:next_pos] = delta_chunk
                            delta_pos = next_pos
                    if full_pos != full_total:
                        raise ValueError(f"strict intensity full stream fill mismatch: {full_pos} vs {full_total}")
                    if delta_pos != delta_total:
                        raise ValueError(f"strict intensity delta stream fill mismatch: {delta_pos} vs {delta_total}")
                stage_timings["intensity_fill_s"] = time.perf_counter() - fill_t0
                stage_timings["intensity_native_fill_used"] = 1.0 if native_filled else 0.0
                encode_t0 = time.perf_counter()
                with ThreadPoolExecutor(max_workers=min(2, self.intensity_stream_workers)) as executor:
                    full_future = executor.submit(
                        _encode_float64_planes,
                        np.asarray(full_concat, dtype=np.float64),
                        self.backend,
                        self.intensity_stream_workers,
                    )
                    delta_future = executor.submit(
                        _encode_float64_planes,
                        np.asarray(delta_concat, dtype=np.float64),
                        self.backend,
                        self.intensity_stream_workers,
                    )
                    full_payload, full_meta = full_future.result()
                    delta_payload, delta_meta = delta_future.result()
                stage_timings["intensity_stream_compress_s"] = time.perf_counter() - encode_t0
                stage_timings["intensity_stream_workers"] = float(self.intensity_stream_workers)
                stage_timings["intensity_total_s"] = time.perf_counter() - total_t0
                _release_temp_memmap(full_concat)
                _release_temp_memmap(delta_concat)
                del full_concat, delta_concat
                gc.collect()
                return {
                    "full_intensity": full_payload,
                    "full_overflow_idx": b"",
                    "full_overflow_vals": b"",
                    "delta_intensity": delta_payload,
                    "delta_overflow_idx": b"",
                    "delta_overflow_vals": b"",
                }, {
                    "full_intensity": full_meta,
                    "full_overflow_idx": {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0},
                    "full_overflow_vals": {"count": 0, "outer_backend": self.backend},
                    "delta_intensity": delta_meta,
                    "delta_overflow_idx": {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0},
                    "delta_overflow_vals": {"count": 0, "outer_backend": self.backend},
                    "__stage_timings_s": stage_timings,
                }

            if self.intensity_mode == "szdpd_xdelta_equalfidelity":
                fill_t0 = time.perf_counter()
                precision = rep.get("equal_fidelity_precision")
                if precision is None:
                    precision = self._detect_equal_fidelity_precision_from_rep_source(rep)
                full_code64 = self._make_temp_dense_array(tmp_dir, "full_code64", np.int64, full_total)
                delta_code64 = self._make_temp_dense_array(tmp_dir, "delta_code64", np.int64, delta_total)
                full_pos = 0
                delta_pos = 0
                native_filled = self._try_fill_equal_fidelity_intensity_native(
                    rep,
                    full_code64,
                    delta_code64,
                    int(precision),
                )
                if not native_filled:
                    for _, int_kind, curr_seg, ref_seg, prev_seg, prev2_seg, encoded_len in self._iter_intensity_source_contexts(rep):
                        if int_kind == INT_KIND_FULL:
                            codes = self._get_equal_fidelity_codes_cached(
                                curr_seg,
                                int(precision),
                                runtime_cache=runtime_cache,
                            )
                            next_pos = full_pos + len(codes)
                            full_code64[full_pos:next_pos] = codes
                            full_pos = next_pos
                        elif int_kind == INT_KIND_DELTA2:
                            curr_codes = self._get_equal_fidelity_codes_cached(
                                curr_seg,
                                int(precision),
                                encoded_len=int(encoded_len),
                                runtime_cache=runtime_cache,
                            )
                            prev_codes = self._get_equal_fidelity_codes_cached(
                                prev_seg,
                                int(precision),
                                encoded_len=int(encoded_len),
                                runtime_cache=runtime_cache,
                            )
                            prev2_codes = self._get_equal_fidelity_codes_cached(
                                prev2_seg,
                                int(precision),
                                encoded_len=int(encoded_len),
                                runtime_cache=runtime_cache,
                            )
                            delta_codes = curr_codes - (2 * prev_codes) + prev2_codes
                            next_pos = delta_pos + len(delta_codes)
                            delta_code64[delta_pos:next_pos] = delta_codes
                            delta_pos = next_pos
                        elif int_kind == INT_KIND_DELTA:
                            curr_codes = self._get_equal_fidelity_codes_cached(
                                curr_seg,
                                int(precision),
                                encoded_len=int(encoded_len),
                                runtime_cache=runtime_cache,
                            )
                            ref_codes = self._get_equal_fidelity_codes_cached(
                                ref_seg,
                                int(precision),
                                encoded_len=int(encoded_len),
                                runtime_cache=runtime_cache,
                            )
                            delta_codes = curr_codes - ref_codes
                            next_pos = delta_pos + len(delta_codes)
                            delta_code64[delta_pos:next_pos] = delta_codes
                            delta_pos = next_pos
                    if full_pos != full_total:
                        raise ValueError(f"equal-fidelity full code fill mismatch: {full_pos} vs {full_total}")
                    if delta_pos != delta_total:
                        raise ValueError(f"equal-fidelity delta code fill mismatch: {delta_pos} vs {delta_total}")
                stage_timings["intensity_fill_s"] = time.perf_counter() - fill_t0
                stage_timings["intensity_native_fill_used"] = 1.0 if native_filled else 0.0
                split_t0 = time.perf_counter()
                full_int32, full_overflow_idx, full_overflow_vals = _encode_signed_int64_to_int32_main(np.asarray(full_code64, dtype=np.int64))
                delta_int32, delta_overflow_idx, delta_overflow_vals = _encode_signed_int64_to_int32_main(np.asarray(delta_code64, dtype=np.int64))
                _release_temp_memmap(full_code64)
                _release_temp_memmap(delta_code64)
                del full_code64, delta_code64
                gc.collect()
                stage_timings["intensity_int32_split_s"] = time.perf_counter() - split_t0
                full_base_meta = {
                    "count": int(len(full_int32)),
                    "precision": int(precision),
                    "overflow_count": int(len(full_overflow_idx)),
                    "equal_fidelity": True,
                    "overflow_policy": self.equal_fidelity_overflow_mode,
                }
                delta_base_meta = {
                    "count": int(len(delta_int32)),
                    "precision": int(precision),
                    "overflow_count": int(len(delta_overflow_idx)),
                    "equal_fidelity": True,
                    "delta_domain": "szdpd_code",
                    "overflow_policy": self.equal_fidelity_overflow_mode,
                }
            else:
                fill_t0 = time.perf_counter()
                full_concat = self._make_temp_dense_array(tmp_dir, "full_float64", np.float64, full_total)
                delta_concat = self._make_temp_dense_array(tmp_dir, "delta_float64", np.float64, delta_total)
                full_pos = 0
                delta_pos = 0
                for _, int_kind, curr_seg, ref_seg, prev_seg, prev2_seg, encoded_len in self._iter_intensity_source_contexts(rep):
                    if int_kind == INT_KIND_FULL:
                        next_pos = full_pos + len(curr_seg)
                        full_concat[full_pos:next_pos] = np.asarray(curr_seg, dtype=np.float64)
                        full_pos = next_pos
                    elif int_kind == INT_KIND_DELTA2:
                        delta_chunk = np.asarray(curr_seg, dtype=np.float64) - (2.0 * np.asarray(prev_seg, dtype=np.float64)) + np.asarray(prev2_seg, dtype=np.float64)
                        next_pos = delta_pos + len(delta_chunk)
                        delta_concat[delta_pos:next_pos] = delta_chunk
                        delta_pos = next_pos
                    elif int_kind == INT_KIND_DELTA:
                        curr_padded = _pad_float64(curr_seg, int(encoded_len))
                        ref_padded = _pad_float64(ref_seg, int(encoded_len))
                        delta_chunk = curr_padded - ref_padded
                        next_pos = delta_pos + len(delta_chunk)
                        delta_concat[delta_pos:next_pos] = delta_chunk
                        delta_pos = next_pos
                if full_pos != full_total:
                    raise ValueError(f"full stream fill mismatch: {full_pos} vs {full_total}")
                if delta_pos != delta_total:
                    raise ValueError(f"delta stream fill mismatch: {delta_pos} vs {delta_total}")
                stage_timings["intensity_fill_s"] = time.perf_counter() - fill_t0
                stage_timings["intensity_native_fill_used"] = 0.0
                quant_t0 = time.perf_counter()
                full_int32, full_base_meta = _szdpd_encode_to_int32(np.asarray(full_concat, dtype=np.float64))
                full_overflow_idx = EMPTY_UINT32
                full_overflow_vals = EMPTY_INT64
                delta_int32, delta_base_meta, delta_overflow_idx, delta_overflow_vals = _signed_residual_encode_to_int32(
                    np.asarray(delta_concat, dtype=np.float64)
                )
                _release_temp_memmap(full_concat)
                _release_temp_memmap(delta_concat)
                del full_concat, delta_concat
                gc.collect()
                stage_timings["intensity_int32_split_s"] = time.perf_counter() - quant_t0

        stream_t0 = time.perf_counter()
        full_uint32 = _zigzag_encode_int32(full_int32)
        delta_uint32_for_stream = _zigzag_encode_int32(delta_int32)

        def _encode_full_intensity_stream():
            return _encode_uint32_stream_best(
                full_uint32,
                self.backend,
                use_pfor=self._should_use_pfor("full_intensity", int(len(full_int32))),
                pfor_codec=self.pfor_codec,
                zero_rle=self._should_use_zero_rle("full_intensity", int(len(full_int32))),
                backend_candidates=self._candidate_backends("full_intensity"),
                try_pfor=self.adaptive_uint32 and self.adaptive_intensity_search and len(full_int32) >= self.pfor_min_count,
                try_zero_rle=self.adaptive_uint32 and self.adaptive_intensity_search,
                search_mode=self.adaptive_search_mode if self.adaptive_intensity_search else "full",
                sample_count=self.adaptive_search_sample_count,
            )

        def _encode_delta_intensity_stream():
            kwargs = {
                "use_pfor": self._should_use_pfor("delta_intensity", int(len(delta_int32))),
                "pfor_codec": self.pfor_codec,
                "zero_rle": self._should_use_zero_rle("delta_intensity", int(len(delta_int32))),
                "backend_candidates": self._candidate_backends("delta_intensity"),
                "try_pfor": self.adaptive_uint32 and self.adaptive_intensity_search and len(delta_int32) >= 512,
                "try_zero_rle": self.adaptive_uint32 and self.adaptive_intensity_search,
                "search_mode": self.adaptive_search_mode if self.adaptive_intensity_search else "full",
                "sample_count": self.adaptive_search_sample_count,
            }
            if self.enable_delta_intensity_chunked_stream:
                return _encode_uint32_stream_best_chunked(
                    delta_uint32_for_stream,
                    self.backend,
                    chunk_items=self.delta_intensity_chunk_items,
                    max_workers=self.intensity_stream_workers,
                    **kwargs,
                )
            return _encode_uint32_stream_best(
                delta_uint32_for_stream,
                self.backend,
                **kwargs,
            )

        if self.intensity_stream_workers > 1:
            with ThreadPoolExecutor(max_workers=2) as executor:
                full_future = executor.submit(_encode_full_intensity_stream)
                delta_future = executor.submit(_encode_delta_intensity_stream)
                full_payload, full_stream_meta = full_future.result()
                delta_payload, delta_stream_meta = delta_future.result()
        else:
            full_payload, full_stream_meta = _encode_full_intensity_stream()
            delta_payload, delta_stream_meta = _encode_delta_intensity_stream()
        if (
            self.enable_delta_int16_pack
            and self.intensity_mode == "szdpd_xdelta_equalfidelity"
            and len(delta_int32) > 0
            and len(delta_overflow_idx) == 0
        ):
            max_abs_delta = int(np.max(np.abs(delta_int32), initial=0))
            if max_abs_delta <= np.iinfo(np.int16).max:
                alt_payload, alt_meta = self._encode_int16_stream_best(delta_int32.astype(np.int16), "delta_intensity")
                if len(alt_payload) < len(delta_payload):
                    delta_payload = alt_payload
                    delta_stream_meta = alt_meta
        stage_timings["intensity_sparse_l0_enabled"] = 1.0 if self.enable_sparse_l0_delta_intensity else 0.0
        stage_timings["intensity_sparse_l0_attempted"] = 0.0
        stage_timings["intensity_sparse_l0_selected"] = 0.0
        stage_timings["intensity_sparse_l0_eligible"] = 0.0
        delta_nonzero_count = 0
        if self.enable_sparse_l0_delta_intensity and len(delta_int32) >= self.sparse_l0_delta_min_count:
            delta_nonzero_count = int(np.count_nonzero(delta_int32))
            nonzero_fraction = float(delta_nonzero_count) / float(len(delta_int32)) if len(delta_int32) else 0.0
            stage_timings["intensity_sparse_l0_nonzero_fraction"] = nonzero_fraction
            if nonzero_fraction <= self.sparse_l0_delta_max_nonzero_fraction:
                stage_timings["intensity_sparse_l0_eligible"] = 1.0
        if (
            self.enable_sparse_l0_delta_intensity
            and len(delta_int32) >= self.sparse_l0_delta_min_count
            and stage_timings["intensity_sparse_l0_eligible"] > 0.0
        ):
            sparse_t0 = time.perf_counter()
            dense_delta_bytes = int(len(delta_payload))
            try:
                sparse_payload, sparse_meta = self._encode_sparse_l0_signed_int32_stream(delta_int32)
                sparse_delta_bytes = int(len(sparse_payload))
                dense_accounted_bytes = dense_delta_bytes + _estimate_json_meta_bytes({**delta_base_meta, **delta_stream_meta})
                sparse_accounted_bytes = sparse_delta_bytes + _estimate_json_meta_bytes({**delta_base_meta, **sparse_meta})
                stage_timings["intensity_sparse_l0_attempted"] = 1.0
                stage_timings["intensity_sparse_l0_candidate_s"] = time.perf_counter() - sparse_t0
                stage_timings["intensity_sparse_l0_dense_bytes"] = float(dense_delta_bytes)
                stage_timings["intensity_sparse_l0_bytes"] = float(sparse_delta_bytes)
                stage_timings["intensity_sparse_l0_dense_accounted_bytes"] = float(dense_accounted_bytes)
                stage_timings["intensity_sparse_l0_accounted_bytes"] = float(sparse_accounted_bytes)
                stage_timings["intensity_sparse_l0_nnz"] = float(sparse_meta.get("nnz_count", delta_nonzero_count))
                if sparse_accounted_bytes + self.sparse_l0_delta_min_gain_bytes <= dense_accounted_bytes:
                    delta_payload = sparse_payload
                    delta_stream_meta = sparse_meta
                    stage_timings["intensity_sparse_l0_selected"] = 1.0
            except Exception:
                stage_timings["intensity_sparse_l0_candidate_s"] = time.perf_counter() - sparse_t0
                stage_timings["intensity_sparse_l0_candidate_failed"] = 1.0
        stage_timings["intensity_main_stream_compress_s"] = time.perf_counter() - stream_t0
        stage_timings["intensity_stream_workers"] = float(self.intensity_stream_workers)
        overflow_t0 = time.perf_counter()

        def _encode_full_overflow_idx_stream():
            return _encode_uint32_stream_best(
                full_overflow_idx,
                self.backend,
                use_pfor=False,
                pfor_codec=self.pfor_codec,
                zero_rle=False,
                backend_candidates=self._candidate_backends("full_overflow_idx"),
                try_pfor=False,
                try_zero_rle=False,
                search_mode=self.adaptive_search_mode,
                sample_count=self.adaptive_search_sample_count,
            )

        def _encode_delta_overflow_idx_stream():
            return _encode_uint32_stream_best(
                delta_overflow_idx,
                self.backend,
                use_pfor=False,
                pfor_codec=self.pfor_codec,
                zero_rle=False,
                backend_candidates=self._candidate_backends("delta_overflow_idx"),
                try_pfor=False,
                try_zero_rle=False,
                search_mode=self.adaptive_search_mode,
                sample_count=self.adaptive_search_sample_count,
            )

        def _encode_full_overflow_vals_stream():
            full_overflow_bytes = np.asarray(full_overflow_vals, dtype=np.int64).tobytes()
            best_backend = self.backend
            best_payload = compress(full_overflow_bytes, self.backend)
            for candidate_backend in self._candidate_backends("full_overflow_vals"):
                candidate = compress(full_overflow_bytes, candidate_backend)
                if len(candidate) < len(best_payload):
                    best_payload = candidate
                    best_backend = candidate_backend
            return best_payload, {"count": int(len(full_overflow_vals)), "outer_backend": best_backend}

        def _encode_delta_overflow_vals_stream():
            overflow_bytes = np.asarray(delta_overflow_vals, dtype=np.int64).tobytes()
            best_backend = self.backend
            best_payload = compress(overflow_bytes, self.backend)
            for candidate_backend in self._candidate_backends("delta_overflow_vals"):
                candidate = compress(overflow_bytes, candidate_backend)
                if len(candidate) < len(best_payload):
                    best_payload = candidate
                    best_backend = candidate_backend
            return best_payload, {"count": int(len(delta_overflow_vals)), "outer_backend": best_backend}

        if self.intensity_stream_workers > 1:
            with ThreadPoolExecutor(max_workers=min(4, self.intensity_stream_workers)) as executor:
                f_full_idx = executor.submit(_encode_full_overflow_idx_stream)
                f_full_vals = executor.submit(_encode_full_overflow_vals_stream)
                f_delta_idx = executor.submit(_encode_delta_overflow_idx_stream)
                f_delta_vals = executor.submit(_encode_delta_overflow_vals_stream)
                full_overflow_idx_payload, full_overflow_idx_meta = f_full_idx.result()
                full_overflow_vals_payload, full_overflow_vals_meta = f_full_vals.result()
                overflow_idx_payload, overflow_idx_meta = f_delta_idx.result()
                overflow_vals_payload, overflow_vals_meta = f_delta_vals.result()
        else:
            full_overflow_idx_payload, full_overflow_idx_meta = _encode_full_overflow_idx_stream()
            full_overflow_vals_payload, full_overflow_vals_meta = _encode_full_overflow_vals_stream()
            overflow_idx_payload, overflow_idx_meta = _encode_delta_overflow_idx_stream()
            overflow_vals_payload, overflow_vals_meta = _encode_delta_overflow_vals_stream()
        stage_timings["intensity_overflow_stream_compress_s"] = time.perf_counter() - overflow_t0
        stage_timings["intensity_total_s"] = time.perf_counter() - total_t0
        return {
            "full_intensity": full_payload,
            "full_overflow_idx": full_overflow_idx_payload,
            "full_overflow_vals": full_overflow_vals_payload,
            "delta_intensity": delta_payload,
            "delta_overflow_idx": overflow_idx_payload,
            "delta_overflow_vals": overflow_vals_payload,
        }, {
            "full_intensity": {**full_base_meta, **full_stream_meta},
            "full_overflow_idx": full_overflow_idx_meta,
            "full_overflow_vals": full_overflow_vals_meta,
            "delta_intensity": {**delta_base_meta, **delta_stream_meta},
            "delta_overflow_idx": overflow_idx_meta,
            "delta_overflow_vals": overflow_vals_meta,
            "__stage_timings_s": stage_timings,
        }

    def _decode_intensity_segments(self, segments: Dict[str, bytes], meta: Dict) -> Tuple[np.ndarray, np.ndarray]:
        full_meta = meta.get("full_intensity", {})
        if full_meta.get("storage_kind") == "stackzdpd_float64_round1":
            raw = decompress(segments["full_intensity"], full_meta.get("outer_backend", self.backend))
            full = np.frombuffer(raw, dtype=np.float64).copy()
            expected = int(full_meta.get("count", len(full)))
            if len(full) != expected:
                raise ValueError(f"stackzdpd passthrough intensity count mismatch: {len(full)} vs {expected}")
            return full, np.array([], dtype=np.float64)

        if self.intensity_mode == "strict_lossless_xdelta":
            if self.intensity_decode_workers > 1:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    full_future = executor.submit(
                        _decode_float64_planes,
                        segments["full_intensity"],
                        meta["full_intensity"],
                        self.intensity_decode_workers,
                    )
                    delta_future = executor.submit(
                        _decode_float64_planes,
                        segments["delta_intensity"],
                        meta["delta_intensity"],
                        self.intensity_decode_workers,
                    )
                    full = full_future.result()
                    delta = delta_future.result()
            else:
                full = _decode_float64_planes(segments["full_intensity"], meta["full_intensity"], 1)
                delta = _decode_float64_planes(segments["delta_intensity"], meta["delta_intensity"], 1)
            return full, delta

        full_overflow_idx_meta = meta.get("full_overflow_idx", {"count": 0, "outer_backend": self.backend, "use_pfor": False, "zero_rle": False, "encoded_count": 0})
        full_overflow_vals_meta = meta.get("full_overflow_vals", {"count": 0, "outer_backend": self.backend})
        full_overflow_idx_payload = segments.get("full_overflow_idx", b"")
        full_overflow_vals_payload = segments.get("full_overflow_vals", b"")

        def _decode_full_main():
            return _decode_uint32_stream(
                segments["full_intensity"],
                meta["full_intensity"],
                max_workers=self.intensity_decode_workers,
            )

        def _decode_full_overflow_idx():
            return _decode_uint32_stream(
                full_overflow_idx_payload,
                full_overflow_idx_meta,
                max_workers=self.intensity_decode_workers,
            )

        def _decode_full_overflow_vals():
            if not full_overflow_vals_meta["count"]:
                return np.array([], dtype=np.int64)
            raw = decompress(full_overflow_vals_payload, full_overflow_vals_meta["outer_backend"])
            return np.frombuffer(raw, dtype=np.int64).copy()

        def _decode_delta_main():
            storage_kind = meta["delta_intensity"].get("storage_kind")
            if storage_kind == SPARSE_L0_DELTA_STORAGE_KIND:
                return None, self._decode_sparse_l0_signed_int32_stream(
                    segments["delta_intensity"],
                    meta["delta_intensity"],
                )
            if storage_kind == "int16_raw":
                raw = decompress(segments["delta_intensity"], meta["delta_intensity"]["outer_backend"])
                return None, np.frombuffer(raw, dtype=np.int16).astype(np.int32)
            return _decode_uint32_stream(
                segments["delta_intensity"],
                meta["delta_intensity"],
                max_workers=self.intensity_decode_workers,
            ), None

        def _decode_delta_overflow_idx():
            return _decode_uint32_stream(
                segments["delta_overflow_idx"],
                meta["delta_overflow_idx"],
                max_workers=self.intensity_decode_workers,
            )

        def _decode_delta_overflow_vals():
            if not meta["delta_overflow_vals"]["count"]:
                return np.array([], dtype=np.int64)
            raw = decompress(segments["delta_overflow_vals"], meta["delta_overflow_vals"]["outer_backend"])
            return np.frombuffer(raw, dtype=np.int64).copy()

        if self.intensity_decode_workers > 1:
            with ThreadPoolExecutor(max_workers=min(6, self.intensity_decode_workers)) as executor:
                f_full = executor.submit(_decode_full_main)
                f_full_idx = executor.submit(_decode_full_overflow_idx)
                f_full_vals = executor.submit(_decode_full_overflow_vals)
                f_delta = executor.submit(_decode_delta_main)
                f_delta_idx = executor.submit(_decode_delta_overflow_idx)
                f_delta_vals = executor.submit(_decode_delta_overflow_vals)
                full_uint32 = f_full.result()
                full_overflow_idx = f_full_idx.result()
                full_overflow_vals = f_full_vals.result()
                delta_uint32, delta_int32 = f_delta.result()
                overflow_idx = f_delta_idx.result()
                overflow_vals = f_delta_vals.result()
        else:
            full_uint32 = _decode_full_main()
            full_overflow_idx = _decode_full_overflow_idx()
            full_overflow_vals = _decode_full_overflow_vals()
            delta_uint32, delta_int32 = _decode_delta_main()
            overflow_idx = _decode_delta_overflow_idx()
            overflow_vals = _decode_delta_overflow_vals()
        if self.intensity_mode == "szdpd_xdelta_equalfidelity":
            full_codes = _decode_signed_int64_from_int32_main(
                _zigzag_decode_uint32(full_uint32),
                full_overflow_idx,
                full_overflow_vals,
            )
            if delta_uint32 is None:
                delta_codes = _decode_signed_int64_from_int32_main(delta_int32, overflow_idx, overflow_vals)
            else:
                delta_codes = _decode_signed_int64_from_int32_main(_zigzag_decode_uint32(delta_uint32), overflow_idx, overflow_vals)
            return full_codes, delta_codes
        full = _szdpd_decode_from_int32(_zigzag_decode_uint32(full_uint32), meta["full_intensity"])
        if delta_uint32 is None:
            delta = _signed_residual_decode_from_int32(delta_int32, meta["delta_intensity"], overflow_idx, overflow_vals)
        else:
            delta = _signed_residual_decode_from_int32(_zigzag_decode_uint32(delta_uint32), meta["delta_intensity"], overflow_idx, overflow_vals)
        return full, delta

    def _encode_impl(
        self,
        tracks,
        ms1_scans=None,
        progress_callback: Callable[[Dict], None] | None = None,
        output_path: str | Path | None = None,
    ):
        t0 = time.perf_counter()
        stage_timings: Dict[str, float] = {}
        t_stage = time.perf_counter()
        rep = self._build_representation(tracks, ms1_scans=ms1_scans, progress_callback=progress_callback)
        stage_timings["ms1_build_representation_s"] = time.perf_counter() - t_stage
        stage_timings["ms1_native_representation_used"] = 1.0 if rep.get("_native_representation_used") else 0.0
        stage_timings["ms1_source_context_materialized"] = 1.0 if rep.get("_source_context_materialized") else 0.0
        t_stage = time.perf_counter()
        self._spill_representation_arrays(rep)
        stage_timings["ms1_spill_representation_arrays_s"] = time.perf_counter() - t_stage
        if self.preserve_full_scan and ms1_scans is None:
            raise ValueError(
                "retain_zero_intensity_mz=True requires ms1_scans for full-scan m/z reconstruction"
            )
        total_tracks = int(rep.get("stats", {}).get("n_tracks", 0) or 0)

        def _run_encode_jobs():
            jobs = {
                "meta": lambda: self._encode_metadata_segments(rep),
                "intensity": lambda: self._encode_intensity_segments(rep),
            }
            if not self.omit_island_mz:
                jobs = {
                    "mz": lambda: self._encode_mz_library(rep),
                    "mz_model": lambda: self._encode_mz_model_segments(rep),
                    **jobs,
                }
            if self.preserve_full_scan:
                jobs["full_scan"] = lambda: self._encode_full_scan_sidecar(ms1_scans)
            total_sections = len(jobs)
            _emit_progress(
                progress_callback,
                {
                    "stage": "section_encode_start",
                    "pct": 92.0,
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                    "done_sections": 0,
                    "total_sections": int(total_sections),
                },
            )
            if self.encode_section_workers <= 1 or len(jobs) <= 1:
                out = {}
                for idx, (name, fn) in enumerate(jobs.items(), start=1):
                    t_section = time.perf_counter()
                    out[name] = fn()
                    stage_timings[f"ms1_section_{name}_s"] = time.perf_counter() - t_section
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": f"section_{name}_done",
                            "pct": float(92.0 + 8.0 * float(idx) / float(total_sections)),
                            "done_tracks": int(total_tracks),
                            "total_tracks": int(total_tracks),
                            "done_sections": int(idx),
                            "total_sections": int(total_sections),
                        },
                    )
                return out
            max_workers = min(self.encode_section_workers, len(jobs))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                section_start_times = {name: time.perf_counter() for name in jobs}
                future_map = {executor.submit(fn): name for name, fn in jobs.items()}
                out = {}
                done_sections = 0
                for future in as_completed(future_map):
                    name = future_map[future]
                    out[name] = future.result()
                    stage_timings[f"ms1_section_{name}_s"] = time.perf_counter() - section_start_times.get(name, time.perf_counter())
                    done_sections += 1
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": f"section_{name}_done",
                            "pct": float(92.0 + 8.0 * float(done_sections) / float(total_sections)),
                            "done_tracks": int(total_tracks),
                            "total_tracks": int(total_tracks),
                            "done_sections": int(done_sections),
                            "total_sections": int(total_sections),
                        },
                    )
                return out

        try:
            encoded_sections = _run_encode_jobs()
            if self.omit_island_mz:
                mz_segments, mz_meta = {}, {}
                mz_model_segments, mz_model_meta = {}, {}
            else:
                mz_segments, mz_meta = encoded_sections["mz"]
                mz_model_segments, mz_model_meta = encoded_sections["mz_model"]
            meta_segments, meta_meta = encoded_sections["meta"]
            int_segments, int_meta = encoded_sections["intensity"]
            intensity_stage_timings = int_meta.pop("__stage_timings_s", {}) if isinstance(int_meta, dict) else {}
            for key, value in intensity_stage_timings.items():
                stage_timings[f"ms1_{key}"] = float(value)
            if self.preserve_full_scan:
                full_scan_segments, full_scan_meta = encoded_sections["full_scan"]
            else:
                full_scan_segments, full_scan_meta = {}, {}
            runtime_cache = rep.get("_runtime_cache")
            if isinstance(runtime_cache, dict):
                runtime_cache.clear()
            _emit_progress(
                progress_callback,
                {
                    "stage": "encode_done",
                    "pct": 100.0,
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                },
            )

            header = {
                "format": "trackcodec_ms1_cross_scan",
                "backend": self.backend,
                "mz_precision_decimals": self.mz_precision,
                "mz_scale": rep["scale"],
                "intensity_mode": self.intensity_mode,
                "equal_fidelity_overflow_mode": self.equal_fidelity_overflow_mode,
                "use_pfor": self.use_pfor,
                "pfor_codec": self.pfor_codec,
                "pfor_min_count": self.pfor_min_count,
                "mz_dedup": self.mz_dedup,
                "zero_rle": self.zero_rle,
                "zero_rle_min_count": self.zero_rle_min_count,
                "metadata_transform": self.metadata_transform,
                "adaptive_uint32": self.adaptive_uint32,
                "delta_ref_window": self.delta_ref_window,
                "adaptive_intensity_search": self.adaptive_intensity_search,
                "compact_uint32": self.compact_uint32,
                "enhanced_residual_model": self.enhanced_residual_model,
                "adaptive_search_mode": self.adaptive_search_mode,
                "adaptive_search_sample_count": self.adaptive_search_sample_count,
                "padded_delta_max_diff": self.padded_delta_max_diff,
                "delta_length_mode": self.delta_length_mode,
                "adaptive_padded_delta_accounting": self.adaptive_padded_delta_accounting,
                "enable_second_order_delta": self.enable_second_order_delta,
                "enable_cross_track_full_prediction": self.enable_cross_track_full_prediction,
                "cross_track_candidate_limit": self.cross_track_candidate_limit,
                "cross_track_min_gain_bytes": self.cross_track_min_gain_bytes,
                "enable_byteaware_delta_ref_selection": self.enable_byteaware_delta_ref_selection,
                "enable_metadata_trim": self.enable_metadata_trim,
                "enable_stream_backend_tuning": self.enable_stream_backend_tuning,
                "enable_delta_int16_pack": self.enable_delta_int16_pack,
                "omit_array_starts": self.omit_array_starts,
                "omit_n_points": self.omit_n_points,
                "omit_island_mz": self.omit_island_mz,
                "preserve_full_scan": self.preserve_full_scan,
                "retain_zero_intensity_mz": self.retain_zero_intensity_mz,
                "enable_full_scan_delta_same_prev": self.enable_full_scan_delta_same_prev,
                "encode_section_workers": self.encode_section_workers,
                "intensity_stream_workers": self.intensity_stream_workers,
                "intensity_decode_workers": self.intensity_decode_workers,
                "enable_delta_intensity_chunked_stream": self.enable_delta_intensity_chunked_stream,
                "delta_intensity_chunk_items": self.delta_intensity_chunk_items,
                "enable_sparse_l0_delta_intensity": self.enable_sparse_l0_delta_intensity,
                "sparse_l0_delta_min_count": self.sparse_l0_delta_min_count,
                "sparse_l0_delta_min_gain_bytes": self.sparse_l0_delta_min_gain_bytes,
                "sparse_l0_delta_max_nonzero_fraction": self.sparse_l0_delta_max_nonzero_fraction,
                "disable_native_compact_views": self.disable_native_compact_views,
                "stats": rep["stats"],
                "segment_meta": {**mz_meta, **mz_model_meta, **meta_meta, **int_meta, **full_scan_meta},
            }
            ordered_segments = {
                **mz_segments,
                **mz_model_segments,
                **meta_segments,
                **int_segments,
                **full_scan_segments,
            }
            stage_timings["ms1_section_encode_wall_s"] = sum(
                float(value)
                for key, value in stage_timings.items()
                if key.startswith("ms1_section_") and key.endswith("_s")
            )
            t_stage = time.perf_counter()
            if output_path is None:
                output = _pack_segments(header, ordered_segments)
                compressed_bytes = int(len(output))
            else:
                compressed_bytes = _pack_segments_to_path(header, ordered_segments, output_path)
                output = Path(output_path)
            stage_timings["ms1_pack_segments_s"] = time.perf_counter() - t_stage
            encode_time = time.perf_counter() - t0
        finally:
            runtime_cache = rep.get("_runtime_cache")
            if isinstance(runtime_cache, dict):
                runtime_cache.clear()
            tempdir = rep.get("_tempdir")
            if tempdir:
                shutil.rmtree(tempdir, ignore_errors=True)

        if ms1_scans:
            raw_bytes = sum(len(s["mz_array"]) * 8 + len(s["intensity_array"]) * 8 for s in ms1_scans)
        elif isinstance(tracks, CompactIslandTracks):
            raw_bytes = int(tracks.raw_bytes)
        else:
            raw_bytes = sum(len(isl.mz_array) * 8 + len(isl.intensity_array) * 8 for t in tracks for isl in t.islands)

        segment_sizes = {name: len(payload) for name, payload in ordered_segments.items()}
        metadata = {
            "raw_bytes": raw_bytes,
            "compressed_bytes": compressed_bytes,
            "compression_ratio": raw_bytes / compressed_bytes if compressed_bytes else 0.0,
            "encode_time_s": encode_time,
            "header": header,
            "segment_sizes": segment_sizes,
            "reconstructable_full_scan": bool(self.preserve_full_scan),
            "retain_zero_intensity_mz": bool(self.retain_zero_intensity_mz),
            "stage_timings_s": stage_timings,
        }
        return output, metadata

    def encode(self, tracks, ms1_scans=None, progress_callback: Callable[[Dict], None] | None = None):
        return self._encode_impl(
            tracks,
            ms1_scans=ms1_scans,
            progress_callback=progress_callback,
            output_path=None,
        )

    def encode_to_path(
        self,
        tracks,
        ms1_scans=None,
        output_path: str | Path | None = None,
        progress_callback: Callable[[Dict], None] | None = None,
    ):
        if output_path is None:
            raise ValueError("encode_to_path requires output_path")
        return self._encode_impl(
            tracks,
            ms1_scans=ms1_scans,
            progress_callback=progress_callback,
            output_path=output_path,
        )

    def iter_decoded_islands(self, compressed_bytes, metadata=None):
        """Yield decoded islands one by one without materializing the output list.

        The byte stream and reconstruction rules are identical to ``decode()``.
        This path is intended for validation/streaming consumers: it keeps only
        bounded in-track history plus the specific prior islands referenced by
        cross-track residuals.
        """
        header, segments = _unpack_segments(compressed_bytes)
        segment_meta = header["segment_meta"]
        scale = float(header["mz_scale"])
        omit_island_mz = bool(header.get("omit_island_mz", False))

        if omit_island_mz:
            mz_library_q = []
            mz_model_offsets, mz_model_steps, mz_model_residuals = (
                np.array([], dtype=np.int32),
                np.array([], dtype=np.int32),
                [],
            )
        else:
            mz_library_q = self._decode_mz_library(segments, segment_meta)
            mz_model_offsets, mz_model_steps, mz_model_residuals = self._decode_mz_model_segments(segments, segment_meta)
        decoded_meta = self._decode_metadata_segments(segments, segment_meta)
        full_concat, delta_concat = self._decode_intensity_segments(segments, segment_meta)
        equal_fidelity = header["intensity_mode"] == "szdpd_xdelta_equalfidelity"
        intensity_precision = int(segment_meta["full_intensity"].get("precision", 1))
        equal_fidelity_overflow_mode = header.get(
            "equal_fidelity_overflow_mode",
            segment_meta.get("full_intensity", {}).get(
                "overflow_policy",
                self.equal_fidelity_overflow_mode,
            ),
        )

        track_lengths = decoded_meta["track_lengths"]
        scan_indices = decoded_meta["scan_indices"]
        array_starts = decoded_meta.get("array_starts")
        mz_refs = decoded_meta["mz_ref_indices"]
        mz_kinds = decoded_meta["mz_kinds"]
        int_kinds = decoded_meta["int_kinds"]
        delta_ref_offsets = decoded_meta["delta_ref_offsets"]
        cross_track_ref_indices = decoded_meta.get("cross_track_ref_indices", np.array([], dtype=np.uint32))
        n_points = decoded_meta.get("n_points")
        if n_points is None:
            if omit_island_mz:
                raise ValueError("Corrupt TrackCodec MS1 stream: omit_island_mz requires n_points metadata")
            n_points = self._derive_n_points_from_mz(track_lengths, mz_kinds, mz_refs, mz_library_q)
            decoded_meta["n_points"] = n_points
        padded_delta_max_diff = int(header.get("padded_delta_max_diff", self.padded_delta_max_diff))
        delta_length_mode = self._resolve_decode_delta_length_mode(header, decoded_meta, segment_meta)

        islands = []
        island_pos = 0
        full_value_pos = 0
        delta_pos = 0
        delta_ref_pos = 0
        cross_track_ref_pos = 0
        needed_cross_track_refs = set(
            int(value) for value in np.asarray(cross_track_ref_indices, dtype=np.uint32).tolist()
        )
        cross_track_int_history = {}
        cross_track_code_history = {}
        track_history_limit = max(
            1,
            int(header.get("delta_ref_window", self.delta_ref_window)),
            int(np.max(delta_ref_offsets, initial=0)) if len(delta_ref_offsets) else 0,
            2 if bool(header.get("enable_second_order_delta", self.enable_second_order_delta)) else 1,
        )

        def _append_track_history(history: List[np.ndarray], value: np.ndarray) -> None:
            history.append(value)
            overflow = len(history) - track_history_limit
            if overflow > 0:
                del history[:overflow]

        for track_id, track_len in enumerate(track_lengths):
            track_int_history = []
            track_code_history = []
            prev_mz_q = None
            for _ in range(int(track_len)):
                int_kind = int(int_kinds[island_pos])
                if int_kind == INT_KIND_DELTA and delta_ref_pos < len(delta_ref_offsets):
                    ref_offset = int(delta_ref_offsets[delta_ref_pos])
                    delta_ref_pos += 1
                else:
                    ref_offset = 1 if int_kind == INT_KIND_DELTA else 0
                if equal_fidelity:
                    if int_kind == INT_KIND_FULL:
                        n = int(n_points[island_pos])
                        intensity_code = full_concat[full_value_pos:full_value_pos + n].astype(np.int64, copy=False)
                        full_value_pos += n
                    elif int_kind == INT_KIND_DELTA2:
                        if len(track_code_history) < 2:
                            raise ValueError("Corrupt TrackCodec MS1 stream: delta2 island without two code references")
                        curr_len = int(n_points[island_pos])
                        delta_len = curr_len
                        delta_chunk = delta_concat[delta_pos:delta_pos + delta_len].astype(np.int64, copy=False)
                        delta_pos += delta_len
                        prev1 = _pad_int64(track_code_history[-1], delta_len)
                        prev2 = _pad_int64(track_code_history[-2], delta_len)
                        intensity_code = (delta_chunk + 2 * prev1 - prev2)[:curr_len]
                    else:
                        curr_len = int(n_points[island_pos])
                        if ref_offset == 0:
                            if cross_track_ref_pos >= len(cross_track_ref_indices):
                                raise ValueError("Corrupt TrackCodec MS1 stream: missing cross-track code reference index")
                            ref_idx = int(cross_track_ref_indices[cross_track_ref_pos])
                            cross_track_ref_pos += 1
                            if ref_idx not in cross_track_code_history:
                                raise ValueError("Corrupt TrackCodec MS1 stream: invalid cross-track code reference index")
                            ref_codes = cross_track_code_history[ref_idx]
                        else:
                            if ref_offset > len(track_code_history):
                                raise ValueError("Corrupt TrackCodec MS1 stream: delta island without valid code reference")
                            ref_codes = track_code_history[-ref_offset]
                        ref_len = len(ref_codes)
                        delta_len = self._delta_encoded_length(
                            curr_len,
                            ref_len,
                            mode=delta_length_mode,
                            padded_delta_max_diff=padded_delta_max_diff,
                        )
                        delta_chunk = delta_concat[delta_pos:delta_pos + delta_len].astype(np.int64, copy=False)
                        delta_pos += delta_len
                        padded_prev = _pad_int64(ref_codes, delta_len)
                        intensity_code = (padded_prev + delta_chunk)[:curr_len]
                    intensity = _decode_equal_fidelity_codes_to_float64(
                        intensity_code,
                        precision=intensity_precision,
                        overflow_mode=equal_fidelity_overflow_mode,
                    )
                else:
                    if int_kind == INT_KIND_FULL:
                        n = int(n_points[island_pos])
                        intensity = full_concat[full_value_pos:full_value_pos + n]
                        full_value_pos += n
                    elif int_kind == INT_KIND_DELTA2:
                        if len(track_int_history) < 2:
                            raise ValueError("Corrupt TrackCodec MS1 stream: delta2 island without two intensity references")
                        curr_len = int(n_points[island_pos])
                        delta_len = curr_len
                        delta_chunk = delta_concat[delta_pos:delta_pos + delta_len]
                        delta_pos += delta_len
                        prev1 = _pad_float64(track_int_history[-1], delta_len)
                        prev2 = _pad_float64(track_int_history[-2], delta_len)
                        intensity = (delta_chunk + 2 * prev1 - prev2)[:curr_len]
                    else:
                        curr_len = int(n_points[island_pos])
                        if ref_offset == 0:
                            if cross_track_ref_pos >= len(cross_track_ref_indices):
                                raise ValueError("Corrupt TrackCodec MS1 stream: missing cross-track intensity reference index")
                            ref_idx = int(cross_track_ref_indices[cross_track_ref_pos])
                            cross_track_ref_pos += 1
                            if ref_idx not in cross_track_int_history:
                                raise ValueError("Corrupt TrackCodec MS1 stream: invalid cross-track intensity reference index")
                            ref_intensity = cross_track_int_history[ref_idx]
                        else:
                            if ref_offset > len(track_int_history):
                                raise ValueError("Corrupt TrackCodec MS1 stream: delta island without valid intensity reference")
                            ref_intensity = track_int_history[-ref_offset]
                        ref_len = len(ref_intensity)
                        delta_len = self._delta_encoded_length(
                            curr_len,
                            ref_len,
                            mode=delta_length_mode,
                            padded_delta_max_diff=padded_delta_max_diff,
                        )
                        delta_chunk = delta_concat[delta_pos:delta_pos + delta_len]
                        delta_pos += delta_len
                        padded_prev = _pad_float64(ref_intensity, delta_len)
                        intensity = (padded_prev + delta_chunk)[:curr_len]

                n = int(n_points[island_pos])
                if omit_island_mz:
                    mz_array = np.array([], dtype=np.float64)
                    mz_q = None
                else:
                    mz_kind = int(mz_kinds[island_pos]) if island_pos < len(mz_kinds) else MZ_KIND_RAW
                    mz_idx = int(mz_refs[island_pos])
                    if mz_kind == MZ_KIND_RAW:
                        mz_q = np.asarray(mz_library_q[mz_idx], dtype=np.uint64).copy()
                    elif mz_kind == MZ_KIND_PREV_OFFSET:
                        if prev_mz_q is None:
                            raise ValueError("Corrupt TrackCodec MS1 stream: offset-mode m/z without previous island")
                        mz_q64 = prev_mz_q.astype(np.int64) + int(mz_model_offsets[mz_idx])
                        if np.any(mz_q64 < 0):
                            raise ValueError("Corrupt TrackCodec MS1 stream: offset-model m/z below zero")
                        mz_q = mz_q64.astype(np.uint64)
                    elif mz_kind == MZ_KIND_PREV_OFFSET_RESIDUAL:
                        if prev_mz_q is None:
                            raise ValueError("Corrupt TrackCodec MS1 stream: residual offset-mode m/z without previous island")
                        base = prev_mz_q.astype(np.int64) + int(mz_model_offsets[mz_idx])
                        step = int(mz_model_steps[mz_idx]) if mz_idx < len(mz_model_steps) else 0
                        if step:
                            base = base + step * np.arange(len(prev_mz_q), dtype=np.int64)
                        mz_q64 = base
                        if mz_idx >= len(mz_model_residuals):
                            raise ValueError("Corrupt TrackCodec MS1 stream: missing residual offset payload")
                        residual = mz_model_residuals[mz_idx].astype(np.int64)
                        if len(residual) != len(prev_mz_q):
                            raise ValueError("Corrupt TrackCodec MS1 stream: residual offset length mismatch")
                        mz_q64 = mz_q64 + residual
                        if np.any(mz_q64 < 0):
                            raise ValueError("Corrupt TrackCodec MS1 stream: residual offset-model m/z below zero")
                        mz_q = mz_q64.astype(np.uint64)
                    else:
                        raise ValueError(f"Unsupported mz_kind {mz_kind}")

                    mz_array = mz_q.astype(np.float64) / scale
                    if n != len(mz_array):
                        raise ValueError(f"Decoded m/z length mismatch: {len(mz_array)} vs {n}")
                island = {
                    "track_id": int(track_id),
                    "scan_idx": int(scan_indices[island_pos]),
                    "mz_start": float(mz_array[0]) if len(mz_array) else 0.0,
                    "mz_step": float(mz_array[1] - mz_array[0]) if len(mz_array) > 1 else 0.0,
                    "n_points": n,
                    "array_start_idx": int(array_starts[island_pos]) if array_starts is not None and len(array_starts) else -1,
                    "mz_array": mz_array,
                    "intensity_array": intensity,
                }
                _append_track_history(track_int_history, intensity)
                if equal_fidelity:
                    _append_track_history(track_code_history, intensity_code)
                    if island_pos in needed_cross_track_refs:
                        cross_track_code_history[island_pos] = np.asarray(intensity_code, dtype=np.int64)
                else:
                    if island_pos in needed_cross_track_refs:
                        cross_track_int_history[island_pos] = np.asarray(intensity, dtype=np.float64)
                prev_mz_q = mz_q
                island_pos += 1
                yield island

    def decode(self, compressed_bytes, metadata=None):
        t0 = time.perf_counter()
        header, segments = _unpack_segments(compressed_bytes)
        del segments
        islands = list(self.iter_decoded_islands(compressed_bytes, metadata))
        decode_time = time.perf_counter() - t0
        return islands, header, decode_time

    def decode_full_scans(self, compressed_bytes, metadata=None, original_rts=None):
        header, segments = _unpack_segments(compressed_bytes)
        if not header.get("retain_zero_intensity_mz", header.get("preserve_full_scan", False)):
            raise ValueError(
                "This MS1 payload does not retain zero-intensity-position m/z values for full-scan reconstruction"
            )
        segment_meta = header["segment_meta"]
        full_scan_indices, full_mz_arrays = self._decode_full_scan_sidecar(segments, segment_meta)
        islands, _, decode_time = self.decode(compressed_bytes, metadata)
        by_scan: Dict[int, List[Dict]] = {}
        for isl in islands:
            by_scan.setdefault(int(isl["scan_idx"]), []).append(isl)

        reconstructed = []

        def _infer_array_start_idx(mz_full: np.ndarray, target: np.ndarray) -> int:
            n = int(len(target))
            if n <= 0 or len(mz_full) < n:
                return 0
            pos = int(np.searchsorted(mz_full, float(target[0])))
            candidates = []
            for cand in range(max(0, pos - 3), min(len(mz_full) - n + 1, pos + 4)):
                diff = np.max(np.abs(mz_full[cand:cand + n] - target))
                candidates.append((float(diff), cand))
            if not candidates:
                return max(0, min(len(mz_full) - n, pos))
            candidates.sort(key=lambda x: (x[0], x[1]))
            return int(candidates[0][1])

        for local_pos, mz_array in enumerate(full_mz_arrays):
            scan_idx = int(full_scan_indices[local_pos]) if local_pos < len(full_scan_indices) else int(local_pos)
            mz_array = np.asarray(mz_array, dtype=np.float64)
            intensity_array = np.zeros(len(mz_array), dtype=np.float64)
            for isl in by_scan.get(scan_idx, []):
                start = int(isl.get("array_start_idx", -1))
                if start < 0:
                    start = _infer_array_start_idx(mz_array, np.asarray(isl["mz_array"], dtype=np.float64))
                end = start + int(isl["n_points"])
                if end <= len(intensity_array):
                    intensity_array[start:end] = np.asarray(isl["intensity_array"], dtype=np.float64)
            reconstructed.append(
                {
                    "scan_idx": int(scan_idx),
                    "rt": float(original_rts[local_pos]) if original_rts is not None and local_pos < len(original_rts) else float(scan_idx),
                    "mz_array": mz_array,
                    "intensity_array": intensity_array,
                }
            )
        return reconstructed, decode_time
