#!/usr/bin/env bash
# memory-hub watch-loop: 常驻循环（每 30 分钟 capture + distill + index），供 launchd 调用
set -uo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] capture"
  bash "$HUB_DIR/scripts/capture.sh" 2>&1 | tail -2 || true
  LATEST="$(ls -t "$HUB_DIR"/staging/observations-*.jsonl 2>/dev/null | head -1 || true)"
  if [[ -n "${LATEST:-}" ]]; then
    echo "[$(date '+%H:%M:%S')] distill"
    bash "$HUB_DIR/scripts/distill.sh" "$LATEST" 2>&1 | tail -2 || true
  fi
  if [[ -x "$HUB_DIR/scripts/index.sh" ]]; then
    echo "[$(date '+%H:%M:%S')] index"
    bash "$HUB_DIR/scripts/index.sh" 2>&1 | tail -2 || true
  fi
  sleep 1800
done
