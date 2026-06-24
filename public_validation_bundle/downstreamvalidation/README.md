# Downstream Validation Bundle

This directory contains the public downstream validation code and outputs. It includes both figure reproduction scripts and the heavy workflow entry points for stream-loss/XIC extraction and DIA-NN/MSFragger validation.

## Directory Layout

- `inputs/`: documentation snippets and file-scope manifests.
- `inputs/manifests/`: explicit file lists used by the public bundle.
- `inputs/assets/`: expected location for FASTA, target-decoy FASTA, and MSFragger parameter files if the full search workflow is rerun.
- `scripts/`: copied and cleaned downstream validation scripts.
- `outputs/search_validation_figures/`: DIA-NN, DDA IonQuant, PSM recovery, combined recovery tables and figures.
- `outputs/stream_loss_downstream24_top2500/`: Top-2500 XIC target table, Top-K gradient summaries, and XIC figures.

## File Scope Manifests

- `inputs/manifests/current36_search_coverage.csv`: current 36-file search coverage table and notes.
- `inputs/manifests/diann_recovery_files.csv`: files with DIA-NN precursor/peptide/protein-group recovery summaries.
- `inputs/manifests/dda_psm_recovery_files.csv`: files used for DDA PSM and peptide-ion recovery audit.
- `inputs/manifests/downstream24_xic_pairs.csv`: 24-file XIC extraction pair manifest with original, archive, and reconstructed mzML paths.

The public manifests intentionally keep sanitized placeholders such as
`<MSCODEC_ROOT>`, `<TRACKCODEC_PARENT>`, and `<TRACKCODEC_PUBLIC_RELEASE>`.
These preserve the validated file scope without exposing a private machine
layout.

## Scripts

### One-shot figure driver

- `run_downstream_public_figures.py`: regenerates the public downstream figures from included summary tables. It calls DIA-NN recovery plotting, DDA PSM recovery plotting, combined DDA/DIA recovery plotting, Top-K XIC gradient plotting, and PDF companion checks.

### Stream-loss and XIC extraction

- `validate_trackcodec_outputs.py`: main stream-loss validation workflow. It can write pair manifests, check external tools, decode TrackCodec archives, compare roundtrip mzML arrays, extract Top-K XIC target-level metrics, and summarize results. The default path table is `inputs/manifests/downstream24_xic_pairs.csv`; the default output root is `outputs/stream_loss_downstream24_top2500`.
- `plot_xic_topk_gradient_from_maxk.py`: aggregates a single max-K target table, e.g. Top-2500, into Top-500/1000/1500/2000/2500 summaries and figures without rescanning mzML.
- `plot_xic_topk_gradient.py`: earlier Top-K plotting helper retained for compatibility.
- `filter_stream_loss_current36.py`, `merge_stream_loss_results.py`, `make_path_table_from_manifest.py`, `build_current36_recon_manifest.py`, `diagnose_mz_outlier.py`: utilities for constructing/diagnosing stream-loss manifests and merged tables.

### DIA-NN and MSFragger/Philosopher/IonQuant workflows

- `run_full_downstream_validation.py`: heavy workflow entry point for DIA-NN and DDA MSFragger/Philosopher/IonQuant validation. It discovers original/reconstructed pairs, writes manifests, launches DIA-NN for DIA/AIF files, launches MSFragger/Philosopher/IonQuant for DDA/ETD files, and writes comparison summaries. It requires external tools on PATH and FASTA/parameter assets.
- `run_search_validation_all20.py`: earlier isolated per-file search runner retained for compatibility and comparison.
- `recover_diann_single_run_summary.py`: recover/summarize DIA-NN single-run outputs into pairwise tables.
- `recover_dda_ionquant_pairwise.py`: recover/summarize DDA IonQuant pairwise outputs.
- `plot_search_validation_results.py`: broader search-validation plotting script for DIA-NN and DDA/IonQuant result families.

### PSM/precursor recovery

- `summarize_psm_precursor_recovery.py`: joins existing search-engine outputs to compute DDA PSM recovery, peptide-ion recovery, and DIA precursor-style recovery summaries.
- `plot_dda_psm_recovery_results.py`: generates DDA PSM/peptide-ion recovery heatmaps, gain/loss bars, score agreement heatmaps, and recovery-vs-count scatter plots.
- `plot_diann_recovery_results.py`: generates DIA-NN precursor, peptide, and protein-group recovery plots.
- `plot_combined_search_recovery_results.py`: combines DDA and DIA recovery levels in NMI-style overview figures.

### Other retained scripts

- `run_openms_feature_validation_all20.py`, `run_openms_feature_validation_all20_process_pool.py`: OpenMS feature-level validation runners retained from the earlier workflow.
- `plot_validation_results.py`: stream-loss/OpenMS summary plotting helper.
- `summarize_current36_search_coverage.py`: creates the current 36-file search coverage audit table.
- `ensure_pdf_outputs.py`: ensures PNG figures have PDF companions.

## One-shot Figure Reproduction CLI

```powershell
$env:REPO_ROOT = "<path-to-this-repository>"
$env:TRACKCODEC_PYTHON = "<python-with-trackcodec-dependencies>"
$env:PLOT_PYTHON = "<python-with-matplotlib-pandas-numpy>"

& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\run_downstream_public_figures.py"
```

This command regenerates:

- DIA-NN recovery figures.
- DDA PSM / peptide-ion recovery figures.
- Combined DDA/DIA molecular recovery figures.
- Top-K XIC gradient figures from the existing Top-2500 target table.
- PDF companions for PNG outputs.

## External Toolchain

Figure reproduction from included tables does not launch external search
software. Heavy reruns require the following tools on `PATH` or explicitly
configured by the script environment:

| Tool | Validation role | Version used on the manuscript workstation | Installation and source status |
| --- | --- | --- | --- |
| DIA-NN | DIA/AIF precursor, peptide, and protein-group search/quantification recovery | 2.5.1 Academia | Installed from the official binary distribution; no source modification. |
| MSFragger | DDA/ETD peptide-spectrum matching | 4.4.1 | Installed from the official binary distribution; no source modification. |
| Philosopher | DDA FDR filtering and peptide/protein reporting after MSFragger | v5.1.0, build 202311202158 | Installed from the official binary distribution; no source modification. |
| IonQuant | DDA label-free quantification and precursor/peptide/protein quantitative recovery | 1.11.20 | Installed from the official binary distribution; no source modification. |
| OpenMS/PeakPickerHiRes | Optional DDA peak-picking fallback when MSFragger requires centroided input | OpenMS 3.5.0 | Installed from the official binary distribution; no source modification. |
| TrackCodec | Decode archives and produce reconstructed mzML for downstream comparison | This repository | Public source tree; no downstream-specific source modification. |

The downstream validation code itself was reorganized for public release, but
the external search engines listed above were not patched. The only patched
third-party compressors in this project are mspack/MassComp/AirdPro-family
benchmark tools, documented in the benchmark README.

## Individual Figure CLI

DIA-NN recovery:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\plot_diann_recovery_results.py"
```

DDA PSM / peptide-ion recovery:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\plot_dda_psm_recovery_results.py"
```

Combined DDA/DIA recovery:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\plot_combined_search_recovery_results.py"
```

Top-K XIC gradient from the included Top-2500 table:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\plot_xic_topk_gradient_from_maxk.py" `
  --target-table "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500\tables\xic_target_metrics.csv" `
  --out-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --top-k 500 1000 1500 2000 2500 `
  --plot-error-vs-signal
```

## Heavy Rerun CLI

Top-2500 XIC extraction. This reuses reconstructed mzML paths from `inputs/manifests/downstream24_xic_pairs.csv` and does not require repeated Top-K scans for smaller K values:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\validate_trackcodec_outputs.py" `
  --path-table "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\inputs\manifests\downstream24_xic_pairs.csv" `
  --output-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --stages xic summarize `
  --xic-top-k 2500 `
  --xic-workers 8
```

Full DIA-NN / MSFragger / Philosopher / IonQuant manifest dry-run:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\run_full_downstream_validation.py" `
  --validation-results "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --result-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\full_downstream" `
  --stages all `
  --dry-run `
  --max-diann-files 2 `
  --max-dda-files 2
```

The `manifest` stage alone writes pair, tool, and asset audit tables. Use
`--stages all --dry-run` to additionally plan DIA-NN and DDA
MSFragger/Philosopher/IonQuant runs without launching external tools.

Public tables may contain sanitized placeholders. The downstream scripts
resolve these placeholders before checking file existence:

| Placeholder | Default resolution | Override environment variable |
| --- | --- | --- |
| `<MSCODEC_ROOT>` | workspace root containing the benchmark data | `MSCODEC_ROOT` |
| `<TRACKCODEC_PARENT>` | directory containing `TrackCodec_public_release` | `TRACKCODEC_PARENT` |
| `<TRACKCODEC_ROOT>` / `<TRACKCODEC_PUBLIC_RELEASE>` | `TrackCodec_public_release` | `TRACKCODEC_ROOT` |

For real reruns, place or pass these assets:

- `inputs/assets/human_reviewed_UP000005640_uniprot_20260425.fasta`
- `inputs/assets/human_reviewed_UP000005640_uniprot_20260425.target_decoy.fasta`
- `inputs/assets/closed_dda_target_decoy_fragger.params`

These assets are not bundled in the public repository. They are downstream
workflow inputs rather than TrackCodec implementation files, and users may need
to substitute an institution-approved or updated database snapshot.

## Data Availability

The exact downstream file scope is documented by file name in:

- `../FIGURE_LINKS_AND_FILE_LISTS.md`
- `inputs/manifests/downstream24_xic_pairs.csv`
- `inputs/manifests/current36_search_coverage.csv`

The cohort spans public proteomics repository downloads plus local normalized
derivatives produced during manuscript preparation. In practice, users should
locate the public datasets by searching the exact file names in PRIDE /
ProteomeXchange, MassIVE, jPOST, or the dataset-specific repository linked by
the corresponding publication.

The public release does not include all original mzML files, reconstructed
mzML files, or vendor raw files. Use the included repository paths, manifests,
and public links in this bundle to locate the available source data.

For a full heavy rerun, all of the following must be available together:

- original mzML files;
- reconstructed mzML files, or the TrackCodec archives plus a decode step;
- FASTA and target-decoy FASTA;
- MSFragger parameter template;
- external search tools on `PATH`.

### Heavy rerun parameter notes

`validate_trackcodec_outputs.py` is the codec/downstream bridge. Important
parameters:

| Option | Meaning and reason |
| --- | --- |
| `--path-table` | CSV manifest with original mzML, archive, and reconstructed mzML paths. This defines the file scope. |
| `--trackcodec-root` | TrackCodec source tree used for archive decoding. |
| `--output-root` | Destination for decoded mzML, roundtrip summaries, XIC target tables, and plots. |
| `--stages` | Selects `manifest`, `tools`, `decode`, `roundtrip`, `xic`, `summarize`, or `all`. Use `xic summarize` when reconstructed mzML already exists. |
| `--binary-compression` | Compression mode for reconstructed mzML: `preserve_template`, `none`, or `zlib`. |
| `--section-workers`, `--ms2-segment-workers` | TrackCodec decode worker counts. |
| `--write-per-spectrum` | Writes detailed per-spectrum validation CSVs; expensive for large cohorts. |
| `--xic-top-k` | Number of high-signal MS1 targets selected per file for XIC comparison. The manuscript Top-K gradient starts from Top-2500 and summarizes smaller K values without rescanning mzML. |
| `--xic-workers` | File-level workers for XIC extraction. |
| `--xic-peaks-per-scan`, `--xic-bin-width-da`, `--xic-ppm` | XIC target discovery and extraction tolerances. |
| `--reuse-recon-root` | Reuses an existing `recon/` tree instead of decoding archives again. This is useful for downstream reruns but does not test archive reconstruction itself. |

`run_full_downstream_validation.py` launches search/quantification workflows.
Important parameters:

| Option | Meaning and reason |
| --- | --- |
| `--validation-results` | Existing TrackCodec validation/XIC root used to find original/reconstructed mzML pairs. |
| `--result-root` | Destination for search manifests, logs, DIA-NN outputs, DDA search outputs, and comparison tables. |
| `--stages manifest diann dda all` | `manifest` writes file plans only; `diann` runs DIA/AIF searches; `dda` runs MSFragger/Philosopher/IonQuant; `all` runs the complete heavy workflow. |
| `--dry-run` | Writes manifests and planned rows without launching external tools. This is the recommended publication-bundle smoke test. |
| `--scope` | Restricts file discovery to the intended biological file scope. |
| `--include-aif-diann` | Includes AIF-like files in the DIA-NN candidate set when appropriate. |
| `--only` | Filters file names by substring; repeatable for focused subset runs. |
| `--max-diann-files`, `--max-dda-files` | Limits heavy reruns for smoke tests. |
| `--threads`, `--diann-threads`, `--dda-threads` | Thread controls for external tools. |
| `--fasta`, `--target-decoy-fasta`, `--msfragger-params` | Required assets for real DDA searches. |
| `--skip-msfragger`, `--skip-philosopher`, `--skip-ionquant`, `--skip-plots` | Allows partial reruns from cached intermediate outputs. |
| `--timeout-sec` | External command timeout. |

## Primary Outputs

Search validation:

- `outputs/search_validation_figures/tables/`
- `outputs/search_validation_figures/plots/diann_recovery/`
- `outputs/search_validation_figures/plots/dda_psm_recovery/`
- `outputs/search_validation_figures/plots/search_recovery_combined/`
- `outputs/search_validation_figures/plots/diann/`
- `outputs/search_validation_figures/plots/dda_ionquant/`

Top-K XIC:

- `outputs/stream_loss_downstream24_top2500/tables/xic_target_metrics.csv`
- `outputs/stream_loss_downstream24_top2500/tables/xic_topk_gradient_summary.csv`
- `outputs/stream_loss_downstream24_top2500/plots/xic/`

## Notes

- The figure-only workflow starts from included summary tables and does not relaunch DIA-NN, MSFragger, Philosopher, IonQuant, or mzML XIC extraction.
- Heavy workflows are included and can be rerun if the raw mzML/reconstructed mzML/protein database/toolchain paths are available.
- PSM recovery code is included: use `summarize_psm_precursor_recovery.py` for table generation and `plot_dda_psm_recovery_results.py` for figures.

