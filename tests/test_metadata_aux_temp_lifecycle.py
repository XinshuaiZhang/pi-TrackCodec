import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from TrackCodec.production.mzml import archive_codec


def test_metadata_auxiliary_payloads_can_share_scoped_temp_root(tmp_path: Path) -> None:
    source = tmp_path / "source.mzML"
    source.write_bytes(b"test source")
    scoped_temp = Path(tempfile.mkdtemp(prefix="trackcodec-test-scope-"))
    try:
        with patch.object(
            archive_codec,
            "_extract_and_encode_metadata_auxiliary_timed",
            return_value=(b"metadata", {"backend": "test"}, b"aux", {"backend": "test"}, {}),
        ):
            metadata_path, _, auxiliary_path, _, _ = archive_codec._extract_and_encode_metadata_auxiliary_payloads_cached(
                source,
                metadata_codec=SimpleNamespace(backend="test"),
                cache_root=scoped_temp,
                metadata_xml_bytes=b"xml",
                auxiliary_records=[],
            )

        assert Path(metadata_path).is_relative_to(scoped_temp)
        assert Path(auxiliary_path).is_relative_to(scoped_temp)
        assert Path(metadata_path).read_bytes() == b"metadata"
        assert Path(auxiliary_path).read_bytes() == b"aux"
    finally:
        import shutil

        shutil.rmtree(scoped_temp)

    assert not scoped_temp.exists()
