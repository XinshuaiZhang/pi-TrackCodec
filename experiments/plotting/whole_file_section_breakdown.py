from __future__ import annotations

import argparse
import csv
import json
import os
import struct
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


from TrackCodec.production.common.extensions import TRACKCODEC_ARCHIVE_EXTENSION
from TrackCodec.production.mzml.archive_codec import MAGIC
GROUP_ORDER = [
    "ms2_payload",
    "ms1_payload",
    "ms1_exact_sidecar",
    "metadata_payload",
    "auxiliary_payload",
    "misc_overhead",
]
GROUP_LABELS = {
    "ms2_payload": "MS2 payload",
    "ms1_payload": "MS1 payload",
    "ms1_exact_sidecar": "MS1 exact sidecar",
    "metadata_payload": "XML metadata",
    "auxiliary_payload": "Auxiliary / chromatogram",
    "misc_overhead": "Archive misc",
}
GROUP_COLORS = {
    "ms2_payload": "#E45756",
    "ms1_payload": "#4C78A8",
    "ms1_exact_sidecar": "#72B7B2",
    "metadata_payload": "#54A24B",
    "auxiliary_payload": "#F58518",
    "misc_overhead": "#B279A2",
}


def _file_kind(path: Path) -> str:
    name = path.name.upper()
    return "DIA" if "DIA" in name else "DDA"


def _read_archive_header(path: Path):
    with path.open("rb") as handle:
        prefix = handle.read(65536)
    if not prefix.startswith(MAGIC):
        raise ValueError(f"Invalid archive magic: {path}")
    header_len = struct.unpack("<I", prefix[len(MAGIC):len(MAGIC) + 4])[0]
    header_start = len(MAGIC) + 4
    header_end = header_start + header_len
    if header_end > len(prefix):
        with path.open("rb") as handle:
            prefix = handle.read(header_end)
    header = json.loads(prefix[header_start:header_end].decode("utf-8"))
    return header, header_end


def build_rows(archive_dir: Path) -> list[dict]:
    rows = []
    archives = sorted(archive_dir.glob(f"*{TRACKCODEC_ARCHIVE_EXTENSION}"))
    for idx, archive_path in enumerate(archives, start=1):
        header, header_end = _read_archive_header(archive_path)
        section_sizes = {item["name"]: int(item["size"]) for item in header}
        archive_size = int(archive_path.stat().st_size)
        misc = archive_size - sum(section_sizes.values())
        misc += sum(
            section_sizes.get(name, 0)
            for name in (
                "archive_meta",
                "ms1_meta",
                "ms1_array_starts_meta",
                "ms1_full_mz_meta",
                "ms2_meta",
                "metadata_meta",
                "auxiliary_meta",
            )
        )
        grouped = {
            "file": archive_path.name.removesuffix(TRACKCODEC_ARCHIVE_EXTENSION),
            "file_label": f"File_{idx}_{_file_kind(archive_path)}",
            "archive_bytes": archive_size,
            "ms2_payload": section_sizes.get("ms2_payload", 0),
            "ms1_payload": section_sizes.get("ms1_payload", 0),
            "ms1_exact_sidecar": section_sizes.get("ms1_array_starts_payload", 0) + section_sizes.get("ms1_full_mz_payload", 0),
            "metadata_payload": section_sizes.get("metadata_payload", 0),
            "auxiliary_payload": section_sizes.get("auxiliary_payload", 0),
            "misc_overhead": misc,
            "header_bytes": header_end,
        }
        for group in GROUP_ORDER:
            grouped[f"{group}_pct"] = grouped[group] / archive_size if archive_size else 0.0
        rows.append(grouped)
    return rows


def write_csv(rows: list[dict], output_path: Path) -> None:
    if not rows:
        return
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_stacked(rows: list[dict], output_path: Path) -> None:
    labels = [row["file_label"] for row in rows]
    x = np.arange(len(rows))
    bottom = np.zeros(len(rows), dtype=np.float64)

    fig, ax = plt.subplots(figsize=(10, 7))
    for group in GROUP_ORDER:
        values = np.asarray([row[f"{group}_pct"] * 100.0 for row in rows], dtype=np.float64)
        ax.bar(x, values, bottom=bottom, label=GROUP_LABELS[group], color=GROUP_COLORS[group], width=0.62)
        bottom += values

    ax.set_ylabel("% Of Compressed Archive Bytes", fontsize=14)
    ax.set_xlabel("Core5 Files", fontsize=14)
    ax.set_title("Whole-file Archive Section Breakdown", fontsize=16)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=0, fontsize=12)
    ax.tick_params(axis="y", labelsize=12)
    ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), fontsize=11, frameon=False)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot TrackCodec whole-file archive section breakdown.")
    parser.add_argument("--archive-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = build_rows(Path(args.archive_dir))
    write_csv(rows, output_dir / "whole_file_section_breakdown.csv")
    plot_stacked(rows, output_dir / "whole_file_section_breakdown_pct_stacked.png")
    (output_dir / "whole_file_section_breakdown_summary.json").write_text(
        json.dumps(rows, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"file_count": len(rows), "output_dir": str(output_dir)}, indent=2))


if __name__ == "__main__":
    main()
