from __future__ import annotations

import numpy as np

from production.ms2.codec import DIAWindowMS2Codec, MS2ModeConfig
from production.ms2.window_dictionary_codec import DIAWindowExactTrackCodec


def test_exact_track_section_dict_uses_int64_deltas_above_uint32_range() -> None:
    codec = DIAWindowMS2Codec(
        MS2ModeConfig.exact_track_dda_current(dia_exact_track_mode="prefer_section")
    )
    window_codec = codec.new_window_codec()
    assert isinstance(window_codec, DIAWindowExactTrackCodec)

    scale = 10**6
    dict_mz_q = np.array(
        [
            round(2817.383290 * scale),
            round(7112.350586 * scale),
            round(10000.0 * scale),
        ],
        dtype=np.int64,
    )
    specs = [{"dc": 2}, {"dc": 1}]

    payload, meta = codec._encode_exact_track_section_dict_flat(
        window_codec, dict_mz_q, specs
    )
    decoded = codec._decode_exact_track_section_dict(
        window_codec, payload, meta, specs
    )

    assert meta["section_dict_mode"] == "window_anchor_delta_i64"
    assert np.array_equal(decoded, dict_mz_q)
    assert decoded[1] / scale > np.iinfo(np.uint32).max / scale
