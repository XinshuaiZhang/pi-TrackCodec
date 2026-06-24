"""Island extraction and grid-consistency detection"""
import numpy as np
from dataclasses import dataclass

try:
    from ..ms1 import _cross_scan_speedups as _cross_scan_speedups
    HAVE_ISLAND_SPEEDUPS = True
except ImportError:  # pragma: no cover - optional native extension
    _cross_scan_speedups = None
    HAVE_ISLAND_SPEEDUPS = False


DEFAULT_STRATEGY_B_VALLEY_RATIO = 0.01


@dataclass(slots=True)
class Island:
    mz_start:        float
    mz_step:         float
    n_points:        int
    intensity_array: np.ndarray  # float64, shape (n_points,)
    mz_array:        np.ndarray  # float64, shape (n_points,) - original m/z data
    center_mz:       float       # intensity-weighted centroid
    max_intensity:   float
    scan_idx:        int         # index of the owning scan
    array_start_idx: int         # start index within the original scan array (for roundtrip)


def _nonzero_runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if mask.size == 0 or not np.any(mask):
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64)
    padded = np.concatenate((np.array([False], dtype=bool), mask, np.array([False], dtype=bool)))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return edges[0::2], edges[1::2]


def identify_islands(mz_array: np.ndarray,
                     intensity_array: np.ndarray,
                     scan_idx: int,
                     strategy: str = 'auto',
                     min_points: int = 1,
                     strategy_b_valley_ratio: float = DEFAULT_STRATEGY_B_VALLEY_RATIO) -> list:
    """
    Extract islands from a profile scan.

    Strategy A (zero-filled profile): contiguous non-zero runs.
    Strategy B (sparse profile): local intensity valleys.
    Strategy auto: zero fraction >20% -> A, otherwise B.

    Returns: list of Island
    """
    mz_arr  = np.asarray(mz_array,       dtype=np.float64)
    int_arr = np.asarray(intensity_array, dtype=np.float64)
    n = len(int_arr)
    if n == 0:
        return []

    if strategy == 'auto':
        zero_frac = np.sum(int_arr == 0) / n
        strategy = 'A' if zero_frac > 0.20 else 'B'

    if strategy == 'A':
        return _islands_strategy_a(mz_arr, int_arr, scan_idx, min_points)
    else:
        return _islands_strategy_b(
            mz_arr,
            int_arr,
            scan_idx,
            min_points,
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
        )


def identify_island_features_compact(
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
    scan_idx: int,
    strategy: str = "auto",
    min_points: int = 1,
    strategy_b_valley_ratio: float = DEFAULT_STRATEGY_B_VALLEY_RATIO,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Return compact island metadata without materializing Island objects.

    The output order and island boundaries match identify_islands() for the
    default strategies. It is intended for views-only CompactIslandTracks where
    downstream code only needs center m/z, n_points, scan_idx, and array_start.
    """
    mz_arr = np.asarray(mz_array, dtype=np.float64)
    int_arr = np.asarray(intensity_array, dtype=np.float64)
    n = int(len(int_arr))
    if n == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.uint32),
            int(scan_idx),
        )

    if strategy == "auto":
        zero_frac = np.sum(int_arr == 0) / n
        strategy = "A" if zero_frac > 0.20 else "B"

    if strategy == "A":
        starts, ends, centers = _island_feature_runs_strategy_a(mz_arr, int_arr, min_points)
    else:
        starts, ends, centers = _island_feature_runs_strategy_b(
            mz_arr,
            int_arr,
            min_points,
            strategy_b_valley_ratio=float(strategy_b_valley_ratio),
        )
    if starts.size == 0:
        return (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.uint32),
            int(scan_idx),
        )
    n_points = (ends - starts).astype(np.uint32, copy=False)
    return (
        np.asarray(centers, dtype=np.float64),
        n_points,
        starts.astype(np.uint32, copy=False),
        int(scan_idx),
    )


def _centers_from_runs(mz_arr: np.ndarray, int_arr: np.ndarray, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    centers = np.empty(int(starts.size), dtype=np.float64)
    for idx, (start, end) in enumerate(zip(starts, ends)):
        s = int(start)
        e = int(end)
        isl_mz = mz_arr[s:e]
        isl_int = int_arr[s:e]
        total = float(isl_int.sum())
        centers[idx] = float((isl_mz * isl_int).sum() / total) if total > 0.0 else float(isl_mz.mean())
    return centers


def _island_feature_runs_strategy_a(mz_arr: np.ndarray, int_arr: np.ndarray, min_points: int):
    min_points = max(1, int(min_points))
    if HAVE_ISLAND_SPEEDUPS:
        starts, ends, _mz_starts, _mz_steps, centers, _max_values = _cross_scan_speedups.strategy_a_island_features(
            np.ascontiguousarray(mz_arr, dtype=np.float64),
            np.ascontiguousarray(int_arr, dtype=np.float64),
            min_points,
        )
        return (
            np.asarray(starts, dtype=np.uint32),
            np.asarray(ends, dtype=np.uint32),
            np.asarray(centers, dtype=np.float64),
        )
    starts, ends = _nonzero_runs(int_arr > 0)
    if starts.size == 0:
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.float64),
        )
    keep = (ends - starts) >= min_points
    starts = starts[keep].astype(np.uint32, copy=False)
    ends = ends[keep].astype(np.uint32, copy=False)
    return starts, ends, _centers_from_runs(mz_arr, int_arr, starts, ends)


def _island_feature_runs_strategy_b(
    mz_arr: np.ndarray,
    int_arr: np.ndarray,
    min_points: int,
    strategy_b_valley_ratio: float,
):
    if len(int_arr) < 3:
        return _island_feature_runs_strategy_a(mz_arr, int_arr, min_points)

    if HAVE_ISLAND_SPEEDUPS and hasattr(_cross_scan_speedups, "strategy_b_island_features"):
        starts, ends, _mz_starts, _mz_steps, centers, _max_values = _cross_scan_speedups.strategy_b_island_features(
            np.ascontiguousarray(mz_arr, dtype=np.float64),
            np.ascontiguousarray(int_arr, dtype=np.float64),
            int(max(min_points, 3)),
            float(strategy_b_valley_ratio),
        )
        return (
            np.asarray(starts, dtype=np.uint32),
            np.asarray(ends, dtype=np.uint32),
            np.asarray(centers, dtype=np.float64),
        )

    min_pts = max(min_points, 3)
    from scipy.signal import argrelmin
    valley_idx = argrelmin(int_arr, order=2)[0]

    valley_ratio = max(0.0, float(strategy_b_valley_ratio))
    split_pts = [0]
    for vi in valley_idx:
        if vi <= 0 or vi >= len(int_arr) - 1:
            continue
        left_max = int_arr[max(0, vi - 5):vi].max() if vi > 0 else 0
        right_max = int_arr[vi + 1:min(len(int_arr), vi + 6)].max()
        if int_arr[vi] < max(left_max, right_max) * valley_ratio:
            split_pts.append(int(vi))
    split_pts.append(len(int_arr))

    starts_list = []
    ends_list = []
    for j in range(len(split_pts) - 1):
        s = int(split_pts[j])
        e = int(split_pts[j + 1])
        seg_int = int_arr[s:e]
        if np.any(seg_int > 0) and (e - s) >= min_pts:
            nz = np.where(seg_int > 0)[0]
            s2 = s + int(nz[0])
            e2 = s + int(nz[-1]) + 1
            if e2 - s2 >= min_pts:
                starts_list.append(s2)
                ends_list.append(e2)

    if starts_list:
        covered = np.zeros(len(int_arr), dtype=bool)
        for start, end in zip(starts_list, ends_list):
            covered[int(start):int(end)] = True
        uncovered_nonzero = (int_arr > 0) & (~covered)
        if np.any(uncovered_nonzero):
            extra_starts, extra_ends = _nonzero_runs(uncovered_nonzero)
            starts_list.extend(int(x) for x in extra_starts)
            ends_list.extend(int(x) for x in extra_ends)
            order = np.argsort(np.asarray(starts_list, dtype=np.int64))
            starts_list = [starts_list[int(i)] for i in order]
            ends_list = [ends_list[int(i)] for i in order]

    if not starts_list:
        return (
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.uint32),
            np.empty(0, dtype=np.float64),
        )
    starts = np.asarray(starts_list, dtype=np.uint32)
    ends = np.asarray(ends_list, dtype=np.uint32)
    return starts, ends, _centers_from_runs(mz_arr, int_arr, starts, ends)


def _islands_strategy_a(mz_arr, int_arr, scan_idx, min_points):
    """Contiguous non-zero runs."""
    if HAVE_ISLAND_SPEEDUPS:
        starts, ends, mz_starts, mz_steps, centers, max_values = _cross_scan_speedups.strategy_a_island_features(
            np.ascontiguousarray(mz_arr, dtype=np.float64),
            np.ascontiguousarray(int_arr, dtype=np.float64),
            int(max(1, int(min_points))),
        )
        if len(starts) == 0:
            return []
        islands = []
        for start, end, mz_start, mz_step, center, max_intensity in zip(
            starts.tolist(),
            ends.tolist(),
            mz_starts.tolist(),
            mz_steps.tolist(),
            centers.tolist(),
            max_values.tolist(),
        ):
            islands.append(
                Island(
                    mz_start=float(mz_start),
                    mz_step=float(mz_step),
                    n_points=int(end - start),
                    intensity_array=int_arr[int(start):int(end)],
                    mz_array=mz_arr[int(start):int(end)],
                    center_mz=float(center),
                    max_intensity=float(max_intensity),
                    scan_idx=int(scan_idx),
                    array_start_idx=int(start),
                )
            )
        return islands
    starts, ends = _nonzero_runs(int_arr > 0)
    if len(starts) == 0:
        return []
    min_points = max(1, int(min_points))
    keep = (ends - starts) >= min_points
    return [
        _make_island(mz_arr, int_arr, int(start), int(end), scan_idx)
        for start, end in zip(starts[keep], ends[keep])
    ]


def _islands_strategy_b(mz_arr, int_arr, scan_idx, min_points, strategy_b_valley_ratio):
    """Identify island boundaries by local valleys and fill in every uncovered non-zero run."""
    if len(int_arr) < 3:
        return _islands_strategy_a(mz_arr, int_arr, scan_idx, min_points)

    if HAVE_ISLAND_SPEEDUPS and hasattr(_cross_scan_speedups, "strategy_b_island_features"):
        starts, ends, mz_starts, mz_steps, centers, max_values = _cross_scan_speedups.strategy_b_island_features(
            np.ascontiguousarray(mz_arr, dtype=np.float64),
            np.ascontiguousarray(int_arr, dtype=np.float64),
            int(max(min_points, 3)),
            float(strategy_b_valley_ratio),
        )
        if len(starts) == 0:
            return []
        islands = []
        for start, end, mz_start, mz_step, center, max_intensity in zip(
            starts.tolist(),
            ends.tolist(),
            mz_starts.tolist(),
            mz_steps.tolist(),
            centers.tolist(),
            max_values.tolist(),
        ):
            islands.append(
                Island(
                    mz_start=float(mz_start),
                    mz_step=float(mz_step),
                    n_points=int(end - start),
                    intensity_array=int_arr[int(start):int(end)],
                    mz_array=mz_arr[int(start):int(end)],
                    center_mz=float(center),
                    max_intensity=float(max_intensity),
                    scan_idx=int(scan_idx),
                    array_start_idx=int(start),
                )
            )
        return islands

    min_pts = max(min_points, 3)
    from scipy.signal import argrelmin
    valley_idx = argrelmin(int_arr, order=2)[0]

    valley_ratio = max(0.0, float(strategy_b_valley_ratio))
    # Filter by valley depth: valley < max(left, right) x valley_ratio
    split_pts = [0]
    for vi in valley_idx:
        if vi <= 0 or vi >= len(int_arr) - 1:
            continue
        left_max  = int_arr[max(0, vi-5):vi].max() if vi > 0 else 0
        right_max = int_arr[vi+1:min(len(int_arr), vi+6)].max()
        if int_arr[vi] < max(left_max, right_max) * valley_ratio:
            split_pts.append(vi)
    split_pts.append(len(int_arr))

    islands = []
    for j in range(len(split_pts) - 1):
        s = split_pts[j]
        e = split_pts[j + 1]
        seg_int = int_arr[s:e]
        if np.any(seg_int > 0) and (e - s) >= min_pts:
            # trim leading/trailing zeros
            nz = np.where(seg_int > 0)[0]
            s2 = s + nz[0]
            e2 = s + nz[-1] + 1
            if e2 - s2 >= min_pts:
                islands.append(_make_island(mz_arr, int_arr, s2, e2, scan_idx))

    # Strategy B historically dropped short/singleton nonzero segments.
    # For reconstructable MS1 coding we must retain every nonzero point.
    if islands:
        covered = np.zeros(len(int_arr), dtype=bool)
        for island in islands:
            start = int(island.array_start_idx)
            end = start + int(island.n_points)
            covered[start:end] = True
        uncovered_nonzero = (int_arr > 0) & (~covered)
        if np.any(uncovered_nonzero):
            starts, ends = _nonzero_runs(uncovered_nonzero)
            islands.extend(
                _make_island(mz_arr, int_arr, int(start), int(end), scan_idx)
                for start, end in zip(starts, ends)
            )
            islands.sort(key=lambda isl: int(isl.array_start_idx))
    return islands


def _make_island(mz_arr, int_arr, start, end, scan_idx):
    isl_mz  = mz_arr[start:end]
    isl_int = int_arr[start:end]
    total   = isl_int.sum()
    center  = float((isl_mz * isl_int).sum() / total) if total > 0 else float(isl_mz.mean())
    mz_step = float(isl_mz[1] - isl_mz[0]) if len(isl_mz) > 1 else 0.0
    return Island(
        mz_start        = float(isl_mz[0]),
        mz_step         = mz_step,
        n_points        = end - start,
        intensity_array = isl_int,
        mz_array        = isl_mz,  # use views to avoid duplicating full scan arrays
        center_mz       = center,
        max_intensity   = float(isl_int.max()),
        scan_idx        = scan_idx,
        array_start_idx = start,
    )


def check_grid_consistency(a: Island, b: Island,
                            strict_mz_start_tol: float = 1e-6,
                            strict_mz_step_tol:  float = 1e-8) -> str:
    """
    Returns: 'strict' | 'relaxed' | 'inconsistent'
    strict:       n_points equal AND |delta mz_start| < tol AND |delta mz_step| < tol
    relaxed:      n_points equal (start/step may be offset)
    inconsistent: n_points differ
    """
    if a.n_points != b.n_points:
        return 'inconsistent'
    npts_ok = True
    start_ok = abs(a.mz_start - b.mz_start) < strict_mz_start_tol
    step_ok  = (a.n_points == 1) or (abs(a.mz_step - b.mz_step) < strict_mz_step_tol)
    if npts_ok and start_ok and step_ok:
        return 'strict'
    return 'relaxed'
