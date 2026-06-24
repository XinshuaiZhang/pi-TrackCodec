from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_CONDA_ENV = os.environ.get("TRACKCODEC_CONDA_ENV", "").strip()


def _candidate_conda_prefixes(env_name: str) -> list[Path]:
    prefixes: list[Path] = []
    for key in ("CONDA_PREFIX", "MAMBA_ROOT_PREFIX", "CONDA_ROOT"):
        value = os.environ.get(key)
        if not value:
            continue
        path = Path(value)
        if path.name == env_name:
            prefixes.append(path)
        prefixes.append(path / "envs" / env_name)
    for root in (
        Path.home() / "anaconda3",
        Path.home() / "miniconda3",
        Path("/opt/conda"),
        Path("/usr/local/conda"),
    ):
        prefixes.append(root / "envs" / env_name)
    out: list[Path] = []
    seen: set[str] = set()
    for prefix in prefixes:
        text = str(prefix)
        if text in seen:
            continue
        seen.add(text)
        out.append(prefix)
    return out


def resolve_trackcodec_python(prefer_conda_env: str | None = None) -> Path:
    """Resolve the Python executable that should run TrackCodec workloads."""
    explicit = os.environ.get("TRACKCODEC_PYTHON")
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_name = (prefer_conda_env if prefer_conda_env is not None else DEFAULT_CONDA_ENV).strip()
    if not env_name:
        return Path(sys.executable).resolve()
    for prefix in _candidate_conda_prefixes(env_name):
        candidate = prefix / "bin" / "python"
        if candidate.exists():
            return candidate.resolve()
    mamba = shutil.which("mamba")
    if mamba:
        probe = subprocess.run(
            [mamba, "run", "-n", env_name, "python", "-c", "import sys; print(sys.executable)"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip():
            return Path(probe.stdout.strip().splitlines()[-1]).resolve()
    return Path(sys.executable).resolve()


def native_speedup_status() -> dict:
    try:
        from TrackCodec.production.common import islands, tracking
        from TrackCodec.production.ms1 import cross_scan_codec
        from TrackCodec.production.ms1 import _cross_scan_speedups
    except Exception as exc:
        return {
            "available": False,
            "python_executable": sys.executable,
            "python_version": sys.version.split()[0],
            "error_type": exc.__class__.__name__,
            "error": str(exc),
        }
    module_path = Path(getattr(_cross_scan_speedups, "__file__", ""))
    return {
        "available": bool(
            getattr(cross_scan_codec, "HAVE_CROSS_SCAN_SPEEDUPS", False)
            and getattr(tracking, "HAVE_TRACK_LINK_SPEEDUPS", False)
            and getattr(islands, "HAVE_ISLAND_SPEEDUPS", False)
        ),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "module_path": str(module_path),
        "cross_scan": bool(getattr(cross_scan_codec, "HAVE_CROSS_SCAN_SPEEDUPS", False)),
        "track_link": bool(getattr(tracking, "HAVE_TRACK_LINK_SPEEDUPS", False)),
        "islands": bool(getattr(islands, "HAVE_ISLAND_SPEEDUPS", False)),
    }


def assert_native_speedups_available(*, required: bool = True) -> dict:
    status = native_speedup_status()
    if required and not bool(status.get("available", False)):
        raise RuntimeError(
            "TrackCodec native MS1 speedups are not available. "
            "Use a Python ABI that matches the compiled extension or rebuild "
            "the native extension from this source tree. "
            f"status={json.dumps(status, ensure_ascii=False)}"
        )
    return status


def assert_python_native_speedups_available(python_bin: str | os.PathLike[str], *, cwd: str | os.PathLike[str] | None = None) -> dict:
    bootstrap = ""
    env = os.environ.copy()
    if cwd is not None:
        cwd_path = Path(cwd).resolve()
        package_parent = cwd_path.parent
        env["PYTHONPATH"] = str(package_parent) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        if cwd_path.name != "TrackCodec" and (cwd_path / "__init__.py").exists():
            bootstrap = (
                "import importlib.util, pathlib, sys; "
                f"_root=pathlib.Path({str(cwd_path)!r}); "
                "_spec=importlib.util.spec_from_file_location('TrackCodec', _root/'__init__.py', submodule_search_locations=[str(_root)]); "
                "_module=importlib.util.module_from_spec(_spec); "
                "sys.modules['TrackCodec']=_module; "
                "_spec.loader.exec_module(_module); "
            )
    command = [
        str(python_bin),
        "-c",
        (
            "import json; "
            + bootstrap
            +
            "from TrackCodec.production.common.runtime_env import assert_native_speedups_available; "
            "print(json.dumps(assert_native_speedups_available(required=True)))"
        ),
    ]
    probe = subprocess.run(command, cwd=str(cwd) if cwd is not None else None, env=env, capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        raise RuntimeError(
            "TrackCodec native speedup probe failed: "
            f"python_bin={python_bin} stdout={probe.stdout.strip()} stderr={probe.stderr.strip()}"
        )
    try:
        return json.loads(probe.stdout.strip().splitlines()[-1])
    except Exception as exc:
        raise RuntimeError(f"Invalid native speedup probe output from {python_bin}: {probe.stdout!r}") from exc
