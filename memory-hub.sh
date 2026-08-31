#!/usr/bin/env bash
# memctl 主入口 (All-in-One Agent 记忆 CLI)
# 子命令: capture | distill | scope-backfill | publish | search | inject | status | watch | run | help
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HUB_DIR"

usage() {
  echo "用法: memory-hub.sh <命令> [参数]"
  echo "  capture             采集: 解析 Codex 会话 JSONL → staging/（--all 全量 / --since <ms> / --source claude-mem）"
  echo "  embed [index|search] 语义/向量检索（fastembed 本地模型，增量索引 + 余弦相似度）"
echo "  distill [--llm]     蒸馏: → staging/pages/（L0摘要/L1概述/L2明细，免费模型摘要可选）"
  echo "  scope-backfill [--apply] [--limit N] [--cursor C] [--json]  确定性 scope 回填（默认 dry-run）"
  echo "  publish [--apply]   发布: → ~/llm-wiki 按 type 映射目录 + index/log（默认 dry-run）"
  echo "  search \"词\" [--top N] [--raw] [--all] [--gbrain]  检索 ~/llm-wiki"
  echo "  index [--with-raw]    索引 ~/llm-wiki → SQLite FTS5（trigram 中文分词）"
 echo "  ask \"问题\" [--top N]   知识库问答（FTS 检索 + 免费模型生成）"
  echo "  export [--project X] [--type Y] [--format jsonl|json|markdown] 结构化导出知识库"
 echo "  inject [--apply --file X]  记忆上下文注入（默认输出 stdout）"
 echo "  eval [--top N]       自评测基准(F3): 跑 evaluation/golden.jsonl 算 hit@N/MRR → reports/eval-<date>.md"
  echo "  archive [--keep N] [--apply]  归档已消费的 observation 文件(F4): → staging/archive/（可恢复）"
 echo "  status              健康检查/统计"
  echo "  verify              静态漂移校验（toml/hook/MCP/DB，CI 用）"
  echo "  metrics             输出 Prometheus 文本指标"
  echo "  serve [--port N]    启动 REST 查询服务（/search /ask /status /metrics）"
  echo "  watch               定时采集循环（每 60 秒）"
  echo "  run [--apply] [--llm]  一键全链路 capture→distill→publish（默认 dry-run）"
}

CMD="${1:-}"
shift || true

if [[ -z "$CMD" || "$CMD" == "help" || "$CMD" == "-h" || "$CMD" == "--help" ]]; then
  usage
  if [[ -z "$CMD" ]]; then
    exit 1
  else
    exit 0
  fi
fi

case "$CMD" in
  capture) exec "$HUB_DIR/scripts/capture.sh" "$@" ;;
  distill) exec "$HUB_DIR/scripts/distill.sh" "$@" ;;
  scope-backfill) exec python3 "$HUB_DIR/scripts/automation_cli.py" scope-backfill "$@" ;;
  embed) exec python3 "$HUB_DIR/scripts/embed.py" "$@" ;;
  publish) exec "$HUB_DIR/scripts/publish.sh" "$@" ;;
  search) exec "$HUB_DIR/scripts/search.sh" "$@" ;;
  index) exec "$HUB_DIR/scripts/index.sh" "$@" ;;
 ask) exec "$HUB_DIR/scripts/ask.sh" "$@" ;;
 export) exec python3 "$HUB_DIR/scripts/export.py" "$@" ;;
 inject) exec "$HUB_DIR/scripts/inject.sh" "$@" ;;
 eval) exec python3 "$HUB_DIR/scripts/eval.py" "$@" ;;
  archive) exec "$HUB_DIR/scripts/archive.sh" "$@" ;;
 status) exec "$HUB_DIR/scripts/status.sh" "$@" ;;
  verify) exec "$HUB_DIR/scripts/verify.sh" "$@" ;;
  metrics) exec "$HUB_DIR/scripts/metrics.sh" "$@" ;;
  serve) exec python3 "$HUB_DIR/scripts/server.py" "$@" ;;
  watch)
    echo "== memory-hub watch: 每 60 秒增量采集 + 蒸馏 (Ctrl-C 退出) =="
    while true; do
      "$HUB_DIR/scripts/capture.sh"
      LATEST="$(ls -t "$HUB_DIR"/staging/observations-*.jsonl 2>/dev/null | head -1 || true)"
      if [[ -n "${LATEST:-}" ]]; then
        "$HUB_DIR/scripts/distill.sh" "$LATEST" >/dev/null 2>&1 || true
      fi
      echo "== $(date '+%H:%M:%S') 等待下一轮 =="
      sleep 60
    done
    ;;
  maintain)
    WIKI_DIR="${WIKI_PATH:-$HOME/llm-wiki}"
    echo "== memory-hub maintain: 死链修复 -> 时间戳回填 -> 双链补全 -> lint 摘要 =="
    cd "$WIKI_DIR"
    python3 "$WIKI_DIR/.scripts/fix_deadlinks.py" --apply
    python3 "$WIKI_DIR/.scripts/backfill_timestamps.py" --apply
    python3 "$WIKI_DIR/.scripts/backfill_links.py" --apply
    python3 "$WIKI_DIR/.scripts/lint_triage.py" | tail -20
    printf '%s\n' "- $(date '+%Y-%m-%d %H:%M') memory-hub maintain 完成" >> "$WIKI_DIR/log.md"
    cd "$WIKI_DIR"
    MODIFIED_FILES="$(git diff --name-only || true)"
    if [[ -n "$MODIFIED_FILES" ]]; then
      while IFS= read -r f; do
        [[ -n "$f" ]] && git add -- "$f"
      done <<< "$MODIFIED_FILES"
    fi
    git add -- "$WIKI_DIR/log.md" "$WIKI_DIR/index.md" 2>/dev/null || true
    git commit -m "chore(wiki): memory-hub maintain" 2>/dev/null || true
    echo "== memory-hub maintain: 完成 =="
    ;;
  run)
    APPLY=0
    LLM=0
    COMMIT=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --apply) APPLY=1 ;;
        --llm) LLM=1 ;;
        --commit) COMMIT=1 ;;
        --help|-h) usage ;;
        *) echo "未知参数: $1" >&2; usage ;;
      esac
      shift
    done
    PUBLISH_ARGS=""
    if [[ "$APPLY" == 1 ]]; then
      PUBLISH_ARGS="--apply"
      if [[ "$COMMIT" == 1 ]]; then
        PUBLISH_ARGS="$PUBLISH_ARGS --commit"
      fi
    fi
    echo "== memory-hub: capture -> distill -> publish $( [[ $APPLY == 1 ]] && echo '(apply)' || echo '(dry-run)' ) =="
    "$HUB_DIR/scripts/capture.sh"
    if [[ "$LLM" == 1 ]]; then
      "$HUB_DIR/scripts/distill.sh" --llm
    else
      "$HUB_DIR/scripts/distill.sh"
    fi
    # 先把存量页向量化（autolink 的 L2 语义匹配依赖 vec 表）
    python3 "$HUB_DIR/scripts/embed.py" index 2>&1 | tail -1
    # 自动双链：蒸馏页在发布前补足有效出链（L1 tags/title 规则 + L2 embed 语义，缺一则降级）
    python3 "$HUB_DIR/scripts/autolink.py" --apply
   "$HUB_DIR/scripts/publish.sh" $PUBLISH_ARGS
    # F4: publish 成功后归档已消费的观察文件（仅日期命名，绝不碰 realtime/test）
    if [[ "$APPLY" == 1 ]]; then
      "$HUB_DIR/scripts/archive.sh" --apply >/dev/null 2>&1 || true
    else
      "$HUB_DIR/scripts/archive.sh" >/dev/null 2>&1 || true
    fi
   echo "== memory-hub: 完成 =="
    ;;
  *) echo "未知命令: $CMD" >&2; usage; exit 2 ;;
esac
