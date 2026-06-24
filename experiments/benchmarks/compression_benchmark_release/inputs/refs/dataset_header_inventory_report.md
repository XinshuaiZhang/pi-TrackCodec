# Dataset Header Inventory and Proposal Dataset Summary

- Updated: 2026-05-21T00:00:00+08:00
- Primary report path: <MSCODEC_ROOT>/7_xic_DIA/7.1_paper/dataset_header_inventory_report.md
- Note: the earlier report was originally generated only for full8 and stackzdpd_validation_uncompressed; data_Proposal/data_mzML was mentioned later but not integrated into the main summary. This version makes the Proposal mzML and timsTOF datasets first-class inventory entries.

## 1. Metric Definitions

- spectrum_count: total spectra read from the mzML header &lt;spectrumList count="..."&gt;.
- parsed_spectrum_count: spectra counted from indexed spectrum entries; when equal to spectrum_count, the header and index-level scan accounting are self-consistent.
- MS1 / MS2: spectra with ms level = 1 and ms level = 2; other levels are reported as Other.
- MS1 summary / MS2 summary: per-level representation summary, one of profile, centroid, mixed, or unknown.
- source_format: original vendor format declared in sourceFileList, for example Thermo RAW format, ABI WIFF format, Agilent MassHunter format, or Bruker TDF format.
- instrument_model and instrument_serial: vendor-exported instrument model and device serial, used to separate platform family from physical machine identity.
- For Bruker .d directories, Size (GiB) is directory size from du -sb; mzML-only fields such as spectrum_count are only available after conversion to mzML.

## 2. Included Datasets

| Dataset name | Absolute location | Entries counted | Format / role | Comment |
| --- | --- | --- | --- | --- |
| full8_core_data | <MSCODEC_ROOT>/data_raw/data/ | 5 mzML | Thermo RAW-derived mzML | Orbitrap Exploris 480 DDA/DIA working files; JC_MEOH_SWITCH.mzML is counted separately below. |
| full8_pxd004732 | <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/ | 3 mzML | Thermo RAW-derived uncompressed mzML | Orbitrap Fusion Lumos DDA/ETD files. |
| stackzdpd_validation_uncompressed | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/ | 18 mzML | Thermo / SCIEX / Agilent converted mzML | Original Stack-ZDPD validation set. |
| data_Proposal_data_mzML | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/ | 12 mzML | Thermo RAW-derived mzML | New Proposal Thermo set; now fully summarized in this report. |
| data_Proposal_timstof_raw_d | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/ | 20 .d directories | Bruker TDF raw directory data | Original timsTOF raw runs. Counted as requested, but overlaps by run name with converted mzML below. |
| data_Proposal_timstof_uncompressed_mzML | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/ | 19 mzML | Bruker TDF-derived uncompressed mzML | Large converted timsTOF mzML set, named data_Proposal_timstof. |
| data_Proposal_timstof_small_mzML | <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/ | 2 mzML | Bruker TDF-derived mzML | Small working timsTOF DDA/DIA pair; includes the 20240401 DIA run absent from the uncompressed directory. |
| data_AIF_uncompressed | <MSCODEC_ROOT>/data_raw/data_AIF/mzML_msconvert/uncompressed/ | 2 mzML | Thermo RAW-derived uncompressed mzML | Two Q Exactive HF-X AIF files converted with msconvert. |
| JC_MEOH_SWITCH | <MSCODEC_ROOT>/data_raw/data/JC_MEOH_SWITCH.mzML | 1 mzML | Thermo RAW-derived mzML | Additional Q Exactive Focus feature extraction target. |

Counting note: if every representation is counted as an entry, the current scope has 82 entries: 41 non-Bruker mzML entries and 41 Bruker .d / mzML entries. If Bruker raw .d and converted mzML are de-duplicated by run stem, Bruker contributes 20 underlying timsTOF runs rather than 41 independent runs.

## 3. Dataset-Level Summary

| Dataset | Vendor(s) | Entries | Total size (GiB) | Total spectra | Instrument coverage | MS1/MS2 representation summary |
| --- | --- | --- | --- | --- | --- | --- |
| full8 | Thermo Fisher | 8 mzML | 15.057 | 741,363 | Orbitrap Exploris 480 5; Orbitrap Fusion Lumos 3 | All MS1 profile + MS2 centroid |
| stackzdpd_validation_uncompressed | Thermo Fisher; SCIEX; Agilent | 18 mzML | 475.460 | 1,527,570 | Q Exactive HF/HF-X/Plus; TripleTOF 5600/6600; Agilent QTOF | Mixed: profile/profile, profile/centroid, centroid/centroid, mixed/centroid, MS1-only |
| data_Proposal_data_mzML | Thermo Fisher | 12 mzML | 147.068 | 2,485,305 | Q Exactive HF-X 2; Orbitrap Exploris 240 2; Orbitrap Exploris 480 1; Orbitrap Astral 7 | 11 files MS1 profile + MS2 centroid; 1 file MS1 profile + MS2 profile |
| data_Proposal_timstof_raw_d | Bruker | 20 .d | 256.806 | not directly available from mzML header | timsTOF series, grouped as TOF3/TOF4/TOF5 | Raw Bruker TDF directory data; acquisition labels infer 10 DDA and 10 DIA from names |
| data_Proposal_timstof_uncompressed_mzML | Bruker | 19 mzML | 1614.549 | 8,996,818 | timsTOF series, serials 1854399.10455, 1854399.10465, 1854399.10459 | Bruker TDF centroid peak-list mzML; 10 DDA, 9 DIA |
| data_Proposal_timstof_small_mzML | Bruker | 2 mzML | 29.782 | 642,635 | timsTOF series, serial 1854399.10455 | Both MS1 centroid + MS2 centroid; 1 DDA, 1 DIA |
| data_AIF_uncompressed | Thermo Fisher | 2 mzML | 3.654 | 472,279 | Q Exactive HF-X 2 | Both MS1 centroid + MS2 centroid; AIF |
| JC_MEOH_SWITCH | Thermo Fisher | 1 mzML | 0.152 | 21,773 | Q Exactive Focus | MS1 centroid + MS2 centroid, plus 11,550 other-level spectra |

## 4. Per-Dataset Features

### 4.1 full8

- Absolute locations: <MSCODEC_ROOT>/data_raw/data/ and <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/.
- Scope: 8 Thermo mzML files, total 15.057 GiB, 741,363 spectra.
- Platform coverage: Orbitrap Exploris 480 (5 files) and Orbitrap Fusion Lumos (3 files).
- Acquisition coverage from filenames: 5 DDA, 2 DIA, 1 ETD-DDA. The two 01625b...DDA... entries are two uncompressed exports of the same raw run and should be treated carefully in de-duplicated benchmark design.
- Data representation: all files are MS1 profile + MS2 centroid, so they exercise profile MS1 tracks and centroid MS2 peak lists but do not cover profile MS2.

| File | Size (GiB) | Spectra | MS1 | MS2 | Other | Instrument | Serial | MS1 summary | MS2 summary | Run start | Source format | Source file | Absolute path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML | 1.544 | 54,749 | 3,867 | 50,882 | 0 | Orbitrap Fusion Lumos | FSN20108 | profile | centroid | 2016-05-26T22:34:49Z | Thermo RAW format | 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.raw | <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML |
| 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML | 1.090 | 54,749 | 3,867 | 50,882 | 0 | Orbitrap Fusion Lumos | FSN20108 | profile | centroid | 2016-05-26T22:34:49Z | Thermo RAW format | 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.raw | <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML |
| 01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML | 0.661 | 13,724 | 2,302 | 11,422 | 0 | Orbitrap Fusion Lumos | FSN20108 | profile | centroid | 2016-06-29T15:08:32Z | Thermo RAW format | 01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.raw | <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML |
| QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML | 2.420 | 91,019 | 3,051 | 87,968 | 0 | Orbitrap Exploris 480 | MA10473C | profile | centroid | 2024-07-03T13:54:22Z | Thermo RAW format | QC_E4802_240703_DIA_293T_500ng_90min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML |
| QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML | 2.953 | 189,924 | 9,436 | 180,488 | 0 | Orbitrap Exploris 480 | MA10477C | profile | centroid | 2024-03-20T19:59:12Z | Thermo RAW format | QC_E4804_240320_DDA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML |
| QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML | 3.092 | 189,260 | 9,205 | 180,055 | 0 | Orbitrap Exploris 480 | MA10477C | profile | centroid | 2024-04-03T08:27:20Z | Thermo RAW format | QC_E4804_240403_DDA_293T_500ng_120min_R2.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML |
| QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML | 1.773 | 96,002 | 4,501 | 91,501 | 0 | Orbitrap Exploris 480 | MA10470C | profile | centroid | 2024-03-28T08:03:14Z | Thermo RAW format | QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML |
| QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML | 1.523 | 51,936 | 2,474 | 49,462 | 0 | Orbitrap Exploris 480 | MA10470C | profile | centroid | 2024-07-09T08:18:24Z | Thermo RAW format | QC_E4805_240709_DIA_293T_200ng_60min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML |

### 4.2 stackzdpd_validation_uncompressed

- Absolute location: <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/.
- Scope: 18 mzML files, total 475.460 GiB, 1,527,570 spectra.
- Vendor coverage: Thermo Fisher 7, SCIEX 10, Agilent 1.
- Instrument coverage: Q Exactive HF 1, Q Exactive HF-X 4, Q Exactive Plus 2, TripleTOF 5600 6, TripleTOF 6600 4, Agilent QTOF 1.
- Representation coverage: this is still the broadest legacy representation set: SCIEX profile/profile, Thermo profile/profile, Thermo profile/centroid, Thermo centroid/centroid, one Thermo mixed/centroid, one MS1-only, and one Agilent centroid/centroid.
- Fragmentation labels from filenames and prior inventory: SCIEX contributes DDA and SWATH/DIA; Thermo includes AIF, DDA-like and profile/profile examples; Agilent metadata does not expose a more specific acquisition label in the current mzML header.

| File | Size (GiB) | Spectra | MS1 | MS2 | Other | Instrument | Serial | MS1 summary | MS2 summary | Run start | Source format | Source file | Absolute path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| File10_HYE124_TTOF5600_32fix_lgillet_L150206_001.uncompressed.mzML | 77.955 | 71,940 | 2,180 | 69,760 | 0 | TripleTOF 5600 | AY21281103 | profile | profile | 2015-02-09T06:44:04Z | ABI WIFF format | File10_HYE124_TTOF5600_32fix_lgillet_L150206_001.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File10_HYE124_TTOF5600_32fix_lgillet_L150206_001.uncompressed.mzML |
| File11_HYE124_TTOF5600_64var_lgillet_L150206_007.uncompressed.mzML | 91.427 | 147,550 | 2,270 | 145,280 | 0 | TripleTOF 5600 | AY21281103 | profile | profile | 2015-02-10T04:32:23Z | ABI WIFF format | File11_HYE124_TTOF5600_64var_lgillet_L150206_007.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File11_HYE124_TTOF5600_64var_lgillet_L150206_007.uncompressed.mzML |
| File13_SA1.uncompressed.mzML | 2.683 | 12,470 | 12,470 | 0 | 0 | Q Exactive HF | Exactive Series slot #346 | profile | unknown | 2017-05-11T15:42:27Z | Thermo RAW format | File13_SA1.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File13_SA1.uncompressed.mzML |
| File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | 2.288 | 236,136 | 3,108 | 233,028 | 0 | Q Exactive HF-X | Exactive Series slot #1 | centroid | centroid | 2021-03-13T07:51:36Z | Thermo RAW format | File14_LFQ_Orbitrap_AIF_Human_01.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML |
| File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML | 12.836 | 143,136 | 27,110 | 116,026 | 0 | Q Exactive HF-X | Exactive Series slot #1 | profile | profile | 2021-02-24T16:05:08Z | Thermo RAW format | File15_LFQ_Orbitrap_DDA_Human_01.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML |
| File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML | 7.694 | 32,613 | 3,454 | 29,159 | 0 | TripleTOF 5600 | AY22841204 | profile | profile | 2018-12-04T13:55:27Z | ABI WIFF format | File16_LFQ_TTOF5600_DDA_Human_01.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML |
| File17_LFQ_TTOF5600_SWATH_Human_01.uncompressed.mzML | 36.496 | 152,047 | 2,339 | 149,708 | 0 | TripleTOF 5600 | AY22841204 | profile | profile | 2019-01-10T15:41:03Z | ABI WIFF format | File17_LFQ_TTOF5600_SWATH_Human_01.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File17_LFQ_TTOF5600_SWATH_Human_01.uncompressed.mzML |
| File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML | 16.576 | 61,938 | 3,293 | 58,645 | 0 | TripleTOF 6600 | BR20921502 | profile | profile | 2020-12-03T01:15:14Z | ABI WIFF format | File18_LFQ_TTOF6600_DDA_Human_01.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML |
| File19_LFQ_TTOF6600_SWATH_Human_01.uncompressed.mzML | 77.276 | 189,469 | 1,894 | 187,575 | 0 | TripleTOF 6600 | BR20921502 | profile | profile | 2020-12-04T06:23:59Z | ABI WIFF format | File19_LFQ_TTOF6600_SWATH_Human_01.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File19_LFQ_TTOF6600_SWATH_Human_01.uncompressed.mzML |
| File1_0530_BG_293T_1_SWATH_1.uncompressed.mzML | 20.263 | 34,683 | 1,051 | 33,632 | 0 | TripleTOF 5600 | AY20741011 | profile | profile | 2013-05-30T11:04:29Z | ABI WIFF format | File1_0530_BG_293T_1_SWATH_1.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File1_0530_BG_293T_1_SWATH_1.uncompressed.mzML |
| File2_20180722_L929_test_DDA_1.uncompressed.mzML | 9.410 | 46,635 | 4,206 | 42,429 | 0 | TripleTOF 5600 | AY20741011 | profile | profile | 2018-07-23T23:48:48Z | ABI WIFF format | File2_20180722_L929_test_DDA_1.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File2_20180722_L929_test_DDA_1.uncompressed.mzML |
| File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML | 1.521 | 44,817 | 2,135 | 42,682 | 0 | Q Exactive HF-X | Exactive Series slot #6311 | mixed | centroid | 2019-07-21T02:18:22Z | Thermo RAW format | File3_QE-HFX-20190719_50cm_60min_OFe4_2.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML |
| File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML | 2.029 | 63,731 | 6,026 | 57,705 | 0 | Q Exactive HF-X | Exactive Series slot #6311 | profile | centroid | 2019-07-26T21:27:20Z | Thermo RAW format | File4_QE-HFX-20190719_50cm_60min_Fr1.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML |
| File5_S8184TPST_01.uncompressed.mzML | 2.286 | 24,569 | 4,500 | 20,069 | 0 | Q Exactive Plus | Exactive Series slot #184 | profile | profile | 2018-08-01T17:14:26Z | Thermo RAW format | File5_S8184TPST_01.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File5_S8184TPST_01.uncompressed.mzML |
| File6_Negative_000333.uncompressed.mzML | 0.764 | 33,489 | 3,047 | 30,442 | 0 | Q Exactive Plus | Exactive Series slot #1 | profile | profile | 2016-05-14T07:29:09Z | Thermo RAW format | File6_Negative_000333.raw | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File6_Negative_000333.uncompressed.mzML |
| File8_HYE110_TTOF6600_32fix_lgillet_I160308_001.uncompressed.mzML | 69.670 | 72,501 | 2,197 | 70,304 | 0 | TripleTOF 6600 | BR21571511 | profile | profile | 2016-03-08T03:48:05Z | ABI WIFF format | File8_HYE110_TTOF6600_32fix_lgillet_I160308_001.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File8_HYE110_TTOF6600_32fix_lgillet_I160308_001.uncompressed.mzML |
| File9_HYE110_TTOF6600_64fix_lgillet_I160310_001.uncompressed.mzML | 43.974 | 141,570 | 2,178 | 139,392 | 0 | TripleTOF 6600 | BR21571511 | profile | profile | 2016-03-11T03:07:33Z | ABI WIFF format | File9_HYE110_TTOF6600_64fix_lgillet_I160310_001.wiff | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File9_HYE110_TTOF6600_64fix_lgillet_I160310_001.uncompressed.mzML |
| Set 1_F2.uncompressed.mzML | 0.315 | 18,276 | 7,641 | 10,635 | 0 | Agilent QTOF | SG1241B004 | centroid | centroid | 2016-03-30T18:48:40Z | Agilent MassHunter format | AcqMethod.xml | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/Set 1_F2.uncompressed.mzML |

### 4.3 data_Proposal_data_mzML: Thermo Proposal mzML

- Absolute location: <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/.
- Scope: 12 mzML files, total 147.068 GiB, 2,485,305 spectra; all parsed spectrum counts match header counts.
- Source format: all Thermo RAW format.
- Platform coverage: Q Exactive HF-X 2, Orbitrap Exploris 240 2, Orbitrap Exploris 480 1, Orbitrap Astral 7.
- Representation: 11 files are MS1 profile + MS2 centroid; 20181210_QX3... is MS1 profile + MS2 profile.
- Acquisition caveat: filenames and mzML headers do not consistently encode DDA/DIA/AIF labels for these 12 files, so the acquisition column is intentionally treated as unknown unless later metadata provides method-level labels.

| File | Size (GiB) | Spectra | MS1 | MS2 | Instrument | Serial | MS1 summary | MS2 summary | Run start | Absolute path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 20181210_QX3_JoMu_SA_LC12-7_uPAC200cm_MusmusculusiRT_F6.mzML | 16.895 | 202,711 | 19,645 | 183,066 | Q Exactive HF-X | Exactive Series slot #6025 | profile | profile | 2018-12-10T18:26:08Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/20181210_QX3_JoMu_SA_LC12-7_uPAC200cm_MusmusculusiRT_F6.mzML |
| 20230621_M_CC_3_CoIP_A549-1.mzML | 2.587 | 57,861 | 3,030 | 54,831 | Q Exactive HF-X | Exactive Series slot #6148 | profile | centroid | 2023-06-21T14:43:14Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/20230621_M_CC_3_CoIP_A549-1.mzML |
| 250424_05_ppt_total_CT18_1.mzML | 42.225 | 606,866 | 5,785 | 601,081 | Orbitrap Astral | OA10103 | profile | centroid | 2025-04-25T14:24:46Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/250424_05_ppt_total_CT18_1.mzML |
| 250907_E17_Liver_CT2A.mzML | 11.406 | 300,964 | 2,817 | 298,147 | Orbitrap Astral | OA10103 | profile | centroid | 2025-09-10T00:21:26Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/250907_E17_Liver_CT2A.mzML |
| 572MesADP3.mzML | 5.101 | 150,606 | 12,848 | 137,758 | Orbitrap Exploris 240 | MM10337C | profile | centroid | 2025-08-08T21:09:46Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/572MesADP3.mzML |
| 572MesADPControl1.mzML | 5.340 | 156,630 | 15,256 | 141,374 | Orbitrap Exploris 240 | MM10337C | profile | centroid | 2025-08-09T15:35:29Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/572MesADPControl1.mzML |
| EX1_SR_17182_D1_F4_18122022.mzML | 3.276 | 110,404 | 13,037 | 97,367 | Orbitrap Exploris 480 | MA10233C | profile | centroid | 2022-12-19T00:23:32Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/EX1_SR_17182_D1_F4_18122022.mzML |
| ID113916_01_SPD30_OA10034_10472_062924.mzML | 9.460 | 114,083 | 4,216 | 109,867 | Orbitrap Astral | OA10034 | profile | centroid | 2024-06-29T13:10:11Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/ID113916_01_SPD30_OA10034_10472_062924.mzML |
| ID113917_01_SPD30_OA10034_10472_062924.mzML | 10.152 | 121,162 | 3,854 | 117,308 | Orbitrap Astral | OA10034 | profile | centroid | 2024-06-29T10:43:20Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/ID113917_01_SPD30_OA10034_10472_062924.mzML |
| ID113919_01_SPD30_OA10034_10472_062924.mzML | 15.018 | 152,141 | 2,902 | 149,239 | Orbitrap Astral | OA10034 | profile | centroid | 2024-06-29T13:59:08Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/ID113919_01_SPD30_OA10034_10472_062924.mzML |
| XB00312DA_RH1.mzML | 12.577 | 255,890 | 2,265 | 253,625 | Orbitrap Astral | OA10060 | profile | centroid | 2024-01-23T22:05:45Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/XB00312DA_RH1.mzML |
| XB00312DA_RH2.mzML | 13.030 | 255,987 | 2,264 | 253,723 | Orbitrap Astral | OA10060 | profile | centroid | 2024-01-23T22:32:04Z | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/XB00312DA_RH2.mzML |

### 4.4 data_Proposal_timstof_raw_d: Bruker Original .d Directories

- Absolute location: <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/.
- Scope: 20 Bruker .d directories, total 256.806 GiB by directory size.
- Batch naming: TOF3 has 8 directories, TOF4 has 8, and TOF5 has 4. These labels are local batch/instrument aliases; converted mzML headers map them to three serials: TOF3 -> 1854399.10455, TOF4 -> 1854399.10465, TOF5 -> 1854399.10459.
- Acquisition labels inferred from directory names: 10 DDA and 10 DIA.
- Format: Bruker TDF raw data is stored as .d directories, typically containing analysis.tdf and analysis.tdf_bin; spectrum counts and MS1/MS2 mode counts require mzML conversion or TDF parsing rather than mzML header reading.

| Directory | Size (GiB) | TOF group | Acquisition | Absolute path |
| --- | --- | --- | --- | --- |
| TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.d | 3.821 | TOF3 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.d |
| TOF3_DDA_20220730_iRT_Hela_50ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.d | 3.816 | TOF3 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DDA_20220730_iRT_Hela_50ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.d |
| TOF3_DIA_20230821_293T_200ng_300nl_110min_QC_IonOptics_Slot1-26_1_7878.d | 23.770 | TOF3 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DIA_20230821_293T_200ng_300nl_110min_QC_IonOptics_Slot1-26_1_7878.d |
| TOF3_DIA_20240401_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10488.d | 4.160 | TOF3 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DIA_20240401_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10488.d |
| TOF3_DIA_20240403_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10490.d | 4.133 | TOF3 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DIA_20240403_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10490.d |
| TOF3_DIA_20240824_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11667.d | 14.715 | TOF3 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DIA_20240824_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11667.d |
| TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11924.d | 12.933 | TOF3 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11924.d |
| TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11934.d | 16.989 | TOF3 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11934.d |
| TOF4_DDA_20240607_293T_200ng_120min_column0530_QC_RA3_1_7604.d | 9.866 | TOF4 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DDA_20240607_293T_200ng_120min_column0530_QC_RA3_1_7604.d |
| TOF4_DDA_20240615_293T_200ng_120min_column0530_QC_RA2_1_7666.d | 9.977 | TOF4 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DDA_20240615_293T_200ng_120min_column0530_QC_RA2_1_7666.d |
| TOF4_DDA_20240627_293T_200ng_120min_column0621_QC_RA2_1_7781.d | 12.437 | TOF4 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DDA_20240627_293T_200ng_120min_column0621_QC_RA2_1_7781.d |
| TOF4_DDA_20240831_293T_200ng_120min_column0820_QC_RA2_1_8567.d | 19.960 | TOF4 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DDA_20240831_293T_200ng_120min_column0820_QC_RA2_1_8567.d |
| TOF4_DDA_20240904_293T_200ng_120min_column0820_QC_RA2_1_8650.d | 17.400 | TOF4 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DDA_20240904_293T_200ng_120min_column0820_QC_RA2_1_8650.d |
| TOF4_DDA_20240919_293T_200ng_120min_IonOptics0909_huiyanColumn_2238_RA2_1_8735.d | 24.819 | TOF4 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DDA_20240919_293T_200ng_120min_IonOptics0909_huiyanColumn_2238_RA2_1_8735.d |
| TOF4_DIA_20240830_293T_200ng_120min_column0820_QC_test2_RA6_1_8551.d | 18.666 | TOF4 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DIA_20240830_293T_200ng_120min_column0820_QC_test2_RA6_1_8551.d |
| TOF4_DIA_20241016_293T_200ng_120min_column1015_QC_RA2_1_8885.d | 9.443 | TOF4 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DIA_20241016_293T_200ng_120min_column1015_QC_RA2_1_8885.d |
| TOF5_20240408_293T_DDA_200ng_column0105_QC_Slot1-2_1_6847.d | 8.327 | TOF5 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF5_20240408_293T_DDA_200ng_column0105_QC_Slot1-2_1_6847.d |
| TOF5_20240624_293T_DIA_200ng_column0105_QC_Slot1-25_1_7807.d | 16.631 | TOF5 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF5_20240624_293T_DIA_200ng_column0105_QC_Slot1-25_1_7807.d |
| TOF5_20240717_293T_DIA_200ng_column0105_QC_Slot1-26_1_8028.d | 16.412 | TOF5 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF5_20240717_293T_DIA_200ng_column0105_QC_Slot1-26_1_8028.d |
| TOF5_20241013_293T_DDA_200ng_column0927_QC_Slot1-25_1_8863.d | 8.533 | TOF5 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF5_20241013_293T_DDA_200ng_column0927_QC_Slot1-25_1_8863.d |

### 4.5 data_Proposal_timstof: Bruker Uncompressed mzML

- Absolute location: <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/.
- Dataset name used in this report: data_Proposal_timstof.
- Scope: 19 uncompressed mzML files, total 1614.549 GiB, 8,996,818 header spectra.
- Source format: all Bruker TDF format; instrument model: Bruker Daltonics timsTOF series.
- Group summary: TOF3 7 files, 382.671 GiB, 2,128,101 spectra; TOF4 8 files, 771.890 GiB, 4,829,657 spectra; TOF5 4 files, 459.988 GiB, 2,039,060 spectra.
- Conversion coverage: this directory has 19 of the 20 raw .d runs. The missing uncompressed mzML run is TOF3_DIA_20240401_...10488, which is present in <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/.
- Representation: these mzML files should be treated as Bruker TDF centroid peak-list data rather than Thermo-style profile traces.

| File | Size (GiB) | Spectra | TOF group | Serial | Acquisition | Absolute path |
| --- | --- | --- | --- | --- | --- | --- |
| TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.uncompressed.mzML | 39.628 | 450,466 | TOF3 | 1854399.10455 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.uncompressed.mzML |
| TOF3_DDA_20220730_iRT_Hela_50ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.uncompressed.mzML | 39.628 | 450,466 | TOF3 | 1854399.10455 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DDA_20220730_iRT_Hela_50ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.uncompressed.mzML |
| TOF3_DIA_20230821_293T_200ng_300nl_110min_QC_IonOptics_Slot1-26_1_7878.uncompressed.mzML | 98.965 | 235,707 | TOF3 | 1854399.10455 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DIA_20230821_293T_200ng_300nl_110min_QC_IonOptics_Slot1-26_1_7878.uncompressed.mzML |
| TOF3_DIA_20240403_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10490.uncompressed.mzML | 33.969 | 237,315 | TOF3 | 1854399.10455 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DIA_20240403_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10490.uncompressed.mzML |
| TOF3_DIA_20240824_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11667.uncompressed.mzML | 60.803 | 237,307 | TOF3 | 1854399.10455 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DIA_20240824_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11667.uncompressed.mzML |
| TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11924.uncompressed.mzML | 58.907 | 237,315 | TOF3 | 1854399.10455 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11924.uncompressed.mzML |
| TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11934.uncompressed.mzML | 89.774 | 237,311 | TOF3 | 1854399.10455 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF3_DIA_20241009_new293T_200ng_300nl_110min_column0731_Slot1-25_1_11934.uncompressed.mzML |
| TOF4_DDA_20240607_293T_200ng_120min_column0530_QC_RA3_1_7604.uncompressed.mzML | 107.906 | 753,165 | TOF4 | 1854399.10465 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DDA_20240607_293T_200ng_120min_column0530_QC_RA3_1_7604.uncompressed.mzML |
| TOF4_DDA_20240615_293T_200ng_120min_column0530_QC_RA2_1_7666.uncompressed.mzML | 108.551 | 696,026 | TOF4 | 1854399.10465 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DDA_20240615_293T_200ng_120min_column0530_QC_RA2_1_7666.uncompressed.mzML |
| TOF4_DDA_20240627_293T_200ng_120min_column0621_QC_RA2_1_7781.uncompressed.mzML | 93.520 | 697,938 | TOF4 | 1854399.10465 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DDA_20240627_293T_200ng_120min_column0621_QC_RA2_1_7781.uncompressed.mzML |
| TOF4_DDA_20240831_293T_200ng_120min_column0820_QC_RA2_1_8567.uncompressed.mzML | 125.199 | 744,740 | TOF4 | 1854399.10465 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DDA_20240831_293T_200ng_120min_column0820_QC_RA2_1_8567.uncompressed.mzML |
| TOF4_DDA_20240904_293T_200ng_120min_column0820_QC_RA2_1_8650.uncompressed.mzML | 131.026 | 737,338 | TOF4 | 1854399.10465 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DDA_20240904_293T_200ng_120min_column0820_QC_RA2_1_8650.uncompressed.mzML |
| TOF4_DDA_20240919_293T_200ng_120min_IonOptics0909_huiyanColumn_2238_RA2_1_8735.uncompressed.mzML | 144.482 | 874,934 | TOF4 | 1854399.10465 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DDA_20240919_293T_200ng_120min_IonOptics0909_huiyanColumn_2238_RA2_1_8735.uncompressed.mzML |
| TOF4_DIA_20240830_293T_200ng_120min_column0820_QC_test2_RA6_1_8551.uncompressed.mzML | 81.180 | 312,764 | TOF4 | 1854399.10465 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DIA_20240830_293T_200ng_120min_column0820_QC_test2_RA6_1_8551.uncompressed.mzML |
| TOF4_DIA_20241016_293T_200ng_120min_column1015_QC_RA2_1_8885.uncompressed.mzML | 80.226 | 312,752 | TOF4 | 1854399.10465 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF4_DIA_20241016_293T_200ng_120min_column1015_QC_RA2_1_8885.uncompressed.mzML |
| TOF5_20240408_293T_DDA_200ng_column0105_QC_Slot1-2_1_6847.uncompressed.mzML | 93.685 | 633,735 | TOF5 | 1854399.10459 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF5_20240408_293T_DDA_200ng_column0105_QC_Slot1-2_1_6847.uncompressed.mzML |
| TOF5_20240624_293T_DIA_200ng_column0105_QC_Slot1-25_1_7807.uncompressed.mzML | 67.604 | 237,263 | TOF5 | 1854399.10459 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF5_20240624_293T_DIA_200ng_column0105_QC_Slot1-25_1_7807.uncompressed.mzML |
| TOF5_20240717_293T_DIA_200ng_column0105_QC_Slot1-26_1_8028.uncompressed.mzML | 67.017 | 237,263 | TOF5 | 1854399.10459 | DIA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF5_20240717_293T_DIA_200ng_column0105_QC_Slot1-26_1_8028.uncompressed.mzML |
| TOF5_20241013_293T_DDA_200ng_column0927_QC_Slot1-25_1_8863.uncompressed.mzML | 92.479 | 673,013 | TOF5 | 1854399.10459 | DDA | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/mzML_msconvert/uncompressed/TOF5_20241013_293T_DDA_200ng_column0927_QC_Slot1-25_1_8863.uncompressed.mzML |

### 4.6 data_Proposal_timstof_small_mzML

- Absolute location: <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/.
- Scope: 2 mzML files, total 29.782 GiB, 642,635 spectra; both parsed spectrum counts match header counts.
- Both files are Bruker TDF format, Bruker Daltonics timsTOF series, serial 1854399.10455.
- Both are MS1 centroid + MS2 centroid. This pair is useful as a smaller DDA/DIA working set for timsTOF feature and compression tests.

| File | Size (GiB) | Spectra | MS1 | MS2 | Serial | Acquisition | Run start | Absolute path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.mzML | 17.120 | 450,466 | 4,986 | 445,480 | 1854399.10455 | DDA | 2022-07-30T23:16:30Z | <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.mzML |
| TOF3_DIA_20240401_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10488.mzML | 12.662 | 192,169 | 2,957 | 189,212 | 1854399.10455 | DIA | 2024-04-03T10:29:53Z | <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/TOF3_DIA_20240401_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10488.mzML |

### 4.7 JC_MEOH_SWITCH

- Absolute file: <MSCODEC_ROOT>/data_raw/data/JC_MEOH_SWITCH.mzML.
- Source format: Thermo RAW format; instrument: Q Exactive Focus; serial: Exactive Series slot #1; source file: JC_MEOH_SWITCH.raw.
- Size: 0.152 GiB; header spectra: 21,773; parsed spectra: 21,773.
- Scan counts: MS1 = 2,593, MS2 = 7,630, Other = 11,550.
- Representation: MS1 centroid + MS2 centroid; this file is therefore a Thermo centroid peak-list example with substantial non-MS1/MS2 entries that should not be folded into MS2 statistics.

### 4.8 data_AIF_uncompressed

- Absolute location: <MSCODEC_ROOT>/data_raw/data_AIF/mzML_msconvert/uncompressed/.
- Scope: 2 Thermo mzML files, total 3.654 GiB, 472,279 spectra; both parsed spectrum counts match header counts.
- Source format: Thermo RAW format.
- Platform coverage: Q Exactive HF-X, serial Exactive Series slot #1.
- Acquisition and representation: both files are AIF and both are MS1 centroid + MS2 centroid, making this dataset a focused Thermo centroid peak-list AIF supplement.

| File | Size (GiB) | Spectra | Parsed | MS1 | MS2 | Other | Instrument | Serial | MS1 summary | MS2 summary | Run start | Source format | Source file | Absolute path |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | 1.852 | 236,136 | 236,136 | 3,108 | 233,028 | 0 | Q Exactive HF-X | Exactive Series slot #1 | centroid | centroid | 2021-03-13T07:51:36Z | Thermo RAW format | LFQ_Orbitrap_AIF_Human_01.raw | <MSCODEC_ROOT>/data_raw/data_AIF/mzML_msconvert/uncompressed/LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML |
| LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML | 1.802 | 236,143 | 236,143 | 3,108 | 233,035 | 0 | Q Exactive HF-X | Exactive Series slot #1 | centroid | centroid | 2021-03-13T17:42:50Z | Thermo RAW format | LFQ_Orbitrap_AIF_Human_02.raw | <MSCODEC_ROOT>/data_raw/data_AIF/mzML_msconvert/uncompressed/LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML |

## 5. Platform and Format Notes

| Vendor | Instrument families in current inventory | Mass analyzer / platform class | Current representation coverage | Current local gap |
| --- | --- | --- | --- | --- |
| Thermo Fisher | Orbitrap Fusion Lumos; Orbitrap Exploris 240/480; Orbitrap Astral; Q Exactive HF/HF-X/Plus/Focus | Orbitrap-based high-resolution MS; Fusion Lumos is Tribrid, Q Exactive/Exploris/Astral are Orbitrap families | profile/centroid, profile/profile, centroid/centroid, mixed/centroid, MS1-only, AIF centroid/centroid | Coverage is strongest; remaining need is more method-labeled Astral and more centroid/profile diversity by acquisition method. |
| SCIEX | TripleTOF 5600; TripleTOF 6600 | QTOF platform, commonly used for DDA and SWATH/DIA | Current local files are MS1 profile + MS2 profile after mzML conversion | Missing clear SCIEX centroid peak-list exports and newer ZenoTOF / X500-style data. |
| Agilent | Agilent QTOF only in current mzML header | QTOF / MassHunter ecosystem | Only MS1 centroid + MS2 centroid from one file | Major gap: too few Agilent files; need additional MassHunter .d and converted mzML, preferably both positive/negative and method-diverse examples. |
| Bruker | timsTOF series, local TOF3/TOF4/TOF5 aliases | TIMS + QTOF, stored as Bruker TDF .d directories and converted mzML | TDF centroid peak-list, DDA and DIA/dia-PASEF-like filenames | Need non-timsTOF Bruker comparison only if the paper claims broad Bruker coverage beyond TIMS/QTOF. |

### 5.1 timsTOF Equipment and Profile/Centroid Status

- timsTOF is Bruker's TIMS + QTOF platform family. Bruker's current timsTOF product overview lists timsTOF-class systems including timsOmni, timsMetabo, timsUltra AIP, timsTOF HT, timsTOF MALDI PharmaPulse, timsTOF Pro 2, timsTOF fleX, and timsTOF fleX MALDI-2, covering proteomics, spatial/MALDI, top-down/proteoformics, high-throughput screening, and metabolomics workflows.
- For standard LC-TIMS-MS/MS raw data, timsTOF does not behave like a Thermo Orbitrap profile trace dataset. The OpenTIMS paper states that transferring and storing profile-mode mass spectra at single TOF-push level is technically challenging and that timsTOF datasets consist of long series of centroided mass spectra.
- Therefore, the correct formal wording is not "all timsTOF data can only ever be centroid". The safer statement is: current Bruker timsTOF LC-MS/MS TDF data and our converted mzML files are centroid peak-list data; profile-like TOF transient/detail may exist below the exported TDF/mzML abstraction but is not represented in these local files as profile spectra.
- In this inventory, every local Bruker timsTOF mzML examined is MS1 centroid + MS2 centroid, and the raw .d directories should be grouped with the same TDF centroid peak-list family unless a lower-level vendor SDK export proves otherwise.

External references used for this section: Bruker timsTOF product overview https://www.bruker.com/en/products-and-solutions/mass-spectrometry/timstof.html; OpenTIMS/TimsPy/TimsR paper https://pubs.acs.org/doi/10.1021/acs.jproteome.0c00962.

## 6. Counts by Vendor, Platform, Representation, and Acquisition

### 6.1 Vendor Counts

| Vendor | Entries counted | Underlying run note | Instrument coverage |
| --- | --- | --- | --- |
| Thermo Fisher | 30 mzML | No raw/converted duplicate accounting applied here | Fusion Lumos 3; Exploris 480 6; Exploris 240 2; Astral 7; Q Exactive HF 1; HF-X 8; Plus 2; Focus 1 |
| SCIEX | 10 mzML | All from Stack-ZDPD validation mzML | TripleTOF 5600 6; TripleTOF 6600 4 |
| Agilent | 1 mzML | Single current local Agilent example | Agilent QTOF 1 |
| Bruker | 41 entries | 20 raw .d runs + 19 uncompressed mzML + 2 small mzML; de-duplicated underlying timsTOF runs = 20 | timsTOF series; TOF3/TOF4/TOF5 aliases |
| All | 82 entries | Counting includes both Bruker raw .d and converted mzML as requested | Four vendors represented locally |

### 6.2 Representation Counts

| Representation class | Entries | Main sources |
| --- | --- | --- |
| MS1 profile + MS2 centroid | 20 | full8 8; Stack-ZDPD Thermo File4 1; Proposal Thermo 11 |
| MS1 profile + MS2 profile | 14 | Stack-ZDPD SCIEX/Thermo 13; Proposal Thermo HF-X 1 |
| MS1 centroid + MS2 centroid / TDF centroid peak-list | 46 | Stack-ZDPD Thermo/Agilent 2; data_AIF 2; JC 1; Bruker .d/mzML entries 41 |
| MS1 mixed + MS2 centroid | 1 | Stack-ZDPD File3_QE-HFX... |
| MS1-only profile | 1 | Stack-ZDPD File13_SA1 |

### 6.3 Acquisition / Fragmentation Counts

| Acquisition / fragmentation class | Entries | How classified |
| --- | --- | --- |
| DDA | 30 | Filename labels from full8, Stack-ZDPD SCIEX/Thermo DDA, Bruker raw .d, Bruker uncompressed mzML, and small timstof DDA. |
| DIA / SWATH | 29 | Filename labels DIA or SWATH; Bruker DIA entries may correspond to DIA/dia-PASEF-style methods but are kept as DIA unless method files are parsed. |
| DDA-like | 2 | Stack-ZDPD QE-HFX files without explicit DDA string but previously treated as DDA-like. |
| ETD-DDA | 1 | Fusion Lumos ETD file. |
| AIF | 3 | Q Exactive HF-X AIF files: one Stack-ZDPD validation file and two data_AIF files. |
| MS1-only | 1 | Q Exactive HF File13_SA1. |
| unknown / method label not exposed | 16 | Proposal Thermo 12, Stack-ZDPD File5/File6/Set1 3, and JC_MEOH_SWITCH 1. |

## 7. Recommended Rich Sample Set

- Suggested dataset name: vendor_platform_rich_representative_v2.
- Selection rule: choose approximately two files per major vendor/platform family where possible; prioritize different acquisition methods and different profile/centroid representations; avoid duplicated raw/converted Bruker runs unless the benchmark explicitly compares vendor raw .d vs converted mzML.
- Agilent remains the limiting vendor because only one local Agilent mzML is currently available.

| Vendor | Platform | Selected absolute file | Representation / acquisition | Reason |
| --- | --- | --- | --- | --- |
| Thermo Fisher | Orbitrap Fusion Lumos | <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML | MS1 profile + MS2 centroid; DDA | Fusion Lumos DDA baseline |
| Thermo Fisher | Orbitrap Fusion Lumos | <MSCODEC_ROOT>/data_raw/PXD004732/mzml_PXD004732/uncompressed/01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML | MS1 profile + MS2 centroid; ETD-DDA | Same platform, different fragmentation route |
| Thermo Fisher | Orbitrap Exploris 480 | <MSCODEC_ROOT>/data_raw/data/QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML | MS1 profile + MS2 centroid; DIA | Exploris 480 DIA |
| Thermo Fisher | Orbitrap Exploris 480 | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML | MS1 profile + MS2 centroid; DDA | Exploris 480 DDA |
| Thermo Fisher | Orbitrap Exploris 240 | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/572MesADP3.mzML | MS1 profile + MS2 centroid; method unknown | Adds Exploris 240 |
| Thermo Fisher | Orbitrap Exploris 240 | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/572MesADPControl1.mzML | MS1 profile + MS2 centroid; method unknown | Same model control pair |
| Thermo Fisher | Orbitrap Astral | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/250424_05_ppt_total_CT18_1.mzML | MS1 profile + MS2 centroid; method unknown | Large Astral file |
| Thermo Fisher | Orbitrap Astral | <MSCODEC_ROOT>/data_raw/data_Proposal/data_mzML/XB00312DA_RH1.mzML | MS1 profile + MS2 centroid; method unknown | Second Astral serial/run family |
| Thermo Fisher | Q Exactive family | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | MS1 centroid + MS2 centroid; AIF | Thermo centroid peak-list |
| Thermo Fisher | Q Exactive family | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File5_S8184TPST_01.uncompressed.mzML | MS1 profile + MS2 profile | Thermo profile/profile |
| SCIEX | TripleTOF 5600 | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML | MS1 profile + MS2 profile; DDA | SCIEX 5600 DDA |
| SCIEX | TripleTOF 5600 | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File17_LFQ_TTOF5600_SWATH_Human_01.uncompressed.mzML | MS1 profile + MS2 profile; SWATH/DIA | SCIEX 5600 SWATH |
| SCIEX | TripleTOF 6600 | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML | MS1 profile + MS2 profile; DDA | SCIEX 6600 DDA |
| SCIEX | TripleTOF 6600 | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/File19_LFQ_TTOF6600_SWATH_Human_01.uncompressed.mzML | MS1 profile + MS2 profile; SWATH/DIA | SCIEX 6600 SWATH |
| Agilent | Agilent QTOF | <MSCODEC_ROOT>/data_raw/data_StackZDPD/stackzdpd_validation_mzML/uncompressed/Set 1_F2.uncompressed.mzML | MS1 centroid + MS2 centroid; method unknown | Only local Agilent mzML |
| Bruker | timsTOF raw .d | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.d | TDF centroid peak-list family; DDA | Raw vendor directory DDA |
| Bruker | timsTOF raw .d | <MSCODEC_ROOT>/data_raw/data_Proposal/data_vendor/TimsTOF/TOF4_DIA_20241016_293T_200ng_120min_column1015_QC_RA2_1_8885.d | TDF centroid peak-list family; DIA | Raw vendor directory DIA |
| Bruker | timsTOF mzML | <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/TOF3_DDA_20220730_iRT_Hela_100ng_300nl_80min_IonOptics_QC_Slot1-19_1_3680.mzML | MS1 centroid + MS2 centroid; DDA | Small converted working DDA |
| Bruker | timsTOF mzML | <MSCODEC_ROOT>/data_raw/data_Proposal/timstof/TOF3_DIA_20240401_293T_200ng_300nl_110min_75umID_QC_column0327_Slot1-24_1_10488.mzML | MS1 centroid + MS2 centroid; DIA | Small converted working DIA and missing uncompressed conversion counterpart |

## 8. Remaining Data Gaps

- Agilent: only one local Agilent QTOF mzML exists, and it is centroid/centroid; this is the most important vendor imbalance. Use <MSCODEC_ROOT>/data_raw/metadata_Proposal/agilent_supplement_candidates.tsv and Aird 2.0 Table S1 candidates to add more Agilent .d / mzML files.
- SCIEX: current local SCIEX data are all profile/profile TripleTOF 5600/6600 conversions; missing SCIEX centroid exports and newer SCIEX platforms.
- Bruker: current Bruker coverage is strong for timsTOF/TDF centroid peak-list data, but not for non-timsTOF Bruker platforms. Do not claim broad Bruker platform coverage beyond timsTOF unless more data are added.
- Thermo Fisher: platform coverage is broad, but several Proposal Thermo files lack explicit method labels in filenames/header; method files or external metadata are needed before counting them as DDA/DIA/AIF.

## 9. Proposal Metadata and Aird 2.0 Supplement Files

- Proposal workbook split output: <MSCODEC_ROOT>/data_raw/metadata_Proposal/.
- Current retained sheet exports are TSV files: 01_E480质谱仪数据集.tsv, 02_TimsTOF质谱仪数据集.tsv, 03_Orbitrap_Astral数据集.tsv, 04_TripleTOF_5600数据集.tsv, 05_Q-Exactive数据集.tsv, 06_Orbitrap_Fusion&Lumos数据集.tsv, and 07_压缩结果模板.tsv.
- Aird 2.0 Table S1 transcription: <MSCODEC_ROOT>/data_raw/metadata_Proposal/aird2_table_s1_datasets.tsv.
- Local availability comparison tables: <MSCODEC_ROOT>/data_raw/metadata_Proposal/aird2_table_s1_local_availability.tsv and <MSCODEC_ROOT>/data_raw/metadata_Proposal/aird2_table_s1_local_availability_strict.tsv.
- Agilent supplement candidates: <MSCODEC_ROOT>/data_raw/metadata_Proposal/agilent_supplement_candidates.tsv.

Recommended Agilent supplement priority remains: MTBLS6732/1_c1_ESI_pos_.d.zip, MTBLS3505/Meta-NEG-HSS-MS1-ICU-C1X.d.zip, and MTBLS3505/Meta-POS-HSS-MS1-ICU-S3X.d.zip, because they directly address the local Agilent imbalance.

## 10. Selected 30-File Test Dataset Summary

| 文件名 | 测序仪器厂商 | 仪器平台 | 采集方式（DDA/DIA） | 电离方式（AIF，ETD等） | mzML大小 (GiB) | MS1类别 | MS2类别 |
| --- | --- | --- | --- | --- | ---: | --- | --- |
| 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML | Thermo Fisher | Orbitrap Fusion Lumos | DDA | HCD | 1.544 | profile | centroid |
| 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML | Thermo Fisher | Orbitrap Fusion Lumos | DDA | HCD | 1.090 | profile | centroid |
| 01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML | Thermo Fisher | Orbitrap Fusion Lumos | DDA | ETD | 0.661 | profile | centroid |
| QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 2.420 | profile | centroid |
| QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 2.953 | profile | centroid |
| QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 3.092 | profile | centroid |
| QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 1.773 | profile | centroid |
| QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 1.523 | profile | centroid |
| 20181210_QX3_JoMu_SA_LC12-7_uPAC200cm_MusmusculusiRT_F6.mzML | Thermo Fisher | Q Exactive HF-X | unknown | unknown | 16.895 | profile | profile |
| 20230621_M_CC_3_CoIP_A549-1.mzML | Thermo Fisher | Q Exactive HF-X | unknown | unknown | 2.587 | profile | centroid |
| 250907_E17_Liver_CT2A.mzML | Thermo Fisher | Orbitrap Astral | unknown | unknown | 11.406 | profile | centroid |
| 572MesADP3.mzML | Thermo Fisher | Orbitrap Exploris 240 | unknown | unknown | 5.101 | profile | centroid |
| 572MesADPControl1.mzML | Thermo Fisher | Orbitrap Exploris 240 | unknown | unknown | 5.340 | profile | centroid |
| EX1_SR_17182_D1_F4_18122022.mzML | Thermo Fisher | Orbitrap Exploris 480 | unknown | unknown | 3.276 | profile | centroid |
| ID113916_01_SPD30_OA10034_10472_062924.mzML | Thermo Fisher | Orbitrap Astral | unknown | unknown | 9.460 | profile | centroid |
| ID113917_01_SPD30_OA10034_10472_062924.mzML | Thermo Fisher | Orbitrap Astral | unknown | unknown | 10.152 | profile | centroid |
| XB00312DA_RH1.mzML | Thermo Fisher | Orbitrap Astral | unknown | unknown | 12.577 | profile | centroid |
| XB00312DA_RH2.mzML | Thermo Fisher | Orbitrap Astral | unknown | unknown | 13.030 | profile | centroid |
| File13_SA1.uncompressed.mzML | Thermo Fisher | Q Exactive HF | MS1-only | none | 2.683 | profile | unknown |
| File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | Thermo Fisher | Q Exactive HF-X | AIF | AIF | 2.288 | centroid | centroid |
| File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML | Thermo Fisher | Q Exactive HF-X | DDA | HCD | 12.836 | profile | profile |
| File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML | SCIEX | TripleTOF 5600 | DDA | CID | 7.694 | profile | profile |
| File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML | SCIEX | TripleTOF 6600 | DDA | CID | 16.576 | profile | profile |
| File1_0530_BG_293T_1_SWATH_1.uncompressed.mzML | SCIEX | TripleTOF 5600 | DIA/SWATH | CID | 20.263 | profile | profile |
| File2_20180722_L929_test_DDA_1.uncompressed.mzML | SCIEX | TripleTOF 5600 | DDA | CID | 9.410 | profile | profile |
| File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML | Thermo Fisher | Q Exactive HF-X | DDA-like | HCD | 1.521 | mixed | centroid |
| File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML | Thermo Fisher | Q Exactive HF-X | DDA-like | HCD | 2.029 | profile | centroid |
| File5_S8184TPST_01.uncompressed.mzML | Thermo Fisher | Q Exactive Plus | unknown | unknown | 2.286 | profile | profile |
| File6_Negative_000333.uncompressed.mzML | Thermo Fisher | Q Exactive Plus | unknown | unknown | 0.764 | profile | profile |
| Set 1_F2.uncompressed.mzML | Agilent | Agilent QTOF | unknown | CID-like/unknown | 0.315 | centroid | centroid |

## 11. Raw Source Size Versus Converted mzML Size

- mzML大小来自本地转换后文件的实际大小或前文 inventory 统计。
- local RAW、local WIFF + scan、local Agilent .d directory 表示本地存在对应原始数据，并按文件或目录实际字节数折算为 GiB。
- metadata_Proposal 表示本地未找到原始 RAW 文件，但 Proposal metadata TSV 中记录了原始大小；这些 M/G 单位保留原表写法，属于 metadata 近似记录。
- header RAW only, not found locally or publicly 表示 mzML header 中记录了 RAW 名称，但本地未找到 RAW，metadata_Proposal 中也没有完全同名大小记录；2026-05-09 联网检索精确文件名、文件 stem、EBI/PRIDE/MassIVE/ProteomeXchange 站内组合检索后也未找到公开原始大小记录。
- data_full8_raw 表示后来补充发现的本地 RAW 目录 <MSCODEC_ROOT>/data_raw/data_full8_raw/，当前 full8 QC 文件均可在该目录找到对应 RAW。

| 文件名 | 原始文件大小 | 原始大小来源 | mzML大小 (GiB) |
| --- | ---: | --- | ---: |
| 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML | 0.641 GiB | local RAW | 1.544 |
| 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML | 0.641 GiB | local RAW, same source RAW as previous row | 1.090 |
| 01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML | 0.432 GiB | local RAW | 0.661 |
| QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML | 2.824 GiB | local RAW, data_full8_raw | 2.420 |
| QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML | 1.784 GiB | local RAW, data_full8_raw | 2.953 |
| QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML | 1.798 GiB | local RAW, data_full8_raw | 3.092 |
| QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML | 1.426 GiB | local RAW, data_full8_raw | 1.773 |
| QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML | 1.404 GiB | local RAW, data_full8_raw | 1.523 |
| 20181210_QX3_JoMu_SA_LC12-7_uPAC200cm_MusmusculusiRT_F6.mzML | 3.2G | metadata_Proposal | 16.895 |
| 20230621_M_CC_3_CoIP_A549-1.mzML | 877M | metadata_Proposal | 2.587 |
| 250907_E17_Liver_CT2A.mzML | 6.6G | metadata_Proposal | 11.406 |
| 572MesADP3.mzML | 1.2G | metadata_Proposal | 5.101 |
| 572MesADPControl1.mzML | 1.2G | metadata_Proposal | 5.340 |
| EX1_SR_17182_D1_F4_18122022.mzML | 786M | metadata_Proposal | 3.276 |
| ID113916_01_SPD30_OA10034_10472_062924.mzML | 5.9G | metadata_Proposal | 9.460 |
| ID113917_01_SPD30_OA10034_10472_062924.mzML | 6.4G | metadata_Proposal | 10.152 |
| XB00312DA_RH1.mzML | 7.7G | metadata_Proposal | 12.577 |
| XB00312DA_RH2.mzML | 8.0G | metadata_Proposal | 13.030 |
| File13_SA1.uncompressed.mzML | 0.686 GiB | local RAW | 2.683 |
| File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | 1.579 GiB | local RAW | 2.288 |
| File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML | 3.450 GiB | local RAW | 12.836 |
| File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML | 0.470 GiB | local WIFF + scan | 7.694 |
| File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML | 1.090 GiB | local WIFF + scan | 16.576 |
| File1_0530_BG_293T_1_SWATH_1.uncompressed.mzML | 1.062 GiB | local WIFF + scan | 20.263 |
| File2_20180722_L929_test_DDA_1.uncompressed.mzML | 0.571 GiB | local WIFF + scan | 9.410 |
| File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML | 0.835 GiB | local RAW | 1.521 |
| File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML | 0.650 GiB | local RAW | 2.029 |
| File5_S8184TPST_01.uncompressed.mzML | 0.591 GiB | local RAW | 2.286 |
| File6_Negative_000333.uncompressed.mzML | 0.195 GiB | local RAW | 0.764 |
| Set 1_F2.uncompressed.mzML | 0.195 GiB | local Agilent .d directory | 0.315 |

## 12. Additional data Directory QC Files Outside full8

- Absolute mzML location: <MSCODEC_ROOT>/data_raw/data/.
- Absolute RAW location: <MSCODEC_ROOT>/data_raw/data_full8_raw/.
- Scope: 15 QC mzML files in <MSCODEC_ROOT>/data_raw/data/ that are outside the 5 QC files counted in full8_core_data and excluding the QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML derived file.
- All 15 files have matching local Thermo RAW files in data_full8_raw.
- Acquisition is inferred from filename labels DDA or DIA. Fragmentation is listed as HCD because these are Thermo Orbitrap Exploris 480 QC DDA/DIA files; if method files are later parsed, this column can be tightened.

| 文件名 | 测序仪器厂商 | 仪器平台 | 采集方式（DDA/DIA） | 电离方式（AIF，ETD等） | mzML大小 (GiB) | 原始RAW大小 (GiB) | 原始大小来源 | MS1/MS2类别 | RAW absolute path | mzML absolute path |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- |
| QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 4.126 | 2.005 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4801_240408_DDA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 2.422 | 1.958 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML |
| QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 2.438 | 1.961 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML |
| QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 3.069 | 1.424 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4802_240703_DDA_293T_500ng_60min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML |
| QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 4.015 | 3.517 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4804_240226_DIA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 3.951 | 3.457 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4804_240308_DIA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 3.649 | 3.104 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4804_240320_DIA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 4.172 | 2.063 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4804_240429_DDA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 4.129 | 3.641 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4804_240507_DIA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DIA | HCD | 4.045 | 3.604 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4804_240628_DIA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 2.362 | 1.443 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML |
| QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 3.948 | 2.141 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4805_240416_DDA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 3.984 | 2.147 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4805_240426_DDA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 4.917 | 1.573 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4806_240522_DDA_293T_500ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML |
| QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML | Thermo Fisher | Orbitrap Exploris 480 | DDA | HCD | 3.914 | 1.716 | local RAW | MS1 profile + MS2 centroid | <MSCODEC_ROOT>/data_raw/data_full8_raw/QC_E4806_240709_DDA_293T_200ng_120min_R1.raw | <MSCODEC_ROOT>/data_raw/data/QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML |
