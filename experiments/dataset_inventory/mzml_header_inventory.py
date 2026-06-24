from __future__ import annotations

import argparse
import csv
import json
import mmap
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT.parent))

from TrackCodec.production.common.datasets import FULL8_PROFILE_FILES


STACKZDPD_ROOT_ENV = "TRACKCODEC_STACKZDPD_ROOT"
DEFAULT_OUT_ROOT = Path(os.environ.get(
    "TRACKCODEC_HEADER_INVENTORY_ROOT",
    ROOT.parent / "benchmark_results" / "dataset_header_inventory",
))

SERIAL_ACCESSION = "MS:1000529"
MS_LEVEL_ACCESSION = "MS:1000511"
PROFILE_ACCESSION = "MS:1000128"
CENTROID_ACCESSION = "MS:1000127"
GENERIC_MODEL_NAMES = {
    "agilent instrument model",
}
EXCLUDED_INSTRUMENT_NAMES = {
    "instrument serial number",
    "no combination",
    "ms1 spectrum",
    "msn spectrum",
}
NUMERIC_SUMMARY_FIELDS = (
    "file_size_bytes",
    "spectrum_count",
    "parsed_spectrum_count",
    "ms1_scan_count",
    "ms2_scan_count",
    "other_ms_level_count",
    "ms1_profile_count",
    "ms1_centroid_count",
    "ms1_mixed_mode_count",
    "ms1_unknown_mode_count",
    "ms2_profile_count",
    "ms2_centroid_count",
    "ms2_mixed_mode_count",
    "ms2_unknown_mode_count",
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iter_stackzdpd_uncompressed_files() -> list[Path]:
    stackzdpd_root_value = os.environ.get(STACKZDPD_ROOT_ENV)
    if not stackzdpd_root_value:
        raise ValueError(f"{STACKZDPD_ROOT_ENV} is required when inventorying stackzdpd_validation_uncompressed.")
    stackzdpd_root = Path(stackzdpd_root_value).expanduser()
    stackzdpd_validation_uncompressed_dir = stackzdpd_root / "stackzdpd_validation_mzML" / "uncompressed"
    files = []
    for path in stackzdpd_validation_uncompressed_dir.glob("*.mzML"):
        name = path.name.lower()
        if name.endswith(".uncompressed.mzml") or name.endswith(".true_uncompressed.mzml"):
            files.append(path)
    return sorted(files)


def _dataset_map(selected_datasets: list[str] | None = None) -> dict[str, list[Path]]:
    selected = set(selected_datasets or ("full8", "stackzdpd_validation_uncompressed"))
    datasets: dict[str, list[Path]] = {}
    if "full8" in selected:
        datasets["full8"] = list(FULL8_PROFILE_FILES)
    if "stackzdpd_validation_uncompressed" in selected:
        datasets["stackzdpd_validation_uncompressed"] = _iter_stackzdpd_uncompressed_files()
    return datasets


def _human_gib(n: int) -> float:
    return float(n) / (1024 ** 3)


def _relative_group(path: Path, dataset: str) -> str:
    if dataset == "full8":
        if "PXD004732" in str(path):
            return "PXD004732_uncompressed"
        return "core_raw_data"
    stackzdpd_root_value = os.environ.get(STACKZDPD_ROOT_ENV)
    if not stackzdpd_root_value:
        return "stackzdpd_validation_uncompressed"
    rel = path.relative_to(Path(stackzdpd_root_value).expanduser())
    return rel.parts[0]


def _safe_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_model(model: str, user_model: str, source_format: str) -> str:
    model = (model or "").strip()
    user_model = (user_model or "").strip()
    if model.lower() in GENERIC_MODEL_NAMES and user_model:
        if source_format.startswith("Agilent") and not user_model.lower().startswith("agilent "):
            return f"Agilent {user_model}"
        return user_model
    if model:
        return model
    if user_model:
        if source_format.startswith("Agilent") and not user_model.lower().startswith("agilent "):
            return f"Agilent {user_model}"
        return user_model
    return ""


def _mode_summary(profile_count: int, centroid_count: int, mixed_count: int, unknown_count: int, total_count: int) -> str:
    if total_count <= 0:
        return "unknown"
    if profile_count == total_count:
        return "profile"
    if centroid_count == total_count:
        return "centroid"
    if unknown_count == total_count:
        return "unknown"
    return "mixed"


def _format_mode_counts(profile_count: int, centroid_count: int, mixed_count: int, unknown_count: int) -> str:
    return (
        f"profile={profile_count}, centroid={centroid_count}, "
        f"mixed={mixed_count}, unknown={unknown_count}"
    )


ATTR_RE = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:.]*)="([^"]*)"')
INDEX_LIST_OFFSET_RE = re.compile(br"<indexListOffset>(\d+)</indexListOffset>")
OFFSET_RE = re.compile(br">(\d+)</offset>")
IDREF_RE = re.compile(br'idRef="([^"]+)"')
MS_LEVEL_VALUE_RE = re.compile(br'name="ms level" value="(\d+)"')
WIFF_EXPERIMENT_RE = re.compile(r"experiment=(\d+)")


def _attrs(line: str) -> dict[str, str]:
    return dict(ATTR_RE.findall(line))


def _new_referenceable_group(group_id: str) -> dict:
    return {
        "id": group_id,
        "model": "",
        "user_model": "",
        "serial": "",
    }


def _new_instrument_config(config_id: str) -> dict:
    return {
        "id": config_id,
        "model": "",
        "user_model": "",
        "serial": "",
        "refs": [],
    }


def _sample_indices(n: int, max_samples: int) -> list[int]:
    if n <= 0:
        return []
    if n <= max_samples:
        return list(range(n))
    return sorted({round(i * (n - 1) / (max_samples - 1)) for i in range(max_samples)})


def _parse_header_prefix(path: Path, info: dict) -> dict[str, dict]:
    current_ref_group: dict | None = None
    current_instrument_config: dict | None = None
    ref_groups: dict[str, dict] = {}
    in_source_file = False
    in_component_list = False
    root_seen = False
    run_seen = False

    with path.open("r", encoding="utf-8", errors="replace", buffering=1024 * 1024) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("<spectrum "):
                break

            if not root_seen and line.startswith("<mzML "):
                info["mzml_id"] = _attrs(line).get("id", "")
                root_seen = True
                continue

            if line.startswith("<sourceFile "):
                in_source_file = True
                if not info["source_file_name"]:
                    info["source_file_name"] = _attrs(line).get("name", "")
                continue
            if line.startswith("</sourceFile"):
                in_source_file = False
                continue

            if line.startswith("<referenceableParamGroup "):
                current_ref_group = _new_referenceable_group(_attrs(line).get("id", ""))
                continue
            if line.startswith("</referenceableParamGroup"):
                if current_ref_group is not None:
                    ref_groups[current_ref_group["id"]] = current_ref_group
                current_ref_group = None
                continue

            if line.startswith("<instrumentConfiguration "):
                current_instrument_config = _new_instrument_config(_attrs(line).get("id", ""))
                continue
            if line.startswith("</instrumentConfiguration"):
                if current_instrument_config is not None:
                    _finalize_instrument_config(info, current_instrument_config, ref_groups)
                current_instrument_config = None
                in_component_list = False
                continue

            if line.startswith("<referenceableParamGroupRef "):
                if current_instrument_config is not None:
                    ref_id = _attrs(line).get("ref", "")
                    if ref_id:
                        current_instrument_config["refs"].append(ref_id)
                continue

            if line.startswith("<componentList "):
                in_component_list = True
                continue
            if line.startswith("</componentList"):
                in_component_list = False
                continue

            if not run_seen and line.startswith("<run "):
                info["run_start_time"] = _attrs(line).get("startTimeStamp", "")
                run_seen = True
                continue

            if line.startswith("<spectrumList "):
                info["spectrum_count"] = _safe_int(_attrs(line).get("count", "0")) or 0
                continue

            if line.startswith("<cvParam "):
                attrs = _attrs(line)
                name = attrs.get("name", "")
                accession = attrs.get("accession", "")
                value = attrs.get("value", "")
                lname = name.lower()
                if in_source_file:
                    if lname.endswith("format") and "nativeid" not in lname and not info["source_format"]:
                        info["source_format"] = name
                elif current_ref_group is not None:
                    if accession == SERIAL_ACCESSION or lname == "instrument serial number":
                        current_ref_group["serial"] = value
                    elif name and lname not in EXCLUDED_INSTRUMENT_NAMES and not current_ref_group["model"]:
                        current_ref_group["model"] = name
                elif current_instrument_config is not None and not in_component_list:
                    if accession == SERIAL_ACCESSION or lname == "instrument serial number":
                        current_instrument_config["serial"] = value
                    elif name and lname not in EXCLUDED_INSTRUMENT_NAMES and not current_instrument_config["model"]:
                        current_instrument_config["model"] = name
                else:
                    if lname == "ms1 spectrum":
                        info["filecontent_has_ms1"] = 1
                    elif lname == "msn spectrum":
                        info["filecontent_has_msn"] = 1
                continue

            if line.startswith("<userParam "):
                attrs = _attrs(line)
                lname = attrs.get("name", "").lower()
                value = attrs.get("value", "")
                if lname == "instrument model":
                    if current_ref_group is not None:
                        current_ref_group["user_model"] = value
                    elif current_instrument_config is not None:
                        current_instrument_config["user_model"] = value

    return ref_groups


def _find_index_list_offset(mm: mmap.mmap) -> int | None:
    file_size = len(mm)
    for window in (1024 * 1024, 8 * 1024 * 1024):
        read_size = min(window, file_size)
        tail = mm[file_size - read_size:file_size]
        match = INDEX_LIST_OFFSET_RE.search(tail)
        if match:
            return int(match.group(1))
    return None


def _load_spectrum_index_entries(mm: mmap.mmap) -> list[tuple[str, int]]:
    index_list_offset = _find_index_list_offset(mm)
    if index_list_offset is None:
        return []

    entries: list[tuple[str, int]] = []
    in_spectrum_index = False
    for raw_line in mm[index_list_offset:].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(b'<index name="spectrum"'):
            in_spectrum_index = True
            continue
        if in_spectrum_index and line.startswith(b"</index>"):
            break
        if in_spectrum_index and line.startswith(b"<offset "):
            offset_match = OFFSET_RE.search(line)
            idref_match = IDREF_RE.search(line)
            if offset_match and idref_match:
                entries.append((idref_match.group(1).decode("utf-8", errors="replace"), int(offset_match.group(1))))
    return entries


def _read_spectrum_prefix(mm: mmap.mmap, offset: int, initial_read_size: int = 1024, max_read_size: int = 4096) -> bytes:
    read_size = initial_read_size
    while read_size <= max_read_size:
        data = mm[offset:offset + read_size]
        has_level = b'name="ms level"' in data
        has_mode = (b'name="profile spectrum"' in data) or (b'name="centroid spectrum"' in data)
        if has_level and (has_mode or read_size >= 2048):
            return data
        read_size *= 2
    return mm[offset:offset + max_read_size]


def _prefix_mode(prefix: bytes) -> str:
    has_profile = b'name="profile spectrum"' in prefix
    has_centroid = b'name="centroid spectrum"' in prefix
    if has_profile and has_centroid:
        return "mixed_mode"
    if has_profile:
        return "profile"
    if has_centroid:
        return "centroid"
    return "unknown_mode"


def _wiff_level_offsets(entries: list[tuple[str, int]]) -> tuple[list[int], list[int], int]:
    ms1_offsets: list[int] = []
    ms2_offsets: list[int] = []
    other_count = 0
    for idref, offset in entries:
        match = WIFF_EXPERIMENT_RE.search(idref)
        if not match:
            other_count += 1
            continue
        experiment = int(match.group(1))
        if experiment == 1:
            ms1_offsets.append(offset)
        elif experiment > 1:
            ms2_offsets.append(offset)
        else:
            other_count += 1
    return ms1_offsets, ms2_offsets, other_count


def _sample_uniform_mode(mm: mmap.mmap, offsets: list[int]) -> str | None:
    if not offsets:
        return None
    sample_indices = sorted({0, len(offsets) // 4, len(offsets) // 2, (3 * len(offsets)) // 4, len(offsets) - 1})
    modes = {_prefix_mode(_read_spectrum_prefix(mm, offsets[idx])) for idx in sample_indices}
    if len(modes) == 1:
        mode = next(iter(modes))
        if mode in {"profile", "centroid"}:
            return mode
    return None


def _entry_sizes(entries: list[tuple[str, int]]) -> list[int]:
    if not entries:
        return []
    sizes = [entries[i + 1][1] - entries[i][1] for i in range(len(entries) - 1)]
    sizes.append(0)
    return sizes


def _sample_entry_metadata(mm: mmap.mmap, entries: list[tuple[str, int]], max_samples: int = 64) -> list[dict]:
    sizes = _entry_sizes(entries)
    rows = []
    for idx in _sample_indices(len(entries), max_samples):
        offset = entries[idx][1]
        prefix = _read_spectrum_prefix(mm, offset)
        match = MS_LEVEL_VALUE_RE.search(prefix)
        rows.append(
            {
                "idx": idx,
                "size": sizes[idx],
                "ms_level": int(match.group(1)) if match else None,
                "mode": _prefix_mode(prefix),
                "offset": offset,
            }
        )
    return rows


def _scan_first_entry_metadata(mm: mmap.mmap, entries: list[tuple[str, int]], limit: int = 1024) -> list[dict]:
    rows = []
    for idx, (_, offset) in enumerate(entries[:limit]):
        prefix = _read_spectrum_prefix(mm, offset)
        match = MS_LEVEL_VALUE_RE.search(prefix)
        rows.append(
            {
                "idx": idx,
                "ms_level": int(match.group(1)) if match else None,
                "mode": _prefix_mode(prefix),
            }
        )
    return rows


def _classify_sizes_from_samples(samples: list[dict], info: dict) -> tuple[str, float] | None:
    ms1_sizes = [row["size"] for row in samples if row["ms_level"] == 1 and row["size"] > 0]
    ms2_sizes = [row["size"] for row in samples if row["ms_level"] == 2 and row["size"] > 0]
    if ms1_sizes and ms2_sizes:
        if max(ms2_sizes) < min(ms1_sizes):
            return ("gt", (max(ms2_sizes) + min(ms1_sizes)) / 2.0)
        if max(ms1_sizes) < min(ms2_sizes):
            return ("lt", (max(ms1_sizes) + min(ms2_sizes)) / 2.0)
        return None

    sampled_levels = {row["ms_level"] for row in samples if row["ms_level"] is not None}
    if sampled_levels == {1} and not info["filecontent_has_msn"]:
        return ("all_ms1", 0.0)
    if sampled_levels == {2} and not info["filecontent_has_ms1"]:
        return ("all_ms2", 0.0)
    return None


def _detect_fixed_cycle_pattern(first_rows: list[dict]) -> list[int] | None:
    levels = [row["ms_level"] for row in first_rows]
    ms1_positions = [i for i, level in enumerate(levels) if level == 1]
    if len(ms1_positions) < 3 or ms1_positions[0] != 0:
        return None
    gaps = [b - a for a, b in zip(ms1_positions, ms1_positions[1:])]
    if not gaps:
        return None
    cycle_len = gaps[0]
    if cycle_len <= 1:
        return None
    if any(gap != cycle_len for gap in gaps[: min(len(gaps), 8)]):
        return None
    if len(levels) < cycle_len * 2:
        return None

    pattern = levels[:cycle_len]
    if any(level not in (1, 2) for level in pattern):
        return None
    for idx, level in enumerate(levels):
        if level not in (1, 2):
            return None
        if level != pattern[idx % cycle_len]:
            return None
    return pattern


def _sample_uniform_mode_from_indices(mm: mmap.mmap, entries: list[tuple[str, int]], indices: list[int]) -> str | None:
    if not indices:
        return None
    sample_indices = _sample_indices(len(indices), 5)
    modes = {_prefix_mode(_read_spectrum_prefix(mm, entries[indices[pos]][1])) for pos in sample_indices}
    if len(modes) == 1:
        mode = next(iter(modes))
        if mode in {"profile", "centroid"}:
            return mode
    return None


def _try_fixed_cycle_scan(mm: mmap.mmap, entries: list[tuple[str, int]], info: dict) -> bool:
    first_rows = _scan_first_entry_metadata(mm, entries)
    pattern = _detect_fixed_cycle_pattern(first_rows)
    if pattern is None:
        return False

    ms1_indices = [idx for idx, level in enumerate(pattern) if level == 1]
    ms2_indices = [idx for idx, level in enumerate(pattern) if level == 2]
    if not ms1_indices or not ms2_indices:
        return False

    total = len(entries)
    cycle_len = len(pattern)
    cycles, remainder = divmod(total, cycle_len)

    ms1_global_indices = []
    ms2_global_indices = []
    for cycle_id in (0, max(cycles // 2, 0), max(cycles - 1, 0)):
        base = cycle_id * cycle_len
        for pos in ms1_indices[:1]:
            idx = base + pos
            if idx < total:
                ms1_global_indices.append(idx)
        for pos in ms2_indices[: min(3, len(ms2_indices))]:
            idx = base + pos
            if idx < total:
                ms2_global_indices.append(idx)
    if remainder:
        base = cycles * cycle_len
        for pos, level in enumerate(pattern[:remainder]):
            idx = base + pos
            if level == 1:
                ms1_global_indices.append(idx)
            elif level == 2:
                ms2_global_indices.append(idx)

    ms1_mode = _sample_uniform_mode_from_indices(mm, entries, sorted(set(ms1_global_indices)))
    ms2_mode = _sample_uniform_mode_from_indices(mm, entries, sorted(set(ms2_global_indices)))
    if ms1_mode is None or ms2_mode is None:
        return False

    ms1_count = cycles * len(ms1_indices) + sum(1 for level in pattern[:remainder] if level == 1)
    ms2_count = cycles * len(ms2_indices) + sum(1 for level in pattern[:remainder] if level == 2)
    info["ms1_scan_count"] = ms1_count
    info["ms2_scan_count"] = ms2_count
    info[f"ms1_{ms1_mode}_count"] = ms1_count
    info[f"ms2_{ms2_mode}_count"] = ms2_count
    return True


def _try_size_based_scan(mm: mmap.mmap, entries: list[tuple[str, int]], info: dict) -> bool:
    if not entries:
        return False
    samples = _sample_entry_metadata(mm, entries)
    classifier = _classify_sizes_from_samples(samples, info)
    if classifier is None:
        return False

    sizes = _entry_sizes(entries)
    direction, threshold = classifier
    ms1_offsets: list[int] = []
    ms2_offsets: list[int] = []
    other_count = 0
    last_exact_level = None
    if entries:
        last_prefix = _read_spectrum_prefix(mm, entries[-1][1])
        last_match = MS_LEVEL_VALUE_RE.search(last_prefix)
        last_exact_level = int(last_match.group(1)) if last_match else None

    for idx, (_, offset) in enumerate(entries):
        if idx == len(entries) - 1:
            level = last_exact_level
        elif direction == "all_ms1":
            level = 1
        elif direction == "all_ms2":
            level = 2
        elif direction == "gt":
            level = 1 if sizes[idx] > threshold else 2
        elif direction == "lt":
            level = 1 if sizes[idx] < threshold else 2
        else:
            level = None

        if level == 1:
            ms1_offsets.append(offset)
        elif level == 2:
            ms2_offsets.append(offset)
        else:
            other_count += 1

    ms1_mode = _sample_uniform_mode(mm, ms1_offsets)
    ms2_mode = _sample_uniform_mode(mm, ms2_offsets)
    if ms1_offsets and ms1_mode is None:
        return False
    if ms2_offsets and ms2_mode is None:
        return False

    if ms1_offsets:
        ms1_count = len(ms1_offsets)
    if ms2_offsets:
        ms2_count = len(ms2_offsets)
    else:
        ms2_count = 0
    if not ms1_offsets:
        ms1_count = 0

    info["other_ms_level_count"] = other_count
    if ms1_offsets:
        info["ms1_scan_count"] = ms1_count
        info[f"ms1_{ms1_mode}_count"] = ms1_count
    if ms2_offsets:
        info["ms2_scan_count"] = ms2_count
        info[f"ms2_{ms2_mode}_count"] = ms2_count
    return True


def _scan_wiff_spectra(mm: mmap.mmap, entries: list[tuple[str, int]], info: dict) -> bool:
    ms1_offsets, ms2_offsets, other_count = _wiff_level_offsets(entries)

    ms1_mode = _sample_uniform_mode(mm, ms1_offsets)
    ms2_mode = _sample_uniform_mode(mm, ms2_offsets)
    if ms1_offsets and ms1_mode is None:
        return False
    if ms2_offsets and ms2_mode is None:
        return False

    info["other_ms_level_count"] = other_count
    if ms1_offsets and ms1_mode is not None:
        info["ms1_scan_count"] = len(ms1_offsets)
        info[f"ms1_{ms1_mode}_count"] = len(ms1_offsets)
    if ms2_offsets and ms2_mode is not None:
        info["ms2_scan_count"] = len(ms2_offsets)
        info[f"ms2_{ms2_mode}_count"] = len(ms2_offsets)
    return True


def _scan_spectra(path: Path, info: dict) -> None:
    with path.open("rb") as handle:
        with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            entries = _load_spectrum_index_entries(mm)
            if not entries:
                raise RuntimeError(f"indexedmzML spectrum offsets not found in {path}")
            if info["source_format"] == "ABI WIFF format" and _scan_wiff_spectra(mm, entries, info):
                return
            if info["source_format"] != "ABI WIFF format" and _try_size_based_scan(mm, entries, info):
                return
            if info["source_format"] != "ABI WIFF format" and _try_fixed_cycle_scan(mm, entries, info):
                return
            for _, offset in entries:
                prefix = _read_spectrum_prefix(mm, offset)
                match = MS_LEVEL_VALUE_RE.search(prefix)
                current_spectrum = {
                    "ms_level": int(match.group(1)) if match else None,
                    "is_profile": b'name="profile spectrum"' in prefix,
                    "is_centroid": b'name="centroid spectrum"' in prefix,
                }
                _finalize_spectrum(info, current_spectrum)


def _apply_mode_counts(info: dict, prefix: str, mode: str) -> None:
    key = f"{prefix}_{mode}_count"
    info[key] += 1


def _finalize_instrument_config(info: dict, config: dict, ref_groups: dict[str, dict]) -> None:
    model = config["model"]
    user_model = config["user_model"]
    serial = config["serial"]
    for ref_id in config["refs"]:
        ref = ref_groups.get(ref_id)
        if not ref:
            continue
        if not model:
            model = ref["model"]
        if not user_model:
            user_model = ref["user_model"]
        if not serial:
            serial = ref["serial"]
    model = _normalize_model(model, user_model, info["source_format"])
    if model and not info["instrument_model"]:
        info["instrument_model"] = model
    if serial and not info["instrument_serial"]:
        info["instrument_serial"] = serial


def _finalize_spectrum(info: dict, spectrum: dict) -> None:
    ms_level = spectrum["ms_level"]
    is_profile = spectrum["is_profile"]
    is_centroid = spectrum["is_centroid"]
    if is_profile and is_centroid:
        mode = "mixed_mode"
    elif is_profile:
        mode = "profile"
    elif is_centroid:
        mode = "centroid"
    else:
        mode = "unknown_mode"

    if ms_level == 1:
        info["ms1_scan_count"] += 1
        _apply_mode_counts(info, "ms1", mode)
    elif ms_level == 2:
        info["ms2_scan_count"] += 1
        _apply_mode_counts(info, "ms2", mode)
    else:
        info["other_ms_level_count"] += 1


def _derive_row_metrics(row: dict) -> None:
    row["parsed_spectrum_count"] = int(row["ms1_scan_count"]) + int(row["ms2_scan_count"]) + int(row["other_ms_level_count"])
    row["header_parsed_match"] = int(row["parsed_spectrum_count"] == int(row["spectrum_count"]))
    row["ms1_mode_summary"] = _mode_summary(
        int(row["ms1_profile_count"]),
        int(row["ms1_centroid_count"]),
        int(row["ms1_mixed_mode_count"]),
        int(row["ms1_unknown_mode_count"]),
        int(row["ms1_scan_count"]),
    )
    row["ms2_mode_summary"] = _mode_summary(
        int(row["ms2_profile_count"]),
        int(row["ms2_centroid_count"]),
        int(row["ms2_mixed_mode_count"]),
        int(row["ms2_unknown_mode_count"]),
        int(row["ms2_scan_count"]),
    )
    row["ms1_mode_counts"] = _format_mode_counts(
        int(row["ms1_profile_count"]),
        int(row["ms1_centroid_count"]),
        int(row["ms1_mixed_mode_count"]),
        int(row["ms1_unknown_mode_count"]),
    )
    row["ms2_mode_counts"] = _format_mode_counts(
        int(row["ms2_profile_count"]),
        int(row["ms2_centroid_count"]),
        int(row["ms2_mixed_mode_count"]),
        int(row["ms2_unknown_mode_count"]),
    )


def _analyze_header(path: Path) -> dict:
    file_size_bytes = int(path.stat().st_size)
    info = {
        "file": str(path),
        "file_name": path.name,
        "file_size_bytes": file_size_bytes,
        "file_size_gib": _human_gib(file_size_bytes),
        "source_file_name": "",
        "source_format": "",
        "instrument_model": "",
        "instrument_serial": "",
        "mzml_id": "",
        "run_start_time": "",
        "spectrum_count": 0,
        "parsed_spectrum_count": 0,
        "ms1_scan_count": 0,
        "ms2_scan_count": 0,
        "other_ms_level_count": 0,
        "ms1_profile_count": 0,
        "ms1_centroid_count": 0,
        "ms1_mixed_mode_count": 0,
        "ms1_unknown_mode_count": 0,
        "ms2_profile_count": 0,
        "ms2_centroid_count": 0,
        "ms2_mixed_mode_count": 0,
        "ms2_unknown_mode_count": 0,
        "ms1_mode_summary": "",
        "ms2_mode_summary": "",
        "ms1_mode_counts": "",
        "ms2_mode_counts": "",
        "header_parsed_match": 0,
        "filecontent_has_ms1": 0,
        "filecontent_has_msn": 0,
    }

    _parse_header_prefix(path, info)
    _scan_spectra(path, info)

    _derive_row_metrics(info)
    return info


def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _aggregate_rows(rows: list[dict]) -> dict:
    total_size_bytes = sum(int(row["file_size_bytes"]) for row in rows)
    total_size_gib = _human_gib(total_size_bytes)
    sizes = [float(row["file_size_gib"]) for row in rows]
    out = {
        "n_files": len(rows),
        "total_size_bytes": total_size_bytes,
        "total_size_gib": total_size_gib,
        "mean_size_gib": sum(sizes) / len(sizes) if sizes else 0.0,
        "max_size_gib": max(sizes) if sizes else 0.0,
        "min_size_gib": min(sizes) if sizes else 0.0,
    }
    for key in NUMERIC_SUMMARY_FIELDS:
        out[key] = sum(int(row[key]) for row in rows)
    out["mean_spectra_per_file"] = (out["spectrum_count"] / len(rows)) if rows else 0.0
    out["header_parsed_match_files"] = sum(int(row["header_parsed_match"]) for row in rows)
    _derive_row_metrics(out)
    return out


def _dataset_summary_rows(per_file_rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in per_file_rows:
        grouped[row["dataset"]].append(row)

    out = []
    for dataset, rows in grouped.items():
        summary = _aggregate_rows(rows)
        summary["dataset"] = dataset
        out.append(summary)
    return sorted(out, key=lambda row: row["dataset"])


def _group_summary_rows(per_file_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in per_file_rows:
        grouped[(row["dataset"], row["group_tag"])].append(row)

    out = []
    for (dataset, group_tag), rows in sorted(grouped.items()):
        summary = _aggregate_rows(rows)
        summary["dataset"] = dataset
        summary["group_tag"] = group_tag
        out.append(summary)
    return out


def _instrument_overview_rows(per_file_rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in per_file_rows:
        grouped[row["instrument_model"] or "Unknown"].append(row)

    rows = []
    for instrument, items in sorted(grouped.items(), key=lambda kv: kv[0]):
        source_formats = sorted({row["source_format"] for row in items if row["source_format"]})
        serials = sorted({row["instrument_serial"] for row in items if row["instrument_serial"]})
        rows.append(
            {
                "instrument_model": instrument,
                "n_files": len(items),
                "datasets": ", ".join(sorted({row["dataset"] for row in items})),
                "source_formats": ", ".join(source_formats),
                "serial_count": len(serials),
            }
        )
    return rows


def _write_report(
    out_path: Path,
    summary_rows: list[dict],
    group_rows: list[dict],
    per_file_rows: list[dict],
    instrument_rows: list[dict],
) -> None:
    lines = [
        "# Dataset mzML Header Inventory",
        "",
        f"- Updated: `{_now_iso()}`",
        "",
        "## 1. Scope",
        "",
        "- `full8`",
        "- `stackzdpd_validation_uncompressed`",
        "",
        "## 2. Metric Definition",
        "",
        "- `spectrum_count`: 从 mzML 头部 `<spectrumList count=\"...\">` 读取的总 spectrum 数。",
        "- `parsed_spectrum_count`: 逐个解析 `<spectrum>` 后得到的实际计数，应与 `spectrum_count` 一致。",
        "- `ms1_scan_count` / `ms2_scan_count`: 仅统计 `ms level = 1` 与 `ms level = 2` 的 spectrum 数；其余层级计入 `other_ms_level_count`。",
        "- `ms1_profile_count` / `ms2_profile_count`: 对应 MS 级别中被标记为 `profile spectrum` 的 spectrum 数。",
        "- `ms1_centroid_count` / `ms2_centroid_count`: 对应 MS 级别中被标记为 `centroid spectrum` 的 spectrum 数。",
        "- `ms1_mixed_mode_count` / `ms2_mixed_mode_count`: 单个 spectrum 同时带有 `profile spectrum` 与 `centroid spectrum` 标记的计数。",
        "- `ms1_unknown_mode_count` / `ms2_unknown_mode_count`: 对应 MS 级别中未发现 profile/centroid 明确标记的计数。",
        "- `ms1_mode_summary` / `ms2_mode_summary`: 对该文件在对应 MS 级别上的整体模式结论，取值为 `profile`、`centroid`、`mixed` 或 `unknown`。",
        "- `source_format`: 从 `sourceFileList` 提取的原始源文件格式，例如 `Thermo RAW format`、`ABI WIFF format`、`Agilent MassHunter format`。",
        "- `instrument_model`: 优先从 `instrumentConfiguration` 提取；若只给出通用占位值，则回退到 `referenceableParamGroup` 或 `userParam instrument model`。例如 `Agilent instrument model + QTOF` 统一记为 `Agilent QTOF`。",
        "- `instrument_serial`: mzML 头部中的 vendor-reported instrument serial number，用于区分同型号但不同物理设备的仪器。",
        "",
        "## 3. Dataset Summary",
        "",
        "| Dataset | Files | Total size (GiB) | Mean size (GiB) | Total spectra | Parsed spectra | Match files | Total MS1 | Total MS2 | Other | MS1 summary | MS2 summary |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in summary_rows:
        lines.append(
            f"| `{row['dataset']}` | {row['n_files']} | {row['total_size_gib']:.3f} | {row['mean_size_gib']:.3f} | "
            f"{row['spectrum_count']} | {row['parsed_spectrum_count']} | {row['header_parsed_match_files']} | "
            f"{row['ms1_scan_count']} | {row['ms2_scan_count']} | {row['other_ms_level_count']} | "
            f"`{row['ms1_mode_summary']}` | `{row['ms2_mode_summary']}` |"
        )

    lines.extend(
        [
            "",
            "## 4. Group Summary",
            "",
            "| Dataset | Group | Files | Total size (GiB) | Total spectra | Parsed spectra | Total MS1 | Total MS2 | Other | MS1 summary | MS2 summary |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in group_rows:
        lines.append(
            f"| `{row['dataset']}` | `{row['group_tag']}` | {row['n_files']} | {row['total_size_gib']:.3f} | "
            f"{row['spectrum_count']} | {row['parsed_spectrum_count']} | {row['ms1_scan_count']} | "
            f"{row['ms2_scan_count']} | {row['other_ms_level_count']} | `{row['ms1_mode_summary']}` | `{row['ms2_mode_summary']}` |"
        )

    lines.extend(
        [
            "",
            "## 5. Instrument Inventory",
            "",
            "| Instrument model | Files | Datasets | Source formats | Distinct serials |",
            "|---|---:|---|---|---:|",
        ]
    )
    for row in instrument_rows:
        lines.append(
            f"| `{row['instrument_model']}` | {row['n_files']} | `{row['datasets']}` | `{row['source_formats']}` | {row['serial_count']} |"
        )

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in per_file_rows:
        grouped[row["dataset"]].append(row)
    for dataset, rows in sorted(grouped.items()):
        lines.extend(
            [
                "",
                f"## 6. {dataset}: Per-file Summary",
                "",
                "| File | Group | Source format | Instrument | Serial | Size (GiB) | Header spectra | Parsed | MS1 | MS2 | Other | MS1 summary | MS2 summary | Source file |",
                "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|",
            ]
        )
        for row in rows:
            lines.append(
                f"| `{row['file_name']}` | `{row['group_tag']}` | `{row['source_format']}` | `{row['instrument_model']}` | "
                f"`{row['instrument_serial']}` | {float(row['file_size_gib']):.3f} | {row['spectrum_count']} | "
                f"{row['parsed_spectrum_count']} | {row['ms1_scan_count']} | {row['ms2_scan_count']} | {row['other_ms_level_count']} | "
                f"`{row['ms1_mode_summary']}` | `{row['ms2_mode_summary']}` | `{row['source_file_name']}` |"
            )

        lines.extend(
            [
                "",
                f"## 7. {dataset}: Per-file Mode Counts",
                "",
                "| File | MS1 profile | MS1 centroid | MS1 mixed | MS1 unknown | MS2 profile | MS2 centroid | MS2 mixed | MS2 unknown |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in rows:
            lines.append(
                f"| `{row['file_name']}` | {row['ms1_profile_count']} | {row['ms1_centroid_count']} | {row['ms1_mixed_mode_count']} | "
                f"{row['ms1_unknown_mode_count']} | {row['ms2_profile_count']} | {row['ms2_centroid_count']} | "
                f"{row['ms2_mixed_mode_count']} | {row['ms2_unknown_mode_count']} |"
            )

    lines.extend(
        [
            "",
            "## 8. Instrument Difference Notes",
            "",
            "- `Orbitrap Exploris 480`: Thermo 的四极杆-Orbitrap 平台，定位是高分辨率、高质量精度的常规 DDA/DIA 采集。相比 Tribrid，它结构更单一，但定量型 DIA 工作流很常见。",
            "- `Orbitrap Fusion Lumos`: Thermo Tribrid 平台，组合了 quadrupole、linear ion trap 与 Orbitrap，碎裂方式和扫描路径更灵活，适合复杂 DDA、ETD 等高级工作流。",
            "- `Q Exactive Plus` / `Q Exactive HF` / `Q Exactive HF-X`: 都属于 Thermo 的四极杆-Orbitrap 家族。`HF` / `HF-X` 相比 `Plus` 具有更高扫描速度；`HF-X` 进一步优化了高通量蛋白组学场景。",
            "- `TripleTOF 5600` / `TripleTOF 6600`: SCIEX 的 QTOF 平台，典型结构是 quadrupole + TOF。它们在 SWATH/DIA 历史数据中很常见，`6600` 是较新的代际，通常具有更高的采集速度与更好的工作流吞吐。",
            "- `Agilent QTOF`: 该文件头只暴露到了 `QTOF` 类型，没有给出更精确的商业系列号，因此本报告只能确认到 Agilent QTOF 架构层级，不能从当前 mzML 头部进一步确认到更细型号。",
            "- 架构层面上，`Orbitrap` 文件与 `QTOF` 文件最大的差别在于质量分析器类型不同；这会影响分辨率-速度权衡、峰形表现和典型 acquisition 习惯，也解释了跨数据集时 profile/centroid 分布和扫描数结构可能明显不同。",
            "",
            "## 9. What `serial` Means",
            "",
            "- `serial` 指仪器序列号或 vendor 在 mzML 中导出的设备唯一标识。",
            "- 它不是实验文件编号，也不是样本编号；它用于区分同一型号的不同物理机器，例如两台 `Orbitrap Exploris 480` 可以共享型号但 serial 不同。",
            "- Thermo 文件中常见的 `Exactive Series slot #...` 或 `MA...`，以及 SCIEX 文件中的 `AY...`、`BR...`，都属于 vendor 写入 mzML 的设备标识字符串。",
            "",
            "## 10. Interpretation",
            "",
            "- 如果某文件 `MS1 summary = profile` 且 `MS2 summary = profile`，表示两级谱图在 mzML 中都以 profile 形式保存。",
            "- 如果某文件 `MS1 summary = centroid` 或 `MS2 summary = centroid`，表示对应 MS 级别已经是离散峰列表，而不是连续 profile 采样点。",
            "- 如果 `summary = mixed`，表示同一文件内该 MS 级别至少出现了两种保存模式，或存在部分 spectrum 未标记，后续下游比较时需要按文件而不是按数据集整体做更细分解释。",
            "- 如果 `header_parsed_match_files = n_files`，说明逐谱解析结果与 mzML 头部 `spectrumList count` 完全一致，本次 inventory 统计在计数层面自洽。",
        ]
    )

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=("full8", "stackzdpd_validation_uncompressed"),
        default=("full8", "stackzdpd_validation_uncompressed"),
    )
    args = parser.parse_args()

    datasets = _dataset_map(list(args.datasets))
    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    per_file_rows = []
    for dataset, files in datasets.items():
        for idx, path in enumerate(files, start=1):
            t0 = datetime.now()
            print(f"[inventory] start dataset={dataset} file={idx}/{len(files)} name={path.name}", flush=True)
            row = _analyze_header(path)
            row["dataset"] = dataset
            row["dataset_index"] = idx
            row["group_tag"] = _relative_group(path, dataset)
            per_file_rows.append(row)
            elapsed = (datetime.now() - t0).total_seconds()
            print(f"[inventory] done dataset={dataset} file={idx}/{len(files)} name={path.name} elapsed_s={elapsed:.2f}", flush=True)

    per_file_rows.sort(key=lambda row: (row["dataset"], row["group_tag"], row["file_name"]))
    summary_rows = _dataset_summary_rows(per_file_rows)
    group_rows = _group_summary_rows(per_file_rows)
    instrument_rows = _instrument_overview_rows(per_file_rows)

    _write_csv(per_file_rows, out_root / "dataset_header_inventory_per_file.csv")
    _write_csv(summary_rows, out_root / "dataset_header_inventory_summary.csv")
    _write_csv(group_rows, out_root / "dataset_header_inventory_group_summary.csv")
    _write_csv(instrument_rows, out_root / "dataset_header_inventory_instrument_summary.csv")
    _write_report(out_root / "dataset_header_inventory_report.md", summary_rows, group_rows, per_file_rows, instrument_rows)
    (out_root / "summary.json").write_text(
        json.dumps(
            {
                "updated_at": _now_iso(),
                "datasets": {name: len(files) for name, files in datasets.items()},
                "summary_rows": summary_rows,
                "group_rows": group_rows,
                "instrument_rows": instrument_rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
