from __future__ import annotations


TRACKCODEC_ARCHIVE_EXTENSION = ".tcarchive"


def archive_name(source_name: str, *, strict_q6: bool = False) -> str:
    suffix = ".strict_q6" if strict_q6 else ""
    return f"{source_name}{suffix}{TRACKCODEC_ARCHIVE_EXTENSION}"
