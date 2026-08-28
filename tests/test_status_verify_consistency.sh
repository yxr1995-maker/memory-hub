#!/usr/bin/env bash
# End-to-end consistency test: status.sh + verify.sh use identical token detection.
# No grep-based self-inspection — every pass/fail asserts real scanner output.
set -euo pipefail

ROOT="$(cd "$(dirname "$BASH_SOURCE[0]")/.." && pwd)"
SCANNER="$ROOT/scripts/verify_tokens.py"
FAIL=0
FAIL_N=0

pass() { echo "PASS  $1"; }
fail() { echo "FAIL  $1"; FAIL=1; FAIL_N=$((FAIL_N + 1)); }
setup_wiki() {
  mkdir -p "$1/concepts" "$1/queries" "$1/.scripts"
  printf '%s\n' 'print("未解/多候选: 0")' 'print("raw 区死链: 0")' > "$1/.scripts/fix_deadlinks.py"
}

# --- End-to-end behavioral checks against a real wiki directory ---

# 5. Safe placeholders: 9 categories must all yield 0 hits.
SAFE_CASES=(
  'Bearer [REDACTED_BEARER]'
  'Bearer $TOKEN'
  'Bearer token.'
  'Bearer tokens.'
  'Bearer 令牌.'
  'Bearer 凭据.'
  'Bearer  '
  'Bearer $(security find-generic-password -s GitHub -w 2>/dev/null)'
  'Authorization: Bearer $TOKEN); extra'
)

TMPDIR_SAFE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_SAFE"' EXIT
setup_wiki "$TMPDIR_SAFE"
for i in "${!SAFE_CASES[@]}"; do
  echo "${SAFE_CASES[$i]}" > "$TMPDIR_SAFE/page$i.md"
done
SAFE_RESULT=$(python3 "$SCANNER" "$TMPDIR_SAFE" 2>&1 || true)
if echo "$SAFE_RESULT" | grep -q 'token_hits=0'; then
  pass "safe placeholders (9 categories): token_hits=0"
else
  fail "safe placeholders produced unexpected hits: $SAFE_RESULT"
fi

# 6. Leaked tokens: 10 categories, each must yield 1 hit on its own page.
LEAK_CASES=(
  'Bearer short'
  'Bearer $(printf literal-secret)'
  'Bearer another-static-value'
  'Bearer $(security find-generic-password -w | printf literal)'
  'Bearer $(security find-generic-password -w; printf literal)'
  'Bearer $(security find-generic-password -w && printf literal)'
  'Bearer $(security find-generic-password -w > literal)'
  'Bearer $(cat <<< literal)'
  'eyJ.synthetic.payload'
  'sk-ant-synthetic_token_0123456789'
)

TMPDIR_LEAK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_SAFE" "$TMPDIR_LEAK"' EXIT
setup_wiki "$TMPDIR_LEAK"
for i in "${!LEAK_CASES[@]}"; do
  echo "${LEAK_CASES[$i]}" > "$TMPDIR_LEAK/page$i.md"
done
LEAK_RESULT=$(python3 "$SCANNER" "$TMPDIR_LEAK" 2>&1 || true)
EXPECTED_HITS=${#LEAK_CASES[@]}
ACTUAL_HITS=$(echo "$LEAK_RESULT" | grep '^token_hits=' | cut -d= -f2)
if [[ "$ACTUAL_HITS" -eq "$EXPECTED_HITS" ]]; then
  pass "leaked tokens ($EXPECTED_HITS categories): token_hits=$EXPECTED_HITS"
else
  fail "leaked tokens: expected $EXPECTED_HITS hits, got $ACTUAL_HITS -- $LEAK_RESULT"
fi

SAFE_STATUS=$(WIKI_PATH="$TMPDIR_SAFE" bash "$ROOT/scripts/status.sh" 2>&1)
if echo "$SAFE_STATUS" | grep -q 'token命中=0' \
   && WIKI_PATH="$TMPDIR_SAFE" bash "$ROOT/scripts/verify.sh" >/dev/null 2>&1; then
  pass "status and verify agree on safe placeholders"
else
  fail "status and verify disagree on safe placeholders"
fi

LEAK_STATUS=$(WIKI_PATH="$TMPDIR_LEAK" bash "$ROOT/scripts/status.sh" 2>&1)
if echo "$LEAK_STATUS" | grep -q "token命中=$EXPECTED_HITS" \
   && ! WIKI_PATH="$TMPDIR_LEAK" bash "$ROOT/scripts/verify.sh" >/dev/null 2>&1; then
  pass "status and verify agree on leaked credentials"
else
  fail "status and verify disagree on leaked credentials"
fi

TMPDIR_EXCLUDED=$(mktemp -d)
trap 'rm -rf "$TMPDIR_SAFE" "$TMPDIR_LEAK" "$TMPDIR_EXCLUDED"' EXIT
setup_wiki "$TMPDIR_EXCLUDED"
mkdir -p "$TMPDIR_EXCLUDED/raw" "$TMPDIR_EXCLUDED/_archive" "$TMPDIR_EXCLUDED/drafts/memoryhub"
echo 'Bearer excluded-raw-value' > "$TMPDIR_EXCLUDED/raw/page.md"
echo 'Bearer excluded-archive-value' > "$TMPDIR_EXCLUDED/_archive/page.md"
echo 'Bearer excluded-memoryhub-draft-value' > "$TMPDIR_EXCLUDED/drafts/memoryhub/page.md"
EXCLUDED_STATUS=$(WIKI_PATH="$TMPDIR_EXCLUDED" bash "$ROOT/scripts/status.sh" 2>&1)
if echo "$EXCLUDED_STATUS" | grep -q 'token命中=0' \
   && WIKI_PATH="$TMPDIR_EXCLUDED" bash "$ROOT/scripts/verify.sh" >/dev/null 2>&1; then
  pass "status and verify ignore raw, archive, and drafts/memoryhub"
else
  fail "status and verify disagree on exact exclusions"
fi

TMPDIR_DRAFT_LEAK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_SAFE" "$TMPDIR_LEAK" "$TMPDIR_EXCLUDED" "$TMPDIR_DRAFT_LEAK"' EXIT
setup_wiki "$TMPDIR_DRAFT_LEAK"
mkdir -p "$TMPDIR_DRAFT_LEAK/drafts/review"
echo 'Bearer public-draft-value' > "$TMPDIR_DRAFT_LEAK/drafts/review/page.md"
DRAFT_LEAK_STATUS=$(WIKI_PATH="$TMPDIR_DRAFT_LEAK" bash "$ROOT/scripts/status.sh" 2>&1)
if echo "$DRAFT_LEAK_STATUS" | grep -q 'token命中=1' \
   && ! WIKI_PATH="$TMPDIR_DRAFT_LEAK" bash "$ROOT/scripts/verify.sh" >/dev/null 2>&1; then
  pass "status and verify reject leaked credentials in other drafts"
else
  fail "status and verify incorrectly exclude non-memoryhub drafts"
fi

# 7. Real wiki: scanner yields 0 hits.
REAL_RESULT=$(python3 "$SCANNER" "$HOME/llm-wiki" 2>&1 || true)
if echo "$REAL_RESULT" | grep -q 'token_hits=0'; then
  pass "real wiki: token_hits=0"
else
  fail "real wiki token_hits unexpected: $REAL_RESULT"
fi

# 8. --format json produces valid JSON.
TMPDIR_FMT=$(mktemp -d)
trap 'rm -rf "$TMPDIR_SAFE" "$TMPDIR_LEAK" "$TMPDIR_FMT"' EXIT
echo 'Bearer short' > "$TMPDIR_FMT/page.md"
JSON_RESULT=$(python3 "$SCANNER" --format json "$TMPDIR_FMT" 2>&1 || true)
if echo "$JSON_RESULT" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
  pass "--format json produces valid JSON"
else
  fail "--format json did not produce valid JSON: $JSON_RESULT"
fi

# 9. --json alias works identically.
JSON_ALIAS=$(python3 "$SCANNER" --json "$TMPDIR_FMT" 2>&1 || true)
if echo "$JSON_ALIAS" | python3 -c "import json,sys; json.loads(sys.stdin.read())" 2>/dev/null; then
  pass "--json alias produces valid JSON"
else
  fail "--json alias did not produce valid JSON: $JSON_ALIAS"
fi

# 10. Stdout never contains the actual credential value.
TMPDIR_SECRET=$(mktemp -d)
trap 'rm -rf "$TMPDIR_SAFE" "$TMPDIR_LEAK" "$TMPDIR_FMT" "$TMPDIR_SECRET"' EXIT
echo 'Bearer synthetic-secret-0123456789ABCDEF' > "$TMPDIR_SECRET/page.md"
SECRET_RESULT=$(python3 "$SCANNER" "$TMPDIR_SECRET" 2>&1 || true)
if echo "$SECRET_RESULT" | grep -q 'synthetic-secret-0123456789ABCDEF'; then
  fail "credential value leaked to stdout"
else
  pass "credential value not in stdout"
fi

# 11. Both scripts exit 0 on clean wiki (verify.sh full run).
bash "$ROOT/scripts/verify.sh" > /dev/null 2>&1 && \
  pass "verify.sh exits 0 on real wiki" || \
  fail "verify.sh exits non-zero on real wiki"

# 12. status.sh exits 0 on clean wiki.
bash "$ROOT/scripts/status.sh" > /dev/null 2>&1 && \
  pass "status.sh exits 0 on real wiki" || \
  fail "status.sh exits non-zero on real wiki"

if [[ "$FAIL" -eq 0 ]]; then
  echo "== 全部通过 =="
  exit 0
else
  echo "== 存在失败项（$FAIL_N 项）=="
  exit 1
fi
