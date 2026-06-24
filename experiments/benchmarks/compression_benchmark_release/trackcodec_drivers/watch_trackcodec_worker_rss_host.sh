#!/usr/bin/env bash
set -u

RESULT_ROOT="${1:?usage: watch_trackcodec_worker_rss_host.sh RESULT_ROOT [POLL_SECONDS] [IDLE_EXIT_SECONDS]}"
POLL_SECONDS="${2:-5}"
IDLE_EXIT_SECONDS="${3:-600}"
PYTHON_BIN="${TRACKCODEC_PYTHON:-${PYTHON:-python3}}"

OUT_DIR="${RESULT_ROOT}/live_rss_monitor_host"
PROGRESS_LOG="${OUT_DIR}/rss_monitor.progress.jsonl"
SAMPLES_TSV="${OUT_DIR}/rss_samples.tsv"
SUMMARY_TSV="${OUT_DIR}/rss_peak_by_stage.tsv"
SUMMARY_FILE_TSV="${OUT_DIR}/rss_peak_by_file.tsv"

mkdir -p "${OUT_DIR}"

now_iso() {
  date +"%Y-%m-%dT%H:%M:%S%z"
}

json_escape() {
  "${PYTHON_BIN}" - "$1" <<'PY'
import json, sys
print(json.dumps(sys.argv[1], ensure_ascii=False))
PY
}

printf '{"event":"rss_monitor_start","timestamp":%s,"result_root":%s,"poll_seconds":%s,"idle_exit_seconds":%s}\n' \
  "$(json_escape "$(now_iso)")" \
  "$(json_escape "${RESULT_ROOT}")" \
  "$(json_escape "${POLL_SECONDS}")" \
  "$(json_escape "${IDLE_EXIT_SECONDS}")" >> "${PROGRESS_LOG}"

if [[ ! -f "${SAMPLES_TSV}" ]]; then
  printf 'timestamp\tdataset_label\tfile\tfile_path\tphase\tstage\tpid\trss_bytes\traw_file_bytes\n' > "${SAMPLES_TSV}"
fi

idle_started=0

while true; do
  timestamp="$(now_iso)"
  worker_lines="$(ps -eo pid=,ppid=,rss=,args= | grep 'single_file_benchmark.py' | grep "${RESULT_ROOT}" | grep -v grep || true)"
  supervisor_count="$(ps -ef | grep 'multi_dataset_supervisor.py' | grep "${RESULT_ROOT}" | grep -v grep | wc -l | tr -d ' ')"
  worker_count=0

  if [[ -n "${worker_lines}" ]]; then
    while IFS= read -r line; do
      [[ -z "${line}" ]] && continue
      worker_count=$((worker_count + 1))
      pid="$(awk '{print $1}' <<<"${line}")"
      rss_kb="$(awk '{print $3}' <<<"${line}")"
      file_path="$(sed -n 's/.*--file \([^ ]*\).*/\1/p' <<<"${line}" | head -n 1)"
      worker_root="$(sed -n 's/.*--result-root \([^ ]*\).*/\1/p' <<<"${line}" | head -n 1)"
      phase="$(sed -n 's/.*--phase \([^ ]*\).*/\1/p' <<<"${line}" | head -n 1)"
      dataset_label="$(sed -n 's/.*--dataset-label \([^ ]*\).*/\1/p' <<<"${line}" | head -n 1)"
      file_name="$(basename "${file_path}")"
      raw_file_bytes="$(stat -c %s "${file_path}" 2>/dev/null || echo 0)"
      stage="${phase}"
      progress_path="${worker_root}/logs/worker.progress.jsonl"
      if [[ -f "${progress_path}" ]]; then
        stage_line="$(grep -E '"event": "rss_stage_(start|done)"|"event": "section_start"|"event": "whole_variant_start"' "${progress_path}" | tail -n 1 || true)"
        if [[ "${stage_line}" == *'"stage":'* ]]; then
          parsed_stage="$(sed -n 's/.*"stage": "\([^"]*\)".*/\1/p' <<<"${stage_line}" | head -n 1)"
          [[ -n "${parsed_stage}" ]] && stage="${parsed_stage}"
        elif [[ "${stage_line}" == *'"section":'* ]]; then
          parsed_section="$(sed -n 's/.*"section": "\([^"]*\)".*/\1/p' <<<"${stage_line}" | head -n 1)"
          [[ -n "${parsed_section}" ]] && stage="section_${parsed_section}"
        elif [[ "${stage_line}" == *'"archive_variant":'* ]]; then
          parsed_variant="$(sed -n 's/.*"archive_variant": "\([^"]*\)".*/\1/p' <<<"${stage_line}" | head -n 1)"
          [[ -n "${parsed_variant}" ]] && stage="whole_variant_${parsed_variant}"
        fi
      fi
      printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${timestamp}" \
        "${dataset_label}" \
        "${file_name}" \
        "${file_path}" \
        "${phase}" \
        "${stage}" \
        "${pid}" \
        "$((rss_kb * 1024))" \
        "${raw_file_bytes}" >> "${SAMPLES_TSV}"
    done <<< "${worker_lines}"
  fi

  "${PYTHON_BIN}" - "${SAMPLES_TSV}" "${SUMMARY_TSV}" "${SUMMARY_FILE_TSV}" <<'PY'
import csv, sys
from pathlib import Path

samples_path = Path(sys.argv[1])
stage_path = Path(sys.argv[2])
file_path = Path(sys.argv[3])
rows = []
with samples_path.open("r", encoding="utf-8") as handle:
    reader = csv.DictReader(handle, delimiter="\t")
    rows = list(reader)

stage_best = {}
file_best = {}
for row in rows:
    key = (row["file_path"], row["phase"], row["stage"])
    rss = int(row["rss_bytes"])
    raw = int(row["raw_file_bytes"])
    existing = stage_best.get(key)
    if existing is None or rss >= int(existing["peak_rss_bytes"]):
        stage_best[key] = {
            "dataset_label": row["dataset_label"],
            "file": row["file"],
            "file_path": row["file_path"],
            "phase": row["phase"],
            "stage": row["stage"],
            "raw_file_bytes": raw,
            "peak_rss_bytes": rss,
            "last_pid": int(row["pid"]),
            "timestamp": row["timestamp"],
        }
for item in stage_best.values():
    bucket = file_best.get(item["file_path"])
    if bucket is None or int(item["peak_rss_bytes"]) >= int(bucket["max_peak_rss_bytes"]):
        file_best[item["file_path"]] = {
            "dataset_label": item["dataset_label"],
            "file": item["file"],
            "file_path": item["file_path"],
            "raw_file_bytes": int(item["raw_file_bytes"]),
            "max_peak_rss_bytes": int(item["peak_rss_bytes"]),
            "max_peak_rss_stage": item["stage"],
            "timestamp": item["timestamp"],
        }

stage_rows = sorted(stage_best.values(), key=lambda x: (x["dataset_label"], x["file"], x["phase"], x["stage"]))
file_rows = sorted(file_best.values(), key=lambda x: (x["dataset_label"], x["file"]))

with stage_path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=[
        "dataset_label", "file", "file_path", "phase", "stage",
        "raw_file_bytes", "peak_rss_bytes", "last_pid", "timestamp",
    ], delimiter="\t")
    writer.writeheader()
    writer.writerows(stage_rows)

with file_path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=[
        "dataset_label", "file", "file_path", "raw_file_bytes",
        "max_peak_rss_bytes", "max_peak_rss_stage", "timestamp",
    ], delimiter="\t")
    writer.writeheader()
    writer.writerows(file_rows)
PY

  if [[ "${supervisor_count}" != "0" || "${worker_count}" != "0" ]]; then
    idle_started=0
  else
    if [[ "${idle_started}" -eq 0 ]]; then
      idle_started="$(date +%s)"
    else
      now_epoch="$(date +%s)"
      if (( now_epoch - idle_started >= IDLE_EXIT_SECONDS )); then
        break
      fi
    fi
  fi

  sleep "${POLL_SECONDS}"
done

printf '{"event":"rss_monitor_done","timestamp":%s,"result_root":%s}\n' \
  "$(json_escape "$(now_iso)")" \
  "$(json_escape "${RESULT_ROOT}")" >> "${PROGRESS_LOG}"
