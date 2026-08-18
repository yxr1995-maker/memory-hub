#!/usr/bin/env bash
# memory-hub search: 内置检索 ~/llm-wiki（FTS5 索引优先，rg 关键词回退，--gbrain 可选混合）
set -euo pipefail

WIKI="${WIKI_PATH:-$HOME/llm-wiki}"
HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${MEMORY_HUB_DATA:-$HOME/.memory-hub}/index.db"
TOP=10
RAW=0
ALL=0
GBRAIN=0
NO_FTS=0
NOFALLBACK=0
EXPAND=0
FUSE=0
MODEL="${CLAUDE_MEM_MODEL:-volcengine-coding-plan/ark-code-latest}"
PROXY="${OPENCODEX_URL:-http://127.0.0.1:10100/v1}"

usage() {
  echo "用法: search.sh \"关键词\" [--top N] [--raw] [--all] [--gbrain] [--no-fts] [--expand]"
  echo "  --top N    结果数上限（默认 10）"
  echo "  --raw      包含 raw/ 目录"
  echo "  --all      包含 _legacy-para/ 目录"
  echo "  --gbrain   优先用 gbrain 混合检索，失败回退 rg"
  echo "  --no-fts   跳过 FTS5 索引，直接用 rg"
 echo "  --fuse     使用 fuse.py 融合检索（FTS5 bm25 + 向量，RRF k=60）"
  echo "  --no-fallback  内部用：FTS 无命中时不回退 rg（fuse 调用 search.sh 用）"
  echo "  --tau N    与 --fuse 合用: 时间衰减常数(天,默认 90; --tau 0 关闭, 新页优先)"
 echo "  --expand   LLM 查询扩展（语义检索：生成相关关键词后 FTS5 检索）"
  exit 0
}

Q=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --top) TOP="$2"; shift ;;
    --raw) RAW=1 ;;
    --all) ALL=1 ;;
    --gbrain) GBRAIN=1 ;;
    --no-fts) NO_FTS=1 ;;
    --no-fallback) NOFALLBACK=1 ;;
    --expand) EXPAND=1 ;;
   --fuse) FUSE=1 ;;
    --tau) TAU="$2"; shift ;;
   --help|-h) usage ;;
    -*) echo "未知参数: $1" >&2; usage ;;
    *) Q="$1" ;;
  esac
  shift
done

[[ -n "$Q" ]] || { echo "用法: search.sh \"关键词\"" >&2; exit 1; }
[[ -d "$WIKI" ]] || { echo "错误: 知识库不存在: $WIKI" >&2; exit 1; }

# —— --fuse: 委托 fuse.py 做 FTS5 bm25 + 向量 RRF 融合检索 ——
if [[ "$FUSE" == 1 ]]; then
  exec python3 "$HUB_DIR/scripts/fuse.py" "$Q" --top "$TOP" --tau "${TAU:-90}"
fi

# —— LLM 查询扩展（语义检索近似：生成相关关键词）——
if [[ "$EXPAND" == 1 ]]; then
  EXP_PROMPT="把「${Q}」扩展成6个相关检索关键词（中英文同义词、相关概念、近义表述），只输出关键词，空格分隔，不要解释"
  EXP_PAYLOAD="$(jq -n --arg model "$MODEL" --arg content "$EXP_PROMPT" \
    '{model:$model, messages:[{role:"user", content:$content}], temperature:0.3}')"
  EXPANDED="$(curl -s --max-time 25 -H 'Content-Type: application/json' -d "$EXP_PAYLOAD" "$PROXY/chat/completions" 2>/dev/null | jq -r '.choices[0].message.content // empty' 2>/dev/null | tr '\n' ' ')"
  if [[ -n "$EXPANDED" ]]; then
    echo "== 查询扩展: $Q → $EXPANDED =="
    Q="$EXPANDED"
  else
    echo "search: 查询扩展失败，用原关键词"
  fi
fi

# —— 可选 gbrain 混合检索 ——
if [[ "$GBRAIN" == 1 ]]; then
  if PATH="$HOME/.bun/bin:$PATH" command -v gbrain >/dev/null 2>&1; then
    if GOUT="$(PATH="$HOME/.bun/bin:$PATH" gbrain query "$Q" 2>/dev/null)"; then
      echo "== gbrain 混合检索: $Q =="
      printf '%s\n' "$GOUT" | head -8 | sed 's/^/  /'
      exit 0
    else
      echo "search: gbrain 调用失败，回退 rg 关键词检索"
    fi
  else
    echo "search: gbrain 未安装，回退 rg 关键词检索"
  fi
fi

# —— FTS5 索引检索（trigram 分词，支持中文子串）——
if [[ "$NO_FTS" == 0 && -f "$DB" ]]; then
  # 查询词拆分：trigram 需要 3+ 字符的词；短词（<3 字符）单独忽略或走 rg
  # 中文分词适配：trigram 索引按 3 字符滑窗建词，查询整句作单词无法命中。
  # 对每个 >=3 字符的词产出 3-gram 滑窗子串，OR 拼接让 trigram 索引真正生效。
  ngram() {
    local s="$1" out="" i
    for ((i = 0; i < ${#s} - 2; i++)); do
      out="${out:+$out OR }${s:i:3}"
    done
    printf '%s' "$out"
  }
  TERMS=""
  for w in $Q; do
    for w2 in ${w//-/ }; do
      W_LEN="$(printf '%s' "$w2" | wc -m | tr -d ' ')"
      if [[ "$W_LEN" -ge 3 ]]; then
        # FTS5 注入防护：元字符 *|&~()- 替换为空格
        W_S="$(printf '%s' "$w2" | sed 's/[*|&~()]/ /g; s/"/""/g; s/  */ /g; s/^ //; s/ $//')"
        if [[ -n "$W_S" ]]; then
          G="$(ngram "$W_S")"
          [[ -n "$G" ]] && TERMS="${TERMS:+$TERMS OR }$G"
        fi
      fi
    done
  done
  if [[ -n "$TERMS" ]]; then
    HITS="$(sqlite3 "$DB" "SELECT path, bm25(pages) FROM pages WHERE pages MATCH '$TERMS' ORDER BY bm25(pages) LIMIT $TOP;" 2>/dev/null || true)"
    if [[ -n "$HITS" ]]; then
      echo "== FTS5 检索 ($DB): $Q =="
      printf '%s\n' "$HITS" | awk -F'|' '{printf "[%.1f] %s\n", $2, $1}'
      exit 0
    else
      if [[ "$NOFALLBACK" == 1 ]]; then
        exit 0
      fi
      echo "search: FTS5 无命中，回退 rg"
    fi
  fi
fi

# —— rg 关键词检索 ——
# rg -g 排除 glob 需 **/ 前缀匹配任意深度目录（!raw/** 格式错误，raw/ 从未被真正排除）
EXCLUDES=(-g '!**/raw/**' -g '!**/_legacy-para/**')
[[ "$RAW" == 1 ]] && EXCLUDES=(-g '!**/_legacy-para/**')
[[ "$ALL" == 1 ]] && EXCLUDES=()

echo "== 检索 $WIKI: $Q (rg) =="
rg -i -l "$Q" "$WIKI" -g '*.md' "${EXCLUDES[@]+"${EXCLUDES[@]}"}" >/dev/null 2>&1 || { echo "无命中"; exit 0; }

rg -i -c "$Q" "$WIKI" -g '*.md' "${EXCLUDES[@]+"${EXCLUDES[@]}"}" 2>/dev/null \
  | sort -t: -k2 -rn \
  | head -"$TOP" \
  | while IFS=: read -r f c; do
      rel="${f#$WIKI/}"
      echo "[$c 处] $rel"
      Q_RE="$(printf '%s' "$Q" | sed 's/[][\.*^$|()?+{}]/\\&/g')"
      rg -i -m 1 --no-heading -o ".{0,40}$Q_RE.{0,60}" "$f" 2>/dev/null | head -1 | sed 's/^/      …/'
    done
