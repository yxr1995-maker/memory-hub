# status.sh RED 修复验证证据

## 问题描述
status.sh 第 22 行在 `set -euo pipefail` 模式下，当 staging 目录没有 observations-*.jsonl 文件时，ls 返回非零退出码导致脚本提前终止（EXIT=1）。

## 修复内容
在 ls | wc | tr 管道后添加 || true，使空 staging 目录时正常返回 0。

## 验证结果

### 1. 测试套件 (test_status_verify_consistency.sh)
PASS  safe placeholders (9 categories): token_hits=0
PASS  leaked tokens (10 categories): token_hits=10
PASS  status and verify agree on safe placeholders
PASS  status and verify agree on leaked credentials
PASS  status and verify ignore raw, archive, and drafts/memoryhub
PASS  status and verify reject leaked credentials in other drafts
PASS  real wiki: token_hits=0
PASS  --format json produces valid JSON
PASS  --json alias produces valid JSON
PASS  credential value not in stdout
PASS  verify.sh exits 0 on real wiki
PASS  status.sh exits 0 on real wiki
== 全部通过 ==
TEST_EXIT=0
