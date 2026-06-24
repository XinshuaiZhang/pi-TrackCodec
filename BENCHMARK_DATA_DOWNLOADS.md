# Benchmark Dataset Downloads and Local Placement

This is the benchmark-specific entry point for the source data used by the
TrackCodec compression benchmark. The public release includes the benchmark
scripts, summary tables, combined tables, and figures, but it does not re-host
the large original vendor files or derived mzML inputs.

The per-file source links below are the same formal 36-file scope used by the
current benchmark figures. `DATA_DOWNLOADS.md` contains the same public source
links in the shared benchmark/downstream source table.

## Quick Answer

The benchmark figures can be regenerated from the CSV tables already included
in the release without downloading raw data:

```text
experiments/benchmarks/compression_benchmark_release/inputs/whole_file_summaries/
experiments/benchmarks/compression_benchmark_release/outputs/combined_release/
```

Rerunning the full compression benchmark requires restoring or recreating the
derived mzML input files at the local paths encoded in the summary tables. Use
`<MSCODEC_ROOT>` as a placeholder for the local workspace root.

The benchmark input mzML roots are:

```text
<MSCODEC_ROOT>\benchmark\data_full8\
<MSCODEC_ROOT>\benchmark\data_StackZDPD\
```

The public links in this document point to the original vendor/source files
used to create those mzML inputs. The exact mzML files are local derived files,
not public files downloaded directly from the repository links.

## Formal Benchmark Scope

The release keeps three whole-file summary inputs:

| Summary CSV | Dataset key used by plotting | Unique mzML files before exclusions | Formal files used in combined figures | Expected local mzML root |
|---|---|---:|---:|---|
| `full8_summary.csv` | `full8` | 24 | 23 | `<MSCODEC_ROOT>\benchmark\data_full8\` |
| `data_stackzdpd_summary.csv` | `data_stackzdpd` | 11 | 11 | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\` |
| `data_stackzdpd_aif_summary.csv` | `data_stackzdpd` | 2 | 2 | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\` |

The formal combined benchmark has 36 mzML files. The 37th entry present in the
raw `full8_summary.csv` input is intentionally excluded by the plotting CLI:

```text
01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML
```

The exclusion is encoded in the documented plotting command:

```powershell
python -B experiments\benchmarks\compression_benchmark_release\scripts\plot_combined_benchmark_summaries_svg.py `
  --input full8=experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\full8_summary.csv `
  --input data_stackzdpd=experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\data_stackzdpd_summary.csv `
  --input data_stackzdpd=experiments\benchmarks\compression_benchmark_release\inputs\whole_file_summaries\data_stackzdpd_aif_summary.csv `
  --exclude-file 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML `
  --vendor-size-table experiments\benchmarks\compression_benchmark_release\inputs\refs\compression_ratio_tables_with_ms_format.md
```

## Public Source Roots

For SCIEX WIFF inputs, download both `.wiff` and `.wiff.scan` files.

| Repository / database | Accession | Project page | File directory |
|---|---|---|---|
| MetaboLights | `MTBLS733` | <https://www.ebi.ac.uk/metabolights/MTBLS733> | <https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public/MTBLS733/FILES/> |
| PRIDE Archive / ProteomeXchange | `PXD028735` | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/> |
| PRIDE Archive / ProteomeXchange | `PXD025142` | <https://www.ebi.ac.uk/pride/archive/projects/PXD025142> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2021/04/PXD025142/> |
| PRIDE Archive / ProteomeXchange | `PXD004712` | <https://www.ebi.ac.uk/pride/archive/projects/PXD004712> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2018/10/PXD004712/> |
| PRIDE Archive / ProteomeXchange | `PXD004732` | <https://www.ebi.ac.uk/pride/archive/projects/PXD004732> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2017/02/PXD004732/> |
| iProX / ProteomeXchange | `PXD021390` | <https://proteomecentral.proteomexchange.org/cgi/GetDataset?ID=PXD021390> | <https://download.iprox.cn/IPX0002201000/IPX0002201001/> |
| iProX | N/A | <https://download.iprox.cn/IPX0002201000/IPX0002201002/> | <https://download.iprox.cn/IPX0002201000/IPX0002201002/> |
| iProX | N/A | <https://download.iprox.cn/IPX0002075000/IPX0002075003/> | <https://download.iprox.cn/IPX0002075000/IPX0002075003/> |
| iProX | N/A | <https://download.iprox.cn/IPX0001509000/IPX0001509002/> | <https://download.iprox.cn/IPX0001509000/IPX0001509002/> |
| Zenodo | `10.5281/zenodo.20589572` | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> |

## Per-File Benchmark Source Table

The `Expected local mzML path` column uses `<MSCODEC_ROOT>` as a placeholder for the local workspace root.

| Index | Dataset | Benchmark mzML file | Expected local mzML path | Source/vendor file(s) | Public source link(s) |
|---:|---|---|---|---|---|
| 1 | `data_stackzdpd` | `File13_SA1.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File13_SA1.uncompressed.mzML` | `SA1.raw` | <https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public/MTBLS733/FILES/SA1.raw> |
| 2 | `data_stackzdpd` | `File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML` | `LFQ_Orbitrap_AIF_Human_01.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_AIF_Human_01.raw> |
| 3 | `data_stackzdpd` | `File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML` | `LFQ_Orbitrap_DDA_Human_01.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_DDA_Human_01.raw> |
| 4 | `data_stackzdpd` | `File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML` | `LFQ_TTOF5600_DDA_Human_01.wiff`; `LFQ_TTOF5600_DDA_Human_01.wiff.scan` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF5600_DDA_Human_01.wiff><br><https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF5600_DDA_Human_01.wiff.scan> |
| 5 | `data_stackzdpd` | `File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML` | `LFQ_TTOF6600_DDA_Human_01.wiff`; `LFQ_TTOF6600_DDA_Human_01.wiff.scan` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF6600_DDA_Human_01.wiff><br><https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF6600_DDA_Human_01.wiff.scan> |
| 6 | `data_stackzdpd` | `File2_20180722_L929_test_DDA_1.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File2_20180722_L929_test_DDA_1.uncompressed.mzML` | `20180722_L929_test_DDA_1.wiff`; `20180722_L929_test_DDA_1.wiff.scan` | <https://download.iprox.cn/IPX0002201000/IPX0002201002/20180722_L929_test_DDA_1.wiff><br><https://download.iprox.cn/IPX0002201000/IPX0002201002/20180722_L929_test_DDA_1.wiff.scan> |
| 7 | `data_stackzdpd` | `File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML` | `QE-HFX-20190719_50cm_60min_OFe4_2.raw` | <https://download.iprox.cn/IPX0002075000/IPX0002075003/QE-HFX-20190719_50cm_60min_OFe4_2.raw> |
| 8 | `data_stackzdpd` | `File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML` | `QE-HFX-20190719_50cm_60min_Fr1.raw` | <https://download.iprox.cn/IPX0002075000/IPX0002075003/QE-HFX-20190719_50cm_60min_Fr1.raw> |
| 9 | `data_stackzdpd` | `File5_S8184TPST_01.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File5_S8184TPST_01.uncompressed.mzML` | `S8184TPST_01.raw` | <https://download.iprox.cn/IPX0001509000/IPX0001509002/S8184TPST_01.raw> |
| 10 | `data_stackzdpd` | `File6_Negative_000333.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\File6_Negative_000333.uncompressed.mzML` | `Negative_000333.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2021/04/PXD025142/Negative_000333.raw> |
| 11 | `data_stackzdpd_aif` | `LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML` | `LFQ_Orbitrap_AIF_Human_01.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_AIF_Human_01.raw> |
| 12 | `data_stackzdpd_aif` | `LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML` | `LFQ_Orbitrap_AIF_Human_02.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_AIF_Human_02.raw> |
| 13 | `data_stackzdpd` | `Set 1_F2.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_StackZDPD\Set 1_F2.uncompressed.mzML` | `SET-1.zip`, containing Agilent `Set 1_F2.d` after extraction | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2018/10/PXD004712/SET-1.zip> |
| 14 | `full8` | `01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML` | `01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2017/02/PXD004732/01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.raw> |
| 15 | `full8` | `QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4806_240522_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 16 | `full8` | `QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240429_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 17 | `full8` | `QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240507_DIA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 18 | `full8` | `QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4801_240408_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 19 | `full8` | `QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240628_DIA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 20 | `full8` | `QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240226_DIA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 21 | `full8` | `QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4805_240426_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 22 | `full8` | `QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240308_DIA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 23 | `full8` | `QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4805_240416_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 24 | `full8` | `QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML` | `QC_E4806_240709_DDA_293T_200ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 25 | `full8` | `QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML` | `QC_E4802_240703_DIA_293T_500ng_90min_R1.raw` | <https://zenodo.org/records/20589572> |
| 26 | `full8` | `QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML` | `QC_E4802_240703_DDA_293T_500ng_60min_R1.raw` | <https://zenodo.org/records/20589572> |
| 27 | `full8` | `QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML` | `QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.raw` | <https://zenodo.org/records/20589572> |
| 28 | `full8` | `QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML` | `QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.raw` | <https://zenodo.org/records/20589572> |
| 29 | `full8` | `QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML` | `QC_E4804_240320_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 30 | `full8` | `QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240320_DIA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 31 | `full8` | `QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML` | `QC_E4804_240403_DDA_293T_500ng_120min_R2.raw` | <https://zenodo.org/records/20589572> |
| 32 | `full8` | `QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML` | `QC_E4804_240320_DDA_293T_500ng_120min_R1.raw` | <https://zenodo.org/records/20589572> |
| 33 | `full8` | `QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML` | `QC_E4805_240709_DIA_293T_200ng_60min_R1.raw` | <https://zenodo.org/records/20589572> |
| 34 | `full8` | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML` | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.raw` | <https://zenodo.org/records/20589572> |
| 35 | `full8` | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML` | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.raw` | <https://zenodo.org/records/20589572> |
| 36 | `full8` | `01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML` | `<MSCODEC_ROOT>\benchmark\data_full8\01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML` | `01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.raw` | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2017/02/PXD004732/01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.raw> |

## Where These Inputs Are Referenced

The included summary CSVs preserve the original benchmark local paths in the
`input_path`, `method_dir`, and `output_files` columns:

```text
experiments/benchmarks/compression_benchmark_release/inputs/whole_file_summaries/full8_summary.csv
experiments/benchmarks/compression_benchmark_release/inputs/whole_file_summaries/data_stackzdpd_summary.csv
experiments/benchmarks/compression_benchmark_release/inputs/whole_file_summaries/data_stackzdpd_aif_summary.csv
```

The current formal combined tables and figures are under:

```text
experiments/benchmarks/compression_benchmark_release/outputs/combined_release/tables/
experiments/benchmarks/compression_benchmark_release/outputs/combined_release/plots/
```

The release does not ship historical per-method compressed outputs. If the
heavy benchmark is rerun, the default release-bundle output root is:

```text
experiments/benchmarks/compression_benchmark_release/outputs/runs/run_<timestamp>/
```

The V6 manuscript summary CSVs also preserve original run-root placeholders,
including:

```text
<MSCODEC_ROOT>\benchmark\compression_benchmark_runs\full8_except_jc_all_methods_w4_20260520\
<MSCODEC_ROOT>\benchmark\compression_benchmark_runs\full8_new15_all_methods_w8_size_desc_20260523\
<MSCODEC_ROOT>\benchmark\compression_benchmark_runs\data_stackzdpd_lt20g_all_methods_w5_size_desc_20260521\
<MSCODEC_ROOT>\benchmark\compression_benchmark_runs\data_stackzdpd_lfq_aif_01_02_all_methods_w2_20260526\
<MSCODEC_ROOT>\mspack_fix64_20files_20260523\
<MSCODEC_ROOT>\masscomp_mz64int32_w10_20260522_run3\
```

Those workstation run roots are provenance paths for the published tables; they
are not required for figure-only reproduction from the included release CSVs.

## Rerun Notes

- Download the public vendor/source files listed above.
- Convert vendor files to mzML with the same local conversion policy used for
  the corresponding benchmark row. The derived mzML filenames in this document
  are the names expected by the V6 summary tables.
- Keep SCIEX `.wiff` and `.wiff.scan` sidecars together before conversion.
- Run figure-only reproduction from the included CSVs when the goal is to
  reproduce the manuscript plots rather than redo the heavy compression runs.
- Rerun heavy compression benchmarks with
  `experiments/benchmarks/compression_benchmark_release/scripts/benchmark_compression_methods.py`.

