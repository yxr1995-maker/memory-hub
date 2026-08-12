#!/usr/bin/env bash
# memory-hub lib: 通用工具（阶段耗时记录 → $HOME/.memory-hub/timings.tsv）
# 被 capture/distill/publish/index 等脚本 source；embed.py 用同格式自记录。
set -euo pipefail

TIMING_FILE="${MEMORY_HUB_DATA:-$HOME/.memory-hub}/timings.tsv"
_timing_start_ts=0
_timing_active=0

timing_begin() {
  _timing_start_ts=$SECONDS
  _timing_active=1
}

timing_end() {
  local cmd="${1:-x}" rc="${2:-0}"
  if [[ "$_timing_active" != 1 ]]; then
    return 0
  fi
  local ms=$(( (SECONDS - _timing_start_ts) * 1000 ))
  mkdir -p "$(dirname "$TIMING_FILE")"
  printf '%s\t%s\t%s\t%s\n' "$(date '+%s')" "$cmd" "$ms" "$rc" >> "$TIMING_FILE"
  _timing_active=0
}
