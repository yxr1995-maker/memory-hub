#!/usr/bin/env bash
# memory-hub metrics: 输出 Prometheus text 格式（兼容 node_exporter textfile collector）
# 指标含即时统计 + timings.tsv 阶段耗时（capture/distill/publish/index/embed）
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
DATA_DIR="${MEMORY_HUB_DATA:-$HOME/.memory-hub}"
DB="$DATA_DIR/index.db"
TIMINGS="$DATA_DIR/timings.tsv"

gauge() {
  local name="$1" val="$2"
  echo "# TYPE memory_hub_${name} gauge"
  echo "memory_hub_${name} $val"
}

count() {
  local name="$1" val="$2"
  echo "# TYPE memory_hub_${name} counter"
  echo "memory_hub_${name} $val"
}

SESSIONS_DIR="${CODEX_SESSIONS_DIR:-$HOME/.codex/sessions}"
N_SESSIONS="$(find "$SESSIONS_DIR" -type f -name '*.jsonl' -mtime -3 2>/dev/null | wc -l | tr -d ' ')"
N_OBS_FILES="$(ls "$STAGING"/observations-*.jsonl 2>/dev/null | wc -l | tr -d ' ')"
N_OBS_LINES="$(cat "$STAGING"/observations-*.jsonl 2>/dev/null | wc -l | tr -d ' ')"
RT_FILE="$STAGING/observations-realtime.jsonl"
N_RT_LINES=0
if [[ -f "$RT_FILE" ]]; then
  N_RT_LINES="$(wc -l < "$RT_FILE" | tr -d ' ')"
fi
N_PAGES="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' 2>/dev/null | wc -l | tr -d ' ')"
DB_BYTES=0
if [[ -f "$DB" ]]; then
  DB_BYTES="$(stat -f '%z' "$DB")"
fi
CM_DB="${CLAUDE_MEM_DB:-$HOME/.claude-mem/data/claude-mem.db}"
CM_ROWS=0
if [[ -f "$CM_DB" ]]; then
  CM_ROWS="$(sqlite3 "$CM_DB" 'SELECT count(*) FROM observations;' 2>/dev/null || echo 0)"
fi

gauge sessions_recent "$N_SESSIONS"
gauge observations_files "$N_OBS_FILES"
count observations_lines "$N_OBS_LINES"
count realtime_observations_lines "$N_RT_LINES"
# 最近一次采集落盘距今（秒），衡量实时采集是否停滞
LATEST_OBS="$(ls -t "$STAGING"/observations-*.jsonl 2>/dev/null | head -1 || true)"
if [[ -n "${LATEST_OBS:-}" ]]; then
  MTIME="$(stat -f '%m' "$LATEST_OBS")"
  gauge last_capture_age_seconds "$(( $(date '+%s') - MTIME ))"
fi
gauge wiki_pages "$N_PAGES"
gauge index_db_bytes "$DB_BYTES"
gauge claude_mem_rows "$CM_ROWS"

# wiki 内容健康（来自 verify.sh 的扫描结果）
WIKI_DIR="${WIKI_PATH:-$HOME/llm-wiki}"
TOKEN_HITS="$(python3 - "$WIKI_DIR" <<'PY'
import pathlib, re, sys
wiki = pathlib.Path(sys.argv[1])
patterns = [
    re.compile(r'Bearer\s+\S+'),
    re.compile(r'eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*'),
    re.compile(r'sk-(?:ant-)?[A-Za-z0-9_\-]{16,}'),
]
doc_markers = [
    re.compile(r'--bearer-token-env-var'),
    re.compile(r'Bearer token', re.I),
]
hits = 0
for p in wiki.rglob('*.md'):
    if 'raw/' in p.as_posix() or '/_' in p.as_posix():
        continue
    for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
        if any(m.search(line) for m in doc_markers):
            continue
        if any(pat.search(line) for pat in patterns):
            hits += 1
            break
print(hits)
PY
)"
gauge wiki_token_hits "${TOKEN_HITS:-0}"

DEAD_RES="$(cd "$WIKI_DIR" && python3 .scripts/fix_deadlinks.py 2>&1)"
DEAD_N="$(echo "$DEAD_RES" | awk -F': ' '/^未解\/多候选:/ {print $2}')"
RAW_DEAD_N="$(echo "$DEAD_RES" | awk -F': ' '/^raw 区死链:/ {print $2}')"
gauge wiki_dead_links "${DEAD_N:-0}"
gauge wiki_raw_dead_links "${RAW_DEAD_N:-0}"

MH_N="$(find "$WIKI_DIR/concepts" "$WIKI_DIR/queries" -maxdepth 1 -type f -name '*memoryhub*' -o -name '*obse-rv-at-memoryhub*' 2>/dev/null | wc -l | tr -d ' ')"
gauge wiki_memoryhub_stray_pages "${MH_N:-0}"

if [[ -f "$TIMINGS" ]]; then
  echo "# TYPE memory_hub_duration_seconds_total counter"
  echo "# TYPE memory_hub_duration_seconds_count counter"
  echo "# TYPE memory_hub_duration_seconds_last gauge"
  awk -F'\t' '
    { c[$2]++; s[$2] += $3; l[$2] = $3 }
    END {
      for (k in c) {
        printf "memory_hub_duration_seconds_total{stage=\"%s\"} %.3f\n", k, s[k]/1000
        printf "memory_hub_duration_seconds_count{stage=\"%s\"} %d\n", k, c[k]
        printf "memory_hub_duration_seconds_last{stage=\"%s\"} %.3f\n", k, l[k]/1000
      }
    }' "$TIMINGS" | sort
fi
