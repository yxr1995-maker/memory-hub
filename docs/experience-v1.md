# Experience v1: explicit, source-backed reuse

This experimental path shares the existing Python/MCP process. Legacy wiki Markdown, search/ask/inject, and `memory-hub.sh` are unchanged. It uses `MEMORY_HUB_DATA/experience.sqlite3` as authority, not the rebuildable wiki index. No model calls occur on record/recall/read. All shipped development cases are **synthetic**, not observed user preferences or measured marketing outcomes.

## Local CLI

From the repository, use the existing environment:

```bash
.venv/bin/python -m scripts.automation_core.experience.cli --help
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --owner init
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture --owner record --file /tmp/episode.json --key episode-1
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture recall --task '修改文案，保留感情但不要制造负担' --mode explore
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture read --id EPISODE_ID --revision 1
```

Copy one JSON line from `evaluation/experience_v1/development.jsonl` into `/tmp/episode.json` for the synthetic example. Record returns the actual ID. `init` is explicit; a missing database on read returns `NOT_INITIALIZED`. Authority migrations run only from init, with SQLite backup to `experience.sqlite3.v1-backup` before changing v1. Existing backup conflicts fail closed. Initialization, record, correction, feedback, export and purge are explicit writes. Recall/read use a read-only SQLite transaction and no access log or implicit initialization; the optional adapter session cache stores delivery state only in process memory.

Add `--artifact-root /absolute/approved/root` before the command for local media references. Each artifact has `artifact_id`, `media_type` (`text`, `image`, `video`, `other`), absolute `location`, SHA256 `checksum`, and `license` (`read` or `reference`); video additionally has a string `segment`. Files are not copied. Read checks approved roots and current hashes; missing or changed references fail. The tool returns references, not decoded image/video understanding. The host must honor the license when opening media. Full-file integrity hashing may be expensive for large media; no unmeasured latency claim or read-side cache is provided. An artifact may carry an optional `caption`: a non-empty string of at most 1000 characters written by whoever already looked at the media. The caption is the only artifact text that becomes searchable; an uncaptioned artifact stays invisible to recall, and no pixel or frame analysis happens here. Changing or removing a caption is a revision like any other field; deleting it removes its derived keys on the next rebuild.

`--owner` is an explicit local trusted management entry. It is not an OS security boundary against a caller who already controls Python or the filesystem. MCP always constructs an agent context from launch configuration, never from tool parameters.

## Correction and governance

```bash
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture --owner revise --id EPISODE_ID --base-revision 1 --patch /tmp/patch.json --reason 'current brief correction' --evidence-id EPISODE_ID
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture --owner revoke --id EPISODE_ID --base-revision 2 --reason 'stop recommending'
```

A patch may change `explicit_reason`, `conditions`, structured `preconditions`, `interpretation`, and explicit `counterexample_ids`. It cannot replace the original narrative/source record. Old versions remain readable until revoked. Stale writes return `VERSION_CONFLICT`. Withdrawing a supporting source flags dependent interpretations for review and suppresses their current recommendation; explicitly reading them returns `review_required=true` for inspection, not endorsement.

`export --id ID --destination /tmp/view.md` creates a tool-owned Markdown view. Changed/unowned files cause `EXPORT_CONFLICT`; export edits do not mutate authority. There is no automatic import of edited exports; apply deliberate changes through `revise` with a base revision and evidence.

`purge --id ID --base-revision N --reason privacy` erases the selected episode's local content, derived keys, feedback and unchanged owned exports. It does not unlink user-owned/shared media references, erase copies in other conversations, independent records or backups, or promise remote erasure. A revoked episode may also be purged using its current revision. There is no owned blob store in this version, so there is no blob copying/garbage-collection job.

### Explicit backup and recovery to a new path

The existing management CLI can take a consistent SQLite snapshot and restore it to a **new, unused database path**. Both commands use `--db` as the source and require `--owner` plus authorization for every collection present in the snapshot. Restore additionally requires `--current-store`, the trusted current governance database, and access to all of its collections. Recovery accepts schema v2 only and validates tables and integrity; older stores must use the existing explicit migration flow before backup. Neither command changes the host's configuration or replaces its active database.

```bash
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture --owner backup --destination /tmp/demo/snapshot.sqlite3
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/snapshot.sqlite3 --collection fixture --owner restore --destination /tmp/recovered/experience.sqlite3 --current-store /tmp/demo/experience.sqlite3
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/recovered/experience.sqlite3 --collection fixture recall --task '修改文案'
```

SQLite's backup API includes committed changes still in the write-ahead log. The copy retains recorded versions, feedback, revocations, deleted tombstones and dependent interpretations requiring review. Existing destinations, symbolic links and conflicting SQLite sidecar files are rejected. The completed copy is published without overwriting a competing file; private temporary files are cleaned up on failure.

A restore clears the copy's **export ownership registry**, reporting `detached_exports_count`. Original exported Markdown files remain with the original store; purging a restored episode cannot delete those exports. Export recovered episodes to new destinations when needed. External media remain references and are not included in the snapshot; their current files, permissions and checksums must still pass the normal read checks.

Restore applies the supplied current store's governance before publishing the copy. Later deletions erase the copy's matching content and feedback; later revocations prevent all evidence reads. An older understanding whose current revision has advanced, or which currently requires review, stays available for explicit inspection with `review_required=true` and is excluded from recommendations; dependent interpretations are invalidated too. Existing withdrawn/deleted states cannot be reactivated. Recovery does not merge newer narratives or promote an old version as current advice.

Without `--current-store`, restore returns `CURRENT_STATE_REQUIRED` and creates no destination. The current store must cover every snapshot event with compatible identity and a revision at least as recent; missing, older, conflicting or unauthorized governance is refused. Passing the backup itself (including a link to the same file) is refused. If the current store was lost, first obtain a trustworthy source of current governance; an old backup alone is insufficient to authorize readable recovery.

This check reflects one consistent read snapshot of the explicitly supplied governance database. The tool cannot prove that an arbitrarily supplied file is the latest copy elsewhere, or synchronize future withdrawals into an already restored independent copy. Review the receipt and governance source before any later host activation. Original backups and external media remain user-owned copies subject to separate retention; recovery does not erase them or enable daily use. Earlier recovery evidence without `--current-store` describes the historical implementation only.

Feedback is an explicit `record_episode` payload with exactly `schema_version`, `collection_id`, `event_kind="feedback"`, `target_id`, `revision`, `action`, `source_kind`, `receipt_id`. Actions: displayed/adopted/modified/validated/corrected/rejected. Display/adoption never implies validation. Agent callers cannot attest validated/corrected results or create user_explicit/validator_observed sources. Feedback does not update Q values or activate interpretations.

An owner-approved revision preserves the episode's `source_kind`; permission to correct an understanding does not turn synthetic or agent-reported material into user testimony. Recall cards, revision receipts and evidence reads use the source kind in the version's payload. Older versions may have incorrectly stored `user_explicit` in the redundant metadata column; readers use the unchanged payload without migrating or rewriting historical data. This describes episode provenance, not the correction author's identity or outcome verification. A new trusted user statement must be recorded as its own source rather than retagging an existing episode.

## Retrieval limits and interpretation

If derived retrieval keys are missing or stale, use the explicit management command below.
It requires `--owner` and access to **all collections in the store**, including feedback
collections. It rebuilds `experience_keys` and `lookup_keys` in one transaction from
the latest active, non-review-required versions. It does not change source records,
revision history, feedback, export ownership, withdrawal/deletion state, or media.
It does not initialize a missing database or migrate an older schema; invalid current
versions fail with `SCHEMA_MISMATCH`, and a failed rebuild rolls back the index changes.

```bash
.venv/bin/python -m scripts.automation_core.experience.cli --db /tmp/demo/experience.sqlite3 --collection fixture --owner rebuild-index
```

This is an explicit repair operation, not automatic corruption detection. Recall still
reports `INDEX_UNAVAILABLE` for a missing keys table; arbitrary deletion of individual
key rows is not automatically distinguished from an ordinary no-match result. Normal
reads remain read-only. Rebuilding keys does not improve the lexical matching algorithm
or supply evidence that an experience helps the next task.

Four deterministic key groups (goal, reason/narrative, conditions, artifact captions) retrieve at most 20 authorized candidates per group and 60 overall. The condition group includes historical scalar metadata plus explicit precondition target text and require/exclude entries; matching an exclusion can retrieve a cautionary episode and does not establish applicability. New records and revisions refresh their own keys. Stores indexed before precondition support need the explicit owner `rebuild-index` above; ordinary reads never rewrite old keys. English words and Chinese character n-grams provide lexical retrieval; this is not general semantic transfer. At most 3 primary cards plus one explicitly linked counterexample are returned. Cards compare structured `preconditions.require`/`exclude` against optional caller-supplied current `conditions`. A matching exclusion reports `incompatible`; otherwise `compatible` requires a nonempty require map whose values all match and every exclusion to be checked against a caller value of the same scalar type that differs from the excluded value. Missing, null or differently typed exclusion values remain `unknown`, as do missing or mismatched required values. This is a comparison of supplied metadata, not independent verification of the current environment. Historical `conditions` describe the source episode only. No-fit returns empty; missing index returns INDEX_UNAVAILABLE. Recall drops cards to bound the complete compact JSON at 6000 Unicode characters by default (minimum accepted budget 512), with omissions and degradation labels. All lexical terms in the accepted task (at most 10,000 Unicode characters) participate in retrieval. Query terms and collection IDs use SQLite JSON `json_each` parameters, requiring JSON support in the existing Python SQLite build; no extension is loaded. Candidate and output limits remain unchanged. Lexical matching can still miss paraphrases or rank irrelevant shared words highly. `explore` omits old narrative previews/artifact lists, while retaining reasons, constraints and evidence IDs.

If the highest-ranked card still exceeds the output budget after lower-ranked cards have been removed, recall returns a `presentation="reference_only"` card when it fits. This card retains the episode ID, revision, source kind, synthetic label and evidence reference, with a `goal_preview`, `read_required=true`, and an explicit `omitted_fields` list. It is an expansion pointer, not usable advice: fetch the named version with `read_evidence` before relying on its reasons, conditions or interpretation. `OUTPUT_BUDGET` still reports the degradation; `omitted_count` counts wholly omitted episodes, not hidden fields. If even that pointer cannot fit (for example at 512 characters), recall returns no card and keeps the budget warning. Normal cards and permission checks are unchanged.

Recall cards also expose `retrieval_match` from the actual authorized index intersection: matched terms grouped by goal/reason/condition, at most eight shown per group, exact query and distinct-match counts, and an explicit lexical-only warning. These fields explain why a card was found; they neither establish applicability nor alter ranking. Explicitly linked counterexamples report link provenance instead of inventing a lexical match. Reference-only cards may omit this explanation and list it in `omitted_fields`. The unchanged 6000-character budget still applies. This small candidate has passed local tests and a stdio call; whether it improves task outcomes is evaluated separately.

Evidence reads page the serialized source record with a maximum 12000-character content window and `next_offset`. Full media references appear on the first page. Query receipt IDs are ephemeral until explicitly recorded. Neither retrieval nor the model's use of a result is automatically observed.

## Existing MCP integration

`mcp/server.py` registers `recall_for_decision`, `read_evidence`, `record_episode`, keeping all old tools. A launch-time `MEMORY_HUB_EXPERIENCE_PROFILE=1` exposes only the three new tools on the same process; default preserves the old tools. Trusted launch settings are JSON lists in `MEMORY_HUB_EXPERIENCE_COLLECTIONS` and `MEMORY_HUB_EXPERIENCE_ROOTS`; absent settings grant no collections. There is no role argument and no separate runtime. Short server instructions identify memory as evidence, not authority.

The unapplied example in `docs/evidence/experience-v1/host-profile.diff` targets the **existing** memory-hub identity. It is a review template: the installed plugin's actual effective overlay must be confirmed before applying it, and collection/media-root values require authorization. Do not create a duplicate live server. That template was not itself applied. Subsequent user-authorized activation of the existing plugin is limited to the synthetic workspace described below.

## Evidence and scope

See `docs/evidence/experience-v1/` for PR-0 baseline, RED outputs, CLI JSON transcripts, fresh stdio sessions, full regression and review notes. The fresh-session checks are protocol-client processes, not claimed Desktop natural invocation. The later pilot and fixed-action diagnostic were run using the existing Agnes route; see `action-stage-delivery.md` and `current-stage-gates.json`. The scale probe covers warm lexical lookup with disjoint-topic distractors only. Human blind review, paid model runs, demonstrated product benefit and Desktop natural invocation remain unverified. No claim of improved creativity or operating results is made from these fixtures.

## Isolated host activation update (2026-09-09)

The user authorized one synthetic workspace. `experience-host.json` in that workspace data root selects collections, artifact roots and the three-tool profile. Missing settings leave experience scope empty; other workspace mappings remain unchanged. Environment overrides retain precedence. See `docs/evidence/experience-v1/host-integration-notes.md` for actual installed-launcher results and the Desktop UI restriction.

`recall_for_decision` accepts optional `session_id` and `refresh`; supplying a stable session ID suppresses unchanged repeat delivery for the same task in the same service process. Each request still rechecks current state. Omitting the ID preserves stateless behavior. This does not suppress evidence retained in the host's existing conversation.

## Explicit project context (2026-09-10)

The installed plugin starts with its cache directory as cwd. The current Desktop connection supplies no usable workspace roots, so a runtime workspace mapping alone was insufficient. In the authorized synthetic workspace, `.codex/config.toml` now disables the plugin mount locally and declares the same `memory-hub` server identity using the existing installed `launch.sh`. Its cwd and `MEMORY_HUB_WORKSPACE` both point to that workspace, with only the three experience tools enabled. Codex also required one persisted `projects.<workspace>.trust_level="trusted"` entry before loading that project configuration. No global default workspace was added.

`host_context.from_context` treats a host's supplied roots as authoritative. Empty, unmapped, or ambiguous roots fail closed even when process cwd happens to match an authorized workspace. A mapped cwd is used only when the host does not support roots. The tool schemas publish the same character budgets and presentation modes enforced by retrieval, so clients can form valid requests before calling.

Actual effective configuration, launcher protocol, two free Agnes Codex CLI sessions, and rollback receipts are under `docs/evidence/experience-v1/host-context-20260910/`. Both domains read revision 2. The second CLI answer quoted the current creative correction, but still overgeneralized a historical preference. These are connection and revision checks, not a product-benefit result. Later on 2026-09-10, the specified isolated Desktop task successfully recalled and read evidence after the workspace configuration repair. Its subsequent unprompted continuation voluntarily read creative revisions 2 and 1, but mislabeled synthetic sources as user-explicit feedback. Public tool guidance was clarified and protocol-verified; a fresh Desktop answer using that guidance has not been observed. The earlier roots failure does not describe the repaired target task. See `docs/evidence/experience-v1/natural-provenance-20260910/desktop-natural-evidence.json` and the current stage gates.

Rollback: remove only the synthetic workspace's `mcp_servers.memory-hub` and `plugins."memory-hub@personal"` overrides, preserving other project settings. This restores the original plugin transport; it was verified by temporarily disabling and then restoring the exact project file. For full removal, also remove only the trust entry and the runtime workspace mapping added for this fixture. Preserve its database and other user configuration. Do not restore a whole stale global configuration backup.

## Completed M2 comparison (2026-09-12)

A complete 36-session A/B/C comparison ran on the free SenseNova route (`sensenova/sensenova-6.8-flash-lite`) with zero transport errors, per-cell model-route audits, an independent execution audit and two structurally validated blind reviews from different model families. Outcome: engineering and negative-transfer tasks ceilinged for every group; creative intent median was C=5.0 vs B=5.0 under one reviewer and C=4.0 vs B=5.0 under the other; no two checkable improvements over B. The frozen comparison did not meet the M2 exit criteria. On 2026-09-12 the owner decided to pass M2 anyway; that pass is recorded as a product decision, M3 (single-workspace daily use) is unblocked but not enabled, and ordinary retrieval stays the default. Evidence: docs/evidence/experience-v1/m2-sensenova-20260912/ (交付记录.md, gate-result.json, execution-audit.json, review-a/b.json).

## M3 activation (2026-09-12)

The experience path is active for every Codex workspace by owner instruction. `~/.memory-hub/experience-host.json` grants one collection (`codex`) with `profile:false`, so the six legacy memory tools stay available next to `recall_for_decision`, `read_evidence` and `record_episode`; the store lives at `~/.memory-hub/experience.sqlite3`. The plugin launcher treats a host scope file at the resolved data root as explicit scope, so no per-workspace mapping is needed. Rollback: empty that file, or restore the launcher backup in docs/evidence/experience-v1/m3-activation-20260912/cache-backup/. Availability is verified; daily benefit is not.


## M4-1 semantic recall (2026-09-13)

Lexical 2/3-gram keys cannot match paraphrases, and the M2 comparison ran the experience condition with lexical-only retrieval. Local vectors (fastembed BAAI/bge-small-zh-v1.5, same model as scripts/embed.py) now sit behind a derived, rebuildable experience_vectors table; recall fuses lexical and semantic candidates with RRF, and store.index_version refreshes the current revision vector when the switch is on. Frozen paraphrase comparison: hit@3 0.0 to 0.8 on the five zero-overlap queries, 1.0 = 1.0 on self-queries; cost +4.1 ms per recall, 0.88 s to index ten episodes, no LLM calls. Default: on whenever a host scope file exists, MEMORY_HUB_EXPERIENCE_SEMANTIC=0 opts out, and a missing embedder falls back to lexical. This is retrieval quality only; task-outcome benefit is still unproven.

## M4-2 artifact captions (2026-09-13)

Media references could only be found through the episode goal, reason or narrative, so an image whose content mattered was invisible to recall. An optional bounded `caption` per artifact fixes that without decoding pixels: caption tokens form the fourth lexical key group, and the derived semantic document also carries `media_type`, `segment` and the caption. Frozen comparison over ten synthetic episodes whose caption terms never occur in goal, reason or narrative: caption queries hit@3 0.0 to 1.0 and hit@1 0.0 to 1.0, the same episodes without captions returned empty for all ten queries, self-queries stayed at hit@3 1.0, and the key count grew by exactly the caption tokens (182). Cost: +0.1 to 0.4 ms per recall, no model calls, no new runtime; the store grew from 184 KB to 233-242 KB. Same limit as M4-1: this measures findability, not task-outcome benefit.

## Frozen M2 diagnostic

`evaluation.experience_v1.m2` reuses the existing snapshot freezer, B/C retrieval, Agnes transport and fixed-action executor. It freezes four previously exposed engineering cases plus four new synthetic creative briefs, then performs 24 single-response calls across A/B/C. This is a bounded diagnostic, not an unseen generalization benchmark or a Desktop acceptance substitute.

```bash
.venv/bin/python -m evaluation.experience_v1.m2 freeze /absolute/new/empty/run
# Write an authorized free-only authorization.json matching manifest.json.
.venv/bin/python -m evaluation.experience_v1.m2 run /absolute/new/empty/run
```

Freeze does not grant authorization. Execution requires an explicit model, at least 24 authorized requests within the existing maximum of 36, an output cap of 1..1200, and paid_cap=0. Tasks, rubric, snapshots and execution-source hashes are checked before each request; the authorization is also pinned for the run. The transport uses the existing 40-second timeout with no retries. An unexpected returned model is recorded and stops further calls. Other failed requests remain failures; missing usage and billing remain null.

Engineering results distinguish transport completion, valid action JSON and actual postconditions, including preserved edits, the no-conflict control, and recoverable SQLite backup rows. Creative outputs go into a randomized `blind-review.json`; its private `blind-key.json` holds group identity separately. Factual accuracy and intent must be judged separately against the frozen rubric. A formatting advantage alone cannot satisfy the two-semantic-improvement gate. The run summary remains `M2_gate=unassessed` until reviewed evidence is combined; no run automatically enables daily use or publication.

The existing `paired_host` freezer also accepts `--baseline hybrid`. This opt-in B baseline invokes the existing `scripts/embed.py index` against the new synthetic wiki and uses `MemoryService` with fusion enabled and query expansion disabled. `HF_HUB_OFFLINE=1` requires an already cached embedding model; missing dependencies, indexing failures or empty vector results prevent a valid freeze rather than silently substituting lexical search. The default remains lexical. Run the freezer with an existing Python that has fastembed; `--python` independently selects the existing MCP host interpreter. No packages are installed by this command.

```bash
python3 -m evaluation.experience_v1.paired_host freeze /absolute/new/empty/run \
  --suite evaluation/experience_v1/transfer-suite.json \
  --python /absolute/existing/mcp/python --structured-output --baseline hybrid
```

`transfer-suite.json` contains twelve synthetic candidate tasks across different material families, including four new fixed action checks: manifest-owned cleanup, staged publication on interruption, actual process exit status, and CSV round-trip preservation. It reuses the same freezer and executor. It is a locally authored transfer candidate, not proof of hidden-training independence or improved outcomes. The A snapshot is empty; B and C receive identical active sources. Withdrawn material is absent from both. Vector setup time and consumed query results are recorded outside model prompts; input and source hashes enter the existing manifest. This freeze performs zero generation-model calls and does not replace earlier failed runs or change stage gates.

For an existing frozen run, the offline diagnostic can expose an action declaration even when strict formatting prevented execution:

```bash
.venv/bin/python -m evaluation.experience_v1.diagnostics /absolute/frozen/run --output /absolute/new/diagnostic-report.json
```

It reads the results without calling a model or executing an action. A single fenced JSON declaration may be inspected with surrounding prose, but duplicate keys, additional structured candidates, invalid shapes and transport failures remain unknown. The expected action is available only for an exact known task contract whose action-source hash matches the frozen manifest. Declaration agreement, original format validity and executed postconditions remain separate; the diagnostic cannot promote the run or rewrite its grading. It records input hashes and refuses an existing output or any output inside the frozen directory.

The same offline CLI can validate an external review receipt before a reviewer judgment is considered:

```bash
.venv/bin/python -m evaluation.experience_v1.diagnostics /absolute/frozen/run --review /absolute/review.json --output /absolute/new/review-intake.json
```

It reads only `blind-review.json` and the supplied receipt. The receipt must contain the packet's byte-level `packet_sha256`, a nonempty `reviewer`, `group_identity_exposed: false`, and `rows` covering each packet `review_id` exactly once. Each row needs a reason and exact answer-substring `quotes`. Creative rows require `fact_accuracy` (`pass`, `fail`, `unknown`) and `intent_score` (integer 1–5 or null); negative-transfer rows require nullable booleans `decision_pass`/`reason_pass` and `historical_source_accuracy` (`pass`, `fail`, `unknown`). Missing or incomplete answers require unknown judgments; an empty answer permits an empty quote list. Duplicate JSON keys, non-finite numbers, wrong hashes, missing/duplicate/unknown IDs and explicit group exposure fail validation.

Exit 0 means only that the receipt meets this structure; quality remains `unassessed` and blindness remains `declared_unverified`. Exact quotes do not prove the explanation or score is correct, and a false declaration cannot establish actual blindness. Invalid receipts return exit 1 without repairing them. Both the review input and the new output must be outside the frozen directory; existing outputs are refused. This entry neither reads the private group key nor changes scores, model outputs or stage gates. See `docs/evidence/experience-v1/review-intake-20260910/` for test and CLI evidence, including the incomplete independent code-review attempt.
