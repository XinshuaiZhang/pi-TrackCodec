"""Production-facing MS2 codec wrappers for DIA and DDA."""

from __future__ import annotations

import json
import os
import struct
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Tuple

import numpy as np

from ..common.compression_backends import compress, decompress
from ..ms1.cross_scan_codec import _decode_uint32_stream, _encode_uint32_stream_best
from .window_dictionary_codec import (
    DIAWindowDictionaryCodec,
    DIAWindowExactTrackCodec,
    DIAWindowHybridCodec,
    INTENSITY_CODE_MODE_SZDPD_INT32,
    SCAN_STEP_MODE_DELTA,
    SCAN_STEP_MODE_GAP_MINUS_ONE,
    _pack_exact_track_segments,
    _unpack_exact_track_segments,
)

OUTER_CONTAINER_MAGIC = b"DMS2OC6\0"


def _default_section_workers(max_cap: int = 16) -> int:
    return max(1, min(int(max_cap), int(os.cpu_count() or 1)))


def _emit_progress(progress_callback: Callable[[Dict], None] | None, payload: Dict) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(payload)
    except Exception:
        pass


def _extract_track_units_from_record(record: Dict) -> int:
    if not isinstance(record, dict):
        return 0
    if record.get("dictionary_size") is not None:
        return int(record["dictionary_size"])
    stats = record.get("stats")
    if isinstance(stats, dict) and stats.get("dictionary_size") is not None:
        return int(stats["dictionary_size"])
    return 0


def _extract_track_units_from_meta(meta: Dict) -> int:
    if not isinstance(meta, dict):
        return 0
    header = meta.get("header")
    if isinstance(header, dict):
        if header.get("dictionary_size") is not None:
            return int(header["dictionary_size"])
        stats = header.get("stats")
        if isinstance(stats, dict) and stats.get("dictionary_size") is not None:
            return int(stats["dictionary_size"])
    return 0


class _LazyDIAWindowDecoded:
    """Dictionary-like DIA MS2 decoded payload with per-window lazy decode."""

    def __init__(self, parent: "DIAWindowMS2Codec", encoded: Dict):
        self._parent = parent
        self._section_blob = encoded.get("section_blob")
        self._decoded_section = None
        self._cache: Dict[Tuple[float, float, float], List[dict]] = {}
        self._items: Dict[Tuple[float, float, float], Dict] = {}
        if self._section_blob is None:
            items = encoded["windows"]
            if encoded.get("container_blob") is not None:
                items = parent._unpack_outer_container(encoded["container_blob"], "payload")
            self._items = {tuple(item["window_key"]): item for item in items}

    def get(self, key, default=None):
        window_key = tuple(key)
        if self._section_blob is not None:
            if self._decoded_section is None:
                self._decoded_section = self._parent._decode_exact_track_dia_section(self._section_blob)
            return self._decoded_section.get(window_key, default)
        if window_key in self._cache:
            return self._cache[window_key]
        item = self._items.get(window_key)
        if item is None:
            return default
        codec = self._parent.new_window_codec()
        scans, _, _ = codec.decode(item["payload"])
        self._cache[window_key] = scans
        return scans

    def release_if_done(self, key, consumed_count: int) -> None:
        window_key = tuple(key)
        scans = self._cache.get(window_key)
        if scans is not None and int(consumed_count) >= len(scans):
            self._cache.pop(window_key, None)


@dataclass
class MS2ModeConfig:
    backend: str = "brotli"
    mz_precision: int = 6
    top_k: int = 96
    min_track_length: int = 3
    codec_variant: str = "dictionary"
    use_pfor: bool = True
    pfor_min_count: int = 256
    adaptive_uint32: bool = True
    adaptive_search_mode: str = "converged"
    adaptive_search_sample_count: int = 8192
    helper_compact_uint32: bool = False
    helper_enable_stream_backend_tuning: bool = False
    helper_extended_metadata_adaptive: bool = False
    enable_signed_zero_rle_search: bool = False
    dda_block_size: int = 256
    dda_min_block_scans: int = 16
    dia_section_workers: int = 1
    section_segment_workers: int = 0
    dia_section_parallel_min_windows: int = 4
    dia_exact_track_mode: str = "gated"

    @classmethod
    def equal_mz_dictionary_default(cls, **overrides):
        """Preset matching the same-fidelity dictionary route."""
        cfg = cls(
            backend="brotli",
            mz_precision=6,
            top_k=96,
            min_track_length=3,
            codec_variant="dictionary",
            use_pfor=True,
            pfor_min_count=256,
            adaptive_uint32=True,
            adaptive_search_mode="converged",
            adaptive_search_sample_count=8192,
            dda_block_size=256,
            dda_min_block_scans=16,
            dia_section_workers=_default_section_workers(),
            section_segment_workers=0,
            dia_section_parallel_min_windows=4,
            dia_exact_track_mode="gated",
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    @classmethod
    def exact_track_current(cls, **overrides):
        """Preset preserving the production exact-track path."""
        return cls.exact_track_dia_current(**overrides)

    @classmethod
    def exact_track_dia_current(cls, **overrides):
        """Current DIA-focused exact-track preset."""
        cfg = cls(
            backend="brotli",
            mz_precision=6,
            top_k=32,
            min_track_length=3,
            codec_variant="exact_track",
            use_pfor=True,
            pfor_min_count=256,
            adaptive_uint32=True,
            adaptive_search_mode="converged",
            adaptive_search_sample_count=8192,
            helper_compact_uint32=True,
            helper_enable_stream_backend_tuning=True,
            helper_extended_metadata_adaptive=True,
            enable_signed_zero_rle_search=True,
            dda_block_size=256,
            dda_min_block_scans=16,
            dia_section_workers=_default_section_workers(),
            section_segment_workers=0,
            dia_section_parallel_min_windows=4,
            dia_exact_track_mode="prefer_section",
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    @classmethod
    def exact_track_dda_current(cls, **overrides):
        """Current DDA-focused exact-track preset."""
        cfg = cls(
            backend="brotli",
            mz_precision=6,
            top_k=32,
            min_track_length=3,
            codec_variant="exact_track",
            use_pfor=True,
            pfor_min_count=256,
            adaptive_uint32=True,
            adaptive_search_mode="converged",
            adaptive_search_sample_count=8192,
            helper_compact_uint32=True,
            helper_enable_stream_backend_tuning=True,
            helper_extended_metadata_adaptive=True,
            enable_signed_zero_rle_search=True,
            dda_block_size=512,
            dda_min_block_scans=64,
            dia_section_workers=_default_section_workers(),
            section_segment_workers=0,
            dia_section_parallel_min_windows=4,
            dia_exact_track_mode="gated",
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg

    @classmethod
    def dictionary_dia_current(cls, **overrides):
        """Current DIA dictionary/top-K preset with DIA-window grouping."""
        cfg = cls(
            backend="brotli",
            mz_precision=6,
            top_k=96,
            min_track_length=3,
            codec_variant="dictionary",
            use_pfor=True,
            pfor_min_count=256,
            adaptive_uint32=True,
            adaptive_search_mode="converged",
            adaptive_search_sample_count=8192,
            dda_block_size=256,
            dda_min_block_scans=16,
            dia_section_workers=_default_section_workers(),
            section_segment_workers=0,
            dia_section_parallel_min_windows=4,
            dia_exact_track_mode="gated",
        )
        for key, value in overrides.items():
            setattr(cfg, key, value)
        return cfg


class DIAWindowMS2Codec:
    def __init__(self, config: MS2ModeConfig | None = None):
        self.config = config or MS2ModeConfig.equal_mz_dictionary_default()

    def _normalize_dia_window_items(
        self,
        ms2_by_window: Dict[Tuple[float, float, float], List[dict]] | Iterable[Tuple[Tuple[float, float, float], List[dict]]],
    ) -> List[Tuple[Tuple[float, float, float], List[dict]]]:
        if isinstance(ms2_by_window, dict):
            return sorted(ms2_by_window.items(), key=lambda item: item[0])
        return sorted(list(ms2_by_window), key=lambda item: item[0])

    def _pack_outer_container(self, kind: str, records: List[Dict], payload_field: str) -> bytes:
        header_records = []
        payload_parts = []
        for item in records:
            header_record = {key: value for key, value in item.items() if key != payload_field}
            header_record["payload_len"] = len(item[payload_field])
            header_records.append(header_record)
            payload_parts.append(item[payload_field])
        header_blob = json.dumps(
            {
                "kind": kind,
                "records": header_records,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        raw = bytearray()
        raw.extend(OUTER_CONTAINER_MAGIC)
        raw.extend(struct.pack("<I", len(header_blob)))
        raw.extend(header_blob)
        for part in payload_parts:
            raw.extend(part)
        return compress(bytes(raw), self.config.backend)

    def _unpack_outer_container(self, blob: bytes, payload_field: str) -> List[Dict]:
        raw = decompress(blob, self.config.backend)
        if not raw.startswith(OUTER_CONTAINER_MAGIC):
            raise ValueError("Invalid MS2 outer container magic")
        header_len = struct.unpack("<I", raw[len(OUTER_CONTAINER_MAGIC):len(OUTER_CONTAINER_MAGIC) + 4])[0]
        offset = len(OUTER_CONTAINER_MAGIC) + 4
        header = json.loads(raw[offset:offset + header_len].decode("utf-8"))
        offset += header_len
        records = []
        for item in header["records"]:
            payload_len = int(item["payload_len"])
            payload = raw[offset:offset + payload_len]
            offset += payload_len
            record = dict(item)
            record[payload_field] = payload
            records.append(record)
        return records

    def new_window_codec(self) -> DIAWindowDictionaryCodec:
        cfg = self.config
        common_kwargs = dict(
            backend=cfg.backend,
            mz_precision=cfg.mz_precision,
            top_k=cfg.top_k,
            min_track_length=cfg.min_track_length,
            use_pfor=cfg.use_pfor,
            pfor_min_count=cfg.pfor_min_count,
            adaptive_uint32=cfg.adaptive_uint32,
            adaptive_search_mode=cfg.adaptive_search_mode,
            adaptive_search_sample_count=cfg.adaptive_search_sample_count,
            helper_compact_uint32=cfg.helper_compact_uint32,
            helper_enable_stream_backend_tuning=cfg.helper_enable_stream_backend_tuning,
            helper_extended_metadata_adaptive=cfg.helper_extended_metadata_adaptive,
            enable_signed_zero_rle_search=cfg.enable_signed_zero_rle_search,
        )
        if cfg.codec_variant == "dictionary":
            return DIAWindowDictionaryCodec(**common_kwargs)
        if cfg.codec_variant == "exact_track":
            return DIAWindowExactTrackCodec(**common_kwargs)
        if cfg.codec_variant == "hybrid":
            return DIAWindowHybridCodec(**common_kwargs)
        raise ValueError(f"Unsupported MS2 codec variant: {cfg.codec_variant}")

    def _encode_exact_track_section_dict(self, codec: DIAWindowExactTrackCodec, records: List[Dict]) -> Tuple[bytes, Dict]:
        dict_parts = [np.asarray(item["record"]["dict_mz_q"], dtype=np.int64) for item in records]
        flat = np.concatenate(dict_parts) if dict_parts else np.array([], dtype=np.int64)
        specs = [{"dc": int(len(values))} for values in dict_parts]
        return self._encode_exact_track_section_dict_flat(codec, flat, specs)

    def _encode_exact_track_section_dict_flat(
        self,
        codec: DIAWindowExactTrackCodec,
        flat: np.ndarray,
        specs: List[Dict],
    ) -> Tuple[bytes, Dict]:
        flat = np.asarray(flat, dtype=np.int64)
        if len(flat) == 0:
            payload, meta = codec._encode_dict_mz_stream(flat)
            return payload, {**meta, "section_dict_mode": "flat"}
        anchor = int(flat[0])
        offsets = flat - anchor
        if offsets.min(initial=0) >= 0 and offsets.max(initial=0) <= np.iinfo(np.uint32).max:
            payload, meta = codec._encode_dict_mz_stream(flat)
            return payload, {**meta, "section_dict_mode": "flat"}

        anchors = np.empty(len(specs), dtype=np.int64)
        delta_count = sum(max(0, int(spec["dc"]) - 1) for spec in specs)
        delta_values_i64 = np.empty(delta_count, dtype=np.int64)
        value_pos = 0
        delta_pos = 0
        for idx, spec in enumerate(specs):
            count = int(spec["dc"])
            if count <= 0:
                anchors[idx] = 0
                continue
            values = flat[value_pos:value_pos + count]
            value_pos += count
            anchors[idx] = int(values[0])
            if count > 1:
                take = count - 1
                diffs = np.diff(values)
                if np.any(diffs < 0):
                    raise ValueError(
                        f"MS2 exact-track dictionary m/z values must be non-decreasing within each window; "
                        f"window {idx} has a negative delta"
                    )
                delta_values_i64[delta_pos:delta_pos + take] = diffs.astype(np.int64, copy=False)
                delta_pos += take

        anchor_payload, anchor_meta = codec._compress_int64_values(anchors, "mz_first_values")
        max_delta = int(delta_values_i64.max(initial=0)) if len(delta_values_i64) else 0
        if max_delta <= int(np.iinfo(np.uint32).max):
            delta_payload, delta_meta = _encode_uint32_stream_best(
                delta_values_i64.astype(np.uint32, copy=False),
                "zstd-9",
                use_pfor=False,
                pfor_codec=codec.helper.pfor_codec,
                zero_rle=False,
                backend_candidates=["zstd-9"],
                try_pfor=False,
                try_zero_rle=False,
                allow_bitpack=True,
                search_mode="full",
                sample_count=codec.adaptive_search_sample_count,
            )
            section_dict_mode = "window_anchor_delta"
        else:
            delta_payload, delta_meta = codec._compress_int64_values(delta_values_i64, "mz_first_deltas")
            section_dict_mode = "window_anchor_delta_i64"
        payload = bytearray()
        payload.extend(struct.pack("<II", len(anchor_payload), len(delta_payload)))
        payload.extend(anchor_payload)
        payload.extend(delta_payload)
        return bytes(payload), {
            "section_dict_mode": section_dict_mode,
            "anchor_meta": anchor_meta,
            "delta_meta": delta_meta,
        }

    def _decode_exact_track_section_dict(
        self,
        codec: DIAWindowExactTrackCodec,
        payload: bytes,
        meta: Dict,
        window_specs: List[Dict],
    ) -> np.ndarray:
        if meta.get("section_dict_mode", "flat") == "flat":
            return codec._decode_dict_mz_stream(payload, meta).astype(np.int64)

        anchor_len, delta_len = struct.unpack("<II", payload[:8])
        offset = 8
        anchor_payload = payload[offset:offset + anchor_len]
        offset += anchor_len
        delta_payload = payload[offset:offset + delta_len]
        anchors = codec._decompress_int64_values(anchor_payload, meta["anchor_meta"])
        mode = meta.get("section_dict_mode")
        if mode == "window_anchor_delta_i64":
            delta_values = codec._decompress_int64_values(delta_payload, meta["delta_meta"])
        else:
            delta_values = _decode_uint32_stream(delta_payload, meta["delta_meta"]).astype(np.int64)

        out_parts = []
        delta_pos = 0
        for window_idx, spec in enumerate(window_specs):
            count = int(spec["dc"])
            if count <= 0:
                out_parts.append(np.array([], dtype=np.int64))
                continue
            anchor = int(anchors[window_idx])
            if count == 1:
                out_parts.append(np.asarray([anchor], dtype=np.int64))
                continue
            local_delta = delta_values[delta_pos:delta_pos + count - 1]
            delta_pos += count - 1
            values = np.empty(count, dtype=np.int64)
            values[0] = anchor
            values[1:] = anchor + np.cumsum(local_delta, dtype=np.int64)
            out_parts.append(values)
        return np.concatenate(out_parts) if out_parts else np.array([], dtype=np.int64)

    def _run_exact_track_section_encode_jobs(self, jobs: List[Tuple[str, Callable[[], Tuple[bytes, Dict]]]]) -> Tuple[Dict[str, bytes], Dict]:
        segments: Dict[str, bytes] = {}
        meta: Dict = {}
        if not jobs:
            return segments, meta

        worker_count = self._exact_track_section_segment_worker_count(len(jobs))
        if worker_count <= 1:
            for name, fn in jobs:
                payload, payload_meta = fn()
                segments[name] = payload
                meta[name] = payload_meta
            return segments, meta

        results = [None] * len(jobs)
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_map = {executor.submit(fn): idx for idx, (_, fn) in enumerate(jobs)}
            for future in as_completed(future_map):
                results[future_map[future]] = future.result()

        for idx, (name, _) in enumerate(jobs):
            result = results[idx]
            if result is None:
                raise RuntimeError(f"MS2 exact-track section encode job did not finish: {name}")
            payload, payload_meta = result
            segments[name] = payload
            meta[name] = payload_meta
        return segments, meta

    def _exact_track_section_segment_worker_count(self, job_count: int) -> int:
        if job_count <= 1:
            return max(1, job_count)
        configured = int(getattr(self.config, "section_segment_workers", 0) or 0)
        if configured > 0:
            return max(1, min(configured, job_count))
        block_workers = int(getattr(self.config, "dia_section_workers", 1) or 1)
        if block_workers > 1:
            return max(1, min(block_workers, job_count))
        return max(1, min(2, job_count, int(os.cpu_count() or 1)))

    def _collect_exact_track_section_arrays(self, records: List[Dict], kind: str) -> Tuple[Dict[str, np.ndarray], List[Dict]]:
        total_dict = 0
        total_gap = 0
        total_delta = 0
        total_first_overflow = 0
        total_delta_overflow = 0
        specs: List[Dict] = []
        for item in records:
            record = item["record"]
            dict_count = int(len(record["dict_mz_q"]))
            first_overflow_idx = np.asarray(record.get("int_first_overflow_idx", np.array([], dtype=np.uint32)), dtype=np.uint32)
            int_delta = np.asarray(record["int_delta_main"], dtype=np.int32)
            overflow_idx = np.asarray(record["int_delta_overflow_idx"], dtype=np.uint32)
            spec = {
                "ns": int(record["n_scans"]),
                "pr": int(record["precision"]),
                "dc": dict_count,
                "ifo": int(len(first_overflow_idx)),
                "gc": int(len(record["scan_gaps"])),
                "idc": int(len(int_delta)),
                "ofc": int(len(overflow_idx)),
                "rb": int(record["raw_bytes"]),
                "rtc": int(record["raw_track_count"]),
                "mtl": float(record["stats"]["mean_track_length"]),
                "xtl": int(record["stats"]["max_track_length"]),
            }
            if kind == "DIA":
                spec["wk"] = [float(x) for x in item["window_key"]]
            else:
                spec["bs"] = int(item["block_start"])
            specs.append(spec)
            total_dict += dict_count
            total_gap += int(spec["gc"])
            total_delta += int(spec["idc"])
            total_first_overflow += int(spec["ifo"])
            total_delta_overflow += int(spec["ofc"])

        arrays = {
            "dict_mz_q": np.empty(total_dict, dtype=np.int64),
            "track_lengths": np.empty(total_dict, dtype=np.uint32),
            "scan_firsts": np.empty(total_dict, dtype=np.uint32),
            "scan_gaps": np.empty(total_gap, dtype=np.uint32),
            "int_first_codes": np.empty(total_dict, dtype=np.int32),
            "int_first_overflow_idx": np.empty(total_first_overflow, dtype=np.uint32),
            "int_first_overflow_vals": np.empty(total_first_overflow, dtype=np.int64),
            "int_delta_main": np.empty(total_delta, dtype=np.int32),
            "int_delta_overflow_idx": np.empty(total_delta_overflow, dtype=np.uint32),
            "int_delta_overflow_vals": np.empty(total_delta_overflow, dtype=np.int64),
        }
        dict_pos = 0
        gap_pos = 0
        first_overflow_pos = 0
        delta_pos = 0
        overflow_pos = 0
        for item in records:
            record = item["record"]
            dict_values = np.asarray(record["dict_mz_q"], dtype=np.int64)
            dict_count = int(len(dict_values))
            gap_values = np.asarray(record["scan_gaps"], dtype=np.uint32)
            gap_count = int(len(gap_values))
            first_overflow_idx = np.asarray(record.get("int_first_overflow_idx", np.array([], dtype=np.uint32)), dtype=np.uint32)
            first_overflow_count = int(len(first_overflow_idx))
            int_delta = np.asarray(record["int_delta_main"], dtype=np.int32)
            delta_count = int(len(int_delta))
            overflow_idx = np.asarray(record["int_delta_overflow_idx"], dtype=np.uint32)
            overflow_count = int(len(overflow_idx))

            arrays["dict_mz_q"][dict_pos:dict_pos + dict_count] = dict_values
            arrays["track_lengths"][dict_pos:dict_pos + dict_count] = np.asarray(record["track_lengths"], dtype=np.uint32)
            arrays["scan_firsts"][dict_pos:dict_pos + dict_count] = np.asarray(record["scan_firsts"], dtype=np.uint32)
            arrays["int_first_codes"][dict_pos:dict_pos + dict_count] = np.asarray(record["int_first_codes"], dtype=np.int32)
            arrays["scan_gaps"][gap_pos:gap_pos + gap_count] = gap_values
            arrays["int_delta_main"][delta_pos:delta_pos + delta_count] = int_delta

            if first_overflow_count:
                arrays["int_first_overflow_idx"][first_overflow_pos:first_overflow_pos + first_overflow_count] = first_overflow_idx + np.uint32(dict_pos)
                arrays["int_first_overflow_vals"][first_overflow_pos:first_overflow_pos + first_overflow_count] = np.asarray(
                    record.get("int_first_overflow_vals", np.array([], dtype=np.int64)),
                    dtype=np.int64,
                )
            if overflow_count:
                arrays["int_delta_overflow_idx"][overflow_pos:overflow_pos + overflow_count] = overflow_idx + np.uint32(delta_pos)
                arrays["int_delta_overflow_vals"][overflow_pos:overflow_pos + overflow_count] = np.asarray(record["int_delta_overflow_vals"], dtype=np.int64)

            dict_pos += dict_count
            gap_pos += gap_count
            first_overflow_pos += first_overflow_count
            delta_pos += delta_count
            overflow_pos += overflow_count
        return arrays, specs

    def _encode_exact_track_dia_section(self, codec: DIAWindowExactTrackCodec, records: List[Dict]) -> bytes:
        arrays, window_specs = self._collect_exact_track_section_arrays(records, "DIA")

        def _new_exact_codec() -> DIAWindowExactTrackCodec:
            local_codec = self.new_window_codec()
            if not isinstance(local_codec, DIAWindowExactTrackCodec):
                raise ValueError("Exact-track section encode requires exact-track codec")
            return local_codec

        jobs: List[Tuple[str, Callable[[], Tuple[bytes, Dict]]]] = [
            ("dict_mz_q", lambda: self._encode_exact_track_section_dict_flat(_new_exact_codec(), arrays["dict_mz_q"], window_specs)),
            ("track_lengths", lambda: _new_exact_codec()._encode_compact_uint32_meta(arrays["track_lengths"], "track_lengths", prefer_small_uint=True)),
            ("scan_firsts", lambda: _new_exact_codec()._encode_compact_uint32_meta(arrays["scan_firsts"], "scan_indices", prefer_small_uint=True)),
            ("scan_gaps", lambda: _new_exact_codec()._encode_compact_uint32_meta(arrays["scan_gaps"], "delta_ref_offsets", prefer_small_uint=True)),
            ("int_first_codes", lambda: _new_exact_codec()._encode_signed_int32_stream(arrays["int_first_codes"], "full_intensity")),
            ("int_delta_main", lambda: _new_exact_codec()._encode_signed_int32_stream(arrays["int_delta_main"], "delta_intensity")),
            ("int_delta_overflow_idx", lambda: _new_exact_codec()._encode_compact_uint32_meta(
                arrays["int_delta_overflow_idx"],
                "delta_overflow_idx",
                prefer_small_uint=True,
            )),
            ("int_delta_overflow_vals", lambda: _new_exact_codec()._compress_int64_values(arrays["int_delta_overflow_vals"], "delta_overflow_vals")),
        ]
        if len(arrays["int_first_overflow_idx"]):
            jobs.extend(
                [
                    ("int_first_overflow_idx", lambda: _new_exact_codec()._encode_compact_uint32_meta(
                        arrays["int_first_overflow_idx"],
                        "full_intensity_overflow_idx",
                        prefer_small_uint=True,
                    )),
                    ("int_first_overflow_vals", lambda: _new_exact_codec()._compress_int64_values(
                        arrays["int_first_overflow_vals"],
                        "full_intensity_overflow_vals",
                    )),
                ]
            )
        segments, meta = self._run_exact_track_section_encode_jobs(jobs)

        return _pack_exact_track_segments(
            {
                "section": True,
                "fmt": "dia_ms2_exact_track_section",
                "kind": "DIA",
                "ms": int(codec.mz_scale),
                "icm": records[0]["record"].get("intensity_code_mode", INTENSITY_CODE_MODE_SZDPD_INT32) if records else INTENSITY_CODE_MODE_SZDPD_INT32,
                "sgm": records[0]["record"].get("scan_step_mode", SCAN_STEP_MODE_DELTA) if records else SCAN_STEP_MODE_DELTA,
                "sm": meta,
                "ws": window_specs,
            },
            segments,
            self.config.backend,
        )

    def _encode_exact_track_dda_section(self, codec: DIAWindowExactTrackCodec, records: List[Dict]) -> bytes:
        arrays, block_specs = self._collect_exact_track_section_arrays(records, "DDA")

        def _new_exact_codec() -> DIAWindowExactTrackCodec:
            local_codec = self.new_window_codec()
            if not isinstance(local_codec, DIAWindowExactTrackCodec):
                raise ValueError("Exact-track section encode requires exact-track codec")
            return local_codec

        jobs: List[Tuple[str, Callable[[], Tuple[bytes, Dict]]]] = [
            ("dict_mz_q", lambda: self._encode_exact_track_section_dict_flat(_new_exact_codec(), arrays["dict_mz_q"], block_specs)),
            ("track_lengths", lambda: _new_exact_codec()._encode_compact_uint32_meta(arrays["track_lengths"], "track_lengths", prefer_small_uint=True)),
            ("scan_firsts", lambda: _new_exact_codec()._encode_compact_uint32_meta(arrays["scan_firsts"], "scan_indices", prefer_small_uint=True)),
            ("scan_gaps", lambda: _new_exact_codec()._encode_compact_uint32_meta(arrays["scan_gaps"], "delta_ref_offsets", prefer_small_uint=True)),
            ("int_first_codes", lambda: _new_exact_codec()._encode_signed_int32_stream(arrays["int_first_codes"], "full_intensity")),
            ("int_delta_main", lambda: _new_exact_codec()._encode_signed_int32_stream(arrays["int_delta_main"], "delta_intensity")),
            ("int_delta_overflow_idx", lambda: _new_exact_codec()._encode_compact_uint32_meta(
                arrays["int_delta_overflow_idx"],
                "delta_overflow_idx",
                prefer_small_uint=True,
            )),
            ("int_delta_overflow_vals", lambda: _new_exact_codec()._compress_int64_values(arrays["int_delta_overflow_vals"], "delta_overflow_vals")),
        ]
        if len(arrays["int_first_overflow_idx"]):
            jobs.extend(
                [
                    ("int_first_overflow_idx", lambda: _new_exact_codec()._encode_compact_uint32_meta(
                        arrays["int_first_overflow_idx"],
                        "full_intensity_overflow_idx",
                        prefer_small_uint=True,
                    )),
                    ("int_first_overflow_vals", lambda: _new_exact_codec()._compress_int64_values(
                        arrays["int_first_overflow_vals"],
                        "full_intensity_overflow_vals",
                    )),
                ]
            )
        segments, meta = self._run_exact_track_section_encode_jobs(jobs)

        return _pack_exact_track_segments(
            {
                "section": True,
                "fmt": "dda_ms2_exact_track_section",
                "kind": "DDA",
                "ms": int(codec.mz_scale),
                "icm": records[0]["record"].get("intensity_code_mode", INTENSITY_CODE_MODE_SZDPD_INT32) if records else INTENSITY_CODE_MODE_SZDPD_INT32,
                "sgm": records[0]["record"].get("scan_step_mode", SCAN_STEP_MODE_DELTA) if records else SCAN_STEP_MODE_DELTA,
                "sm": meta,
                "bs": block_specs,
            },
            segments,
            self.config.backend,
        )

    def _build_exact_track_window_record(self, item: Tuple[Tuple[float, float, float], List[dict]]) -> Dict:
        window_key, scans = item
        codec = self.new_window_codec()
        if not isinstance(codec, DIAWindowExactTrackCodec):
            raise ValueError("Expected exact-track codec for exact-track record build")
        record = codec.build_component_record(scans, window_target_mz=window_key[0])
        return {
            "window_key": window_key,
            "record": record,
            "n_scans": len(scans),
        }

    def _build_exact_track_dda_block_record(self, item: Tuple[int, List[dict]]) -> Dict:
        block_start, scans = item
        codec = self.new_window_codec()
        if not isinstance(codec, DIAWindowExactTrackCodec):
            raise ValueError("Expected exact-track codec for exact-track record build")
        record = codec.build_component_record(scans, window_target_mz=0.0)
        return {
            "block_start": int(block_start),
            "record": record,
            "n_scans": len(scans),
        }

    def _encode_dia_window_payload(self, item: Tuple[Tuple[float, float, float], List[dict]]) -> Dict:
        window_key, scans = item
        codec = self.new_window_codec()
        payload, meta = codec.encode(scans, window_target_mz=window_key[0])
        return {
            "window_key": window_key,
            "payload": payload,
            "meta": meta,
            "n_scans": len(scans),
        }

    def _encode_dda_block_payload(self, item: Tuple[int, List[dict]]) -> Dict:
        block_start, scans = item
        codec = self.new_window_codec()
        payload, meta = codec.encode(scans, window_target_mz=0.0)
        return {
            "block_start": int(block_start),
            "payload": payload,
            "meta": meta,
            "n_scans": len(scans),
        }

    def _decode_exact_track_dia_section(self, blob: bytes) -> Dict[Tuple[float, float, float], List[dict]]:
        header, segments = _unpack_exact_track_segments(blob)
        if not header.get("section"):
            raise ValueError("Not an exact-track DIA section payload")
        codec = self.new_window_codec()
        if not isinstance(codec, DIAWindowExactTrackCodec):
            raise ValueError("Exact-track DIA section decode requires exact-track codec")

        meta = header["sm"]
        dict_mz_q_all = self._decode_exact_track_section_dict(codec, segments["dict_mz_q"], meta["dict_mz_q"], header["ws"]).astype(np.int64)
        track_lengths_all = codec._decode_compact_uint32_meta(segments["track_lengths"], meta["track_lengths"])
        scan_firsts_all = codec._decode_compact_uint32_meta(segments["scan_firsts"], meta["scan_firsts"])
        scan_gaps_all = codec._decode_compact_uint32_meta(segments["scan_gaps"], meta["scan_gaps"])
        int_first_codes_all = codec._decode_signed_int32_stream(segments["int_first_codes"], meta["int_first_codes"])
        int_first_overflow_idx_all = codec._decode_optional_uint32_sidecar(segments, meta, "int_first_overflow_idx").astype(np.int64)
        int_first_overflow_vals_all = codec._decode_optional_int64_sidecar(segments, meta, "int_first_overflow_vals")
        int_delta_main_all = codec._decode_signed_int32_stream(segments["int_delta_main"], meta["int_delta_main"])
        overflow_idx_all = codec._decode_compact_uint32_meta(segments["int_delta_overflow_idx"], meta["int_delta_overflow_idx"]).astype(np.int64)
        overflow_vals_all = codec._decompress_int64_values(segments["int_delta_overflow_vals"], meta["int_delta_overflow_vals"])

        jobs = []
        dict_pos = 0
        gap_pos = 0
        first_overflow_pos = 0
        delta_pos = 0
        overflow_pos = 0
        for spec in header["ws"]:
            dict_count = int(spec["dc"])
            first_overflow_count = int(spec.get("ifo", 0))
            gap_count = int(spec["gc"])
            delta_count = int(spec["idc"])
            overflow_count = int(spec["ofc"])

            dict_slice = dict_mz_q_all[dict_pos:dict_pos + dict_count]
            track_slice = track_lengths_all[dict_pos:dict_pos + dict_count]
            first_slice = scan_firsts_all[dict_pos:dict_pos + dict_count]
            int_first_slice = int_first_codes_all[dict_pos:dict_pos + dict_count]
            int_first_overflow_idx_slice = int_first_overflow_idx_all[first_overflow_pos:first_overflow_pos + first_overflow_count] - dict_pos
            int_first_overflow_val_slice = int_first_overflow_vals_all[first_overflow_pos:first_overflow_pos + first_overflow_count]
            gap_slice = scan_gaps_all[gap_pos:gap_pos + gap_count]
            delta_slice = np.asarray(int_delta_main_all[delta_pos:delta_pos + delta_count], dtype=np.int64)
            overflow_idx_slice = overflow_idx_all[overflow_pos:overflow_pos + overflow_count] - delta_pos
            overflow_val_slice = overflow_vals_all[overflow_pos:overflow_pos + overflow_count]

            jobs.append(
                (
                    tuple(spec["wk"]),
                    int(spec["ns"]),
                    int(header["ms"]),
                    int(spec["pr"]),
                    header.get("icm", INTENSITY_CODE_MODE_SZDPD_INT32),
                    header.get("sgm", SCAN_STEP_MODE_GAP_MINUS_ONE),
                    dict_slice,
                    track_slice,
                    first_slice,
                    gap_slice,
                    int_first_slice,
                    delta_slice,
                    overflow_idx_slice,
                    overflow_val_slice,
                    int_first_overflow_idx_slice,
                    int_first_overflow_val_slice,
                )
            )

            dict_pos += dict_count
            gap_pos += gap_count
            first_overflow_pos += first_overflow_count
            delta_pos += delta_count
            overflow_pos += overflow_count

        def _decode_section_job(job):
            (
                window_key,
                n_scans,
                mz_scale,
                precision,
                intensity_code_mode,
                scan_step_mode,
                dict_slice,
                track_slice,
                first_slice,
                gap_slice,
                int_first_slice,
                delta_slice,
                overflow_idx_slice,
                overflow_val_slice,
                int_first_overflow_idx_slice,
                int_first_overflow_val_slice,
            ) = job
            local_codec = self.new_window_codec()
            if not isinstance(local_codec, DIAWindowExactTrackCodec):
                raise ValueError("Exact-track DIA section decode requires exact-track codec")
            scans = local_codec._reconstruct_scans_from_arrays(
                n_scans=n_scans,
                mz_scale=mz_scale,
                precision=precision,
                intensity_code_mode=intensity_code_mode,
                scan_step_mode=scan_step_mode,
                dict_mz_q=dict_slice,
                track_lengths=track_slice,
                scan_firsts=first_slice,
                scan_gaps=gap_slice,
                int_first_codes=int_first_slice,
                int_delta_main=delta_slice,
                int_delta_overflow_idx=overflow_idx_slice,
                int_delta_overflow_vals=overflow_val_slice,
                int_first_overflow_idx=int_first_overflow_idx_slice,
                int_first_overflow_vals=int_first_overflow_val_slice,
            )
            return window_key, scans

        worker_count = max(1, min(int(self.config.dia_section_workers), len(jobs)))
        decoded = {}
        if worker_count > 1 and len(jobs) > 1:
            results = [None] * len(jobs)
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {executor.submit(_decode_section_job, job): idx for idx, job in enumerate(jobs)}
                for future in as_completed(future_map):
                    results[future_map[future]] = future.result()
            for result in results:
                if result is None:
                    raise RuntimeError("MS2 exact-track section decode job did not finish")
                window_key, scans = result
                decoded[window_key] = scans
        else:
            for job in jobs:
                window_key, scans = _decode_section_job(job)
                decoded[window_key] = scans
        return decoded

    def _decode_exact_track_dda_section(self, blob: bytes) -> List[dict]:
        decoded: List[dict] = []
        for block_start, scans in self._iter_decode_exact_track_dda_section_blocks(blob):
            for local_idx, (mz_array, intensity_array) in enumerate(scans):
                decoded.append(
                    {
                        "scan_idx": int(block_start) + int(local_idx),
                        "mz_array": mz_array,
                        "intensity_array": intensity_array,
                    }
                )
        return decoded

    def _iter_decode_exact_track_dda_section_blocks(self, blob: bytes):
        header, segments = _unpack_exact_track_segments(blob)
        if not header.get("section"):
            raise ValueError("Not an exact-track DDA section payload")
        if header.get("kind") != "DDA":
            raise ValueError(f"Expected DDA exact-track section, got {header.get('kind')}")
        codec = self.new_window_codec()
        if not isinstance(codec, DIAWindowExactTrackCodec):
            raise ValueError("Exact-track DDA section decode requires exact-track codec")

        block_specs = header["bs"]
        meta = header["sm"]
        dict_mz_q_all = self._decode_exact_track_section_dict(codec, segments["dict_mz_q"], meta["dict_mz_q"], block_specs).astype(np.int64)
        track_lengths_all = codec._decode_compact_uint32_meta(segments["track_lengths"], meta["track_lengths"])
        scan_firsts_all = codec._decode_compact_uint32_meta(segments["scan_firsts"], meta["scan_firsts"])
        scan_gaps_all = codec._decode_compact_uint32_meta(segments["scan_gaps"], meta["scan_gaps"])
        int_first_codes_all = codec._decode_signed_int32_stream(segments["int_first_codes"], meta["int_first_codes"])
        int_first_overflow_idx_all = codec._decode_optional_uint32_sidecar(segments, meta, "int_first_overflow_idx").astype(np.int64)
        int_first_overflow_vals_all = codec._decode_optional_int64_sidecar(segments, meta, "int_first_overflow_vals")
        int_delta_main_all = codec._decode_signed_int32_stream(segments["int_delta_main"], meta["int_delta_main"])
        overflow_idx_all = codec._decode_compact_uint32_meta(segments["int_delta_overflow_idx"], meta["int_delta_overflow_idx"]).astype(np.int64)
        overflow_vals_all = codec._decompress_int64_values(segments["int_delta_overflow_vals"], meta["int_delta_overflow_vals"])

        jobs = []
        dict_pos = 0
        gap_pos = 0
        first_overflow_pos = 0
        delta_pos = 0
        overflow_pos = 0
        for spec in block_specs:
            dict_count = int(spec["dc"])
            first_overflow_count = int(spec.get("ifo", 0))
            gap_count = int(spec["gc"])
            delta_count = int(spec["idc"])
            overflow_count = int(spec["ofc"])

            dict_slice = dict_mz_q_all[dict_pos:dict_pos + dict_count]
            track_slice = track_lengths_all[dict_pos:dict_pos + dict_count]
            first_slice = scan_firsts_all[dict_pos:dict_pos + dict_count]
            int_first_slice = int_first_codes_all[dict_pos:dict_pos + dict_count]
            int_first_overflow_idx_slice = int_first_overflow_idx_all[first_overflow_pos:first_overflow_pos + first_overflow_count] - dict_pos
            int_first_overflow_val_slice = int_first_overflow_vals_all[first_overflow_pos:first_overflow_pos + first_overflow_count]
            gap_slice = scan_gaps_all[gap_pos:gap_pos + gap_count]
            delta_slice = np.asarray(int_delta_main_all[delta_pos:delta_pos + delta_count], dtype=np.int64)
            overflow_idx_slice = overflow_idx_all[overflow_pos:overflow_pos + overflow_count] - delta_pos
            overflow_val_slice = overflow_vals_all[overflow_pos:overflow_pos + overflow_count]

            jobs.append(
                (
                    int(spec["bs"]),
                    int(spec["ns"]),
                    int(header["ms"]),
                    int(spec["pr"]),
                    header.get("icm", INTENSITY_CODE_MODE_SZDPD_INT32),
                    header.get("sgm", SCAN_STEP_MODE_GAP_MINUS_ONE),
                    dict_slice,
                    track_slice,
                    first_slice,
                    gap_slice,
                    int_first_slice,
                    delta_slice,
                    overflow_idx_slice,
                    overflow_val_slice,
                    int_first_overflow_idx_slice,
                    int_first_overflow_val_slice,
                )
            )

            dict_pos += dict_count
            gap_pos += gap_count
            first_overflow_pos += first_overflow_count
            delta_pos += delta_count
            overflow_pos += overflow_count

        def _decode_section_job(job):
            (
                block_start,
                n_scans,
                mz_scale,
                precision,
                intensity_code_mode,
                scan_step_mode,
                dict_slice,
                track_slice,
                first_slice,
                gap_slice,
                int_first_slice,
                delta_slice,
                overflow_idx_slice,
                overflow_val_slice,
                int_first_overflow_idx_slice,
                int_first_overflow_val_slice,
            ) = job
            local_codec = self.new_window_codec()
            if not isinstance(local_codec, DIAWindowExactTrackCodec):
                raise ValueError("Exact-track DDA section decode requires exact-track codec")
            scans = list(
                local_codec.iter_reconstruct_scan_arrays_from_arrays(
                    n_scans=n_scans,
                    mz_scale=mz_scale,
                    precision=precision,
                    intensity_code_mode=intensity_code_mode,
                    scan_step_mode=scan_step_mode,
                    dict_mz_q=dict_slice,
                    track_lengths=track_slice,
                    scan_firsts=first_slice,
                    scan_gaps=gap_slice,
                    int_first_codes=int_first_slice,
                    int_delta_main=delta_slice,
                    int_delta_overflow_idx=overflow_idx_slice,
                    int_delta_overflow_vals=overflow_val_slice,
                    int_first_overflow_idx=int_first_overflow_idx_slice,
                    int_first_overflow_vals=int_first_overflow_val_slice,
                )
            )
            return block_start, scans

        worker_count = self._exact_track_section_segment_worker_count(len(jobs))
        if worker_count > 1 and len(jobs) > 1:
            results = [None] * len(jobs)
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {executor.submit(_decode_section_job, job): idx for idx, job in enumerate(jobs)}
                for future in as_completed(future_map):
                    results[future_map[future]] = future.result()
            for result in results:
                if result is None:
                    raise RuntimeError("MS2 exact-track DDA section decode job did not finish")
                yield result
        else:
            for job in jobs:
                yield _decode_section_job(job)

    def _iter_decode_exact_track_dda_section_scan_arrays(self, blob: bytes):
        for _, scans in self._iter_decode_exact_track_dda_section_blocks(blob):
            yield from scans

    def encode_dia_windows(
        self,
        ms2_by_window: Dict[Tuple[float, float, float], List[dict]] | Iterable[Tuple[Tuple[float, float, float], List[dict]]],
        progress_callback: Callable[[Dict], None] | None = None,
    ):
        total_t0 = time.perf_counter()
        sorted_items = self._normalize_dia_window_items(ms2_by_window)
        total_windows = int(len(sorted_items))
        worker_count = max(1, min(int(self.config.dia_section_workers), len(sorted_items)))
        use_parallel = worker_count > 1 and len(sorted_items) >= int(self.config.dia_section_parallel_min_windows)
        stage_timings = {
            "ms2_normalize_windows_s": time.perf_counter() - total_t0,
            "ms2_parallel_workers": float(worker_count if use_parallel else 1),
            "ms2_total_units": float(total_windows),
        }
        _emit_progress(
            progress_callback,
            {
                "stage": "start",
                "pct": 0.0,
                "done_units": 0,
                "total_units": total_windows,
                "unit_kind": "windows",
                "done_tracks": 0,
                "total_tracks": None,
                "track_kind": "dictionary_entries",
            },
        )

        if self.config.codec_variant == "exact_track":
            codec = self.new_window_codec()
            if not isinstance(codec, DIAWindowExactTrackCodec):
                raise ValueError("Expected exact-track codec for exact-track variant")

            stage_t0 = time.perf_counter()
            if use_parallel:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    artifacts = [None] * total_windows
                    future_map = {
                        executor.submit(self._build_exact_track_window_record, item): idx
                        for idx, item in enumerate(sorted_items)
                    }
                    done_windows = 0
                    done_tracks = 0
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        artifact = future.result()
                        artifacts[idx] = artifact
                        done_windows += 1
                        done_tracks += _extract_track_units_from_record(artifact["record"])
                        _emit_progress(
                            progress_callback,
                            {
                                "stage": "build_window_records",
                                "pct": float(70.0 * float(done_windows) / float(max(total_windows, 1))),
                                "done_units": int(done_windows),
                                "total_units": int(total_windows),
                                "unit_kind": "windows",
                                "done_tracks": int(done_tracks),
                                "total_tracks": None,
                                "track_kind": "dictionary_entries",
                            },
                        )
            else:
                artifacts = []
                done_tracks = 0
                for idx, item in enumerate(sorted_items, start=1):
                    artifact = self._build_exact_track_window_record(item)
                    artifacts.append(artifact)
                    done_tracks += _extract_track_units_from_record(artifact["record"])
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": "build_window_records",
                            "pct": float(70.0 * float(idx) / float(max(total_windows, 1))),
                            "done_units": int(idx),
                            "total_units": int(total_windows),
                            "unit_kind": "windows",
                            "done_tracks": int(done_tracks),
                            "total_tracks": None,
                            "track_kind": "dictionary_entries",
                        },
                    )
            stage_timings["ms2_build_window_records_s"] = time.perf_counter() - stage_t0

            encoded_windows = []
            section_records = []
            total_raw = 0
            total_tracks = 0
            for artifact in artifacts:
                section_records.append({"window_key": artifact["window_key"], "record": artifact["record"]})
                total_raw += int(artifact["record"]["raw_bytes"])
                total_tracks += _extract_track_units_from_record(artifact["record"])
            _emit_progress(
                progress_callback,
                {
                    "stage": "build_window_records_done",
                    "pct": 70.0,
                    "done_units": int(total_windows),
                    "total_units": int(total_windows),
                    "unit_kind": "windows",
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                    "track_kind": "dictionary_entries",
                },
            )

            prefer_section = (
                self.config.dia_exact_track_mode == "prefer_section"
                and len(sorted_items) >= int(self.config.dia_section_parallel_min_windows)
            )
            if prefer_section:
                stage_t0 = time.perf_counter()
                section_blob = self._encode_exact_track_dia_section(codec, section_records)
                stage_timings["ms2_section_aggregate_s"] = time.perf_counter() - stage_t0
                stage_timings["ms2_total_encode_s"] = time.perf_counter() - total_t0
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "section_aggregate_done",
                        "pct": 100.0,
                        "done_units": int(total_windows),
                        "total_units": int(total_windows),
                        "unit_kind": "windows",
                        "done_tracks": int(total_tracks),
                        "total_tracks": int(total_tracks),
                        "track_kind": "dictionary_entries",
                    },
                )
                return {
                    "windows": [],
                    "container_blob": None,
                    "section_blob": section_blob,
                    "container_backend": None,
                    "exact_track_container_mode": "section_aggregate_preferred",
                    "exact_track_parallel_workers": worker_count if use_parallel else 1,
                    "raw_bytes": total_raw,
                    "compressed_bytes": len(section_blob),
                    "compression_ratio": total_raw / len(section_blob) if len(section_blob) else 0.0,
                    "stage_timings_s": stage_timings,
                }

            def _encode_exact_track_window_artifact(artifact: Dict) -> Dict:
                local_codec = self.new_window_codec()
                if not isinstance(local_codec, DIAWindowExactTrackCodec):
                    raise ValueError("Expected exact-track codec for exact-track payload encode")
                payload, meta = local_codec.encode_from_record(artifact["record"])
                return {
                    "window_key": artifact["window_key"],
                    "payload": payload,
                    "meta": meta,
                    "n_scans": artifact["n_scans"],
                    "_track_units": _extract_track_units_from_record(artifact["record"]),
                }

            stage_t0 = time.perf_counter()
            done_tracks = 0
            if use_parallel:
                encoded_windows = [None] * total_windows
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    future_map = {
                        executor.submit(_encode_exact_track_window_artifact, artifact): idx
                        for idx, artifact in enumerate(artifacts)
                    }
                    done_windows = 0
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        item = future.result()
                        done_tracks += int(item.pop("_track_units", 0))
                        encoded_windows[idx] = item
                        done_windows += 1
                        _emit_progress(
                            progress_callback,
                            {
                                "stage": "encode_window_payloads",
                                "pct": float(70.0 + 25.0 * float(done_windows) / float(max(total_windows, 1))),
                                "done_units": int(done_windows),
                                "total_units": int(total_windows),
                                "unit_kind": "windows",
                                "done_tracks": int(done_tracks),
                                "total_tracks": int(total_tracks),
                                "track_kind": "dictionary_entries",
                            },
                        )
            else:
                encoded_windows = []
                for idx, artifact in enumerate(artifacts, start=1):
                    item = _encode_exact_track_window_artifact(artifact)
                    done_tracks += int(item.pop("_track_units", 0))
                    encoded_windows.append(item)
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": "encode_window_payloads",
                            "pct": float(70.0 + 25.0 * float(idx) / float(max(total_windows, 1))),
                            "done_units": int(idx),
                            "total_units": int(total_windows),
                            "unit_kind": "windows",
                            "done_tracks": int(done_tracks),
                            "total_tracks": int(total_tracks),
                            "track_kind": "dictionary_entries",
                        },
                    )
            stage_timings["ms2_encode_window_payloads_s"] = time.perf_counter() - stage_t0

            raw_sum = sum(int(item["meta"]["compressed_bytes"]) for item in encoded_windows)
            best_mode = "per_window"
            best_comp = raw_sum
            container_blob = None
            section_blob = None

            stage_t0 = time.perf_counter()
            candidate_outer = self._pack_outer_container("DIA", encoded_windows, "payload")
            stage_timings["ms2_pack_outer_container_s"] = time.perf_counter() - stage_t0
            if len(candidate_outer) < best_comp:
                best_mode = "outer_container"
                best_comp = len(candidate_outer)
                container_blob = candidate_outer

            stage_t0 = time.perf_counter()
            candidate_section = self._encode_exact_track_dia_section(codec, section_records)
            stage_timings["ms2_section_aggregate_s"] = time.perf_counter() - stage_t0
            if len(candidate_section) < best_comp:
                best_mode = "section_aggregate"
                best_comp = len(candidate_section)
                container_blob = None
                section_blob = candidate_section

            _emit_progress(
                progress_callback,
                {
                    "stage": "encode_done",
                    "pct": 100.0,
                    "done_units": int(total_windows),
                    "total_units": int(total_windows),
                    "unit_kind": "windows",
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                    "track_kind": "dictionary_entries",
                },
            )
            stage_timings["ms2_total_encode_s"] = time.perf_counter() - total_t0
            return {
                "windows": [] if section_blob is not None else encoded_windows,
                "container_blob": container_blob,
                "section_blob": section_blob,
                "container_backend": self.config.backend if container_blob is not None else None,
                "exact_track_container_mode": best_mode,
                "exact_track_parallel_workers": worker_count if use_parallel else 1,
                "raw_bytes": total_raw,
                "compressed_bytes": best_comp,
                "compression_ratio": total_raw / best_comp if best_comp else 0.0,
                "stage_timings_s": stage_timings,
            }

        stage_t0 = time.perf_counter()
        if use_parallel:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                encoded_windows = [None] * total_windows
                future_map = {
                    executor.submit(self._encode_dia_window_payload, item): idx
                    for idx, item in enumerate(sorted_items)
                }
                done_windows = 0
                done_tracks = 0
                for future in as_completed(future_map):
                    idx = future_map[future]
                    item = future.result()
                    encoded_windows[idx] = item
                    done_windows += 1
                    done_tracks += _extract_track_units_from_meta(item["meta"])
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": "encode_window_payloads",
                            "pct": float(95.0 * float(done_windows) / float(max(total_windows, 1))),
                            "done_units": int(done_windows),
                            "total_units": int(total_windows),
                            "unit_kind": "windows",
                            "done_tracks": int(done_tracks),
                            "total_tracks": None,
                            "track_kind": "dictionary_entries",
                        },
                    )
        else:
            encoded_windows = []
            done_tracks = 0
            for idx, item in enumerate(sorted_items, start=1):
                encoded = self._encode_dia_window_payload(item)
                encoded_windows.append(encoded)
                done_tracks += _extract_track_units_from_meta(encoded["meta"])
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "encode_window_payloads",
                        "pct": float(95.0 * float(idx) / float(max(total_windows, 1))),
                        "done_units": int(idx),
                        "total_units": int(total_windows),
                        "unit_kind": "windows",
                        "done_tracks": int(done_tracks),
                        "total_tracks": None,
                        "track_kind": "dictionary_entries",
                        },
                    )
        stage_timings["ms2_encode_window_payloads_s"] = time.perf_counter() - stage_t0

        total_raw = sum(int(item["meta"]["raw_bytes"]) for item in encoded_windows)
        total_tracks = sum(_extract_track_units_from_meta(item["meta"]) for item in encoded_windows)
        container_blob = None
        total_comp = sum(int(item["meta"]["compressed_bytes"]) for item in encoded_windows)
        _emit_progress(
            progress_callback,
            {
                "stage": "encode_done",
                "pct": 100.0,
                "done_units": int(total_windows),
                "total_units": int(total_windows),
                "unit_kind": "windows",
                "done_tracks": int(total_tracks),
                "total_tracks": int(total_tracks),
                "track_kind": "dictionary_entries",
            },
        )
        stage_timings["ms2_total_encode_s"] = time.perf_counter() - total_t0
        return {
            "windows": encoded_windows,
            "container_blob": container_blob,
            "container_backend": self.config.backend if container_blob is not None else None,
            "raw_bytes": total_raw,
            "compressed_bytes": total_comp,
            "compression_ratio": total_raw / total_comp if total_comp else 0.0,
            "stage_timings_s": stage_timings,
        }

    def decode_dia_windows(self, encoded):
        if encoded.get("section_blob") is not None:
            return self._decode_exact_track_dia_section(encoded["section_blob"])
        decoded = {}
        items = encoded["windows"]
        if encoded.get("container_blob") is not None:
            items = self._unpack_outer_container(encoded["container_blob"], "payload")
        worker_count = max(1, min(int(self.config.dia_section_workers), len(items)))
        if worker_count > 1 and len(items) > 1:
            results = [None] * len(items)
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {}
                for idx, item in enumerate(items):
                    def _decode_item(item=item):
                        codec = self.new_window_codec()
                        scans, _, _ = codec.decode(item["payload"])
                        return tuple(item["window_key"]), scans
                    future_map[executor.submit(_decode_item)] = idx
                for future in as_completed(future_map):
                    results[future_map[future]] = future.result()
            for result in results:
                if result is None:
                    raise RuntimeError("MS2 DIA window decode job did not finish")
                window_key, scans = result
                decoded[window_key] = scans
        else:
            for item in items:
                codec = self.new_window_codec()
                scans, _, _ = codec.decode(item["payload"])
                decoded[tuple(item["window_key"])] = scans
        return decoded

    def lazy_decode_dia_windows(self, encoded):
        """Return a lazy window-key provider for reconstruction-time decoding."""
        return _LazyDIAWindowDecoded(self, encoded)

    def encode_dda_blocks(self, scans: List[dict], progress_callback: Callable[[Dict], None] | None = None):
        total_t0 = time.perf_counter()
        cfg = self.config
        tasks = []
        for start in range(0, len(scans), cfg.dda_block_size):
            block = scans[start:start + cfg.dda_block_size]
            if block:
                tasks.append((start, block))

        total_blocks = int(len(tasks))
        worker_count = max(1, min(int(cfg.dia_section_workers), len(tasks)))
        use_parallel = worker_count > 1 and len(tasks) > 1
        stage_timings = {
            "ms2_prepare_dda_blocks_s": time.perf_counter() - total_t0,
            "ms2_parallel_workers": float(worker_count if use_parallel else 1),
            "ms2_total_units": float(total_blocks),
        }
        _emit_progress(
            progress_callback,
            {
                "stage": "start",
                "pct": 0.0,
                "done_units": 0,
                "total_units": total_blocks,
                "unit_kind": "blocks",
                "done_tracks": 0,
                "total_tracks": None,
                "track_kind": "dictionary_entries",
            },
        )

        if self.config.codec_variant == "exact_track":
            codec = self.new_window_codec()
            if not isinstance(codec, DIAWindowExactTrackCodec):
                raise ValueError("Expected exact-track codec for exact-track variant")
            stage_t0 = time.perf_counter()
            if use_parallel:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    artifacts = [None] * total_blocks
                    future_map = {
                        executor.submit(self._build_exact_track_dda_block_record, task): idx
                        for idx, task in enumerate(tasks)
                    }
                    done_blocks = 0
                    done_tracks = 0
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        artifact = future.result()
                        artifacts[idx] = artifact
                        done_blocks += 1
                        done_tracks += _extract_track_units_from_record(artifact["record"])
                        _emit_progress(
                            progress_callback,
                            {
                                "stage": "build_block_records",
                                "pct": float(70.0 * float(done_blocks) / float(max(total_blocks, 1))),
                                "done_units": int(done_blocks),
                                "total_units": int(total_blocks),
                                "unit_kind": "blocks",
                                "done_tracks": int(done_tracks),
                                "total_tracks": None,
                                "track_kind": "dictionary_entries",
                            },
                        )
            else:
                artifacts = []
                done_tracks = 0
                for idx, task in enumerate(tasks, start=1):
                    artifact = self._build_exact_track_dda_block_record(task)
                    artifacts.append(artifact)
                    done_tracks += _extract_track_units_from_record(artifact["record"])
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": "build_block_records",
                            "pct": float(70.0 * float(idx) / float(max(total_blocks, 1))),
                            "done_units": int(idx),
                            "total_units": int(total_blocks),
                            "unit_kind": "blocks",
                            "done_tracks": int(done_tracks),
                            "total_tracks": None,
                            "track_kind": "dictionary_entries",
                        },
                    )
            stage_timings["ms2_build_block_records_s"] = time.perf_counter() - stage_t0

            total_tracks = sum(_extract_track_units_from_record(artifact["record"]) for artifact in artifacts)
            _emit_progress(
                progress_callback,
                {
                    "stage": "build_block_records_done",
                    "pct": 70.0,
                    "done_units": int(total_blocks),
                    "total_units": int(total_blocks),
                    "unit_kind": "blocks",
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                    "track_kind": "dictionary_entries",
                },
            )

            prefer_section = self.config.dia_exact_track_mode == "prefer_section"
            if prefer_section:
                stage_t0 = time.perf_counter()
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "section_aggregate",
                        "pct": 98.0,
                        "done_units": int(total_blocks),
                        "total_units": int(total_blocks),
                        "unit_kind": "blocks",
                        "done_tracks": int(total_tracks),
                        "total_tracks": int(total_tracks),
                        "track_kind": "dictionary_entries",
                    },
                )
                section_records = [
                    {"block_start": int(artifact["block_start"]), "record": artifact["record"]}
                    for artifact in artifacts
                ]
                section_blob = self._encode_exact_track_dda_section(codec, section_records)
                stage_timings["ms2_section_aggregate_s"] = time.perf_counter() - stage_t0
                stage_timings["ms2_total_encode_s"] = time.perf_counter() - total_t0
                _emit_progress(
                    progress_callback,
                    {
                        "stage": "section_aggregate_done",
                        "pct": 100.0,
                        "done_units": int(total_blocks),
                        "total_units": int(total_blocks),
                        "unit_kind": "blocks",
                        "done_tracks": int(total_tracks),
                        "total_tracks": int(total_tracks),
                        "track_kind": "dictionary_entries",
                    },
                )
                total_raw = sum(int(artifact["record"]["raw_bytes"]) for artifact in artifacts)
                return {
                    "blocks": [],
                    "container_blob": None,
                    "section_blob": section_blob,
                    "container_backend": None,
                    "exact_track_container_mode": "section_aggregate_preferred",
                    "raw_bytes": total_raw,
                    "compressed_bytes": len(section_blob),
                    "compression_ratio": total_raw / len(section_blob) if len(section_blob) else 0.0,
                    "stage_timings_s": stage_timings,
                }

            def _encode_exact_track_dda_artifact(artifact: Dict) -> Dict:
                local_codec = self.new_window_codec()
                if not isinstance(local_codec, DIAWindowExactTrackCodec):
                    raise ValueError("Expected exact-track codec for exact-track payload encode")
                payload, meta = local_codec.encode_from_record(artifact["record"])
                return {
                    "block_start": int(artifact["block_start"]),
                    "payload": payload,
                    "meta": meta,
                    "n_scans": artifact["n_scans"],
                    "_track_units": _extract_track_units_from_record(artifact["record"]),
                }

            stage_t0 = time.perf_counter()
            blocks = []
            done_tracks = 0
            if use_parallel:
                blocks = [None] * total_blocks
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    future_map = {
                        executor.submit(_encode_exact_track_dda_artifact, artifact): idx
                        for idx, artifact in enumerate(artifacts)
                    }
                    done_blocks = 0
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        item = future.result()
                        done_tracks += int(item.pop("_track_units", 0))
                        blocks[idx] = item
                        done_blocks += 1
                        _emit_progress(
                            progress_callback,
                            {
                                "stage": "encode_block_payloads",
                                "pct": float(70.0 + 25.0 * float(done_blocks) / float(max(total_blocks, 1))),
                                "done_units": int(done_blocks),
                                "total_units": int(total_blocks),
                                "unit_kind": "blocks",
                                "done_tracks": int(done_tracks),
                                "total_tracks": int(total_tracks),
                                "track_kind": "dictionary_entries",
                            },
                        )
            else:
                blocks = []
                for idx, artifact in enumerate(artifacts, start=1):
                    item = _encode_exact_track_dda_artifact(artifact)
                    done_tracks += int(item.pop("_track_units", 0))
                    blocks.append(item)
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": "encode_block_payloads",
                            "pct": float(70.0 + 25.0 * float(idx) / float(max(total_blocks, 1))),
                            "done_units": int(idx),
                            "total_units": int(total_blocks),
                            "unit_kind": "blocks",
                            "done_tracks": int(done_tracks),
                            "total_tracks": int(total_tracks),
                            "track_kind": "dictionary_entries",
                        },
                    )
            stage_timings["ms2_encode_block_payloads_s"] = time.perf_counter() - stage_t0
        else:
            stage_t0 = time.perf_counter()
            if use_parallel:
                with ThreadPoolExecutor(max_workers=worker_count) as executor:
                    blocks = [None] * total_blocks
                    future_map = {
                        executor.submit(self._encode_dda_block_payload, task): idx
                        for idx, task in enumerate(tasks)
                    }
                    done_blocks = 0
                    done_tracks = 0
                    for future in as_completed(future_map):
                        idx = future_map[future]
                        item = future.result()
                        blocks[idx] = item
                        done_blocks += 1
                        done_tracks += _extract_track_units_from_meta(item["meta"])
                        _emit_progress(
                            progress_callback,
                            {
                                "stage": "encode_block_payloads",
                                "pct": float(95.0 * float(done_blocks) / float(max(total_blocks, 1))),
                                "done_units": int(done_blocks),
                                "total_units": int(total_blocks),
                                "unit_kind": "blocks",
                                "done_tracks": int(done_tracks),
                                "total_tracks": None,
                                "track_kind": "dictionary_entries",
                            },
                        )
            else:
                blocks = []
                done_tracks = 0
                for idx, task in enumerate(tasks, start=1):
                    block = self._encode_dda_block_payload(task)
                    blocks.append(block)
                    done_tracks += _extract_track_units_from_meta(block["meta"])
                    _emit_progress(
                        progress_callback,
                        {
                            "stage": "encode_block_payloads",
                            "pct": float(95.0 * float(idx) / float(max(total_blocks, 1))),
                            "done_units": int(idx),
                            "total_units": int(total_blocks),
                            "unit_kind": "blocks",
                            "done_tracks": int(done_tracks),
                            "total_tracks": None,
                            "track_kind": "dictionary_entries",
                        },
                    )
            stage_timings["ms2_encode_block_payloads_s"] = time.perf_counter() - stage_t0

        total_raw = sum(int(item["meta"]["raw_bytes"]) for item in blocks)
        total_tracks = sum(_extract_track_units_from_meta(item["meta"]) for item in blocks)
        if self.config.codec_variant == "exact_track":
            raw_sum = sum(int(item["meta"]["compressed_bytes"]) for item in blocks)
            best_mode = "per_block"
            total_comp = raw_sum
            container_blob = None
            section_blob = None
            stage_t0 = time.perf_counter()
            _emit_progress(
                progress_callback,
                {
                    "stage": "pack_outer_container",
                    "pct": 96.0,
                    "done_units": int(total_blocks),
                    "total_units": int(total_blocks),
                    "unit_kind": "blocks",
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                    "track_kind": "dictionary_entries",
                },
            )
            candidate_blob = self._pack_outer_container("DDA", blocks, "payload")
            stage_timings["ms2_pack_outer_container_s"] = time.perf_counter() - stage_t0
            if len(candidate_blob) < raw_sum:
                best_mode = "outer_container"
                container_blob = candidate_blob
                total_comp = len(container_blob)
            stage_t0 = time.perf_counter()
            _emit_progress(
                progress_callback,
                {
                    "stage": "section_aggregate",
                    "pct": 98.0,
                    "done_units": int(total_blocks),
                    "total_units": int(total_blocks),
                    "unit_kind": "blocks",
                    "done_tracks": int(total_tracks),
                    "total_tracks": int(total_tracks),
                    "track_kind": "dictionary_entries",
                },
            )
            section_records = [
                {"block_start": int(artifact["block_start"]), "record": artifact["record"]}
                for artifact in artifacts
            ]
            candidate_section = self._encode_exact_track_dda_section(codec, section_records)
            stage_timings["ms2_section_aggregate_s"] = time.perf_counter() - stage_t0
            if len(candidate_section) < total_comp:
                best_mode = "section_aggregate"
                container_blob = None
                section_blob = candidate_section
                total_comp = len(section_blob)
        else:
            container_blob = None
            section_blob = None
            best_mode = None
            total_comp = sum(int(item["meta"]["compressed_bytes"]) for item in blocks)
        _emit_progress(
            progress_callback,
            {
                "stage": "encode_done",
                "pct": 100.0,
                "done_units": int(total_blocks),
                "total_units": int(total_blocks),
                "unit_kind": "blocks",
                "done_tracks": int(total_tracks),
                "total_tracks": int(total_tracks),
                "track_kind": "dictionary_entries",
            },
        )
        stage_timings["ms2_total_encode_s"] = time.perf_counter() - total_t0
        return {
            "blocks": [] if section_blob is not None else blocks,
            "container_blob": container_blob,
            "section_blob": section_blob,
            "container_backend": self.config.backend if container_blob is not None else None,
            "exact_track_container_mode": best_mode,
            "raw_bytes": total_raw,
            "compressed_bytes": total_comp,
            "compression_ratio": total_raw / total_comp if total_comp else 0.0,
            "stage_timings_s": stage_timings,
        }

    def decode_dda_blocks(self, encoded):
        if encoded.get("section_blob") is not None:
            return self._decode_exact_track_dda_section(encoded["section_blob"])
        items = encoded["blocks"]
        if encoded.get("container_blob") is not None:
            items = self._unpack_outer_container(encoded["container_blob"], "payload")
        worker_count = max(1, min(int(self.config.dia_section_workers), len(items)))
        decoded = []
        if worker_count > 1 and len(items) > 1:
            results = [None] * len(items)
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                future_map = {}
                for idx, item in enumerate(items):
                    def _decode_item(item=item):
                        codec = self.new_window_codec()
                        scans, _, _ = codec.decode(item["payload"])
                        return scans
                    future_map[executor.submit(_decode_item)] = idx
                for future in as_completed(future_map):
                    results[future_map[future]] = future.result()
            for scans in results:
                if scans is None:
                    raise RuntimeError("MS2 DDA block decode job did not finish")
                decoded.extend(scans)
        else:
            for item in items:
                codec = self.new_window_codec()
                scans, _, _ = codec.decode(item["payload"])
                decoded.extend(scans)
        return decoded

    def iter_decode_dda_blocks(self, encoded):
        """Yield decoded DDA MS2 scans in archive order with bounded block state."""
        if encoded.get("section_blob") is not None:
            yield from self._iter_decode_exact_track_dda_section_scan_arrays(encoded["section_blob"])
            return
        items = encoded["blocks"]
        if encoded.get("container_blob") is not None:
            items = self._unpack_outer_container(encoded["container_blob"], "payload")
        for item in items:
            codec = self.new_window_codec()
            scans, _, _ = codec.decode(item["payload"])
            for scan in scans:
                yield np.asarray(scan["mz_array"], dtype=np.float64), np.asarray(scan["intensity_array"], dtype=np.float64)
