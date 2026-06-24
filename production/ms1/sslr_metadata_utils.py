"""Shared helper functions for MS1 SSLR metadata and residual byte modeling."""

from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np

from ..common.compression_backends import compress


def _compress_bytes(blob: bytes, backend: str = "zstd-9") -> int:
    return int(len(compress(blob, backend)))


def pack_shift_array(shifts: Sequence[float]) -> np.ndarray:
    shift_values = [float(value) for value in shifts]
    if all(abs(value - round(value)) <= 1e-9 for value in shift_values):
        return np.asarray([int(round(value)) for value in shift_values], dtype=np.int16)
    return np.asarray(shift_values, dtype=np.float32)


def member_id_bytes(
    track_ids: Sequence[int],
    *,
    backend: str = "zstd-9",
    use_delta: bool,
) -> tuple[int, str]:
    if not track_ids:
        return 0, "none"
    raw_arr = np.asarray([int(track_id) for track_id in track_ids], dtype=np.uint32)
    raw_bytes = _compress_bytes(raw_arr.tobytes(), backend)
    if not use_delta or len(track_ids) <= 1:
        return int(raw_bytes), "uint32_raw"
    leader_id = int(track_ids[0])
    delta_arr = np.asarray(
        [leader_id] + [int(track_id) - leader_id for track_id in track_ids[1:]],
        dtype=np.int32,
    )
    delta_bytes = _compress_bytes(delta_arr.tobytes(), backend)
    if delta_bytes < raw_bytes:
        return int(delta_bytes), "int32_delta"
    return int(raw_bytes), "uint32_raw"


def shift_bytes(
    shifts: Sequence[float],
    *,
    backend: str = "zstd-9",
    use_zero_shortcut: bool,
) -> tuple[int, str]:
    shift_values = [float(value) for value in shifts]
    if not shift_values:
        return 0, "none"
    if use_zero_shortcut and all(abs(value) <= 1e-9 for value in shift_values):
        return 1, "zero_flag"
    packed = pack_shift_array(shift_values)
    return int(_compress_bytes(packed.tobytes(), backend)), str(packed.dtype)


def ratio_bytes(ratios: Sequence[float], *, backend: str = "zstd-9") -> int:
    if not ratios:
        return 0
    arr = np.asarray([float(value) for value in ratios], dtype=np.float32)
    return int(_compress_bytes(arr.tobytes(), backend))


def estimate_member_meta_bytes(
    *,
    leader_track_id: int,
    member_track_id: int,
    member_shift: float,
    member_ratio: float,
    byte_predict_meta_mode: str,
    byte_predict_meta_estimate_bytes: float,
    use_member_id_delta: bool,
    use_zero_shift_shortcut: bool,
    backend: str = "zstd-9",
) -> float:
    if byte_predict_meta_mode == "dynamic_current":
        member_id_nbytes, _ = member_id_bytes(
            [int(leader_track_id), int(member_track_id)],
            backend=backend,
            use_delta=use_member_id_delta,
        )
        shift_nbytes, _ = shift_bytes(
            [float(member_shift)],
            backend=backend,
            use_zero_shortcut=use_zero_shift_shortcut,
        )
        ratio_nbytes = ratio_bytes([float(member_ratio)], backend=backend)
        return float(member_id_nbytes + shift_nbytes + ratio_nbytes)
    return float(byte_predict_meta_estimate_bytes)


def concat_member_residuals(
    selected_members: Sequence[Mapping[str, object]],
    *,
    interleave_by_scan: bool,
) -> np.ndarray:
    if not selected_members:
        return np.array([], dtype=np.int64)
    if not interleave_by_scan:
        return np.concatenate(
            [np.asarray(item["residual_q"], dtype=np.int64) for item in selected_members]
        ).astype(np.int64, copy=False)

    residual_records: list[tuple[int, int, int]] = []
    for item in selected_members:
        scan_idx = np.asarray(item["residual_scan_idx"], dtype=np.int32)
        residual_q = np.asarray(item["residual_q"], dtype=np.int64)
        track_id = int(item["track_id"])
        for pos, value in zip(scan_idx.tolist(), residual_q.tolist()):
            residual_records.append((int(pos), track_id, int(value)))
    residual_records.sort(key=lambda item: (item[0], item[1]))
    return np.asarray([item[2] for item in residual_records], dtype=np.int64)
