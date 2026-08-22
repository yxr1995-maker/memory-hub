#!/usr/bin/env bash
# memory-hub lib: 通用工具
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


scan_wiki_tokens() {
  local wiki_dir="${1:-$HOME/llm-wiki}"
  local extra_args=("${@:2}")
  python3 "$HUB_DIR/scripts/verify_tokens.py" "${extra_args[@]}" "$wiki_dir"
}

# sanitize_text: 脱敏文本
sanitize_text() {
  local text="${1:-}"
  if [[ $# -eq 0 ]]; then
    text="$(cat)"
  fi
  # Bearer token
  text="$(printf '%s' "$text" | sed -E 's/(Bearer[[:space:]]+)[A-Za-z0-9_\-\.]+/[REDACTED_BEARER]/g')"
  # JWT
  text="$(printf '%s' "$text" | sed -E 's/eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*/[REDACTED_JWT]/g')"
  # sk-ant- / sk-
  text="$(printf '%s' "$text" | sed -E 's/(sk-ant-[A-Za-z0-9_\-]{16,})/[REDACTED_SK]/g')"
  text="$(printf '%s' "$text" | sed -E 's/(sk-[A-Za-z0-9]{48})/[REDACTED_SK]/g')"
  # api_key= / password= / token=
  text="$(printf '%s' "$text" | sed -E 's/(api_key[[:space:]]*=[[:space:]]*)[^[:space:]\"'\''`&|;]+/\1[REDACTED]/gI')"
  text="$(printf '%s' "$text" | sed -E 's/(password[[:space:]]*=[[:space:]]*)[^[:space:]\"'\''`&|;]+/\1[REDACTED]/gI')"
  text="$(printf '%s' "$text" | sed -E 's/(token[[:space:]]*=[[:space:]]*)[^[:space:]\"'\''`&|;]+/\1[REDACTED]/gI')"
  printf '%s' "$text"
}
