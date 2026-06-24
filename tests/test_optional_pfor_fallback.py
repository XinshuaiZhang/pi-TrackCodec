import unittest
from unittest.mock import patch

import numpy as np

from production.common.compression_backends import compress
from production.ms1 import cross_scan_codec as codec_module


class OptionalPforFallbackTest(unittest.TestCase):
    def test_missing_pfor_falls_back_to_non_pfor_strategy(self):
        values = np.arange(8192, dtype=np.uint32) % 37

        with (
            patch.object(codec_module, "HAVE_PYFASTPFOR", False),
            patch.object(codec_module, "pyfastpfor", None),
        ):
            payload, meta = codec_module._encode_uint32_stream_best(
                values,
                "brotli",
                use_pfor=True,
                pfor_codec="simdfastpfor256",
                zero_rle=True,
                backend_candidates=["brotli", "zstd-9"],
                try_pfor=True,
                try_zero_rle=True,
                allow_bitpack=True,
            )

            self.assertFalse(meta["use_pfor"])
            np.testing.assert_array_equal(
                codec_module._decode_uint32_stream(payload, meta),
                values,
            )

    def test_missing_pfor_disables_direct_stream_selection(self):
        with patch.object(codec_module, "HAVE_PYFASTPFOR", False):
            codec = codec_module.CrossScanMS1Codec(
                backend="brotli",
                use_pfor=True,
                pfor_min_count=1,
            )

            self.assertFalse(codec._should_use_pfor("delta_intensity", 10000))

    def test_pfor_encoded_stream_still_requires_optional_dependency(self):
        with (
            patch.object(codec_module, "HAVE_PYFASTPFOR", False),
            patch.object(codec_module, "pyfastpfor", None),
            self.assertRaisesRegex(ImportError, r"TrackCodec\[pfor\]"),
        ):
            codec_module._decode_uint32_stream(
                compress(np.zeros(1, dtype=np.uint32).tobytes(), "brotli"),
                {
                    "count": 1,
                    "encoded_count": 1,
                    "zero_rle": False,
                    "use_pfor": True,
                    "outer_backend": "brotli",
                    "pfor_codec": "simdfastpfor256",
                    "encoded_words": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
