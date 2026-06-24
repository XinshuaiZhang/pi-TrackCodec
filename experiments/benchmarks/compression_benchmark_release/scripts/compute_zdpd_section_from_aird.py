#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from benchmark_release_paths import default_combined_output_dir, find_workspace_root, inputs_root

ROOT = find_workspace_root(Path(__file__))
OUTPUT_ROOT = default_combined_output_dir(Path(__file__))
TABLES_DIR = OUTPUT_ROOT / "tables"
TRACKCODEC_SECTION_CSV = TABLES_DIR / "trackcodec_ms1_ms2_section_benchmark.csv"
SOURCE_MD = inputs_root(Path(__file__)) / "refs" / "compression_ratio_tables_with_ms_format.md"
DEFAULT_SUMMARY_CSVS = [
    inputs_root(Path(__file__)) / "whole_file_summaries" / "full8_summary.csv",
    inputs_root(Path(__file__)) / "whole_file_summaries" / "data_stackzdpd_summary.csv",
    inputs_root(Path(__file__)) / "whole_file_summaries" / "data_stackzdpd_aif_summary.csv",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compute AirdPro-family section-level compression ratios from existing "
            ".json indexList byte ranges and TrackCodec section raw bytes."
        )
    )
    parser.add_argument(
        "--method",
        default="zdpd",
        help="Method key in benchmark summary.csv, e.g. zdpd or airdpro. Default: zdpd.",
    )
    parser.add_argument(
        "--label",
        default="zdpd_baseline",
        help="Label written to the output CSV. Default: zdpd_baseline.",
    )
    parser.add_argument(
        "--display-name",
        default="ZDPD",
        help="Display name written to the output CSV. Default: ZDPD.",
    )
    parser.add_argument(
        "--summary-csv",
        action="append",
        default=[],
        help="Benchmark summary.csv containing zdpd rows. Can be repeated.",
    )
    parser.add_argument(
        "--trackcodec-section-csv",
        default=str(TRACKCODEC_SECTION_CSV),
        help="TrackCodec section CSV used as the MS1/MS2 raw-byte source.",
    )
    parser.add_argument(
        "--output-csv",
        default=str(TABLES_DIR / "zdpd_aird_section_benchmark.csv"),
        help="Output section-level ZDPD CSV.",
    )
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        help="Input file name to exclude from output. Can be repeated.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _short_file_label(file_name: str) -> str:
    if file_name.startswith("Set 1_F2") or file_name.startswith("Set_1_F2"):
        return "Set_1_F2"
    if file_name.startswith("File"):
        return file_name.split("_", 1)[0].replace(".uncompressed.mzML", "")
    for suffix in (".true_uncompressed.mzML", ".uncompressed.mzML", ".mzML"):
        if file_name.endswith(suffix):
            return file_name[: -len(suffix)]
    return Path(file_name).stem


def _ms_format_from_source() -> dict[str, str]:
    formats: dict[str, str] = {}
    if not SOURCE_MD.exists():
        return formats
    for line in SOURCE_MD.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|---") or stripped.startswith("| File"):
            continue
        cells = [part.strip() for part in stripped.strip("|").split("|")]
        if len(cells) < 2 or cells[0].startswith("Mean"):
            continue
        formats[cells[0]] = cells[1]
    return formats


def _trackcodec_raw_rows(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    rows: dict[tuple[str, str], dict[str, str]] = {}
    for row in read_csv(path):
        section = row.get("section", "")
        file_name = row.get("file", "")
        if section in {"MS1", "MS2"} and file_name:
            rows[(file_name, section)] = row
    return rows


def _output_paths(row: dict[str, str]) -> tuple[Path | None, Path | None]:
    json_path: Path | None = None
    aird_path: Path | None = None
    for item in (row.get("output_files") or "").split(";"):
        text = item.strip()
        if not text:
            continue
        path = Path(text)
        suffix = path.suffix.lower()
        if suffix == ".json":
            json_path = path
        elif suffix == ".aird":
            aird_path = path
    if json_path is None:
        method_dir = Path(row.get("method_dir") or "")
        matches = sorted(method_dir.glob("*.json")) if method_dir.exists() else []
        if matches:
            json_path = matches[0]
    if aird_path is None:
        method_dir = Path(row.get("method_dir") or "")
        matches = sorted(method_dir.glob("*.aird")) if method_dir.exists() else []
        if matches:
            aird_path = matches[0]
    return json_path, aird_path


def _aird_section_bytes(json_path: Path) -> tuple[dict[str, int], dict[str, int], list[str]]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    section_bytes = {"MS1": 0, "MS2": 0}
    section_scans = {"MS1": 0, "MS2": 0}
    notes: list[str] = []
    for item in data.get("indexList", []):
        try:
            level = int(item.get("level", 0) or 0)
            start = int(item.get("startPtr", 0) or 0)
            end = int(item.get("endPtr", 0) or 0)
        except (TypeError, ValueError):
            notes.append("invalid indexList pointer row")
            continue
        if level == 1:
            section = "MS1"
        elif level == 2:
            section = "MS2"
        else:
            continue
        if end < start:
            notes.append(f"{section} indexList row has endPtr < startPtr")
            continue
        section_bytes[section] += end - start
        nums = item.get("nums")
        if isinstance(nums, list):
            section_scans[section] += len(nums)
    aird_path_text = str(data.get("airdPath") or "")
    if aird_path_text:
        notes.append(f"airdPath={aird_path_text}")
    return section_bytes, section_scans, notes


def _method_rows(summary_paths: list[Path], method: str) -> list[dict[str, str]]:
    by_input: dict[str, dict[str, str]] = {}
    for summary_path in summary_paths:
        for row in read_csv(summary_path):
            if row.get("method") != method or row.get("status") != "ok":
                continue
            key = (row.get("input_path") or row.get("file_name") or "").lower()
            if key:
                by_input[key] = row
    return list(by_input.values())


def main() -> int:
    args = parse_args()
    summary_paths = [Path(p) for p in args.summary_csv] if args.summary_csv else DEFAULT_SUMMARY_CSVS
    trackcodec_csv = Path(args.trackcodec_section_csv)
    output_csv = Path(args.output_csv)
    exclude_files = set(args.exclude_file)
    raw_rows = _trackcodec_raw_rows(trackcodec_csv)
    formats = _ms_format_from_source()

    rows: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for zrow in _method_rows(summary_paths, args.method):
        file_name = zrow.get("file_name") or Path(zrow.get("input_path", "")).name
        if file_name in exclude_files:
            continue
        json_path, aird_path = _output_paths(zrow)
        if json_path is None or not json_path.exists():
            missing.append({"file": file_name, "reason": "missing AirdPro json"})
            continue
        section_bytes, section_scans, notes = _aird_section_bytes(json_path)
        if aird_path is not None and aird_path.exists():
            aird_size = int(aird_path.stat().st_size)
            indexed_size = section_bytes["MS1"] + section_bytes["MS2"]
            if indexed_size != aird_size:
                notes.append(f"indexed_aird_bytes={indexed_size}; aird_file_bytes={aird_size}")
        for section in ("MS1", "MS2"):
            raw_row = raw_rows.get((file_name, section))
            if raw_row is None:
                missing.append({"file": file_name, "section": section, "reason": "missing TrackCodec raw row"})
                continue
            raw_bytes = int(float(raw_row.get("raw_bytes") or 0))
            compressed_bytes = int(section_bytes[section])
            ratio = raw_bytes / compressed_bytes if raw_bytes > 0 and compressed_bytes > 0 else 0.0
            include = raw_bytes > 0 and compressed_bytes > 0
            rows.append(
                {
                    "section": section,
                    "file": file_name,
                    "file_label": raw_row.get("label") or _short_file_label(file_name),
                    "ms_format": formats.get(file_name.replace(".uncompressed.mzML", ""), ""),
                    "label": args.label,
                    "display_name": args.display_name,
                    "family": "baseline",
                    "raw_bytes": str(raw_bytes),
                    "compressed_bytes": str(compressed_bytes),
                    "compression_ratio": f"{ratio:.6f}",
                    "include_in_aggregate": "yes" if include else "no",
                    "ms_scans": str(section_scans[section]),
                    "note": "; ".join(notes),
                }
            )

    rows.sort(key=lambda r: (r["section"], r["file"]))
    write_csv(output_csv, rows)
    summary = {
        "output_csv": str(output_csv),
        "trackcodec_section_csv": str(trackcodec_csv),
        "summary_csvs": [str(path) for path in summary_paths],
        "method": args.method,
        "label": args.label,
        "exclude_files": sorted(exclude_files),
        "scope": "AirdPro .aird numeric payload bytes from json indexList; raw bytes from TrackCodec section stats",
        "n_rows": len(rows),
        "n_files": len({row["file"] for row in rows}),
        "included_counts": {
            section: sum(1 for row in rows if row["section"] == section and row["include_in_aggregate"] == "yes")
            for section in ("MS1", "MS2")
        },
        "missing": missing,
    }
    summary_path = output_csv.with_name(f"{output_csv.stem}_summary.json")
    summary_path.write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
