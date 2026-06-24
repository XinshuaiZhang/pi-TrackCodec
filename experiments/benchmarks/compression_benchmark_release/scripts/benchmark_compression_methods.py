from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import html
import json
import locale
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from benchmark_release_paths import default_run_output_root, find_trackcodec_root, find_workspace_root

REPO_ROOT = find_workspace_root(Path(__file__))
BIN_DIR = REPO_ROOT / "bin"
TRACKCODEC_ROOT = find_trackcodec_root(Path(__file__))
DEFAULT_TRACKCODEC_PYTHON = Path(
    os.environ.get(
        "TRACKCODEC_PYTHON",
        sys.executable,
    )
)
AIRDPRO_CONFIG_JSON = Path(
    os.environ.get(
        "AIRDPRO_CONFIG_JSON",
        str(REPO_ROOT / "tools" / "AirdPro-6.0.3-mz6-keepzero" / "ConversionConfig.json"),
    )
)
AIRDPRO_STACKFIX_EXE = Path(
    os.environ.get(
        "AIRDPRO_STACKFIX_EXE",
        str(REPO_ROOT / "tools" / "AirdPro-5.3.1-stackfix-dda" / "AirdPro.exe"),
    )
)

DISCOVER_EXTENSIONS = (".mzml", ".mzxml")
LOG_TAIL_CHARS = 4000


METHODS = OrderedDict(
    [
        ("msconvert_zlib", "msconvert zlib"),
        ("msconvert_gzip", "msconvert gzip"),
        ("msconvert_numpress", "msconvert numpressAll+zlib"),
        ("mspack", "mspack"),
        ("masscomp", "MassComp"),
        ("airdpro", "airdpro-compress"),
        ("zdpd", "ZDPD wrapper"),
        ("stackzdpd", "StackZDPD wrapper"),
        ("trackcodec", "TrackCodec"),
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run mass-spec compression benchmarks and generate an HTML summary. "
            "Inputs may be files or directories containing mzML/mzXML files."
        )
    )
    parser.add_argument("paths", nargs="*", help="Input file or directory paths.")
    parser.add_argument(
        "-i",
        "--input",
        action="append",
        nargs="+",
        default=[],
        help="Additional input file or directory paths. Can be used multiple times.",
    )
    parser.add_argument(
        "--input-list",
        action="append",
        default=[],
        help="Text file containing one input path per line.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively discover mzML/mzXML files under input directories.",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=list(DISCOVER_EXTENSIONS),
        help="Extensions to discover in directories. Default: .mzML .mzXML",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        default=list(METHODS.keys()),
        help=f"Methods to run, or 'all'. Available: {', '.join(METHODS)}",
    )
    parser.add_argument(
        "--skip-methods",
        nargs="+",
        default=[],
        help="Methods to remove from the selected method list.",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="Benchmark output directory. Default: timestamped directory under the release benchmark outputs/runs directory.",
    )
    parser.add_argument(
        "--html",
        default="",
        help="HTML report path. Default: <output-root>\\summary.html",
    )
    parser.add_argument(
        "--json",
        default="",
        help="JSON result path. Default: <output-root>\\summary.json",
    )
    parser.add_argument(
        "--csv",
        default="",
        help="CSV result path. Default: <output-root>\\summary.csv",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=0,
        help="Per command timeout in seconds. 0 disables timeout.",
    )
    parser.add_argument(
        "--airdpro-timeout-seconds",
        type=int,
        default=1800,
        help=(
            "Timeout for AirdPro wrapper commands. AirdPro CLI can write output "
            "but keep waiting; timed-out commands with output files are measured."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse results already present in the JSON report and run only missing file/method pairs.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="Only benchmark the first N discovered files. 0 means no limit.",
    )
    parser.add_argument(
        "--sort-by-size-desc",
        action="store_true",
        help="Sort discovered input files by size descending before benchmarking.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first method failure.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "Number of input files to benchmark concurrently. Each file still runs "
            "the selected methods sequentially. Default: 1."
        ),
    )
    parser.add_argument(
        "--airdpro-config",
        default="Default",
        help="AirdPro config for the airdpro-compress method.",
    )
    parser.add_argument(
        "--airdpro-zdpd-config",
        default="ZDPD",
        help="AirdPro config used by the zdpd wrapper method.",
    )
    parser.add_argument(
        "--airdpro-stackzdpd-config",
        default="StackZDPD",
        help="AirdPro config used by the stackzdpd wrapper method.",
    )
    parser.add_argument(
        "--airdpro-stackzdpd-extra",
        default="--digit 8",
        help=(
            "Extra arguments passed to the AirdPro 5.3.1 bridge for stackzdpd. "
            "Default uses digit=8 with the local AirdPro 5.3.1 stack-tail fix."
        ),
    )
    parser.add_argument(
        "--airdpro-acquisition",
        default="",
        help="Optional acquisition value passed as the 4th argument to AirdPro wrappers, e.g. DIA.",
    )
    parser.add_argument(
        "--trackcodec-python",
        default=str(DEFAULT_TRACKCODEC_PYTHON),
        help="Python executable for TrackCodec.",
    )
    parser.add_argument(
        "--trackcodec-section-workers",
        type=int,
        default=2,
        help="TrackCodec --section-workers value.",
    )
    parser.add_argument(
        "--trackcodec-ms2-segment-workers",
        type=int,
        default=2,
        help="TrackCodec --ms2-segment-workers value.",
    )
    parser.add_argument(
        "--trackcodec-ms1-island-workers",
        type=int,
        default=1,
        help="TrackCodec --ms1-island-workers value.",
    )
    return parser.parse_args()


def flatten_inputs(args: argparse.Namespace) -> list[Path]:
    raw: list[str] = []
    raw.extend(args.paths)
    for group in args.input:
        raw.extend(group)
    for list_path in args.input_list:
        p = Path(list_path)
        with p.open("r", encoding="utf-8-sig") as handle:
            for line in handle:
                stripped = line.strip().strip('"')
                if stripped and not stripped.startswith("#"):
                    raw.append(stripped)
    return [Path(item).expanduser() for item in raw]


def discover_files(args: argparse.Namespace) -> list[Path]:
    extensions = {ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in args.extensions}
    files: list[Path] = []
    for path in flatten_inputs(args):
        if path.is_file():
            files.append(path.resolve())
            continue
        if path.is_dir():
            iterator = path.rglob("*") if args.recursive else path.glob("*")
            for item in sorted(iterator, key=lambda p: str(p).lower()):
                if item.is_file() and item.suffix.lower() in extensions:
                    files.append(item.resolve())
            continue
        raise FileNotFoundError(f"Input path does not exist: {path}")
    unique = list(dict.fromkeys(files))
    if args.sort_by_size_desc:
        unique.sort(key=lambda p: p.stat().st_size, reverse=True)
    if args.max_files and args.max_files > 0:
        unique = unique[: args.max_files]
    return unique


def selected_methods(args: argparse.Namespace) -> list[str]:
    requested = list(args.methods)
    if any(m.lower() == "all" for m in requested):
        requested = list(METHODS.keys())
    unknown = [m for m in requested if m not in METHODS]
    if unknown:
        raise ValueError(f"Unknown methods: {', '.join(unknown)}")
    skip = set(args.skip_methods)
    unknown_skip = [m for m in skip if m not in METHODS]
    if unknown_skip:
        raise ValueError(f"Unknown skip methods: {', '.join(unknown_skip)}")
    return [m for m in requested if m not in skip]


def make_output_root(args: argparse.Namespace) -> Path:
    if args.output_root:
        root = Path(args.output_root)
    else:
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        root = default_run_output_root(Path(__file__)) / f"run_{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def safe_name(value: str, max_len: int = 96) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    if not name:
        name = "input"
    return name[:max_len]


def input_id(path: Path) -> str:
    digest = hashlib.sha1(str(path).lower().encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe_name(path.stem)}_{digest}"


def fmt_bytes(value: int | None) -> str:
    if value is None:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    n = float(value)
    for unit in units:
        if n < 1024.0 or unit == units[-1]:
            return f"{n:.2f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{value} B"


def fmt_num(value: float | None, digits: int = 4) -> str:
    if value is None:
        return ""
    return f"{value:.{digits}f}"


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}s"


def dir_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted([p for p in path.rglob("*") if p.is_file()], key=lambda p: str(p).lower())


def file_size_sum(files: list[Path]) -> int:
    total = 0
    for file_path in files:
        try:
            total += file_path.stat().st_size
        except OSError:
            pass
    return total


def command_text(args: list[str]) -> str:
    return subprocess.list2cmdline([str(a) for a in args])


def decode_text(data: bytes) -> str:
    encodings = [locale.getpreferredencoding(False), "utf-8", "gbk", "cp936"]
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def run_command(
    args: list[str],
    cwd: Path | None,
    timeout_seconds: int,
    log_prefix: Path,
) -> dict[str, Any]:
    start = time.perf_counter()
    started_at_epoch = time.time()
    result: dict[str, Any] = {
        "args": [str(a) for a in args],
        "command": command_text([str(a) for a in args]),
        "cwd": str(cwd) if cwd else "",
        "returncode": None,
        "elapsed_seconds": None,
        "started_at_epoch": started_at_epoch,
        "ended_at_epoch": None,
        "timed_out": False,
        "stdout_log": str(log_prefix.with_suffix(".stdout.log")),
        "stderr_log": str(log_prefix.with_suffix(".stderr.log")),
        "stdout_tail": "",
        "stderr_tail": "",
    }
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [str(a) for a in args],
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = proc.communicate(
            timeout=timeout_seconds if timeout_seconds and timeout_seconds > 0 else None
        )
        elapsed = time.perf_counter() - start
        stdout = decode_text(stdout_bytes)
        stderr = decode_text(stderr_bytes)
        result["returncode"] = proc.returncode
        result["elapsed_seconds"] = elapsed
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - start
        stdout = decode_text(exc.stdout or b"")
        stderr = decode_text(exc.stderr or b"")
        try:
            if proc is not None and os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
            elif proc is not None:
                proc.kill()
        except Exception as kill_exc:
            stderr += f"\nFailed to terminate process tree: {kill_exc}"
        try:
            if proc is not None:
                more_stdout, more_stderr = proc.communicate(timeout=10)
                stdout += decode_text(more_stdout or b"")
                stderr += decode_text(more_stderr or b"")
        except Exception:
            pass
        stderr = (stderr + "\n" if stderr else "") + f"Timed out after {timeout_seconds} seconds."
        result["returncode"] = -999
        result["elapsed_seconds"] = elapsed
        result["timed_out"] = True
    result["ended_at_epoch"] = time.time()
    log_prefix.parent.mkdir(parents=True, exist_ok=True)
    Path(result["stdout_log"]).write_text(stdout, encoding="utf-8", errors="replace")
    Path(result["stderr_log"]).write_text(stderr, encoding="utf-8", errors="replace")
    result["stdout_tail"] = stdout[-LOG_TAIL_CHARS:]
    result["stderr_tail"] = stderr[-LOG_TAIL_CHARS:]
    return result


def base_result(input_path: Path, method: str, method_dir: Path) -> dict[str, Any]:
    original_size = input_path.stat().st_size
    return {
        "file_name": input_path.name,
        "input_path": str(input_path),
        "method": method,
        "method_label": METHODS[method],
        "method_dir": str(method_dir),
        "original_size_bytes": original_size,
        "compressed_size_bytes": None,
        "compression_ratio": None,
        "compression_rate": None,
        "elapsed_seconds": None,
        "status": "not_started",
        "note": "",
        "output_files": [],
        "commands": [],
    }


def finish_result(
    result: dict[str, Any],
    status: str,
    output_files: list[Path],
    elapsed_seconds: float | None,
    note: str = "",
) -> dict[str, Any]:
    result["status"] = status
    result["output_files"] = [str(p) for p in output_files]
    result["elapsed_seconds"] = elapsed_seconds
    if note:
        result["note"] = note
    if status == "ok":
        compressed_size = file_size_sum(output_files)
        result["compressed_size_bytes"] = compressed_size
        original_size = int(result["original_size_bytes"])
        if compressed_size <= 0:
            result["status"] = "error"
            result["note"] = (note + " " if note else "") + "Output files have zero compressed size."
            return result
        if compressed_size > 0 and original_size > 0:
            result["compression_ratio"] = original_size / compressed_size
            result["compression_rate"] = compressed_size / original_size
    return result


def failed_result(result: dict[str, Any], commands: list[dict[str, Any]], note: str) -> dict[str, Any]:
    elapsed = sum(float(cmd.get("elapsed_seconds") or 0.0) for cmd in commands)
    result["commands"].extend(commands)
    return finish_result(result, "error", [], elapsed, note)


def skipped_result(result: dict[str, Any], note: str) -> dict[str, Any]:
    return finish_result(result, "skipped", [], 0.0, note)


def ensure_wrapper(name: str) -> Path | None:
    path = BIN_DIR / name
    return path if path.exists() else None


def ensure_msconvert_file(
    input_path: Path,
    output_dir: Path,
    extensions: set[str],
    cmd: list[str],
    timeout_seconds: int,
    log_prefix: Path,
) -> tuple[Path | None, dict[str, Any] | None]:
    existing = find_first_file(output_dir, extensions) if output_dir.exists() else None
    if existing:
        return existing, None

    output_dir.mkdir(parents=True, exist_ok=True)
    command = run_command(cmd, None, timeout_seconds, log_prefix)
    if command["returncode"] != 0:
        return None, command
    converted = find_first_file(output_dir, extensions)
    return converted, command


def run_msconvert(input_path: Path, method: str, method_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = base_result(input_path, method, method_dir)
    wrapper = ensure_wrapper("msconvert.cmd")
    if not wrapper:
        return skipped_result(result, "msconvert.cmd not found.")
    method_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(wrapper), str(input_path), "--mzML"]
    if method == "msconvert_zlib":
        cmd.append("--zlib")
    elif method == "msconvert_gzip":
        cmd.append("--gzip")
    elif method == "msconvert_numpress":
        cmd.extend(["--zlib", "--numpressAll"])
    else:
        return skipped_result(result, f"Unsupported msconvert method: {method}")
    cmd.extend(["-o", str(method_dir)])
    command = run_command(cmd, None, args.timeout_seconds, method_dir / "msconvert")
    result["commands"].append(command)
    files = dir_files(method_dir)
    if command["returncode"] != 0:
        return failed_result(result, [command], "msconvert returned a non-zero exit code.")
    if not files:
        return failed_result(result, [command], "msconvert did not produce output files.")
    return finish_result(result, "ok", files, command["elapsed_seconds"])


def run_mspack(input_path: Path, method: str, method_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = base_result(input_path, method, method_dir)
    wrapper = ensure_wrapper("mspack.cmd")
    msconvert = ensure_wrapper("msconvert.cmd")
    if not wrapper:
        return skipped_result(result, "mspack.cmd not found.")
    ext = input_path.suffix.lower()
    if ext not in {".mzml", ".mzxml"}:
        return skipped_result(result, "mspack wrapper supports mzML and mzXML inputs only.")
    method_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    source = input_path
    note = ""
    if ext == ".mzml":
        if not msconvert:
            return skipped_result(result, "msconvert.cmd is required to normalize mzML before mspack.")
        converted_dir = method_dir / "mspack_uncompressed_mzml"
        converted_cmd = [
            str(msconvert),
            str(input_path),
            "--mzML",
            "--noindex",
            "--mz64",
            "--inten32",
            "-o",
            str(converted_dir),
        ]
        converted, convert_result = ensure_msconvert_file(
            input_path,
            converted_dir,
            {".mzml"},
            converted_cmd,
            args.timeout_seconds,
            method_dir / "mspack_msconvert",
        )
        if convert_result is not None:
            commands.append(convert_result)
        if converted is None:
            return failed_result(result, commands, "mzML normalization failed before mspack.")
        source = converted
        note = (
            "Elapsed time includes msconvert normalization to uncompressed, non-indexed mzML with "
            "64-bit m/z, 32-bit intensity, and 64-bit rt; mspack cannot read zlib-compressed binary arrays."
        )
    output = method_dir / f"{safe_name(input_path.stem)}.mgz"
    flag = "--mzmle" if ext == ".mzml" else "--mzxmle"
    cmd = [str(wrapper), flag, str(source), str(output)]
    command = run_command(cmd, None, args.timeout_seconds, method_dir / "mspack")
    commands.append(command)
    if command["returncode"] != 0:
        return failed_result(result, commands, "mspack returned a non-zero exit code.")
    if not output.exists():
        return failed_result(result, commands, "mspack did not produce the expected .mgz file.")
    elapsed = sum(float(cmd_result.get("elapsed_seconds") or 0.0) for cmd_result in commands)
    return finish_result(result, "ok", [output], elapsed, note)


def find_first_file(path: Path, extensions: set[str]) -> Path | None:
    matches = [p for p in dir_files(path) if p.suffix.lower() in extensions]
    return matches[0] if matches else None


def run_masscomp(input_path: Path, method: str, method_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = base_result(input_path, method, method_dir)
    masscomp = ensure_wrapper("masscomp.cmd")
    msconvert = ensure_wrapper("msconvert.cmd")
    if not masscomp:
        return skipped_result(result, "masscomp.cmd not found.")
    ext = input_path.suffix.lower()
    if ext not in {".mzml", ".mzxml"}:
        return skipped_result(result, "MassComp path supports mzML via conversion or direct mzXML only.")
    method_dir.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    source = input_path
    note = ""
    if ext == ".mzml":
        if not msconvert:
            return skipped_result(result, "msconvert.cmd is required to convert mzML to mzXML before MassComp.")
        converted_dir = method_dir / "converted_mzxml"
        convert_cmd = [
            str(msconvert),
            str(input_path),
            "--mzXML",
            "--mz64",
            "--inten32",
            "-o",
            str(converted_dir),
        ]
        converted, convert_result = ensure_msconvert_file(
            input_path,
            converted_dir,
            {".mzxml"},
            convert_cmd,
            args.timeout_seconds,
            method_dir / "masscomp_msconvert",
        )
        if convert_result is not None:
            commands.append(convert_result)
        if not converted:
            return failed_result(result, commands, "msconvert did not produce an mzXML file for MassComp.")
        source = converted
        note = (
            "Elapsed time includes mzML to mzXML conversion with 64-bit m/z, 32-bit intensity, and 64-bit rt. "
            "masscomp.cmd uses the 64-bit-safe MassComp build when available, so >2GB mzXML files do not trigger "
            "the original tinyxml2 file-length overflow."
        )
    output = method_dir / f"{safe_name(input_path.stem)}.masscomp"
    comp_cmd = [str(masscomp), "-c", str(source), str(output)]
    comp_result = run_command(comp_cmd, None, args.timeout_seconds, method_dir / "masscomp")
    commands.append(comp_result)
    result["commands"].extend(commands)
    if comp_result["returncode"] != 0:
        return failed_result(result, commands, "MassComp returned a non-zero exit code.")
    if not output.exists():
        return failed_result(result, commands, "MassComp did not produce the expected .masscomp file.")
    elapsed = sum(float(cmd.get("elapsed_seconds") or 0.0) for cmd in commands)
    return finish_result(result, "ok", [output], elapsed, note)


def load_aird_configs() -> set[str]:
    if not AIRDPRO_CONFIG_JSON.exists():
        return set()
    try:
        data = json.loads(AIRDPRO_CONFIG_JSON.read_text(encoding="utf-8"))
    except Exception:
        return set()
    return set(data.keys()) if isinstance(data, dict) else set()


def run_airdpro_like(input_path: Path, method: str, method_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = base_result(input_path, method, method_dir)
    wrapper_name = {
        "airdpro": "airdpro-compress.cmd",
        "zdpd": "zdpd.cmd",
        "stackzdpd": "stackzdpd.cmd",
    }[method]
    wrapper = ensure_wrapper(wrapper_name)
    if not wrapper:
        return skipped_result(result, f"{wrapper_name} not found.")
    config = {
        "airdpro": args.airdpro_config,
        "zdpd": args.airdpro_zdpd_config,
        "stackzdpd": args.airdpro_stackzdpd_config,
    }[method]
    known_configs = load_aird_configs()
    note_parts = []
    if known_configs and config not in known_configs:
        note_parts.append(
            f"AirdPro config '{config}' is not present in {AIRDPRO_CONFIG_JSON}; command still attempted."
        )
    if method in {"zdpd", "stackzdpd"}:
        note_parts.append(
            f"{wrapper_name} calls airdpro-compress.cmd; config used: {config}."
        )
    method_dir.mkdir(parents=True, exist_ok=True)
    cmd = [str(wrapper), str(input_path), str(method_dir), config]
    if args.airdpro_acquisition:
        cmd.append(args.airdpro_acquisition)
    elif method == "stackzdpd" and args.airdpro_stackzdpd_extra:
        cmd.append("Auto")
    if method == "stackzdpd" and args.airdpro_stackzdpd_extra:
        cmd.append("")
        cmd.append(args.airdpro_stackzdpd_extra)
    timeout_seconds = args.airdpro_timeout_seconds if args.airdpro_timeout_seconds > 0 else args.timeout_seconds
    command = run_command(cmd, None, timeout_seconds, method_dir / method)
    result["commands"].append(command)
    files = dir_files(method_dir)
    log_files = {Path(command["stdout_log"]).resolve(), Path(command["stderr_log"]).resolve()}
    output_files = [p for p in files if p.resolve() not in log_files]
    if command["returncode"] != 0:
        if command.get("timed_out") and output_files:
            note_parts.append(
                "Wrapper timed out after output files were present; measured compressed size from existing output files."
            )
            started = float(command.get("started_at_epoch") or 0.0)
            if started > 0:
                latest_output_mtime = max(p.stat().st_mtime for p in output_files)
                observed_elapsed = max(0.0, latest_output_mtime - started)
                command_elapsed = float(command.get("elapsed_seconds") or observed_elapsed)
                elapsed = min(command_elapsed, observed_elapsed) if observed_elapsed > 0 else command_elapsed
            else:
                elapsed = command.get("elapsed_seconds")
            return finish_result(result, "ok", output_files, elapsed, " ".join(note_parts))
        return failed_result(result, [command], "AirdPro wrapper returned a non-zero exit code.")
    if not output_files:
        return failed_result(result, [command], "AirdPro wrapper did not produce output files.")
    return finish_result(result, "ok", output_files, command["elapsed_seconds"], " ".join(note_parts))


def run_trackcodec(input_path: Path, method: str, method_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    result = base_result(input_path, method, method_dir)
    if input_path.suffix.lower() != ".mzml":
        return skipped_result(result, "TrackCodec compression CLI expects mzML input.")
    if not TRACKCODEC_ROOT.exists():
        return skipped_result(result, f"TrackCodec root not found: {TRACKCODEC_ROOT}")
    trackcodec_python = Path(args.trackcodec_python)
    if not trackcodec_python.exists():
        conda = shutil.which("conda")
        if not conda:
            return skipped_result(
                result,
                f"TrackCodec Python not found: {trackcodec_python}; conda fallback is unavailable.",
            )
        python_cmd = [conda, "run", "-n", "trackcodec-py311", "python"]
    else:
        python_cmd = [str(trackcodec_python)]
    method_dir.mkdir(parents=True, exist_ok=True)
    output = method_dir / f"{safe_name(input_path.stem)}.tcarchive"
    stats_json = method_dir / f"{safe_name(input_path.stem)}.stats.json"
    cache_dir = method_dir / "cache"
    cmd = [
        *python_cmd,
        "-m",
        "production.cli.compress_mzml",
        "--input",
        str(input_path),
        "--output",
        str(output),
        "--stats-json",
        str(stats_json),
        "--section-workers",
        str(args.trackcodec_section_workers),
        "--ms2-segment-workers",
        str(args.trackcodec_ms2_segment_workers),
        "--ms1-island-workers",
        str(args.trackcodec_ms1_island_workers),
        "--parallel-ms1-ms2-processes",
        "false",
        "--cache-dir",
        str(cache_dir),
    ]
    command = run_command(cmd, TRACKCODEC_ROOT, args.timeout_seconds, method_dir / "trackcodec")
    result["commands"].append(command)
    if command["returncode"] != 0:
        return failed_result(result, [command], "TrackCodec returned a non-zero exit code.")
    if not output.exists():
        return failed_result(result, [command], "TrackCodec did not produce the expected .tcarchive file.")
    note = f"Stats JSON: {stats_json}" if stats_json.exists() else ""
    return finish_result(result, "ok", [output], command["elapsed_seconds"], note)


def run_method(input_path: Path, method: str, output_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    file_dir = output_root / input_id(input_path)
    method_dir = file_dir / method
    if method.startswith("msconvert_"):
        return run_msconvert(input_path, method, method_dir, args)
    if method == "mspack":
        return run_mspack(input_path, method, method_dir, args)
    if method == "masscomp":
        return run_masscomp(input_path, method, method_dir, args)
    if method in {"airdpro", "zdpd", "stackzdpd"}:
        return run_airdpro_like(input_path, method, method_dir, args)
    if method == "trackcodec":
        return run_trackcodec(input_path, method, method_dir, args)
    return skipped_result(base_result(input_path, method, method_dir), f"Unknown method: {method}")


def rows_for_csv(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in results:
        rows.append(
            {
                "file_name": item["file_name"],
                "input_path": item["input_path"],
                "method": item["method"],
                "method_label": item["method_label"],
                "status": item["status"],
                "original_size_bytes": item["original_size_bytes"],
                "compressed_size_bytes": item["compressed_size_bytes"],
                "compression_ratio": item["compression_ratio"],
                "compression_rate": item["compression_rate"],
                "elapsed_seconds": item["elapsed_seconds"],
                "note": item["note"],
                "method_dir": item["method_dir"],
                "output_files": "; ".join(item["output_files"]),
            }
        )
    return rows


def write_csv(results: list[dict[str, Any]], csv_path: Path) -> None:
    rows = rows_for_csv(results)
    if not rows:
        return
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def status_class(status: str) -> str:
    return {
        "ok": "ok",
        "error": "error",
        "skipped": "skipped",
    }.get(status, "other")


def clipped_text(value: str, limit: int = 1200) -> str:
    value = value.strip()
    if len(value) <= limit:
        return value
    return value[-limit:]


def diagnostic_notes() -> list[dict[str, str]]:
    return [
        {
            "topic": "StackZDPD mz6 Int32 overflow",
            "scope": "stackzdpd error rows on high m/z files",
            "reason": (
                "AirdPro 5.3.1 StackZDPD scales m/z by mzPrecision and stores the result in Int32. "
                "At mzPrecision=1000000, the largest representable m/z is 2147.483647. "
                "Files containing points above that range overflow in StackComp.compress via Convert.ToInt32(Double)."
            ),
            "resolution": (
                "Current StackZDPD format cannot preserve mz6 for these files with a local one-line fix. "
                "Compatible benchmark choices are to mark StackZDPD mz6 as unsupported for those files or add a separate "
                "StackZDPD_mz5 fallback. A true mz6 fix requires an Int64 stack m/z stream plus decoder/metadata changes."
            ),
        },
        {
            "topic": "TrackCodec interrupted during MS1 representation",
            "scope": "historical TrackCodec failures on QC_E4804 R1, QC_E4804 R2, QC_E4805 DIA R1",
            "reason": (
                "The failed logs show KeyboardInterrupt inside MS1 track representation reference selection and byte "
                "estimation. Successful large QC runs also reported ms1_internal_ms1_native_representation_used=0, "
                "showing that Windows native speedups were not loaded and the job fell back to the slow Python path."
            ),
            "resolution": (
                "TrackCodec production/ms1 now bootstraps Windows DLL search directories before importing "
                "_cross_scan_speedups.cp311-win_amd64.pyd. Native status is expected to be cross_scan=true, "
                "track_link=true, islands=true; reruns should use native MS1 representation instead of the Python fallback."
            ),
        },
        {
            "topic": "mspack read failures",
            "scope": "mspack error rows after msconvert normalization",
            "reason": (
                "mspack stderr reports 'Error while reading' the msconvert-normalized uncompressed mzML. "
                "The normalization step completed, so this is treated as an mspack reader/format limitation on those mzML files."
            ),
            "resolution": (
                "No local data-changing workaround was applied. Rows remain failed with the normalized mzML path and log excerpt "
                "recorded in the report."
            ),
        },
    ]


def failure_reason_and_excerpt(item: dict[str, Any]) -> tuple[str, str]:
    command_text = "\n".join(
        "\n".join(
            part
            for part in (cmd.get("stderr_tail") or "", cmd.get("stdout_tail") or "")
            if part
        )
        for cmd in item.get("commands", [])
    )
    note = item.get("note") or ""
    method = item.get("method") or ""
    reason = note
    excerpt = clipped_text(command_text)

    error_read = re.search(r"Error while reading[^\r\n]*", command_text)
    overflow = "OverflowException" in command_text or "Convert.ToInt32" in command_text
    timeout = re.search(r"Timed out after\s+\d+\s+seconds", command_text)

    if method == "mspack" and error_read:
        reason = "mspack read failed on msconvert-normalized uncompressed mzML"
        excerpt = error_read.group(0)
    elif method == "stackzdpd" and overflow:
        reason = "AirdPro 5.3.1 StackZDPD mz6 Int32 overflow in StackComp.compress"
        excerpt = (
            "System.OverflowException: Convert.ToInt32(Double) in StackComp.compress. "
            "At mzPrecision=1000000, Int32 max m/z is 2147.483647; higher m/z needs mz5 fallback or Int64 format changes."
        )
    elif method == "trackcodec" and timeout:
        reason = "TrackCodec reached per-method timeout"
        excerpt = timeout.group(0)
    elif method == "trackcodec" and "KeyboardInterrupt" in command_text:
        reason = "TrackCodec interrupted during slow Python MS1 representation path"
        excerpt = (
            "KeyboardInterrupt inside MS1 representation/reference selection. "
            "Native speedups were not loaded in the historical run; Windows DLL bootstrap has been fixed before rerun."
        )
    elif method == "trackcodec":
        reason = "TrackCodec returned a non-zero exit code"

    return reason, excerpt


def generate_html(
    inputs: list[Path],
    methods: list[str],
    results: list[dict[str, Any]],
    output_root: Path,
    html_path: Path,
    json_path: Path,
    csv_path: Path,
    args: argparse.Namespace,
) -> None:
    by_key = {(item["input_path"], item["method"]): item for item in results}
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    method_header = "\n".join(
        f"<th colspan=\"4\">{html.escape(METHODS[m])}</th>" for m in methods
    )
    method_subheader = "\n".join(
        "<th>compressed size</th><th>ratio</th><th>rate</th><th>time/status</th>" for _ in methods
    )
    rows = []
    for input_path in inputs:
        original_size = input_path.stat().st_size
        cells = [
            f"<td><code>{html.escape(input_path.name)}</code><br><span class=\"path\">{html.escape(str(input_path))}</span></td>",
            f"<td data-sort=\"{original_size}\">{fmt_bytes(original_size)}<br><span class=\"bytes\">{original_size:,} bytes</span></td>",
        ]
        for method in methods:
            item = by_key.get((str(input_path), method))
            if not item:
                cells.extend(["<td></td>", "<td></td>", "<td></td>", "<td></td>"])
                continue
            size = item["compressed_size_bytes"]
            ratio = item["compression_ratio"]
            rate = item["compression_rate"]
            status = item["status"]
            elapsed = item["elapsed_seconds"]
            cells.append(
                f"<td data-sort=\"{size or 0}\">{fmt_bytes(size)}"
                + (f"<br><span class=\"bytes\">{int(size):,} bytes</span>" if size else "")
                + "</td>"
            )
            cells.append(f"<td>{fmt_num(ratio)}</td>")
            cells.append(f"<td>{fmt_num(rate)}</td>")
            cells.append(
                f"<td><span class=\"status {status_class(status)}\">{html.escape(status)}</span>"
                f"<br>{fmt_seconds(elapsed)}</td>"
            )
        rows.append("<tr>" + "\n".join(cells) + "</tr>")

    failure_items = [item for item in results if item.get("status") == "error"]
    failure_rows = []
    for item in failure_items:
        reason, excerpt = failure_reason_and_excerpt(item)
        failure_rows.append(
            "<tr>"
            f"<td><code>{html.escape(item['file_name'])}</code></td>"
            f"<td><code>{html.escape(item['method'])}</code></td>"
            f"<td>{fmt_seconds(item.get('elapsed_seconds'))}</td>"
            f"<td>{html.escape(reason)}</td>"
            f"<td><pre><code>{html.escape(excerpt)}</code></pre></td>"
            f"<td><code>{html.escape(item.get('method_dir') or '')}</code></td>"
            "</tr>"
        )
    if failure_rows:
        failure_summary = (
            "<h2>Failure Summary</h2>"
            "<p class=\"meta\">Failure rows are summarized here; full stdout/stderr tails remain in the Details section below.</p>"
            "<div class=\"table-wrap\"><table>"
            "<thead><tr><th>file name</th><th>method</th><th>time</th><th>reason</th><th>log excerpt</th><th>method dir</th></tr></thead>"
            f"<tbody>{''.join(failure_rows)}</tbody>"
            "</table></div>"
        )
    else:
        failure_summary = (
            "<h2>Failure Summary</h2>"
            "<p class=\"meta\">No failed benchmark rows.</p>"
        )

    diagnostic_rows = []
    for note in diagnostic_notes():
        diagnostic_rows.append(
            "<tr>"
            f"<td>{html.escape(note['topic'])}</td>"
            f"<td>{html.escape(note['scope'])}</td>"
            f"<td>{html.escape(note['reason'])}</td>"
            f"<td>{html.escape(note['resolution'])}</td>"
            "</tr>"
        )
    diagnostic_summary = (
        "<h2>Diagnostic Notes</h2>"
        "<div class=\"table-wrap\"><table>"
        "<thead><tr><th>topic</th><th>scope</th><th>reason</th><th>resolution</th></tr></thead>"
        f"<tbody>{''.join(diagnostic_rows)}</tbody>"
        "</table></div>"
    )

    detail_blocks = []
    for item in results:
        commands = item.get("commands", [])
        command_texts = "".join(
            "<li>"
            f"<code>{html.escape(cmd.get('command', ''))}</code>"
            f"<br>cwd: <code>{html.escape(cmd.get('cwd') or '')}</code>"
            f"<br>returncode: {html.escape(str(cmd.get('returncode')))}; "
            f"elapsed: {fmt_seconds(cmd.get('elapsed_seconds'))}"
            "</li>"
            for cmd in commands
        )
        logs = []
        for idx, cmd in enumerate(commands, start=1):
            stdout_tail = cmd.get("stdout_tail") or ""
            stderr_tail = cmd.get("stderr_tail") or ""
            if stdout_tail:
                logs.append(
                    f"<h4>command {idx} stdout tail</h4><pre><code>{html.escape(stdout_tail)}</code></pre>"
                )
            if stderr_tail:
                logs.append(
                    f"<h4>command {idx} stderr tail</h4><pre><code>{html.escape(stderr_tail)}</code></pre>"
                )
        outputs = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in item.get("output_files", []))
        detail_blocks.append(
            "<details>"
            f"<summary><code>{html.escape(item['file_name'])}</code> - "
            f"{html.escape(item['method_label'])}: "
            f"<span class=\"status {status_class(item['status'])}\">{html.escape(item['status'])}</span>"
            "</summary>"
            f"<p>{html.escape(item.get('note') or '')}</p>"
            "<h4>commands</h4>"
            f"<ul>{command_texts}</ul>"
            "<h4>outputs</h4>"
            f"<ul>{outputs}</ul>"
            + "".join(logs)
            + "</details>"
        )

    method_notes = [
        "<li><code>msconvert_zlib</code>: <code>msconvert input --mzML --zlib -o out</code>.</li>",
        "<li><code>msconvert_gzip</code>: <code>msconvert input --mzML --gzip -o out</code>.</li>",
        "<li><code>msconvert_numpress</code>: <code>msconvert input --mzML --zlib --numpressAll -o out</code>.</li>",
        "<li><code>mspack</code>: writes one <code>.mgz</code> file.</li>",
        "<li><code>masscomp</code>: supports mzXML; mzML inputs are first converted with <code>msconvert --mzXML --64</code>. Time includes that conversion. The wrapper uses the local 64-bit-safe MassComp build when available.</li>",
        "<li><code>airdpro</code>: calls <code>airdpro-compress.cmd</code> with AirdPro 6.0.3 Default from the local <code>AirdPro-6.0.3-mz6-keepzero</code> copy; this Default keeps zero-intensity points and uses <code>mzPrecision=1000000</code>.</li>",
        "<li><code>zdpd</code> and <code>stackzdpd</code>: call <code>airdpro-compress.cmd</code> after pinning <code>AIRDPRO_EXE</code> to the local AirdPro 5.3.1 stack-tail fixed build. Their default configs are <code>ZDPD</code> and <code>StackZDPD</code>; <code>stackzdpd</code> defaults to <code>--digit 8</code>. The fixed build keeps full 256-layer StackZDPD groups and handles incomplete final groups with a safe full-sort encoder.</li>",
        "<li><code>trackcodec</code>: calls <code>python -m production.cli.compress_mzml</code> in the TrackCodec source tree.</li>",
    ]
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mass-spec Compression Benchmark</title>
  <style>
    body {{ margin: 0; background: #f6f8fb; color: #1f2933; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; line-height: 1.55; }}
    main {{ max-width: 1600px; margin: 0 auto; padding: 32px 22px 64px; }}
    h1 {{ margin: 0 0 10px; font-size: 30px; }}
    h2 {{ margin-top: 34px; padding-top: 18px; border-top: 1px solid #d9e1ec; font-size: 22px; }}
    h4 {{ margin: 16px 0 6px; }}
    .panel {{ background: #fff; border: 1px solid #d9e1ec; border-radius: 8px; padding: 24px; }}
    .meta {{ color: #526071; }}
    .path, .bytes {{ color: #617083; font-size: 12px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 14px; font-size: 13px; }}
    th, td {{ border: 1px solid #d9e1ec; padding: 8px 9px; text-align: left; vertical-align: top; }}
    th {{ background: #edf2f7; font-weight: 650; }}
    td:first-child {{ min-width: 260px; }}
    code, pre {{ font-family: Consolas, "Courier New", monospace; }}
    code {{ background: #f0f3f7; border: 1px solid #e1e7ef; border-radius: 4px; padding: 1px 4px; }}
    pre {{ overflow-x: auto; background: #101820; color: #e9eef5; padding: 12px 14px; border-radius: 6px; }}
    pre code {{ background: transparent; border: 0; padding: 0; color: inherit; }}
    .table-wrap {{ overflow-x: auto; }}
    .status {{ display: inline-block; border-radius: 4px; padding: 1px 6px; font-weight: 650; }}
    .ok {{ background: #e6f4ea; color: #137333; }}
    .error {{ background: #fce8e6; color: #a50e0e; }}
    .skipped {{ background: #f1f3f4; color: #5f6368; }}
    details {{ border: 1px solid #d9e1ec; border-radius: 8px; padding: 10px 14px; margin: 10px 0; background: #fff; }}
    summary {{ cursor: pointer; font-weight: 650; }}
    ul {{ margin: 8px 0 14px; }}
  </style>
</head>
<body>
<main>
  <section class="panel">
    <h1>Mass-spec Compression Benchmark</h1>
    <p class="meta">Generated: {html.escape(now)}</p>
    <p class="meta">Output root: <code>{html.escape(str(output_root))}</code></p>
    <p class="meta">JSON: <code>{html.escape(str(json_path))}</code> | CSV: <code>{html.escape(str(csv_path))}</code></p>
    <p class="meta">AirdPro configs: airdpro=<code>{html.escape(args.airdpro_config)}</code>,
       zdpd=<code>{html.escape(args.airdpro_zdpd_config)}</code>,
       stackzdpd=<code>{html.escape(args.airdpro_stackzdpd_config)}</code></p>
  </section>

  <h2>Summary</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th rowspan="2">file name</th>
          <th rowspan="2">original size</th>
          {method_header}
        </tr>
        <tr>
          {method_subheader}
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </div>

  {diagnostic_summary}

  {failure_summary}

  <h2>Method Notes</h2>
  <ul>
    {''.join(method_notes)}
  </ul>

  <h2>Details</h2>
  {''.join(detail_blocks)}
</main>
</body>
</html>
"""
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(html_text, encoding="utf-8")


def write_json(results: list[dict[str, Any]], json_path: Path, inputs: list[Path], methods: list[str]) -> None:
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "inputs": [str(p) for p in inputs],
        "methods": methods,
        "diagnostic_notes": diagnostic_notes(),
        "results": results,
    }
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_resume_results(json_path: Path, inputs: list[Path], methods: list[str]) -> list[dict[str, Any]]:
    if not json_path.exists():
        return []
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    allowed_inputs = {str(p) for p in inputs}
    allowed_methods = set(methods)
    loaded: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in payload.get("results", []):
        key = (item.get("input_path"), item.get("method"))
        if key in seen:
            continue
        if key[0] in allowed_inputs and key[1] in allowed_methods:
            loaded.append(item)
            seen.add(key)
    return loaded


def write_reports(
    results: list[dict[str, Any]],
    inputs: list[Path],
    methods: list[str],
    output_root: Path,
    html_path: Path,
    json_path: Path,
    csv_path: Path,
    args: argparse.Namespace,
) -> None:
    write_json(results, json_path, inputs, methods)
    write_csv(results, csv_path)
    generate_html(inputs, methods, results, output_root, html_path, json_path, csv_path, args)


def run_one_method_for_report(
    input_path: Path,
    method: str,
    output_root: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    try:
        return run_method(input_path, method, output_root, args)
    except Exception as exc:  # Keep a report even if one method crashes.
        method_dir = output_root / input_id(input_path) / method
        result = base_result(input_path, method, method_dir)
        return finish_result(result, "error", [], 0.0, f"Unhandled exception: {exc}")


def result_sort_key(item: dict[str, Any], input_order: dict[str, int], method_order: dict[str, int]) -> tuple[int, int]:
    return (
        input_order.get(item.get("input_path"), len(input_order)),
        method_order.get(item.get("method"), len(method_order)),
    )


def main() -> int:
    args = parse_args()
    inputs = discover_files(args)
    if not inputs:
        print("No input files discovered.", file=sys.stderr)
        return 2
    methods = selected_methods(args)
    output_root = make_output_root(args)
    html_path = Path(args.html) if args.html else output_root / "summary.html"
    json_path = Path(args.json) if args.json else output_root / "summary.json"
    csv_path = Path(args.csv) if args.csv else output_root / "summary.csv"

    print(f"[benchmark] inputs={len(inputs)} methods={len(methods)}")
    print(f"[benchmark] workers={max(1, args.workers)}")
    print(f"[benchmark] output_root={output_root}")
    results: list[dict[str, Any]] = load_resume_results(json_path, inputs, methods) if args.resume else []
    existing_keys = {(item["input_path"], item["method"]) for item in results}
    input_order = {str(path): idx for idx, path in enumerate(inputs)}
    method_order = {method: idx for idx, method in enumerate(methods)}
    if results:
        print(f"[benchmark] resumed_results={len(results)}")
        results.sort(key=lambda item: result_sort_key(item, input_order, method_order))
        write_reports(results, inputs, methods, output_root, html_path, json_path, csv_path, args)

    workers = max(1, args.workers)
    if workers == 1:
        for input_path in inputs:
            print(f"[benchmark] file={input_path}")
            for method in methods:
                key = (str(input_path), method)
                if key in existing_keys:
                    print(f"[benchmark] method={method} reused from existing JSON")
                    continue
                print(f"[benchmark] method={method} ({METHODS[method]})")
                result = run_one_method_for_report(input_path, method, output_root, args)
                results.append(result)
                existing_keys.add(key)
                print(
                    "[benchmark] "
                    f"status={result['status']} "
                    f"size={result['compressed_size_bytes']} "
                    f"ratio={result['compression_ratio']} "
                    f"time={result['elapsed_seconds']}"
                )
                results.sort(key=lambda item: result_sort_key(item, input_order, method_order))
                write_reports(results, inputs, methods, output_root, html_path, json_path, csv_path, args)
                if args.fail_fast and result["status"] == "error":
                    print("[benchmark] fail-fast triggered.", file=sys.stderr)
                    return 1
    else:
        report_lock = threading.Lock()

        def run_file(input_path: Path) -> bool:
            file_ok = True
            print(f"[benchmark] file={input_path}")
            for method in methods:
                key = (str(input_path), method)
                with report_lock:
                    if key in existing_keys:
                        print(f"[benchmark] method={method} reused from existing JSON")
                        continue
                    existing_keys.add(key)
                print(f"[benchmark] file={input_path.name} method={method} ({METHODS[method]})")
                result = run_one_method_for_report(input_path, method, output_root, args)
                print(
                    "[benchmark] "
                    f"file={input_path.name} "
                    f"method={method} "
                    f"status={result['status']} "
                    f"size={result['compressed_size_bytes']} "
                    f"ratio={result['compression_ratio']} "
                    f"time={result['elapsed_seconds']}"
                )
                with report_lock:
                    results.append(result)
                    results.sort(key=lambda item: result_sort_key(item, input_order, method_order))
                    write_reports(results, inputs, methods, output_root, html_path, json_path, csv_path, args)
                if result["status"] == "error":
                    file_ok = False
                    if args.fail_fast:
                        return False
            return file_ok

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {executor.submit(run_file, input_path): input_path for input_path in inputs}
            for future in concurrent.futures.as_completed(future_map):
                input_path = future_map[future]
                try:
                    ok = future.result()
                except Exception as exc:
                    print(f"[benchmark] file={input_path} worker exception: {exc}", file=sys.stderr)
                    ok = False
                if args.fail_fast and not ok:
                    print("[benchmark] fail-fast triggered.", file=sys.stderr)
                    return 1
    print(f"[benchmark] html={html_path}")
    print(f"[benchmark] json={json_path}")
    print(f"[benchmark] csv={csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
