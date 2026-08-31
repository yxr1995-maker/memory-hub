# Task 1 repair validation — 2026-08-31

Repair baseline reviewed: `acf39c85bae75610b1889d160fcd00f838526859`.

## Review requirements and behavior evidence

| Requirement | Scenario and invocation | Binary observable |
| --- | --- | --- |
| All four sources normalize before publication | `PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py` | `test_all_capture_sources_publish_controlled_provenance` ran Codex, Claude Code, Claude-mem, and WorkBuddy against real `scripts/capture.sh`; all published one normalized JSONL record with `project_id=memory-hub`, no `session_meta`, and no private cwd/user string. |
| Provenance uses a controlled project identifier | Same focused invocation | `test_session_project_id_wins_and_is_reduced_to_a_controlled_leaf` proved a session project id wins over raw `/Users/...` data and persists only `controlled-project`; the four-source scenario proved Claude-mem's raw absolute project becomes `memory-hub`. |
| Normalizer failure preserves retry state | Same focused invocation | `test_normalizer_failure_preserves_seen_and_since_for_retry` injects a nonexistent normalizer module into Codex, Claude Code, WorkBuddy, and Claude-mem. Each first capture exits non-zero with no final observations and unchanged `.since` (plus unchanged `.seen` where used); retry with the real normalizer exits zero and advances state. |
| Closing delimiter is an independent line | Same focused invocation | malformed `title: value---` is rejected by `test_parse_page_rejects_malformed_or_unsafe_frontmatter`; `test_parse_page_accepts_an_empty_frontmatter_block` proves the valid empty-block form remains parseable. |
| Claims are current and artifact-backed | This file and Task 1 implementer report | The report separates historical first-round observations from this repair-round runs and does not claim `tests/test_status_verify_consistency.sh` passed. |

## Repair-round command transcript

All commands ran in `/Users/earan/Documents/memory-hub/.worktrees/roadmap-full-auto` after the repair edits.

| Command | Exit | Observable |
| --- | ---: | --- |
| `PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py` | 0 | `11 passed in 2.72s` |
| `python3 -m py_compile scripts/automation_core/*.py tests/helpers/*.py` | 0 | no compiler output |
| `bash -n scripts/capture.sh scripts/distill.sh` | 0 | no syntax output |
| `PYTHONPATH=. uv run --with pytest pytest -q` | 0 | `23 passed, 6 subtests passed in 3.22s` |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 0 | `Ran 12 tests ... OK` |
| `./scripts/verify.sh` | 0 | `== verify: 全部通过 ==` |
| `git diff --check` | 0 | no whitespace diagnostics |

`bash tests/test_status_verify_consistency.sh` was intentionally not run: it is outside Task 1 ownership and has an independently investigated pre-existing early `exit 1`; this repair neither changes it nor represents it as passing.

## Stop-hook independent rerun

The completion gate requested a fresh direct rerun after commit `c93d39449238693c3d7319262999cb204bd3a64a`.

| Command | Exit | Raw observable |
| --- | ---: | --- |
| `git rev-parse HEAD` | 0 | `c93d39449238693c3d7319262999cb204bd3a64a` |
| `git status --short` | 0 | no output (worktree and index clean) |
| `git show --format= --name-only HEAD` | 0 | exactly `scripts/automation_core/frontmatter.py`, `scripts/automation_core/provenance.py`, `scripts/capture.sh`, and `tests/test_capture_provenance.py` |
| `PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py` | 0 | `11 passed in 3.51s` |
| `python3 -m py_compile scripts/automation_core/*.py tests/helpers/*.py` | 0 | no compiler output |
| `bash -n scripts/capture.sh scripts/distill.sh` | 0 | no syntax output |
| `PYTHONPATH=. uv run --with pytest pytest -q` | 0 | `23 passed, 6 subtests passed in 4.01s` |
| `python3 -m unittest discover -s tests -p 'test_*.py'` | 0 | `Ran 12 tests ... OK` |
| `./scripts/verify.sh` | 0 | `== verify: 全部通过 ==` |
| `git diff HEAD^ HEAD --check` | 0 | no whitespace diagnostics |

Conclusion: the committed Task 1 change remains present, its exact tracked scope is verified, and all requested in-scope validation invocations exited zero in this post-commit rerun.

## Stop-hook second independent rerun

The completion gate requested a second fresh direct post-commit check. All commands below ran after the previous evidence append, against unchanged commit `c93d39449238693c3d7319262999cb204bd3a64a`.

| Command | Exit | Raw observable |
| --- | ---: | --- |
| `git rev-parse HEAD` | 0 | `c93d39449238693c3d7319262999cb204bd3a64a` |
| `git status --short` | 0 | no output (worktree and index clean) |
| `git show --format= --name-only HEAD` | 0 | exactly the four Task 1 files: `frontmatter.py`, `provenance.py`, `capture.sh`, and `test_capture_provenance.py` |
| `PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py` | 0 | `11 passed in 4.11s` |
| `PYTHONPATH=. uv run --with pytest pytest -q` | 0 | `23 passed, 6 subtests passed in 4.28s` |
| `./scripts/verify.sh` | 0 | `== verify: 全部通过 ==` |
| `git diff HEAD^ HEAD --check` | 0 | no whitespace diagnostics |

Conclusion: the second independent rerun confirms the same committed source scope, clean working state, and passing relevant validation results. The evidence file itself remains gitignored and non-empty; no new tracked change was created by this verification.

## Stop-hook third independent rerun

The completion gate requested a third fresh direct post-commit check. All commands below ran against commit `c93d39449238693c3d7319262999cb204bd3a64a` after the previous two evidence rounds.

| Command | Exit | Raw observable |
| --- | ---: | --- |
| `git rev-parse HEAD` | 0 | `c93d39449238693c3d7319262999cb204bd3a64a` |
| `git status --short` | 0 | no output (worktree and index clean) |
| `git show --format= --name-only HEAD` | 0 | exactly `scripts/automation_core/frontmatter.py`, `scripts/automation_core/provenance.py`, `scripts/capture.sh`, and `tests/test_capture_provenance.py` |
| `PYTHONPATH=. uv run --with pytest pytest -q tests/test_capture_provenance.py` | 0 | `11 passed in 3.58s` |
| `PYTHONPATH=. uv run --with pytest pytest -q` | 0 | `23 passed, 6 subtests passed in 4.02s` |
| `./scripts/verify.sh` | 0 | `== verify: 全部通过 ==` |
| `git diff HEAD^ HEAD --check` | 0 | no whitespace diagnostics |

Conclusion: this third direct post-commit verification again confirms a clean worktree, exact Task 1 commit scope, and successful relevant tests/verification. The evidence artifact is non-empty and gitignored, so this record does not alter the committed Task 1 source scope.
