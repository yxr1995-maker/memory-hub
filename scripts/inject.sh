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
    # 候选按 mtime 取前 50，再用 frontmatter 解析过滤 rejected 后取 5：
    # 全量文件逐一起 python 太慢，grep 整文件则误伤正文与引号写法（P2 评审）。
    _CAND50="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' -print0 2>/dev/null | xargs -0 ls -t 2>/dev/null | head -50 || true)"
    # 区分“过滤后为空”（python exit=0，为正确答案）与“python 不可用”（exit!=0 才降级）：
    # 旧逻辑以输出为空判断，不可用与全 rejected 混同，导致全 rejected 时回退注入 rejected 页。
    _FILTER_TMP="$(mktemp)"
    _FILTER_N_TMP="$(mktemp)"
    if printf '%s\n' "$_CAND50" | MH_HUB="$HUB_DIR" MH_TOTAL="$_FILTER_N_TMP" python3 -c 'import os, sys
sys.path.insert(0, os.environ["MH_HUB"])
from pathlib import Path
from scripts.automation_core.frontmatter import parse_page
_kept = []
_total = 0
for _line in sys.stdin:
    _p = _line.rstrip("\n")
    if not _p:
        continue
    try:
        _fm = parse_page(Path(_p)).frontmatter or {}
    except Exception:
        _fm = {}
    if str(_fm.get("status") or "active") == "rejected":
        continue
    _total += 1
    if len(_kept) < 5:
        _kept.append(_p)
print("\n".join(_kept))
open(os.environ["MH_TOTAL"], "w").write(str(_total))
' > "$_FILTER_TMP" 2>/dev/null; then
      RECENT_LINES="$(cat "$_FILTER_TMP")"
      # 无库回退计数复用同一趟过滤结果，但总数与展示分开：展示取前 5，
      # 总数遍历全部候选（6 active 页不再被展示截断成 5）。
      # 候选不足 50 时为精确值，否则为下限（注记）。python 不可用时才接受含 rejected 的已知降级。
      _NOFILTER_TOTAL="$(cat "$_FILTER_N_TMP" 2>/dev/null || true)"
      if [[ "$_NOFILTER_TOTAL" =~ ^[0-9]+$ ]] && [[ -n "$_CAND50" ]]; then
        _CAND_N="$(printf '%s' "$_CAND50" | grep -c . || true)"
        if [[ "$_CAND_N" -lt 50 ]]; then
          _NOFILTER_N="$_NOFILTER_TOTAL"
        else
          _NOFILTER_N="$_NOFILTER_TOTAL+"
        fi
      fi
    elif [[ -n "$_CAND50" ]]; then
      # python 不可用时的降级：不过滤直接取 5（可能含 rejected）
      RECENT_LINES="$(head -5 <<< "$_CAND50")"
    fi
    rm -f "$_FILTER_TMP" "$_FILTER_N_TMP" 2>/dev/null || true
    if [[ -n "$RECENT_LINES" ]]; then
      while IFS= read -r f; do
        rel="${f#$WIKI/}"
        title="$(awk -F': ' '/^title:/{sub(/^title: /,""); print; exit}' "$f" 2>/dev/null | tr -d "'")"
        echo "- $rel — ${title:-无标题}"
      done <<< "$RECENT_LINES"
    else
      # 空列表两种含义：wiki 真空，或候选全被过滤（后者绝不回退未过滤候选）
      if [[ -n "$_CAND50" ]]; then
        echo "- 最近无可展示页（候选均为 rejected）"
      else
        echo "- 知识库为空或不可读"
      fi
    fi
  else
    echo "- 知识库不存在"
  fi
  echo ""
  echo "## 统计"
  if [[ -d "$WIKI" ]]; then
    # 优先读索引库计数（建库时已排除 rejected）；-exec 后缺 -print 曾使计数恒 0（P2 评审）。
    # 索引缺失时复用上趟 frontmatter 过滤结果；仅 python 不可用时回退含 rejected 的文件计数（已知降级）。
    N="$(MH_DB="${MEMORY_HUB_DATA:-$HOME/.memory-hub}/index.db" python3 -c 'import os, sqlite3
_db = os.environ["MH_DB"]
_con = sqlite3.connect("file:%s?mode=ro" % _db, uri=True, timeout=5)
try:
    print(_con.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
finally:
    _con.close()
' 2>/dev/null || true)"
    if [[ ! "$N" =~ ^[0-9]+$ ]]; then
      if [[ -n "${_NOFILTER_N:-}" ]]; then
        N="$_NOFILTER_N"
      else
        N="$(find "$WIKI" -name '*.md' -not -path '*/raw/*' -not -path '*/_legacy-para/*' -not -path '*/_archive/*' -print 2>/dev/null | wc -l | tr -d ' ')"
      fi
    fi
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
