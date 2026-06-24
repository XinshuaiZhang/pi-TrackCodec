from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ms1_ablation_fresh_common import (
    COMPONENT_VARIANTS,
    build_tracks_for_ablation,
    combine_component_row,
    infer_vendor,
    load_limited_scans,
    run_ms1_variant,
    run_ms2_variant,
    write_csv,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fresh-run TrackCodec MS1/MS2 component ablation A0-A6 for one mzML file.")
    parser.add_argument("--input", type=Path, required=True, help="Input mzML file.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Per-file output directory.")
    parser.add_argument("--index", type=int, default=0, help="Optional file index written to output rows.")
    parser.add_argument("--dataset", default="", help="Optional dataset label.")
    parser.add_argument("--vendor", default="", help="Optional vendor label; inferred from file name if omitted.")
    parser.add_argument("--variants", nargs="*", default=None, help="Optional subset, e.g. A0 A1 A3.")
    parser.add_argument("--max-ms1-scans", type=int, default=0, help="Limit MS1 scans for smoke tests; 0 means all MS1 scans.")
    parser.add_argument("--max-ms2-scans", type=int, default=0, help="Limit MS2 scans for smoke tests; 0 means all MS2 scans; -1 skips MS2.")
    return parser.parse_args()


def _append_run_log(path: Path, lines: list[str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _select_variants(names: list[str] | None):
    variants = list(COMPONENT_VARIANTS)
    if names:
        wanted = {name.upper() for name in names}
        variants = [variant for variant in variants if variant.variant_id.upper() in wanted]
    if not variants:
        raise ValueError("No component variants selected")
    return variants


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = args.input.resolve()
    variants = _select_variants(args.variants)
    file_item = {
        "file": input_path.name,
        "path": str(input_path),
        "dataset": args.dataset,
        "vendor": args.vendor or infer_vendor(input_path),
        "index": int(args.index),
    }
    run_log = output_dir / "RUN_LOG.md"
    run_log.write_text(
        "\n".join(
            [
                "# TrackCodec Component Ablation Single-File Run",
                "",
                f"- started_at: `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
                f"- input: `{input_path}`",
                f"- output_dir: `{output_dir}`",
                f"- variants: `{','.join(variant.variant_id for variant in variants)}`",
                f"- max_ms1_scans: `{int(args.max_ms1_scans)}`",
                f"- max_ms2_scans: `{int(args.max_ms2_scans)}`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _append_run_log(run_log, [f"- stage: `load_scans_start` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`"])
    loaded = load_limited_scans(
        input_path,
        max_ms1_scans=int(args.max_ms1_scans),
        max_ms2_scans=int(args.max_ms2_scans),
    )
    _append_run_log(
        run_log,
        [
            f"- stage: `load_scans_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
            f"- ms1_scans: `{len(loaded.ms1_scans)}`",
            f"- dia_windows: `{len(loaded.ms2_by_window)}`",
            f"- dda_ms2_scans: `{len(loaded.dda_ms2_scans)}`",
            f"- raw_ms1_bytes: `{int(loaded.raw_ms1_bytes)}`",
            f"- raw_ms2_bytes: `{int(loaded.raw_ms2_bytes)}`",
        ],
    )

    _append_run_log(run_log, [f"- stage: `build_tracks_start` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`"])
    tracks = build_tracks_for_ablation(loaded.ms1_scans)
    _append_run_log(
        run_log,
        [
            f"- stage: `build_tracks_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
            f"- track_storage: `{type(tracks).__name__}`",
            f"- track_count: `{len(tracks)}`",
        ],
    )

    rows = []
    details = {}
    ms2_cache: dict[tuple, dict] = {}
    for variant in variants:
        _append_run_log(run_log, [f"- stage: `{variant.variant_id}_start` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`"])
        ms1 = run_ms1_variant(loaded, tracks, variant)
        ms2 = run_ms2_variant(loaded, variant, ms2_cache)
        row = combine_component_row(file_item, variant, ms1, ms2)
        rows.append(row)
        details[variant.variant_id] = {"ms1": ms1, "ms2": ms2}
        write_csv(rows, output_dir / "component_ablation_per_file.csv")
        _append_run_log(
            run_log,
            [
                f"- stage: `{variant.variant_id}_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
                f"  - ms1_cr: `{row.get('ms1_cr')}`",
                f"  - ms2_cr: `{row.get('ms2_cr')}`",
            ],
        )

    summary = {
        "file": input_path.name,
        "input": str(input_path),
        "dataset": args.dataset,
        "vendor": file_item["vendor"],
        "index": int(args.index),
        "variants": [variant.variant_id for variant in variants],
        "details": details,
    }
    (output_dir / "component_ablation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    write_csv(rows, output_dir / "component_ablation_per_file.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
