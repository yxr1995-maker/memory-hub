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

TOTAL_INSERTED="$(python3 - "$WIKI" "$DB" "$WITH_RAW" <<'PY'
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

wiki, db, with_raw = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "1"
fd, tmp = tempfile.mkstemp(prefix=".index.", suffix=".db", dir=db.parent)
os.close(fd)
try:
    out = sqlite3.connect(tmp)
    out.executescript("""
        CREATE VIRTUAL TABLE pages USING fts5(
          path, title, type, tags, abstract, content, tokenize='trigram');
        CREATE TABLE meta(path TEXT PRIMARY KEY, updated TEXT, last_verified TEXT);
        CREATE TABLE vec(path TEXT PRIMARY KEY, title TEXT, dim INT, v BLOB);
    """)
    rows = []
    metadata = []
    excluded = {"_legacy-para", "_archive"} | (set() if with_raw else {"raw"})
    fail_after = int(os.environ.get("MEMORY_HUB_INDEX_FAIL_AFTER", "0"))
    for path in wiki.rglob("*.md"):
        rel = path.relative_to(wiki)
        if excluded.intersection(rel.parts):
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        lines = raw.splitlines()
        fields = {}
        tags = []
        body = raw
        if lines and lines[0].strip() == "---":
            end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
            if end is not None:
                current = ""
                for line in lines[1:end]:
                    if ":" in line and not line.startswith((" ", "-")):
                        current, value = line.split(":", 1)
                        fields[current] = value.strip().strip("'")
                    elif current == "tags" and line.startswith("  - "):
                        tags.append(line[4:])
                body = "\n".join(lines[end + 1:])
        rel_s = str(rel)
        rows.append((rel_s, fields.get("title") or path.stem,
                     fields.get("type") or "unknown", " ".join(tags),
                     fields.get("abstract", ""), body))
        metadata.append((rel_s, fields.get("updated", ""), fields.get("last_verified", "")))
        if fail_after and len(rows) >= fail_after:
            raise RuntimeError("injected index failure")
    with out:
        out.executemany("INSERT INTO pages VALUES(?,?,?,?,?,?)", rows)
        out.executemany("INSERT INTO meta VALUES(?,?,?)", metadata)
        if db.is_file():
            out.execute("ATTACH DATABASE ? AS old", (str(db),))
            if out.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name='vec'").fetchone():
                out.execute("INSERT INTO vec SELECT v.* FROM old.vec v JOIN pages p ON p.path=v.path")
    pages, meta = out.execute("SELECT count(*) FROM pages").fetchone()[0], out.execute("SELECT count(*) FROM meta").fetchone()[0]
    stale = out.execute("SELECT count(*) FROM vec v LEFT JOIN pages p ON p.path=v.path WHERE p.path IS NULL").fetchone()[0]
    if pages != meta or stale:
        raise RuntimeError(f"inconsistent index: pages={pages} meta={meta} stale_vec={stale}")
    out.execute("PRAGMA optimize")
    out.close()
    os.replace(tmp, db)
    print(pages)
except Exception:
    try:
        out.close()
    except Exception:
        pass
    os.unlink(tmp)
    raise
PY
)"
echo "index: 索引完成，${TOTAL_INSERTED} 页 -> $DB"
sqlite3 "$DB" "SELECT count(*) AS total, min(length(content)) AS min_len, max(length(content)) AS max_len, avg(length(content)) AS avg_len FROM pages;" | awk -F'|' '{printf "  统计: %s 页, 内容长度 %s~%s (均值 %s)\n", $1, $2, $3, int($4)}'
