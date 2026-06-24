from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_TABLE_DIR = SCRIPT_DIR.parent / "inputs"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR.parent / "outputs" / "ms2_ablation_unified30"
PAPER_BLUE = "#355c7d"
PAPER_PINK = "#f67280"
UNIFIED30_FILES = {
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML",
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML",
    "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML",
    "File5_S8184TPST_01.uncompressed.mzML",
    "File6_Negative_000333.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML",
    "QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML",
    "QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML",
    "QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML",
    "QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML",
    "QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML",
    "QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML",
    "QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML",
    "Set 1_F2.uncompressed.mzML",
}
UNIFIED30_ORDER = [
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML",
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML",
    "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML",
    "File5_S8184TPST_01.uncompressed.mzML",
    "File6_Negative_000333.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML",
    "QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML",
    "QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML",
    "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML",
    "QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML",
    "QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML",
    "QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML",
    "QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML",
    "QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML",
    "QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML",
    "Set 1_F2.uncompressed.mzML",
]


def _to_int(value) -> int:
    if value in (None, "", "nan"):
        return 0
    return int(round(float(value)))


def _to_float(value) -> float:
    if value in (None, "", "nan"):
        return 0.0
    return float(value)


def _safe_ratio(num: float, den: float) -> float | None:
    if den <= 0:
        return None
    return float(num) / float(den)


def read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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


def fmt_num(value, digits: int = 3) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _get_plt():
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42

    import matplotlib.pyplot as plt

    return plt


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _betacf(a: float, b: float, x: float) -> float:
    max_iter = 200
    eps = 3.0e-14
    fpmin = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < fpmin:
        d = fpmin
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < fpmin:
            d = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


def _regularized_incomplete_beta(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    bt = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _student_t_cdf(t: float, df: float) -> float:
    if df <= 0:
        return float("nan")
    x = df / (df + t * t)
    ib = _regularized_incomplete_beta(df / 2.0, 0.5, x)
    if t >= 0:
        return 1.0 - 0.5 * ib
    return 0.5 * ib


def _paired_ttest_pvalue(before: list[float], after: list[float]) -> float | None:
    if len(before) != len(after) or len(before) < 2:
        return None
    diffs = [float(b) - float(a) for b, a in zip(before, after)]
    n = len(diffs)
    mean_diff = sum(diffs) / n
    if n < 2:
        return None
    var = sum((d - mean_diff) ** 2 for d in diffs) / (n - 1)
    if var <= 0:
        return 0.0
    se = math.sqrt(var / n)
    if se == 0:
        return 0.0
    t_stat = mean_diff / se
    df = n - 1
    p = 2.0 * (1.0 - _student_t_cdf(abs(t_stat), df))
    return max(0.0, min(1.0, p))


def _median(values: list[float]) -> float:
    ordered = sorted(float(v) for v in values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return 0.5 * (ordered[mid - 1] + ordered[mid])


def _load_trackcodec_sections(path: Path) -> dict[str, dict]:
    rows = read_csv(path)
    out: dict[str, dict] = {}
    for row in rows:
        if str(row.get("section", "")).upper() != "MS2":
            continue
        file_name = str(row["file"])
        out[file_name] = {
            "file": file_name,
            "file_label": row.get("label") or row.get("file_label") or file_name,
            "ms2_raw_bytes": _to_int(row.get("raw_bytes")),
            "tc_ms2_compressed_bytes": _to_int(row.get("compressed_bytes")),
        }
    return out


def _load_zdpd_sections(path: Path) -> dict[str, dict]:
    rows = read_csv(path)
    out: dict[str, dict] = {}
    for row in rows:
        if str(row.get("section", "")).upper() != "MS2":
            continue
        file_name = str(row["file"])
        out[file_name] = {
            "file": file_name,
            "file_label": row.get("file_label") or row.get("label") or file_name,
            "zdpd_ms2_compressed_bytes": _to_int(row.get("compressed_bytes")),
        }
    return out


def _load_trackcodec_archives(path: Path) -> dict[str, dict]:
    rows = read_csv(path)
    out: dict[str, dict] = {}
    for row in rows:
        if str(row.get("method", "")) != "trackcodec":
            continue
        file_name = str(row["file"])
        out[file_name] = {
            "file": file_name,
            "file_label": row.get("combined_file_label") or row.get("file_label") or file_name,
            "input_path": row.get("input_path") or "",
            "mzml_bytes": _to_int(row.get("raw_bytes")),
            "b0_archive_bytes": _to_int(row.get("compressed_bytes")),
            "b0_archive_cr": _to_float(row.get("compression_ratio")),
            "dataset": row.get("dataset") or "",
            "dataset_key": row.get("dataset_key") or "",
        }
    return out


def build_ms2_ablation_rows(
    trackcodec_sections: dict[str, dict],
    zdpd_sections: dict[str, dict],
    trackcodec_archives: dict[str, dict],
) -> tuple[list[dict], dict]:
    all_files = [file_name for file_name in UNIFIED30_ORDER if file_name in (set(trackcodec_archives) | set(trackcodec_sections) | set(zdpd_sections))]
    extra_files = sorted((set(trackcodec_archives) | set(trackcodec_sections) | set(zdpd_sections)) - set(all_files))
    all_files.extend(extra_files)
    rows: list[dict] = []

    sum_ms2_raw = 0
    sum_tc_ms2 = 0
    sum_zdpd_ms2 = 0
    sum_b0_archive = 0
    sum_b1_archive = 0
    sum_mzml = 0
    n_ms2_bearing = 0
    file_index_map = {file_name: idx + 1 for idx, file_name in enumerate(all_files)}

    for file_name in all_files:
        sec_tc = trackcodec_sections.get(file_name, {})
        sec_zd = zdpd_sections.get(file_name, {})
        arc = trackcodec_archives.get(file_name, {})
        file_index = file_index_map.get(file_name, 0)

        ms2_raw_bytes = _to_int(sec_tc.get("ms2_raw_bytes"))
        tc_ms2_compressed_bytes = _to_int(sec_tc.get("tc_ms2_compressed_bytes"))
        zdpd_ms2_compressed_bytes = _to_int(sec_zd.get("zdpd_ms2_compressed_bytes"))
        b0_archive_bytes = _to_int(arc.get("b0_archive_bytes"))
        mzml_bytes = _to_int(arc.get("mzml_bytes"))
        if ms2_raw_bytes <= 0:
            b1_archive_bytes = b0_archive_bytes
            tc_ms2_compressed_bytes = 0
            zdpd_ms2_compressed_bytes = 0
        else:
            b1_archive_bytes = b0_archive_bytes - tc_ms2_compressed_bytes + zdpd_ms2_compressed_bytes

        b0_ms2_cr = _safe_ratio(ms2_raw_bytes, tc_ms2_compressed_bytes)
        b1_ms2_cr = _safe_ratio(ms2_raw_bytes, zdpd_ms2_compressed_bytes)
        b0_archive_cr = _safe_ratio(mzml_bytes, b0_archive_bytes)
        b1_archive_cr = _safe_ratio(mzml_bytes, b1_archive_bytes)
        delta_archive_pct = None
        if b0_archive_bytes > 0:
            delta_archive_pct = (float(b1_archive_bytes) / float(b0_archive_bytes) - 1.0) * 100.0

        if ms2_raw_bytes > 0:
            n_ms2_bearing += 1
            sum_ms2_raw += ms2_raw_bytes
            sum_tc_ms2 += tc_ms2_compressed_bytes
            sum_zdpd_ms2 += zdpd_ms2_compressed_bytes
        sum_b0_archive += b0_archive_bytes
        sum_b1_archive += b1_archive_bytes
        sum_mzml += mzml_bytes

        rows.append(
            {
                "file": file_name,
                "file_index": file_index,
                "file_short": f"File{file_index}" if file_index else file_name,
                "file_label": arc.get("file_label") or sec_tc.get("file_label") or sec_zd.get("file_label") or file_name,
                "dataset": arc.get("dataset") or "",
                "dataset_key": arc.get("dataset_key") or "",
                "input_path": arc.get("input_path") or "",
                "ms2_raw_bytes": ms2_raw_bytes,
                "tc_ms2_compressed_bytes": tc_ms2_compressed_bytes,
                "zdpd_ms2_compressed_bytes": zdpd_ms2_compressed_bytes,
                "b0_archive_bytes": b0_archive_bytes,
                "b1_archive_bytes": b1_archive_bytes,
                "mzml_bytes": mzml_bytes,
                "b0_ms2_cr": "" if b0_ms2_cr is None else b0_ms2_cr,
                "b1_ms2_cr": "" if b1_ms2_cr is None else b1_ms2_cr,
                "b0_archive_cr": "" if b0_archive_cr is None else b0_archive_cr,
                "b1_archive_cr": "" if b1_archive_cr is None else b1_archive_cr,
                "delta_archive_pct": "" if delta_archive_pct is None else delta_archive_pct,
            }
        )

    b0_ms2_aggregate_cr = _safe_ratio(sum_ms2_raw, sum_tc_ms2)
    b1_ms2_aggregate_cr = _safe_ratio(sum_ms2_raw, sum_zdpd_ms2)
    b0_archive_aggregate_cr = _safe_ratio(sum_mzml, sum_b0_archive)
    b1_archive_aggregate_cr = _safe_ratio(sum_mzml, sum_b1_archive)
    delta_archive_size_pct = None
    if sum_b0_archive > 0:
        delta_archive_size_pct = (float(sum_b1_archive) / float(sum_b0_archive) - 1.0) * 100.0
    ms2_dict_contribution_pct = None
    if b0_ms2_aggregate_cr and b1_ms2_aggregate_cr:
        ms2_dict_contribution_pct = (float(b0_ms2_aggregate_cr) / float(b1_ms2_aggregate_cr) - 1.0) * 100.0

    rows.append(
        {
            "file": "AGGREGATE",
            "file_index": "",
            "file_short": "AGGREGATE",
            "file_label": "AGGREGATE",
            "dataset": "",
            "dataset_key": "",
            "input_path": "",
            "ms2_raw_bytes": sum_ms2_raw,
            "tc_ms2_compressed_bytes": sum_tc_ms2,
            "zdpd_ms2_compressed_bytes": sum_zdpd_ms2,
            "b0_archive_bytes": sum_b0_archive,
            "b1_archive_bytes": sum_b1_archive,
            "mzml_bytes": sum_mzml,
            "b0_ms2_cr": "" if b0_ms2_aggregate_cr is None else b0_ms2_aggregate_cr,
            "b1_ms2_cr": "" if b1_ms2_aggregate_cr is None else b1_ms2_aggregate_cr,
            "b0_archive_cr": "" if b0_archive_aggregate_cr is None else b0_archive_aggregate_cr,
            "b1_archive_cr": "" if b1_archive_aggregate_cr is None else b1_archive_aggregate_cr,
            "delta_archive_pct": "" if delta_archive_size_pct is None else delta_archive_size_pct,
        }
    )

    summary = {
        "n_files_total": len(all_files),
        "n_files_ms2_bearing": n_ms2_bearing,
        "b0_ms2_aggregate_cr": b0_ms2_aggregate_cr,
        "b1_ms2_aggregate_cr": b1_ms2_aggregate_cr,
        "b0_archive_aggregate_cr": b0_archive_aggregate_cr,
        "b1_archive_aggregate_cr": b1_archive_aggregate_cr,
        "delta_archive_size_pct": delta_archive_size_pct,
        "ms2_dict_contribution_pct": ms2_dict_contribution_pct,
        "sum_ms2_raw_bytes": sum_ms2_raw,
        "sum_tc_ms2_compressed_bytes": sum_tc_ms2,
        "sum_zdpd_ms2_compressed_bytes": sum_zdpd_ms2,
        "sum_b0_archive_bytes": sum_b0_archive,
        "sum_b1_archive_bytes": sum_b1_archive,
        "sum_mzml_bytes": sum_mzml,
    }
    return rows, summary


def _summary_markdown(summary: dict) -> str:
    return "\n".join(
        [
            "# MS2 Ablation B1 Summary",
            "",
            f"- Files total: {summary['n_files_total']}",
            f"- MS2-bearing files: {summary['n_files_ms2_bearing']}",
            f"- B0 MS2 aggregate CR: {fmt_num(summary['b0_ms2_aggregate_cr'])}x",
            f"- B1 MS2 aggregate CR: {fmt_num(summary['b1_ms2_aggregate_cr'])}x",
            f"- B0 archive aggregate CR: {fmt_num(summary['b0_archive_aggregate_cr'])}x",
            f"- B1 archive aggregate CR: {fmt_num(summary['b1_archive_aggregate_cr'])}x",
            f"- Archive size delta vs B0: +{fmt_num(summary['delta_archive_size_pct'])}%",
            f"- MS2 dict contribution: +{fmt_num(summary['ms2_dict_contribution_pct'])}%",
            "",
            "Paper row:",
            "",
            "```latex",
            f"B1 & w/o MS2 dict (ZDPD passthrough) & {fmt_num(summary['b1_ms2_aggregate_cr'], 2)}$\\\\times$ & $+{fmt_num(summary['delta_archive_size_pct'], 2)}\\\\%$ & 0.05 \\\\",
            "```",
            "",
        ]
    )


def _save_mpl_figure(fig, out_base: Path, *, dpi: int = 220) -> None:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "png", "pdf"):
        save_kwargs = {"bbox_inches": "tight"}
        if ext == "png":
            save_kwargs["dpi"] = dpi
        fig.savefig(out_base.with_suffix(f".{ext}"), **save_kwargs)
    fig.clf()


def _plot_summary_cr(summary: dict, out_base: Path) -> None:
    plt = _get_plt()
    labels = ["MS2 section CR", "Whole-archive CR"]
    b0 = [float(summary["b0_ms2_aggregate_cr"] or 0.0), float(summary["b0_archive_aggregate_cr"] or 0.0)]
    b1 = [float(summary["b1_ms2_aggregate_cr"] or 0.0), float(summary["b1_archive_aggregate_cr"] or 0.0)]
    y = [1, 0]
    bar_h = 0.34
    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.barh([v + bar_h / 2 for v in y], b0, height=bar_h, color=PAPER_BLUE, label="B0 Full MS2 dict")
    ax.barh([v - bar_h / 2 for v in y], b1, height=bar_h, color=PAPER_PINK, label="B1 ZDPD passthrough")
    for yy, val in zip([v + bar_h / 2 for v in y], b0):
        ax.text(val + 0.06, yy, f"{val:.2f}x", va="center", ha="left", fontsize=10)
    for yy, val in zip([v - bar_h / 2 for v in y], b1):
        ax.text(val + 0.06, yy, f"{val:.2f}x", va="center", ha="left", fontsize=10)
    ax.set_yticks(y, labels)
    ax.set_xlabel("Compression ratio (x)")
    ax.set_xlim(0, max(b0 + b1) * 1.18)
    ax.grid(axis="x", color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="lower right")
    fig.tight_layout()
    _save_mpl_figure(fig, out_base)
    plt.close(fig)


def _plot_per_file_archive_delta(rows: list[dict], out_base: Path) -> None:
    plt = _get_plt()
    items = []
    for row in rows:
        if row["file"] == "AGGREGATE":
            continue
        delta = row.get("delta_archive_pct")
        if delta in (None, ""):
            continue
        items.append((str(row["file_label"]), float(delta)))
    items.sort(key=lambda item: item[1], reverse=True)
    labels = [item[0] for item in items]
    values = [item[1] for item in items]
    ypos = list(range(len(labels)))
    fig_h = max(8.6, 0.28 * len(labels) + 1.6)
    fig, ax = plt.subplots(figsize=(9.2, fig_h))
    ax.barh(ypos, values, color=PAPER_PINK, edgecolor="none")
    ax.set_yticks(ypos, labels)
    ax.invert_yaxis()
    for yv, val in zip(ypos, values):
        ax.text(val + 0.2, yv, f"{val:.1f}%", va="center", ha="left", fontsize=8)
    ax.set_xlabel("Archive size increase vs B0 (%)")
    ax.set_xlim(0, max(values) * 1.16 if values else 1.0)
    ax.grid(axis="x", color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    fig.tight_layout()
    _save_mpl_figure(fig, out_base)
    plt.close(fig)


def _plot_ms2_cr_scatter(rows: list[dict], out_base: Path) -> None:
    plt = _get_plt()
    x_vals = []
    y_vals = []
    labels = []
    for row in rows:
        if row["file"] == "AGGREGATE":
            continue
        b0 = row.get("b0_ms2_cr")
        b1 = row.get("b1_ms2_cr")
        if b0 in (None, "") or b1 in (None, ""):
            continue
        x_vals.append(float(b0))
        y_vals.append(float(b1))
        labels.append(str(row["file_label"]))
    max_v = max(x_vals + y_vals) * 1.08 if x_vals or y_vals else 1.0
    fig, ax = plt.subplots(figsize=(5.9, 5.4))
    ax.scatter(x_vals, y_vals, s=38, color=PAPER_BLUE, alpha=0.88)
    ax.plot([0, max_v], [0, max_v], linestyle="--", linewidth=1.0, color="#9ca3af")
    if x_vals and y_vals:
        top_gap = sorted(zip(labels, x_vals, y_vals), key=lambda item: item[1] - item[2], reverse=True)[:4]
        for label, xv, yv in top_gap:
            ax.text(xv + 0.04, yv - 0.03, label, fontsize=8, color="#374151")
    ax.set_xlabel("B0 MS2 section CR (TrackCodec dict)")
    ax.set_ylabel("B1 MS2 section CR (ZDPD passthrough)")
    ax.set_xlim(0, max_v)
    ax.set_ylim(0, max_v)
    ax.grid(color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    fig.tight_layout()
    _save_mpl_figure(fig, out_base)
    plt.close(fig)


def _plot_aggregate_archive_bytes(summary: dict, out_base: Path) -> None:
    plt = _get_plt()
    b0 = float(summary["sum_b0_archive_bytes"] or 0.0) / (1024 ** 3)
    b1 = float(summary["sum_b1_archive_bytes"] or 0.0) / (1024 ** 3)
    fig, ax = plt.subplots(figsize=(4.8, 4.3))
    bars = ax.bar(["B0", "B1"], [b0, b1], color=[PAPER_BLUE, PAPER_PINK], width=0.58)
    for bar, val in zip(bars, [b0, b1]):
        ax.text(bar.get_x() + bar.get_width() / 2, val + max(b0, b1) * 0.015, f"{val:.2f} GiB", ha="center", va="bottom", fontsize=10)
    ax.text(1, b1 + max(b0, b1) * 0.085, f"+{float(summary['delta_archive_size_pct'] or 0.0):.2f}%", ha="center", va="bottom", fontsize=11, color="#374151")
    ax.set_ylabel("Aggregate archive size (GiB)")
    ax.grid(axis="y", color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    fig.tight_layout()
    _save_mpl_figure(fig, out_base)
    plt.close(fig)


def _plot_sci_main_composite(rows: list[dict], summary: dict, out_base: Path) -> None:
    plt = _get_plt()
    valid_rows = [row for row in rows if row["file"] != "AGGREGATE" and row.get("b0_ms2_cr") not in (None, "") and row.get("b1_ms2_cr") not in (None, "")]
    paired = []
    for row in valid_rows:
        b0 = float(row["b0_ms2_cr"])
        b1 = float(row["b1_ms2_cr"])
        delta = b0 - b1
        archive_delta = float(row.get("delta_archive_pct") or 0.0)
        paired.append(
            {
                "label": str(row["file_label"]),
                "b0": b0,
                "b1": b1,
                "cr_delta": delta,
                "archive_delta": archive_delta,
            }
        )
    paired.sort(key=lambda item: item["cr_delta"], reverse=True)

    labels = [item["label"] for item in paired]
    file_ids = [f"File {idx + 1}" for idx in range(len(labels))]
    b0_vals = [item["b0"] for item in paired]
    b1_vals = [item["b1"] for item in paired]
    xpos = list(range(len(labels)))
    cr_gain_pct = [((b0 / b1) - 1.0) * 100.0 if b1 > 0 else 0.0 for b0, b1 in zip(b0_vals, b1_vals)]
    median_gain_pct = _median(cr_gain_pct)

    fig = plt.figure(figsize=(14.8, 4.15))
    gs = fig.add_gridspec(1, 2, width_ratios=[2.35, 0.36], wspace=0.16)
    ax_a = fig.add_subplot(gs[0, 0])
    ax_b = fig.add_subplot(gs[0, 1])

    for idx, item in enumerate(paired):
        ax_a.plot([idx, idx], [item["b1"], item["b0"]], color="#c7cdd6", linewidth=1.0, zorder=1)
    ax_a.scatter(xpos, b1_vals, s=20, color=PAPER_PINK, label="B1 ZDPD passthrough", zorder=3)
    ax_a.scatter(xpos, b0_vals, s=20, color=PAPER_BLUE, label="B0 Full MS2 dict", zorder=4)
    ax_a.set_xticks(xpos, file_ids, rotation=-55, ha="right", fontsize=7.2)
    ax_a.set_xlabel("Benchmark files")
    ax_a.set_ylabel("MS2 section compression ratio (x)")
    ax_a.grid(axis="y", color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax_a.set_axisbelow(True)
    ax_a.legend(frameon=False, loc="upper right", fontsize=8.5, handletextpad=0.4, borderpad=0.2)
    ax_a.set_title("A  Paired per-file MS2 section CR", loc="left", fontweight="bold")
    ax_a.spines["top"].set_visible(False)
    ax_a.spines["right"].set_visible(False)

    x = [0, 1]
    rng_offsets = []
    n = max(len(b0_vals), 1)
    for idx in range(n):
        frac = (idx % 7) / 6.0 - 0.5 if n > 1 else 0.0
        rng_offsets.append(frac * 0.12)
    for idx, (b0, b1) in enumerate(zip(b0_vals, b1_vals)):
        off = rng_offsets[idx]
        ax_b.plot([x[0] + off, x[1] + off], [b0, b1], color="#c7cdd6", linewidth=0.9, zorder=1, alpha=0.8)
    ax_b.scatter([x[0] + o for o in rng_offsets], b0_vals, color=PAPER_BLUE, s=18, zorder=3)
    ax_b.scatter([x[1] + o for o in rng_offsets], b1_vals, color=PAPER_PINK, s=18, zorder=3)
    ax_b.boxplot(
        [b0_vals, b1_vals],
        positions=x,
        widths=0.38,
        patch_artist=True,
        boxprops=dict(facecolor="white", edgecolor="#6b7280", linewidth=1.1),
        medianprops=dict(color="#111827", linewidth=1.2),
        whiskerprops=dict(color="#6b7280", linewidth=1.0),
        capprops=dict(color="#6b7280", linewidth=1.0),
        flierprops=dict(marker=""),
    )
    ax_b.text(
        0.02,
        0.98,
        f"median gain = {median_gain_pct:.1f}%",
        transform=ax_b.transAxes,
        ha="left",
        va="top",
        fontsize=9,
    )
    ax_b.set_xticks(x, ["B0", "B1"])
    ax_b.set_ylabel("MS2 section CR (x)")
    ax_b.grid(axis="y", color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax_b.set_axisbelow(True)
    ax_b.set_title("B  Distribution and paired effect", loc="left", fontweight="bold")
    ax_b.spines["top"].set_visible(False)
    ax_b.spines["right"].set_visible(False)

    fig.subplots_adjust(left=0.075, right=0.99, top=0.92, bottom=0.34, wspace=0.18)
    _save_mpl_figure(fig, out_base)
    plt.close(fig)


def _plot_sci_paired_archive_delta(rows: list[dict], summary: dict, out_base: Path) -> None:
    plt = _get_plt()
    valid_rows = [row for row in rows if row["file"] != "AGGREGATE" and row.get("b0_ms2_cr") not in (None, "")]
    valid_rows.sort(key=lambda row: float(row.get("delta_archive_pct") or 0.0), reverse=True)
    labels = [str(row["file_label"]) for row in valid_rows]
    deltas = [float(row.get("delta_archive_pct") or 0.0) for row in valid_rows]

    fig, ax = plt.subplots(figsize=(10.8, max(7.8, 0.24 * len(labels) + 1.4)))
    ypos = list(range(len(labels)))
    ax.hlines(y=ypos, xmin=0, xmax=deltas, color=PAPER_PINK, linewidth=1.2, alpha=0.9)
    ax.scatter(deltas, ypos, s=28, color=PAPER_PINK, zorder=3)
    ax.set_yticks(ypos, labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("Archive size increase after removing MS2 dictionary (%)")
    ax.axvline(float(summary["delta_archive_size_pct"] or 0.0), color=PAPER_BLUE, linestyle="--", linewidth=1.1, label=f"Aggregate = {float(summary['delta_archive_size_pct'] or 0.0):.2f}%")
    ax.grid(axis="x", color="#d1d5db", linewidth=0.8, alpha=0.9)
    ax.set_axisbelow(True)
    ax.legend(frameon=False, loc="lower right", fontsize=9)
    ax.set_title("Per-file archive penalty with aggregate reference", loc="left", fontweight="bold")
    fig.tight_layout()
    _save_mpl_figure(fig, out_base)
    plt.close(fig)


def _build_plots(rows: list[dict], summary: dict, plot_dir: Path) -> dict[str, str]:
    plot_dir.mkdir(parents=True, exist_ok=True)
    summary_cr = plot_dir / "ms2_ablation_summary_cr_b0_vs_b1"
    delta_plot = plot_dir / "ms2_ablation_per_file_archive_delta_vs_b0"
    scatter_plot = plot_dir / "ms2_ablation_b0_vs_b1_ms2_cr_scatter"
    bytes_plot = plot_dir / "ms2_ablation_aggregate_archive_bytes"
    sci_main = plot_dir / "ms2_ablation_sci_main_figure"
    sci_delta = plot_dir / "ms2_ablation_sci_archive_penalty_paired"
    _plot_summary_cr(summary, summary_cr)
    _plot_per_file_archive_delta(rows, delta_plot)
    _plot_ms2_cr_scatter(rows, scatter_plot)
    _plot_aggregate_archive_bytes(summary, bytes_plot)
    _plot_sci_main_composite(rows, summary, sci_main)
    _plot_sci_paired_archive_delta(rows, summary, sci_delta)
    return {
        "summary_cr_svg": str(summary_cr.with_suffix(".svg")),
        "summary_cr_png": str(summary_cr.with_suffix(".png")),
        "summary_cr_pdf": str(summary_cr.with_suffix(".pdf")),
        "delta_svg": str(delta_plot.with_suffix(".svg")),
        "delta_png": str(delta_plot.with_suffix(".png")),
        "delta_pdf": str(delta_plot.with_suffix(".pdf")),
        "scatter_svg": str(scatter_plot.with_suffix(".svg")),
        "scatter_png": str(scatter_plot.with_suffix(".png")),
        "scatter_pdf": str(scatter_plot.with_suffix(".pdf")),
        "archive_bytes_svg": str(bytes_plot.with_suffix(".svg")),
        "archive_bytes_png": str(bytes_plot.with_suffix(".png")),
        "archive_bytes_pdf": str(bytes_plot.with_suffix(".pdf")),
        "sci_main_svg": str(sci_main.with_suffix(".svg")),
        "sci_main_png": str(sci_main.with_suffix(".png")),
        "sci_main_pdf": str(sci_main.with_suffix(".pdf")),
        "sci_delta_svg": str(sci_delta.with_suffix(".svg")),
        "sci_delta_png": str(sci_delta.with_suffix(".png")),
        "sci_delta_pdf": str(sci_delta.with_suffix(".pdf")),
    }


def _summary_html(summary: dict, rows: list[dict], plots: dict[str, str] | None = None) -> str:
    body_rows = []
    for row in rows:
        body_rows.append(
            "<tr>"
            f"<td>{row.get('file_short','')}</td>"
            f"<td>{row['file_label']}</td>"
            f"<td>{row['file']}</td>"
            f"<td>{row['ms2_raw_bytes']}</td>"
            f"<td>{row['tc_ms2_compressed_bytes']}</td>"
            f"<td>{row['zdpd_ms2_compressed_bytes']}</td>"
            f"<td>{row['b0_archive_bytes']}</td>"
            f"<td>{row['b1_archive_bytes']}</td>"
            f"<td>{row['mzml_bytes']}</td>"
            f"<td>{fmt_num(row.get('b0_ms2_cr'))}</td>"
            f"<td>{fmt_num(row.get('b1_ms2_cr'))}</td>"
            f"<td>{fmt_num(row.get('b0_archive_cr'))}</td>"
            f"<td>{fmt_num(row.get('b1_archive_cr'))}</td>"
            f"<td>{fmt_num(row.get('delta_archive_pct'))}</td>"
            "</tr>"
        )
    summary_items = [
        ("Files total", summary["n_files_total"]),
        ("MS2-bearing files", summary["n_files_ms2_bearing"]),
        ("B0 MS2 aggregate CR", f"{fmt_num(summary['b0_ms2_aggregate_cr'])}x"),
        ("B1 MS2 aggregate CR", f"{fmt_num(summary['b1_ms2_aggregate_cr'])}x"),
        ("B0 archive aggregate CR", f"{fmt_num(summary['b0_archive_aggregate_cr'])}x"),
        ("B1 archive aggregate CR", f"{fmt_num(summary['b1_archive_aggregate_cr'])}x"),
        ("Archive size delta vs B0", f"+{fmt_num(summary['delta_archive_size_pct'])}%"),
        ("MS2 dict contribution", f"+{fmt_num(summary['ms2_dict_contribution_pct'])}%"),
    ]
    li = "\n".join(f"<li><strong>{k}:</strong> {v}</li>" for k, v in summary_items)
    figure_block = ""
    if plots:
        figure_block = f"""
  <h2>Plots</h2>
  <div style="display:grid;grid-template-columns:repeat(2,minmax(320px,1fr));gap:18px;align-items:start;">
    <figure style="margin:0;"><img src="plots/{Path(plots['summary_cr_svg']).name}" style="width:100%;height:auto;"><figcaption>B0 vs B1 aggregate compression ratios.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/{Path(plots['archive_bytes_svg']).name}" style="width:100%;height:auto;"><figcaption>Aggregate archive size increase after removing the MS2 dictionary.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/{Path(plots['delta_svg']).name}" style="width:100%;height:auto;"><figcaption>Per-file archive size penalty relative to B0.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/{Path(plots['scatter_svg']).name}" style="width:100%;height:auto;"><figcaption>Per-file MS2 section CR under B0 and B1.</figcaption></figure>
  </div>
"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MS2 Ablation B1 Summary</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 24px; color: #111827; }}
    h1 {{ margin-bottom: 8px; }}
    ul {{ line-height: 1.6; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d1d5db; padding: 6px 8px; text-align: right; }}
    th:first-child, th:nth-child(2), td:first-child, td:nth-child(2) {{ text-align: left; }}
    thead th {{ background: #f3f4f6; position: sticky; top: 0; }}
  </style>
</head>
<body>
  <h1>MS2 Ablation B1</h1>
  <p>Hybrid archive calculation replacing TrackCodec MS2 window-native dictionary bytes with ZDPD MS2 passthrough bytes.</p>
  <ul>{li}</ul>
  {figure_block}
  <table>
    <thead>
      <tr>
        <th>Short</th><th>Label</th><th>File</th><th>MS2 Raw</th><th>B0 MS2 Bytes</th><th>B1 MS2 Bytes</th>
        <th>B0 Archive</th><th>B1 Archive</th><th>mzML Bytes</th><th>B0 MS2 CR</th><th>B1 MS2 CR</th>
        <th>B0 Archive CR</th><th>B1 Archive CR</th><th>Delta %</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build MS2 ablation B1 from existing benchmark tables.")
    parser.add_argument("--table-dir", type=Path, default=DEFAULT_TABLE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    table_dir = args.table_dir
    out_dir = args.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    input_csv = table_dir / "ms2_ablation_b1_per_file_all36.csv"
    rows = read_csv(input_csv)
    rows = [
        row
        for row in rows
        if str(row.get("file", "")) == "AGGREGATE" or str(row.get("file", "")) in UNIFIED30_FILES
    ]
    body = [row for row in rows if str(row.get("file", "")) != "AGGREGATE"]
    order_map = {file_name: idx + 1 for idx, file_name in enumerate(UNIFIED30_ORDER)}
    body.sort(key=lambda row: order_map.get(str(row.get("file", "")), 10_000))
    for row in body:
        file_name = str(row.get("file", ""))
        file_index = order_map.get(file_name, "")
        row["file_index"] = file_index
        row["file_short"] = f"File{file_index}" if file_index != "" else file_name
    sum_ms2_raw = sum(_to_int(row.get("ms2_raw_bytes")) for row in body)
    sum_tc_ms2 = sum(_to_int(row.get("tc_ms2_compressed_bytes")) for row in body)
    sum_zdpd_ms2 = sum(_to_int(row.get("zdpd_ms2_compressed_bytes")) for row in body)
    sum_b0_archive = sum(_to_int(row.get("b0_archive_bytes")) for row in body)
    sum_b1_archive = sum(_to_int(row.get("b1_archive_bytes")) for row in body)
    sum_mzml = sum(_to_int(row.get("mzml_bytes")) for row in body)
    summary = {
        "n_files_total": len(body),
        "n_files_ms2_bearing": sum(1 for row in body if _to_int(row.get("ms2_raw_bytes")) > 0),
        "b0_ms2_aggregate_cr": _safe_ratio(sum_ms2_raw, sum_tc_ms2),
        "b1_ms2_aggregate_cr": _safe_ratio(sum_ms2_raw, sum_zdpd_ms2),
        "b0_archive_aggregate_cr": _safe_ratio(sum_mzml, sum_b0_archive),
        "b1_archive_aggregate_cr": _safe_ratio(sum_mzml, sum_b1_archive),
        "delta_archive_size_pct": (float(sum_b1_archive) / float(sum_b0_archive) - 1.0) * 100.0 if sum_b0_archive > 0 else None,
        "ms2_dict_contribution_pct": ((_safe_ratio(sum_ms2_raw, sum_tc_ms2) / _safe_ratio(sum_ms2_raw, sum_zdpd_ms2)) - 1.0) * 100.0 if sum_tc_ms2 > 0 and sum_zdpd_ms2 > 0 else None,
        "sum_ms2_raw_bytes": sum_ms2_raw,
        "sum_tc_ms2_compressed_bytes": sum_tc_ms2,
        "sum_zdpd_ms2_compressed_bytes": sum_zdpd_ms2,
        "sum_b0_archive_bytes": sum_b0_archive,
        "sum_b1_archive_bytes": sum_b1_archive,
        "sum_mzml_bytes": sum_mzml,
    }
    rows = body + [{
        "file": "AGGREGATE",
        "file_index": "",
        "file_short": "AGGREGATE",
        "file_label": "AGGREGATE",
        "dataset": "",
        "dataset_key": "",
        "input_path": "",
        "ms2_raw_bytes": summary["sum_ms2_raw_bytes"],
        "tc_ms2_compressed_bytes": summary["sum_tc_ms2_compressed_bytes"],
        "zdpd_ms2_compressed_bytes": summary["sum_zdpd_ms2_compressed_bytes"],
        "b0_archive_bytes": summary["sum_b0_archive_bytes"],
        "b1_archive_bytes": summary["sum_b1_archive_bytes"],
        "mzml_bytes": summary["sum_mzml_bytes"],
        "b0_ms2_cr": summary["b0_ms2_aggregate_cr"],
        "b1_ms2_cr": summary["b1_ms2_aggregate_cr"],
        "b0_archive_cr": summary["b0_archive_aggregate_cr"],
        "b1_archive_cr": summary["b1_archive_aggregate_cr"],
        "delta_archive_pct": summary["delta_archive_size_pct"],
    }]
    write_csv(rows, out_dir / "ms2_ablation_b1_per_file.csv")
    (out_dir / "ms2_ablation_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "ms2_ablation_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    (out_dir / "ms2_ablation_summary.html").write_text(_summary_html(summary, rows, None), encoding="utf-8")

    print(json.dumps({"output_dir": str(out_dir), **summary}, indent=2))


if __name__ == "__main__":
    main()
