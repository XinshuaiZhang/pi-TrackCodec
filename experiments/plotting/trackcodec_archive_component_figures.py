from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ORIG_COLORS = {
    "chromatogram_list": "#F58518",
    "remaining_mzml": "#4C78A8",
}

ARCHIVE_COLORS = {
    "ms1": "#4C78A8",
    "ms2": "#E45756",
    "metadata": "#54A24B",
    "auxiliary": "#F58518",
    "ms1_full_mz": "#72B7B2",
}


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _plot_single_stacked_bar(
    rows: list[dict],
    label: str,
    title: str,
    ylabel: str,
    output_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.8))
    bottom = 0.0
    x = [0]
    for row in rows:
        value_mb = row["bytes"] / (1024.0 * 1024.0)
        ax.bar(
            x,
            [value_mb],
            bottom=[bottom],
            color=row["color_hex"],
            width=0.58,
            label=row["component_label"],
        )
        text_y = bottom + value_mb / 2.0
        ax.text(
            0,
            text_y,
            f"{row['component_label']}\n{value_mb:.2f} MB\n{row['pct_of_total'] * 100:.2f}%",
            ha="center",
            va="center",
            fontsize=13,
            color="black",
        )
        bottom += value_mb

    ax.set_xlim(-0.75, 0.75)
    ax.set_xticks([0])
    ax.set_xticklabels([label], fontsize=13)
    ax.set_ylabel(ylabel, fontsize=14)
    ax.set_title(title, fontsize=16)
    ax.tick_params(axis="y", labelsize=12)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=12)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def build_original_rows(chrom_summary: dict) -> list[dict]:
    total = int(chrom_summary["whole_file_bytes"])
    chrom_bytes = int(chrom_summary["chromatogram_list_bytes"])
    remaining = total - chrom_bytes
    rows = [
        {
            "component": "chromatogram_list",
            "component_label": "chromatogramList",
            "bytes": chrom_bytes,
            "pct_of_total": chrom_bytes / total if total else 0.0,
            "color_hex": ORIG_COLORS["chromatogram_list"],
        },
        {
            "component": "remaining_mzml",
            "component_label": "remaining mzML",
            "bytes": remaining,
            "pct_of_total": remaining / total if total else 0.0,
            "color_hex": ORIG_COLORS["remaining_mzml"],
        },
    ]
    return rows


def build_archive_rows_from_smoke(smoke_summary: dict) -> tuple[list[dict], int, float, str]:
    artifact_rows = smoke_summary.get("artifact_rows", [])
    archive_artifact = next((row for row in artifact_rows if row.get("artifact") == "trackcodec_archive"), None)
    if archive_artifact is None:
        raise KeyError("trackcodec_archive artifact not found in smoke_summary['artifact_rows']")
    total = int(archive_artifact["bytes"])
    archive_meta = smoke_summary["archive_meta"]
    rows = []
    for key, label in (
        ("ms1", "MS1"),
        ("ms2", "MS2"),
        ("metadata", "metadata"),
        ("auxiliary", "auxiliary"),
        ("ms1_full_mz", "MS1 full m/z sidecar"),
    ):
        value = int(archive_meta[key]["compressed_bytes"])
        rows.append(
            {
                "component": key,
                "component_label": label,
                "bytes": value,
                "pct_of_total": value / total if total else 0.0,
                "color_hex": ARCHIVE_COLORS[key],
            }
        )
    return rows, total, float(archive_artifact["compression_ratio_vs_input"]), str(smoke_summary["input_file"])


def build_archive_rows_from_whole_stats(whole_stats: dict) -> tuple[list[dict], int, float, str]:
    total = int(whole_stats["compressed_bytes"])
    rows = []
    for key, label, field in (
        ("ms1", "MS1", "ms1_compressed_bytes"),
        ("ms2", "MS2", "ms2_compressed_bytes"),
        ("metadata", "metadata", "metadata_compressed_bytes"),
        ("auxiliary", "auxiliary", "auxiliary_compressed_bytes"),
        ("ms1_full_mz", "MS1 full m/z sidecar", "ms1_full_mz_compressed_bytes"),
    ):
        value = int(whole_stats[field])
        rows.append(
            {
                "component": key,
                "component_label": label,
                "bytes": value,
                "pct_of_total": value / total if total else 0.0,
                "color_hex": ARCHIVE_COLORS[key],
            }
        )
    return rows, total, float(whole_stats["compression_ratio"]), str(whole_stats["file"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot original mini64 and archive composition figures.")
    parser.add_argument("--chrom-summary-json", required=True)
    parser.add_argument("--smoke-summary-json")
    parser.add_argument("--whole-stats-json")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    chrom_summary = json.loads(Path(args.chrom_summary_json).read_text(encoding="utf-8"))
    if bool(args.smoke_summary_json) == bool(args.whole_stats_json):
        raise ValueError("Provide exactly one of --smoke-summary-json or --whole-stats-json")

    original_rows = build_original_rows(chrom_summary)
    if args.smoke_summary_json:
        smoke_summary = json.loads(Path(args.smoke_summary_json).read_text(encoding="utf-8"))
        archive_rows, archive_bytes, archive_cr, archive_input_file = build_archive_rows_from_smoke(smoke_summary)
    else:
        whole_stats = json.loads(Path(args.whole_stats_json).read_text(encoding="utf-8"))
        archive_rows, archive_bytes, archive_cr, archive_input_file = build_archive_rows_from_whole_stats(whole_stats)

    _write_csv(original_rows, output_dir / "plot_data" / "original_composition.csv")
    _write_csv(archive_rows, output_dir / "plot_data" / "archive_composition.csv")

    _plot_single_stacked_bar(
        original_rows,
        label="original mzML",
        title="Original Whole-file Composition",
        ylabel="Size (MB)",
        output_path=output_dir / "plots" / "original_wholefile_composition.png",
    )
    _plot_single_stacked_bar(
        archive_rows,
        label="TrackCodec-Archive",
        title="Compressed TrackCodec-Archive Composition",
        ylabel="Compressed Size (MB)",
        output_path=output_dir / "plots" / "archive_compressed_composition.png",
    )

    summary = {
        "original_file": chrom_summary["file"],
        "original_whole_file_bytes": int(chrom_summary["whole_file_bytes"]),
        "original_chromatogram_list_bytes": int(chrom_summary["chromatogram_list_bytes"]),
        "original_chromatogram_list_pct": float(chrom_summary["chromatogram_list_pct_of_whole_file"]),
        "original_sum_raw_chromatogram_blob_bytes": int(chrom_summary["sum_raw_blob_bytes"]),
        "archive_input_file": archive_input_file,
        "archive_bytes": archive_bytes,
        "archive_compression_ratio_vs_input": archive_cr,
        "output_plots": {
            "original": str(output_dir / "plots" / "original_wholefile_composition.png"),
            "archive": str(output_dir / "plots" / "archive_compressed_composition.png"),
        },
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
