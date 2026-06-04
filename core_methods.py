from __future__ import annotations

"""Unified production-facing core entry points for TrackCodec methods.

This file centralizes the currently used production paths for:
- MS1 section compression/decompression
  - equal-fidelity
  - strict-q6
- MS2 section compression/decompression
  - equal-fidelity
- whole-file containers
  - TrackCodec-Archive

The underlying implementations still live in the production package. This module is a
single place to inspect and call the current method presets without having to jump
between benchmark scripts, runtime wrappers, and production classes.
"""

import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parent
ROOT = PACKAGE_ROOT.parent


def _ensure_trackcodec_package() -> None:
    if importlib.util.find_spec("TrackCodec") is not None:
        return
    init_py = PACKAGE_ROOT / "__init__.py"
    if not init_py.exists():
        return
    spec = importlib.util.spec_from_file_location(
        "TrackCodec",
        init_py,
        submodule_search_locations=[str(PACKAGE_ROOT)],
    )
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("TrackCodec", module)
    spec.loader.exec_module(module)


_ensure_trackcodec_package()


from TrackCodec.production.common.io import (  # noqa: E402
    detect_file_type,
    load_scans,
    load_scans_with_ms2_payloads,
)
from TrackCodec.production.ms1.unified_codec import MS1Codec  # noqa: E402
from TrackCodec.production.ms2.codec import (  # noqa: E402
    DIAWindowMS2Codec,
    MS2ModeConfig,
)
from TrackCodec.production.mzml.archive_codec import (  # noqa: E402
    DEFAULT_MS1_ISLAND_WORKERS,
    build_ms1_tracks,
)
from TrackCodec.production.trackcodec import TrackCodecArchive  # noqa: E402


MS1_SECTION_EQUAL_FIDELITY_MODE: Dict[str, Any] = {
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
        "omit_array_starts": True,
        "omit_n_points": True,
        "preserve_full_scan": True,
        "retain_zero_intensity_mz": True,
    },
}

MS1_SECTION_STRICT_Q6_MODE: Dict[str, Any] = {
    **{key: value for key, value in MS1_SECTION_EQUAL_FIDELITY_MODE.items() if key != "kwargs"},
    "kwargs": {
        **MS1_SECTION_EQUAL_FIDELITY_MODE["kwargs"],
    },
    "intensity_mode": "strict_lossless_xdelta",
}

WHOLEFILE_ARCHIVE_MS1_EQUAL_FIDELITY_MODE: Dict[str, Any] = {
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
        "omit_array_starts": True,
        "omit_n_points": True,
    },
}

WHOLEFILE_ARCHIVE_MS1_STRICT_Q6_MODE: Dict[str, Any] = {
    **{key: value for key, value in WHOLEFILE_ARCHIVE_MS1_EQUAL_FIDELITY_MODE.items() if key != "kwargs"},
    "kwargs": {
        **WHOLEFILE_ARCHIVE_MS1_EQUAL_FIDELITY_MODE["kwargs"],
    },
    "intensity_mode": "strict_lossless_xdelta",
}


def _clone_mode(mode_cfg: Dict[str, Any]) -> Dict[str, Any]:
    return copy.deepcopy(mode_cfg)


def get_ms1_section_mode(mode: str = "equal-fidelity") -> Dict[str, Any]:
    if mode == "equal-fidelity":
        return _clone_mode(MS1_SECTION_EQUAL_FIDELITY_MODE)
    if mode == "strict-q6":
        return _clone_mode(MS1_SECTION_STRICT_Q6_MODE)
    raise ValueError(f"Unsupported MS1 section mode: {mode}")


def get_wholefile_ms1_mode(container_kind: str = "archive", mode: str = "equal-fidelity") -> Dict[str, Any]:
    if container_kind != "archive":
        raise ValueError(f"Unsupported whole-file container kind: {container_kind}")
    if mode == "equal-fidelity":
        return _clone_mode(WHOLEFILE_ARCHIVE_MS1_EQUAL_FIDELITY_MODE)
    if mode == "strict-q6":
        return _clone_mode(WHOLEFILE_ARCHIVE_MS1_STRICT_Q6_MODE)
    raise ValueError(f"Unsupported whole-file mode: container_kind={container_kind}, mode={mode}")


def _new_ms1_codec(mode_cfg: Dict[str, Any]) -> MS1Codec:
    return MS1Codec(
        mz_precision=int(mode_cfg["mz_precision"]),
        intensity_mode=str(mode_cfg["intensity_mode"]),
        backend=str(mode_cfg["backend"]),
        **dict(mode_cfg["kwargs"]),
    )


def load_ms_payloads_from_mzml(
    mzml_path: str | Path,
    *,
    file_type: str | None = None,
    max_ms1_scans: int | None = None,
    show_progress: bool = False,
):
    mzml_path = str(mzml_path)
    resolved_type = file_type or detect_file_type(mzml_path)
    return load_scans_with_ms2_payloads(
        mzml_path,
        cfg_or_file_type=resolved_type,
        max_ms1_scans=max_ms1_scans,
        show_progress=show_progress,
    )


def load_ms1_scans_from_mzml(
    mzml_path: str | Path,
    *,
    file_type: str | None = None,
    max_ms1_scans: int | None = None,
    show_progress: bool = False,
):
    mzml_path = str(mzml_path)
    resolved_type = file_type or detect_file_type(mzml_path)
    ms1_scans, _, raw_ms1_bytes, _ = load_scans(
        mzml_path,
        cfg_or_file_type=resolved_type,
        max_ms1_scans=max_ms1_scans,
        show_progress=show_progress,
    )
    return ms1_scans, raw_ms1_bytes


def build_ms1_tracks_from_scans(ms1_scans, island_workers: int | None = DEFAULT_MS1_ISLAND_WORKERS):
    return build_ms1_tracks(ms1_scans, island_workers=island_workers)


def encode_ms1_section(ms1_scans, *, mode: str = "equal-fidelity", island_workers: int | None = DEFAULT_MS1_ISLAND_WORKERS):
    mode_cfg = get_ms1_section_mode(mode)
    codec = _new_ms1_codec(mode_cfg)
    tracks = build_ms1_tracks_from_scans(ms1_scans, island_workers=island_workers)
    preserve_full_scan = bool(mode_cfg["kwargs"].get("preserve_full_scan", False))
    payload, meta = codec.encode(tracks, ms1_scans if preserve_full_scan else None)
    return {
        "mode": mode_cfg,
        "codec": codec,
        "tracks": tracks,
        "payload": payload,
        "meta": meta,
    }


def decode_ms1_section_to_islands(payload: bytes, meta: Dict[str, Any], *, mode: str = "equal-fidelity"):
    codec = _new_ms1_codec(get_ms1_section_mode(mode))
    return codec.decode(payload, meta)


def decode_ms1_section_to_full_scans(
    payload: bytes,
    meta: Dict[str, Any],
    *,
    original_rts: Sequence[float],
    mode: str = "equal-fidelity",
):
    codec = _new_ms1_codec(get_ms1_section_mode(mode))
    return codec.decode_full_scans(
        payload,
        meta,
        original_rts=[float(x) for x in original_rts],
    )


def build_ms2_equal_fidelity_codec(file_type: str = "DIA") -> DIAWindowMS2Codec:
    resolved = str(file_type).upper()
    if resolved == "DIA":
        return DIAWindowMS2Codec(MS2ModeConfig.exact_track_dia_current())
    if resolved == "DDA":
        return DIAWindowMS2Codec(MS2ModeConfig.exact_track_dda_current())
    raise ValueError(f"Unsupported MS2 file type: {file_type}")


def encode_ms2_equal_fidelity(ms2_payload, *, file_type: str = "DIA"):
    codec = build_ms2_equal_fidelity_codec(file_type)
    if str(file_type).upper() == "DIA":
        encoded = codec.encode_dia_windows(ms2_payload)
    else:
        encoded = codec.encode_dda_blocks(ms2_payload)
    return {
        "codec": codec,
        "config": copy.deepcopy(codec.config),
        "encoded": encoded,
    }


def decode_ms2_equal_fidelity(encoded, *, file_type: str = "DIA", codec: DIAWindowMS2Codec | None = None):
    runtime_codec = codec or build_ms2_equal_fidelity_codec(file_type)
    if str(file_type).upper() == "DIA":
        return runtime_codec.decode_dia_windows(encoded)
    return runtime_codec.decode_dda_blocks(encoded)


def build_trackcodec_archive(
    *,
    ms1_mode: str = "equal-fidelity",
    ms1_island_workers: int | None = DEFAULT_MS1_ISLAND_WORKERS,
    ms2_config: MS2ModeConfig | None = None,
    ms2_section_workers: int | None = None,
    ms2_segment_workers: int | None = None,
    metadata_backend: str = "zstd-9",
    auxiliary_backend: str = "zstd-9",
    ms1_sidecar_backend: str = "zstd-9",
    ms1_full_mz_backend: str = "zstd-9",
    retain_zero_intensity_mz: bool = True,
    cache_dir: str | Path | None = None,
) -> TrackCodecArchive:
    return TrackCodecArchive(
        ms1_mode=get_wholefile_ms1_mode("archive", ms1_mode),
        ms1_island_workers=ms1_island_workers,
        ms2_config=ms2_config,
        ms2_section_workers=ms2_section_workers,
        ms2_segment_workers=ms2_segment_workers,
        metadata_backend=metadata_backend,
        auxiliary_backend=auxiliary_backend,
        ms1_sidecar_backend=ms1_sidecar_backend,
        ms1_full_mz_backend=ms1_full_mz_backend,
        retain_zero_intensity_mz=retain_zero_intensity_mz,
        cache_dir=cache_dir,
    )


def compress_whole_file_to_path(
    mzml_path: str | Path,
    output_path: str | Path,
    *,
    ms1_mode: str = "equal-fidelity",
    ms1_island_workers: int | None = DEFAULT_MS1_ISLAND_WORKERS,
    ms2_section_workers: int | None = None,
    retain_zero_intensity_mz: bool = True,
    cache_dir: str | Path | None = None,
):
    codec = build_trackcodec_archive(
        ms1_mode=ms1_mode,
        ms1_island_workers=ms1_island_workers,
        ms2_section_workers=ms2_section_workers,
        retain_zero_intensity_mz=retain_zero_intensity_mz,
        cache_dir=cache_dir,
    )
    output_path, meta = codec.encode_file_to_path(mzml_path, output_path)
    return {
        "codec": codec,
        "output_path": Path(output_path),
        "meta": meta,
    }


def decode_archive_sections(
    archive_path: str | Path,
    *,
    decode_ms1_sidecars: bool = True,
    decode_ms2_payload_json: bool = True,
    decode_auxiliary_records: bool = True,
    decode_metadata_xml: bool = True,
):
    codec = build_trackcodec_archive()
    return codec.decode_archive_file(
        archive_path,
        decode_ms1_sidecars=decode_ms1_sidecars,
        decode_ms2_payload_json=decode_ms2_payload_json,
        decode_auxiliary_records_flag=decode_auxiliary_records,
        decode_metadata_xml=decode_metadata_xml,
    )


def export_archive_to_mzml(
    archive_path: str | Path,
    output_mzml_path: str | Path,
    *,
    binary_compression: str = "preserve_template",
):
    codec = build_trackcodec_archive()
    return codec.export_to_mzml_file(
        archive_path,
        output_mzml_path,
        binary_compression=binary_compression,
    )


__all__ = [
    "MS1_SECTION_EQUAL_FIDELITY_MODE",
    "MS1_SECTION_STRICT_Q6_MODE",
    "WHOLEFILE_ARCHIVE_MS1_EQUAL_FIDELITY_MODE",
    "WHOLEFILE_ARCHIVE_MS1_STRICT_Q6_MODE",
    "build_ms1_tracks_from_scans",
    "load_ms_payloads_from_mzml",
    "load_ms1_scans_from_mzml",
    "encode_ms1_section",
    "decode_ms1_section_to_islands",
    "decode_ms1_section_to_full_scans",
    "build_ms2_equal_fidelity_codec",
    "encode_ms2_equal_fidelity",
    "decode_ms2_equal_fidelity",
    "build_trackcodec_archive",
    "compress_whole_file_to_path",
    "decode_archive_sections",
    "export_archive_to_mzml",
]
