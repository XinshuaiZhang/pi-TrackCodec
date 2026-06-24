from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DRIVER_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = DRIVER_DIR.parent
ROOT = RELEASE_ROOT.parents[2]
sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.datasets import FULL8_PROFILE_FILES
from TrackCodec.production.common.runtime_env import (
    assert_native_speedups_available,
    assert_python_native_speedups_available,
    resolve_trackcodec_python,
)
sys.path.insert(0, str(DRIVER_DIR))
from stackzdpd_validation_benchmark import (
    BENCHMARK_TIME_SCOPE_VERSION,
    COLOR_MAP,
    DISPLAY_NAME,
    META_ORDER,
    MS1_ORDER,
    MS2_ORDER,
    NO_WHOLE_FILE_MZML_DECODER_SCOPE,
    SECTION_NUMERIC_DECODE_TIME_SCOPE,
    SECTION_NUMERIC_ENCODE_TIME_SCOPE,
    SECTION_FAMILY_MAP,
    WHOLE_ORDER,
    WHOLE_VALIDATED_ROUNDTRIP_MAP,
    _aggregate_whole_rows,
    _append_jsonl,
    _detect_kind,
    _json_safe,
    _make_alias_map,
    _normalise_section_methods_for_reporting,
    _sum_numeric_times,
    _summarize_section,
    _write_csv,
    plot_compression_bar_custom,
    plot_speed_bar,
)
from single_file_benchmark import CONTAINER_METADATA_METHOD


STACKZDPD_VALIDATION_ENV = "TRACKCODEC_STACKZDPD_VALIDATION_DIR"
PROPOSAL18_ENV = "TRACKCODEC_PROPOSAL18_DIR"
DEFAULT_RESULT_ROOT = ROOT.parent / "benchmark_results" / "trackcodec_multi_dataset_supervised_20260426"
WORKER_SCRIPT = DRIVER_DIR / "single_file_benchmark.py"


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    display_name: str
    files: tuple[Path, ...]


@dataclass
class RunningTask:
    dataset_key: str
    stage: str
    file_path: Path
    shard_root: Path
    attempt: int
    stdout_path: Path
    stderr_path: Path
    proc: subprocess.Popen


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return slug.strip("._") or "file"


def _discover_mzml(input_dir: Path) -> list[Path]:
    return sorted([path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".mzml"], key=lambda p: p.name.lower())


def _required_input_dir(env_name: str, dataset_key: str) -> Path:
    value = os.environ.get(env_name)
    if not value:
        raise ValueError(f"Dataset '{dataset_key}' requires {env_name} to point to its mzML input directory.")
    return Path(value).expanduser()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _dataset_specs(selected_datasets: set[str]) -> list[DatasetSpec]:
    specs: list[DatasetSpec] = []
    if "full8" in selected_datasets:
        specs.append(DatasetSpec("full8", "Full8", tuple(Path(path).resolve() for path in FULL8_PROFILE_FILES)))
    if "stackzdpd_validation" in selected_datasets:
        input_dir = _required_input_dir(STACKZDPD_VALIDATION_ENV, "stackzdpd_validation")
        specs.append(DatasetSpec("stackzdpd_validation", "StackZDPD Validation", tuple(path.resolve() for path in _discover_mzml(input_dir))))
    if "proposal18" in selected_datasets:
        input_dir = _required_input_dir(PROPOSAL18_ENV, "proposal18")
        specs.append(DatasetSpec("proposal18", "Proposal 18", tuple(path.resolve() for path in _discover_mzml(input_dir))))
    return specs


def _parse_skip_file_names(cli_values: list[str] | None) -> set[str]:
    values: list[str] = []
    env_value = os.environ.get("TRACKCODEC_SKIP_FILE_NAMES", "")
    if env_value:
        values.extend(env_value.split(","))
    if cli_values:
        for item in cli_values:
            values.extend(str(item).split(","))
    return {value.strip() for value in values if value.strip()}


def _filter_skipped_files(dataset: DatasetSpec, skip_file_names: set[str]) -> DatasetSpec:
    if not skip_file_names:
        return dataset
    return DatasetSpec(
        dataset.key,
        dataset.display_name,
        tuple(path for path in dataset.files if path.name not in skip_file_names),
    )


def _dataset_root(result_root: Path, dataset_key: str) -> Path:
    return result_root / dataset_key


def _shard_root(result_root: Path, dataset_key: str, file_path: Path) -> Path:
    slug = _slugify(file_path.name)
    return _dataset_root(result_root, dataset_key) / "per_file" / slug


def _section_shard_root(result_root: Path, dataset_key: str, file_path: Path) -> Path:
    return _shard_root(result_root, dataset_key, file_path) / "section"


def _whole_shard_root(result_root: Path, dataset_key: str, file_path: Path) -> Path:
    return _shard_root(result_root, dataset_key, file_path) / "whole"


def _section_done(result_root: Path, dataset_key: str, file_path: Path) -> bool:
    root = _section_shard_root(result_root, dataset_key, file_path)
    file_name = file_path.name
    return all(
        (
            root / "compression_results" / f"{section}_sidecars" / f"{file_name}.{section}_methods.json"
        ).exists()
        for section in ("ms1", "ms2", "metadata")
    )


def _whole_done(result_root: Path, dataset_key: str, file_path: Path) -> bool:
    root = _whole_shard_root(result_root, dataset_key, file_path)
    file_name = file_path.name
    paths = [
        root / "compression_results" / f"{file_name}.whole_archive_stats.json",
        root / "compression_results" / f"{file_name}.strict_q6.whole_archive_stats.json",
    ]
    for path in paths:
        payload = _load_json(path)
        if not payload or not payload.get("compare_completed"):
            return False
    return True


def _load_section_methods(section_root: Path, file_name: str, section_label: str) -> dict | None:
    payload = _load_json(section_root / "compression_results" / f"{section_label}_sidecars" / f"{file_name}.{section_label}_methods.json")
    if not payload:
        return None
    methods = payload.get("methods")
    return methods if isinstance(methods, dict) else None


def _build_overall_container_methods(ms1_methods: dict, ms2_methods: dict, meta_methods: dict) -> dict:
    ms1_methods = _normalise_section_methods_for_reporting(ms1_methods)
    ms2_methods = _normalise_section_methods_for_reporting(ms2_methods)
    meta_methods = _normalise_section_methods_for_reporting(meta_methods)
    container_meta = meta_methods[CONTAINER_METADATA_METHOD]
    zdpd_encode = _sum_numeric_times(ms1_methods["zdpd_baseline"], ms2_methods["zdpd_baseline"], container_meta, key="numeric_encode_time_s")
    zdpd_decode = _sum_numeric_times(ms1_methods["zdpd_baseline"], ms2_methods["zdpd_baseline"], container_meta, key="numeric_decode_time_s")
    stack_encode = _sum_numeric_times(
        ms1_methods["stack_zdpd_baseline"],
        ms2_methods["stack_zdpd_baseline"],
        container_meta,
        key="numeric_encode_time_s",
    )
    stack_decode = _sum_numeric_times(
        ms1_methods["stack_zdpd_baseline"],
        ms2_methods["stack_zdpd_baseline"],
        container_meta,
        key="numeric_decode_time_s",
    )
    return {
        "raw": {
            "raw_bytes": ms1_methods["raw"]["raw_bytes"] + ms2_methods["raw"]["raw_bytes"] + meta_methods["raw"]["raw_bytes"],
            "compressed_bytes": ms1_methods["raw"]["raw_bytes"] + ms2_methods["raw"]["raw_bytes"] + meta_methods["raw"]["raw_bytes"],
            "compression_ratio": 1.0,
            "encode_time_s": 0.0,
            "decode_time_s": 0.0,
        },
        "gzip": {
            "raw_bytes": ms1_methods["gzip"]["raw_bytes"] + ms2_methods["gzip"]["raw_bytes"] + meta_methods["gzip"]["raw_bytes"],
            "compressed_bytes": ms1_methods["gzip"]["compressed_bytes"] + ms2_methods["gzip"]["compressed_bytes"] + meta_methods["gzip"]["compressed_bytes"],
            "compression_ratio": (
                ms1_methods["gzip"]["raw_bytes"] + ms2_methods["gzip"]["raw_bytes"] + meta_methods["gzip"]["raw_bytes"]
            )
            / (
                ms1_methods["gzip"]["compressed_bytes"]
                + ms2_methods["gzip"]["compressed_bytes"]
                + meta_methods["gzip"]["compressed_bytes"]
            ),
            "encode_time_s": ms1_methods["gzip"]["encode_time_s"] + ms2_methods["gzip"]["encode_time_s"] + meta_methods["gzip"]["encode_time_s"],
            "decode_time_s": ms1_methods["gzip"]["decode_time_s"] + ms2_methods["gzip"]["decode_time_s"] + meta_methods["gzip"]["decode_time_s"],
        },
        "zlib": {
            "raw_bytes": ms1_methods["zlib"]["raw_bytes"] + ms2_methods["zlib"]["raw_bytes"] + meta_methods["zlib"]["raw_bytes"],
            "compressed_bytes": ms1_methods["zlib"]["compressed_bytes"] + ms2_methods["zlib"]["compressed_bytes"] + meta_methods["zlib"]["compressed_bytes"],
            "compression_ratio": (
                ms1_methods["zlib"]["raw_bytes"] + ms2_methods["zlib"]["raw_bytes"] + meta_methods["zlib"]["raw_bytes"]
            )
            / (
                ms1_methods["zlib"]["compressed_bytes"]
                + ms2_methods["zlib"]["compressed_bytes"]
                + meta_methods["zlib"]["compressed_bytes"]
            ),
            "encode_time_s": ms1_methods["zlib"]["encode_time_s"] + ms2_methods["zlib"]["encode_time_s"] + meta_methods["zlib"]["encode_time_s"],
            "decode_time_s": ms1_methods["zlib"]["decode_time_s"] + ms2_methods["zlib"]["decode_time_s"] + meta_methods["zlib"]["decode_time_s"],
        },
        "zstd-9": {
            "raw_bytes": ms1_methods["zstd-9"]["raw_bytes"] + ms2_methods["zstd-9"]["raw_bytes"] + meta_methods["zstd-9"]["raw_bytes"],
            "compressed_bytes": ms1_methods["zstd-9"]["compressed_bytes"] + ms2_methods["zstd-9"]["compressed_bytes"] + meta_methods["zstd-9"]["compressed_bytes"],
            "compression_ratio": (
                ms1_methods["zstd-9"]["raw_bytes"] + ms2_methods["zstd-9"]["raw_bytes"] + meta_methods["zstd-9"]["raw_bytes"]
            )
            / (
                ms1_methods["zstd-9"]["compressed_bytes"]
                + ms2_methods["zstd-9"]["compressed_bytes"]
                + meta_methods["zstd-9"]["compressed_bytes"]
            ),
            "encode_time_s": ms1_methods["zstd-9"]["encode_time_s"] + ms2_methods["zstd-9"]["encode_time_s"] + meta_methods["zstd-9"]["encode_time_s"],
            "decode_time_s": ms1_methods["zstd-9"]["decode_time_s"] + ms2_methods["zstd-9"]["decode_time_s"] + meta_methods["zstd-9"]["decode_time_s"],
        },
        "zdpd_container": {
            "raw_bytes": ms1_methods["zdpd_baseline"]["raw_bytes"] + ms2_methods["zdpd_baseline"]["raw_bytes"] + container_meta["raw_bytes"],
            "compressed_bytes": ms1_methods["zdpd_baseline"]["compressed_bytes"] + ms2_methods["zdpd_baseline"]["compressed_bytes"] + container_meta["compressed_bytes"],
            "compression_ratio": (
                ms1_methods["zdpd_baseline"]["raw_bytes"] + ms2_methods["zdpd_baseline"]["raw_bytes"] + container_meta["raw_bytes"]
            )
            / (
                ms1_methods["zdpd_baseline"]["compressed_bytes"]
                + ms2_methods["zdpd_baseline"]["compressed_bytes"]
                + container_meta["compressed_bytes"]
            ),
            "encode_time_s": zdpd_encode,
            "decode_time_s": zdpd_decode,
            "numeric_encode_time_s": zdpd_encode,
            "numeric_decode_time_s": zdpd_decode,
            "metadata_method": CONTAINER_METADATA_METHOD,
            "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
            "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
        },
        "stack_zdpd_container": {
            "raw_bytes": ms1_methods["stack_zdpd_baseline"]["raw_bytes"] + ms2_methods["stack_zdpd_baseline"]["raw_bytes"] + container_meta["raw_bytes"],
            "compressed_bytes": ms1_methods["stack_zdpd_baseline"]["compressed_bytes"] + ms2_methods["stack_zdpd_baseline"]["compressed_bytes"] + container_meta["compressed_bytes"],
            "compression_ratio": (
                ms1_methods["stack_zdpd_baseline"]["raw_bytes"]
                + ms2_methods["stack_zdpd_baseline"]["raw_bytes"]
                + container_meta["raw_bytes"]
            )
            / (
                ms1_methods["stack_zdpd_baseline"]["compressed_bytes"]
                + ms2_methods["stack_zdpd_baseline"]["compressed_bytes"]
                + container_meta["compressed_bytes"]
            ),
            "encode_time_s": stack_encode,
            "decode_time_s": stack_decode,
            "numeric_encode_time_s": stack_encode,
            "numeric_decode_time_s": stack_decode,
            "metadata_method": CONTAINER_METADATA_METHOD,
            "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
            "decode_time_scope": SECTION_NUMERIC_DECODE_TIME_SCOPE,
        },
        "ours_eqfidelity": {
            "raw_bytes": ms1_methods["ours_eqfidelity"]["raw_bytes"] + ms2_methods["ours_eqfidelity"]["raw_bytes"] + meta_methods["ours_metadata"]["raw_bytes"],
            "compressed_bytes": ms1_methods["ours_eqfidelity"]["compressed_bytes"] + ms2_methods["ours_eqfidelity"]["compressed_bytes"] + meta_methods["ours_metadata"]["compressed_bytes"],
            "compression_ratio": (
                ms1_methods["ours_eqfidelity"]["raw_bytes"]
                + ms2_methods["ours_eqfidelity"]["raw_bytes"]
                + meta_methods["ours_metadata"]["raw_bytes"]
            )
            / (
                ms1_methods["ours_eqfidelity"]["compressed_bytes"]
                + ms2_methods["ours_eqfidelity"]["compressed_bytes"]
                + meta_methods["ours_metadata"]["compressed_bytes"]
            ),
            "encode_time_s": ms1_methods["ours_eqfidelity"]["encode_time_s"] + ms2_methods["ours_eqfidelity"]["encode_time_s"] + meta_methods["ours_metadata"]["encode_time_s"],
            "decode_time_s": ms1_methods["ours_eqfidelity"]["decode_time_s"] + ms2_methods["ours_eqfidelity"]["decode_time_s"] + meta_methods["ours_metadata"]["decode_time_s"],
        },
    }


def _plot_whole_boxplot_generic(per_file_rows: list[dict], output_png: Path, dataset_title: str) -> None:
    order = [label for label in WHOLE_ORDER if any(row["method"] == label for row in per_file_rows)]
    data = [[float(row["compression_ratio"]) for row in per_file_rows if row["method"] == label] for label in order]
    fig, ax = plt.subplots(figsize=(13, 9))
    box = ax.boxplot(data, labels=[DISPLAY_NAME[label] for label in order], patch_artist=True, showfliers=False)
    for patch, label in zip(box["boxes"], order):
        patch.set_facecolor(COLOR_MAP[label])
        patch.set_alpha(0.78)
    for idx, label in enumerate(order, start=1):
        ys = [float(row["compression_ratio"]) for row in per_file_rows if row["method"] == label]
        xs = np.linspace(idx - 0.10, idx + 0.10, len(ys)) if len(ys) > 1 else [idx]
        ax.scatter(xs, ys, color="black", s=22, alpha=0.8, zorder=3)
    ax.set_title(f"{dataset_title} Whole-file Compression Ratio Distribution", fontsize=18)
    ax.set_ylabel("Compression Ratio", fontsize=15)
    ax.grid(axis="y", alpha=0.3)
    ax.tick_params(axis="x", labelrotation=38, labelsize=11)
    ax.tick_params(axis="y", labelsize=12)
    plt.tight_layout()
    plt.savefig(output_png, dpi=150)
    plt.close(fig)


def _plot_whole_line_generic(per_file_rows: list[dict], output_png: Path, files: list[Path], alias_map: dict[str, str], dataset_title: str) -> None:
    order = [label for label in WHOLE_ORDER if any(row["method"] == label for row in per_file_rows)]
    file_order = [alias_map[path.name] for path in files]
    fig, ax = plt.subplots(figsize=(11.8, 6.8))
    x = np.arange(len(file_order))
    for label in order:
        rows = [row for row in per_file_rows if row["method"] == label]
        mapping = {alias_map[row["file"]]: float(row["compression_ratio"]) for row in rows}
        y = [mapping[name] for name in file_order if name in mapping]
        local_x = [idx for idx, name in enumerate(file_order) if name in mapping]
        highlight = label in {"ours_whole_archive", "ours_strict_q6_whole_archive"}
        lw = 2.8 if label == "ours_whole_archive" else (2.5 if label == "ours_strict_q6_whole_archive" else 1.8)
        alpha = 1.0 if highlight else 0.82
        ax.plot(local_x, y, marker="o", linewidth=lw, color=COLOR_MAP[label], alpha=alpha, label=DISPLAY_NAME[label])
        if highlight and y:
            offset = max(0.015 * max(y), 0.02)
            for xi, yi in zip(local_x, y):
                ax.text(xi, yi + offset, f"{yi:.2f}x", ha="center", va="bottom", fontsize=10, color=COLOR_MAP[label])
    ax.set_title(f"{dataset_title} Whole-file Compression Ratio Per File", fontsize=18)
    ax.set_ylabel("Compression Ratio", fontsize=15)
    ax.set_xticks(x)
    ax.set_xticklabels(file_order, rotation=0, ha="center", fontsize=11)
    ax.tick_params(axis="y", labelsize=12)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=10, loc="upper left", bbox_to_anchor=(1.01, 1.0))
    plt.tight_layout()
    plt.savefig(output_png, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _build_dataset_manifest(dataset_root: Path, dataset: DatasetSpec) -> None:
    manifest = {
        "dataset_key": dataset.key,
        "display_name": dataset.display_name,
        "n_files": len(dataset.files),
        "files": [str(path) for path in dataset.files],
        "generated_at": _now_iso(),
    }
    _write_json(dataset_root / "manifest.json", manifest)
    lines = [
        f"# {dataset.display_name} Benchmark Manifest",
        "",
        f"- dataset_key: `{dataset.key}`",
        f"- n_files: `{len(dataset.files)}`",
        "",
        "## Files",
        "",
    ]
    for path in dataset.files:
        lines.append(f"- `{path}`")
    (dataset_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _merge_section_results(result_root: Path, dataset: DatasetSpec) -> dict:
    dataset_root = _dataset_root(result_root, dataset.key)
    merged_root = dataset_root / "section_merged"
    comp_dir = merged_root / "compression_results"
    table_dir = merged_root / "comparison_tables"
    for path in (merged_root, comp_dir, table_dir):
        path.mkdir(parents=True, exist_ok=True)

    alias_map = _make_alias_map(list(dataset.files))
    ms1_map: dict[str, dict] = {}
    ms2_map: dict[str, dict] = {}
    meta_map: dict[str, dict] = {}
    completed_files: list[Path] = []
    for file_path in dataset.files:
        section_root = _section_shard_root(result_root, dataset.key, file_path)
        ms1 = _load_section_methods(section_root, file_path.name, "ms1")
        ms2 = _load_section_methods(section_root, file_path.name, "ms2")
        meta = _load_section_methods(section_root, file_path.name, "metadata")
        if not (ms1 and ms2 and meta):
            continue
        ms1_map[file_path.name] = ms1
        ms2_map[file_path.name] = ms2
        meta_map[file_path.name] = meta
        completed_files.append(file_path)

    _write_csv(
        [{"file": file_name, "method": method, **values} for file_name, methods in ms1_map.items() for method, values in methods.items()],
        comp_dir / "ms1_per_file_methods.csv",
    )
    _write_csv(
        [{"file": file_name, "method": method, **values} for file_name, methods in ms2_map.items() for method, values in methods.items()],
        comp_dir / "ms2_per_file_methods.csv",
    )
    _write_csv(
        [{"file": file_name, "method": method, **values} for file_name, methods in meta_map.items() for method, values in methods.items()],
        comp_dir / "metadata_per_file_methods.csv",
    )

    overall_per_file: dict[str, dict] = {}
    for file_path in completed_files:
        overall_per_file[file_path.name] = _build_overall_container_methods(
            ms1_map[file_path.name],
            ms2_map[file_path.name],
            meta_map[file_path.name],
        )
    _write_csv(
        [{"file": file_name, "method": method, **values} for file_name, methods in overall_per_file.items() for method, values in methods.items()],
        comp_dir / "overall_per_file_methods.csv",
    )

    ms1_bar = _summarize_section(f"{dataset.key}_ms1", ms1_map, MS1_ORDER, merged_root / "ms1", alias_map, "stack_zdpd_baseline") if ms1_map else []
    ms2_bar = _summarize_section(f"{dataset.key}_ms2", ms2_map, MS2_ORDER, merged_root / "ms2", alias_map, "stack_zdpd_baseline") if ms2_map else []
    meta_bar = _summarize_section(f"{dataset.key}_metadata", meta_map, META_ORDER, merged_root / "metadata", alias_map, None) if meta_map else []
    overall_bar = (
        _summarize_section(
            f"{dataset.key}_overall_container",
            overall_per_file,
            ["raw", "gzip", "zlib", "zstd-9", "zdpd_container", "stack_zdpd_container", "ours_eqfidelity"],
            merged_root / "overall_container",
            alias_map,
            "stack_zdpd_container",
        )
        if overall_per_file
        else []
    )
    alias_rows = [
        {
            "file_alias": alias_map[path.name],
            "file_name": path.name,
            "file_type": _detect_kind(path),
            "file_path": str(path),
        }
        for path in completed_files
    ]
    _write_csv(alias_rows, table_dir / "file_alias_mapping.csv")
    summary = {
        "dataset_key": dataset.key,
        "display_name": dataset.display_name,
        "n_completed_files": len(completed_files),
        "n_total_files": len(dataset.files),
        "completed_files": [str(path) for path in completed_files],
        "alias_map": alias_map,
        "ms1_bar": ms1_bar,
        "ms2_bar": ms2_bar,
        "metadata_bar": meta_bar,
        "overall_bar": overall_bar,
    }
    _write_json(merged_root / "section_merged_summary.json", summary)
    return {
        "alias_map": alias_map,
        "completed_files": completed_files,
        "ms1_map": ms1_map,
        "ms2_map": ms2_map,
        "meta_map": meta_map,
    }


def _merge_whole_results(result_root: Path, dataset: DatasetSpec, alias_map: dict[str, str], section_maps: dict) -> dict:
    dataset_root = _dataset_root(result_root, dataset.key)
    merged_root = dataset_root / "whole_merged"
    comp_dir = merged_root / "compression_results"
    plot_data_dir = merged_root / "plot_data"
    plot_dir = merged_root / "plots"
    table_dir = merged_root / "comparison_tables"
    roundtrip_dir = merged_root / "roundtrip_validation"
    for path in (merged_root, comp_dir, plot_data_dir, plot_dir, table_dir, roundtrip_dir):
        path.mkdir(parents=True, exist_ok=True)

    completed_files = []
    overall_rows = []
    component_rows = []
    roundtrip_rows = []
    for file_path in dataset.files:
        if file_path.name not in section_maps["ms1_map"]:
            continue
        whole_root = _whole_shard_root(result_root, dataset.key, file_path)
        whole_summary = _load_json(whole_root / "whole_summary.json")
        if whole_summary is None:
            continue
        baseline_stats = whole_summary["baseline_stats"]
        aux_stats = whole_summary["auxiliary_zlib_stats"]
        ms1_methods = _normalise_section_methods_for_reporting(section_maps["ms1_map"][file_path.name])
        ms2_methods = _normalise_section_methods_for_reporting(section_maps["ms2_map"][file_path.name])
        meta_methods = _normalise_section_methods_for_reporting(section_maps["meta_map"][file_path.name])
        input_bytes = int(file_path.stat().st_size)
        completed_files.append(file_path)

        for method in ("raw_file", "gzip_file", "zlib_file", "zstd-9_file"):
            stats = baseline_stats[method]
            overall_rows.append(
                {
                    "file": file_path.name,
                    "file_alias": alias_map[file_path.name],
                    "method": method,
                    "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP[method],
                    "raw_bytes": input_bytes,
                    "compressed_bytes": int(stats["compressed_bytes"]),
                    "compression_ratio": float(stats["compression_ratio"]),
                    "encode_time_s": float(stats["encode_time_s"]),
                    "decode_time_s": float(stats["decode_time_s"]),
                    "encode_time_scope": str(stats.get("encode_time_scope", "")),
                    "decode_time_scope": str(stats.get("decode_time_scope", "")),
                    "timing_scope_version": str(stats.get("timing_scope_version", "")),
                    "decoded_bytes_checked": int(stats.get("decoded_bytes_checked", 0)),
                }
            )

        container_meta = meta_methods[CONTAINER_METADATA_METHOD]
        zdpd_comp = int(ms1_methods["zdpd_baseline"]["compressed_bytes"]) + int(ms2_methods["zdpd_baseline"]["compressed_bytes"]) + int(container_meta["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
        zdpd_encode = float(_sum_numeric_times(ms1_methods["zdpd_baseline"], ms2_methods["zdpd_baseline"], container_meta, key="numeric_encode_time_s") or 0.0) + float(aux_stats["encode_time_s"])
        zdpd_decode = float(_sum_numeric_times(ms1_methods["zdpd_baseline"], ms2_methods["zdpd_baseline"], container_meta, key="numeric_decode_time_s") or 0.0) + float(aux_stats["decode_time_s"])
        overall_rows.append(
            {
                "file": file_path.name,
                "file_alias": alias_map[file_path.name],
                "method": "zdpd_container",
                "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP["zdpd_container"],
                "raw_bytes": input_bytes,
                "compressed_bytes": zdpd_comp,
                "compression_ratio": input_bytes / zdpd_comp if zdpd_comp else 0.0,
                "encode_time_s": zdpd_encode,
                "decode_time_s": None,
                "numeric_encode_time_s": zdpd_encode,
                "numeric_decode_time_s": zdpd_decode,
                "metadata_method": CONTAINER_METADATA_METHOD,
                "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            }
        )

        stack_comp = int(ms1_methods["stack_zdpd_baseline"]["compressed_bytes"]) + int(ms2_methods["stack_zdpd_baseline"]["compressed_bytes"]) + int(container_meta["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
        stack_encode = float(_sum_numeric_times(ms1_methods["stack_zdpd_baseline"], ms2_methods["stack_zdpd_baseline"], container_meta, key="numeric_encode_time_s") or 0.0) + float(aux_stats["encode_time_s"])
        stack_decode = float(_sum_numeric_times(ms1_methods["stack_zdpd_baseline"], ms2_methods["stack_zdpd_baseline"], container_meta, key="numeric_decode_time_s") or 0.0) + float(aux_stats["decode_time_s"])
        overall_rows.append(
            {
                "file": file_path.name,
                "file_alias": alias_map[file_path.name],
                "method": "stack_zdpd_container",
                "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP["stack_zdpd_container"],
                "raw_bytes": input_bytes,
                "compressed_bytes": stack_comp,
                "compression_ratio": input_bytes / stack_comp if stack_comp else 0.0,
                "encode_time_s": stack_encode,
                "decode_time_s": None,
                "numeric_encode_time_s": stack_encode,
                "numeric_decode_time_s": stack_decode,
                "metadata_method": CONTAINER_METADATA_METHOD,
                "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            }
        )

        for row in whole_summary.get("whole_rows", []):
            method = str(row["method"])
            if method not in {"ours_whole_archive", "ours_strict_q6_whole_archive"}:
                continue
            overall_rows.append(
                {
                    "file": file_path.name,
                    "file_alias": alias_map[file_path.name],
                    "method": method,
                    "validated_roundtrip": WHOLE_VALIDATED_ROUNDTRIP_MAP[method],
                    **{key: value for key, value in row.items() if key not in {"file", "method"}},
                }
            )
        for row in whole_summary.get("component_rows", []):
            component_rows.append({"file_alias": alias_map[file_path.name], **row})
        for row in whole_summary.get("roundtrip_rows", []):
            roundtrip_rows.append({"file_alias": alias_map[file_path.name], **row})

    aggregate_rows = _aggregate_whole_rows(overall_rows, WHOLE_ORDER) if overall_rows else []
    line_rows = [
        {
            "file": row["file"],
            "file_label": row["file_alias"],
            "label": row["method"],
            "display_name": DISPLAY_NAME[row["method"]],
            "validated_roundtrip": row.get("validated_roundtrip", False),
            "compression_ratio": row["compression_ratio"],
            "color_hex": COLOR_MAP[row["method"]],
            "family": "strict_q6" if row["method"] == "ours_strict_q6_whole_archive" else "near",
        }
        for row in overall_rows
    ]
    speed_rows = [
        {
            "label": row["label"],
            "display_name": row["display_name"],
            "validated_roundtrip": row.get("validated_roundtrip", False),
            "mean_encode_time_s": row["mean_encode_time_s"],
            "mean_decode_time_s": row["mean_decode_time_s"],
            "time_scope_available": row.get("time_scope_available", True),
            "color_hex": row["color_hex"],
            "family": row["family"],
        }
        for row in aggregate_rows
    ]

    prefix = f"{dataset.key}_whole_archive"
    _write_csv(overall_rows, comp_dir / f"{prefix}_per_file_methods.csv")
    _write_csv(aggregate_rows, comp_dir / f"{prefix}_aggregate.csv")
    _write_csv(component_rows, comp_dir / f"{prefix}_component_breakdown.csv")
    _write_csv(roundtrip_rows, roundtrip_dir / f"{prefix}_roundtrip_validation.csv")
    _write_csv(aggregate_rows, table_dir / f"{prefix}_compare_table.csv")
    _write_csv(aggregate_rows, plot_data_dir / f"{prefix}_compression_bar_data.csv")
    _write_csv(line_rows, plot_data_dir / f"{prefix}_compression_line_data.csv")
    _write_csv(speed_rows, plot_data_dir / f"{prefix}_speed_bar_data.csv")

    if aggregate_rows:
        plot_compression_bar_custom(
            aggregate_rows,
            plot_dir / f"{prefix}_compression_comparison.png",
            title=f"{dataset.display_name} Whole-file Mean Compression Ratio",
            figsize=(12.2, 8.6),
            legend_loc="upper right",
            annotation_fontsize=12,
        )
        _plot_whole_boxplot_generic(overall_rows, plot_dir / f"{prefix}_compression_boxplot.png", dataset.display_name)
        _plot_whole_line_generic(overall_rows, plot_dir / f"{prefix}_per_file_line.png", completed_files, alias_map, dataset.display_name)
        plot_speed_bar(
            speed_rows,
            plot_dir / f"{prefix}_speed_bar.png",
            title_encode=f"{dataset.display_name} Whole-file Mean Encode Time",
            title_decode=f"{dataset.display_name} Whole-file Mean Decode Time",
        )

    roundtrip_summary = {
        "dataset_key": dataset.key,
        "display_name": dataset.display_name,
        "n_completed_files": len(completed_files),
        "n_roundtrip_rows": len(roundtrip_rows),
        "max_abs_mz_error": max((float(row["max_abs_mz_error"]) for row in roundtrip_rows), default=0.0),
        "max_abs_intensity_error": max((float(row["max_abs_intensity_error"]) for row in roundtrip_rows), default=0.0),
        "all_roundtrip_passed": all(bool(row.get("roundtrip_passed")) for row in roundtrip_rows) if roundtrip_rows else False,
        "all_mz_within_ceiling": all(bool(row.get("mz_within_ceiling")) for row in roundtrip_rows) if roundtrip_rows else False,
        "all_intensity_within_ceiling": all(bool(row.get("intensity_within_ceiling")) for row in roundtrip_rows) if roundtrip_rows else False,
        "all_aux_counts_match": all(bool(row.get("aux_counts_match")) for row in roundtrip_rows) if roundtrip_rows else False,
        "xml_metadata_warning_count": sum(1 for row in roundtrip_rows if bool(row.get("xml_metadata_warning"))),
        "all_binary_stripped_xml_equal": all(bool(row.get("binary_stripped_xml_equal")) for row in roundtrip_rows) if roundtrip_rows else False,
        "all_xml_without_binary_arrays_equal": all(bool(row.get("xml_without_binary_arrays_equal")) for row in roundtrip_rows) if roundtrip_rows else False,
    }
    _write_json(roundtrip_dir / f"{prefix}_roundtrip_summary.json", roundtrip_summary)
    _write_json(
        merged_root / "whole_merged_summary.json",
        {
            "dataset_key": dataset.key,
            "display_name": dataset.display_name,
            "n_completed_files": len(completed_files),
            "n_total_files": len(dataset.files),
            "aggregate_rows": aggregate_rows,
            "roundtrip_summary": roundtrip_summary,
        },
    )
    return {
        "completed_files": completed_files,
        "aggregate_rows": aggregate_rows,
        "roundtrip_summary": roundtrip_summary,
    }


def _log_event(progress_log: Path, md_log: Path, event: str, **payload) -> None:
    row = {"event": event, "timestamp": _now_iso(), **payload}
    _append_jsonl(progress_log, row)
    md_log.parent.mkdir(parents=True, exist_ok=True)
    with md_log.open("a", encoding="utf-8") as handle:
        handle.write(f"- [{row['timestamp']}] `{event}` " + " ".join(f"{key}=`{value}`" for key, value in payload.items()) + "\n")


def _check_child_native_speedups(python_bin: str) -> dict:
    return assert_python_native_speedups_available(python_bin, cwd=ROOT)


def _kill_process_group(proc: subprocess.Popen, grace_seconds: int) -> None:
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(1)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _whole_ms2_section_workers(per_file_cpu: int) -> int:
    """Cap whole-file MS2 section workers to avoid memory amplification after backoff."""
    try:
        cap = int(os.environ.get("TRACKCODEC_WHOLE_MS2_SECTION_WORKERS_MAX", "2"))
    except ValueError:
        cap = 2
    return max(1, min(max(1, int(per_file_cpu)), max(1, cap)))


def _whole_ms2_segment_workers(per_file_cpu: int) -> int:
    """Per-section MS2 segment parallelism for exact-track section aggregation."""
    try:
        cap = int(os.environ.get("TRACKCODEC_WHOLE_MS2_SEGMENT_WORKERS_MAX", "4"))
    except ValueError:
        cap = 4
    return max(1, min(max(1, int(per_file_cpu)), cap))


def _launch_task(
    *,
    python_bin: str,
    result_root: Path,
    dataset: DatasetSpec,
    stage: str,
    file_path: Path,
    attempt: int,
    total_cpu_cores: int,
    current_parallel_files: int,
    min_available_mem_gb: float,
) -> RunningTask:
    dataset_root = _dataset_root(result_root, dataset.key)
    logs_dir = dataset_root / "logs" / stage
    logs_dir.mkdir(parents=True, exist_ok=True)
    shard_root = _shard_root(result_root, dataset.key, file_path)
    shard_root.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_dir / f"{_slugify(file_path.name)}.attempt{attempt}.stdout.log"
    stderr_path = logs_dir / f"{_slugify(file_path.name)}.attempt{attempt}.stderr.log"
    per_file_cpu = max(1, int(total_cpu_cores) // max(1, int(current_parallel_files)))

    if stage == "section":
        cmd = [
            python_bin,
            str(WORKER_SCRIPT),
            "--file",
            str(file_path),
            "--result-root",
            str(_section_shard_root(result_root, dataset.key, file_path)),
            "--phase",
            "section",
            "--baseline-threads",
            "1",
            "--ms1-cpu-budget",
            str(per_file_cpu),
            "--ms2-section-workers",
            str(per_file_cpu),
            "--ms2-segment-workers",
            str(per_file_cpu),
            "--min-available-mem-gb",
            str(float(min_available_mem_gb)),
            "--dataset-label",
            dataset.key,
        ]
    else:
        whole_ms2_workers = _whole_ms2_section_workers(per_file_cpu)
        whole_ms2_segment_workers = _whole_ms2_segment_workers(per_file_cpu)
        cmd = [
            python_bin,
            str(WORKER_SCRIPT),
            "--file",
            str(file_path),
            "--result-root",
            str(_whole_shard_root(result_root, dataset.key, file_path)),
            "--section-root",
            str(_section_shard_root(result_root, dataset.key, file_path)),
            "--phase",
            "whole",
            "--whole-run-mode",
            "all",
            "--whole-ms2-section-workers",
            str(whole_ms2_workers),
            "--dataset-label",
            dataset.key,
        ]
        if whole_ms2_segment_workers > 0:
            cmd.extend(["--whole-ms2-segment-workers", str(whole_ms2_segment_workers)])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT.parent) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["TRACKCODEC_PYTHON"] = str(python_bin)
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("NUMEXPR_NUM_THREADS", "1")
    with stdout_path.open("a", encoding="utf-8") as stdout_handle, stderr_path.open("a", encoding="utf-8") as stderr_handle:
        stdout_handle.write(f"[{_now_iso()}] START {' '.join(cmd)}\n")
        stderr_handle.write(f"[{_now_iso()}] START {' '.join(cmd)}\n")
    stdout_handle = stdout_path.open("a", encoding="utf-8")
    stderr_handle = stderr_path.open("a", encoding="utf-8")
    proc = subprocess.Popen(
        cmd,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
        cwd=str(ROOT),
        env=env,
        preexec_fn=os.setsid,
    )
    proc._trackcodec_stdout_handle = stdout_handle  # type: ignore[attr-defined]
    proc._trackcodec_stderr_handle = stderr_handle  # type: ignore[attr-defined]
    return RunningTask(
        dataset_key=dataset.key,
        stage=stage,
        file_path=file_path,
        shard_root=shard_root,
        attempt=attempt,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        proc=proc,
    )


def _close_task_handles(task: RunningTask) -> None:
    stdout_handle = getattr(task.proc, "_trackcodec_stdout_handle", None)
    stderr_handle = getattr(task.proc, "_trackcodec_stderr_handle", None)
    if stdout_handle is not None:
        stdout_handle.close()
    if stderr_handle is not None:
        stderr_handle.close()


def _wait_for_available_memory(
    *,
    progress_log: Path,
    md_log: Path,
    dataset_key: str,
    stage: str,
    min_available_mem_gb: float,
    poll_seconds: int,
) -> None:
    waiting_logged = False
    while True:
        available_gb = psutil.virtual_memory().available / (1024 ** 3)
        if available_gb >= float(min_available_mem_gb):
            if waiting_logged:
                _log_event(
                    progress_log,
                    md_log,
                    "memory_guard_recovered",
                    dataset=dataset_key,
                    stage=stage,
                    available_gb=f"{available_gb:.2f}",
                    threshold_gb=f"{min_available_mem_gb:.2f}",
                )
            return
        if not waiting_logged:
            _log_event(
                progress_log,
                md_log,
                "memory_guard_waiting",
                dataset=dataset_key,
                stage=stage,
                available_gb=f"{available_gb:.2f}",
                threshold_gb=f"{min_available_mem_gb:.2f}",
            )
            waiting_logged = True
        time.sleep(max(3, poll_seconds))


def _maybe_log_soft_memory_pause(
    *,
    progress_log: Path,
    md_log: Path,
    dataset_key: str,
    stage: str,
    available_gb: float,
    min_available_mem_gb: float,
    active_tasks: int,
) -> None:
    _log_event(
        progress_log,
        md_log,
        "memory_guard_pause_new_launches",
        dataset=dataset_key,
        stage=stage,
        available_gb=f"{available_gb:.2f}",
        threshold_gb=f"{min_available_mem_gb:.2f}",
        active_tasks=active_tasks,
    )


def _initial_attempts_from_logs(result_root: Path, dataset: DatasetSpec, stage: str) -> dict[str, int]:
    logs_dir = _dataset_root(result_root, dataset.key) / "logs" / stage
    attempts: dict[str, int] = {}
    if not logs_dir.exists():
        return attempts
    for file_path in dataset.files:
        slug = _slugify(file_path.name)
        max_attempt = 0
        for log_path in logs_dir.glob(f"{slug}.attempt*.stdout.log"):
            match = re.search(r"\.attempt(\d+)\.stdout\.log$", log_path.name)
            if match:
                max_attempt = max(max_attempt, int(match.group(1)))
        for log_path in logs_dir.glob(f"{slug}.attempt*.stderr.log"):
            match = re.search(r"\.attempt(\d+)\.stderr\.log$", log_path.name)
            if match:
                max_attempt = max(max_attempt, int(match.group(1)))
        if max_attempt > 0:
            attempts[file_path.name] = max_attempt
    return attempts


def _run_stage(
    *,
    python_bin: str,
    result_root: Path,
    dataset: DatasetSpec,
    stage: str,
    total_cpu_cores: int,
    parallel_state: dict,
    min_available_mem_gb: float,
    kill_below_available_mem_gb: float,
    poll_seconds: int,
    retry_limit: int,
    kill_grace_seconds: int,
) -> None:
    dataset_root = _dataset_root(result_root, dataset.key)
    progress_log = dataset_root / "logs" / "supervisor.progress.jsonl"
    md_log = dataset_root / "logs" / "supervisor.log.md"
    attempts: dict[str, int] = _initial_attempts_from_logs(result_root, dataset, stage)
    active: list[RunningTask] = []
    if stage == "section":
        pending = [path for path in dataset.files if not _section_done(result_root, dataset.key, path)]
    else:
        pending = [path for path in dataset.files if not _whole_done(result_root, dataset.key, path)]

    _log_event(
        progress_log,
        md_log,
        "stage_start",
        dataset=dataset.key,
        stage=stage,
        pending_files=len(pending),
        parallel_files=parallel_state["current_parallel_files"],
        total_cpu_cores=total_cpu_cores,
        min_available_mem_gb=min_available_mem_gb,
    )

    while pending or active:
        if pending and not active:
            _wait_for_available_memory(
                progress_log=progress_log,
                md_log=md_log,
                dataset_key=dataset.key,
                stage=stage,
                min_available_mem_gb=min_available_mem_gb,
                poll_seconds=poll_seconds,
            )
        soft_pause_logged = False
        while pending and len(active) < parallel_state["current_parallel_files"]:
            available_gb = psutil.virtual_memory().available / (1024 ** 3)
            if available_gb < float(min_available_mem_gb):
                if not soft_pause_logged:
                    _maybe_log_soft_memory_pause(
                        progress_log=progress_log,
                        md_log=md_log,
                        dataset_key=dataset.key,
                        stage=stage,
                        available_gb=available_gb,
                        min_available_mem_gb=min_available_mem_gb,
                        active_tasks=len(active),
                    )
                    soft_pause_logged = True
                break
            file_path = pending.pop(0)
            attempt = attempts.get(file_path.name, 0) + 1
            attempts[file_path.name] = attempt
            task = _launch_task(
                python_bin=python_bin,
                result_root=result_root,
                dataset=dataset,
                stage=stage,
                file_path=file_path,
                attempt=attempt,
                total_cpu_cores=total_cpu_cores,
                current_parallel_files=parallel_state["current_parallel_files"],
                min_available_mem_gb=min_available_mem_gb,
            )
            active.append(task)
            launch_payload = {
                "dataset": dataset.key,
                "stage": stage,
                "file": file_path.name,
                "attempt": attempt,
                "pid": task.proc.pid,
                "parallel_files": parallel_state["current_parallel_files"],
            }
            if stage == "whole":
                per_file_cpu = max(1, int(total_cpu_cores) // max(1, int(parallel_state["current_parallel_files"])))
                launch_payload["whole_ms2_section_workers"] = _whole_ms2_section_workers(per_file_cpu)
                launch_payload["whole_ms2_segment_workers"] = _whole_ms2_segment_workers(per_file_cpu)
            _log_event(progress_log, md_log, "task_launch", **launch_payload)

        available_gb = psutil.virtual_memory().available / (1024 ** 3)
        if active and available_gb < float(kill_below_available_mem_gb):
            if stage == "whole" and len(active) == 1 and parallel_state["current_parallel_files"] <= 1:
                _log_event(
                    progress_log,
                    md_log,
                    "memory_guard_single_whole_worker_continue",
                    dataset=dataset.key,
                    stage=stage,
                    available_gb=f"{available_gb:.2f}",
                    threshold_gb=f"{kill_below_available_mem_gb:.2f}",
                    active_tasks=len(active),
                    reason="avoid_restarting_last_whole_file_worker",
                )
                time.sleep(max(1, poll_seconds))
                continue
            _log_event(
                progress_log,
                md_log,
                "memory_guard_triggered",
                dataset=dataset.key,
                stage=stage,
                available_gb=f"{available_gb:.2f}",
                threshold_gb=f"{kill_below_available_mem_gb:.2f}",
                active_tasks=len(active),
            )
            for task in list(active):
                _kill_process_group(task.proc, kill_grace_seconds)
                _close_task_handles(task)
                pending.insert(0, task.file_path)
            active.clear()
            if parallel_state["current_parallel_files"] > 1:
                parallel_state["current_parallel_files"] -= 1
                _log_event(
                    progress_log,
                    md_log,
                    "parallel_reduced",
                    dataset=dataset.key,
                    stage=stage,
                    new_parallel_files=parallel_state["current_parallel_files"],
                )
            _wait_for_available_memory(
                progress_log=progress_log,
                md_log=md_log,
                dataset_key=dataset.key,
                stage=stage,
                min_available_mem_gb=min_available_mem_gb,
                poll_seconds=poll_seconds,
            )
            continue
        if active and available_gb < float(min_available_mem_gb):
            _maybe_log_soft_memory_pause(
                progress_log=progress_log,
                md_log=md_log,
                dataset_key=dataset.key,
                stage=stage,
                available_gb=available_gb,
                min_available_mem_gb=min_available_mem_gb,
                active_tasks=len(active),
            )

        time.sleep(max(1, poll_seconds))
        finished: list[RunningTask] = []
        for task in active:
            rc = task.proc.poll()
            if rc is None:
                continue
            finished.append(task)
            _close_task_handles(task)
            if rc == 0:
                _log_event(
                    progress_log,
                    md_log,
                    "task_done",
                    dataset=dataset.key,
                    stage=stage,
                    file=task.file_path.name,
                    attempt=task.attempt,
                    returncode=rc,
                )
            else:
                _log_event(
                    progress_log,
                    md_log,
                    "task_failed",
                    dataset=dataset.key,
                    stage=stage,
                    file=task.file_path.name,
                    attempt=task.attempt,
                    returncode=rc,
                    stderr_log=task.stderr_path,
                )
                if task.attempt < retry_limit:
                    pending.insert(0, task.file_path)
                else:
                    raise RuntimeError(
                        f"{dataset.key}:{stage} failed for {task.file_path.name} after {task.attempt} attempts; see {task.stderr_path}"
                    )
        active = [task for task in active if task not in finished]

    _log_event(
        progress_log,
        md_log,
        "stage_done",
        dataset=dataset.key,
        stage=stage,
        parallel_files=parallel_state["current_parallel_files"],
    )


def _write_top_level_summary(result_root: Path, datasets: list[DatasetSpec], parallel_state: dict) -> None:
    summary = {
        "updated_at": _now_iso(),
        "result_root": str(result_root),
        "current_parallel_files": parallel_state["current_parallel_files"],
        "section_parallel_files": parallel_state.get("section_parallel_files", parallel_state["current_parallel_files"]),
        "whole_parallel_files": parallel_state.get("whole_parallel_files", parallel_state["current_parallel_files"]),
        "datasets": [],
    }
    lines = [
        "# TrackCodec Multi-dataset Supervisor Summary",
        "",
        f"- updated_at: `{summary['updated_at']}`",
        f"- result_root: `{result_root}`",
        f"- current_parallel_files: `{parallel_state['current_parallel_files']}`",
        f"- section_parallel_files: `{summary['section_parallel_files']}`",
        f"- whole_parallel_files: `{summary['whole_parallel_files']}`",
        "",
        "## Dataset Status",
        "",
    ]
    for dataset in datasets:
        dataset_root = _dataset_root(result_root, dataset.key)
        section_summary = _load_json(dataset_root / "section_merged" / "section_merged_summary.json")
        whole_summary = _load_json(dataset_root / "whole_merged" / "whole_merged_summary.json")
        item = {
            "dataset_key": dataset.key,
            "display_name": dataset.display_name,
            "section_summary": section_summary,
            "whole_summary": whole_summary,
        }
        summary["datasets"].append(item)
        lines.append(f"- `{dataset.key}`:")
        lines.append(f"  section = `{(section_summary or {}).get('n_completed_files', 0)}` / `{len(dataset.files)}`")
        lines.append(f"  whole = `{(whole_summary or {}).get('n_completed_files', 0)}` / `{len(dataset.files)}`")
    _write_json(result_root / "supervisor_summary.json", summary)
    (result_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TrackCodec benchmark on full8, StackZDPD validation, and Proposal18 with memory-aware supervision.")
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--parallel-files", type=int, default=5)
    parser.add_argument("--whole-parallel-files", type=int, default=None)
    parser.add_argument("--total-cpu-cores", type=int, default=10)
    parser.add_argument("--min-available-mem-gb", type=float, default=20.0)
    parser.add_argument("--kill-below-available-mem-gb", type=float, default=8.0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--retry-limit", type=int, default=3)
    parser.add_argument("--kill-grace-seconds", type=int, default=10)
    parser.add_argument("--python-bin", default=str(resolve_trackcodec_python()))
    parser.add_argument("--datasets", nargs="+", choices=("full8", "stackzdpd_validation", "proposal18"), default=["full8", "stackzdpd_validation", "proposal18"])
    parser.add_argument(
        "--skip-file-names",
        nargs="*",
        default=None,
        help="File basenames to skip. Accepts space-separated values or comma-separated groups. Can also be set via TRACKCODEC_SKIP_FILE_NAMES.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    python_bin = str(Path(args.python_bin).expanduser().resolve())
    if Path(python_bin) == Path(sys.executable).resolve():
        native_status = assert_native_speedups_available(required=True)
    else:
        native_status = _check_child_native_speedups(python_bin)
    result_root = Path(args.result_root).resolve()
    result_root.mkdir(parents=True, exist_ok=True)

    skip_file_names = _parse_skip_file_names(args.skip_file_names)
    selected_datasets = set(args.datasets)
    datasets = [
        _filter_skipped_files(dataset, skip_file_names)
        for dataset in _dataset_specs(selected_datasets)
    ]
    section_parallel_state = {"current_parallel_files": max(1, int(args.parallel_files))}
    whole_parallel_state = {
        "current_parallel_files": max(
            1,
            int(args.whole_parallel_files) if args.whole_parallel_files is not None else int(args.parallel_files),
        )
    }
    summary_parallel_state = {
        "current_parallel_files": section_parallel_state["current_parallel_files"],
        "section_parallel_files": section_parallel_state["current_parallel_files"],
        "whole_parallel_files": whole_parallel_state["current_parallel_files"],
    }

    top_progress = result_root / "supervisor.progress.jsonl"
    _append_jsonl(
        top_progress,
        {
            "event": "supervisor_start",
            "timestamp": _now_iso(),
            "result_root": str(result_root),
            "parallel_files": section_parallel_state["current_parallel_files"],
            "section_parallel_files": section_parallel_state["current_parallel_files"],
            "whole_parallel_files": whole_parallel_state["current_parallel_files"],
            "total_cpu_cores": int(args.total_cpu_cores),
            "min_available_mem_gb": float(args.min_available_mem_gb),
            "datasets": [dataset.key for dataset in datasets],
            "skip_file_names": sorted(skip_file_names),
            "python_bin": python_bin,
            "native_speedups": native_status,
        },
    )

    for dataset in datasets:
        dataset_root = _dataset_root(result_root, dataset.key)
        dataset_root.mkdir(parents=True, exist_ok=True)
        _build_dataset_manifest(dataset_root, dataset)
        _run_stage(
            python_bin=python_bin,
            result_root=result_root,
            dataset=dataset,
            stage="section",
            total_cpu_cores=int(args.total_cpu_cores),
            parallel_state=section_parallel_state,
            min_available_mem_gb=float(args.min_available_mem_gb),
            kill_below_available_mem_gb=float(args.kill_below_available_mem_gb),
            poll_seconds=int(args.poll_seconds),
            retry_limit=int(args.retry_limit),
            kill_grace_seconds=int(args.kill_grace_seconds),
        )
        section_maps = _merge_section_results(result_root, dataset)
        _run_stage(
            python_bin=python_bin,
            result_root=result_root,
            dataset=dataset,
            stage="whole",
            total_cpu_cores=int(args.total_cpu_cores),
            parallel_state=whole_parallel_state,
            min_available_mem_gb=float(args.min_available_mem_gb),
            kill_below_available_mem_gb=float(args.kill_below_available_mem_gb),
            poll_seconds=int(args.poll_seconds),
            retry_limit=int(args.retry_limit),
            kill_grace_seconds=int(args.kill_grace_seconds),
        )
        _merge_whole_results(result_root, dataset, section_maps["alias_map"], section_maps)
        summary_parallel_state["current_parallel_files"] = section_parallel_state["current_parallel_files"]
        summary_parallel_state["section_parallel_files"] = section_parallel_state["current_parallel_files"]
        summary_parallel_state["whole_parallel_files"] = whole_parallel_state["current_parallel_files"]
        _write_top_level_summary(result_root, datasets, summary_parallel_state)

    _append_jsonl(
        top_progress,
        {
            "event": "supervisor_done",
            "timestamp": _now_iso(),
            "result_root": str(result_root),
            "current_parallel_files": section_parallel_state["current_parallel_files"],
            "section_parallel_files": section_parallel_state["current_parallel_files"],
            "whole_parallel_files": whole_parallel_state["current_parallel_files"],
        },
    )
    summary_parallel_state["current_parallel_files"] = section_parallel_state["current_parallel_files"]
    summary_parallel_state["section_parallel_files"] = section_parallel_state["current_parallel_files"]
    summary_parallel_state["whole_parallel_files"] = whole_parallel_state["current_parallel_files"]
    _write_top_level_summary(result_root, datasets, summary_parallel_state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
