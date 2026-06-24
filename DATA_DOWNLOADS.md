# Dataset Download Links

This file lists the source-data download status for the 36-file formal
benchmark/downstream cohort documented in
`public_validation_bundle/downstreamvalidation/inputs/manifests/current36_search_coverage.csv`
and the benchmark whole-file summary tables under
`experiments/benchmarks/compression_benchmark_release/inputs/whole_file_summaries/`.

For a benchmark-specific acquisition and local-placement index, see
`BENCHMARK_DATA_DOWNLOADS.md`.

The benchmark inputs are mzML files converted or normalized from vendor raw
files. Public links below point to the source vendor files where those files
could be verified in public repositories. The large derived mzML files are not
re-hosted in this source release.

For SCIEX WIFF inputs, download both `.wiff` and `.wiff.scan` files when both
are listed.

## Public Repository Roots

| Repository / database | Website address | Accession | Project page | File directory |
|---|---|---|---|---|
| MetaboLights | <https://www.ebi.ac.uk/metabolights/> | `MTBLS733` | <https://www.ebi.ac.uk/metabolights/MTBLS733> | <https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public/MTBLS733/FILES/> |
| PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/> | `PXD028735` | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/> |
| PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/> | `PXD025142` | <https://www.ebi.ac.uk/pride/archive/projects/PXD025142> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2021/04/PXD025142/> |
| PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/> | `PXD004712` | <https://www.ebi.ac.uk/pride/archive/projects/PXD004712> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2018/10/PXD004712/> |
| PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/> | `PXD004732` | <https://www.ebi.ac.uk/pride/archive/projects/PXD004732> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2017/02/PXD004732/> |
| iProX / ProteomeXchange | <https://www.iprox.cn/> | `PXD021390` | <https://proteomecentral.proteomexchange.org/cgi/GetDataset?ID=PXD021390> | <https://download.iprox.cn/IPX0002201000/IPX0002201001/> |
| iProX | <https://www.iprox.cn/> | N/A | <https://download.iprox.cn/IPX0002201000/IPX0002201002/> | <https://download.iprox.cn/IPX0002201000/IPX0002201002/> |
| iProX | <https://www.iprox.cn/> | N/A | <https://download.iprox.cn/IPX0002075000/IPX0002075003/> | <https://download.iprox.cn/IPX0002075000/IPX0002075003/> |
| iProX | <https://www.iprox.cn/> | N/A | <https://download.iprox.cn/IPX0001509000/IPX0001509002/> | <https://download.iprox.cn/IPX0001509000/IPX0001509002/> |
| Zenodo | <https://zenodo.org/> | `10.5281/zenodo.20589572` | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> |

## 36-File Cohort

| Index | Benchmark mzML file | Source/vendor file(s) | Repository / database | Website address | Download link(s) | Notes |
|---:|---|---|---|---|---|---|
| 1 | `File13_SA1.uncompressed.mzML` | `SA1.raw` | MetaboLights | <https://www.ebi.ac.uk/metabolights/MTBLS733> | <https://ftp.ebi.ac.uk/pub/databases/metabolights/studies/public/MTBLS733/FILES/SA1.raw> | Local benchmark prefix `File13_` is not part of the MetaboLights raw filename. |
| 2 | `File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML` | `LFQ_Orbitrap_AIF_Human_01.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_AIF_Human_01.raw> | Local benchmark prefix `File14_` is not part of the PRIDE raw filename. |
| 3 | `File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML` | `LFQ_Orbitrap_DDA_Human_01.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_DDA_Human_01.raw> | Local benchmark prefix `File15_` is not part of the PRIDE raw filename. |
| 4 | `File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML` | `LFQ_TTOF5600_DDA_Human_01.wiff`; `LFQ_TTOF5600_DDA_Human_01.wiff.scan` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF5600_DDA_Human_01.wiff><br><https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF5600_DDA_Human_01.wiff.scan> | Download both WIFF sidecar files. Local benchmark prefix `File16_` is not part of the PRIDE vendor filename. |
| 5 | `File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML` | `LFQ_TTOF6600_DDA_Human_01.wiff`; `LFQ_TTOF6600_DDA_Human_01.wiff.scan` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF6600_DDA_Human_01.wiff><br><https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_TTOF6600_DDA_Human_01.wiff.scan> | Download both WIFF sidecar files. Local benchmark prefix `File18_` is not part of the PRIDE vendor filename. |
| 6 | `File2_20180722_L929_test_DDA_1.uncompressed.mzML` | `20180722_L929_test_DDA_1.wiff`; `20180722_L929_test_DDA_1.wiff.scan` | iProX | <https://download.iprox.cn/IPX0002201000/IPX0002201002/> | <https://download.iprox.cn/IPX0002201000/IPX0002201002/20180722_L929_test_DDA_1.wiff><br><https://download.iprox.cn/IPX0002201000/IPX0002201002/20180722_L929_test_DDA_1.wiff.scan> | Local benchmark prefix `File2_` is not part of the iProX vendor filename. |
| 7 | `File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML` | `QE-HFX-20190719_50cm_60min_OFe4_2.raw` | iProX | <https://download.iprox.cn/IPX0002075000/IPX0002075003/> | <https://download.iprox.cn/IPX0002075000/IPX0002075003/QE-HFX-20190719_50cm_60min_OFe4_2.raw> | Local benchmark prefix `File3_` is not part of the iProX raw filename. |
| 8 | `File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML` | `QE-HFX-20190719_50cm_60min_Fr1.raw` | iProX | <https://download.iprox.cn/IPX0002075000/IPX0002075003/> | <https://download.iprox.cn/IPX0002075000/IPX0002075003/QE-HFX-20190719_50cm_60min_Fr1.raw> | Local benchmark prefix `File4_` is not part of the iProX raw filename. |
| 9 | `File5_S8184TPST_01.uncompressed.mzML` | `S8184TPST_01.raw` | iProX | <https://download.iprox.cn/IPX0001509000/IPX0001509002/> | <https://download.iprox.cn/IPX0001509000/IPX0001509002/S8184TPST_01.raw> | Local benchmark prefix `File5_` is not part of the iProX raw filename. |
| 10 | `File6_Negative_000333.uncompressed.mzML` | `Negative_000333.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD025142> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2021/04/PXD025142/Negative_000333.raw> | Local benchmark prefix `File6_` is not part of the PRIDE raw filename. |
| 11 | `LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML` | `LFQ_Orbitrap_AIF_Human_01.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_AIF_Human_01.raw> | Same public raw file as row 2, represented as a separate mzML input in the validation bundle. |
| 12 | `LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML` | `LFQ_Orbitrap_AIF_Human_02.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD028735> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2022/02/PXD028735/LFQ_Orbitrap_AIF_Human_02.raw> | Public AIF raw file under `PXD028735`. |
| 13 | `Set 1_F2.uncompressed.mzML` | `SET-1.zip`; Agilent MassHunter `.d` directory; `AcqMethod.xml` in mzML metadata | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD004712> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2018/10/PXD004712/SET-1.zip> | `SET-1.zip` contains the Agilent Set 1 directories, including `Set 1_F2.d` after extraction. |
| 14 | `01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML` | `01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD004732> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2017/02/PXD004732/01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.raw> | mzML generated locally from the public Thermo RAW file. |
| 15 | `QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4806_240522_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 16 | `QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240429_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 17 | `QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240507_DIA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 18 | `QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4801_240408_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 19 | `QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240628_DIA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 20 | `QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240226_DIA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 21 | `QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4805_240426_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 22 | `QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240308_DIA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 23 | `QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4805_240416_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 24 | `QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML` | `QC_E4806_240709_DDA_293T_200ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 25 | `QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML` | `QC_E4802_240703_DIA_293T_500ng_90min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 26 | `QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML` | `QC_E4802_240703_DDA_293T_500ng_60min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 27 | `QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML` | `QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 28 | `QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML` | `QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 29 | `QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML` | `QC_E4804_240320_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 30 | `QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML` | `QC_E4804_240320_DIA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 31 | `QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML` | `QC_E4804_240403_DDA_293T_500ng_120min_R2.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 32 | `QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML` | `QC_E4804_240320_DDA_293T_500ng_120min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Centroided derivative of the row 29 local source file; same Zenodo raw source. |
| 33 | `QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML` | `QC_E4805_240709_DIA_293T_200ng_60min_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 34 | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML` | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 35 | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML` | `QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.raw` | Zenodo | <https://zenodo.org/records/20589572> | <https://zenodo.org/records/20589572> | Zenodo-hosted QC source file. |
| 36 | `01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML` | `01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.raw` | PRIDE Archive / ProteomeXchange | <https://www.ebi.ac.uk/pride/archive/projects/PXD004732> | <https://ftp.pride.ebi.ac.uk/pride/data/archive/2017/02/PXD004732/01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.raw> | mzML generated locally from the public Thermo RAW file. |
