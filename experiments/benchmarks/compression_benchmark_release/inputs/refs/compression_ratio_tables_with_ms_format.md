# Compression Ratio Tables with MS1/MS2 Format

- Source scope: `8` Stack-ZDPD validation files.
- Format source: `<MSCODEC_ROOT>/7_xic_DIA/7.1_paper/dataset_header_inventory_report.md`.
- `File13_SA1` has no MS2 spectra in the inventory, so its format is reported as `MS1 profile / MS2 none`.

## MS1 CR

| File | MS1/MS2 format | ZDPD baseline | Stack-ZDPD baseline | Ours eqfidelity | Ours strict_q6 | Ours archive_fidelity |
|---|---|---:|---:|---:|---:|---:|
| File13_SA1 | MS1 profile / MS2 none | 6.837 | 6.755 | 8.689 | 7.113 | 8.317 |
| File14_LFQ_Orbitrap_AIF_Human_01 | MS1 centroid / MS2 centroid | 2.569 | 2.886 | 1.944 | 1.973 | 2.681 |
| File16_LFQ_TTOF5600_DDA_Human_01 | MS1 profile / MS2 profile | 13.977 | 10.896 | 10.346 | 9.745 | 9.434 |
| File3_QE-HFX-20190719_50cm_60min_OFe4_2 | MS1 mixed / MS2 centroid | 5.948 | 4.990 | 5.963 | 5.835 | 6.022 |
| File4_QE-HFX-20190719_50cm_60min_Fr1 | MS1 profile / MS2 centroid | 5.961 | 5.540 | 6.876 | 6.282 | 6.679 |
| File5_S8184TPST_01 | MS1 profile / MS2 profile | 6.164 | 5.494 | 6.817 | 6.229 | 6.602 |
| File6_Negative_000333 | MS1 profile / MS2 profile | 6.695 | 6.532 | 8.349 | 6.746 | 8.058 |
| Set_1_F2 | MS1 centroid / MS2 centroid | 3.190 | 3.333 | 2.271 | 1.958 | 3.227 |
| Mean (8 files) | mixed benchmark | 6.418 | 5.803 | 6.407 | 5.735 | 6.377 |

## MS2 CR

| File | MS1/MS2 format | ZDPD baseline | Stack-ZDPD baseline | Ours eqfidelity |
|---|---|---:|---:|---:|
| File13_SA1 | MS1 profile / MS2 none | 0.000 | 0.000 | 0.000 |
| File14_LFQ_Orbitrap_AIF_Human_01 | MS1 centroid / MS2 centroid | 2.509 | 2.904 | 3.266 |
| File16_LFQ_TTOF5600_DDA_Human_01 | MS1 profile / MS2 profile | 9.814 | 10.332 | 11.771 |
| File3_QE-HFX-20190719_50cm_60min_OFe4_2 | MS1 mixed / MS2 centroid | 2.697 | 3.045 | 3.435 |
| File4_QE-HFX-20190719_50cm_60min_Fr1 | MS1 profile / MS2 centroid | 2.326 | 2.761 | 3.061 |
| File5_S8184TPST_01 | MS1 profile / MS2 profile | 5.707 | 5.571 | 6.208 |
| File6_Negative_000333 | MS1 profile / MS2 profile | 5.743 | 6.402 | 7.233 |
| Set_1_F2 | MS1 centroid / MS2 centroid | 3.225 | 3.523 | 3.818 |
| Mean (8 files) | mixed benchmark | 4.003 | 4.317 | 4.849 |

## Overall CR

| File | MS1/MS2 format | ZDPD container | Stack-ZDPD container | Ours eqfidelity |
|---|---|---:|---:|---:|
| File13_SA1 | MS1 profile / MS2 none | 6.927 | 6.843 | 8.808 |
| File14_LFQ_Orbitrap_AIF_Human_01 | MS1 centroid / MS2 centroid | 4.788 | 5.475 | 6.274 |
| File16_LFQ_TTOF5600_DDA_Human_01 | MS1 profile / MS2 profile | 11.129 | 10.656 | 11.365 |
| File3_QE-HFX-20190719_50cm_60min_OFe4_2 | MS1 mixed / MS2 centroid | 4.501 | 4.495 | 5.248 |
| File4_QE-HFX-20190719_50cm_60min_Fr1 | MS1 profile / MS2 centroid | 6.003 | 5.842 | 7.175 |
| File5_S8184TPST_01 | MS1 profile / MS2 profile | 6.311 | 5.752 | 6.981 |
| File6_Negative_000333 | MS1 profile / MS2 profile | 7.832 | 7.851 | 9.981 |
| Set_1_F2 | MS1 centroid / MS2 centroid | 3.966 | 4.235 | 3.666 |
| Mean (8 files) | mixed benchmark | 6.432 | 6.394 | 7.437 |
