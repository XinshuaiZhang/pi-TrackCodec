from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CURRENT_MANIFEST = ROOT / "outputs" / "stream_loss_current36" / "tables" / "pair_manifest.csv"
DEFAULT_OLD_ROOTS = [
    ROOT / "results",
    ROOT / "results" / "new15_validation",
    ROOT / "results" / "extra2_validation",
]


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _candidate_roots_for_row(row: pd.Series, fallback_roots: list[Path]) -> list[Path]:
    roots: list[Path] = []
    source_result_root = str(row.get("source_result_root", "")).strip()
    if source_result_root:
        preferred = (ROOT / source_result_root).resolve()
        roots.append(preferred)
    for root in fallback_roots:
        resolved = root.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _find_recon(index: int, roots: list[Path]) -> Path | None:
    rel = Path("recon") / f"{index:02d}.mzML"
    for root in roots:
        cand = root / rel
        if cand.exists():
            return cand
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a current36 manifest with resolved reconstructed mzML paths.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_CURRENT_MANIFEST)
    parser.add_argument("--old-root", type=Path, action="append", default=[])
    parser.add_argument("--out-csv", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    args = parser.parse_args()

    roots = args.old_root or DEFAULT_OLD_ROOTS
    df = pd.read_csv(args.manifest)
    rows: list[dict[str, Any]] = []
    md_lines = [
        "| # | file_name | original_path | archive_path | reconstructed_path |",
        "|---:|---|---|---|---|",
    ]
    for _, row in df.sort_values("index").iterrows():
        idx = int(row["index"])
        search_roots = _candidate_roots_for_row(row, roots)
        recon = _find_recon(idx, search_roots)
        out = {
            "index": idx,
            "dataset": row.get("dataset", ""),
            "file_name": row["file_name"],
            "original_path": row["original_path"],
            "archive_path": row["archive_path"],
            "reconstructed_path": "" if recon is None else str(recon),
            "reconstructed_exists": bool(recon is not None and recon.exists()),
        }
        rows.append(out)
        md_lines.append(
            f"| {idx} | `{row['file_name']}` | `{row['original_path']}` | `{row['archive_path']}` | `{'' if recon is None else recon}` |"
        )
    _write_csv(rows, args.out_csv)
    args.out_md.parent.mkdir(parents=True, exist_ok=True)
    args.out_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(args.out_csv)
    print(args.out_md)


if __name__ == "__main__":
    main()
