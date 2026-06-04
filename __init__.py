"""Official top-level package for the portable TrackCodec project.

This directory is intentionally self-contained: copying the ``TrackCodec/``
folder to another machine should preserve the package layout and allow the
runtime, production modules, tests, and tools to be imported without relying
on any sibling repository directories.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


_PKG_DIR = Path(__file__).resolve().parent
# Keep package resolution entirely inside this directory so the folder can be
# copied elsewhere and still work as a standalone project tree.
__path__ = [str(_PKG_DIR)]
__file__ = str(_PKG_DIR / "__init__.py")
__package__ = __name__
__doc__ = __doc__

__all__ = ["TrackCodecArchive"]


def __getattr__(name: str) -> Any:
    if name == "TrackCodecArchive":
        from TrackCodec.production.trackcodec import TrackCodecArchive

        return TrackCodecArchive
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
