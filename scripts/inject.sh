#!/usr/bin/env bash
# memory-hub inject: 记忆上下文注入
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
APPLY=0
TARGET=""
SCOPE=""
SCOPE_ID=""
MARKER_START="<!-- memctl-memory-start -->"
MARKER_END="<!-- memctl-memory-end -->"

usage() {
  echo "用法: inject.sh [--apply] [--file <AGENTS.md>] [--scope <user|project|agent>] [--scope-id <id>]"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --file) TARGET="$2"; shift ;;
    --scope) SCOPE="$2"; shift ;;
    --scope-id) SCOPE_ID="$2"; shift ;;
    --help|-h) usage ;;
    *) shift ;;
  esac
  shift
done

OUT="$(mktemp)"
PYTHONPATH="$HUB_DIR${PYTHONPATH:+:$PYTHONPATH}"
args=(search "" --json --top 5)
[[ -n "$SCOPE" ]] && args+=(--scope "$SCOPE")
[[ -n "$SCOPE_ID" ]] && args+=(--scope-id "$SCOPE_ID")

{
  echo "<!-- memctl: Agent 记忆上下文（自动生成，$(date '+%Y-%m-%d %H:%M')）-->"
  echo ""
  echo "## 知识库最近更新（~/llm-wiki）"
  if [[ -d "$WIKI" ]]; then
    RECENT_LINES="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -5 || true)"
    if [[ -n "$RECENT_LINES" ]]; then
      while IFS= read -r f; do
        rel="${f#$WIKI/}"
        title="$(awk -F': ' '/^title:/{sub(/^title: /,""); print; exit}' "$f" 2>/dev/null | tr -d "'")"
        echo "- $rel — ${title:-无标题}"
      done <<< "$RECENT_LINES"
    else
      echo "- 知识库为空或不可读"
    fi
  else
    echo "- 知识库不存在"
  fi
  echo ""
  echo "## 统计"
  if [[ -d "$WIKI" ]]; then
    N="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' 2>/dev/null | wc -l | tr -d ' ')"
    echo "- 知识库页面: ${N}"
  fi
} > "$OUT"

if [[ "$APPLY" == 1 ]]; then
  [[ -n "$TARGET" ]] || { echo "错误: --apply 需要 --file <路径>" >&2; exit 1; }
  [[ "$TARGET" == /* ]] || { echo "错误: --file 必须是绝对路径" >&2; exit 1; }
  [[ -L "$TARGET" ]] && { echo "错误: 拒绝写入符号链接: $TARGET" >&2; exit 1; }
  mkdir -p "$(dirname "$TARGET")"
  if [[ -f "$TARGET" ]]; then
    cp "$TARGET" "$TARGET.bak"
    awk -v start="$MARKER_START" -v end="$MARKER_END" -v out="$OUT" '
      $0 == start { print; while ((getline line < out) > 0) print line; close(out); skip=1; next }
      skip && $0 == end { skip=0; print; next }
      !skip { print }
    ' "$TARGET" > "$TARGET.new"
    mv "$TARGET.new" "$TARGET"
  else
    {
      echo "$MARKER_START"
      cat "$OUT"
      echo ""
      echo "$MARKER_END"
    } > "$TARGET"
  fi
  echo "inject: 已写入 ${TARGET}（备份: ${TARGET}.bak）"
else
  cat "$OUT"
fi
rm -f "$OUT" 2>/dev/null || true
