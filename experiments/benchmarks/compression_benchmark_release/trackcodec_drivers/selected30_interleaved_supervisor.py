from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

DRIVER_DIR = Path(__file__).resolve().parent
RELEASE_ROOT = DRIVER_DIR.parent
ROOT = RELEASE_ROOT.parents[2]
sys.path.insert(0, str(ROOT.parent))
if ROOT.name != "TrackCodec" and "TrackCodec" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "TrackCodec",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot bootstrap TrackCodec package from {ROOT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["TrackCodec"] = module
    spec.loader.exec_module(module)

sys.path.insert(0, str(DRIVER_DIR))
from single_file_benchmark import CONTAINER_METADATA_METHOD
from stackzdpd_validation_benchmark import (
    BENCHMARK_TIME_SCOPE_VERSION,
    NO_WHOLE_FILE_MZML_DECODER_SCOPE,
    SECTION_NUMERIC_DECODE_TIME_SCOPE,
    SECTION_NUMERIC_ENCODE_TIME_SCOPE,
    _append_jsonl,
    _auxiliary_zlib_stats,
    _json_safe,
    _normalise_section_methods_for_reporting,
    _sum_numeric_times,
    _write_csv,
)
from TrackCodec.production.common.runtime_env import (
    assert_native_speedups_available,
    assert_python_native_speedups_available,
    resolve_trackcodec_python,
)


DEFAULT_RESULT_ROOT = ROOT.parent / "benchmark_results" / "trackcodec_release_selected30_interleaved"
WORKER_SCRIPT = DRIVER_DIR / "single_file_benchmark.py"
SUMMARY_SCRIPT = ROOT / "tools" / "summarize_completed_benchmark_results.py"
DEFAULT_DATA_ROOT = Path(os.environ.get("TRACKCODEC_DATA_ROOT", str(ROOT.parent / "data")))

SELECTED30_BASENAMES = (
    "File1_0530_BG_293T_1_SWATH_1.uncompressed.mzML",
    "Set 1_F2.uncompressed.mzML",
    "20181210_QX3_JoMu_SA_LC12-7_uPAC200cm_MusmusculusiRT_F6.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML",
    "File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML",
    "File6_Negative_000333.uncompressed.mzML",
    "XB00312DA_RH2.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML",
    "File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML",
    "File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML",
    "XB00312DA_RH1.mzML",
    "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML",
    "250907_E17_Liver_CT2A.mzML",
    "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML",
    "ID113917_01_SPD30_OA10034_10472_062924.mzML",
    "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML",
    "ID113916_01_SPD30_OA10034_10472_062924.mzML",
    "File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML",
    "File2_20180722_L929_test_DDA_1.uncompressed.mzML",
    "File5_S8184TPST_01.uncompressed.mzML",
    "File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML",
    "File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML",
    "572MesADPControl1.mzML",
    "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML",
    "572MesADP3.mzML",
    "20230621_M_CC_3_CoIP_A549-1.mzML",
    "EX1_SR_17182_D1_F4_18122022.mzML",
    "File13_SA1.uncompressed.mzML",
    "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML",
    "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML",
)

SEARCH_ROOTS = (
    DEFAULT_DATA_ROOT / "data_full8",
    DEFAULT_DATA_ROOT / "data_stackZDPD",
    DEFAULT_DATA_ROOT / "data_Proposal",
    DEFAULT_DATA_ROOT,
)


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    display_name: str
    files: tuple[Path, ...]


@dataclass(frozen=True)
class FileSpec:
    dataset_key: str
    dataset_display: str
    path: Path
    size_bytes: int
    size_rank: int


@dataclass
class RunningTask:
    stage: str
    file_spec: FileSpec
    attempt: int
    stdout_path: Path
    stderr_path: Path
    proc: subprocess.Popen


@dataclass
class FileState:
    file_spec: FileSpec
    section_status: str = "pending"
    whole_status: str = "pending"
    section_attempts: int = 0
    whole_attempts: int = 0
    section_started_at: str | None = None
    section_finished_at: str | None = None
    whole_started_at: str | None = None
    whole_finished_at: str | None = None
    errors: list[dict] = field(default_factory=list)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    return slug.strip("._") or "file"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _dataset_for_path(path: Path) -> tuple[str, str]:
    parts = {part.lower() for part in path.parts}
    if "data_full8" in parts:
        return "full8", "Full8"
    if "data_stackzdpd" in parts:
        return "stackzdpd_validation", "StackZDPD Validation"
    if "data_proposal" in parts:
        return "proposal18", "Proposal"
    return "selected30", "Selected 30"


def _resolve_selected_files() -> list[FileSpec]:
    by_name: dict[str, list[Path]] = {name: [] for name in SELECTED30_BASENAMES}
    wanted = set(SELECTED30_BASENAMES)
    primary_roots = SEARCH_ROOTS[:3]
    fallback_roots = SEARCH_ROOTS[3:]
    for root in primary_roots:
        if not root.exists():
            continue
        for path in root.rglob("*.mzML"):
            if path.name in wanted:
                by_name[path.name].append(path.resolve())
    missing = [name for name, paths in by_name.items() if not paths]
    if missing:
        missing_set = set(missing)
        for root in fallback_roots:
            if not root.exists():
                continue
            for path in root.rglob("*.mzML"):
                if path.name in missing_set:
                    by_name[path.name].append(path.resolve())
            missing = [name for name, paths in by_name.items() if not paths]
            if not missing:
                break
    if missing:
        raise FileNotFoundError("Missing selected mzML files: " + ", ".join(missing))

    resolved: list[Path] = []
    seen: set[str] = set()
    for name in SELECTED30_BASENAMES:
        candidates = sorted(by_name[name], key=lambda p: (len(str(p)), str(p)))
        chosen = candidates[0]
        key = str(chosen)
        if key in seen:
            raise RuntimeError(f"Duplicate resolved selected file path: {chosen}")
        seen.add(key)
        resolved.append(chosen)

    size_sorted = sorted(resolved, key=lambda p: p.stat().st_size, reverse=True)
    size_rank = {path: idx + 1 for idx, path in enumerate(size_sorted)}
    specs: list[FileSpec] = []
    for path in resolved:
        dataset_key, display = _dataset_for_path(path)
        specs.append(
            FileSpec(
                dataset_key=dataset_key,
                dataset_display=display,
                path=path,
                size_bytes=int(path.stat().st_size),
                size_rank=size_rank[path],
            )
        )
    if len(specs) != 30:
        raise RuntimeError(f"Expected 30 selected files, resolved {len(specs)}")
    return specs


def _dataset_specs(file_specs: list[FileSpec]) -> list[DatasetSpec]:
    grouped: dict[str, list[FileSpec]] = {}
    display: dict[str, str] = {}
    for spec in file_specs:
        grouped.setdefault(spec.dataset_key, []).append(spec)
        display[spec.dataset_key] = spec.dataset_display
    order = ("full8", "stackzdpd_validation", "proposal18", "selected30")
    out = []
    for key in order:
        if key not in grouped:
            continue
        paths = tuple(spec.path for spec in grouped[key])
        out.append(DatasetSpec(key, display[key], paths))
    return out


def _dataset_root(result_root: Path, dataset_key: str) -> Path:
    return result_root / dataset_key


def _shard_root(result_root: Path, spec: FileSpec) -> Path:
    return _dataset_root(result_root, spec.dataset_key) / "per_file" / _slugify(spec.path.name)


def _section_root(result_root: Path, spec: FileSpec) -> Path:
    return _shard_root(result_root, spec) / "section"


def _whole_root(result_root: Path, spec: FileSpec) -> Path:
    return _shard_root(result_root, spec) / "whole"


def _section_done(result_root: Path, spec: FileSpec) -> bool:
    root = _section_root(result_root, spec)
    file_name = spec.path.name
    return all(
        (root / "compression_results" / f"{section}_sidecars" / f"{file_name}.{section}_methods.json").exists()
        for section in ("ms1", "ms2", "metadata")
    )


def _whole_done(result_root: Path, spec: FileSpec) -> bool:
    comp = _whole_root(result_root, spec) / "compression_results"
    eq = _load_json(comp / f"{spec.path.name}.whole_archive_stats.json") or {}
    strict = _load_json(comp / f"{spec.path.name}.strict_q6.whole_archive_stats.json") or {}
    return bool(eq.get("compare_completed") and strict.get("compare_completed"))


def _load_section_methods(section_root: Path, file_name: str, section_label: str) -> dict | None:
    payload = _load_json(section_root / "compression_results" / f"{section_label}_sidecars" / f"{file_name}.{section_label}_methods.json")
    if not payload:
        return None
    methods = payload.get("methods")
    return methods if isinstance(methods, dict) else None


def _cgroup_memory_snapshot() -> dict:
    host_limit = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines() if line.startswith("MemTotal:"))) * 1024
    v2_base = Path("/sys/fs/cgroup")
    if (v2_base / "memory.current").exists():
        limit_text = (v2_base / "memory.max").read_text().strip()
        if limit_text == "max":
            limit = host_limit
        else:
            limit = min(int(limit_text), host_limit)
        usage = int((v2_base / "memory.current").read_text().strip())
        stat = dict(line.split()[:2] for line in (v2_base / "memory.stat").read_text().splitlines() if line.strip())
        cache = int(stat.get("file", 0))
        rss = int(stat.get("anon", 0))
        version = 2
    else:
        v1_base = Path("/sys/fs/cgroup/memory")
        limit = min(int((v1_base / "memory.limit_in_bytes").read_text().strip()), host_limit)
        usage = int((v1_base / "memory.usage_in_bytes").read_text().strip())
        stat = dict(line.split()[:2] for line in (v1_base / "memory.stat").read_text().splitlines() if line.strip())
        cache = int(stat.get("cache", 0))
        rss = int(stat.get("rss", 0))
        version = 1
    used_no_cache = max(0, usage - cache)
    available_no_cache = max(0, limit - used_no_cache)
    return {
        "cgroup_version": version,
        "limit_bytes": limit,
        "usage_bytes": usage,
        "page_cache_bytes": cache,
        "rss_bytes": rss,
        "used_no_cache_bytes": used_no_cache,
        "available_no_cache_bytes": available_no_cache,
        "limit_gb": limit / 1024**3,
        "usage_gb": usage / 1024**3,
        "page_cache_gb": cache / 1024**3,
        "rss_gb": rss / 1024**3,
        "used_no_cache_gb": used_no_cache / 1024**3,
        "available_no_cache_gb": available_no_cache / 1024**3,
    }


def _build_manifest(result_root: Path, datasets: list[DatasetSpec], file_specs: list[FileSpec]) -> None:
    by_dataset = {dataset.key: dataset for dataset in datasets}
    for dataset in datasets:
        root = _dataset_root(result_root, dataset.key)
        root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "dataset_key": dataset.key,
            "display_name": dataset.display_name,
            "n_files": len(dataset.files),
            "files": [str(path) for path in dataset.files],
            "generated_at": _now_iso(),
        }
        _write_json(root / "manifest.json", manifest)
        lines = [
            f"# {dataset.display_name} Selected30 Manifest",
            "",
            f"- dataset_key: `{dataset.key}`",
            f"- n_files: `{len(dataset.files)}`",
            "",
            "## Files",
            "",
        ]
        for path in dataset.files:
            lines.append(f"- `{path}`")
        (root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    summary = {
        "generated_at": _now_iso(),
        "result_root": str(result_root),
        "n_files": len(file_specs),
        "datasets": [{"dataset_key": key, "n_files": len(dataset.files)} for key, dataset in by_dataset.items()],
        "files_by_input_order": [
            {
                "input_order": idx + 1,
                "dataset": spec.dataset_key,
                "file": spec.path.name,
                "path": str(spec.path),
                "size_bytes": spec.size_bytes,
                "size_gb": spec.size_bytes / 1024**3,
                "size_rank_desc": spec.size_rank,
            }
            for idx, spec in enumerate(file_specs)
        ],
    }
    _write_json(result_root / "selected30_manifest.json", summary)


def _priority_section_queue(states: dict[str, FileState], large_threshold_gb: float) -> list[str]:
    pending = [name for name, state in states.items() if state.section_status == "pending"]
    large = [name for name in pending if states[name].file_spec.size_bytes >= large_threshold_gb * 1024**3]
    small = [name for name in pending if name not in large]
    large.sort(key=lambda name: states[name].file_spec.size_bytes, reverse=True)
    small.sort(key=lambda name: states[name].file_spec.size_bytes)
    queue: list[str] = []
    large_idx = 0
    small_idx = 0
    if large_idx < len(large):
        queue.append(large[large_idx])
        large_idx += 1
    small_between_large = 4
    while small_idx < len(small) or large_idx < len(large):
        for _ in range(small_between_large):
            if small_idx >= len(small):
                break
            queue.append(small[small_idx])
            small_idx += 1
        if large_idx < len(large):
            queue.append(large[large_idx])
            large_idx += 1
        if small_idx >= len(small):
            while large_idx < len(large):
                queue.append(large[large_idx])
                large_idx += 1
    return queue


def _is_large(state: FileState, large_threshold_gb: float) -> bool:
    return state.file_spec.size_bytes >= large_threshold_gb * 1024**3


def _large_section_allowed(states: dict[str, FileState], large_threshold_gb: float, small_completions_per_extra_large: int) -> bool:
    small_done = sum(
        1
        for state in states.values()
        if state.section_status == "done" and not _is_large(state, large_threshold_gb)
    )
    large_started = sum(
        1
        for state in states.values()
        if state.section_status in {"running", "done"} and _is_large(state, large_threshold_gb)
    )
    allowed_large = 1 + (small_done // max(1, int(small_completions_per_extra_large)))
    return large_started < allowed_large


def _choose_task(
    states: dict[str, FileState],
    active: list[RunningTask],
    section_queue: list[str],
    large_threshold_gb: float,
    small_completions_per_extra_large: int,
) -> tuple[str, str] | None:
    active_keys = {(task.stage, task.file_spec.path.name) for task in active}
    active_files = {task.file_spec.path.name for task in active}
    for name in list(section_queue):
        state = states[name]
        if state.section_status != "pending":
            section_queue.remove(name)
            continue
        if name in active_files:
            continue
        if ("section", name) in active_keys:
            continue
        if _is_large(state, large_threshold_gb) and not _large_section_allowed(
            states,
            large_threshold_gb,
            small_completions_per_extra_large,
        ):
            continue
        return "section", name

    whole_candidates = [
        name
        for name, state in states.items()
        if state.whole_status == "pending" and ("whole", name) not in active_keys
    ]
    if not whole_candidates:
        return None
    whole_candidates.sort(
        key=lambda name: (
            0 if states[name].section_status == "done" else 1,
            -states[name].file_spec.size_bytes,
        )
    )
    return "whole", whole_candidates[0]


def _launch_task(
    *,
    python_bin: str,
    result_root: Path,
    state: FileState,
    stage: str,
    current_parallel_files: int,
    total_cpu_cores: int,
    min_available_mem_gb: float,
    allow_missing_section_sidecars: bool,
) -> RunningTask:
    spec = state.file_spec
    logs_dir = _dataset_root(result_root, spec.dataset_key) / "logs" / stage
    logs_dir.mkdir(parents=True, exist_ok=True)
    attempt = state.section_attempts + 1 if stage == "section" else state.whole_attempts + 1
    if stage == "section":
        state.section_attempts = attempt
        state.section_status = "running"
        state.section_started_at = _now_iso()
    else:
        state.whole_attempts = attempt
        state.whole_status = "running"
        state.whole_started_at = _now_iso()

    stdout_path = logs_dir / f"{_slugify(spec.path.name)}.attempt{attempt}.stdout.log"
    stderr_path = logs_dir / f"{_slugify(spec.path.name)}.attempt{attempt}.stderr.log"
    per_file_cpu = max(1, int(total_cpu_cores) // max(1, int(current_parallel_files)))
    if stage == "section":
        cmd = [
            python_bin,
            str(WORKER_SCRIPT),
            "--file",
            str(spec.path),
            "--result-root",
            str(_section_root(result_root, spec)),
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
            spec.dataset_key,
        ]
    else:
        whole_ms2_section_workers = max(1, min(per_file_cpu, int(os.environ.get("TRACKCODEC_WHOLE_MS2_SECTION_WORKERS_MAX", "2"))))
        whole_ms2_segment_workers = max(1, min(per_file_cpu, int(os.environ.get("TRACKCODEC_WHOLE_MS2_SEGMENT_WORKERS_MAX", "4"))))
        cmd = [
            python_bin,
            str(WORKER_SCRIPT),
            "--file",
            str(spec.path),
            "--result-root",
            str(_whole_root(result_root, spec)),
            "--section-root",
            str(_section_root(result_root, spec)),
            "--phase",
            "whole",
            "--whole-run-mode",
            "all",
            "--whole-ms2-section-workers",
            str(whole_ms2_section_workers),
            "--whole-ms2-segment-workers",
            str(whole_ms2_segment_workers),
            "--dataset-label",
            spec.dataset_key,
        ]
        if allow_missing_section_sidecars:
            cmd.append("--allow-missing-section-sidecars")

    env = os.environ.copy()
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
        cwd=str(ROOT),
        env=env,
        stdout=stdout_handle,
        stderr=stderr_handle,
        text=True,
        preexec_fn=os.setsid,
    )
    proc._trackcodec_stdout_handle = stdout_handle  # type: ignore[attr-defined]
    proc._trackcodec_stderr_handle = stderr_handle  # type: ignore[attr-defined]
    return RunningTask(stage=stage, file_spec=spec, attempt=attempt, stdout_path=stdout_path, stderr_path=stderr_path, proc=proc)


def _close_task_handles(task: RunningTask) -> None:
    stdout_handle = getattr(task.proc, "_trackcodec_stdout_handle", None)
    stderr_handle = getattr(task.proc, "_trackcodec_stderr_handle", None)
    if stdout_handle is not None:
        stdout_handle.close()
    if stderr_handle is not None:
        stderr_handle.close()


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


def _task_done(result_root: Path, task: RunningTask) -> bool:
    if task.stage == "section":
        return _section_done(result_root, task.file_spec)
    return _whole_done(result_root, task.file_spec)


def _write_status(result_root: Path, states: dict[str, FileState], active: list[RunningTask], current_parallel_files: int) -> None:
    snapshot = _cgroup_memory_snapshot()
    payload = {
        "updated_at": _now_iso(),
        "result_root": str(result_root),
        "current_parallel_files": current_parallel_files,
        "active_tasks": [
            {
                "stage": task.stage,
                "file": task.file_spec.path.name,
                "pid": task.proc.pid,
                "attempt": task.attempt,
                "elapsed_s": None,
            }
            for task in active
        ],
        "counts": {
            "section_done": sum(1 for state in states.values() if state.section_status == "done"),
            "section_running": sum(1 for state in states.values() if state.section_status == "running"),
            "section_pending": sum(1 for state in states.values() if state.section_status == "pending"),
            "whole_done": sum(1 for state in states.values() if state.whole_status == "done"),
            "whole_running": sum(1 for state in states.values() if state.whole_status == "running"),
            "whole_pending": sum(1 for state in states.values() if state.whole_status == "pending"),
        },
        "cgroup_memory": snapshot,
        "files": [
            {
                "dataset": state.file_spec.dataset_key,
                "file": name,
                "size_gb": state.file_spec.size_bytes / 1024**3,
                "size_rank_desc": state.file_spec.size_rank,
                "section_status": state.section_status,
                "whole_status": state.whole_status,
                "section_attempts": state.section_attempts,
                "whole_attempts": state.whole_attempts,
                "errors": state.errors,
            }
            for name, state in sorted(states.items(), key=lambda item: item[1].file_spec.size_rank)
        ],
    }
    _write_json(result_root / "selected30_supervisor_status.json", payload)
    lines = [
        "# TrackCodec Selected30 Interleaved Benchmark",
        "",
        f"- updated_at: `{payload['updated_at']}`",
        f"- result_root: `{result_root}`",
        f"- current_parallel_files: `{current_parallel_files}`",
        f"- cgroup_available_no_cache_gb: `{snapshot['available_no_cache_gb']:.2f}`",
        f"- section: `{payload['counts']['section_done']}` done / `{len(states)}`",
        f"- whole: `{payload['counts']['whole_done']}` done / `{len(states)}`",
        "",
        "## Active Tasks",
        "",
    ]
    for task in active:
        lines.append(f"- `{task.stage}` `{task.file_spec.path.name}` pid=`{task.proc.pid}` attempt=`{task.attempt}`")
    (result_root / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        "zdpd_container": {
            "raw_bytes": ms1_methods["zdpd_baseline"]["raw_bytes"] + ms2_methods["zdpd_baseline"]["raw_bytes"] + container_meta["raw_bytes"],
            "compressed_bytes": ms1_methods["zdpd_baseline"]["compressed_bytes"] + ms2_methods["zdpd_baseline"]["compressed_bytes"] + container_meta["compressed_bytes"],
            "encode_time_s": zdpd_encode,
            "decode_time_s": None,
            "numeric_encode_time_s": zdpd_encode,
            "numeric_decode_time_s": zdpd_decode,
            "metadata_method": CONTAINER_METADATA_METHOD,
            "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
            "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        },
        "stack_zdpd_container": {
            "raw_bytes": ms1_methods["stack_zdpd_baseline"]["raw_bytes"] + ms2_methods["stack_zdpd_baseline"]["raw_bytes"] + container_meta["raw_bytes"],
            "compressed_bytes": ms1_methods["stack_zdpd_baseline"]["compressed_bytes"] + ms2_methods["stack_zdpd_baseline"]["compressed_bytes"] + container_meta["compressed_bytes"],
            "encode_time_s": stack_encode,
            "decode_time_s": None,
            "numeric_encode_time_s": stack_encode,
            "numeric_decode_time_s": stack_decode,
            "metadata_method": CONTAINER_METADATA_METHOD,
            "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
            "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
        },
    }


def _complete_deferred_whole_rows(result_root: Path, states: dict[str, FileState]) -> None:
    for state in states.values():
        spec = state.file_spec
        section_root = _section_root(result_root, spec)
        whole_root = _whole_root(result_root, spec)
        comp = whole_root / "compression_results"
        whole_csv = comp / "whole_archive_per_file_methods.csv"
        if not whole_csv.exists():
            continue
        ms1 = _load_section_methods(section_root, spec.path.name, "ms1")
        ms2 = _load_section_methods(section_root, spec.path.name, "ms2")
        meta = _load_section_methods(section_root, spec.path.name, "metadata")
        whole_summary = _load_json(whole_root / "whole_summary.json") or {}
        aux_stats = whole_summary.get("auxiliary_zlib_stats")
        if not aux_stats:
            aux_stats = _load_json(comp / f"{spec.path.name}.auxiliary_zlib_stats.json")
        if not aux_stats:
            aux_stats = _auxiliary_zlib_stats(spec.path)
            _write_json(comp / f"{spec.path.name}.auxiliary_zlib_stats.json", aux_stats)
        if not (ms1 and ms2 and meta and aux_stats):
            continue
        rows = _read_csv(whole_csv)
        existing = {row.get("method") for row in rows}
        if {"zdpd_container", "stack_zdpd_container"}.issubset(existing):
            continue
        input_bytes = int(spec.path.stat().st_size)
        containers = _build_overall_container_methods(ms1, ms2, meta)
        for method, values in containers.items():
            if method in existing:
                continue
            comp_bytes = int(values["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
            raw_bytes = input_bytes
            row = {
                "file": spec.path.name,
                "method": method,
                "raw_bytes": raw_bytes,
                "compressed_bytes": comp_bytes,
                "compression_ratio": raw_bytes / comp_bytes if comp_bytes else 0.0,
                "encode_time_s": float(values.get("numeric_encode_time_s") or 0.0) + float(aux_stats["encode_time_s"]),
                "decode_time_s": None,
                "numeric_encode_time_s": float(values.get("numeric_encode_time_s") or 0.0) + float(aux_stats["encode_time_s"]),
                "numeric_decode_time_s": float(values.get("numeric_decode_time_s") or 0.0) + float(aux_stats["decode_time_s"]),
                "metadata_method": CONTAINER_METADATA_METHOD,
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            }
            rows.append(row)
        _write_csv(rows, whole_csv)
        whole_summary["section_methods_available"] = True
        whole_summary["missing_section_sidecars"] = []
        whole_summary["section_dependent_rows_deferred"] = False
        whole_summary["section_dependent_rows_completed_by_supervisor"] = True
        whole_summary["whole_rows"] = rows
        _write_json(whole_root / "whole_summary.json", whole_summary)


def _read_csv(path: Path) -> list[dict]:
    import csv

    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _run_aggregate(python_bin: str, result_root: Path, progress_log: Path) -> None:
    if not SUMMARY_SCRIPT.exists():
        _append_jsonl(progress_log, {"event": "aggregate_skipped_missing_script", "timestamp": _now_iso(), "script": str(SUMMARY_SCRIPT)})
        return
    output_dir = result_root / f"aggregate_completed_{time.strftime('%Y%m%d_%H%M%S')}"
    cmd = [python_bin, str(SUMMARY_SCRIPT), "--result-root", str(result_root), "--output-dir", str(output_dir)]
    proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False)
    _append_jsonl(
        progress_log,
        {
            "event": "aggregate_done" if proc.returncode == 0 else "aggregate_failed",
            "timestamp": _now_iso(),
            "returncode": proc.returncode,
            "output_dir": str(output_dir),
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        },
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Aggregate failed with return code {proc.returncode}; see supervisor progress log")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TrackCodec selected30 benchmark with interleaved section and whole-file scheduling.")
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--python-bin", default=str(resolve_trackcodec_python()))
    parser.add_argument("--parallel-files", type=int, default=15)
    parser.add_argument("--max-parallel-files", type=int, default=20)
    parser.add_argument("--total-cpu-cores", type=int, default=40)
    parser.add_argument("--min-available-mem-gb", type=float, default=10.0)
    parser.add_argument("--recover-available-mem-gb", type=float, default=40.0)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--retry-limit", type=int, default=2)
    parser.add_argument("--kill-grace-seconds", type=int, default=10)
    parser.add_argument("--large-file-threshold-gb", type=float, default=10.0)
    parser.add_argument("--small-completions-per-extra-large", type=int, default=4)
    parser.add_argument("--increase-when-available-gain-gb", type=float, default=50.0)
    parser.add_argument("--allow-missing-section-sidecars", action="store_true", default=True)
    parser.add_argument("--no-aggregate", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    python_bin = str(Path(args.python_bin).expanduser().resolve())
    if Path(python_bin) == Path(sys.executable).resolve():
        native_status = assert_native_speedups_available(required=True)
    else:
        native_status = assert_python_native_speedups_available(python_bin, cwd=ROOT)
    result_root = Path(args.result_root).resolve()
    result_root.mkdir(parents=True, exist_ok=True)
    progress_log = result_root / "supervisor.progress.jsonl"
    md_log = result_root / "supervisor.log.md"

    file_specs = _resolve_selected_files()
    datasets = _dataset_specs(file_specs)
    _build_manifest(result_root, datasets, file_specs)
    states = {
        spec.path.name: FileState(
            file_spec=spec,
            section_status="done" if _section_done(result_root, spec) else "pending",
            whole_status="done" if _whole_done(result_root, spec) else "pending",
        )
        for spec in file_specs
    }
    section_queue = _priority_section_queue(states, float(args.large_file_threshold_gb))
    current_parallel_files = max(1, int(args.parallel_files))
    initial_parallel_files = current_parallel_files
    max_parallel_files = max(current_parallel_files, int(args.max_parallel_files))
    active: list[RunningTask] = []
    startup_available_gb = float(_cgroup_memory_snapshot()["available_no_cache_gb"])

    _append_jsonl(
        progress_log,
        {
            "event": "supervisor_start",
            "timestamp": _now_iso(),
            "result_root": str(result_root),
            "python_bin": python_bin,
            "native_speedups": native_status,
            "parallel_files": current_parallel_files,
            "max_parallel_files": max_parallel_files,
            "total_cpu_cores": int(args.total_cpu_cores),
            "min_available_mem_gb": float(args.min_available_mem_gb),
            "recover_available_mem_gb": float(args.recover_available_mem_gb),
            "increase_when_available_gain_gb": float(args.increase_when_available_gain_gb),
            "startup_available_no_cache_gb": startup_available_gb,
            "large_file_threshold_gb": float(args.large_file_threshold_gb),
            "small_completions_per_extra_large": int(args.small_completions_per_extra_large),
            "selected_files": len(file_specs),
            "cgroup_memory": _cgroup_memory_snapshot(),
        },
    )
    md_log.write_text(f"# Selected30 TrackCodec Supervisor Log\n\n- [{_now_iso()}] `supervisor_start` result_root=`{result_root}`\n", encoding="utf-8")

    last_status_write = 0.0
    while active or any(state.section_status != "done" or state.whole_status != "done" for state in states.values()):
        snapshot = _cgroup_memory_snapshot()
        available_gb = float(snapshot["available_no_cache_gb"])
        if available_gb < float(args.min_available_mem_gb) and current_parallel_files > 1:
            current_parallel_files -= 1
            _append_jsonl(
                progress_log,
                {
                    "event": "parallel_reduced",
                    "timestamp": _now_iso(),
                    "available_no_cache_gb": available_gb,
                    "threshold_gb": float(args.min_available_mem_gb),
                    "new_parallel_files": current_parallel_files,
                },
            )
        allow_growth_above_initial = available_gb >= startup_available_gb + float(args.increase_when_available_gain_gb)
        growth_cap = max_parallel_files if allow_growth_above_initial else initial_parallel_files
        if available_gb >= float(args.recover_available_mem_gb) and current_parallel_files < growth_cap:
            current_parallel_files += 1
            _append_jsonl(
                progress_log,
                {
                    "event": "parallel_increased",
                    "timestamp": _now_iso(),
                    "available_no_cache_gb": available_gb,
                    "threshold_gb": float(args.recover_available_mem_gb),
                    "growth_cap": growth_cap,
                    "startup_available_no_cache_gb": startup_available_gb,
                    "new_parallel_files": current_parallel_files,
                },
            )

        while len(active) < current_parallel_files and available_gb >= float(args.min_available_mem_gb):
            choice = _choose_task(
                states,
                active,
                section_queue,
                float(args.large_file_threshold_gb),
                int(args.small_completions_per_extra_large),
            )
            if choice is None:
                break
            stage, name = choice
            state = states[name]
            task = _launch_task(
                python_bin=python_bin,
                result_root=result_root,
                state=state,
                stage=stage,
                current_parallel_files=current_parallel_files,
                total_cpu_cores=int(args.total_cpu_cores),
                min_available_mem_gb=float(args.min_available_mem_gb),
                allow_missing_section_sidecars=bool(args.allow_missing_section_sidecars),
            )
            active.append(task)
            _append_jsonl(
                progress_log,
                {
                    "event": "task_launch",
                    "timestamp": _now_iso(),
                    "stage": stage,
                    "dataset": state.file_spec.dataset_key,
                    "file": name,
                    "pid": task.proc.pid,
                    "attempt": task.attempt,
                    "parallel_files": current_parallel_files,
                    "size_gb": state.file_spec.size_bytes / 1024**3,
                    "size_rank_desc": state.file_spec.size_rank,
                    "section_status": state.section_status,
                    "whole_status": state.whole_status,
                },
            )
            available_gb = float(_cgroup_memory_snapshot()["available_no_cache_gb"])

        time.sleep(max(1, int(args.poll_seconds)))
        finished: list[RunningTask] = []
        for task in active:
            rc = task.proc.poll()
            if rc is None:
                continue
            finished.append(task)
            _close_task_handles(task)
            state = states[task.file_spec.path.name]
            done = rc == 0 and _task_done(result_root, task)
            if done:
                if task.stage == "section":
                    state.section_status = "done"
                    state.section_finished_at = _now_iso()
                else:
                    state.whole_status = "done"
                    state.whole_finished_at = _now_iso()
                event = "task_done"
            else:
                event = "task_failed"
                error = {
                    "stage": task.stage,
                    "attempt": task.attempt,
                    "returncode": rc,
                    "stderr_log": str(task.stderr_path),
                    "timestamp": _now_iso(),
                }
                state.errors.append(error)
                if task.stage == "section":
                    if task.attempt < int(args.retry_limit):
                        state.section_status = "pending"
                        if task.file_spec.path.name not in section_queue:
                            section_queue.insert(0, task.file_spec.path.name)
                    else:
                        state.section_status = "failed"
                else:
                    if task.attempt < int(args.retry_limit):
                        state.whole_status = "pending"
                    else:
                        state.whole_status = "failed"
            _append_jsonl(
                progress_log,
                {
                    "event": event,
                    "timestamp": _now_iso(),
                    "stage": task.stage,
                    "dataset": task.file_spec.dataset_key,
                    "file": task.file_spec.path.name,
                    "attempt": task.attempt,
                    "returncode": rc,
                    "done_check": done,
                    "stderr_log": str(task.stderr_path),
                },
            )
            if event == "task_failed" and (
                (task.stage == "section" and state.section_status == "failed")
                or (task.stage == "whole" and state.whole_status == "failed")
            ):
                _write_status(result_root, states, active, current_parallel_files)
                raise RuntimeError(f"{task.stage} failed for {task.file_spec.path.name}; see {task.stderr_path}")
        active = [task for task in active if task not in finished]
        if time.time() - last_status_write >= max(10, int(args.poll_seconds)):
            _write_status(result_root, states, active, current_parallel_files)
            last_status_write = time.time()

    _complete_deferred_whole_rows(result_root, states)
    _write_status(result_root, states, active, current_parallel_files)
    _append_jsonl(progress_log, {"event": "supervisor_done", "timestamp": _now_iso(), "result_root": str(result_root)})
    if not bool(args.no_aggregate):
        _run_aggregate(python_bin, result_root, progress_log)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
