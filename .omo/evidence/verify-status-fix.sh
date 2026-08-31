#!/usr/bin/env bash
set -euo pipefail
WORKTREE="/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto"
cd "$WORKTREE"

echo "=== 검증 1: 테스트 스크립트 실행 ==="
bash tests/test_status_verify_consistency.sh 2>&1
echo "TEST_EXIT=$?"

echo "=== 검증 2: status.sh 직접 실행 ==="
cd scripts
bash status.sh 2>&1
echo "STATUS_EXIT=$?"

echo "=== 검증 3: diff 확인 ==="
git diff HEAD~1 HEAD -- scripts/status.sh

echo "=== 검증 4: 커밋 확인 ==="
git log --oneline -1

echo "=== 검증 5: staging 상태 ==="
ls staging/ 2>/dev/null || echo 'staging directory empty or missing'

echo '모든 검증 완료'
