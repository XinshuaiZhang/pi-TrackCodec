#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PYTHON_BIN="${TRACKCODEC_PYTHON:-${PYTHON:-python3}}"
"$PYTHON_BIN" setup.py build_ext --inplace
