# TrackCodec MS1/MS2 Section-Level Benchmark

Scope: TrackCodec only. Other methods in the whole-file benchmark are omitted here because their wrappers do not expose a comparable MS1/MS2 section-level output interface.

Source: existing TrackCodec `.stats.json` files from the completed full8 and Data_StackZDPD benchmark runs. No compression rerun was required.

MS1 compressed bytes include the TrackCodec MS1 core plus `ms1_full_mz` and `ms1_orphan_intensity` sidecar compressed bytes. The MS1 raw denominator remains the original MS1 m/z + intensity raw payload, so the sidecar raw bytes are not double-counted.

## Aggregate

| Section | Files | Nonzero Raw Files | Raw GiB | Compressed GiB | Aggregate CR | Median CR | Mean Encode s |
|---|---|---|---|---|---|---|---|
| MS1 | 36 | 36 | 66.4020 | 8.9013 | 7.4598 | 6.6946 | 2190.1689 |
| MS2 | 36 | 35 | 51.8272 | 10.5828 | 4.8973 | 3.3347 | 1951.0996 |

## Per-File MS1/MS2 Sections

| Dataset | Label | File | Section | Raw GiB | Compressed GiB | CR | Rate % | Encode s |
|---|---|---|---|---|---|---|---|---|
| Data_StackZDPD | SZ-File13 | File13_SA1.uncompressed.mzML | MS1 | 2.6404 | 0.3013 | 8.7646 | 11.4096 | 1515.0122 |
| Data_StackZDPD | SZ-File13 | File13_SA1.uncompressed.mzML | MS2 | 0.0000 | 0.0000 |  |  | 0.0064 |
| Data_StackZDPD | SZ-File14 | File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | MS1 | 0.0433 | 0.0159 | 2.7242 | 36.7075 | 32.5497 |
| Data_StackZDPD | SZ-File14 | File14_LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | MS2 | 1.0660 | 0.3282 | 3.2480 | 30.7879 | 1159.3642 |
| Data_StackZDPD | SZ-File15 | File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML | MS1 | 7.9470 | 1.1260 | 7.0577 | 14.1689 | 4533.1746 |
| Data_StackZDPD | SZ-File15 | File15_LFQ_Orbitrap_DDA_Human_01.uncompressed.mzML | MS2 | 4.1902 | 0.6880 | 6.0905 | 16.4190 | 6938.2366 |
| Data_StackZDPD | SZ-File16 | File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML | MS1 | 2.7342 | 0.2232 | 12.2517 | 8.1622 | 6371.5897 |
| Data_StackZDPD | SZ-File16 | File16_LFQ_TTOF5600_DDA_Human_01.uncompressed.mzML | MS2 | 4.8336 | 0.4155 | 11.6325 | 8.5966 | 8646.4985 |
| Data_StackZDPD | SZ-File18 | File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML | MS1 | 4.3169 | 0.4934 | 8.7494 | 11.4294 | 9135.0547 |
| Data_StackZDPD | SZ-File18 | File18_LFQ_TTOF6600_DDA_Human_01.uncompressed.mzML | MS2 | 12.0194 | 1.1170 | 10.7599 | 9.2937 | 15118.1903 |
| Data_StackZDPD | SZ-File2 | File2_20180722_L929_test_DDA_1.uncompressed.mzML | MS1 | 5.3864 | 0.4466 | 12.0602 | 8.2918 | 6253.1608 |
| Data_StackZDPD | SZ-File2 | File2_20180722_L929_test_DDA_1.uncompressed.mzML | MS2 | 3.8450 | 0.3848 | 9.9914 | 10.0086 | 6640.0051 |
| Data_StackZDPD | SZ-File3 | File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML | MS1 | 0.7456 | 0.1151 | 6.4806 | 15.4307 | 443.7275 |
| Data_StackZDPD | SZ-File3 | File3_QE-HFX-20190719_50cm_60min_OFe4_2.uncompressed.mzML | MS2 | 0.5555 | 0.1641 | 3.3844 | 29.5472 | 588.5223 |
| Data_StackZDPD | SZ-File4 | File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML | MS1 | 1.5574 | 0.2237 | 6.9621 | 14.3635 | 783.4446 |
| Data_StackZDPD | SZ-File4 | File4_QE-HFX-20190719_50cm_60min_Fr1.uncompressed.mzML | MS2 | 0.1558 | 0.0503 | 3.0976 | 32.2833 | 165.6500 |
| Data_StackZDPD | SZ-File5 | File5_S8184TPST_01.uncompressed.mzML | MS1 | 1.6736 | 0.2399 | 6.9765 | 14.3337 | 1062.5937 |
| Data_StackZDPD | SZ-File5 | File5_S8184TPST_01.uncompressed.mzML | MS2 | 0.4947 | 0.0819 | 6.0421 | 16.5506 | 849.0522 |
| Data_StackZDPD | SZ-File6 | File6_Negative_000333.uncompressed.mzML | MS1 | 0.4860 | 0.0571 | 8.5148 | 11.7442 | 273.7173 |
| Data_StackZDPD | SZ-File6 | File6_Negative_000333.uncompressed.mzML | MS2 | 0.1139 | 0.0164 | 6.9289 | 14.4322 | 234.6092 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | MS1 | 0.0433 | 0.0159 | 2.7242 | 36.7075 | 32.8855 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | LFQ_Orbitrap_AIF_Human_01.uncompressed.mzML | MS2 | 1.0660 | 0.3282 | 3.2480 | 30.7879 | 1092.7913 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML | MS1 | 0.0437 | 0.0160 | 2.7342 | 36.5740 | 31.2705 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | LFQ_Orbitrap_AIF_Human_02.uncompressed.mzML | MS2 | 0.9788 | 0.3020 | 3.2412 | 30.8531 | 1021.2599 |
| Data_StackZDPD | SZ-Set_1_F2 | Set 1_F2.uncompressed.mzML | MS1 | 0.1103 | 0.0333 | 3.3171 | 30.1471 | 110.0634 |
| Data_StackZDPD | SZ-Set_1_F2 | Set 1_F2.uncompressed.mzML | MS2 | 0.1356 | 0.0377 | 3.5921 | 27.8386 | 147.1815 |
| Full8 | F8-DDA-true | 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML | MS1 | 0.9953 | 0.1217 | 8.1773 | 12.2290 | 5944.3004 |
| Full8 | F8-DDA-true | 01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML | MS2 | 0.2783 | 0.0834 | 3.3347 | 29.9876 | 368.3734 |
| Full8 | F8-DDA-true | QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 4.5574 | 0.5393 | 8.4512 | 11.8326 | 3207.5502 |
| Full8 | F8-DDA-true | QC_E4806_240522_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 0.0122 | 0.0043 | 2.8543 | 35.0345 | 14.5161 |
| Full8 | F8-DDA-true | QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 2.4479 | 0.3674 | 6.6627 | 15.0089 | 1762.4339 |
| Full8 | F8-DDA-true | QC_E4804_240429_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 0.7435 | 0.2383 | 3.1202 | 32.0493 | 1084.7172 |
| Full8 | F8-DDA-true | QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 1.3571 | 0.2127 | 6.3799 | 15.6743 | 1116.8658 |
| Full8 | F8-DDA-true | QC_E4804_240507_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 2.2499 | 0.6559 | 3.4300 | 29.1543 |  |
| Full8 | F8-DDA-true | QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 2.4379 | 0.3527 | 6.9117 | 14.4682 | 1795.8348 |
| Full8 | F8-DDA-true | QC_E4801_240408_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 0.6952 | 0.2219 | 3.1335 | 31.9134 | 997.0946 |
| Full8 | F8-DDA-true | QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 1.2835 | 0.1963 | 6.5379 | 15.2955 | 909.7222 |
| Full8 | F8-DDA-true | QC_E4804_240628_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 2.2419 | 0.6402 | 3.5018 | 28.5566 |  |
| Full8 | F8-DDA-true | QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 1.3354 | 0.2077 | 6.4309 | 15.5499 | 914.3184 |
| Full8 | F8-DDA-true | QC_E4804_240226_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 2.1593 | 0.6209 | 3.4779 | 28.7531 |  |
| Full8 | F8-DDA-true | QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 2.2644 | 0.3346 | 6.7668 | 14.7780 | 1291.6955 |
| Full8 | F8-DDA-true | QC_E4805_240426_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 0.7882 | 0.2433 | 3.2395 | 30.8686 | 1960.8063 |
| Full8 | F8-DDA-true | QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 1.3128 | 0.2038 | 6.4402 | 15.5276 | 918.0148 |
| Full8 | F8-DDA-true | QC_E4804_240308_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 2.1181 | 0.6084 | 3.4814 | 28.7243 |  |
| Full8 | F8-DDA-true | QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 2.2245 | 0.3307 | 6.7265 | 14.8665 | 1371.3950 |
| Full8 | F8-DDA-true | QC_E4805_240416_DDA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 0.7934 | 0.2531 | 3.1351 | 31.8969 | 915.8558 |
| Full8 | F8-DDA-true | QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML | MS1 | 2.5940 | 0.3613 | 7.1802 | 13.9272 | 1635.0582 |
| Full8 | F8-DDA-true | QC_E4806_240709_DDA_293T_200ng_120min_R1.true_uncompressed.mzML | MS2 | 0.3846 | 0.1244 | 3.0916 | 32.3459 | 448.0890 |
| Full8 | F8-E4802 | QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML | MS1 | 1.2707 | 0.1957 | 6.4914 | 15.4049 | 6633.5477 |
| Full8 | F8-E4802 | QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML | MS2 | 1.6851 | 0.4916 | 3.4278 | 29.1731 |  |
| Full8 | F8-E4802 | QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML | MS1 | 2.0957 | 0.2986 | 7.0180 | 14.2491 | 1270.6325 |
| Full8 | F8-E4802 | QC_E4802_240703_DDA_293T_500ng_60min_R1.true_uncompressed.mzML | MS2 | 0.4553 | 0.1469 | 3.0982 | 32.2765 | 585.3236 |
| Full8 | F8-E4802 | QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML | MS1 | 0.9669 | 0.1503 | 6.4327 | 15.5456 | 617.9157 |
| Full8 | F8-E4802 | QC_E4802_240511_DIA_293T_500ng_60min_26w_R1.true_uncompressed.mzML | MS2 | 1.1449 | 0.3421 | 3.3466 | 29.8813 |  |
| Full8 | F8-E4802 | QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML | MS1 | 0.9498 | 0.1475 | 6.4394 | 15.5293 | 608.9359 |
| Full8 | F8-E4802 | QC_E4802_240508_DIA_293T_500ng_60min_26w_R3.true_uncompressed.mzML | MS2 | 1.1460 | 0.3420 | 3.3507 | 29.8441 |  |
| Full8 | F8-E4804-R1 | QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML | MS1 | 2.1814 | 0.3296 | 6.6179 | 15.1106 |  |
| Full8 | F8-E4804-R1 | QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML | MS2 | 0.5897 | 0.1893 | 3.1145 | 32.1075 | 0.0000 |
| Full8 | F8-E4804-R1 | QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS1 | 1.2700 | 0.1939 | 6.5495 | 15.2684 | 868.2714 |
| Full8 | F8-E4804-R1 | QC_E4804_240320_DIA_293T_500ng_120min_R1.true_uncompressed.mzML | MS2 | 1.8615 | 0.5306 | 3.5083 | 28.5041 |  |
| Full8 | F8-E4804-R2 | QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML | MS1 | 2.4267 | 0.3592 | 6.7549 | 14.8041 |  |
| Full8 | F8-E4804-R2 | QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML | MS2 | 0.5438 | 0.1748 | 3.1116 | 32.1378 | 568.0137 |
| Full8 | F8-E4804-cent | QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML | MS1 | 0.1205 | 0.0477 | 2.5253 | 39.5990 | 153.4694 |
| Full8 | F8-E4804-cent | QC_E4804_240320_DDA_293T_500ng_120min_R1_centroided.mzML | MS2 | 0.5897 | 0.1893 | 3.1145 | 32.1075 | 785.9672 |
| Full8 | F8-E4805-DIA | QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML | MS1 | 1.2117 | 0.1636 | 7.4067 | 13.5013 | 636.7975 |
| Full8 | F8-E4805-DIA | QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML | MS2 | 0.6493 | 0.1773 | 3.6623 | 27.3051 |  |
| Full8 | F8-E4805-R2 | QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML | MS1 | 1.2032 | 0.1835 | 6.5582 | 15.2480 | 5738.6896 |
| Full8 | F8-E4805-R2 | QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML | MS2 | 0.6134 | 0.1952 | 3.1423 | 31.8243 | 716.1644 |
| Full8 | F8-E4805-R2 | QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML | MS1 | 1.2572 | 0.1889 | 6.6567 | 15.0225 | 765.0698 |
| Full8 | F8-E4805-R2 | QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R1.true_uncompressed.mzML | MS2 | 0.6155 | 0.1901 | 3.2371 | 30.8916 | 1614.7987 |
| Full8 | F8-ETD | 01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML | MS1 | 0.9102 | 0.1073 | 8.4835 | 11.7875 | 5726.9815 |
| Full8 | F8-ETD | 01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML | MS2 | 0.0142 | 0.0052 | 2.7546 | 36.3034 | 18.6009 |

## Component Breakdown

This table records TrackCodec components present in the stats files. It is diagnostic and should not be mixed with the MS1/MS2 aggregate denominators without checking component semantics.

| Dataset | Label | Component | Raw GiB | Compressed GiB | CR | Rate % |
|---|---|---|---|---|---|---|
| Data_StackZDPD | SZ-File13 | MS1 core | 2.6404 | 0.2190 | 12.0551 | 8.2953 |
| Data_StackZDPD | SZ-File13 | MS2 core | 0.0000 | 0.0000 | 0.0000 |  |
| Data_StackZDPD | SZ-File13 | Metadata XML | 0.0417 | 0.0008 | 53.5172 | 1.8686 |
| Data_StackZDPD | SZ-File13 | Auxiliary | 0.0003 | 0.0001 | 2.3559 | 42.4474 |
| Data_StackZDPD | SZ-File13 | MS1 full m/z sidecar | 0.6602 | 0.0822 | 8.0286 | 12.4554 |
| Data_StackZDPD | SZ-File13 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File14 | MS1 core | 0.0433 | 0.0089 | 4.8653 | 20.5537 |
| Data_StackZDPD | SZ-File14 | MS2 core | 1.0660 | 0.3282 | 3.2480 | 30.7879 |
| Data_StackZDPD | SZ-File14 | Metadata XML | 1.1646 | 0.0177 | 65.9124 | 1.5172 |
| Data_StackZDPD | SZ-File14 | Auxiliary | 0.0121 | 0.0026 | 4.6039 | 21.7209 |
| Data_StackZDPD | SZ-File14 | MS1 full m/z sidecar | 0.0108 | 0.0070 | 1.5511 | 64.4721 |
| Data_StackZDPD | SZ-File14 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File15 | MS1 core | 7.9470 | 0.8219 | 9.6695 | 10.3418 |
| Data_StackZDPD | SZ-File15 | MS2 core | 4.1902 | 0.6880 | 6.0905 | 16.4190 |
| Data_StackZDPD | SZ-File15 | Metadata XML | 0.6882 | 0.0151 | 45.5726 | 2.1943 |
| Data_StackZDPD | SZ-File15 | Auxiliary | 0.0095 | 0.0020 | 4.6659 | 21.4321 |
| Data_StackZDPD | SZ-File15 | MS1 full m/z sidecar | 1.9869 | 0.3041 | 6.5329 | 15.3072 |
| Data_StackZDPD | SZ-File15 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File16 | MS1 core | 2.7342 | 0.1278 | 21.3917 | 4.6747 |
| Data_StackZDPD | SZ-File16 | MS2 core | 4.8336 | 0.4155 | 11.6325 | 8.5966 |
| Data_StackZDPD | SZ-File16 | Metadata XML | 0.1207 | 0.0019 | 63.6265 | 1.5717 |
| Data_StackZDPD | SZ-File16 | Auxiliary | 0.0047 | 0.0003 | 13.9259 | 7.1809 |
| Data_StackZDPD | SZ-File16 | MS1 full m/z sidecar | 0.6836 | 0.0954 | 7.1689 | 13.9492 |
| Data_StackZDPD | SZ-File16 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File18 | MS1 core | 4.3169 | 0.3186 | 13.5505 | 7.3798 |
| Data_StackZDPD | SZ-File18 | MS2 core | 12.0194 | 1.1170 | 10.7599 | 9.2937 |
| Data_StackZDPD | SZ-File18 | Metadata XML | 0.2330 | 0.0037 | 63.0126 | 1.5870 |
| Data_StackZDPD | SZ-File18 | Auxiliary | 0.0058 | 0.0005 | 11.0194 | 9.0749 |
| Data_StackZDPD | SZ-File18 | MS1 full m/z sidecar | 1.0792 | 0.1748 | 6.1737 | 16.1979 |
| Data_StackZDPD | SZ-File18 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File2 | MS1 core | 5.3864 | 0.1454 | 37.0324 | 2.7003 |
| Data_StackZDPD | SZ-File2 | MS2 core | 3.8450 | 0.3848 | 9.9914 | 10.0086 |
| Data_StackZDPD | SZ-File2 | Metadata XML | 0.1734 | 0.0027 | 65.1185 | 1.5357 |
| Data_StackZDPD | SZ-File2 | Auxiliary | 0.0048 | 0.0004 | 13.5920 | 7.3573 |
| Data_StackZDPD | SZ-File2 | MS1 full m/z sidecar | 1.3466 | 0.2247 | 5.9936 | 16.6845 |
| Data_StackZDPD | SZ-File2 | MS1 orphan intensity sidecar | 1.0490 | 0.0765 | 13.7125 | 7.2926 |
| Data_StackZDPD | SZ-File3 | MS1 core | 0.7456 | 0.0833 | 8.9549 | 11.1671 |
| Data_StackZDPD | SZ-File3 | MS2 core | 0.5555 | 0.1641 | 3.3844 | 29.5472 |
| Data_StackZDPD | SZ-File3 | Metadata XML | 0.2182 | 0.0035 | 62.8129 | 1.5920 |
| Data_StackZDPD | SZ-File3 | Auxiliary | 0.0011 | 0.0004 | 2.6924 | 37.1413 |
| Data_StackZDPD | SZ-File3 | MS1 full m/z sidecar | 0.1864 | 0.0318 | 5.8641 | 17.0528 |
| Data_StackZDPD | SZ-File3 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File4 | MS1 core | 1.5574 | 0.1629 | 9.5596 | 10.4606 |
| Data_StackZDPD | SZ-File4 | MS2 core | 0.1558 | 0.0503 | 3.0976 | 32.2833 |
| Data_StackZDPD | SZ-File4 | Metadata XML | 0.3140 | 0.0064 | 48.8330 | 2.0478 |
| Data_StackZDPD | SZ-File4 | Auxiliary | 0.0016 | 0.0005 | 2.8795 | 34.7280 |
| Data_StackZDPD | SZ-File4 | MS1 full m/z sidecar | 0.3894 | 0.0608 | 6.4064 | 15.6094 |
| Data_StackZDPD | SZ-File4 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File5 | MS1 core | 1.6736 | 0.1726 | 9.6988 | 10.3105 |
| Data_StackZDPD | SZ-File5 | MS2 core | 0.4947 | 0.0819 | 6.0421 | 16.5506 |
| Data_StackZDPD | SZ-File5 | Metadata XML | 0.1167 | 0.0028 | 42.2130 | 2.3689 |
| Data_StackZDPD | SZ-File5 | Auxiliary | 0.0006 | 0.0003 | 2.3748 | 42.1081 |
| Data_StackZDPD | SZ-File5 | MS1 full m/z sidecar | 0.4184 | 0.0673 | 6.2145 | 16.0914 |
| Data_StackZDPD | SZ-File5 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-File6 | MS1 core | 0.4860 | 0.0393 | 12.3578 | 8.0920 |
| Data_StackZDPD | SZ-File6 | MS2 core | 0.1139 | 0.0164 | 6.9289 | 14.4322 |
| Data_StackZDPD | SZ-File6 | Metadata XML | 0.1632 | 0.0032 | 51.1261 | 1.9559 |
| Data_StackZDPD | SZ-File6 | Auxiliary | 0.0008 | 0.0003 | 2.5362 | 39.4297 |
| Data_StackZDPD | SZ-File6 | MS1 full m/z sidecar | 0.1215 | 0.0177 | 6.8467 | 14.6055 |
| Data_StackZDPD | SZ-File6 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS1 core | 0.0433 | 0.0089 | 4.8653 | 20.5537 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS2 core | 1.0660 | 0.3282 | 3.2480 | 30.7879 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | Metadata XML | 1.1655 | 0.0177 | 65.9996 | 1.5152 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | Auxiliary | 0.0043 | 0.0031 | 1.4012 | 71.3658 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS1 full m/z sidecar | 0.0108 | 0.0070 | 1.5511 | 64.4721 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS1 core | 0.0437 | 0.0089 | 4.8906 | 20.4472 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS2 core | 0.9788 | 0.3020 | 3.2412 | 30.8531 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | Metadata XML | 1.1658 | 0.0176 | 66.1916 | 1.5108 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | Auxiliary | 0.0043 | 0.0031 | 1.4017 | 71.3425 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS1 full m/z sidecar | 0.0110 | 0.0070 | 1.5536 | 64.3659 |
| Data_StackZDPD | SZ-LFQ_Orbitrap_AIF_H | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Data_StackZDPD | SZ-Set_1_F2 | MS1 core | 0.1103 | 0.0129 | 8.5508 | 11.6949 |
| Data_StackZDPD | SZ-Set_1_F2 | MS2 core | 0.1356 | 0.0377 | 3.5921 | 27.8386 |
| Data_StackZDPD | SZ-Set_1_F2 | Metadata XML | 0.0672 | 0.0017 | 39.6023 | 2.5251 |
| Data_StackZDPD | SZ-Set_1_F2 | Auxiliary | 0.0016 | 0.0003 | 5.9141 | 16.9089 |
| Data_StackZDPD | SZ-Set_1_F2 | MS1 full m/z sidecar | 0.0276 | 0.0204 | 1.3577 | 73.6549 |
| Data_StackZDPD | SZ-Set_1_F2 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 0.9953 | 0.0836 | 11.9007 | 8.4029 |
| Full8 | F8-DDA-true | MS2 core | 0.2783 | 0.0834 | 3.3347 | 29.9876 |
| Full8 | F8-DDA-true | Metadata XML | 0.2685 | 0.0053 | 50.1954 | 1.9922 |
| Full8 | F8-DDA-true | Auxiliary | 0.0014 | 0.0006 | 2.4090 | 41.5108 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.2488 | 0.0381 | 6.5350 | 15.3022 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 4.5574 | 0.3911 | 11.6524 | 8.5819 |
| Full8 | F8-DDA-true | MS2 core | 0.0122 | 0.0043 | 2.8543 | 35.0345 |
| Full8 | F8-DDA-true | Metadata XML | 0.3329 | 0.0075 | 44.1092 | 2.2671 |
| Full8 | F8-DDA-true | Auxiliary | 0.0141 | 0.0024 | 6.0071 | 16.6470 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 1.1396 | 0.1481 | 7.6926 | 12.9995 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 2.4479 | 0.2641 | 9.2685 | 10.7893 |
| Full8 | F8-DDA-true | MS2 core | 0.7435 | 0.2383 | 3.1202 | 32.0493 |
| Full8 | F8-DDA-true | Metadata XML | 0.9671 | 0.0227 | 42.6877 | 2.3426 |
| Full8 | F8-DDA-true | Auxiliary | 0.0119 | 0.0027 | 4.4211 | 22.6186 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.6120 | 0.1033 | 5.9254 | 16.8765 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 1.3571 | 0.1568 | 8.6545 | 11.5546 |
| Full8 | F8-DDA-true | MS2 core | 2.2499 | 0.6559 | 3.4300 | 29.1543 |
| Full8 | F8-DDA-true | Metadata XML | 0.5116 | 0.0076 | 67.5536 | 1.4803 |
| Full8 | F8-DDA-true | Auxiliary | 0.0098 | 0.0020 | 4.8053 | 20.8106 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.3393 | 0.0559 | 6.0688 | 16.4776 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 2.4379 | 0.2505 | 9.7325 | 10.2748 |
| Full8 | F8-DDA-true | MS2 core | 0.6952 | 0.2219 | 3.1335 | 31.9134 |
| Full8 | F8-DDA-true | Metadata XML | 0.9796 | 0.0228 | 43.0087 | 2.3251 |
| Full8 | F8-DDA-true | Auxiliary | 0.0118 | 0.0027 | 4.3957 | 22.7495 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.6095 | 0.1022 | 5.9624 | 16.7717 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 1.2835 | 0.1437 | 8.9286 | 11.2000 |
| Full8 | F8-DDA-true | MS2 core | 2.2419 | 0.6402 | 3.5018 | 28.5566 |
| Full8 | F8-DDA-true | Metadata XML | 0.5089 | 0.0074 | 68.5490 | 1.4588 |
| Full8 | F8-DDA-true | Auxiliary | 0.0097 | 0.0020 | 4.7812 | 20.9153 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.3209 | 0.0526 | 6.1047 | 16.3807 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 1.3354 | 0.1530 | 8.7254 | 11.4608 |
| Full8 | F8-DDA-true | MS2 core | 2.1593 | 0.6209 | 3.4779 | 28.7531 |
| Full8 | F8-DDA-true | Metadata XML | 0.5101 | 0.0074 | 68.6307 | 1.4571 |
| Full8 | F8-DDA-true | Auxiliary | 0.0096 | 0.0020 | 4.7625 | 20.9972 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.3339 | 0.0546 | 6.1142 | 16.3553 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 2.2644 | 0.2431 | 9.3159 | 10.7343 |
| Full8 | F8-DDA-true | MS2 core | 0.7882 | 0.2433 | 3.2395 | 30.8686 |
| Full8 | F8-DDA-true | Metadata XML | 0.9151 | 0.0214 | 42.8169 | 2.3355 |
| Full8 | F8-DDA-true | Auxiliary | 0.0147 | 0.0029 | 5.0114 | 19.9547 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.5662 | 0.0916 | 6.1834 | 16.1722 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 1.3128 | 0.1504 | 8.7307 | 11.4539 |
| Full8 | F8-DDA-true | MS2 core | 2.1181 | 0.6084 | 3.4814 | 28.7243 |
| Full8 | F8-DDA-true | Metadata XML | 0.5096 | 0.0074 | 68.7538 | 1.4545 |
| Full8 | F8-DDA-true | Auxiliary | 0.0098 | 0.0021 | 4.7306 | 21.1388 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.3282 | 0.0535 | 6.1374 | 16.2935 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 2.2245 | 0.2395 | 9.2861 | 10.7688 |
| Full8 | F8-DDA-true | MS2 core | 0.7934 | 0.2531 | 3.1351 | 31.8969 |
| Full8 | F8-DDA-true | Metadata XML | 0.9144 | 0.0214 | 42.8024 | 2.3363 |
| Full8 | F8-DDA-true | Auxiliary | 0.0147 | 0.0029 | 5.0270 | 19.8927 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.5562 | 0.0912 | 6.1020 | 16.3881 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-DDA-true | MS1 core | 2.5940 | 0.2596 | 9.9916 | 10.0084 |
| Full8 | F8-DDA-true | MS2 core | 0.3846 | 0.1244 | 3.0916 | 32.3459 |
| Full8 | F8-DDA-true | Metadata XML | 0.9196 | 0.0214 | 43.0671 | 2.3220 |
| Full8 | F8-DDA-true | Auxiliary | 0.0149 | 0.0030 | 5.0524 | 19.7925 |
| Full8 | F8-DDA-true | MS1 full m/z sidecar | 0.6486 | 0.1017 | 6.3804 | 15.6731 |
| Full8 | F8-DDA-true | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4802 | MS1 core | 1.2707 | 0.1441 | 8.8154 | 11.3438 |
| Full8 | F8-E4802 | MS2 core | 1.6851 | 0.4916 | 3.4278 | 29.1731 |
| Full8 | F8-E4802 | Metadata XML | 0.4542 | 0.0069 | 66.2204 | 1.5101 |
| Full8 | F8-E4802 | Auxiliary | 0.0028 | 0.0020 | 1.4103 | 70.9050 |
| Full8 | F8-E4802 | MS1 full m/z sidecar | 0.3177 | 0.0516 | 6.1565 | 16.2430 |
| Full8 | F8-E4802 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4802 | MS1 core | 2.0957 | 0.2193 | 9.5563 | 10.4644 |
| Full8 | F8-E4802 | MS2 core | 0.4553 | 0.1469 | 3.0982 | 32.2765 |
| Full8 | F8-E4802 | Metadata XML | 0.5102 | 0.0120 | 42.5247 | 2.3516 |
| Full8 | F8-E4802 | Auxiliary | 0.0072 | 0.0016 | 4.5489 | 21.9831 |
| Full8 | F8-E4802 | MS1 full m/z sidecar | 0.5240 | 0.0793 | 6.6066 | 15.1363 |
| Full8 | F8-E4802 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4802 | MS1 core | 0.9669 | 0.1109 | 8.7166 | 11.4724 |
| Full8 | F8-E4802 | MS2 core | 1.1449 | 0.3421 | 3.3466 | 29.8813 |
| Full8 | F8-E4802 | Metadata XML | 0.3192 | 0.0046 | 69.0618 | 1.4480 |
| Full8 | F8-E4802 | Auxiliary | 0.0063 | 0.0013 | 4.7877 | 20.8869 |
| Full8 | F8-E4802 | MS1 full m/z sidecar | 0.2418 | 0.0394 | 6.1381 | 16.2917 |
| Full8 | F8-E4802 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4802 | MS1 core | 0.9498 | 0.1088 | 8.7277 | 11.4578 |
| Full8 | F8-E4802 | MS2 core | 1.1460 | 0.3420 | 3.3507 | 29.8441 |
| Full8 | F8-E4802 | Metadata XML | 0.3192 | 0.0046 | 68.9499 | 1.4503 |
| Full8 | F8-E4802 | Auxiliary | 0.0063 | 0.0013 | 4.7855 | 20.8963 |
| Full8 | F8-E4802 | MS1 full m/z sidecar | 0.2375 | 0.0387 | 6.1408 | 16.2845 |
| Full8 | F8-E4802 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4804-R1 | MS1 core | 2.1814 | 0.2359 | 9.2474 | 10.8138 |
| Full8 | F8-E4804-R1 | MS2 core | 0.5897 | 0.1893 | 3.1145 | 32.1075 |
| Full8 | F8-E4804-R1 | Metadata XML | 1.0063 | 0.0239 | 42.0914 | 2.3758 |
| Full8 | F8-E4804-R1 | Auxiliary | 0.0042 | 0.0030 | 1.3873 | 72.0819 |
| Full8 | F8-E4804-R1 | MS1 full m/z sidecar | 0.5454 | 0.0937 | 5.8191 | 17.1847 |
| Full8 | F8-E4804-R1 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4804-R1 | MS1 core | 1.2700 | 0.1417 | 8.9639 | 11.1558 |
| Full8 | F8-E4804-R1 | MS2 core | 1.8615 | 0.5306 | 3.5083 | 28.5041 |
| Full8 | F8-E4804-R1 | Metadata XML | 0.5068 | 0.0072 | 69.9987 | 1.4286 |
| Full8 | F8-E4804-R1 | Auxiliary | 0.0097 | 0.0020 | 4.7702 | 20.9635 |
| Full8 | F8-E4804-R1 | MS1 full m/z sidecar | 0.3175 | 0.0522 | 6.0794 | 16.4491 |
| Full8 | F8-E4804-R1 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4804-R2 | MS1 core | 2.4267 | 0.2564 | 9.4651 | 10.5651 |
| Full8 | F8-E4804-R2 | MS2 core | 0.5438 | 0.1748 | 3.1116 | 32.1378 |
| Full8 | F8-E4804-R2 | Metadata XML | 1.0031 | 0.0237 | 42.3091 | 2.3636 |
| Full8 | F8-E4804-R2 | Auxiliary | 0.0043 | 0.0031 | 1.3883 | 72.0306 |
| Full8 | F8-E4804-R2 | MS1 full m/z sidecar | 0.6067 | 0.1029 | 5.8984 | 16.9537 |
| Full8 | F8-E4804-R2 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4804-cent | MS1 core | 0.1205 | 0.0253 | 4.7733 | 20.9500 |
| Full8 | F8-E4804-cent | MS2 core | 0.5897 | 0.1893 | 3.1145 | 32.1075 |
| Full8 | F8-E4804-cent | Metadata XML | 0.8980 | 0.0239 | 37.5487 | 2.6632 |
| Full8 | F8-E4804-cent | Auxiliary | 0.0001 | 0.0000 | 2.0086 | 49.7853 |
| Full8 | F8-E4804-cent | MS1 full m/z sidecar | 0.0302 | 0.0225 | 1.3437 | 74.4207 |
| Full8 | F8-E4804-cent | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4805-DIA | MS1 core | 1.2117 | 0.1179 | 10.2736 | 9.7337 |
| Full8 | F8-E4805-DIA | MS2 core | 0.6493 | 0.1773 | 3.6623 | 27.3051 |
| Full8 | F8-E4805-DIA | Metadata XML | 0.2559 | 0.0036 | 71.2275 | 1.4040 |
| Full8 | F8-E4805-DIA | Auxiliary | 0.0028 | 0.0018 | 1.5688 | 63.7418 |
| Full8 | F8-E4805-DIA | MS1 full m/z sidecar | 0.3030 | 0.0457 | 6.6360 | 15.0694 |
| Full8 | F8-E4805-DIA | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4805-R2 | MS1 core | 1.2032 | 0.1355 | 8.8771 | 11.2649 |
| Full8 | F8-E4805-R2 | MS2 core | 0.6134 | 0.1952 | 3.1423 | 31.8243 |
| Full8 | F8-E4805-R2 | Metadata XML | 0.5093 | 0.0122 | 41.7063 | 2.3977 |
| Full8 | F8-E4805-R2 | Auxiliary | 0.0032 | 0.0021 | 1.5140 | 66.0522 |
| Full8 | F8-E4805-R2 | MS1 full m/z sidecar | 0.3008 | 0.0479 | 6.2773 | 15.9304 |
| Full8 | F8-E4805-R2 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-E4805-R2 | MS1 core | 1.2572 | 0.1394 | 9.0214 | 11.0847 |
| Full8 | F8-E4805-R2 | MS2 core | 0.6155 | 0.1901 | 3.2371 | 30.8916 |
| Full8 | F8-E4805-R2 | Metadata XML | 0.4796 | 0.0114 | 42.2373 | 2.3676 |
| Full8 | F8-E4805-R2 | Auxiliary | 0.0090 | 0.0018 | 5.0700 | 19.7239 |
| Full8 | F8-E4805-R2 | MS1 full m/z sidecar | 0.3143 | 0.0495 | 6.3496 | 15.7491 |
| Full8 | F8-E4805-R2 | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
| Full8 | F8-ETD | MS1 core | 0.9102 | 0.0721 | 12.6241 | 7.9214 |
| Full8 | F8-ETD | MS2 core | 0.0142 | 0.0052 | 2.7546 | 36.3034 |
| Full8 | F8-ETD | Metadata XML | 0.0673 | 0.0011 | 60.2240 | 1.6605 |
| Full8 | F8-ETD | Auxiliary | 0.0002 | 0.0001 | 1.3315 | 75.1010 |
| Full8 | F8-ETD | MS1 full m/z sidecar | 0.2276 | 0.0352 | 6.4669 | 15.4633 |
| Full8 | F8-ETD | MS1 orphan intensity sidecar | 0.0000 | 0.0000 |  |  |
