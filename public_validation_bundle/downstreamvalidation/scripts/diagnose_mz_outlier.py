from __future__ import annotations

import argparse
import csv
import json
import sys
from itertools import zip_longest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from validate_trackcodec_outputs import _iter_spectra_lxml  # noqa: E402


def diagnose_pair(original: Path, reconstructed: Path, *, top_n: int, threshold: float) -> dict:
    top_rows: list[dict] = []
    threshold_rows: list[dict] = []
    spectrum_count = 0
    spectra_over_threshold = 0
    ms_level_over_threshold: dict[str, int] = {}
    max_abs_mz_error = 0.0

    for spectrum_count, (orig, recon) in enumerate(
        zip_longest(_iter_spectra_lxml(original), _iter_spectra_lxml(reconstructed)),
        start=1,
    ):
        if orig is None or recon is None:
            continue
        n_cmp = min(len(orig["mz_array"]), len(recon["mz_array"]))
        if n_cmp <= 0:
            continue
        diff = np.abs(orig["mz_array"][:n_cmp] - recon["mz_array"][:n_cmp])
        local_max = float(diff.max(initial=0.0))
        if local_max <= 0.0:
            continue
        point_index = int(np.argmax(diff))
        row = {
            "spectrum_index": int(spectrum_count),
            "spectrum_id": str(orig.get("id", "")),
            "ms_level": int(orig.get("ms_level", -1)),
            "rt": float(orig.get("rt", 0.0)),
            "n_points": int(n_cmp),
            "point_index": int(point_index),
            "original_mz": float(orig["mz_array"][point_index]),
            "reconstructed_mz": float(recon["mz_array"][point_index]),
            "abs_mz_error": float(local_max),
            "original_intensity": float(orig["intensity_array"][point_index]),
            "reconstructed_intensity": float(recon["intensity_array"][point_index]),
        }
        max_abs_mz_error = max(max_abs_mz_error, local_max)
        if local_max >= threshold:
            spectra_over_threshold += 1
            level = str(row["ms_level"])
            ms_level_over_threshold[level] = ms_level_over_threshold.get(level, 0) + 1
            threshold_rows.append(row)
        top_rows.append(row)
        top_rows.sort(key=lambda item: item["abs_mz_error"], reverse=True)
        del top_rows[top_n:]

    return {
        "original": str(original),
        "reconstructed": str(reconstructed),
        "spectrum_count": int(spectrum_count),
        "max_abs_mz_error": float(max_abs_mz_error),
        "threshold": float(threshold),
        "spectra_over_threshold": int(spectra_over_threshold),
        "ms_level_over_threshold": ms_level_over_threshold,
        "top_rows": top_rows,
        "threshold_rows": threshold_rows,
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Locate extreme m/z outlier points between original and reconstructed mzML files.")
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--reconstructed", type=Path, required=True)
    parser.add_argument("--out-prefix", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--threshold", type=float, default=1.0)
    args = parser.parse_args()

    result = diagnose_pair(args.original, args.reconstructed, top_n=args.top_n, threshold=args.threshold)
    args.out_prefix.parent.mkdir(parents=True, exist_ok=True)
    args.out_prefix.with_suffix(".json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    _write_csv(args.out_prefix.with_name(args.out_prefix.name + "_top.csv"), result["top_rows"])
    _write_csv(args.out_prefix.with_name(args.out_prefix.name + "_threshold.csv"), result["threshold_rows"])
    print(json.dumps({k: v for k, v in result.items() if k not in {"top_rows", "threshold_rows"}}, indent=2), flush=True)


if __name__ == "__main__":
    main()
