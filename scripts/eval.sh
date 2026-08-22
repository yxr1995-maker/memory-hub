#!/usr/bin/env bash
# memory-hub eval (F3): run self-eval baseline. Usage: ./eval.sh [--top N]
set -euo pipefail
HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOP=5
while [[ $# -gt 0 ]]; do case "$1" in --top) TOP="$2"; shift;; *) echo "unknown: $1">&2; exit 2;; esac; shift; done
exec python3 "$HUB_DIR/scripts/eval.py" --top "$TOP"
