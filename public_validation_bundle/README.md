# TrackCodec Public Validation Bundle

This bundle collects the public reproduction code for the manuscript validation figures. It is intentionally organized without date-stamped directory names.

## Layout

- `ablation/`: MS1/MS2 ablation tables, scripts, and figures.
- `downstreamvalidation/`: stream-loss, XIC, DIA-NN, MSFragger/Philosopher/IonQuant, and PSM recovery validation tables, scripts, and figures.
- `FIGURE_LINKS_AND_FILE_LISTS.md`: figure paths used for manuscript assembly plus the exact file scopes.

All generated results are written under each module's `outputs/` directory.

## Python Environments

Set the repository root and Python executables:

```powershell
$env:REPO_ROOT = "<path-to-this-repository>"
$env:TRACKCODEC_PYTHON = "<python-with-trackcodec-dependencies>"
$env:PLOT_PYTHON = "<python-with-matplotlib-pandas-numpy>"
```

During preparation, `TRACKCODEC_PYTHON` referred to a Python 3.11 environment
with TrackCodec dependencies, and `PLOT_PYTHON` referred to a Matplotlib/pandas
environment used for figure generation.

DIA-NN, MSFragger, Philosopher, IonQuant, and OpenMS are expected on `PATH` only when rerunning the expensive search/extraction workflows. Figure reproduction from the included tables does not require launching those tools.

On this Windows host, `trackcodec-py311` can hit a Matplotlib PDF backend
crash (`0xC06D007F`) when writing some editable PDF ablation figures. The
bundled ablation one-shot driver therefore uses `PLOT_PYTHON` for the
figure-generating steps when that variable is set. A practical setup is:

```powershell
$env:TRACKCODEC_PYTHON = "<python-3.11-with-trackcodec-dependencies>"
$env:PLOT_PYTHON = "<python-with-matplotlib-pandas-numpy>"
```

## One-shot Figure Reproduction

Ablation figures:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\ablation\scripts\run_ablation_unified30.py"
```

Downstream validation figures:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\run_downstream_public_figures.py"
```

## Heavy Workflow Entry Points

These are included for reproducibility, but they are not run by the figure-only commands above.

Top-K XIC extraction from mzML pairs:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\validate_trackcodec_outputs.py" `
  --path-table "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\inputs\manifests\downstream24_xic_pairs.csv" `
  --output-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --stages xic summarize `
  --xic-top-k 2500 `
  --xic-workers 8
```

Full DIA-NN / MSFragger / Philosopher / IonQuant validation planning or rerun:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\run_full_downstream_validation.py" `
  --validation-results "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --result-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\full_downstream" `
  --stages manifest `
  --dry-run
```

For real search-engine reruns, provide or copy the FASTA and parameter files under `downstreamvalidation/inputs/assets/`, or pass `--fasta`, `--target-decoy-fasta`, and `--msfragger-params` explicitly.

Those FASTA and parameter assets are intentionally not bundled in this public
repository. They are downstream workflow inputs rather than TrackCodec source
files, and users may need to substitute institution-approved or newer database
snapshots when rerunning searches.

## Important Notes

- The bundle does not mutate the original working directories.
- Some copied result tables keep absolute provenance paths to the original mzML, reconstructed mzML, or internal work directories. These are data provenance fields, not output locations.
- The official figure outputs are under `ablation/outputs/` and `downstreamvalidation/outputs/`.
- `FIGURE_LINKS_AND_FILE_LISTS.md` is the quickest index for manuscript figure insertion.
- Heavy-workflow manifests contain sanitized placeholders such as
  `<MSCODEC_ROOT>` and `<TRACKCODEC_PARENT>`. They preserve the validated file
  scope, but they are not by themselves a self-contained original-data release.
- The benchmark and validation cohorts are assembled from public repository
  downloads plus local normalized derivatives. Use the exact file names in
  `FIGURE_LINKS_AND_FILE_LISTS.md` and the manifests to locate the public
  downloads and bundled repository paths.

