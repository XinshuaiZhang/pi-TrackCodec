# Ablation Bundle

This directory contains the public ablation code and outputs. MS1 and MS2 ablation are both filtered to the same 30-file scope.

## Directory Layout

- `inputs/`: copied CSV inputs and `unified30_file_scope.txt`.
- `scripts/`: copied and cleaned plotting/aggregation scripts.
- `scripts/fresh_rerun/`: optional raw-mzML rerun scripts for A0-A6 component ablation and the A0/A7 intensity-isolation ablation.
- `outputs/ms1_ablation_unified30/`: MS1 ablation tables and figures.
- `outputs/ms2_ablation_unified30/`: MS2 ablation tables and figures.

## File Scope

All ablation figures use exactly the files listed in:

- `inputs/unified30_file_scope.txt`

The labels `File1` to `File30` map directly to that file.

## Scripts

- `run_ablation_unified30.py`: one-shot driver. Runs MS1 aggregation, optional MS1 matplotlib plotting, MS2 summary generation, and MS2 plotting.
- `ablation_result_aggregation_unified30.py`: builds MS1 ablation summary tables and native SVG/PNG/PDF figures from A0-A6 and F1 per-file CSV inputs.
- `plot_ms1_ablation_matplotlib_unified30.py`: optional matplotlib implementation for selected MS1 plots. Use the `pyopenms-3.5` environment on this Windows host.
- `ms2_ablation_b1_unified30.py`: filters the MS2 all-file input table to the unified 30-file scope and writes MS2 summary tables.
- `plot_ms2_ablation_unified30.py`: generates MS2 ablation figures in SVG/PNG/PDF.
- `ms2_svg_plot_helpers.py`: lightweight SVG/PNG/PDF drawing helpers for MS2 plots.
- `split_ms2_sci_main_figure_unified30.py`: helper for splitting the combined MS2 science panel if needed.
- `ablation_io_helpers.py`: shared small I/O helpers.
- `fresh_rerun/run_component_sweep_single.py`: fresh-runs A0-A6 component ablation on one mzML file.
- `fresh_rerun/run_component_sweep_manifest.py`: fresh-runs A0-A6 component ablation from a CSV or JSON manifest, scheduled by file size in descending order.
- `fresh_rerun/run_ms1_intensity_isolation_single.py`: fresh-runs A0 and A7 on one mzML file.
- `fresh_rerun/run_ms1_intensity_isolation_manifest.py`: fresh-runs the same A0/A7 workflow from a CSV or JSON manifest, scheduled by file size in descending order.
- `fresh_rerun/ms1_ablation_fresh_common.py`: shared mzML loading, MS1/MS2 section compression, MS1 track construction, variant definitions, and round-trip checks for the fresh rerun scripts.

## One-shot CLI

```powershell
$env:REPO_ROOT = "<path-to-this-repository>"
$env:TRACKCODEC_PYTHON = "<python-with-trackcodec-dependencies>"
$env:PLOT_PYTHON = "<python-with-matplotlib-pandas-numpy>"

& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\run_ablation_unified30.py"
```

`run_ablation_unified30.py` uses `PLOT_PYTHON` for the figure-generating
steps when that variable is set. This avoids a Windows-local Matplotlib PDF
backend crash seen in `trackcodec-py311` (`0xC06D007F`) while keeping
TrackCodec-only table generation available in the main environment.

To regenerate the optional matplotlib plots directly:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\plot_ms1_ablation_matplotlib_unified30.py"
```

## Environment and Toolchain

The primary public workflow is table-driven: it aggregates included per-file
ablation CSV inputs and regenerates figures without requiring raw mzML files.
The optional `scripts/fresh_rerun/` workflow reruns the A0-A6 component
ablation and the MS1 A0/A7 intensity-isolation ablation from raw mzML files.

| Tool | Role | Version used on the manuscript workstation | Source status |
| --- | --- | --- | --- |
| Python | Aggregation and plotting runtime | 3.11.15 | Unmodified. |
| pandas/numpy/scipy | Table aggregation and paired statistics | pandas 3.0.3, numpy 2.4.6, scipy 1.17.1 | Unmodified. |
| Matplotlib | Optional editable PDF/PNG/SVG figure generation | 3.10.9 | Unmodified. |
| pyteomics | Optional fresh mzML loading for MS1 ablation reruns | 4.7.0 | Unmodified. |
| TrackCodec source tree | Core compressor, MS1 track construction, and ablation-only A7 mode | This repository | Includes `stackzdpd_passthrough`, an explicit ablation-only MS1 intensity mode used to isolate the intensity codec contribution. |

The ablation code was copied into this public bundle and cleaned for stable
paths and reproducible outputs. No external compressor or search engine is
required for figure reproduction. Raw-mzML fresh reruns require only the
TrackCodec Python dependencies and the mzML files supplied by the user.

`run_ablation_unified30.py` is a one-shot driver. It currently does not expose
its own `--help` parser; passing `--help` still runs the workflow because the
script forwards fixed commands to the component scripts. Use the individual
commands below for parameter-level control.

## Individual CLI

MS1 native aggregation and figures:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\ablation_result_aggregation_unified30.py" `
  --nmi-ablation `
  --component-csv "$env:REPO_ROOT\public_validation_bundle\ablation\inputs\component_A0_A6_per_file_input.csv" `
  --f1-csv `
    "$env:REPO_ROOT\public_validation_bundle\ablation\inputs\ablation_F1_full8_fresh_per_file.csv" `
    "$env:REPO_ROOT\public_validation_bundle\ablation\inputs\ablation_F1_selected_fresh_per_file.csv" `
    "$env:REPO_ROOT\public_validation_bundle\ablation\inputs\ablation_F1_new_files_fresh_per_file.csv" `
  --output-dir "$env:REPO_ROOT\public_validation_bundle\ablation\outputs\ms1_ablation_unified30"
```

MS1 parameters:

| Option | Meaning and reason |
| --- | --- |
| `--nmi-ablation` | Enables the manuscript NMI-style ablation labeling and plot set. |
| `--component-csv` | Input table with A0-A6 per-file MS1 ablation components. |
| `--f1-csv` | One or more CSV files containing the A7/no-intensity-delta rows; multiple sources are merged into the unified 30-file scope. |
| `--output-dir` | Destination for MS1 ablation tables and figures. |

## Optional Fresh Raw-mzML Reruns

The table-driven figures include A0-A6 component ablations and the A7/F1
intensity-codec-isolation rows. The scripts below allow both parts to be
rerun from raw mzML files.

### A0-A6 Component Sweep

A0-A6 component variants:

| Variant | Meaning | MS1 intensity mode | Backend | m/z precision | Sidecar policy |
| --- | --- | --- | --- | --- | --- |
| A0 | Full TrackCodec MS1 configuration | `szdpd_xdelta_equalfidelity` | `zstd-9` | 6 | enabled |
| A1 | No cross-scan intensity delta | `szdpd` | `zstd-9` | 6 | enabled |
| A2 | No equal-fidelity planner | `szdpd_xdelta` | `zstd-9` | 6 | enabled |
| A3 | Strict lossless intensity delta | `strict_lossless_xdelta` | `zstd-9` | 6 | enabled |
| A4 | No full-scan sidecar inside codec | `szdpd_xdelta_equalfidelity` | `zstd-9` | 6 | external fair sidecar accounting |
| A5 | zlib backend | `szdpd_xdelta_equalfidelity` | `zlib` | 6 | enabled |
| A6 | faster zstd backend | `szdpd_xdelta_equalfidelity` | `zstd-3` | 6 | enabled |

Single-file A0-A6 rerun:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\fresh_rerun\run_component_sweep_single.py" `
  --input "<path-to-input.mzML>" `
  --output-dir "<output-dir>" `
  --index 1 `
  --dataset "<dataset-label>" `
  --max-ms1-scans 0 `
  --max-ms2-scans 0 `
  --variants A0 A1 A2 A3 A4 A5 A6
```

Manifest-based A0-A6 rerun:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\fresh_rerun\run_component_sweep_manifest.py" `
  --manifest "<path-to-manifest.csv-or.json>" `
  --output-dir "<output-dir>" `
  --workers 8 `
  --max-ms1-scans 0 `
  --max-ms2-scans 0 `
  --variants A0 A1 A2 A3 A4 A5 A6 `
  --resume
```

The merged A0-A6 per-file table is written to
`component_sweep/ablation_per_file.csv`. This table can be passed to
`ablation_result_aggregation_unified30.py` as `--component-csv`.

### A7/F1 Intensity-Isolation Rerun

A7/F1 was not only a plotting label: it uses the ablation-only TrackCodec
intensity mode `stackzdpd_passthrough` in
`production/ms1/cross_scan_codec.py`. This mode preserves the normal MS1 track
construction and m/z representation, but forces all MS1 intensity islands to be
stored as full rounded float64 values instead of using the TrackCodec intensity
delta path. It is therefore a diagnostic mode for attributing the compression
benefit of the intensity codec and is not the default TrackCodec compressor.

Single-file A7/F1 rerun:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\fresh_rerun\run_ms1_intensity_isolation_single.py" `
  --input "<path-to-input.mzML>" `
  --output-dir "<output-dir>" `
  --index 1 `
  --dataset "<dataset-label>" `
  --max-ms1-scans 0
```

Manifest-based A7/F1 rerun:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\fresh_rerun\run_ms1_intensity_isolation_manifest.py" `
  --manifest "<path-to-manifest.csv-or.json>" `
  --output-dir "<output-dir>" `
  --workers 8 `
  --max-ms1-scans 0 `
  --resume
```

Manifest rows may be CSV or JSON objects with one of `path`, `file_path`, or
`mzml_path`. Optional columns such as `file`, `dataset`, `vendor`, and `index`
are copied into the output tables. Files are sorted by byte size in descending
order before scheduling.

Fresh-rerun outputs:

- `per_file/*/ms1_intensity_isolation_per_file.csv`
- `per_file/*/ms1_intensity_isolation_summary.json`
- `ms1_intensity_isolation_per_file.csv`
- `ms1_intensity_isolation_summary.csv`
- `ms1_intensity_isolation_errors.csv`

MS2 summary only:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\ms2_ablation_b1_unified30.py" `
  --table-dir "$env:REPO_ROOT\public_validation_bundle\ablation\inputs" `
  --output-dir "$env:REPO_ROOT\public_validation_bundle\ablation\outputs\ms2_ablation_unified30"
```

MS2 summary parameters:

| Option | Meaning and reason |
| --- | --- |
| `--table-dir` | Directory containing the included MS2 ablation input tables and file-scope list. |
| `--output-dir` | Destination for filtered MS2 summary tables. |

MS2 figures only:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\plot_ms2_ablation_unified30.py"
```

MS2 plotting is driven by tables under
`outputs/ms2_ablation_unified30/tables/` and writes editable SVG/PDF plus PNG
figures under `outputs/ms2_ablation_unified30/plots/`.

## Primary Outputs

MS1 figures:

- `outputs/ms1_ablation_unified30/plots/nmi_ablation_ms1_cr_A0_A6_F1.pdf`
- `outputs/ms1_ablation_unified30/plots/nmi_ablation_ms1_size_delta_vs_A0.pdf`
- `outputs/ms1_ablation_unified30/plots/nmi_ablation_F1_per_file_size_penalty_vs_A0.pdf`
- `outputs/ms1_ablation_unified30/plots/nmi_ablation_a0_vs_f1_ms1_cr_all_files.pdf`
- `outputs/ms1_ablation_unified30/plots/nmi_ablation_per_file_delta_heatmap.pdf`
- `outputs/ms1_ablation_unified30/plots/nmi_ablation_ms1_cr_boxplot_linked_files.pdf`

MS2 figures:

- `outputs/ms2_ablation_unified30/plots/ms2_ablation_sci_panel_A_ms2_cr.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_sci_panel_B_distribution.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_sci_main_figure.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_sci_archive_penalty_paired.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_summary_cr_b0_vs_b1.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_per_file_archive_delta_vs_b0.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_b0_vs_b1_ms2_cr_scatter.pdf`
- `outputs/ms2_ablation_unified30/plots/ms2_ablation_aggregate_archive_bytes.pdf`

## Suggested Manuscript Wording

Benchmark uses 36 files because it reports end-to-end compression performance over the complete benchmark. Ablation uses the unified 30-file subset because all compared ablation variants are available and directly comparable for those files. Thus benchmark emphasizes coverage, while ablation emphasizes paired variant-to-variant attribution.

