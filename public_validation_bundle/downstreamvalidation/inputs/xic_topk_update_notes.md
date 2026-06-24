# XIC Top-K update notes

The downstream validation bundle includes Top-K XIC sensitivity plots derived
from the included Top-2500 target table:

```powershell
& $env:PLOT_PYTHON -B `
  "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\scripts\plot_xic_topk_gradient_from_maxk.py" `
  --target-table "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500\tables\xic_target_metrics.csv" `
  --out-root "$env:REPO_ROOT\public_validation_bundle\downstreamvalidation\outputs\stream_loss_downstream24_top2500" `
  --top-k 500 1000 1500 2000 2500 `
  --plot-error-vs-signal
```

This command writes:

- `outputs/stream_loss_downstream24_top2500/tables/xic_topk_gradient_summary.csv`
- `outputs/stream_loss_downstream24_top2500/tables/xic_topk_gradient_all_file_summary.csv`
- `outputs/stream_loss_downstream24_top2500/plots/xic/xic_topk_gradient_p95_area_error.pdf`
- `outputs/stream_loss_downstream24_top2500/plots/xic/xic_topk_gradient_pearson_p05.pdf`
- `outputs/stream_loss_downstream24_top2500/plots/xic/xic_topk_gradient_apex_shift_p95.pdf`
- `outputs/stream_loss_downstream24_top2500/plots/xic/top500_2500_xic_area_error_vs_signal_all_files.pdf`

The Top-K gradient is a sensitivity analysis: the main stream-loss result uses
high-signal XIC targets, and the gradient checks whether fidelity remains stable
as progressively weaker targets are included.

