"""Reconstruct a valid mzML document from stripped XML plus decoded MS1/MS2 arrays."""

from __future__ import annotations

import base64
import copy
import os
import re
import tempfile
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import numpy as np
from lxml import etree

from ..metadata.metadata_codec import extract_binary_stripped_metadata_xml

try:  # pragma: no cover - optional compiled speedup
    from . import _native_writer as _native_writer
    HAVE_NATIVE_WRITER = os.environ.get("TRACKCODEC_ENABLE_NATIVE_MZML_WRITER", "0") in {"1", "true", "TRUE", "yes", "YES"}
except Exception:  # pragma: no cover
    _native_writer = None
    HAVE_NATIVE_WRITER = False


def _native_writer_batch_size() -> int:
    if not HAVE_NATIVE_WRITER or _native_writer is None:
        return 0
    value = os.environ.get("TRACKCODEC_NATIVE_MZML_BATCH_SIZE")
    if value is None:
        return 64
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


NATIVE_WRITER_BATCH_SIZE = _native_writer_batch_size()


def _native_indexed_finalizer_enabled() -> bool:
    if _native_writer is None:
        return False
    value = os.environ.get("TRACKCODEC_ENABLE_NATIVE_INDEXED_MZML_FINALIZER", "0")
    return value in {"1", "true", "TRUE", "yes", "YES"}


NATIVE_INDEXED_MZML_FINALIZER = _native_indexed_finalizer_enabled()
NATIVE_INDEXED_MZML_FINALIZER_VALIDATE = os.environ.get(
    "TRACKCODEC_VALIDATE_NATIVE_INDEXED_MZML_FINALIZER",
    "0",
) in {"1", "true", "TRUE", "yes", "YES"}
DIRECT_OUTPUT_INDEXED_MZML_WRITER = os.environ.get(
    "TRACKCODEC_ENABLE_DIRECT_OUTPUT_INDEXED_MZML_WRITER",
    "0",
) in {"1", "true", "TRUE", "yes", "YES"}
VALIDATE_INDEX_OFFSET_PROBES = os.environ.get(
    "TRACKCODEC_VALIDATE_INDEX_OFFSET_PROBES",
    "0",
) in {"1", "true", "TRUE", "yes", "YES"}


NS_URI = "http://psi.hupo.org/ms/mzml"
NS = {"mzml": NS_URI}
XSI_URI = "http://www.w3.org/2001/XMLSchema-instance"
CV_PARAM_TAG = f"{{{NS_URI}}}cvParam"
BINARY_DATA_ARRAY_LIST_TAG = f"{{{NS_URI}}}binaryDataArrayList"
BINARY_DATA_ARRAY_TAG = f"{{{NS_URI}}}binaryDataArray"
BINARY_TAG = f"{{{NS_URI}}}binary"
SPECTRUM_TAG = f"{{{NS_URI}}}spectrum"
SCAN_LIST_TAG = f"{{{NS_URI}}}scanList"
SCAN_TAG = f"{{{NS_URI}}}scan"
PRECURSOR_LIST_TAG = f"{{{NS_URI}}}precursorList"
PRECURSOR_TAG = f"{{{NS_URI}}}precursor"
ISOLATION_WINDOW_TAG = f"{{{NS_URI}}}isolationWindow"

COMPRESSION_CV_MAP = {
    "none": ("MS:1000576", "no compression"),
    "zlib": ("MS:1000574", "zlib compression"),
}

DTYPE_CV_MAP = {
    "MS:1000519": np.dtype("<i4"),
    "MS:1000522": np.dtype("<i8"),
    "MS:1000521": np.dtype("<f4"),
    "MS:1000523": np.dtype("<f8"),
}

NATIVE_DTYPE_CODE_MAP = {
    np.dtype("<f8"): "f8",
    np.dtype("<f4"): "f4",
    np.dtype("<i4"): "i4",
    np.dtype("<i8"): "i8",
}

_SPECTRUM_OPEN_RE = re.compile(br'<(?:[A-Za-z_][\w.\-]*:)?spectrum\b[^>]*\bid="([^"]+)"')
_CHROMATOGRAM_OPEN_RE = re.compile(br'<(?:[A-Za-z_][\w.\-]*:)?chromatogram\b[^>]*\bid="([^"]+)"')
_INDEX_SCAN_OVERLAP_BYTES = 65536


def _cv_param(accession: str, name: str, value: str = "", **attrs):
    elem = etree.Element(f"{{{NS_URI}}}cvParam")
    elem.set("cvRef", "MS" if accession.startswith("MS:") else "UO")
    elem.set("accession", accession)
    elem.set("name", name)
    elem.set("value", value)
    for key, val in attrs.items():
        elem.set(key, str(val))
    return elem


def _binary_array_elem(array: np.ndarray, accession: str, name: str, *, binary_compression: str = "none"):
    raw_payload = np.asarray(array, dtype="<f8").tobytes()
    if binary_compression == "none":
        payload = raw_payload
        compression_accession = "MS:1000576"
        compression_name = "no compression"
    elif binary_compression == "zlib":
        payload = zlib.compress(raw_payload)
        compression_accession = "MS:1000574"
        compression_name = "zlib compression"
    else:
        raise ValueError(f"Unsupported mzML binary compression: {binary_compression}")
    encoded = base64.b64encode(payload).decode("ascii")
    elem = etree.Element(
        f"{{{NS_URI}}}binaryDataArray",
        encodedLength=str(len(encoded)),
        arrayLength=str(len(array)),
    )
    elem.append(_cv_param("MS:1000523", "64-bit float"))
    elem.append(_cv_param(compression_accession, compression_name))
    elem.append(_cv_param(accession, name))
    binary = etree.SubElement(elem, f"{{{NS_URI}}}binary")
    binary.text = encoded
    return elem


def _find_cv_param(elem, accession: str):
    for child in elem:
        if child.tag == CV_PARAM_TAG and child.get("accession") == accession:
            return child
    return None


def _find_spectrum_binary_array(spectrum_elem, accession: str):
    array_list = spectrum_elem.find(BINARY_DATA_ARRAY_LIST_TAG)
    if array_list is None:
        return None
    for array_elem in array_list:
        if array_elem.tag != BINARY_DATA_ARRAY_TAG:
            continue
        if _find_cv_param(array_elem, accession) is not None:
            return array_elem
    return None


def _find_spectrum_mz_int_binary_arrays(spectrum_elem):
    array_list = spectrum_elem.find(BINARY_DATA_ARRAY_LIST_TAG)
    if array_list is None:
        return None, None
    mz_elem = None
    int_elem = None
    for array_elem in array_list:
        if array_elem.tag != BINARY_DATA_ARRAY_TAG:
            continue
        for child in array_elem:
            if child.tag != CV_PARAM_TAG:
                continue
            accession = child.get("accession")
            if accession == "MS:1000514":
                mz_elem = array_elem
            elif accession == "MS:1000515":
                int_elem = array_elem
            if mz_elem is not None and int_elem is not None:
                return mz_elem, int_elem
    return mz_elem, int_elem


def _find_binary_array_type_cv(array_elem):
    for child in array_elem:
        if child.tag != CV_PARAM_TAG:
            continue
        accession = child.get("accession")
        dtype = DTYPE_CV_MAP.get(accession)
        if dtype is not None:
            return child, dtype
    return None, np.dtype("<f8")


def _find_binary_array_compression_cv(array_elem):
    accession_to_mode = {accession: mode for mode, (accession, _) in COMPRESSION_CV_MAP.items()}
    for child in array_elem:
        if child.tag != CV_PARAM_TAG:
            continue
        mode = accession_to_mode.get(child.get("accession"))
        if mode is not None:
            return child, mode
    return None, None


def _detect_binary_array_compression(array_elem) -> str:
    _cv, mode = _find_binary_array_compression_cv(array_elem)
    if mode is None:
        raise ValueError("Unsupported or missing mzML binary compression template")
    return mode


def _upsert_binary_array_compression_cv(array_elem, binary_compression: str) -> None:
    desired_accession, desired_name = COMPRESSION_CV_MAP[binary_compression]
    compression_accessions = {accession for accession, _name in COMPRESSION_CV_MAP.values()}
    existing = [
        child
        for child in array_elem
        if child.tag == CV_PARAM_TAG and child.get("accession") in compression_accessions
    ]
    if existing:
        target = existing[0]
        target.set("cvRef", "MS")
        target.set("accession", desired_accession)
        target.set("name", desired_name)
        target.set("value", "")
        for extra in existing[1:]:
            array_elem.remove(extra)
        return

    target = _find_cv_param(array_elem, "MS:1000514")
    if target is None:
        target = _find_cv_param(array_elem, "MS:1000515")
    new_cv = _cv_param(desired_accession, desired_name)
    if target is None:
        binary = array_elem.find(BINARY_TAG)
        if binary is None:
            array_elem.append(new_cv)
        else:
            binary.addprevious(new_cv)
    else:
        target.addprevious(new_cv)


def _encode_array_like_template(
    array: np.ndarray,
    array_elem,
    *,
    binary_compression: str,
) -> str:
    _type_cv, dtype = _find_binary_array_type_cv(array_elem)
    if binary_compression == "preserve_template":
        compression_mode = _detect_binary_array_compression(array_elem)
    else:
        compression_mode = binary_compression
        _upsert_binary_array_compression_cv(array_elem, compression_mode)

    raw_payload = np.asarray(array, dtype=dtype).tobytes()
    if compression_mode == "none":
        payload = raw_payload
    elif compression_mode == "zlib":
        payload = zlib.compress(raw_payload)
    else:
        raise ValueError(f"Unsupported mzML binary compression: {compression_mode}")
    return base64.b64encode(payload).decode("ascii")


def _binary_array_template_info(array_elem, *, binary_compression: str):
    _type_cv, dtype = _find_binary_array_type_cv(array_elem)
    if binary_compression == "preserve_template":
        compression_mode = _detect_binary_array_compression(array_elem)
    else:
        compression_mode = binary_compression
        _upsert_binary_array_compression_cv(array_elem, compression_mode)
    binary = array_elem.find(BINARY_TAG)
    if binary is None:
        binary = etree.SubElement(array_elem, BINARY_TAG)
    return dtype, compression_mode, binary


def _encode_array_with_template_info(
    array: np.ndarray,
    *,
    dtype: np.dtype,
    compression_mode: str,
) -> str:
    dtype = np.dtype(dtype)
    if HAVE_NATIVE_WRITER and _native_writer is not None:
        dtype_code = NATIVE_DTYPE_CODE_MAP.get(dtype)
        if dtype_code is not None and compression_mode in ("none", "zlib"):
            try:
                return _native_writer.encode_float_array(
                    array,
                    dtype_code,
                    compression_mode == "zlib",
                )
            except Exception:
                pass
    raw_payload = np.asarray(array, dtype=dtype).tobytes()
    if compression_mode == "none":
        payload = raw_payload
    elif compression_mode == "zlib":
        payload = zlib.compress(raw_payload)
    else:
        raise ValueError(f"Unsupported mzML binary compression: {compression_mode}")
    return base64.b64encode(payload).decode("ascii")


def _update_binary_array_in_place_with_template_info(
    array_elem,
    array: np.ndarray,
    *,
    dtype: np.dtype,
    compression_mode: str,
    binary_elem,
) -> None:
    encoded = _encode_array_with_template_info(
        array,
        dtype=dtype,
        compression_mode=compression_mode,
    )
    array_elem.set("encodedLength", str(len(encoded)))
    if "arrayLength" in array_elem.attrib:
        array_elem.set("arrayLength", str(len(array)))
    binary_elem.text = encoded


def _try_native_encode_array_pair(
    first_array: np.ndarray,
    *,
    first_dtype: np.dtype,
    first_compression_mode: str,
    second_array: np.ndarray,
    second_dtype: np.dtype,
    second_compression_mode: str,
):
    if not HAVE_NATIVE_WRITER or _native_writer is None:
        return None
    first_code = NATIVE_DTYPE_CODE_MAP.get(np.dtype(first_dtype))
    second_code = NATIVE_DTYPE_CODE_MAP.get(np.dtype(second_dtype))
    if first_code is None or second_code is None:
        return None
    if first_compression_mode not in ("none", "zlib") or second_compression_mode not in ("none", "zlib"):
        return None
    pair_func = getattr(_native_writer, "encode_float_array_pair", None)
    if pair_func is None:
        return None
    try:
        return pair_func(
            first_array,
            first_code,
            first_compression_mode == "zlib",
            second_array,
            second_code,
            second_compression_mode == "zlib",
        )
    except Exception:
        return None


def _try_native_encode_prepared_array_batch(batch: Sequence[Dict]):
    if (
        not HAVE_NATIVE_WRITER
        or _native_writer is None
        or NATIVE_WRITER_BATCH_SIZE <= 1
        or len(batch) <= 1
    ):
        return None
    batch_func = getattr(_native_writer, "encode_float_array_pairs_batch", None)
    if batch_func is None:
        return None
    first_code = NATIVE_DTYPE_CODE_MAP.get(np.dtype(batch[0]["mz_dtype"]))
    second_code = NATIVE_DTYPE_CODE_MAP.get(np.dtype(batch[0]["int_dtype"]))
    if first_code is None or second_code is None:
        return None
    try:
        return batch_func(
            [item["mz_array"] for item in batch],
            first_code,
            batch[0]["mz_compression_mode"] == "zlib",
            [item["intensity_array"] for item in batch],
            second_code,
            batch[0]["int_compression_mode"] == "zlib",
        )
    except Exception:
        return None


def _native_batch_key(prepared: Dict):
    mz_code = NATIVE_DTYPE_CODE_MAP.get(np.dtype(prepared["mz_dtype"]))
    int_code = NATIVE_DTYPE_CODE_MAP.get(np.dtype(prepared["int_dtype"]))
    if mz_code is None or int_code is None:
        return None
    if prepared["mz_compression_mode"] not in ("none", "zlib"):
        return None
    if prepared["int_compression_mode"] not in ("none", "zlib"):
        return None
    return (
        mz_code,
        prepared["mz_compression_mode"],
        int_code,
        prepared["int_compression_mode"],
    )


def _prepare_existing_spectrum_binary_pair(
    spectrum_elem,
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
    *,
    binary_compression: str,
):
    if len(mz_array) != len(intensity_array):
        raise ValueError(f"m/z and intensity array length mismatch: {len(mz_array)} vs {len(intensity_array)}")
    spectrum_elem.set("defaultArrayLength", str(len(mz_array)))
    mz_elem, int_elem = _find_spectrum_mz_int_binary_arrays(spectrum_elem)
    if mz_elem is None or int_elem is None:
        return None

    mz_dtype, mz_compression_mode, mz_binary = _binary_array_template_info(
        mz_elem,
        binary_compression=binary_compression,
    )
    int_dtype, int_compression_mode, int_binary = _binary_array_template_info(
        int_elem,
        binary_compression=binary_compression,
    )
    mz_encoded_array = np.asarray(mz_array, dtype=mz_dtype)
    int_encoded_array = np.asarray(intensity_array, dtype=int_dtype)
    _update_spectrum_derived_cv_params(
        spectrum_elem,
        mz_encoded_array,
        int_encoded_array,
    )
    return {
        "spectrum_elem": spectrum_elem,
        "mz_array": mz_encoded_array,
        "intensity_array": int_encoded_array,
        "mz_elem": mz_elem,
        "int_elem": int_elem,
        "mz_dtype": mz_dtype,
        "int_dtype": int_dtype,
        "mz_compression_mode": mz_compression_mode,
        "int_compression_mode": int_compression_mode,
        "mz_binary": mz_binary,
        "int_binary": int_binary,
    }


def _apply_encoded_pair_to_prepared(prepared: Dict, mz_encoded: str, int_encoded: str) -> None:
    mz_elem = prepared["mz_elem"]
    int_elem = prepared["int_elem"]
    mz_array = prepared["mz_array"]
    intensity_array = prepared["intensity_array"]
    mz_elem.set("encodedLength", str(len(mz_encoded)))
    if "arrayLength" in mz_elem.attrib:
        mz_elem.set("arrayLength", str(len(mz_array)))
    int_elem.set("encodedLength", str(len(int_encoded)))
    if "arrayLength" in int_elem.attrib:
        int_elem.set("arrayLength", str(len(intensity_array)))
    prepared["mz_binary"].text = mz_encoded
    prepared["int_binary"].text = int_encoded


def _update_prepared_spectrum_binary_pair(prepared: Dict) -> None:
    native_pair = _try_native_encode_array_pair(
        prepared["mz_array"],
        first_dtype=prepared["mz_dtype"],
        first_compression_mode=prepared["mz_compression_mode"],
        second_array=prepared["intensity_array"],
        second_dtype=prepared["int_dtype"],
        second_compression_mode=prepared["int_compression_mode"],
    )
    if native_pair is not None:
        mz_encoded, int_encoded = native_pair
        _apply_encoded_pair_to_prepared(prepared, mz_encoded, int_encoded)
        return
    _update_binary_array_in_place_with_template_info(
        prepared["mz_elem"],
        prepared["mz_array"],
        dtype=prepared["mz_dtype"],
        compression_mode=prepared["mz_compression_mode"],
        binary_elem=prepared["mz_binary"],
    )
    _update_binary_array_in_place_with_template_info(
        prepared["int_elem"],
        prepared["intensity_array"],
        dtype=prepared["int_dtype"],
        compression_mode=prepared["int_compression_mode"],
        binary_elem=prepared["int_binary"],
    )


def _flush_prepared_spectrum_binary_batch(batch: List[Dict]) -> None:
    if not batch:
        return
    native_encoded = _try_native_encode_prepared_array_batch(batch)
    if native_encoded is not None:
        if len(native_encoded) != len(batch):
            raise ValueError(f"native mzML writer batch length mismatch: {len(native_encoded)} vs {len(batch)}")
        for prepared, pair in zip(batch, native_encoded):
            mz_encoded, int_encoded = pair
            _apply_encoded_pair_to_prepared(prepared, mz_encoded, int_encoded)
        return
    for prepared in batch:
        _update_prepared_spectrum_binary_pair(prepared)


def _update_binary_array_in_place(
    array_elem,
    array: np.ndarray,
    *,
    binary_compression: str,
) -> None:
    encoded = _encode_array_like_template(
        array,
        array_elem,
        binary_compression=binary_compression,
    )
    array_elem.set("encodedLength", str(len(encoded)))
    if "arrayLength" in array_elem.attrib:
        array_elem.set("arrayLength", str(len(array)))
    binary = array_elem.find(BINARY_TAG)
    if binary is None:
        binary = etree.SubElement(array_elem, BINARY_TAG)
    binary.text = encoded


def _format_mzml_float(value: float) -> str:
    """Use a compact deterministic float form for derived mzML cvParam values."""
    if not np.isfinite(value):
        value = 0.0
    return format(float(value), ".15g")


def _update_direct_spectrum_cv_value(spectrum_elem, accession: str, value: float) -> None:
    node = _find_cv_param(spectrum_elem, accession)
    if node is None:
        return
    node.set("value", _format_mzml_float(value))


def _update_direct_spectrum_cv_values(spectrum_elem, values: Dict[str, float]) -> None:
    if not values:
        return
    remaining = set(values)
    for child in spectrum_elem:
        if child.tag != CV_PARAM_TAG:
            continue
        accession = child.get("accession")
        if accession not in remaining:
            continue
        child.set("value", _format_mzml_float(values[accession]))
        remaining.remove(accession)
        if not remaining:
            return


def _update_spectrum_derived_cv_params(
    spectrum_elem,
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
) -> None:
    """Keep summary cvParams synchronized with rewritten binary arrays.

    DIA-NN is less tolerant than OpenMS when mzML summary metadata disagrees with
    the binary arrays.  The compressor may quantize or rebuild arrays, so the
    direct spectrum-level metadata must be regenerated with the final arrays.
    """
    mz = np.asarray(mz_array, dtype=np.float64)
    intensity = np.asarray(intensity_array, dtype=np.float64)
    if mz.shape != intensity.shape:
        raise ValueError(f"m/z and intensity array length mismatch: {mz.size} vs {intensity.size}")

    if mz.size == 0:
        tic = 0.0
        base_peak_intensity = 0.0
        base_peak_mz = 0.0
        lowest_mz = 0.0
        highest_mz = 0.0
    elif bool(np.isfinite(mz).all()) and bool(np.isfinite(intensity).all()):
        peak_idx = int(np.argmax(intensity))
        tic = float(np.sum(intensity, dtype=np.float64))
        base_peak_intensity = float(intensity[peak_idx])
        base_peak_mz = float(mz[peak_idx])
        lowest_mz = float(np.min(mz))
        highest_mz = float(np.max(mz))
    else:
        finite_intensity = np.nan_to_num(intensity, nan=0.0, posinf=0.0, neginf=0.0)
        finite_mz = np.nan_to_num(mz, nan=0.0, posinf=0.0, neginf=0.0)
        peak_idx = int(np.argmax(finite_intensity))
        tic = float(np.sum(finite_intensity, dtype=np.float64))
        base_peak_intensity = float(finite_intensity[peak_idx])
        base_peak_mz = float(finite_mz[peak_idx])
        lowest_mz = float(np.min(finite_mz))
        highest_mz = float(np.max(finite_mz))

    _update_direct_spectrum_cv_values(
        spectrum_elem,
        {
            "MS:1000504": base_peak_mz,
            "MS:1000505": base_peak_intensity,
            "MS:1000285": tic,
            "MS:1000528": lowest_mz,
            "MS:1000527": highest_mz,
        },
    )


def _parse_root(metadata_xml: str | bytes):
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    if isinstance(metadata_xml, bytes):
        return etree.fromstring(metadata_xml, parser=parser)
    return etree.fromstring(metadata_xml.encode("utf-8"), parser=parser)


def _local_name(elem) -> str:
    return etree.QName(elem.tag).localname


def _split_indexed_root(root):
    if _local_name(root) == "indexedmzML":
        mzml_child = None
        for child in root:
            if _local_name(child) == "mzML":
                mzml_child = child
                break
        if mzml_child is None:
            raise ValueError("indexedmzML root missing mzML child")
        return root, mzml_child
    if _local_name(root) == "mzML":
        return None, root
    raise ValueError(f"Unsupported root tag for mzML reconstruction: {root.tag}")


def _serialize_indexed_open_tag(indexed_root) -> bytes:
    attrs = []
    for prefix, uri in indexed_root.nsmap.items():
        if prefix is None:
            attrs.append(f'xmlns="{uri}"')
        else:
            attrs.append(f'xmlns:{prefix}="{uri}"')
    for key, value in indexed_root.attrib.items():
        qname = etree.QName(key)
        if qname.namespace == XSI_URI:
            attr_name = f"xsi:{qname.localname}"
        elif qname.namespace:
            attr_name = qname.localname
        else:
            attr_name = key
        attrs.append(f'{attr_name}="{value}"')
    attr_blob = (" " + " ".join(attrs)) if attrs else ""
    return f"<indexedmzML{attr_blob}>".encode("utf-8")


def _default_indexed_open_tag(mzml_elem) -> bytes:
    ns_uri = mzml_elem.nsmap.get(None) or NS_URI
    schema_location = (
        "http://psi.hupo.org/ms/mzml "
        "https://raw.githubusercontent.com/HUPO-PSI/mzML/master/schema/mzML1.1.0_idx.xsd"
    )
    return (
        f'<indexedmzML xmlns="{ns_uri}" xmlns:xsi="{XSI_URI}" '
        f'xsi:schemaLocation="{schema_location}">'
    ).encode("utf-8")


def _render_index_block(name: str, pairs: Sequence[Tuple[str, int]]) -> bytes:
    lines = [f'    <index name="{name}">']
    for ident, offset in pairs:
        lines.append(f'      <offset idRef="{ident}">{offset}</offset>')
    lines.append("    </index>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _element_list_count(mzml_elem, list_name: str) -> int | None:
    node = mzml_elem.find(f".//mzml:{list_name}", namespaces=NS)
    if node is None:
        return None
    value = node.get("count")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _assert_unique_index_ids(name: str, pairs: Sequence[Tuple[str, int]]) -> None:
    seen = set()
    duplicates = []
    for ident, _offset in pairs:
        if ident in seen:
            duplicates.append(ident)
        seen.add(ident)
    if duplicates:
        preview = ", ".join(duplicates[:5])
        raise ValueError(f"{name} indexedmzML offsets contain duplicate idRef entries: {preview}")


def _validate_index_pair_counts(
    *,
    spectrum_offsets: Sequence[Tuple[str, int]],
    chromatogram_offsets: Sequence[Tuple[str, int]],
    expected_spectrum_count: int | None,
    expected_chromatogram_count: int | None,
) -> None:
    if expected_spectrum_count is not None and len(spectrum_offsets) != int(expected_spectrum_count):
        raise ValueError(
            "indexedmzML spectrum offset count mismatch: "
            f"{len(spectrum_offsets)} offsets vs spectrumList count {expected_spectrum_count}"
        )
    if expected_chromatogram_count is not None and len(chromatogram_offsets) != int(expected_chromatogram_count):
        raise ValueError(
            "indexedmzML chromatogram offset count mismatch: "
            f"{len(chromatogram_offsets)} offsets vs chromatogramList count {expected_chromatogram_count}"
        )
    _assert_unique_index_ids("spectrum", spectrum_offsets)
    _assert_unique_index_ids("chromatogram", chromatogram_offsets)


def _validate_index_pairs_against_body(
    body_path: Path,
    *,
    spectrum_offsets: Sequence[Tuple[str, int]],
    chromatogram_offsets: Sequence[Tuple[str, int]],
    mzml_base_offset: int,
    expected_spectrum_count: int | None,
    expected_chromatogram_count: int | None,
) -> None:
    _validate_index_pair_counts(
        spectrum_offsets=spectrum_offsets,
        chromatogram_offsets=chromatogram_offsets,
        expected_spectrum_count=expected_spectrum_count,
        expected_chromatogram_count=expected_chromatogram_count,
    )
    if not VALIDATE_INDEX_OFFSET_PROBES:
        return

    with body_path.open("rb") as handle:
        for name, pairs, expected_re in (
            ("spectrum", spectrum_offsets, _SPECTRUM_OPEN_RE),
            ("chromatogram", chromatogram_offsets, _CHROMATOGRAM_OPEN_RE),
        ):
            for ident, absolute_offset in pairs:
                body_offset = int(absolute_offset) - int(mzml_base_offset)
                if body_offset < 0:
                    raise ValueError(f"{name} offset for {ident} points before mzML body: {absolute_offset}")
                handle.seek(body_offset)
                probe = handle.read(512)
                if expected_re.match(probe) is None:
                    raise ValueError(
                        f"{name} offset for {ident} does not point to an opening {name} tag: "
                        f"offset={absolute_offset} bytes_at_offset={probe!r}"
                    )


def _validate_index_pairs_against_file(
    path: Path,
    *,
    spectrum_offsets: Sequence[Tuple[str, int]],
    chromatogram_offsets: Sequence[Tuple[str, int]],
    expected_spectrum_count: int | None,
    expected_chromatogram_count: int | None,
) -> None:
    _validate_index_pair_counts(
        spectrum_offsets=spectrum_offsets,
        chromatogram_offsets=chromatogram_offsets,
        expected_spectrum_count=expected_spectrum_count,
        expected_chromatogram_count=expected_chromatogram_count,
    )
    if not VALIDATE_INDEX_OFFSET_PROBES:
        return

    with path.open("rb") as handle:
        for name, pairs, expected_re in (
            ("spectrum", spectrum_offsets, _SPECTRUM_OPEN_RE),
            ("chromatogram", chromatogram_offsets, _CHROMATOGRAM_OPEN_RE),
        ):
            for ident, absolute_offset in pairs:
                if int(absolute_offset) < 0:
                    raise ValueError(f"{name} offset for {ident} is negative: {absolute_offset}")
                handle.seek(int(absolute_offset))
                probe = handle.read(512)
                if expected_re.match(probe) is None:
                    raise ValueError(
                        f"{name} offset for {ident} does not point to an opening {name} tag: "
                        f"offset={absolute_offset} bytes_at_offset={probe!r}"
                    )


def _build_fresh_indexed_mzml_bytes(root) -> bytes:
    indexed_root, mzml_elem = _split_indexed_root(root)

    mzml_clone = copy.deepcopy(mzml_elem)
    xml_decl = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    indexed_open = _serialize_indexed_open_tag(indexed_root) if indexed_root is not None else _default_indexed_open_tag(mzml_elem)
    indexed_close = b"</indexedmzML>\n"
    mzml_bytes = etree.tostring(mzml_clone, encoding="UTF-8", xml_declaration=False)
    prefix = xml_decl + indexed_open + b"\n" + mzml_bytes + b"\n"

    spectrum_offsets = [
        (m.group(1).decode("utf-8"), len(xml_decl) + len(indexed_open) + 1 + m.start())
        for m in _SPECTRUM_OPEN_RE.finditer(mzml_bytes)
    ]
    chromatogram_offsets = [
        (m.group(1).decode("utf-8"), len(xml_decl) + len(indexed_open) + 1 + m.start())
        for m in _CHROMATOGRAM_OPEN_RE.finditer(mzml_bytes)
    ]
    _validate_index_pair_counts(
        spectrum_offsets=spectrum_offsets,
        chromatogram_offsets=chromatogram_offsets,
        expected_spectrum_count=_element_list_count(mzml_elem, "spectrumList"),
        expected_chromatogram_count=_element_list_count(mzml_elem, "chromatogramList"),
    )

    index_blocks = [ _render_index_block("spectrum", spectrum_offsets) ]
    if chromatogram_offsets:
        index_blocks.append(_render_index_block("chromatogram", chromatogram_offsets))
    index_list = (
        f'<indexList count="{len(index_blocks)}">\n'.encode("utf-8")
        + b"".join(index_blocks)
        + b"</indexList>\n"
    )
    index_list_offset_value = len(prefix)
    index_list_offset = f"  <indexListOffset>{index_list_offset_value}</indexListOffset>\n".encode("utf-8")
    # OpenMS writes an indexedmzML checksum value of 0.  Several downstream
    # readers accept this path more consistently than custom SHA-1 variants.
    file_checksum = b"  <fileChecksum>0</fileChecksum>\n"
    return prefix + index_list + index_list_offset + file_checksum + indexed_close


def _iter_open_tag_offsets_from_file(
    path: Path,
    regex: re.Pattern[bytes],
    *,
    base_offset: int,
    chunk_size: int = 8 * 1024 * 1024,
    overlap: int = _INDEX_SCAN_OVERLAP_BYTES,
) -> List[Tuple[str, int]]:
    """Find indexedmzML target offsets without materializing the mzML body.

    lxml may serialize very large spectra as long physical lines, so line-based
    scanning can accidentally read a large fraction of the mzML body at once.
    This overlap scanner keeps only a bounded suffix while still preserving
    regex matches that cross arbitrary chunk boundaries.
    """
    out: List[Tuple[str, int]] = []
    file_offset = 0
    tail = b""
    last_emitted_abs = -1
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            eof = not chunk
            data = tail + chunk
            data_start = file_offset - len(tail)
            if eof:
                process_limit = len(data)
            elif len(data) > overlap:
                process_limit = len(data) - overlap
            else:
                process_limit = 0

            if process_limit > 0:
                for match in regex.finditer(data):
                    if match.start() >= process_limit:
                        break
                    abs_pos = int(data_start + match.start())
                    if abs_pos <= last_emitted_abs:
                        continue
                    out.append((match.group(1).decode("utf-8"), int(base_offset + abs_pos)))
                    last_emitted_abs = abs_pos

            if eof:
                break
            tail = data[process_limit:]
            file_offset += len(chunk)
    return out


def _iter_open_tag_offsets_from_file_region(
    path: Path,
    regex: re.Pattern[bytes],
    *,
    start_offset: int,
    end_offset: int,
    chunk_size: int = 8 * 1024 * 1024,
    overlap: int = _INDEX_SCAN_OVERLAP_BYTES,
) -> List[Tuple[str, int]]:
    """Find absolute offsets for opening tags in a bounded file region."""
    out: List[Tuple[str, int]] = []
    start_offset = int(start_offset)
    end_offset = int(end_offset)
    if end_offset < start_offset:
        raise ValueError(f"invalid index scan region: start={start_offset} end={end_offset}")
    file_offset = start_offset
    tail = b""
    last_emitted_abs = -1
    with path.open("rb") as handle:
        handle.seek(start_offset)
        while file_offset < end_offset:
            remaining = end_offset - file_offset
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                break
            eof = file_offset + len(chunk) >= end_offset
            data = tail + chunk
            data_start = file_offset - len(tail)
            if eof:
                process_limit = len(data)
            elif len(data) > overlap:
                process_limit = len(data) - overlap
            else:
                process_limit = 0

            if process_limit > 0:
                for match in regex.finditer(data):
                    if match.start() >= process_limit:
                        break
                    abs_pos = int(data_start + match.start())
                    if abs_pos <= last_emitted_abs:
                        continue
                    out.append((match.group(1).decode("utf-8"), abs_pos))
                    last_emitted_abs = abs_pos

            tail = data[process_limit:]
            file_offset += len(chunk)
    return out


def _write_fresh_indexed_mzml_file(root, output_path: str | Path) -> Path:
    """Write mzML/indexedmzML without constructing the final document bytes."""
    if not DIRECT_OUTPUT_INDEXED_MZML_WRITER:
        return _write_indexed_mzml_body_spool(root, output_path)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indexed_root, mzml_elem = _split_indexed_root(root)

    xml_decl = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    indexed_open = _serialize_indexed_open_tag(indexed_root) if indexed_root is not None else _default_indexed_open_tag(mzml_elem)
    indexed_close = b"</indexedmzML>\n"
    mzml_base_offset = len(xml_decl) + len(indexed_open) + 1

    expected_spectrum_count = _element_list_count(mzml_elem, "spectrumList")
    expected_chromatogram_count = _element_list_count(mzml_elem, "chromatogramList")
    tmp_output = output_path.with_name(f".{output_path.name}.tmp")
    if NATIVE_INDEXED_MZML_FINALIZER and _native_writer is not None:
        with tempfile.NamedTemporaryFile(prefix="trackcodec_mzml_body_", suffix=".xml", delete=True) as tmp:
            body_path = Path(tmp.name)
            etree.ElementTree(mzml_elem).write(
                str(body_path),
                encoding="UTF-8",
                xml_declaration=False,
            )
            try:
                _native_writer.write_indexed_mzml_from_body(
                    str(body_path),
                    str(tmp_output),
                    xml_decl,
                    indexed_open,
                    indexed_close,
                    int(mzml_base_offset),
                    -1 if expected_spectrum_count is None else int(expected_spectrum_count),
                    -1 if expected_chromatogram_count is None else int(expected_chromatogram_count),
                )
                if NATIVE_INDEXED_MZML_FINALIZER_VALIDATE:
                    spectrum_offsets = _iter_open_tag_offsets_from_file(
                        body_path,
                        _SPECTRUM_OPEN_RE,
                        base_offset=mzml_base_offset,
                    )
                    chromatogram_offsets = _iter_open_tag_offsets_from_file(
                        body_path,
                        _CHROMATOGRAM_OPEN_RE,
                        base_offset=mzml_base_offset,
                    )
                    _validate_index_pairs_against_body(
                        body_path,
                        spectrum_offsets=spectrum_offsets,
                        chromatogram_offsets=chromatogram_offsets,
                        mzml_base_offset=mzml_base_offset,
                        expected_spectrum_count=expected_spectrum_count,
                        expected_chromatogram_count=expected_chromatogram_count,
                    )
                os.replace(tmp_output, output_path)
                return output_path
            except Exception:
                if tmp_output.exists():
                    tmp_output.unlink()

    try:
        bytes_written = 0

        def _write_counted(handle, data: bytes) -> None:
            nonlocal bytes_written
            handle.write(data)
            bytes_written += len(data)

        with tmp_output.open("wb") as out:
            _write_counted(out, xml_decl)
            _write_counted(out, indexed_open)
            _write_counted(out, b"\n")
            body_start = bytes_written
            etree.ElementTree(mzml_elem).write(
                out,
                encoding="UTF-8",
                xml_declaration=False,
            )
            bytes_written = out.tell()
            body_end = bytes_written

        spectrum_offsets = _iter_open_tag_offsets_from_file_region(
            tmp_output,
            _SPECTRUM_OPEN_RE,
            start_offset=body_start,
            end_offset=body_end,
        )
        chromatogram_offsets = _iter_open_tag_offsets_from_file_region(
            tmp_output,
            _CHROMATOGRAM_OPEN_RE,
            start_offset=body_start,
            end_offset=body_end,
        )

        _validate_index_pairs_against_file(
            tmp_output,
            spectrum_offsets=spectrum_offsets,
            chromatogram_offsets=chromatogram_offsets,
            expected_spectrum_count=expected_spectrum_count,
            expected_chromatogram_count=expected_chromatogram_count,
        )

        index_blocks = [_render_index_block("spectrum", spectrum_offsets)]
        if chromatogram_offsets:
            index_blocks.append(_render_index_block("chromatogram", chromatogram_offsets))
        index_list = (
            f'<indexList count="{len(index_blocks)}">\n'.encode("utf-8")
            + b"".join(index_blocks)
            + b"</indexList>\n"
        )

        with tmp_output.open("ab") as out:
            _write_counted(out, b"\n")
            index_list_offset_value = bytes_written
            _write_counted(out, index_list)
            index_list_offset = f"  <indexListOffset>{index_list_offset_value}</indexListOffset>\n".encode("utf-8")
            _write_counted(out, index_list_offset)
            _write_counted(out, b"  <fileChecksum>0</fileChecksum>\n")
            _write_counted(out, indexed_close)
        os.replace(tmp_output, output_path)
    finally:
        if tmp_output.exists():
            tmp_output.unlink()
    return output_path


def _write_indexed_mzml_body_spool(root, output_path: str | Path) -> Path:
    """Write indexedmzML through a body spool file, the fastest validated path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indexed_root, mzml_elem = _split_indexed_root(root)

    xml_decl = b'<?xml version="1.0" encoding="UTF-8"?>\n'
    indexed_open = _serialize_indexed_open_tag(indexed_root) if indexed_root is not None else _default_indexed_open_tag(mzml_elem)
    indexed_close = b"</indexedmzML>\n"
    mzml_base_offset = len(xml_decl) + len(indexed_open) + 1

    fd, body_name = tempfile.mkstemp(prefix="trackcodec_mzml_body_", suffix=".xml")
    os.close(fd)
    body_path = Path(body_name)
    try:
        etree.ElementTree(mzml_elem).write(
            str(body_path),
            encoding="UTF-8",
            xml_declaration=False,
        )
        expected_spectrum_count = _element_list_count(mzml_elem, "spectrumList")
        expected_chromatogram_count = _element_list_count(mzml_elem, "chromatogramList")
        tmp_output = output_path.with_name(f".{output_path.name}.tmp")
        if NATIVE_INDEXED_MZML_FINALIZER and _native_writer is not None:
            try:
                _native_writer.write_indexed_mzml_from_body(
                    str(body_path),
                    str(tmp_output),
                    xml_decl,
                    indexed_open,
                    indexed_close,
                    int(mzml_base_offset),
                    -1 if expected_spectrum_count is None else int(expected_spectrum_count),
                    -1 if expected_chromatogram_count is None else int(expected_chromatogram_count),
                )
                if NATIVE_INDEXED_MZML_FINALIZER_VALIDATE:
                    spectrum_offsets = _iter_open_tag_offsets_from_file(
                        body_path,
                        _SPECTRUM_OPEN_RE,
                        base_offset=mzml_base_offset,
                    )
                    chromatogram_offsets = _iter_open_tag_offsets_from_file(
                        body_path,
                        _CHROMATOGRAM_OPEN_RE,
                        base_offset=mzml_base_offset,
                    )
                    _validate_index_pairs_against_body(
                        body_path,
                        spectrum_offsets=spectrum_offsets,
                        chromatogram_offsets=chromatogram_offsets,
                        mzml_base_offset=mzml_base_offset,
                        expected_spectrum_count=expected_spectrum_count,
                        expected_chromatogram_count=expected_chromatogram_count,
                    )
                os.replace(tmp_output, output_path)
                return output_path
            except Exception:
                if tmp_output.exists():
                    tmp_output.unlink()

        spectrum_offsets = _iter_open_tag_offsets_from_file(
            body_path,
            _SPECTRUM_OPEN_RE,
            base_offset=mzml_base_offset,
        )
        chromatogram_offsets = _iter_open_tag_offsets_from_file(
            body_path,
            _CHROMATOGRAM_OPEN_RE,
            base_offset=mzml_base_offset,
        )

        _validate_index_pairs_against_body(
            body_path,
            spectrum_offsets=spectrum_offsets,
            chromatogram_offsets=chromatogram_offsets,
            mzml_base_offset=mzml_base_offset,
            expected_spectrum_count=expected_spectrum_count,
            expected_chromatogram_count=expected_chromatogram_count,
        )

        index_blocks = [_render_index_block("spectrum", spectrum_offsets)]
        if chromatogram_offsets:
            index_blocks.append(_render_index_block("chromatogram", chromatogram_offsets))
        index_list = (
            f'<indexList count="{len(index_blocks)}">\n'.encode("utf-8")
            + b"".join(index_blocks)
            + b"</indexList>\n"
        )
        bytes_written = 0

        def _write_counted(handle, data: bytes) -> None:
            nonlocal bytes_written
            handle.write(data)
            bytes_written += len(data)

        try:
            with tmp_output.open("wb") as out:
                _write_counted(out, xml_decl)
                _write_counted(out, indexed_open)
                _write_counted(out, b"\n")
                with body_path.open("rb") as body:
                    while True:
                        chunk = body.read(8 * 1024 * 1024)
                        if not chunk:
                            break
                        _write_counted(out, chunk)

                _write_counted(out, b"\n")
                index_list_offset_value = bytes_written
                _write_counted(out, index_list)
                index_list_offset = f"  <indexListOffset>{index_list_offset_value}</indexListOffset>\n".encode("utf-8")
                _write_counted(out, index_list_offset)
                _write_counted(out, b"  <fileChecksum>0</fileChecksum>\n")
                _write_counted(out, indexed_close)
            os.replace(tmp_output, output_path)
        finally:
            if tmp_output.exists():
                tmp_output.unlink()
    finally:
        if body_path.exists():
            body_path.unlink()
    return output_path


def _spectrum_ms_level(spectrum_elem) -> int:
    cv = _find_cv_param(spectrum_elem, "MS:1000511")
    if cv is not None:
        try:
            return int(float(cv.get("value", "0")))
        except (TypeError, ValueError):
            return 0
    return 0


def _scan_start_time(spectrum_elem) -> float:
    scan_list = spectrum_elem.find(SCAN_LIST_TAG)
    if scan_list is None:
        return 0.0
    for scan in scan_list:
        if scan.tag != SCAN_TAG:
            continue
        node = _find_cv_param(scan, "MS:1000016")
        if node is None:
            continue
        try:
            return float(node.get("value", "0.0"))
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _direct_child(parent, tag: str):
    if parent is None:
        return None
    for child in parent:
        if child.tag == tag:
            return child
    try:
        return parent.find(tag)
    except Exception:
        return None


def _dia_window_key_from_spectrum(spectrum_elem) -> Tuple[float, float, float]:
    precursor_list = _direct_child(spectrum_elem, PRECURSOR_LIST_TAG)
    precursor = _direct_child(precursor_list, PRECURSOR_TAG)
    if precursor is None:
        return (0.0, 0.0, 0.0)
    isolation = _direct_child(precursor, ISOLATION_WINDOW_TAG)
    if isolation is None:
        return (0.0, 0.0, 0.0)

    def _cv_float(accession: str) -> float:
        node = _find_cv_param(isolation, accession)
        if node is None:
            return 0.0
        try:
            return float(node.get("value", "0.0"))
        except (TypeError, ValueError):
            return 0.0

    target = _cv_float("MS:1000827")
    lower = _cv_float("MS:1000828")
    upper = _cv_float("MS:1000829")
    return (round(target, 4), round(lower, 4), round(upper, 4))


def _iter_aux_binary_arrays(root) -> List[etree._Element]:
    out = []
    for array_elem in root.xpath(".//mzml:binaryDataArray", namespaces=NS):
        spectrum = array_elem.xpath("ancestor::mzml:spectrum[1]", namespaces=NS)
        if spectrum:
            continue
        out.append(array_elem)
    return out


def extract_auxiliary_binary_records(mzml_path: str) -> List[Dict]:
    return _extract_auxiliary_binary_records_iterparse(mzml_path)


def _extract_auxiliary_binary_records_iterparse(mzml_path: str) -> List[Dict]:
    context = etree.iterparse(
        str(mzml_path),
        events=("start", "end"),
        tag=(f"{{{NS_URI}}}spectrum", f"{{{NS_URI}}}binaryDataArray"),
        recover=True,
    )
    records = []
    spectrum_stack: list[int | None] = []
    current_ms_level: int | None = None
    for event, elem in context:
        local_name = etree.QName(elem.tag).localname
        if event == "start":
            if local_name == "spectrum":
                spectrum_stack.append(current_ms_level)
                current_ms_level = None
            continue
        if local_name == "spectrum":
            current_ms_level = spectrum_stack.pop() if spectrum_stack else None
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
            continue
        if local_name != "binaryDataArray":
            continue
        if spectrum_stack:
            ms_level = current_ms_level
            if ms_level is None:
                node = elem.getparent()
                while node is not None and etree.QName(node.tag).localname != "spectrum":
                    node = node.getparent()
                ms_level = _spectrum_ms_level(node) if node is not None else 0
                current_ms_level = ms_level
            if ms_level in (1, 2):
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
                continue
        binary = elem.find("./mzml:binary", namespaces=NS)
        cv_accessions = [
            cv.get("accession")
            for cv in elem.xpath("./mzml:cvParam", namespaces=NS)
            if cv.get("accession")
        ]
        chromatogram = elem.xpath("ancestor::mzml:chromatogram[1]", namespaces=NS)
        records.append({
            "encoded_length": elem.get("encodedLength", "0"),
            "binary_text": binary.text or "",
            "cv_accessions": cv_accessions,
            "array_length": elem.get("arrayLength", "0"),
            "chromatogram_id": chromatogram[0].get("id") if chromatogram else None,
        })
        elem.clear()
        while elem.getprevious() is not None:
            del elem.getparent()[0]
    del context
    return records


def extract_binary_stripped_metadata_and_auxiliary_records(mzml_path: str) -> Tuple[bytes, List[Dict]]:
    records = _extract_auxiliary_binary_records_iterparse(mzml_path)
    xml_bytes = extract_binary_stripped_metadata_xml(mzml_path)
    return xml_bytes, records


def restore_auxiliary_binary_records(root, records: Sequence[Dict]) -> None:
    aux_arrays = _iter_aux_binary_arrays(root)
    if len(aux_arrays) != len(records):
        raise ValueError(f"Aux binary count mismatch: {len(aux_arrays)} vs {len(records)}")
    for array_elem, record in zip(aux_arrays, records):
        array_elem.set("encodedLength", str(record.get("encoded_length", "0")))
        binary = array_elem.find("./mzml:binary", namespaces=NS)
        if binary is None:
            binary = etree.SubElement(array_elem, f"{{{NS_URI}}}binary")
        binary.text = record.get("binary_text", "")


def extract_ms1_scan_templates(metadata_xml: str | bytes) -> List[Dict]:
    root = _parse_root(metadata_xml)
    return extract_ms1_scan_templates_from_root(root)


def extract_ms1_scan_templates_from_root(root) -> List[Dict]:
    templates = []
    for spectrum in root.xpath(".//mzml:spectrum", namespaces=NS):
        if _spectrum_ms_level(spectrum) != 1:
            continue
        templates.append(
            {
                "scan_idx": len(templates),
                "rt": _scan_start_time(spectrum),
                "array_length": int(spectrum.get("defaultArrayLength", "0")),
            }
        )
    return templates


def reconstruct_ms1_scans_from_islands(decoded_islands: Sequence[Dict], templates: Sequence[Dict]) -> List[Dict]:
    by_scan = defaultdict(list)
    for island in decoded_islands:
        by_scan[int(island["scan_idx"])].append(island)

    reconstructed = []
    for template in templates:
        scan_idx = int(template["scan_idx"])
        n = int(template["array_length"])
        mz_array = np.zeros(n, dtype=np.float64)
        intensity_array = np.zeros(n, dtype=np.float64)
        for island in by_scan.get(scan_idx, []):
            start = int(island.get("array_start_idx", -1))
            count = int(island["n_points"])
            if start < 0:
                raise ValueError("Missing array_start_idx for MS1 reconstruction")
            end = start + count
            if end > n:
                raise ValueError(f"MS1 island overruns scan length: {end} > {n}")
            mz_array[start:end] = np.asarray(island["mz_array"], dtype=np.float64)
            intensity_array[start:end] = np.asarray(island["intensity_array"], dtype=np.float64)
        reconstructed.append(
            {
                "scan_idx": scan_idx,
                "rt": float(template["rt"]),
                "mz_array": mz_array,
                "intensity_array": intensity_array,
            }
        )
    return reconstructed


def reconstruct_ms1_scans_from_islands_with_complement_mz(
    decoded_islands: Sequence[Dict],
    templates: Sequence[Dict],
    complement_mz_entries: Dict[str, Sequence] | None,
) -> List[Dict]:
    by_scan = defaultdict(list)
    for island in decoded_islands:
        by_scan[int(island["scan_idx"])].append(island)

    complement_by_scan = defaultdict(list)
    if complement_mz_entries is not None:
        scan_indices = complement_mz_entries.get("scan_indices", [])
        run_starts = complement_mz_entries.get("run_starts", [])
        run_lengths = complement_mz_entries.get("run_lengths", [])
        mz_runs = complement_mz_entries.get("mz_runs", [])
        if not (len(scan_indices) == len(run_starts) == len(run_lengths) == len(mz_runs)):
            raise ValueError("MS1 complement m/z sidecar length mismatch")
        for scan_idx, start, length, mz_run in zip(scan_indices, run_starts, run_lengths, mz_runs):
            complement_by_scan[int(scan_idx)].append((int(start), int(length), np.asarray(mz_run, dtype=np.float64)))

    reconstructed = []
    for template in templates:
        scan_idx = int(template["scan_idx"])
        n = int(template["array_length"])
        mz_array = np.zeros(n, dtype=np.float64)
        intensity_array = np.zeros(n, dtype=np.float64)
        for start, count, mz_run in complement_by_scan.get(scan_idx, []):
            end = start + count
            if start < 0 or end > n:
                raise ValueError(f"MS1 complement m/z run overruns scan length: scan {scan_idx}, {start}:{end} > {n}")
            if len(mz_run) != count:
                raise ValueError(f"MS1 complement m/z run length mismatch for scan {scan_idx}: {len(mz_run)} vs {count}")
            mz_array[start:end] = mz_run
        for island in by_scan.get(scan_idx, []):
            start = int(island.get("array_start_idx", -1))
            count = int(island["n_points"])
            if start < 0:
                raise ValueError("Missing array_start_idx for MS1 reconstruction")
            end = start + count
            if end > n:
                raise ValueError(f"MS1 island overruns scan length: {end} > {n}")
            island_mz = np.asarray(island.get("mz_array", []), dtype=np.float64)
            if full_mz_arrays is None:
                if len(island_mz) != count:
                    raise ValueError(f"Missing MS1 island m/z values without full m/z sidecar: {len(island_mz)} vs {count}")
                mz_array[start:end] = island_mz
            elif len(island_mz) not in (0, count):
                raise ValueError(f"MS1 island m/z length mismatch: {len(island_mz)} vs {count}")
            intensity_array[start:end] = np.asarray(island["intensity_array"], dtype=np.float64)
        reconstructed.append(
            {
                "scan_idx": scan_idx,
                "rt": float(template["rt"]),
                "mz_array": mz_array,
                "intensity_array": intensity_array,
            }
        )
    return reconstructed


def reconstruct_ms1_scans_from_islands_with_full_mz(
    decoded_islands: Sequence[Dict],
    templates: Sequence[Dict],
    full_mz_arrays: Sequence[np.ndarray] | None,
) -> List[Dict]:
    by_scan = defaultdict(list)
    for island in decoded_islands:
        by_scan[int(island["scan_idx"])].append(island)

    reconstructed = []
    for template in templates:
        scan_idx = int(template["scan_idx"])
        n = int(template["array_length"])
        if full_mz_arrays is not None:
            if scan_idx >= len(full_mz_arrays):
                raise ValueError(f"Missing full MS1 m/z sidecar for scan {scan_idx}")
            mz_array = np.asarray(full_mz_arrays[scan_idx], dtype=np.float64).copy()
            if len(mz_array) != n:
                raise ValueError(f"MS1 full m/z sidecar length mismatch for scan {scan_idx}: {len(mz_array)} vs {n}")
        else:
            mz_array = np.zeros(n, dtype=np.float64)
        intensity_array = np.zeros(n, dtype=np.float64)
        for island in by_scan.get(scan_idx, []):
            start = int(island.get("array_start_idx", -1))
            count = int(island["n_points"])
            if start < 0:
                raise ValueError("Missing array_start_idx for MS1 reconstruction")
            end = start + count
            if end > n:
                raise ValueError(f"MS1 island overruns scan length: {end} > {n}")
            island_mz = np.asarray(island.get("mz_array", []), dtype=np.float64)
            if full_mz_arrays is None:
                if len(island_mz) != count:
                    raise ValueError(f"Missing MS1 island m/z values without full m/z sidecar: {len(island_mz)} vs {count}")
                mz_array[start:end] = island_mz
            elif len(island_mz) not in (0, count):
                raise ValueError(f"MS1 island m/z length mismatch: {len(island_mz)} vs {count}")
            intensity_array[start:end] = np.asarray(island["intensity_array"], dtype=np.float64)
        reconstructed.append(
            {
                "scan_idx": scan_idx,
                "rt": float(template["rt"]),
                "mz_array": mz_array,
                "intensity_array": intensity_array,
            }
        )
    return reconstructed


def _replace_spectrum_binary_arrays(
    spectrum_elem,
    mz_array: np.ndarray,
    intensity_array: np.ndarray,
    *,
    binary_compression: str = "preserve_template",
) -> None:
    prepared = _prepare_existing_spectrum_binary_pair(
        spectrum_elem,
        mz_array,
        intensity_array,
        binary_compression=binary_compression,
    )
    if prepared is not None:
        _update_prepared_spectrum_binary_pair(prepared)
        return

    old = spectrum_elem.find(BINARY_DATA_ARRAY_LIST_TAG)
    if old is not None:
        spectrum_elem.remove(old)
    _update_spectrum_derived_cv_params(
        spectrum_elem,
        np.asarray(mz_array, dtype="<f8"),
        np.asarray(intensity_array, dtype="<f8"),
    )
    fallback_compression = "zlib" if binary_compression == "preserve_template" else binary_compression
    badl = etree.SubElement(spectrum_elem, BINARY_DATA_ARRAY_LIST_TAG, count="2")
    badl.append(
        _binary_array_elem(
            mz_array,
            "MS:1000514",
            "m/z array",
            binary_compression=fallback_compression,
        )
    )
    badl.append(
        _binary_array_elem(
            intensity_array,
            "MS:1000515",
            "intensity array",
            binary_compression=fallback_compression,
        )
    )


def _scan_arrays(scan) -> Tuple[np.ndarray, np.ndarray]:
    if isinstance(scan, tuple) and len(scan) >= 2:
        return np.asarray(scan[0], dtype=np.float64), np.asarray(scan[1], dtype=np.float64)
    return np.asarray(scan["mz_array"], dtype=np.float64), np.asarray(scan["intensity_array"], dtype=np.float64)


def rebuild_mzml(
    metadata_xml: str | bytes,
    ms1_scans: Sequence[Dict],
    ms2_payload,
    *,
    file_type: str,
    auxiliary_binary_records: Sequence[Dict] | None = None,
    binary_compression: str = "preserve_template",
) -> bytes:
    root = _parse_root(metadata_xml)
    return rebuild_mzml_from_root(
        root,
        ms1_scans,
        ms2_payload,
        file_type=file_type,
        auxiliary_binary_records=auxiliary_binary_records,
        binary_compression=binary_compression,
    )


def _populate_rebuilt_mzml_root(
    root,
    ms1_scans: Sequence[Dict],
    ms2_payload,
    *,
    file_type: str,
    auxiliary_binary_records: Sequence[Dict] | None = None,
    binary_compression: str = "preserve_template",
) -> None:

    if auxiliary_binary_records:
        restore_auxiliary_binary_records(root, auxiliary_binary_records)

    ms1_iter = iter(ms1_scans)
    ms2_iter = iter(ms2_payload) if file_type != "DIA" else None
    ms2_window_offsets = defaultdict(int)
    prepared_batch: List[Dict] = []
    prepared_batch_key = None

    def _flush_batch() -> None:
        nonlocal prepared_batch, prepared_batch_key
        if prepared_batch:
            _flush_prepared_spectrum_binary_batch(prepared_batch)
            prepared_batch = []
            prepared_batch_key = None

    def _replace_or_queue(spectrum, mz_array, intensity_array) -> None:
        nonlocal prepared_batch, prepared_batch_key
        if HAVE_NATIVE_WRITER and NATIVE_WRITER_BATCH_SIZE > 1:
            prepared = _prepare_existing_spectrum_binary_pair(
                spectrum,
                mz_array,
                intensity_array,
                binary_compression=binary_compression,
            )
            if prepared is not None:
                key = _native_batch_key(prepared)
                if key is None:
                    _flush_batch()
                    _update_prepared_spectrum_binary_pair(prepared)
                    return
                if prepared_batch and prepared_batch_key != key:
                    _flush_batch()
                prepared_batch_key = key
                prepared_batch.append(prepared)
                if len(prepared_batch) >= NATIVE_WRITER_BATCH_SIZE:
                    _flush_batch()
                return
            _flush_batch()
        else:
            _flush_batch()
        _replace_spectrum_binary_arrays(
            spectrum,
            mz_array,
            intensity_array,
            binary_compression=binary_compression,
        )

    for spectrum in root.iter(SPECTRUM_TAG):
        ms_level = _spectrum_ms_level(spectrum)
        if ms_level == 1:
            scan = next(ms1_iter)
            mz_array, intensity_array = _scan_arrays(scan)
            _replace_or_queue(
                spectrum,
                mz_array,
                intensity_array,
            )
        elif ms_level == 2:
            if file_type == "DIA":
                window_key = _dia_window_key_from_spectrum(spectrum)
                scans = ms2_payload.get(window_key)
                if scans is None:
                    raise ValueError(f"Missing DIA decoded window {window_key}")
                pos = ms2_window_offsets[window_key]
                if pos >= len(scans):
                    raise ValueError(f"DIA decoded scan exhausted for window {window_key}")
                scan = scans[pos]
                ms2_window_offsets[window_key] += 1
                release_done = getattr(ms2_payload, "release_if_done", None)
                if release_done is not None:
                    release_done(window_key, ms2_window_offsets[window_key])
            else:
                scan = next(ms2_iter)
            mz_array, intensity_array = _scan_arrays(scan)
            _replace_or_queue(
                spectrum,
                mz_array,
                intensity_array,
            )
    _flush_batch()


def rebuild_mzml_from_root(
    root,
    ms1_scans: Sequence[Dict],
    ms2_payload,
    *,
    file_type: str,
    auxiliary_binary_records: Sequence[Dict] | None = None,
    binary_compression: str = "preserve_template",
) -> bytes:
    _populate_rebuilt_mzml_root(
        root,
        ms1_scans,
        ms2_payload,
        file_type=file_type,
        auxiliary_binary_records=auxiliary_binary_records,
        binary_compression=binary_compression,
    )
    return _build_fresh_indexed_mzml_bytes(root)


def write_rebuilt_mzml_from_root(
    root,
    ms1_scans: Sequence[Dict],
    ms2_payload,
    output_path: str | Path,
    *,
    file_type: str,
    auxiliary_binary_records: Sequence[Dict] | None = None,
    binary_compression: str = "preserve_template",
) -> Path:
    _populate_rebuilt_mzml_root(
        root,
        ms1_scans,
        ms2_payload,
        file_type=file_type,
        auxiliary_binary_records=auxiliary_binary_records,
        binary_compression=binary_compression,
    )
    return _write_fresh_indexed_mzml_file(root, output_path)
