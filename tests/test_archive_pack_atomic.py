from pathlib import Path

import pytest

from TrackCodec.production.mzml.archive_codec import _pack_named_section_files


def test_pack_named_section_files_replaces_destination_after_success(tmp_path: Path) -> None:
    section = tmp_path / "section.bin"
    section.write_bytes(b"payload")
    archive = tmp_path / "result.tcarchive"
    archive.write_bytes(b"old archive")

    size = _pack_named_section_files([("section", section)], archive)

    assert archive.stat().st_size == size
    assert archive.read_bytes().startswith(b"TCARCH\0\0")
    assert not list(tmp_path.glob(".result.tcarchive.tmp.*"))


def test_pack_named_section_files_preserves_previous_destination_on_failure(tmp_path: Path) -> None:
    archive = tmp_path / "result.tcarchive"
    archive.write_bytes(b"old archive")
    missing_section = tmp_path / "missing.bin"

    with pytest.raises(FileNotFoundError):
        _pack_named_section_files([("missing", missing_section)], archive)

    assert archive.read_bytes() == b"old archive"
    assert not list(tmp_path.glob(".result.tcarchive.tmp.*"))
