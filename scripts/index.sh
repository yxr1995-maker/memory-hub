#!/usr/bin/env bash
# memory-hub index: 把 ~/llm-wiki 的 markdown 索引进 SQLite FTS5（trigram 分词，支持中文子串匹配）
# 全量重建，零依赖（sqlite3 已带 FTS5）；--with-raw 包含 raw/ 目录
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${MEMORY_HUB_DATA:-$HOME/.memory-hub}"
DB="$DATA_DIR/index.db"
source "$HUB_DIR/scripts/lib.sh"
timing_begin
trap 'timing_end index "$?"' EXIT
WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
WITH_RAW=0

usage() {
  echo "用法: index.sh [--with-raw]"
  echo "  全量重建 ~/llm-wiki 的 FTS5 索引 → $DB"
  echo "  --with-raw  包含 raw/ 目录（默认排除 raw/_legacy-para/_archive）"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-raw) WITH_RAW=1 ;;
    --help|-h) usage ;;
    *) echo "未知参数: $1" >&2; usage ;;
  esac
  shift
done

[[ -d "$WIKI" ]] || { echo "错误: 知识库不存在: $WIKI" >&2; exit 1; }
mkdir -p "$DATA_DIR"

args=(--wiki "$WIKI" --data "$DATA_DIR")
[[ "$WITH_RAW" == 1 ]] && args+=(--with-raw)

PYTHONPATH="$HUB_DIR${PYTHONPATH:+:$PYTHONPATH}"
TOTAL_INSERTED="$(python3 -m scripts.automation_core.indexer "${args[@]}")"

echo "index: 索引完成，${TOTAL_INSERTED} 页 -> $DB"
sqlite3 "$DB" "SELECT count(*) AS total, min(length(content)) AS min_len, max(length(content)) AS max_len, avg(length(content)) AS avg_len FROM pages;" | awk -F'|' '{printf "  统计: %s 页, 内容长度 %s~%s (均值 %s)\n", $1, $2, $3, int($4)}'

