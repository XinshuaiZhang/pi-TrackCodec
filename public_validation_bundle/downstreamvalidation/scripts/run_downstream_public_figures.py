from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
PYTHON = sys.executable


def _run(cmd: list[str]) -> None:
    print("RUN", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def _run_optional(cmd: list[str], label: str) -> None:
    print("RUN optional", label, ":", " ".join(cmd), flush=True)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"WARNING optional step failed: {label}: exit={exc.returncode}", flush=True)


def main() -> int:
    outputs = ROOT / "outputs"
    search_root = outputs / "search_validation_figures"
    search_tables = search_root / "tables"
    xic_root = outputs / "stream_loss_downstream24_top2500"
    xic_target_table = xic_root / "tables" / "xic_target_metrics.csv"

    _run(
        [
            PYTHON,
            str(SCRIPT_DIR / "plot_diann_recovery_results.py"),
            "--table-root",
            str(search_tables),
            "--out-root",
            str(search_root),
        ]
    )
    _run(
        [
            PYTHON,
            str(SCRIPT_DIR / "plot_dda_psm_recovery_results.py"),
            "--input",
            str(search_tables / "dda_psm_ion_recovery_summary.csv"),
            "--file-set-table",
            str(search_tables / "dda_ionquant_pairwise_summary_input.csv"),
            "--out-root",
            str(search_root),
        ]
    )
    _run(
        [
            PYTHON,
            str(SCRIPT_DIR / "plot_combined_search_recovery_results.py"),
            "--table-root",
            str(search_tables),
            "--out-root",
            str(search_root),
        ]
    )
    _run(
        [
            PYTHON,
            str(SCRIPT_DIR / "plot_xic_topk_gradient_from_maxk.py"),
            "--target-table",
            str(xic_target_table),
            "--out-root",
            str(xic_root),
            "--top-k",
            "500",
            "1000",
            "1500",
            "2000",
            "2500",
            "--plot-error-vs-signal",
        ]
    )
    _run_optional([PYTHON, str(SCRIPT_DIR / "ensure_pdf_outputs.py")], "ensure PDF companions")
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
