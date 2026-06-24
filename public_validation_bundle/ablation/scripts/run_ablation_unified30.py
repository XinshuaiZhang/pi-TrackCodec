from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
PYTHON = sys.executable
PLOT_PYTHON = os.environ.get("PLOT_PYTHON", "").strip() or PYTHON


def _run(cmd: list[str]) -> None:
    print("RUN", " ".join(cmd))
    subprocess.run(cmd, check=True)


def _run_optional(cmd: list[str], label: str) -> None:
    print("RUN optional", label, ":", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"WARNING optional step failed: {label}: exit={exc.returncode}")


def main() -> int:
    inputs = ROOT / "inputs"
    outputs = ROOT / "outputs"
    ms1_out = outputs / "ms1_ablation_unified30"
    ms2_out = outputs / "ms2_ablation_unified30"

    _run(
        [
            PLOT_PYTHON,
            str(SCRIPT_DIR / "ablation_result_aggregation_unified30.py"),
            "--nmi-ablation",
            "--component-csv",
            str(inputs / "component_A0_A6_per_file_input.csv"),
            "--f1-csv",
            str(inputs / "ablation_F1_full8_fresh_per_file.csv"),
            str(inputs / "ablation_F1_selected_fresh_per_file.csv"),
            str(inputs / "ablation_F1_new_files_fresh_per_file.csv"),
            "--output-dir",
            str(ms1_out),
        ]
    )

    _run_optional([PLOT_PYTHON, str(SCRIPT_DIR / "plot_ms1_ablation_matplotlib_unified30.py")], "MS1 matplotlib figures")

    _run(
        [
            PYTHON,
            str(SCRIPT_DIR / "ms2_ablation_b1_unified30.py"),
            "--table-dir",
            str(inputs),
            "--output-dir",
            str(ms2_out),
        ]
    )

    _run([PLOT_PYTHON, str(SCRIPT_DIR / "plot_ms2_ablation_unified30.py")])
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
