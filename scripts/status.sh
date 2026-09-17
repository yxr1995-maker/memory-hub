#!/usr/bin/env bash
# memory-hub status: 健康检查/统计（对齐 gbrain doctor + claude-mem status）
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="${MEMORY_HUB_STAGING:-$HUB_DIR/staging}"
WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
SESSIONS_DIR="${CODEX_SESSIONS_DIR:-$HOME/.codex/sessions}"

source "$HUB_DIR/scripts/lib.sh"

echo "== memory-hub status ($(date '+%Y-%m-%d %H:%M:%S')) =="
# 1. Codex 会话
if [[ -d "$SESSIONS_DIR" ]]; then
  N_FILES="$(find "$SESSIONS_DIR" -type f -name '*.jsonl' -mtime -3 2>/dev/null | wc -l | tr -d ' ')"
  LATEST_SESSION="$(find "$SESSIONS_DIR" -type f -name '*.jsonl' -mtime -3 -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -1 || true)"
  echo "Codex会话(近3天): ${N_FILES} 个文件"
  if [[ -n "${LATEST_SESSION:-}" ]]; then
    echo "  最新会话: ${LATEST_SESSION#$SESSIONS_DIR/} ($(stat -f '%Sm' -t '%m-%d %H:%M' "$LATEST_SESSION"))"
  fi
else
  echo "Codex会话: 未找到 $SESSIONS_DIR"
fi
# 2. staging 观察
N_OBS="$(ls "$STAGING"/observations-*.jsonl 2>/dev/null | wc -l | tr -d ' ' || true)"
echo "staging观察文件: ${N_OBS}"
if [[ -f "$STAGING/.since" ]]; then
  SINCE_RAW="$(cat "$STAGING/.since")"
  [[ "$SINCE_RAW" =~ ^[0-9]+$ ]] || SINCE_RAW=0
  SINCE_TXT="$(date -r $((SINCE_RAW / 1000)) '+%Y-%m-%d %H:%M' 2>/dev/null || echo "$SINCE_RAW")"
  echo "  采集游标: $SINCE_TXT"
fi
LATEST_OBS="$(ls -t "$STAGING"/observations-*.jsonl 2>/dev/null | head -1 || true)"
if [[ -n "${LATEST_OBS:-}" ]]; then
  echo "  最新观察: $(basename "$LATEST_OBS") ($(wc -l < "$LATEST_OBS" | tr -d ' ') 条)"
fi
N_PEND_CAND=0
if ls "$STAGING"/pages/*.md >/dev/null 2>&1; then
  # 无匹配时 grep 退出 1：用 || true 吞掉，保证 pipefail 下 exit 0（M6 评审 #5）
  N_PEND_CAND="$({ grep -l '^status: candidate' "$STAGING"/pages/*.md 2>/dev/null || true; } | wc -l | tr -d ' ')"
fi
echo "待审候选(staging/pages): ${N_PEND_CAND:-0}"
DATA_DIR_DEFAULT="${MEMORY_HUB_DATA:-${HOME:-}/.memory-hub}"
EXP_DB="${MEMORY_HUB_EXPERIENCE_DB:-$DATA_DIR_DEFAULT/experience.sqlite3}"
if [[ -f "$EXP_DB" ]]; then
  # 只计当前 revision 且事件 active，与 /api/pending 同口径（M6 评审 #4）
  N_EXP_REV="$(sqlite3 "$EXP_DB" 'SELECT count(*) FROM experience_versions v JOIN experience_events e ON e.event_id=v.event_id AND e.revision=v.revision WHERE v.review_required=1 AND e.status='"'"'active'"'"';' 2>/dev/null || echo '?')"
  echo "待审经验(experience review_required): ${N_EXP_REV}"
else
  echo "待审经验(experience review_required): 0(无库)"
fi
# 3. ~/llm-wiki
if [[ -d "$WIKI" ]]; then
  N_PAGES="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' 2>/dev/null | wc -l | tr -d ' ')"
  echo "llm-wiki页面: ${N_PAGES}"
  # wiki 健康摘要
  TOKEN_RES="$(python3 "$HUB_DIR/scripts/verify_tokens.py" "$WIKI" || true)"
  TOKEN_HITS="$(echo "$TOKEN_RES" | grep '^token_hits=' | cut -d= -f2 || true)"
  DEAD_RES="$(cd "$WIKI" && python3 .scripts/fix_deadlinks.py 2>&1 || true)"
  DEAD_N="$(echo "$DEAD_RES" | awk -F': ' '/^未解\/多候选:/ {print $2}')"
  RAW_DEAD_N="$(echo "$DEAD_RES" | awk -F': ' '/^raw 区死链:/ {print $2}')"
  # concepts/queries 可能不存在：find 非零退出会被 pipefail 放大，加 || true（M6 评审 #5 同类）
  MH_N="$(find "$WIKI/concepts" "$WIKI/queries" -maxdepth 1 -type f -name '*memoryhub*' -o -name '*obse-rv-at-memoryhub*' 2>/dev/null | wc -l | tr -d ' ' || true)"
  echo "  健康: token命中=${TOKEN_HITS:-0} 死链=${DEAD_N:-?}(raw=${RAW_DEAD_N:-?}) concepts/queries-memoryhub=${MH_N:-0}"
  echo "  最近更新:"
  { find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' -print0 2>/dev/null \
    | xargs -0 ls -t 2>/dev/null | head -3 | while read -r f; do
        echo "    - ${f#$WIKI/}"
      done; } || true
else
  echo "llm-wiki: 未找到 $WIKI"
fi
# 4. claude-mem DB
CM_DB="${CLAUDE_MEM_DB:-$HOME/.claude-mem/data/claude-mem.db}"
if [[ -f "$CM_DB" ]]; then
  CM_N="$(sqlite3 "$CM_DB" 'SELECT count(*) FROM observations;' 2>/dev/null || echo '?')"
  echo "claude-mem: DB存在, ${CM_N} 条观察"
else
  echo "claude-mem: 未安装/不可用"
fi
# 5. 本地 LLM 代理
PROXY_BASE="${OPENCODEX_URL:-http://127.0.0.1:10100/v1}"
PROXY_LABEL="${PROXY_BASE#http://}"
PROXY_LABEL="${PROXY_LABEL%/v1}"
# Any HTTP answer means the proxy is up; only a failed connection counts as down.
PROXY_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$PROXY_BASE/models" 2>/dev/null || echo 000)"
if [[ "$PROXY_CODE" =~ ^[1-5][0-9][0-9]$ ]]; then
  echo "LLM代理($PROXY_LABEL): 可用 (http $PROXY_CODE)"
else
  echo "LLM代理($PROXY_LABEL): 不可用"
fi
# 6. 近7天使用量摘要（缺数据降级为0，不中断）
python3 "$HUB_DIR/scripts/usage.py" --days 7 2>/dev/null || echo "使用量(近7天): 无数据"
