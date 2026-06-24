# Stream-loss validation notes

This note describes the stream-loss validation workflow included in this public
bundle. The runnable public entry point is:

```powershell
& $env:TRACKCODEC_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\validate_trackcodec_outputs.py" `
  --path-table "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\inputs\manifests\downstream24_xic_pairs.csv" `
  --output-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --stages xic summarize `
  --xic-top-k 2500 `
  --xic-workers 8
```

The figure-only workflow starts from the included tables and does not decode
archives or rescan mzML files:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\run_downstream_public_figures.py"
```

Outputs are written under:

- `outputs/stream_loss_downstream24_top2500/tables/`
- `outputs/stream_loss_downstream24_top2500/plots/xic/`

The historical internal working paths used to generate the bundled tables are
preserved only inside data provenance fields in the manifest and result tables.
They are not required for reproducing the public figures from included tables.

