"""MS1 native extension bootstrap."""

from __future__ import annotations

import os
from pathlib import Path
import sys

_DLL_DIRECTORY_HANDLES = []


def _enable_windows_native_dll_dirs() -> None:
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return

    candidates: list[Path] = []
    extra = os.environ.get("TRACKCODEC_EXTRA_DLL_DIRS", "")
    for item in extra.split(os.pathsep):
        if item:
            candidates.append(Path(item))

    prefixes = [Path(sys.prefix), Path(getattr(sys, "base_prefix", sys.prefix))]
    for prefix in list(prefixes):
        if prefix.parent.name.lower() == "envs":
            prefixes.append(prefix.parent.parent)
    for root in prefixes:
        candidates.extend([root, root / "Library" / "bin"])
        pkgs = root / "pkgs"
        if pkgs.exists():
            for pattern in ("libstdcxx-*", "libgcc-*", "libwinpthread-*", "vc14_runtime-*", "ucrt-*"):
                for package_dir in pkgs.glob(pattern):
                    candidates.append(package_dir / "Library" / "bin")

    seen = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        text = str(resolved)
        if text in seen or not resolved.is_dir():
            continue
        seen.add(text)
        try:
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(text))
        except OSError:
            continue


_enable_windows_native_dll_dirs()
