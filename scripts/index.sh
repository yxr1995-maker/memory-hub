#!/usr/bin/env bash
# memory-hub index: 把 ~/llm-wiki 的 markdown 索引进 SQLite FTS5（trigram 分词，支持中文子串匹配）
# 全量重建，零依赖（sqlite3 已带 FTS5）；--with-raw 包含 raw/ 目录
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${MEMORY_HUB_DATA:-$HOME/.memory-hub}"
DB="$DATA_DIR/index.db"
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

# 排除规则
if [[ "$WITH_RAW" == 1 ]]; then
  EXCLUDES=(-not -path '*/_legacy-para/*' -not -path '*/_archive/*')
else
  EXCLUDES=(-not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*')
fi

sqlite3 "$DB" <<'SQL'
DROP TABLE IF EXISTS pages;
CREATE VIRTUAL TABLE pages USING fts5(
  path, title, type, tags, abstract, content,
  tokenize='trigram'
);
SQL

# 单进程 + 单事务批量 INSERT（性能优化：3663 页从 ~3 分钟 → 秒级；原实现每页单独启动 sqlite3 进程）
COUNT=0
{
  echo "BEGIN;"
  while IFS= read -r -d '' f; do
    rel="${f#$WIKI/}"
    title="$(awk -F': ' '/^title:/{sub(/^title: /,""); print; exit}' "$f" 2>/dev/null | tr -d "'")"
    ptype="$(awk -F': ' '/^type:/{sub(/^type: /,""); print; exit}' "$f" 2>/dev/null | tr -d "'")"
    ptags="$(sed -n '/^tags:/,/^[^ -]/p' "$f" 2>/dev/null | sed -n 's/^  - //p' | tr '\n' ' ')"
    pabstract="$(awk -F': ' '/^abstract:/{sub(/^abstract: /,""); print; exit}' "$f" 2>/dev/null | tr -d "'")"
    # 正文：去掉 frontmatter（第一个 --- 到第二个 --- 之间）
    body="$(awk 'BEGIN{fm=0} /^---$/{fm++; next} fm>=2 || /^[^-]/ && fm==0 {print}' "$f" 2>/dev/null)"
    [[ -n "$title" ]] || title="$(basename "$f" .md)"
    [[ -n "$ptype" ]] || ptype="unknown"

    # SQL 注入防护：单引号转义
    rel_s="$(printf '%s' "$rel" | sed "s/'/''/g")"
    title_s="$(printf '%s' "$title" | sed "s/'/''/g")"
    ptype_s="$(printf '%s' "$ptype" | sed "s/'/''/g")"
    ptags_s="$(printf '%s' "$ptags" | sed "s/'/''/g")"
    pabstract_s="$(printf '%s' "$pabstract" | sed "s/'/''/g")"
    body_s="$(printf '%s' "$body" | sed "s/'/''/g")"

    printf "INSERT INTO pages(path,title,type,tags,abstract,content) VALUES('%s','%s','%s','%s','%s','%s');\n" \
      "$rel_s" "$title_s" "$ptype_s" "$ptags_s" "$pabstract_s" "$body_s"
  done < <(find "$WIKI" -name '*.md' "${EXCLUDES[@]+"${EXCLUDES[@]}"}" -print0 2>/dev/null)
  echo "COMMIT;"
} | sqlite3 "$DB"

# COUNT 在 done < <(find) 子 shell 里会丢失，改用 sqlite3 查询总数
TOTAL_INSERTED="$(sqlite3 "$DB" 'SELECT count(*) FROM pages;' 2>/dev/null || echo 0)"
echo "index: 索引完成，${TOTAL_INSERTED} 页 -> $DB"
sqlite3 "$DB" "SELECT count(*) AS total, min(length(content)) AS min_len, max(length(content)) AS max_len, avg(length(content)) AS avg_len FROM pages;" | awk -F'|' '{printf "  统计: %s 页, 内容长度 %s~%s (均值 %s)\n", $1, $2, $3, int($4)}'
