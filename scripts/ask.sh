#!/usr/bin/env bash
# memory-hub ask: 基于 llm-wiki 知识库的问答（FTS5 检索 + 免费模型生成回答）
# 替代 gbrain ask/query 的问答能力；检索用 SQLite FTS5（trigram），生成用本地免费模型
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${MEMORY_HUB_DATA:-$HOME/.memory-hub}/index.db"
MODEL="${CLAUDE_MEM_MODEL:-sensenova/sensenova-6.8-flash-lite}"
PROXY="${OPENCODEX_URL:-http://127.0.0.1:10100/v1}"
TOP=5
EXPAND=0
FUSE=0
EXPAND_MODEL="${CLAUDE_MEM_EXPAND_MODEL:-volcengine-coding-plan/ark-code-latest}"

usage() {
  echo "用法: ask.sh \"问题\" [--top N] [--expand] [--fuse]"
  echo "  检索相关页面 + 免费模型生成回答（引用来源页面）"
  echo "  --top N  检索页数（默认 5）"
  echo "  --expand 检索前先做 LLM 查询扩展（语义检索：生成相关关键词）"
  echo "  --fuse   使用 fuse.py 进行融合检索（替代独立 FTS5 检索）"
  exit 0
}

Q=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --top) TOP="$2"; shift ;;
    --expand) EXPAND=1 ;;
    --fuse) FUSE=1 ;;
    --help|-h) usage ;;
    -*) echo "未知参数: $1" >&2; usage ;;
    *) Q="$1" ;;
  esac
  shift
done

[[ -n "$Q" ]] || { echo "用法: ask.sh \"问题\"" >&2; exit 1; }
[[ -f "$DB" ]] || { echo "错误: 索引不存在（先运行: memory-hub.sh index）: $DB" >&2; exit 1; }

# —— LLM 查询扩展（语义检索近似）——
if [[ "$EXPAND" == 1 ]]; then
  EXP_PROMPT="把「${Q}」扩展成6个相关检索关键词（中英文同义词、相关概念、近义表述），只输出关键词，空格分隔，不要解释"
  EXP_PAYLOAD="$(jq -n --arg model "$EXPAND_MODEL" --arg content "$EXP_PROMPT" \
    '{model:$model, messages:[{role:"user", content:$content}], temperature:0.3}')"
  EXPANDED="$(curl -s --max-time 25 -H 'Content-Type: application/json' -d "$EXP_PAYLOAD" "$PROXY/chat/completions" 2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null | tr '\n' ' ')"
  if [[ -n "$EXPANDED" ]]; then
    echo "== 查询扩展: $Q → $EXPANDED =="
    Q="$EXPANDED"
  fi
fi

# —— 使用 fuse.py 融合检索 ——
RESULTS=""
if [[ "$FUSE" == 1 ]]; then
  FUSE_RESULTS="$(python3 "$HUB_DIR/scripts/fuse.py" "$Q" --top "$TOP" 2>&1 || true)"
  if echo "$FUSE_RESULTS" | grep -q "^\[.*\] .*\.md$"; then
    # fuse.py 输出格式：[score] path.md
    # 提取 path.md 并通过 sqlite3 获取完整信息
    PATHS="$(echo "$FUSE_RESULTS" | grep "^\[.*\] .*\.md$" | sed 's/^\[.*\] //')"
    if [[ -n "$PATHS" ]]; then
      # 构建 IN 子句查询
      IN_CLAUSE="$(echo "$PATHS" | sed 's/.*/'\''&'\''/' | paste -sd, -)"
      RESULTS="$(sqlite3 -separator '|~|' "$DB" "
        SELECT path, title, replace(abstract, char(10), ' '), replace(substr(content, 1, 800), char(10), ' '), bm25(pages)
        FROM pages WHERE path IN ($IN_CLAUSE)
        ORDER BY bm25(pages) LIMIT $TOP;" 2>/dev/null || true)"
    fi
  fi
fi

# 查询词拆分：trigram 需要 3+ 字符的词；短词忽略（自动降级为可用词 OR）
if [[ -z "$RESULTS" ]]; then
  TERMS=""
  for w in $Q; do
    for w2 in ${w//-/ }; do
      W_LEN="$(printf '%s' "$w2" | wc -m | tr -d ' ')"
      if [[ "$W_LEN" -ge 3 ]]; then
        # FTS5 注入防护：元字符 *|&~()- 替换为空格，" 转义为 ""（phrase 内），合并多余空格
        W_S="$(printf '%s' "$w2" | sed 's/[*|&~()]/ /g; s/"/""/g; s/  */ /g; s/^ //; s/ $//')"
        [[ -n "$W_S" ]] && TERMS="${TERMS:+$TERMS OR }\"$W_S\""
      fi
    done
  done
  [[ -n "$TERMS" ]] || { echo "错误: 问题中无 ≥3 字符的词（trigram 分词要求）" >&2; exit 1; }

  # FTS5 检索：标题/摘要/正文综合，bm25 排名
  # 用自定义分隔符 |~| 避免内容里的 | 冲突；取正文前 800 字符做上下文
  RESULTS="$(sqlite3 -separator '|~|' "$DB" "
    SELECT path, title, replace(abstract, char(10), ' '), replace(substr(content, 1, 800), char(10), ' '), bm25(pages)
    FROM pages WHERE pages MATCH '$TERMS'
    ORDER BY bm25(pages) LIMIT $TOP;" 2>/dev/null || true)"
fi

[[ -n "$RESULTS" ]] || { echo "无命中相关页面"; exit 0; }

# 构建上下文（前 3 页的标题+摘要+正文片段）
CONTEXT="$(printf '%s\n' "$RESULTS" | awk -F'[|][~][|]' 'NR<=3 {printf "页面 %s（%s）\n摘要: %s\n正文: %s\n\n", $1, $2, $3, $4}')"

PAYLOAD="$(jq -n --arg model "$MODEL" \
  --arg sys "你是知识库问答助手。基于给定页面内容回答问题，只基于页面内容，不要编造。回答后标注来源页面（用 path）。简洁中文。" \
  --arg user "问题: $Q\n\n相关页面:\n$CONTEXT" \
  '{model:$model, messages:[{role:"system",content:$sys},{role:"user",content:$user}], temperature:0.2}')"

ANSWER="$(curl -s --max-time 60 -H 'Content-Type: application/json' -d "$PAYLOAD" "$PROXY/chat/completions" 2>/dev/null \
  | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)"

if [[ -n "$ANSWER" ]]; then
  echo "== 回答 ($MODEL) =="
  printf '%s\n' "$ANSWER"
  echo ""
fi
echo "== 引用页面 =="
printf '%s\n' "$RESULTS" | awk -F'[|][~][|]' '{if (NF >= 2 && $2 != "") printf "  - %s — %s\n", $1, $2; else if ($1 != "") printf "  - %s\n", $1}'
