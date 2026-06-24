"""TrackCodec MS1 short-track sidecar codec.

This module is intentionally not wired into the default Archive path yet.  It
probes a hybrid representation for files where millions of length-1/2/3 tracks
make exact-track metadata dominate runtime and size.
"""

from __future__ import annotations

import json
import struct
import time
from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np

from ..common.compression_backends import available_backends, compress, decompress
from ..common.tracking import CompactIslandTracks
from .cross_scan_codec import (
    EMPTY_INT64,
    EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT,
    _decode_signed_int64_from_int32_main,
    _decode_uint32_stream,
    _encode_signed_int64_to_int32_main,
    _encode_uint32_stream_best,
)


MAGIC_SHORT = b"MS1ST1\0\0"


@dataclass(slots=True)
class TrackSplit:
    long_tracks: CompactIslandTracks
    short_track_indices: np.ndarray
    short_island_start: np.ndarray
    short_island_end: np.ndarray
    short_island_count: int
    long_island_count: int
    short_point_count: int
    long_point_count: int


def split_compact_tracks_by_length(
    tracks: CompactIslandTracks,
    *,
    short_track_max_len: int = 3,
) -> TrackSplit:
    if not isinstance(tracks, CompactIslandTracks):
        raise TypeError("split_compact_tracks_by_length expects CompactIslandTracks")
    if len(tracks.islands):
        raise ValueError("split_compact_tracks_by_length expects views-only CompactIslandTracks")
    max_len = max(1, int(short_track_max_len))
    track_lengths = np.asarray(tracks.track_lengths, dtype=np.uint32)
    track_offsets = np.asarray(tracks.track_offsets, dtype=np.uint32)
    short_track_indices = np.flatnonzero(track_lengths <= max_len).astype(np.uint32)
    long_track_indices = np.flatnonzero(track_lengths > max_len).astype(np.uint32)

    long_lengths = track_lengths[long_track_indices].astype(np.uint32, copy=True)
    long_offsets = np.empty(len(long_lengths) + 1, dtype=np.uint32)
    long_offsets[0] = 0
    if len(long_lengths):
        long_offsets[1:] = np.cumsum(long_lengths, dtype=np.uint64).astype(np.uint32)
    long_island_count = int(long_offsets[-1])
    long_scan_indices = np.empty(long_island_count, dtype=np.uint32)
    long_array_starts = np.empty(long_island_count, dtype=np.uint32)
    long_n_points = np.empty(long_island_count, dtype=np.uint32)
    dst = 0
    long_point_count = 0
    for track_idx in long_track_indices:
        start = int(track_offsets[int(track_idx)])
        end = int(track_offsets[int(track_idx) + 1])
        n = end - start
        if n <= 0:
            continue
        long_scan_indices[dst:dst + n] = np.asarray(tracks.scan_indices[start:end], dtype=np.uint32)
        long_array_starts[dst:dst + n] = np.asarray(tracks.array_start_indices[start:end], dtype=np.uint32)
        long_n_points[dst:dst + n] = np.asarray(tracks.n_points[start:end], dtype=np.uint32)
        long_point_count += int(np.sum(long_n_points[dst:dst + n], dtype=np.uint64))
        dst += n
    long_tracks = CompactIslandTracks(
        track_lengths=long_lengths,
        track_offsets=long_offsets,
        islands=[],
        scan_indices=long_scan_indices,
        array_start_indices=long_array_starts,
        n_points=long_n_points,
        point_count=int(long_point_count),
    )

    short_start = track_offsets[short_track_indices].astype(np.uint32, copy=True)
    short_end = track_offsets[short_track_indices + 1].astype(np.uint32, copy=True)
    short_point_count = 0
    for start, end in zip(short_start, short_end):
        short_point_count += int(np.sum(np.asarray(tracks.n_points[int(start):int(end)], dtype=np.uint32), dtype=np.uint64))

    return TrackSplit(
        long_tracks=long_tracks,
        short_track_indices=short_track_indices,
        short_island_start=short_start,
        short_island_end=short_end,
        short_island_count=int(np.sum(track_lengths[short_track_indices], dtype=np.uint64)) if len(short_track_indices) else 0,
        long_island_count=long_island_count,
        short_point_count=int(short_point_count),
        long_point_count=int(long_point_count),
    )


def _backend_candidates(default_backend: str, adaptive_uint32: bool) -> list[str]:
    candidates = [default_backend]
    if adaptive_uint32:
        available = set(available_backends())
        for candidate in ("brotli", "zstd-19", "zstd-9", "zlib"):
            if candidate in available and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _encode_u32(arr: np.ndarray, *, backend: str, adaptive_uint32: bool, zero_rle: bool, stream_name: str) -> Tuple[bytes, Dict]:
    return _encode_uint32_stream_best(
        np.asarray(arr, dtype=np.uint32),
        backend,
        use_pfor=False,
        pfor_codec="simdfastpfor256",
        zero_rle=bool(zero_rle),
        backend_candidates=_backend_candidates(backend, adaptive_uint32),
        try_pfor=False,
        try_zero_rle=bool(zero_rle),
        allow_bitpack=True,
        search_mode="converged",
        sample_count=16384,
    )


def _encode_i64(arr: np.ndarray, *, backend: str, adaptive_uint32: bool, zero_rle: bool) -> Tuple[bytes, bytes, bytes, Dict, Dict, Dict]:
    main_i32, overflow_idx, overflow_vals = _encode_signed_int64_to_int32_main(np.asarray(arr, dtype=np.int64))
    main_payload, main_meta = _encode_u32(
        ((main_i32.astype(np.int64) << 1) ^ (main_i32.astype(np.int64) >> 31)).astype(np.uint32),
        backend=backend,
        adaptive_uint32=adaptive_uint32,
        zero_rle=zero_rle,
        stream_name="intensity_main",
    )
    overflow_idx_payload, overflow_idx_meta = _encode_u32(
        overflow_idx,
        backend=backend,
        adaptive_uint32=adaptive_uint32,
        zero_rle=False,
        stream_name="intensity_overflow_idx",
    )
    overflow_backend = backend
    overflow_payload = compress(np.asarray(overflow_vals, dtype=np.int64).tobytes(), overflow_backend) if len(overflow_vals) else b""
    overflow_meta = {"count": int(len(overflow_vals)), "outer_backend": overflow_backend}
    return main_payload, overflow_idx_payload, overflow_payload, main_meta, overflow_idx_meta, overflow_meta


def _decode_i64(main_payload: bytes, overflow_idx_payload: bytes, overflow_payload: bytes, main_meta: Dict, overflow_idx_meta: Dict, overflow_meta: Dict) -> np.ndarray:
    main_u32 = _decode_uint32_stream(main_payload, main_meta)
    main_i32 = ((main_u32.astype(np.uint64).astype(np.int64) >> 1) ^ -(main_u32.astype(np.uint64).astype(np.int64) & 1)).astype(np.int32)
    overflow_idx = _decode_uint32_stream(overflow_idx_payload, overflow_idx_meta)
    if int(overflow_meta.get("count", 0)):
        overflow_vals = np.frombuffer(decompress(overflow_payload, overflow_meta["outer_backend"]), dtype=np.int64).copy()
    else:
        overflow_vals = EMPTY_INT64
    return _decode_signed_int64_from_int32_main(main_i32, overflow_idx, overflow_vals)


def _collect_short_island_records(
    tracks: CompactIslandTracks,
    split: TrackSplit,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n_records = int(split.short_island_count)
    if n_records == 0:
        return (
            np.array([], dtype=np.uint32),
            np.array([], dtype=np.uint32),
            np.array([], dtype=np.uint32),
        )

    scan_indices = np.empty(n_records, dtype=np.uint32)
    array_starts = np.empty(n_records, dtype=np.uint32)
    n_points = np.empty(n_records, dtype=np.uint32)
    pos = 0
    for start, end in zip(split.short_island_start, split.short_island_end):
        start_i = int(start)
        end_i = int(end)
        count = end_i - start_i
        if count <= 0:
            continue
        next_pos = pos + count
        scan_indices[pos:next_pos] = np.asarray(tracks.scan_indices[start_i:end_i], dtype=np.uint32)
        array_starts[pos:next_pos] = np.asarray(tracks.array_start_indices[start_i:end_i], dtype=np.uint32)
        n_points[pos:next_pos] = np.asarray(tracks.n_points[start_i:end_i], dtype=np.uint32)
        pos = next_pos

    if pos != n_records:
        scan_indices = scan_indices[:pos]
        array_starts = array_starts[:pos]
        n_points = n_points[:pos]

    if len(scan_indices) > 1:
        order = np.lexsort((array_starts, scan_indices))
        scan_indices = scan_indices[order]
        array_starts = array_starts[order]
        n_points = n_points[order]
    return scan_indices, array_starts, n_points


def _merge_short_records_to_runs(
    scan_indices: np.ndarray,
    array_starts: np.ndarray,
    n_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    if len(scan_indices) == 0:
        return (
            np.array([], dtype=np.uint32),
            np.array([], dtype=np.uint32),
            np.array([], dtype=np.uint32),
            0,
        )

    run_scan_indices: list[int] = []
    run_array_starts: list[int] = []
    run_n_points: list[int] = []
    original_island_count = int(len(scan_indices))

    curr_scan = int(scan_indices[0])
    curr_start = int(array_starts[0])
    curr_len = int(n_points[0])
    curr_end = curr_start + curr_len

    for idx in range(1, int(len(scan_indices))):
        scan_idx = int(scan_indices[idx])
        start = int(array_starts[idx])
        length = int(n_points[idx])
        if scan_idx == curr_scan and start == curr_end:
            curr_len += length
            curr_end += length
            continue
        if scan_idx == curr_scan and start < curr_end:
            raise ValueError(
                f"Overlapping short-track islands in scan {scan_idx}: "
                f"start={start}, current_end={curr_end}"
            )
        run_scan_indices.append(curr_scan)
        run_array_starts.append(curr_start)
        run_n_points.append(curr_len)
        curr_scan = scan_idx
        curr_start = start
        curr_len = length
        curr_end = start + length

    run_scan_indices.append(curr_scan)
    run_array_starts.append(curr_start)
    run_n_points.append(curr_len)

    return (
        np.asarray(run_scan_indices, dtype=np.uint32),
        np.asarray(run_array_starts, dtype=np.uint32),
        np.asarray(run_n_points, dtype=np.uint32),
        original_island_count,
    )


class ShortTrackSidecarCodec:
    """Scan-local sidecar for short tracks.

    The sidecar stores no m/z arrays.  It relies on Archive/full-sidecar mode to
    preserve the complete m/z grid, and stores only scan-local start/length plus
    equal-fidelity intensity codes for short-track islands.
    """

    def __init__(
        self,
        *,
        backend: str = "brotli",
        intensity_precision: int = 1,
        adaptive_uint32: bool = True,
        zero_rle: bool = True,
    ):
        self.backend = str(backend)
        self.intensity_precision = int(intensity_precision)
        self.adaptive_uint32 = bool(adaptive_uint32)
        self.zero_rle = bool(zero_rle)

    def encode(self, tracks: CompactIslandTracks, split: TrackSplit, ms1_scans) -> tuple[bytes, dict]:
        t0 = time.perf_counter()
        short_scan_indices, short_array_starts, short_n_points = _collect_short_island_records(tracks, split)
        run_scan_indices, run_array_starts, run_n_points, original_island_count = _merge_short_records_to_runs(
            short_scan_indices,
            short_array_starts,
            short_n_points,
        )

        intensity_codes_parts = []
        for scan_idx, arr_start, length in zip(run_scan_indices, run_array_starts, run_n_points):
            scan = ms1_scans[int(scan_idx)]
            start_i = int(arr_start)
            length_i = int(length)
            intensity = np.asarray(scan["intensity_array"], dtype=np.float64)[start_i:start_i + length_i]
            intensity_codes_parts.append(np.round(intensity * float(self.intensity_precision)).astype(np.int64))

        if intensity_codes_parts:
            intensity_codes = np.concatenate(intensity_codes_parts).astype(np.int64, copy=False)
        else:
            intensity_codes = EMPTY_INT64

        segments = {}
        segment_meta = {}
        for name, arr in (
            ("scan_indices", run_scan_indices),
            ("array_starts", run_array_starts),
            ("n_points", run_n_points),
        ):
            payload, meta = _encode_u32(
                arr,
                backend=self.backend,
                adaptive_uint32=self.adaptive_uint32,
                zero_rle=self.zero_rle,
                stream_name=name,
            )
            segments[name] = payload
            segment_meta[name] = meta

        (
            segments["intensity_main"],
            segments["intensity_overflow_idx"],
            segments["intensity_overflow_vals"],
            segment_meta["intensity_main"],
            segment_meta["intensity_overflow_idx"],
            segment_meta["intensity_overflow_vals"],
        ) = _encode_i64(
            intensity_codes,
            backend=self.backend,
            adaptive_uint32=self.adaptive_uint32,
            zero_rle=self.zero_rle,
        )

        header = {
            "format": "ms1_short_track_sidecar",
            "record_kind": "scan_local_runs",
            "backend": self.backend,
            "intensity_precision": int(self.intensity_precision),
            "intensity_mode": "equal_fidelity_codes",
            "overflow_mode": EQUAL_FIDELITY_OVERFLOW_MODE_STRICT_LOSSLESS_FULL_SEGMENT,
            "short_track_count": int(len(split.short_track_indices)),
            "short_island_count": int(original_island_count),
            "short_run_count": int(len(run_scan_indices)),
            "short_point_count": int(len(intensity_codes)),
            "segment_meta": segment_meta,
        }
        payload = _pack_short_segments(header, segments)
        raw_bytes = int(len(intensity_codes) * 8 + len(run_scan_indices) * 12)
        meta = {
            "raw_bytes": raw_bytes,
            "compressed_bytes": int(len(payload)),
            "compression_ratio": float(raw_bytes / len(payload)) if len(payload) else 0.0,
            "encode_time_s": float(time.perf_counter() - t0),
            "segment_sizes": {name: len(blob) for name, blob in segments.items()},
            "short_track_count": int(len(split.short_track_indices)),
            "short_island_count": int(original_island_count),
            "short_run_count": int(len(run_scan_indices)),
            "short_point_count": int(len(intensity_codes)),
        }
        return payload, meta

    def iter_decoded_runs(self, payload: bytes):
        header, segments = _unpack_short_segments(payload)
        meta = header["segment_meta"]
        scan_indices = _decode_uint32_stream(segments["scan_indices"], meta["scan_indices"])
        array_starts = _decode_uint32_stream(segments["array_starts"], meta["array_starts"])
        n_points = _decode_uint32_stream(segments["n_points"], meta["n_points"])
        intensity_codes = _decode_i64(
            segments["intensity_main"],
            segments["intensity_overflow_idx"],
            segments["intensity_overflow_vals"],
            meta["intensity_main"],
            meta["intensity_overflow_idx"],
            meta["intensity_overflow_vals"],
        )
        precision = float(header["intensity_precision"])
        pos = 0
        for island_idx in range(int(len(scan_indices))):
            n = int(n_points[island_idx])
            codes = intensity_codes[pos:pos + n]
            pos += n
            yield {
                "scan_idx": int(scan_indices[island_idx]),
                "array_start_idx": int(array_starts[island_idx]),
                "n_points": n,
                "intensity_array": codes.astype(np.float64) / precision,
            }

def _pack_short_segments(header: Dict, segments: Dict[str, bytes]) -> bytes:
    header_json = json.dumps(header, default=str, separators=(",", ":")).encode("utf-8")
    parts = [MAGIC_SHORT, struct.pack("<I", len(header_json)), header_json, struct.pack("<I", len(segments))]
    for name, payload in segments.items():
        name_bytes = name.encode("utf-8")
        parts.extend((struct.pack("<H", len(name_bytes)), name_bytes, struct.pack("<Q", len(payload)), payload))
    return b"".join(parts)


def _unpack_short_segments(blob: bytes) -> Tuple[Dict, Dict[str, bytes]]:
    offset = 0
    magic = blob[offset:offset + len(MAGIC_SHORT)]
    if magic != MAGIC_SHORT:
        raise ValueError("Not a short-track sidecar payload")
    offset += len(MAGIC_SHORT)
    header_len = struct.unpack("<I", blob[offset:offset + 4])[0]
    offset += 4
    header = json.loads(blob[offset:offset + header_len].decode("utf-8"))
    offset += header_len
    n_segments = struct.unpack("<I", blob[offset:offset + 4])[0]
    offset += 4
    segments = {}
    for _ in range(n_segments):
        name_len = struct.unpack("<H", blob[offset:offset + 2])[0]
        offset += 2
        name = blob[offset:offset + name_len].decode("utf-8")
        offset += name_len
        data_len = struct.unpack("<Q", blob[offset:offset + 8])[0]
        offset += 8
        segments[name] = blob[offset:offset + data_len]
        offset += data_len
    return header, segments


def compare_short_sidecar_roundtrip(payload: bytes, tracks: CompactIslandTracks, split: TrackSplit, ms1_scans) -> dict:
    codec = ShortTrackSidecarCodec()
    max_abs_intensity = 0.0
    decoded_run_count = 0
    decoded_point_count = 0
    short_scan_indices, short_array_starts, short_n_points = _collect_short_island_records(tracks, split)
    exp_scan_indices, exp_array_starts, exp_n_points, _ = _merge_short_records_to_runs(
        short_scan_indices,
        short_array_starts,
        short_n_points,
    )

    decoded_iter = iter(codec.iter_decoded_runs(payload))
    for exp_scan, exp_start, exp_len in zip(exp_scan_indices, exp_array_starts, exp_n_points):
        decoded = next(decoded_iter)
        scan_idx = int(exp_scan)
        arr_start = int(exp_start)
        n = int(exp_len)
        if scan_idx != int(decoded["scan_idx"]) or arr_start != int(decoded["array_start_idx"]) or n != int(decoded["n_points"]):
            raise ValueError("Short sidecar run metadata roundtrip mismatch")
        orig = np.asarray(ms1_scans[scan_idx]["intensity_array"], dtype=np.float64)[arr_start:arr_start + n]
        dec = np.asarray(decoded["intensity_array"], dtype=np.float64)
        if len(orig) != len(dec):
            raise ValueError("Short sidecar intensity length mismatch")
        if len(orig):
            max_abs_intensity = max(max_abs_intensity, float(np.max(np.abs(orig - dec))))
        decoded_run_count += 1
        decoded_point_count += n
    try:
        next(decoded_iter)
    except StopIteration:
        pass
    else:
        raise ValueError("Short sidecar decoded extra runs")
    return {
        "short_decoded_runs": int(decoded_run_count),
        "short_decoded_points": int(decoded_point_count),
        "max_abs_intensity_error": float(max_abs_intensity),
    }
