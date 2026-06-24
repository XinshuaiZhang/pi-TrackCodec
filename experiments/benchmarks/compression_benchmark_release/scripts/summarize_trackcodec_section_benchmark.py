from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path
from statistics import mean, median

from benchmark_release_paths import default_combined_output_dir

GIB = 1024**3

COMBINED_DIR = default_combined_output_dir(Path(__file__))
COMBINED_TABLE = COMBINED_DIR / "tables" / "combined_per_file_methods.csv"
TABLE_DIR = COMBINED_DIR / "tables"
PLOT_DIR = COMBINED_DIR / "plots"


def safe_float(value: object) -> float:
    try:
        if value is None:
            return math.nan
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def safe_int(value: object) -> int:
    try:
        if value is None:
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def fmt_bytes(value: int) -> str:
    return f"{value / GIB:.3f} GiB"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def read_trackcodec_rows() -> list[dict[str, str]]:
    with COMBINED_TABLE.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [row for row in rows if row.get("method") == "trackcodec" and row.get("status") == "ok"]


def stats_path_from_note(row: dict[str, str]) -> Path:
    note = row.get("note", "")
    prefix = "Stats JSON:"
    if prefix in note:
        return Path(note.split(prefix, 1)[1].strip())
    method_dir = Path(row.get("source_csv", "")).parent / row.get("file", "")
    raise ValueError(f"TrackCodec stats path not found for {row.get('file')}: {method_dir}")


def component_compressed_bytes(stats: dict[str, object], component_key: str) -> int:
    component = stats.get(component_key, {})
    if not isinstance(component, dict):
        return 0
    return safe_int(component.get("compressed_bytes"))


def section_record(row: dict[str, str], stats: dict[str, object], section: str) -> dict[str, object]:
    sec = stats.get(section, {})
    if not isinstance(sec, dict):
        sec = {}
    raw = safe_int(sec.get("raw_bytes"))
    core_compressed = safe_int(sec.get("compressed_bytes"))
    sidecar_compressed = 0
    compression_scope = "section core"
    if section == "ms1":
        sidecar_compressed = component_compressed_bytes(
            stats, "ms1_full_mz"
        ) + component_compressed_bytes(stats, "ms1_orphan_intensity")
        compression_scope = "MS1 core + full m/z sidecar + orphan intensity sidecar"
    compressed = core_compressed + sidecar_compressed
    ratio = raw / compressed if raw > 0 and compressed > 0 else math.nan
    core_only_ratio = raw / core_compressed if raw > 0 and core_compressed > 0 else math.nan
    timing = stats.get("stage_timings_s", {})
    if not isinstance(timing, dict):
        timing = {}
    encode_key = f"{section}_encode_s"
    return {
        "dataset": row.get("dataset", ""),
        "dataset_key": row.get("dataset_key", ""),
        "file": row.get("file", ""),
        "label": row.get("combined_file_label", ""),
        "section": section.upper(),
        "raw_bytes": raw,
        "compressed_bytes": compressed,
        "core_compressed_bytes": core_compressed,
        "sidecar_compressed_bytes": sidecar_compressed,
        "raw_gib": raw / GIB,
        "compressed_gib": compressed / GIB,
        "core_compressed_gib": core_compressed / GIB,
        "sidecar_compressed_gib": sidecar_compressed / GIB,
        "compression_ratio": ratio if math.isfinite(ratio) else "",
        "core_only_compression_ratio": core_only_ratio if math.isfinite(core_only_ratio) else "",
        "compression_rate_pct": (compressed / raw * 100.0) if raw > 0 and compressed > 0 else "",
        "compression_scope": compression_scope,
        "encode_time_s": safe_float(sec.get("encode_time_s") if section == "ms1" else timing.get(encode_key)),
        "stats_json": str(stats_path_from_note(row)),
    }


def component_records(row: dict[str, str], stats: dict[str, object]) -> list[dict[str, object]]:
    components = [
        ("ms1", "MS1 core"),
        ("ms2", "MS2 core"),
        ("metadata", "Metadata XML"),
        ("auxiliary", "Auxiliary"),
        ("ms1_full_mz", "MS1 full m/z sidecar"),
        ("ms1_orphan_intensity", "MS1 orphan intensity sidecar"),
    ]
    out: list[dict[str, object]] = []
    for key, label in components:
        comp = stats.get(key, {})
        if not isinstance(comp, dict):
            continue
        raw = safe_int(comp.get("raw_bytes"))
        compressed = safe_int(comp.get("compressed_bytes"))
        ratio = safe_float(comp.get("compression_ratio"))
        if not math.isfinite(ratio) and raw > 0 and compressed > 0:
            ratio = raw / compressed
        out.append(
            {
                "dataset": row.get("dataset", ""),
                "file": row.get("file", ""),
                "label": row.get("combined_file_label", ""),
                "component": label,
                "component_key": key,
                "raw_bytes": raw,
                "compressed_bytes": compressed,
                "raw_gib": raw / GIB,
                "compressed_gib": compressed / GIB,
                "compression_ratio": ratio if math.isfinite(ratio) else "",
                "compression_rate_pct": (compressed / raw * 100.0) if raw > 0 and compressed > 0 else "",
            }
        )
    return out


def aggregate_section(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for section in ("MS1", "MS2"):
        sub = [r for r in rows if r["section"] == section]
        nonzero = [r for r in sub if int(r["raw_bytes"]) > 0 and int(r["compressed_bytes"]) > 0]
        total_raw = sum(int(r["raw_bytes"]) for r in nonzero)
        total_comp = sum(int(r["compressed_bytes"]) for r in nonzero)
        ratios = [float(r["compression_ratio"]) for r in nonzero if r["compression_ratio"] != ""]
        times = [float(r["encode_time_s"]) for r in sub if math.isfinite(safe_float(r["encode_time_s"]))]
        out.append(
            {
                "section": section,
                "n_files_total": len(sub),
                "n_files_nonzero_raw": len(nonzero),
                "total_raw_bytes": total_raw,
                "total_compressed_bytes": total_comp,
                "total_raw_gib": total_raw / GIB,
                "total_compressed_gib": total_comp / GIB,
                "aggregate_compression_ratio": total_raw / total_comp if total_comp > 0 else "",
                "mean_compression_ratio": mean(ratios) if ratios else "",
                "median_compression_ratio": median(ratios) if ratios else "",
                "compression_rate_pct": total_comp / total_raw * 100.0 if total_raw > 0 else "",
                "mean_encode_time_s": mean(times) if times else "",
                "median_encode_time_s": median(times) if times else "",
            }
        )
    return out


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(rows: list[dict[str, object]], fields: list[tuple[str, str]]) -> str:
    lines = ["| " + " | ".join(header for header, _ in fields) + " |"]
    lines.append("|" + "|".join("---" for _ in fields) + "|")
    for row in rows:
        values = []
        for _, key in fields:
            value = row.get(key, "")
            if isinstance(value, float):
                if math.isfinite(value):
                    value = f"{value:.4f}"
                else:
                    value = ""
            values.append(esc(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def svg_doc(width: int, height: int, body: list[str]) -> str:
    style = """
<style>
.title{font:700 22px Arial,sans-serif;fill:#1f2933}
.subtitle{font:12px Arial,sans-serif;fill:#52606d}
.axis{font:12px Arial,sans-serif;fill:#374151}
.small{font:11px Arial,sans-serif;fill:#374151}
.grid{stroke:#e5e7eb;stroke-width:1}
.axisline{stroke:#374151;stroke-width:1.2}
</style>"""
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">\n{style}\n' + "\n".join(body) + "\n</svg>\n"


def plot_section_ratios(section_rows: list[dict[str, object]], path: Path) -> None:
    rows = [r for r in section_rows if r["compression_ratio"] != "" and int(r["raw_bytes"]) > 0]
    width, height = 1180, 660
    left, right, top, bottom = 82, 36, 88, 160
    labels = list(dict.fromkeys(str(r["label"]) for r in rows))
    y_max = max([float(r["compression_ratio"]) for r in rows] or [1.0]) * 1.18
    plot_w, plot_h = width - left - right, height - top - bottom
    body = [
        '<text class="title" x="82" y="36">TrackCodec MS1/MS2 Section Compression Ratios</text>',
        '<text class="subtitle" x="82" y="60">Only TrackCodec exposes section-level stats in this benchmark; other methods are intentionally omitted.</text>',
    ]
    for i in range(6):
        value = y_max * i / 5
        y = top + plot_h - value / y_max * plot_h
        body.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        body.append(f'<text class="axis" x="{left-8}" y="{y+4:.2f}" text-anchor="end">{value:.1f}</text>')
    body.append(f'<line class="axisline" x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}"/>')
    body.append(f'<line class="axisline" x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}"/>')
    n = max(1, len(labels))
    group_w = plot_w / n
    colors = {"MS1": "#3C5488", "MS2": "#E64B35"}
    for i, label in enumerate(labels):
        x_mid = left + (i + 0.5) * group_w
        for section, dx in (("MS1", -8), ("MS2", 8)):
            row = next((r for r in rows if r["label"] == label and r["section"] == section), None)
            if not row:
                continue
            ratio = float(row["compression_ratio"])
            if ratio <= 0:
                continue
            h = ratio / y_max * plot_h
            x = x_mid + dx
            body.append(f'<rect x="{x-7:.2f}" y="{top+plot_h-h:.2f}" width="14" height="{h:.2f}" fill="{colors[section]}" fill-opacity="0.86"/>')
        body.append(f'<text class="small" x="{x_mid:.2f}" y="{top+plot_h+18}" text-anchor="end" transform="rotate(-55 {x_mid:.2f} {top+plot_h+18})">{esc(label)}</text>')
    legend_x = width - 210
    for idx, section in enumerate(("MS1", "MS2")):
        y = 32 + idx * 22
        body.append(f'<rect x="{legend_x}" y="{y-11}" width="14" height="14" fill="{colors[section]}" fill-opacity="0.86"/>')
        body.append(f'<text class="axis" x="{legend_x+20}" y="{y}">{section}</text>')
    body.append(f'<text class="axis" x="24" y="{top+plot_h/2:.2f}" text-anchor="middle" transform="rotate(-90 24 {top+plot_h/2:.2f})">Section compression ratio (x)</text>')
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def plot_section_sizes(section_rows: list[dict[str, object]], path: Path) -> None:
    agg = aggregate_section(section_rows)
    width, height = 760, 520
    left, right, top, bottom = 82, 36, 78, 70
    y_max = max([float(r["total_raw_gib"]) for r in agg] + [float(r["total_compressed_gib"]) for r in agg]) * 1.18
    plot_w, plot_h = width - left - right, height - top - bottom
    body = [
        '<text class="title" x="82" y="36">TrackCodec Aggregate Section Sizes</text>',
        '<text class="subtitle" x="82" y="58">Raw and compressed totals are summed across files with nonzero raw bytes per section.</text>',
    ]
    for i in range(6):
        value = y_max * i / 5
        y = top + plot_h - value / y_max * plot_h
        body.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}"/>')
        body.append(f'<text class="axis" x="{left-8}" y="{y+4:.2f}" text-anchor="end">{value:.1f}</text>')
    body.append(f'<line class="axisline" x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}"/>')
    body.append(f'<line class="axisline" x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}"/>')
    colors = {"raw": "#B09C85", "compressed": "#DC0000"}
    for i, row in enumerate(agg):
        x_mid = left + (i + 0.5) * plot_w / len(agg)
        for key, dx, label in (("total_raw_gib", -22, "raw"), ("total_compressed_gib", 22, "compressed")):
            value = float(row[key])
            h = value / y_max * plot_h if y_max > 0 else 0
            x = x_mid + dx
            body.append(f'<rect x="{x-18:.2f}" y="{top+plot_h-h:.2f}" width="36" height="{h:.2f}" fill="{colors[label]}" fill-opacity="0.86"/>')
            body.append(f'<text class="small" x="{x:.2f}" y="{top+plot_h-h-7:.2f}" text-anchor="middle">{value:.2f}G</text>')
        body.append(f'<text class="axis" x="{x_mid:.2f}" y="{height-34}" text-anchor="middle">{row["section"]}</text>')
    for idx, label in enumerate(("raw", "compressed")):
        x = 520 + idx * 112
        body.append(f'<rect x="{x}" y="28" width="14" height="14" fill="{colors[label]}" fill-opacity="0.86"/>')
        body.append(f'<text class="axis" x="{x+20}" y="40">{label}</text>')
    body.append(f'<text class="axis" x="24" y="{top+plot_h/2:.2f}" text-anchor="middle" transform="rotate(-90 24 {top+plot_h/2:.2f})">GiB</text>')
    path.write_text(svg_doc(width, height, body), encoding="utf-8")


def write_markdown(section_rows: list[dict[str, object]], component_rows: list[dict[str, object]], agg: list[dict[str, object]]) -> None:
    md = [
        "# TrackCodec MS1/MS2 Section-Level Benchmark",
        "",
        "Scope: TrackCodec only. Other methods in the whole-file benchmark are omitted here because their wrappers do not expose a comparable MS1/MS2 section-level output interface.",
        "",
        "Source: existing TrackCodec `.stats.json` files from the completed full8 and Data_StackZDPD benchmark runs. No compression rerun was required.",
        "",
        "MS1 compressed bytes include the TrackCodec MS1 core plus `ms1_full_mz` and `ms1_orphan_intensity` sidecar compressed bytes. The MS1 raw denominator remains the original MS1 m/z + intensity raw payload, so the sidecar raw bytes are not double-counted.",
        "",
        "## Aggregate",
        "",
        markdown_table(
            agg,
            [
                ("Section", "section"),
                ("Files", "n_files_total"),
                ("Nonzero Raw Files", "n_files_nonzero_raw"),
                ("Raw GiB", "total_raw_gib"),
                ("Compressed GiB", "total_compressed_gib"),
                ("Aggregate CR", "aggregate_compression_ratio"),
                ("Median CR", "median_compression_ratio"),
                ("Mean Encode s", "mean_encode_time_s"),
            ],
        ),
        "",
        "## Per-File MS1/MS2 Sections",
        "",
        markdown_table(
            section_rows,
            [
                ("Dataset", "dataset"),
                ("Label", "label"),
                ("File", "file"),
                ("Section", "section"),
                ("Raw GiB", "raw_gib"),
                ("Compressed GiB", "compressed_gib"),
                ("CR", "compression_ratio"),
                ("Rate %", "compression_rate_pct"),
                ("Encode s", "encode_time_s"),
            ],
        ),
        "",
        "## Component Breakdown",
        "",
        "This table records TrackCodec components present in the stats files. It is diagnostic and should not be mixed with the MS1/MS2 aggregate denominators without checking component semantics.",
        "",
        markdown_table(
            component_rows,
            [
                ("Dataset", "dataset"),
                ("Label", "label"),
                ("Component", "component"),
                ("Raw GiB", "raw_gib"),
                ("Compressed GiB", "compressed_gib"),
                ("CR", "compression_ratio"),
                ("Rate %", "compression_rate_pct"),
            ],
        ),
        "",
    ]
    (TABLE_DIR / "trackcodec_ms1_ms2_section_benchmark.md").write_text("\n".join(md), encoding="utf-8")


def write_html_report(summary: dict[str, object]) -> None:
    doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>TrackCodec MS1/MS2 Section Benchmark</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; background: #f7f9fc; color: #1f2933; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 26px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .meta {{ color: #52606d; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 18px 0 24px; }}
    .tile {{ background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 12px; }}
    .tile b {{ display: block; font-size: 22px; }}
    .figure {{ background: #fff; border: 1px solid #d9e2ec; border-radius: 6px; padding: 14px; margin: 16px 0; overflow-x: auto; }}
    img {{ width: 100%; min-width: 760px; height: auto; display: block; }}
    code {{ background: #eef2f7; padding: 1px 4px; border-radius: 3px; }}
  </style>
</head>
<body>
<main>
  <h1>TrackCodec MS1/MS2 Section-Level Benchmark</h1>
  <p class="meta">TrackCodec-only section summary generated from existing <code>.stats.json</code> files. Other benchmark methods are omitted because they do not expose comparable MS1/MS2 section stats in this run.</p>
  <div class="summary">
    <div class="tile"><span>Files</span><b>{summary["n_files"]}</b></div>
    <div class="tile"><span>MS1 aggregate CR</span><b>{summary["ms1_aggregate_cr"]:.2f}x</b></div>
    <div class="tile"><span>MS2 aggregate CR</span><b>{summary["ms2_aggregate_cr"]:.2f}x</b></div>
    <div class="tile"><span>MS2 nonzero files</span><b>{summary["ms2_nonzero_files"]}</b></div>
  </div>
  <section class="figure"><h2>Section Compression Ratios</h2><img src="../plots/trackcodec_ms1_ms2_section_ratios.svg" alt="TrackCodec MS1/MS2 section ratios"></section>
  <section class="figure"><h2>Aggregate Section Sizes</h2><img src="../plots/trackcodec_ms1_ms2_aggregate_sizes.svg" alt="TrackCodec aggregate section sizes"></section>
  <p class="meta">Tables: <code>trackcodec_ms1_ms2_section_benchmark.csv</code>, <code>trackcodec_ms1_ms2_section_aggregate.csv</code>, and <code>trackcodec_component_breakdown.csv</code>.</p>
</main>
</body>
</html>
"""
    (TABLE_DIR / "trackcodec_ms1_ms2_section_benchmark.html").write_text(doc, encoding="utf-8")


def main() -> int:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    section_rows: list[dict[str, object]] = []
    component_rows: list[dict[str, object]] = []
    missing: list[str] = []
    for row in read_trackcodec_rows():
        stats_path = stats_path_from_note(row)
        if not stats_path.exists():
            missing.append(str(stats_path))
            continue
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        section_rows.append(section_record(row, stats, "ms1"))
        section_rows.append(section_record(row, stats, "ms2"))
        component_rows.extend(component_records(row, stats))
    agg = aggregate_section(section_rows)
    write_csv(TABLE_DIR / "trackcodec_ms1_ms2_section_benchmark.csv", section_rows)
    write_csv(TABLE_DIR / "trackcodec_ms1_ms2_section_aggregate.csv", agg)
    write_csv(TABLE_DIR / "trackcodec_component_breakdown.csv", component_rows)
    write_markdown(section_rows, component_rows, agg)
    plot_section_ratios(section_rows, PLOT_DIR / "trackcodec_ms1_ms2_section_ratios.svg")
    plot_section_sizes(section_rows, PLOT_DIR / "trackcodec_ms1_ms2_aggregate_sizes.svg")
    ms1 = next(r for r in agg if r["section"] == "MS1")
    ms2 = next(r for r in agg if r["section"] == "MS2")
    summary = {
        "n_files": len({r["file"] for r in section_rows}),
        "n_section_rows": len(section_rows),
        "n_component_rows": len(component_rows),
        "missing_stats": missing,
        "ms1_aggregate_cr": float(ms1["aggregate_compression_ratio"] or 0.0),
        "ms2_aggregate_cr": float(ms2["aggregate_compression_ratio"] or 0.0),
        "ms2_nonzero_files": int(ms2["n_files_nonzero_raw"]),
        "tables": {
            "section": str(TABLE_DIR / "trackcodec_ms1_ms2_section_benchmark.csv"),
            "aggregate": str(TABLE_DIR / "trackcodec_ms1_ms2_section_aggregate.csv"),
            "components": str(TABLE_DIR / "trackcodec_component_breakdown.csv"),
            "markdown": str(TABLE_DIR / "trackcodec_ms1_ms2_section_benchmark.md"),
            "html": str(TABLE_DIR / "trackcodec_ms1_ms2_section_benchmark.html"),
        },
        "plots": {
            "ratios": str(PLOT_DIR / "trackcodec_ms1_ms2_section_ratios.svg"),
            "sizes": str(PLOT_DIR / "trackcodec_ms1_ms2_aggregate_sizes.svg"),
        },
    }
    (TABLE_DIR / "trackcodec_ms1_ms2_section_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_html_report(summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
