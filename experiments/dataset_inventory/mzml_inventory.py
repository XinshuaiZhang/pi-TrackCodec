from __future__ import annotations

import argparse
import csv
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[2]
os.sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.datasets import FULL8_PROFILE_FILES


STACKZDPD_ROOT_ENV = "TRACKCODEC_STACKZDPD_ROOT"
DEFAULT_OUT_ROOT = Path(os.environ.get(
    "TRACKCODEC_MZML_INVENTORY_ROOT",
    ROOT.parent / "benchmark_results" / "dataset_mzml_inventory",
))

MS_LEVEL_ACCESSION = "MS:1000511"
MZ_ARRAY_ACCESSION = "MS:1000514"
INTENSITY_ARRAY_ACCESSION = "MS:1000515"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"ts": _now_iso(), **payload}, ensure_ascii=False) + "\n")


def _iter_stackzdpd_uncompressed_files() -> list[Path]:
    stackzdpd_root_value = os.environ.get(STACKZDPD_ROOT_ENV)
    if not stackzdpd_root_value:
        raise ValueError(f"{STACKZDPD_ROOT_ENV} is required when inventorying stackzdpd_all_uncompressed.")
    stackzdpd_root = Path(stackzdpd_root_value).expanduser()
    files = []
    for path in stackzdpd_root.rglob("*.mzML"):
        name = path.name.lower()
        if name.endswith(".uncompressed.mzml") or name.endswith(".true_uncompressed.mzml"):
            files.append(path)
    return sorted(files)


def _dataset_map(selected_datasets: list[str] | None = None) -> dict[str, list[Path]]:
    selected = set(selected_datasets or ("full8", "stackzdpd_all_uncompressed"))
    datasets: dict[str, list[Path]] = {}
    if "full8" in selected:
        datasets["full8"] = list(FULL8_PROFILE_FILES)
    if "stackzdpd_all_uncompressed" in selected:
        datasets["stackzdpd_all_uncompressed"] = _iter_stackzdpd_uncompressed_files()
    return datasets


def _unique_files_in_order(datasets: dict[str, list[Path]]) -> tuple[list[Path], dict[str, list[str]]]:
    seen: set[str] = set()
    unique_files: list[Path] = []
    file_to_datasets: dict[str, list[str]] = {}
    for dataset, files in datasets.items():
        for path in files:
            key = str(path)
            file_to_datasets.setdefault(key, []).append(dataset)
            if key in seen:
                continue
            seen.add(key)
            unique_files.append(path)
    return unique_files, file_to_datasets


def _get_ms_level(spectrum_elem) -> int:
    for cv in spectrum_elem.iterfind(".//{*}cvParam"):
        accession = cv.get("accession")
        if accession == MS_LEVEL_ACCESSION or (cv.get("name") or "").lower() == "ms level":
            try:
                return int(float(cv.get("value", "0")))
            except Exception:
                return 0
    return 0


def _get_binary_kind(binary_array_elem) -> str | None:
    for cv in binary_array_elem.iterfind("./{*}cvParam"):
        accession = cv.get("accession")
        name = (cv.get("name") or "").lower()
        if accession == MZ_ARRAY_ACCESSION or name == "m/z array":
            return "mz"
        if accession == INTENSITY_ARRAY_ACCESSION or name == "intensity array":
            return "intensity"
    return None


def analyze_mzml_file(path: Path) -> dict:
    file_size_bytes = int(path.stat().st_size)
    ms1_scan_count = 0
    ms2_scan_count = 0
    ms1_mz_int_binary_text_bytes = 0
    ms2_mz_int_binary_text_bytes = 0
    total_spectrum_binary_text_bytes = 0
    total_non_mzint_spectrum_binary_text_bytes = 0

    context = etree.iterparse(
        str(path),
        events=("end",),
        tag="{*}spectrum",
        huge_tree=True,
        recover=True,
    )
    for _, spectrum in context:
        ms_level = _get_ms_level(spectrum)
        if ms_level == 1:
            ms1_scan_count += 1
        elif ms_level == 2:
            ms2_scan_count += 1

        for bda in spectrum.iterfind(".//{*}binaryDataArray"):
            kind = _get_binary_kind(bda)
            binary = bda.find("./{*}binary")
            text = binary.text if binary is not None and binary.text is not None else ""
            binary_len = len(text.strip()) if text else 0
            total_spectrum_binary_text_bytes += binary_len
            if kind in {"mz", "intensity"}:
                if ms_level == 1:
                    ms1_mz_int_binary_text_bytes += binary_len
                elif ms_level == 2:
                    ms2_mz_int_binary_text_bytes += binary_len
                else:
                    total_non_mzint_spectrum_binary_text_bytes += binary_len
            else:
                total_non_mzint_spectrum_binary_text_bytes += binary_len

        spectrum.clear()
        while spectrum.getprevious() is not None:
            del spectrum.getparent()[0]
    del context

    mzint_binary_text_bytes = ms1_mz_int_binary_text_bytes + ms2_mz_int_binary_text_bytes
    other_bytes = max(0, file_size_bytes - mzint_binary_text_bytes)
    return {
        "file": str(path),
        "file_name": path.name,
        "file_size_bytes": file_size_bytes,
        "ms1_scan_count": ms1_scan_count,
        "ms2_scan_count": ms2_scan_count,
        "total_scan_count": ms1_scan_count + ms2_scan_count,
        "ms1_mz_int_binary_text_bytes": ms1_mz_int_binary_text_bytes,
        "ms2_mz_int_binary_text_bytes": ms2_mz_int_binary_text_bytes,
        "mzint_binary_text_bytes_total": mzint_binary_text_bytes,
        "other_bytes": other_bytes,
        "total_spectrum_binary_text_bytes": total_spectrum_binary_text_bytes,
        "non_mzint_spectrum_binary_text_bytes": total_non_mzint_spectrum_binary_text_bytes,
        "ms1_share_of_file_pct": (ms1_mz_int_binary_text_bytes / file_size_bytes * 100.0) if file_size_bytes else 0.0,
        "ms2_share_of_file_pct": (ms2_mz_int_binary_text_bytes / file_size_bytes * 100.0) if file_size_bytes else 0.0,
        "other_share_of_file_pct": (other_bytes / file_size_bytes * 100.0) if file_size_bytes else 0.0,
    }


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _human_gb(n: int) -> float:
    return float(n) / (1024 ** 3)


def _dataset_summary_rows(per_file_rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in per_file_rows:
        grouped.setdefault(row["dataset"], []).append(row)
    out = []
    for dataset, rows in grouped.items():
        file_size = sum(int(r["file_size_bytes"]) for r in rows)
        ms1_bytes = sum(int(r["ms1_mz_int_binary_text_bytes"]) for r in rows)
        ms2_bytes = sum(int(r["ms2_mz_int_binary_text_bytes"]) for r in rows)
        other_bytes = sum(int(r["other_bytes"]) for r in rows)
        out.append(
            {
                "dataset": dataset,
                "n_files": len(rows),
                "file_size_bytes_total": file_size,
                "file_size_gib_total": _human_gb(file_size),
                "ms1_scan_count_total": sum(int(r["ms1_scan_count"]) for r in rows),
                "ms2_scan_count_total": sum(int(r["ms2_scan_count"]) for r in rows),
                "total_scan_count_total": sum(int(r["total_scan_count"]) for r in rows),
                "ms1_mz_int_binary_text_bytes_total": ms1_bytes,
                "ms1_mz_int_binary_text_gib_total": _human_gb(ms1_bytes),
                "ms2_mz_int_binary_text_bytes_total": ms2_bytes,
                "ms2_mz_int_binary_text_gib_total": _human_gb(ms2_bytes),
                "other_bytes_total": other_bytes,
                "other_gib_total": _human_gb(other_bytes),
                "ms1_pct_of_file_total": (ms1_bytes / file_size * 100.0) if file_size else 0.0,
                "ms2_pct_of_file_total": (ms2_bytes / file_size * 100.0) if file_size else 0.0,
                "other_pct_of_file_total": (other_bytes / file_size * 100.0) if file_size else 0.0,
            }
        )
    return out


def _write_md_report(
    out_path: Path,
    summary_rows: list[dict],
    per_file_rows: list[dict],
    assumptions: list[str],
) -> None:
    grouped: dict[str, list[dict]] = {}
    for row in per_file_rows:
        grouped.setdefault(row["dataset"], []).append(row)

    lines = [
        "# mzML Dataset Inventory",
        "",
        f"- Updated: `{_now_iso()}`",
        "",
        "## Metric Definition",
        "",
        "- `file_size_bytes`: 文件在磁盘上的实际大小。",
        "- `ms1_mz_int_binary_text_bytes`: `MS1 spectrum` 中 `m/z array + intensity array` 的 `<binary>` base64 文本长度总和。",
        "- `ms2_mz_int_binary_text_bytes`: `MS2 spectrum` 中 `m/z array + intensity array` 的 `<binary>` base64 文本长度总和。",
        "- `other_bytes`: `file_size_bytes - ms1_mz_int_binary_text_bytes - ms2_mz_int_binary_text_bytes`。",
        "- 因此 `other_bytes` 表示“除去 MS1/MS2 的 m/z-intensity binary 文本之后的其余部分”，包括 XML 结构、非 m/z-intensity binary arrays、chromatogram、metadata 等。",
        "",
        "## Assumptions",
        "",
    ]
    lines.extend([f"- {item}" for item in assumptions])
    lines.extend(["", "## Dataset Summary", ""])
    lines.append("| Dataset | Files | MS1 scans | MS2 scans | Whole size (GiB) | MS1 m/z-int (GiB) | MS2 m/z-int (GiB) | Other (GiB) | MS1 % | MS2 % | Other % |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in summary_rows:
        lines.append(
            f"| `{row['dataset']}` | {row['n_files']} | {row['ms1_scan_count_total']} | {row['ms2_scan_count_total']} | "
            f"{row['file_size_gib_total']:.3f} | {row['ms1_mz_int_binary_text_gib_total']:.3f} | "
            f"{row['ms2_mz_int_binary_text_gib_total']:.3f} | {row['other_gib_total']:.3f} | "
            f"{row['ms1_pct_of_file_total']:.2f} | {row['ms2_pct_of_file_total']:.2f} | {row['other_pct_of_file_total']:.2f} |"
        )
    for dataset, rows in grouped.items():
        lines.extend(["", f"## {dataset}", ""])
        lines.append("| File | MS1 scans | MS2 scans | Whole size (GiB) | MS1 m/z-int (GiB) | MS2 m/z-int (GiB) | Other (GiB) |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|")
        for row in rows:
            lines.append(
                f"| `{row['file_name']}` | {row['ms1_scan_count']} | {row['ms2_scan_count']} | "
                f"{_human_gb(int(row['file_size_bytes'])):.3f} | "
                f"{_human_gb(int(row['ms1_mz_int_binary_text_bytes'])):.3f} | "
                f"{_human_gb(int(row['ms2_mz_int_binary_text_bytes'])):.3f} | "
                f"{_human_gb(int(row['other_bytes'])):.3f} |"
            )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=("full8", "stackzdpd_all_uncompressed"),
        help="Only inventory the selected datasets. Default: all datasets.",
    )
    args = parser.parse_args()

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    logs_dir = out_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    progress_path = logs_dir / "dataset_mzml_inventory.progress.jsonl"
    per_file_csv = out_root / "dataset_mzml_inventory_per_file.csv"
    summary_csv = out_root / "dataset_mzml_inventory_summary.csv"
    report_md = out_root / "dataset_mzml_inventory_report.md"
    for path in [progress_path, per_file_csv, summary_csv, report_md]:
        if path.exists():
            path.unlink()

    datasets = _dataset_map(args.datasets)
    unique_files, file_to_datasets = _unique_files_in_order(datasets)
    unique_files = sorted(unique_files, key=lambda path: path.stat().st_size, reverse=True)
    workers = max(1, int(args.workers))
    assumptions = [
        "`full8` 使用 `production/common/datasets.py` 中的 `FULL8_PROFILE_FILES`。",
        f"`stackzdpd_all_uncompressed` is defined as all `*.uncompressed.mzML` or `*.true_uncompressed.mzML` files under `{STACKZDPD_ROOT_ENV}`; derived `numpress/zlib/zstd` mzML files are excluded.",
        "同一物理样本若在不同子目录下各有一个 `uncompressed.mzML` 文件，会按“不同文件”分别统计。",
        f"唯一物理文件只解析一次；当前运行使用 `workers={workers}` 个进程并行统计，再按 `full8/stackzdpd_all_uncompressed` 两个数据集回填汇总。",
    ]
    _append_jsonl(
        progress_path,
        {
            "event": "inventory_start",
            "datasets": {k: len(v) for k, v in datasets.items()},
            "unique_files": len(unique_files),
            "workers": workers,
        },
    )

    file_rows_by_path: dict[str, dict] = {}
    if workers == 1:
        for idx, path in enumerate(unique_files, start=1):
            _append_jsonl(
                progress_path,
                {
                    "event": "unique_file_start",
                    "index": idx,
                    "n_files": len(unique_files),
                    "file": str(path),
                    "datasets": file_to_datasets.get(str(path), []),
                },
            )
            row = analyze_mzml_file(path)
            file_rows_by_path[str(path)] = row
            _append_jsonl(
                progress_path,
                {
                    "event": "unique_file_done",
                    "index": idx,
                    "n_files": len(unique_files),
                    "file": str(path),
                    "datasets": file_to_datasets.get(str(path), []),
                    "ms1_scan_count": row["ms1_scan_count"],
                    "ms2_scan_count": row["ms2_scan_count"],
                    "file_size_bytes": row["file_size_bytes"],
                },
            )
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_map = {}
            for idx, path in enumerate(unique_files, start=1):
                _append_jsonl(
                    progress_path,
                    {
                        "event": "unique_file_submit",
                        "index": idx,
                        "n_files": len(unique_files),
                        "file": str(path),
                        "datasets": file_to_datasets.get(str(path), []),
                    },
                )
                future_map[pool.submit(analyze_mzml_file, path)] = (idx, path)
            for future in as_completed(future_map):
                idx, path = future_map[future]
                try:
                    row = future.result()
                except Exception as exc:
                    _append_jsonl(
                        progress_path,
                        {
                            "event": "unique_file_error",
                            "index": idx,
                            "n_files": len(unique_files),
                            "file": str(path),
                            "datasets": file_to_datasets.get(str(path), []),
                            "error": repr(exc),
                        },
                    )
                    raise
                file_rows_by_path[str(path)] = row
                _append_jsonl(
                    progress_path,
                    {
                        "event": "unique_file_done",
                        "index": idx,
                        "n_files": len(unique_files),
                        "file": str(path),
                        "datasets": file_to_datasets.get(str(path), []),
                        "ms1_scan_count": row["ms1_scan_count"],
                        "ms2_scan_count": row["ms2_scan_count"],
                        "file_size_bytes": row["file_size_bytes"],
                    },
                )

    per_file_rows: list[dict] = []
    for dataset, files in datasets.items():
        _append_jsonl(progress_path, {"event": "dataset_materialize_start", "dataset": dataset, "n_files": len(files)})
        for idx, path in enumerate(files, start=1):
            row = dict(file_rows_by_path[str(path)])
            row["dataset"] = dataset
            row["dataset_index"] = idx
            per_file_rows.append(row)
        _append_jsonl(progress_path, {"event": "dataset_materialize_done", "dataset": dataset, "n_files": len(files)})

    summary_rows = _dataset_summary_rows(per_file_rows)
    _write_csv(per_file_rows, per_file_csv)
    _write_csv(summary_rows, summary_csv)
    _write_md_report(
        report_md,
        summary_rows=summary_rows,
        per_file_rows=per_file_rows,
        assumptions=assumptions,
    )
    _append_jsonl(progress_path, {"event": "inventory_done", "summary_rows": len(summary_rows), "per_file_rows": len(per_file_rows)})


if __name__ == "__main__":
    main()
