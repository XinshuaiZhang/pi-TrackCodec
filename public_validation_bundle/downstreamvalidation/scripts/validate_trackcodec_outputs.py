from __future__ import annotations

import argparse
import base64
import csv
import concurrent.futures
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import time
import traceback
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from lxml import etree
from pyteomics import mzml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH_TABLE = ROOT / "inputs" / "manifests" / "downstream24_xic_pairs.csv"
DEFAULT_TRACKCODEC_ROOT = Path(
    os.environ.get("TRACKCODEC_ROOT", str(ROOT.parent.parent))
)
DEFAULT_TRACKCODEC_PARENT = Path(os.environ.get("TRACKCODEC_PARENT", str(DEFAULT_TRACKCODEC_ROOT.parent)))


def _default_mscodec_root(trackcodec_root: Path) -> Path:
    env_root = os.environ.get("MSCODEC_ROOT")
    if env_root:
        return Path(env_root)
    for candidate in (trackcodec_root.parent, *trackcodec_root.parents):
        if (candidate / "benchmark").exists():
            return candidate
    return trackcodec_root.parent


DEFAULT_MSCODEC_ROOT = _default_mscodec_root(DEFAULT_TRACKCODEC_ROOT)
DEFAULT_OUTPUT_ROOT = ROOT / "outputs" / "stream_loss_downstream24_top2500"

TOOL_ENV = {
    "diann": "TRACKCODEC_DIANN_BIN",
    "OpenMSInfo": "TRACKCODEC_OPENMSINFO_BIN",
    "FileFilter": "TRACKCODEC_OPENMS_FILEFILTER_BIN",
    "PeakPickerHiRes": "TRACKCODEC_OPENMS_PEAKPICKER_BIN",
    "FeatureFinderCentroided": "TRACKCODEC_OPENMS_FEATUREFINDER_BIN",
    "msfragger": "TRACKCODEC_MSFRAGGER_BIN",
    "philosopher": "TRACKCODEC_PHILOSOPHER_BIN",
    "ionquant": "TRACKCODEC_IONQUANT_BIN",
}
EXTRA_TOOL_PATHS = [
    Path(item)
    for item in os.environ.get("TRACKCODEC_EXTRA_TOOL_PATHS", "").split(os.pathsep)
    if item
]
MZML_NS = "http://psi.hupo.org/ms/mzml"
SPECTRUM_TAG = f"{{{MZML_NS}}}spectrum"
CV_PARAM_TAG = f"{{{MZML_NS}}}cvParam"
MZ_ARRAY_ACCESSION = "MS:1000514"
INTENSITY_ARRAY_ACCESSION = "MS:1000515"
MS_LEVEL_ACCESSION = "MS:1000511"
SCAN_START_TIME_ACCESSION = "MS:1000016"
ZLIB_ACCESSION = "MS:1000574"
NO_COMPRESSION_ACCESSION = "MS:1000576"
FLOAT32_ACCESSION = "MS:1000521"
FLOAT64_ACCESSION = "MS:1000523"


@dataclass
class Pair:
    index: int
    file_name: str
    original_path: Path
    archive_path: Path
    dataset: str
    reconstructed_path: Path | None = None


def _safe_name(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    out = out.strip("._")
    return out or "item"


def _resolve_public_path(value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    replacements = {
        "<MSCODEC_ROOT>": DEFAULT_MSCODEC_ROOT,
        "<TRACKCODEC_PARENT>": DEFAULT_TRACKCODEC_PARENT,
        "<TRACKCODEC_ROOT>": DEFAULT_TRACKCODEC_ROOT,
        "<TRACKCODEC_PUBLIC_RELEASE>": DEFAULT_TRACKCODEC_ROOT,
    }
    for token, root in replacements.items():
        if token in text:
            text = text.replace(token, str(root))
    text = os.path.expandvars(text)
    return Path(text).expanduser()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows([{k: _csv_value(row.get(k, "")) for k in fields} for row in rows])


def _merge_rows_by_index(path: Path, new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for row in _read_csv_rows(path):
        try:
            merged[int(row.get("index", ""))] = dict(row)
        except ValueError:
            continue
    for row in new_rows:
        try:
            merged[int(row.get("index", ""))] = dict(row)
        except (TypeError, ValueError):
            continue
    return [merged[key] for key in sorted(merged)]


def _csv_value(value: Any) -> Any:
    value = _json_safe(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return value


def _read_pairs(path_table: Path) -> list[Pair]:
    pairs: list[Pair] = []
    if path_table.suffix.lower() == ".csv":
        rows = _read_csv_rows(path_table)
        for row in rows:
            idx_text = str(row.get("index", "")).strip()
            if not idx_text:
                continue
            idx = int(idx_text)
            file_name = str(row.get("file_name", "")).strip()
            original = _resolve_public_path(row.get("original_path", "")) or Path("__missing_original__")
            archive_text = str(row.get("archive_path", "")).strip()
            archive = _resolve_public_path(archive_text) if archive_text else Path("__missing_archive__")
            if archive is None:
                archive = Path("__missing_archive__")
            dataset = str(row.get("dataset", "")).strip() or ("data_stackzdpd" if "data_StackZDPD" in str(original) else "full8")
            reconstructed_text = str(row.get("reconstructed_path", "")).strip()
            reconstructed = _resolve_public_path(reconstructed_text) if reconstructed_text else None
            pairs.append(Pair(idx, file_name, original, archive, dataset, reconstructed))
        if not pairs:
            raise ValueError(f"No path rows parsed from {path_table}")
        return pairs

    text = path_table.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if line.startswith("|---") or "压缩前" in line or "TrackCodec" in line and "# |" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        try:
            idx = int(cells[0])
        except ValueError:
            continue
        file_name = _strip_backticks(cells[1])
        original = _resolve_public_path(_strip_backticks(cells[2])) or Path("__missing_original__")
        archive = _resolve_public_path(_strip_backticks(cells[3])) or Path("__missing_archive__")
        dataset = "data_stackzdpd" if "data_StackZDPD" in str(original) else "full8"
        reconstructed = None
        if len(cells) >= 5:
            recon_text = _strip_backticks(cells[4])
            if recon_text:
                reconstructed = _resolve_public_path(recon_text)
        pairs.append(Pair(idx, file_name, original, archive, dataset, reconstructed))
    if not pairs:
        raise ValueError(f"No path rows parsed from {path_table}")
    return pairs


def _strip_backticks(value: str) -> str:
    value = value.strip()
    if value.startswith("`") and value.endswith("`"):
        return value[1:-1]
    return value


def _bootstrap_trackcodec(trackcodec_root: Path) -> None:
    if "TrackCodec" in sys.modules:
        return
    # The default body-spool indexed mzML writer is Linux-friendly but can hit
    # Windows file locking because lxml reopens a NamedTemporaryFile path.
    os.environ.setdefault("TRACKCODEC_ENABLE_DIRECT_OUTPUT_INDEXED_MZML_WRITER", "1")
    init_py = trackcodec_root / "__init__.py"
    if not init_py.exists():
        raise FileNotFoundError(f"TrackCodec __init__.py not found: {init_py}")
    spec = importlib.util.spec_from_file_location(
        "TrackCodec",
        init_py,
        submodule_search_locations=[str(trackcodec_root)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build import spec for {trackcodec_root}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["TrackCodec"] = module
    spec.loader.exec_module(module)


def _pair_rows(pairs: list[Pair]) -> list[dict[str, Any]]:
    rows = []
    total = len(pairs)
    for pos, pair in enumerate(pairs, start=1):
        stats_path = pair.archive_path.with_suffix(".stats.json")
        rows.append(
            {
                "index": pair.index,
                "dataset": pair.dataset,
                "file_name": pair.file_name,
                "original_path": str(pair.original_path),
                "original_exists": pair.original_path.exists(),
                "original_bytes": pair.original_path.stat().st_size if pair.original_path.exists() else "",
                "archive_path": str(pair.archive_path),
                "archive_exists": pair.archive_path.exists(),
                "archive_bytes": pair.archive_path.stat().st_size if pair.archive_path.exists() else "",
                "stats_json": str(stats_path),
                "stats_json_exists": stats_path.exists(),
            }
        )
    return rows


def _reconstructed_path(output_root: Path, pair: Pair, *, reuse_recon_root: Path | None = None) -> Path:
    if pair.reconstructed_path is not None:
        return pair.reconstructed_path
    # Keep the physical path short. Some Windows/lxml writer paths fail near
    # MAX_PATH even when the parent directory is writable.
    recon_root = reuse_recon_root if reuse_recon_root is not None else output_root
    return recon_root / "recon" / f"{pair.index:02d}.mzML"


def _decode_archives(
    pairs: list[Pair],
    output_root: Path,
    *,
    trackcodec_root: Path,
    binary_compression: str,
    section_workers: int,
    ms2_segment_workers: int | None,
    decode_segment_workers: int | None,
    skip_existing: bool,
    reuse_recon_root: Path | None = None,
) -> list[dict[str, Any]]:
    _bootstrap_trackcodec(trackcodec_root)
    from TrackCodec.production.mzml.archive_codec import MzMLSectionArchiveCodec

    codec = MzMLSectionArchiveCodec(
        ms2_section_workers=int(section_workers),
        ms2_segment_workers=ms2_segment_workers,
        decode_segment_workers=decode_segment_workers or int(section_workers),
    )
    rows: list[dict[str, Any]] = []
    total = len(pairs)
    for pos, pair in enumerate(pairs, start=1):
        out_path = _reconstructed_path(output_root, pair, reuse_recon_root=reuse_recon_root)
        row: dict[str, Any] = {
            "index": pair.index,
            "dataset": pair.dataset,
            "file_name": pair.file_name,
            "archive_path": str(pair.archive_path),
            "reconstructed_path": str(out_path),
            "binary_compression": binary_compression,
        }
        start = time.perf_counter()
        try:
            if not pair.archive_path.exists():
                raise FileNotFoundError(pair.archive_path)
            if skip_existing and out_path.exists() and out_path.stat().st_size > 0:
                row.update(
                    {
                        "status": "skipped_existing",
                        "elapsed_s": 0.0,
                        "reconstructed_bytes": out_path.stat().st_size,
                    }
                )
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                codec.decode_to_mzml_file(
                    pair.archive_path,
                    out_path,
                    binary_compression=binary_compression,
                )
                row.update(
                    {
                        "status": "ok",
                        "elapsed_s": time.perf_counter() - start,
                        "reconstructed_bytes": out_path.stat().st_size,
                    }
                )
        except Exception as exc:
            row.update(
                {
                    "status": "error",
                    "elapsed_s": time.perf_counter() - start,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=8),
                }
            )
        rows.append(row)
        _write_csv(output_root / "tables" / "decode_status.csv", rows)
        print(f"[decode] {pos:02d}/{total:02d} [{pair.index:02d}] {pair.file_name}: {row['status']}", flush=True)
    return rows


def _structure_ok(roundtrip: dict[str, Any]) -> bool:
    return (
        int(roundtrip.get("missing_original_spectra", 0)) == 0
        and int(roundtrip.get("missing_reconstructed_spectra", 0)) == 0
        and int(roundtrip.get("spectrum_id_mismatch_count", 0)) == 0
        and int(roundtrip.get("ms_level_mismatch_count", 0)) == 0
        and int(roundtrip.get("rt_mismatch_count", 0)) == 0
        and int(roundtrip.get("array_length_mismatch_count", 0)) == 0
    )


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _find_cv_value(elem: Any, accession: str, default: str = "") -> str:
    for cv in elem.iter(CV_PARAM_TAG):
        if cv.get("accession") == accession:
            return cv.get("value", default)
    return default


def _decode_binary_array(binary_array_elem: Any) -> tuple[str, np.ndarray] | None:
    accessions = {cv.get("accession") for cv in binary_array_elem.iter(CV_PARAM_TAG)}
    if MZ_ARRAY_ACCESSION in accessions:
        kind = "mz"
    elif INTENSITY_ARRAY_ACCESSION in accessions:
        kind = "intensity"
    else:
        return None

    binary_elem = None
    for child in binary_array_elem.iter():
        if _local_name(child.tag) == "binary":
            binary_elem = child
            break
    if binary_elem is None or not binary_elem.text:
        return kind, np.asarray([], dtype=np.float64)

    payload = base64.b64decode(binary_elem.text.encode("ascii"))
    if ZLIB_ACCESSION in accessions:
        payload = zlib.decompress(payload)
    elif NO_COMPRESSION_ACCESSION not in accessions:
        raise ValueError(f"Unsupported mzML binary compression accessions: {sorted(x for x in accessions if x)}")

    if FLOAT64_ACCESSION in accessions:
        arr = np.frombuffer(payload, dtype="<f8")
    elif FLOAT32_ACCESSION in accessions:
        arr = np.frombuffer(payload, dtype="<f4")
    else:
        raise ValueError(f"Unsupported mzML binary dtype accessions: {sorted(x for x in accessions if x)}")
    return kind, arr.astype(np.float64, copy=False)


def _iter_spectra_lxml(path: Path) -> Iterable[dict[str, Any]]:
    context = etree.iterparse(
        str(path),
        events=("end",),
        tag=SPECTRUM_TAG,
        recover=True,
        huge_tree=True,
    )
    for _, elem in context:
        ms_level = int(float(_find_cv_value(elem, MS_LEVEL_ACCESSION, "0") or "0"))
        rt = float(_find_cv_value(elem, SCAN_START_TIME_ACCESSION, "0") or "0")
        mz_arr = np.asarray([], dtype=np.float64)
        int_arr = np.asarray([], dtype=np.float64)
        for child in elem.iter():
            if _local_name(child.tag) != "binaryDataArray":
                continue
            decoded = _decode_binary_array(child)
            if decoded is None:
                continue
            kind, arr = decoded
            if kind == "mz":
                mz_arr = arr
            elif kind == "intensity":
                int_arr = arr
        yield {
            "id": elem.get("id"),
            "ms_level": ms_level,
            "rt": rt,
            "mz_array": mz_arr,
            "intensity_array": int_arr,
        }
        elem.clear()
        parent = elem.getparent()
        while parent is not None and elem.getprevious() is not None:
            del parent[0]


def _vector_stats_local(values: list[float]) -> dict[str, float]:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        arr = np.asarray([0.0], dtype=np.float64)
    return {
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(np.max(arr)),
    }


def _safe_rel(ref: float, val: float) -> float:
    denom = abs(float(ref))
    if denom == 0.0:
        return 0.0 if float(val) == 0.0 else float("inf")
    return abs(float(val) - float(ref)) / denom


def _compare_files_array_only(original: Path, reconstructed: Path) -> dict[str, Any]:
    from itertools import zip_longest

    max_abs_mz = 0.0
    max_abs_int = 0.0
    spectrum_count = 0
    ms1_count = 0
    ms2_count = 0
    compared_point_count = 0
    total_nonzero_orig_points = 0
    total_nonzero_recon_points = 0
    spectrum_id_mismatch_count = 0
    ms_level_mismatch_count = 0
    rt_mismatch_count = 0
    array_length_mismatch_count = 0
    nonzero_count_mismatch_count = 0
    spectra_with_any_mz_error = 0
    spectra_with_any_intensity_error = 0
    missing_original_spectra = 0
    missing_reconstructed_spectra = 0
    spectrum_mean_abs_mz: list[float] = []
    spectrum_p95_abs_mz: list[float] = []
    spectrum_mean_abs_int: list[float] = []
    spectrum_p95_abs_int: list[float] = []
    spectrum_tic_rel: list[float] = []
    spectrum_bpi_rel: list[float] = []

    for spectrum_count, pair in enumerate(
        zip_longest(_iter_spectra_lxml(original), _iter_spectra_lxml(reconstructed)),
        start=1,
    ):
        orig, recon = pair
        if orig is None:
            missing_original_spectra += 1
            continue
        if recon is None:
            missing_reconstructed_spectra += 1
            continue
        if orig["id"] != recon["id"]:
            spectrum_id_mismatch_count += 1
        if orig["ms_level"] != recon["ms_level"]:
            ms_level_mismatch_count += 1
        if abs(float(orig["rt"]) - float(recon["rt"])) > 0.0:
            rt_mismatch_count += 1

        n_orig = len(orig["mz_array"])
        n_recon = len(recon["mz_array"])
        if n_orig != n_recon:
            array_length_mismatch_count += 1
        n_cmp = min(n_orig, n_recon)
        mz_diff = np.abs(orig["mz_array"][:n_cmp] - recon["mz_array"][:n_cmp]) if n_cmp else np.asarray([], dtype=np.float64)
        int_diff = (
            np.abs(orig["intensity_array"][:n_cmp] - recon["intensity_array"][:n_cmp])
            if n_cmp
            else np.asarray([], dtype=np.float64)
        )
        max_mz_local = float(mz_diff.max(initial=0.0))
        max_int_local = float(int_diff.max(initial=0.0))
        max_abs_mz = max(max_abs_mz, max_mz_local)
        max_abs_int = max(max_abs_int, max_int_local)
        compared_point_count += n_cmp
        if max_mz_local > 0.0:
            spectra_with_any_mz_error += 1
        if max_int_local > 0.0:
            spectra_with_any_intensity_error += 1

        orig_nonzero = int(np.count_nonzero(orig["intensity_array"]))
        recon_nonzero = int(np.count_nonzero(recon["intensity_array"]))
        total_nonzero_orig_points += orig_nonzero
        total_nonzero_recon_points += recon_nonzero
        if orig_nonzero != recon_nonzero:
            nonzero_count_mismatch_count += 1

        spectrum_mean_abs_mz.append(float(mz_diff.mean()) if n_cmp else 0.0)
        spectrum_p95_abs_mz.append(float(np.percentile(mz_diff, 95)) if n_cmp else 0.0)
        spectrum_mean_abs_int.append(float(int_diff.mean()) if n_cmp else 0.0)
        spectrum_p95_abs_int.append(float(np.percentile(int_diff, 95)) if n_cmp else 0.0)
        tic_orig = float(np.sum(orig["intensity_array"], dtype=np.float64))
        tic_recon = float(np.sum(recon["intensity_array"], dtype=np.float64))
        bpi_orig = float(np.max(orig["intensity_array"], initial=0.0))
        bpi_recon = float(np.max(recon["intensity_array"], initial=0.0))
        spectrum_tic_rel.append(_safe_rel(tic_orig, tic_recon))
        spectrum_bpi_rel.append(_safe_rel(bpi_orig, bpi_recon))
        if orig["ms_level"] == 1:
            ms1_count += 1
        elif orig["ms_level"] == 2:
            ms2_count += 1

    return {
        "roundtrip_engine": "array_only_recovering_lxml",
        "spectrum_count": spectrum_count,
        "ms1_count": ms1_count,
        "ms2_count": ms2_count,
        "compared_point_count": compared_point_count,
        "total_nonzero_original_points": total_nonzero_orig_points,
        "total_nonzero_reconstructed_points": total_nonzero_recon_points,
        "missing_original_spectra": missing_original_spectra,
        "missing_reconstructed_spectra": missing_reconstructed_spectra,
        "spectrum_id_mismatch_count": spectrum_id_mismatch_count,
        "ms_level_mismatch_count": ms_level_mismatch_count,
        "rt_mismatch_count": rt_mismatch_count,
        "array_length_mismatch_count": array_length_mismatch_count,
        "nonzero_count_mismatch_count": nonzero_count_mismatch_count,
        "spectra_with_any_mz_error": spectra_with_any_mz_error,
        "spectra_with_any_intensity_error": spectra_with_any_intensity_error,
        "max_abs_mz_error": max_abs_mz,
        "max_abs_intensity_error": max_abs_int,
        "spectrum_mean_abs_mz_error": _vector_stats_local(spectrum_mean_abs_mz),
        "spectrum_p95_abs_mz_error": _vector_stats_local(spectrum_p95_abs_mz),
        "spectrum_mean_abs_intensity_error": _vector_stats_local(spectrum_mean_abs_int),
        "spectrum_p95_abs_intensity_error": _vector_stats_local(spectrum_p95_abs_int),
        "spectrum_tic_rel_error": _vector_stats_local(spectrum_tic_rel),
        "spectrum_bpi_rel_error": _vector_stats_local(spectrum_bpi_rel),
        "aux_counts_match": "",
        "aux_binary_raw_equal": "",
        "binary_stripped_xml_equal": "",
        "xml_without_binary_arrays_equal": "",
        "effective_intensity_error_ceiling": 0.1,
        "intensity_dtype_match": "",
    }


def _run_roundtrip(
    pairs: list[Pair],
    output_root: Path,
    *,
    trackcodec_root: Path,
    mz_error_ceiling: float,
    intensity_error_ceiling_float64: float,
    intensity_error_ceiling_float32: float,
    ceiling_eps: float,
    write_per_spectrum: bool,
    reuse_recon_root: Path | None = None,
) -> list[dict[str, Any]]:
    _bootstrap_trackcodec(trackcodec_root)
    from TrackCodec.production.validation.roundtrip import compare_files

    table_path = output_root / "tables" / "roundtrip_summary.csv"
    rows: list[dict[str, Any]] = []
    total = len(pairs)
    for pos, pair in enumerate(pairs, start=1):
        recon_path = _reconstructed_path(output_root, pair, reuse_recon_root=reuse_recon_root)
        summary_json = output_root / "roundtrip_json" / f"{pair.index:02d}_{_safe_name(Path(pair.file_name).stem)}.json"
        per_spectrum_csv = (
            output_root / "per_spectrum_roundtrip" / f"{pair.index:02d}_{_safe_name(Path(pair.file_name).stem)}.csv"
            if write_per_spectrum
            else None
        )
        row: dict[str, Any] = {
            "index": pair.index,
            "dataset": pair.dataset,
            "file_name": pair.file_name,
            "original_path": str(pair.original_path),
            "reconstructed_path": str(recon_path),
        }
        start = time.perf_counter()
        try:
            if not pair.original_path.exists():
                raise FileNotFoundError(pair.original_path)
            if not recon_path.exists():
                raise FileNotFoundError(recon_path)
            try:
                rt = compare_files(
                    pair.original_path,
                    recon_path,
                    per_spectrum_csv=per_spectrum_csv,
                    intensity_error_ceiling_float64=float(intensity_error_ceiling_float64),
                    intensity_error_ceiling_float32=float(intensity_error_ceiling_float32),
                )
                rt.setdefault("roundtrip_engine", "trackcodec_compare_files")
            except etree.XMLSyntaxError:
                rt = _compare_files_array_only(pair.original_path, recon_path)
            structure_ok = _structure_ok(rt)
            mz_ok = float(rt["max_abs_mz_error"]) <= float(mz_error_ceiling + ceiling_eps)
            int_ok = float(rt["max_abs_intensity_error"]) <= float(rt["effective_intensity_error_ceiling"]) + ceiling_eps
            rt["structure_within_ceiling"] = bool(structure_ok)
            rt["mz_numeric_within_ceiling"] = bool(mz_ok)
            rt["intensity_numeric_within_ceiling"] = bool(int_ok)
            rt["roundtrip_pass"] = bool(structure_ok and mz_ok and int_ok)
            payload = {
                "pair": _json_safe(pair.__dict__),
                "mz_error_ceiling": mz_error_ceiling,
                "intensity_error_ceiling_float64": intensity_error_ceiling_float64,
                "intensity_error_ceiling_float32": intensity_error_ceiling_float32,
                "roundtrip": rt,
            }
            _write_json(summary_json, payload)
            row.update(
                {
                    "status": "ok",
                    "roundtrip_engine": rt.get("roundtrip_engine", ""),
                    "elapsed_s": time.perf_counter() - start,
                    "spectrum_count": rt.get("spectrum_count", 0),
                    "ms1_count": rt.get("ms1_count", 0),
                    "ms2_count": rt.get("ms2_count", 0),
                    "compared_point_count": rt.get("compared_point_count", 0),
                    "max_abs_mz_error": rt.get("max_abs_mz_error", ""),
                    "max_abs_intensity_error": rt.get("max_abs_intensity_error", ""),
                    "spectrum_p95_abs_mz_error_p95": rt.get("spectrum_p95_abs_mz_error", {}).get("p95", ""),
                    "spectrum_p95_abs_intensity_error_p95": rt.get("spectrum_p95_abs_intensity_error", {}).get("p95", ""),
                    "spectrum_tic_rel_error_p95": rt.get("spectrum_tic_rel_error", {}).get("p95", ""),
                    "spectrum_bpi_rel_error_p95": rt.get("spectrum_bpi_rel_error", {}).get("p95", ""),
                    "missing_original_spectra": rt.get("missing_original_spectra", ""),
                    "missing_reconstructed_spectra": rt.get("missing_reconstructed_spectra", ""),
                    "spectrum_id_mismatch_count": rt.get("spectrum_id_mismatch_count", ""),
                    "ms_level_mismatch_count": rt.get("ms_level_mismatch_count", ""),
                    "rt_mismatch_count": rt.get("rt_mismatch_count", ""),
                    "array_length_mismatch_count": rt.get("array_length_mismatch_count", ""),
                    "nonzero_count_mismatch_count": rt.get("nonzero_count_mismatch_count", ""),
                    "aux_counts_match": rt.get("aux_counts_match", ""),
                    "aux_binary_raw_equal": rt.get("aux_binary_raw_equal", ""),
                    "binary_stripped_xml_equal": rt.get("binary_stripped_xml_equal", ""),
                    "xml_without_binary_arrays_equal": rt.get("xml_without_binary_arrays_equal", ""),
                    "effective_intensity_error_ceiling": rt.get("effective_intensity_error_ceiling", ""),
                    "intensity_dtype_match": rt.get("intensity_dtype_match", ""),
                    "structure_within_ceiling": structure_ok,
                    "mz_numeric_within_ceiling": mz_ok,
                    "intensity_numeric_within_ceiling": int_ok,
                    "roundtrip_pass": bool(structure_ok and mz_ok and int_ok),
                    "summary_json": str(summary_json),
                    "per_spectrum_csv": str(per_spectrum_csv) if per_spectrum_csv else "",
                }
            )
        except Exception as exc:
            row.update(
                {
                    "status": "error",
                    "elapsed_s": time.perf_counter() - start,
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=8),
                }
            )
        rows.append(row)
        _write_csv(table_path, _merge_rows_by_index(table_path, rows))
        print(f"[roundtrip] {pos:02d}/{total:02d} [{pair.index:02d}] {pair.file_name}: {row['status']}", flush=True)
    return rows


def _iter_spectra(path: Path) -> Iterable[dict[str, Any]]:
    # Use the local lxml decoder for XIC extraction as well. It avoids
    # pyteomics/lxml failures seen on some valid, very long binary lines.
    yield from _iter_spectra_lxml(path)


def _scan_rt(spec: dict[str, Any]) -> float:
    if "rt" in spec:
        return float(spec.get("rt", 0.0))
    return float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))


def _is_ms1(spec: dict[str, Any]) -> bool:
    try:
        if "ms_level" in spec:
            return int(spec.get("ms_level", 0)) == 1
        return int(spec.get("ms level", 0)) == 1
    except Exception:
        return False


def _as_sorted_arrays(spec: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    mz_arr = np.asarray(spec.get("m/z array", spec.get("mz_array", [])), dtype=np.float64)
    int_arr = np.asarray(spec.get("intensity array", spec.get("intensity_array", [])), dtype=np.float64)
    if mz_arr.size > 1 and np.any(np.diff(mz_arr) < 0):
        order = np.argsort(mz_arr)
        mz_arr = mz_arr[order]
        int_arr = int_arr[order]
    return mz_arr, int_arr


def _discover_xic_targets(path: Path, *, top_k: int, peaks_per_scan: int, bin_width_da: float) -> list[dict[str, Any]]:
    buckets: dict[int, list[float]] = {}
    ms1_scans = 0
    for spec in _iter_spectra(path):
        if not _is_ms1(spec):
            continue
        ms1_scans += 1
        scan_rt = _scan_rt(spec)
        mz_arr, int_arr = _as_sorted_arrays(spec)
        if mz_arr.size == 0:
            continue
        take = min(int(peaks_per_scan), int_arr.size)
        if take <= 0:
            continue
        if take < int_arr.size:
            idx = np.argpartition(int_arr, -take)[-take:]
        else:
            idx = np.arange(int_arr.size)
        for mz_value, intensity_value in zip(mz_arr[idx], int_arr[idx]):
            intensity = float(intensity_value)
            if intensity <= 0.0:
                continue
            key = int(round(float(mz_value) / float(bin_width_da)))
            current = buckets.get(key)
            if current is None:
                buckets[key] = [float(mz_value) * intensity, intensity, 1.0, float(scan_rt) * intensity]
            else:
                current[0] += float(mz_value) * intensity
                current[1] += intensity
                current[2] += 1.0
                current[3] += float(scan_rt) * intensity
    ranked = []
    for key, (weighted_mz_sum, intensity_sum, count, weighted_rt_sum) in buckets.items():
        if intensity_sum <= 0.0:
            continue
        ranked.append(
            {
                "target_mz": float(weighted_mz_sum / intensity_sum),
                "target_rt": float(weighted_rt_sum / intensity_sum),
                "bucket_key": int(key),
                "bucket_width_da": float(bin_width_da),
                "seed_intensity_sum": float(intensity_sum),
                "seed_peak_count": int(count),
                "ms1_scans": int(ms1_scans),
            }
        )
    ranked.sort(key=lambda row: (-float(row["seed_intensity_sum"]), float(row["target_mz"])))
    for rank, row in enumerate(ranked, start=1):
        row["target_rank"] = int(rank)
        row["seed_signal_score"] = float(row["seed_intensity_sum"])
    return ranked[: int(top_k)]


def _extract_xic_matrix(path: Path, targets_mz: np.ndarray, *, ppm: float) -> tuple[np.ndarray, np.ndarray]:
    lows = targets_mz * (1.0 - float(ppm) * 1e-6)
    highs = targets_mz * (1.0 + float(ppm) * 1e-6)
    rows: list[np.ndarray] = []
    rts: list[float] = []
    for spec in _iter_spectra(path):
        if not _is_ms1(spec):
            continue
        mz_arr, int_arr = _as_sorted_arrays(spec)
        if mz_arr.size == 0:
            values = np.zeros(targets_mz.size, dtype=np.float64)
        else:
            csum = np.concatenate(([0.0], np.cumsum(int_arr, dtype=np.float64)))
            left = np.searchsorted(mz_arr, lows, side="left")
            right = np.searchsorted(mz_arr, highs, side="right")
            values = csum[right] - csum[left]
        rows.append(values.astype(np.float64, copy=False))
        rts.append(_scan_rt(spec))
    if not rows:
        return np.zeros((0, targets_mz.size), dtype=np.float64), np.asarray([], dtype=np.float64)
    return np.vstack(rows), np.asarray(rts, dtype=np.float64)


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    n = min(len(x), len(y))
    sx = sy = sxx = syy = sxy = 0.0
    count = 0
    for i in range(n):
        xi = float(x[i])
        yi = float(y[i])
        if not math.isfinite(xi) or not math.isfinite(yi):
            continue
        sx += xi
        sy += yi
        sxx += xi * xi
        syy += yi * yi
        sxy += xi * yi
        count += 1
    if count < 2:
        return 1.0
    x_ss = sxx - (sx * sx / count)
    y_ss = syy - (sy * sy / count)
    xy_ss = sxy - (sx * sy / count)
    if abs(x_ss) < 1e-300:
        x_ss = 0.0
    if abs(y_ss) < 1e-300:
        y_ss = 0.0
    if x_ss == 0.0 and y_ss == 0.0:
        return 1.0
    if x_ss == 0.0 or y_ss == 0.0:
        return 0.0
    return float(max(-1.0, min(1.0, xy_ss / math.sqrt(x_ss * y_ss))))


def _trapz(y: np.ndarray, x: np.ndarray) -> float:
    fn = getattr(np, "trapezoid", None) or np.trapz
    return float(fn(y, x))


def _stat(values: list[float], name: str) -> float:
    arr = np.asarray(values, dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    if name == "mean":
        return float(np.mean(arr))
    if name == "median":
        return float(np.median(arr))
    if name == "p95":
        return float(np.percentile(arr, 95))
    if name == "p99":
        return float(np.percentile(arr, 99))
    if name == "min":
        return float(np.min(arr))
    if name == "max":
        return float(np.max(arr))
    raise ValueError(name)


def _run_xic(
    pairs: list[Pair],
    output_root: Path,
    *,
    top_k: int,
    peaks_per_scan: int,
    bin_width_da: float,
    ppm: float,
    reuse_recon_root: Path | None = None,
    skip_existing: bool = True,
    xic_workers: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary_table = output_root / "tables" / "xic_summary.csv"
    target_table = output_root / "tables" / "xic_target_metrics.csv"
    existing_summary = _read_csv_rows(summary_table)
    done_indices = {
        int(row.get("index", "0"))
        for row in existing_summary
        if str(row.get("status", "")).strip() == "ok" and str(row.get("index", "")).strip()
    }
    pairs_to_run = [pair for pair in pairs if not (skip_existing and pair.index in done_indices)]
    rerun_indices = {pair.index for pair in pairs_to_run}
    existing_targets = [row for row in _read_csv_rows(target_table) if str(row.get("index", "")).isdigit() and int(row["index"]) not in rerun_indices]
    summary_rows: list[dict[str, Any]] = []
    target_rows: list[dict[str, Any]] = []
    total = len(pairs_to_run)
    if total == 0:
        print("[xic] nothing to run; all requested files already completed", flush=True)
        return existing_summary, existing_targets

    if int(xic_workers) <= 1:
        for pos, pair in enumerate(pairs_to_run, start=1):
            row, per_target = _compute_xic_for_pair(pair, output_root, top_k=top_k, peaks_per_scan=peaks_per_scan, bin_width_da=bin_width_da, ppm=ppm, reuse_recon_root=reuse_recon_root)
            summary_rows.append(row)
            target_rows.extend(per_target)
            _write_csv(summary_table, _merge_rows_by_index(summary_table, summary_rows))
            _write_csv(target_table, existing_targets + target_rows)
            print(f"[xic] {pos:02d}/{total:02d} [{pair.index:02d}] {pair.file_name}: {row['status']}", flush=True)
        return summary_rows, target_rows

    with concurrent.futures.ProcessPoolExecutor(max_workers=int(xic_workers)) as executor:
        future_map = {
            executor.submit(
                _compute_xic_for_pair,
                pair,
                output_root,
                top_k=top_k,
                peaks_per_scan=peaks_per_scan,
                bin_width_da=bin_width_da,
                ppm=ppm,
                reuse_recon_root=reuse_recon_root,
            ): pair
            for pair in pairs_to_run
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_map):
            pair = future_map[future]
            try:
                row, per_target = future.result()
            except Exception as exc:
                row = {
                    "index": pair.index,
                    "dataset": pair.dataset,
                    "file_name": pair.file_name,
                    "original_path": str(pair.original_path),
                    "reconstructed_path": str(_reconstructed_path(output_root, pair, reuse_recon_root=reuse_recon_root)),
                    "top_k": int(top_k),
                    "peaks_per_scan": int(peaks_per_scan),
                    "bin_width_da": float(bin_width_da),
                    "xic_ppm": float(ppm),
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(limit=8),
                }
                per_target = []
            completed += 1
            summary_rows.append(row)
            target_rows.extend(per_target)
            _write_csv(summary_table, _merge_rows_by_index(summary_table, summary_rows))
            _write_csv(target_table, existing_targets + target_rows)
            print(f"[xic] {completed:02d}/{total:02d} [{pair.index:02d}] {pair.file_name}: {row['status']}", flush=True)
    return summary_rows, target_rows


def _compute_xic_for_pair(
    pair: Pair,
    output_root: Path,
    *,
    top_k: int,
    peaks_per_scan: int,
    bin_width_da: float,
    ppm: float,
    reuse_recon_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    recon_path = _reconstructed_path(output_root, pair, reuse_recon_root=reuse_recon_root)
    row: dict[str, Any] = {
        "index": pair.index,
        "dataset": pair.dataset,
        "file_name": pair.file_name,
        "original_path": str(pair.original_path),
        "reconstructed_path": str(recon_path),
        "top_k": int(top_k),
        "peaks_per_scan": int(peaks_per_scan),
        "bin_width_da": float(bin_width_da),
        "xic_ppm": float(ppm),
    }
    target_rows: list[dict[str, Any]] = []
    start = time.perf_counter()
    try:
        if not pair.original_path.exists():
            raise FileNotFoundError(pair.original_path)
        if not recon_path.exists():
            raise FileNotFoundError(recon_path)
        targets = _discover_xic_targets(
            pair.original_path,
            top_k=top_k,
            peaks_per_scan=peaks_per_scan,
            bin_width_da=bin_width_da,
        )
        if not targets:
            raise RuntimeError("No XIC targets discovered")
        targets_mz = np.asarray([target["target_mz"] for target in targets], dtype=np.float64)
        orig_matrix, orig_rt = _extract_xic_matrix(pair.original_path, targets_mz, ppm=ppm)
        recon_matrix, recon_rt = _extract_xic_matrix(recon_path, targets_mz, ppm=ppm)
        n_scans = min(orig_matrix.shape[0], recon_matrix.shape[0])
        n_targets = min(orig_matrix.shape[1], recon_matrix.shape[1])
        orig_matrix = orig_matrix[:n_scans, :n_targets]
        recon_matrix = recon_matrix[:n_scans, :n_targets]
        orig_rt = orig_rt[:n_scans]
        recon_rt = recon_rt[:n_scans]
        rt_abs_max = float(np.max(np.abs(orig_rt - recon_rt), initial=0.0)) if n_scans else 0.0

        area_abs_rel_errors: list[float] = []
        area_signed_rel_errors: list[float] = []
        pearsons: list[float] = []
        apex_shifts: list[float] = []
        tic_abs_rel_errors: list[float] = []

        for j in range(n_targets):
            orig_xic = orig_matrix[:, j]
            recon_xic = recon_matrix[:, j]
            area_orig = _trapz(orig_xic, orig_rt) if n_scans > 1 else float(np.sum(orig_xic))
            area_recon = _trapz(recon_xic, recon_rt) if n_scans > 1 else float(np.sum(recon_xic))
            denom = max(abs(area_orig), 1.0)
            signed_area_rel = float((area_recon - area_orig) / denom)
            abs_area_rel = abs(signed_area_rel)
            corr = _pearson(orig_xic, recon_xic)
            apex_shift = 0.0
            if n_scans:
                apex_shift = abs(float(recon_rt[int(np.argmax(recon_xic))] - orig_rt[int(np.argmax(orig_xic))]))
            tic_orig = float(np.sum(orig_xic, dtype=np.float64))
            tic_recon = float(np.sum(recon_xic, dtype=np.float64))
            tic_rel = abs(tic_recon - tic_orig) / max(abs(tic_orig), 1.0)

            area_abs_rel_errors.append(abs_area_rel)
            area_signed_rel_errors.append(signed_area_rel)
            pearsons.append(corr)
            apex_shifts.append(apex_shift)
            tic_abs_rel_errors.append(tic_rel)
            target = targets[j]
            target_rows.append(
                {
                    "index": pair.index,
                    "dataset": pair.dataset,
                    "file_name": pair.file_name,
                    "file_index": pair.index,
                    "file": pair.file_name,
                    "target_rank": int(target.get("target_rank", j + 1)),
                    "rank": j + 1,
                    "target_mz": target["target_mz"],
                    "target_rt": target.get("target_rt", float("nan")),
                    "target_id": f"{pair.index}:{int(target.get('target_rank', j + 1))}:{float(target['target_mz']):.6f}",
                    "seed_signal_score": target.get("seed_signal_score", target["seed_intensity_sum"]),
                    "seed_intensity_sum": target["seed_intensity_sum"],
                    "seed_peak_count": target["seed_peak_count"],
                    "area_original": area_orig,
                    "area_reconstructed": area_recon,
                    "area_signed_rel_error": signed_area_rel,
                    "area_abs_rel_error": abs_area_rel,
                    "tic_abs_rel_error": tic_rel,
                    "pearson": corr,
                    "apex_shift": apex_shift,
                }
            )
        row.update(
            {
                "status": "ok",
                "elapsed_s": time.perf_counter() - start,
                "target_count": n_targets,
                "ms1_scans_original": int(orig_matrix.shape[0]),
                "ms1_scans_reconstructed": int(recon_matrix.shape[0]),
                "ms1_scans_compared": int(n_scans),
                "rt_abs_diff_max": rt_abs_max,
                "area_abs_rel_error_median": _stat(area_abs_rel_errors, "median"),
                "area_abs_rel_error_p95": _stat(area_abs_rel_errors, "p95"),
                "area_abs_rel_error_max": _stat(area_abs_rel_errors, "max"),
                "area_signed_rel_error_median": _stat(area_signed_rel_errors, "median"),
                "tic_abs_rel_error_p95": _stat(tic_abs_rel_errors, "p95"),
                "pearson_median": _stat(pearsons, "median"),
                "pearson_p05": float(np.percentile(np.asarray(pearsons, dtype=np.float64), 5)) if pearsons else float("nan"),
                "pearson_min": _stat(pearsons, "min"),
                "apex_shift_p95": _stat(apex_shifts, "p95"),
                "apex_shift_max": _stat(apex_shifts, "max"),
            }
        )
    except Exception as exc:
        row.update(
            {
                "status": "error",
                "elapsed_s": time.perf_counter() - start,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=8),
            }
        )
    return row, target_rows


def _check_external_tools() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    search_path = os.pathsep.join(str(path) for path in EXTRA_TOOL_PATHS if path.exists())
    base_path = os.environ.get("PATH", "")
    combined_path = os.pathsep.join(part for part in [search_path, base_path] if part)
    for tool, env_name in TOOL_ENV.items():
        env_value = os.environ.get(env_name, "")
        resolved = env_value if env_value and Path(env_value).exists() else shutil.which(tool, path=combined_path)
        rows.append(
            {
                "tool": tool,
                "env_var": env_name,
                "env_value": env_value,
                "resolved_path": resolved or "",
                "available": bool(resolved),
                "extra_search_paths": ";".join(str(path) for path in EXTRA_TOOL_PATHS if path.exists()),
            }
        )
    return rows


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _float_values(rows: list[dict[str, str]], key: str) -> list[float]:
    out: list[float] = []
    for row in rows:
        try:
            value = float(row.get(key, ""))
        except ValueError:
            continue
        if math.isfinite(value):
            out.append(value)
    return out


def _summarize(output_root: Path, pairs: list[Pair], stages: list[str]) -> dict[str, Any]:
    decode_rows = _read_csv_rows(output_root / "tables" / "decode_status.csv")
    roundtrip_rows = _read_csv_rows(output_root / "tables" / "roundtrip_summary.csv")
    xic_rows = _read_csv_rows(output_root / "tables" / "xic_summary.csv")
    tool_rows = _read_csv_rows(output_root / "tables" / "external_tool_availability.csv")

    rt_ok = [row for row in roundtrip_rows if row.get("status") == "ok"]
    rt_pass = [row for row in rt_ok if str(row.get("roundtrip_pass", "")).lower() == "true"]
    xic_ok = [row for row in xic_rows if row.get("status") == "ok"]

    summary: dict[str, Any] = {
        "output_root": str(output_root),
        "requested_stages": stages,
        "n_pairs_requested": len(pairs),
        "decode": {
            "rows": len(decode_rows),
            "ok_or_skipped": sum(1 for row in decode_rows if row.get("status") in {"ok", "skipped_existing"}),
            "errors": sum(1 for row in decode_rows if row.get("status") == "error"),
        },
        "roundtrip": {
            "rows": len(roundtrip_rows),
            "ok": len(rt_ok),
            "pass": len(rt_pass),
            "errors": sum(1 for row in roundtrip_rows if row.get("status") == "error"),
            "max_abs_mz_error_max": _stat(_float_values(roundtrip_rows, "max_abs_mz_error"), "max") if roundtrip_rows else None,
            "max_abs_intensity_error_max": _stat(_float_values(roundtrip_rows, "max_abs_intensity_error"), "max") if roundtrip_rows else None,
            "tic_rel_error_p95_max": _stat(_float_values(roundtrip_rows, "spectrum_tic_rel_error_p95"), "max") if roundtrip_rows else None,
            "bpi_rel_error_p95_max": _stat(_float_values(roundtrip_rows, "spectrum_bpi_rel_error_p95"), "max") if roundtrip_rows else None,
        },
        "xic": {
            "rows": len(xic_rows),
            "ok": len(xic_ok),
            "errors": sum(1 for row in xic_rows if row.get("status") == "error"),
            "area_abs_rel_error_p95_max": _stat(_float_values(xic_rows, "area_abs_rel_error_p95"), "max") if xic_rows else None,
            "area_abs_rel_error_max_max": _stat(_float_values(xic_rows, "area_abs_rel_error_max"), "max") if xic_rows else None,
            "pearson_median_min": _stat(_float_values(xic_rows, "pearson_median"), "min") if xic_rows else None,
            "pearson_p05_min": _stat(_float_values(xic_rows, "pearson_p05"), "min") if xic_rows else None,
            "apex_shift_p95_max": _stat(_float_values(xic_rows, "apex_shift_p95"), "max") if xic_rows else None,
        },
        "external_tools": {
            "rows": len(tool_rows),
            "available": sum(1 for row in tool_rows if str(row.get("available", "")).lower() == "true"),
            "missing": [row.get("tool") for row in tool_rows if str(row.get("available", "")).lower() != "true"],
        },
    }
    _write_json(output_root / "validation_summary.json", summary)
    _write_conclusion_md(output_root / "validation_conclusion.md", summary)
    return summary


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "NA"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "NA"
    if number == 0:
        return "0"
    if abs(number) < 1e-3 or abs(number) >= 1e5:
        return f"{number:.3e}"
    return f"{number:.{digits}g}"


def _write_conclusion_md(path: Path, summary: dict[str, Any]) -> None:
    rt = summary["roundtrip"]
    xic = summary["xic"]
    tools = summary["external_tools"]
    lines = [
        "# TrackCodec downstream validation conclusion",
        "",
        f"- Pairs requested: {summary['n_pairs_requested']}",
        f"- Decode ok/skipped: {summary['decode']['ok_or_skipped']} / {summary['decode']['rows']}; errors: {summary['decode']['errors']}",
        f"- Roundtrip pass: {rt['pass']} / {rt['ok']}; errors: {rt['errors']}",
        f"- Roundtrip max abs m/z error across files: {_fmt(rt['max_abs_mz_error_max'])}",
        f"- Roundtrip max abs intensity error across files: {_fmt(rt['max_abs_intensity_error_max'])}",
        f"- Roundtrip worst per-file p95 TIC relative error: {_fmt(rt['tic_rel_error_p95_max'])}",
        f"- XIC ok: {xic['ok']} / {xic['rows']}; errors: {xic['errors']}",
        f"- XIC worst per-file p95 area abs relative error: {_fmt(xic['area_abs_rel_error_p95_max'])}",
        f"- XIC worst target max area abs relative error: {_fmt(xic['area_abs_rel_error_max_max'])}",
        f"- XIC minimum median Pearson across files: {_fmt(xic['pearson_median_min'])}",
        f"- XIC minimum p05 Pearson across files: {_fmt(xic['pearson_p05_min'])}",
        f"- XIC worst p95 apex shift: {_fmt(xic['apex_shift_p95_max'])}",
        f"- External downstream tools available: {tools['available']} / {tools['rows']}",
        "",
    ]
    if rt["ok"] and rt["pass"] == rt["ok"] and xic["ok"] and xic["errors"] == 0:
        lines.append(
            "Main conclusion: reconstructed TrackCodec mzML files preserve the tested array-level and XIC-level downstream signals for the completed validation set."
        )
    else:
        lines.append(
            "Main conclusion: validation is incomplete or has failures; inspect the per-stage tables before using these outputs as downstream-equivalent."
        )
    if tools["missing"]:
        lines.extend(
            [
                "",
                "External tool workflows not completed in this run because these tools were not resolved locally:",
                "",
                ", ".join(str(x) for x in tools["missing"]),
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _filter_pairs(pairs: list[Pair], *, limit: int | None, only: list[str]) -> list[Pair]:
    selected = pairs
    if only:
        selected = [
            pair
            for pair in selected
            if any(term.lower() in pair.file_name.lower() or term.lower() in str(pair.original_path).lower() for term in only)
        ]
    selected = sorted(selected, key=lambda p: p.index)
    if limit is not None:
        selected = selected[: int(limit)]
    return selected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate TrackCodec 20-file downstream/loss outputs.")
    parser.add_argument("--path-table", type=Path, default=DEFAULT_PATH_TABLE)
    parser.add_argument("--trackcodec-root", type=Path, default=DEFAULT_TRACKCODEC_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=("manifest", "tools", "decode", "roundtrip", "xic", "summarize", "all"),
        default=["all"],
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--skip-existing", action="store_true", default=True)
    parser.add_argument("--force-decode", action="store_true")
    parser.add_argument("--binary-compression", choices=("preserve_template", "none", "zlib"), default="preserve_template")
    parser.add_argument("--section-workers", type=int, default=4)
    parser.add_argument("--ms2-segment-workers", type=int, default=0)
    parser.add_argument("--decode-segment-workers", type=int, default=4)
    parser.add_argument("--mz-error-ceiling", type=float, default=5e-7)
    parser.add_argument("--intensity-error-ceiling-float64", type=float, default=0.05)
    parser.add_argument("--intensity-error-ceiling-float32", type=float, default=0.1)
    parser.add_argument("--ceiling-eps", type=float, default=1e-10)
    parser.add_argument("--write-per-spectrum", action="store_true")
    parser.add_argument("--xic-top-k", type=int, default=500)
    parser.add_argument("--xic-workers", type=int, default=1)
    parser.add_argument("--xic-peaks-per-scan", type=int, default=80)
    parser.add_argument("--xic-bin-width-da", type=float, default=0.01)
    parser.add_argument("--xic-ppm", type=float, default=10.0)
    parser.add_argument("--reuse-recon-root", type=Path, default=None, help="Optional existing validation root whose recon/ directory should be reused instead of decoding again.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.force_decode:
        args.skip_existing = False
    stages = list(args.stages)
    if "all" in stages:
        stages = ["manifest", "tools", "decode", "roundtrip", "xic", "summarize"]

    pairs = _filter_pairs(_read_pairs(args.path_table), limit=args.limit, only=args.only)
    args.output_root.mkdir(parents=True, exist_ok=True)
    _write_json(
        args.output_root / "run_config.json",
        {
            "path_table": args.path_table,
            "trackcodec_root": args.trackcodec_root,
            "output_root": args.output_root,
            "stages": stages,
            "limit": args.limit,
            "only": args.only,
            "binary_compression": args.binary_compression,
            "xic_top_k": args.xic_top_k,
            "xic_workers": args.xic_workers,
            "xic_peaks_per_scan": args.xic_peaks_per_scan,
            "xic_bin_width_da": args.xic_bin_width_da,
            "xic_ppm": args.xic_ppm,
            "reuse_recon_root": args.reuse_recon_root,
        },
    )

    if "manifest" in stages:
        _write_csv(args.output_root / "tables" / "pair_manifest.csv", _pair_rows(pairs))
        print(f"[manifest] wrote {len(pairs)} pairs", flush=True)

    if "tools" in stages:
        _write_csv(args.output_root / "tables" / "external_tool_availability.csv", _check_external_tools())
        print("[tools] wrote external tool availability", flush=True)

    if "decode" in stages:
        _decode_archives(
            pairs,
            args.output_root,
            trackcodec_root=args.trackcodec_root,
            binary_compression=args.binary_compression,
            section_workers=args.section_workers,
            ms2_segment_workers=args.ms2_segment_workers if args.ms2_segment_workers > 0 else None,
            decode_segment_workers=args.decode_segment_workers if args.decode_segment_workers > 0 else None,
            skip_existing=bool(args.skip_existing),
            reuse_recon_root=args.reuse_recon_root,
        )

    if "roundtrip" in stages:
        _run_roundtrip(
            pairs,
            args.output_root,
            trackcodec_root=args.trackcodec_root,
            mz_error_ceiling=args.mz_error_ceiling,
            intensity_error_ceiling_float64=args.intensity_error_ceiling_float64,
            intensity_error_ceiling_float32=args.intensity_error_ceiling_float32,
            ceiling_eps=args.ceiling_eps,
            write_per_spectrum=bool(args.write_per_spectrum),
            reuse_recon_root=args.reuse_recon_root,
        )

    if "xic" in stages:
        _run_xic(
            pairs,
            args.output_root,
            top_k=args.xic_top_k,
            xic_workers=args.xic_workers,
            peaks_per_scan=args.xic_peaks_per_scan,
            bin_width_da=args.xic_bin_width_da,
            ppm=args.xic_ppm,
            reuse_recon_root=args.reuse_recon_root,
            skip_existing=bool(args.skip_existing),
        )

    if "summarize" in stages:
        _summarize(args.output_root, pairs, stages)
        print(f"[summarize] wrote {args.output_root / 'validation_conclusion.md'}", flush=True)


if __name__ == "__main__":
    main()
