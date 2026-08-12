#!/usr/bin/env bash
# memory-hub capture: 解析 Codex / claude code / claude-mem 会话, 输出到 staging/observations-*.jsonl
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
source "$HUB_DIR/scripts/lib.sh"
timing_begin
trap 'timing_end capture "$?"' EXIT
SOURCE="codex"
SINCE=""
ALL=0
WATCH=0

ACTIVE_SINCE_FILE="$STAGING/.since"
ACTIVE_SEEN_FILE="$STAGING/.seen"

usage() {
  echo "用法: capture.sh [--source codex|claude-mem|claude-code] [--all] [--since <epoch_ms>] [--watch]"
  echo "  --source workbuddy: 解析 ~/.workbuddy/projects JSONL, id 前缀 'w', 独立游标"
  echo "  默认 codex: 解析 ~/.codex/sessions JSONL, 增量游标 staging/.since, 内容去重 staging/.seen"
  echo "  --source claude-code: 解析 ~/.claude/projects JSONL, id 前缀 'd'"
  echo "  --source claude-mem: 兼容旧 SQLite"
  echo "  --watch: 每 60 秒增量采集一次, Ctrl-C 退出"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source) SOURCE="$2"; shift ;;
    --all) ALL=1 ;;
    --since) SINCE="$2"; shift ;;
    --watch) WATCH=1 ;;
    --help|-h) usage ;;
    *) echo "未知参数: $1" >&2; usage ;;
  esac
  shift
done

mkdir -p "$STAGING"

resolve_since() {
  _sf="${1:-$STAGING/.since}"
  _seen="${2:-$STAGING/.seen}"
  ACTIVE_SINCE_FILE="$_sf"
  ACTIVE_SEEN_FILE="$_seen"
  if [[ -n "$SINCE" ]]; then
    SINCE_MS="$SINCE"
  elif [[ "$ALL" == 1 ]]; then
    SINCE_MS=0
  elif [[ -f "$ACTIVE_SINCE_FILE" ]]; then
    SINCE_MS="$(cat "$ACTIVE_SINCE_FILE")"
  else
    SINCE_MS=$(( ($(date +%s) - 86400) * 1000 ))
  fi
}

CLEAN_JQ='gsub("<[a-zA-Z0-9 ._-]+>[^<>]*</[a-zA-Z0-9 ._-]+>"; "") | gsub("<[a-zA-Z0-9 ._-]+>[^<>]*</[a-zA-Z0-9 ._-]+>"; "") | gsub("<[^>]*>"; "") | gsub("<[a-zA-Z0-9 ._-]+>[^<>]*</[a-zA-Z0-9 ._-]+>"; "") | gsub("\\s+"; " ") | sub("^ +"; "") | sub(" +$"; "")'

# ============ workbuddy (~/.workbuddy/projects) ============
if [[ "$SOURCE" == "workbuddy" ]]; then
  WB_DIR="${WORKBUDDY_PROJECTS_DIR:-$HOME/.workbuddy/projects}"
  [[ -d "$WB_DIR" ]] || { echo "错误: workbuddy 项目目录不存在: $WB_DIR" >&2; exit 1; }
  find -L "$WB_DIR" -type f -name '*.jsonl' -print -quit 2>/dev/null | grep -q . || {
    echo "错误: workbuddy 项目目录下无任何 .jsonl 文件: $WB_DIR" >&2
    exit 1
  }
  resolve_since "$STAGING/.since.workbuddy" "$STAGING/.seen.workbuddy"
  touch "$ACTIVE_SEEN_FILE"

  # workbuddy JSONL: 顶层 type=message, role, timestamp(epoch ms 数字), content 数组含 input_text
  WB_EXTRACT_JQ='
    (select(.type == "message" and (.role == "user" or .role == "assistant"))
     | select(.timestamp != null and (.content // []) != null)
     | {ts: .timestamp, role: .role, type: "message",
        text: ([.content[]? | select(.type == "input_text") | .text] | join("\n"))})
    | select((.text | length) > 0)
    | select(.ts > $since)
    | .text |= (__CLEAN_JQ_PLACEHOLDER__)
    | select((.text | length) > 0)
    | select(.text | test("^# ?AGENTS\\.md instructions") | not)
    | {project: $proj, role, type, text: (.text | .[0:800]),
       created_at: (.ts | tostring | .[0:10] | tonumber | strftime("%Y-%m-%dT%H:%M:%SZ")),
       created_at_epoch: .ts}'

  scan_files_wb() {
    if [[ "$ALL" == 1 ]]; then
      find -L "$WB_DIR" -type f -name '*.jsonl' -print0
    else
      find -L "$WB_DIR" -type f -name '*.jsonl' -mtime -3 -print0
    fi
  }

  run_capture_wb() {
    resolve_since "$STAGING/.since.workbuddy" "$STAGING/.seen.workbuddy"
    TS="$(date +%Y%m%d-%H%M%S)"
    OUT="$STAGING/observations-$TS.jsonl"
    RAW="$(mktemp)"
    WB_JQ_FILTER="$(mktemp)"
    _jq_template="$WB_EXTRACT_JQ"
    _jq_template="${_jq_template//__CLEAN_JQ_PLACEHOLDER__/$CLEAN_JQ}"
    printf '%s' "$_jq_template" > "$WB_JQ_FILTER"
    : > "$RAW"

    while IFS= read -r -d '' f; do
      [[ -f "$f" ]] || continue
      proj="$(basename "$(dirname "$f")")"
      proj="$(printf '%s' "$proj" | sed 's/^Users-earan-WorkBuddy-//; s/^[0-9-]*//; s/^-*//; s/-*$//')"
      [[ -n "$proj" ]] || proj="workbuddy"
      jq -R -c 'fromjson? // empty | select(.)' "$f" 2>/dev/null | jq -c -f "$WB_JQ_FILTER" --arg proj "$proj" --argjson since "$SINCE_MS" >> "$RAW" 2>/dev/null || true
    done < <(scan_files_wb)

    jq -s -c '
      sort_by(.created_at_epoch)
      | map(select((.text | gsub("\\s"; "")) != ""))
      | to_entries
      | map(.value.id = ("w" + (100000 + .key | tostring)))
      | map(.value)
      | .[]
    ' "$RAW" > "$OUT"
    rm -f "$RAW"
    rm -f "$WB_JQ_FILTER"

    FILTERED="$(mktemp)"
    COUNT=0
    SEEN_TMP="$(mktemp)"
    cp "$ACTIVE_SEEN_FILE" "$SEEN_TMP"
    while IFS= read -r line; do
      h="$(printf '%s' "$line" | jq -c "del(.id)" 2>/dev/null | shasum | cut -c1-16)"
      if ! grep -qxF "$h" "$SEEN_TMP"; then
        echo "$h" >> "$SEEN_TMP"
        echo "$line" >> "$FILTERED"
        COUNT=$((COUNT + 1))
      fi
    done < "$OUT"
    mv "$FILTERED" "$OUT"
    mv "$SEEN_TMP" "$ACTIVE_SEEN_FILE"

    if [[ "$COUNT" -gt 0 ]]; then
      printf '%s\n' "$(jq -r '.created_at_epoch' "$OUT" | tail -1)" > "$ACTIVE_SINCE_FILE"
      echo "capture(workbuddy): $COUNT 条新观察 -> $OUT"
    else
      rm -f "$OUT"
      echo "capture(workbuddy): 无新观察 (since=$SINCE_MS)"
    fi
  }

  if [[ "$WATCH" == 1 ]]; then
    ROUND=0
    while true; do
      ROUND=$((ROUND + 1))
      echo "== capture workbuddy --watch 第 $ROUND 轮 ($(date '+%H:%M:%S')) =="
      run_capture_wb
      sleep 60
    done
  else
    run_capture_wb
  fi
  exit 0
fi

# ============ claude-mem (兼容旧 SQLite) ============
if [[ "$SOURCE" == "claude-mem" ]]; then
  DB="${CLAUDE_MEM_DB:-$HOME/.claude-mem/data/claude-mem.db}"
  [[ -f "$DB" ]] || { echo "错误: claude-mem DB 不存在: $DB" >&2; exit 1; }
  resolve_since
  TS="$(date +%Y%m%d-%H%M%S)"
  OUT="$STAGING/observations-$TS.jsonl"
  sqlite3 -json "$DB" "
  SELECT id, project, type, title, text, facts, narrative, concepts, files_read, files_modified, created_at, created_at_epoch
  FROM observations
  WHERE created_at_epoch > $SINCE_MS
  ORDER BY created_at_epoch ASC;" \
    | jq -c '.[]' > "$OUT"
  COUNT="$(wc -l < "$OUT" | tr -d ' ')"
  if [[ "$COUNT" -gt 0 ]]; then
    printf '%s\n' "$(jq -r '.created_at_epoch' "$OUT" | tail -1)" > "$ACTIVE_SINCE_FILE"
    echo "capture(claude-mem): $COUNT 条新观察 -> $OUT"
  else
    rm -f "$OUT"
    echo "capture(claude-mem): 无新观察 (since=$SINCE_MS)"
  fi
  exit 0
fi

# ============ claude code 会话 JSONL ============
if [[ "$SOURCE" == "claude-code" ]]; then
  CLAUDE_PROJECTS_DIR="${CLAUDE_PROJECTS_DIR:-$HOME/.claude/projects}"
  [[ -d "$CLAUDE_PROJECTS_DIR" ]] || { echo "错误: claude code 项目目录不存在: $CLAUDE_PROJECTS_DIR" >&2; exit 1; }
  find -L "$CLAUDE_PROJECTS_DIR" -type f -name '*.jsonl' -print -quit 2>/dev/null | grep -q . || {
    echo "错误: claude code 项目目录下无任何 .jsonl 文件: $CLAUDE_PROJECTS_DIR" >&2
    exit 1
  }
  resolve_since "$STAGING/.since.claude-code" "$STAGING/.seen.claude-code"
  touch "$ACTIVE_SEEN_FILE"
  if [[ "$ALL" == 1 ]]; then
    rm -f "$ACTIVE_SINCE_FILE"
  fi

  CLAUDE_EXTRACT_JQ='
    (select(.type == "assistant")
     | select(.timestamp != null and (.message // {}) != null)
     | [.timestamp, "assistant"] as $meta
     | ((.message.content // [])
        | (if type == "array"
           then map(
                  if .type == "text" then .text
                  elif .type == "tool_use" then [(.name // ""), ((.input // .arguments // "") | tostring)] | join(" ")
                  elif .type == "thinking" then ""
                  else "" end)
           else [""] end)
        | join("\n"))
     | {ts: $meta[0], role: $meta[1], type: "message", text: .}),
    (select(.type == "user")
     | select(.timestamp != null and (.message // {}) != null)
     | [.timestamp, "user"] as $meta
     | ((.message.content // "")
        | (if type == "array"
           then map(
                  if .type == "text" then .text
                  elif .type == "tool_result" then ((.content // "") | tostring)
                  else "" end)
                  | join("\n")
           else . end))
     | {ts: $meta[0], role: $meta[1], type: "message", text: .})
    | select((.text | length) > 0)
    | select(.ts | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 | . * 1000 > $since)
    | .text |= (__CLEAN_JQ_PLACEHOLDER__)
    | select((.text | length) > 0)
    | select(.text | test("^# ?AGENTS\\.md instructions") | not)
    | {project: $proj, role, type, text: (.text | .[0:800]),
       created_at: .ts,
       created_at_epoch: (.ts | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 | . * 1000)}'

  scan_files_claude() {
    if [[ "$ALL" == 1 ]]; then
      find -L "$CLAUDE_PROJECTS_DIR" -type f -name '*.jsonl' -print0
    else
      find -L "$CLAUDE_PROJECTS_DIR" -type f -name '*.jsonl' -mtime -3 -print0
    fi
  }

  run_capture_claude() {
    resolve_since "$STAGING/.since.claude-code" "$STAGING/.seen.claude-code"
    TS="$(date +%Y%m%d-%H%M%S)"
    OUT="$STAGING/observations-$TS.jsonl"
    RAW="$(mktemp)"
    CLAUDE_JQ_FILTER="$(mktemp)"
    _jq_template="$CLAUDE_EXTRACT_JQ"
    _jq_template="${_jq_template//__CLEAN_JQ_PLACEHOLDER__/$CLEAN_JQ}"
    printf '%s' "$_jq_template" > "$CLAUDE_JQ_FILTER"
    : > "$RAW"

    while IFS= read -r -d '' f; do
      [[ -f "$f" ]] || continue
      meta="$(jq -c 'select(.timestamp != null) | {cwd: (.cwd // "")}' "$f" 2>/dev/null | head -1 || true)"
      proj="$(printf '%s' "$meta" | jq -r '.cwd // ""' 2>/dev/null | sed 's#/$##' | sed 's#.*/##' || true)"
      if [[ -z "${proj:-}" ]]; then
        proj="$(basename "$(dirname "$f")")"
      fi
      [[ -n "$proj" ]] || proj="unknown"
      jq -R -c 'fromjson? // empty | select(.)' "$f" 2>/dev/null | jq -c -f "$CLAUDE_JQ_FILTER" --arg proj "$proj" --argjson since "$SINCE_MS" >> "$RAW" 2>/dev/null || true
    done < <(scan_files_claude)

    jq -s -c '
      sort_by(.created_at_epoch)
      | map(select((.text | gsub("\\s"; "")) != ""))
      | to_entries
      | map(.value.id = ("d" + (100000 + .key | tostring)))
      | map(.value)
      | .[]
    ' "$RAW" > "$OUT"
    rm -f "$RAW"
    rm -f "$CLAUDE_JQ_FILTER"

    FILTERED="$(mktemp)"
    COUNT=0
    SEEN_TMP="$(mktemp)"
    cp "$ACTIVE_SEEN_FILE" "$SEEN_TMP"
    while IFS= read -r line; do
      h="$(printf '%s' "$line" | jq -c "del(.id)" 2>/dev/null | shasum | cut -c1-16)"
      if ! grep -qxF "$h" "$SEEN_TMP"; then
        echo "$h" >> "$SEEN_TMP"
        echo "$line" >> "$FILTERED"
        COUNT=$((COUNT + 1))
      fi
    done < "$OUT"
    mv "$FILTERED" "$OUT"
    mv "$SEEN_TMP" "$ACTIVE_SEEN_FILE"

    if [[ "$COUNT" -gt 0 ]]; then
      printf '%s\n' "$(jq -r '.created_at_epoch' "$OUT" | tail -1)" > "$ACTIVE_SINCE_FILE"
      echo "capture(claude-code): $COUNT 条新观察 -> $OUT"
    else
      rm -f "$OUT"
      echo "capture(claude-code): 无新观察 (since=$SINCE_MS)"
    fi
  }

  if [[ "$WATCH" == 1 ]]; then
    ROUND=0
    while true; do
      ROUND=$((ROUND + 1))
      echo "== capture claude-code --watch 第 $ROUND 轮 ($(date '+%H:%M:%S')) =="
      run_capture_claude
      sleep 60
    done
  else
    run_capture_claude
  fi
  exit 0
fi

# ============ Codex 会话 JSONL (默认) ============
SESSIONS_DIR="${CODEX_SESSIONS_DIR:-$HOME/.codex/sessions}"
[[ -d "$SESSIONS_DIR" ]] || { echo "错误: 会话目录不存在: $SESSIONS_DIR" >&2; exit 1; }
resolve_since
touch "$ACTIVE_SEEN_FILE"
if [[ "$ALL" == 1 ]]; then
  rm -f "$ACTIVE_SINCE_FILE"
fi

EXTRACT_JQ='
  (select(.type=="response_item" and (.payload.role=="user" or .payload.role=="assistant"))
   | select(.timestamp != null)
   | {ts:.timestamp, role:.payload.role, type:"message",
      text:([.payload.content[]? | select(.type=="input_text") | .text] | join("\n"))}),
  (select(.type=="response_item" and (.payload.type=="function_call" or .payload.type=="custom_tool_call" or .payload.type=="web_search_call" or .payload.type=="exec_command"))
   | select(.timestamp != null)
   | {ts:.timestamp, role:"tool", type:"tool",
      text:([.payload.name // "", .payload.command // "", (.payload.input // .payload.arguments // "" | tostring)] | join(" ") | .[0:300])})
  | select((.text | length) > 0)
  | select(.ts | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 | . * 1000 > $since)
  | .text |= (__CLEAN_JQ_PLACEHOLDER__)
  | select((.text | length) > 0)
  | select(.text | test("^# ?AGENTS\\.md instructions") | not)
  | {project:$proj, role, type, text:(.text | .[0:800]), created_at:.ts,
     created_at_epoch:(.ts | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 | . * 1000)}'

scan_files() {
  if [[ "$ALL" == 1 ]]; then
    find -L "$SESSIONS_DIR" -type f -name '*.jsonl' -print0
  else
    find -L "$SESSIONS_DIR" -type f -name '*.jsonl' -mtime -3 -print0
  fi
}

run_capture() {
  resolve_since
  TS="$(date +%Y%m%d-%H%M%S)"
  OUT="$STAGING/observations-$TS.jsonl"
  RAW="$(mktemp)"
  CODEX_JQ_FILTER="$(mktemp)"
  _jq_template="$EXTRACT_JQ"
  _jq_template="${_jq_template//__CLEAN_JQ_PLACEHOLDER__/$CLEAN_JQ}"
  printf '%s' "$_jq_template" > "$CODEX_JQ_FILTER"
  : > "$RAW"

  while IFS= read -r -d '' f; do
    [[ -f "$f" ]] || continue
    meta="$(jq -c 'select(.type=="session_meta") | {cwd:.payload.cwd}' "$f" 2>/dev/null | head -1 || true)"
    proj="$(printf '%s' "$meta" | jq -r '.cwd // ""' 2>/dev/null | sed 's#/$##' | sed 's#.*/##' || true)"
    [[ -n "${proj:-}" ]] || proj="unknown"
    jq -R -c 'fromjson? // empty | select(.)' "$f" 2>/dev/null | jq -c -f "$CODEX_JQ_FILTER" --arg proj "$proj" --argjson since "$SINCE_MS" >> "$RAW" 2>/dev/null || true
  done < <(scan_files)

  jq -s -c '
    sort_by(.created_at_epoch)
    | map(select((.text | gsub("\\s"; "")) != ""))
    | to_entries
    | map(.value.id = ("c" + (100000 + .key | tostring)))
    | map(.value)
    | .[]
  ' "$RAW" > "$OUT"
  rm -f "$RAW"
  rm -f "$CODEX_JQ_FILTER"

  FILTERED="$(mktemp)"
  COUNT=0
  SEEN_TMP="$(mktemp)"
  cp "$ACTIVE_SEEN_FILE" "$SEEN_TMP"
  while IFS= read -r line; do
    h="$(printf '%s' "$line" | jq -c "del(.id)" 2>/dev/null | shasum | cut -c1-16)"
    if ! grep -qxF "$h" "$SEEN_TMP"; then
      echo "$h" >> "$SEEN_TMP"
      echo "$line" >> "$FILTERED"
      COUNT=$((COUNT + 1))
    fi
  done < "$OUT"
  mv "$FILTERED" "$OUT"
  mv "$SEEN_TMP" "$ACTIVE_SEEN_FILE"

  if [[ "$COUNT" -gt 0 ]]; then
    printf '%s\n' "$(jq -r '.created_at_epoch' "$OUT" | tail -1)" > "$ACTIVE_SINCE_FILE"
    echo "capture(codex): $COUNT 条新观察 -> $OUT"
  else
    rm -f "$OUT"
    echo "capture(codex): 无新观察 (since=$SINCE_MS)"
  fi
}

if [[ "$WATCH" == 1 ]]; then
  ROUND=0
  while true; do
    ROUND=$((ROUND + 1))
    echo "== capture --watch 第 $ROUND 轮 ($(date '+%H:%M:%S')) =="
    run_capture
    sleep 60
  done
else
  run_capture
fi
