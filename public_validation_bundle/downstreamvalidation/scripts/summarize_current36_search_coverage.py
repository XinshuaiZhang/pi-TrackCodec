from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STREAM_ROOT = ROOT / "outputs" / "stream_loss_current36"
DEFAULT_SEARCH_FIG_ROOT = ROOT / "outputs" / "search_validation_figures"


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _modality(file_name: str) -> str:
    name = file_name.upper()
    if "DIA" in name or "AIF" in name:
        return "DIA/AIF"
    if "DDA" in name or "ETD" in name:
        return "DDA/ETD"
    return "other"


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize current36 stream/search validation coverage.")
    parser.add_argument("--stream-root", type=Path, default=DEFAULT_STREAM_ROOT)
    parser.add_argument("--search-figure-root", type=Path, default=DEFAULT_SEARCH_FIG_ROOT)
    args = parser.parse_args()

    manifest = pd.read_csv(args.stream_root / "tables" / "pair_manifest.csv")
    roundtrip = pd.read_csv(args.stream_root / "tables" / "roundtrip_summary.csv")
    xic = pd.read_csv(args.stream_root / "tables" / "xic_summary.csv")
    diann_path = args.search_figure_root / "tables" / "diann_search_summary_input.csv"
    dda_path = args.search_figure_root / "tables" / "dda_ionquant_enriched_pairwise_summary.csv"
    diann = pd.read_csv(diann_path) if diann_path.exists() and diann_path.stat().st_size else pd.DataFrame()
    dda = pd.read_csv(dda_path) if dda_path.exists() and dda_path.stat().st_size else pd.DataFrame()

    rt_ok = set(roundtrip.loc[roundtrip["roundtrip_pass"].astype(bool), "file_name"].astype(str))
    xic_ok = set(xic.loc[xic["status"].astype(str) == "ok", "file_name"].astype(str))
    diann_files = set(diann["file"].astype(str)) if not diann.empty else set()
    dda_files = set(dda["file"].astype(str)) if not dda.empty else set()

    rows: list[dict[str, Any]] = []
    for _, row in manifest.sort_values("index").iterrows():
        file_name = str(row["file_name"])
        modality = _modality(file_name)
        has_diann = file_name in diann_files
        has_dda = file_name in dda_files
        if has_diann or has_dda:
            note = "search validation available"
        elif modality == "DIA/AIF":
            note = "DIA/AIF stream-loss only in this run; no usable DIA-NN summary was produced"
        elif modality == "DDA/ETD":
            note = "DDA/ETD stream-loss only in this run; no usable IonQuant pairwise summary was produced"
        else:
            note = "stream-loss only; not a proteomics DDA/DIA search target"
        rows.append(
            {
                "index": int(row["index"]),
                "dataset": row.get("dataset", ""),
                "file_name": file_name,
                "modality": modality,
                "stream_roundtrip_pass": file_name in rt_ok,
                "stream_top500_xic_ok": file_name in xic_ok,
                "diann_summary": has_diann,
                "dda_ionquant_summary": has_dda,
                "search_note": note,
            }
        )
    out = args.search_figure_root / "tables" / "current36_search_coverage.csv"
    _write_csv(rows, out)
    print(out)


if __name__ == "__main__":
    main()
