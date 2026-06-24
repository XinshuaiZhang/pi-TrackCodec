from __future__ import annotations

import csv
import json
import math
from pathlib import Path

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


def _get_plt():
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = "Times New Roman"
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    import matplotlib.pyplot as plt

    return plt


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


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
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            ranks[order[k]] = rank
        i = j
    w_pos = sum(rank for rank, diff in zip(ranks, diffs) if diff > 0)
    w_neg = sum(rank for rank, diff in zip(ranks, diffs) if diff < 0)
    if scipy_stats is not None:
        try:
            _, p_value = scipy_stats.wilcoxon(x, y, zero_method="wilcox", alternative="two-sided", method="auto")
        except Exception:
            p_value = _exact_wilcoxon_p_from_ranks(ranks, w_pos, w_neg)
    else:
        p_value = _exact_wilcoxon_p_from_ranks(ranks, w_pos, w_neg)
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


def _format_p_value(value: float) -> str:
    if not math.isfinite(value):
        return "NA"
    if value < 1e-4:
        return f"{value:.1e}"
    return f"{value:.3g}"


def _paired_rows(rows: list[dict]) -> list[dict]:
    paired = []
    for row in rows:
        if row.get("file") == "AGGREGATE":
            continue
        if row.get("b0_ms2_cr") in (None, "") or row.get("b1_ms2_cr") in (None, ""):
            continue
        b0 = float(row["b0_ms2_cr"])
        b1 = float(row["b1_ms2_cr"])
        paired.append(
            {
                "label": str(row.get("file_label") or row.get("file")),
                "b0": b0,
                "b1": b1,
                "cr_delta": b0 - b1,
            }
        )
    paired.sort(key=lambda item: item["cr_delta"], reverse=True)
    return paired


def _save(fig, out_base: Path) -> None:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".svg", ".png", ".pdf"):
        fig.savefig(out_base.with_suffix(suffix), bbox_inches="tight")


def plot_panel_a(rows: list[dict]) -> None:
    plt = _get_plt()
    paired = _paired_rows(rows)
    file_ids = [f"File {idx + 1}" for idx in range(len(paired))]
    b0_vals = [item["b0"] for item in paired]
    b1_vals = [item["b1"] for item in paired]
    xpos = list(range(len(paired)))

    fig_w = max(10.6, 0.34 * max(len(paired), 1) + 2.8)
    fig, ax = plt.subplots(figsize=(fig_w, 3.9))
    for idx, item in enumerate(paired):
        ax.plot([idx, idx], [item["b1"], item["b0"]], color="#c7cdd6", linewidth=1.0, zorder=1)
    ax.scatter(xpos, b1_vals, s=20, color=PAPER_PINK, label="B1 ZDPD passthrough", zorder=3)
    ax.scatter(xpos, b0_vals, s=20, color=PAPER_BLUE, label="B0 Full MS2 dict", zorder=4)
    ax.set_xticks(xpos, file_ids, rotation=-55, ha="right", fontsize=7.4)
    ax.set_xlabel("Benchmark files")
    ax.set_ylabel("MS2 section compression ratio (x)")
    ax.grid(False)
    ax.legend(frameon=False, loc="upper right", fontsize=8.5, handletextpad=0.4, borderpad=0.2)
    ax.set_title("A  Paired per-file MS2 section CR", loc="left", fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.subplots_adjust(left=0.08, right=0.995, top=0.88, bottom=0.34)
    _save(fig, PLOT_DIR / "ms2_ablation_sci_panel_A_ms2_cr")
    plt.close(fig)


def plot_panel_b(rows: list[dict]) -> None:
    plt = _get_plt()
    paired = _paired_rows(rows)
    b0_vals = [item["b0"] for item in paired]
    b1_vals = [item["b1"] for item in paired]
    cr_gain_pct = [((b0 / b1) - 1.0) * 100.0 if b1 > 0 else 0.0 for b0, b1 in zip(b0_vals, b1_vals)]
    median_gain_pct = _median(cr_gain_pct)
    p_value, effect, _, _ = _paired_wilcoxon_stats(b0_vals, b1_vals)

    x = [0, 1]
    n = max(len(b0_vals), 1)
    offsets = [(((idx % 7) / 6.0 - 0.5) if n > 1 else 0.0) * 0.12 for idx in range(n)]
    fig, ax = plt.subplots(figsize=(2.55, 4.10))
    for idx, (b0, b1) in enumerate(zip(b0_vals, b1_vals)):
        off = offsets[idx]
        ax.plot([x[0] + off, x[1] + off], [b0, b1], color="#c7cdd6", linewidth=0.9, zorder=1, alpha=0.8)
    ax.scatter([x[0] + o for o in offsets], b0_vals, color=PAPER_BLUE, s=18, zorder=3)
    ax.scatter([x[1] + o for o in offsets], b1_vals, color=PAPER_PINK, s=18, zorder=3)
    ax.boxplot(
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
    ax.text(
        0.04,
        0.98,
        f"median gain = {median_gain_pct:.1f}%\nWilcoxon p = {_format_p_value(p_value)}\neffect r = {effect:.2f}",
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10.5,
    )
    ax.set_xticks(x, ["B0", "B1"])
    ax.set_ylabel("MS2 section CR (x)", fontsize=12.5)
    ax.tick_params(axis="both", which="major", labelsize=11.0)
    ax.grid(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.subplots_adjust(left=0.30, right=0.98, top=0.97, bottom=0.12)
    _save(fig, PLOT_DIR / "ms2_ablation_sci_panel_B_distribution")
    plt.close(fig)


def main() -> int:
    rows = _read_csv(CSV_PATH)
    SUMMARY_PATH.read_text(encoding="utf-8")
    plot_panel_a(rows)
    plot_panel_b(rows)
    print(PLOT_DIR / "ms2_ablation_sci_panel_A_ms2_cr.pdf")
    print(PLOT_DIR / "ms2_ablation_sci_panel_B_distribution.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
