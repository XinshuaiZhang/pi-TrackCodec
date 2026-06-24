from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MS1_OUT = ROOT / "outputs" / "ms1_ablation_unified30"
TABLE_DIR = MS1_OUT / "tables"
PLOT_DIR = MS1_OUT / "plots_matplotlib"

VARIANT_ORDER = ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "F1"]
VARIANT_COLORS = {
    "A0": "#D95F02",
    "A1": "#6BAED6",
    "A2": "#3182BD",
    "A3": "#756BB1",
    "A4": "#9E9AC8",
    "A5": "#74A66A",
    "A6": "#A1D99B",
    "F1": "#B84C4C",
}


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    matplotlib.rcParams["svg.fonttype"] = "none"
    matplotlib.rcParams["pdf.fonttype"] = 42
    matplotlib.rcParams["ps.fonttype"] = 42
    import matplotlib.pyplot as plt

    return plt


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _f(row: dict, key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key)
        if value in (None, "", "nan"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _save(fig, out_base: Path) -> None:
    out_base.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".svg", ".png", ".pdf"):
        kwargs = {"bbox_inches": "tight"}
        if suffix == ".png":
            kwargs["dpi"] = 300
        fig.savefig(out_base.with_suffix(suffix), **kwargs)


def plot_summary_cr(summary_rows: list[dict]) -> None:
    plt = _plt()
    rows = [row for row in summary_rows if row["variant_id"] in VARIANT_ORDER]
    rows.sort(key=lambda row: VARIANT_ORDER.index(row["variant_id"]))
    x = list(range(len(rows)))
    vals = [_f(row, "aggregate_ms1_cr") for row in rows]
    labels = [row["variant_id"] for row in rows]
    colors = [VARIANT_COLORS.get(label, "#6B7280") for label in labels]

    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    ax.bar(x, vals, color=colors, edgecolor="#111827", linewidth=0.4)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Aggregate MS1 CR (x)")
    ax.set_title("MS1 ablation compression ratio", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for xi, val in zip(x, vals):
        ax.text(xi, val + max(vals) * 0.025, f"{val:.2f}", ha="center", va="bottom", fontsize=8)
    _save(fig, PLOT_DIR / "matplotlib_ms1_aggregate_cr")
    plt.close(fig)


def plot_size_delta(summary_rows: list[dict]) -> None:
    plt = _plt()
    rows = [row for row in summary_rows if row["variant_id"] in VARIANT_ORDER and row["variant_id"] != "A0"]
    rows.sort(key=lambda row: VARIANT_ORDER.index(row["variant_id"]))
    x = list(range(len(rows)))
    vals = [_f(row, "size_delta_vs_a0_pct") for row in rows]
    labels = [row["variant_id"] for row in rows]
    colors = [VARIANT_COLORS.get(label, "#6B7280") for label in labels]

    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    ax.bar(x, vals, color=colors, edgecolor="#111827", linewidth=0.4)
    ax.axhline(0, color="#111827", linewidth=0.8)
    ax.set_xticks(x, labels)
    ax.set_ylabel("Compressed size delta vs A0 (%)")
    ax.set_title("MS1 ablation size penalty", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save(fig, PLOT_DIR / "matplotlib_ms1_size_delta_vs_a0")
    plt.close(fig)


def plot_boxplot(per_file_rows: list[dict]) -> None:
    plt = _plt()
    values = []
    labels = []
    for variant in VARIANT_ORDER:
        vals = [_f(row, "ms1_cr") for row in per_file_rows if row.get("variant_id") == variant and _f(row, "ms1_cr") > 0]
        if vals:
            values.append(vals)
            labels.append(variant)

    fig, ax = plt.subplots(figsize=(7.8, 4.4))
    parts = ax.boxplot(values, labels=labels, patch_artist=True, widths=0.55)
    for patch, label in zip(parts["boxes"], labels):
        patch.set_facecolor(VARIANT_COLORS.get(label, "#CBD5E1"))
        patch.set_alpha(0.7)
        patch.set_edgecolor("#111827")
    for item in parts["medians"]:
        item.set_color("#111827")
        item.set_linewidth(1.3)
    ax.set_ylim(bottom=1.0)
    ax.set_ylabel("MS1 CR (x)")
    ax.set_title("MS1 ablation per-file distribution", loc="left", fontweight="bold")
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.8)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    _save(fig, PLOT_DIR / "matplotlib_ms1_cr_boxplot")
    plt.close(fig)


def main() -> int:
    summary_rows = _read_csv(TABLE_DIR / "ablation_ms1_summary_vs_a0.csv")
    per_file_rows = _read_csv(TABLE_DIR / "ablation_ms1_per_file_delta_vs_a0.csv")
    plot_summary_cr(summary_rows)
    plot_size_delta(summary_rows)
    plot_boxplot(per_file_rows)
    print(PLOT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
