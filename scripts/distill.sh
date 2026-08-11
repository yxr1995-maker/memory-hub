#!/usr/bin/env bash
# memory-hub distill: 观察记录 -> 合规 wiki 页面(frontmatter 6 字段 + wikilink + confidence)
# 只写 staging/pages/，不碰知识库; --llm 时用本地免费模型生成 AI 摘要(尽力而为)
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING="$HUB_DIR/staging"
PAGES_DIR="$STAGING/pages"
LLM=0
MODEL="${CLAUDE_MEM_MODEL:-combo/freelancer}"
PROXY="${OPENCODEX_URL:-http://127.0.0.1:10100/v1}"

# YAML 单引号标量转义：' → ''，换行 → 空格（防止 title/abstract 含单引号或换行破坏 frontmatter）
yaml_sq() {
  printf '%s' "$1" | sed "s/'/''/g" | tr '\n' ' '
}

usage() {
  echo "用法: distill.sh [--llm] [<observations-*.jsonl>]"
  echo "  默认使用 staging/ 里最新一份 jsonl"
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --llm) LLM=1 ;;
    --help|-h) usage ;;
    -*) echo "未知参数: $1" >&2; usage ;;
    *) IN="$1" ;;
  esac
  shift
done

if [[ -z "${IN:-}" ]]; then
  IN="$(ls -t "$STAGING"/observations-*.jsonl 2>/dev/null | head -1 || true)"
fi
[[ -n "${IN:-}" && -f "$IN" ]] || { echo "distill: 无观察数据（先运行 capture.sh）"; exit 0; }

mkdir -p "$PAGES_DIR"
rm -f "$PAGES_DIR"/*.md 2>/dev/null || true

TS_BASE="$(basename "$IN" | sed -E 's/observations-([0-9]{8})-([0-9]{6})\.jsonl/\1-\2/')"
DATE="${TS_BASE:0:4}-${TS_BASE:4:2}-${TS_BASE:6:2}"

llm_summary() {
  local project="$1"
  local content
  content="$(jq -r --arg p "$project" 'select(.project==$p) | "- [\(.type)] \(.title // .text // "")"' "$IN" | head -40)"
  [[ -n "$content" ]] || return 0
  local payload
  payload="$(jq -n --arg model "$MODEL" \
    --arg sys '你是记忆蒸馏器。把下面 claude-mem 观察记录压缩成 3-5 条要点，中文，每条以 "- " 开头。只基于给定内容，不要编造。' \
    --arg content "$content" \
    '{model:$model, messages:[{role:"system",content:$sys},{role:"user",content:$content}], temperature:0.2}')"
  curl -s --max-time 30 -H 'Content-Type: application/json' -d "$payload" "$PROXY/chat/completions" \
    | jq -r '.choices[0].message.content // empty' 2>/dev/null || true
}

PAGES=0
for p in $(jq -r '.project' "$IN" | sort -u); do
  [[ -n "$p" ]] || continue
  safe_p="$(printf '%s' "$p" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-' | sed -E 's/-+/-/g' | sed -E 's/^-|-$//')"
  [[ -n "$safe_p" ]] || safe_p="misc"

  # —— type 智能映射（决策/失败/对比，默认 concept）——
  GROUP_TEXT="$(jq -sr --arg p "$p" 'first(.[] | select(.project==$p)) | .text | .[0:2000]' "$IN")"
  PAGE_TYPE="concept"
  if [[ "$GROUP_TEXT" =~ 决策|决定|确认|改为|方案 ]]; then
    PAGE_TYPE="decision"
  elif [[ "$GROUP_TEXT" =~ 报错|失败|踩坑|错误|坑|问题 ]]; then
    PAGE_TYPE="failure"
  elif [[ "$GROUP_TEXT" =~ 对比|比较|横评 ]]; then
    PAGE_TYPE="comparison"
  fi

  STAMP="$(date +%H%M%S)"
  TOTAL_N="$(jq -c --arg p "$p" 'select(.project==$p)' "$IN" 2>/dev/null | wc -l | tr -d ' ')"
  [[ "$TOTAL_N" =~ ^[0-9]+$ ]] || TOTAL_N=0
  TYPES="$(jq -r --arg p "$p" 'select(.project==$p) | .type' "$IN" | sort | uniq -c | awk '{printf "%s×%s ", $2, $1}' | sed 's/ $//')"
  ROLE_N="$(jq -r --arg p "$p" 'select(.project==$p) | .role' "$IN" | sort | uniq -c | awk '{printf "%s×%s ", $2, $1}' | sed 's/ $//')"
  CHUNK_SIZE="${DETAIL_CAP:-60}"
  NCHUNKS=$(( (TOTAL_N + CHUNK_SIZE - 1) / CHUNK_SIZE ))
  # 同 project 其他分页 slug 列表（互链用）
  shopt -s nullglob
  SLUG_FILES=("$PAGES_DIR"/*.md)
  ALL_SLUGS=""
  if [[ ${#SLUG_FILES[@]} -gt 0 ]]; then
    ALL_SLUGS="$(printf '%s\n' "${SLUG_FILES[@]}" | sed 's#.*/##; s#\.md$##' | tr '\n' ' ')"
  fi

  for ((i=0; i<NCHUNKS; i++)); do
    START=$((i * CHUNK_SIZE + 1))
    END=$((i * CHUNK_SIZE + CHUNK_SIZE)); [[ $END -gt $TOTAL_N ]] && END=$TOTAL_N
    N=$((END - START + 1))
    CHUNK_SUFFIX=""; [[ $NCHUNKS -gt 1 ]] && CHUNK_SUFFIX="-$((i + 1))"
    TITLE_PART=""; [[ $NCHUNKS -gt 1 ]] && TITLE_PART="（第 $((i + 1))/$NCHUNKS 部分，$START-${END}）"
    SLUG="$DATE-memoryhub-$safe_p-$STAMP$CHUNK_SUFFIX"
    FILE="$PAGES_DIR/$SLUG.md"
    NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%S.000Z)"

    # L0 摘要: --llm 时用 AI，否则取组内第一条观察文本前 100 字符
    ABSTRACT=""
    if [[ "$LLM" == 1 ]]; then
      ABSTRACT="$(llm_summary "$p" | head -1 | cut -c1-160)" || ABSTRACT=""
    fi
    [[ -n "$ABSTRACT" ]] || ABSTRACT="$(jq -sr --arg p "$p" 'first(.[] | select(.project==$p)) | .text | .[0:100]' "$IN")"

    {
      echo "---"
      echo "type: $PAGE_TYPE"
      echo "title: '$(yaml_sq "$DATE memory-hub 蒸馏: $p$TITLE_PART")'"
      echo "created: '$NOW_ISO'"
      echo "updated: '$NOW_ISO'"
      echo "abstract: '$(yaml_sq "$ABSTRACT")'"
      echo "tags:"
      echo "  - memoryhub"
      echo "  - $p"
      echo "sources:"
      echo "  - codex://sessions"
      echo "confidence: medium"
      echo "contested: false"
      echo "status: fresh"
      echo "last_verified: '$(date +%Y-%m-%d)'"
      echo "---"
      echo ""
      echo "# $DATE memory-hub 蒸馏: $p$TITLE_PART"
      echo ""
      echo "> ⚠️ 本页由 memory-hub 从 Codex 会话自动蒸馏生成，未经人工复核；AI 摘要标记 ⚠️待核实。"
      echo ""
      echo "- 本页观察: $N 条（共 ${TOTAL_N}）| 类型分布: $TYPES | 角色分布: $ROLE_N"
      echo ""
      echo "## 概述 (L1)"
      echo ""
      if [[ "$LLM" == 1 && $i -eq 0 ]]; then
        SUMMARY="$(llm_summary "$p")" || SUMMARY=""
        if [[ -n "$SUMMARY" ]]; then
          echo "⚠️ 以下为 AI 生成摘要，待核实："
          echo "$SUMMARY"
          echo ""
        else
          echo "（--llm 摘要不可用，降级为统计信息）"
          echo ""
        fi
      else
        echo "（本组共 ${N} 条观察，类型分布: ${TYPES}）"
        echo ""
      fi
      echo "## 观察明细 (L2)"
      echo ""
      jq -r --arg p "$p" '
        select(.project==$p)
        | (if .role=="user" then "用户：" elif .role=="tool" then "工具调用：" elif .role=="assistant" then "助手：" else "" end) as $prefix
        | ("- \($prefix)\(.text // "") [\(.type) \(.id)]") | gsub("\n"; " ")' "$IN" \
        | sed -n "${START},${END}p"
      echo ""
      echo "---"
      LINKS="[[index]] · [[log]]"
      [[ -f "$HOME/llm-wiki/moc/MOC.md" ]] && LINKS="$LINKS · [[moc/MOC]]"
      for s in $ALL_SLUGS; do
        [[ "$s" == "$SLUG" ]] && continue
        LINKS="$LINKS · [[$s]]"
      done
      echo "关联: $LINKS"
    } > "$FILE"
    PAGES=$((PAGES + 1))
    echo "distill: $FILE (第 $START-$END 条 / 共 $TOTAL_N)"
  done
done

echo "distill: 完成，共 $PAGES 页"
