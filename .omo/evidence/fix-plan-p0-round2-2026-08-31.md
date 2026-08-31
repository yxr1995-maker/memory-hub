# P0 plan/spec repair — reviewer revision round 2

Scope: documentation-only repair to the assigned specification and plan. This ignored `.omo/evidence/` file records the direct validation required for this round.

## Changes tied to reviewer criteria

1. Lifecycle index ownership: specification section 5.3 and Tasks 8/11 now split page preparation from post-index finalization. `prepare_successor_pages()` remains `PREPARED` without an index rebuild, Task 11 performs exactly one swap and then finalizes, and standalone manual publish is exactly `prepare → one index swap → finalize`.
2. Provenance privacy and interface consistency: raw `session_cwd` is removed from the Observation model/output pseudocode. Provenance retains only `project_id` and irreversible `cwd_hash`; the Task 1 test asserts a realistic `/Users/real-user` prefix is absent from JSONL and report. Every `write_page` reference uses the declared four arguments.
3. Fixture seeding ownership: Task 1 owns the creation of `tests/helpers/full_auto_fixture.py` and its public `seed-verify-dependencies` CLI. Task 13 consumes that CLI; Task 14 consumes it without declaring a helper modification.

## Direct command output

```text
COMMAND: round2 contract audit
spec_prepare_finalize=PASS
spec_maintain_single_swap=PASS
spec_manual_wrapper=PASS
task8_split_interfaces=PASS
task8_no_swap_in_prepare=PASS
task8_finalize_gate=PASS
task8_manual_one_swap_test=PASS
task11_only_swap_stage=PASS
task11_one_swap_test=PASS
no_legacy_apply_successor=PASS
spec_no_raw_cwd=PASS
plan_no_observation_session_cwd_field=PASS
plan_project_hash_only=PASS
plan_home_jsonl_report_test=PASS
write_page_four_arg_example=PASS
seed_task1_owner=PASS
task13_consumes_seed=PASS
task14_no_helper_modify=PASS
CHECK_COUNT=18 RESULT=PASS

COMMAND: obsolete/reference scans
PASS: no obsolete lifecycle/raw-cwd/three-argument-write_page references

COMMAND: placeholder scan
PASS: no placeholders

COMMAND: git diff check
PASS: git diff --check

COMMAND: scope
 M docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md
?? docs/superpowers/plans/

COMMAND: ownership and arity audit
full_auto_fixture_file_owner=Task1
write_page_occurrences=2
write_page_arity=4/4

COMMAND: untracked plan whitespace audit
untracked_plan_diff_check_exit=1
untracked_plan_whitespace_diagnostics=0

COMMAND: spec/plan hashes
136bec8f43ac2315d617f28bab9b7945798ca54b6e6300a6ce7bec82fdf4d467  docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md
b6d06892dfcc626960dfb4868d252056560ed71c93a12e44916018c269b5f716  docs/superpowers/plans/2026-08-31-roadmap-full-auto.md
```

Interpretation: the `--no-index --check` invocation returns 1 only because the plan is untracked and differs from `/dev/null`; its captured output was empty, so there are zero whitespace diagnostics. The tracked `git diff --check` passed. The status output lists only the two assigned documentation paths; evidence lives in the ignored required `.omo/evidence/` directory.

## Stop-hook direct recheck

The first stop-hook audit expression used a literal without Markdown backticks for the sentence that says `` `index_swap` is the sole call ...``. A direct source lookup confirmed the intended sentence exists at plan line 1073; no document defect existed. The corrected independent audit then ran to completion:

```text
COMMAND: round2-stop-hook-recheck-corrected
lifecycle_spec_split=PASS
lifecycle_task8_prepare_no_swap=PASS
lifecycle_task8_finalize_gate=PASS
lifecycle_task11_one_swap=PASS
lifecycle_tests=PASS
no_legacy_apply=PASS
cwd_spec=PASS
cwd_model=PASS
cwd_jsonl_report_test=PASS
write_page_uniform=PASS
seed_owner_task1=PASS
seed_task13_consumer=PASS
seed_task14_consumer_only=PASS
CHECK_COUNT=13 RESULT=PASS

COMMAND: direct-diff-check
git_diff_check_exit=0

COMMAND: direct-evidence-check
evidence_bytes_before_append=2982
evidence_nonempty=PASS
```

Judgment: direct reread and corrected contract audit verify all three round-2 reviewer requirements against the edited documents. No feature code was authorized or changed.

## Stop-hook final direct audit

```text
COMMAND: round2-final-direct-audit
spec_lifecycle_prepare_finalize=PASS
spec_provenance_redaction=PASS
plan_maintain_single_index=PASS
plan_privacy_test=PASS
plan_seed_ownership=PASS
legacy_lifecycle_raw_cwd_write_page=PASS
spec_sha256=136bec8f43ac2315d617f28bab9b7945798ca54b6e6300a6ce7bec82fdf4d467
plan_sha256=b6d06892dfcc626960dfb4868d252056560ed71c93a12e44916018c269b5f716
RESULT=PASS

COMMAND: current-diff-check
git_diff_check_exit=0

COMMAND: current-status
 M docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md
?? docs/superpowers/plans/
```

Judgment: the exact file bytes identified by the recorded hashes satisfy lifecycle separation, cwd/home redaction, four-argument `write_page`, and Task 1-only fixture seeding ownership. The only product content paths reported by Git remain the assigned specification and plan.

## Stop-hook third direct audit

```text
COMMAND: round2-third-stop-audit
round2_third_contract_assertions=9 PASS
git_diff_check_exit=0
preexisting_evidence_nonempty=PASS
```

Judgment: a fresh direct assertion check revalidated the nine critical lifecycle, privacy, API-arity, and fixture-ownership conditions. The tracked documentation diff remains whitespace-clean. This output was recorded immediately after the command completed.
