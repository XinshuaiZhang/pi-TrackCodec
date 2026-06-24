from __future__ import annotations


OUR_MODE = dict(
    mz_precision=6,
    intensity_mode="szdpd_xdelta_equalfidelity",
    backend="brotli",
    use_pfor=True,
    mz_dedup="residual_offset_model",
    zero_rle=True,
    metadata_transform=True,
    adaptive_uint32=True,
    adaptive_intensity_search=False,
    adaptive_search_mode="converged",
    adaptive_search_sample_count=16384,
    delta_ref_window=4,
    padded_delta_max_diff=1,
    adaptive_padded_delta_accounting=True,
    enable_byteaware_delta_ref_selection=True,
    enable_cross_track_full_prediction=True,
    cross_track_candidate_limit=4,
    cross_track_min_gain_bytes=32,
    omit_array_starts=True,
    omit_n_points=True,
    preserve_full_scan=True,
    retain_zero_intensity_mz=True,
    enable_full_scan_delta2=True,
    native_track_plan_min_track_len=3,
)
