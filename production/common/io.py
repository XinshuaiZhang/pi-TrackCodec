"""mzML reading and DIA/DDA detection"""
import os
from pathlib import Path
import numpy as np
from collections import Counter
from pyteomics import mzml
from tqdm import tqdm


TIMSTOF_UNSUPPORTED_MESSAGE = (
    "TrackCodec does not support Bruker timsTOF native .d directories or "
    "timsTOF/Bruker .d-derived mzML files. Exclude these inputs from TrackCodec "
    "benchmarks or convert them with a format-specific workflow outside TrackCodec."
)


def _path_mentions_timstof(path: str | os.PathLike) -> bool:
    p = Path(path)
    for part in p.parts:
        text = part.lower()
        if text.endswith(".d") or text in {"timstof", "tims-tof", "tims_tof"}:
            return True
    name = p.name.lower()
    if "timstof" in name or "tims-tof" in name or "tims_tof" in name:
        return True
    return False


def _mzml_header_mentions_timstof(path: str | os.PathLike, max_bytes: int = 8 * 1024 * 1024) -> bool:
    p = Path(path)
    if not p.is_file():
        return False
    try:
        with p.open("rb") as fh:
            header = fh.read(int(max_bytes)).lower()
    except OSError:
        return False
    if b"timstof" in header or b"tims-tof" in header or b"bruker daltonics" in header:
        return True
    # Bruker-converted timsTOF mzML commonly uses frame/scanStart/scanEnd IDs.
    return b"scanstart=" in header and b"scanend=" in header and b"frame=" in header


def assert_supported_mzml_input(path: str | os.PathLike) -> None:
    """Reject input classes intentionally outside the formal TrackCodec scope."""
    p = Path(path)
    if p.is_dir() and p.name.lower().endswith(".d"):
        raise NotImplementedError(TIMSTOF_UNSUPPORTED_MESSAGE)
    if _path_mentions_timstof(p) or _mzml_header_mentions_timstof(p):
        raise NotImplementedError(TIMSTOF_UNSUPPORTED_MESSAGE)


def _mzml_reader(path: str):
    """
    Whole-file codecs only need sequential iteration, not random spectrum lookup.
    Disabling the pyteomics byte-offset index avoids extra XML scanning overhead
    without changing decoded spectra.
    """
    assert_supported_mzml_input(path)
    return mzml.MzML(
        str(path),
        read_schema=False,
        iterative=True,
        use_index=False,
        huge_tree=True,
        decode_binary=True,
    )


def detect_file_type_by_name(mzml_path: str) -> str:
    """Quickly infer DIA/DDA from the file name."""
    if _path_mentions_timstof(mzml_path):
        raise NotImplementedError(TIMSTOF_UNSUPPORTED_MESSAGE)
    name = os.path.basename(mzml_path).upper()
    if 'DIA' in name:
        return 'DIA'
    if 'DDA' in name:
        return 'DDA'
    return 'UNKNOWN'


def detect_file_type(mzml_path: str, n_sample: int = 200) -> str:
    """
    Detect whether the mzML file is DIA or DDA (only called when the file name
    is inconclusive).
    """
    # Fast path: filename
    by_name = detect_file_type_by_name(mzml_path)
    if by_name != 'UNKNOWN':
        return by_name

    targets = []
    with _mzml_reader(mzml_path) as r:
        for i, spec in enumerate(r):
            if i >= n_sample:
                break
            if spec.get('ms level', 0) == 2:
                iw = spec.get('precursorList', {}).get('precursor', [{}])[0].get('isolationWindow', {})
                tmz = iw.get('isolation window target m/z')
                if tmz is not None:
                    targets.append(round(float(tmz), 2))

    if not targets:
        return 'UNKNOWN'
    counts = Counter(targets)
    n_unique = len(counts)
    n_total  = len(targets)
    if n_unique < 50 and n_total / n_unique > 5:
        return 'DIA'
    return 'DDA'


def discover_mzml_files(mzml_dir: str):
    """
    Scan the directory and return a list of [(path, stem, file_type)], DIA first.
    Use the file name for a fast decision to avoid parsing large files.
    """
    files = []
    for fn in sorted(os.listdir(mzml_dir)):
        if not fn.endswith('.mzML'):
            continue
        path  = os.path.join(mzml_dir, fn)
        if _path_mentions_timstof(path):
            continue
        stem  = os.path.splitext(fn)[0]
        ftype = detect_file_type_by_name(path)
        files.append((path, stem, ftype))
    # DIA first
    files.sort(key=lambda x: (0 if x[2] == 'DIA' else 1, x[1]))
    return files


def _dia_window_key(spec) -> tuple[float, float, float]:
    """Use the full DIA isolation window as the stable grouping key."""
    iw = spec.get('precursorList', {}).get('precursor', [{}])[0].get('isolationWindow', {})
    try:
        target = float(iw.get('isolation window target m/z', 0.0))
    except (TypeError, ValueError):
        target = 0.0
    try:
        lower = float(iw.get('isolation window lower offset', 0.0))
    except (TypeError, ValueError):
        lower = 0.0
    try:
        upper = float(iw.get('isolation window upper offset', 0.0))
    except (TypeError, ValueError):
        upper = 0.0
    return (round(target, 4), round(lower, 4), round(upper, 4))


def load_scans_with_ms2_payloads(mzml_path: str, cfg_or_file_type='DIA', max_ms1_scans=None, show_progress: bool = False):
    """
    Read all scans and return:
      ms1_scans: list of dict  {scan_idx, rt, mz_array (float64), intensity_array (float64)}
      ms2_by_window: dict {(target_mz, lower, upper) -> [dict ...]}   DIA only
      dda_ms2_scans: list[dict]   DDA only
      raw_ms1_bytes: raw MS1 data byte count (mz+intensity float64)
      raw_ms2_bytes: raw MS2 data byte count
    cfg_or_file_type: a ConfigV3 object or a 'DIA'/'DDA' string
    max_ms1_scans: if set, stop reading after this many MS1 scans (fast test mode)
    """
    # Accept either a ConfigV3 or a string
    if isinstance(cfg_or_file_type, str):
        file_type = cfg_or_file_type
    else:
        # ConfigV3 — infer file type from filename
        file_type = detect_file_type_by_name(mzml_path)
        if file_type == 'UNKNOWN':
            file_type = 'DIA'

    ms1_scans     = []
    ms2_by_window = {}
    dda_ms2_scans = []
    ms1_idx = 0
    ms2_idx = 0
    raw_ms1_bytes = 0
    raw_ms2_bytes = 0

    with _mzml_reader(mzml_path) as reader:
        iterator = reader
        if show_progress:
            iterator = tqdm(reader, desc=f"  reading {os.path.basename(mzml_path)}", miniters=500)
        for spec in iterator:
            ms_level = spec.get('ms level', 0)
            rt = float(spec.get('scanList', {}).get('scan', [{}])[0].get('scan start time', 0))
            mz_arr  = np.asarray(spec.get('m/z array',       []), dtype=np.float64)
            int_arr = np.asarray(spec.get('intensity array', []), dtype=np.float64)
            n = len(mz_arr)

            if ms_level == 1:
                ms1_scans.append({
                    'scan_idx':        ms1_idx,
                    'rt':              rt,
                    'mz_array':        mz_arr,
                    'intensity_array': int_arr,
                })
                raw_ms1_bytes += n * 16   # 2 × float64
                ms1_idx += 1
                if max_ms1_scans and ms1_idx >= max_ms1_scans:
                    break

            elif ms_level == 2:
                raw_ms2_bytes += n * 16
                if file_type == 'DIA':
                    window_key = _dia_window_key(spec)
                    tmz = float(window_key[0])
                    entry = {
                        'scan_idx':        ms2_idx,
                        'rt':              rt,
                        'mz_array':        mz_arr,
                        'intensity_array': int_arr,
                        'target_mz':       tmz,
                        'window_key':      window_key,
                    }
                    ms2_by_window.setdefault(window_key, []).append(entry)
                else:
                    iw = spec.get('precursorList', {}).get('precursor', [{}])[0].get('isolationWindow', {})
                    try:
                        tmz = round(float(iw.get('isolation window target m/z', 0.0)), 4)
                    except (TypeError, ValueError):
                        tmz = 0.0
                    dda_ms2_scans.append(
                        {
                            'scan_idx': ms2_idx,
                            'rt': rt,
                            'mz_array': mz_arr,
                            'intensity_array': int_arr,
                            'target_mz': tmz,
                            'original_id': spec.get('id'),
                        }
                    )
                ms2_idx += 1

    for k in ms2_by_window:
        ms2_by_window[k].sort(key=lambda x: x['rt'])

    return ms1_scans, ms2_by_window, dda_ms2_scans, raw_ms1_bytes, raw_ms2_bytes


def load_scans(mzml_path: str, cfg_or_file_type='DIA', max_ms1_scans=None, show_progress: bool = False):
    ms1_scans, ms2_by_window, _, raw_ms1_bytes, raw_ms2_bytes = load_scans_with_ms2_payloads(
        mzml_path,
        cfg_or_file_type=cfg_or_file_type,
        max_ms1_scans=max_ms1_scans,
        show_progress=show_progress,
    )
    return ms1_scans, ms2_by_window, raw_ms1_bytes, raw_ms2_bytes


def load_all_ms2_scans(mzml_path: str, max_ms2_scans: int = 0):
    """
    Read all MS2 scans and return:
      ms2_scans: list of dict {scan_idx, rt, mz_array, intensity_array, target_mz}
      raw_ms2_bytes: raw MS2 data byte count (mz+intensity float64)
    """
    ms2_scans = []
    raw_ms2_bytes = 0
    with _mzml_reader(mzml_path) as reader:
        for spec in reader:
            if spec.get('ms level', 0) != 2:
                continue
            rt = float(spec.get('scanList', {}).get('scan', [{}])[0].get('scan start time', 0))
            mz_arr = np.asarray(spec.get('m/z array', []), dtype=np.float64)
            int_arr = np.asarray(spec.get('intensity array', []), dtype=np.float64)
            iw = spec.get('precursorList', {}).get('precursor', [{}])[0].get('isolationWindow', {})
            try:
                tmz = round(float(iw.get('isolation window target m/z', 0.0)), 4)
            except (TypeError, ValueError):
                tmz = 0.0
            ms2_scans.append(
                {
                    'scan_idx': len(ms2_scans),
                    'rt': rt,
                    'mz_array': mz_arr,
                    'intensity_array': int_arr,
                    'target_mz': tmz,
                    'original_id': spec.get('id'),
                }
            )
            raw_ms2_bytes += len(mz_arr) * 16
            if max_ms2_scans > 0 and len(ms2_scans) >= max_ms2_scans:
                break
    return ms2_scans, raw_ms2_bytes
