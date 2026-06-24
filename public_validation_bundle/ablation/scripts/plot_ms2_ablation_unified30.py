from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from ms2_svg_plot_helpers import SvgFigure

try:
    from scipy import stats as scipy_stats
except Exception:  # pragma: no cover
    scipy_stats = None


INPUT_DIR = Path(__file__).resolve().parents[1] / "outputs" / "ms2_ablation_unified30"
CSV_PATH = INPUT_DIR / "ms2_ablation_b1_per_file.csv"
SUMMARY_PATH = INPUT_DIR / "ms2_ablation_summary.json"
PLOT_DIR = INPUT_DIR / "plots"
PAPER_BLUE = "#355c7d"
PAPER_PINK = "#f67280"
GRID = "#E5E7EB"
TEXT = "#111827"
MUTED = "#6B7280"
DOT_LINK = "#C7CDD6"


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _to_float(value, default: float = math.nan) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _median(values: list[float]) -> float:
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return math.nan
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def _paired_wilcoxon_stats(x: list[float], y: list[float]) -> tuple[float, float, float, float]:
    diffs = [a - b for a, b in zip(x, y) if math.isfinite(a) and math.isfinite(b) and abs(a - b) > 0]
    if not diffs:
        return math.nan, math.nan, math.nan, math.nan
    abs_diffs = [abs(d) for d in diffs]
    order = sorted(range(len(abs_diffs)), key=lambda i: abs_diffs[i])
    ranks = [0.0] * len(abs_diffs)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and abs_diffs[order[j]] == abs_diffs[order[i]]:
            j += 1
        rank_start = i + 1
        rank_end = j
        rank = (rank_start + rank_end) / 2.0
        for k in range(i, j):
            ranks[order[k]] = rank
        i = j
    w_pos = sum(rank for rank, diff in zip(ranks, diffs) if diff > 0)
    w_neg = sum(rank for rank, diff in zip(ranks, diffs) if diff < 0)
    if scipy_stats is not None:
        try:
            _, p_value = scipy_stats.wilcoxon(x, y, zero_method="wilcox", alternative="two-sided", method="auto")
        except Exception:
            p_value = math.nan
    else:
        p_value = math.nan
    if not math.isfinite(p_value):
        p_value = _exact_wilcoxon_p_from_ranks(ranks, w_pos, w_neg)
    denom = w_pos + w_neg
    effect = (w_pos - w_neg) / denom if denom > 0 else math.nan
    return float(p_value), float(effect), float(w_pos), float(w_neg)


def _exact_wilcoxon_p_from_ranks(ranks: list[float], w_pos: float, w_neg: float) -> float:
    if not ranks:
        return math.nan
    scaled_ranks = [int(round(rank * 2.0)) for rank in ranks]
    observed = int(round(min(w_pos, w_neg) * 2.0))
    total_rank = sum(scaled_ranks)
    counts: dict[int, int] = {0: 1}
    for rank in scaled_ranks:
        updated = counts.copy()
        for rank_sum, count in counts.items():
            updated[rank_sum + rank] = updated.get(rank_sum + rank, 0) + count
        counts = updated
    tail_count = sum(count for rank_sum, count in counts.items() if rank_sum <= observed or rank_sum >= total_rank - observed)
    return min(1.0, tail_count / float(2 ** len(scaled_ranks)))


def _nice_ticks(max_value: float, n: int = 4) -> list[float]:
    if not math.isfinite(max_value) or max_value <= 0:
        return [0.0, 1.0]
    raw = max_value / max(n, 1)
    power = 10 ** math.floor(math.log10(raw))
    step = min((1, 2, 5, 10), key=lambda m: abs(raw - m * power)) * power
    top = math.ceil(max_value / step) * step
    return [i * step for i in range(int(round(top / step)) + 1)]


def _paired_rows(rows: list[dict]) -> list[dict]:
    paired = []
    for row in rows:
        if row.get("file") == "AGGREGATE":
            continue
        b0 = _to_float(row.get("b0_ms2_cr"))
        b1 = _to_float(row.get("b1_ms2_cr"))
        if not math.isfinite(b0) or not math.isfinite(b1):
            continue
        file_index = int(float(row.get("file_index") or 0))
        paired.append(
            {
                "file": str(row["file"]),
                "file_index": file_index,
                "file_short": str(row.get("file_short") or f"File{file_index}"),
                "label": str(row.get("file_label") or row.get("file")),
                "b0": b0,
                "b1": b1,
                "archive_delta": _to_float(row.get("delta_archive_pct"), 0.0),
                "b0_archive_cr": _to_float(row.get("b0_archive_cr")),
                "b1_archive_cr": _to_float(row.get("b1_archive_cr")),
                "cr_delta": b0 - b1,
            }
        )
    paired.sort(key=lambda item: item["file_index"])
    return paired


def _axis_y(value: float, ymin: float, ymax: float, top: float, plot_h: float) -> float:
    return top + plot_h - ((value - ymin) / (ymax - ymin)) * plot_h


def _draw_y_axis(fig: SvgFigure, *, left: float, top: float, plot_w: float, plot_h: float, ymin: float, ymax: float, ticks: list[float], label: str) -> None:
    for tick in ticks:
        y = _axis_y(tick, ymin, ymax, top, plot_h)
        fig.line(left, y, left + plot_w, y, color=GRID, width=0.9)
        fig.line(left - 4, y, left, y, color=TEXT, width=1.0)
        fig.text(left - 8, y + 4, f"{tick:g}", size=12.0, anchor="end", color=MUTED)
    fig.line(left, top, left, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color=TEXT, width=1.0)
    fig.text(24, top + plot_h / 2, label, size=14.0, weight="400", anchor="middle", color=TEXT, rotate=-90)


def _draw_x_ticks(
    fig: SvgFigure,
    *,
    xs: list[float],
    labels: list[str],
    base_y: float,
    tick_len: float = 5.0,
    rotate: float = -50.0,
    size: float = 9.0,
    text_offset_y: float = 13.0,
) -> None:
    for x, label in zip(xs, labels):
        fig.line(x, base_y, x, base_y + tick_len, color=TEXT, width=1.0)
        fig.text(x - 1, base_y + tick_len + text_offset_y, label, size=size, anchor="end", color=TEXT, rotate=rotate)


def plot_summary_cr(summary: dict, out_base: Path) -> None:
    width, height = 740, 360
    fig = SvgFigure(width, height)
    left, top, right, bottom = 160, 42, 36, 50
    plot_w = width - left - right
    plot_h = height - top - bottom
    labels = ["MS2 section CR", "Whole-archive CR"]
    b0 = [float(summary["b0_ms2_aggregate_cr"] or 0.0), float(summary["b0_archive_aggregate_cr"] or 0.0)]
    b1 = [float(summary["b1_ms2_aggregate_cr"] or 0.0), float(summary["b1_archive_aggregate_cr"] or 0.0)]
    max_v = max(b0 + b1) * 1.16
    ticks = _nice_ticks(max_v, 4)
    for tick in ticks:
        x = left + (tick / max(ticks)) * plot_w
        fig.line(x, top, x, top + plot_h, color=GRID, width=0.9)
        fig.line(x, top + plot_h, x, top + plot_h + 4, color=TEXT, width=1.0)
        fig.text(x, top + plot_h + 20, f"{tick:g}", size=10.5, anchor="middle", color=MUTED)
    fig.line(left, top, left, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color=TEXT, width=1.0)
    fig.text(left + plot_w / 2, height - 10, "Compression ratio (x)", size=12.5, anchor="middle")
    fig.text(left, 20, "MS2 ablation summary", size=15.5, weight="700")

    centers = [top + plot_h * 0.32, top + plot_h * 0.72]
    bar_h = 26
    for idx, label in enumerate(labels):
        y0 = centers[idx] - 18
        y1 = centers[idx] + 18
        fig.text(left - 16, centers[idx] + 4, label, size=12, anchor="end")
        b0_w = (b0[idx] / max(ticks)) * plot_w
        b1_w = (b1[idx] / max(ticks)) * plot_w
        fig.rect(left, y0 - bar_h / 2, b0_w, bar_h, fill=PAPER_BLUE, opacity=0.92)
        fig.rect(left, y1 - bar_h / 2, b1_w, bar_h, fill=PAPER_PINK, opacity=0.92)
        fig.text(left + b0_w + 8, y0 + 4, f"{b0[idx]:.2f}x", size=11)
        fig.text(left + b1_w + 8, y1 + 4, f"{b1[idx]:.2f}x", size=11)

    fig.circle(width - 212, 24, 5, fill=PAPER_BLUE)
    fig.text(width - 200, 28, "B0 Full MS2 dict", size=10.5)
    fig.circle(width - 88, 24, 5, fill=PAPER_PINK)
    fig.text(width - 76, 28, "B1 ZDPD passthrough", size=10.5)
    fig.save(out_base)


def plot_per_file_archive_delta(rows: list[dict], summary: dict, out_base: Path) -> None:
    items = _paired_rows(rows)
    items.sort(key=lambda item: item["archive_delta"], reverse=True)
    width = 940
    row_h = 20
    height = max(460, 92 + row_h * len(items) + 28)
    fig = SvgFigure(width, height)
    left, top, right, bottom = 170, 48, 38, 36
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_v = max(item["archive_delta"] for item in items) * 1.14
    ticks = _nice_ticks(max_v, 5)
    for tick in ticks:
        x = left + (tick / max(ticks)) * plot_w
        fig.line(x, top, x, top + plot_h, color=GRID, width=0.9)
        fig.line(x, top + plot_h, x, top + plot_h + 4, color=TEXT, width=1.0)
        fig.text(x, top + plot_h + 18, f"{tick:g}", size=10, anchor="middle", color=MUTED)
    fig.line(left, top, left, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color=TEXT, width=1.0)
    fig.text(left, 22, "MS2 ablation: archive size penalty vs B0", size=15.5, weight="700")
    fig.text(left + plot_w / 2, height - 8, "Archive size increase vs B0 (%)", size=12.5, anchor="middle")
    agg_x = left + (float(summary["delta_archive_size_pct"] or 0.0) / max(ticks)) * plot_w
    fig.line(agg_x, top, agg_x, top + plot_h, color=PAPER_BLUE, width=1.2, dash="5,4")
    fig.text(agg_x + 6, top + 14, f"aggregate = {float(summary['delta_archive_size_pct'] or 0.0):.2f}%", size=10, color=PAPER_BLUE)
    for idx, item in enumerate(items):
        y = top + idx * row_h + 12
        bar_w = (item["archive_delta"] / max(ticks)) * plot_w
        fig.text(left - 10, y + 4, item["file_short"], size=10, anchor="end")
        fig.rect(left, y - 7, bar_w, 14, fill=PAPER_PINK, opacity=0.90)
        fig.text(left + bar_w + 6, y + 4, f"{item['archive_delta']:.1f}%", size=9.5, color=MUTED)
    fig.save(out_base)


def plot_ms2_cr_scatter(rows: list[dict], out_base: Path) -> None:
    items = _paired_rows(rows)
    vals = [v for item in items for v in (item["b0"], item["b1"])]
    max_v = max(vals) * 1.08
    ticks = _nice_ticks(max_v, 5)
    width, height = 520, 500
    fig = SvgFigure(width, height)
    left, top, right, bottom = 72, 32, 26, 66
    plot_w = width - left - right
    plot_h = height - top - bottom
    for tick in ticks:
        x = left + (tick / max(ticks)) * plot_w
        y = top + plot_h - (tick / max(ticks)) * plot_h
        fig.line(x, top, x, top + plot_h, color=GRID, width=0.9)
        fig.line(left, y, left + plot_w, y, color=GRID, width=0.9)
        fig.text(x, top + plot_h + 18, f"{tick:g}", size=10, anchor="middle", color=MUTED)
        fig.text(left - 8, y + 4, f"{tick:g}", size=10, anchor="end", color=MUTED)
    fig.line(left, top, left, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top, color="#9CA3AF", width=1.0, dash="5,4")
    fig.text(left + plot_w / 2, height - 10, "B0 MS2 section CR", size=12.5, anchor="middle")
    fig.text(20, top + plot_h / 2, "B1 MS2 section CR", size=12.5, anchor="middle", rotate=-90)
    fig.text(left, 18, "MS2 section CR: B0 vs B1", size=15.5, weight="700")
    top_gap = sorted(items, key=lambda item: item["cr_delta"], reverse=True)[:4]
    top_gap_names = {item["file"] for item in top_gap}
    for item in items:
        x = left + (item["b0"] / max(ticks)) * plot_w
        y = top + plot_h - (item["b1"] / max(ticks)) * plot_h
        fig.circle(x, y, 4.0, fill=PAPER_BLUE, opacity=0.88)
        if item["file"] in top_gap_names:
            fig.text(x + 6, y - 4, item["file_short"], size=9.0, color=MUTED)
    fig.save(out_base)


def plot_aggregate_archive_bytes(summary: dict, out_base: Path) -> None:
    b0 = float(summary["sum_b0_archive_bytes"] or 0.0) / (1024 ** 3)
    b1 = float(summary["sum_b1_archive_bytes"] or 0.0) / (1024 ** 3)
    max_v = max(b0, b1) * 1.22
    ticks = _nice_ticks(max_v, 4)
    width, height = 420, 390
    fig = SvgFigure(width, height)
    left, top, right, bottom = 62, 34, 24, 56
    plot_w = width - left - right
    plot_h = height - top - bottom
    for tick in ticks:
        y = _axis_y(tick, 0.0, max(ticks), top, plot_h)
        fig.line(left, y, left + plot_w, y, color=GRID, width=0.9)
        fig.line(left - 4, y, left, y, color=TEXT, width=1.0)
        fig.text(left - 8, y + 4, f"{tick:g}", size=10, anchor="end", color=MUTED)
    fig.line(left, top, left, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color=TEXT, width=1.0)
    fig.text(left, 18, "Aggregate archive bytes", size=15.5, weight="700")
    fig.text(18, top + plot_h / 2, "Archive size (GiB)", size=12.5, anchor="middle", rotate=-90)
    centers = [left + plot_w * 0.33, left + plot_w * 0.72]
    bar_w = 64
    for center, label, val, color in zip(centers, ["B0", "B1"], [b0, b1], [PAPER_BLUE, PAPER_PINK]):
        y = _axis_y(val, 0.0, max(ticks), top, plot_h)
        fig.rect(center - bar_w / 2, y, bar_w, top + plot_h - y, fill=color, opacity=0.92)
        fig.text(center, top + plot_h + 22, label, size=11, anchor="middle")
        fig.text(center, y - 8, f"{val:.2f} GiB", size=10.5, anchor="middle")
    fig.text(centers[1], _axis_y(b1, 0.0, max(ticks), top, plot_h) - 28, f"+{float(summary['delta_archive_size_pct'] or 0.0):.2f}%", size=12, weight="700", anchor="middle", color=MUTED)
    fig.save(out_base)


def plot_sci_panel_a(rows: list[dict], out_base: Path) -> None:
    items = _paired_rows(rows)
    width = max(1080, 96 + 34 * len(items))
    height = 380
    fig = SvgFigure(width, height)
    left, top, right, bottom = 72, 38, 18, 112
    plot_w = width - left - right
    plot_h = height - top - bottom
    vals = [v for item in items for v in (item["b0"], item["b1"])]
    ymin = 0.0
    ymax = max(vals) * 1.08
    ticks = _nice_ticks(ymax, 4)
    ymax_use = max(ticks)
    _draw_y_axis(fig, left=left, top=top, plot_w=plot_w, plot_h=plot_h, ymin=ymin, ymax=ymax_use, ticks=ticks, label="MS2 section compression ratio (x)")
    step = plot_w / max(len(items), 1)
    xs = [left + step * (idx + 0.5) for idx in range(len(items))]
    labels = [item["file_short"] for item in items]
    for x, item in zip(xs, items):
        y0 = _axis_y(item["b0"], ymin, ymax_use, top, plot_h)
        y1 = _axis_y(item["b1"], ymin, ymax_use, top, plot_h)
        fig.line(x, y0, x, y1, color=DOT_LINK, width=1.0)
        fig.circle(x, y1, 3.4, fill=PAPER_PINK)
        fig.circle(x, y0, 3.4, fill=PAPER_BLUE)
    _draw_x_ticks(fig, xs=xs, labels=labels, base_y=top + plot_h, rotate=-55.0, size=8.6, text_offset_y=10.5)
    fig.text(left + plot_w / 2, height - 8, "Benchmark files", size=12.5, anchor="middle")
    fig.text(left, 18, "A  Paired per-file MS2 section CR", size=15.5, weight="700")
    fig.circle(width - 240, 20, 4.6, fill=PAPER_BLUE)
    fig.text(width - 228, 24, "B0 Full MS2 dict", size=10.5)
    fig.circle(width - 118, 20, 4.6, fill=PAPER_PINK)
    fig.text(width - 106, 24, "B1 ZDPD passthrough", size=10.5)
    fig.save(out_base)


def plot_sci_panel_b(rows: list[dict], out_base: Path) -> None:
    items = _paired_rows(rows)
    b0_vals = [item["b0"] for item in items]
    b1_vals = [item["b1"] for item in items]
    cr_gain_pct = [((b0 / b1) - 1.0) * 100.0 if b1 > 0 else 0.0 for b0, b1 in zip(b0_vals, b1_vals)]
    width, height = 305, 410
    fig = SvgFigure(width, height)
    left, top, right, bottom = 76, 30, 18, 52
    plot_w = width - left - right
    plot_h = height - top - bottom
    ymin = 0.0
    ymax = max(b0_vals + b1_vals) * 1.08
    ticks = _nice_ticks(ymax, 4)
    ymax_use = max(ticks)
    _draw_y_axis(fig, left=left, top=top, plot_w=plot_w, plot_h=plot_h, ymin=ymin, ymax=ymax_use, ticks=ticks, label="MS2 section CR (x)")
    xs = [left + plot_w * 0.28, left + plot_w * 0.74]
    offsets = [(((idx % 7) / 6.0 - 0.5) if len(items) > 1 else 0.0) * 18 for idx in range(len(items))]
    for off, b0, b1 in zip(offsets, b0_vals, b1_vals):
        x0 = xs[0] + off
        x1 = xs[1] + off
        y0 = _axis_y(b0, ymin, ymax_use, top, plot_h)
        y1 = _axis_y(b1, ymin, ymax_use, top, plot_h)
        fig.line(x0, y0, x1, y1, color=DOT_LINK, width=0.9)
        fig.circle(x0, y0, 2.8, fill=PAPER_BLUE)
        fig.circle(x1, y1, 2.8, fill=PAPER_PINK)
    box_w = 30
    for x, vals, color in zip(xs, [b0_vals, b1_vals], [PAPER_BLUE, PAPER_PINK]):
        q1 = _percentile(vals, 0.25)
        med = _percentile(vals, 0.50)
        q3 = _percentile(vals, 0.75)
        iqr = q3 - q1
        low_bound = q1 - 1.5 * iqr
        high_bound = q3 + 1.5 * iqr
        whisk_low = min([v for v in vals if v >= low_bound] or [vals[0]])
        whisk_high = max([v for v in vals if v <= high_bound] or [vals[-1]])
        fig.line(x, _axis_y(whisk_low, ymin, ymax_use, top, plot_h), x, _axis_y(q1, ymin, ymax_use, top, plot_h), color=TEXT, width=1.0)
        fig.line(x, _axis_y(q3, ymin, ymax_use, top, plot_h), x, _axis_y(whisk_high, ymin, ymax_use, top, plot_h), color=TEXT, width=1.0)
        fig.line(x - box_w * 0.35, _axis_y(whisk_low, ymin, ymax_use, top, plot_h), x + box_w * 0.35, _axis_y(whisk_low, ymin, ymax_use, top, plot_h), color=TEXT, width=1.0)
        fig.line(x - box_w * 0.35, _axis_y(whisk_high, ymin, ymax_use, top, plot_h), x + box_w * 0.35, _axis_y(whisk_high, ymin, ymax_use, top, plot_h), color=TEXT, width=1.0)
        fig.rect(x - box_w / 2, _axis_y(q3, ymin, ymax_use, top, plot_h), box_w, _axis_y(q1, ymin, ymax_use, top, plot_h) - _axis_y(q3, ymin, ymax_use, top, plot_h), fill="#FFFFFF", stroke=TEXT, opacity=1.0)
        fig.line(x - box_w / 2, _axis_y(med, ymin, ymax_use, top, plot_h), x + box_w / 2, _axis_y(med, ymin, ymax_use, top, plot_h), color=TEXT, width=1.2)
    for x, label in zip(xs, ["B0", "B1"]):
        fig.line(x, top + plot_h, x, top + plot_h + 5, color=TEXT, width=1.0)
        fig.text(x, top + plot_h + 22, label, size=13, anchor="middle")
    p_value, effect, _, _ = _paired_wilcoxon_stats(b0_vals, b1_vals)
    p_text = f"p={p_value:.2e}" if math.isfinite(p_value) else "p=NA"
    e_text = f"r={effect:.3f}" if math.isfinite(effect) else "r=NA"
    fig.text(left + 6, top + 14, f"median gain = {_median(cr_gain_pct):.1f}%", size=11, color=MUTED)
    fig.text(left + 6, top + 30, f"Wilcoxon {p_text}", size=11, color=MUTED)
    fig.text(left + 6, top + 46, f"effect {e_text}", size=11, color=MUTED)
    fig.save(out_base)


def plot_sci_main_figure(rows: list[dict], out_base: Path) -> None:
    plot_sci_panel_a(rows, out_base.parent / "ms2_ablation_sci_panel_A_ms2_cr")
    plot_sci_panel_b(rows, out_base.parent / "ms2_ablation_sci_panel_B_distribution")
    items = _paired_rows(rows)
    width = max(1320, 110 + 34 * len(items))
    height = 400
    fig = SvgFigure(width, height)
    split_x = width - 250

    panel_a = (out_base.parent / "ms2_ablation_sci_panel_A_ms2_cr.svg").read_text(encoding="utf-8")
    panel_b = (out_base.parent / "ms2_ablation_sci_panel_B_distribution.svg").read_text(encoding="utf-8")

    def _inner(svg_text: str) -> str:
        start = svg_text.find(">")
        end = svg_text.rfind("</svg>")
        return svg_text[start + 1:end]

    fig.elements.append(f'<g transform="translate(0,0)">{_inner(panel_a)}</g>')
    fig.elements.append(f'<g transform="translate({split_x},18) scale(0.88,0.88)">{_inner(panel_b)}</g>')
    fig.save(out_base)


def plot_sci_archive_penalty(rows: list[dict], summary: dict, out_base: Path) -> None:
    items = _paired_rows(rows)
    items.sort(key=lambda item: item["archive_delta"], reverse=True)
    width = 920
    row_h = 20
    height = max(460, 96 + row_h * len(items) + 30)
    fig = SvgFigure(width, height)
    left, top, right, bottom = 170, 42, 32, 40
    plot_w = width - left - right
    plot_h = height - top - bottom
    max_v = max(item["archive_delta"] for item in items) * 1.14
    ticks = _nice_ticks(max_v, 5)
    for tick in ticks:
        x = left + (tick / max(ticks)) * plot_w
        fig.line(x, top, x, top + plot_h, color=GRID, width=0.9)
        fig.line(x, top + plot_h, x, top + plot_h + 4, color=TEXT, width=1.0)
        fig.text(x, top + plot_h + 18, f"{tick:g}", size=10, anchor="middle", color=MUTED)
    agg_x = left + (float(summary["delta_archive_size_pct"] or 0.0) / max(ticks)) * plot_w
    fig.line(agg_x, top, agg_x, top + plot_h, color=PAPER_BLUE, width=1.2, dash="5,4")
    fig.text(agg_x + 6, top + 14, f"aggregate = {float(summary['delta_archive_size_pct'] or 0.0):.2f}%", size=10, color=PAPER_BLUE)
    fig.line(left, top, left, top + plot_h, color=TEXT, width=1.0)
    fig.line(left, top + plot_h, left + plot_w, top + plot_h, color=TEXT, width=1.0)
    fig.text(left, 18, "Per-file archive penalty with aggregate reference", size=15.5, weight="700")
    fig.text(left + plot_w / 2, height - 8, "Archive size increase after removing MS2 dictionary (%)", size=12.5, anchor="middle")
    for idx, item in enumerate(items):
        y = top + idx * row_h + 12
        x = left + (item["archive_delta"] / max(ticks)) * plot_w
        fig.text(left - 10, y + 4, item["file_short"], size=10, anchor="end")
        fig.line(left, y, x, y, color=PAPER_PINK, width=1.3)
        fig.circle(x, y, 3.1, fill=PAPER_PINK)
    fig.save(out_base)


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    frac = pos - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def update_summary_html(rows: list[dict], summary: dict) -> None:
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
            f"<td>{_fmt(row.get('b0_ms2_cr'))}</td>"
            f"<td>{_fmt(row.get('b1_ms2_cr'))}</td>"
            f"<td>{_fmt(row.get('b0_archive_cr'))}</td>"
            f"<td>{_fmt(row.get('b1_archive_cr'))}</td>"
            f"<td>{_fmt(row.get('delta_archive_pct'))}</td>"
            "</tr>"
        )
    li = "\n".join(
        [
            f"<li><strong>Files total:</strong> {summary['n_files_total']}</li>",
            f"<li><strong>MS2-bearing files:</strong> {summary['n_files_ms2_bearing']}</li>",
            f"<li><strong>B0 MS2 aggregate CR:</strong> {_fmt(summary['b0_ms2_aggregate_cr'])}x</li>",
            f"<li><strong>B1 MS2 aggregate CR:</strong> {_fmt(summary['b1_ms2_aggregate_cr'])}x</li>",
            f"<li><strong>B0 archive aggregate CR:</strong> {_fmt(summary['b0_archive_aggregate_cr'])}x</li>",
            f"<li><strong>B1 archive aggregate CR:</strong> {_fmt(summary['b1_archive_aggregate_cr'])}x</li>",
            f"<li><strong>Archive size delta vs B0:</strong> +{_fmt(summary['delta_archive_size_pct'])}%</li>",
            f"<li><strong>MS2 dict contribution:</strong> +{_fmt(summary['ms2_dict_contribution_pct'])}%</li>",
        ]
    )
    html_text = f"""<!doctype html>
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
    th:first-child, th:nth-child(2), th:nth-child(3), td:first-child, td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
    thead th {{ background: #f3f4f6; position: sticky; top: 0; }}
  </style>
</head>
<body>
  <h1>MS2 Ablation B1</h1>
  <p>Hybrid archive calculation replacing TrackCodec MS2 window-native dictionary bytes with ZDPD MS2 passthrough bytes.</p>
  <ul>{li}</ul>
  <h2>Plots</h2>
  <div style="display:grid;grid-template-columns:repeat(2,minmax(320px,1fr));gap:18px;align-items:start;">
    <figure style="margin:0;"><img src="plots/ms2_ablation_summary_cr_b0_vs_b1.svg" style="width:100%;height:auto;"><figcaption>B0 vs B1 aggregate compression ratios.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/ms2_ablation_aggregate_archive_bytes.svg" style="width:100%;height:auto;"><figcaption>Aggregate archive size increase after removing the MS2 dictionary.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/ms2_ablation_per_file_archive_delta_vs_b0.svg" style="width:100%;height:auto;"><figcaption>Per-file archive size penalty relative to B0.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/ms2_ablation_b0_vs_b1_ms2_cr_scatter.svg" style="width:100%;height:auto;"><figcaption>Per-file MS2 section CR under B0 and B1.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/ms2_ablation_sci_main_figure.svg" style="width:100%;height:auto;"><figcaption>Two-panel scientific figure.</figcaption></figure>
    <figure style="margin:0;"><img src="plots/ms2_ablation_sci_archive_penalty_paired.svg" style="width:100%;height:auto;"><figcaption>Per-file archive penalty with aggregate reference.</figcaption></figure>
  </div>
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
    (INPUT_DIR / "ms2_ablation_summary.html").write_text(html_text, encoding="utf-8")


def _fmt(value, digits: int = 3) -> str:
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def main() -> int:
    rows = _read_csv(CSV_PATH)
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    PLOT_DIR.mkdir(parents=True, exist_ok=True)
    plot_summary_cr(summary, PLOT_DIR / "ms2_ablation_summary_cr_b0_vs_b1")
    plot_per_file_archive_delta(rows, summary, PLOT_DIR / "ms2_ablation_per_file_archive_delta_vs_b0")
    plot_ms2_cr_scatter(rows, PLOT_DIR / "ms2_ablation_b0_vs_b1_ms2_cr_scatter")
    plot_aggregate_archive_bytes(summary, PLOT_DIR / "ms2_ablation_aggregate_archive_bytes")
    plot_sci_panel_a(rows, PLOT_DIR / "ms2_ablation_sci_panel_A_ms2_cr")
    plot_sci_panel_b(rows, PLOT_DIR / "ms2_ablation_sci_panel_B_distribution")
    plot_sci_main_figure(rows, PLOT_DIR / "ms2_ablation_sci_main_figure")
    plot_sci_archive_penalty(rows, summary, PLOT_DIR / "ms2_ablation_sci_archive_penalty_paired")
    update_summary_html(rows, summary)
    print(PLOT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
