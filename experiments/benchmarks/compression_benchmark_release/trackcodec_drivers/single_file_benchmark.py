from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import traceback
import importlib.util
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

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

try:
    import psutil
except Exception:
    psutil = None

from stackzdpd_validation_benchmark import (
    BENCHMARK_TIME_SCOPE_VERSION,
    NO_WHOLE_FILE_MZML_DECODER_SCOPE,
    SECTION_NUMERIC_DECODE_TIME_SCOPE,
    SECTION_NUMERIC_ENCODE_TIME_SCOPE,
    WHOLE_ARCHIVE_DECODE_TIME_SCOPE,
    WHOLE_ARCHIVE_ENCODE_TIME_SCOPE,
    WHOLE_FILE_BASELINE_METHODS,
    WHOLE_RUN_MODE_CHOICES,
    _append_jsonl,
    _auxiliary_zlib_stats,
    _compress_file_streaming,
    _flatten_method_rows,
    _json_safe,
    _load_json,
    _optional_float,
    _whole_file_baseline_cache_valid,
    _write_csv,
    _write_section_sidecar,
    benchmark_metadata_validation_file,
    benchmark_ms1_validation_file,
    benchmark_ms2_validation_file,
    benchmark_whole_archive_file,
)
from TrackCodec.production.common.runtime_env import assert_native_speedups_available

CONTAINER_METADATA_METHOD = "zstd-9"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def _available_memory_gb() -> float:
    if psutil is not None:
        try:
            return float(psutil.virtual_memory().available) / (1024.0 ** 3)
        except Exception:
            pass
    try:
        mem_available_kb = 0
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])
                break
        if mem_available_kb > 0:
            return float(mem_available_kb) / (1024.0 ** 2)
    except OSError:
        pass
    return 0.0


def _resolve_ms1_section_workers(
    *,
    file_path: Path,
    cpu_budget: int,
    track_prepare_workers: int,
    encode_section_workers: int,
    min_available_mem_gb: float,
) -> dict:
    raw_file_gb = float(file_path.stat().st_size) / (1024.0 ** 3)
    available_mem_gb = _available_memory_gb()
    cpu_budget = max(1, int(cpu_budget) if int(cpu_budget) > 0 else int(os.cpu_count() or 1))
    auto_track_prepare = 1
    auto_encode_workers = 1
    headroom_gb = max(0.0, float(available_mem_gb) - float(min_available_mem_gb))
    tight_threshold_gb = max(6.0, raw_file_gb * 1.25)
    guarded_threshold_gb = max(12.0, raw_file_gb * 2.0)

    if cpu_budget <= 1:
        reason = "cpu_budget_1"
    elif headroom_gb < tight_threshold_gb:
        reason = "memory_tight"
    elif headroom_gb < guarded_threshold_gb:
        auto_track_prepare = 1
        auto_encode_workers = min(2, cpu_budget)
        reason = "memory_guarded"
    else:
        auto_track_prepare = 2 if cpu_budget >= 4 else 1
        if auto_track_prepare == 1:
            auto_encode_workers = min(4, cpu_budget)
        else:
            auto_encode_workers = min(4, max(2, cpu_budget - 1))
        if raw_file_gb >= 3.5:
            auto_encode_workers = min(auto_encode_workers, 3)
        reason = "multicore"

    resolved_track_prepare = max(1, int(track_prepare_workers) if int(track_prepare_workers) > 0 else int(auto_track_prepare))
    resolved_encode_workers = max(1, int(encode_section_workers) if int(encode_section_workers) > 0 else int(auto_encode_workers))
    if resolved_encode_workers == 1 and resolved_track_prepare > 1:
        resolved_track_prepare = 1

    return {
        "cpu_budget": int(cpu_budget),
        "track_prepare_workers": int(resolved_track_prepare),
        "encode_section_workers": int(resolved_encode_workers),
        "available_mem_gb": float(available_mem_gb),
        "min_available_mem_gb": float(min_available_mem_gb),
        "headroom_gb": float(headroom_gb),
        "raw_file_gb": float(raw_file_gb),
        "reason": reason,
    }


class RSSPeakMonitor:
    def __init__(self, interval_s: float = 0.2):
        self.interval_s = float(interval_s)
        self.peak_rss_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._root = psutil.Process(os.getpid()) if psutil is not None else None
        self.system_total_bytes = self._read_total_memory_bytes()

    def _read_total_memory_bytes(self) -> int:
        if psutil is not None:
            try:
                return int(psutil.virtual_memory().total)
            except Exception:
                pass
        try:
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    def _read_self_rss_bytes(self) -> int:
        if psutil is not None and self._root is not None:
            rss = 0
            procs = [self._root]
            try:
                procs.extend(self._root.children(recursive=True))
            except Exception:
                pass
            seen = set()
            for proc in procs:
                if proc is None or proc.pid in seen:
                    continue
                seen.add(proc.pid)
                try:
                    rss += int(proc.memory_info().rss)
                except Exception:
                    continue
            return int(rss)
        try:
            for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
        return 0

    def _sample_once(self) -> None:
        self.peak_rss_bytes = max(int(self.peak_rss_bytes), int(self._read_self_rss_bytes()))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample_once()
            self._stop.wait(self.interval_s)
        self._sample_once()

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_s * 4.0))

    @property
    def peak_memory_pct(self) -> float:
        if self.system_total_bytes <= 0:
            return 0.0
        return 100.0 * float(self.peak_rss_bytes) / float(self.system_total_bytes)


def _rss_stage_log_path(result_root: Path) -> Path:
    return result_root / "logs" / "rss_stage_peaks.jsonl"


def _rss_stage_csv_path(result_root: Path) -> Path:
    return result_root / "logs" / "rss_stage_peaks.csv"


def _rss_stage_summary_path(result_root: Path) -> Path:
    return result_root / "logs" / "rss_stage_summary.json"


def _write_rss_stage_outputs(result_root: Path, file_path: Path, phase: str, stage_rows: list[dict]) -> None:
    _write_csv(stage_rows, _rss_stage_csv_path(result_root))
    peak_bytes = max((int(row.get("peak_rss_bytes", 0)) for row in stage_rows), default=0)
    summary = {
        "file": file_path.name,
        "file_path": str(file_path),
        "phase": phase,
        "raw_file_bytes": int(file_path.stat().st_size),
        "raw_file_gb": float(file_path.stat().st_size) / (1024.0 ** 3),
        "overall_peak_rss_bytes": int(peak_bytes),
        "overall_peak_rss_gb": float(peak_bytes) / (1024.0 ** 3),
        "stage_rows": stage_rows,
        "updated_at": _now_iso(),
    }
    _write_json(_rss_stage_summary_path(result_root), summary)


def _run_monitored_stage(
    *,
    result_root: Path,
    progress_log: Path,
    file_path: Path,
    phase: str,
    stage_rows: list[dict],
    stage: str,
    fn,
    **extra,
):
    _append_jsonl(
        progress_log,
        {
            "event": "rss_stage_start",
            "timestamp": _now_iso(),
            "file": file_path.name,
            "phase": phase,
            "stage": stage,
            **extra,
        },
    )
    t0 = time.perf_counter()
    exc = None
    result = None
    with RSSPeakMonitor() as monitor:
        try:
            result = fn()
        except Exception as err:
            exc = err
    elapsed_s = time.perf_counter() - t0
    row = {
        "file": file_path.name,
        "phase": phase,
        "stage": stage,
        "elapsed_s": float(elapsed_s),
        "peak_rss_bytes": int(monitor.peak_rss_bytes),
        "peak_rss_gb": float(monitor.peak_rss_bytes) / (1024.0 ** 3),
        "peak_memory_pct": float(monitor.peak_memory_pct),
        **extra,
    }
    if exc is None:
        row["status"] = "done"
    else:
        row["status"] = "failed"
        row["error_type"] = exc.__class__.__name__
        row["error"] = str(exc)
    stage_rows.append(row)
    _append_jsonl(_rss_stage_log_path(result_root), row)
    _append_jsonl(
        progress_log,
        {
            "event": "rss_stage_done",
            "timestamp": _now_iso(),
            **row,
        },
    )
    _write_rss_stage_outputs(result_root, file_path, phase, stage_rows)
    if exc is not None:
        raise exc
    return result, row


def _section_sidecar_path(result_root: Path, file_name: str, section_label: str) -> Path:
    return result_root / "compression_results" / f"{section_label}_sidecars" / f"{file_name}.{section_label}_methods.json"


def _load_section_methods(result_root: Path, file_name: str, section_label: str) -> dict | None:
    payload = _load_json(_section_sidecar_path(result_root, file_name, section_label))
    if not payload:
        return None
    methods = payload.get("methods")
    return methods if isinstance(methods, dict) else None


def _sum_optional_times(*values) -> float | None:
    parsed = [_optional_float(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return float(sum(parsed))


def _sum_numeric_times(*stats: dict, key: str) -> float | None:
    values: list[float] = []
    for item in stats:
        value = item.get(key)
        if value is None:
            value = item.get("encode_time_s" if key == "numeric_encode_time_s" else "decode_time_s")
        parsed = _optional_float(value)
        if parsed is None:
            return None
        values.append(parsed)
    return float(sum(values))


def _combined_section_codec_method(
    *,
    method: str,
    ms1_stats: dict,
    ms2_stats: dict,
    meta_stats: dict,
) -> dict:
    raw_bytes = int(ms1_stats["raw_bytes"]) + int(ms2_stats["raw_bytes"]) + int(meta_stats["raw_bytes"])
    compressed_bytes = int(ms1_stats["compressed_bytes"]) + int(ms2_stats["compressed_bytes"]) + int(meta_stats["compressed_bytes"])
    out = {
        "raw_bytes": raw_bytes,
        "compressed_bytes": compressed_bytes,
        "compression_ratio": raw_bytes / compressed_bytes if compressed_bytes else 0.0,
    }
    numeric_encode = _sum_numeric_times(ms1_stats, ms2_stats, meta_stats, key="numeric_encode_time_s")
    numeric_decode = _sum_numeric_times(ms1_stats, ms2_stats, meta_stats, key="numeric_decode_time_s")
    encode_time = _sum_optional_times(
        ms1_stats.get("encode_time_s"),
        ms2_stats.get("encode_time_s"),
        meta_stats.get("encode_time_s"),
    )
    decode_time = _sum_optional_times(
        ms1_stats.get("decode_time_s"),
        ms2_stats.get("decode_time_s"),
        meta_stats.get("decode_time_s"),
    )
    if encode_time is None:
        encode_time = numeric_encode
    if decode_time is None:
        decode_time = numeric_decode
    out["encode_time_s"] = encode_time
    out["decode_time_s"] = decode_time
    if numeric_encode is not None:
        out["numeric_encode_time_s"] = numeric_encode
    if numeric_decode is not None:
        out["numeric_decode_time_s"] = numeric_decode
    has_numeric_section_scope = any(
        stats.get("encode_time_scope") == SECTION_NUMERIC_ENCODE_TIME_SCOPE
        or stats.get("decode_time_scope") == SECTION_NUMERIC_DECODE_TIME_SCOPE
        or stats.get("numeric_encode_time_s") is not None
        or stats.get("numeric_decode_time_s") is not None
        for stats in (ms1_stats, ms2_stats, meta_stats)
    )
    if has_numeric_section_scope or out["encode_time_s"] is None or out["decode_time_s"] is None:
        out["encode_time_scope"] = SECTION_NUMERIC_ENCODE_TIME_SCOPE
        out["decode_time_scope"] = SECTION_NUMERIC_DECODE_TIME_SCOPE
        out["timing_scope_version"] = BENCHMARK_TIME_SCOPE_VERSION
    return out


def _build_overall_container_methods(ms1_methods: dict, ms2_methods: dict, meta_methods: dict) -> dict:
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
    out = {
        "raw": {
            "raw_bytes": ms1_methods["raw"]["raw_bytes"] + ms2_methods["raw"]["raw_bytes"] + meta_methods["raw"]["raw_bytes"],
            "compressed_bytes": ms1_methods["raw"]["raw_bytes"] + ms2_methods["raw"]["raw_bytes"] + meta_methods["raw"]["raw_bytes"],
            "compression_ratio": 1.0,
            "encode_time_s": 0.0,
            "decode_time_s": 0.0,
        },
        "gzip": _combined_section_codec_method(
            method="gzip",
            ms1_stats=ms1_methods["gzip"],
            ms2_stats=ms2_methods["gzip"],
            meta_stats=meta_methods["gzip"],
        ),
        "zlib": _combined_section_codec_method(
            method="zlib",
            ms1_stats=ms1_methods["zlib"],
            ms2_stats=ms2_methods["zlib"],
            meta_stats=meta_methods["zlib"],
        ),
        "zstd-9": _combined_section_codec_method(
            method="zstd-9",
            ms1_stats=ms1_methods["zstd-9"],
            ms2_stats=ms2_methods["zstd-9"],
            meta_stats=meta_methods["zstd-9"],
        ),
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
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
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
            "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
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
    if "ours_archive_fidelity_auto" in ms1_methods:
        out["ours_archive_fidelity_auto_container"] = _combined_section_codec_method(
            method="ours_archive_fidelity_auto_container",
            ms1_stats=ms1_methods["ours_archive_fidelity_auto"],
            ms2_stats=ms2_methods["ours_eqfidelity"],
            meta_stats=meta_methods["ours_metadata"],
        )
        for key in (
            "ms1_representation",
            "ms1_full_mz_compressed_bytes",
        ):
            out["ours_archive_fidelity_auto_container"][key] = ms1_methods["ours_archive_fidelity_auto"].get(key)
    return out


def run_section(
    file_path: Path,
    result_root: Path,
    sections: list[str],
    baseline_threads: int,
    ms1_cpu_budget: int,
    ms1_track_prepare_workers: int,
    ms1_encode_section_workers: int,
    ms2_section_workers: int,
    ms2_segment_workers: int,
    min_available_mem_gb: float,
    compare_baseline_errors: bool,
    progress_log: Path,
    phase: str,
    stage_rows: list[dict],
) -> dict:
    comp_dir = result_root / "compression_results"
    comp_dir.mkdir(parents=True, exist_ok=True)
    file_name = file_path.name
    selected = set(sections)
    summary: dict[str, dict] = {"file": file_name, "sections": {}}
    ms1_worker_cfg = _resolve_ms1_section_workers(
        file_path=file_path,
        cpu_budget=ms1_cpu_budget,
        track_prepare_workers=ms1_track_prepare_workers,
        encode_section_workers=ms1_encode_section_workers,
        min_available_mem_gb=min_available_mem_gb,
    )
    if "ms1" in selected:
        _append_jsonl(
            progress_log,
            {
                "event": "section_ms1_worker_config",
                "timestamp": _now_iso(),
                "file": file_name,
                "phase": phase,
                **ms1_worker_cfg,
            },
        )

    runners = {
        "ms1": lambda: benchmark_ms1_validation_file(
            str(file_path),
            baseline_threads,
            compare_baseline_errors,
            artifact_root=result_root,
            ms1_cpu_budget=int(ms1_worker_cfg["cpu_budget"]),
            ms1_track_prepare_workers=int(ms1_worker_cfg["track_prepare_workers"]),
            ms1_encode_section_workers=int(ms1_worker_cfg["encode_section_workers"]),
            min_available_mem_gb=float(ms1_worker_cfg["min_available_mem_gb"]),
        ),
        "ms2": lambda: benchmark_ms2_validation_file(
            str(file_path),
            baseline_threads,
            compare_baseline_errors,
            artifact_root=result_root,
            ms2_section_workers=max(0, int(ms2_section_workers)),
            ms2_segment_workers=max(0, int(ms2_segment_workers)),
        ),
        "metadata": lambda: benchmark_metadata_validation_file(str(file_path)),
    }

    for section_name in ("ms1", "ms2", "metadata"):
        if section_name not in selected:
            continue
        _append_jsonl(
            progress_log,
            {
                "event": "section_start",
                "timestamp": _now_iso(),
                "file": file_name,
                "section": section_name,
            },
        )
        def _section_job():
            cached = _load_json(_section_sidecar_path(result_root, file_name, section_name))
            if cached and isinstance(cached.get("methods"), dict):
                _write_section_sidecar(comp_dir / f"{section_name}_sidecars", file_name, section_name, cached)
                _write_csv(_flatten_method_rows({file_name: cached["methods"]}), comp_dir / f"{section_name}_per_file_methods.csv")
                _append_jsonl(
                    progress_log,
                    {
                        "event": "section_cache_hit",
                        "timestamp": _now_iso(),
                        "file": file_name,
                        "section": section_name,
                    },
                )
                return {"payload": cached, "cache_hit": True}
            row = runners[section_name]()
            _write_section_sidecar(comp_dir / f"{section_name}_sidecars", file_name, section_name, row)
            _write_csv(_flatten_method_rows({file_name: row["methods"]}), comp_dir / f"{section_name}_per_file_methods.csv")
            return {"payload": row, "cache_hit": False}

        stage_result, stage_metric = _run_monitored_stage(
            result_root=result_root,
            progress_log=progress_log,
            file_path=file_path,
            phase=phase,
            stage_rows=stage_rows,
            stage=f"section_{section_name}",
            fn=_section_job,
            section=section_name,
        )
        row = stage_result["payload"]
        _append_jsonl(
            progress_log,
            {
                "event": "section_done",
                "timestamp": _now_iso(),
                "file": file_name,
                "section": section_name,
                "elapsed_s": float(stage_metric["elapsed_s"]),
                "peak_rss_bytes": int(stage_metric["peak_rss_bytes"]),
                "peak_rss_gb": float(stage_metric["peak_rss_gb"]),
                "cache_hit": bool(stage_result.get("cache_hit", False)),
            },
        )
        summary["sections"][section_name] = row

    ms1_methods = _load_section_methods(result_root, file_name, "ms1")
    ms2_methods = _load_section_methods(result_root, file_name, "ms2")
    meta_methods = _load_section_methods(result_root, file_name, "metadata")
    if ms1_methods and ms2_methods and meta_methods:
        overall_methods = _build_overall_container_methods(ms1_methods, ms2_methods, meta_methods)
        _write_json(
            comp_dir / f"{file_name}.overall_methods.json",
            {"file": file_name, "methods": overall_methods},
        )
        _write_csv(_flatten_method_rows({file_name: overall_methods}), comp_dir / "overall_per_file_methods.csv")
        summary["overall_methods"] = overall_methods

    _write_json(result_root / "section_summary.json", summary)
    return summary


def run_whole(
    file_path: Path,
    section_root: Path | None,
    result_root: Path,
    whole_run_mode: str,
    whole_ms2_section_workers: int,
    whole_ms2_segment_workers: int,
    variants: list[str],
    progress_log: Path,
    phase: str,
    stage_rows: list[dict],
    require_section_sidecars: bool = True,
) -> dict:
    comp_dir = result_root / "compression_results"
    archive_dir = result_root / "compressed_archives"
    recon_dir = result_root / "reconstructed_mzml"
    roundtrip_dir = result_root / "roundtrip_validation"
    for path in (comp_dir, archive_dir, recon_dir, roundtrip_dir):
        path.mkdir(parents=True, exist_ok=True)

    file_name = file_path.name
    ms1_methods = _load_section_methods(section_root, file_name, "ms1") if section_root is not None else None
    ms2_methods = _load_section_methods(section_root, file_name, "ms2") if section_root is not None else None
    meta_methods = _load_section_methods(section_root, file_name, "metadata") if section_root is not None else None
    missing_section_sidecars = [
        name
        for name, methods in (
            ("ms1", ms1_methods),
            ("ms2", ms2_methods),
            ("metadata", meta_methods),
        )
        if not methods
    ]
    section_methods_available = not missing_section_sidecars
    section_dependent_rows_deferred = not section_methods_available
    whole_artifact_root = section_root if section_methods_available and section_root is not None else result_root
    if not section_methods_available:
        if require_section_sidecars:
            raise FileNotFoundError(f"Missing section sidecars for {file_name}; expected ms1/ms2/metadata under {section_root}")
        _append_jsonl(
            progress_log,
            {
                "event": "whole_section_sidecars_deferred",
                "timestamp": _now_iso(),
                "file": file_name,
                "section_root": str(section_root) if section_root is not None else "",
                "missing_section_sidecars": missing_section_sidecars,
                "whole_artifact_root": str(whole_artifact_root),
            },
        )

    aux_cache = comp_dir / f"{file_name}.auxiliary_zlib_stats.json"
    def _aux_job():
        aux_stats = _load_json(aux_cache)
        if aux_stats is None:
            aux_stats = _auxiliary_zlib_stats(file_path)
            _write_json(aux_cache, aux_stats)
            return {"payload": aux_stats, "cache_hit": False}
        return {"payload": aux_stats, "cache_hit": True}

    aux_stats = None
    if section_methods_available:
        aux_result, _ = _run_monitored_stage(
            result_root=result_root,
            progress_log=progress_log,
            file_path=file_path,
            phase=phase,
            stage_rows=stage_rows,
            stage="whole_auxiliary_zlib",
            fn=_aux_job,
        )
        aux_stats = aux_result["payload"]

    baseline_cache = comp_dir / f"{file_name}.whole_file_baselines.json"
    def _baseline_job():
        baseline_stats = _load_json(baseline_cache)
        if not _whole_file_baseline_cache_valid(baseline_stats, file_path):
            baseline_stats = {}
            with tempfile.TemporaryDirectory(prefix="trackcodec_single_whole_") as tmp_root:
                tmp_dir = Path(tmp_root)
                for method in WHOLE_FILE_BASELINE_METHODS:
                    baseline_stats[method] = _compress_file_streaming(file_path, method, tmp_dir)
            _write_json(baseline_cache, baseline_stats)
            return {"payload": baseline_stats, "cache_hit": False}
        return {"payload": baseline_stats, "cache_hit": True}

    baseline_result, _ = _run_monitored_stage(
        result_root=result_root,
        progress_log=progress_log,
        file_path=file_path,
        phase=phase,
        stage_rows=stage_rows,
        stage="whole_file_baselines",
        fn=_baseline_job,
    )
    baseline_stats = baseline_result["payload"]

    variant_map = {
        "eqfidelity": "ours_whole_archive",
        "strict_q6": "ours_strict_q6_whole_archive",
    }
    whole_results: dict[str, dict] = {}
    for variant in variants:
        _append_jsonl(
            progress_log,
            {
                "event": "whole_variant_start",
                "timestamp": _now_iso(),
                "file": file_name,
                "archive_variant": variant,
                "whole_run_mode": whole_run_mode,
            },
        )
        result, stage_metric = _run_monitored_stage(
            result_root=result_root,
            progress_log=progress_log,
            file_path=file_path,
            phase=phase,
            stage_rows=stage_rows,
            stage=f"whole_variant_{variant}",
            fn=lambda variant=variant: benchmark_whole_archive_file(
                str(file_path),
                str(archive_dir),
                str(recon_dir),
                str(roundtrip_dir),
                str(comp_dir),
                int(whole_ms2_section_workers),
                int(whole_ms2_segment_workers),
                variant,
                whole_run_mode,
                str(progress_log),
                str(whole_artifact_root),
            ),
            archive_variant=variant,
        )
        whole_results[variant_map[variant]] = result
        _append_jsonl(
            progress_log,
            {
                "event": "whole_variant_done",
                "timestamp": _now_iso(),
                "file": file_name,
                "archive_variant": variant,
                "method": variant_map[variant],
                "elapsed_s": float(stage_metric["elapsed_s"]),
                "peak_rss_bytes": int(stage_metric["peak_rss_bytes"]),
                "peak_rss_gb": float(stage_metric["peak_rss_gb"]),
            },
        )

    input_bytes = int(file_path.stat().st_size)
    overall_rows = []
    for method in WHOLE_FILE_BASELINE_METHODS:
        stats = baseline_stats[method]
        overall_rows.append(
            {
                "file": file_name,
                "method": method,
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

    if section_methods_available:
        container_meta = meta_methods[CONTAINER_METADATA_METHOD]
        zdpd_comp = int(ms1_methods["zdpd_baseline"]["compressed_bytes"]) + int(ms2_methods["zdpd_baseline"]["compressed_bytes"]) + int(container_meta["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
        zdpd_encode = (
            float(ms1_methods["zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(ms2_methods["zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(container_meta.get("numeric_encode_time_s", container_meta.get("encode_time_s", 0.0)))
            + float(aux_stats["encode_time_s"])
        )
        zdpd_decode = (
            float(ms1_methods["zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(ms2_methods["zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(container_meta.get("numeric_decode_time_s", container_meta.get("decode_time_s", 0.0)))
            + float(aux_stats["decode_time_s"])
        )
        overall_rows.append(
            {
                "file": file_name,
                "method": "zdpd_container",
                "raw_bytes": input_bytes,
                "compressed_bytes": zdpd_comp,
                "compression_ratio": input_bytes / zdpd_comp if zdpd_comp else 0.0,
                "encode_time_s": zdpd_encode,
                "decode_time_s": None,
                "numeric_encode_time_s": zdpd_encode,
                "numeric_decode_time_s": zdpd_decode,
                "metadata_method": CONTAINER_METADATA_METHOD,
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            }
        )

        stack_comp = int(ms1_methods["stack_zdpd_baseline"]["compressed_bytes"]) + int(ms2_methods["stack_zdpd_baseline"]["compressed_bytes"]) + int(container_meta["compressed_bytes"]) + int(aux_stats["compressed_bytes"])
        stack_encode = (
            float(ms1_methods["stack_zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(ms2_methods["stack_zdpd_baseline"].get("numeric_encode_time_s", 0.0))
            + float(container_meta.get("numeric_encode_time_s", container_meta.get("encode_time_s", 0.0)))
            + float(aux_stats["encode_time_s"])
        )
        stack_decode = (
            float(ms1_methods["stack_zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(ms2_methods["stack_zdpd_baseline"].get("numeric_decode_time_s", 0.0))
            + float(container_meta.get("numeric_decode_time_s", container_meta.get("decode_time_s", 0.0)))
            + float(aux_stats["decode_time_s"])
        )
        overall_rows.append(
            {
                "file": file_name,
                "method": "stack_zdpd_container",
                "raw_bytes": input_bytes,
                "compressed_bytes": stack_comp,
                "compression_ratio": input_bytes / stack_comp if stack_comp else 0.0,
                "encode_time_s": stack_encode,
                "decode_time_s": None,
                "numeric_encode_time_s": stack_encode,
                "numeric_decode_time_s": stack_decode,
                "metadata_method": CONTAINER_METADATA_METHOD,
                "encode_time_scope": SECTION_NUMERIC_ENCODE_TIME_SCOPE,
                "decode_time_scope": NO_WHOLE_FILE_MZML_DECODER_SCOPE,
                "timing_scope_version": BENCHMARK_TIME_SCOPE_VERSION,
            }
        )

    component_rows = []
    roundtrip_rows = []
    for method, result in whole_results.items():
        stats = result.get("stats") or {}
        summary = result.get("summary")
        overall_rows.append(
            {
                "file": file_name,
                "method": method,
                "raw_bytes": int(stats.get("raw_bytes", input_bytes)),
                "compressed_bytes": int(stats.get("compressed_bytes", 0)),
                "compression_ratio": float(stats.get("compression_ratio", 0.0)),
                "encode_time_s": float(stats.get("encode_time_s", 0.0)),
                "decode_time_s": stats.get("decode_time_s"),
                "encode_time_scope": str(stats.get("encode_time_scope", WHOLE_ARCHIVE_ENCODE_TIME_SCOPE)),
                "decode_time_scope": str(stats.get("decode_time_scope", WHOLE_ARCHIVE_DECODE_TIME_SCOPE)),
                "timing_scope_version": str(stats.get("timing_scope_version", "")),
                "max_abs_mz_error": stats.get("max_abs_mz_error"),
                "max_abs_intensity_error": stats.get("max_abs_intensity_error"),
                "mz_within_ceiling": stats.get("mz_within_ceiling"),
                "intensity_within_ceiling": stats.get("intensity_within_ceiling"),
                "aux_counts_match": stats.get("aux_counts_match"),
                "compare_completed": bool(stats.get("compare_completed", False)),
            }
        )
        component_rows.append(
            {
                "file": file_name,
                "method": method,
                "archive_variant": stats.get("archive_variant"),
                "file_type": stats.get("file_type"),
                "archive_bytes": int(stats.get("compressed_bytes", 0)),
                "ms1_compressed_bytes": int(stats.get("ms1_compressed_bytes", 0)),
                "ms2_compressed_bytes": int(stats.get("ms2_compressed_bytes", 0)),
                "metadata_compressed_bytes": int(stats.get("metadata_compressed_bytes", 0)),
                "auxiliary_compressed_bytes": int(stats.get("auxiliary_compressed_bytes", 0)),
                "ms1_full_mz_compressed_bytes": int(stats.get("ms1_full_mz_compressed_bytes", 0)),
            }
        )
        if summary and summary.get("roundtrip"):
            roundtrip = summary["roundtrip"]
            roundtrip_rows.append(
                {
                    "file": file_name,
                    "method": method,
                    "archive_variant": summary.get("archive_variant"),
                    "archive_path": summary["archive_path"],
                    "reconstructed_mzml_path": summary["reconstructed_mzml_path"],
                    "compression_ratio_vs_input_file": summary["compression_ratio_vs_input_file"],
                    "encode_time_s": summary["encode_time_s"],
                    "decode_time_s": summary["decode_time_s"],
                    "spectrum_count": roundtrip["spectrum_count"],
                    "ms1_count": roundtrip["ms1_count"],
                    "ms2_count": roundtrip["ms2_count"],
                    "compared_point_count": roundtrip["compared_point_count"],
                    "max_abs_mz_error": roundtrip["max_abs_mz_error"],
                    "max_abs_intensity_error": roundtrip["max_abs_intensity_error"],
                    "p95_spectrum_p95_abs_mz_error": roundtrip["spectrum_p95_abs_mz_error"]["p95"],
                    "p95_spectrum_p95_abs_intensity_error": roundtrip["spectrum_p95_abs_intensity_error"]["p95"],
                    "p95_spectrum_p95_rel_intensity_error": roundtrip["spectrum_p95_rel_intensity_error"]["p95"],
                    "p95_spectrum_tic_rel_error": roundtrip["spectrum_tic_rel_error"]["p95"],
                    "p95_spectrum_bpi_rel_error": roundtrip["spectrum_bpi_rel_error"]["p95"],
                    "effective_intensity_error_ceiling": roundtrip.get("effective_intensity_error_ceiling"),
                    "effective_intensity_error_ceiling_basis": roundtrip.get("effective_intensity_error_ceiling_basis"),
                    "effective_intensity_error_ceiling_dtype": roundtrip.get("effective_intensity_error_ceiling_dtype"),
                    "intensity_dtype_match": roundtrip.get("intensity_dtype_match"),
                    "mz_within_ceiling": summary["mz_within_ceiling"],
                    "intensity_within_ceiling": summary["intensity_within_ceiling"],
                    "roundtrip_passed": summary.get("roundtrip_passed"),
                    "xml_metadata_warning": summary.get("xml_metadata_warning"),
                    "aux_counts_match": roundtrip["aux_counts_match"],
                    "binary_stripped_xml_equal": roundtrip["binary_stripped_xml_equal"],
                    "xml_without_binary_arrays_equal": roundtrip["xml_without_binary_arrays_equal"],
                }
            )

    _write_csv(overall_rows, comp_dir / "whole_archive_per_file_methods.csv")
    _write_csv(component_rows, comp_dir / "whole_archive_component_breakdown.csv")
    _write_csv(roundtrip_rows, roundtrip_dir / "whole_archive_roundtrip_validation.csv")
    summary = {
        "file": file_name,
        "baseline_stats": baseline_stats,
        "auxiliary_zlib_stats": aux_stats,
        "section_methods_available": bool(section_methods_available),
        "missing_section_sidecars": missing_section_sidecars,
        "section_root": str(section_root) if section_root is not None else "",
        "whole_artifact_root": str(whole_artifact_root),
        "section_dependent_rows_deferred": bool(section_dependent_rows_deferred),
        "whole_rows": overall_rows,
        "component_rows": component_rows,
        "roundtrip_rows": roundtrip_rows,
    }
    _write_json(result_root / "whole_summary.json", summary)
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run TrackCodec benchmark for one mzML file with resumable sidecars.")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("section", "whole", "all"), required=True)
    parser.add_argument("--section-root", type=Path, default=None)
    parser.add_argument("--sections", nargs="+", choices=("ms1", "ms2", "metadata"), default=["ms1", "ms2", "metadata"])
    parser.add_argument("--whole-variants", nargs="+", choices=("eqfidelity", "strict_q6"), default=["eqfidelity", "strict_q6"])
    parser.add_argument("--whole-run-mode", choices=WHOLE_RUN_MODE_CHOICES, default="all")
    parser.add_argument("--baseline-threads", type=int, default=1)
    parser.add_argument("--ms1-cpu-budget", type=int, default=0)
    parser.add_argument("--ms1-track-prepare-workers", type=int, default=0)
    parser.add_argument("--ms1-encode-section-workers", type=int, default=0)
    parser.add_argument("--ms2-section-workers", type=int, default=0)
    parser.add_argument("--ms2-segment-workers", type=int, default=0)
    parser.add_argument("--min-available-mem-gb", type=float, default=20.0)
    parser.add_argument("--whole-ms2-section-workers", type=int, default=2)
    parser.add_argument("--whole-ms2-segment-workers", type=int, default=0)
    parser.add_argument("--compare-baseline-errors", action="store_true")
    parser.add_argument("--allow-missing-section-sidecars", action="store_true")
    parser.add_argument("--dataset-label", type=str, default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    native_status = assert_native_speedups_available(required=True)
    file_path = Path(args.file).resolve()
    result_root = Path(args.result_root).resolve()
    result_root.mkdir(parents=True, exist_ok=True)
    log_dir = result_root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    progress_log = log_dir / "worker.progress.jsonl"
    stage_rows: list[dict] = []
    stale_failure = result_root / "worker_failure.json"
    if stale_failure.exists():
        stale_failure.rename(log_dir / f"worker_failure.previous.{int(time.time())}.json")

    _append_jsonl(
        progress_log,
        {
            "event": "worker_start",
            "timestamp": _now_iso(),
            "file": file_path.name,
            "file_path": str(file_path),
            "phase": args.phase,
            "dataset_label": args.dataset_label,
            "args": _json_safe(vars(args)),
            "native_speedups": native_status,
        },
    )

    try:
        if args.phase in ("section", "all"):
            run_section(
                file_path=file_path,
                result_root=result_root if args.phase == "section" else result_root / "section",
                sections=list(args.sections),
                baseline_threads=max(0, int(args.baseline_threads)),
                ms1_cpu_budget=max(0, int(args.ms1_cpu_budget)),
                ms1_track_prepare_workers=max(0, int(args.ms1_track_prepare_workers)),
                ms1_encode_section_workers=max(0, int(args.ms1_encode_section_workers)),
                ms2_section_workers=max(0, int(args.ms2_section_workers)),
                ms2_segment_workers=max(0, int(args.ms2_segment_workers)),
                min_available_mem_gb=float(args.min_available_mem_gb),
                compare_baseline_errors=bool(args.compare_baseline_errors),
                progress_log=progress_log,
                phase=args.phase if args.phase == "section" else "section",
                stage_rows=stage_rows,
            )
        if args.phase in ("whole", "all"):
            if args.phase == "all":
                section_root = result_root / "section"
                whole_root = result_root / "whole"
            else:
                if args.section_root is None:
                    if not bool(args.allow_missing_section_sidecars):
                        raise SystemExit("--section-root is required when --phase whole unless --allow-missing-section-sidecars is set")
                    section_root = None
                else:
                    section_root = Path(args.section_root).resolve()
                whole_root = result_root
            run_whole(
                file_path=file_path,
                section_root=section_root,
                result_root=whole_root,
                whole_run_mode=str(args.whole_run_mode),
                whole_ms2_section_workers=max(1, int(args.whole_ms2_section_workers)),
                whole_ms2_segment_workers=max(0, int(args.whole_ms2_segment_workers)),
                variants=list(args.whole_variants),
                progress_log=progress_log,
                phase=args.phase if args.phase == "whole" else "whole",
                stage_rows=stage_rows,
                require_section_sidecars=not bool(args.allow_missing_section_sidecars),
            )
    except Exception as exc:
        failure = {
            "event": "worker_failed",
            "timestamp": _now_iso(),
            "file": file_path.name,
            "phase": args.phase,
            "error_type": exc.__class__.__name__,
            "error": str(exc),
            "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        }
        _append_jsonl(progress_log, failure)
        _write_json(result_root / "worker_failure.json", failure)
        raise

    _append_jsonl(
        progress_log,
        {
            "event": "worker_done",
            "timestamp": _now_iso(),
            "file": file_path.name,
            "phase": args.phase,
        },
    )
    _write_rss_stage_outputs(result_root, file_path, args.phase, stage_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
