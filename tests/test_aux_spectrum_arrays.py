import base64
import struct
from pathlib import Path

from TrackCodec.production.common.scan_store import create_memmap_scan_store_with_metadata_from_mzml
from TrackCodec.production.mzml.reconstruction import (
    NS,
    _iter_aux_binary_arrays,
    _parse_root,
    extract_binary_stripped_metadata_and_auxiliary_records,
    restore_auxiliary_binary_records,
)
from TrackCodec.production.validation.roundtrip import _xml_structure_summary, evaluate_roundtrip


NS_URI = "http://psi.hupo.org/ms/mzml"


def _binary_array(accession: str, value: float, *, include_binary: bool = True) -> str:
    text = base64.b64encode(struct.pack("<d", value)).decode("ascii")
    binary_text = text if include_binary else ""
    return (
        f'<binaryDataArray encodedLength="{len(text)}" arrayLength="1">'
        f'<cvParam accession="{accession}"/><cvParam accession="MS:1000523"/>'
        f'<cvParam accession="MS:1000576"/><binary>{binary_text}</binary></binaryDataArray>'
    )


def _write_fixture(path: Path, *, preserve_wavelength: bool = True) -> None:
    ms1_arrays = _binary_array("MS:1000514", 100.0) + _binary_array("MS:1000515", 10.0)
    wavelength_arrays = (
        _binary_array("MS:1000617", 500.0) + _binary_array("MS:1000515", 20.0)
        if preserve_wavelength
        else _binary_array("MS:1000617", 500.0, include_binary=False)
        + _binary_array("MS:1000515", 20.0, include_binary=False)
    )
    chromatogram_array = _binary_array("MS:1000595", 1.0)
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<mzML xmlns="{NS_URI}" version="1.1.0">
  <run>
    <spectrumList count="2">
      <spectrum id="ms1" defaultArrayLength="1">
        <cvParam accession="MS:1000511" value="1"/>
        <binaryDataArrayList count="2">{ms1_arrays}</binaryDataArrayList>
      </spectrum>
      <spectrum id="wavelength" defaultArrayLength="1">
        <cvParam accession="MS:1000804"/>
        <binaryDataArrayList count="2">{wavelength_arrays}</binaryDataArrayList>
      </spectrum>
    </spectrumList>
    <chromatogramList count="1">
      <chromatogram id="tic" defaultArrayLength="1">
        <binaryDataArrayList count="1">{chromatogram_array}</binaryDataArrayList>
      </chromatogram>
    </chromatogramList>
  </run>
</mzML>'''
    path.write_text(xml, encoding="utf-8")


def test_fused_scan_store_preserves_non_ms_spectrum_arrays(tmp_path: Path) -> None:
    source = tmp_path / "source.mzML"
    _write_fixture(source)

    store, metadata_xml, records = create_memmap_scan_store_with_metadata_from_mzml(
        source, "DDA", tmp_path / "store"
    )
    try:
        assert store.ms1_count == 1
        assert [record.get("spectrum_id") for record in records if record.get("spectrum_id")] == [
            "wavelength",
            "wavelength",
        ]
        assert [record.get("chromatogram_id") for record in records if record.get("chromatogram_id")] == ["tic"]

        fallback_records = extract_binary_stripped_metadata_and_auxiliary_records(str(source))[1]
        assert [record["binary_text"] for record in records] == [record["binary_text"] for record in fallback_records]

        root = _parse_root(metadata_xml)
        aux_arrays = _iter_aux_binary_arrays(root)
        assert len(aux_arrays) == len(records) == 3
        restore_auxiliary_binary_records(root, records)
        restored = [
            array.find("./mzml:binary", namespaces=NS).text
            for array in _iter_aux_binary_arrays(root)
        ]
        assert restored == [record["binary_text"] for record in records]
    finally:
        store.close()


def test_roundtrip_summary_counts_aux_spectrum_arrays_and_detects_loss(tmp_path: Path) -> None:
    original = tmp_path / "original.mzML"
    reconstructed = tmp_path / "reconstructed.mzML"
    _write_fixture(original)
    _write_fixture(reconstructed, preserve_wavelength=False)

    original_summary = _xml_structure_summary(original)
    reconstructed_summary = _xml_structure_summary(reconstructed)
    assert original_summary["aux_binary_array_count"] == 3
    assert reconstructed_summary["aux_binary_array_count"] == 3
    assert original_summary["aux_binary_nonempty_count"] == 3
    assert reconstructed_summary["aux_binary_nonempty_count"] == 1
    assert original_summary["aux_binary_raw_sha256"] != reconstructed_summary["aux_binary_raw_sha256"]

    result = evaluate_roundtrip(
        {
            "max_abs_mz_error": 0.0,
            "max_abs_intensity_error": 0.0,
            "effective_intensity_error_ceiling": 0.05,
            "aux_counts_match": True,
            "aux_binary_raw_equal": False,
        }
    )
    assert result["auxiliary_within_ceiling"] is False
    assert result["roundtrip_pass"] is False
