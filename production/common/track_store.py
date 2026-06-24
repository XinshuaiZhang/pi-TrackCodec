"""File-backed CompactIslandTracks store for archive/whole-file workflows."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .tracking import CompactIslandTracks


def write_compact_track_store(root: str | Path, tracks: CompactIslandTracks) -> CompactIslandTracks:
    if not isinstance(tracks, CompactIslandTracks):
        raise TypeError("write_compact_track_store expects CompactIslandTracks")
    if len(tracks.islands):
        raise ValueError("write_compact_track_store only supports views-only CompactIslandTracks")

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    np.save(root / "track_lengths.npy", np.asarray(tracks.track_lengths, dtype=np.uint32))
    np.save(root / "track_offsets.npy", np.asarray(tracks.track_offsets, dtype=np.uint32))
    np.save(root / "scan_indices.npy", np.asarray(tracks.scan_indices, dtype=np.uint32))
    np.save(root / "array_start_indices.npy", np.asarray(tracks.array_start_indices, dtype=np.uint32))
    np.save(root / "n_points.npy", np.asarray(tracks.n_points, dtype=np.uint32))

    manifest = {
        "point_count": int(tracks.point_count),
        "views_only": True,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return open_compact_track_store(root)


def open_compact_track_store(root: str | Path) -> CompactIslandTracks:
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not bool(manifest.get("views_only", False)):
        raise ValueError("open_compact_track_store currently expects a views-only store")
    return CompactIslandTracks(
        track_lengths=np.load(root / "track_lengths.npy", mmap_mode="r"),
        track_offsets=np.load(root / "track_offsets.npy", mmap_mode="r"),
        islands=[],
        scan_indices=np.load(root / "scan_indices.npy", mmap_mode="r"),
        array_start_indices=np.load(root / "array_start_indices.npy", mmap_mode="r"),
        n_points=np.load(root / "n_points.npy", mmap_mode="r"),
        point_count=int(manifest["point_count"]),
    )
