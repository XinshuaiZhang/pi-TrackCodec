"""Helpers for writing and validating reconstructed MS1-only mzML files."""

from __future__ import annotations

import base64
from pathlib import Path

import numpy as np
from lxml import etree
from pyteomics import mzml


NS = "http://psi.hupo.org/ms/mzml"
NSMAP = {None: NS}


def _cv_param(accession: str, name: str, value: str = "", **attrs):
    elem = etree.Element(f"{{{NS}}}cvParam")
    elem.set("cvRef", "MS" if accession.startswith("MS:") else "UO")
    elem.set("accession", accession)
    elem.set("name", name)
    elem.set("value", value)
    for key, val in attrs.items():
        elem.set(key, str(val))
    return elem


def _binary_array_elem(array: np.ndarray, accession: str, name: str):
    payload = np.asarray(array, dtype="<f8").tobytes()
    encoded = base64.b64encode(payload).decode("ascii")
    elem = etree.Element(
        f"{{{NS}}}binaryDataArray",
        encodedLength=str(len(encoded)),
        arrayLength=str(len(array)),
    )
    elem.append(_cv_param("MS:1000523", "64-bit float"))
    elem.append(_cv_param("MS:1000576", "no compression"))
    elem.append(_cv_param(accession, name))
    binary = etree.SubElement(elem, f"{{{NS}}}binary")
    binary.text = encoded
    return elem


def write_ms1_only_mzml(scans, output_path: Path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with etree.xmlfile(str(output_path), encoding="UTF-8") as xf:
        xf.write_declaration()
        with xf.element(f"{{{NS}}}mzML", nsmap=NSMAP, version="1.1.0"):
            with xf.element(f"{{{NS}}}cvList", count="2"):
                xf.write(
                    etree.Element(
                        f"{{{NS}}}cv",
                        id="MS",
                        fullName="Proteomics Standards Initiative Mass Spectrometry Ontology",
                        version="4.1.0",
                        URI="https://github.com/HUPO-PSI/psi-ms-CV/releases",
                    )
                )
                xf.write(
                    etree.Element(
                        f"{{{NS}}}cv",
                        id="UO",
                        fullName="Unit Ontology",
                        version="12:10:2011",
                        URI="http://ontologies.berkeleybop.org/uo.obo",
                    )
                )

            with xf.element(f"{{{NS}}}fileDescription"):
                xf.write(etree.Element(f"{{{NS}}}fileContent"))

            with xf.element(f"{{{NS}}}softwareList", count="1"):
                xf.write(etree.Element(f"{{{NS}}}software", id="TrackCodec", version="1.0"))

            with xf.element(f"{{{NS}}}instrumentConfigurationList", count="1"):
                xf.write(etree.Element(f"{{{NS}}}instrumentConfiguration", id="IC1"))

            with xf.element(f"{{{NS}}}dataProcessingList", count="1"):
                with xf.element(f"{{{NS}}}dataProcessing", id="DP1"):
                    xf.write(etree.Element(f"{{{NS}}}processingMethod", order="0", softwareRef="TrackCodec"))

            with xf.element(f"{{{NS}}}run", id="run1", defaultInstrumentConfigurationRef="IC1"):
                with xf.element(
                    f"{{{NS}}}spectrumList",
                    count=str(len(scans)),
                    defaultDataProcessingRef="DP1",
                ):
                    for index, scan in enumerate(scans):
                        mz_array = np.asarray(scan["mz_array"], dtype=np.float64)
                        intensity_array = np.asarray(scan["intensity_array"], dtype=np.float64)
                        with xf.element(
                            f"{{{NS}}}spectrum",
                            id=f"scan={int(scan['scan_idx']) + 1}",
                            index=str(index),
                            defaultArrayLength=str(len(mz_array)),
                        ):
                            xf.write(_cv_param("MS:1000511", "ms level", value="1"))
                            xf.write(_cv_param("MS:1000128", "profile spectrum"))
                            with xf.element(f"{{{NS}}}scanList", count="1"):
                                with xf.element(f"{{{NS}}}scan"):
                                    xf.write(
                                        _cv_param(
                                            "MS:1000016",
                                            "scan start time",
                                            value=f"{float(scan['rt']):.10f}",
                                            unitCvRef="UO",
                                            unitAccession="UO:0000031",
                                            unitName="minute",
                                        )
                                    )
                            with xf.element(f"{{{NS}}}binaryDataArrayList", count="2"):
                                xf.write(_binary_array_elem(mz_array, "MS:1000514", "m/z array"))
                                xf.write(_binary_array_elem(intensity_array, "MS:1000515", "intensity array"))


def load_ms1_only_mzml(path: Path):
    scans = []
    with mzml.MzML(str(path)) as reader:
        for ms1_idx, spec in enumerate(reader):
            if spec.get("ms level", 0) != 1:
                continue
            rt = float(spec.get("scanList", {}).get("scan", [{}])[0].get("scan start time", 0.0))
            scans.append(
                {
                    "scan_idx": ms1_idx,
                    "rt": rt,
                    "mz_array": np.asarray(spec.get("m/z array", []), dtype=np.float64),
                    "intensity_array": np.asarray(spec.get("intensity array", []), dtype=np.float64),
                }
            )
    return scans


def compare_scans(original_scans, reconstructed_scans):
    if len(original_scans) != len(reconstructed_scans):
        raise ValueError(f"Scan count mismatch: {len(original_scans)} vs {len(reconstructed_scans)}")
    max_mz_abs = 0.0
    max_int_abs = 0.0
    max_rt_abs = 0.0
    exact_count = 0
    for orig, recon in zip(original_scans, reconstructed_scans):
        mz_diff = np.abs(np.asarray(orig["mz_array"]) - np.asarray(recon["mz_array"]))
        int_diff = np.abs(np.asarray(orig["intensity_array"]) - np.asarray(recon["intensity_array"]))
        max_mz_abs = max(max_mz_abs, float(mz_diff.max(initial=0.0)))
        max_int_abs = max(max_int_abs, float(int_diff.max(initial=0.0)))
        max_rt_abs = max(max_rt_abs, abs(float(orig["rt"]) - float(recon["rt"])))
        if np.array_equal(orig["mz_array"], recon["mz_array"]) and np.array_equal(orig["intensity_array"], recon["intensity_array"]):
            exact_count += 1
    return {
        "scan_count": len(original_scans),
        "max_mz_abs_error": max_mz_abs,
        "max_intensity_abs_error": max_int_abs,
        "max_rt_abs_error": max_rt_abs,
        "exact_scan_fraction": exact_count / len(original_scans) if original_scans else 1.0,
    }
