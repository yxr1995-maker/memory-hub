#!/usr/bin/env bash
# memory-hub inject: 记忆上下文注入（对齐 claude-mem AGENTS.md 注入机制）
# 默认输出 Markdown 到 stdout; --apply 时写入 --file 指定文件（MARKER 区段替换，不破坏其他内容）
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
APPLY=0
TARGET=""
MARKER_START="<!-- memctl-memory-start -->"
MARKER_END="<!-- memctl-memory-end -->"

usage() {
  echo "用法: inject.sh [--apply] [--file <AGENTS.md>]"
  echo "  默认输出记忆上下文到 stdout; --apply 写入 --file 指定文件（MARKER 区段）"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --file) TARGET="$2"; shift ;;
    --help|-h) usage ;;
    *) echo "未知参数: $1" >&2; usage ;;
  esac
  shift
done

OUT="$(mktemp)"
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
  echo "## 最近采集观察（staging）"
  LATEST="$(ls -t "$STAGING"/observations-*.jsonl 2>/dev/null | head -1 || true)"
  if [[ -n "${LATEST:-}" ]]; then
    jq -sr '.[0:10][] | "- [\(.id)] \(.text | .[0:100])"' "$LATEST" 2>/dev/null | sed 's/\\n/ /g'
  else
    echo "- 暂无（先运行 capture）"
  fi
  echo ""
  echo "## 统计"
  if [[ -d "$WIKI" ]]; then
    N="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' 2>/dev/null | wc -l | tr -d ' ')"
    echo "- 知识库页面: ${N}"
  fi
  if [[ -f "$STAGING/.since" ]]; then
    echo "- 采集游标: $(cat "$STAGING/.since")"
  fi
} > "$OUT"

if [[ "$APPLY" == 1 ]]; then
  [[ -n "$TARGET" ]] || { echo "错误: --apply 需要 --file <路径>" >&2; exit 1; }
  # 路径安全：必须绝对路径、拒绝符号链接（防止写入意外位置）
  [[ "$TARGET" == /* ]] || { echo "错误: --file 必须是绝对路径" >&2; exit 1; }
  [[ -L "$TARGET" ]] && { echo "错误: 拒绝写入符号链接: $TARGET" >&2; exit 1; }
  mkdir -p "$(dirname "$TARGET")"
  if [[ -f "$TARGET" ]]; then
    # MARKER 完整性校验：缺任一端点即报错退出，防止静默截断目标文件
    grep -qF "$MARKER_START" "$TARGET" || { echo "错误: 目标文件缺少 MARKER_START（不注入，防止破坏）" >&2; exit 1; }
    grep -qF "$MARKER_END" "$TARGET" || { echo "错误: 目标文件缺少 MARKER_END（不注入，防止截断）" >&2; exit 1; }
    # 写入前备份（可恢复）
    cp "$TARGET" "$TARGET.bak"
    # 单遍替换 MARKER 区段：整行字面匹配（== 而非 ~ 正则，避免内容含标记子串时误判）
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
rm -f "$OUT" "$TARGET.tmp" "$TARGET.new" 2>/dev/null || true
