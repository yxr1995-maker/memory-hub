#!/usr/bin/env bash
# memory-hub obs_search: 检索 staging 原始观察记录（对齐 claude-mem mcp-search 的 search）
# 与 memory_search（检索 llm-wiki 蒸馏页）互补：这里检索未蒸馏的原始会话观察
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
TOP=10
PROJECT=""

usage() {
  echo "用法: obs_search.sh \"关键词\" [--top N] [--project <名>]"
  echo "  检索 staging/observations-*.jsonl 原始观察记录"
  echo "  --project <名>  只看指定项目"
  exit 0
}

Q=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --top) TOP="$2"; shift ;;
    --project) PROJECT="$2"; shift ;;
    --help|-h) usage ;;
    -*) echo "未知参数: $1" >&2; usage ;;
    *) Q="$1" ;;
  esac
  shift
done

[[ -n "$Q" ]] || { echo "用法: obs_search.sh \"关键词\"" >&2; exit 1; }
shopt -s nullglob
FILES=("$STAGING"/observations-*.jsonl)
[[ ${#FILES[@]} -gt 0 ]] || { echo "无观察数据（先运行 capture）"; exit 0; }

echo "== 原始观察检索: $Q ${PROJECT:+(项目: $PROJECT)} =="
# 用 jq 流式过滤：project 过滤 + text 关键词（大小写不敏感）+ 取前 N 条
jq -rc --arg q "$Q" --arg proj "$PROJECT" --argjson top "$TOP" '
  (if $proj != "" then select(.project == $proj) else . end)
  | select((.text // "") | ascii_downcase | contains($q | ascii_downcase))
  | "[\(.id)] \(.project) (\(.role)/\(.type)) \(.created_at // ""): \(.text | .[0:150])"
' "${FILES[@]}" 2>/dev/null | head -"$TOP"
