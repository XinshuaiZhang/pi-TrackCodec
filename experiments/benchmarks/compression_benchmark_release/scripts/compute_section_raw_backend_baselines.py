#!/usr/bin/env python3
from __future__ import annotations

import csv
import argparse
import concurrent.futures as futures
import gzip
import json
import sys
import time
import zlib
from pathlib import Path

import numpy as np
import zstandard as zstd
from pyteomics import mzml

from benchmark_release_paths import default_combined_output_dir, find_workspace_root, inputs_root

ROOT = find_workspace_root(Path(__file__))
SOURCE_MD = inputs_root(Path(__file__)) / "refs" / "compression_ratio_tables_with_ms_format.md"
DATA_DIR = ROOT / "benchmark" / "data_StackZDPD"
OUTPUT_DIR = default_combined_output_dir(Path(__file__))
TABLES_DIR = OUTPUT_DIR / "tables"

TARGET_FILES = [
    "File13_SA1.uncompressed.mzML",
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML",
    "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML",
    "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML",
    "File5_S8184TPST_01.uncompressed.mzML",
    "File6_Negative_000333.uncompressed.mzML",
    "Set 1_F2.uncompressed.mzML",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compute section-level gzip/zlib/zstd raw float64 baselines for mzML files."
    )
    parser.add_argument(
        "--input-list",
        action="append",
        default=[],
        help="Text file with one mzML path per line. Can be repeated.",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Additional mzML input path. Can be repeated.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of files to process concurrently. Default: 1.",
    )
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        help="Previously computed file key or mzML file name to remove from output. Can be repeated.",
    )
    return parser.parse_args()


def _target_paths(args: argparse.Namespace) -> list[Path]:
    paths: list[Path] = []
    for value in args.input:
        paths.append(Path(value).resolve())
    for list_path_text in args.input_list:
        list_path = Path(list_path_text)
        for line in list_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text and not text.startswith("#"):
                paths.append(Path(text).resolve())
    if not paths:
        paths = [(DATA_DIR / name).resolve() for name in TARGET_FILES]
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _file_key(path: Path) -> str:
    stem = path.name
    for suffix in (".uncompressed.mzML", ".mzML"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return path.stem


def _display_file_name(path: Path) -> str:
    key = _file_key(path)
    return key.replace("Set 1_F2", "Set_1_F2")


def _short_file_label(file_name: str) -> str:
    if file_name.startswith("Set_1_F2"):
        return "Set_1_F2"
    if file_name.startswith("File"):
        return file_name.split("_", 1)[0]
    return file_name


def _ms_format_from_source() -> dict[str, str]:
    formats: dict[str, str] = {}
    for line in SOURCE_MD.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("| File"):
            continue
        break
    for line in SOURCE_MD.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("|---") or stripped.startswith("| File"):
            continue
        cells = [part.strip() for part in stripped.strip("|").split("|")]
        if len(cells) < 2:
            continue
        file_name, ms_format = cells[0], cells[1]
        if file_name.startswith("Mean"):
            continue
        formats[file_name] = ms_format
    return formats


class SectionCompressors:
    def __init__(self) -> None:
        self.gzip_ms1 = zlib.compressobj(level=6, wbits=16 + zlib.MAX_WBITS)
        self.gzip_ms2 = zlib.compressobj(level=6, wbits=16 + zlib.MAX_WBITS)
        self.zlib_ms1 = zlib.compressobj(level=6)
        self.zlib_ms2 = zlib.compressobj(level=6)
        self.zstd_ms1 = zstd.ZstdCompressor(level=9).compressobj()
        self.zstd_ms2 = zstd.ZstdCompressor(level=9).compressobj()
        self.raw = {"MS1": 0, "MS2": 0}
        self.comp = {
            ("MS1", "gzip"): 0,
            ("MS2", "gzip"): 0,
            ("MS1", "zlib"): 0,
            ("MS2", "zlib"): 0,
            ("MS1", "zstd-9"): 0,
            ("MS2", "zstd-9"): 0,
        }

    def update(self, section: str, payload: bytes) -> None:
        self.raw[section] += len(payload)
        if section == "MS1":
            self.comp[(section, "gzip")] += len(self.gzip_ms1.compress(payload))
            self.comp[(section, "zlib")] += len(self.zlib_ms1.compress(payload))
            self.comp[(section, "zstd-9")] += len(self.zstd_ms1.compress(payload))
        else:
            self.comp[(section, "gzip")] += len(self.gzip_ms2.compress(payload))
            self.comp[(section, "zlib")] += len(self.zlib_ms2.compress(payload))
            self.comp[(section, "zstd-9")] += len(self.zstd_ms2.compress(payload))

    def finish(self) -> None:
        self.comp[("MS1", "gzip")] += len(self.gzip_ms1.flush())
        self.comp[("MS2", "gzip")] += len(self.gzip_ms2.flush())
        self.comp[("MS1", "zlib")] += len(self.zlib_ms1.flush())
        self.comp[("MS2", "zlib")] += len(self.zlib_ms2.flush())
        self.comp[("MS1", "zstd-9")] += len(self.zstd_ms1.flush())
        self.comp[("MS2", "zstd-9")] += len(self.zstd_ms2.flush())


def _iter_mzml(path: Path):
    return mzml.MzML(
        str(path),
        read_schema=False,
        iterative=True,
        use_index=False,
        huge_tree=True,
        decode_binary=True,
    )


def compute_file(path: Path, formats: dict[str, str]) -> list[dict[str, str]]:
    started = time.perf_counter()
    compressors = SectionCompressors()
    ms_counts = {"MS1": 0, "MS2": 0}
    with _iter_mzml(path) as reader:
        for idx, spec in enumerate(reader, start=1):
            level = int(spec.get("ms level", 0) or 0)
            if level not in {1, 2}:
                continue
            mz_arr = np.asarray(spec.get("m/z array", []), dtype=np.float64)
            int_arr = np.asarray(spec.get("intensity array", []), dtype=np.float64)
            if mz_arr.size == 0 and int_arr.size == 0:
                continue
            section = "MS1" if level == 1 else "MS2"
            payload = mz_arr.tobytes(order="C") + int_arr.tobytes(order="C")
            compressors.update(section, payload)
            ms_counts[section] += 1
            if idx % 2000 == 0:
                print(f"[{path.name}] spectra={idx} ms1_raw={compressors.raw['MS1']} ms2_raw={compressors.raw['MS2']}", flush=True)
    compressors.finish()
    elapsed = time.perf_counter() - started
    file_name = _display_file_name(path)
    ms_format = formats.get(file_name, "")
    rows: list[dict[str, str]] = []
    for section in ("MS1", "MS2"):
        for method in ("gzip", "zlib", "zstd-9"):
            raw_bytes = compressors.raw[section]
            compressed_bytes = compressors.comp[(section, method)]
            rows.append(
                {
                    "section": section,
                    "file": file_name,
                    "file_label": _short_file_label(file_name),
                    "ms_format": ms_format,
                    "label": method,
                    "display_name": {
                        "gzip": "gzip raw float64",
                        "zlib": "zlib raw float64",
                        "zstd-9": "zstd-9 raw float64",
                    }[method],
                    "family": "raw_backend",
                    "raw_bytes": str(raw_bytes),
                    "compressed_bytes": str(compressed_bytes),
                    "compression_ratio": f"{raw_bytes / compressed_bytes:.6f}" if compressed_bytes else "0.000000",
                    "include_in_aggregate": "yes" if raw_bytes and compressed_bytes else "no",
                    "ms_scans": str(ms_counts[section]),
                    "elapsed_seconds_file": f"{elapsed:.6f}",
                }
            )
    print(f"[done] {path.name} elapsed_s={elapsed:.2f}", flush=True)
    return rows


def read_existing(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
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


def main() -> int:
    args = parse_args()
    out_path = TABLES_DIR / "section_raw_backend_baselines.csv"
    existing = read_existing(out_path)
    exclude_keys = {_display_file_name(Path(name)) for name in args.exclude_file}
    if exclude_keys:
        existing = [row for row in existing if row.get("file") not in exclude_keys]
    done = {(row["file"], row["section"], row["label"]) for row in existing}
    formats = _ms_format_from_source()
    all_rows = list(existing)
    target_paths = _target_paths(args)
    pending: list[tuple[Path, set[tuple[str, str, str]]]] = []
    for path in target_paths:
        file_name = _display_file_name(path)
        expected_keys = {(file_name, sec, method) for sec in ("MS1", "MS2") for method in ("gzip", "zlib", "zstd-9")}
        if expected_keys.issubset(done):
            print(f"[skip] {path.name} already complete", flush=True)
            continue
        if not path.exists():
            raise FileNotFoundError(path)
        pending.append((path, expected_keys))

    workers = max(1, int(args.workers))
    if workers == 1:
        iterator = ((path, keys, compute_file(path, formats)) for path, keys in pending)
        for path, expected_keys, file_rows in iterator:
            all_rows = [
                row
                for row in all_rows
                if (row["file"], row["section"], row["label"]) not in expected_keys
            ]
            all_rows.extend(file_rows)
            write_csv(out_path, all_rows)
            done = {(row["file"], row["section"], row["label"]) for row in all_rows}
    else:
        with futures.ProcessPoolExecutor(max_workers=workers) as pool:
            submitted = {
                pool.submit(compute_file, path, formats): (path, expected_keys)
                for path, expected_keys in pending
            }
            for future in futures.as_completed(submitted):
                path, expected_keys = submitted[future]
                file_rows = future.result()
                all_rows = [
                    row
                    for row in all_rows
                    if (row["file"], row["section"], row["label"]) not in expected_keys
                ]
                all_rows.extend(file_rows)
                write_csv(out_path, all_rows)
                done = {(row["file"], row["section"], row["label"]) for row in all_rows}
                print(f"[merged] {path.name}", flush=True)
    write_csv(out_path, all_rows)
    summary = {
        "output_csv": str(out_path),
        "n_rows": len(all_rows),
        "files": [str(path) for path in target_paths],
        "exclude_files": sorted(args.exclude_file),
        "excluded_file_keys": sorted(exclude_keys),
        "methods": ["gzip", "zlib", "zstd-9"],
    }
    (TABLES_DIR / "section_raw_backend_baselines_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
