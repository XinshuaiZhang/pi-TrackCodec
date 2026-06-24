from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


TABLES = [
    "decode_status.csv",
    "pair_manifest.csv",
    "roundtrip_summary.csv",
    "xic_summary.csv",
    "xic_target_metrics.csv",
]


def _read_table(root: Path, name: str) -> pd.DataFrame | None:
    path = root / "tables" / name
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["source_result_root"] = str(root)
    return df


def _dedup(df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    if df.empty:
        return df
    if table_name == "xic_target_metrics.csv":
        keys = [col for col in ["index", "file_name", "rank", "target_mz"] if col in df.columns]
        if keys:
            return df.drop_duplicates(subset=keys, keep="last").sort_values([col for col in ["index", "rank"] if col in df.columns])
        return df.drop_duplicates(keep="last")
    if "index" in df.columns and "file_name" in df.columns:
        return df.drop_duplicates(subset=["index", "file_name"], keep="last").sort_values("index")
    if "index" in df.columns:
        return df.drop_duplicates(subset=["index"], keep="last").sort_values("index")
    return df.drop_duplicates(keep="last")


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge old and new stream-loss validation tables.")
    parser.add_argument("--root", type=Path, action="append", required=True, help="Result root containing tables/*.csv; can be repeated.")
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()

    out_tables = args.out_root / "tables"
    out_tables.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for table in TABLES:
        frames = []
        for root in args.root:
            df = _read_table(root, table)
            if df is not None:
                frames.append(df)
        if not frames:
            continue
        merged = _dedup(pd.concat(frames, ignore_index=True), table)
        merged.to_csv(out_tables / table, index=False)
        written[table] = int(len(merged))

    summary = {
        "out_root": str(args.out_root),
        "roots": [str(root) for root in args.root],
        "tables": written,
    }
    (args.out_root / "merge_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
