from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any


CURRENT36: list[tuple[int, str, str]] = [
    (1, "data_stackzdpd", "File13_SA1.uncompressed.mzML"),
    (2, "data_stackzdpd", "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML"),
    (3, "data_stackzdpd", "File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML"),
    (4, "data_stackzdpd", "File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML"),
    (5, "data_stackzdpd", "File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML"),
    (6, "data_stackzdpd", "File2_20180722_L929_test_DDA_1.uncompressed.mzML"),
    (7, "data_stackzdpd", "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML"),
    (8, "data_stackzdpd", "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML"),
    (9, "data_stackzdpd", "File5_S8184TPST_01.uncompressed.mzML"),
    (10, "data_stackzdpd", "File6_Negative_000333.uncompressed.mzML"),
    (11, "data_stackzdpd", "LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML"),
    (12, "data_stackzdpd", "LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML"),
    (13, "data_stackzdpd", "Set 1_F2.uncompressed.mzML"),
    (14, "full8", "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML"),
    (15, "full8", "QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (16, "full8", "QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (17, "full8", "QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (18, "full8", "QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (19, "full8", "QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (20, "full8", "QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (21, "full8", "QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (22, "full8", "QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (23, "full8", "QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (24, "full8", "QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML"),
    (25, "full8", "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML"),
    (26, "full8", "QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML"),
    (27, "full8", "QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML"),
    (28, "full8", "QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML"),
    (29, "full8", "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML"),
    (30, "full8", "QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML"),
    (31, "full8", "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML"),
    (32, "full8", "QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML"),
    (33, "full8", "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML"),
    (34, "full8", "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML"),
    (35, "full8", "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML"),
    (36, "full8", "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML"),
]


def norm_name(value: str) -> str:
    name = Path(str(value)).name
    name = re.sub(r"^\d{1,3}_", "", name)
    return name.lower()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def filter_table(rows: list[dict[str, str]], file_col: str) -> tuple[list[dict[str, Any]], list[str]]:
    by_name = {norm_name(row[file_col]): row for row in rows if row.get(file_col)}
    out: list[dict[str, Any]] = []
    missing: list[str] = []
    for new_index, dataset, file_name in CURRENT36:
        row = by_name.get(norm_name(file_name))
        if row is None:
            missing.append(file_name)
            continue
        new_row = dict(row)
        new_row["index"] = new_index
        new_row["dataset"] = dataset
        new_row[file_col] = file_name
        out.append(new_row)
    return out, missing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in-root", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args()

    tables_in = args.in_root / "tables"
    tables_out = args.out_root / "tables"
    manifest_missing: list[str] = []
    summary: dict[str, Any] = {"in_root": str(args.in_root), "out_root": str(args.out_root), "tables": {}, "missing": {}}
    for table in ["decode_status.csv", "pair_manifest.csv", "roundtrip_summary.csv", "xic_summary.csv"]:
        rows, missing = filter_table(read_csv(tables_in / table), "file_name")
        write_csv(tables_out / table, rows)
        summary["tables"][table] = len(rows)
        summary["missing"][table] = missing
        if table == "pair_manifest.csv":
            manifest_missing = missing

    target_rows = read_csv(tables_in / "xic_target_metrics.csv")
    target_by_name: dict[str, list[dict[str, str]]] = {}
    for row in target_rows:
        target_by_name.setdefault(norm_name(row.get("file_name", "")), []).append(row)
    filtered_targets: list[dict[str, Any]] = []
    missing_targets: list[str] = []
    for new_index, dataset, file_name in CURRENT36:
        rows = target_by_name.get(norm_name(file_name))
        if not rows:
            missing_targets.append(file_name)
            continue
        for row in rows:
            new_row = dict(row)
            new_row["index"] = new_index
            new_row["dataset"] = dataset
            new_row["file_name"] = file_name
            filtered_targets.append(new_row)
    write_csv(tables_out / "xic_target_metrics.csv", filtered_targets)
    summary["tables"]["xic_target_metrics.csv"] = len(filtered_targets)
    summary["missing"]["xic_target_metrics.csv"] = missing_targets
    summary["complete_current36"] = not manifest_missing and not missing_targets
    args.out_root.mkdir(parents=True, exist_ok=True)
    (args.out_root / "current36_filter_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    main()
