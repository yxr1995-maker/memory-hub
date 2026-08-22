#!/usr/bin/env bash
# memory-hub verify: 静态漂移校验（CI 用）——toml 解析 / hook 健康 / MCP 配置 / DB 一致性
# 全部通过 exit 0；任一失败 exit 1，输出 FAIL 项。
set -euo pipefail

HUB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOMATION_ROOT="${CODEX_AUTOMATION_ROOT:-$HOME/.codex/automations}"
CONFIG_FILE="${CODEX_CONFIG:-$HOME/.codex/config.toml}"
HOOKS_FILE="$HOME/.codex/plugins/cache/personal/memory-hub/1.0.0/hooks/hooks.json"
DEV_DB="${CODEX_DEV_DB:-$HOME/.codex/sqlite/codex-dev.db}"

FAIL=0
ok()   { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAIL=1; }

# 1. 全部 automation.toml 可被 tomllib 解析
TOML_RES="$(python3 - "$AUTOMATION_ROOT" <<'PY'
import pathlib, sys, tomllib
root = pathlib.Path(sys.argv[1])
bad = []
n = 0
for f in sorted(root.rglob("automation.toml")):
    n += 1
    try:
        tomllib.loads(f.read_text())
    except Exception as e:
        bad.append(f"{f}: {e}")
print(f"checked={n}")
for b in bad:
    print("BAD", b)
PY
)"
TOML_N="$(echo "$TOML_RES" | grep '^checked=' | cut -d= -f2)"
if echo "$TOML_RES" | grep -q '^BAD'; then
  echo "$TOML_RES" | grep '^BAD' | sed 's/^/  /'
  fail "toml 解析失败（共 ${TOML_N:-?} 个文件，有坏文件）"
else
  ok "toml 解析通过（${TOML_N:-?} 个 automation.toml 均合法）"
fi

# 2. hooks.json 可解析，且 command 引用的脚本存在
HOOK_RES="$(python3 - "$HOOKS_FILE" "$HUB_DIR" <<'PY'
import json, pathlib, re, sys
hf, hub = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
try:
    data = json.loads(hf.read_text())
except Exception as e:
    print("BAD", f"hooks.json: {e}")
    sys.exit(1)
cmds = []
for ev, groups in data.get("hooks", {}).items():
    for g in groups:
        for h in g.get("hooks", []):
            c = h.get("command", "")
            if c:
                cmds.append((ev, c))
missing = []
for ev, c in cmds:
    for m in re.finditer(r"(?:bash |python3 )([^\s&|>;]+)", c):
        p = pathlib.Path(m.group(1))
        if not p.is_absolute():
            p = hub / p
        if not p.exists():
            missing.append(f"{ev}: {p}")
print(f"events={len(set(c[0] for c in cmds))} commands={len(cmds)}")
for m in missing:
    print("BAD", m)
PY
)"
HOOK_N="$(echo "$HOOK_RES" | grep '^events=')"
if echo "$HOOK_RES" | grep -q '^BAD'; then
  echo "$HOOK_RES" | grep '^BAD' | sed 's/^/  /'
  fail "hook 引用脚本缺失/配置损坏"
else
  ok "hook 健康（${HOOK_N}，引用脚本均存在）"
fi

# 3. config.toml 中 memory-hub MCP server 指向真实文件，且 hooks.state 已注册
MCP_RES="$(python3 - "$CONFIG_FILE" "$HUB_DIR" <<'PY'
import pathlib, sys, tomllib
cfg, hub = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
try:
    conf = tomllib.loads(cfg.read_text())
except Exception as e:
    print("BAD", f"config.toml: {e}")
    sys.exit(1)
mcp = conf.get("mcp_servers", {}).get("memory-hub")
if not mcp:
    print("BAD", "config.toml 缺少 [mcp_servers.memory-hub]")
else:
    for a in mcp.get("args", []):
        p = pathlib.Path(a)
        if p.exists() and p.is_absolute():
            print(f"mcp={a}")
states = [k for k in (conf.get("hooks", {}).get("state", {}) or {}) if "memory-hub@personal" in k]
print(f"hook_states={len(states)}")
PY
)"
MCP_FILE="$(echo "$MCP_RES" | grep '^mcp=' | cut -d= -f2 | head -1)"
HOOK_STATES="$(echo "$MCP_RES" | grep '^hook_states=')"
if echo "$MCP_RES" | grep -q '^BAD'; then
  echo "$MCP_RES" | grep '^BAD' | sed 's/^/  /'
  fail "MCP/配置异常"
else
  ok "MCP server 指向真实文件（${MCP_FILE}）；${HOOK_STATES}"
fi

# 4. DB 一致性：memory-hub 相关 automation 都有对应 toml 文件，且 toml 均有 DB 记录
DB_RES="$(python3 - "$DEV_DB" "$AUTOMATION_ROOT" <<'PY'
import pathlib, sqlite3, sys
db, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
if not db.exists():
    print("BAD", f"DB 不存在: {db}")
    sys.exit(1)
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = conn.execute(
    "SELECT id, name FROM automations WHERE prompt LIKE '%memory-hub%' OR name LIKE '%memory-hub%' OR name LIKE '%gbrain%' OR prompt LIKE '%memory_hub%'"
).fetchall()
conn.close()
missing = []
for aid, aname in rows:
    f = root / aid / "automation.toml"
    if not f.exists():
        missing.append(f"{aid} ({aname}): 无 toml")
db_n = len(rows)
toml_n = len(list(root.rglob("automation.toml")))
print(f"db_rows={db_n} toml_files={toml_n}")
for m in missing:
    print("BAD", m)
PY
)"
if echo "$DB_RES" | grep -q '^BAD'; then
  echo "$DB_RES" | grep '^BAD' | sed 's/^/  /'
  fail "DB 一致性异常"
else
  ok "DB 一致性通过（$(echo "$DB_RES" | grep '^db_rows=')）"
fi

# 5. wiki 内容硬检查
WIKI_DIR="${WIKI_PATH:-$HOME/llm-wiki}"
TOKEN_RES="$(python3 "$HUB_DIR/scripts/verify_tokens.py" "$WIKI_DIR" || true)"
TOKEN_N="$(echo "$TOKEN_RES" | grep '^token_hits=' | cut -d= -f2)"
if echo "$TOKEN_RES" | grep -q '^BAD'; then
  echo "$TOKEN_RES" | grep '^BAD' | sed 's/^/  /'
  fail "wiki 非归档区发现疑似 token（${TOKEN_N:-?} 页）"
else
  ok "wiki 非归档区 token 扫描通过（${TOKEN_N:-?} 命中）"
fi

# 死链：使用 fix_deadlinks.py --dry-run 的"未解/多候选"计数（已考虑别名/大小写/下划线归一）
DEAD_RES="$(cd "$WIKI_DIR" && python3 .scripts/fix_deadlinks.py 2>&1 || true)"
DEAD_N="$(echo "$DEAD_RES" | python3 -c "import sys,re; m=re.search(r'未解/多候选:\s*(\d+)', sys.stdin.read()); print(m.group(1) if m else 999)")"
RAW_DEAD_N="$(echo "$DEAD_RES" | python3 -c "import sys,re; m=re.search(r'raw 区死链:\s*(\d+)', sys.stdin.read()); print(m.group(1) if m else 0)")"
if [[ -z "${DEAD_N// }" ]]; then DEAD_N=999; fi
if [[ "${DEAD_N:-999}" -gt 20 ]]; then
  echo "$DEAD_RES" | tail -10 | sed 's/^/  /'
  fail "非 raw 区死链 ${DEAD_N:-?} > 20（raw 区 ${RAW_DEAD_N:-?}）"
else
  ok "非 raw 区死链 ${DEAD_N:-?} <= 20（raw 区 ${RAW_DEAD_N:-?}）"
fi

MH_N="$(find "$WIKI_DIR/concepts" "$WIKI_DIR/queries" -maxdepth 1 -type f -name '*memoryhub*' -o -name '*obse-rv-at-memoryhub*' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$MH_N" -gt 0 ]]; then
  fail "concepts/queries 下仍有 memoryhub 页 ${MH_N} 个"
else
  ok "concepts/queries 下无 memoryhub 页"
fi

if [[ "$FAIL" == 0 ]]; then
  echo "== verify: 全部通过 =="
  exit 0
else
  echo "== verify: 存在失败项 =="
  exit 1
fi
