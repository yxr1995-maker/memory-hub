# Roadmap plan final-fix retry validation

Date: 2026-08-31

## Scope

- Edited only `docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`.
- Preserved the pre-existing unrelated modification to `docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md`.

## Scenario A — checkpoint ownership is unique

- Invocation: `rg -n -C 2 'Checkpoint ownership is unique|if not outcome\\.checkpoint_owned|StageOutcome\\.ok\\(checkpoint_owned=True\\)|test_maintain_checkpoint_owners_emit_one_complete_nonduplicated_sequence|checkpoint_names\\(\\).*len\\(set|lifecycle_checkpoints\\(\\)\\[-2:\]' docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`
- Binary observable: exit 0; the plan assigns `INDEX_SWAPPED` to `StageRunner.index_swap_once_and_finalize()`, `MANIFEST_COMMITTED` to `commit_manifest()`, and makes the generic loop skip `tx.journal.checkpoint()` when `outcome.checkpoint_owned` is true.
- Test observable: the Task 11 pseudocode asserts the full operation-stage sequence has no duplicates and lifecycle ends `INDEX_SWAPPED`, `COMMITTED`.

## Scenario B — Task 9 precedes Task 8 without a reverse dependency

- Invocation: `rg -n -C 2 '## Global execution order|Tasks \`1–7, 9, 8, 10–14\`|Task 9 depends only on Task 1|Complete Task 9 before starting this task|Consumes: Task 1 immutable \`OperationContext\`' docs/superpowers/plans/2026-08-31-roadmap-full-auto.md && ! rg -n 'Consumes:.*Task 8 prepared lifecycle state|Task 9 depends on Task 8' docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`
- Binary observable: exit 0; the plan fixes actual execution order to `1–7, 9, 8, 10–14`, makes Task 8 require completed Task 9 transaction infrastructure, and confirms Task 9 consumes only Task 1 `OperationContext` plus path helpers.

## Scenario C — task structure and whitespace

- Invocation: `test "$(rg -c '^### Task [0-9]+:' docs/superpowers/plans/2026-08-31-roadmap-full-auto.md)" -eq 14 && git diff --check -- docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`
- Binary observable: exit 0; `task_heading_count=14` and `git_diff_check=PASS exit=0`.

## Consolidated static assertion

- Invocation:

```bash
python3 - docs/superpowers/plans/2026-08-31-roadmap-full-auto.md <<'PY'
from pathlib import Path
import re
import sys
p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
assert 'Tasks `1–7, 9, 8, 10–14`' in s
assert 'Task 9 depends only on Task 1' in s
assert 'Complete Task 9 before starting this task' in s
assert not re.search(r'Consumes:.*Task 8 prepared lifecycle state|Task 9 depends on Task 8', s)
assert 'if not outcome.checkpoint_owned:' in s
assert s.count('StageOutcome.ok(checkpoint_owned=True)') == 2
assert 'assert len(report.journal.checkpoint_names()) == len(set(report.journal.checkpoint_names()))' in s
assert s.count('### Task ') == 14
assert not any(line.rstrip('\\n').endswith((' ', '\\t')) for line in s.splitlines(True))
print('plan_static_assertions=PASS')
PY
```

- Binary observable: `plan_static_assertions=PASS`; exit 0.

## Direct verification capture (completion hook)

Executed after this evidence file was created with `set -euo pipefail`:

```text
scenario_a_exit=0 reverse_dependency=ABSENT
scenario_b_exit=0 checkpoint_owned_outcomes=2
plan_static_assertions=PASS
scenario_c_exit=0 task_heading_count=14 git_diff_check=PASS
```

The invocation used fixed-string occurrence counting (`rg -o -F ... | wc -l`) for `StageOutcome.ok(checkpoint_owned=True)`, so both self-owned outcomes were counted even though they share one prose line.

The completion-hook direct command output is captured verbatim in `direct-verification.log` in this same directory. It completed with `verification_complete=PASS exit=0`; SHA-256: `430d9a22963c7a453cb094288ca298b3ac706d9f8292a98e7c34e0e636d71f6d`.
