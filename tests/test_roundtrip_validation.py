from unittest.mock import patch

from TrackCodec.production.validation import roundtrip


class _FakeMzMLReader:
    def __init__(self, records):
        self.records = records

    def __enter__(self):
        return iter(self.records)

    def __exit__(self, exc_type, exc, tb):
        return False


def test_iter_spectra_excludes_chromatograms() -> None:
    records = [
        {
            "id": "chromatogram-1",
            "time array": [1.0, 2.0],
            "intensity array": [3.0, 4.0],
        },
        {
            "id": "scan=1",
            "ms level": 1,
            "scanList": {"scan": [{"scan start time": 1.5}]},
            "m/z array": [100.0],
            "intensity array": [10.0],
        },
        {
            "id": "scan=2",
            "ms level": 2,
            "scanList": {"scan": [{"scan start time": 2.5}]},
            "m/z array": [200.0],
            "intensity array": [20.0],
        },
    ]

    with patch.object(roundtrip.mzml, "MzML", return_value=_FakeMzMLReader(records)):
        spectra = list(roundtrip._iter_spectra("sample.mzML"))

    assert [spectrum["id"] for spectrum in spectra] == ["scan=1", "scan=2"]
    assert [spectrum["ms_level"] for spectrum in spectra] == [1, 2]
