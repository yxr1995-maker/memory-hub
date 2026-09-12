# Native Codex memory

Memory Hub integrates through a versioned Codex plugin, command hooks and stdio MCP. It does not patch Codex or write Codex's built-in memory files.

## Controls

```bash
./memory-hub.sh codex doctor --json
./memory-hub.sh codex configure --recall on --capture on --publish off
./memory-hub.sh memory-worker --once
./memory-hub.sh memory-worker --once --retry-failed
./memory-hub.sh codex launchagent
```

Configuration and runtime data live under `MEMORY_HUB_DATA` (default `~/.memory-hub`). `WIKI_PATH` selects the Markdown knowledge base. `codex-memory-settings.json` contains independent `recall`, `capture`, and `publish` booleans. Publication defaults off. Changing these switches does not require restarting the worker.

`SessionStart` supplies brief memory-use rules. `UserPromptSubmit` retrieves local history without making another model request. `Stop` and `SessionEnd` incrementally queue complete transcript records. Hooks must not block normal conversations on memory failures. Historical content is evidence, not instructions, and never overrides the current user request.

## Background publication

A single LaunchAgent runs `memory-worker --once` every 60 seconds. The worker shares the existing automation lock. `OPENCODEX_URL` and `CLAUDE_MEM_MODEL` select the configured model gateway; credentials must not be embedded in the launch agent or plugin.

Model proposals are checked against source records. Unsupported claims, volatile facts, duplicates and conflicting conclusions do not overwrite existing knowledge. A Git commit and successful index rebuild are distinct states: after a commit, an index failure must be repaired without replaying the publication.

Keep publication disabled until both CLI and Desktop acceptance have passed. A registered hook does not prove it fired, and a fired hook does not prove the model used its memory. `codex doctor` reports these separately. `model_use_verified` stays false unless an explicit client acceptance record is available; raw event counts are never substituted for that evidence.

## Installation and recovery

The source plugin is `plugins/memory-hub`. Update the already registered local `memory-hub@personal` source through the official plugin workflow, after backing up its source, Codex configuration, marketplace, runtime mapping and switches. Never edit the plugin cache directly. Validate the plugin before reinstalling and use a new Codex thread to pick up new hooks and tools.

`codex-runtime.json` points the installed plugin at the checkout and Python interpreter. Explicit environment overrides permit isolated CLI tests. Workspace-specific mappings permit isolated Desktop Hook tests without directing other conversations into a test database. MCP uses the same runtime resolver: explicit MEMORY_HUB_DATA/WIKI_PATH override workspace mappings and runtime defaults. Its workspace is MEMORY_HUB_WORKSPACE when provided, otherwise the actual process working directory; if a client launches MCP from the plugin directory, configure an explicit workspace or isolated runtime instead of assuming the client project directory is available.

If client hook trust is required, inspect and approve only the Memory Hub hook commands through Codex's supported mechanism. Do not disable global hook trust or unrelated protections. Keep the original global MCP entry until the plugin MCP handshake has succeeded; then remove only the duplicate Memory Hub registration.

Backups are stored in `~/.memory-hub/backups/`. To suspend all automatic work, turn capture and publish off. The LaunchAgent can be unloaded independently; persisted queue items remain available for recovery. Never turn a failed client acceptance into an automatic-publication rollout.

## Acceptance records

`codex-acceptance.json` records explicit CLI and Desktop experiments under `clients`.
Each client record contains `passed`, distinct `session_id` and `recall_session_id`,
`hook_events`, a 40-character `commit`, the published `source`, the observed model
`answer`, `elapsed_seconds`, and `verified_at`. This is an operator-recorded test
result, not an authentication or authorization mechanism. Doctor will not infer
model use from a hook event or an incomplete acceptance record. Keep experiment
transcripts and hook outputs with the record for independent inspection.

Hooks require trust in Codex. The one-off CLI acceptance process may use the
supported `--dangerously-bypass-hook-trust` flag for reviewed fixture hooks; it
must not change global hook-trust settings. The official event and trust contract
is documented at https://learn.chatgpt.com/docs/hooks.

## Current verification

Acceptance and runtime records are stored locally under `docs/evidence/codex-native-2026-09-08/` and the integration data directory. Use `codex doctor --json` for current switches and verification state. Historical CLI/Desktop experiments do not certify later code changes. Publication must remain disabled until current client evidence has been checked against the deployed source.

## Bounded transcript backlog

Hooks retain a local capture_backlog row before reading a transcript. The scheduled worker continues at most eight chunks per invocation, with a two-second loop budget, even when publication is off. Each chunk reads at most 2 MiB. This local continuation does not call a model. Incomplete rows remain pending and rotate behind other sessions. The committed byte cursor advances only through complete JSONL lines; an oversized line uses a separate scan position, is explicitly recorded as skipped, and does not prevent subsequent normal records from being captured. Skipping a record resets the inferred turn until a real turn_context arrives. Doctor reports capture_backlog independently of the publication queue.

The publication queue reserves half of each 20-record batch for recent records and fills the rest from the oldest pending records. This avoids putting every new turn behind the historical import while still making progress on older items. It is not a throughput guarantee during sustained overload. Model proposal objects are schema-validated before deterministic evidence checks; malformed output enters the bounded retry path.

## Semantic comparison and production preflight

The worker checks source eligibility and local duplicate/conflict signals before asking the configured gateway to compare grounded claims. Semantic duplicate/conflict output must cite an exact supplied page and quote. A cross-project duplicate cannot silently erase project provenance. Comparison is bounded to 50 claims and 20,000 characters; incomplete input stays a candidate. Transport failure enters the three-attempt retry path. An isolated real-gateway bilingual duplicate required no page or Git commit (27.23 seconds on retry); its first timeout is retained as evidence.

The 2026-09-08 real-library preflight found 9,766 structured Markdown pages, of which 9,738 lack scope_id. Legacy compatibility keeps all pages retrievable and leaves their files unchanged. Explicitly scoped memories participate according to project/global applicability. Unknown-scope pages enter semantic comparison only when decision, abstract, source_quote, or lesson contains an explicit preference/decision statement. This recognizes historical statements, not their authorship or global applicability: unknown-scope duplicates and conflicts remain review candidates. Other unknown-scope history provides conservative local lexical/topic conflict checks. A nonmatching imported article is not treated as a global user constraint.

This is bounded comparison over recognized memory statements, not a guarantee of exhaustive semantic equivalence across arbitrary legacy prose. A preference paraphrased in unrecognized historical prose may escape semantic comparison. Known matches are retained for review; the worker does not automatically rewrite, merge, or retire old pages. Candidate files retain grounded semantic match paths and quotes. Transport failures retry; incompatible scope is a review reason rather than a transport failure.

The final local scan of 16,435 real Markdown pages selected nine unknown-scope explicit claims in 2.883 seconds, with a relevant legacy overlap correctly routed toward review. A 10,001-page isolated fixture using the actual gateway published one new page, committed and indexed it, and returned its source through the recall Hook in a 13.64-second final-code worker run (legacy-scale-runtime.json). That is a manual worker-scale test; the unattended CLI/Desktop measurements remain separate.

The three rows from the known Desktop acceptance session accidentally created in the real workspace were backed up and quarantined by exact session ID. No other queue rows were changed, and no test preference was published into the real wiki during this preflight.

A batch with no possible eligible source is retained as candidates locally without calling the model gateway. The check uses necessary conditions of the existing publisher: explicit user preference/decision syntax, or an exact PASSED tool assertion line. Mixed batches still use the normal proposal and evidence-validation path. This avoids repeatedly sending pure assistant suggestions and unrelated command logs for a publication decision they cannot pass. A production timeout that motivated this change is preserved in production-first-worker.json.

### Review hardening (2026-09-08)

Doctor now distinguishes `historical_acceptance` from `current_build_verified`.
Current verification requires both client records, matching core source hashes and
plugin version, an installed MCP launcher, and a healthy index. `model_use_verified`
follows current verification; old client evidence is not silently re-stamped.
The worker filters gateway source rows and sanitizes strings again at egress;
empty proposals bypass wiki scanning. Unknown explicit lifecycle statuses are
indexed conservatively as candidates; existing indexes require a normal rebuild
to adopt this normalization.
