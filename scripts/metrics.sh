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

SESSIONS_DIR="${CODEX_SESSIONS_DIR:-${HOME:-}/.codex/sessions}"
N_SESSIONS=0
if [[ -d "$SESSIONS_DIR" ]]; then
  N_SESSIONS="$(find "$SESSIONS_DIR" -type f -name '*.jsonl' -mtime -3 2>/dev/null | wc -l | tr -d ' ')"
fi
N_OBS_FILES=0
if [[ -d "$STAGING" ]]; then
  N_OBS_FILES="$(ls "$STAGING"/observations-*.jsonl 2>/dev/null | wc -l | tr -d ' ' || true)"
fi
N_OBS_LINES=0
if [[ -d "$STAGING" ]]; then
  N_OBS_LINES="$(cat "$STAGING"/observations-*.jsonl 2>/dev/null | wc -l | tr -d ' ' || true)"
fi
RT_FILE="$STAGING/observations-realtime.jsonl"
N_RT_LINES=0
if [[ -f "$RT_FILE" ]]; then
  N_RT_LINES="$(wc -l < "$RT_FILE" | tr -d ' ')"
fi
N_PAGES=0
if [[ -d "$WIKI" ]]; then
  N_PAGES="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' 2>/dev/null | wc -l | tr -d ' ')"
fi
DB_BYTES=0
if [[ -f "$DB" ]]; then
  DB_BYTES="$(stat -f '%z' "$DB" 2>/dev/null || echo 0)"
fi
CM_DB="${CLAUDE_MEM_DB:-${HOME:-}/.claude-mem/data/claude-mem.db}"
CM_ROWS=0
if [[ -n "${HOME:-}" && -f "$CM_DB" ]]; then
  CM_ROWS="$(sqlite3 "$CM_DB" 'SELECT count(*) FROM observations;' 2>/dev/null || echo 0)"
fi

gauge sessions_recent "$N_SESSIONS"
gauge observations_files "$N_OBS_FILES"
count observations_lines "$N_OBS_LINES"
count realtime_observations_lines "$N_RT_LINES"

if [[ -d "$STAGING" ]]; then
  LATEST_OBS="$(ls -t "$STAGING"/observations-*.jsonl 2>/dev/null | head -1 || true)"
  if [[ -n "${LATEST_OBS:-}" ]]; then
    MTIME="$(stat -f '%m' "$LATEST_OBS" 2>/dev/null || echo 0)"
    gauge last_capture_age_seconds "$(( $(date '+%s') - MTIME ))"
  fi
fi
gauge wiki_pages "$N_PAGES"
gauge index_db_bytes "$DB_BYTES"
gauge claude_mem_rows "$CM_ROWS"

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

# 自动化操作 Prometheus 指标
REPORTS_DIR="$DATA_DIR/reports"
if [[ -d "$REPORTS_DIR" ]]; then
  python3 - "$REPORTS_DIR" <<'PY'
import glob, json, os, sys
reports_dir = sys.argv[1]

for f in glob.glob(os.path.join(reports_dir, "scope-*.jsonl")):
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip(): continue
                d = json.loads(line)
                scope = d.get("scope", "unknown")
                conf = d.get("confidence", "unknown")
                res = d.get("result", "unknown")
                print(f'memory_hub_scope_backfill_total{{scope="{scope}",confidence="{conf}",result="{res}"}} 1')
                if d.get("conflict"):
                    print('memory_hub_scope_conflict_total 1')
    except Exception:
        pass

for f in glob.glob(os.path.join(reports_dir, "query-plan-*.jsonl")):
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip(): continue
                d = json.loads(line)
                planner = d.get("planner", "unknown")
                fb = d.get("fallback_reason") or "none"
                print(f'memory_hub_query_plan_total{{planner="{planner}",fallback_reason="{fb}"}} 1')
                n_exp = len(d.get("expansions", []))
                print(f'memory_hub_query_expand_terms {n_exp}')
    except Exception:
        pass

for f in glob.glob(os.path.join(reports_dir, "lifecycle-*.jsonl")):
    try:
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip(): continue
                d = json.loads(line)
                dec = d.get("decision", "unknown")
                res = d.get("result", "unknown")
                print(f'memory_hub_successor_total{{decision="{dec}",result="{res}"}} 1')
    except Exception:
        pass

for f in glob.glob(os.path.join(reports_dir, "operation-*.json")):
    try:
        with open(f, encoding="utf-8") as fh:
            d = json.loads(fh.read())
            cmd = d.get("command", "unknown")
            mode = d.get("mode", "unknown")
            res = d.get("result", "unknown")
            print(f'memory_hub_auto_operation_total{{command="{cmd}",mode="{mode}",result="{res}"}} 1')
    except Exception:
        pass
PY
fi
