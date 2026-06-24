"""DIA MS2 window-level dictionary codec prototype.

Core ideas:
- build fragment tracks inside one DIA window
- store fragment m/z once in a window dictionary
- preserve same-fidelity m/z by storing per-occurrence quantized residuals
- top-K stable fragments use dense per-cycle residual coding
- tail fragments use per-scan sparse index + intensity vectors
"""

from __future__ import annotations

import json
import struct
import time
from typing import Dict, List, Tuple

import numpy as np

from ..ms1 import baseline_ms1_codec
from ..ms1.cross_scan_codec import (
    _decode_signed_int64_from_int32_main,
    _decode_small_uint_stream,
    _decode_uint32_stream,
    _equal_fidelity_exact_scaled_int64,
    _encode_signed_int64_to_int32_main,
    _encode_small_uint_stream,
    _encode_uint32_stream_best,
    _pack_segments,
    _szdpd_decode_from_int32,
    _szdpd_encode_to_int32_with_precision,
    _unpack_segments,
    _zigzag_decode_uint32,
    _zigzag_encode_int32,
    CrossScanMS1Codec,
)
from ..common.compression_backends import compress, decompress

try:
    from ..ms1 import _cross_scan_speedups as _native_speedups
except (ImportError, AttributeError):  # pragma: no cover - optional native extension
    _native_speedups = None


MAGIC = b"DMS2W5\0\0"
EXACT_TRACK_MAGIC = b"DMS2ET6\0"
HEADER_BACKEND_TO_CODE = {
    "brotli": 1,
    "zstd-9": 2,
    "zlib": 3,
    "lz4": 4,
    "gzip": 5,
    "zstd-3": 6,
}
HEADER_CODE_TO_BACKEND = {value: key for key, value in HEADER_BACKEND_TO_CODE.items()}
EXACT_TRACK_SEGMENT_ORDER = [
    "dict_mz_q",
    "track_lengths",
    "scan_firsts",
    "scan_gaps",
    "int_first_codes",
    "int_delta_main",
    "int_delta_overflow_idx",
    "int_delta_overflow_vals",
]
EXACT_TRACK_OPTIONAL_SEGMENT_ORDER = [
    "int_first_overflow_idx",
    "int_first_overflow_vals",
]
INTENSITY_CODE_MODE_SZDPD_INT32 = "szdpd_int32"
INTENSITY_CODE_MODE_EXACT_SCALED_INT64_MAIN_OVERFLOW = "exact_scaled_int64_main_overflow"
SCAN_STEP_MODE_GAP_MINUS_ONE = "gap_minus_one"
SCAN_STEP_MODE_DELTA = "delta"


def _scan_store_view(scans):
    store = getattr(scans, "store", None)
    global_indices = getattr(scans, "global_indices", None)
    if store is None or global_indices is None:
        return None
    if not all(hasattr(store, name) for name in ("mz_data", "intensity_data", "offsets", "lengths")):
        return None
    return store, np.asarray(global_indices, dtype=np.uint32)


def _pack_exact_track_segments(header: Dict, segments: Dict[str, bytes], header_backend: str) -> bytes:
    if header_backend not in HEADER_BACKEND_TO_CODE:
        raise ValueError(f"Unsupported exact-track header backend: {header_backend}")
    header_json = json.dumps(header, separators=(",", ":"), default=str).encode("utf-8")
    header_blob = compress(header_json, header_backend)
    parts = [
        EXACT_TRACK_MAGIC,
        struct.pack("<B", HEADER_BACKEND_TO_CODE[header_backend]),
        struct.pack("<I", len(header_blob)),
        header_blob,
    ]
    for name in EXACT_TRACK_SEGMENT_ORDER + EXACT_TRACK_OPTIONAL_SEGMENT_ORDER:
        payload = segments.get(name, b"")
        parts.append(struct.pack("<I", len(payload)))
        parts.append(payload)
    return b"".join(parts)


def _unpack_exact_track_segments(blob: bytes) -> Tuple[Dict, Dict[str, bytes]]:
    offset = 0
    magic = blob[offset:offset + len(EXACT_TRACK_MAGIC)]
    if magic != EXACT_TRACK_MAGIC:
        raise ValueError("Not an exact-track payload")
    offset += len(EXACT_TRACK_MAGIC)
    backend_code = struct.unpack("<B", blob[offset:offset + 1])[0]
    offset += 1
    header_backend = HEADER_CODE_TO_BACKEND.get(backend_code)
    if header_backend is None:
        raise ValueError(f"Unsupported exact-track header backend code: {backend_code}")
    header_len = struct.unpack("<I", blob[offset:offset + 4])[0]
    offset += 4
    header = json.loads(decompress(blob[offset:offset + header_len], header_backend).decode("utf-8"))
    offset += header_len
    segments = {}
    for name in EXACT_TRACK_SEGMENT_ORDER:
        payload_len = struct.unpack("<I", blob[offset:offset + 4])[0]
        offset += 4
        segments[name] = blob[offset:offset + payload_len]
        offset += payload_len
    for name in EXACT_TRACK_OPTIONAL_SEGMENT_ORDER:
        if offset + 4 > len(blob):
            segments[name] = b""
            continue
        payload_len = struct.unpack("<I", blob[offset:offset + 4])[0]
        offset += 4
        segments[name] = blob[offset:offset + payload_len]
        offset += payload_len
    return header, segments


class DIAWindowDictionaryCodec:
    def __init__(
        self,
        *,
        backend: str = "brotli",
        mz_precision: int = 6,
        top_k: int = 128,
        min_track_length: int = 3,
        ppm_tol: float = 15.0,
        use_pfor: bool = True,
        pfor_min_count: int = 256,
        adaptive_uint32: bool = True,
        adaptive_search_mode: str = "converged",
        adaptive_search_sample_count: int = 8192,
        helper_compact_uint32: bool = False,
        helper_enable_stream_backend_tuning: bool = False,
        helper_extended_metadata_adaptive: bool = False,
        enable_signed_zero_rle_search: bool = False,
    ):
        self.backend = backend
        self.mz_precision = int(mz_precision)
        self.mz_scale = 10 ** self.mz_precision
        self.top_k = int(top_k)
        self.min_track_length = int(min_track_length)
        self.ppm_tol = float(ppm_tol)
        self.use_pfor = bool(use_pfor)
        self.pfor_min_count = int(pfor_min_count)
        self.adaptive_uint32 = bool(adaptive_uint32)
        self.adaptive_search_mode = adaptive_search_mode
        self.adaptive_search_sample_count = int(adaptive_search_sample_count)
        self.helper_compact_uint32 = bool(helper_compact_uint32)
        self.helper_enable_stream_backend_tuning = bool(helper_enable_stream_backend_tuning)
        self.helper_extended_metadata_adaptive = bool(helper_extended_metadata_adaptive)
        self.enable_signed_zero_rle_search = bool(enable_signed_zero_rle_search)
        self.helper = CrossScanMS1Codec(
            mz_precision=6,
            intensity_mode="szdpd_xdelta_equalfidelity",
            backend=backend,
            use_pfor=use_pfor,
            pfor_min_count=pfor_min_count,
            metadata_transform=True,
            adaptive_uint32=adaptive_uint32,
            adaptive_intensity_search=False,
            adaptive_search_mode=adaptive_search_mode,
            adaptive_search_sample_count=adaptive_search_sample_count,
            compact_uint32=self.helper_compact_uint32,
            enable_stream_backend_tuning=self.helper_enable_stream_backend_tuning,
            extended_metadata_adaptive=self.helper_extended_metadata_adaptive,
        )

    def _quantize_mz(self, values: np.ndarray) -> np.ndarray:
        return np.round(np.asarray(values, dtype=np.float64) * self.mz_scale).astype(np.int64)

    def _encode_dict_mz_stream(self, values: np.ndarray) -> Tuple[bytes, Dict]:
        values = np.asarray(values, dtype=np.int64)
        if len(values) == 0:
            payload, payload_meta = self._encode_uint32_meta(np.array([], dtype=np.uint32), "mz_first_values")
            payload_meta["dict_storage"] = "u32_offsets"
            payload_meta["anchor_q"] = 0
            return payload, payload_meta

        anchor_q = int(values[0])
        offsets = values - anchor_q
        if offsets.min(initial=0) >= 0 and offsets.max(initial=0) <= np.iinfo(np.uint32).max:
            payload, payload_meta = self._encode_uint32_meta(offsets.astype(np.uint32, copy=False), "mz_first_values")
            payload_meta["dict_storage"] = "u32_offsets"
            payload_meta["anchor_q"] = anchor_q
            return payload, payload_meta

        raw = values.astype(np.int64, copy=False).tobytes()
        best_backend = self.backend
        best_payload = compress(raw, self.backend)
        for candidate in self.helper._candidate_backends("mz_first_values"):
            cand = compress(raw, candidate)
            if len(cand) < len(best_payload):
                best_payload = cand
                best_backend = candidate
        return best_payload, {
            "dict_storage": "int64_raw",
            "count": int(len(values)),
            "outer_backend": best_backend,
        }

    def _decode_dict_mz_stream(self, payload: bytes, meta: Dict) -> np.ndarray:
        kind = meta.get("dict_storage", "u32_offsets")
        if kind == "u32_offsets":
            offsets = self._decode_uint32_meta(payload, meta).astype(np.int64)
            return offsets + int(meta.get("anchor_q", 0))
        if kind == "int64_raw":
            raw = decompress(payload, meta["outer_backend"])
            return np.frombuffer(raw, dtype=np.int64).copy()
        raise ValueError(f"Unsupported dict m/z storage: {kind}")

    def _encode_uint32_meta(self, arr: np.ndarray, stream_name: str) -> Tuple[bytes, Dict]:
        return self.helper._encode_transformed_uint32_metadata(np.asarray(arr, dtype=np.uint32), stream_name)

    def _decode_uint32_meta(self, payload: bytes, meta: Dict) -> np.ndarray:
        return self.helper._decode_transformed_uint32_metadata(payload, meta)

    def _encode_compact_uint32_meta(
        self,
        arr: np.ndarray,
        stream_name: str,
        *,
        prefer_small_uint: bool = False,
    ) -> Tuple[bytes, Dict]:
        arr = np.asarray(arr, dtype=np.uint32)
        max_val = int(arr.max(initial=0)) if len(arr) else 0
        small_bits = []
        if max_val <= 1:
            small_bits.append(1)
        if max_val <= 3:
            small_bits.append(2)
        if max_val <= 15:
            small_bits.append(4)
        if max_val <= 255:
            small_bits.append(8)

        if prefer_small_uint and small_bits:
            payload, meta = _encode_small_uint_stream(
                arr.astype(np.uint8, copy=False),
                self.backend,
                bits=small_bits[0],
                backend_candidates=[self.backend],
            )
            return payload, {**meta, "small_uint": True}

        best_payload, best_meta = self._encode_uint32_meta(arr, stream_name)
        best_score = (len(best_payload), 1)
        if small_bits:
            arr8 = arr.astype(np.uint8, copy=False)
            for bits in small_bits:
                payload, meta = _encode_small_uint_stream(
                    arr8,
                    self.backend,
                    bits=bits,
                    backend_candidates=self.helper._candidate_backends(stream_name),
                )
                score = (len(payload), 0)
                if score < best_score:
                    best_payload = payload
                    best_meta = {**meta, "small_uint": True}
                    best_score = score
        return best_payload, best_meta

    def _decode_compact_uint32_meta(self, payload: bytes, meta: Dict) -> np.ndarray:
        if meta.get("small_uint"):
            return _decode_small_uint_stream(payload, meta).astype(np.uint32)
        return self._decode_uint32_meta(payload, meta).astype(np.uint32)

    def _encode_signed_int32_stream(self, arr: np.ndarray, stream_name: str) -> Tuple[bytes, Dict]:
        encoded = _zigzag_encode_int32(np.asarray(arr, dtype=np.int32))
        payload, payload_meta = _encode_uint32_stream_best(
            encoded,
            self.backend,
            use_pfor=self.use_pfor and len(encoded) >= self.pfor_min_count,
            pfor_codec=self.helper.pfor_codec,
            zero_rle=self.enable_signed_zero_rle_search and stream_name in {"full_intensity", "delta_intensity"},
            backend_candidates=self.helper._candidate_backends(stream_name),
            try_pfor=self.adaptive_uint32 and len(encoded) >= self.pfor_min_count,
            try_zero_rle=self.adaptive_uint32 and self.enable_signed_zero_rle_search and stream_name in {"full_intensity", "delta_intensity"},
            search_mode=self.adaptive_search_mode,
            sample_count=self.adaptive_search_sample_count,
        )
        return payload, payload_meta

    def _decode_signed_int32_stream(self, payload: bytes, meta: Dict) -> np.ndarray:
        return _zigzag_decode_uint32(_decode_uint32_stream(payload, meta)).astype(np.int32)

    def _encode_scan_count_vector(self, counts: np.ndarray, prefix: str) -> Tuple[Dict[str, bytes], Dict]:
        counts = np.asarray(counts, dtype=np.uint32)
        full_payload, full_meta = self._encode_compact_uint32_meta(
            counts,
            f"{prefix}_counts",
            prefer_small_uint=True,
        )
        best_segments = {f"{prefix}_counts": full_payload}
        best_meta = {f"{prefix}_counts": {**full_meta, "count_mode": "dense"}}
        best_bytes = len(full_payload)

        active_idx = np.flatnonzero(counts).astype(np.uint32)
        if len(active_idx) and len(active_idx) < len(counts):
            active_deltas = np.empty(len(active_idx), dtype=np.uint32)
            active_deltas[0] = active_idx[0]
            if len(active_idx) > 1:
                active_deltas[1:] = np.diff(active_idx).astype(np.uint32)
            active_counts = counts[active_idx].astype(np.uint32, copy=False)
            idx_payload, idx_meta = self._encode_compact_uint32_meta(
                active_deltas,
                f"{prefix}_active_scan_deltas",
                prefer_small_uint=True,
            )
            cnt_payload, cnt_meta = self._encode_compact_uint32_meta(
                active_counts,
                f"{prefix}_active_scan_counts",
                prefer_small_uint=True,
            )
            sparse_bytes = len(idx_payload) + len(cnt_payload)
            if sparse_bytes < best_bytes:
                best_segments = {
                    f"{prefix}_active_scan_deltas": idx_payload,
                    f"{prefix}_active_scan_counts": cnt_payload,
                }
                best_meta = {
                    f"{prefix}_active_scan_deltas": {**idx_meta, "count_mode": "sparse", "n_scans": int(len(counts))},
                    f"{prefix}_active_scan_counts": cnt_meta,
                }
                best_bytes = sparse_bytes
        return best_segments, best_meta

    def _decode_scan_count_vector(self, segments: Dict[str, bytes], meta: Dict, prefix: str, n_scans: int) -> np.ndarray:
        dense_name = f"{prefix}_counts"
        if dense_name in segments and dense_name in meta:
            return self._decode_compact_uint32_meta(segments[dense_name], meta[dense_name]).astype(np.int64)

        delta_name = f"{prefix}_active_scan_deltas"
        count_name = f"{prefix}_active_scan_counts"
        if delta_name in segments and count_name in segments:
            active_deltas = self._decode_compact_uint32_meta(segments[delta_name], meta[delta_name]).astype(np.int64)
            active_counts = self._decode_compact_uint32_meta(segments[count_name], meta[count_name]).astype(np.int64)
            out = np.zeros(n_scans, dtype=np.int64)
            if len(active_deltas):
                active_idx = np.cumsum(active_deltas, dtype=np.int64)
                out[active_idx] = active_counts
            return out
        return np.zeros(n_scans, dtype=np.int64)

    def _encode_tail_index_vector(self, counts: np.ndarray, deltas: np.ndarray, prefix: str) -> Tuple[Dict[str, bytes], Dict]:
        counts = np.asarray(counts, dtype=np.uint32)
        deltas = np.asarray(deltas, dtype=np.uint32)
        payload, meta = self._encode_compact_uint32_meta(deltas, "mz_ref_indices", prefer_small_uint=True)
        best_segments = {f"{prefix}_deltas": payload}
        best_meta = {f"{prefix}_deltas": {"index_mode": "flat", **meta}}
        best_bytes = len(payload)

        active_counts = counts[counts > 0].astype(np.int64, copy=False)
        if len(active_counts):
            first_values = np.empty(len(active_counts), dtype=np.uint32)
            follow_count = int(np.maximum(active_counts - 1, 0).sum())
            follow_values = np.empty(follow_count, dtype=np.uint32)
            src_pos = 0
            follow_pos = 0
            for idx, nnz in enumerate(active_counts):
                first_values[idx] = deltas[src_pos]
                src_pos += 1
                if nnz > 1:
                    take = int(nnz) - 1
                    follow_values[follow_pos:follow_pos + take] = deltas[src_pos:src_pos + take]
                    src_pos += take
                    follow_pos += take

            first_payload, first_meta = self._encode_compact_uint32_meta(
                first_values,
                f"{prefix}_first_values",
                prefer_small_uint=True,
            )
            split_segments = {f"{prefix}_first_values": first_payload}
            split_meta = {
                f"{prefix}_first_values": {"index_mode": "split", **first_meta},
            }
            split_bytes = len(first_payload)
            if len(follow_values):
                follow_payload, follow_meta = self._encode_compact_uint32_meta(
                    follow_values,
                    f"{prefix}_follow_deltas",
                    prefer_small_uint=True,
                )
                split_segments[f"{prefix}_follow_deltas"] = follow_payload
                split_meta[f"{prefix}_follow_deltas"] = follow_meta
                split_bytes += len(follow_payload)
            if split_bytes < best_bytes:
                best_segments = split_segments
                best_meta = split_meta
        return best_segments, best_meta

    def _decode_tail_index_vector(self, segments: Dict[str, bytes], meta: Dict, counts: np.ndarray, prefix: str) -> np.ndarray:
        flat_name = f"{prefix}_deltas"
        if flat_name in segments and flat_name in meta:
            return self._decode_compact_uint32_meta(segments[flat_name], meta[flat_name]).astype(np.int64)

        first_name = f"{prefix}_first_values"
        if first_name not in segments or first_name not in meta:
            return np.array([], dtype=np.int64)
        first_values = self._decode_compact_uint32_meta(segments[first_name], meta[first_name]).astype(np.int64)
        follow_name = f"{prefix}_follow_deltas"
        if follow_name in segments and follow_name in meta:
            follow_values = self._decode_compact_uint32_meta(segments[follow_name], meta[follow_name]).astype(np.int64)
        else:
            follow_values = np.array([], dtype=np.int64)

        counts = np.asarray(counts, dtype=np.int64)
        total = int(counts.sum())
        out = np.empty(total, dtype=np.int64)
        first_pos = 0
        follow_pos = 0
        out_pos = 0
        for nnz in counts:
            if nnz <= 0:
                continue
            out[out_pos] = first_values[first_pos]
            first_pos += 1
            out_pos += 1
            if nnz > 1:
                take = int(nnz) - 1
                out[out_pos:out_pos + take] = follow_values[follow_pos:follow_pos + take]
                out_pos += take
                follow_pos += take
        return out

    def _build_entry_arrays(self, scans: List[Dict], window_target_mz: float = 0.0) -> Dict:
        """Build dictionary groups as contiguous arrays.

        Keeping this path SoA avoids millions of short-lived Python objects on
        centroid/DDA files while preserving stable m/z grouping and within-group
        scan order.
        """
        del window_target_mz
        scan_store_view = _scan_store_view(scans)
        if scan_store_view is not None:
            store, global_indices = scan_store_view
            indices_i64 = np.asarray(global_indices, dtype=np.int64)
            scan_lengths = np.asarray(store.lengths[indices_i64], dtype=np.uint64)
            offsets = np.asarray(store.offsets[indices_i64], dtype=np.uint64)
            raw_point_count = int(scan_lengths.astype(np.uint64, copy=False).sum())
            raw_bytes = int(raw_point_count * 16)
            if raw_point_count == 0:
                empty_u32 = np.empty(0, dtype=np.uint32)
                return {
                    "raw_point_count": int(raw_point_count),
                    "raw_bytes": int(raw_bytes),
                    "dict_mz_q": np.empty(0, dtype=np.int64),
                    "track_lengths": empty_u32,
                    "scan_firsts": empty_u32,
                    "flat_scan_idx": np.empty(0, dtype=np.int64),
                    "joined_intensity": np.empty(0, dtype=np.float64),
                    "starts": np.empty(0, dtype=np.int64),
                }
            if _native_speedups is not None and hasattr(_native_speedups, "ms2_exact_track_collect_entries"):
                try:
                    native = _native_speedups.ms2_exact_track_collect_entries(
                        np.asarray(store.mz_data, dtype=np.float64),
                        np.asarray(store.intensity_data, dtype=np.float64),
                        offsets.astype(np.uint64, copy=False),
                        scan_lengths.astype(np.uint32, copy=False),
                        float(self.mz_scale),
                    )
                    all_mz_q = np.asarray(native[0], dtype=np.int64)
                    all_intensity = np.asarray(native[1], dtype=np.float64)
                    all_scan_idx = np.asarray(native[2], dtype=np.uint32)
                    order = np.argsort(all_mz_q, kind="mergesort")
                    mz_sorted = all_mz_q[order]
                    intensity_sorted = all_intensity[order].astype(np.float64, copy=False)
                    scan_sorted = all_scan_idx[order].astype(np.int64, copy=False)
                    starts = np.concatenate((
                        np.array([0], dtype=np.int64),
                        np.flatnonzero(np.diff(mz_sorted)) + 1,
                    ))
                    ends = np.concatenate((starts[1:], np.array([len(mz_sorted)], dtype=np.int64)))
                    return {
                        "raw_point_count": int(raw_point_count),
                        "raw_bytes": int(raw_bytes),
                        "dict_mz_q": mz_sorted[starts].astype(np.int64, copy=False),
                        "track_lengths": (ends - starts).astype(np.uint32, copy=False),
                        "scan_firsts": scan_sorted[starts].astype(np.uint32, copy=False),
                        "flat_scan_idx": scan_sorted,
                        "joined_intensity": intensity_sorted,
                        "starts": starts,
                    }
                except RuntimeError as exc:
                    raise RuntimeError("Native MS2 exact-track entry collection failed") from exc
            all_mz_q = np.empty(raw_point_count, dtype=np.int64)
            all_intensity = np.empty(raw_point_count, dtype=np.float64)
            all_scan_idx = np.empty(raw_point_count, dtype=np.uint32)
            pos = 0
            for scan_idx, (offset, length) in enumerate(zip(offsets, scan_lengths)):
                n = int(length)
                if n == 0:
                    continue
                next_pos = pos + n
                start = int(offset)
                all_mz_q[pos:next_pos] = self._quantize_mz(store.mz_data[start:start + n]).astype(np.int64, copy=False)
                all_intensity[pos:next_pos] = np.asarray(store.intensity_data[start:start + n], dtype=np.float64)
                all_scan_idx[pos:next_pos] = np.uint32(scan_idx)
                pos = next_pos
            order = np.argsort(all_mz_q, kind="mergesort")
            mz_sorted = all_mz_q[order]
            intensity_sorted = all_intensity[order].astype(np.float64, copy=False)
            scan_sorted = all_scan_idx[order].astype(np.int64, copy=False)
            starts = np.concatenate((
                np.array([0], dtype=np.int64),
                np.flatnonzero(np.diff(mz_sorted)) + 1,
            ))
            ends = np.concatenate((starts[1:], np.array([len(mz_sorted)], dtype=np.int64)))
            track_lengths = (ends - starts).astype(np.uint32, copy=False)
            scan_firsts = scan_sorted[starts].astype(np.uint32, copy=False)
            return {
                "raw_point_count": int(raw_point_count),
                "raw_bytes": int(raw_bytes),
                "dict_mz_q": mz_sorted[starts].astype(np.int64, copy=False),
                "track_lengths": track_lengths,
                "scan_firsts": scan_firsts,
                "flat_scan_idx": scan_sorted,
                "joined_intensity": intensity_sorted,
                "starts": starts,
            }

        scan_lengths = np.empty(len(scans), dtype=np.uint64)
        raw_bytes = 0
        for scan_idx, scan in enumerate(scans):
            mz_arr = np.asarray(scan["mz_array"], dtype=np.float64)
            intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
            if int(mz_arr.size) != int(intensity.size):
                raise ValueError(
                    "MS2 scan has mismatched m/z and intensity lengths: "
                    f"scan_idx={scan_idx} mz_len={int(mz_arr.size)} intensity_len={int(intensity.size)}"
                )
            scan_lengths[scan_idx] = int(mz_arr.size)
            raw_bytes += int(mz_arr.size + intensity.size) * 8

        raw_point_count = int(scan_lengths.astype(np.uint64, copy=False).sum())
        if raw_point_count == 0:
            empty_u32 = np.empty(0, dtype=np.uint32)
            return {
                "raw_point_count": int(raw_point_count),
                "raw_bytes": int(raw_bytes),
                "dict_mz_q": np.empty(0, dtype=np.int64),
                "track_lengths": empty_u32,
                "scan_firsts": empty_u32,
                "flat_scan_idx": np.empty(0, dtype=np.int64),
                "joined_intensity": np.empty(0, dtype=np.float64),
                "starts": np.empty(0, dtype=np.int64),
            }

        all_mz_q = np.empty(raw_point_count, dtype=np.int64)
        all_intensity = np.empty(raw_point_count, dtype=np.float64)
        all_scan_idx = np.empty(raw_point_count, dtype=np.uint32)
        pos = 0
        for scan_idx, scan in enumerate(scans):
            length = int(scan_lengths[scan_idx])
            if length == 0:
                continue
            next_pos = pos + length
            all_mz_q[pos:next_pos] = self._quantize_mz(np.asarray(scan["mz_array"], dtype=np.float64)).astype(np.int64, copy=False)
            all_intensity[pos:next_pos] = np.asarray(scan["intensity_array"], dtype=np.float64)
            all_scan_idx[pos:next_pos] = np.uint32(scan_idx)
            pos = next_pos
        order = np.argsort(all_mz_q, kind="mergesort")
        mz_sorted = all_mz_q[order]
        intensity_sorted = all_intensity[order].astype(np.float64, copy=False)
        scan_sorted = all_scan_idx[order].astype(np.int64, copy=False)
        starts = np.concatenate((
            np.array([0], dtype=np.int64),
            np.flatnonzero(np.diff(mz_sorted)) + 1,
        ))
        ends = np.concatenate((starts[1:], np.array([len(mz_sorted)], dtype=np.int64)))
        track_lengths = (ends - starts).astype(np.uint32, copy=False)
        scan_firsts = scan_sorted[starts].astype(np.uint32, copy=False)
        return {
            "raw_point_count": int(raw_point_count),
            "raw_bytes": int(raw_bytes),
            "dict_mz_q": mz_sorted[starts].astype(np.int64, copy=False),
            "track_lengths": track_lengths,
            "scan_firsts": scan_firsts,
            "flat_scan_idx": scan_sorted,
            "joined_intensity": intensity_sorted,
            "starts": starts,
        }

    @staticmethod
    def _select_top_ids_from_arrays(
        *,
        track_lengths: np.ndarray,
        starts: np.ndarray,
        flat_scan_idx: np.ndarray,
        joined_intensity: np.ndarray,
        top_k: int,
        min_track_length: int,
    ) -> np.ndarray:
        track_lengths = np.asarray(track_lengths, dtype=np.int64)
        starts = np.asarray(starts, dtype=np.int64)
        flat_scan_idx = np.asarray(flat_scan_idx, dtype=np.int64)
        joined_intensity = np.asarray(joined_intensity, dtype=np.float64)
        n_tracks = int(track_lengths.size)
        if n_tracks == 0 or int(top_k) <= 0:
            return np.empty(0, dtype=np.uint32)
        if _native_speedups is not None and hasattr(_native_speedups, "ms2_select_top_ids"):
            try:
                return np.asarray(
                    _native_speedups.ms2_select_top_ids(
                        track_lengths.astype(np.uint32, copy=False),
                        starts.astype(np.int64, copy=False),
                        flat_scan_idx.astype(np.int64, copy=False),
                        joined_intensity.astype(np.float64, copy=False),
                        int(top_k),
                        int(min_track_length),
                    ),
                    dtype=np.uint32,
                )
            except RuntimeError as exc:
                raise RuntimeError("Native MS2 top-id selection failed") from exc
        total_intensity = np.add.reduceat(joined_intensity, starts) if int(joined_intensity.size) else np.zeros(n_tracks, dtype=np.float64)
        has_duplicate_scan = np.zeros(n_tracks, dtype=bool)
        if int(flat_scan_idx.size) > 1:
            same_scan_pair = flat_scan_idx[1:] == flat_scan_idx[:-1]
            same_track_pair = np.ones(int(flat_scan_idx.size) - 1, dtype=bool)
            if n_tracks > 1:
                same_track_pair[starts[1:].astype(np.int64, copy=False) - 1] = False
            duplicate_pair_pos = np.flatnonzero(same_scan_pair & same_track_pair)
            if int(duplicate_pair_pos.size):
                duplicate_track_ids = np.searchsorted(starts, duplicate_pair_pos, side="right") - 1
                has_duplicate_scan[duplicate_track_ids] = True
        candidate_mask = (~has_duplicate_scan) & (track_lengths >= int(min_track_length))
        candidate_ids = np.flatnonzero(candidate_mask).astype(np.int64)
        if int(candidate_ids.size) == 0:
            return np.empty(0, dtype=np.uint32)
        order = np.lexsort((
            candidate_ids,
            -total_intensity[candidate_ids],
            -track_lengths[candidate_ids],
        ))
        return candidate_ids[order[: int(top_k)]].astype(np.uint32, copy=False)

    @staticmethod
    def _encode_dense_track_codes(
        *,
        track_ids: np.ndarray,
        track_lengths: np.ndarray,
        starts: np.ndarray,
        flat_scan_idx: np.ndarray,
        joined_intensity: np.ndarray,
        n_scans: int,
        precision: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
        track_ids = np.asarray(track_ids, dtype=np.int64)
        n_top = int(track_ids.size)
        n_scans_i = int(n_scans)
        top_presence = np.zeros((n_top, n_scans_i), dtype=np.uint8)
        if n_top == 0:
            return top_presence, np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int64), 0
        selected_lengths = track_lengths[track_ids].astype(np.int64, copy=False)
        present_points = int(selected_lengths.sum())
        dense_values = np.zeros((n_top, n_scans_i), dtype=np.float64)
        if present_points:
            row_idx = np.repeat(np.arange(n_top, dtype=np.int64), selected_lengths)
            group_offsets = np.repeat(np.cumsum(selected_lengths, dtype=np.int64) - selected_lengths, selected_lengths)
            local_offsets = np.arange(present_points, dtype=np.int64) - group_offsets
            point_pos = np.repeat(starts[track_ids].astype(np.int64, copy=False), selected_lengths) + local_offsets
            scan_idx = flat_scan_idx[point_pos].astype(np.int64, copy=False)
            dense_values[row_idx, scan_idx] = joined_intensity[point_pos]
            top_presence[row_idx, scan_idx] = 1
        codes = _szdpd_encode_to_int32_with_precision(dense_values.reshape(-1), int(precision))[0].reshape(n_top, n_scans_i)
        first_codes = codes[:, 0].astype(np.int32, copy=False) if n_scans_i else np.zeros(n_top, dtype=np.int32)
        if n_scans_i > 1:
            delta_concat64 = (codes[:, 1:].astype(np.int64, copy=False) - codes[:, :-1].astype(np.int64, copy=False)).reshape(-1)
        else:
            delta_concat64 = np.empty(0, dtype=np.int64)
        return top_presence, first_codes, delta_concat64, int(present_points)

    @staticmethod
    def _encode_sparse_tail_arrays(
        *,
        sparse_ids: np.ndarray,
        track_lengths: np.ndarray,
        starts: np.ndarray,
        flat_scan_idx: np.ndarray,
        joined_intensity: np.ndarray,
        n_scans: int,
        precision: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int]:
        sparse_ids = np.asarray(sparse_ids, dtype=np.int64)
        n_points = int(track_lengths[sparse_ids].sum()) if int(sparse_ids.size) else 0
        if n_points == 0:
            return (
                np.zeros(int(n_scans), dtype=np.uint32),
                np.empty(0, dtype=np.uint32),
                np.empty(0, dtype=np.int32),
                0,
            )
        selected_lengths = track_lengths[sparse_ids].astype(np.int64, copy=False)
        group_offsets = np.repeat(np.cumsum(selected_lengths, dtype=np.int64) - selected_lengths, selected_lengths)
        local_offsets = np.arange(n_points, dtype=np.int64) - group_offsets
        point_pos = np.repeat(starts[sparse_ids].astype(np.int64, copy=False), selected_lengths) + local_offsets
        scan_values = flat_scan_idx[point_pos].astype(np.uint32, copy=False)
        dict_values = np.repeat(sparse_ids.astype(np.uint32, copy=False), selected_lengths)
        intensity_values = joined_intensity[point_pos].astype(np.float64, copy=False)
        stable_order = np.arange(n_points, dtype=np.uint32)
        order = np.lexsort((stable_order, dict_values, scan_values))
        scan_sorted = scan_values[order]
        dict_sorted = dict_values[order]
        intensity_sorted = intensity_values[order]
        counts = np.bincount(scan_sorted.astype(np.int64, copy=False), minlength=int(n_scans)).astype(np.uint32, copy=False)
        active = int(scan_sorted.size)
        if active == 0:
            return counts, np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.int32), 0
        group_start = np.empty(active, dtype=bool)
        group_start[0] = True
        if active > 1:
            group_start[1:] = scan_sorted[1:] != scan_sorted[:-1]
        prev_dict = np.empty(active, dtype=np.uint32)
        prev_dict[0] = 0
        if active > 1:
            prev_dict[1:] = dict_sorted[:-1]
        prev_dict[group_start] = 0
        deltas = dict_sorted - prev_dict
        codes = _szdpd_encode_to_int32_with_precision(intensity_sorted.astype(np.float64, copy=False), int(precision))[0].astype(np.int32, copy=False)
        return counts, deltas.astype(np.uint32, copy=False), codes, int(active)

    @staticmethod
    def _encode_track_first_delta_arrays(
        *,
        track_ids: np.ndarray,
        track_lengths: np.ndarray,
        starts: np.ndarray,
        flat_scan_idx: np.ndarray,
        joined_intensity: np.ndarray,
        precision: int,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
        track_ids = np.asarray(track_ids, dtype=np.int64)
        if int(track_ids.size) == 0:
            return (
                np.empty(0, dtype=np.uint32),
                np.empty(0, dtype=np.uint32),
                np.empty(0, dtype=np.int32),
                np.empty(0, dtype=np.int64),
                0,
            )
        lengths = np.asarray(track_lengths[track_ids], dtype=np.int64)
        points = int(lengths.sum())
        starts_selected = np.asarray(starts[track_ids], dtype=np.int64)
        scan_firsts = flat_scan_idx[starts_selected].astype(np.uint32, copy=False)
        if points == 0:
            return (
                lengths.astype(np.uint32, copy=False),
                scan_firsts,
                np.zeros(int(track_ids.size), dtype=np.int32),
                np.empty(0, dtype=np.int64),
                0,
            )
        group_offsets = np.repeat(np.cumsum(lengths, dtype=np.int64) - lengths, lengths)
        local_offsets = np.arange(points, dtype=np.int64) - group_offsets
        point_pos = np.repeat(starts_selected, lengths) + local_offsets
        scan_values = flat_scan_idx[point_pos].astype(np.int64, copy=False)
        codes = _szdpd_encode_to_int32_with_precision(joined_intensity[point_pos].astype(np.float64, copy=False), int(precision))[0].astype(np.int64, copy=False)
        first_positions = np.cumsum(lengths, dtype=np.int64) - lengths
        int_first_codes = codes[first_positions].astype(np.int32, copy=False)
        follow_mask = np.ones(points, dtype=bool)
        follow_mask[first_positions] = False
        scan_deltas = np.diff(scan_values).astype(np.int64, copy=False)
        code_deltas = np.diff(codes).astype(np.int64, copy=False)
        if points > 1:
            keep = follow_mask[1:]
            scan_gaps = scan_deltas[keep]
            int_delta_concat64 = code_deltas[keep]
        else:
            scan_gaps = np.empty(0, dtype=np.int64)
            int_delta_concat64 = np.empty(0, dtype=np.int64)
        return (
            lengths.astype(np.uint32, copy=False),
            scan_firsts,
            int_first_codes,
            scan_gaps,
            int_delta_concat64,
            int(points),
        )

    def _decode_window_components_to_scans(
        self,
        *,
        n_scans: int,
        mz_scale: int,
        precision: int,
        dict_mz_q: np.ndarray,
        top_ids: np.ndarray,
        top_presence: np.ndarray,
        top_codes: np.ndarray,
        top_mz_residuals: np.ndarray | None,
        tail_nnz_counts: np.ndarray,
        tail_index_deltas: np.ndarray,
        tail_codes: np.ndarray,
        tail_mz_residuals: np.ndarray | None = None,
        mid_ids: np.ndarray | None = None,
        mid_track_lengths: np.ndarray | None = None,
        mid_scan_firsts: np.ndarray | None = None,
        mid_scan_gaps: np.ndarray | None = None,
        mid_int_first_codes: np.ndarray | None = None,
        mid_int_delta_main: np.ndarray | None = None,
        scan_step_mode: str = SCAN_STEP_MODE_GAP_MINUS_ONE,
    ) -> List[Dict]:
        dict_mz_q = np.asarray(dict_mz_q, dtype=np.int64)
        top_ids = np.asarray(top_ids, dtype=np.int64)
        top_presence = np.asarray(top_presence, dtype=np.uint8)
        top_codes = np.asarray(top_codes, dtype=np.int64)
        tail_nnz_counts = np.asarray(tail_nnz_counts, dtype=np.int64)
        tail_index_deltas = np.asarray(tail_index_deltas, dtype=np.int64)
        tail_codes = np.asarray(tail_codes, dtype=np.int32)
        top_present_counts = top_presence.sum(axis=0).astype(np.int64, copy=False) if top_presence.size else np.zeros(int(n_scans), dtype=np.int64)
        scan_counts = top_present_counts + tail_nnz_counts.astype(np.int64, copy=False)
        if mid_ids is not None and mid_track_lengths is not None and mid_scan_firsts is not None and mid_scan_gaps is not None:
            mid_ids = np.asarray(mid_ids, dtype=np.int64)
            mid_track_lengths = np.asarray(mid_track_lengths, dtype=np.int64)
            mid_scan_firsts = np.asarray(mid_scan_firsts, dtype=np.int64)
            mid_scan_gaps = np.asarray(mid_scan_gaps, dtype=np.int64)
            gap_pos = 0
            for row_idx in range(int(mid_ids.size)):
                length = int(mid_track_lengths[row_idx]) if row_idx < int(mid_track_lengths.size) else 0
                if length <= 0:
                    continue
                scan_idx = int(mid_scan_firsts[row_idx])
                if scan_idx < 0 or scan_idx >= int(n_scans):
                    raise ValueError(f"MS2 window decode invalid mid first scan: row_idx={row_idx} scan_idx={scan_idx} n_scans={n_scans}")
                scan_counts[scan_idx] += 1
                for i in range(1, length):
                    step = int(mid_scan_gaps[gap_pos])
                    gap_pos += 1
                    if scan_step_mode == SCAN_STEP_MODE_GAP_MINUS_ONE:
                        step += 1
                    scan_idx += step
                    if scan_idx < 0 or scan_idx >= int(n_scans):
                        raise ValueError(
                            f"MS2 window decode invalid mid scan: row_idx={row_idx} "
                            f"track_pos={i} scan_idx={scan_idx} n_scans={n_scans}"
                        )
                    scan_counts[scan_idx] += 1

        mz_arrays = [np.empty(int(count), dtype=np.float64) for count in scan_counts]
        intensity_arrays = [np.empty(int(count), dtype=np.float64) for count in scan_counts]
        write_pos = np.zeros(int(n_scans), dtype=np.int64)
        mz_scale_f = float(mz_scale)
        top_mz_residuals = np.zeros(int(top_presence.sum()), dtype=np.int64) if top_mz_residuals is None else np.asarray(top_mz_residuals, dtype=np.int64)
        top_mz_pos = 0
        for row_idx, dict_id in enumerate(top_ids):
            present = np.flatnonzero(top_presence[row_idx]) if row_idx < int(top_presence.shape[0]) else np.empty(0, dtype=np.int64)
            if int(present.size) == 0:
                continue
            mz_q_base = int(dict_mz_q[int(dict_id)])
            intensities = _szdpd_decode_from_int32(top_codes[row_idx, present].astype(np.int32, copy=False), {"precision": int(precision)})
            for local_idx, scan_idx in enumerate(present):
                target_scan = int(scan_idx)
                pos = int(write_pos[target_scan])
                mz_arrays[target_scan][pos] = float(mz_q_base + int(top_mz_residuals[top_mz_pos])) / mz_scale_f
                intensity_arrays[target_scan][pos] = float(intensities[local_idx])
                write_pos[target_scan] = pos + 1
                top_mz_pos += 1

        if mid_ids is not None and mid_track_lengths is not None and mid_scan_firsts is not None and mid_scan_gaps is not None:
            mid_int_first_codes = np.asarray(mid_int_first_codes if mid_int_first_codes is not None else np.empty(0, dtype=np.int64), dtype=np.int64)
            mid_int_delta_main = np.asarray(mid_int_delta_main if mid_int_delta_main is not None else np.empty(0, dtype=np.int64), dtype=np.int64)
            gap_pos = 0
            delta_pos = 0
            for row_idx, dict_id in enumerate(np.asarray(mid_ids, dtype=np.int64)):
                length = int(mid_track_lengths[row_idx]) if row_idx < int(mid_track_lengths.size) else 0
                if length <= 0:
                    continue
                scan_indices = np.empty(length, dtype=np.int64)
                scan_indices[0] = int(mid_scan_firsts[row_idx])
                if scan_indices[0] < 0 or scan_indices[0] >= int(n_scans):
                    raise ValueError(f"MS2 window decode invalid mid first scan: row_idx={row_idx} scan_idx={int(scan_indices[0])} n_scans={n_scans}")
                for i in range(1, length):
                    step = int(mid_scan_gaps[gap_pos])
                    gap_pos += 1
                    if scan_step_mode == SCAN_STEP_MODE_GAP_MINUS_ONE:
                        step += 1
                    scan_indices[i] = scan_indices[i - 1] + step
                    if scan_indices[i] < 0 or scan_indices[i] >= int(n_scans):
                        raise ValueError(
                            f"MS2 window decode invalid mid scan: row_idx={row_idx} "
                            f"track_pos={i} scan_idx={int(scan_indices[i])} n_scans={n_scans}"
                        )
                codes = np.empty(length, dtype=np.int64)
                codes[0] = int(mid_int_first_codes[row_idx])
                for i in range(1, length):
                    codes[i] = codes[i - 1] + int(mid_int_delta_main[delta_pos])
                    delta_pos += 1
                intensities = _szdpd_decode_from_int32(codes.astype(np.int32, copy=False), {"precision": int(precision)})
                mz_value = float(dict_mz_q[int(dict_id)]) / mz_scale_f
                for local_idx, scan_idx in enumerate(scan_indices):
                    target_scan = int(scan_idx)
                    pos = int(write_pos[target_scan])
                    mz_arrays[target_scan][pos] = mz_value
                    intensity_arrays[target_scan][pos] = float(intensities[local_idx])
                    write_pos[target_scan] = pos + 1

        tail_mz_residuals = np.zeros(int(tail_nnz_counts.sum()), dtype=np.int64) if tail_mz_residuals is None else np.asarray(tail_mz_residuals, dtype=np.int64)
        tail_delta_pos = 0
        tail_code_pos = 0
        tail_mz_pos = 0
        for scan_idx in range(int(n_scans)):
            nnz = int(tail_nnz_counts[scan_idx]) if scan_idx < int(tail_nnz_counts.size) else 0
            if nnz <= 0:
                continue
            deltas = tail_index_deltas[tail_delta_pos:tail_delta_pos + nnz]
            dict_ids = np.cumsum(deltas, dtype=np.int64)
            codes = tail_codes[tail_code_pos:tail_code_pos + nnz]
            intensities = _szdpd_decode_from_int32(codes.astype(np.int32, copy=False), {"precision": int(precision)})
            mz_resid = tail_mz_residuals[tail_mz_pos:tail_mz_pos + nnz]
            for local_idx, dict_id in enumerate(dict_ids):
                pos = int(write_pos[scan_idx])
                mz_arrays[scan_idx][pos] = float(int(dict_mz_q[int(dict_id)]) + int(mz_resid[local_idx])) / mz_scale_f
                intensity_arrays[scan_idx][pos] = float(intensities[local_idx])
                write_pos[scan_idx] = pos + 1
            tail_delta_pos += nnz
            tail_code_pos += nnz
            tail_mz_pos += nnz

        reconstructed = []
        for scan_idx in range(int(n_scans)):
            if scan_counts[scan_idx]:
                mz_array = mz_arrays[scan_idx]
                intensity_array = intensity_arrays[scan_idx]
                if mz_array.size > 1 and np.any(mz_array[1:] < mz_array[:-1]):
                    order = np.argsort(mz_array)
                    mz_array = mz_array[order]
                    intensity_array = intensity_array[order]
            else:
                mz_array = np.array([], dtype=np.float64)
                intensity_array = np.array([], dtype=np.float64)
            reconstructed.append({"scan_idx": scan_idx, "mz_array": mz_array, "intensity_array": intensity_array})
        return reconstructed

    def encode(self, scans: List[Dict], window_target_mz: float = 0.0) -> Tuple[bytes, Dict]:
        t0 = time.perf_counter()
        n_scans = len(scans)
        arrays = self._build_entry_arrays(scans, window_target_mz)
        dict_mz_q = np.asarray(arrays["dict_mz_q"], dtype=np.int64)
        track_lengths = np.asarray(arrays["track_lengths"], dtype=np.int64)
        starts = np.asarray(arrays["starts"], dtype=np.int64)
        flat_scan_idx = np.asarray(arrays["flat_scan_idx"], dtype=np.int64)
        joined = np.asarray(arrays["joined_intensity"], dtype=np.float64)
        raw_track_count = int(arrays["raw_point_count"])
        n_entries = int(track_lengths.size)
        top_ids = self._select_top_ids_from_arrays(
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            top_k=self.top_k,
            min_track_length=self.min_track_length,
        )
        top_mask = np.zeros(n_entries, dtype=bool)
        if int(top_ids.size):
            top_mask[top_ids.astype(np.int64, copy=False)] = True
        tail_ids = np.flatnonzero(~top_mask).astype(np.uint32, copy=False)
        precision = int(max(1, baseline_ms1_codec._szdpd_detect_precision(joined) if len(joined) else 1))

        top_presence, top_first_codes, top_delta_concat64, top_present_points = self._encode_dense_track_codes(
            track_ids=top_ids,
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            n_scans=n_scans,
            precision=precision,
        )
        top_delta_main, top_delta_overflow_idx, top_delta_overflow_vals = _encode_signed_int64_to_int32_main(top_delta_concat64)
        top_mz_residuals = np.array([], dtype=np.int64)

        tail_nnz_counts, tail_index_deltas, tail_codes, tail_points = self._encode_sparse_tail_arrays(
            sparse_ids=tail_ids,
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            n_scans=n_scans,
            precision=precision,
        )
        tail_mz_residuals = np.array([], dtype=np.int64)

        segments = {}
        meta = {}

        payload, payload_meta = self._encode_dict_mz_stream(dict_mz_q)
        segments["dict_mz_q"] = payload
        meta["dict_mz_q"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(top_ids, "mz_ref_indices", prefer_small_uint=True)
        segments["top_ids"] = payload
        meta["top_ids"] = payload_meta

        payload, payload_meta = _encode_small_uint_stream(
            top_presence.reshape(-1),
            self.backend,
            bits=1,
            backend_candidates=self.helper._candidate_backends("mz_kinds"),
        )
        payload_meta["small_uint"] = True
        segments["top_presence"] = payload
        meta["top_presence"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(top_first_codes, "full_intensity")
        segments["top_first_codes"] = payload
        meta["top_first_codes"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(top_delta_main, "delta_intensity")
        segments["top_delta_main"] = payload
        meta["top_delta_main"] = payload_meta

        top_mz_all_zero = True
        if not top_mz_all_zero:
            payload, payload_meta = self._encode_signed_int32_stream(top_mz_residuals.astype(np.int32, copy=False), "mz_residual_values")
            segments["top_mz_residuals"] = payload
            meta["top_mz_residuals"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(
            top_delta_overflow_idx,
            "delta_overflow_idx",
            prefer_small_uint=True,
        )
        segments["top_delta_overflow_idx"] = payload
        meta["top_delta_overflow_idx"] = payload_meta

        overflow_vals_bytes = np.asarray(top_delta_overflow_vals, dtype=np.int64).tobytes()
        best_backend = self.backend
        best_payload = compress(overflow_vals_bytes, self.backend)
        for candidate in self.helper._candidate_backends("delta_overflow_vals"):
            cand = compress(overflow_vals_bytes, candidate)
            if len(cand) < len(best_payload):
                best_payload = cand
                best_backend = candidate
        segments["top_delta_overflow_vals"] = best_payload
        meta["top_delta_overflow_vals"] = {"count": int(len(top_delta_overflow_vals)), "outer_backend": best_backend}

        tail_count_segments, tail_count_meta = self._encode_scan_count_vector(np.asarray(tail_nnz_counts, dtype=np.uint32), "tail_nnz")
        segments.update(tail_count_segments)
        meta.update(tail_count_meta)

        tail_index_segments, tail_index_meta = self._encode_tail_index_vector(
            np.asarray(tail_nnz_counts, dtype=np.uint32),
            np.asarray(tail_index_deltas, dtype=np.uint32),
            "tail_index",
        )
        segments.update(tail_index_segments)
        meta.update(tail_index_meta)

        payload, payload_meta = self._encode_signed_int32_stream(np.asarray(tail_codes, dtype=np.int32), "delta_intensity")
        segments["tail_codes"] = payload
        meta["tail_codes"] = payload_meta

        tail_mz_all_zero = True
        if not tail_mz_all_zero:
            payload, payload_meta = self._encode_signed_int32_stream(tail_mz_residuals.astype(np.int32, copy=False), "mz_residual_values")
            segments["tail_mz_residuals"] = payload
            meta["tail_mz_residuals"] = payload_meta

        header = {
            "format": "dia_ms2_window_dictionary",
            "magic": MAGIC.decode("latin1"),
            "backend": self.backend,
            "mz_precision": self.mz_precision,
            "mz_scale": self.mz_scale,
            "n_scans": n_scans,
            "dictionary_size": int(n_entries),
            "top_count": int(len(top_ids)),
            "precision": precision,
            "top_k": self.top_k,
            "min_track_length": self.min_track_length,
            "window_target_mz": float(window_target_mz),
            "segment_meta": meta,
            "stats": {
                "raw_track_count": int(raw_track_count),
                "dictionary_size": int(n_entries),
                "top_count": int(len(top_ids)),
                "top_point_fraction": float(top_present_points / (top_present_points + tail_points)) if (top_present_points + tail_points) else 0.0,
                "tail_point_fraction": float(tail_points / (top_present_points + tail_points)) if (top_present_points + tail_points) else 0.0,
                "tail_mean_nnz_per_scan": float(np.mean(tail_nnz_counts)) if int(np.asarray(tail_nnz_counts).size) else 0.0,
                "mz_same_fidelity": True,
                "top_mz_residuals_stored": not top_mz_all_zero,
                "tail_mz_residuals_stored": not tail_mz_all_zero,
            },
        }
        payload = _pack_segments(header, segments)
        raw_bytes = int(arrays["raw_bytes"])
        return payload, {
            "raw_bytes": raw_bytes,
            "compressed_bytes": len(payload),
            "compression_ratio": raw_bytes / len(payload) if len(payload) else 0.0,
            "encode_time_s": time.perf_counter() - t0,
            "header": header,
        }

    def decode(self, payload: bytes) -> Tuple[List[Dict], Dict, float]:
        from ..common.compression_backends import decompress

        t0 = time.perf_counter()
        header, segments = _unpack_segments(payload)
        meta = header["segment_meta"]
        n_scans = int(header["n_scans"])
        dict_mz_q = self._decode_dict_mz_stream(segments["dict_mz_q"], meta["dict_mz_q"])
        top_ids = self._decode_compact_uint32_meta(segments["top_ids"], meta["top_ids"]).astype(np.int64)
        top_presence = _decode_small_uint_stream(segments["top_presence"], meta["top_presence"]).reshape(len(top_ids), n_scans)
        top_first_codes = self._decode_signed_int32_stream(segments["top_first_codes"], meta["top_first_codes"]).astype(np.int64)
        top_delta_main = self._decode_signed_int32_stream(segments["top_delta_main"], meta["top_delta_main"]).astype(np.int64)
        if "top_mz_residuals" in segments:
            top_mz_residuals = self._decode_signed_int32_stream(segments["top_mz_residuals"], meta["top_mz_residuals"]).astype(np.int64)
        else:
            top_mz_residuals = np.zeros(int(top_presence.sum()), dtype=np.int64)
        top_delta_overflow_idx = self._decode_compact_uint32_meta(
            segments["top_delta_overflow_idx"],
            meta["top_delta_overflow_idx"],
        ).astype(np.int64)
        overflow_vals_raw = decompress(segments["top_delta_overflow_vals"], meta["top_delta_overflow_vals"]["outer_backend"])
        top_delta_overflow_vals = np.frombuffer(overflow_vals_raw, dtype=np.int64).copy() if meta["top_delta_overflow_vals"]["count"] else np.array([], dtype=np.int64)
        if len(top_delta_overflow_idx):
            top_delta_main[top_delta_overflow_idx] = top_delta_overflow_vals
        delta_matrix = top_delta_main.reshape(len(top_ids), max(0, n_scans - 1)) if len(top_ids) and n_scans > 1 else np.zeros((len(top_ids), 0), dtype=np.int64)
        top_codes = np.zeros((len(top_ids), n_scans), dtype=np.int64)
        if len(top_ids):
            top_codes[:, 0] = top_first_codes
            if n_scans > 1:
                top_codes[:, 1:] = np.cumsum(delta_matrix, axis=1) + top_first_codes[:, None]

        tail_nnz_counts = self._decode_scan_count_vector(segments, meta, "tail_nnz", n_scans)
        tail_index_deltas = self._decode_tail_index_vector(segments, meta, tail_nnz_counts, "tail_index").astype(np.int64)
        tail_codes = self._decode_signed_int32_stream(segments["tail_codes"], meta["tail_codes"]).astype(np.int32)
        if "tail_mz_residuals" in segments:
            tail_mz_residuals = self._decode_signed_int32_stream(segments["tail_mz_residuals"], meta["tail_mz_residuals"]).astype(np.int64)
        else:
            tail_mz_residuals = np.zeros(int(tail_nnz_counts.sum()), dtype=np.int64)

        precision = int(header["precision"])
        reconstructed = self._decode_window_components_to_scans(
            n_scans=n_scans,
            mz_scale=int(header["mz_scale"]),
            precision=precision,
            dict_mz_q=dict_mz_q,
            top_ids=top_ids,
            top_presence=top_presence,
            top_codes=top_codes,
            top_mz_residuals=top_mz_residuals,
            tail_nnz_counts=tail_nnz_counts,
            tail_index_deltas=tail_index_deltas,
            tail_codes=tail_codes,
            tail_mz_residuals=tail_mz_residuals,
        )
        return reconstructed, header, time.perf_counter() - t0


class DIAWindowExactTrackCodec(DIAWindowDictionaryCodec):
    """Exact quantized m/z dictionary + per-entry track coding."""

    def _decode_optional_uint32_sidecar(self, segments: Dict[str, bytes], meta: Dict, name: str) -> np.ndarray:
        payload_meta = meta.get(name)
        if payload_meta is None:
            return np.array([], dtype=np.uint32)
        return self._decode_compact_uint32_meta(segments.get(name, b""), payload_meta)

    def _decode_optional_int64_sidecar(self, segments: Dict[str, bytes], meta: Dict, name: str) -> np.ndarray:
        payload_meta = meta.get(name)
        if payload_meta is None:
            return np.array([], dtype=np.int64)
        return self._decompress_int64_values(segments.get(name, b""), payload_meta)

    def _compress_int64_values(self, values: np.ndarray, stream_name: str) -> Tuple[bytes, Dict]:
        values = np.asarray(values, dtype=np.int64)
        raw = values.tobytes()
        best_backend = self.backend
        best_payload = compress(raw, self.backend)
        for candidate in self.helper._candidate_backends(stream_name):
            cand = compress(raw, candidate)
            if len(cand) < len(best_payload):
                best_payload = cand
                best_backend = candidate
        return best_payload, {"count": int(len(values)), "outer_backend": best_backend}

    def _decompress_int64_values(self, payload: bytes, meta: Dict) -> np.ndarray:
        if int(meta["count"]) == 0:
            return np.array([], dtype=np.int64)
        raw = decompress(payload, meta["outer_backend"])
        return np.frombuffer(raw, dtype=np.int64).copy()

    def build_component_record(self, scans: List[Dict], window_target_mz: float = 0.0) -> Dict:
        arrays = self._build_entry_arrays(scans, window_target_mz)
        dict_mz_q = np.asarray(arrays["dict_mz_q"], dtype=np.int64)
        track_lengths = np.asarray(arrays["track_lengths"], dtype=np.uint32)
        scan_firsts = np.asarray(arrays["scan_firsts"], dtype=np.uint32)
        flat_scan_idx = np.asarray(arrays["flat_scan_idx"], dtype=np.int64)
        joined = np.asarray(arrays["joined_intensity"], dtype=np.float64)
        total_points = int(joined.size)
        n_entries = int(track_lengths.size)
        precision = int(max(1, baseline_ms1_codec._szdpd_detect_precision(joined) if len(joined) else 1))
        int_first_codes = np.zeros(n_entries, dtype=np.int32)
        int_first_overflow_idx = np.array([], dtype=np.uint32)
        int_first_overflow_vals = np.array([], dtype=np.int64)
        scan_gap_values = np.array([], dtype=np.int64)
        int_delta_concat64 = np.array([], dtype=np.int64)
        if len(joined):
            codes_concat64 = _equal_fidelity_exact_scaled_int64(joined, precision)
            starts = np.cumsum(track_lengths.astype(np.int64), dtype=np.int64) - track_lengths.astype(np.int64)
            int_first_codes64 = codes_concat64[starts]
            int_first_codes, int_first_overflow_idx, int_first_overflow_vals = _encode_signed_int64_to_int32_main(int_first_codes64)
            if len(codes_concat64) > n_entries:
                native_ok = False
                if _native_speedups is not None and hasattr(_native_speedups, "ms2_exact_track_build_follow_deltas"):
                    try:
                        scan_gap_values, int_delta_concat64 = _native_speedups.ms2_exact_track_build_follow_deltas(
                            track_lengths.astype(np.uint32, copy=False),
                            flat_scan_idx.astype(np.int64, copy=False),
                            codes_concat64.astype(np.int64, copy=False),
                        )
                        scan_gap_values = np.asarray(scan_gap_values, dtype=np.uint32)
                        int_delta_concat64 = np.asarray(int_delta_concat64, dtype=np.int64)
                        native_ok = True
                    except RuntimeError as exc:
                        raise RuntimeError("Native MS2 follow-delta builder failed") from exc
                if not native_ok:
                    boundary_pos = np.cumsum(track_lengths[:-1].astype(np.int64), dtype=np.int64) - 1
                    scan_deltas = np.diff(flat_scan_idx).astype(np.int64, copy=False)
                    code_deltas = np.diff(codes_concat64).astype(np.int64, copy=False)
                    if len(boundary_pos):
                        keep = np.ones(len(scan_deltas), dtype=bool)
                        keep[boundary_pos] = False
                        scan_gap_values = scan_deltas[keep]
                        int_delta_concat64 = code_deltas[keep]
                    else:
                        scan_gap_values = scan_deltas
                        int_delta_concat64 = code_deltas
                else:
                    scan_gap_values = np.asarray(scan_gap_values, dtype=np.uint32)
        int_delta_main, int_delta_overflow_idx, int_delta_overflow_vals = _encode_signed_int64_to_int32_main(int_delta_concat64)
        return {
            "window_target_mz": float(window_target_mz),
            "n_scans": int(len(scans)),
            "raw_bytes": int(arrays["raw_bytes"]),
            "raw_track_count": int(arrays["raw_point_count"]),
            "dictionary_size": int(n_entries),
            "precision": int(precision),
            "dict_mz_q": dict_mz_q,
            "track_lengths": track_lengths,
            "scan_firsts": scan_firsts,
            "scan_gaps": scan_gap_values.astype(np.uint32, copy=False),
            "int_first_codes": int_first_codes.astype(np.int32, copy=False),
            "int_first_overflow_idx": int_first_overflow_idx.astype(np.uint32, copy=False),
            "int_first_overflow_vals": np.asarray(int_first_overflow_vals, dtype=np.int64),
            "int_delta_main": int_delta_main.astype(np.int32, copy=False),
            "int_delta_overflow_idx": int_delta_overflow_idx.astype(np.uint32, copy=False),
            "int_delta_overflow_vals": np.asarray(int_delta_overflow_vals, dtype=np.int64),
            "intensity_code_mode": INTENSITY_CODE_MODE_EXACT_SCALED_INT64_MAIN_OVERFLOW,
            "scan_step_mode": SCAN_STEP_MODE_DELTA,
            "stats": {
                "raw_track_count": int(arrays["raw_point_count"]),
                "dictionary_size": int(n_entries),
                "mean_track_length": float(np.mean(track_lengths)) if len(track_lengths) else 0.0,
                "max_track_length": int(track_lengths.max()) if len(track_lengths) else 0,
                "top_count": 0,
                "top_point_fraction": 0.0,
                "tail_point_fraction": 1.0 if total_points else 0.0,
                "tail_mean_nnz_per_scan": float(total_points / max(len(scans), 1)),
                "mz_same_fidelity": True,
                "track_first_representation": True,
            },
        }

    def _encode_component_segments(self, record: Dict) -> Tuple[Dict[str, bytes], Dict]:
        segments = {}
        meta = {}

        payload, payload_meta = self._encode_dict_mz_stream(record["dict_mz_q"])
        segments["dict_mz_q"] = payload
        meta["dict_mz_q"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(record["track_lengths"], "track_lengths", prefer_small_uint=True)
        segments["track_lengths"] = payload
        meta["track_lengths"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(record["scan_firsts"], "scan_indices", prefer_small_uint=True)
        segments["scan_firsts"] = payload
        meta["scan_firsts"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(record["scan_gaps"], "delta_ref_offsets", prefer_small_uint=True)
        segments["scan_gaps"] = payload
        meta["scan_gaps"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(record["int_first_codes"], "full_intensity")
        segments["int_first_codes"] = payload
        meta["int_first_codes"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(
            record["int_first_overflow_idx"],
            "full_intensity_overflow_idx",
            prefer_small_uint=True,
        )
        segments["int_first_overflow_idx"] = payload
        meta["int_first_overflow_idx"] = payload_meta

        payload, payload_meta = self._compress_int64_values(record["int_first_overflow_vals"], "full_intensity_overflow_vals")
        segments["int_first_overflow_vals"] = payload
        meta["int_first_overflow_vals"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(record["int_delta_main"], "delta_intensity")
        segments["int_delta_main"] = payload
        meta["int_delta_main"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(
            record["int_delta_overflow_idx"],
            "delta_overflow_idx",
            prefer_small_uint=True,
        )
        segments["int_delta_overflow_idx"] = payload
        meta["int_delta_overflow_idx"] = payload_meta

        payload, payload_meta = self._compress_int64_values(record["int_delta_overflow_vals"], "delta_overflow_vals")
        segments["int_delta_overflow_vals"] = payload
        meta["int_delta_overflow_vals"] = payload_meta
        return segments, meta

    def _make_public_header(self, record: Dict, meta: Dict) -> Dict:
        header = {
            "format": "dia_ms2_exact_track",
            "magic": MAGIC.decode("latin1"),
            "backend": self.backend,
            "mz_precision": self.mz_precision,
            "mz_scale": self.mz_scale,
            "n_scans": int(record["n_scans"]),
            "dictionary_size": int(record["dictionary_size"]),
            "precision": int(record["precision"]),
            "window_target_mz": float(record["window_target_mz"]),
            "intensity_code_mode": record.get("intensity_code_mode", INTENSITY_CODE_MODE_SZDPD_INT32),
            "scan_step_mode": record.get("scan_step_mode", SCAN_STEP_MODE_DELTA),
            "segment_meta": meta,
            "stats": dict(record["stats"]),
        }
        return header

    def _make_payload_header(self, record: Dict, meta: Dict) -> Dict:
        return {
            "ns": int(record["n_scans"]),
            "ms": int(self.mz_scale),
            "pr": int(record["precision"]),
            "icm": record.get("intensity_code_mode", INTENSITY_CODE_MODE_SZDPD_INT32),
            "sgm": record.get("scan_step_mode", SCAN_STEP_MODE_DELTA),
            "sm": meta,
        }

    def iter_reconstruct_scan_arrays_from_arrays(
        self,
        *,
        n_scans: int,
        mz_scale: int,
        precision: int,
        dict_mz_q: np.ndarray,
        track_lengths: np.ndarray,
        scan_firsts: np.ndarray,
        scan_gaps: np.ndarray,
        int_first_codes: np.ndarray,
        int_delta_main: np.ndarray,
        int_delta_overflow_idx: np.ndarray,
        int_delta_overflow_vals: np.ndarray,
        int_first_overflow_idx: np.ndarray | None = None,
        int_first_overflow_vals: np.ndarray | None = None,
        intensity_code_mode: str = INTENSITY_CODE_MODE_SZDPD_INT32,
        scan_step_mode: str = SCAN_STEP_MODE_GAP_MINUS_ONE,
    ):
        dict_mz_q = np.asarray(dict_mz_q, dtype=np.int64)
        track_lengths = np.asarray(track_lengths, dtype=np.int64)
        scan_firsts = np.asarray(scan_firsts, dtype=np.int64)
        scan_gaps = np.asarray(scan_gaps, dtype=np.int64)
        int_first_codes = np.asarray(int_first_codes, dtype=np.int64)
        int_delta_main = np.asarray(int_delta_main, dtype=np.int64).copy()
        int_delta_overflow_idx = np.asarray(int_delta_overflow_idx, dtype=np.int64)
        int_delta_overflow_vals = np.asarray(int_delta_overflow_vals, dtype=np.int64)
        if len(int_delta_overflow_idx):
            int_delta_main[int_delta_overflow_idx] = int_delta_overflow_vals
        if intensity_code_mode == INTENSITY_CODE_MODE_EXACT_SCALED_INT64_MAIN_OVERFLOW:
            int_first_codes64 = _decode_signed_int64_from_int32_main(
                int_first_codes.astype(np.int32, copy=False),
                np.asarray(int_first_overflow_idx if int_first_overflow_idx is not None else np.array([], dtype=np.uint32), dtype=np.uint32),
                np.asarray(int_first_overflow_vals if int_first_overflow_vals is not None else np.array([], dtype=np.int64), dtype=np.int64),
            )
        else:
            int_first_codes64 = int_first_codes.astype(np.int64, copy=False)

        native_expanded_f64 = None
        if _native_speedups is not None and hasattr(_native_speedups, "ms2_exact_track_expand_by_scan_f64"):
            try:
                native_expanded_f64 = _native_speedups.ms2_exact_track_expand_by_scan_f64(
                    int(n_scans),
                    dict_mz_q.astype(np.int64, copy=False),
                    track_lengths.astype(np.uint32, copy=False),
                    scan_firsts.astype(np.uint32, copy=False),
                    scan_gaps.astype(np.uint32, copy=False),
                    int_first_codes64.astype(np.int64, copy=False),
                    int_delta_main.astype(np.int64, copy=False),
                    bool(scan_step_mode == SCAN_STEP_MODE_GAP_MINUS_ONE),
                    float(mz_scale),
                    float(precision),
                    bool(intensity_code_mode == INTENSITY_CODE_MODE_EXACT_SCALED_INT64_MAIN_OVERFLOW),
                )
            except RuntimeError as exc:
                raise RuntimeError("Native MS2 exact-track f64 expansion failed") from exc

        if native_expanded_f64 is not None:
            offsets = np.asarray(native_expanded_f64[0], dtype=np.uint64)
            flat_mz = np.asarray(native_expanded_f64[1], dtype=np.float64)
            flat_intensity = np.asarray(native_expanded_f64[2], dtype=np.float64)
            for scan_idx in range(n_scans):
                start = int(offsets[scan_idx])
                end = int(offsets[scan_idx + 1])
                if start < end:
                    mz_array = flat_mz[start:end]
                    intensity_array = flat_intensity[start:end]
                else:
                    mz_array = np.array([], dtype=np.float64)
                    intensity_array = np.array([], dtype=np.float64)
                yield mz_array, intensity_array
            return

        native_expanded = None
        if _native_speedups is not None and hasattr(_native_speedups, "ms2_exact_track_expand_by_scan"):
            try:
                native_expanded = _native_speedups.ms2_exact_track_expand_by_scan(
                    int(n_scans),
                    dict_mz_q.astype(np.int64, copy=False),
                    track_lengths.astype(np.uint32, copy=False),
                    scan_firsts.astype(np.uint32, copy=False),
                    scan_gaps.astype(np.uint32, copy=False),
                    int_first_codes64.astype(np.int64, copy=False),
                    int_delta_main.astype(np.int64, copy=False),
                    bool(scan_step_mode == SCAN_STEP_MODE_GAP_MINUS_ONE),
                )
            except RuntimeError as exc:
                raise RuntimeError("Native MS2 exact-track integer expansion failed") from exc

        if native_expanded is not None:
            offsets = np.asarray(native_expanded[0], dtype=np.uint64)
            flat_mz_q = np.asarray(native_expanded[1], dtype=np.int64)
            flat_codes = np.asarray(native_expanded[2], dtype=np.int64)
            for scan_idx in range(n_scans):
                start = int(offsets[scan_idx])
                end = int(offsets[scan_idx + 1])
                if start < end:
                    mz_array = flat_mz_q[start:end].astype(np.float64) / float(mz_scale)
                    if intensity_code_mode == INTENSITY_CODE_MODE_EXACT_SCALED_INT64_MAIN_OVERFLOW:
                        intensity_array = flat_codes[start:end].astype(np.float64) / float(precision)
                    else:
                        intensity_array = _szdpd_decode_from_int32(flat_codes[start:end].astype(np.int32, copy=False), {"precision": precision})
                    if mz_array.size > 1 and np.any(mz_array[1:] < mz_array[:-1]):
                        order = np.argsort(mz_array)
                        mz_array = mz_array[order]
                        intensity_array = intensity_array[order]
                else:
                    mz_array = np.array([], dtype=np.float64)
                    intensity_array = np.array([], dtype=np.float64)
                yield mz_array, intensity_array
            return

        scan_counts = np.zeros(n_scans, dtype=np.int64)
        gap_pos = 0
        for dict_id, length in enumerate(track_lengths):
            length = int(length)
            if length <= 0:
                continue
            scan_idx = int(scan_firsts[dict_id])
            if scan_idx < 0 or scan_idx >= n_scans:
                raise ValueError(
                    f"MS2 exact-track decode invalid first scan index: dict_id={dict_id} "
                    f"scan_idx={scan_idx} n_scans={n_scans}"
                )
            scan_counts[scan_idx] += 1
            for i in range(1, length):
                step = int(scan_gaps[gap_pos])
                gap_pos += 1
                if scan_step_mode == SCAN_STEP_MODE_GAP_MINUS_ONE:
                    step += 1
                scan_idx += step
                if scan_idx < 0 or scan_idx >= n_scans:
                    raise ValueError(
                        f"MS2 exact-track decode invalid scan index: dict_id={dict_id} "
                        f"track_pos={i} scan_idx={scan_idx} n_scans={n_scans} "
                        f"raw_step={int(scan_gaps[gap_pos - 1])} scan_step_mode={scan_step_mode}"
                    )
                scan_counts[scan_idx] += 1

        mz_arrays = [np.empty(int(count), dtype=np.float64) for count in scan_counts]
        intensity_arrays = [np.empty(int(count), dtype=np.float64) for count in scan_counts]
        write_pos = np.zeros(n_scans, dtype=np.int64)
        gap_pos = 0
        delta_pos = 0
        for dict_id, length in enumerate(track_lengths):
            length = int(length)
            if length <= 0:
                continue
            scan_indices = np.empty(length, dtype=np.int64)
            scan_indices[0] = int(scan_firsts[dict_id])
            if scan_indices[0] < 0 or scan_indices[0] >= n_scans:
                raise ValueError(
                    f"MS2 exact-track decode invalid first scan index: dict_id={dict_id} "
                    f"scan_idx={int(scan_indices[0])} n_scans={n_scans}"
                )
            for i in range(1, length):
                step = int(scan_gaps[gap_pos])
                gap_pos += 1
                if scan_step_mode == SCAN_STEP_MODE_GAP_MINUS_ONE:
                    step += 1
                scan_indices[i] = scan_indices[i - 1] + step
                if scan_indices[i] < 0 or scan_indices[i] >= n_scans:
                    raise ValueError(
                        f"MS2 exact-track decode invalid scan index: dict_id={dict_id} "
                        f"track_pos={i} scan_idx={int(scan_indices[i])} n_scans={n_scans} "
                        f"raw_step={int(scan_gaps[gap_pos - 1])} scan_step_mode={scan_step_mode}"
                    )

            codes = np.empty(length, dtype=np.int64)
            codes[0] = int(int_first_codes64[dict_id])
            for i in range(1, length):
                codes[i] = codes[i - 1] + int(int_delta_main[delta_pos])
                delta_pos += 1
            if intensity_code_mode == INTENSITY_CODE_MODE_EXACT_SCALED_INT64_MAIN_OVERFLOW:
                intensities = codes.astype(np.float64) / float(precision)
            else:
                intensities = _szdpd_decode_from_int32(codes.astype(np.int32), {"precision": precision})
            mz_value = float(dict_mz_q[dict_id]) / float(mz_scale)
            for scan_idx, intensity in zip(scan_indices, intensities):
                target_scan = int(scan_idx)
                pos = int(write_pos[target_scan])
                mz_arrays[target_scan][pos] = mz_value
                intensity_arrays[target_scan][pos] = float(intensity)
                write_pos[target_scan] = pos + 1

        for scan_idx in range(n_scans):
            if scan_counts[scan_idx]:
                mz_array = mz_arrays[scan_idx]
                intensity_array = intensity_arrays[scan_idx]
                if mz_array.size > 1 and np.any(mz_array[1:] < mz_array[:-1]):
                    order = np.argsort(mz_array)
                    mz_array = mz_array[order]
                    intensity_array = intensity_array[order]
            else:
                mz_array = np.array([], dtype=np.float64)
                intensity_array = np.array([], dtype=np.float64)
            yield mz_array, intensity_array

    def _reconstruct_scans_from_arrays(self, **kwargs) -> List[Dict]:
        return [
            {
                "scan_idx": scan_idx,
                "mz_array": mz_array,
                "intensity_array": intensity_array,
            }
            for scan_idx, (mz_array, intensity_array) in enumerate(
                self.iter_reconstruct_scan_arrays_from_arrays(**kwargs)
            )
        ]

    def encode_from_record(self, record: Dict) -> Tuple[bytes, Dict]:
        t0 = time.perf_counter()
        segments, meta = self._encode_component_segments(record)
        payload = _pack_exact_track_segments(self._make_payload_header(record, meta), segments, self.backend)
        return payload, {
            "raw_bytes": int(record["raw_bytes"]),
            "compressed_bytes": len(payload),
            "compression_ratio": float(record["raw_bytes"]) / len(payload) if len(payload) else 0.0,
            "encode_time_s": time.perf_counter() - t0,
            "header": self._make_public_header(record, meta),
        }

    def encode(self, scans: List[Dict], window_target_mz: float = 0.0) -> Tuple[bytes, Dict]:
        t0 = time.perf_counter()
        record = self.build_component_record(scans, window_target_mz)
        payload, meta = self.encode_from_record(record)
        meta = dict(meta)
        meta["encode_time_s"] = time.perf_counter() - t0
        return payload, meta

    def decode(self, payload: bytes) -> Tuple[List[Dict], Dict, float]:
        t0 = time.perf_counter()
        header, segments = _unpack_exact_track_segments(payload)
        meta = header["sm"]
        n_scans = int(header["ns"])
        precision = int(header["pr"])
        reconstructed = self._reconstruct_scans_from_arrays(
            n_scans=n_scans,
            mz_scale=int(header["ms"]),
            precision=precision,
            intensity_code_mode=header.get("icm", INTENSITY_CODE_MODE_SZDPD_INT32),
            scan_step_mode=header.get("sgm", SCAN_STEP_MODE_GAP_MINUS_ONE),
            dict_mz_q=self._decode_dict_mz_stream(segments["dict_mz_q"], meta["dict_mz_q"]).astype(np.int64),
            track_lengths=self._decode_compact_uint32_meta(segments["track_lengths"], meta["track_lengths"]),
            scan_firsts=self._decode_compact_uint32_meta(segments["scan_firsts"], meta["scan_firsts"]),
            scan_gaps=self._decode_compact_uint32_meta(segments["scan_gaps"], meta["scan_gaps"]),
            int_first_codes=self._decode_signed_int32_stream(segments["int_first_codes"], meta["int_first_codes"]),
            int_delta_main=self._decode_signed_int32_stream(segments["int_delta_main"], meta["int_delta_main"]),
            int_delta_overflow_idx=self._decode_compact_uint32_meta(segments["int_delta_overflow_idx"], meta["int_delta_overflow_idx"]),
            int_delta_overflow_vals=self._decompress_int64_values(segments["int_delta_overflow_vals"], meta["int_delta_overflow_vals"]),
            int_first_overflow_idx=self._decode_optional_uint32_sidecar(segments, meta, "int_first_overflow_idx"),
            int_first_overflow_vals=self._decode_optional_int64_sidecar(segments, meta, "int_first_overflow_vals"),
        )
        public_header = {
            "format": "dia_ms2_exact_track",
            "n_scans": n_scans,
            "mz_scale": int(header["ms"]),
            "precision": precision,
            "intensity_code_mode": header.get("icm", INTENSITY_CODE_MODE_SZDPD_INT32),
            "scan_step_mode": header.get("sgm", SCAN_STEP_MODE_GAP_MINUS_ONE),
            "segment_meta": meta,
        }
        return reconstructed, public_header, time.perf_counter() - t0


class DIAWindowHybridCodec(DIAWindowDictionaryCodec):
    """Exact dictionary + Top-K dense + mid-frequency track + singleton sparse."""

    def __init__(
        self,
        *,
        tail_track_min_length: int = 2,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.tail_track_min_length = int(tail_track_min_length)

    def encode(self, scans: List[Dict], window_target_mz: float = 0.0) -> Tuple[bytes, Dict]:
        t0 = time.perf_counter()
        n_scans = len(scans)
        arrays = self._build_entry_arrays(scans, window_target_mz)
        dict_mz_q = np.asarray(arrays["dict_mz_q"], dtype=np.int64)
        track_lengths = np.asarray(arrays["track_lengths"], dtype=np.int64)
        starts = np.asarray(arrays["starts"], dtype=np.int64)
        flat_scan_idx = np.asarray(arrays["flat_scan_idx"], dtype=np.int64)
        joined = np.asarray(arrays["joined_intensity"], dtype=np.float64)
        raw_track_count = int(arrays["raw_point_count"])
        n_entries = int(track_lengths.size)
        top_ids = self._select_top_ids_from_arrays(
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            top_k=self.top_k,
            min_track_length=self.min_track_length,
        )
        top_mask = np.zeros(n_entries, dtype=bool)
        if int(top_ids.size):
            top_mask[top_ids.astype(np.int64, copy=False)] = True
        non_top_ids = np.flatnonzero(~top_mask).astype(np.uint32, copy=False)
        mid_ids = non_top_ids[track_lengths[non_top_ids.astype(np.int64, copy=False)] >= int(self.tail_track_min_length)].astype(np.uint32, copy=False)
        sparse_ids = non_top_ids[track_lengths[non_top_ids.astype(np.int64, copy=False)] < int(self.tail_track_min_length)].astype(np.uint32, copy=False)
        precision = int(max(1, baseline_ms1_codec._szdpd_detect_precision(joined) if len(joined) else 1))

        top_presence, top_first_codes, top_delta_concat64, top_present_points = self._encode_dense_track_codes(
            track_ids=top_ids,
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            n_scans=n_scans,
            precision=precision,
        )
        top_delta_main, top_delta_overflow_idx, top_delta_overflow_vals = _encode_signed_int64_to_int32_main(top_delta_concat64)

        (
            mid_track_lengths,
            mid_scan_firsts,
            mid_int_first_codes,
            mid_scan_gap_values,
            mid_int_delta_concat64,
            mid_points,
        ) = self._encode_track_first_delta_arrays(
            track_ids=mid_ids,
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            precision=precision,
        )
        mid_int_delta_main, mid_int_delta_overflow_idx, mid_int_delta_overflow_vals = _encode_signed_int64_to_int32_main(mid_int_delta_concat64)

        tail_nnz_counts, tail_index_deltas, tail_codes, tail_points = self._encode_sparse_tail_arrays(
            sparse_ids=sparse_ids,
            track_lengths=track_lengths,
            starts=starts,
            flat_scan_idx=flat_scan_idx,
            joined_intensity=joined,
            n_scans=n_scans,
            precision=precision,
        )

        segments = {}
        meta = {}

        payload, payload_meta = self._encode_dict_mz_stream(dict_mz_q)
        segments["dict_mz_q"] = payload
        meta["dict_mz_q"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(top_ids, "mz_ref_indices", prefer_small_uint=True)
        segments["top_ids"] = payload
        meta["top_ids"] = payload_meta

        payload, payload_meta = _encode_small_uint_stream(
            top_presence.reshape(-1),
            self.backend,
            bits=1,
            backend_candidates=self.helper._candidate_backends("mz_kinds"),
        )
        payload_meta["small_uint"] = True
        segments["top_presence"] = payload
        meta["top_presence"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(top_first_codes, "full_intensity")
        segments["top_first_codes"] = payload
        meta["top_first_codes"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(top_delta_main, "delta_intensity")
        segments["top_delta_main"] = payload
        meta["top_delta_main"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(
            top_delta_overflow_idx,
            "delta_overflow_idx",
            prefer_small_uint=True,
        )
        segments["top_delta_overflow_idx"] = payload
        meta["top_delta_overflow_idx"] = payload_meta

        overflow_vals_bytes = np.asarray(top_delta_overflow_vals, dtype=np.int64).tobytes()
        best_backend = self.backend
        best_payload = compress(overflow_vals_bytes, self.backend)
        for candidate in self.helper._candidate_backends("delta_overflow_vals"):
            cand = compress(overflow_vals_bytes, candidate)
            if len(cand) < len(best_payload):
                best_payload = cand
                best_backend = candidate
        segments["top_delta_overflow_vals"] = best_payload
        meta["top_delta_overflow_vals"] = {"count": int(len(top_delta_overflow_vals)), "outer_backend": best_backend}

        payload, payload_meta = self._encode_compact_uint32_meta(mid_ids, "mz_ref_indices", prefer_small_uint=True)
        segments["mid_ids"] = payload
        meta["mid_ids"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(mid_track_lengths, "track_lengths", prefer_small_uint=True)
        segments["mid_track_lengths"] = payload
        meta["mid_track_lengths"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(mid_scan_firsts, "scan_indices", prefer_small_uint=True)
        segments["mid_scan_firsts"] = payload
        meta["mid_scan_firsts"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(
            mid_scan_gap_values.astype(np.uint32, copy=False),
            "delta_ref_offsets",
            prefer_small_uint=True,
        )
        segments["mid_scan_gaps"] = payload
        meta["mid_scan_gaps"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(mid_int_first_codes, "full_intensity")
        segments["mid_int_first_codes"] = payload
        meta["mid_int_first_codes"] = payload_meta

        payload, payload_meta = self._encode_signed_int32_stream(mid_int_delta_main, "delta_intensity")
        segments["mid_int_delta_main"] = payload
        meta["mid_int_delta_main"] = payload_meta

        payload, payload_meta = self._encode_compact_uint32_meta(
            mid_int_delta_overflow_idx,
            "delta_overflow_idx",
            prefer_small_uint=True,
        )
        segments["mid_int_delta_overflow_idx"] = payload
        meta["mid_int_delta_overflow_idx"] = payload_meta

        overflow_vals_bytes = np.asarray(mid_int_delta_overflow_vals, dtype=np.int64).tobytes()
        best_backend = self.backend
        best_payload = compress(overflow_vals_bytes, self.backend)
        for candidate in self.helper._candidate_backends("delta_overflow_vals"):
            cand = compress(overflow_vals_bytes, candidate)
            if len(cand) < len(best_payload):
                best_payload = cand
                best_backend = candidate
        segments["mid_int_delta_overflow_vals"] = best_payload
        meta["mid_int_delta_overflow_vals"] = {"count": int(len(mid_int_delta_overflow_vals)), "outer_backend": best_backend}

        tail_count_segments, tail_count_meta = self._encode_scan_count_vector(np.asarray(tail_nnz_counts, dtype=np.uint32), "tail_nnz")
        segments.update(tail_count_segments)
        meta.update(tail_count_meta)

        tail_index_segments, tail_index_meta = self._encode_tail_index_vector(
            np.asarray(tail_nnz_counts, dtype=np.uint32),
            np.asarray(tail_index_deltas, dtype=np.uint32),
            "tail_index",
        )
        segments.update(tail_index_segments)
        meta.update(tail_index_meta)

        payload, payload_meta = self._encode_signed_int32_stream(np.asarray(tail_codes, dtype=np.int32), "delta_intensity")
        segments["tail_codes"] = payload
        meta["tail_codes"] = payload_meta

        total_points = top_present_points + mid_points + tail_points
        header = {
            "format": "dia_ms2_window_hybrid",
            "magic": MAGIC.decode("latin1"),
            "backend": self.backend,
            "mz_precision": self.mz_precision,
            "mz_scale": self.mz_scale,
            "n_scans": n_scans,
            "dictionary_size": int(n_entries),
            "top_count": int(len(top_ids)),
            "mid_count": int(len(mid_ids)),
            "sparse_count": int(len(sparse_ids)),
            "precision": precision,
            "top_k": self.top_k,
            "min_track_length": self.min_track_length,
            "tail_track_min_length": self.tail_track_min_length,
            "window_target_mz": float(window_target_mz),
            "scan_step_mode": SCAN_STEP_MODE_DELTA,
            "segment_meta": meta,
            "stats": {
                "raw_track_count": int(raw_track_count),
                "dictionary_size": int(n_entries),
                "top_count": int(len(top_ids)),
                "mid_count": int(len(mid_ids)),
                "sparse_count": int(len(sparse_ids)),
                "top_point_fraction": float(top_present_points / total_points) if total_points else 0.0,
                "mid_point_fraction": float(mid_points / total_points) if total_points else 0.0,
                "tail_point_fraction": float(tail_points / total_points) if total_points else 0.0,
                "tail_mean_nnz_per_scan": float(np.mean(tail_nnz_counts)) if int(np.asarray(tail_nnz_counts).size) else 0.0,
                "mz_same_fidelity": True,
                "hybrid_track_sparse": True,
            },
        }
        payload = _pack_segments(header, segments)
        raw_bytes = int(arrays["raw_bytes"])
        return payload, {
            "raw_bytes": raw_bytes,
            "compressed_bytes": len(payload),
            "compression_ratio": raw_bytes / len(payload) if len(payload) else 0.0,
            "encode_time_s": time.perf_counter() - t0,
            "header": header,
        }

    def decode(self, payload: bytes) -> Tuple[List[Dict], Dict, float]:
        t0 = time.perf_counter()
        header, segments = _unpack_segments(payload)
        meta = header["segment_meta"]
        n_scans = int(header["n_scans"])
        dict_mz_q = self._decode_dict_mz_stream(segments["dict_mz_q"], meta["dict_mz_q"]).astype(np.int64)

        precision = int(header["precision"])

        top_ids = self._decode_compact_uint32_meta(segments["top_ids"], meta["top_ids"]).astype(np.int64)
        top_presence = _decode_small_uint_stream(segments["top_presence"], meta["top_presence"]).reshape(len(top_ids), n_scans)
        top_first_codes = self._decode_signed_int32_stream(segments["top_first_codes"], meta["top_first_codes"]).astype(np.int64)
        top_delta_main = self._decode_signed_int32_stream(segments["top_delta_main"], meta["top_delta_main"]).astype(np.int64)
        top_delta_overflow_idx = self._decode_compact_uint32_meta(
            segments["top_delta_overflow_idx"],
            meta["top_delta_overflow_idx"],
        ).astype(np.int64)
        overflow_vals_raw = decompress(segments["top_delta_overflow_vals"], meta["top_delta_overflow_vals"]["outer_backend"])
        top_delta_overflow_vals = np.frombuffer(overflow_vals_raw, dtype=np.int64).copy() if meta["top_delta_overflow_vals"]["count"] else np.array([], dtype=np.int64)
        if len(top_delta_overflow_idx):
            top_delta_main[top_delta_overflow_idx] = top_delta_overflow_vals
        delta_matrix = top_delta_main.reshape(len(top_ids), max(0, n_scans - 1)) if len(top_ids) and n_scans > 1 else np.zeros((len(top_ids), 0), dtype=np.int64)
        top_codes = np.zeros((len(top_ids), n_scans), dtype=np.int64)
        if len(top_ids):
            top_codes[:, 0] = top_first_codes
            if n_scans > 1:
                top_codes[:, 1:] = np.cumsum(delta_matrix, axis=1) + top_first_codes[:, None]

        mid_ids = self._decode_compact_uint32_meta(segments["mid_ids"], meta["mid_ids"]).astype(np.int64)
        mid_track_lengths = self._decode_compact_uint32_meta(segments["mid_track_lengths"], meta["mid_track_lengths"]).astype(np.int64)
        mid_scan_firsts = self._decode_compact_uint32_meta(segments["mid_scan_firsts"], meta["mid_scan_firsts"]).astype(np.int64)
        mid_scan_gaps = self._decode_compact_uint32_meta(segments["mid_scan_gaps"], meta["mid_scan_gaps"]).astype(np.int64)
        mid_int_first_codes = self._decode_signed_int32_stream(segments["mid_int_first_codes"], meta["mid_int_first_codes"]).astype(np.int64)
        mid_int_delta_main = self._decode_signed_int32_stream(segments["mid_int_delta_main"], meta["mid_int_delta_main"]).astype(np.int64)
        mid_int_delta_overflow_idx = self._decode_compact_uint32_meta(
            segments["mid_int_delta_overflow_idx"],
            meta["mid_int_delta_overflow_idx"],
        ).astype(np.int64)
        overflow_vals_raw = decompress(segments["mid_int_delta_overflow_vals"], meta["mid_int_delta_overflow_vals"]["outer_backend"])
        mid_int_delta_overflow_vals = np.frombuffer(overflow_vals_raw, dtype=np.int64).copy() if meta["mid_int_delta_overflow_vals"]["count"] else np.array([], dtype=np.int64)
        if len(mid_int_delta_overflow_idx):
            mid_int_delta_main[mid_int_delta_overflow_idx] = mid_int_delta_overflow_vals

        tail_nnz_counts = self._decode_scan_count_vector(segments, meta, "tail_nnz", n_scans)
        tail_index_deltas = self._decode_tail_index_vector(segments, meta, tail_nnz_counts, "tail_index").astype(np.int64)
        tail_codes = self._decode_signed_int32_stream(segments["tail_codes"], meta["tail_codes"]).astype(np.int32)
        reconstructed = self._decode_window_components_to_scans(
            n_scans=n_scans,
            mz_scale=int(header["mz_scale"]),
            precision=precision,
            dict_mz_q=dict_mz_q,
            top_ids=top_ids,
            top_presence=top_presence,
            top_codes=top_codes,
            top_mz_residuals=None,
            tail_nnz_counts=tail_nnz_counts,
            tail_index_deltas=tail_index_deltas,
            tail_codes=tail_codes,
            tail_mz_residuals=None,
            mid_ids=mid_ids,
            mid_track_lengths=mid_track_lengths,
            mid_scan_firsts=mid_scan_firsts,
            mid_scan_gaps=mid_scan_gaps,
            mid_int_first_codes=mid_int_first_codes,
            mid_int_delta_main=mid_int_delta_main,
            scan_step_mode=header.get("scan_step_mode", SCAN_STEP_MODE_GAP_MINUS_ONE),
        )
        return reconstructed, header, time.perf_counter() - t0
