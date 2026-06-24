from __future__ import annotations

import csv
import gc
import importlib.util
import json
import statistics
import sys
import time
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from pyteomics import mzml


REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_PARENT = REPO_ROOT.parent
if str(REPO_PARENT) not in sys.path:
    sys.path.insert(0, str(REPO_PARENT))
if REPO_ROOT.name != "TrackCodec" and "TrackCodec" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "TrackCodec",
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot bootstrap TrackCodec package from {REPO_ROOT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["TrackCodec"] = module
    spec.loader.exec_module(module)

from TrackCodec.production.common.io import detect_file_type
from TrackCodec.production.common.tracking import CompactIslandTracks
from TrackCodec.production.mzml.archive_codec import build_ms1_tracks
from TrackCodec.production.ms1.cross_scan_codec import CrossScanMS1Codec, _pack_segments, _unpack_segments
from TrackCodec.production.ms1.unified_codec import MS1Codec
from TrackCodec.production.ms2.codec import DIAWindowMS2Codec, MS2ModeConfig


MZ_LOSSLESS_EPS = 5.1e-7

FULL_MS1_KWARGS = {
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
    "omit_array_starts": True,
    "omit_n_points": True,
    "preserve_full_scan": True,
    "track_prepare_workers": 1,
    "track_prepare_lookahead": 1,
    "encode_section_workers": 1,
}


@dataclass(frozen=True)
class Variant:
    variant_id: str
    variant_name: str
    intensity_mode: str
    backend: str
    mz_precision: int
    disable_sidecar: bool = False


@dataclass
class LoadedScans:
    file_path: Path
    file_type: str
    ms1_scans: list[dict]
    ms2_by_window: dict[tuple[float, float, float], list[dict]]
    dda_ms2_scans: list[dict]
    raw_ms1_bytes: int
    raw_ms2_bytes: int


A0_VARIANT = Variant(
    "A0",
    "Full",
    "szdpd_xdelta_equalfidelity",
    "zstd-9",
    6,
    False,
)

A7_VARIANT = Variant(
    "A7",
    "intensity codec isolation",
    "stackzdpd_passthrough",
    "zstd-9",
    6,
    False,
)

COMPONENT_VARIANTS = [
    Variant("A0", "Full", "szdpd_xdelta_equalfidelity", "zstd-9", 6, False),
    Variant("A1", "w/o cross-scan", "szdpd", "zstd-9", 6, False),
    Variant("A2", "w/o equal-fid", "szdpd_xdelta", "zstd-9", 6, False),
    Variant("A3", "lossless cross-scan", "strict_lossless_xdelta", "zstd-9", 6, False),
    Variant("A4", "w/o sidecar", "szdpd_xdelta_equalfidelity", "zstd-9", 6, True),
    Variant("A5", "zlib backend", "szdpd_xdelta_equalfidelity", "zlib", 6, False),
    Variant("A6", "fast backend", "szdpd_xdelta_equalfidelity", "zstd-3", 6, False),
]


def infer_vendor(path: str | Path) -> str:
    name = Path(path).name.lower()
    if any(token in name for token in ("ttof", "tripletof", "sciex")):
        return "SCIEX"
    if any(token in name for token in ("q-exactive", "qe-hfx", "orbitrap", "exactive", "e480", "file13", "file14")):
        return "Thermo"
    if any(token in name for token in ("agilent", "set 1_")):
        return "Agilent"
    if any(token in name for token in ("timstof", "tims")):
        return "Bruker"
    return "unknown"


def load_manifest(path: Path, *, max_files: int = 0) -> list[dict]:
    if path.suffix.lower() == ".csv":
        with path.open("r", newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.DictReader(handle))
    else:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_rows = data.get("files", data) if isinstance(data, dict) else data

    rows: list[dict] = []
    for idx, row in enumerate(raw_rows, start=1):
        if isinstance(row, str):
            mzml_path = Path(row)
            item = {"file": mzml_path.name, "path": str(mzml_path)}
        else:
            mzml_path = Path(str(row.get("path") or row.get("file_path") or row.get("mzml_path") or row.get("file") or ""))
            item = dict(row)
            item["file"] = str(row.get("file") or mzml_path.name)
            item["path"] = str(mzml_path)
        if not mzml_path.exists():
            raise FileNotFoundError(f"Manifest entry does not exist: {mzml_path}")
        item["index"] = int(item.get("index") or idx)
        item["resolved_path"] = str(mzml_path.resolve())
        item["size_bytes"] = int(mzml_path.stat().st_size)
        item["vendor"] = item.get("vendor") or infer_vendor(mzml_path)
        rows.append(item)

    rows.sort(key=lambda item: int(item.get("size_bytes", 0)), reverse=True)
    if max_files and int(max_files) > 0:
        rows = rows[: int(max_files)]
    return rows


def _dia_window_key(spec) -> tuple[float, float, float]:
    isolation = spec.get("precursorList", {}).get("precursor", [{}])[0].get("isolationWindow", {})
    target = float(isolation.get("isolation window target m/z", 0.0) or 0.0)
    lower = float(isolation.get("isolation window lower offset", 0.0) or 0.0)
    upper = float(isolation.get("isolation window upper offset", 0.0) or 0.0)
    return (round(target, 4), round(lower, 4), round(upper, 4))


def _scan_rt(spec) -> float:
    return float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0) or 0.0)


def _precursor_target_mz(spec) -> float:
    isolation = spec.get("precursorList", {}).get("precursor", [{}])[0].get("isolationWindow", {})
    return round(float(isolation.get("isolation window target m/z", 0.0) or 0.0), 4)


def load_limited_scans(
    mzml_path: str | Path,
    *,
    max_ms1_scans: int = 0,
    max_ms2_scans: int = 0,
) -> LoadedScans:
    path = Path(mzml_path)
    file_type = detect_file_type(str(path))
    ms1_scans: list[dict] = []
    ms2_by_window: dict[tuple[float, float, float], list[dict]] = {}
    dda_ms2_scans: list[dict] = []
    raw_ms1_bytes = 0
    raw_ms2_bytes = 0
    limit_ms1 = int(max_ms1_scans) > 0
    limit_ms2 = int(max_ms2_scans) > 0
    skip_ms2 = int(max_ms2_scans) < 0

    with mzml.MzML(
        str(path),
        read_schema=False,
        iterative=True,
        use_index=False,
        huge_tree=True,
        decode_binary=True,
    ) as reader:
        ms1_idx = 0
        ms2_idx = 0
        for spec in reader:
            ms_level = int(spec.get("ms level", 0) or 0)
            if ms_level == 1:
                if limit_ms1 and len(ms1_scans) >= int(max_ms1_scans):
                    if skip_ms2 or (limit_ms2 and ms2_idx >= int(max_ms2_scans)):
                        break
                    continue
                mz_arr = np.asarray(spec.get("m/z array", []), dtype=np.float64)
                int_arr = np.asarray(spec.get("intensity array", []), dtype=np.float64)
                ms1_scans.append(
                    {
                        "scan_idx": ms1_idx,
                        "rt": _scan_rt(spec),
                        "mz_array": mz_arr,
                        "intensity_array": int_arr,
                    }
                )
                raw_ms1_bytes += int(len(mz_arr)) * 16
                ms1_idx += 1
            elif ms_level == 2:
                if skip_ms2:
                    continue
                if limit_ms2 and ms2_idx >= int(max_ms2_scans):
                    if limit_ms1 and len(ms1_scans) >= int(max_ms1_scans):
                        break
                    continue
                mz_arr = np.asarray(spec.get("m/z array", []), dtype=np.float64)
                int_arr = np.asarray(spec.get("intensity array", []), dtype=np.float64)
                raw_ms2_bytes += int(len(mz_arr)) * 16
                if file_type == "DIA":
                    key = _dia_window_key(spec)
                    ms2_by_window.setdefault(key, []).append(
                        {
                            "scan_idx": ms2_idx,
                            "rt": _scan_rt(spec),
                            "mz_array": mz_arr,
                            "intensity_array": int_arr,
                            "target_mz": float(key[0]),
                            "window_key": key,
                        }
                    )
                else:
                    dda_ms2_scans.append(
                        {
                            "scan_idx": ms2_idx,
                            "rt": _scan_rt(spec),
                            "mz_array": mz_arr,
                            "intensity_array": int_arr,
                            "target_mz": _precursor_target_mz(spec),
                            "original_id": spec.get("id"),
                        }
                    )
                ms2_idx += 1
            if (limit_ms1 and len(ms1_scans) >= int(max_ms1_scans)) and (
                skip_ms2 or (limit_ms2 and ms2_idx >= int(max_ms2_scans))
            ):
                break

    for scans in ms2_by_window.values():
        scans.sort(key=lambda item: item["rt"])
    return LoadedScans(path, file_type, ms1_scans, ms2_by_window, dda_ms2_scans, raw_ms1_bytes, raw_ms2_bytes)


def load_ms1_scans(mzml_path: str | Path, *, max_ms1_scans: int = 0) -> LoadedScans:
    return load_limited_scans(mzml_path, max_ms1_scans=max_ms1_scans, max_ms2_scans=-1)


def build_tracks_for_ablation(ms1_scans: list[dict]):
    return build_ms1_tracks(
        ms1_scans,
        island_workers=1,
        compact=True,
        drop_islands_in_compact=False,
    )


def make_ms1_codec(variant: Variant) -> MS1Codec:
    kwargs = dict(FULL_MS1_KWARGS)
    if variant.disable_sidecar:
        kwargs["preserve_full_scan"] = False
        kwargs["retain_zero_intensity_mz"] = False
    return MS1Codec(
        mz_precision=int(variant.mz_precision),
        intensity_mode=str(variant.intensity_mode),
        backend=str(variant.backend),
        **kwargs,
    )


def make_ms2_codec(file_type: str, *, backend: str, mz_precision: int) -> DIAWindowMS2Codec:
    overrides = {
        "backend": str(backend),
        "mz_precision": int(mz_precision),
        "dia_section_workers": 1,
    }
    if file_type == "DIA":
        cfg = MS2ModeConfig.exact_track_dia_current(**overrides)
    else:
        cfg = MS2ModeConfig.exact_track_dda_current(**overrides)
    return DIAWindowMS2Codec(cfg)


def _estimate_external_full_scan_sidecar_bytes(loaded: LoadedScans, variant: Variant) -> int:
    kwargs = dict(FULL_MS1_KWARGS)
    kwargs["preserve_full_scan"] = True
    kwargs["retain_zero_intensity_mz"] = True
    sidecar_codec = CrossScanMS1Codec(
        mz_precision=int(variant.mz_precision),
        intensity_mode="szdpd_xdelta_equalfidelity",
        backend=str(variant.backend),
        **kwargs,
    )
    sidecar_segments, sidecar_meta = sidecar_codec._encode_full_scan_sidecar(loaded.ms1_scans)
    sidecar_blob = _pack_segments(
        {
            "format": "ms1_full_scan_sidecar_ablation",
            "backend": str(variant.backend),
            "mz_precision_decimals": int(variant.mz_precision),
            "retain_zero_intensity_mz": True,
            "segment_meta": sidecar_meta,
        },
        sidecar_segments,
    )
    return int(len(sidecar_blob))


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


def _compare_baseline_ms1_roundtrip(codec, payload, metadata, tracks, ms1_scans):
    t0 = time.perf_counter()
    decoded_islands, _, decode_time = codec.decode(payload, metadata)
    errors = _compare_track_domain_roundtrip_streaming(
        tracks,
        iter(decoded_islands),
        ms1_scans=ms1_scans,
        validate_mz=True,
    )
    errors["decode_time_s"] = float(time.perf_counter() - t0)
    errors["track_decode_time_s"] = float(decode_time)
    errors["validation_mode"] = "baseline_track_domain"
    errors["dense_reconstruction_materialized"] = False
    errors["decoded_islands_materialized"] = True
    return errors


def _compare_ms1_codec_roundtrip_low_memory(codec, payload, metadata, tracks, ms1_scans):
    """Validate MS1 sidecar m/z and decoded track islands without dense reconstruction."""
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
        errors = _compare_track_domain_roundtrip_streaming(
            tracks,
            iter(decoded_islands),
            ms1_scans=ms1_scans,
            validate_mz=not omit_island_mz,
        )
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
    errors["dense_reconstruction_materialized"] = False
    errors["decoded_islands_materialized"] = bool(decoded_islands_materialized)
    gc.collect()
    return errors


def run_ms1_variant(loaded: LoadedScans, tracks, variant: Variant) -> dict:
    if not loaded.ms1_scans:
        return empty_stats()
    codec = make_ms1_codec(variant)
    t0 = time.perf_counter()
    payload, meta = codec.encode(tracks, loaded.ms1_scans)
    encode_time = time.perf_counter() - t0
    impl = getattr(codec, "impl", codec)
    if isinstance(impl, CrossScanMS1Codec):
        errors = _compare_ms1_codec_roundtrip_low_memory(
            codec,
            payload,
            meta,
            tracks,
            loaded.ms1_scans,
        )
    else:
        errors = _compare_baseline_ms1_roundtrip(
            codec,
            payload,
            meta,
            tracks,
            loaded.ms1_scans,
        )
    payload_only_compressed_bytes = int(meta.get("compressed_bytes", len(payload)))
    payload_only_compression_ratio = float(meta.get("compression_ratio", 0.0))
    sidecar_in_codec = bool(getattr(codec, "preserve_full_scan", False))
    external_sidecar_bytes = 0
    if sidecar_in_codec:
        fair_compressed_bytes = payload_only_compressed_bytes
    else:
        external_sidecar_bytes = _estimate_external_full_scan_sidecar_bytes(loaded, variant)
        fair_compressed_bytes = payload_only_compressed_bytes + external_sidecar_bytes
    raw_bytes = int(meta.get("raw_bytes", loaded.raw_ms1_bytes))
    return {
        "raw_bytes": raw_bytes,
        "compressed_bytes": fair_compressed_bytes,
        "compression_ratio": (raw_bytes / fair_compressed_bytes) if fair_compressed_bytes else None,
        "payload_only_compressed_bytes": payload_only_compressed_bytes,
        "payload_only_compression_ratio": payload_only_compression_ratio,
        "external_sidecar_compressed_bytes": external_sidecar_bytes,
        "sidecar_in_codec": sidecar_in_codec,
        "sidecar_enabled": True,
        "roundtrip_cr_valid": True,
        "encode_time_s": float(encode_time),
        "decode_time_s": float(errors.get("decode_time_s", 0.0)),
        "codec_decode_time_s": float(errors.get("track_decode_time_s", errors.get("decode_time_s", 0.0))),
        "max_abs_mz_error": float(errors["max_abs_mz_error"]),
        "max_abs_intensity_error": float(errors["max_abs_intensity_error"]),
    }


def compare_centroid_scans(original_scans: list[dict], decoded_scans: list[dict]) -> dict:
    if len(original_scans) != len(decoded_scans):
        raise ValueError(f"MS2 scan count mismatch: {len(original_scans)} vs {len(decoded_scans)}")
    max_abs_mz = 0.0
    max_abs_int = 0.0
    for idx, (orig, dec) in enumerate(zip(original_scans, decoded_scans)):
        orig_mz = np.asarray(orig.get("mz_array", []), dtype=np.float64)
        dec_mz = np.asarray(dec.get("mz_array", []), dtype=np.float64)
        orig_int = np.asarray(orig.get("intensity_array", []), dtype=np.float64)
        dec_int = np.asarray(dec.get("intensity_array", []), dtype=np.float64)
        if len(orig_mz) != len(dec_mz) or len(orig_int) != len(dec_int):
            raise ValueError(
                f"MS2 array length mismatch at scan {idx}: "
                f"{len(orig_mz)}/{len(orig_int)} vs {len(dec_mz)}/{len(dec_int)}"
            )
        if len(orig_mz):
            max_abs_mz = max(max_abs_mz, float(np.max(np.abs(orig_mz - dec_mz))))
        if len(orig_int):
            max_abs_int = max(max_abs_int, float(np.max(np.abs(orig_int - dec_int))))
    return {
        "max_abs_mz_error": max_abs_mz,
        "max_abs_intensity_error": max_abs_int,
    }


def run_ms2_variant(loaded: LoadedScans, variant: Variant, cache: dict[tuple, dict]) -> dict:
    key = (loaded.file_path.name, loaded.file_type, variant.backend, int(variant.mz_precision))
    if key in cache:
        return dict(cache[key])
    if loaded.file_type == "DIA":
        if not loaded.ms2_by_window:
            stats = empty_stats()
            cache[key] = stats
            return dict(stats)
        codec = make_ms2_codec(loaded.file_type, backend=variant.backend, mz_precision=variant.mz_precision)
        items = sorted(loaded.ms2_by_window.items(), key=lambda item: item[0])
        t0 = time.perf_counter()
        encoded = codec.encode_dia_windows(items)
        encode_time = time.perf_counter() - t0
        dt0 = time.perf_counter()
        decoded = codec.decode_dia_windows(encoded)
        decode_time = time.perf_counter() - dt0
        compare_rows = [compare_centroid_scans(scans, decoded[key]) for key, scans in items]
        max_abs_mz = max((row["max_abs_mz_error"] for row in compare_rows), default=0.0)
        max_abs_int = max((row["max_abs_intensity_error"] for row in compare_rows), default=0.0)
    else:
        if not loaded.dda_ms2_scans:
            stats = empty_stats()
            cache[key] = stats
            return dict(stats)
        codec = make_ms2_codec(loaded.file_type, backend=variant.backend, mz_precision=variant.mz_precision)
        t0 = time.perf_counter()
        encoded = codec.encode_dda_blocks(loaded.dda_ms2_scans)
        encode_time = time.perf_counter() - t0
        dt0 = time.perf_counter()
        decoded = codec.decode_dda_blocks(encoded)
        decode_time = time.perf_counter() - dt0
        compare = compare_centroid_scans(loaded.dda_ms2_scans, decoded)
        max_abs_mz = float(compare["max_abs_mz_error"])
        max_abs_int = float(compare["max_abs_intensity_error"])

    stats = {
        "raw_bytes": int(encoded.get("raw_bytes", loaded.raw_ms2_bytes)),
        "compressed_bytes": int(encoded.get("compressed_bytes", 0)),
        "compression_ratio": float(encoded.get("compression_ratio", 0.0)),
        "payload_only_compressed_bytes": int(encoded.get("compressed_bytes", 0)),
        "payload_only_compression_ratio": float(encoded.get("compression_ratio", 0.0)),
        "external_sidecar_compressed_bytes": 0,
        "sidecar_in_codec": True,
        "sidecar_enabled": True,
        "roundtrip_cr_valid": True,
        "encode_time_s": float(encode_time),
        "decode_time_s": float(decode_time),
        "codec_decode_time_s": float(decode_time),
        "max_abs_mz_error": float(max_abs_mz),
        "max_abs_intensity_error": float(max_abs_int),
    }
    cache[key] = stats
    return dict(stats)


def empty_stats() -> dict:
    return {
        "raw_bytes": 0,
        "compressed_bytes": 0,
        "compression_ratio": None,
        "payload_only_compressed_bytes": 0,
        "payload_only_compression_ratio": None,
        "external_sidecar_compressed_bytes": 0,
        "sidecar_in_codec": True,
        "sidecar_enabled": True,
        "roundtrip_cr_valid": True,
        "encode_time_s": 0.0,
        "decode_time_s": 0.0,
        "codec_decode_time_s": 0.0,
        "max_abs_mz_error": 0.0,
        "max_abs_intensity_error": 0.0,
    }


def combine_row(file_item: dict, variant: Variant, ms1: dict) -> dict:
    return {
        "file": file_item["file"],
        "vendor": file_item.get("vendor") or "unknown",
        "variant_id": variant.variant_id,
        "variant_name": variant.variant_name,
        "intensity_mode": variant.intensity_mode,
        "backend": variant.backend,
        "mz_precision": int(variant.mz_precision),
        "ms1_cr": ms1.get("compression_ratio"),
        "ms1_payload_only_cr": ms1.get("payload_only_compression_ratio"),
        "ms1_sidecar_in_codec": bool(ms1.get("sidecar_in_codec", False)),
        "ms1_sidecar_enabled": bool(ms1.get("sidecar_enabled", False)),
        "ms1_roundtrip_cr_valid": bool(ms1.get("roundtrip_cr_valid", False)),
        "encode_time_s": float(ms1.get("encode_time_s", 0.0)),
        "decode_time_s": float(ms1.get("decode_time_s", 0.0)),
        "max_abs_delta_intensity": float(ms1.get("max_abs_intensity_error", 0.0)),
        "max_abs_delta_mz": float(ms1.get("max_abs_mz_error", 0.0)),
        "mz_lossless_ok": float(ms1.get("max_abs_mz_error", 0.0)) <= MZ_LOSSLESS_EPS,
        "dtype_template": "section_float64_scan_payload",
        "ms1_raw_bytes": int(ms1.get("raw_bytes") or 0),
        "ms1_compressed_bytes": int(ms1.get("compressed_bytes") or 0),
        "ms1_payload_only_compressed_bytes": int(ms1.get("payload_only_compressed_bytes") or 0),
        "ms1_external_sidecar_compressed_bytes": int(ms1.get("external_sidecar_compressed_bytes") or 0),
    }


def combine_component_row(file_item: dict, variant: Variant, ms1: dict, ms2: dict) -> dict:
    raw_total = int(ms1.get("raw_bytes") or 0) + int(ms2.get("raw_bytes") or 0)
    ms1_roundtrip_comp = ms1.get("compressed_bytes")
    ms2_comp = int(ms2.get("compressed_bytes") or 0)
    comp_total = None if ms1_roundtrip_comp in (None, "") else int(ms1_roundtrip_comp) + ms2_comp
    payload_only_comp_total = int(ms1.get("payload_only_compressed_bytes") or 0) + ms2_comp
    ms1_int_err = float(ms1.get("max_abs_intensity_error", 0.0))
    ms2_int_err = float(ms2.get("max_abs_intensity_error", 0.0))
    ms1_mz_err = float(ms1.get("max_abs_mz_error", 0.0))
    ms2_mz_err = float(ms2.get("max_abs_mz_error", 0.0))
    max_mz_err = max(ms1_mz_err, ms2_mz_err)
    return {
        "file": file_item["file"],
        "vendor": file_item.get("vendor") or "unknown",
        "variant_id": variant.variant_id,
        "variant_name": variant.variant_name,
        "intensity_mode": variant.intensity_mode,
        "backend": variant.backend,
        "mz_precision": int(variant.mz_precision),
        "ms1_cr": ms1.get("compression_ratio"),
        "ms1_payload_only_cr": ms1.get("payload_only_compression_ratio"),
        "ms1_sidecar_in_codec": bool(ms1.get("sidecar_in_codec", False)),
        "ms1_sidecar_enabled": bool(ms1.get("sidecar_enabled", False)),
        "ms1_roundtrip_cr_valid": bool(ms1.get("roundtrip_cr_valid", False)),
        "ms2_cr": ms2.get("compression_ratio"),
        "whole_cr": (raw_total / comp_total) if comp_total else None,
        "whole_payload_only_cr": (raw_total / payload_only_comp_total) if payload_only_comp_total else None,
        "encode_time_s": float(ms1.get("encode_time_s", 0.0)) + float(ms2.get("encode_time_s", 0.0)),
        "decode_time_s": float(ms1.get("decode_time_s", 0.0)) + float(ms2.get("decode_time_s", 0.0)),
        "max_abs_delta_intensity": ms1_int_err,
        "max_abs_delta_intensity_all": max(ms1_int_err, ms2_int_err),
        "max_abs_delta_mz": max_mz_err,
        "ms1_max_abs_delta_intensity": ms1_int_err,
        "ms2_max_abs_delta_intensity": ms2_int_err,
        "ms1_max_abs_delta_mz": ms1_mz_err,
        "ms2_max_abs_delta_mz": ms2_mz_err,
        "mz_lossless_ok": max_mz_err <= MZ_LOSSLESS_EPS,
        "dtype_template": "section_float64_scan_payload",
        "ms1_raw_bytes": int(ms1.get("raw_bytes") or 0),
        "ms1_compressed_bytes": int(ms1_roundtrip_comp) if ms1_roundtrip_comp not in (None, "") else None,
        "ms1_payload_only_compressed_bytes": int(ms1.get("payload_only_compressed_bytes") or 0),
        "ms1_external_sidecar_compressed_bytes": int(ms1.get("external_sidecar_compressed_bytes") or 0),
        "ms2_raw_bytes": int(ms2.get("raw_bytes") or 0),
        "ms2_compressed_bytes": int(ms2.get("compressed_bytes") or 0),
        "dataset": file_item.get("dataset", ""),
        "index": int(file_item.get("index") or 0),
    }


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fieldnames})


def aggregate_rows(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    cr = [float(row["ms1_cr"]) for row in rows if row.get("ms1_cr") not in (None, "")]
    enc = [float(row["encode_time_s"]) for row in rows if row.get("encode_time_s") not in (None, "")]
    dec = [float(row["decode_time_s"]) for row in rows if row.get("decode_time_s") not in (None, "")]
    return {
        "n_files": len({row.get("file") for row in rows}),
        "mean_ms1_cr": statistics.mean(cr) if cr else None,
        "median_ms1_cr": statistics.median(cr) if cr else None,
        "mean_encode_time_s": statistics.mean(enc) if enc else 0.0,
        "mean_decode_time_s": statistics.mean(dec) if dec else 0.0,
    }
