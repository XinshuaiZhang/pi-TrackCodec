# Compression Benchmark Release Manifest

| File | Role |
|---|---|
| `scripts/benchmark_release_paths.py` | Unified release-bundle path policy and default output locations. |
| `scripts/benchmark_compression_methods.py` | Whole-file benchmark runner. |
| `scripts/plot_combined_benchmark_summaries_svg.py` | Whole-file combined SVG/HTML plotting. |
| `scripts/plot_benchmark_figures_matplotlib.py` | Whole-file and section-level Matplotlib plotting. |
| `scripts/summarize_trackcodec_section_benchmark.py` | TrackCodec section-level summary extraction. |
| `scripts/compute_zdpd_section_from_aird.py` | ZDPD/AirdPro Default section-level CR computation. |
| `scripts/compute_section_raw_backend_baselines.py` | gzip/zlib/zstd section-level baselines. |
| `scripts/plot_section_advantage_benchmark.py` | Original section-level SVG/PNG plotting and aggregate table generation. |
| `scripts/generate_benchmark_35_file_metadata.py` | Metadata table generation for the benchmark cohort. |
| `scripts/add_airdpro_zdpd_configs.ps1` | AirdPro config helper script. |
| `trackcodec_drivers/` | Copied original TrackCodec benchmark drivers, watchers, and validation scripts from `experiments/benchmarks`. |
| `outputs/` | Unified output root for runs, merged tables, and figures. |
