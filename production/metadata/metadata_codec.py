"""Codec for mzML metadata excluding binary array payloads."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Tuple

from ..common.compression_backends import compress, decompress


_BINARY_OPEN_RE = re.compile(br"<((?:[A-Za-z_][A-Za-z0-9_.-]*:)?binary)(?=[\s>/])")
_ENCODED_LENGTH_RE = re.compile(br'encodedLength\s*=\s*("|\')[^"\']*\1')


def _rewrite_encoded_length_attrs(blob: bytes) -> bytes:
    return _ENCODED_LENGTH_RE.sub(b'encodedLength="0"', blob)


class BinaryStrippedMetadataAccumulator:
    """Incrementally build mzML XML with binary payload text removed.

    The stripping logic is byte-oriented to preserve non-binary XML formatting.
    It is shared by the standalone metadata extractor and by the fused archive
    scan-store builder that tees raw mzML bytes into this accumulator while
    lxml decodes spectra.
    """

    def __init__(self):
        self.out = bytearray()
        self.buf = bytearray()
        self.in_binary = False
        self.binary_close = b""

    def _flush_normal(self, final: bool = False) -> None:
        while self.buf:
            match = _BINARY_OPEN_RE.search(self.buf)
            if match is None:
                if final:
                    safe = len(self.buf)
                else:
                    # Keep enough tail to match a split "<binary" token and
                    # keep an incomplete XML tag for encodedLength rewriting.
                    safe = max(0, len(self.buf) - 16)
                    last_lt = self.buf.rfind(b"<", 0, safe)
                    last_gt = self.buf.rfind(b">", 0, safe)
                    if last_lt > last_gt:
                        safe = last_lt
                if safe <= 0:
                    return
                self.out.extend(_rewrite_encoded_length_attrs(bytes(self.buf[:safe])))
                del self.buf[:safe]
                continue

            if match.start() > 0:
                self.out.extend(_rewrite_encoded_length_attrs(bytes(self.buf[: match.start()])))
                del self.buf[: match.start()]
                continue

            tag_end = self.buf.find(b">")
            if tag_end < 0:
                return
            binary_tag_name = bytes(match.group(1))
            open_tag = bytes(self.buf[: tag_end + 1])
            self.out.extend(open_tag)
            del self.buf[: tag_end + 1]
            if open_tag.rstrip().endswith(b"/>"):
                continue
            self.in_binary = True
            self.binary_close = b"</" + binary_tag_name + b">"
            return

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.buf.extend(chunk)
        while True:
            if self.in_binary:
                close_pos = self.buf.find(self.binary_close)
                if close_pos < 0:
                    close_keep = max(0, len(self.binary_close) - 1)
                    if len(self.buf) > close_keep:
                        del self.buf[: len(self.buf) - close_keep]
                    break
                self.out.extend(self.binary_close)
                del self.buf[: close_pos + len(self.binary_close)]
                self.in_binary = False
                self.binary_close = b""
                continue
            before = len(self.buf)
            self._flush_normal(final=False)
            if self.in_binary:
                continue
            if len(self.buf) == before:
                break

    def finish(self, mzml_path: str | Path = "<stream>") -> bytes:
        while self.buf or self.in_binary:
            if self.in_binary:
                close_pos = self.buf.find(self.binary_close)
                if close_pos < 0:
                    raise ValueError(f"unterminated <binary> element while stripping metadata: {mzml_path}")
                self.out.extend(self.binary_close)
                del self.buf[: close_pos + len(self.binary_close)]
                self.in_binary = False
                self.binary_close = b""
                continue
            before = len(self.buf)
            self._flush_normal(final=True)
            if self.in_binary:
                continue
            if len(self.buf) == before:
                if self.buf:
                    self.out.extend(_rewrite_encoded_length_attrs(bytes(self.buf)))
                    self.buf.clear()
                break
        return bytes(self.out)


def extract_binary_stripped_metadata_xml(mzml_path: str | Path) -> bytes:
    """Return mzML XML with binary payload text removed.

    This intentionally avoids building an lxml tree.  Large mzML files can
    contain gigabytes of base64 binary text, and tree parsing those files makes
    metadata-only benchmarks and archive metadata extraction memory-bound.
    """

    accumulator = BinaryStrippedMetadataAccumulator()
    with Path(mzml_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            accumulator.feed(chunk)
    return accumulator.finish(mzml_path)


class MzMLMetadataCodec:
    def __init__(self, backend: str = "zstd-9"):
        self.backend = backend

    def encode_bytes(self, raw: bytes) -> Tuple[bytes, Dict]:
        payload = compress(raw, self.backend)
        meta = {
            "backend": self.backend,
            "raw_bytes": len(raw),
            "compressed_bytes": len(payload),
            "compression_ratio": len(raw) / len(payload) if len(payload) else 0.0,
            "format": "binary_stripped_mzml_xml",
        }
        return payload, meta

    def encode_file(self, mzml_path: str | Path) -> Tuple[bytes, Dict]:
        raw = extract_binary_stripped_metadata_xml(mzml_path)
        return self.encode_bytes(raw)

    def decode_to_bytes(self, payload: bytes, meta: Dict) -> bytes:
        return decompress(payload, meta["backend"])

    def decode_to_text(self, payload: bytes, meta: Dict) -> str:
        return self.decode_to_bytes(payload, meta).decode("utf-8")

    def decode_to_json(self, payload: bytes, meta: Dict) -> Dict:
        return {
            "format": meta.get("format", "binary_stripped_mzml_xml"),
            "xml_text": self.decode_to_text(payload, meta),
        }
