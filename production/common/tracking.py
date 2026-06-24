"""Island/Peak tracking: baseline + enhanced matching, track construction"""
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional
from .islands import Island, check_grid_consistency
try:
    from ..ms1 import _cross_scan_speedups as _cross_scan_speedups
    HAVE_TRACK_LINK_SPEEDUPS = True
except ImportError:  # pragma: no cover - optional native extension
    _cross_scan_speedups = None
    HAVE_TRACK_LINK_SPEEDUPS = False


@dataclass(slots=True)
class IslandTrack:
    track_id:    int
    islands:     list          # list of Island (ordered by scan)
    scan_indices: list         # scan_idx list
    rt_values:   list          # RT list
    length:      int
    mean_center_mz: float
    max_intensity:  float
    grid_consistencies: list   # per-pair grid consistency ('strict'|'relaxed'|'inconsistent')

    @property
    def total_intensity(self):
        return sum(isl.max_intensity for isl in self.islands)


@dataclass(slots=True)
class PeakTrack:
    track_id:      int
    mz_values:     list
    intensity_values: list
    scan_indices:  list
    rt_values:     list
    length:        int
    mean_mz:       float
    max_intensity: float
    total_intensity: float
    window_target_mz: float


@dataclass(slots=True)
class CompactIslandTracks:
    track_lengths: np.ndarray
    track_offsets: np.ndarray
    islands: list
    scan_indices: np.ndarray
    array_start_indices: np.ndarray
    n_points: np.ndarray
    point_count: int

    def __len__(self) -> int:
        return int(self.track_lengths.size)

    @property
    def island_count(self) -> int:
        if len(self.islands):
            return int(len(self.islands))
        return int(self.scan_indices.size)

    @property
    def raw_bytes(self) -> int:
        return int(self.point_count) * 16

    def iter_track_ranges(self):
        for idx in range(int(self.track_lengths.size)):
            yield int(self.track_offsets[idx]), int(self.track_offsets[idx + 1])

    def iter_track_islands(self):
        for start, end in self.iter_track_ranges():
            yield self.islands[start:end]


def strip_compact_track_islands(tracks: CompactIslandTracks) -> CompactIslandTracks:
    if not isinstance(tracks, CompactIslandTracks):
        raise TypeError("strip_compact_track_islands expects CompactIslandTracks")
    if len(tracks.islands) == 0:
        return tracks
    return CompactIslandTracks(
        track_lengths=np.asarray(tracks.track_lengths, dtype=np.uint32),
        track_offsets=np.asarray(tracks.track_offsets, dtype=np.uint32),
        islands=[],
        scan_indices=np.asarray(tracks.scan_indices, dtype=np.uint32),
        array_start_indices=np.asarray(tracks.array_start_indices, dtype=np.uint32),
        n_points=np.asarray(tracks.n_points, dtype=np.uint32),
        point_count=int(tracks.point_count),
    )


# ── Baseline matching ────────────────────────────────────────────────────

def match_islands_baseline(prev_islands, curr_islands, ppm_tol=15.0):
    """
    Nearest neighbor on center_mz + one-to-one constraint (greedy: assign in
    ascending order of mz distance).
    Returns: matched [(prev_idx, curr_idx, ppm_diff, grid_cons)], unmatched_curr, unmatched_prev
    """
    if not prev_islands or not curr_islands:
        return [], list(range(len(curr_islands))), list(range(len(prev_islands)))

    prev_cmz = np.array([isl.center_mz for isl in prev_islands])
    curr_cmz = np.array([isl.center_mz for isl in curr_islands])
    prev_sorted = np.argsort(prev_cmz)
    prev_cmz_s  = prev_cmz[prev_sorted]

    candidates = []
    for ci, cmz in enumerate(curr_cmz):
        pos = np.searchsorted(prev_cmz_s, cmz)
        for p in [pos - 1, pos]:
            if 0 <= p < len(prev_cmz_s):
                pmz = prev_cmz_s[p]
                ppm = abs(cmz - pmz) / pmz * 1e6
                if ppm < ppm_tol:
                    candidates.append((ppm, prev_sorted[p], ci))

    candidates.sort(key=lambda x: x[0])
    used_prev, used_curr = set(), set()
    matched = []
    for ppm, pi, ci in candidates:
        if pi not in used_prev and ci not in used_curr:
            used_prev.add(pi); used_curr.add(ci)
            gc = check_grid_consistency(prev_islands[pi], curr_islands[ci])
            matched.append((pi, ci, ppm, gc))

    unc = [i for i in range(len(curr_islands)) if i not in used_curr]
    unp = [i for i in range(len(prev_islands)) if i not in used_prev]
    return matched, unc, unp


# ── Enhanced matching ────────────────────────────────────────────────────

def _mz_range_overlap(a: Island, b: Island) -> float:
    a_end = a.mz_start + a.mz_step * (a.n_points - 1)
    b_end = b.mz_start + b.mz_step * (b.n_points - 1)
    lo = max(a.mz_start, b.mz_start)
    hi = min(a_end, b_end)
    if hi <= lo:
        return 0.0
    union = max(a_end, b_end) - min(a.mz_start, b.mz_start)
    return (hi - lo) / union if union > 0 else 0.0


def match_islands_enhanced(prev_islands, curr_islands, ppm_tol=20.0,
                            weights=(0.5, 0.2, 0.2, 0.1), score_threshold=0.3):
    """
    Multi-feature scoring + greedy assignment.
    score = w1*(1-ppm/ppm_tol) + w2*range_overlap + w3*(1-|log2(int_ratio)|/5) + w4*(n_pts_same)
    """
    w1, w2, w3, w4 = weights
    if not prev_islands or not curr_islands:
        return [], list(range(len(curr_islands))), list(range(len(prev_islands)))

    prev_cmz = np.array([isl.center_mz for isl in prev_islands])
    curr_cmz = np.array([isl.center_mz for isl in curr_islands])
    prev_sorted = np.argsort(prev_cmz)
    prev_cmz_s  = prev_cmz[prev_sorted]

    candidates = []
    for ci, cmz in enumerate(curr_cmz):
        pos = np.searchsorted(prev_cmz_s, cmz)
        for p in range(max(0, pos - 2), min(len(prev_cmz_s), pos + 3)):
            pi = prev_sorted[p]
            pmz = prev_cmz[pi]
            ppm = abs(cmz - pmz) / pmz * 1e6
            if ppm >= ppm_tol:
                continue
            # intensity score
            pi_int = prev_islands[pi].max_intensity
            ci_int = curr_islands[ci].max_intensity
            if pi_int > 0 and ci_int > 0:
                int_score = max(0.0, 1.0 - abs(np.log2(ci_int / pi_int)) / 5.0)
            else:
                int_score = 0.5
            # overlap
            overlap = _mz_range_overlap(prev_islands[pi], curr_islands[ci])
            # n_pts
            npts_score = 1.0 if prev_islands[pi].n_points == curr_islands[ci].n_points else 0.5
            score = w1 * (1 - ppm / ppm_tol) + w2 * overlap + w3 * int_score + w4 * npts_score
            if score >= score_threshold:
                candidates.append((-score, pi, ci, ppm))  # negative for sort ascending

    candidates.sort(key=lambda x: x[0])
    used_prev, used_curr = set(), set()
    matched = []
    for neg_score, pi, ci, ppm in candidates:
        if pi not in used_prev and ci not in used_curr:
            used_prev.add(pi); used_curr.add(ci)
            gc = check_grid_consistency(prev_islands[pi], curr_islands[ci])
            matched.append((pi, ci, ppm, gc))

    unc = [i for i in range(len(curr_islands)) if i not in used_curr]
    unp = [i for i in range(len(prev_islands)) if i not in used_prev]
    return matched, unc, unp


# ── Peak matching (MS2, reuses the stable matching logic) ────────────────────────────────

def match_peaks(prev_mz, prev_int, curr_mz, curr_int, ppm_tol=15.0):
    if len(prev_mz) == 0 or len(curr_mz) == 0:
        return [], list(range(len(curr_mz))), list(range(len(prev_mz)))
    prev_sorted = np.argsort(prev_mz)
    prev_mz_s   = prev_mz[prev_sorted]
    candidates  = []
    for ci, cmz in enumerate(curr_mz):
        pos = np.searchsorted(prev_mz_s, cmz)
        for p in [pos - 1, pos]:
            if 0 <= p < len(prev_mz_s):
                ppm = abs(cmz - prev_mz_s[p]) / prev_mz_s[p] * 1e6
                if ppm < ppm_tol:
                    pi = prev_sorted[p]
                    ir = curr_int[ci] / prev_int[pi] if prev_int[pi] > 0 else 1.0
                    candidates.append((ppm, pi, ci, ir))
    candidates.sort()
    used_prev, used_curr = set(), set()
    matched = []
    for ppm, pi, ci, ir in candidates:
        if pi not in used_prev and ci not in used_curr:
            used_prev.add(pi); used_curr.add(ci)
            matched.append((pi, ci, ppm, ir))
    unc = [i for i in range(len(curr_mz)) if i not in used_curr]
    unp = [i for i in range(len(prev_mz)) if i not in used_prev]
    return matched, unc, unp


# ── Track construction ────────────────────────────────────────────────────────

def build_island_tracks(scan_island_list, ppm_tol=15.0, use_enhanced=False,
                        enhanced_weights=(0.5,0.2,0.2,0.1), score_thr=0.3,
                        compact: bool = False,
                        drop_islands_when_compact: bool = False):
    """
    scan_island_list: list of (scan_idx, rt, list[Island])
    Returns: list[IslandTrack]
    """
    if compact and (not use_enhanced) and HAVE_TRACK_LINK_SPEEDUPS:
        flat_islands = [] if not drop_islands_when_compact else None
        center_mz_vals = []
        n_points_vals = []
        scan_idx_vals = []
        array_start_idx_vals = []
        scan_offsets = [0]
        for _, _, curr_islands in scan_island_list:
            if flat_islands is not None:
                flat_islands.extend(curr_islands)
            for isl in curr_islands:
                center_mz_vals.append(float(isl.center_mz))
                n_points_vals.append(int(isl.n_points))
                scan_idx_vals.append(int(isl.scan_idx))
                array_start_idx_vals.append(int(isl.array_start_idx))
            scan_offsets.append(scan_offsets[-1] + len(curr_islands))
        if center_mz_vals:
            center_mz = np.asarray(center_mz_vals, dtype=np.float64)
            # The native baseline linker only uses center m/z and scan offsets.
            # Keep placeholder arrays for ABI compatibility without collecting
            # per-island values that are not consumed by the C++ implementation.
            mz_start = np.empty(0, dtype=np.float64)
            mz_step = np.empty(0, dtype=np.float64)
            n_points = np.asarray(n_points_vals, dtype=np.uint32)
            max_intensity = np.empty(0, dtype=np.float64)
            scan_indices_flat = np.asarray(scan_idx_vals, dtype=np.uint32)
            array_start_indices_flat = np.asarray(array_start_idx_vals, dtype=np.uint32)
            scan_offsets_arr = np.asarray(scan_offsets, dtype=np.uint32)
            track_lengths, island_order = _cross_scan_speedups.build_compact_tracks_baseline(
                center_mz,
                mz_start,
                mz_step,
                n_points,
                max_intensity,
                scan_offsets_arr,
                float(ppm_tol),
            )
            track_lengths = np.asarray(track_lengths, dtype=np.uint32)
            island_order = np.asarray(island_order, dtype=np.uint32)
            if drop_islands_when_compact:
                reordered_islands = []
            else:
                reordered_islands = [flat_islands[int(idx)] for idx in island_order]
            track_offsets = np.zeros(len(track_lengths) + 1, dtype=np.uint32)
            if len(track_lengths):
                track_offsets[1:] = np.cumsum(track_lengths, dtype=np.uint32)
            scan_indices = np.take(scan_indices_flat, island_order)
            array_start_indices = np.take(array_start_indices_flat, island_order)
            n_points_reordered = np.take(n_points, island_order)
            point_count = int(n_points_reordered.astype(np.uint64, copy=False).sum()) if n_points_reordered.size else 0
            return CompactIslandTracks(
                track_lengths=track_lengths,
                track_offsets=track_offsets,
                islands=[] if drop_islands_when_compact else reordered_islands,
                scan_indices=scan_indices,
                array_start_indices=array_start_indices,
                n_points=n_points_reordered,
                point_count=point_count,
            )
        return CompactIslandTracks(
            track_lengths=np.array([], dtype=np.uint32),
            track_offsets=np.array([0], dtype=np.uint32),
            islands=[],
            scan_indices=np.array([], dtype=np.uint32),
            array_start_indices=np.array([], dtype=np.uint32),
            n_points=np.array([], dtype=np.uint32),
            point_count=0,
        )

    match_fn = match_islands_enhanced if use_enhanced else match_islands_baseline
    extra_kw = {'weights': enhanced_weights, 'score_threshold': score_thr} if use_enhanced else {}

    # active_tracks: curr_island_key -> track data dict
    active = {}  # island_key -> {'track_id', 'islands', 'scan_indices', 'rt_values', 'grid_consistencies'}
    finished = []
    compact_track_lengths = []
    compact_islands = []
    compact_scan_indices = []
    compact_array_start_indices = []
    compact_n_points = []
    track_id_counter = [0]
    compact_metadata_only = bool(compact and drop_islands_when_compact)

    def new_track(isl, si, rt):
        tid = track_id_counter[0]; track_id_counter[0] += 1
        if compact_metadata_only:
            return {
                'track_id': tid,
                'scan_indices': [si],
                'array_start_indices': [int(isl.array_start_idx)],
                'n_points': [int(isl.n_points)],
            }
        return {'track_id': tid, 'islands': [isl], 'scan_indices': [si],
                'rt_values': [rt], 'grid_consistencies': [],
                'center_mz_sum': float(isl.center_mz),
                'max_intensity_running': float(isl.max_intensity)}

    def finalize(t):
        if compact:
            if compact_metadata_only:
                compact_track_lengths.append(int(len(t['scan_indices'])))
                compact_scan_indices.extend(t['scan_indices'])
                compact_array_start_indices.extend(t['array_start_indices'])
                compact_n_points.extend(t['n_points'])
            else:
                isls = t['islands']
                compact_track_lengths.append(int(len(isls)))
                compact_islands.extend(isls)
            return
        isls = t['islands']
        finished.append(IslandTrack(
            track_id            = t['track_id'],
            islands             = isls,
            scan_indices        = t['scan_indices'],
            rt_values           = t['rt_values'],
            length              = len(isls),
            mean_center_mz      = float(t['center_mz_sum'] / max(1, len(isls))),
            max_intensity       = float(t['max_intensity_running']),
            grid_consistencies  = t['grid_consistencies'],
        ))

    prev_islands = []
    prev_active  = {}  # prev_island_idx -> track dict

    for scan_idx, rt, curr_islands in scan_island_list:
        if not prev_islands:
            # first scan: start all as new tracks
            prev_active = {i: new_track(curr_islands[i], scan_idx, rt)
                           for i in range(len(curr_islands))}
            prev_islands = curr_islands
            continue

        kw = dict(ppm_tol=ppm_tol, **extra_kw)
        matched, unc_curr, unp_prev = match_fn(prev_islands, curr_islands, **kw)

        new_active = {}
        # extend matched
        for pi, ci, ppm, gc in matched:
            t = prev_active[pi]
            isl = curr_islands[ci]
            t['scan_indices'].append(scan_idx)
            if compact_metadata_only:
                t['array_start_indices'].append(int(isl.array_start_idx))
                t['n_points'].append(int(isl.n_points))
            else:
                t['islands'].append(isl)
                t['rt_values'].append(rt)
                t['grid_consistencies'].append(gc)
                t['center_mz_sum'] += float(isl.center_mz)
                curr_max_intensity = float(isl.max_intensity)
                if curr_max_intensity > t['max_intensity_running']:
                    t['max_intensity_running'] = curr_max_intensity
            new_active[ci] = t

        # finalize unmatched prev
        for pi in unp_prev:
            finalize(prev_active[pi])

        # start new tracks for unmatched curr
        for ci in unc_curr:
            new_active[ci] = new_track(curr_islands[ci], scan_idx, rt)

        prev_islands = curr_islands
        prev_active  = new_active

    # finalize remaining
    for t in prev_active.values():
        finalize(t)

    if compact:
        track_lengths = np.asarray(compact_track_lengths, dtype=np.uint32)
        track_offsets = np.zeros(len(track_lengths) + 1, dtype=np.uint32)
        if len(track_lengths):
            track_offsets[1:] = np.cumsum(track_lengths, dtype=np.uint32)
        if compact_metadata_only:
            island_count = int(len(compact_scan_indices))
            scan_indices = np.asarray(compact_scan_indices, dtype=np.uint32)
            array_start_indices = np.asarray(compact_array_start_indices, dtype=np.uint32)
            n_points = np.asarray(compact_n_points, dtype=np.uint32)
        else:
            island_count = int(len(compact_islands))
            scan_indices = np.fromiter(
                (int(island.scan_idx) for island in compact_islands),
                dtype=np.uint32,
                count=island_count,
            )
            array_start_indices = np.fromiter(
                (int(island.array_start_idx) for island in compact_islands),
                dtype=np.uint32,
                count=island_count,
            )
            n_points = np.fromiter(
                (int(island.n_points) for island in compact_islands),
                dtype=np.uint32,
                count=island_count,
            )
        point_count = int(n_points.astype(np.uint64, copy=False).sum()) if island_count else 0
        return CompactIslandTracks(
            track_lengths=track_lengths,
            track_offsets=track_offsets,
            islands=[] if compact_metadata_only else compact_islands,
            scan_indices=scan_indices,
            array_start_indices=array_start_indices,
            n_points=n_points,
            point_count=point_count,
        )

    return finished


def build_peak_tracks(scan_list, ppm_tol=15.0, window_target_mz=0.0):
    """
    scan_list: list of dict {scan_idx, rt, mz_array, intensity_array}
    Returns: list[PeakTrack]
    """
    active = {}  # prev_idx -> track dict
    finished = []
    tid = [0]

    def new_t(mz, it, si, rt):
        t = tid[0]; tid[0] += 1
        return {'track_id': t, 'mzs': [mz], 'ints': [it], 'scans': [si], 'rts': [rt]}

    def fin(t):
        mzs = t['mzs']; ints = t['ints']
        finished.append(PeakTrack(
            track_id         = t['track_id'],
            mz_values        = mzs,
            intensity_values = ints,
            scan_indices     = t['scans'],
            rt_values        = t['rts'],
            length           = len(mzs),
            mean_mz          = float(np.mean(mzs)),
            max_intensity    = float(max(ints)),
            total_intensity  = float(sum(ints)),
            window_target_mz = window_target_mz,
        ))

    prev_mz  = np.array([])
    prev_int = np.array([])

    for entry in scan_list:
        si  = entry['scan_idx']
        rt  = entry['rt']
        mz  = np.asarray(entry['mz_array'],       dtype=np.float64)
        it  = np.asarray(entry['intensity_array'], dtype=np.float64)

        if len(prev_mz) == 0:
            active = {i: new_t(mz[i], it[i], si, rt) for i in range(len(mz))}
            prev_mz = mz; prev_int = it
            continue

        matched, unc_curr, unp_prev = match_peaks(prev_mz, prev_int, mz, it, ppm_tol)
        new_active = {}
        for pi, ci, ppm, ir in matched:
            t = active[pi]
            t['mzs'].append(mz[ci]); t['ints'].append(it[ci])
            t['scans'].append(si);   t['rts'].append(rt)
            new_active[ci] = t
        for pi in unp_prev:
            fin(active[pi])
        for ci in unc_curr:
            new_active[ci] = new_t(mz[ci], it[ci], si, rt)

        prev_mz = mz; prev_int = it; active = new_active

    for t in active.values():
        fin(t)

    return finished
