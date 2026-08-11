#!/usr/bin/env bash
# memory-hub UI 开发/预览一键启动：REST(8787) + harness 静态服务(8902)
# 幂等：端口已在监听则跳过。用法: bash ui/dev.sh
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

up() {  # up <port> <cmd...>
  local port="$1"; shift
  if curl -s -m 2 "http://127.0.0.1:${port}/" >/dev/null 2>&1 \
     || lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✓ :${port} 已在运行"
  else
    nohup "$@" >/tmp/memory-hub-dev-"${port}".log 2>&1 &
    echo "▲ :${port} 已启动（日志 /tmp/memory-hub-dev-${port}.log）"
  fi
}

up 8787 python3 "${HUB_DIR}/scripts/server.py" --port 8787
up 8902 python3 -m http.server 8902 --bind 127.0.0.1 --directory "${HUB_DIR}/ui"

sleep 1
curl -s -m 3 http://127.0.0.1:8787/health >/dev/null && echo "✓ REST 健康: http://127.0.0.1:8787"
curl -s -m 3 -o /dev/null http://127.0.0.1:8902/dev-harness.html \
  && echo "✓ 面板预览: http://127.0.0.1:8902/dev-harness.html"
