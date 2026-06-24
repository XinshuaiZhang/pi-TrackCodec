from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert pair_manifest.csv to a markdown path table accepted by validate_trackcodec_outputs.py.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--archive-column", default="archive_path")
    parser.add_argument("--original-column", default="original_path")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    df = pd.read_csv(args.manifest)
    lines = [
        "| # | file_name | original_path | archive_path |",
        "|---:|---|---|---|",
    ]
    for _, row in df.sort_values("index").iterrows():
        lines.append(
            f"| {int(row['index'])} | `{row['file_name']}` | `{row[args.original_column]}` | `{row[args.archive_column]}` |"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
