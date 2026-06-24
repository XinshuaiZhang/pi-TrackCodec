#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from benchmark_release_paths import default_combined_output_dir, find_workspace_root

ROOT = find_workspace_root(Path(__file__))
COMBINED_DIR = default_combined_output_dir(Path(__file__))
COMBINED_TABLE = COMBINED_DIR / "tables" / "combined_per_file_methods.csv"
INVENTORY_MD = ROOT / "7.1_paper" / "dataset_header_inventory_report.md"
DATA_FULL8_RAW = ROOT / "benchmark" / "data_full8_raw"
OUTPUT_DIR = COMBINED_DIR / "tables"
OUTPUT_CSV = OUTPUT_DIR / "benchmark_35_file_metadata.csv"
OUTPUT_MD = OUTPUT_DIR / "benchmark_35_file_metadata.md"
OUTPUT_JSON = OUTPUT_DIR / "benchmark_35_file_metadata_summary.json"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def split_md_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_size_to_bytes(text: str) -> int:
    text = str(text or "").strip()
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?i?B?|[KMGT])?", text, re.IGNORECASE)
    if not match:
        return 0
    value = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    multipliers = {
        "": 1,
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
        "tib": 1024**4,
    }
    return int(round(value * multipliers.get(unit, 1)))


def has_positive_number(text: str) -> bool:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(text or ""))
    return bool(match and float(match.group(1)) > 0)


def gib(bytes_value: int) -> str:
    if not bytes_value:
        return ""
    return f"{bytes_value / (1024**3):.3f}"


def original_file_type(source_format: str, source_file: str, raw_source_note: str) -> str:
    text = " ".join([source_format or "", source_file or "", raw_source_note or ""]).lower()
    if "wiff" in text:
        return ".wiff + .scan"
    if "agilent" in text or ".d directory" in text or "masshunter" in text:
        return ".d"
    if "thermo" in text or ".raw" in text:
        return ".raw"
    if "bruker" in text or "tdf" in text:
        return ".d"
    return ""


def acquisition_from_name(file_name: str) -> str:
    upper = file_name.upper()
    if "SWATH" in upper:
        return "SWATH"
    if "AIF" in upper:
        return "AIF"
    if "ETD" in upper:
        return "DDA"
    if "DIA" in upper:
        return "DIA"
    if "DDA" in upper:
        return "DDA"
    return ""


def activation_from_name(file_name: str, vendor: str, acquisition: str) -> str:
    upper = file_name.upper()
    if "ETD" in upper:
        return "ETD"
    if acquisition == "MS1-only":
        return "none"
    if "SCIEX" in vendor.upper() or "TRIPLETOF" in upper or "TTOF" in upper:
        return "CID"
    if "THERMO" in vendor.upper() or "QE-" in upper or "E480" in upper or "01625B" in upper:
        return "HCD"
    return ""


MANUAL_METADATA_FIXES: dict[str, dict[str, str]] = {
    # Verified from mzML filter strings and activation CV terms:
    #   FTMS ... Full ms2 ... @hcd... and MS:1000422 beam-type CID.
    "File5_S8184TPST_01.uncompressed.mzML": {
        "acquisition": "DDA",
        "activation_fragmentation": "HCD",
    },
    "File6_Negative_000333.uncompressed.mzML": {
        "acquisition": "DDA",
        "activation_fragmentation": "HCD",
    },
    # Agilent QTOF file has MS1 and MS2 spectra with precursorList and
    # beam-type CID/collision energy CV terms.
    "Set 1_F2.uncompressed.mzML": {
        "acquisition": "DDA",
        "activation_fragmentation": "CID",
    },
    # AIF describes the acquisition mode. The actual activation in the mzML
    # filter strings is @hcd27 with beam-type collision-induced dissociation.
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML": {
        "acquisition": "AIF",
        "activation_fragmentation": "HCD",
    },
    "File13_SA1.uncompressed.mzML": {
        "ms2_type": "not applicable",
    },
}


def parse_inventory(path: Path) -> dict[str, dict[str, str]]:
    info: dict[str, dict[str, str]] = {}
    raw_size_info: dict[str, dict[str, str]] = {}
    current_table = ""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("| ---"):
            continue
        cells = split_md_row(stripped)
        if not cells:
            continue
        first = cells[0]
        if first in {"File", "Directory"} or "鏂囦欢" in first:
            if len(cells) >= 14 and cells[1] == "Size (GiB)":
                current_table = "header_inventory"
            elif len(cells) >= 8 and ("娴嬪簭" in "".join(cells[:3]) or "mzML澶у皬" in "".join(cells)):
                current_table = "selected_or_additional"
            elif len(cells) >= 4 and "鍘熷" in "".join(cells[:3]):
                current_table = "raw_size"
            else:
                current_table = ""
            continue
        if first.startswith("---") or not first.lower().endswith(".mzml"):
            continue

        if current_table == "header_inventory" and len(cells) >= 13:
            entry = info.setdefault(first, {})
            entry.update(
                {
                    "mzml_size_gib_inventory": cells[1],
                    "spectra": cells[2],
                    "ms1_count": cells[3],
                    "ms2_count": cells[4],
                    "other_count": cells[5],
                    "instrument_model": cells[6],
                    "instrument_serial": cells[7],
                    "ms1_type": cells[8],
                    "ms2_type": cells[9],
                    "run_start": cells[10],
                    "source_format": cells[11],
                    "source_file": cells[12],
                }
            )
        elif current_table == "selected_or_additional" and len(cells) >= 8:
            entry = info.setdefault(first, {})
            entry.update(
                {
                    "vendor": cells[1],
                    "instrument_model": cells[2],
                    "acquisition": cells[3],
                    "activation_fragmentation": cells[4],
                    "mzml_size_gib_inventory": cells[5],
                    "ms1_type": cells[6],
                    "ms2_type": cells[7],
                }
            )
            if len(cells) >= 11:
                entry.update(
                    {
                        "original_size_gib_inventory": cells[6],
                        "original_size_source": cells[7],
                        "ms_format_text": cells[8],
                        "source_path_inventory": cells[9],
                    }
                )
                if " + " in cells[8]:
                    parts = [part.strip() for part in cells[8].split("+")]
                    if len(parts) >= 2:
                        entry["ms1_type"] = parts[0].replace("MS1", "").strip()
                        entry["ms2_type"] = parts[1].replace("MS2", "").strip()
        elif current_table == "raw_size" and len(cells) >= 4:
            raw_size_info[first] = {
                "original_size_text": cells[1],
                "original_size_source": cells[2],
                "mzml_size_gib_inventory": cells[3],
            }

    for file_name, raw_info in raw_size_info.items():
        entry = info.setdefault(file_name, {})
        entry.update(raw_info)
    return info


def parse_inventory_v2(path: Path) -> dict[str, dict[str, str]]:
    """Parse inventory markdown by row shape instead of mojibake table headers."""
    info: dict[str, dict[str, str]] = {}
    raw_size_info: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("| ---"):
            continue
        cells = split_md_row(stripped)
        if not cells:
            continue
        first = cells[0]
        if first.startswith("---") or not first.lower().endswith(".mzml"):
            continue

        # Header inventory rows: File, mzML size, spectra counts, instrument,
        # MS1/MS2 type, source format, source file, ...
        if len(cells) >= 13 and has_positive_number(cells[1]) and cells[11]:
            entry = info.setdefault(first, {})
            entry.update(
                {
                    "mzml_size_gib_inventory": cells[1],
                    "spectra": cells[2],
                    "ms1_count": cells[3],
                    "ms2_count": cells[4],
                    "other_count": cells[5],
                    "instrument_model": cells[6],
                    "instrument_serial": cells[7],
                    "ms1_type": cells[8],
                    "ms2_type": cells[9],
                    "run_start": cells[10],
                    "source_format": cells[11],
                    "source_file": cells[12],
                }
            )
            continue

        # Additional 15 QC rows: file, vendor, instrument, acquisition,
        # activation, mzML size, RAW size, source, "MS1 ... + MS2 ...", paths.
        if len(cells) >= 11 and has_positive_number(cells[5]) and "MS1" in cells[8]:
            entry = info.setdefault(first, {})
            entry.update(
                {
                    "vendor": cells[1],
                    "instrument_model": cells[2],
                    "acquisition": cells[3],
                    "activation_fragmentation": cells[4],
                    "mzml_size_gib_inventory": cells[5],
                    "original_size_text": cells[6],
                    "original_size_source": cells[7],
                    "ms_format_text": cells[8],
                    "source_path_inventory": cells[9],
                }
            )
            if " + " in cells[8]:
                parts = [part.strip() for part in cells[8].split("+")]
                if len(parts) >= 2:
                    entry["ms1_type"] = parts[0].replace("MS1", "").strip()
                    entry["ms2_type"] = parts[1].replace("MS2", "").strip()
            continue

        # Selected dataset summary rows: file, vendor, instrument, acquisition,
        # activation, mzML size, MS1 type, MS2 type.
        if len(cells) == 8 and has_positive_number(cells[5]):
            entry = info.setdefault(first, {})
            entry.update(
                {
                    "vendor": cells[1],
                    "instrument_model": cells[2],
                    "acquisition": cells[3],
                    "activation_fragmentation": cells[4],
                    "mzml_size_gib_inventory": cells[5],
                    "ms1_type": cells[6],
                    "ms2_type": cells[7],
                }
            )
            continue

        # Raw source versus mzML size rows.
        if len(cells) == 4 and parse_size_to_bytes(cells[1]) and has_positive_number(cells[3]):
            raw_size_info[first] = {
                "original_size_text": cells[1],
                "original_size_source": cells[2],
                "mzml_size_gib_inventory": cells[3],
            }

    for file_name, raw_info in raw_size_info.items():
        entry = info.setdefault(file_name, {})
        entry.update(raw_info)
    return info


def read_final_files() -> list[dict[str, str]]:
    rows = read_csv_rows(COMBINED_TABLE)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        path = row.get("input_path", "")
        if not path:
            continue
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "dataset": row.get("dataset", ""),
                "file": row.get("file", Path(path).name),
                "input_path": path,
            }
        )
    return out


def raw_path_for(file_name: str, source_file: str) -> Path | None:
    candidates = []
    stem = file_name
    for suffix in (".true_uncompressed.mzML", "_centroided.mzML", ".uncompressed.mzML", ".mzML", ".mzml"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if source_file:
        candidates.append(DATA_FULL8_RAW / source_file)
    candidates.append(DATA_FULL8_RAW / f"{stem}.raw")
    if "_centroided" in file_name:
        candidates.append(DATA_FULL8_RAW / file_name.replace("_centroided.mzML", ".raw"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def fallback_from_base(info: dict[str, dict[str, str]], file_name: str) -> dict[str, str]:
    if file_name.endswith("_centroided.mzML"):
        base = file_name.replace("_centroided.mzML", ".mzML")
        out = dict(info.get(base, {}))
        if out:
            out["ms1_type"] = "centroid"
            out["ms2_type"] = "centroid"
        return out
    return {}


def normalize_ms2_type(ms2_type: str, ms2_count: str) -> str:
    if ms2_type == "unknown" and str(ms2_count).replace(",", "").strip() in {"", "0"}:
        return "not applicable"
    return ms2_type


def build_rows() -> list[dict[str, str]]:
    inventory = parse_inventory_v2(INVENTORY_MD)
    rows: list[dict[str, str]] = []
    for item in read_final_files():
        file_name = item["file"]
        path = Path(item["input_path"])
        entry = dict(inventory.get(file_name, {}) or fallback_from_base(inventory, file_name))
        entry.update(MANUAL_METADATA_FIXES.get(file_name, {}))
        vendor = entry.get("vendor", "")
        source_format = entry.get("source_format", "")
        if not vendor and "thermo" in source_format.lower():
            vendor = "Thermo Fisher"
        elif not vendor and "abi wiff" in source_format.lower():
            vendor = "SCIEX"
        elif not vendor and "agilent" in source_format.lower():
            vendor = "Agilent"
        acquisition = entry.get("acquisition", "") or acquisition_from_name(file_name)
        activation = entry.get("activation_fragmentation", "")
        if activation in {"AIF", "unknown", "CID-like/unknown"}:
            activation = ""
        activation = activation or activation_from_name(file_name, vendor, acquisition)
        source_file = entry.get("source_file", "")
        raw_path = raw_path_for(file_name, source_file)
        original_size_bytes = raw_path.stat().st_size if raw_path is not None else parse_size_to_bytes(entry.get("original_size_text", ""))
        original_size_source = "local file" if raw_path is not None else entry.get("original_size_source", "")
        if raw_path is not None:
            source_file = raw_path.name
            source_format = source_format or "Thermo RAW format"
        if file_name.endswith("_centroided.mzML") and not source_file:
            source_file = file_name.replace("_centroided.mzML", ".raw")
            source_format = "Thermo RAW format"
        file_type = original_file_type(source_format, source_file, original_size_source)
        if file_name.endswith("_centroided.mzML") and not file_type:
            file_type = ".raw"
        ms2_type = normalize_ms2_type(entry.get("ms2_type", ""), entry.get("ms2_count", ""))
        rows.append(
            {
                "dataset": item["dataset"],
                "file": file_name,
                "vendor": vendor,
                "instrument_model": entry.get("instrument_model", ""),
                "acquisition": acquisition,
                "activation_fragmentation": activation,
                "original_file_type": file_type,
                "source_format": source_format,
                "source_file": source_file,
                "original_size_bytes": str(original_size_bytes) if original_size_bytes else "",
                "original_size_gib": gib(original_size_bytes),
                "original_size_source": original_size_source,
                "mzml_size_bytes": str(path.stat().st_size) if path.exists() else "",
                "mzml_size_gib": gib(path.stat().st_size) if path.exists() else "",
                "ms1_type": entry.get("ms1_type", ""),
                "ms2_type": ms2_type,
                "ms1_count": entry.get("ms1_count", ""),
                "ms2_count": entry.get("ms2_count", ""),
                "spectra": entry.get("spectra", ""),
                "input_path": str(path),
                "source_path": str(raw_path) if raw_path is not None else entry.get("source_path_inventory", ""),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    headers = [
        ("File ID", "file_id"),
        ("Dataset", "dataset"),
        ("File", "file"),
        ("Vendor", "vendor"),
        ("Instrument model", "instrument_model"),
        ("Acquisition", "acquisition"),
        ("Activation/fragmentation", "activation_fragmentation"),
        ("Original type", "original_file_type"),
        ("Original size (GiB)", "original_size_gib"),
        ("mzML size (GiB)", "mzml_size_gib"),
        ("MS1 type", "ms1_type"),
        ("MS2 type", "ms2_type"),
        ("Source file", "source_file"),
    ]
    lines = [
        "# Benchmark 35-File Metadata",
        "",
        f"- Source benchmark table: `{COMBINED_TABLE}`",
        f"- Metadata inventory: `{INVENTORY_MD}`",
        f"- Rows: {len(rows)}",
        "",
        "| " + " | ".join(title for title, _ in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for index, row in enumerate(rows, start=1):
        row = dict(row)
        row["file_id"] = f"File {index}"
        cells = [row.get(key, "") for _, key in headers]
        lines.append("| " + " | ".join(cell.replace("|", "/") for cell in cells) + " |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    rows = build_rows()
    write_csv(OUTPUT_CSV, rows)
    write_markdown(OUTPUT_MD, rows)
    missing: dict[str, list[str]] = {}
    required = [
        "vendor",
        "instrument_model",
        "acquisition",
        "activation_fragmentation",
        "original_file_type",
        "original_size_gib",
        "mzml_size_gib",
        "ms1_type",
        "ms2_type",
    ]
    for row in rows:
        miss = [field for field in required if not row.get(field)]
        if miss:
            missing[row["file"]] = miss
    summary = {
        "csv": str(OUTPUT_CSV),
        "markdown": str(OUTPUT_MD),
        "rows": len(rows),
        "missing": missing,
    }
    OUTPUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
