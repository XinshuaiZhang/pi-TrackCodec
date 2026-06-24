# Compression Benchmark Release Bundle

This directory is the publication-oriented release bundle for the mass
spectrometry compression benchmark used in the TrackCodec manuscript.

It is intentionally separate from the working scripts under
`<MSCODEC_ROOT>\scripts`. The files here are copied from the working versions and
then normalized so the benchmark run scripts, whole-file plotting scripts, and
section-level plotting scripts all write into one common output tree.

## Goal

This bundle is designed to support public release and paper reproducibility:

- keep the original working scripts untouched;
- group the benchmark execution and plotting code in one place;
- write outputs into one unified folder tree;
- keep figure content identical to the current benchmark figures when run with
  the same input summaries, same vendor size references, and same tool
  versions/configurations.

## Directory Layout

```text
experiments/benchmarks/compression_benchmark_release/
  README.md
  MANIFEST.md
  requirements-benchmark.txt
  .gitignore
  scripts/
    benchmark_release_paths.py
    benchmark_compression_methods.py
    plot_combined_benchmark_summaries_svg.py
    plot_benchmark_figures_matplotlib.py
    summarize_trackcodec_section_benchmark.py
    compute_zdpd_section_from_aird.py
    compute_section_raw_backend_baselines.py
    plot_section_advantage_benchmark.py
    generate_benchmark_35_file_metadata.py
    add_airdpro_zdpd_configs.ps1
  trackcodec_drivers/
    single_file_benchmark.py
    multi_dataset_supervisor.py
    stackzdpd_validation_benchmark.py
    full8_section_benchmark.py
    ...
  outputs/
    runs/
    combined_release/
      tables/
      plots/
      plot_data/
```

## Unified Output Policy

All release-bundle scripts default to the same output root:

```text
experiments/benchmarks/compression_benchmark_release/outputs
```

Subdirectories:

- whole-file benchmark runs:
  `outputs/runs/run_<timestamp>/`
- merged whole-file tables and plots:
  `outputs/combined_release/`
- combined tables:
  `outputs/combined_release/tables/`
- all figures:
  `outputs/combined_release/plots/`
- Matplotlib helper tables:
  `outputs/combined_release/plot_data/`

This satisfies the requirement that generated figures are unified into one
location.

## Environment Variables

Set these before running if your workspace is not the original working layout:

```powershell
$env:MSCODEC_ROOT = "<workspace_root>"
$env:TRACKCODEC_ROOT = "<trackcodec_source_root>"
$env:TRACKCODEC_PYTHON = "<trackcodec_python>"
$env:AIRDPRO_CONFIG_JSON = "<workspace_root>\\tools\\AirdPro-6.0.3-mz6-keepzero\\ConversionConfig.json"
$env:AIRDPRO_STACKFIX_EXE = "<workspace_root>\\tools\\AirdPro-5.3.1-stackfix-dda\\AirdPro.exe"
```

If `MSCODEC_ROOT` is not provided, the scripts try to locate the workspace
automatically.

## Benchmark Input Data

The release bundle includes summary CSVs and combined result tables, but it
does not include the large original mzML/vendor files. Benchmark source-data
links, expected local mzML paths, and the formal 36-file scope are documented
at:

```text
../../../BENCHMARK_DATA_DOWNLOADS.md
```

For figure-only reproduction, use the included CSVs under:

```text
inputs/whole_file_summaries/
outputs/combined_release/
```

For a heavy rerun, recreate the mzML inputs under the local roots encoded in
the summary CSV `input_path` column, typically:

```text
<MSCODEC_ROOT>\benchmark\data_full8\
<MSCODEC_ROOT>\benchmark\data_StackZDPD\
```

## Python Environments

TrackCodec compression runs:

```text
<TRACKCODEC_PYTHON>
```

Matplotlib plotting:

```text
<PLOT_PYTHON>
```

Install plotting dependencies if needed:

```powershell
python -m pip install -r experiments\benchmarks\compression_benchmark_release\requirements-benchmark.txt
```

## Script Roles

Current paper release workflow:

- `benchmark_compression_methods.py`
  Runs whole-file benchmark methods on mzML/mzXML inputs and writes per-run
  `summary.csv`, `summary.json`, and `summary.html`.
- `plot_combined_benchmark_summaries_svg.py`
  Merges one or more whole-file `summary.csv` files and generates the original
  hand-written SVG/HTML plot set.
- `plot_benchmark_figures_matplotlib.py`
  Generates Matplotlib `png/pdf/svg` figures from the merged combined tables.
- `summarize_trackcodec_section_benchmark.py`
  Reads TrackCodec per-file stats JSON paths from the combined benchmark table
  and computes TrackCodec MS1/MS2 section-level summaries.
- `compute_zdpd_section_from_aird.py`
  Computes ZDPD or AirdPro Default section-level CR using Aird index byte
  ranges.
- `compute_section_raw_backend_baselines.py`
  Computes gzip/zlib/zstd section-level raw-array baselines.
- `plot_section_advantage_benchmark.py`
  Builds section-level aggregate tables and the original SVG/PNG section plots.
- `generate_benchmark_35_file_metadata.py`
  Builds the benchmark metadata table.
- `add_airdpro_zdpd_configs.ps1`
  Helper for local AirdPro config setup.

Copied TrackCodec benchmark drivers from `experiments/benchmarks`:

- `trackcodec_drivers/single_file_benchmark.py`
  Original TrackCodec single-file benchmark driver.
- `trackcodec_drivers/multi_dataset_supervisor.py`
  TrackCodec multi-dataset supervisor for internal benchmark orchestration.
- `trackcodec_drivers/stackzdpd_validation_benchmark.py`
  Original StackZDPD validation benchmark driver.
- `trackcodec_drivers/full8_section_benchmark.py`
  Original section benchmark/plotting source used during TrackCodec development.
- `trackcodec_drivers/stackzdpd_validation_formal_suite.py`
  Formal StackZDPD validation entry.
- `trackcodec_drivers/stackzdpd_validation_lt5g_supervised.py`
  Subset validation driver for smaller files.
- `trackcodec_drivers/selected30_interleaved_supervisor.py`
  Another internal benchmark supervisor variant.
- `trackcodec_drivers/decode_archive_profile.py`
  Archive decode profiling helper.
- `trackcodec_drivers/summarize_trackcodec_stage_timings.py`
  Stage timing summarization helper.
- `trackcodec_drivers/watch_stackzdpd_validation_process.py`
  Validation process watcher.
- `trackcodec_drivers/watch_trackcodec_worker_rss.py`
  Worker RSS watcher.
- `trackcodec_drivers/watch_trackcodec_worker_rss_host.sh`
  Host-side RSS watcher shell helper.
- `trackcodec_drivers/java/ComboCompFileBench.java`
  Java helper from the original benchmark tree.

These driver files are included for completeness and code release, but the
main reproducible publication workflow in this bundle is centered on the
scripts under `scripts/`.

## Whole-File Benchmark CLI

Run selected methods and write results to `outputs/runs` by default:

```powershell
<TRACKCODEC_PYTHON> -B `
  $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\scripts\benchmark_compression_methods.py `
  --input <path-to-mzML-file-or-directory> `
  --recursive `
  --sort-by-size-desc `
  --workers 8 `
  --methods msconvert_zlib msconvert_gzip msconvert_numpress mspack masscomp airdpro zdpd stackzdpd trackcodec `
  --trackcodec-python <TRACKCODEC_PYTHON> `
  --resume
```

Override output root explicitly if desired:

```powershell
--output-root <path-to-benchmark-output>
```

## Benchmark Toolchain and Configurations

The benchmark runner can call external compression tools through wrappers on
`PATH` or in the workspace `bin/` directory. These third-party binaries are not
bundled in the public repository.

The host used for the current benchmark had:

| Tool | Role | Version/source used in the benchmark | Source modification |
| --- | --- | --- | --- |
| ProteoWizard `msconvert` | mzML/mzXML conversion; zlib/gzip/Numpress baselines; normalization for MassComp/mspack | `3.0.21229.9668f52` | Unmodified installer extraction. |
| mspack | Whole-file `.mgz` baseline | Locally rebuilt `mspack.exe` benchmark binary replacing the original local binary | `tinyxml2.cpp` uses Windows 64-bit file-length APIs for large mzML files; `decoder.cpp` returns the standard success code after decode completion. |
| MassComp | Whole-file `.masscomp` baseline after mzML-to-mzXML conversion | Local 64-bit-safe build when available | Uses a local wrapper/build that avoids the original large-file length overflow for `>2 GB` mzXML files. |
| AirdPro Default | Whole-file and section-level `.aird` baseline | AirdPro `6.0.3` local copy | Configuration modified for benchmark fairness: mz precision set to 6 decimal places and zero-intensity points retained. |
| ZDPD | AirdPro/ZDPD baseline | AirdPro local wrapper/config | Uses the `ZDPD` config through `zdpd.cmd`. |
| StackZDPD | Historical Stack-ZDPD comparison | AirdPro `5.3.1` stack-tail fixed local build | Local bridge/fix handles incomplete final stack groups. StackZDPD still has a known mz6 Int32 overflow for some files because scaled m/z is stored in Int32; true mz6 support requires an Int64 stack m/z stream and decoder/metadata changes. |
| TrackCodec | Proposed method | This repository | No external binary beyond the Python package and included native speedup extension. |

Method-specific command templates used by `benchmark_compression_methods.py`:

| Method key | Command class and important settings |
| --- | --- |
| `msconvert_zlib` | `msconvert <input> --mzML --zlib -o <outdir>`. ProteoWizard defaults are used for binary precision unless otherwise encoded in the source; default mzML output is m/z float64 and intensity float32. |
| `msconvert_gzip` | `msconvert <input> --mzML --gzip -o <outdir>`. This is ProteoWizard's gzip output option, not a custom compression level argument. |
| `msconvert_numpress` | `msconvert <input> --mzML --zlib --numpressAll -o <outdir>`. No custom Numpress accuracy flags are passed. ProteoWizard's defaults apply, e.g. `--numpressLinear` default relative accuracy shown by `msconvert --help` is `2e-09`; `--numpressAll` applies the ProteoWizard default Numpress encodings to supported arrays. |
| `mspack` | For mzML input, first normalizes with `msconvert --mzML --noindex --mz64 --inten32`, then runs `mspack --mzmle <normalized.mzML> <out.mgz>`. Time includes normalization. |
| `masscomp` | For mzML input, first converts with `msconvert --mzXML --mz64 --inten32`, then runs `masscomp -c <converted.mzXML> <out.masscomp>`. Time includes conversion. |
| `airdpro` | Runs `airdpro-compress.cmd <input> <outdir> <config>`, default config `Default` from the AirdPro 6.0.3 mz6/keep-zero setup. |
| `zdpd` | Runs `zdpd.cmd <input> <outdir> <config>`, default config `ZDPD`. |
| `stackzdpd` | Runs `stackzdpd.cmd <input> <outdir> <config>`, default config `StackZDPD`, with extra stack digit parameter `8` in the local bridge. |
| `trackcodec` | Runs `python -m production.cli.compress_mzml --input <input.mzML> --output <out.tcarchive> --stats-json <stats.json> --section-workers <n> --ms2-segment-workers <n> --ms1-island-workers <n> --parallel-ms1-ms2-processes false --cache-dir <cache>`. |

For a small benchmark script test on two full8 files:

```powershell
<TRACKCODEC_PYTHON> -B `
  $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\scripts\benchmark_compression_methods.py `
  --input-list <two-supported-mzML-paths.txt> `
  --sort-by-size-desc `
  --workers 2 `
  --methods trackcodec `
  --trackcodec-python <TRACKCODEC_PYTHON> `
  --output-root <path-to-two-file-benchmark-output> `
  --resume
```

Use an explicit input list for smoke tests to keep the selected file scope
deterministic.

## Merge Whole-File Runs And Generate SVG Figures

Default output is:

```text
experiments/benchmarks/compression_benchmark_release/outputs/combined_release
```

CLI:

```powershell
python -B $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\scripts\plot_combined_benchmark_summaries_svg.py `
  --input full8=$env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\full8_summary.csv `
  --input data_stackzdpd=$env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\data_stackzdpd_summary.csv `
  --input data_stackzdpd=$env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\data_stackzdpd_aif_summary.csv `
  --exclude-file 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML `
  --vendor-size-table $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\inputs\refs\compression_ratio_tables_with_ms_format.md
```

Single-plot update:

```powershell
python -B $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\scripts\plot_combined_benchmark_summaries_svg.py `
  --input full8=$env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\full8_summary.csv `
  --only-plot combined_compression_ratio_distribution
```

## Matplotlib Figure CLI

This reads merged tables from `outputs/combined_release/tables` by default and
writes all Matplotlib figures into `outputs/combined_release/plots`.

```powershell
<PLOT_PYTHON> -B `
  $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\scripts\plot_benchmark_figures_matplotlib.py `
  --combined-dir $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\outputs\combined_release
```

Single-plot update example:

```powershell
... --only-plot combined_method_file_ratio_heatmap
```

Supported Matplotlib plot keys:

- `combined_compression_ratio_distribution`
- `combined_median_compression_ratio`
- `combined_total_compressed_size`
- `combined_ratio_vs_elapsed_time`
- `combined_method_file_ratio_heatmap`
- `combined_method_file_status_matrix`
- `combined_multipanel_compressed_size`
- `trackcodec_section_advantage_compression_boxplot`
- `trackcodec_section_advantage_mean_bar`

## Section-Level Workflow

1. TrackCodec section stats:

```powershell
python -B ...\summarize_trackcodec_section_benchmark.py
```

2. ZDPD/AirdPro Default section stats:

```powershell
python -B ...\compute_zdpd_section_from_aird.py --method zdpd --label zdpd_baseline --display-name ZDPD
python -B ...\compute_zdpd_section_from_aird.py --method airdpro --label airdpro_default --display-name "AirdPro Default" --output-csv $env:REPO_ROOT\experiments\benchmarks\compression_benchmark_release\outputs\combined_release\tables\airdpro_default_aird_section_benchmark.csv
```

3. Raw backend baselines:

```powershell
python -B ...\compute_section_raw_backend_baselines.py --input-list <path-to-section-file-list> --workers 8
```

4. Section SVG/PNG plots:

```powershell
python -B ...\plot_section_advantage_benchmark.py
```

5. Final Matplotlib section figures:

```powershell
<PLOT_PYTHON> -B ...\plot_benchmark_figures_matplotlib.py
```

## Exact-Figure Reproducibility

To reproduce figures that are visually identical to the current benchmark
figures, you must keep all of the following aligned:

- same `summary.csv` input files;
- same excluded duplicate file list;
- same vendor raw size references;
- same AirdPro configs and executable versions;
- same TrackCodec code version;
- same Matplotlib environment;
- same method ordering and label mappings already encoded in the copied scripts.

The bundle now ships the exact whole-file summary CSV inputs and reference
markdown files under `inputs/`. If you use those bundled inputs and the same
tool environments, the generated benchmark tables match the current benchmark
results, and the SVG/Matplotlib figures are reproduced into the unified
`outputs/combined_release` tree.

