#!/usr/bin/env bash
# memory-hub search: 内置检索 ~/llm-wiki
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$HUB_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HUB_DIR/scripts/automation_cli.py" search "$@"

