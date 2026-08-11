#!/bin/bash
# memory-hub REST API 断言脚本
# 用法: bash ui/test-api.sh [port]    默认 8899
set -euo pipefail

PORT="${1:-8899}"
HOST="127.0.0.1:${PORT}"
BASE="http://${HOST}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

PASS_COUNT=0
FAIL_COUNT=0
PID=""
OWNED_SERVER=0

cleanup() {
    if [ "${OWNED_SERVER}" = "1" ] && [ -n "${PID}" ]; then
        kill "${PID}" 2>/dev/null || true
        wait "${PID}" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

cd "${REPO_ROOT}"

# ---- 等待服务就绪（复用或自启） ----
wait_for_ready() {
    local tries=0
    while [ "${tries}" -lt 10 ]; do
        if curl -sS -o /dev/null "${BASE}/health" 2>/dev/null; then
            return 0
        fi
        tries=$((tries + 1))
        sleep 1
    done
    return 1
}

if ! wait_for_ready; then
    echo "[info] ${HOST}/health 未就绪，尝试自起服务..." >&2
    python3 "${REPO_ROOT}/scripts/server.py" --port "${PORT}" \
        > /tmp/memory-hub-test-server.log 2>&1 &
    PID=$!
    OWNED_SERVER=1
    sleep 2
    if ! wait_for_ready; then
        echo "FATAL: 服务在 ${HOST} 不可达，中止。" >&2
        cat /tmp/memory-hub-test-server.log >&2 || true
        exit 1
    fi
fi

# ---- 断言辅助（全局变量：_STATUS = HTTP 状态码，_BODY = 响应正文） ----

assert_eq() {
    local got="$1" want="$2" label="$3"
    if [ "${got}" = "${want}" ]; then
        echo "PASS  ${label}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo "FAIL  ${label}  (got='${got}', want='${want}')"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

assert_json() {
    local body="$1" label="$2"
    if python3 -c "import json,sys; json.loads(sys.stdin.read())" <<< "${body}" 2>/dev/null; then
        echo "PASS  ${label}"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo "FAIL  ${label}  (非合法 JSON)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# 把结果写入全局 _STATUS / _BODY（避免 $() 子 shell 丢失变量）
_fetch() {
    _STATUS=$(curl -sS -o /tmp/_mh_body -w "%{http_code}" -X "$1" "$2" 2>/dev/null || echo "000")
    _BODY=$(cat /tmp/_mh_body)
}

_post() {
    _STATUS=$(curl -sS -o /tmp/_mh_body -w "%{http_code}" \
        -X POST -H "Content-Type: application/json" -d @"$2" "$1" \
        2>/dev/null || echo "000")
    _BODY=$(cat /tmp/_mh_body)
}

echo "=== memory-hub API 断言 (port=${PORT}) ==="
echo ""

# ---------- 1. health ----------
echo "--- 1/10 health ---"
_fetch GET "${BASE}/health"
assert_eq "${_STATUS}" "200" "health 返回 200"
_health_status=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('status',''))" <<< "${_BODY}" 2>/dev/null)
assert_eq "${_health_status}" "ok" "health.status == ok"

# ---------- 2. overview ----------
echo "--- 2/10 overview ---"
_fetch GET "${BASE}/api/overview"
assert_json "${_BODY}" "overview 是合法 JSON"
_wp=$(python3 -c "
import json,sys
v=json.loads(sys.stdin.read()).get('wiki_pages','')
print(v if isinstance(v,(int,float)) else 'NOT_NUM')
" <<< "${_BODY}" 2>/dev/null)
case "${_wp}" in
    NOT_NUM|'' ) echo "FAIL  overview.wiki_pages 是数字 (got='${_wp}')"; FAIL_COUNT=$((FAIL_COUNT+1)) ;;
    * )          echo "PASS  overview.wiki_pages 是数字 (${_wp})";         PASS_COUNT=$((PASS_COUNT+1)) ;;
esac

# ---------- 3. pages limit=3 ----------
echo "--- 3/10 pages limit=3 ---"
_fetch GET "${BASE}/api/pages?limit=3"
assert_json "${_BODY}" "pages limit=3 是合法 JSON"
_info=$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
items=d.get('items',[])
print(len(items))
print('HAS_PATH' if items and 'path' in items[0] else 'NO_PATH')
" <<< "${_BODY}" 2>/dev/null)
_len=$(echo "${_info}" | sed -n '1p')
_has_path=$(echo "${_info}" | sed -n '2p')
assert_eq "${_len}" "3" "pages limit=3 返回 3 条"
assert_eq "${_has_path}" "HAS_PATH" "pages items[0] 含 path 字段"

# ---------- 4. pages 过滤 ----------
echo "--- 4/10 pages?type=concept&q=memory ---"
_fetch GET "${BASE}/api/pages?type=concept&q=memory"
assert_json "${_BODY}" "pages 过滤返回合法 JSON"
_total=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('total',''))" <<< "${_BODY}" 2>/dev/null)
if [ -n "${_total}" ] && [ "${_total}" -ge 0 ] 2>/dev/null; then
    echo "PASS  pages 过滤 total≥0 (${_total})"
    PASS_COUNT=$((PASS_COUNT + 1))
else
    echo "FAIL  pages 过滤 total≥0 (got='${_total}')"
    FAIL_COUNT=$((FAIL_COUNT + 1))
fi

# ---------- 5. tags ----------
echo "--- 5/10 tags ---"
_fetch GET "${BASE}/api/tags"
assert_json "${_BODY}" "tags 是合法 JSON"
_is_list=$(python3 -c "import json,sys; print('IS_LIST' if isinstance(json.loads(sys.stdin.read()).get('tags'), list) else 'NOT')" <<< "${_BODY}" 2>/dev/null)
assert_eq "${_is_list}" "IS_LIST" "tags.tags 是数组"

# ---------- 6. page GET 真实页面 ----------
echo "--- 6/10 page GET ---"
_fetch GET "${BASE}/api/pages?limit=1"
assert_eq "${_STATUS}" "200" "pages limit=1 返回 200"
_real_path=$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
items=d.get('items',[])
print(items[0]['path'] if items else '')
" <<< "${_BODY}" 2>/dev/null)
if [ -z "${_real_path}" ]; then
    echo "FAIL  取到真实页面 path (empty)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
else
    echo "PASS  取到真实页面 path=${_real_path}"
    PASS_COUNT=$((PASS_COUNT + 1))
fi

if [ -n "${_real_path}" ]; then
    _fetch GET "${BASE}/api/page?path=${_real_path}"
    assert_json "${_BODY}" "page GET 是合法 JSON"
    _fields=$(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
print('OK' if ('path' in d and 'content' in d) else 'MISSING')
" <<< "${_BODY}" 2>/dev/null)
    assert_eq "${_fields}" "OK" "page GET 含 path/content"
fi

# ---------- 7. observations ----------
echo "--- 7/10 observations limit=2 ---"
_fetch GET "${BASE}/api/observations?limit=2"
assert_json "${_BODY}" "observations 是合法 JSON"
_obs_list=$(python3 -c "import json,sys; print('IS_LIST' if isinstance(json.loads(sys.stdin.read()).get('items'), list) else 'NOT')" <<< "${_BODY}" 2>/dev/null)
assert_eq "${_obs_list}" "IS_LIST" "observations.items 是数组"

# ---------- 8. POST → GET → DELETE → GET 404 ----------
echo "--- 8/10 page 写-读-删-404 ---"

_test_path="drafts/ui-api-test-auto.md"
_tmp_content=$(mktemp)
cat > "${_tmp_content}" <<'EOF'
---
title: auto-test
type: draft
---
# auto-test

This page was created and destroyed by ui/test-api.sh.
EOF

_post_file=$(mktemp)
python3 -c "
import json,sys
with open(sys.argv[1], encoding='utf-8') as f:
    content = f.read()
print(json.dumps({'path': sys.argv[2], 'content': content}))
" "${_tmp_content}" "${_test_path}" > "${_post_file}"
rm -f "${_tmp_content}"

_post "${BASE}/api/page" "${_post_file}"
assert_json "${_BODY}" "POST 返回合法 JSON"
_post_ok=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('ok',False))" <<< "${_BODY}" 2>/dev/null)
assert_eq "${_post_ok}" "True" "POST 写入成功 (status=${_STATUS})"

_fetch GET "${BASE}/api/page?path=${_test_path}"
_title_back=$(python3 -c "import json,sys; d=json.loads(sys.stdin.read()); print(d.get('meta',{}).get('title',''))" <<< "${_BODY}" 2>/dev/null)
assert_eq "${_title_back}" "auto-test" "POST 后 GET 读回 title 一致 (got='${_title_back}')"

_STATUS=$(curl -sS -o /tmp/_mh_body -w "%{http_code}" \
    -X DELETE "${BASE}/api/page?path=${_test_path}" 2>/dev/null || echo "000")
_BODY=$(cat /tmp/_mh_body)
assert_json "${_BODY}" "DELETE 返回合法 JSON"
_del_ok=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read()).get('ok',False))" <<< "${_BODY}" 2>/dev/null)
assert_eq "${_del_ok}" "True" "DELETE 成功 (status=${_STATUS})"

rm -f "${_post_file}"

_fetch GET "${BASE}/api/page?path=${_test_path}"
assert_eq "${_STATUS}" "404" "DELETE 后 GET 返回 404"

# ---------- 9. 路径穿越 ----------
echo "--- 9/10 路径穿越保护 ---"
_fetch GET "${BASE}/api/page?path=../../etc/passwd"
case "${_STATUS}" in
    2*) echo "FAIL  路径穿越必须非 2xx (status=${_STATUS})"; FAIL_COUNT=$((FAIL_COUNT+1)) ;;
    *)  echo "PASS  路径穿越 GET 非 2xx (status=${_STATUS})";   PASS_COUNT=$((PASS_COUNT+1)) ;;
esac

# ---------- 10. POST 非 .md ----------
echo "--- 10/10 POST 非 .md ---"
_bad_file=$(mktemp)
printf '{"path":"config.json","content":"not a markdown"}' > "${_bad_file}"
_post "${BASE}/api/page" "${_bad_file}"
rm -f "${_bad_file}"
assert_eq "${_STATUS}" "400" "POST 非 .md 返回 400"

# ---------- 汇总 ----------
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo ""
echo "=== 汇总: ${PASS_COUNT}/${TOTAL} passed ==="
if [ "${FAIL_COUNT}" -gt 0 ]; then
    exit 1
fi
exit 0
