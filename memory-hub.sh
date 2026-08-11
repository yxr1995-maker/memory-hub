#!/usr/bin/env bash
# memctl 主入口 (All-in-One Agent 记忆 CLI)
# 子命令: capture | distill | publish | search | inject | status | watch | run | help
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HUB_DIR"

usage() {
  echo "用法: memory-hub.sh <命令> [参数]"
  echo "  capture             采集: 解析 Codex 会话 JSONL → staging/（--all 全量 / --since <ms> / --source claude-mem）"
  echo "  distill [--llm]     蒸馏: → staging/pages/（L0摘要/L1概述/L2明细，免费模型摘要可选）"
  echo "  publish [--apply]   发布: → ~/llm-wiki 按 type 映射目录 + index/log（默认 dry-run）"
  echo "  search \"词\" [--top N] [--raw] [--all] [--gbrain]  检索 ~/llm-wiki"
  echo "  index [--with-raw]    索引 ~/llm-wiki → SQLite FTS5（trigram 中文分词）"
  echo "  ask \"问题\" [--top N]   知识库问答（FTS 检索 + 免费模型生成）"
  echo "  inject [--apply --file X]  记忆上下文注入（默认输出 stdout）"
  echo "  status              健康检查/统计"
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
  publish) exec "$HUB_DIR/scripts/publish.sh" "$@" ;;
  search) exec "$HUB_DIR/scripts/search.sh" "$@" ;;
  index) exec "$HUB_DIR/scripts/index.sh" "$@" ;;
  ask) exec "$HUB_DIR/scripts/ask.sh" "$@" ;;
  inject) exec "$HUB_DIR/scripts/inject.sh" "$@" ;;
  status) exec "$HUB_DIR/scripts/status.sh" "$@" ;;
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
  run)
    APPLY=0
    LLM=0
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --apply) APPLY=1 ;;
        --llm) LLM=1 ;;
        --help|-h) usage ;;
        *) echo "未知参数: $1" >&2; usage ;;
      esac
      shift
    done
    echo "== memory-hub: capture -> distill -> publish $( [[ $APPLY == 1 ]] && echo '(apply)' || echo '(dry-run)' ) =="
    "$HUB_DIR/scripts/capture.sh"
    if [[ "$LLM" == 1 ]]; then
      "$HUB_DIR/scripts/distill.sh" --llm
    else
      "$HUB_DIR/scripts/distill.sh"
    fi
    if [[ "$APPLY" == 1 ]]; then
      "$HUB_DIR/scripts/publish.sh" --apply
    else
      "$HUB_DIR/scripts/publish.sh"
    fi
    echo "== memory-hub: 完成 =="
    ;;
  *) echo "未知命令: $CMD" >&2; usage; exit 2 ;;
esac
