"""Unified MS1 codec entry point for TrackCodec."""

from pathlib import Path

from .baseline_ms1_codec import INTENSITY_MODES as BASELINE_INTENSITY_MODES
from .baseline_ms1_codec import MS1Codec as BaselineMS1Codec
from .cross_scan_codec import CROSS_SCAN_INTENSITY_MODES, CrossScanMS1Codec

INTENSITY_MODES = list(dict.fromkeys(list(BASELINE_INTENSITY_MODES) + list(CROSS_SCAN_INTENSITY_MODES)))


class MS1Codec:
    """Wrapper used by the production TrackCodec API."""

    def __init__(self, mz_precision=6, intensity_mode="szdpd", backend="zstd-9", **kwargs):
        self.mz_precision = mz_precision
        self.intensity_mode = intensity_mode
        self.backend = backend
        self.kwargs = dict(kwargs)
        if intensity_mode in CROSS_SCAN_INTENSITY_MODES:
            self.impl = CrossScanMS1Codec(
                mz_precision=mz_precision,
                intensity_mode=intensity_mode,
                backend=backend,
                **kwargs,
            )
        else:
            self.impl = BaselineMS1Codec(
                mz_precision=mz_precision,
                intensity_mode=intensity_mode,
                backend=backend,
            )

    def encode(self, tracks, ms1_scans=None, progress_callback=None):
        return self.impl.encode(tracks, ms1_scans, progress_callback=progress_callback)

    def encode_to_path(self, tracks, ms1_scans=None, output_path=None, progress_callback=None):
        if hasattr(self.impl, "encode_to_path"):
            return self.impl.encode_to_path(
                tracks,
                ms1_scans=ms1_scans,
                output_path=output_path,
                progress_callback=progress_callback,
            )
        payload, metadata = self.impl.encode(tracks, ms1_scans, progress_callback=progress_callback)
        if output_path is None:
            raise ValueError("encode_to_path requires output_path")
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(payload)
        return output_path, metadata

    def decode(self, compressed_bytes, metadata=None):
        return self.impl.decode(compressed_bytes, metadata)

    def __getattr__(self, name):
        impl = self.__dict__.get("impl")
        if impl is None:
            raise AttributeError(f"{type(self).__name__!s} object has no attribute {name!r}")
        return getattr(impl, name)
