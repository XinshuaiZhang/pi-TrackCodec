"""TrackCodec roundtrip validation helpers.

The validation pass treats numeric reconstruction fidelity as the pass/fail
criterion. Non-binary XML metadata differences are reported separately because
some derived mzML cvParams are intentionally regenerated from reconstructed
arrays during export.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
from itertools import zip_longest
from pathlib import Path

import numpy as np
from lxml import etree
from pyteomics import mzml


NS = {"mzml": "http://psi.hupo.org/ms/mzml"}
MZML_NS = NS["mzml"]
INTENSITY_ARRAY_ACCESSION = "MS:1000515"
FLOAT32_ACCESSION = "MS:1000521"
FLOAT64_ACCESSION = "MS:1000523"
INTENSITY_DTYPE_LABELS = {
    FLOAT32_ACCESSION: "float32",
    FLOAT64_ACCESSION: "float64",
}


def _mzml_tag(local_name: str) -> str:
    return f"{{{MZML_NS}}}{local_name}"


def _local_name(tag) -> str:
    if not isinstance(tag, str):
        return ""
    if tag.startswith("{"):
        return tag.rsplit("}", 1)[-1]
    return tag


def _iter_mzml_elements(root, local_name: str):
    # Use explicit tree traversal instead of XPath. lxml can raise
    # XPathEvalError on very large mzML trees for local-name() predicates.
    return (elem for elem in root.iter() if _local_name(elem.tag) == local_name)


def _iter_mzml_elements_in(root, local_names: set[str]):
    return (elem for elem in root.iter() if _local_name(elem.tag) in local_names)


def _find_direct_mzml_child(elem, local_name: str):
    for child in elem:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _iter_direct_mzml_children(elem, local_name: str):
    return (child for child in elem if _local_name(child.tag) == local_name)


def _has_ancestor_with_tag(elem, local_name: str) -> bool:
    target = _mzml_tag(local_name)
    parent = elem.getparent()
    while parent is not None:
        if parent.tag == target:
            return True
        parent = parent.getparent()
    return False


def _iter_spectra(path: Path):
    with mzml.MzML(
        str(path),
        read_schema=False,
        iterative=True,
        use_index=False,
        huge_tree=True,
        decode_binary=True,
    ) as reader:
        for spec in reader:
            level = int(spec.get("ms level", 0))
            yield {
                "id": spec.get("id"),
                "ms_level": level,
                "rt": float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0)),
                "mz_array": np.asarray(spec.get("m/z array", []), dtype=np.float64),
                "intensity_array": np.asarray(spec.get("intensity array", []), dtype=np.float64),
            }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_xml_bytes_from_root(root, *, remove_binary_arrays: bool) -> bytes:
    root = copy.deepcopy(root)
    # indexedmzML offsets/checksum are byte-position metadata. They must be
    # regenerated whenever spectrum binary payloads change, so they are not a
    # stable signal for non-binary mzML metadata equality.
    for derived_elem in list(_iter_mzml_elements_in(root, {"indexList", "indexListOffset", "fileChecksum"})):
        parent = derived_elem.getparent()
        if parent is not None:
            parent.remove(derived_elem)
    if remove_binary_arrays:
        for badl in list(_iter_mzml_elements(root, "binaryDataArrayList")):
            parent = badl.getparent()
            if parent is not None:
                parent.remove(badl)
    else:
        for binary in _iter_mzml_elements(root, "binary"):
            binary.text = ""
        for array_elem in _iter_mzml_elements(root, "binaryDataArray"):
            array_elem.set("encodedLength", "0")
    return etree.tostring(root, method="c14n", with_comments=False)


def _attr_name(name: str) -> str:
    if name.startswith("{"):
        qname = etree.QName(name)
        return f"{{{qname.namespace}}}{qname.localname}"
    return str(name)


def _hash_start_token(hasher, elem, *, binary_stripped: bool) -> None:
    local = _local_name(elem.tag)
    attrs = []
    for key, value in elem.attrib.items():
        attr_key = _attr_name(str(key))
        attr_value = "0" if binary_stripped and local == "binaryDataArray" and attr_key == "encodedLength" else str(value)
        attrs.append((attr_key, attr_value))
    hasher.update(b"S|")
    hasher.update(_attr_name(str(elem.tag)).encode("utf-8"))
    for prefix, uri in sorted((str(prefix or ""), str(uri)) for prefix, uri in (elem.nsmap or {}).items()):
        hasher.update(b"|xmlns:")
        hasher.update(prefix.encode("utf-8"))
        hasher.update(b"=")
        hasher.update(uri.encode("utf-8"))
    for key, value in sorted(attrs):
        hasher.update(b"|")
        hasher.update(key.encode("utf-8"))
        hasher.update(b"=")
        hasher.update(value.encode("utf-8"))
    hasher.update(b"\n")


def _hash_end_token(hasher, elem, *, binary_stripped: bool) -> None:
    local = _local_name(elem.tag)
    text = elem.text or ""
    if binary_stripped and local == "binary":
        text = ""
    text = text.strip()
    if text:
        hasher.update(b"T|")
        hasher.update(text.encode("utf-8"))
        hasher.update(b"\n")
    hasher.update(b"E|")
    hasher.update(_attr_name(str(elem.tag)).encode("utf-8"))
    hasher.update(b"\n")


def _intensity_dtype_summary_from_root(root) -> dict:
    total = 0
    dtype_counts: dict[str, int] = {}
    dtype_accession_counts: dict[str, int] = {}

    for array_elem in _iter_mzml_elements(root, "binaryDataArray"):
        if not _has_ancestor_with_tag(array_elem, "spectrum"):
            continue
        cv_accessions = {
            cv.get("accession")
            for cv in _iter_direct_mzml_children(array_elem, "cvParam")
            if cv.get("accession")
        }
        if INTENSITY_ARRAY_ACCESSION not in cv_accessions:
            continue
        total += 1
        dtype_accession = "unknown"
        dtype_label = "unknown"
        if FLOAT32_ACCESSION in cv_accessions:
            dtype_accession = FLOAT32_ACCESSION
            dtype_label = "float32"
        elif FLOAT64_ACCESSION in cv_accessions:
            dtype_accession = FLOAT64_ACCESSION
            dtype_label = "float64"
        dtype_counts[dtype_label] = dtype_counts.get(dtype_label, 0) + 1
        dtype_accession_counts[dtype_accession] = dtype_accession_counts.get(dtype_accession, 0) + 1

    unique_dtypes = sorted(dtype_counts.keys())
    if total == 0:
        effective_dtype = "none"
    elif dtype_counts.get("float32", 0) > 0:
        effective_dtype = "float32"
    elif dtype_counts.get("float64", 0) > 0 and len(unique_dtypes) == 1:
        effective_dtype = "float64"
    elif dtype_counts.get("float64", 0) > 0:
        effective_dtype = "mixed_non_float32"
    else:
        effective_dtype = "unknown"

    return {
        "total_spectrum_intensity_arrays": int(total),
        "dtype_counts": {str(key): int(value) for key, value in sorted(dtype_counts.items())},
        "dtype_accession_counts": {str(key): int(value) for key, value in sorted(dtype_accession_counts.items())},
        "unique_dtypes": unique_dtypes,
        "effective_dtype": effective_dtype,
    }


def _xml_structure_summary(path: Path) -> dict:
    derived_skip = {"indexList", "indexListOffset", "fileChecksum"}
    binary_stripped_hash = hashlib.sha256()
    without_binary_arrays_hash = hashlib.sha256()
    aux_binary_raw_hash = hashlib.sha256()
    chromatogram_count = 0
    aux_binary_array_count = 0
    aux_binary_nonempty_count = 0
    spectrum_depth = 0
    binary_array_stack: list[dict] = []
    dtype_counts: dict[str, int] = {}
    dtype_accession_counts: dict[str, int] = {}
    total_intensity_arrays = 0
    strip_skip_depth = 0
    no_array_skip_depth = 0

    context = etree.iterparse(
        str(path),
        events=("start", "end"),
        recover=True,
        huge_tree=True,
    )
    for event, elem in context:
        local = _local_name(elem.tag)
        if event == "start":
            if local == "spectrum":
                spectrum_depth += 1
            elif local == "chromatogram":
                chromatogram_count += 1
            elif local == "binaryDataArray":
                binary_array_stack.append(
                    {
                        "under_spectrum": spectrum_depth > 0,
                        "cv_accessions": set(),
                        "encoded_length": elem.get("encodedLength", "0"),
                        "binary_text": "",
                    }
                )
            elif local == "cvParam" and binary_array_stack:
                accession = elem.get("accession")
                if accession:
                    binary_array_stack[-1]["cv_accessions"].add(accession)

            if strip_skip_depth > 0 or local in derived_skip:
                strip_skip_depth += 1
            else:
                _hash_start_token(binary_stripped_hash, elem, binary_stripped=True)

            if no_array_skip_depth > 0 or local in derived_skip or local == "binaryDataArrayList":
                no_array_skip_depth += 1
            else:
                _hash_start_token(without_binary_arrays_hash, elem, binary_stripped=False)
            continue

        if local == "binary" and binary_array_stack and not binary_array_stack[-1]["under_spectrum"]:
            binary_array_stack[-1]["binary_text"] = elem.text or ""

        if strip_skip_depth > 0:
            strip_skip_depth -= 1
        else:
            _hash_end_token(binary_stripped_hash, elem, binary_stripped=True)

        if no_array_skip_depth > 0:
            no_array_skip_depth -= 1
        else:
            _hash_end_token(without_binary_arrays_hash, elem, binary_stripped=False)

        if local == "binaryDataArray":
            info = binary_array_stack.pop() if binary_array_stack else {
                "under_spectrum": False,
                "cv_accessions": set(),
                "encoded_length": "0",
                "binary_text": "",
            }
            accessions = set(info["cv_accessions"])
            if info["under_spectrum"]:
                if INTENSITY_ARRAY_ACCESSION in accessions:
                    total_intensity_arrays += 1
                    dtype_accession = "unknown"
                    dtype_label = "unknown"
                    if FLOAT32_ACCESSION in accessions:
                        dtype_accession = FLOAT32_ACCESSION
                        dtype_label = "float32"
                    elif FLOAT64_ACCESSION in accessions:
                        dtype_accession = FLOAT64_ACCESSION
                        dtype_label = "float64"
                    dtype_counts[dtype_label] = dtype_counts.get(dtype_label, 0) + 1
                    dtype_accession_counts[dtype_accession] = dtype_accession_counts.get(dtype_accession, 0) + 1
            else:
                aux_binary_array_count += 1
                binary_text = str(info.get("binary_text", ""))
                if binary_text:
                    aux_binary_nonempty_count += 1
                aux_binary_raw_hash.update(f"{info.get('encoded_length', '0')}|".encode("utf-8"))
                aux_binary_raw_hash.update(binary_text.encode("utf-8"))
                aux_binary_raw_hash.update(b"\n")
        elif local == "spectrum":
            spectrum_depth -= 1

        elem.clear()
        parent = elem.getparent()
        while parent is not None and elem.getprevious() is not None:
            del parent[0]

    unique_dtypes = sorted(dtype_counts.keys())
    if total_intensity_arrays == 0:
        effective_dtype = "none"
    elif dtype_counts.get("float32", 0) > 0:
        effective_dtype = "float32"
    elif dtype_counts.get("float64", 0) > 0 and len(unique_dtypes) == 1:
        effective_dtype = "float64"
    elif dtype_counts.get("float64", 0) > 0:
        effective_dtype = "mixed_non_float32"
    else:
        effective_dtype = "unknown"

    return {
        "chromatogram_count": int(chromatogram_count),
        "aux_binary_array_count": int(aux_binary_array_count),
        "aux_binary_nonempty_count": int(aux_binary_nonempty_count),
        "aux_binary_raw_sha256": aux_binary_raw_hash.hexdigest(),
        "binary_stripped_xml_sha256": binary_stripped_hash.hexdigest(),
        "xml_without_binary_arrays_sha256": without_binary_arrays_hash.hexdigest(),
        "intensity_dtype_summary": {
            "total_spectrum_intensity_arrays": int(total_intensity_arrays),
            "dtype_counts": {str(key): int(value) for key, value in sorted(dtype_counts.items())},
            "dtype_accession_counts": {str(key): int(value) for key, value in sorted(dtype_accession_counts.items())},
            "unique_dtypes": unique_dtypes,
            "effective_dtype": effective_dtype,
        },
    }


def _aux_count_summary(summary: dict) -> dict:
    return {
        "chromatogram_count": int(summary.get("chromatogram_count", 0)),
        "aux_binary_array_count": int(summary.get("aux_binary_array_count", 0)),
        "aux_binary_nonempty_count": int(summary.get("aux_binary_nonempty_count", 0)),
    }


def _safe_rel_error(ref: float, val: float) -> float:
    denom = abs(ref)
    if denom == 0.0:
        return 0.0 if val == 0.0 else float("inf")
    return abs(val - ref) / denom


def _vector_stats(values: list[float]) -> dict:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "max": float(arr.max(initial=0.0)),
    }


def _has_float32_intensity_template(summary: dict) -> bool:
    intensity_summary = summary.get("intensity_dtype_summary", {})
    dtype_counts = intensity_summary.get("dtype_counts", {})
    return int(dtype_counts.get("float32", 0)) > 0


def _intensity_dtype_match(original_summary: dict, reconstructed_summary: dict) -> bool:
    orig = original_summary.get("intensity_dtype_summary", {})
    recon = reconstructed_summary.get("intensity_dtype_summary", {})
    return (
        orig.get("total_spectrum_intensity_arrays") == recon.get("total_spectrum_intensity_arrays")
        and orig.get("dtype_counts") == recon.get("dtype_counts")
        and orig.get("dtype_accession_counts") == recon.get("dtype_accession_counts")
        and orig.get("effective_dtype") == recon.get("effective_dtype")
    )


def _resolve_intensity_error_ceiling(
    original_summary: dict,
    reconstructed_summary: dict,
    *,
    float64_ceiling: float,
    float32_ceiling: float,
) -> dict:
    original_dtype = original_summary["intensity_dtype_summary"]
    reconstructed_dtype = reconstructed_summary["intensity_dtype_summary"]
    dtype_match = _intensity_dtype_match(original_summary, reconstructed_summary)
    original_has_float32 = _has_float32_intensity_template(original_summary)
    reconstructed_has_float32 = _has_float32_intensity_template(reconstructed_summary)

    if original_has_float32:
        return {
            "intensity_error_ceiling_policy": "template_preserved_dynamic_by_intensity_dtype",
            "effective_intensity_error_ceiling": float(float32_ceiling),
            "effective_intensity_error_ceiling_basis": "original_intensity_template",
            "effective_intensity_error_ceiling_dtype": "float32",
            "effective_intensity_error_ceiling_reason": "original mzML intensity template contains float32 arrays; allow template-preserved float32 rewrite rounding.",
            "original_intensity_dtype_summary": original_dtype,
            "reconstructed_intensity_dtype_summary": reconstructed_dtype,
            "intensity_dtype_match": bool(dtype_match),
        }
    if reconstructed_has_float32:
        return {
            "intensity_error_ceiling_policy": "template_preserved_dynamic_by_intensity_dtype",
            "effective_intensity_error_ceiling": float(float32_ceiling),
            "effective_intensity_error_ceiling_basis": "reconstructed_intensity_template",
            "effective_intensity_error_ceiling_dtype": "float32",
            "effective_intensity_error_ceiling_reason": "reconstructed mzML intensity template contains float32 arrays; allow template-preserved float32 rewrite rounding.",
            "original_intensity_dtype_summary": original_dtype,
            "reconstructed_intensity_dtype_summary": reconstructed_dtype,
            "intensity_dtype_match": bool(dtype_match),
        }
    if original_dtype.get("effective_dtype") == "float64":
        return {
            "intensity_error_ceiling_policy": "template_preserved_dynamic_by_intensity_dtype",
            "effective_intensity_error_ceiling": float(float64_ceiling),
            "effective_intensity_error_ceiling_basis": "original_intensity_template",
            "effective_intensity_error_ceiling_dtype": "float64",
            "effective_intensity_error_ceiling_reason": "original mzML intensity template is float64-only; keep the stricter float64 ceiling.",
            "original_intensity_dtype_summary": original_dtype,
            "reconstructed_intensity_dtype_summary": reconstructed_dtype,
            "intensity_dtype_match": bool(dtype_match),
        }
    if reconstructed_dtype.get("effective_dtype") == "float64":
        return {
            "intensity_error_ceiling_policy": "template_preserved_dynamic_by_intensity_dtype",
            "effective_intensity_error_ceiling": float(float64_ceiling),
            "effective_intensity_error_ceiling_basis": "reconstructed_intensity_template",
            "effective_intensity_error_ceiling_dtype": "float64",
            "effective_intensity_error_ceiling_reason": "reconstructed mzML intensity template is float64-only; keep the stricter float64 ceiling.",
            "original_intensity_dtype_summary": original_dtype,
            "reconstructed_intensity_dtype_summary": reconstructed_dtype,
            "intensity_dtype_match": bool(dtype_match),
        }
    return {
        "intensity_error_ceiling_policy": "template_preserved_dynamic_by_intensity_dtype",
        "effective_intensity_error_ceiling": float(float64_ceiling),
        "effective_intensity_error_ceiling_basis": "fallback_no_float32_detected",
        "effective_intensity_error_ceiling_dtype": "unknown",
        "effective_intensity_error_ceiling_reason": "no float32 intensity template was detected; fall back to the stricter float64 ceiling.",
        "original_intensity_dtype_summary": original_dtype,
        "reconstructed_intensity_dtype_summary": reconstructed_dtype,
        "intensity_dtype_match": bool(dtype_match),
    }


def compare_files(
    original: Path,
    reconstructed: Path,
    per_spectrum_csv: Path | None = None,
    *,
    intensity_error_ceiling_float64: float = 0.05,
    intensity_error_ceiling_float32: float = 0.1,
):
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

    spectrum_mean_abs_mz = []
    spectrum_p95_abs_mz = []
    spectrum_mean_abs_int = []
    spectrum_p95_abs_int = []
    spectrum_mean_rel_int = []
    spectrum_p95_rel_int = []
    spectrum_tic_rel = []
    spectrum_bpi_rel = []

    original_aux = _xml_structure_summary(original)
    reconstructed_aux = _xml_structure_summary(reconstructed)
    intensity_ceiling_info = _resolve_intensity_error_ceiling(
        original_aux,
        reconstructed_aux,
        float64_ceiling=float(intensity_error_ceiling_float64),
        float32_ceiling=float(intensity_error_ceiling_float32),
    )

    writer = None
    handle = None
    if per_spectrum_csv is not None:
        per_spectrum_csv.parent.mkdir(parents=True, exist_ok=True)
        handle = per_spectrum_csv.open("w", newline="")
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "spectrum_index",
                "original_id",
                "reconstructed_id",
                "id_match",
                "ms_level_original",
                "ms_level_reconstructed",
                "rt_original",
                "rt_reconstructed",
                "rt_abs_diff",
                "n_points_original",
                "n_points_reconstructed",
                "n_points_compared",
                "nonzero_original",
                "nonzero_reconstructed",
                "max_abs_mz_error",
                "mean_abs_mz_error",
                "p95_abs_mz_error",
                "max_abs_intensity_error",
                "mean_abs_intensity_error",
                "p95_abs_intensity_error",
                "mean_rel_intensity_error",
                "p95_rel_intensity_error",
                "tic_original",
                "tic_reconstructed",
                "tic_rel_error",
                "bpi_original",
                "bpi_reconstructed",
                "bpi_rel_error",
                "effective_intensity_error_ceiling",
                "effective_intensity_error_ceiling_basis",
                "effective_intensity_error_ceiling_dtype",
                "intensity_dtype_match",
                "original_intensity_effective_dtype",
                "reconstructed_intensity_effective_dtype",
            ],
        )
        writer.writeheader()

    for spectrum_count, pair in enumerate(zip_longest(_iter_spectra(original), _iter_spectra(reconstructed)), start=1):
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
        rt_abs_diff = abs(float(orig["rt"]) - float(recon["rt"]))
        if rt_abs_diff > 0.0:
            rt_mismatch_count += 1

        n_orig = len(orig["mz_array"])
        n_recon = len(recon["mz_array"])
        if n_orig != n_recon:
            array_length_mismatch_count += 1
        n_cmp = min(n_orig, n_recon)

        mz_diff = np.abs(orig["mz_array"][:n_cmp] - recon["mz_array"][:n_cmp]) if n_cmp else np.array([], dtype=np.float64)
        int_diff = np.abs(orig["intensity_array"][:n_cmp] - recon["intensity_array"][:n_cmp]) if n_cmp else np.array([], dtype=np.float64)
        max_mz_local = float(mz_diff.max(initial=0.0))
        max_int_local = float(int_diff.max(initial=0.0))
        mean_mz_local = float(mz_diff.mean()) if n_cmp else 0.0
        mean_int_local = float(int_diff.mean()) if n_cmp else 0.0
        p95_mz_local = float(np.percentile(mz_diff, 95)) if n_cmp else 0.0
        p95_int_local = float(np.percentile(int_diff, 95)) if n_cmp else 0.0

        orig_nonzero = int(np.count_nonzero(orig["intensity_array"]))
        recon_nonzero = int(np.count_nonzero(recon["intensity_array"]))
        if orig_nonzero != recon_nonzero:
            nonzero_count_mismatch_count += 1

        rel_mask = np.abs(orig["intensity_array"][:n_cmp]) > 0.0 if n_cmp else np.array([], dtype=bool)
        rel_int = int_diff[rel_mask] / np.abs(orig["intensity_array"][:n_cmp][rel_mask]) if np.any(rel_mask) else np.array([], dtype=np.float64)
        mean_rel_int_local = float(rel_int.mean()) if rel_int.size else 0.0
        p95_rel_int_local = float(np.percentile(rel_int, 95)) if rel_int.size else 0.0

        tic_orig = float(np.sum(orig["intensity_array"], dtype=np.float64))
        tic_recon = float(np.sum(recon["intensity_array"], dtype=np.float64))
        bpi_orig = float(np.max(orig["intensity_array"], initial=0.0))
        bpi_recon = float(np.max(recon["intensity_array"], initial=0.0))
        tic_rel_err = _safe_rel_error(tic_orig, tic_recon)
        bpi_rel_err = _safe_rel_error(bpi_orig, bpi_recon)

        max_abs_mz = max(max_abs_mz, max_mz_local)
        max_abs_int = max(max_abs_int, max_int_local)
        compared_point_count += n_cmp
        total_nonzero_orig_points += orig_nonzero
        total_nonzero_recon_points += recon_nonzero
        if max_mz_local > 0.0:
            spectra_with_any_mz_error += 1
        if max_int_local > 0.0:
            spectra_with_any_intensity_error += 1

        spectrum_mean_abs_mz.append(mean_mz_local)
        spectrum_p95_abs_mz.append(p95_mz_local)
        spectrum_mean_abs_int.append(mean_int_local)
        spectrum_p95_abs_int.append(p95_int_local)
        spectrum_mean_rel_int.append(mean_rel_int_local)
        spectrum_p95_rel_int.append(p95_rel_int_local)
        spectrum_tic_rel.append(tic_rel_err)
        spectrum_bpi_rel.append(bpi_rel_err)

        if writer is not None:
            writer.writerow(
                {
                    "spectrum_index": spectrum_count,
                    "original_id": orig["id"],
                    "reconstructed_id": recon["id"],
                    "id_match": orig["id"] == recon["id"],
                    "ms_level_original": orig["ms_level"],
                    "ms_level_reconstructed": recon["ms_level"],
                    "rt_original": orig["rt"],
                    "rt_reconstructed": recon["rt"],
                    "rt_abs_diff": rt_abs_diff,
                    "n_points_original": n_orig,
                    "n_points_reconstructed": n_recon,
                    "n_points_compared": n_cmp,
                    "nonzero_original": orig_nonzero,
                    "nonzero_reconstructed": recon_nonzero,
                    "max_abs_mz_error": max_mz_local,
                    "mean_abs_mz_error": mean_mz_local,
                    "p95_abs_mz_error": p95_mz_local,
                    "max_abs_intensity_error": max_int_local,
                    "mean_abs_intensity_error": mean_int_local,
                    "p95_abs_intensity_error": p95_int_local,
                    "mean_rel_intensity_error": mean_rel_int_local,
                    "p95_rel_intensity_error": p95_rel_int_local,
                    "tic_original": tic_orig,
                    "tic_reconstructed": tic_recon,
                    "tic_rel_error": tic_rel_err,
                    "bpi_original": bpi_orig,
                    "bpi_reconstructed": bpi_recon,
                    "bpi_rel_error": bpi_rel_err,
                    "effective_intensity_error_ceiling": intensity_ceiling_info["effective_intensity_error_ceiling"],
                    "effective_intensity_error_ceiling_basis": intensity_ceiling_info["effective_intensity_error_ceiling_basis"],
                    "effective_intensity_error_ceiling_dtype": intensity_ceiling_info["effective_intensity_error_ceiling_dtype"],
                    "intensity_dtype_match": intensity_ceiling_info["intensity_dtype_match"],
                    "original_intensity_effective_dtype": intensity_ceiling_info["original_intensity_dtype_summary"]["effective_dtype"],
                    "reconstructed_intensity_effective_dtype": intensity_ceiling_info["reconstructed_intensity_dtype_summary"]["effective_dtype"],
                }
            )

        if orig["ms_level"] == 1:
            ms1_count += 1
        elif orig["ms_level"] == 2:
            ms2_count += 1

    if handle is not None:
        handle.close()

    binary_stripped_equal = (
        original_aux["binary_stripped_xml_sha256"] == reconstructed_aux["binary_stripped_xml_sha256"]
    )
    xml_without_binary_arrays_equal = (
        original_aux["xml_without_binary_arrays_sha256"] == reconstructed_aux["xml_without_binary_arrays_sha256"]
    )
    return {
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
        "spectrum_mean_abs_mz_error": _vector_stats(spectrum_mean_abs_mz),
        "spectrum_p95_abs_mz_error": _vector_stats(spectrum_p95_abs_mz),
        "spectrum_mean_abs_intensity_error": _vector_stats(spectrum_mean_abs_int),
        "spectrum_p95_abs_intensity_error": _vector_stats(spectrum_p95_abs_int),
        "spectrum_mean_rel_intensity_error": _vector_stats(spectrum_mean_rel_int),
        "spectrum_p95_rel_intensity_error": _vector_stats(spectrum_p95_rel_int),
        "spectrum_tic_rel_error": _vector_stats(spectrum_tic_rel),
        "spectrum_bpi_rel_error": _vector_stats(spectrum_bpi_rel),
        "original_aux": original_aux,
        "reconstructed_aux": reconstructed_aux,
        "aux_counts_match": _aux_count_summary(original_aux) == _aux_count_summary(reconstructed_aux),
        "aux_binary_raw_equal": original_aux["aux_binary_raw_sha256"] == reconstructed_aux["aux_binary_raw_sha256"],
        "binary_stripped_xml_equal": binary_stripped_equal,
        "xml_without_binary_arrays_equal": xml_without_binary_arrays_equal,
        "intensity_error_ceiling_policy": intensity_ceiling_info["intensity_error_ceiling_policy"],
        "effective_intensity_error_ceiling": intensity_ceiling_info["effective_intensity_error_ceiling"],
        "effective_intensity_error_ceiling_basis": intensity_ceiling_info["effective_intensity_error_ceiling_basis"],
        "effective_intensity_error_ceiling_dtype": intensity_ceiling_info["effective_intensity_error_ceiling_dtype"],
        "effective_intensity_error_ceiling_reason": intensity_ceiling_info["effective_intensity_error_ceiling_reason"],
        "original_intensity_dtype_summary": intensity_ceiling_info["original_intensity_dtype_summary"],
        "reconstructed_intensity_dtype_summary": intensity_ceiling_info["reconstructed_intensity_dtype_summary"],
        "intensity_dtype_match": intensity_ceiling_info["intensity_dtype_match"],
    }


def main():
    from TrackCodec.production.mzml.archive_codec import MzMLSectionArchiveCodec

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--reconstructed-mzml", required=True)
    parser.add_argument("--summary-json", required=False)
    parser.add_argument("--per-spectrum-csv", required=False)
    parser.add_argument("--mz-error-ceiling", type=float, default=5e-7)
    parser.add_argument("--intensity-error-ceiling", type=float, default=0.05)
    parser.add_argument("--intensity-error-ceiling-float32", type=float, default=0.1)
    parser.add_argument("--ceiling-eps", type=float, default=1e-10)
    args = parser.parse_args()

    codec = MzMLSectionArchiveCodec()
    Path(args.archive).parent.mkdir(parents=True, exist_ok=True)
    _, archive_meta = codec.encode_file_to_path(args.input, args.archive)
    codec.decode_to_mzml_file(args.archive, args.reconstructed_mzml, binary_compression="preserve_template")

    summary = {
        "archive_meta": archive_meta,
        "roundtrip": compare_files(
            Path(args.input),
            Path(args.reconstructed_mzml),
            per_spectrum_csv=Path(args.per_spectrum_csv) if args.per_spectrum_csv else None,
            intensity_error_ceiling_float64=float(args.intensity_error_ceiling),
            intensity_error_ceiling_float32=float(args.intensity_error_ceiling_float32),
        ),
        "mz_error_ceiling": args.mz_error_ceiling,
        "intensity_error_ceiling": args.intensity_error_ceiling,
        "intensity_error_ceiling_float64": args.intensity_error_ceiling,
        "intensity_error_ceiling_float32": args.intensity_error_ceiling_float32,
        "ceiling_eps": args.ceiling_eps,
    }
    mz_numeric_ok = summary["roundtrip"]["max_abs_mz_error"] <= (args.mz_error_ceiling + args.ceiling_eps)
    intensity_numeric_ok = summary["roundtrip"]["max_abs_intensity_error"] <= (
        float(summary["roundtrip"]["effective_intensity_error_ceiling"]) + args.ceiling_eps
    )
    structure_ok = (
        int(summary["roundtrip"].get("missing_original_spectra", 0)) == 0
        and int(summary["roundtrip"].get("missing_reconstructed_spectra", 0)) == 0
        and int(summary["roundtrip"].get("spectrum_id_mismatch_count", 0)) == 0
        and int(summary["roundtrip"].get("ms_level_mismatch_count", 0)) == 0
        and int(summary["roundtrip"].get("rt_mismatch_count", 0)) == 0
        and int(summary["roundtrip"].get("array_length_mismatch_count", 0)) == 0
    )
    summary["roundtrip"]["mz_numeric_within_ceiling"] = bool(mz_numeric_ok)
    summary["roundtrip"]["intensity_numeric_within_ceiling"] = bool(intensity_numeric_ok)
    summary["roundtrip"]["structure_within_ceiling"] = bool(structure_ok)
    summary["roundtrip"]["mz_within_ceiling"] = bool(structure_ok and mz_numeric_ok)
    summary["roundtrip"]["intensity_within_ceiling"] = bool(structure_ok and intensity_numeric_ok)
    summary["roundtrip"]["roundtrip_pass"] = (
        bool(summary["roundtrip"]["mz_within_ceiling"])
        and bool(summary["roundtrip"]["intensity_within_ceiling"])
    )
    summary["intensity_error_ceiling_policy"] = summary["roundtrip"]["intensity_error_ceiling_policy"]
    summary["effective_intensity_error_ceiling"] = summary["roundtrip"]["effective_intensity_error_ceiling"]
    summary["effective_intensity_error_ceiling_basis"] = summary["roundtrip"]["effective_intensity_error_ceiling_basis"]
    summary["effective_intensity_error_ceiling_dtype"] = summary["roundtrip"]["effective_intensity_error_ceiling_dtype"]
    summary["effective_intensity_error_ceiling_reason"] = summary["roundtrip"]["effective_intensity_error_ceiling_reason"]
    summary["original_intensity_dtype_summary"] = summary["roundtrip"]["original_intensity_dtype_summary"]
    summary["reconstructed_intensity_dtype_summary"] = summary["roundtrip"]["reconstructed_intensity_dtype_summary"]
    summary["intensity_dtype_match"] = summary["roundtrip"]["intensity_dtype_match"]

    if args.summary_json:
        Path(args.summary_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.summary_json).write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
