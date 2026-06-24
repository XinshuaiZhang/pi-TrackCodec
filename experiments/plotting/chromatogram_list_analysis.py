from __future__ import annotations

import argparse
import base64
import csv
import json
import mmap
import os
import zlib
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from lxml import etree


NS_URI = "http://psi.hupo.org/ms/mzml"
NS = {"mzml": NS_URI}

TIME_ARRAY_ACCESSIONS = {"MS:1000595"}
INTENSITY_ARRAY_ACCESSIONS = {"MS:1000515"}
FLOAT32_ACCESSIONS = {"MS:1000521"}
FLOAT64_ACCESSIONS = {"MS:1000523"}
ZLIB_COMPRESSION_ACCESSIONS = {"MS:1000574"}
NO_COMPRESSION_ACCESSIONS = {"MS:1000576"}


def _cv_accessions(array_elem) -> set[str]:
    return {
        cv.get("accession")
        for cv in array_elem.xpath("./mzml:cvParam", namespaces=NS)
        if cv.get("accession")
    }


def _array_kind(array_elem) -> str:
    accessions = _cv_accessions(array_elem)
    if accessions & TIME_ARRAY_ACCESSIONS:
        return "time"
    if accessions & INTENSITY_ARRAY_ACCESSIONS:
        return "intensity"
    return "other"


def _array_dtype(array_elem) -> str:
    accessions = _cv_accessions(array_elem)
    if accessions & FLOAT32_ACCESSIONS:
        return "float32"
    if accessions & FLOAT64_ACCESSIONS:
        return "float64"
    return "unknown"


def _compression_kind(array_elem) -> str:
    accessions = _cv_accessions(array_elem)
    if accessions & ZLIB_COMPRESSION_ACCESSIONS:
        return "zlib"
    if accessions & NO_COMPRESSION_ACCESSIONS:
        return "none"
    return "unknown"


def _decode_numeric_array(binary_text: str, compression: str, dtype_name: str) -> np.ndarray:
    if not binary_text:
        return np.array([], dtype=np.float64)
    raw = base64.b64decode(binary_text)
    if compression == "zlib":
        raw = zlib.decompress(raw)
    if dtype_name == "float32":
        arr = np.frombuffer(raw, dtype="<f4")
    elif dtype_name == "float64":
        arr = np.frombuffer(raw, dtype="<f8")
    else:
        return np.array([], dtype=np.float64)
    return arr.astype(np.float64, copy=False)


def _chromatogram_list_byte_span(mzml_path: Path) -> dict:
    with mzml_path.open("rb") as handle:
        mm = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        start = mm.find(b"<chromatogramList")
        end = mm.find(b"</chromatogramList>", start if start >= 0 else 0)
        if start < 0 or end < 0:
            mm.close()
            return {"start": -1, "end": -1, "bytes": 0}
        end += len(b"</chromatogramList>")
        out = {"start": int(start), "end": int(end), "bytes": int(end - start)}
        mm.close()
        return out


def analyze_chromatograms(mzml_path: Path) -> tuple[list[dict], list[dict], dict]:
    chromatogram_rows: list[dict] = []
    array_rows: list[dict] = []

    tag = f"{{{NS_URI}}}chromatogram"
    context = etree.iterparse(str(mzml_path), events=("end",), tag=tag, huge_tree=True, recover=True)

    for _, chrom in context:
        chrom_id = chrom.get("id", "")
        chrom_index = int(chrom.get("index", len(chromatogram_rows)))
        default_array_length = int(chrom.get("defaultArrayLength", "0") or "0")
        chrom_type = "; ".join(
            cv.get("name", "")
            for cv in chrom.xpath("./mzml:cvParam", namespaces=NS)
            if cv.get("name")
        )
        serialized_xml_bytes = len(etree.tostring(chrom, encoding="UTF-8"))

        point_count = default_array_length
        total_base64_chars = 0
        total_raw_blob_bytes = 0
        time_min = 0.0
        time_max = 0.0
        intensity_max = 0.0
        intensity_sum = 0.0

        arrays = chrom.xpath("./mzml:binaryDataArrayList/mzml:binaryDataArray", namespaces=NS)
        for array_idx, array_elem in enumerate(arrays):
            binary = array_elem.find("./mzml:binary", namespaces=NS)
            binary_text = binary.text or "" if binary is not None else ""
            kind = _array_kind(array_elem)
            dtype_name = _array_dtype(array_elem)
            compression = _compression_kind(array_elem)
            raw_blob = base64.b64decode(binary_text) if binary_text else b""
            values = _decode_numeric_array(binary_text, compression, dtype_name)

            total_base64_chars += len(binary_text)
            total_raw_blob_bytes += len(raw_blob)
            if kind == "time" and values.size:
                point_count = int(values.size)
                time_min = float(values.min(initial=0.0))
                time_max = float(values.max(initial=0.0))
            elif kind == "intensity" and values.size:
                intensity_max = float(values.max(initial=0.0))
                intensity_sum = float(values.sum(dtype=np.float64))

            array_rows.append(
                {
                    "chromatogram_id": chrom_id,
                    "chromatogram_index": chrom_index,
                    "array_index": array_idx,
                    "array_kind": kind,
                    "dtype": dtype_name,
                    "compression": compression,
                    "point_count": int(values.size or point_count),
                    "encoded_length": int(array_elem.get("encodedLength", "0") or "0"),
                    "base64_chars": len(binary_text),
                    "raw_blob_bytes": len(raw_blob),
                }
            )

        chromatogram_rows.append(
            {
                "chromatogram_id": chrom_id,
                "chromatogram_index": chrom_index,
                "chromatogram_type": chrom_type,
                "point_count": point_count,
                "time_min": time_min,
                "time_max": time_max,
                "intensity_max": intensity_max,
                "intensity_sum": intensity_sum,
                "base64_chars": total_base64_chars,
                "raw_blob_bytes": total_raw_blob_bytes,
                "serialized_xml_bytes": serialized_xml_bytes,
            }
        )

        chrom.clear()
        while chrom.getprevious() is not None:
            del chrom.getparent()[0]

    whole_file_bytes = int(mzml_path.stat().st_size)
    chrom_span = _chromatogram_list_byte_span(mzml_path)
    summary = {
        "file": str(mzml_path),
        "whole_file_bytes": whole_file_bytes,
        "chromatogram_count": len(chromatogram_rows),
        "chromatogram_list_bytes": int(chrom_span["bytes"]),
        "chromatogram_list_pct_of_whole_file": chrom_span["bytes"] / whole_file_bytes if whole_file_bytes else 0.0,
        "sum_point_count": int(sum(row["point_count"] for row in chromatogram_rows)),
        "sum_base64_chars": int(sum(row["base64_chars"] for row in chromatogram_rows)),
        "sum_raw_blob_bytes": int(sum(row["raw_blob_bytes"] for row in chromatogram_rows)),
        "sum_serialized_xml_bytes": int(sum(row["serialized_xml_bytes"] for row in chromatogram_rows)),
    }
    return chromatogram_rows, array_rows, summary


def _write_csv(rows: list[dict], output_path: Path) -> None:
    if not rows:
        return
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_chromatogram_summary(chrom_rows: list[dict], summary: dict, output_path: Path) -> None:
    if not chrom_rows:
        return
    labels = [f"C{i + 1}" for i in range(len(chrom_rows))]
    points = np.asarray([row["point_count"] for row in chrom_rows], dtype=np.float64)
    raw_mb = np.asarray([row["raw_blob_bytes"] / (1024 * 1024) for row in chrom_rows], dtype=np.float64)

    fig, axes = plt.subplots(2, 1, figsize=(12, 9), constrained_layout=True)

    axes[0].bar(labels, points, color="#4C78A8")
    axes[0].set_ylabel("Point Count")
    axes[0].set_title("Chromatogram Point Counts")
    axes[0].tick_params(axis="x", labelsize=11)
    axes[0].tick_params(axis="y", labelsize=11)
    for idx, value in enumerate(points):
        axes[0].text(idx, value, f"{int(value)}", ha="center", va="bottom", fontsize=11)

    axes[1].bar(labels, raw_mb, color="#F58518")
    axes[1].set_ylabel("Raw Blob Size (MB)")
    axes[1].set_title(
        f"Chromatogram Binary Size, total list = {summary['chromatogram_list_bytes'] / (1024 * 1024):.2f} MB "
        f"({summary['chromatogram_list_pct_of_whole_file'] * 100:.3f}% of whole-file)"
    )
    axes[1].tick_params(axis="x", labelsize=11)
    axes[1].tick_params(axis="y", labelsize=11)
    for idx, value in enumerate(raw_mb):
        axes[1].text(idx, value, f"{value:.2f}", ha="center", va="bottom", fontsize=11)

    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze mzML chromatogramList contents and size contribution.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    chrom_rows, array_rows, summary = analyze_chromatograms(input_path)

    _write_csv(chrom_rows, output_dir / "chromatogram_summary.csv")
    _write_csv(array_rows, output_dir / "chromatogram_arrays.csv")
    (output_dir / "chromatogram_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    plot_chromatogram_summary(chrom_rows, summary, output_dir / "chromatogram_summary.png")

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
