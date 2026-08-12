#!/usr/bin/env bash
# memory-hub publish: staging/pages -> ~/llm-wiki 按 type 映射目录 + 更新 index.md / log.md
# 默认 dry-run（只预览不写入）; --apply 才落盘。永不覆盖已存在页面。
# 对齐 openwiki OKF / gbrain 规范: frontmatter 校验 + 目录映射 + log 防重复
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
PAGES_DIR="$STAGING/pages"
source "$HUB_DIR/scripts/lib.sh"
timing_begin
trap 'timing_end publish "$?"' EXIT
WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
APPLY=0
COMMIT=0

# type -> 目录 映射
type_dir() {
  case "$1" in
    decision) echo "decisions" ;;
    failure) echo "failures" ;;
    concept) echo "concepts" ;;
    query) echo "queries" ;;
    entity) echo "entities" ;;
    comparison) echo "comparisons" ;;
    *) echo "queries" ;;
  esac
}

usage() {
  echo "用法: publish.sh [--apply] [--commit]"
  echo "  默认 dry-run: 预览待发布页面、frontmatter 校验、目录映射"
  echo "  --commit: --apply 基础上，在 ~/llm-wiki 中 git add + commit 本次发布内容"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply) APPLY=1 ;;
    --dry-run) APPLY=0 ;;
    --commit) COMMIT=1 ;;
    --help|-h) usage ;;
    *) echo "未知参数: $1" >&2; usage ;;
  esac
  shift
done

[[ -d "$WIKI" ]] || { echo "错误: 知识库不存在: $WIKI (WIKI_PATH=$WIKI)" >&2; exit 1; }

shopt -s nullglob
PAGES=("$PAGES_DIR"/*.md)
[[ ${#PAGES[@]} -gt 0 ]] || { echo "publish: 无待发布页面"; exit 0; }

REQUIRED_FIELDS="title type created tags sources abstract"

# —— frontmatter 校验 ——
VALID_PAGES=()
for f in "${PAGES[@]}"; do
  MISSING=""
  for field in $REQUIRED_FIELDS; do
    grep -q "^${field}:" "$f" || MISSING="$MISSING $field"
  done
  if [[ -n "$MISSING" ]]; then
    echo "publish: [跳过] $(basename "$f") 缺 frontmatter 字段:$MISSING"
  else
    VALID_PAGES+=("$f")
  fi
done

if [[ ${#VALID_PAGES[@]} -eq 0 ]]; then
  echo "publish: 无通过校验的页面"
  exit 0
fi

if [[ "$APPLY" == 0 ]]; then
  echo "publish: [dry-run] 以下 ${#VALID_PAGES[@]} 页将通过校验:"
  for f in "${VALID_PAGES[@]}"; do
    T="$(sed -n 's/^type: *//p' "$f" | tr -d "'")"
    DIR="$(type_dir "$T")"
    echo "  + $DIR/$(basename "$f") (type=$T)"
  done
  echo "publish: [dry-run] index.md / log.md 将相应更新"
  if [[ "$COMMIT" == 1 ]]; then
    echo "publish: [dry-run] 将执行 --commit（git add + commit 本次发布内容）"
    echo "publish: 确认无误后运行: publish.sh --apply --commit"
  else
    echo "publish: 确认无误后运行: publish.sh --apply（或 memory-hub.sh run --apply）"
  fi
  exit 0
fi

# —— 落盘 ——
COPIED=0
mkdir -p "$STAGING/published"
LOG_ENTRIES=""
for f in "${VALID_PAGES[@]}"; do
  SLUG="$(basename "$f")"
  T="$(sed -n 's/^type: *//p' "$f" | tr -d "'")"
  DIR="$(type_dir "$T")"
  TARGET="$WIKI/$DIR/$SLUG"
  if [[ -f "$TARGET" ]]; then
    echo "publish: 跳过(已存在): $DIR/$SLUG"
    continue
  fi
  mkdir -p "$WIKI/$DIR"
  cp "$f" "$TARGET"
  mv "$f" "$STAGING/published/$SLUG"
  COPIED=$((COPIED + 1))
  echo "publish: + $DIR/$SLUG"
  if ! grep -qF "发布 $SLUG" "$WIKI/log.md" 2>/dev/null; then
    LOG_ENTRIES+="- $(date '+%Y-%m-%d %H:%M') 发布 $SLUG ($T)"$'\n'
  fi
done

# 追加 log.md（防重复）
if [[ -n "$LOG_ENTRIES" ]]; then
  printf '%s' "$LOG_ENTRIES" >> "$WIKI/log.md"
fi

# index.md 更新（对应分区段头后插入新页行）
if [[ "$COPIED" -gt 0 ]]; then
  INDEX_FILE="$(mktemp)"
  for PUB in "$STAGING"/published/*.md; do
    [[ -f "$PUB" ]] || continue
    SLUG="$(basename "$PUB" .md)"
    T="$(sed -n 's/^type: *//p' "$PUB" | tr -d "'")"
    DIR="$(type_dir "$T")"
    TITLE="$(sed -n 's/^title: //p' "$PUB" | tr -d "'")"
    if [[ -f "$WIKI/$DIR/$SLUG.md" ]] && ! grep -qF "[[$DIR/$SLUG]]" "$WIKI/index.md" 2>/dev/null; then
      echo "- [[$DIR/$SLUG]] — ${TITLE:-memory-hub 蒸馏}" >> "$INDEX_FILE"
    fi
  done
  if [[ -s "$INDEX_FILE" ]]; then
    # 在 index.md 尾部追加新页行（不更新旧行，只补新页；避免破坏现有结构）
    cat "$INDEX_FILE" >> "$WIKI/index.md"
  fi
  rm -f "$INDEX_FILE"
fi

echo "publish: 完成，发布 $COPIED 页; index.md / log.md 已更新"
echo "publish: gbrain 索引源即 ~/llm-wiki，新页会被自动纳入检索"

# --commit: 在 ~/llm-wiki 中只 add 本次发布涉及的文件，提交归属清楚的 commit
if [[ "$COMMIT" == 1 ]]; then
  cd "$WIKI" || { echo "publish: [commit] 无法进入 $WIKI"; exit 1; }
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "publish: [commit] $WIKI 不是 git 仓库，跳过提交"
    exit 0
  fi
  # ponytail: 精确 add 而非 add -A，避免把 .obsidian/、.tmp、他人改动带入 commit
  ADD_FILES=()
  for PUB in "$STAGING"/published/*.md; do
    [[ -f "$PUB" ]] || continue
    SLUG="$(basename "$PUB" .md)"
    T="$(sed -n 's/^type: *//p' "$PUB" | tr -d "'")"
    DIR="$(type_dir "$T")"
    TARGET="$WIKI/$DIR/$SLUG.md"
    [[ -f "$TARGET" ]] || continue
    ADD_FILES+=("$DIR/$SLUG.md")
  done
  # index.md / log.md：publish 逻辑已写入这两文件，直接提交
  ADD_FILES+=("index.md" "log.md")
  git add -- "${ADD_FILES[@]}" 2>/dev/null || true
  HASH="$(git commit -m "feat(memoryhub): 蒸馏发布 ${#ADD_FILES[@]} 文件" 2>/dev/null || true)"
  if [[ -n "$HASH" ]]; then
    echo "publish: [commit] $HASH"
    git diff --cached --stat 2>/dev/null
  else
    echo "publish: [commit] 无变更或提交失败"
  fi
fi
