from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

for _thread_var in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "BLIS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_thread_var, "1")

from ..mzml.archive_codec import MzMLSectionArchiveCodec


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, bytes):
        return {"__bytes__": len(obj)}
    return obj


def main():
    parser = argparse.ArgumentParser(description="Decode a TrackCodec archive and optionally reconstruct a full mzML file.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=False)
    parser.add_argument("--output-mzml", required=False)
    parser.add_argument("--binary-compression", choices=("preserve_template", "none", "zlib"), default="preserve_template")
    parser.add_argument("--section-workers", type=int, default=16)
    parser.add_argument("--ms2-segment-workers", type=int, default=0)
    parser.add_argument("--decode-segment-workers", type=int, default=0, help="0 reuses --section-workers for independent MS1/MS2 segment decode.")
    args = parser.parse_args()

    codec = MzMLSectionArchiveCodec(
        ms2_section_workers=args.section_workers,
        ms2_segment_workers=args.ms2_segment_workers if int(args.ms2_segment_workers) > 0 else None,
        decode_segment_workers=args.decode_segment_workers if int(args.decode_segment_workers) > 0 else args.section_workers,
    )
    out = {}

    if args.output_mzml:
        output_mzml = codec.decode_to_mzml_file(
            args.input,
            args.output_mzml,
            binary_compression=args.binary_compression,
        )
        out["output_mzml"] = str(output_mzml)

    if args.output_dir:
        sections = codec.decode_archive_file(
            args.input,
            decode_ms1_sidecars=False,
            decode_ms2_payload_json=True,
            decode_auxiliary_records_flag=True,
        )
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "archive_meta.json").write_text(json.dumps(sections["archive_meta"], indent=2))
        (output_dir / "ms1_meta.json").write_text(json.dumps(sections["ms1_meta"], indent=2))
        (output_dir / "ms2_meta.json").write_text(json.dumps(sections["ms2_meta"], indent=2))
        (output_dir / "metadata_meta.json").write_text(json.dumps(sections["metadata_meta"], indent=2))
        metadata_xml = sections["metadata_xml"]
        if isinstance(metadata_xml, bytes):
            (output_dir / "metadata_stripped.xml").write_bytes(metadata_xml)
        else:
            (output_dir / "metadata_stripped.xml").write_text(str(metadata_xml))
        (output_dir / "ms2_payload.json").write_text(json.dumps(_json_safe(sections["ms2_payload_json"]), indent=2))
        (output_dir / "ms1_payload.bin").write_bytes(sections["ms1_payload"])
        if sections.get("auxiliary_binary_records") is not None:
            (output_dir / "auxiliary_binary_records.json").write_text(
                json.dumps(_json_safe(sections["auxiliary_binary_records"]), indent=2)
            )
        out["output_dir"] = str(output_dir)

    if not out:
        raise SystemExit("At least one of --output-dir or --output-mzml is required")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
