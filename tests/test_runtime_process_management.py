from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

import runtime
from production.common.io import _mzml_header_mentions_timstof


def test_pid_is_alive_uses_psutil_without_sending_a_signal() -> None:
    with (
        patch.object(runtime.psutil, "pid_exists", return_value=True) as pid_exists,
        patch.object(runtime.os, "kill", side_effect=AssertionError("must not signal")),
    ):
        assert runtime._pid_is_alive(1234) is True

    pid_exists.assert_called_once_with(1234)


def test_pid_is_alive_rejects_invalid_pid_without_querying_psutil() -> None:
    with patch.object(runtime.psutil, "pid_exists") as pid_exists:
        assert runtime._pid_is_alive(None) is False
        assert runtime._pid_is_alive(0) is False
        assert runtime._pid_is_alive(-1) is False

    pid_exists.assert_not_called()


def test_background_process_kwargs_are_platform_specific() -> None:
    with (
        patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True),
        patch.object(subprocess, "DETACHED_PROCESS", 0x8, create=True),
    ):
        assert runtime._background_process_kwargs("nt") == {
            "creationflags": 0x208
        }

    assert runtime._background_process_kwargs("posix") == {
        "start_new_session": True
    }


def test_mzml_header_probe_closes_the_file_handle(tmp_path: Path) -> None:
    mzml_path = tmp_path / "sample.mzML"
    mzml_path.write_bytes(b"<mzML>timsTOF</mzML>")
    handle = io.BytesIO(mzml_path.read_bytes())

    with patch.object(Path, "open", return_value=handle):
        assert _mzml_header_mentions_timstof(mzml_path) is True

    assert handle.closed
