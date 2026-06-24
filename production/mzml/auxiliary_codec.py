"""Auxiliary/chromatogram binary codec for whole-file mzML archives."""

from __future__ import annotations

import base64
import json
import struct
from hashlib import sha1
from typing import Dict, List, Sequence

from ..common.compression_backends import compress, decompress
from .reconstruction import NS, _iter_aux_binary_arrays, _parse_root


AUX_MAGIC = b"TCAUX\0\0\0"

TIME_ARRAY_ACCESSIONS = {
    "MS:1000595",  # time array
}
INTENSITY_ARRAY_ACCESSIONS = {
    "MS:1000515",  # intensity array
}
FLOAT32_ACCESSIONS = {
    "MS:1000521",  # 32-bit float
}
FLOAT64_ACCESSIONS = {
    "MS:1000523",  # 64-bit float
}
ZLIB_COMPRESSION_ACCESSIONS = {
    "MS:1000574",  # zlib compression
}
NO_COMPRESSION_ACCESSIONS = {
    "MS:1000576",  # no compression
}


def _pack_aux_sections(named_sections: Dict[str, bytes]) -> bytes:
    header = []
    payload = bytearray()
    offset = 0
    for name, blob in named_sections.items():
        size = len(blob)
        header.append({"name": name, "offset": offset, "size": size})
        payload.extend(blob)
        offset += size
    header_blob = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return AUX_MAGIC + struct.pack("<I", len(header_blob)) + header_blob + bytes(payload)


def _unpack_aux_sections(blob: bytes) -> Dict[str, bytes]:
    if not blob.startswith(AUX_MAGIC):
        raise ValueError("Invalid auxiliary payload magic")
    header_len = struct.unpack("<I", blob[len(AUX_MAGIC):len(AUX_MAGIC) + 4])[0]
    header_start = len(AUX_MAGIC) + 4
    header_end = header_start + header_len
    header = json.loads(blob[header_start:header_end].decode("utf-8"))
    payload = blob[header_end:]
    out = {}
    for item in header:
        out[item["name"]] = payload[item["offset"]:item["offset"] + item["size"]]
    return out


def _cv_accessions(array_elem) -> set[str]:
    return {
        cv.get("accession")
        for cv in array_elem.xpath("./mzml:cvParam", namespaces=NS)
        if cv.get("accession")
    }


def _array_kind_from_accessions(accessions: set[str]) -> str:
    if accessions & TIME_ARRAY_ACCESSIONS:
        return "time"
    if accessions & INTENSITY_ARRAY_ACCESSIONS:
        return "intensity"
    return "other"


def _array_kind(array_elem) -> str:
    return _array_kind_from_accessions(_cv_accessions(array_elem))


def _array_dtype_from_accessions(accessions: set[str]) -> str:
    if accessions & FLOAT32_ACCESSIONS:
        return "float32"
    if accessions & FLOAT64_ACCESSIONS:
        return "float64"
    return "unknown"


def _array_dtype(array_elem) -> str:
    return _array_dtype_from_accessions(_cv_accessions(array_elem))


def _compression_kind_from_accessions(accessions: set[str]) -> str:
    if accessions & ZLIB_COMPRESSION_ACCESSIONS:
        return "zlib"
    if accessions & NO_COMPRESSION_ACCESSIONS:
        return "none"
    return "unknown"


def _compression_kind(array_elem) -> str:
    return _compression_kind_from_accessions(_cv_accessions(array_elem))


def _record_has_aux_description(record: Dict) -> bool:
    return "cv_accessions" in record and "array_length" in record


def describe_auxiliary_records(metadata_xml: bytes | str, records: Sequence[Dict]) -> List[Dict]:
    if all(_record_has_aux_description(record) for record in records):
        descriptions: List[Dict] = []
        for idx, record in enumerate(records):
            binary_text = str(record.get("binary_text", "") or "")
            raw_blob = base64.b64decode(binary_text) if binary_text else b""
            accessions = {str(x) for x in record.get("cv_accessions", []) if x}
            descriptions.append(
                {
                    "record_index": idx,
                    "encoded_length": str(record.get("encoded_length", "0")),
                    "binary_text_len": len(binary_text),
                    "raw_blob": raw_blob,
                    "raw_blob_len": len(raw_blob),
                    "blob_sha1": sha1(raw_blob).hexdigest(),
                    "array_kind": _array_kind_from_accessions(accessions),
                    "data_type": _array_dtype_from_accessions(accessions),
                    "compression": _compression_kind_from_accessions(accessions),
                    "array_length": int(record.get("array_length", "0") or "0"),
                    "chromatogram_id": record.get("chromatogram_id"),
                }
            )
        return descriptions

    root = _parse_root(metadata_xml)
    aux_arrays = _iter_aux_binary_arrays(root)
    if len(aux_arrays) != len(records):
        raise ValueError(f"Aux record count mismatch: {len(aux_arrays)} vs {len(records)}")

    descriptions: List[Dict] = []
    for idx, (array_elem, record) in enumerate(zip(aux_arrays, records)):
        binary_text = str(record.get("binary_text", "") or "")
        raw_blob = base64.b64decode(binary_text) if binary_text else b""
        chromatogram = array_elem.xpath("ancestor::mzml:chromatogram[1]", namespaces=NS)
        descriptions.append(
            {
                "record_index": idx,
                "encoded_length": str(record.get("encoded_length", "0")),
                "binary_text_len": len(binary_text),
                "raw_blob": raw_blob,
                "raw_blob_len": len(raw_blob),
                "blob_sha1": sha1(raw_blob).hexdigest(),
                "array_kind": _array_kind(array_elem),
                "data_type": _array_dtype(array_elem),
                "compression": _compression_kind(array_elem),
                "array_length": int(array_elem.get("arrayLength", "0") or "0"),
                "chromatogram_id": chromatogram[0].get("id") if chromatogram else None,
            }
        )
    return descriptions


def _encode_json_base64(records: Sequence[Dict], backend: str) -> tuple[bytes, Dict]:
    raw_records = [
        {
            "encoded_length": str(record.get("encoded_length", "0")),
            "binary_text": str(record.get("binary_text", "") or ""),
        }
        for record in records
    ]
    raw = json.dumps(raw_records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = compress(raw, backend)
    return payload, {
        "format": "json_base64",
        "backend": backend,
        "raw_bytes": len(raw),
        "compressed_bytes": len(payload),
        "record_count": int(len(records)),
    }


def _encode_shared_blob_exact(metadata_xml: bytes | str, records: Sequence[Dict], backend: str) -> tuple[bytes, Dict]:
    descriptions = describe_auxiliary_records(metadata_xml, records)
    raw_records = [
        {
            "encoded_length": str(record.get("encoded_length", "0")),
            "binary_text": str(record.get("binary_text", "") or ""),
        }
        for record in records
    ]
    raw_json = json.dumps(raw_records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    blob_index_by_sha1: Dict[str, int] = {}
    unique_blobs: List[bytes] = []
    unique_blob_lengths: List[int] = []
    record_entries: List[Dict] = []

    for desc in descriptions:
        blob_sha1 = desc["blob_sha1"]
        blob_index = blob_index_by_sha1.get(blob_sha1)
        raw_blob = desc["raw_blob"]
        if blob_index is None:
            blob_index = len(unique_blobs)
            blob_index_by_sha1[blob_sha1] = blob_index
            unique_blobs.append(raw_blob)
            unique_blob_lengths.append(len(raw_blob))
        record_entries.append(
            {
                "encoded_length": desc["encoded_length"],
                "blob_index": blob_index,
            }
        )

    record_payload = compress(
        json.dumps(record_entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        backend,
    )
    blob_meta = {
        "blob_lengths": unique_blob_lengths,
    }
    blob_meta_payload = compress(
        json.dumps(blob_meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        backend,
    )
    blob_payload = compress(b"".join(unique_blobs), backend)

    packed = _pack_aux_sections(
        {
            "record_payload": record_payload,
            "blob_meta_payload": blob_meta_payload,
            "blob_payload": blob_payload,
        }
    )
    return packed, {
        "format": "shared_blob_exact",
        "backend": backend,
        "raw_bytes": len(raw_json),
        "compressed_bytes": len(packed),
        "record_count": int(len(records)),
        "source_binary_bytes": int(sum(desc["raw_blob_len"] for desc in descriptions)),
        "unique_blob_count": int(len(unique_blobs)),
        "shared_blob_reuse_count": int(len(records) - len(unique_blobs)),
        "time_array_count": int(sum(1 for desc in descriptions if desc["array_kind"] == "time")),
        "intensity_array_count": int(sum(1 for desc in descriptions if desc["array_kind"] == "intensity")),
        "chromatogram_array_count": int(sum(1 for desc in descriptions if desc["chromatogram_id"] is not None)),
    }


def encode_auxiliary_records(metadata_xml: bytes | str, records: Sequence[Dict], backend: str = "zstd-9") -> tuple[bytes, Dict]:
    baseline_payload, baseline_meta = _encode_json_base64(records, backend)
    shared_payload, shared_meta = _encode_shared_blob_exact(metadata_xml, records, backend)
    if len(shared_payload) < len(baseline_payload):
        return shared_payload, shared_meta
    return baseline_payload, baseline_meta


def _decode_json_base64(payload: bytes, meta: Dict) -> List[Dict]:
    raw = decompress(payload, meta["backend"])
    return json.loads(raw.decode("utf-8"))


def _decode_shared_blob_exact(payload: bytes, meta: Dict) -> List[Dict]:
    sections = _unpack_aux_sections(payload)
    backend = meta["backend"]
    record_entries = json.loads(decompress(sections["record_payload"], backend).decode("utf-8"))
    blob_meta = json.loads(decompress(sections["blob_meta_payload"], backend).decode("utf-8"))
    blob_lengths = [int(x) for x in blob_meta.get("blob_lengths", [])]
    blob_bytes = decompress(sections["blob_payload"], backend)

    unique_blobs: List[bytes] = []
    pos = 0
    for length in blob_lengths:
        unique_blobs.append(blob_bytes[pos:pos + length])
        pos += length
    if pos != len(blob_bytes):
        raise ValueError(f"Unused auxiliary blob bytes: {len(blob_bytes) - pos}")

    records: List[Dict] = []
    for entry in record_entries:
        raw_blob = unique_blobs[int(entry["blob_index"])]
        records.append(
            {
                "encoded_length": str(entry.get("encoded_length", "0")),
                "binary_text": base64.b64encode(raw_blob).decode("ascii"),
            }
        )
    return records


def decode_auxiliary_records(payload: bytes, meta: Dict) -> List[Dict]:
    fmt = str(meta["format"])
    if fmt == "json_base64":
        return _decode_json_base64(payload, meta)
    if fmt == "shared_blob_exact":
        return _decode_shared_blob_exact(payload, meta)
    raise ValueError(f"Unsupported auxiliary payload format: {fmt}")
