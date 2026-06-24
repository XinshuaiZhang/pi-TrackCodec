from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ms1_ablation_fresh_common import (
    A0_VARIANT,
    A7_VARIANT,
    build_tracks_for_ablation,
    combine_row,
    empty_stats,
    infer_vendor,
    load_ms1_scans,
    run_ms1_variant,
    write_csv,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the MS1 intensity-codec isolation ablation for one mzML file. "
            "The script fresh-runs A0 and A7 on the same loaded MS1 scans and tracks."
        )
    )
    parser.add_argument("--input", type=Path, required=True, help="Input mzML file.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Per-file output directory.")
    parser.add_argument("--index", type=int, default=0, help="Optional file index written to the output table.")
    parser.add_argument("--dataset", default="", help="Optional dataset label.")
    parser.add_argument("--vendor", default="", help="Optional vendor label; inferred from file name if omitted.")
    parser.add_argument("--max-ms1-scans", type=int, default=0, help="Limit MS1 scans for smoke tests; 0 means all MS1 scans.")
    parser.add_argument("--mz-error-ceiling", type=float, default=5.1e-7, help="Expected maximum absolute m/z error.")
    parser.add_argument("--intensity-error-ceiling", type=float, default=0.050001, help="Expected maximum absolute intensity error.")
    return parser.parse_args()


def _append_run_log(path: Path, lines: list[str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    args = _parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_log = output_dir / "RUN_LOG.md"
    input_path = args.input.resolve()
    file_item = {
        "file": input_path.name,
        "path": str(input_path),
        "dataset": args.dataset,
        "vendor": args.vendor or infer_vendor(input_path),
    }
    if args.index:
        file_item["index"] = int(args.index)

    run_log.write_text(
        "\n".join(
            [
                "# MS1 Intensity Isolation Ablation Single-File Run",
                "",
                f"- started_at: `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
                f"- input: `{input_path}`",
                f"- output_dir: `{output_dir}`",
                "- variant_A0: `szdpd_xdelta_equalfidelity, zstd-9, mz precision 6`",
                "- variant_A7: `stackzdpd_passthrough, zstd-9, mz precision 6`",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    _append_run_log(run_log, [f"- stage: `load_ms1_start` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`"])
    loaded = load_ms1_scans(input_path, max_ms1_scans=int(args.max_ms1_scans))
    _append_run_log(
        run_log,
        [
            f"- stage: `load_ms1_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
            f"- ms1_scans: `{len(loaded.ms1_scans)}`",
            f"- raw_ms1_bytes: `{int(loaded.raw_ms1_bytes)}`",
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

    _append_run_log(run_log, [f"- stage: `A0_start` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`"])
    a0_ms1 = run_ms1_variant(loaded, tracks, A0_VARIANT)
    _append_run_log(
        run_log,
        [
            f"- stage: `A0_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
            f"- A0_ms1_cr: `{float(a0_ms1['compression_ratio']):.6f}`",
        ],
    )

    _append_run_log(run_log, [f"- stage: `A7_start` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`"])
    a7_ms1 = run_ms1_variant(loaded, tracks, A7_VARIANT)
    _append_run_log(
        run_log,
        [
            f"- stage: `A7_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
            f"- A7_ms1_cr: `{float(a7_ms1['compression_ratio']):.6f}`",
        ],
    )

    for label, result in (("A0", a0_ms1), ("A7", a7_ms1)):
        if float(result["max_abs_mz_error"]) > float(args.mz_error_ceiling):
            raise RuntimeError(f"{label} exceeded m/z error ceiling: {result['max_abs_mz_error']}")
        if float(result["max_abs_intensity_error"]) > float(args.intensity_error_ceiling):
            raise RuntimeError(f"{label} exceeded intensity error ceiling: {result['max_abs_intensity_error']}")

    rows = [
        combine_row(file_item, A0_VARIANT, a0_ms1),
        combine_row(file_item, A7_VARIANT, a7_ms1),
    ]
    a0_cr = float(a0_ms1["compression_ratio"] or 0.0)
    a7_cr = float(a7_ms1["compression_ratio"] or 0.0)
    delta_pct = ((a7_cr - a0_cr) / a0_cr * 100.0) if a0_cr else None
    for row in rows:
        row["dataset"] = args.dataset
        row["index"] = int(args.index)
        row["a7_vs_a0_ms1_delta_pct"] = delta_pct

    write_csv(rows, output_dir / "ms1_intensity_isolation_per_file.csv")
    summary = {
        "file": input_path.name,
        "input": str(input_path),
        "dataset": args.dataset,
        "vendor": file_item["vendor"],
        "index": int(args.index),
        "A0_ms1": a0_ms1,
        "A7_ms1": a7_ms1,
        "a7_vs_a0_ms1_delta_pct": delta_pct,
        "empty_ms2_stats": empty_stats(),
    }
    (output_dir / "ms1_intensity_isolation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _append_run_log(
        run_log,
        [
            f"- stage: `write_outputs_done` at `{time.strftime('%Y-%m-%dT%H:%M:%S%z')}`",
            f"- a7_vs_a0_ms1_delta_pct: `{delta_pct}`",
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
