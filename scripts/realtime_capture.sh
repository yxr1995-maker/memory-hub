#!/usr/bin/env bash
# memory-hub realtime_capture: PostToolUse hook 实时增量采集
# 从 stdin 读 hook JSON 的 transcript_path，用每文件字节偏移游标只解析新增行（不全量解析，毫秒级）
# 与轮次级 capture.sh 互补：这里每次工具调用后立即入记忆（实时级）
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
OFFSETS="$STAGING/.rt_offsets"
OBS="$STAGING/observations-realtime.jsonl"
SEEN_FILE="$STAGING/.seen"
LOCKDIR="$STAGING/.rt_lock"

# 1. 从 stdin 读 hook 输入 JSON，提取 transcript_path
INPUT="$(cat)"
TP="$(printf '%s' "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null || true)"
[[ -n "$TP" && -f "$TP" ]] || exit 0

mkdir -p "$STAGING"
touch "$OFFSETS" "$SEEN_FILE"

# 2. 每文件字节偏移游标
OFFSET="$(awk -v f="$TP" '$1==f{o=$2} END{print o+0}' "$OFFSETS" 2>/dev/null)"
OFFSET="${OFFSET:-0}"
SIZE="$(stat -f %z "$TP" 2>/dev/null || echo 0)"
[[ "$SIZE" -gt "$OFFSET" ]] || exit 0

# 3. project（读首行 session_meta.cwd 末段）
PROJ="$(head -1 "$TP" | jq -r '.payload.cwd // empty' 2>/dev/null | sed 's#/$##; s#.*/##' || true)"
[[ -n "$PROJ" ]] || PROJ="unknown"

# 4. 增量解析新增行（消息 + tool 事件，与 capture.sh 同一提取逻辑）
NEW="$(tail -c +$((OFFSET + 1)) "$TP" | jq -c --arg proj "$PROJ" '
  (select(.type=="response_item" and (.payload.role=="user" or .payload.role=="assistant"))
   | select(.timestamp != null)
   | {ts:.timestamp, role:.payload.role, type:"message",
      text:([.payload.content[]? | select(.type=="input_text") | .text] | join("\n"))}),
  (select(.type=="response_item" and (.payload.type=="function_call" or .payload.type=="custom_tool_call" or .payload.type=="web_search_call" or .payload.type=="exec_command"))
   | select(.timestamp != null)
   | {ts:.timestamp, role:"tool", type:"tool",
      text:([.payload.name // "", .payload.command // "", (.payload.input // .payload.arguments // "" | tostring)] | join(" ") | .[0:300])})
  | select((.text | length) > 0)
  | .text |= (gsub("<[a-zA-Z0-9 ._-]+>[^<>]*</[a-zA-Z0-9 ._-]+>"; "") | gsub("<[^>]*>"; "") | gsub("\\s+"; " ") | sub("^ +"; "") | sub(" +$"; ""))
  | select((.text | length) > 0)
  | select(.text | test("^# ?AGENTS\\.md instructions") | not)
  | {project:$proj, role, type, text:(.text | .[0:800]), created_at:.ts,
     created_at_epoch:(.ts | sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601 | . * 1000)}
' 2>/dev/null || true)"
  NEW="$(python3 "$HUB_DIR/scripts/sanitize_jsonl.py" <<< "$NEW")"

# 5. 去重+偏移更新：mkdir 原子锁（macOS 无 flock，防 PostToolUse 高频并发竞争写丢数据）
#    锁内完成：内容去重（与轮次级 capture 共享 .seen，hash 统一对 del(.id) 核心字段，避免两路径重复入库）+ 偏移更新
_acquire_lock() {
  local tries=0
  while ! mkdir "$LOCKDIR" 2>/dev/null; do
    # 陈旧锁清理：锁存在超 10 秒视为残留（持有进程被 kill，trap 未触发），强制清除避免采集永久停止
    if [[ -d "$LOCKDIR" ]]; then
      local mtime
      mtime="$(stat -f %m "$LOCKDIR" 2>/dev/null || echo 0)"
      if [[ $(( $(date +%s) - mtime )) -gt 10 ]]; then
        rmdir "$LOCKDIR" 2>/dev/null || true
        continue
      fi
    fi
    tries=$((tries + 1))
    [[ $tries -gt 500 ]] && return 1
    sleep 0.01
  done
  return 0
}
_release_lock() { rmdir "$LOCKDIR" 2>/dev/null || true; }

if _acquire_lock; then
  trap '_release_lock' EXIT
  COUNT=0
  if [[ -n "$NEW" ]]; then
    while IFS= read -r line; do
      h="$(printf '%s' "$line" | jq -c 'del(.id)' 2>/dev/null | shasum | cut -c1-16)"
      if ! grep -qxF "$h" "$SEEN_FILE"; then
        echo "$h" >> "$SEEN_FILE"
        printf '%s\n' "$line" >> "$OBS"
        COUNT=$((COUNT + 1))
      fi
    done <<< "$NEW"
  fi
  awk -v f="$TP" '$1!=f' "$OFFSETS" > "$OFFSETS.tmp" 2>/dev/null || true
  echo "$TP $SIZE" >> "$OFFSETS.tmp"
  mv "$OFFSETS.tmp" "$OFFSETS"
  _release_lock
  trap - EXIT
else
  echo "realtime_capture: 获取锁失败，跳过本次（数据由下轮兜底采集）" >&2
fi
exit 0
