from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None


class RSSPeakMonitor:
    def __init__(self, pid: int, interval_s: float = 0.2):
        self.pid = int(pid)
        self.interval_s = float(interval_s)
        self.peak_rss_bytes = 0
        self.peak_process_count = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        if psutil is None:
            return
        try:
            root = psutil.Process(self.pid)
            procs = [root] + root.children(recursive=True)
        except Exception:
            return
        rss = 0
        count = 0
        seen = set()
        for proc in procs:
            if proc.pid in seen:
                continue
            seen.add(proc.pid)
            try:
                rss += int(proc.memory_info().rss)
                count += 1
            except Exception:
                continue
        self.peak_rss_bytes = max(int(self.peak_rss_bytes), int(rss))
        self.peak_process_count = max(int(self.peak_process_count), int(count))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval_s)
        self._sample()

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.interval_s * 4.0))


def _run_worker(
    *,
    archive: Path,
    output_mzml: Path,
    workers: int,
    package_root: Path,
    binary_compression: str,
) -> dict:
    output_mzml.parent.mkdir(parents=True, exist_ok=True)
    stats_json = output_mzml.with_suffix(output_mzml.suffix + ".decode_profile.json")
    cmd = [
        sys.executable,
        "-m",
        "decode_archive_profile",
        "--worker",
        "--archive",
        str(archive),
        "--output-mzml",
        str(output_mzml),
        "--workers",
        str(int(workers)),
        "--binary-compression",
        str(binary_compression),
        "--stats-json",
        str(stats_json),
    ]
    env = dict(os.environ)
    parent = str(package_root.parent)
    env["PYTHONPATH"] = parent if not env.get("PYTHONPATH") else f"{parent}:{env['PYTHONPATH']}"
    proc = subprocess.Popen(cmd, cwd=str(package_root), env=env)
    t0 = time.perf_counter()
    with RSSPeakMonitor(proc.pid) as monitor:
        rc = proc.wait()
    elapsed = time.perf_counter() - t0
    if rc != 0:
        raise RuntimeError(f"decode worker failed with exit code {rc}: {' '.join(cmd)}")
    stats = json.loads(stats_json.read_text(encoding="utf-8"))
    stats.update(
        {
            "elapsed_s_parent": float(elapsed),
            "peak_rss_bytes": int(monitor.peak_rss_bytes),
            "peak_rss_gb": float(monitor.peak_rss_bytes) / (1024.0 ** 3),
            "peak_process_count": int(monitor.peak_process_count),
        }
    )
    stats_json.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    return stats


def _worker_main(args) -> None:
    from TrackCodec.production.mzml.archive_codec import MzMLSectionArchiveCodec

    archive = Path(args.archive)
    output_mzml = Path(args.output_mzml)
    worker_arg = args.workers[0] if isinstance(args.workers, list) else args.workers
    workers = max(1, int(worker_arg))
    codec = MzMLSectionArchiveCodec(
        ms2_section_workers=workers,
        ms2_segment_workers=workers,
        decode_segment_workers=workers,
    )
    t0 = time.perf_counter()
    sections = codec.decode_archive_file(
        archive,
        decode_ms1_sidecars=True,
        decode_ms2_payload_json=True,
        decode_auxiliary_records_flag=True,
        decode_metadata_xml=True,
    )
    read_decode_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    codec._sections_to_mzml_file(
        sections,
        output_mzml,
        binary_compression=args.binary_compression,
    )
    rebuild_write_s = time.perf_counter() - t1
    total_s = time.perf_counter() - t0
    archive_meta = sections.get("archive_meta", {})
    stats = {
        "archive": str(archive),
        "output_mzml": str(output_mzml),
        "workers": int(workers),
        "archive_bytes": int(archive.stat().st_size),
        "output_mzml_bytes": int(output_mzml.stat().st_size),
        "file_type": archive_meta.get("file_type"),
        "source_file_name": archive_meta.get("source_file_name"),
        "decode_archive_sections_s": float(read_decode_s),
        "rebuild_write_mzml_s": float(rebuild_write_s),
        "decode_to_mzml_total_s": float(total_s),
        "binary_compression": str(args.binary_compression),
    }
    Path(args.stats_json).write_text(json.dumps(stats, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile TrackCodec archive decode with worker/RSS sweep.")
    parser.add_argument("--archive", required=True)
    parser.add_argument("--result-root", default=None)
    parser.add_argument("--output-mzml", default=None)
    parser.add_argument("--workers", nargs="+", type=int, default=[1, 4, 8, 16])
    parser.add_argument("--binary-compression", choices=("preserve_template", "none", "zlib"), default="preserve_template")
    parser.add_argument("--stats-json", default=None)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()

    package_root = Path(__file__).resolve().parents[2]
    archive = Path(args.archive)
    if args.worker:
        if not args.output_mzml or not args.stats_json:
            raise SystemExit("--worker requires --output-mzml and --stats-json")
        _worker_main(args)
        return

    result_root = Path(args.result_root) if args.result_root else Path.cwd() / "decode_profile"
    result_root.mkdir(parents=True, exist_ok=True)
    rows = []
    for workers in args.workers:
        out = result_root / "reconstructed" / f"{archive.stem}.decode_workers_{int(workers)}.mzML"
        if out.exists():
            out.unlink()
        row = _run_worker(
            archive=archive,
            output_mzml=out,
            workers=int(workers),
            package_root=package_root,
            binary_compression=args.binary_compression,
        )
        rows.append(row)
    summary = {"archive": str(archive), "rows": rows}
    (result_root / "decode_profile_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(result_root / "decode_profile_summary.csv", rows)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
