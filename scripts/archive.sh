#!/usr/bin/env bash
# memory-hub archive (F4): 归档已消费的 observation 文件到 staging/archive/（可恢复，不 rm）
# distill 每次只消费最新一份日期命名的 observations-*.jsonl，其余均为已消费中间产物；
# 只处理 observations-YYYYMMDD-HHMMSS.jsonl，绝不碰 realtime/test；
# 默认归档除最新 --keep 份外的全部（保留最新供下一轮 distill 增量），--apply 才真正移动
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
ARCHIVE="$STAGING/archive"
KEEP=1
APPLY=0

usage() {
  echo "用法: archive.sh [--keep N] [--apply]"
  echo "  --keep N   保留最新 N 份日期命名观察文件（默认 1，供 distill 增量消费）"
  echo "  --apply    真正移动；默认 dry-run 只列出将归档的文件"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --keep) KEEP="$2"; shift ;;
    --apply) APPLY=1 ;;
    --help|-h) usage ;;
    *) echo "未知参数: $1" >&2; usage ;;
  esac
  shift
done

mkdir -p "$ARCHIVE"

# ls -t 写临时文件再 head，避免管道 SIGPIPE 触发 set -e 退出；只匹配日期命名文件
TMP="$(mktemp /tmp/mh-archive-ls.XXXXXX)"
ls -t "$STAGING"/observations-????????-??????.jsonl > "$TMP" 2>/dev/null || true
KEEP_FILES="$(head -n "$KEEP" "$TMP")"
rm -f "$TMP"

MOVED=0
for f in "$STAGING"/observations-????????-??????.jsonl; do
  [[ -f "$f" ]] || continue
  keep=0
  for kf in $KEEP_FILES; do
    [[ "$f" == "$kf" ]] && keep=1 && break
  done
  [[ "$keep" == 1 ]] && continue
  if [[ "$APPLY" == 1 ]]; then
    mv "$f" "$ARCHIVE/"
    echo "archive: $(basename "$f")"
  else
    echo "archive: [dry-run] $(basename "$f")"
  fi
  MOVED=$((MOVED + 1))
done

echo "archive: $([ "$APPLY" == 1 ] && echo 归档 || echo dry-run) $MOVED 个文件 -> $ARCHIVE (保留最新 $KEEP 份)"

