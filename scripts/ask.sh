#!/usr/bin/env bash
# memory-hub ask: 基于 llm-wiki 知识库的问答
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHONPATH="$HUB_DIR${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$HUB_DIR/scripts/automation_cli.py" ask "$@"

