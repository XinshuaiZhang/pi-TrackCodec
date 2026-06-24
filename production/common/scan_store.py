"""Memmap-backed spectrum store for whole-file TrackCodec workflows.

This keeps large mz/intensity payloads in file-backed arrays and exposes
lightweight scan views so whole-file encode paths do not need to retain
`list[dict[np.ndarray]]` in RAM.
"""

from __future__ import annotations

from array import array
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Dict, Iterator, List, Sequence
import zlib

import numpy as np
from lxml import etree
from pyteomics import mzml
from tqdm import tqdm

from .io import assert_supported_mzml_input
from ..metadata.metadata_codec import BinaryStrippedMetadataAccumulator


class FusedScanStoreUnsupported(ValueError):
    """Raised when the fused reader intentionally falls back to pyteomics."""


NS_URI = "http://psi.hupo.org/ms/mzml"
NS = {"mzml": NS_URI}
CV_PARAM_TAG = f"{{{NS_URI}}}cvParam"
BINARY_TAG = f"{{{NS_URI}}}binary"
BINARY_DATA_ARRAY_LIST_TAG = f"{{{NS_URI}}}binaryDataArrayList"
BINARY_DATA_ARRAY_TAG = f"{{{NS_URI}}}binaryDataArray"
SPECTRUM_TAG = f"{{{NS_URI}}}spectrum"
CHROMATOGRAM_TAG = f"{{{NS_URI}}}chromatogram"
SCAN_LIST_TAG = f"{{{NS_URI}}}scanList"
SCAN_TAG = f"{{{NS_URI}}}scan"
PRECURSOR_LIST_TAG = f"{{{NS_URI}}}precursorList"
PRECURSOR_TAG = f"{{{NS_URI}}}precursor"
ISOLATION_WINDOW_TAG = f"{{{NS_URI}}}isolationWindow"
MZ_ARRAY_ACCESSION = "MS:1000514"
INTENSITY_ARRAY_ACCESSION = "MS:1000515"
FLOAT32_ACCESSION = "MS:1000521"
FLOAT64_ACCESSION = "MS:1000523"
ZLIB_ACCESSION = "MS:1000574"
NO_COMPRESSION_ACCESSION = "MS:1000576"


def _mzml_reader(path: str, *, decode_binary: bool = True):
    assert_supported_mzml_input(path)
    return mzml.MzML(
        str(path),
        read_schema=False,
        iterative=True,
        use_index=False,
        huge_tree=True,
        decode_binary=bool(decode_binary),
    )


def _dia_window_key(spec) -> tuple[float, float, float]:
    iw = spec.get("precursorList", {}).get("precursor", [{}])[0].get("isolationWindow", {})
    try:
        target = float(iw.get("isolation window target m/z", 0.0))
    except (TypeError, ValueError):
        target = 0.0
    try:
        lower = float(iw.get("isolation window lower offset", 0.0))
    except (TypeError, ValueError):
        lower = 0.0
    try:
        upper = float(iw.get("isolation window upper offset", 0.0))
    except (TypeError, ValueError):
        upper = 0.0
    return (round(target, 4), round(lower, 4), round(upper, 4))


@dataclass(slots=True)
class _SpectrumView:
    store: "MemmapScanStore"
    global_idx: int

    def as_dict(self) -> Dict:
        return self.store.scan_as_dict(self.global_idx)


class _SpectrumSequence(Sequence[Dict]):
    def __init__(self, store: "MemmapScanStore", global_indices: np.ndarray):
        self.store = store
        self.global_indices = np.asarray(global_indices, dtype=np.uint32)

    def __len__(self) -> int:
        return int(self.global_indices.size)

    def __iter__(self) -> Iterator[Dict]:
        for global_idx in self.global_indices:
            yield self.store.scan_as_dict(int(global_idx))

    def __getitem__(self, item):
        if isinstance(item, slice):
            return _SpectrumSequence(self.store, self.global_indices[item])
        return self.store.scan_as_dict(int(self.global_indices[int(item)]))


class MemmapScanStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.manifest_path = self.root / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._dia_window_items_cache = None

        total_points = int(self.manifest["total_points"])
        if total_points > 0:
            self.mz_data = np.memmap(
                self.root / self.manifest["mz_data_file"],
                dtype=np.float64,
                mode="r",
                shape=(total_points,),
            )
            self.intensity_data = np.memmap(
                self.root / self.manifest["intensity_data_file"],
                dtype=np.float64,
                mode="r",
                shape=(total_points,),
            )
        else:
            # NumPy cannot mmap a zero-byte file. Keep the same slicing
            # semantics for empty MS1/MS2 subsets without forcing callers to
            # special-case files that have no points in the requested level.
            self.mz_data = np.empty((0,), dtype=np.float64)
            self.intensity_data = np.empty((0,), dtype=np.float64)
        self.ms_level = np.load(self.root / self.manifest["ms_level_file"], mmap_mode="r")
        self.local_scan_idx = np.load(self.root / self.manifest["local_scan_idx_file"], mmap_mode="r")
        self.rts = np.load(self.root / self.manifest["rt_file"], mmap_mode="r")
        self.offsets = np.load(self.root / self.manifest["offset_file"], mmap_mode="r")
        self.lengths = np.load(self.root / self.manifest["length_file"], mmap_mode="r")
        self.target_mz = np.load(self.root / self.manifest["target_mz_file"], mmap_mode="r")
        self.window_target = np.load(self.root / self.manifest["window_target_file"], mmap_mode="r")
        self.window_lower = np.load(self.root / self.manifest["window_lower_file"], mmap_mode="r")
        self.window_upper = np.load(self.root / self.manifest["window_upper_file"], mmap_mode="r")
        self.ms1_global_indices = np.load(self.root / self.manifest["ms1_indices_file"], mmap_mode="r")
        self.ms2_global_indices = np.load(self.root / self.manifest["ms2_indices_file"], mmap_mode="r")

    @property
    def raw_ms1_bytes(self) -> int:
        return int(self.manifest["raw_ms1_bytes"])

    @property
    def raw_ms2_bytes(self) -> int:
        return int(self.manifest["raw_ms2_bytes"])

    @property
    def file_type(self) -> str:
        return str(self.manifest["file_type"])

    @property
    def ms1_count(self) -> int:
        return int(self.ms1_global_indices.size)

    @property
    def ms2_count(self) -> int:
        return int(self.ms2_global_indices.size)

    def scan_as_dict(self, global_idx: int) -> Dict:
        start = int(self.offsets[global_idx])
        length = int(self.lengths[global_idx])
        end = start + length
        out = {
            "scan_idx": int(self.local_scan_idx[global_idx]),
            "rt": float(self.rts[global_idx]),
            "mz_array": self.mz_data[start:end],
            "intensity_array": self.intensity_data[start:end],
        }
        if int(self.ms_level[global_idx]) == 2:
            out["target_mz"] = float(self.target_mz[global_idx])
            if self.file_type == "DIA":
                out["window_key"] = (
                    float(self.window_target[global_idx]),
                    float(self.window_lower[global_idx]),
                    float(self.window_upper[global_idx]),
                )
        return out

    def ms1_scans(self) -> Sequence[Dict]:
        return _SpectrumSequence(self, self.ms1_global_indices)

    def dda_ms2_scans(self) -> Sequence[Dict]:
        return _SpectrumSequence(self, self.ms2_global_indices)

    def ms1_rts(self) -> np.ndarray:
        return np.asarray(self.rts[self.ms1_global_indices], dtype=np.float64)

    def materialize_dia_ms2_by_window(self) -> Dict[tuple[float, float, float], List[Dict]]:
        out: Dict[tuple[float, float, float], List[Dict]] = {}
        for global_idx in self.ms2_global_indices:
            global_idx_i = int(global_idx)
            key = (
                float(self.window_target[global_idx_i]),
                float(self.window_lower[global_idx_i]),
                float(self.window_upper[global_idx_i]),
            )
            out.setdefault(key, []).append(self.scan_as_dict(global_idx_i))
        for key in out:
            out[key].sort(key=lambda item: item["rt"])
        return out

    def dia_ms2_window_items(self) -> List[tuple[tuple[float, float, float], Sequence[Dict]]]:
        if self._dia_window_items_cache is not None:
            return self._dia_window_items_cache
        if self.file_type != "DIA" or self.ms2_global_indices.size == 0:
            self._dia_window_items_cache = []
            return self._dia_window_items_cache

        global_indices = np.asarray(self.ms2_global_indices, dtype=np.uint32)
        positions = np.arange(global_indices.size, dtype=np.uint32)
        targets = np.asarray(self.window_target[global_indices], dtype=np.float64)
        lowers = np.asarray(self.window_lower[global_indices], dtype=np.float64)
        uppers = np.asarray(self.window_upper[global_indices], dtype=np.float64)

        order = np.lexsort((positions, uppers, lowers, targets))
        ordered_indices = global_indices[order]
        ordered_targets = targets[order]
        ordered_lowers = lowers[order]
        ordered_uppers = uppers[order]

        if ordered_indices.size == 0:
            self._dia_window_items_cache = []
            return self._dia_window_items_cache

        change_mask = (
            (ordered_targets[1:] != ordered_targets[:-1])
            | (ordered_lowers[1:] != ordered_lowers[:-1])
            | (ordered_uppers[1:] != ordered_uppers[:-1])
        )
        starts = np.concatenate((np.array([0], dtype=np.int64), np.flatnonzero(change_mask).astype(np.int64) + 1))
        ends = np.concatenate((starts[1:], np.array([ordered_indices.size], dtype=np.int64)))

        items: List[tuple[tuple[float, float, float], Sequence[Dict]]] = []
        for start, end in zip(starts, ends):
            key = (
                float(ordered_targets[int(start)]),
                float(ordered_lowers[int(start)]),
                float(ordered_uppers[int(start)]),
            )
            items.append((key, _SpectrumSequence(self, ordered_indices[int(start):int(end)])))
        self._dia_window_items_cache = items
        return items


def open_memmap_scan_store(root: str | Path) -> MemmapScanStore:
    return MemmapScanStore(root)


def _write_json_atomic(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{time.time_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _scan_store_manifest_complete(store_root: Path, manifest: Dict) -> bool:
    required = [
        "mz_data_file",
        "intensity_data_file",
        "ms_level_file",
        "local_scan_idx_file",
        "rt_file",
        "offset_file",
        "length_file",
        "target_mz_file",
        "window_target_file",
        "window_lower_file",
        "window_upper_file",
        "ms1_indices_file",
        "ms2_indices_file",
    ]
    for key in required:
        name = manifest.get(key)
        if not name or not (store_root / str(name)).exists():
            return False
    total_points = int(manifest.get("total_points", -1))
    if total_points < 0:
        return False
    for key in ("mz_data_file", "intensity_data_file"):
        path = store_root / str(manifest[key])
        if int(path.stat().st_size) != total_points * np.dtype(np.float64).itemsize:
            return False
    try:
        n_spectra = int(np.load(store_root / manifest["length_file"], mmap_mode="r").shape[0])
        if int(np.load(store_root / manifest["ms_level_file"], mmap_mode="r").shape[0]) != n_spectra:
            return False
        if int(np.load(store_root / manifest["offset_file"], mmap_mode="r").shape[0]) != n_spectra:
            return False
        if int(np.load(store_root / manifest["local_scan_idx_file"], mmap_mode="r").shape[0]) != n_spectra:
            return False
    except Exception:
        return False
    return True


def is_complete_memmap_scan_store(store_root: str | Path) -> bool:
    store_root = Path(store_root)
    manifest_path = store_root / "manifest.json"
    if not manifest_path.exists():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return _scan_store_manifest_complete(store_root, manifest)


def _decode_array_payload(array_obj) -> np.ndarray:
    if array_obj is None:
        return np.empty(0, dtype=np.float64)
    if isinstance(array_obj, np.ndarray):
        if array_obj.dtype == np.float64:
            return array_obj
        return np.asarray(array_obj, dtype=np.float64)

    source = getattr(array_obj, "source", None)
    data = getattr(array_obj, "data", None)
    if source is not None and data is not None:
        decoded = source.decode_data_array(
            data,
            compression_type=getattr(array_obj, "compression", None),
            dtype=getattr(array_obj, "dtype", np.float64),
        )
        if isinstance(decoded, np.ndarray) and decoded.dtype == np.float64:
            return decoded
        return np.asarray(decoded, dtype=np.float64)

    return np.asarray(array_obj, dtype=np.float64)


def _existing_complete_scan_store(
    mzml_path: Path,
    file_type: str,
    store_root: Path,
    *,
    max_ms1_scans,
    include_ms1: bool,
    include_ms2: bool,
) -> MemmapScanStore | None:
    manifest_path = store_root / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            _scan_store_manifest_complete(store_root, manifest)
            and str(manifest.get("source_file")) == str(mzml_path)
            and str(manifest.get("file_type")) == str(file_type)
            and bool(manifest.get("include_ms1", True)) == include_ms1
            and bool(manifest.get("include_ms2", True)) == include_ms2
            and manifest.get("max_ms1_scans") == max_ms1_scans
        ):
            return MemmapScanStore(store_root)
    except Exception:
        return None
    return None


def _direct_child(elem, tag: str):
    if elem is None:
        return None
    for child in elem:
        if child.tag == tag:
            return child
    return None


def _cv_accessions(elem) -> list[str]:
    accessions = []
    for child in elem:
        if child.tag != CV_PARAM_TAG:
            continue
        accession = child.get("accession")
        if accession:
            accessions.append(accession)
    return accessions


def _cv_value(elem, accession: str, default: str = "") -> str:
    if elem is None:
        return default
    for child in elem:
        if child.tag == CV_PARAM_TAG and child.get("accession") == accession:
            return child.get("value", default)
    return default


def _cv_float(elem, accession: str, default: float = 0.0) -> float:
    try:
        return float(_cv_value(elem, accession, str(default)) or default)
    except (TypeError, ValueError):
        return float(default)


def _spectrum_ms_level_elem(spectrum_elem) -> int:
    try:
        return int(_cv_value(spectrum_elem, "MS:1000511", "0") or 0)
    except (TypeError, ValueError):
        return 0


def _spectrum_rt_elem(spectrum_elem) -> float:
    scan_list = _direct_child(spectrum_elem, SCAN_LIST_TAG)
    scan = _direct_child(scan_list, SCAN_TAG)
    if scan is None:
        return 0.0
    return _cv_float(scan, "MS:1000016", 0.0)


def _isolation_window_values(spectrum_elem) -> tuple[float, float, float]:
    precursor_list = _direct_child(spectrum_elem, PRECURSOR_LIST_TAG)
    precursor = _direct_child(precursor_list, PRECURSOR_TAG)
    isolation = _direct_child(precursor, ISOLATION_WINDOW_TAG)
    if isolation is None:
        return (0.0, 0.0, 0.0)
    target = _cv_float(isolation, "MS:1000827", 0.0)
    lower = _cv_float(isolation, "MS:1000828", 0.0)
    upper = _cv_float(isolation, "MS:1000829", 0.0)
    return (round(target, 4), round(lower, 4), round(upper, 4))


def _decode_binary_array_elem(array_elem, *, preserve_binary_text: bool = False) -> tuple[str, np.ndarray, Dict]:
    cv_accessions = _cv_accessions(array_elem)
    accessions = set(cv_accessions)
    if MZ_ARRAY_ACCESSION in accessions:
        kind = "mz"
    elif INTENSITY_ARRAY_ACCESSION in accessions:
        kind = "intensity"
    else:
        kind = "auxiliary"

    if FLOAT64_ACCESSION in accessions:
        dtype = np.dtype("<f8")
    elif FLOAT32_ACCESSION in accessions:
        dtype = np.dtype("<f4")
    else:
        dtype = None

    binary = _direct_child(array_elem, BINARY_TAG)
    binary_text = binary.text if binary is not None and binary.text is not None else ""
    meta = {
        "encoded_length": array_elem.get("encodedLength", "0"),
        "binary_text": binary_text if preserve_binary_text else "",
        "cv_accessions": cv_accessions,
        "array_length": array_elem.get("arrayLength", "0"),
        "chromatogram_id": None,
    }
    if kind == "auxiliary":
        return kind, np.empty(0, dtype=np.float64), meta

    if dtype is None:
        raise FusedScanStoreUnsupported("Unsupported mzML binary dtype in fused scan-store builder")
    if not binary_text:
        return kind, np.empty(0, dtype=np.float64), meta

    raw = base64.b64decode(binary_text)
    if ZLIB_ACCESSION in accessions:
        raw = zlib.decompress(raw)
    elif NO_COMPRESSION_ACCESSION in accessions:
        pass
    else:
        raise FusedScanStoreUnsupported("Unsupported mzML binary compression in fused scan-store builder")
    arr = np.frombuffer(raw, dtype=dtype)
    return kind, np.asarray(arr, dtype=np.float64), meta


class _MetadataTeeReader:
    def __init__(self, path: Path, accumulator: BinaryStrippedMetadataAccumulator, chunk_size: int = 1024 * 1024):
        self.path = Path(path)
        self.accumulator = accumulator
        self.chunk_size = int(chunk_size)
        self.handle = None

    def __enter__(self):
        self.handle = self.path.open("rb")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.handle is not None:
            self.handle.close()
        self.handle = None

    def read(self, size: int = -1) -> bytes:
        if self.handle is None:
            raise ValueError("I/O operation on closed MetadataTeeReader")
        if size is None or size < 0:
            chunks = []
            while True:
                chunk = self.handle.read(self.chunk_size)
                if not chunk:
                    break
                self.accumulator.feed(chunk)
                chunks.append(chunk)
            return b"".join(chunks)
        chunk = self.handle.read(size)
        if chunk:
            self.accumulator.feed(chunk)
        return chunk

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


def _clear_previous_scan_store_outputs(store_root: Path) -> None:
    for name in (
        "manifest.json",
        "mz_data.bin",
        "intensity_data.bin",
        "ms_level.npy",
        "local_scan_idx.npy",
        "rt.npy",
        "offset.npy",
        "length.npy",
        "target_mz.npy",
        "window_target.npy",
        "window_lower.npy",
        "window_upper.npy",
        "ms1_indices.npy",
        "ms2_indices.npy",
        "metadata_xml.bin",
        "auxiliary_records.json",
    ):
        path = store_root / name
        if path.exists():
            path.unlink()


def _write_scan_store_arrays(
    store_root: Path,
    *,
    ms_levels,
    local_scan_indices,
    rt_values,
    offsets,
    lengths,
    target_mz_values,
    window_targets,
    window_lowers,
    window_uppers,
    ms1_global_indices,
    ms2_global_indices,
) -> None:
    np.save(store_root / "ms_level.npy", np.asarray(ms_levels, dtype=np.uint8))
    np.save(store_root / "local_scan_idx.npy", np.asarray(local_scan_indices, dtype=np.uint32))
    np.save(store_root / "rt.npy", np.asarray(rt_values, dtype=np.float64))
    np.save(store_root / "offset.npy", np.asarray(offsets, dtype=np.uint64))
    np.save(store_root / "length.npy", np.asarray(lengths, dtype=np.uint32))
    np.save(store_root / "target_mz.npy", np.asarray(target_mz_values, dtype=np.float64))
    np.save(store_root / "window_target.npy", np.asarray(window_targets, dtype=np.float64))
    np.save(store_root / "window_lower.npy", np.asarray(window_lowers, dtype=np.float64))
    np.save(store_root / "window_upper.npy", np.asarray(window_uppers, dtype=np.float64))
    np.save(store_root / "ms1_indices.npy", np.asarray(ms1_global_indices, dtype=np.uint32))
    np.save(store_root / "ms2_indices.npy", np.asarray(ms2_global_indices, dtype=np.uint32))


def _scan_store_manifest(
    *,
    file_type: str,
    mzml_path: Path,
    include_ms1: bool,
    include_ms2: bool,
    max_ms1_scans,
    total_points: int,
    raw_ms1_bytes: int,
    raw_ms2_bytes: int,
    builder: str,
) -> Dict:
    return {
        "file_type": str(file_type),
        "source_file": str(mzml_path),
        "include_ms1": bool(include_ms1),
        "include_ms2": bool(include_ms2),
        "max_ms1_scans": max_ms1_scans,
        "total_points": int(total_points),
        "raw_ms1_bytes": int(raw_ms1_bytes),
        "raw_ms2_bytes": int(raw_ms2_bytes),
        "builder": str(builder),
        "mz_data_file": "mz_data.bin",
        "intensity_data_file": "intensity_data.bin",
        "ms_level_file": "ms_level.npy",
        "local_scan_idx_file": "local_scan_idx.npy",
        "rt_file": "rt.npy",
        "offset_file": "offset.npy",
        "length_file": "length.npy",
        "target_mz_file": "target_mz.npy",
        "window_target_file": "window_target.npy",
        "window_lower_file": "window_lower.npy",
        "window_upper_file": "window_upper.npy",
        "ms1_indices_file": "ms1_indices.npy",
        "ms2_indices_file": "ms2_indices.npy",
    }


def _create_memmap_scan_store_from_mzml_fused(
    mzml_path: str | Path,
    file_type: str,
    store_root: str | Path,
    *,
    max_ms1_scans=None,
    include_ms1: bool = True,
    include_ms2: bool = True,
    show_progress: bool = False,
) -> tuple[MemmapScanStore, bytes, List[Dict]]:
    mzml_path = Path(mzml_path)
    assert_supported_mzml_input(mzml_path)
    store_root = Path(store_root)
    store_root.mkdir(parents=True, exist_ok=True)
    include_ms1 = bool(include_ms1)
    include_ms2 = bool(include_ms2)
    if not include_ms1 and not include_ms2:
        raise ValueError("create_memmap_scan_store_from_mzml requires at least one of include_ms1/include_ms2")

    cached = _existing_complete_scan_store(
        mzml_path,
        file_type,
        store_root,
        max_ms1_scans=max_ms1_scans,
        include_ms1=include_ms1,
        include_ms2=include_ms2,
    )
    metadata_xml_path = store_root / "metadata_xml.bin"
    auxiliary_records_path = store_root / "auxiliary_records.json"
    if cached is not None and metadata_xml_path.exists() and auxiliary_records_path.exists():
        return cached, metadata_xml_path.read_bytes(), json.loads(auxiliary_records_path.read_text(encoding="utf-8"))

    _clear_previous_scan_store_outputs(store_root)
    mz_path = store_root / "mz_data.bin"
    intensity_path = store_root / "intensity_data.bin"

    ms_levels = array("B")
    local_scan_indices = array("I")
    rt_values = array("d")
    offsets = array("Q")
    lengths = array("I")
    target_mz_values = array("d")
    window_targets = array("d")
    window_lowers = array("d")
    window_uppers = array("d")
    ms1_global_indices = array("I")
    ms2_global_indices = array("I")
    auxiliary_records: List[Dict] = []

    ms1_idx = 0
    ms2_idx = 0
    source_ms1_seen = 0
    total_points = 0
    raw_ms1_bytes = 0
    raw_ms2_bytes = 0
    accumulator = BinaryStrippedMetadataAccumulator()

    with mz_path.open("wb") as mz_handle, intensity_path.open("wb") as intensity_handle:
        with _MetadataTeeReader(mzml_path, accumulator) as reader:
            iterator = etree.iterparse(
                reader,
                events=("end",),
                tag=(SPECTRUM_TAG, CHROMATOGRAM_TAG),
                recover=True,
                huge_tree=True,
            )
            if show_progress:
                iterator = tqdm(iterator, desc=f"  fused reading {mzml_path.name}", miniters=500)
            for _event, elem in iterator:
                if elem.tag == CHROMATOGRAM_TAG:
                    chromatogram_id = elem.get("id")
                    array_list = _direct_child(elem, BINARY_DATA_ARRAY_LIST_TAG)
                    if array_list is not None:
                        for array_elem in array_list:
                            if array_elem.tag != BINARY_DATA_ARRAY_TAG:
                                continue
                            binary = _direct_child(array_elem, BINARY_TAG)
                            auxiliary_records.append(
                                {
                                    "encoded_length": array_elem.get("encodedLength", "0"),
                                    "binary_text": binary.text or "",
                                    "cv_accessions": _cv_accessions(array_elem),
                                    "array_length": array_elem.get("arrayLength", "0"),
                                    "chromatogram_id": chromatogram_id,
                                }
                            )
                    elem.clear()
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]
                    continue
                spectrum = elem
                ms_level = _spectrum_ms_level_elem(spectrum)
                if ms_level not in (1, 2):
                    spectrum.clear()
                    while spectrum.getprevious() is not None:
                        del spectrum.getparent()[0]
                    continue
                source_ms1_seen += 1 if ms_level == 1 else 0
                retain = (ms_level == 1 and include_ms1) or (ms_level == 2 and include_ms2)
                mz_arr = np.empty(0, dtype=np.float64)
                int_arr = np.empty(0, dtype=np.float64)
                array_list = _direct_child(spectrum, BINARY_DATA_ARRAY_LIST_TAG)
                if array_list is not None:
                    for array_elem in array_list:
                        if array_elem.tag != BINARY_DATA_ARRAY_TAG:
                            continue
                        kind, arr, _meta = _decode_binary_array_elem(array_elem)
                        if kind == "mz":
                            mz_arr = arr
                        elif kind == "intensity":
                            int_arr = arr
                if retain:
                    if int(mz_arr.size) != int(int_arr.size):
                        raise ValueError("fused scan-store mz/intensity length mismatch")
                    n = int(mz_arr.size)
                    offsets.append(int(total_points))
                    lengths.append(n)
                    rt_values.append(_spectrum_rt_elem(spectrum))
                    ms_levels.append(ms_level)
                    mz_arr.tofile(mz_handle)
                    int_arr.tofile(intensity_handle)
                    total_points += n
                    if ms_level == 1:
                        local_scan_indices.append(ms1_idx)
                        ms1_global_indices.append(len(ms_levels) - 1)
                        target_mz_values.append(0.0)
                        window_targets.append(0.0)
                        window_lowers.append(0.0)
                        window_uppers.append(0.0)
                        raw_ms1_bytes += n * 16
                        ms1_idx += 1
                    else:
                        local_scan_indices.append(ms2_idx)
                        ms2_global_indices.append(len(ms_levels) - 1)
                        raw_ms2_bytes += n * 16
                        target, lower, upper = _isolation_window_values(spectrum)
                        if file_type == "DIA":
                            target_mz_values.append(float(target))
                            window_targets.append(float(target))
                            window_lowers.append(float(lower))
                            window_uppers.append(float(upper))
                        else:
                            target_mz_values.append(float(target))
                            window_targets.append(0.0)
                            window_lowers.append(0.0)
                            window_uppers.append(0.0)
                        ms2_idx += 1
                if max_ms1_scans and source_ms1_seen >= max_ms1_scans:
                    raise FusedScanStoreUnsupported("fused scan-store does not support max_ms1_scans truncation")
                spectrum.clear()
                while spectrum.getprevious() is not None:
                    del spectrum.getparent()[0]

    metadata_xml = accumulator.finish(mzml_path)

    _write_scan_store_arrays(
        store_root,
        ms_levels=ms_levels,
        local_scan_indices=local_scan_indices,
        rt_values=rt_values,
        offsets=offsets,
        lengths=lengths,
        target_mz_values=target_mz_values,
        window_targets=window_targets,
        window_lowers=window_lowers,
        window_uppers=window_uppers,
        ms1_global_indices=ms1_global_indices,
        ms2_global_indices=ms2_global_indices,
    )
    manifest = _scan_store_manifest(
        file_type=file_type,
        mzml_path=mzml_path,
        include_ms1=include_ms1,
        include_ms2=include_ms2,
        max_ms1_scans=max_ms1_scans,
        total_points=total_points,
        raw_ms1_bytes=raw_ms1_bytes,
        raw_ms2_bytes=raw_ms2_bytes,
        builder="fused_lxml",
    )
    _write_json_atomic(store_root / "manifest.json", manifest)
    metadata_xml_path.write_bytes(metadata_xml)
    auxiliary_records_path.write_text(json.dumps(auxiliary_records, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return MemmapScanStore(store_root), metadata_xml, auxiliary_records


def _create_memmap_scan_store_from_mzml_pyteomics(
    mzml_path: str | Path,
    file_type: str,
    store_root: str | Path,
    *,
    max_ms1_scans=None,
    include_ms1: bool = True,
    include_ms2: bool = True,
    show_progress: bool = False,
) -> MemmapScanStore:
    mzml_path = Path(mzml_path)
    assert_supported_mzml_input(mzml_path)
    store_root = Path(store_root)
    store_root.mkdir(parents=True, exist_ok=True)
    include_ms1 = bool(include_ms1)
    include_ms2 = bool(include_ms2)
    if not include_ms1 and not include_ms2:
        raise ValueError("create_memmap_scan_store_from_mzml requires at least one of include_ms1/include_ms2")
    manifest_path = store_root / "manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (
                _scan_store_manifest_complete(store_root, manifest)
                and str(manifest.get("source_file")) == str(mzml_path)
                and str(manifest.get("file_type")) == str(file_type)
                and bool(manifest.get("include_ms1", True)) == include_ms1
                and bool(manifest.get("include_ms2", True)) == include_ms2
                and manifest.get("max_ms1_scans") == max_ms1_scans
            ):
                return MemmapScanStore(store_root)
        except Exception:
            pass

    mz_path = store_root / "mz_data.bin"
    intensity_path = store_root / "intensity_data.bin"

    ms_levels = array("B")
    local_scan_indices = array("I")
    rt_values = array("d")
    offsets = array("Q")
    lengths = array("I")
    target_mz_values = array("d")
    window_targets = array("d")
    window_lowers = array("d")
    window_uppers = array("d")
    ms1_global_indices = array("I")
    ms2_global_indices = array("I")

    ms1_idx = 0
    ms2_idx = 0
    source_ms1_seen = 0
    total_points = 0
    raw_ms1_bytes = 0
    raw_ms2_bytes = 0

    # When only one MS level is needed, keep pyteomics in decode_binary=False mode
    # and manually decode just the spectra we retain. This avoids decoding the
    # skipped level's base64 payloads.
    decode_binary = include_ms1 and include_ms2

    with mz_path.open("wb") as mz_handle, intensity_path.open("wb") as intensity_handle:
        with _mzml_reader(str(mzml_path), decode_binary=decode_binary) as reader:
            iterator = reader
            if show_progress:
                iterator = tqdm(reader, desc=f"  reading {mzml_path.name}", miniters=500)
            for spec in iterator:
                ms_level = int(spec.get("ms level", 0))
                if ms_level not in (1, 2):
                    continue
                if ms_level == 1 and not include_ms1:
                    continue
                if ms_level == 2 and not include_ms2:
                    continue
                rt = float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))
                mz_arr = _decode_array_payload(spec.get("m/z array"))
                int_arr = _decode_array_payload(spec.get("intensity array"))
                n = int(mz_arr.size)

                offsets.append(int(total_points))
                lengths.append(n)
                rt_values.append(rt)
                ms_levels.append(ms_level)

                mz_arr.tofile(mz_handle)
                int_arr.tofile(intensity_handle)
                total_points += n

                if ms_level == 1:
                    source_ms1_seen += 1
                    local_scan_indices.append(ms1_idx)
                    ms1_global_indices.append(len(ms_levels) - 1)
                    target_mz_values.append(0.0)
                    window_targets.append(0.0)
                    window_lowers.append(0.0)
                    window_uppers.append(0.0)
                    raw_ms1_bytes += n * 16
                    ms1_idx += 1
                else:
                    local_scan_indices.append(ms2_idx)
                    ms2_global_indices.append(len(ms_levels) - 1)
                    raw_ms2_bytes += n * 16
                    if file_type == "DIA":
                        window_key = _dia_window_key(spec)
                        target_mz_values.append(float(window_key[0]))
                        window_targets.append(float(window_key[0]))
                        window_lowers.append(float(window_key[1]))
                        window_uppers.append(float(window_key[2]))
                    else:
                        iw = spec.get("precursorList", {}).get("precursor", [{}])[0].get("isolationWindow", {})
                        try:
                            tmz = round(float(iw.get("isolation window target m/z", 0.0)), 4)
                        except (TypeError, ValueError):
                            tmz = 0.0
                        target_mz_values.append(float(tmz))
                        window_targets.append(0.0)
                        window_lowers.append(0.0)
                        window_uppers.append(0.0)
                    ms2_idx += 1
                if max_ms1_scans and source_ms1_seen >= max_ms1_scans:
                    break

    _write_scan_store_arrays(
        store_root,
        ms_levels=ms_levels,
        local_scan_indices=local_scan_indices,
        rt_values=rt_values,
        offsets=offsets,
        lengths=lengths,
        target_mz_values=target_mz_values,
        window_targets=window_targets,
        window_lowers=window_lowers,
        window_uppers=window_uppers,
        ms1_global_indices=ms1_global_indices,
        ms2_global_indices=ms2_global_indices,
    )
    manifest = _scan_store_manifest(
        file_type=file_type,
        mzml_path=mzml_path,
        include_ms1=include_ms1,
        include_ms2=include_ms2,
        max_ms1_scans=max_ms1_scans,
        total_points=total_points,
        raw_ms1_bytes=raw_ms1_bytes,
        raw_ms2_bytes=raw_ms2_bytes,
        builder="pyteomics",
    )
    _write_json_atomic(manifest_path, manifest)
    return MemmapScanStore(store_root)


def create_memmap_scan_store_from_mzml(
    mzml_path: str | Path,
    file_type: str,
    store_root: str | Path,
    *,
    max_ms1_scans=None,
    include_ms1: bool = True,
    include_ms2: bool = True,
    show_progress: bool = False,
    fused_metadata: bool = True,
) -> MemmapScanStore:
    use_fused = bool(fused_metadata) and max_ms1_scans is None
    if use_fused:
        try:
            store, _metadata_xml, _auxiliary_records = _create_memmap_scan_store_from_mzml_fused(
                mzml_path,
                file_type,
                store_root,
                max_ms1_scans=max_ms1_scans,
                include_ms1=include_ms1,
                include_ms2=include_ms2,
                show_progress=show_progress,
            )
            return store
        except FusedScanStoreUnsupported:
            _clear_previous_scan_store_outputs(Path(store_root))
    return _create_memmap_scan_store_from_mzml_pyteomics(
        mzml_path,
        file_type,
        store_root,
        max_ms1_scans=max_ms1_scans,
        include_ms1=include_ms1,
        include_ms2=include_ms2,
        show_progress=show_progress,
    )


def create_memmap_scan_store_with_metadata_from_mzml(
    mzml_path: str | Path,
    file_type: str,
    store_root: str | Path,
    *,
    max_ms1_scans=None,
    include_ms1: bool = True,
    include_ms2: bool = True,
    show_progress: bool = False,
) -> tuple[MemmapScanStore, bytes | None, List[Dict] | None]:
    use_fused = max_ms1_scans is None
    if use_fused:
        try:
            return _create_memmap_scan_store_from_mzml_fused(
                mzml_path,
                file_type,
                store_root,
                max_ms1_scans=max_ms1_scans,
                include_ms1=include_ms1,
                include_ms2=include_ms2,
                show_progress=show_progress,
            )
        except FusedScanStoreUnsupported:
            _clear_previous_scan_store_outputs(Path(store_root))
    store = _create_memmap_scan_store_from_mzml_pyteomics(
        mzml_path,
        file_type,
        store_root,
        max_ms1_scans=max_ms1_scans,
        include_ms1=include_ms1,
        include_ms2=include_ms2,
        show_progress=show_progress,
    )
    metadata_xml_path = Path(store_root) / "metadata_xml.bin"
    auxiliary_records_path = Path(store_root) / "auxiliary_records.json"
    if metadata_xml_path.exists() and auxiliary_records_path.exists():
        try:
            return store, metadata_xml_path.read_bytes(), json.loads(auxiliary_records_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return store, None, None
