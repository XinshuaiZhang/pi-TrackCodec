from __future__ import annotations

import csv
import statistics
from pathlib import Path


def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fieldnames})


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _float_values(rows: list[dict], key: str) -> list[float]:
    out = []
    for row in rows:
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            continue
        if value_f > 0:
            out.append(value_f)
    return out


def aggregate_rows(rows: list[dict]) -> list[dict]:
    by_variant: dict[str, list[dict]] = {}
    variant_names: dict[str, str] = {}
    for row in rows:
        variant_id = str(row["variant_id"])
        by_variant.setdefault(variant_id, []).append(row)
        variant_names[variant_id] = str(row.get("variant_name", variant_id))

    full_ms1 = None
    full_ms2 = None
    if "A0" in by_variant:
        full_ms1_vals = _float_values(by_variant["A0"], "ms1_cr")
        full_ms2_vals = _float_values(by_variant["A0"], "ms2_cr")
        full_ms1 = statistics.mean(full_ms1_vals) if full_ms1_vals else None
        full_ms2 = statistics.mean(full_ms2_vals) if full_ms2_vals else None
    elif "B3" in by_variant:
        full_ms1_vals = _float_values(by_variant["B3"], "ms1_cr")
        full_ms2_vals = _float_values(by_variant["B3"], "ms2_cr")
        full_ms1 = statistics.mean(full_ms1_vals) if full_ms1_vals else None
        full_ms2 = statistics.mean(full_ms2_vals) if full_ms2_vals else None

    out = []
    for variant_id in sorted(by_variant):
        group = by_variant[variant_id]
        ms1_vals = _float_values(group, "ms1_cr")
        ms1_payload_only_vals = _float_values(group, "ms1_payload_only_cr")
        ms2_vals = _float_values(group, "ms2_cr")
        enc_vals = _float_values(group, "encode_time_s")
        dec_vals = _float_values(group, "decode_time_s")
        int_err_vals = [float(row.get("max_abs_delta_intensity") or 0.0) for row in group]
        ms1_valid_count = sum(str(row.get("ms1_roundtrip_cr_valid", "")).lower() == "true" for row in group)
        mean_ms1 = statistics.mean(ms1_vals) if ms1_vals else None
        mean_ms2 = statistics.mean(ms2_vals) if ms2_vals else None
        out.append(
            {
                "variant_id": variant_id,
                "variant_name": variant_names.get(variant_id, variant_id),
                "n_files": len({row["file"] for row in group}),
                "ms1_roundtrip_cr_valid_files": ms1_valid_count,
                "mean_ms1_cr": mean_ms1,
                "mean_ms1_payload_only_cr": statistics.mean(ms1_payload_only_vals) if ms1_payload_only_vals else None,
                "median_ms1_cr": statistics.median(ms1_vals) if ms1_vals else None,
                "mean_ms2_cr": mean_ms2,
                "median_ms2_cr": statistics.median(ms2_vals) if ms2_vals else None,
                "delta_vs_full_ms1_pct": ((mean_ms1 - full_ms1) / full_ms1 * 100.0) if mean_ms1 and full_ms1 else None,
                "delta_vs_full_ms2_pct": ((mean_ms2 - full_ms2) / full_ms2 * 100.0) if mean_ms2 and full_ms2 else None,
                "mean_encode_time_s": statistics.mean(enc_vals) if enc_vals else 0.0,
                "mean_decode_time_s": statistics.mean(dec_vals) if dec_vals else 0.0,
                "max_observed_delta_intensity": max(int_err_vals) if int_err_vals else 0.0,
            }
        )
    return out


def fmt_num(value, digits: int = 3) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)
