"""
Unified MS1 Compression Codec
==============================
Supports configurable m/z precision (4/5/6 decimals) and multiple intensity modes:
  - strict_lossless: float64 byteplane, bit-perfect roundtrip
  - near_lossless:   our byteplane approach with controlled quantization
  - szdpd:           AirdPro 6.0 style (precision=1 or 10, log2 for overflow)
  - adaptive:        precision varies by track length (long=20bit, med=16bit, short=12bit)

Full encode → decode roundtrip supported for every mode.
"""

import numpy as np
import struct
import json
import time
from ..common.compression_backends import compress, decompress, available_backends
from ..common.bitshuffle import byteplane_split, byteplane_merge
from ..common.tracking import CompactIslandTracks

# ═══════════════════════════════════════════════════════════════════
#  Configuration
# ═══════════════════════════════════════════════════════════════════

MZ_PRECISION_MAP = {
    4: 10_000,        # 4 decimals → ×10000
    5: 100_000,       # 5 decimals → ×100000
    6: 1_000_000,     # 6 decimals → ×1000000 (default)
}

INTENSITY_MODES = ['strict_lossless', 'near_lossless_20bit', 'near_lossless_16bit',
                   'near_lossless_12bit', 'szdpd', 'adaptive']


# ═══════════════════════════════════════════════════════════════════
#  M/Z Encoding (shared across all modes)
# ═══════════════════════════════════════════════════════════════════

def encode_mz(mz_array, precision_decimals=6):
    """
    Integer quantization + delta encoding for m/z.

    Args:
        mz_array: 1-D float64 m/z values (sorted)
        precision_decimals: 4, 5, or 6

    Returns:
        encoded_bytes, metadata dict
    """
    scale = MZ_PRECISION_MAP[precision_decimals]
    mz_int = np.round(mz_array * scale).astype(np.int64)

    # Delta encode: first value stored directly, rest as diffs
    mz_delta = np.empty(len(mz_int), dtype=np.int64)
    mz_delta[0] = mz_int[0]
    if len(mz_int) > 1:
        mz_delta[1:] = np.diff(mz_int)

    # Use int32 if values fit, else int64
    if mz_delta.min() >= np.iinfo(np.int32).min and mz_delta.max() <= np.iinfo(np.int32).max:
        data = mz_delta.astype(np.int32).tobytes()
        dtype_tag = 'int32'
    else:
        data = mz_delta.tobytes()
        dtype_tag = 'int64'

    return data, {
        'mz_scale': scale,
        'mz_dtype': dtype_tag,
        'mz_count': len(mz_array),
    }


def decode_mz(data, metadata):
    """Inverse of encode_mz."""
    dtype = np.int32 if metadata['mz_dtype'] == 'int32' else np.int64
    mz_delta = np.frombuffer(data, dtype=dtype)
    mz_int = np.cumsum(mz_delta.astype(np.int64))
    return mz_int.astype(np.float64) / metadata['mz_scale']


# ═══════════════════════════════════════════════════════════════════
#  Intensity Encoding — Mode: strict_lossless
# ═══════════════════════════════════════════════════════════════════

def encode_intensity_strict_lossless(intensity_array):
    """Preserve float64 exactly via byteplane split."""
    planes = byteplane_split(intensity_array.astype(np.float64))
    return planes, {'int_mode': 'strict_lossless', 'int_count': len(intensity_array)}


def decode_intensity_strict_lossless(planes, metadata):
    raw = byteplane_merge(planes, dtype=np.float64)
    return np.frombuffer(raw, dtype=np.float64).copy()


# ═══════════════════════════════════════════════════════════════════
#  Intensity Encoding — Mode: near_lossless (Nbit quantization)
# ═══════════════════════════════════════════════════════════════════

def encode_intensity_near_lossless(intensity_array, bits=20):
    """
    Quantize intensity to N bits (12/16/20).
    Store max_val for rescaling.
    """
    arr = intensity_array.astype(np.float64)
    max_val = float(arr.max())
    if max_val <= 0:
        max_val = 1.0

    max_code = (1 << bits) - 1
    quantized = np.round(arr / max_val * max_code).astype(np.int32)
    quantized = np.clip(quantized, 0, max_code)

    # Pack efficiently
    if bits <= 16:
        data = quantized.astype(np.uint16).tobytes()
        pack_dtype = 'uint16'
    else:
        data = quantized.astype(np.int32).tobytes()
        pack_dtype = 'int32'

    return data, {
        'int_mode': f'near_lossless_{bits}bit',
        'int_count': len(arr),
        'int_max_val': max_val,
        'int_bits': bits,
        'int_pack_dtype': pack_dtype,
    }


def decode_intensity_near_lossless(data, metadata):
    dtype = np.uint16 if metadata['int_pack_dtype'] == 'uint16' else np.int32
    quantized = np.frombuffer(data, dtype=dtype).astype(np.float64)
    bits = metadata['int_bits']
    max_code = (1 << bits) - 1
    return quantized / max_code * metadata['int_max_val']


# ═══════════════════════════════════════════════════════════════════
#  Intensity Encoding — Mode: SZDPD (AirdPro 6.0 style)
# ═══════════════════════════════════════════════════════════════════

def _szdpd_detect_precision(intensity_array, sample_size=1000):
    """
    Detect whether intensities have fractional parts.
    If any sampled value has fractional component, use precision=10 (1 decimal).
    Otherwise precision=1 (integer).
    Mirrors AirdPro's PredictForIntensityPrecision().
    """
    n = min(sample_size, len(intensity_array))
    idx = np.linspace(0, len(intensity_array) - 1, n, dtype=int)
    sample = intensity_array[idx]
    has_fraction = np.any(np.abs(sample - np.round(sample)) > 1e-9)
    return 10 if has_fraction else 1


def encode_intensity_szdpd(intensity_array):
    """
    AirdPro 6.0 SZDPD-style encoding:
      - Detect precision (1 or 10)
      - For normal values: round(intensity * precision) → int32
      - For overflow values (> int32.max): encode as -round(log2(val) * 100000)
    """
    INT32_MAX = np.iinfo(np.int32).max
    arr = intensity_array.astype(np.float64)
    precision = _szdpd_detect_precision(arr)

    scaled = arr * precision
    result = np.zeros(len(arr), dtype=np.int32)

    normal_mask = scaled <= INT32_MAX
    overflow_mask = ~normal_mask

    # Normal path: round to int32
    result[normal_mask] = np.round(scaled[normal_mask]).astype(np.int32)

    # Overflow path: -round(log2(scaled) * 100000)
    if np.any(overflow_mask):
        log_vals = np.log2(scaled[overflow_mask]) * 100000
        result[overflow_mask] = -np.round(log_vals).astype(np.int32)

    return result.tobytes(), {
        'int_mode': 'szdpd',
        'int_count': len(arr),
        'int_precision': precision,
        'n_overflow': int(np.sum(overflow_mask)),
    }


def decode_intensity_szdpd(data, metadata):
    """
    Inverse of SZDPD encoding.
    Positive values: value / precision
    Negative values: 2^(-value/100000) / precision
    """
    encoded = np.frombuffer(data, dtype=np.int32).copy().astype(np.float64)
    precision = metadata['int_precision']

    negative_mask = encoded < 0
    positive_mask = ~negative_mask

    result = np.zeros(len(encoded), dtype=np.float64)
    result[positive_mask] = encoded[positive_mask] / precision

    if np.any(negative_mask):
        result[negative_mask] = np.power(2.0, -encoded[negative_mask] / 100000.0) / precision

    return result


# ═══════════════════════════════════════════════════════════════════
#  Intensity Encoding — Mode: adaptive (by track length)
# ═══════════════════════════════════════════════════════════════════

def encode_intensity_adaptive(intensity_array, track_lengths):
    """
    Adaptive precision by track length:
      - Long track (>30 scans):   20-bit quantization
      - Medium track (10-30):     16-bit quantization
      - Short/isolated (<10):     12-bit quantization

    track_lengths: list of (n_values, track_len) tuples indicating
                   how many values belong to each track and the track length
    """
    result_data = bytearray()
    segment_info = []  # (n_values, bits, max_val) per segment
    offset = 0

    for n_values, track_len in track_lengths:
        segment = intensity_array[offset:offset + n_values]
        offset += n_values

        if track_len > 30:
            bits = 20
        elif track_len >= 10:
            bits = 16
        else:
            bits = 12

        max_val = float(segment.max()) if len(segment) > 0 else 1.0
        if max_val <= 0:
            max_val = 1.0

        max_code = (1 << bits) - 1
        quantized = np.round(segment / max_val * max_code).astype(np.int32)
        quantized = np.clip(quantized, 0, max_code)

        if bits <= 16:
            result_data.extend(quantized.astype(np.uint16).tobytes())
        else:
            result_data.extend(quantized.astype(np.int32).tobytes())

        segment_info.append((int(n_values), bits, max_val))

    return bytes(result_data), {
        'int_mode': 'adaptive',
        'int_count': len(intensity_array),
        'segments': segment_info,  # list of (n_values, bits, max_val)
    }


def decode_intensity_adaptive(data, metadata):
    """Inverse of adaptive encoding."""
    segments = metadata['segments']
    result = []
    offset = 0

    for n_values, bits, max_val in segments:
        max_code = (1 << bits) - 1

        if bits <= 16:
            byte_len = n_values * 2
            quantized = np.frombuffer(data[offset:offset + byte_len], dtype=np.uint16).astype(np.float64)
        else:
            byte_len = n_values * 4
            quantized = np.frombuffer(data[offset:offset + byte_len], dtype=np.int32).astype(np.float64)

        offset += byte_len
        result.append(quantized / max_code * max_val)

    return np.concatenate(result) if result else np.array([], dtype=np.float64)


# ═══════════════════════════════════════════════════════════════════
#  Unified Codec Class
# ═══════════════════════════════════════════════════════════════════

class MS1Codec:
    """
    Unified MS1 compression codec.

    Usage:
        codec = MS1Codec(mz_precision=6, intensity_mode='szdpd', backend='zstd-9')
        compressed, meta = codec.encode(tracks, ms1_scans)
        decoded_scans = codec.decode(compressed, meta)
    """

    def __init__(self, mz_precision=6, intensity_mode='szdpd', backend='zstd-9'):
        assert mz_precision in (4, 5, 6), f"mz_precision must be 4, 5, or 6, got {mz_precision}"
        assert intensity_mode in INTENSITY_MODES, f"Unknown mode: {intensity_mode}"
        self.mz_precision = mz_precision
        self.intensity_mode = intensity_mode
        self.backend = backend

    def encode(self, tracks, ms1_scans=None, progress_callback=None):
        """
        Encode tracks to compressed bytes.

        Returns:
            compressed_bytes, metadata_dict
        """
        t0 = time.perf_counter()

        # Collect all m/z and intensity from tracks, maintaining structure
        all_mz = []
        all_int = []
        island_info = []  # (track_id, scan_idx, mz_start, mz_step, n_points, array_start_idx)
        track_length_map = []  # (n_values_in_track, track_length) for adaptive mode

        if isinstance(tracks, CompactIslandTracks):
            for track_id, (track_start, track_end) in enumerate(tracks.iter_track_ranges()):
                track_n_values = 0
                for island_idx in range(track_start, track_end):
                    island = tracks.islands[island_idx]
                    all_mz.append(island.mz_array)
                    all_int.append(island.intensity_array)
                    island_info.append([
                        track_id, island.scan_idx,
                        island.mz_start, island.mz_step,
                        island.n_points, island.array_start_idx
                    ])
                    track_n_values += len(island.intensity_array)
                track_length_map.append((track_n_values, int(track_end - track_start)))
        else:
            for track_id, track in enumerate(tracks):
                track_n_values = 0
                for island in track.islands:
                    all_mz.append(island.mz_array)
                    all_int.append(island.intensity_array)
                    island_info.append([
                        track_id, island.scan_idx,
                        island.mz_start, island.mz_step,
                        island.n_points, island.array_start_idx
                    ])
                    track_n_values += len(island.intensity_array)
                track_length_map.append((track_n_values, len(track.islands)))

        if not all_mz:
            return b'', {'status': 'empty'}

        mz_concat = np.concatenate(all_mz)
        int_concat = np.concatenate(all_int)

        # ── Encode m/z ──
        mz_data, mz_meta = encode_mz(mz_concat, self.mz_precision)
        mz_compressed = compress(mz_data, self.backend)

        # ── Encode intensity ──
        mode = self.intensity_mode
        if mode == 'strict_lossless':
            int_planes, int_meta = encode_intensity_strict_lossless(int_concat)
            int_compressed = b''.join([compress(p, self.backend) for p in int_planes])
            int_meta['plane_sizes'] = [len(compress(p, self.backend)) for p in int_planes]
            # Recompute to avoid double compression
            int_compressed_planes = [compress(p, self.backend) for p in int_planes]
            int_meta['plane_sizes'] = [len(c) for c in int_compressed_planes]
            int_compressed = b''.join(int_compressed_planes)
        elif mode.startswith('near_lossless'):
            bits = int(mode.split('_')[-1].replace('bit', ''))
            int_data, int_meta = encode_intensity_near_lossless(int_concat, bits=bits)
            int_compressed = compress(int_data, self.backend)
        elif mode == 'szdpd':
            int_data, int_meta = encode_intensity_szdpd(int_concat)
            int_compressed = compress(int_data, self.backend)
        elif mode == 'adaptive':
            int_data, int_meta = encode_intensity_adaptive(int_concat, track_length_map)
            int_compressed = compress(int_data, self.backend)
        else:
            raise ValueError(f"Unknown mode: {mode}")

        # ── Encode island metadata ──
        meta_arr = np.array(island_info, dtype=np.float64).flatten()
        meta_compressed = compress(meta_arr.tobytes(), self.backend)

        # ── Pack everything ──
        # Format: [header_len][header_json][mz_len][mz_data][meta_len][meta_data][int_data]
        header = {
            **mz_meta,
            **int_meta,
            'n_islands': len(island_info),
            'n_tracks': len(tracks),
            'backend': self.backend,
            'mz_precision_decimals': self.mz_precision,
        }
        header_json = json.dumps(header, default=str).encode('utf-8')

        parts = [
            struct.pack('<I', len(header_json)),
            header_json,
            struct.pack('<I', len(mz_compressed)),
            mz_compressed,
            struct.pack('<I', len(meta_compressed)),
            meta_compressed,
            int_compressed,
        ]
        output = b''.join(parts)

        encode_time = time.perf_counter() - t0

        # Calculate raw size
        raw_bytes = len(mz_concat) * 8 + len(int_concat) * 8
        if ms1_scans:
            raw_bytes = sum(
                len(s['mz_array']) * 8 + len(s['intensity_array']) * 8
                for s in ms1_scans
            )

        metadata = {
            'raw_bytes': raw_bytes,
            'compressed_bytes': len(output),
            'compression_ratio': raw_bytes / len(output) if len(output) > 0 else 0,
            'encode_time_s': encode_time,
            'mz_compressed_bytes': len(mz_compressed),
            'int_compressed_bytes': len(int_compressed),
            'meta_compressed_bytes': len(meta_compressed),
            'header': header,
        }

        return output, metadata

    def decode(self, compressed_bytes, metadata=None):
        """
        Decode compressed bytes back to arrays.

        Returns:
            list of dicts: [{'mz_array': ..., 'intensity_array': ..., 'scan_idx': ..., 'track_id': ..., ...}, ...]
        """
        t0 = time.perf_counter()

        offset = 0

        # Read header
        header_len = struct.unpack('<I', compressed_bytes[offset:offset + 4])[0]
        offset += 4
        header = json.loads(compressed_bytes[offset:offset + header_len].decode('utf-8'))
        offset += header_len

        # Read m/z
        mz_len = struct.unpack('<I', compressed_bytes[offset:offset + 4])[0]
        offset += 4
        mz_compressed = compressed_bytes[offset:offset + mz_len]
        offset += mz_len

        # Read metadata
        meta_len = struct.unpack('<I', compressed_bytes[offset:offset + 4])[0]
        offset += 4
        meta_compressed = compressed_bytes[offset:offset + meta_len]
        offset += meta_len

        # Read intensity (rest)
        int_compressed = compressed_bytes[offset:]

        backend = header['backend']

        # ── Decode m/z ──
        mz_data = decompress(mz_compressed, backend)
        mz_all = decode_mz(mz_data, header)

        # ── Decode island metadata ──
        meta_raw = decompress(meta_compressed, backend)
        meta_arr = np.frombuffer(meta_raw, dtype=np.float64).reshape(-1, 6)
        # columns: track_id, scan_idx, mz_start, mz_step, n_points, array_start_idx

        # ── Decode intensity ──
        mode = header['int_mode']
        if mode == 'strict_lossless':
            # Need to split int_compressed back into planes
            plane_sizes = header['plane_sizes']
            planes = []
            pos = 0
            for ps in plane_sizes:
                planes.append(decompress(int_compressed[pos:pos + ps], backend))
                pos += ps
            int_all = decode_intensity_strict_lossless(planes, header)
        elif mode.startswith('near_lossless'):
            int_data = decompress(int_compressed, backend)
            int_all = decode_intensity_near_lossless(int_data, header)
        elif mode == 'szdpd':
            int_data = decompress(int_compressed, backend)
            int_all = decode_intensity_szdpd(int_data, header)
        elif mode == 'adaptive':
            int_data = decompress(int_compressed, backend)
            # Need to convert segments from header (JSON stores as lists)
            header_segments = header['segments']
            segments = [(s[0], s[1], s[2]) for s in header_segments]
            header['segments'] = segments
            int_all = decode_intensity_adaptive(int_data, header)
        else:
            raise ValueError(f"Unknown int_mode in header: {mode}")

        decode_time = time.perf_counter() - t0

        # ── Split into per-island arrays ──
        islands = []
        mz_offset = 0
        for row in meta_arr:
            track_id, scan_idx, mz_start, mz_step, n_points, array_start_idx = row
            n = int(n_points)
            islands.append({
                'track_id': int(track_id),
                'scan_idx': int(scan_idx),
                'mz_start': float(mz_start),
                'mz_step': float(mz_step),
                'n_points': n,
                'array_start_idx': int(array_start_idx),
                'mz_array': mz_all[mz_offset:mz_offset + n],
                'intensity_array': int_all[mz_offset:mz_offset + n],
            })
            mz_offset += n

        return islands, header, decode_time


# ═══════════════════════════════════════════════════════════════════
#  Convenience: reconstruct original scan arrays from decoded islands
# ═══════════════════════════════════════════════════════════════════

def reconstruct_scans(decoded_islands, original_scans):
    """
    Reconstruct per-scan m/z and intensity arrays from decoded islands,
    using original_scans as a template for array sizes and ordering.

    Returns list of dicts matching original_scans structure.
    """
    # Group islands by scan_idx
    by_scan = {}
    for isl in decoded_islands:
        sid = isl['scan_idx']
        by_scan.setdefault(sid, []).append(isl)

    reconstructed = []

    def infer_array_start_idx(isl, orig_mz):
        n = int(isl['n_points'])
        if n <= 0 or len(orig_mz) < n:
            return 0
        target = np.asarray(isl['mz_array'], dtype=np.float64)
        pos = int(np.searchsorted(orig_mz, float(target[0])))
        candidates = []
        for cand in range(max(0, pos - 3), min(len(orig_mz) - n + 1, pos + 4)):
            diff = np.max(np.abs(orig_mz[cand:cand + n] - target))
            candidates.append((float(diff), cand))
        if not candidates:
            return max(0, min(len(orig_mz) - n, pos))
        candidates.sort(key=lambda x: (x[0], x[1]))
        return int(candidates[0][1])

    for orig in original_scans:
        sid = orig['scan_idx']
        n = len(orig['mz_array'])
        mz_recon = np.zeros(n, dtype=np.float64)
        int_recon = np.zeros(n, dtype=np.float64)

        if sid in by_scan:
            for isl in by_scan[sid]:
                start = int(isl.get('array_start_idx', -1))
                if start < 0:
                    start = infer_array_start_idx(isl, orig['mz_array'])
                end = start + isl['n_points']
                if end <= n:
                    mz_recon[start:end] = isl['mz_array']
                    int_recon[start:end] = isl['intensity_array']

        reconstructed.append({
            'scan_idx': orig['scan_idx'],
            'rt': orig['rt'],
            'mz_array': mz_recon,
            'intensity_array': int_recon,
        })

    return reconstructed
