"""Aird/ZDPD and Stack-ZDPD local baselines for TrackCodec experiments.

This module implements a closer reproduction of the public AirdPro/Aird-SDK logic
than an earlier local comparison script:
- intensity is quantized to int32 codes before compression
- Stack-ZDPD m/z path enables FastPFor
- stack tags use bit-packing with ceil(log2(k)) bits
- stacked m/z sorting uses pairwise stable merge
"""

from __future__ import annotations

import gzip
import math
import random
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Sequence, Tuple

import numpy as np

from ...production.common.compression_backends import compress
from ...production.ms1.cross_scan_codec import _decode_uint32_stream, _encode_uint32_stream_best, _szdpd_decode_from_int32


INT32_MAX = np.iinfo(np.int32).max


def float64_payload(scans: Sequence[dict]) -> Tuple[bytes, int]:
    parts = []
    raw_bytes = 0
    for scan in scans:
        mz = np.asarray(scan["mz_array"], dtype=np.float64)
        intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
        parts.append(mz.tobytes())
        parts.append(intensity.tobytes())
        raw_bytes += len(mz) * 8 + len(intensity) * 8
    return b"".join(parts), raw_bytes


def gzip_raw_payload(scans: Sequence[dict], compresslevel: int = 6) -> Dict:
    payload, raw_bytes = float64_payload(scans)
    comp = gzip.compress(payload, compresslevel=compresslevel)
    return {
        "raw_bytes": raw_bytes,
        "compressed_bytes": len(comp),
        "compression_ratio": raw_bytes / len(comp) if len(comp) else 0.0,
    }


def raw_backend_payload(scans: Sequence[dict], backend: str) -> Dict:
    payload, raw_bytes = float64_payload(scans)
    comp = compress(payload, backend)
    return {
        "raw_bytes": raw_bytes,
        "compressed_bytes": len(comp),
        "compression_ratio": raw_bytes / len(comp) if len(comp) else 0.0,
    }


def airdpro_detect_intensity_precision(scans: Sequence[dict], sample_count: int = 5, seed: int = 0) -> int:
    if not scans:
        return 1
    rng = random.Random(seed)
    sample_indices = rng.sample(range(len(scans)), min(sample_count, len(scans)))
    for idx in sample_indices:
        arr = np.asarray(scans[idx]["intensity_array"], dtype=np.float64)
        if np.any((arr - arr.astype(np.int64).astype(np.float64)) != 0):
            return 10
    return 1


def airdpro_fetch_intensity(intensity_array: np.ndarray, precision: int) -> np.ndarray:
    arr = np.asarray(intensity_array, dtype=np.float64)
    scaled = arr * float(precision)
    result = np.zeros(len(arr), dtype=np.int32)
    normal_mask = scaled <= INT32_MAX
    overflow_mask = ~normal_mask
    if np.any(normal_mask):
        result[normal_mask] = np.round(scaled[normal_mask]).astype(np.int32)
    if np.any(overflow_mask):
        result[overflow_mask] = -np.round(np.log2(scaled[overflow_mask]) * 100000.0).astype(np.int32)
    return result


def quantize_mz_array(mz_array: np.ndarray, mz_precision: int = 6) -> np.ndarray:
    scale = 10 ** int(mz_precision)
    return np.round(np.asarray(mz_array, dtype=np.float64) * scale).astype(np.int64)


def reconstruct_quantized_scan(scan: dict, precision: int, mz_precision: int = 6) -> Tuple[np.ndarray, np.ndarray]:
    mz_q = quantize_mz_array(scan["mz_array"], mz_precision=mz_precision)
    int_codes = airdpro_fetch_intensity(scan["intensity_array"], precision)
    int_meta = {"precision": precision}
    mz_back = mz_q.astype(np.float64) / (10 ** int(mz_precision))
    int_back = _szdpd_decode_from_int32(int_codes, int_meta)
    return mz_back, int_back


def roundtrip_error_stats(scans: Sequence[dict], mz_precision: int = 6, sample_count: int = 5, seed: int = 0) -> Dict:
    precision = airdpro_detect_intensity_precision(scans, sample_count=sample_count, seed=seed)
    max_abs_mz = 0.0
    max_abs_int = 0.0
    ppm_p95 = []
    int_rel_p95 = []
    for scan in scans:
        mz_back, int_back = reconstruct_quantized_scan(scan, precision=precision, mz_precision=mz_precision)
        mz = np.asarray(scan["mz_array"], dtype=np.float64)
        intensity = np.asarray(scan["intensity_array"], dtype=np.float64)
        mz_diff = np.abs(mz - mz_back)
        int_diff = np.abs(intensity - int_back)
        max_abs_mz = max(max_abs_mz, float(mz_diff.max(initial=0.0)))
        max_abs_int = max(max_abs_int, float(int_diff.max(initial=0.0)))
        if len(mz):
            ppm_p95.append(float(np.percentile(mz_diff / np.maximum(mz, 1e-12) * 1e6, 95)))
        if len(intensity):
            int_rel_p95.append(float(np.percentile(int_diff / np.maximum(np.abs(intensity), 1.0), 95)))
    return {
        "precision": precision,
        "max_abs_mz_error": max_abs_mz,
        "max_abs_intensity_error": max_abs_int,
        "median_mz_ppm_p95": float(np.median(ppm_p95)) if ppm_p95 else 0.0,
        "median_int_rel_p95": float(np.median(int_rel_p95)) if int_rel_p95 else 0.0,
    }


def _stable_merge_pair(left_vals: np.ndarray, left_tags: np.ndarray, right_vals: np.ndarray, right_tags: np.ndarray):
    out_vals = np.empty(len(left_vals) + len(right_vals), dtype=np.int64)
    out_tags = np.empty(len(left_tags) + len(right_tags), dtype=np.uint16)
    i = j = k = 0
    while i < len(left_vals) and j < len(right_vals):
        if left_vals[i] <= right_vals[j]:
            out_vals[k] = left_vals[i]
            out_tags[k] = left_tags[i]
            i += 1
        else:
            out_vals[k] = right_vals[j]
            out_tags[k] = right_tags[j]
            j += 1
        k += 1
    if i < len(left_vals):
        remain = len(left_vals) - i
        out_vals[k:k + remain] = left_vals[i:]
        out_tags[k:k + remain] = left_tags[i:]
        k += remain
    if j < len(right_vals):
        remain = len(right_vals) - j
        out_vals[k:k + remain] = right_vals[j:]
        out_tags[k:k + remain] = right_tags[j:]
    return out_vals, out_tags


def pairwise_merge_sorted_arrays(arrays: Sequence[np.ndarray], tags: Sequence[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    work = [(np.asarray(vals, dtype=np.int64), np.asarray(tag_arr, dtype=np.uint16)) for vals, tag_arr in zip(arrays, tags)]
    if not work:
        return np.array([], dtype=np.uint32), np.array([], dtype=np.uint16)
    while len(work) > 1:
        merged = []
        for i in range(0, len(work), 2):
            if i + 1 >= len(work):
                merged.append(work[i])
                continue
            merged.append(_stable_merge_pair(work[i][0], work[i][1], work[i + 1][0], work[i + 1][1]))
        work = merged
    return work[0]


def stack_tag_bits(stack_size: int) -> int:
    return max(1, int(math.ceil(math.log2(max(2, stack_size)))))


def _pack_bits_uint(values: np.ndarray, bits: int) -> bytes:
    if bits <= 0:
        raise ValueError("bits must be positive")
    values = np.asarray(values, dtype=np.uint64)
    if len(values) == 0:
        return b""
    out = bytearray((len(values) * bits + 7) // 8)
    bit_pos = 0
    mask = (1 << bits) - 1
    for value in values:
        v = int(value) & mask
        for j in range(bits):
            if (v >> j) & 1:
                byte_idx = (bit_pos + j) // 8
                bit_idx = (bit_pos + j) % 8
                out[byte_idx] |= 1 << bit_idx
        bit_pos += bits
    return bytes(out)


def _unpack_bits_uint(blob: bytes, count: int, bits: int) -> np.ndarray:
    if count <= 0:
        return np.array([], dtype=np.uint8)
    buf = np.frombuffer(blob, dtype=np.uint8)
    out = np.zeros(count, dtype=np.uint16)
    bit_pos = 0
    for i in range(count):
        value = 0
        for j in range(bits):
            byte_idx = (bit_pos + j) // 8
            bit_idx = (bit_pos + j) % 8
            if (int(buf[byte_idx]) >> bit_idx) & 1:
                value |= 1 << j
        out[i] = value
        bit_pos += bits
    return out.astype(np.uint8 if bits <= 8 else np.uint16)


def encode_tag_stream(tags: np.ndarray, n_groups: int) -> Tuple[bytes, Dict]:
    bits = stack_tag_bits(n_groups)
    packed = _pack_bits_uint(np.asarray(tags, dtype=np.uint16), bits)
    payload = zlib.compress(packed)
    return payload, {"count": int(len(tags)), "bits": bits, "outer_backend": "zlib"}


def decode_tag_stream(payload: bytes, meta: Dict) -> np.ndarray:
    raw = zlib.decompress(payload)
    return _unpack_bits_uint(raw, int(meta["count"]), int(meta["bits"]))


def _compress_uint32_delta(delta: np.ndarray) -> Tuple[bytes, Dict]:
    return _encode_uint32_stream_best(
        np.asarray(delta, dtype=np.uint32),
        "zlib",
        use_pfor=True,
        pfor_codec="simdfastpfor256",
        zero_rle=False,
        backend_candidates=["zlib"],
        try_pfor=True,
        try_zero_rle=False,
        allow_bitpack=False,
        search_mode="full",
        sample_count=16384,
    )


def _encode_quantized_mz_stream(mz_q: np.ndarray) -> Tuple[bytes, Dict]:
    mz_q = np.asarray(mz_q, dtype=np.int64)
    if len(mz_q) == 0:
        return b"", {"value_count": 0, "mz_stream_kind": "first_q_i64_delta_u32", "first_q": 0}

    if len(mz_q) == 1:
        return b"", {"value_count": 1, "mz_stream_kind": "first_q_i64_delta_u32", "first_q": int(mz_q[0])}

    deltas = np.diff(mz_q)
    if np.all(deltas >= 0) and np.all(deltas <= np.iinfo(np.uint32).max):
        payload, meta = _compress_uint32_delta(deltas.astype(np.uint32, copy=False))
        meta = {**meta, "value_count": int(len(mz_q)), "mz_stream_kind": "first_q_i64_delta_u32", "first_q": int(mz_q[0])}
        return payload, meta

    raw = mz_q.astype(np.int64, copy=False).tobytes()
    payload = zlib.compress(raw)
    return payload, {
        "value_count": int(len(mz_q)),
        "mz_stream_kind": "full_i64_zlib",
        "outer_backend": "zlib",
    }


def _decode_quantized_mz_stream(payload: bytes, meta: Dict) -> np.ndarray:
    kind = meta.get("mz_stream_kind", "first_q_i64_delta_u32")
    count = int(meta.get("value_count", 0))
    if count == 0:
        return np.array([], dtype=np.int64)
    if kind == "first_q_i64_delta_u32":
        out = np.empty(count, dtype=np.int64)
        out[0] = int(meta["first_q"])
        if count > 1:
            deltas = _decode_uint32_stream(payload, meta).astype(np.int64)
            out[1:] = np.cumsum(deltas, dtype=np.int64) + out[0]
        return out
    if kind == "full_i64_zlib":
        raw = zlib.decompress(payload)
        return np.frombuffer(raw, dtype=np.int64).copy()
    raise ValueError(f"Unsupported mz stream kind: {kind}")


def _encode_zdpd_scan(scan: dict, precision: int, mz_precision: int):
    mz_arr = np.asarray(scan["mz_array"], dtype=np.float64)
    int_arr = np.asarray(scan["intensity_array"], dtype=np.float64)
    mz_q = quantize_mz_array(mz_arr, mz_precision=mz_precision)
    intensity_codes = airdpro_fetch_intensity(int_arr, precision)
    raw_bytes = len(mz_arr) * 8 + len(int_arr) * 8
    mz_payload, mz_meta = _encode_quantized_mz_stream(mz_q)
    intensity_payload = compress(np.asarray(intensity_codes, dtype=np.int32).tobytes(), "zlib")
    compressed_bytes = len(mz_payload) + len(intensity_payload)
    return {
        "mz_payload": mz_payload,
        "mz_meta": mz_meta,
        "int_payload": intensity_payload,
        "int_count": int(len(intensity_codes)),
        "mz_count": int(len(mz_q)),
        "rt": float(scan.get("rt", 0.0)),
        "scan_idx": int(scan.get("scan_idx", -1)),
    }, raw_bytes, compressed_bytes


def _encode_stack_block(
    block: Sequence[dict],
    block_start: int,
    precision: int,
    mz_precision: int,
) -> Tuple[Dict, int, int]:
    raw_bytes = sum(len(scan["mz_array"]) * 8 + len(scan["intensity_array"]) * 8 for scan in block)
    mz_arrays = []
    tags = []
    intensity_parts = []
    for local_idx, scan in enumerate(block):
        mz_q = quantize_mz_array(scan["mz_array"], mz_precision=mz_precision)
        if len(mz_q):
            mz_arrays.append(mz_q)
            tags.append(np.full(len(mz_q), local_idx, dtype=np.uint16))
        intensity_codes = airdpro_fetch_intensity(scan["intensity_array"], precision)
        intensity_parts.append(np.asarray(intensity_codes, dtype=np.int32).tobytes())

    if mz_arrays:
        stacked_mz, stacked_tags = pairwise_merge_sorted_arrays(mz_arrays, tags)
        mz_payload, mz_meta = _encode_quantized_mz_stream(stacked_mz)
        tag_payload, tag_meta = encode_tag_stream(stacked_tags.astype(np.uint8), len(block))
    else:
        mz_payload = b""
        tag_payload = b""
        mz_meta = {"value_count": 0, "mz_stream_kind": "first_q_i64_delta_u32", "first_q": 0}
        tag_meta = {"count": 0, "bits": stack_tag_bits(len(block)), "outer_backend": "zlib"}

    intensity_payload = compress(b"".join(intensity_parts), "zlib")
    compressed_bytes = len(mz_payload) + len(tag_payload) + len(intensity_payload)
    block_entry = {
        "block_start": int(block_start),
        "mz_payload": mz_payload,
        "mz_meta": mz_meta,
        "tag_payload": tag_payload,
        "tag_meta": tag_meta,
        "intensity_payload": intensity_payload,
        "scan_lengths": [int(len(scan["mz_array"])) for scan in block],
        "scan_indices": [int(scan.get("scan_idx", block_start + i)) for i, scan in enumerate(block)],
        "rts": [float(scan.get("rt", 0.0)) for scan in block],
    }
    return block_entry, raw_bytes, compressed_bytes


def zdpd_baseline_encode(
    scans: Sequence[dict],
    *,
    mz_precision: int = 6,
    sample_count: int = 5,
    seed: int = 0,
    num_threads: int = 0,
) -> Tuple[Dict, Dict]:
    precision = airdpro_detect_intensity_precision(scans, sample_count=sample_count, seed=seed)
    t0 = time.perf_counter()
    worker = lambda scan: _encode_zdpd_scan(scan, precision, mz_precision)
    if num_threads and num_threads > 1:
        with ThreadPoolExecutor(max_workers=int(num_threads)) as ex:
            results = list(ex.map(worker, scans))
    else:
        results = [worker(scan) for scan in scans]

    scan_entries = [item[0] for item in results]
    raw_bytes_total = sum(item[1] for item in results)
    compressed_bytes_total = sum(item[2] for item in results)
    encode_time_s = time.perf_counter() - t0
    payload = {
        "precision": precision,
        "mz_precision": mz_precision,
        "scans": scan_entries,
    }
    meta = {
        "raw_bytes": raw_bytes_total,
        "compressed_bytes": compressed_bytes_total,
        "compression_ratio": raw_bytes_total / compressed_bytes_total if compressed_bytes_total else 0.0,
        "precision": precision,
        "encode_time_s": encode_time_s,
    }
    return payload, meta


def zdpd_baseline_compress(
    scans: Sequence[dict],
    *,
    mz_precision: int = 6,
    sample_count: int = 5,
    seed: int = 0,
    num_threads: int = 0,
    compare_errors: bool = True,
) -> Dict:
    payload, meta = zdpd_baseline_encode(
        scans,
        mz_precision=mz_precision,
        sample_count=sample_count,
        seed=seed,
        num_threads=num_threads,
    )
    decoded_scans, decode_time_s = zdpd_baseline_decode(payload)
    errors = compare_roundtrip_scans(scans, decoded_scans) if compare_errors else {
        "max_abs_mz_error": None,
        "max_abs_intensity_error": None,
        "median_mz_ppm_p95": None,
        "median_int_rel_p95": None,
    }
    return {
        **meta,
        **errors,
        "decode_time_s": decode_time_s,
        "decode_time_scope": "synthetic_scan_numeric_arrays_decode_only_no_mzml_reconstruction",
    }


def stack_zdpd_baseline_encode(
    scans: Sequence[dict],
    *,
    mz_precision: int = 6,
    stack_size: int = 256,
    sample_count: int = 5,
    seed: int = 0,
    num_threads: int = 0,
) -> Tuple[Dict, Dict]:
    precision = airdpro_detect_intensity_precision(scans, sample_count=sample_count, seed=seed)
    t0 = time.perf_counter()
    block_specs = [
        (scans[block_start:block_start + stack_size], block_start)
        for block_start in range(0, len(scans), stack_size)
        if scans[block_start:block_start + stack_size]
    ]
    worker = lambda item: _encode_stack_block(item[0], item[1], precision, mz_precision)
    if num_threads and num_threads > 1:
        with ThreadPoolExecutor(max_workers=int(num_threads)) as ex:
            results = list(ex.map(worker, block_specs))
    else:
        results = [worker(item) for item in block_specs]

    blocks = [item[0] for item in results]
    raw_bytes_total = sum(item[1] for item in results)
    comp_bytes_total = sum(item[2] for item in results)

    encode_time_s = time.perf_counter() - t0
    payload = {
        "precision": precision,
        "mz_precision": mz_precision,
        "stack_size": stack_size,
        "blocks": blocks,
    }
    meta = {
        "raw_bytes": raw_bytes_total,
        "compressed_bytes": comp_bytes_total,
        "compression_ratio": raw_bytes_total / comp_bytes_total if comp_bytes_total else 0.0,
        "precision": precision,
        "encode_time_s": encode_time_s,
    }
    return payload, meta


def _split_intensity_codes(raw: bytes, lengths: Sequence[int]) -> List[np.ndarray]:
    all_codes = np.frombuffer(raw, dtype=np.int32)
    out = []
    start = 0
    for length in lengths:
        end = start + int(length)
        out.append(all_codes[start:end].copy())
        start = end
    return out


def zdpd_baseline_decode(payload: Dict) -> Tuple[List[dict], float]:
    t0 = time.perf_counter()
    scans = []
    precision = int(payload["precision"])
    scale = 10 ** int(payload["mz_precision"])
    for item in payload["scans"]:
        mz_q = _decode_quantized_mz_stream(item["mz_payload"], item["mz_meta"])
        int_raw = zlib.decompress(item["int_payload"])
        int_codes = np.frombuffer(int_raw, dtype=np.int32).copy()
        intensity = _szdpd_decode_from_int32(int_codes, {"precision": precision})
        scans.append(
            {
                "scan_idx": item["scan_idx"],
                "rt": item["rt"],
                "mz_array": mz_q.astype(np.float64) / scale,
                "intensity_array": intensity,
            }
        )
    return scans, time.perf_counter() - t0


def stack_zdpd_baseline_decode(payload: Dict) -> Tuple[List[dict], float]:
    t0 = time.perf_counter()
    precision = int(payload["precision"])
    scale = 10 ** int(payload["mz_precision"])
    scans = []
    for block in payload["blocks"]:
        scan_lengths = [int(x) for x in block["scan_lengths"]]
        mz_lists = [[] for _ in scan_lengths]
        if block["mz_meta"].get("value_count", 0):
            stacked_mz = _decode_quantized_mz_stream(block["mz_payload"], block["mz_meta"])
            tags = decode_tag_stream(block["tag_payload"], block["tag_meta"]).astype(np.int64)
            for mz_q, tag in zip(stacked_mz, tags):
                mz_lists[int(tag)].append(int(mz_q))
        intensity_codes_per_scan = _split_intensity_codes(zlib.decompress(block["intensity_payload"]), scan_lengths)
        for local_idx, (scan_idx, rt, mz_vals, int_codes) in enumerate(zip(block["scan_indices"], block["rts"], mz_lists, intensity_codes_per_scan)):
            scans.append(
                {
                    "scan_idx": int(scan_idx),
                    "rt": float(rt),
                    "mz_array": np.asarray(mz_vals, dtype=np.int64).astype(np.float64) / scale,
                    "intensity_array": _szdpd_decode_from_int32(int_codes, {"precision": precision}),
                }
            )
    return scans, time.perf_counter() - t0


def compare_roundtrip_scans(original_scans: Sequence[dict], decoded_scans: Sequence[dict]) -> Dict:
    max_abs_mz = 0.0
    max_abs_int = 0.0
    ppm_p95 = []
    int_rel_p95 = []
    for orig, dec in zip(original_scans, decoded_scans):
        mz = np.asarray(orig["mz_array"], dtype=np.float64)
        intensity = np.asarray(orig["intensity_array"], dtype=np.float64)
        dec_mz = np.asarray(dec["mz_array"], dtype=np.float64)
        dec_int = np.asarray(dec["intensity_array"], dtype=np.float64)
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


def stack_zdpd_baseline_compress(
    scans: Sequence[dict],
    *,
    mz_precision: int = 6,
    stack_size: int = 256,
    sample_count: int = 5,
    seed: int = 0,
    num_threads: int = 0,
    compare_errors: bool = True,
) -> Dict:
    payload, meta = stack_zdpd_baseline_encode(
        scans,
        mz_precision=mz_precision,
        stack_size=stack_size,
        sample_count=sample_count,
        seed=seed,
        num_threads=num_threads,
    )
    decoded_scans, decode_time_s = stack_zdpd_baseline_decode(payload)
    errors = compare_roundtrip_scans(scans, decoded_scans) if compare_errors else {
        "max_abs_mz_error": None,
        "max_abs_intensity_error": None,
        "median_mz_ppm_p95": None,
        "median_int_rel_p95": None,
    }
    return {
        **meta,
        **errors,
        "decode_time_s": decode_time_s,
        "decode_time_scope": "synthetic_stacked_numeric_arrays_decode_only_no_mzml_reconstruction",
    }
