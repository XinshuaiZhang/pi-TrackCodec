from __future__ import annotations

import ctypes
import gc
import os
import sys


_LIBC = None
if sys.platform.startswith("linux"):
    try:
        _LIBC = ctypes.CDLL("libc.so.6")
    except OSError:
        _LIBC = None


def release_memory() -> None:
    """Best-effort memory release at large stage boundaries.

    This is intentionally lightweight enough to be called only after large
    object graphs are deleted, for example after scan loading, track building,
    or section decode smoke tests.
    """

    gc.collect()
    if _LIBC is not None:
        try:
            _LIBC.malloc_trim(0)
        except Exception:
            pass


def _read_proc_meminfo_bytes() -> dict[str, int]:
    out: dict[str, int] = {}
    if not sys.platform.startswith("linux"):
        return out
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if not rest:
                    continue
                value_str = rest.strip().split()[0]
                try:
                    out[key] = int(value_str) * 1024
                except ValueError:
                    continue
    except OSError:
        return {}
    return out


def get_total_memory_bytes() -> int:
    meminfo = _read_proc_meminfo_bytes()
    total = int(meminfo.get("MemTotal", 0))
    if total > 0:
        return total
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        phys_pages = int(os.sysconf("SC_PHYS_PAGES"))
        return page_size * phys_pages
    except (AttributeError, OSError, ValueError):
        return 0


def get_available_memory_bytes() -> int:
    meminfo = _read_proc_meminfo_bytes()
    available = int(meminfo.get("MemAvailable", 0))
    if available > 0:
        return available
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        avail_pages = int(os.sysconf("SC_AVPHYS_PAGES"))
        return page_size * avail_pages
    except (AttributeError, OSError, ValueError):
        return 0


def recommend_parallel_section_processes(
    *,
    raw_payload_bytes: int,
    input_file_bytes: int = 0,
    min_available_bytes: int = 24 * (1 << 30),
    working_set_multiplier: float = 6.0,
    fixed_overhead_bytes: int = 2 * (1 << 30),
    headroom_ratio: float = 1.10,
) -> dict[str, int | float | bool | str]:
    raw_payload_bytes = max(0, int(raw_payload_bytes))
    input_file_bytes = max(0, int(input_file_bytes))
    total_bytes = get_total_memory_bytes()
    available_bytes = get_available_memory_bytes()
    estimated_peak_bytes = int(raw_payload_bytes * float(working_set_multiplier) + fixed_overhead_bytes + (input_file_bytes // 8))
    required_available_bytes = max(int(min_available_bytes), int(estimated_peak_bytes * float(headroom_ratio)))

    if available_bytes <= 0:
        enabled = True
        reason = "enabled_unknown_available_memory"
    else:
        enabled = available_bytes >= required_available_bytes
        reason = "enabled" if enabled else "disabled_by_memory_guard"

    return {
        "enabled": bool(enabled),
        "reason": reason,
        "raw_payload_bytes": int(raw_payload_bytes),
        "input_file_bytes": int(input_file_bytes),
        "total_memory_bytes": int(total_bytes),
        "available_memory_bytes": int(available_bytes),
        "estimated_parallel_peak_bytes": int(estimated_peak_bytes),
        "required_available_bytes": int(required_available_bytes),
        "min_available_bytes": int(min_available_bytes),
        "working_set_multiplier": float(working_set_multiplier),
        "fixed_overhead_bytes": int(fixed_overhead_bytes),
        "headroom_ratio": float(headroom_ratio),
    }
