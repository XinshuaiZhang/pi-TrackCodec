import os
from pathlib import Path


def _env_path(name: str, default: str) -> Path:
    return Path(os.environ.get(name, default))


DATA_ROOT = _env_path("TRACKCODEC_DATA_ROOT", "data/full8")
PXD004732_ROOT = _env_path(
    "TRACKCODEC_PXD004732_ROOT",
    "data/PXD004732/mzML",
)


CORE_PROFILE_FILES = [
    DATA_ROOT / "QC_E4802_240703_DIA_293T_500ng_90min_R1.mzML",
    DATA_ROOT / "QC_E4804_240320_DDA_293T_500ng_120min_R1.mzML",
    DATA_ROOT / "QC_E4804_240403_DDA_293T_500ng_120min_R2.mzML",
    DATA_ROOT / "QC_E4805_240328_DDA_293T_1ug_60min_NewCol_R2.mzML",
    DATA_ROOT / "QC_E4805_240709_DIA_293T_200ng_60min_R1.mzML",
]

PXD004732_PROFILE_FILES = [
    PXD004732_ROOT / "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.true_uncompressed.mzML",
    PXD004732_ROOT / "01625b_GA1-TUM_first_pool_1_01_01-DDA-1h-R2.uncompressed.mzML",
    PXD004732_ROOT / "01625b_GA1-TUM_first_pool_1_01_01-ETD-1h-R2.uncompressed.mzML",
]

FULL8_PROFILE_FILES = CORE_PROFILE_FILES + PXD004732_PROFILE_FILES
FULL8_EXTRA3_PROFILE_FILES = [path for path in FULL8_PROFILE_FILES if path not in CORE_PROFILE_FILES]

DIA_FILES = [path for path in FULL8_PROFILE_FILES if "DIA" in path.name.upper()]
DDA_FILES = [path for path in FULL8_PROFILE_FILES if path not in DIA_FILES]
