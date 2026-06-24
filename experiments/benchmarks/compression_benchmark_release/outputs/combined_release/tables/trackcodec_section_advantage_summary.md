# TrackCodec Section Advantage Benchmark

- Source table: `<TRACKCODEC_PUBLIC_RELEASE>\experiments\benchmarks\compression_benchmark_release\inputs\refs\compression_ratio_tables_with_ms_format.md`
- Source scope: AirdPro Default and ZDPD section CR are computed from existing AirdPro `.aird/.json` outputs for the current de-duplicated combined benchmark using `indexList` byte ranges; strict-q6/old eq-fidelity baselines remain available for the original Stack-ZDPD validation section source table; TrackCodec archive-fidelity section stats are read from the current de-duplicated combined benchmark; gzip level 6, zlib level 6, and zstd level 9 raw float64 baselines cover the retained section-baseline subset.
- MS2 figures and aggregates exclude files with zero MS2 raw bytes; the per-file CSV keeps those rows with `include_in_aggregate=no`.
- Reference for relative gain: `ZDPD`.
- Figure structure is adapted from `<TRACKCODEC_ROOT>/experiments/benchmarks/full8_section_benchmark.py`: aggregate bar and method distribution boxplot.
- Rendering uses pure SVG plus Pillow PNG output because Matplotlib crashes in this Windows environment at `Axes.bar()` with `0xc06d007f`.

## Aggregate Summary

| Section | Method | n | Mean CR | Median CR | Mean gain vs ZDPD |
|---|---|---:|---:|---:|---:|
| MS1 | gzip level 6 raw float64 | 24 | 1.920 | 1.913 | -69.5% |
| MS1 | zlib level 6 raw float64 | 24 | 1.920 | 1.913 | -69.5% |
| MS1 | zstd level 9 raw float64 | 24 | 1.746 | 1.677 | -72.3% |
| MS1 | AirdPro Default | 36 | 6.294 | 6.049 | +0.0% |
| MS1 | ZDPD | 36 | 6.294 | 6.049 | +0.0% |
| MS1 | TrackCodec eq-fidelity | 8 | 6.407 | 6.846 | +1.8% |
| MS1 | TrackCodec strict q6 | 8 | 5.735 | 6.255 | -8.9% |
| MS1 | TrackCodec archive fidelity | 36 | 6.775 | 6.695 | +7.6% |
| MS2 | gzip level 6 raw float64 | 23 | 1.957 | 1.945 | -42.7% |
| MS2 | zlib level 6 raw float64 | 23 | 1.957 | 1.945 | -42.7% |
| MS2 | zstd level 9 raw float64 | 23 | 2.139 | 2.139 | -37.3% |
| MS2 | ZDPD | 35 | 3.414 | 2.724 | +0.0% |
| MS2 | AirdPro Default | 35 | 3.414 | 2.724 | -0.0% |
| MS2 | TrackCodec eq-fidelity | 35 | 4.169 | 3.335 | +22.1% |

## Figures

| Figure | PNG | SVG |
|---|---|---|
| trackcodec_section_advantage_mean_bar | `../plots/trackcodec_section_advantage_mean_bar.png` | `../plots/trackcodec_section_advantage_mean_bar.svg` |
| trackcodec_section_advantage_compression_boxplot | `../plots/trackcodec_section_advantage_compression_boxplot.png` | `../plots/trackcodec_section_advantage_compression_boxplot.svg` |
