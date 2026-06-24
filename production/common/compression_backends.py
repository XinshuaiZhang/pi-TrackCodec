"""Compression backend wrappers and benchmarking"""
import zlib, bz2, time
import numpy as np
import threading
from concurrent.futures import ThreadPoolExecutor

_BACKENDS = {}
_TLS = threading.local()


def _thread_local_cache() -> dict:
    cache = getattr(_TLS, "cache", None)
    if cache is None:
        cache = {}
        _TLS.cache = cache
    return cache


def _zstd_compress_factory(zstd_module, level: int):
    def _compress(data: bytes) -> bytes:
        cache = _thread_local_cache()
        key = ("zstd_compressor", level)
        cctx = cache.get(key)
        if cctx is None:
            cctx = zstd_module.ZstdCompressor(level=level)
            cache[key] = cctx
        return cctx.compress(data)
    return _compress


def _zstd_decompress_factory(zstd_module):
    def _decompress(data: bytes) -> bytes:
        cache = _thread_local_cache()
        key = ("zstd_decompressor",)
        dctx = cache.get(key)
        if dctx is None:
            dctx = zstd_module.ZstdDecompressor()
            cache[key] = dctx
        return dctx.decompress(data)
    return _decompress

def _register():
    _BACKENDS['zlib'] = {
        'compress':   lambda d, lv=6:  zlib.compress(d, lv),
        'decompress': lambda d:         zlib.decompress(d),
        'levels':     [6],
    }
    _BACKENDS['bz2'] = {
        'compress':   lambda d, lv=9:  bz2.compress(d, lv),
        'decompress': lambda d:         bz2.decompress(d),
        'levels':     [9],
    }
    try:
        import zstandard as zstd
        for lv in [3, 9, 19]:
            key = f'zstd-{lv}'
            _BACKENDS[key] = {
                'compress':   _zstd_compress_factory(zstd, lv),
                'decompress': _zstd_decompress_factory(zstd),
                'levels':     [lv],
            }
    except ImportError:
        pass

    try:
        import lz4.frame as lz4f
        _BACKENDS['lz4'] = {
            'compress':   lambda d, lv=0:  lz4f.compress(d, compression_level=lv),
            'decompress': lambda d:         lz4f.decompress(d),
            'levels':     [0],
        }
    except ImportError:
        pass

    try:
        import brotli
        _BACKENDS['brotli'] = {
            'compress':   lambda d, lv=11: brotli.compress(d, quality=lv),
            'decompress': lambda d:         brotli.decompress(d),
            'levels':     [11],
        }
    except ImportError:
        pass

_register()


def available_backends():
    return list(_BACKENDS.keys())


def compress(data: bytes, backend: str) -> bytes:
    return _BACKENDS[backend]['compress'](data)


def decompress(data: bytes, backend: str) -> bytes:
    return _BACKENDS[backend]['decompress'](data)


def compress_many(items, backend: str, max_workers: int = 1):
    """Compress independent byte payloads in parallel without changing bytes."""
    payloads = list(items)
    if max_workers <= 1 or len(payloads) <= 1:
        return [compress(item, backend) for item in payloads]
    if sum(len(item) for item in payloads) < 64 * 1024:
        return [compress(item, backend) for item in payloads]
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        return list(executor.map(lambda item: compress(item, backend), payloads))


def decompress_many(items, backend: str, max_workers: int = 1):
    """Decompress independent byte payloads in parallel without changing bytes."""
    payloads = list(items)
    if max_workers <= 1 or len(payloads) <= 1:
        return [decompress(item, backend) for item in payloads]
    if sum(len(item) for item in payloads) < 64 * 1024:
        return [decompress(item, backend) for item in payloads]
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        return list(executor.map(lambda item: decompress(item, backend), payloads))


# Aliases used by codec.py
compress_bytes = compress
decompress_bytes = decompress


def benchmark_backend(data: bytes, backend: str, n_iter: int = 3) -> dict:
    """
    Returns: {'backend', 'ratio', 'compress_mb_s', 'decompress_mb_s', 'compressed_bytes'}
    """
    b = _BACKENDS[backend]
    mb = len(data) / 1e6

    # compress
    t0 = time.perf_counter()
    for _ in range(n_iter):
        comp = b['compress'](data)
    t_comp = (time.perf_counter() - t0) / n_iter

    # decompress
    t0 = time.perf_counter()
    for _ in range(n_iter):
        dec = b['decompress'](comp)
    t_decomp = (time.perf_counter() - t0) / n_iter

    assert dec == data, f"roundtrip fail for backend {backend}"

    return {
        'backend':          backend,
        'compressed_bytes': len(comp),
        'ratio':            len(data) / len(comp),
        'compress_mb_s':    mb / t_comp   if t_comp   > 0 else float('inf'),
        'decompress_mb_s':  mb / t_decomp if t_decomp > 0 else float('inf'),
    }
