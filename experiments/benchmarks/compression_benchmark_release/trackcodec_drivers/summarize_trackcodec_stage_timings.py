from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Dict, Iterable, List


STAGE_GROUPS = [
    ("parse_scan_store", ["load_scans_s", "open_scan_store_s"]),
    ("metadata_auxiliary", ["metadata_auxiliary_total_s", "extract_metadata_auxiliary_s", "encode_metadata_s", "encode_auxiliary_s"]),
    ("ms1_build_tracks", ["build_ms1_tracks_s", "open_ms1_tracks_store_s", "spill_ms1_tracks_store_s"]),
    ("ms1_encode_payload", ["ms1_encode_s"]),
    ("ms1_sidecars", ["encode_ms1_array_starts_sidecar_s", "encode_ms1_orphan_intensity_sidecar_s", "encode_ms1_full_mz_sidecar_s"]),
    ("ms2_encode_payload", ["ms2_encode_s"]),
    ("ms2_serialize", ["serialize_ms2_payload_s"]),
    ("pack_archive", ["pack_archive_s"]),
]

INTERNAL_PREFIXES = ("ms1_internal_", "ms2_internal_")


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_stats_files(root: Path) -> Iterable[Path]:
    yield from root.rglob("*whole_archive_stats.json")


def _stage_timings_from_stats(stats: dict) -> Dict[str, float]:
    timings = stats.get("stage_timings_s")
    if not isinstance(timings, dict):
        archive_meta = stats.get("archive_meta")
        if isinstance(archive_meta, dict):
            timings = archive_meta.get("stage_timings_s")
    if not isinstance(timings, dict):
        return {}
    out: Dict[str, float] = {}
    for key, value in timings.items():
        if isinstance(value, (int, float)):
            out[str(key)] = float(value)
    return out


def _stage_group_values(timings: Dict[str, float]) -> Dict[str, float]:
    values: Dict[str, float] = {}
    consumed = set()
    total = float(timings.get("total_encode_file_s") or 0.0)
    if total <= 0.0:
        total = max(
            float(timings.get("parallel_ms1_child_s", 0.0)),
            float(timings.get("parallel_ms2_child_s", 0.0)),
        )
        total += float(timings.get("load_scans_s", 0.0)) + float(timings.get("pack_archive_s", 0.0))

    for group, keys in STAGE_GROUPS:
        value = 0.0
        for key in keys:
            if key in timings:
                value += float(timings[key])
                consumed.add(key)
        values[group] = value

    measured = sum(values.values())
    if total > 0.0:
        values["unattributed_or_overlap"] = max(0.0, total - measured)
        values["total_encode_file_s"] = total
    else:
        values["unattributed_or_overlap"] = 0.0
        values["total_encode_file_s"] = measured
    return values


def _internal_diagnostic_values(timings: Dict[str, float]) -> Dict[str, float]:
    values: Dict[str, float] = {}
    for prefix in INTERNAL_PREFIXES:
        for key, value in timings.items():
            if key.startswith(prefix) and key.endswith("_s") and isinstance(value, (int, float)):
                short_key = key[len(prefix) :]
                values[f"{prefix.rstrip('_')}_{short_key}"] = float(value)
    return values


def _write_csv(rows: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: List[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def summarize(root: Path, output_dir: Path) -> dict:
    per_file_rows: List[dict] = []
    raw_timing_rows: List[dict] = []
    aggregate: Dict[str, float] = {}
    aggregate_internal: Dict[str, float] = {}

    for path in sorted(_iter_stats_files(root)):
        stats = _read_json(path)
        if not stats:
            continue
        method = str(stats.get("method", ""))
        if method not in {"ours_whole_archive", "ours_strict_q6_whole_archive"}:
            continue
        timings = _stage_timings_from_stats(stats)
        if not timings:
            continue
        grouped = _stage_group_values(timings)
        internal = _internal_diagnostic_values(timings)
        total = float(grouped.get("total_encode_file_s", 0.0))
        row = {
            "file": stats.get("file", path.parent.parent.name),
            "method": method,
            "archive_variant": stats.get("archive_variant", ""),
            "stats_path": str(path),
            "total_encode_file_s": total,
        }
        for key, value in grouped.items():
            if key == "total_encode_file_s":
                continue
            row[f"{key}_s"] = value
            row[f"{key}_pct"] = (100.0 * value / total) if total > 0.0 else 0.0
            aggregate[key] = aggregate.get(key, 0.0) + value
        for key, value in internal.items():
            row[f"diagnostic_{key}_s"] = value
            row[f"diagnostic_{key}_pct_of_total"] = (100.0 * value / total) if total > 0.0 else 0.0
            aggregate_internal[key] = aggregate_internal.get(key, 0.0) + value
        aggregate["total_encode_file_s"] = aggregate.get("total_encode_file_s", 0.0) + total
        per_file_rows.append(row)

        for key, value in sorted(timings.items()):
            raw_timing_rows.append(
                {
                    "file": row["file"],
                    "method": method,
                    "archive_variant": row["archive_variant"],
                    "stage": key,
                    "seconds": value,
                }
            )

    total_all = float(aggregate.get("total_encode_file_s", 0.0))
    aggregate_rows = []
    for key, value in sorted(aggregate.items(), key=lambda item: item[0]):
        if key == "total_encode_file_s":
            continue
        aggregate_rows.append(
            {
                "stage_group": key,
                "seconds": value,
                "percent_of_total": (100.0 * value / total_all) if total_all > 0.0 else 0.0,
            }
        )
    aggregate_internal_rows = []
    for key, value in sorted(aggregate_internal.items(), key=lambda item: item[0]):
        aggregate_internal_rows.append(
            {
                "stage": key,
                "seconds": value,
                "percent_of_total_encode_basis": (100.0 * value / total_all) if total_all > 0.0 else 0.0,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(per_file_rows, output_dir / "stage_timing_per_file.csv")
    _write_csv(raw_timing_rows, output_dir / "stage_timing_raw_keys.csv")
    _write_csv(aggregate_rows, output_dir / "stage_timing_aggregate.csv")
    _write_csv(aggregate_internal_rows, output_dir / "stage_timing_internal_diagnostics.csv")

    summary = {
        "input_root": str(root),
        "output_dir": str(output_dir),
        "n_archive_runs_with_timings": len(per_file_rows),
        "total_encode_file_s": total_all,
        "aggregate": aggregate_rows,
        "aggregate_wall_time": aggregate_rows,
        "aggregate_internal_diagnostics": aggregate_internal_rows,
    }
    (output_dir / "stage_timing_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# TrackCodec Stage Timing Summary",
        "",
        f"- Input root: `{root}`",
        f"- Archive runs with timing: `{len(per_file_rows)}`",
        f"- Total encode wall time basis: `{total_all:.3f}s`",
        "",
        "## Aggregate Wall-Time Timing",
        "",
        "| Stage group | Seconds | Percent |",
        "|---|---:|---:|",
    ]
    for row in aggregate_rows:
        lines.append(
            f"| {row['stage_group']} | {float(row['seconds']):.3f} | {float(row['percent_of_total']):.2f}% |"
        )
    if aggregate_internal_rows:
        lines.extend(
            [
                "",
                "## Internal Diagnostics",
                "",
                "| Internal stage | Seconds | Percent of encode basis |",
                "|---|---:|---:|",
            ]
        )
        for row in aggregate_internal_rows:
            lines.append(
                f"| {row['stage']} | {float(row['seconds']):.3f} | {float(row['percent_of_total_encode_basis']):.2f}% |"
            )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- `unattributed_or_overlap` is expected when MS1 and MS2 child processes run in parallel, because child wall times overlap.",
            "- Internal diagnostics are not mutually exclusive wall-time groups; they can include summed per-section or per-worker timings.",
            "- Internal diagnostics appear only for archives generated after the profiler instrumentation was added.",
        ]
    )
    (output_dir / "stage_timing_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize TrackCodec whole-file stage timings.")
    parser.add_argument("--root", type=Path, required=True, help="Benchmark root or dataset root to scan.")
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    output_dir = args.output_dir or (args.root / "stage_timing_summary")
    summary = summarize(args.root, output_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
