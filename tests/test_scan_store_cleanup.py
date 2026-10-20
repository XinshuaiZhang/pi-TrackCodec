import json

import numpy as np

from TrackCodec.production.common.scan_store import (
    MemmapScanStore,
    SCAN_STORE_SCHEMA_VERSION,
    _scan_store_manifest_complete,
)


def test_legacy_scan_store_cache_is_invalidated(tmp_path):
    assert not _scan_store_manifest_complete(
        tmp_path,
        {"scan_store_schema_version": SCAN_STORE_SCHEMA_VERSION - 1},
    )
    assert not _scan_store_manifest_complete(tmp_path, {})


def test_close_releases_scan_store_mappings(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    (root / "mz_data.bin").write_bytes(np.array([100.0, 101.0], dtype=np.float64).tobytes())
    (root / "intensity_data.bin").write_bytes(np.array([1.0, 2.0], dtype=np.float64).tobytes())

    arrays = {
        "ms_level.npy": np.array([1], dtype=np.uint8),
        "local_scan_idx.npy": np.array([0], dtype=np.uint32),
        "rt.npy": np.array([1.0], dtype=np.float64),
        "offset.npy": np.array([0], dtype=np.uint64),
        "length.npy": np.array([2], dtype=np.uint32),
        "target_mz.npy": np.array([0.0], dtype=np.float64),
        "window_target.npy": np.array([0.0], dtype=np.float64),
        "window_lower.npy": np.array([0.0], dtype=np.float64),
        "window_upper.npy": np.array([0.0], dtype=np.float64),
        "ms1_indices.npy": np.array([0], dtype=np.uint32),
        "ms2_indices.npy": np.array([], dtype=np.uint32),
    }
    for name, array in arrays.items():
        np.save(root / name, array)

    manifest = {
        "file_type": "DDA",
        "total_points": 2,
        "raw_ms1_bytes": 32,
        "raw_ms2_bytes": 0,
        "mz_data_file": "mz_data.bin",
        "intensity_data_file": "intensity_data.bin",
        "ms_level_file": "ms_level.npy",
        "local_scan_idx_file": "local_scan_idx.npy",
        "rt_file": "rt.npy",
        "offset_file": "offset.npy",
        "length_file": "length.npy",
        "target_mz_file": "target_mz.npy",
        "window_target_file": "window_target.npy",
        "window_lower_file": "window_lower.npy",
        "window_upper_file": "window_upper.npy",
        "ms1_indices_file": "ms1_indices.npy",
        "ms2_indices_file": "ms2_indices.npy",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    store = MemmapScanStore(root)
    assert isinstance(store.intensity_data, np.memmap)
    store.close()

    assert store.intensity_data is None
    assert store.ms1_global_indices is None
    for path in root.iterdir():
        path.unlink()

