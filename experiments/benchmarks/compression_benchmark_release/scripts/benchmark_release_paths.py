from __future__ import annotations

import os
from pathlib import Path


def find_release_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if (parent / "scripts").is_dir() and (parent / "README.md").exists():
            return parent
    return current


def find_workspace_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("MSCODEC_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if (parent / "benchmark").is_dir() and ((parent / "scripts").is_dir() or (parent / "bin").is_dir()):
            return parent
    return current


def find_trackcodec_root(start: Path | None = None) -> Path:
    env_root = os.environ.get("TRACKCODEC_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for parent in [current, *current.parents]:
        if (parent / "production").is_dir() and (parent / "pyproject.toml").exists():
            return parent
    return current


def outputs_root(start: Path | None = None) -> Path:
    return find_release_root(start) / "outputs"


def inputs_root(start: Path | None = None) -> Path:
    return find_release_root(start) / "inputs"


def default_run_output_root(start: Path | None = None) -> Path:
    return outputs_root(start) / "runs"


def default_combined_output_dir(start: Path | None = None) -> Path:
    return outputs_root(start) / "combined_release"
