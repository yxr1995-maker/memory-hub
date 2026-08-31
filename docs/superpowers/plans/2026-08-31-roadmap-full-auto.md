# memory-hub B Aggressive Full-Auto Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `run` and `maintain` execute the approved B-route automatic scope, expansion, lifecycle, clustering, publication, indexing, and exact-commit loop by default while preserving deterministic recovery and isolated-fixture safety.

**Architecture:** Move all decisions and mutations into a standard-library Python service layer under `scripts/automation_core/`; shell, REST, and MCP entry points parse arguments, validate boundaries, and render the shared structured result only. Every write operation uses one operation id, one automation lock, resolved-path validation, before images, checkpointed journals, atomic replacement, an atomic index swap, and an exact Git whitelist so recovery can restore the pre-operation state.

**Tech Stack:** Python 3.11+ standard library (`dataclasses`, `enum`, `pathlib`, `sqlite3`, `urllib`, `hashlib`, `json`, `fcntl`-free portable filesystem primitives), Bash 3.2+, SQLite FTS5 trigram, existing local vector search, `unittest`, temporary Git fixture repositories, Prometheus text exposition.

**Spec:** `docs/superpowers/specs/2026-08-31-roadmap-full-auto-design.md`

## Global Constraints

- The approved baseline is the B aggressive full-auto route: automatic inference, automatic query expansion, automatic successor creation, automatic clustering, and automatic publication are defaults; no candidate-awaiting-human-confirmation substitute may be introduced.
- Scope is a retrieval and injection namespace, not an ACL, authorization boundary, or tenant-isolation mechanism.
- Never delete, truncate, or rewrite historical page bodies; lifecycle and scope operations may only insert or replace frontmatter keys.
- Do not synchronize another wiki repository, add a resident service, or modify external automation schedules.
- Tests and acceptance runs MUST NOT read, write, stage, or commit the real `~/llm-wiki` or resolve verify dependencies from the real `$HOME`; every test supplies isolated `HOME`, `WIKI_PATH`, `MEMORY_HUB_DATA`, `CODEX_SESSIONS_DIR`, `CODEX_AUTOMATIONS_DIR`, `CODEX_CONFIG_FILE`, `CODEX_HOOKS_FILE`, and `AUTOMATIONS_DB` values.
- Never record tokens, Cookies, Authorization headers, LLM request bodies, raw private observations, full prompts, HTTP response bodies, absolute home-directory paths, or environment-variable values.
- The controlled wiki is the single root obtained by absolute-path and symlink resolution of `WIKI_PATH`; reject NUL, `..`, absolute wikilinks, external URIs, symlink escapes, non-UTF-8 input, and targets outside that root.
- An operation id is `UTC timestamp + random UUID`; all reports, journals, manifest entries, and commit whitelists for one `run` or `maintain` share it.
- Query length is at most 500 characters, `top` is clamped to 1–50, and scope is exactly `user`, `project`, or `agent`; `scope_id` is non-empty whenever scope is supplied.
- CLI `search`/`ask`, MCP `memory_search`/`memory_ask`, and REST `/search`/`/ask` default to `fuse=on, expand=on`; `--no-fuse`/`fuse=0` and `--no-expand`/`expand=0` are independent diagnostic switches.
- Every visible search result has machine-readable `path`, final `score`, `status`, `scope`, `scope_id`, and `rank_reason`; human CLI lines may retain the `[score] path.md` prefix.
- New distilled, merged, and indexed pages use `scope`, non-empty `scope_id`, `scope_confidence` (`high|medium|low`), `scope_source` (`explicit|session_meta|path|content|fallback`), `scope_conflict`, optional `project`, and optional `agent_id`.
- User scope uses `MEMORY_HUB_USER_SCOPE_ID` or the stable literal `default-user`; it MUST NOT be derived from usernames, home paths, or credentials.
- Project and agent identifiers use Unicode NFKC, trim, lowercase, and replacement of every non-`[a-z0-9._-]` run with `-`.
- Scope inference always returns a result using the six-level priority table in spec section 3.2; equal strong evidence selects the lexicographically smallest normalized id, marks `medium`, and remains deterministic.
- `scope-backfill` defaults to dry-run; `--apply` writes only controlled-wiki Markdown frontmatter, sorts by stable relative path, supports `--limit N --cursor C`, rechecks inode/mtime/content hash, skips concurrent changes, and is idempotent.
- Backfill writes with a same-directory temporary file, file `fsync`, atomic rename, before images under `$MEMORY_HUB_DATA/transactions/<operation-id>/scope-before/`, reverse-order batch rollback on failure, and a non-zero exit after rollback.
- The new index schema is `pages(path, title, type, tags, abstract, content, scope, scope_id, scope_confidence, status)` plus `meta(path, updated, last_verified, valid_at, invalid_at)`; old pages read as `project/default-project`, `low`, `fallback` in the database without mutating wiki files.
- `inject` and `export` accept scope filters and default to cross-scope; JSONL, JSON, and Markdown exports preserve scope, status, and validity fields.
- Every fused query runs original query → L0 FTS5+vector (at most 12 each) → at most 6 deduplicated snippets (abstract or first non-empty body paragraph, at most 320 characters/page) → LLM or local expansion → original and expansion recall → RRF/lifecycle/scope/dedup ranking.
- Expansion produces at most 4 unique terms of 2–64 characters, excluding control characters, URLs, suspected secrets, numeric-only terms, privacy-pattern terms, stopwords, and case-insensitive duplicates of the original query.
- Original-query RRF weight is 0.70; all expansion terms share 0.30 normalized by confidence; when no valid expansion exists, the original query receives the whole weight.
- LLM connection timeout is at most 2 seconds and read timeout at most 6 seconds; HTTP 401, 403, 429, 5xx, invalid JSON, out-of-range output, empty output, and network errors are observable single-attempt fallbacks to local expansion, not operation failures.
- FTS5 or vector failure uses the remaining source; no L0 from either source runs original-query retrieval; expansion failure never mutates the index and never replaces original results with an empty set.
- Final score ties break by active/valid status, higher scope confidence, newer `valid_at`/`updated`, then lexicographic path.
- Deprecated results never consume a final slot when a valid successor exists; cycles, broken links, or missing successors retain the old page only as `lifecycle_error`, emit a metric, and fill only if valid results are fewer than `top`.
- Search and ask context paths MUST come from the same `rank_results()` call so their top-1 and ordered path sequence are identical across CLI, REST, and MCP.
- Successor wikilinks are controlled-wiki-relative canonical paths without absolute paths, `..`, external URIs, or symlink targets; one active page may supersede many pages, one page has one current `deprecated_by`, and no write may create a graph cycle.
- Automatic successor threshold is semantic similarity `>= 0.88` plus matching key entities plus a changed comparable conclusion or freshness field; `0.72–0.88` or entity mismatch is `related-not-successor`; missing embeddings require a lexical/title/entity score `>= 0.92` to supersede.
- Uncertain successor decisions automatically publish a separate active page and retain risk audit; they never create a human-confirmation gate and never overwrite an unrelated same-name page.
- Lifecycle prepare order is validate paths/cycle → journal `PREPARED` → write/verify active successor temp → write/verify deprecated predecessor temp → atomic renames with checkpoints; it never rebuilds/swaps index or commits the lifecycle journal. The caller performs one atomic index rebuild/swap and records `INDEX_SWAPPED`; only then may lifecycle finalize verify hashes/frontmatter and mark `COMMITTED`.
- `maintain` invokes lifecycle prepare in `publish_pages_lifecycle`, performs its only index swap in `index_swap`, and calls lifecycle finalize after that checkpoint. A standalone manual-publish wrapper is `prepare → one index swap → finalize`; no path may perform a second swap. Lifecycle failure or restart restores A/B before images and the previous valid index; replay is `idempotent_skip`, and an existing A→C relation causes `concurrent_successor` without overwrite.
- Lifecycle transaction directories are `$MEMORY_HUB_DATA/transactions/<operation-id>/`, contain plan, before images, completed steps, rollback result, and sanitized relative paths, and have mode `0700`.
- `maintain` scans every date-named `staging/observations-*.jsonl`, excludes `realtime`, `test`, and manifest-consumed ids, and accepts only schema-valid observations with non-empty id/project and sanitized text length 20–2,000.
- Observation JSONL, reports, and frontmatter never persist raw `session_cwd`, absolute cwd, or a real-home prefix; provenance may contain only controlled `project_id` and an irreversible `cwd_hash`.
- Clustering is isolated by project `scope_id`, uses embedding cosine `>= 0.80` or character 3-gram Jaccard `>= 0.52`, requires at least 3 observations from at least 2 UTC dates within at most 45 days, and uses the sorted-member-id SHA-256 prefix as stable cluster key.
- The cluster manifest is `$MEMORY_HUB_DATA/manifests/cluster-observations-v1.json`, is updated by temp+fsync+rename only after `INDEX_SWAPPED` and `LINT_PASSED`, is checkpointed as `MANIFEST_COMMITTED`, and makes replay emit `manifest_skip` without republishing or consuming again.
- All automatic write flows use atomic-mkdir lock `$MEMORY_HUB_DATA/locks/automation.lock`; acquisition failure exits 75 and reports holder operation, and a lock is stale only when its PID is absent and its age exceeds 30 minutes.
- Maintain has one authoritative mutation order: `validate → publish pages/lifecycle → index swap → lint → atomic manifest commit → archive → exact stage/commit`. `MAINTAIN_ORDER` and every test/checkpoint use exactly `VALIDATED`, `PAGES_LIFECYCLE_PUBLISHED`, `INDEX_SWAPPED`, `LINT_PASSED`, `MANIFEST_COMMITTED`, `ARCHIVED`, and `STAGE_COMMITTED`; validate performs all preflight/planning without a controlled-state mutation.
- Any non-zero maintain stage stops the operation; rollback may delete only a page newly created by this operation whose hash still matches and must restore operation-owned pages/frontmatter, the prior index, and the operation-preimage manifest, including failures after manifest, archive, or stage; `git reset --hard`, `git add -A`, `git commit -a`, and directory-wide cleanup are forbidden.
- Exact stage whitelist is limited to operation-verified merged pages, predecessor/successor pages, `index.md`, and `log.md`. A non-empty baseline staged set returns `preexisting_staged`, leaves index unchanged, performs no stage/commit, and safe-fails. Only an empty baseline may stage the verified whitelist; cached paths must then strictly equal it, and any mismatch unstages only this operation's stage before failing.
- Maintain commit message is exactly `chore(wiki): memory-hub maintain <operation-id>`; a non-Git wiki reports `commit=not-a-repository` while retaining a successful publication result.
- Bare `run` and bare `maintain` default to `auto=on, apply=on, commit=on`; the first output line is `mode=auto|safe|no-auto`, `apply=true|false`, and the operation id.
- `--safe` runs parsing, planning, L0, validation, and audit but does not write the wiki, manifest, archive, stage, or commit; `--no-auto` disables scope backfill, smart relationships, successor, cross-day clustering, and automatic publication according to spec section 7.1.
- `--safe` is mutually exclusive with `--apply` and `--commit`; contradictory combinations exit 2.
- Automation anomaly thresholds switch the current operation to safe failure and emit evidence; they do not continue writes and do not create a manual candidate.
- Required reports live in `$MEMORY_HUB_DATA/reports/` with names `scope-<operation-id>.jsonl`, `query-plan-<operation-id>.jsonl`, `lifecycle-<operation-id>.jsonl`, `cluster-<operation-id>.md`, and `operation-<operation-id>.json`.
- Required Prometheus families and labels are exactly those in spec section 9, including scope, query planner/fallback, successor/cycle/rollback, cluster/manifest, operation mode/result, lock, staged-path, and commit observables.
- Implementation and tests use no real network, model, home directory, wiki, or Git user configuration; fixtures initialize isolated repositories, seed every verify input explicitly, inject deterministic clocks, UUIDs, embeddings, HTTP transports, and failure points, and prove all resolved verify paths are inside the fixture root. An empty fixture is expected to make verify fail until its minimal valid dependencies are seeded.
- Release requires all unit/integration/regression tests, `MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py`, `pytest tests/`, isolated-variable `./memory-hub.sh verify`, query expand on/off golden evidence, and the isolated real-CLI fixture to exit 0 with saved stdout/stderr, exit codes, trees, reports, index hashes, Git lists, and resolved verify-path assertions.

---

## Locked File Structure

The following structure is fixed before task decomposition. New modules have one responsibility and existing surfaces become thin adapters.

- Create `scripts/automation_core/__init__.py`: export stable public service types only.
- Create `scripts/automation_core/schema.py`: enums, immutable dataclasses, validation, JSON serialization, operation-id creation, identifier normalization.
- Create `scripts/automation_core/frontmatter.py`: strict UTF-8 frontmatter parsing/rendering, controlled-path resolution, body-preserving field patching, atomic same-directory replace.
- Create `scripts/automation_core/provenance.py`: normalize capture records and stable provenance/session metadata before JSONL output.
- Create `scripts/automation_core/scope.py`: six-level evidence inference, conflict reporting, cursor scan/backfill planning and batch execution.
- Create `scripts/automation_core/indexer.py`: build/validate/swap the new SQLite schema and detect/read legacy schemas.
- Create `scripts/automation_core/query_planner.py`: L0 retrieval snippets, redaction, LLM expansion, local expansion, structured audit.
- Create `scripts/automation_core/ranker.py`: weighted RRF, scope filtering, lifecycle graph resolution, stable tie breaking.
- Create `scripts/automation_core/service.py`: the single `search()`/`ask_context()` composition used by all surfaces.
- Create `scripts/automation_core/lifecycle.py`: successor scoring, canonical links, cycle checks, pair write plan, lifecycle recovery.
- Create `scripts/automation_core/operation.py`: lock, journal, before images, rollback coordinator, exact Git whitelist/stage/commit.
- Create `scripts/automation_core/cluster.py`: observation validation/sanitization, stable cross-day clustering, merge-page model, manifest atomics.
- Create `scripts/automation_core/orchestrator.py`: `run`/`maintain` stage machines and safe/no-auto mode semantics.
- Create `scripts/automation_cli.py`: argparse adapter for `scope-backfill`, `search`, `ask`, `run`, and `maintain`; JSON/human rendering only.
- Modify `memory-hub.sh`: route the five commands to `automation_cli.py`, enforce the new defaults, keep unrelated commands unchanged.
- Modify `scripts/capture.sh`, `scripts/distill.sh`, `scripts/publish.sh`, `scripts/index.sh`, `scripts/search.sh`, `scripts/ask.sh`, `scripts/inject.sh`, `scripts/export.py`, `scripts/fuse.py`, `scripts/metrics.sh`, and `scripts/server.py`: delegate shared behavior and preserve compatibility switches.
- Modify `scripts/verify.sh`: inject all automation/config/hooks/DB/wiki dependencies from explicit variables and emit sanitized resolved-path evidence for isolated verification; do not change verification policy merely to make an empty fixture pass.
- Modify `mcp/server.py`: return structured shared results and expose default-on expansion plus scope/explain parameters.
- Create `tests/helpers/__init__.py` and `tests/helpers/full_auto_fixture.py`: isolated home/wiki/data/session/automation/config/hooks/DB/Git fixture, deterministic clock/UUID/vector/LLM/failure doubles, the public `seed-verify-dependencies` CLI, hashing, command capture. Task 1 is its only owner; later tasks consume it without modifying it.
- Create `tests/test_capture_provenance.py`, `tests/test_scope_backfill.py`, `tests/test_index_scope.py`, `tests/test_query_planner.py`, `tests/test_ranker_lifecycle.py`, `tests/test_surface_parity.py`, `tests/test_successor_lifecycle.py`, `tests/test_operation_safety.py`, `tests/test_cluster_maintain.py`, `tests/test_auto_modes.py`, `tests/test_metrics_audit.py`, `tests/test_verify_isolation.py`, and `tests/test_full_auto_cli.py`: focused unit/integration/real-CLI coverage.
- Modify `tests/test_export.py`, `tests/test_index_atomic.py`, `tests/test_search_semantics.py`, and `.github/workflows/ci.yml`: compatibility assertions and full suite execution.
- Create `tests/fixtures/roadmap_full_auto/golden.jsonl` and `tests/fixtures/roadmap_full_auto/wiki/`: tracked deterministic expand-on/off retrieval corpus.
- Modify `scripts/eval.py`: compare expand-on versus expand-off and fail when hit@5-on is below 90% of hit@5-off.
- Create `docs/roadmap-full-auto-migration.md`; modify `README.md`, `scripts/README-portable-mcp.md`, and `SECURITY.md`: defaults, schema, staged rollout, rollback, API/MCP contracts, and secret/path guarantees.

## Public Interface Contract and Dependency Order

```python
# scripts/automation_core/schema.py
class Scope(str, Enum): USER = "user"; PROJECT = "project"; AGENT = "agent"
class Confidence(str, Enum): HIGH = "high"; MEDIUM = "medium"; LOW = "low"
class Mode(str, Enum): AUTO = "auto"; SAFE = "safe"; NO_AUTO = "no-auto"

@dataclass(frozen=True)
class ScopeAssignment:
    scope: Scope
    scope_id: str
    confidence: Confidence
    source: str
    conflict: bool
    evidence: tuple["ScopeEvidence", ...]

@dataclass(frozen=True)
class SearchRequest:
    query: str
    top: int = 10
    fuse: bool = True
    expand: bool = True
    scope: Scope | None = None
    scope_id: str | None = None
    explain: bool = False

@dataclass(frozen=True)
class SearchResult:
    path: str
    score: float
    status: str
    scope: Scope
    scope_id: str
    rank_reason: dict[str, object]

@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    command: str
    mode: Mode
    apply: bool
    commit: bool
    wiki: Path
    data: Path

FailureHook = Callable[[str], None]

@dataclass
class TransactionContext:
    """The one mutable transaction wrapper for one immutable operation."""
    operation: OperationContext
    journal: "OperationJournal"  # exactly one root journal per operation
    failure_hook: FailureHook | None = None  # test-only; production passes None

    def inject(self, point: str) -> None:
        if self.failure_hook is not None:
            self.failure_hook(point)
```

`OperationContext` is immutable and contains only operation identity/options/roots. `TransactionContext` is created once at the CLI/entry boundary by `begin_transaction(operation, baseline, failure_hook=None)`, owns the one root `OperationJournal`, and is passed explicitly to every transactional function in Tasks 8, 10, and 11. A lifecycle prepared value may reference `tx.journal`, but it never creates a lifecycle-local root journal. `FailureHook` is `Callable[[str], None]`, is injected only by tests, and receives stable named points such as `prepare.rename_new`, `index.before_swap`, `finalize.verify_pair`, and `manifest.before_replace`; production uses `None`.

## Global execution order

Task headings remain numbered 1–14 for stable references, but implementation executes Tasks `1–7, 9, 8, 10–14`. Task 9 comes before Task 8 because it creates the shared `TransactionContext` and rollback contract that lifecycle writes consume. Task 9 depends only on Task 1's `OperationContext` and controlled-path helpers; it does not depend on Task 8 or any prepared lifecycle value. Task 8 then consumes the completed Task 9 transaction API. The resulting dependency order is `schema/frontmatter/provenance` → `scope/indexer` → `query_planner/ranker/service` → surface adapters → `operation` → `lifecycle` → `cluster/orchestrator` → metrics/docs/evaluation/CLI acceptance`. No later task may invent a second result, scope, transaction, journal, or mode type.

## Spec Coverage Matrix

| Spec section | Implementing tasks | Binary observable |
|---|---|---|
| 1. Goal, boundaries, operation id | 1, 9, 11, 14 | deterministic operation id is shared by reports/journals; historical body hashes and isolated-home sentinel remain unchanged |
| 2. Unified CLI/MCP/REST contract | 5, 6, 7 | identical ordered paths/top-1 and complete structured fields for the same fixture request |
| 3. Scope inference/backfill/index/inject/export | 1, 2, 3, 7 | six-level table, 12,900 cursor scan, idempotent atomic apply, new SQLite columns, scope-aware exports all pass |
| 4. L0, expansion, fallback, ranking | 4, 5, 6, 13 | L0 caps, one-attempt fallback reason, 0.70/0.30 weights, stable ties, expand-on hit@5 floor all pass |
| 5. Successor lifecycle/journal/recovery | 5, 8, 9 | threshold decisions, reciprocal fields, cycle rejection, checkpoint failure recovery, idempotent replay hashes pass |
| 6. Cross-day maintain clustering/manifest/exact commit | 9, 10, 11, 14 | one stable cluster page, manifest replay skip, cached whitelist equality, rollback and non-repo reports pass |
| 7. Default auto, safe, no-auto, migration | 11, 12 | mode matrix/exit 2, first-line contract, safe zero-write snapshot, no-auto compatibility and docs pass |
| 8. Non-destructive misclassification control | 2, 4, 8, 10, 11 | deterministic conflict, independent active page on uncertainty, anomaly safe-failure and unchanged body hashes pass |
| 9. Metrics, audit, alerts, redaction | 4, 8, 9, 10, 12 | every required metric/report family exists; secret/home/raw-text scans have zero matches |
| 10. Module responsibilities and test matrix | 1–14 | each locked module has one owner task and its named focused test exits 0 |
| 11. Release gates | 12, 13, 14 | full pytest, fuse selfcheck, verify, dual golden eval, and real CLI artifact assertions all exit 0 |

### Task 1: Establish shared schema, strict frontmatter, and stable capture provenance

**Files:**
- Create: `scripts/automation_core/__init__.py`
- Create: `scripts/automation_core/schema.py`
- Create: `scripts/automation_core/frontmatter.py`
- Create: `scripts/automation_core/provenance.py`
- Create: `tests/helpers/__init__.py`
- Create: `tests/helpers/full_auto_fixture.py`
- Modify: `scripts/capture.sh`
- Modify: `scripts/distill.sh`
- Test: `tests/test_capture_provenance.py`

**Interfaces:**
- Consumes: only standard-library values and capture JSON objects.
- Produces: `normalize_id(value: str, fallback: str) -> str`, `new_operation_id(now: datetime, uuid_factory: Callable[[], UUID]) -> str`, `parse_page(path: Path) -> PageDocument`, `patch_frontmatter(document: PageDocument, updates: Mapping[str, object]) -> bytes`, `normalize_observation(raw: Mapping[str, object], source: str, session_meta: Mapping[str, object]) -> Observation`, `render_observation_report(observation: Observation) -> str`, and test-only `FullAutoFixture.create(evidence_dir: Path) -> FullAutoFixture`, `FullAutoFixture.seed_verify_dependencies() -> None`, public CLI `python3 -m tests.helpers.full_auto_fixture seed-verify-dependencies --root PATH --automations PATH --config PATH --hooks PATH --db PATH --wiki PATH`, `write_page(root: Path, relative: str, frontmatter: Mapping[str, object], body: str) -> Path`, `sha256(path: Path) -> str`, `FakeTransport(responses: Sequence[object])`, `FakeEmbedding(cosine: float, entities: bool = True, changed: bool = True)`, and `InjectedFailure`.

- [ ] **Step 1: Write failing schema/frontmatter/provenance tests**

```python
def test_provenance_is_stable_and_body_patch_is_exact(tmp_path):
    raw = {"id": "42", "project": " Memory Hub ", "text": "safe fact", "created_at": "2026-08-31T00:00:00Z"}
    session = {"session_id": "s-1", "cwd": "/Users/real-user/private/memory-hub", "agent_id": "Agent 7"}
    first = normalize_observation(raw, "codex", session)
    second = normalize_observation(raw, "codex", session)
    assert first.provenance_id == second.provenance_id
    assert first.project_id == "memory-hub"
    assert first.agent_id == "agent-7"
    assert first.cwd_hash == hashlib.sha256(session["cwd"].encode()).hexdigest()
    jsonl = json.dumps(asdict(first), ensure_ascii=False) + "\n"
    report = render_observation_report(first)
    assert "/Users/real-user" not in jsonl and "/Users/real-user" not in report
    assert "session_cwd" not in jsonl
    page = parse_page(write_page(tmp_path, "a.md", {"title": "A"}, "historical body\n"))
    rendered = patch_frontmatter(page, {"scope": "project", "scope_id": "memory-hub"})
    assert rendered.split(b"---\n", 2)[2] == b"historical body\n"
```

- [ ] **Step 2: Run the focused test and verify the missing module failure**

Run: `pytest tests/test_capture_provenance.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.automation_core'`; exit 1.

- [ ] **Step 3: Implement exact types, normalization, strict parsing, and provenance hashing**

```python
def normalize_observation(raw: Mapping[str, object], source: str,
                          session_meta: Mapping[str, object]) -> Observation:
    project_id = normalize_id(str(raw.get("project") or session_meta.get("project_id") or ""), "default-project")
    cwd_hash = hashlib.sha256(str(session_meta.get("cwd", "")).encode()).hexdigest()
    stable = json.dumps({"source": source, "source_id": str(raw["id"]),
                         "session_id": str(session_meta.get("session_id", "")),
                         "project_id": project_id, "cwd_hash": cwd_hash,
                         "created_at": str(raw["created_at"])}, sort_keys=True, separators=(",", ":"))
    return Observation(id=str(raw["id"]), project_id=project_id,
                       provenance_id=hashlib.sha256(stable.encode()).hexdigest(),
                       source=source, source_uri=f"{source}://sessions/{session_meta.get('session_id', '')}",
                       agent_id=normalize_optional_id(session_meta.get("agent_id")),
                       cwd_hash=cwd_hash, text=str(raw["text"]),
                       observed_at=parse_utc(str(raw["created_at"])))
```

`parse_page()` must decode with `errors="strict"`, reject malformed delimiters and duplicate controlled keys, retain the original body bytes, and make `patch_frontmatter()` render only the requested keys in canonical YAML scalar/list form.

Create the shared test helper with deterministic constructors used by later tasks:

```python
class FullAutoFixture:
    @classmethod
    def create(cls, evidence_dir: Path) -> "FullAutoFixture":
        root = Path(tempfile.mkdtemp(prefix="memory-hub-full-auto-"))
        for name in ("wiki", "data", "sessions", "automations"): (root / name).mkdir()
        subprocess.run(["git", "init", "-q", str(root / "wiki")], check=True)
        return cls(root=root, evidence_dir=evidence_dir)

    def seed_verify_dependencies(self) -> None:
        seed_verify_dependencies(root=self.root, automations=self.root / "automations",
                                 config=self.root / "codex-config.toml", hooks=self.root / "hooks.json",
                                 db=self.root / "automations.db", wiki=self.root / "wiki")

    def operation(self, command: str = "maintain", mode: Mode = Mode.AUTO) -> OperationContext:
        return OperationContext("20260831T000000Z-00000000-0000-0000-0000-000000000001",
                                command, mode, mode is Mode.AUTO, mode is Mode.AUTO,
                                self.root / "wiki", self.root / "data")

    def transaction(self, *, failure_hook: FailureHook | None = None) -> TransactionContext:
        operation = self.operation()
        return begin_transaction(operation, self.git_baseline(), failure_hook=failure_hook)

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([str(ROOT / "memory-hub.sh"), *args], env=self.env,
                              text=True, capture_output=True, check=False)

class InjectedFailure(RuntimeError):
    pass
```

`tests.helpers.full_auto_fixture` also owns a module CLI named `seed-verify-dependencies`; it creates the valid automation TOML, config, hooks JSON, SQLite automation row, and wiki helper directories/scripts required by isolated verify. Task 13 and Task 14 invoke this public CLI/method only; neither may modify this helper or duplicate its seeding logic.

- [ ] **Step 4: Route capture records through the normalizer and emit scope-ready distilled frontmatter**

Add a JSONL filter mode to `provenance.py`:

```python
def normalize_jsonl(source: TextIO, sink: TextIO, source_name: str) -> int:
    for line_number, line in enumerate(source, 1):
        raw = json.loads(line)
        sink.write(json.dumps(asdict(normalize_observation(raw, source_name, raw.get("session_meta", {}))), ensure_ascii=False) + "\n")
    return line_number if 'line_number' in locals() else 0
```

Invoke it from every `capture.sh` source branch before atomically moving the output into `staging/`; make `distill.sh` copy provenance fields and call the shared scope inference added in Task 2 rather than synthesizing IDs itself.

- [ ] **Step 5: Run tests and commit the independently reviewable provenance contract**

Run: `pytest tests/test_capture_provenance.py -q && python3 -m py_compile scripts/automation_core/*.py tests/helpers/*.py && bash -n scripts/capture.sh scripts/distill.sh`

Expected: all provenance/frontmatter cases, including a real-home-prefix exclusion from JSONL/report, PASS; py_compile and bash syntax exit 0.

```bash
git add scripts/automation_core/__init__.py scripts/automation_core/schema.py scripts/automation_core/frontmatter.py scripts/automation_core/provenance.py scripts/capture.sh scripts/distill.sh tests/helpers/__init__.py tests/helpers/full_auto_fixture.py tests/test_capture_provenance.py
git commit -m "feat: stabilize automation schema and provenance"
```

### Task 2: Implement deterministic scope inference and resumable backfill

**Files:**
- Create: `scripts/automation_core/scope.py`
- Create: `tests/test_scope_backfill.py`
- Create: `scripts/automation_cli.py`
- Modify: `memory-hub.sh`
- Modify: `scripts/distill.sh`

**Interfaces:**
- Consumes: `PageDocument`, `Observation`, `ScopeAssignment`, `OperationContext`, `normalize_id()` from Task 1.
- Produces: `infer_scope(page: PageDocument, provenance: Mapping[str, object], user_scope_id: str) -> ScopeAssignment`, `plan_backfill(wiki: Path, cursor: str | None, limit: int | None, ctx: OperationContext) -> BackfillPlan`, `apply_backfill(plan: BackfillPlan, ctx: OperationContext) -> BackfillReport`, and CLI `scope-backfill [--apply] [--limit N] [--cursor C] [--json]`.

- [ ] **Step 1: Write failing tests for all six priorities, conflict ties, cursor batches, and safety**

```python
def test_six_level_table_and_equal_strong_tie_are_deterministic(scope_cases):
    got = [infer_scope(c.page, c.provenance, "fixture-user") for c in scope_cases]
    assert [(x.scope.value, x.scope_id, x.confidence.value, x.source) for x in got] == scope_cases.expected
    tie = infer_scope(page(project="zeta"), {"project_candidates": ["zeta", "alpha"], "scores": [1.0, 1.0]}, "fixture-user")
    assert (tie.scope_id, tie.confidence.value, tie.conflict) == ("alpha", "medium", True)

def test_12900_cursor_batches_apply_idempotently_and_skip_changed(tmp_path):
    wiki, data = seed_pages(tmp_path, 12_900)
    first = plan_backfill(wiki, None, 500, operation(wiki, data))
    assert (len(first.entries), first.next_cursor) == (500, "pages/00499.md")
    mutate_after_plan(first.entries[4].path)
    report = apply_backfill(first, operation(wiki, data))
    assert report.counts == {"written": 499, "concurrent_change": 1}
    assert apply_backfill(plan_backfill(wiki, None, 500, operation(wiki, data)), operation(wiki, data)).counts["written"] == 0
```

- [ ] **Step 2: Run the focused test and verify scope entry points are absent**

Run: `pytest tests/test_scope_backfill.py -q`

Expected: FAIL importing `scripts.automation_core.scope`; exit 1.

- [ ] **Step 3: Implement inference and stable scan planning**

```python
def infer_scope(page: PageDocument, provenance: Mapping[str, object], user_scope_id: str) -> ScopeAssignment:
    candidates = collect_scope_evidence(page, provenance, user_scope_id)
    explicit = valid_explicit_assignment(page.frontmatter)
    if explicit:
        return explicit.with_evidence(candidates)
    winner = min(candidates, key=lambda e: (e.priority, -e.score, e.scope.value, e.scope_id)) if candidates else fallback_assignment()
    tied = [e for e in candidates if (e.priority, e.score) == (winner.priority, winner.score)]
    conflict = len({(e.scope, e.scope_id) for e in candidates if e.strong}) > 1
    if len(tied) > 1:
        winner = min(tied, key=lambda e: e.scope_id).with_confidence(Confidence.MEDIUM)
    return assignment_from(winner, conflict=conflict, evidence=tuple(candidates))
```

Sort resolved relative POSIX paths, reject symlink/file escapes before reading, and encode `next_cursor` as the last processed relative path. `BackfillEntry` stores relative path, inode, `st_mtime_ns`, SHA-256, assignment, and original bytes hash.

- [ ] **Step 4: Implement batch atomics, rollback, report, and CLI delegation**

`apply_backfill()` must re-stat/re-hash each entry, save a mode-0600 before image, call `atomic_replace_same_dir()`, append a sanitized JSONL record, and on any write failure restore only the current batch in reverse order. Add `memory-hub.sh scope-backfill)` as `exec python3 "$HUB_DIR/scripts/automation_cli.py" scope-backfill "$@"` and make new distill pages use `infer_scope()`.

```python
def apply_backfill(plan, ctx):
    written = []
    try:
        for entry in plan.entries:
            if fingerprint(entry.path) != entry.planned_fingerprint:
                report(entry, "concurrent_change"); continue
            save_before_image(ctx, entry)
            atomic_replace_same_dir(entry.path, patch_frontmatter(parse_page(entry.path), entry.assignment.fields()))
            written.append(entry)
    except Exception:
        for entry in reversed(written): restore_before_image(ctx, entry)
        raise
    return finalize_backfill_report(ctx, plan)
```

- [ ] **Step 5: Run the 12,900-page test, safety cases, and commit**

Run: `pytest tests/test_scope_backfill.py -q && bash -n memory-hub.sh scripts/distill.sh`

Expected: six priorities, tie, dry-run, cursor, idempotency, concurrent skip, symlink escape, malformed frontmatter, rollback, and report assertions PASS; exit 0.

```bash
git add scripts/automation_core/scope.py scripts/automation_cli.py memory-hub.sh scripts/distill.sh tests/test_scope_backfill.py
git commit -m "feat: add deterministic scope backfill"
```

### Task 3: Migrate the atomic index to scope and lifecycle metadata

**Files:**
- Create: `scripts/automation_core/indexer.py`
- Create: `tests/test_index_scope.py`
- Modify: `scripts/index.sh`
- Modify: `tests/test_index_atomic.py`

**Interfaces:**
- Consumes: `parse_page()`, `infer_scope()` fallback rules, controlled wiki/data paths.
- Produces: `detect_index_schema(db: Path) -> IndexSchema`, `build_index(wiki: Path, destination: Path, include_raw: bool, failure_after: int | None = None) -> IndexBuild`, `atomic_rebuild_index(wiki: Path, data: Path, include_raw: bool = False) -> IndexBuild`, and `load_page_records(db: Path, paths: Sequence[str] | None = None) -> dict[str, IndexedPage]`.

- [ ] **Step 1: Add failing compatibility and atomic-hash tests**

```python
def test_new_columns_and_old_page_fallback(fixture):
    result = atomic_rebuild_index(fixture.wiki, fixture.data)
    with sqlite3.connect(result.db_path) as db:
        assert columns(db, "pages") == ["path", "title", "type", "tags", "abstract", "content", "scope", "scope_id", "scope_confidence", "status"]
        assert columns(db, "meta") == ["path", "updated", "last_verified", "valid_at", "invalid_at"]
        assert db.execute("select scope,scope_id,scope_confidence,status from pages where path='legacy.md'").fetchone() == ("project", "default-project", "low", "active")

def test_failed_rebuild_preserves_legacy_db_hash(fixture):
    before = sha256(fixture.data / "index.db")
    with pytest.raises(InjectedFailure):
        atomic_rebuild_index(fixture.wiki, fixture.data, failure_after=1)
    assert sha256(fixture.data / "index.db") == before
```

- [ ] **Step 2: Run index tests and verify new schema assertions fail**

Run: `pytest tests/test_index_scope.py tests/test_index_atomic.py -q`

Expected: FAIL because `indexer.py` and new columns do not exist; exit 1.

- [ ] **Step 3: Implement schema detection and validated temporary index construction**

```python
PAGES_DDL = """CREATE VIRTUAL TABLE pages USING fts5(path,title,type,tags,abstract,content,scope,scope_id,scope_confidence,status,tokenize='trigram')"""
META_DDL = """CREATE TABLE meta(path TEXT PRIMARY KEY,updated TEXT,last_verified TEXT,valid_at TEXT,invalid_at TEXT)"""

def detect_index_schema(db: Path) -> IndexSchema:
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as con:
        page_cols = tuple(row[1] for row in con.execute("pragma table_info(pages)"))
        meta_cols = tuple(row[1] for row in con.execute("pragma table_info(meta)"))
    return IndexSchema(page_cols=page_cols, meta_cols=meta_cols,
                       supports_scope="scope" in page_cols, supports_validity="valid_at" in meta_cols)
```

Copy only vector rows whose paths still exist, validate pages/meta counts, non-empty scope ids, legal statuses, and no stale vectors, `fsync` the temporary DB and parent directory, then `os.replace()`.

- [ ] **Step 4: Convert `index.sh` into a thin adapter and preserve failure injection**

`index.sh` parses `--with-raw` only and invokes `python3 -m scripts.automation_core.indexer`; map `MEMORY_HUB_INDEX_FAIL_AFTER` to the explicit test-only failure hook and retain the human count line.

```bash
args=(--wiki "$WIKI" --data "$DATA_DIR")
[[ "$WITH_RAW" == 1 ]] && args+=(--with-raw)
exec python3 -m scripts.automation_core.indexer "${args[@]}"
```

- [ ] **Step 5: Run focused and old regression tests, inspect SQLite, and commit**

Run: `pytest tests/test_index_scope.py tests/test_index_atomic.py -q && bash -n scripts/index.sh`

Expected: old/new schema, fallback, vector pruning, failure hash, non-UTF-8 rejection, and swap assertions PASS; exit 0.

```bash
git add scripts/automation_core/indexer.py scripts/index.sh tests/test_index_scope.py tests/test_index_atomic.py
git commit -m "feat: index scope and lifecycle metadata"
```

### Task 4: Implement two-stage L0 plus LLM/local query planning

**Files:**
- Create: `scripts/automation_core/query_planner.py`
- Create: `tests/test_query_planner.py`

**Interfaces:**
- Consumes: `SearchRequest`, `IndexedPage`, FTS/vector callables, injected `HttpTransport`.
- Produces: `plan_query(request: SearchRequest, recall: RecallBackend, transport: HttpTransport, audit: AuditSink) -> QueryPlan`, `sanitize_l0(text: str, max_chars: int = 320) -> str`, `local_expand(query: str, snippets: Sequence[L0Snippet], limit: int = 4) -> tuple[ExpansionTerm, ...]`, and `OpenAICompatiblePlanner.expand(query_hash: str, snippets: Sequence[L0Snippet], connect_timeout: float = 2.0, read_timeout: float = 6.0) -> PlannerResponse`.

- [ ] **Step 1: Write failing L0 limits, LLM, fallback, and secret-redaction tests**

```python
@pytest.mark.parametrize("failure,reason", [(HttpStatus(429), "http_429"), (ReadTimeout(), "read_timeout"), (BadJson(), "invalid_json")])
def test_llm_failure_degrades_once_to_local(failure, reason, fixture):
    transport = FakeTransport([failure])
    plan = plan_query(SearchRequest("fixture lifecycle"), fixture.recall, transport, fixture.audit)
    assert plan.planner == "local"
    assert plan.fallback_reason == reason
    assert transport.calls == 1
    assert all(2 <= len(term.text) <= 64 for term in plan.expansions)
    assert fixture.audit.last.keys() >= {"query_hash", "candidate_paths", "expansions", "planner", "fallback_reason", "latency_ms", "final_hits"}
    assert "fixture lifecycle" not in fixture.audit.raw_text
```

- [ ] **Step 2: Run the planner tests and verify import failure**

Run: `pytest tests/test_query_planner.py -q`

Expected: FAIL importing `query_planner`; exit 1.

- [ ] **Step 3: Implement capped L0 recall and safe snippet selection**

```python
def collect_l0(query: str, recall: RecallBackend) -> tuple[L0Snippet, ...]:
    candidates = dedupe_by_path((*recall.fts(query, 12), *recall.vector(query, 12)))
    return tuple(L0Snippet(c.path, sanitize_l0(c.abstract or first_nonempty_paragraph(c.content), 320))
                 for c in candidates[:6])
```

Each backend exception becomes a source-specific warning; both empty sources produce `planner="original-only"`. Redaction replaces credential-like tokens and private-home fragments before any transport call.

- [ ] **Step 4: Implement one-attempt LLM validation and deterministic local expansion**

The LLM response parser accepts exactly a JSON array of `{term: str, confidence: float}` objects, clamps neither invalid values nor excess terms, and discards the whole response when its shape is invalid. `local_expand()` scores Chinese 2–8-character n-grams and English stems by BM25/term-frequency/query-cooccurrence, then applies the same central validator and stable `(-score, term)` ordering.

```python
def validate_expansions(raw, query):
    if not isinstance(raw, list) or len(raw) > 4: return ()
    checked = [ExpansionTerm(str(x["term"]), float(x["confidence"])) for x in raw]
    return tuple(sorted(dedupe_terms(t for t in checked if valid_term(t.text, query)),
                        key=lambda t: (-t.confidence, t.text)))
```

- [ ] **Step 5: Run all planner paths and commit**

Run: `pytest tests/test_query_planner.py -q`

Expected: L0 12/6/320 caps, successful LLM, 401/403/429/5xx/timeout/network/bad-json/empty fallback, local Chinese/English extraction, original-only, redaction, and audit assertions PASS; exit 0.

```bash
git add scripts/automation_core/query_planner.py tests/test_query_planner.py
git commit -m "feat: add two-stage query planner"
```

### Task 5: Centralize weighted RRF and lifecycle-aware ranking

**Files:**
- Create: `scripts/automation_core/ranker.py`
- Create: `tests/test_ranker_lifecycle.py`
- Modify: `scripts/fuse.py`

**Interfaces:**
- Consumes: `QueryPlan`, `SearchRequest`, `IndexedPage`, ranked lists from recall backends.
- Produces: `rank_results(request: SearchRequest, plan: QueryPlan, recalls: Mapping[str, Sequence[RecallHit]], pages: Mapping[str, IndexedPage], metrics: MetricSink) -> tuple[SearchResult, ...]`, `resolve_successor(path: str, pages: Mapping[str, IndexedPage]) -> LifecycleResolution`, and compatibility renderer `render_human(results: Sequence[SearchResult]) -> str`.

- [ ] **Step 1: Write failing weight, tie, scope, successor, and error-fill tests**

```python
def test_rrf_weights_scope_and_successor_replacement():
    ranked = rank_results(SearchRequest("q", top=2, scope=Scope.PROJECT, scope_id="p"), plan(original_weight=.70, expansions=[("x", .30)]), recalls(), pages_with_successor(), metrics())
    assert [r.path for r in ranked] == ["new.md", "active.md"]
    assert ranked[0].rank_reason["via"] == "successor"
    assert all((r.scope.value, r.scope_id) == ("project", "p") for r in ranked)

def test_cycle_is_only_used_as_shortfall_and_emits_metric():
    ranked = rank_results(SearchRequest("q", top=2), plan(), cycle_recalls(), cycle_pages(), metrics)
    assert [r.status for r in ranked] == ["active", "lifecycle_error"]
    assert metrics.value("memory_hub_lifecycle_cycle_total") == 1
```

- [ ] **Step 2: Run ranker tests and verify missing implementation**

Run: `pytest tests/test_ranker_lifecycle.py -q`

Expected: FAIL importing `rank_results`; exit 1.

- [ ] **Step 3: Implement weighted RRF and stable tie keys**

```python
def rank_results(request, plan, recalls, pages, metrics):
    weights = normalized_weights(plan)  # original=1.0 if no valid expansion; else 0.70 + normalized 0.30
    scores = accumulate_rrf(recalls, weights, k=60)
    resolved = resolve_and_dedupe(scores, pages, metrics)
    eligible = apply_scope_filter(resolved, request.scope, request.scope_id)
    ordered = sorted(eligible.valid, key=lambda h: (-h.score, -confidence_rank(h.page.scope_confidence),
                                                     -timestamp_rank(h.page), h.path))
    return tuple(to_search_result(h) for h in (ordered + eligible.lifecycle_errors)[:request.top])
```

Include status-first ordering before confidence and timestamp, record component ranks/weights/status/successor path in `rank_reason`, and never let a deprecated predecessor and its resolved successor both appear.

- [ ] **Step 4: Make `fuse.py` call the ranker and retain its selfcheck**

Keep `MH_FUSE_SELFCHECK=1` but extend it to assert 0.70/0.30 normalization, stable path tie breaking, and successor replacement using in-memory fixtures.

```python
def _selfcheck():
    assert normalized_weights(plan_with_expansion()) == {"original": .70, "expanded": .30}
    assert [r.path for r in rank_results(selfcheck_request(), selfcheck_plan(), selfcheck_recalls(), selfcheck_pages(), NullMetrics())] == ["new.md", "active.md"]
    print("fuse._selfcheck OK")
```

- [ ] **Step 5: Run ranker and fuse selfcheck, then commit**

Run: `pytest tests/test_ranker_lifecycle.py -q && MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py`

Expected: all ranking cases PASS and stdout contains `fuse._selfcheck OK`; exit 0.

```bash
git add scripts/automation_core/ranker.py scripts/fuse.py tests/test_ranker_lifecycle.py
git commit -m "feat: centralize lifecycle-aware ranking"
```

### Task 6: Build the shared search and ask service plus thin CLI adapters

**Files:**
- Create: `scripts/automation_core/service.py`
- Modify: `scripts/automation_cli.py`
- Modify: `scripts/search.sh`
- Modify: `scripts/ask.sh`
- Modify: `tests/test_search_semantics.py`

**Interfaces:**
- Consumes: `plan_query()` from Task 4, `rank_results()` from Task 5, `SearchRequest` from Task 1.
- Produces: `MemoryService.search(request: SearchRequest) -> SearchResponse`, `MemoryService.ask_context(request: SearchRequest) -> AskContext`, `parse_search_args(argv: Sequence[str]) -> SearchRequest`, and human/JSON renderers.

- [ ] **Step 1: Add failing default-on, independent-disable, validation, and ask-path tests**

```python
def test_cli_defaults_and_ask_share_ranked_paths(cli_fixture):
    search = cli_fixture.run("search", "fixture", "--json")
    ask = cli_fixture.run("ask", "fixture", "--json")
    assert search.json["plan"]["expand"] is True and search.json["plan"]["fuse"] is True
    assert ask.json["context_paths"] == [item["path"] for item in search.json["results"][:len(ask.json["context_paths"])]]
    disabled = cli_fixture.run("search", "fixture", "--no-fuse", "--json")
    assert disabled.json["plan"] == {**disabled.json["plan"], "fuse": False, "expand": True}
```

- [ ] **Step 2: Run search semantics and verify defaults/path parity fail**

Run: `pytest tests/test_search_semantics.py -q`

Expected: FAIL because expansion is not default and ask has a separate retrieval path; exit 1.

- [ ] **Step 3: Implement the single composition and structured response**

```python
class MemoryService:
    def search(self, request: SearchRequest) -> SearchResponse:
        checked = validate_request(request)
        plan = plan_query(checked, self.recall, self.transport, self.audit)
        recalls = self.recall.final_recall(checked.query, plan.expansions, fuse=checked.fuse)
        results = rank_results(checked, plan, recalls, self.index.pages(), self.metrics)
        self.audit.finish(plan, final_hits=len(results))
        return SearchResponse(request=checked, plan=plan.public_explain(), results=results)

    def ask_context(self, request: SearchRequest) -> AskContext:
        response = self.search(request)
        return AskContext(results=response.results, pages=self.pages.load([r.path for r in response.results]))
```

- [ ] **Step 4: Replace search/ask shell logic with thin adapters**

Both scripts `exec python3 "$HUB_DIR/scripts/automation_cli.py" search|ask "$@"`; support `--fuse`, `--no-fuse`, `--expand`, `--no-expand`, `--scope`, `--scope-id`, `--explain`, `--json`, `--top`. Reject query over 500, invalid scope, missing scope id, and malformed top with exit 2.

```bash
# scripts/search.sh
exec python3 "$HUB_DIR/scripts/automation_cli.py" search "$@"
# scripts/ask.sh
exec python3 "$HUB_DIR/scripts/automation_cli.py" ask "$@"
```

- [ ] **Step 5: Run CLI regressions and commit**

Run: `pytest tests/test_search_semantics.py tests/test_query_planner.py tests/test_ranker_lifecycle.py -q && bash -n scripts/search.sh scripts/ask.sh`

Expected: legacy line rendering, JSON fields, default-on flags, independent disables, top clamp, scope validation, and ask ordered paths PASS; exit 0.

```bash
git add scripts/automation_core/service.py scripts/automation_cli.py scripts/search.sh scripts/ask.sh tests/test_search_semantics.py
git commit -m "feat: unify search and ask service"
```

### Task 7: Enforce CLI, MCP, REST, inject, and export parity

**Files:**
- Create: `tests/test_surface_parity.py`
- Modify: `mcp/server.py`
- Modify: `scripts/server.py`
- Modify: `scripts/inject.sh`
- Modify: `scripts/export.py`
- Modify: `tests/test_export.py`

**Interfaces:**
- Consumes: `MemoryService.search()`/`ask_context()` and `SearchRequest` from Task 6.
- Produces: MCP signatures `memory_search(query: str, top: int = 10, expand: bool = True, fuse: bool = True, scope: str | None = None, scope_id: str | None = None, explain: bool = False) -> dict` and `memory_ask(question: str, top: int = 5, expand: bool = True, fuse: bool = True, scope: str | None = None, scope_id: str | None = None, explain: bool = False) -> dict`; REST JSON for `/search` and `/ask`; `collect_pages(..., scope_filter: str | None, scope_id_filter: str | None)`.

- [ ] **Step 1: Write failing same-fixture surface parity tests**

```python
def test_cli_mcp_rest_search_and_ask_order_match(surface_fixture):
    expected = surface_fixture.cli_search(scope="project", scope_id="fixture-project")
    mcp = surface_fixture.mcp_search(scope="project", scope_id="fixture-project")
    rest = surface_fixture.rest_get("/search?scope=project&scope_id=fixture-project")
    assert paths(expected) == paths(mcp) == paths(rest)
    assert paths(expected)[0] == paths(surface_fixture.rest_get("/ask?q=fixture"))[0]
    for response in (expected, mcp, rest):
        assert set(response["results"][0]) >= {"path", "score", "status", "scope", "scope_id", "rank_reason"}
```

- [ ] **Step 2: Run parity/export tests and verify defaults and fields fail**

Run: `pytest tests/test_surface_parity.py tests/test_export.py -q`

Expected: FAIL because MCP/REST default `expand` is false, REST is text, and export lacks lifecycle fields; exit 1.

- [ ] **Step 3: Adapt MCP and REST directly to the shared service**

Remove subprocess parsing for search/ask. REST boolean parsing accepts only `0|1|true|false|yes|no`, defaults both flags to true, clamps top, and returns HTTP 400 for invalid query/scope combinations. MCP returns an object rather than a formatted string and preserves every `SearchResult` field.

```python
def memory_search(query: str, top: int = 10, expand: bool = True, fuse: bool = True,
                  scope: str | None = None, scope_id: str | None = None,
                  explain: bool = False) -> dict:
    request = SearchRequest(query, top, fuse, expand, parse_scope(scope), scope_id, explain)
    return service().search(request).to_dict()
```

- [ ] **Step 4: Add scope-aware inject/export without changing cross-scope defaults**

`inject.sh` parses `--scope/--scope-id`, calls `automation_cli.py search --json --top 5`, and renders returned paths. `collect_pages()` filters only when scope is provided and adds `scope`, `scope_id`, `scope_confidence`, `status`, `valid_at`, and `invalid_at` at top level so JSONL/JSON/Markdown preserve them.

```python
if scope_filter and (page["scope"], page["scope_id"]) != (scope_filter, scope_id_filter):
    continue
page.update({key: frontmatter.get(key, default)
             for key, default in LIFECYCLE_EXPORT_DEFAULTS.items()})
```

- [ ] **Step 5: Run all surface parity cases and commit**

Run: `pytest tests/test_surface_parity.py tests/test_export.py tests/test_server_loopback.py -q && bash -n scripts/inject.sh`

Expected: CLI/MCP/REST search and ask path sequences/top-1 match, expand defaults true, explicit false works, scope filters match, structured fields are complete, loopback security remains PASS; exit 0.

```bash
git add mcp/server.py scripts/server.py scripts/inject.sh scripts/export.py tests/test_surface_parity.py tests/test_export.py
git commit -m "feat: align memory surfaces"
```

### Task 8: Implement automatic successor planning and recoverable pair writes

**Prerequisite and actual execution order:** Complete Task 9 before starting this task. Task 8 is still numbered 8, but runs after Task 9 and receives its sole root `TransactionContext`; it must not construct a journal, lock, or rollback coordinator of its own.

**Files:**
- Create: `scripts/automation_core/lifecycle.py`
- Create: `tests/test_successor_lifecycle.py`
- Modify: `scripts/publish.sh`

**Interfaces:**
- Consumes: `PageDocument`, the completed Task 9 `TransactionContext`, index records, embedding/lexical scorers, atomic frontmatter patcher.
- Produces: `successor_plan(new_page: PageDocument, candidates: Sequence[PageDocument], embeddings: EmbeddingBackend | None, now: datetime) -> SuccessorPlan`, `validate_successor_graph(plan: SuccessorPlan, pages: Mapping[str, PageDocument]) -> None`, `prepare_successor_pages(plan: SuccessorPlan, tx: TransactionContext) -> PreparedLifecycle`, `finalize_successor_after_index(prepared: PreparedLifecycle, tx: TransactionContext) -> LifecycleReport`, standalone `publish_successor_once(plan: SuccessorPlan, tx: TransactionContext, rebuild_index: Callable[[TransactionContext], IndexBuild]) -> LifecycleReport`, and `recover_lifecycle(tx: TransactionContext) -> RecoveryReport`. All use `tx.journal`; none allocates a second root journal.

- [ ] **Step 1: Write failing threshold, field, cycle, recovery, and replay tests**

```python
@pytest.mark.parametrize("similarity,entities,changed,decision", [(0.88, True, True, "successor"), (0.879, True, True, "related-not-successor"), (0.95, False, True, "related-not-successor"), (0.40, True, True, "independent")])
def test_successor_thresholds(similarity, entities, changed, decision, lifecycle_fixture):
    plan = successor_plan(lifecycle_fixture.new, [lifecycle_fixture.old], FakeEmbedding(similarity, entities, changed), lifecycle_fixture.now)
    assert plan.decision == decision

def raise_at(name: str) -> FailureHook:
    def hook(point: str) -> None:
        if point == name:
            raise InjectedFailure(point)
    return hook

@pytest.mark.parametrize("point", ["prepare.rename_new", "index.before_swap", "finalize.verify_pair"])
def test_transaction_hook_restores_pair_index_and_manifest_at_each_lifecycle_boundary(lifecycle_fixture, point):
    before = lifecycle_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True)
    tx = lifecycle_fixture.transaction(failure_hook=raise_at(point))
    with pytest.raises(InjectedFailure, match=point):
        lifecycle_fixture.publish_successor_once(lifecycle_fixture.plan, tx)
    assert lifecycle_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True) == before
    assert tx.journal.rollback_order == ("manifest", "index", "pages")

def test_prepare_replay_is_noop(lifecycle_fixture):
    tx = lifecycle_fixture.transaction()
    prepared = prepare_successor_pages(lifecycle_fixture.plan, tx)
    assert prepared.tx is tx
    assert tx.journal.state == "PREPARED"
    assert tx.journal.checkpoints[-1] == "OLD_RENAMED"
    lifecycle_fixture.swap_index_once(tx, prepared)
    first = finalize_successor_after_index(prepared, tx)
    second = finalize_successor_after_index(prepared, tx)
    assert (first.result, second.result) == ("committed", "idempotent_skip")

def test_prepare_never_swaps_and_finalize_requires_the_callers_index_checkpoint(lifecycle_fixture):
    tx = lifecycle_fixture.transaction()
    prepared = prepare_successor_pages(lifecycle_fixture.plan, tx)
    assert lifecycle_fixture.index_swap_calls == 0
    with pytest.raises(LifecycleOrderError):
        finalize_successor_after_index(prepared, tx)
    lifecycle_fixture.swap_index_once(tx, prepared)
    assert finalize_successor_after_index(prepared, tx).result == "committed"
    assert lifecycle_fixture.index_swap_calls == 1

def test_standalone_publish_wrapper_swaps_exactly_once(lifecycle_fixture):
    result = publish_successor_once(lifecycle_fixture.plan, lifecycle_fixture.transaction(), lifecycle_fixture.rebuild_index)
    assert result.result == "committed"
    assert lifecycle_fixture.index_swap_calls == 1
```

- [ ] **Step 2: Run lifecycle tests and verify module failure**

Run: `pytest tests/test_successor_lifecycle.py -q`

Expected: FAIL importing `lifecycle`; exit 1.

- [ ] **Step 3: Implement candidate narrowing, scoring, canonical links, and graph validation**

Filter candidates by equal `(scope, scope_id)` and project/topic set. Use embedding decision thresholds exactly; missing embeddings call `lexical_successor_score()` and supersede only at `>= 0.92`. Canonicalize links using resolved relative paths and DFS every proposed edge before writes.

```python
def successor_plan(new_page, candidates, embeddings, now):
    eligible = [p for p in candidates if same_namespace_and_topic(new_page, p)]
    scored = [score_candidate(new_page, p, embeddings) for p in eligible]
    winners = [s for s in scored if s.semantic >= .88 and s.entities_match and s.comparable_change]
    if not embeddings:
        winners = [s for s in scored if s.lexical >= .92 and s.entities_match and s.comparable_change]
    return build_successor_or_independent_plan(new_page, stable_best(winners), scored, now)
```

- [ ] **Step 4: Implement PREPARED page preparation, post-index finalization, and recovery**

```python
def prepare_successor_pages(plan, tx):
    tx.journal.register_lifecycle(plan)
    tx.inject("prepare.before_images")
    tx.journal.save_before_images(plan.paths)  # A/B pages and lifecycle checkpoint
    tx.journal.checkpoint("PREPARED")
    tx.journal.write_verified_temp(plan.new_path, active_patch(plan))
    tx.journal.checkpoint("NEW_TEMP_VERIFIED")
    tx.journal.write_verified_temp(plan.old_path, deprecated_patch(plan))
    tx.journal.checkpoint("OLD_TEMP_VERIFIED")
    tx.inject("prepare.rename_new"); tx.journal.rename_new(); tx.journal.checkpoint("NEW_RENAMED")
    tx.inject("prepare.rename_old"); tx.journal.rename_old(); tx.journal.checkpoint("OLD_RENAMED")
    return PreparedLifecycle(tx, plan)

def finalize_successor_after_index(prepared, tx):
    require_same_transaction(prepared.tx, tx)
    require_checkpoints(tx.journal, "INDEX_SWAPPED")
    tx.inject("finalize.verify_pair")
    verify_pair_hashes_and_frontmatter(prepared.plan)
    tx.journal.checkpoint("COMMITTED")
    return LifecycleReport("committed")

def publish_successor_once(plan, tx, rebuild_index):
    try:
        prepared = prepare_successor_pages(plan, tx)
        tx.inject("index.before_swap")
        rebuild_index(tx); tx.journal.checkpoint("INDEX_SWAPPED")
        tx.inject("index.after_swap")
        return finalize_successor_after_index(prepared, tx)
    except Exception:
        rollback_transaction(tx); raise
```

Task 11 owns the `maintain` caller: its `publish_pages_lifecycle` stage invokes only `prepare_successor_pages(plan, tx)`, and its sole `index_swap` stage invokes `atomic_rebuild_index(tx)` once, registers the index before-image in `tx.journal`, records `INDEX_SWAPPED`, then calls `finalize_successor_after_index(prepared, tx)`. The standalone `publish_successor_once()` wrapper is only for manual publish; it is strictly `prepare → one index swap → finalize`, using the same caller-provided `tx`. Recovery is exclusively `rollback_transaction(tx)`: it walks the root journal in reverse to restore manifest (if written), index, then pages/frontmatter; it unstages only operation-owned paths and never touches user baseline staged paths. It records rollback results and rejects overwrite when predecessor already points to a third page.

- [ ] **Step 5: Route publish through successor planning, run tests, and commit**

Run: `pytest tests/test_successor_lifecycle.py tests/test_candidate_flow.py -q && bash -n scripts/publish.sh`

Expected: high/mid/low/missing-embedding decisions, fields/timestamps, body preservation, cycle/path rejection, prepare/index/finalize injected failures, no swap during prepare, finalize rejection before `INDEX_SWAPPED`, exactly one swap for maintain and standalone publish, restart recovery, idempotent replay, concurrent successor, and old same-name non-overwrite PASS; exit 0.

```bash
git add scripts/automation_core/lifecycle.py scripts/publish.sh tests/test_successor_lifecycle.py
git commit -m "feat: add recoverable successor lifecycle"
```

### Task 9: Add the automation lock, operation journal, rollback coordinator, and exact Git staging

**Files:**
- Create: `scripts/automation_core/operation.py`
- Create: `tests/test_operation_safety.py`

**Interfaces:**
- Consumes: Task 1 immutable `OperationContext` and controlled-path helpers only. It deliberately has no dependency on Task 8 or prepared lifecycle state, because Task 8 runs after this transaction API is complete.
- Produces: `AutomationLock.acquire(data: Path, operation: OperationContext, now_monotonic: Callable[[], float]) -> AutomationLock`, `begin_transaction(operation: OperationContext, baseline: GitBaseline, failure_hook: FailureHook | None = None) -> TransactionContext`, `stage_exact(repo: Path, tx: TransactionContext, whitelist: Sequence[OwnedPath]) -> StageReport`, `commit_exact(repo: Path, tx: TransactionContext, stage: StageReport) -> CommitReport`, and `rollback_transaction(tx: TransactionContext) -> RollbackReport`. `begin_transaction()` is the sole constructor of the one root `OperationJournal` for an operation.

- [ ] **Step 1: Write failing live/stale lock, pre-staged safe-failure, and exact-stage tests**

```python
def test_live_lock_exits_75_and_stale_dead_lock_is_archived(operation_fixture):
    operation_fixture.lock(pid=os.getpid(), age_minutes=31)
    assert operation_fixture.acquire_process().returncode == 75
    operation_fixture.lock(pid=999_999, age_minutes=31)
    acquired = operation_fixture.acquire()
    assert acquired.operation_id == operation_fixture.operation_id
    assert list((operation_fixture.data / "locks/archive").iterdir())

def test_preexisting_staged_safely_blocks_operation_without_index_or_git_mutation(operation_fixture):
    operation_fixture.stage_user_file("user.md")
    before_index = sha256(operation_fixture.data / "index.db")
    tx = operation_fixture.transaction()
    report = stage_exact(operation_fixture.repo, tx, [operation_fixture.owned("new.md")])
    assert report.result == "preexisting_staged"
    assert operation_fixture.cached_paths() == ["user.md"]
    assert sha256(operation_fixture.data / "index.db") == before_index
    assert operation_fixture.git_calls("add", "commit") == []

def test_empty_baseline_requires_cached_to_equal_verified_whitelist(operation_fixture):
    tx = operation_fixture.transaction()
    report = stage_exact(operation_fixture.repo, tx, [operation_fixture.owned("new.md")])
    assert report.result == "exact"
    assert operation_fixture.cached_paths() == ["new.md"]
    operation_fixture.stage_racing_file("racer.md")
    failed = stage_exact(operation_fixture.repo, operation_fixture.next_transaction(), [operation_fixture.owned("next.md")])
    assert failed.result == "whitelist_mismatch"
    assert "next.md" not in operation_fixture.cached_paths()
```

- [ ] **Step 2: Run operation tests and verify missing lock/journal implementation**

Run: `pytest tests/test_operation_safety.py -q`

Expected: FAIL importing `operation`; exit 1.

- [ ] **Step 3: Implement atomic-mkdir lock and sanitized operation journal**

The lock directory contains JSON with PID, hostname, operation id, monotonic start, and UTC start. On collision, parse without trusting contents; archive only if PID is absent and age is strictly greater than 30 minutes. The single root `OperationJournal` records baseline staged/unstaged sets, relative paths, before/after hashes, checkpoints, safe errors, and never content. `begin_transaction()` creates it once, wraps it with its immutable operation and optional test-only hook, and all callers receive only the resulting explicit `TransactionContext`.

```python
def begin_transaction(operation, baseline, failure_hook=None):
    journal = OperationJournal.prepare(operation, baseline)  # the only root allocation
    return TransactionContext(operation=operation, journal=journal, failure_hook=failure_hook)

@classmethod
def acquire(cls, data, operation, now_monotonic):
    try: (data / "locks/automation.lock").mkdir(parents=True)
    except FileExistsError:
        holder = read_untrusted_holder(data)
        if pid_alive(holder.pid) or holder.age_minutes <= 30: raise LockBusy(exit_code=75)
        archive_stale_lock(data, holder)
        (data / "locks/automation.lock").mkdir()
    write_lock_metadata(data, operation, now_monotonic())
    return cls(data, operation)
```

- [ ] **Step 4: Implement baseline gate, exact stage comparison, and narrow unstage**

```python
def stage_exact(repo, tx, whitelist):
    baseline = set(tx.journal.baseline.staged)
    if baseline:
        return StageReport("preexisting_staged", tuple(sorted(baseline)), ())
    verified = {p.relative for p in whitelist if sha256(repo / p.relative) == p.after_hash}
    if verified != {p.relative for p in whitelist}:
        return StageReport("whitelist_hash_mismatch", (), tuple(sorted(verified)))
    staged_by_operation = set()
    for rel in sorted(verified):
        git(repo, "add", "--", rel)
        staged_by_operation.add(rel)
    cached = set(git_lines(repo, "diff", "--cached", "--name-only"))
    if cached != verified:
        for rel in sorted(staged_by_operation):
            git(repo, "restore", "--staged", "--", rel)
        return StageReport("whitelist_mismatch", tuple(sorted(cached)), tuple(sorted(verified)))
    return StageReport("exact", tuple(sorted(cached)), tuple(sorted(verified)))
```

`commit_exact()` is unreachable unless `stage_exact()` returned `exact`; `preexisting_staged`, hash mismatch, or cached mismatch performs no commit. A preexisting staged baseline is therefore a safe failure before any index mutation, staging, or commit. Commit only verified operation paths, using `chore(wiki): memory-hub maintain <operation-id>`; report `not-a-repository` without failing publication. `rollback_transaction(tx)` replays the sole root journal strictly in reverse mutation order (manifest if written, index, then pages/frontmatter), then unstages only journal-owned paths; it must preserve all baseline staged paths byte-for-byte.

- [ ] **Step 5: Run lock/Git/rollback tests and commit**

Run: `pytest tests/test_operation_safety.py -q`

Expected: live lock exit 75, dead/young lock rejection, stale archive, path escape, `preexisting_staged` with byte-identical index and no add/commit, empty-baseline exact whitelist, racing cached mismatch with operation-only unstage, non-repo, rollback hash guard, permissions, and prohibited-command assertions PASS; exit 0.

```bash
git add scripts/automation_core/operation.py tests/test_operation_safety.py
git commit -m "feat: guard automatic operations"
```

### Task 10: Implement deterministic cross-day clustering and atomic manifest updates

**Files:**
- Create: `scripts/automation_core/cluster.py`
- Create: `tests/test_cluster_maintain.py`

**Interfaces:**
- Consumes: normalized `Observation`, `ScopeAssignment`, `successor_plan()`, and the caller's explicit `TransactionContext`.
- Produces: `scan_observations(staging: Path, manifest: ClusterManifest) -> tuple[ClusterObservation, ...]`, `cluster_observations(observations: Sequence[ClusterObservation], embeddings: EmbeddingBackend | None) -> tuple[ClusterPlan, ...]`, `render_merge_page(cluster: ClusterPlan, now: datetime) -> bytes`, `load_manifest(path: Path) -> ClusterManifest`, and `commit_manifest(path: Path, update: ManifestEntry, tx: TransactionContext) -> ManifestResult`. It uses the existing `tx.journal` rather than allocating a cluster journal.

- [ ] **Step 1: Write failing eligibility, embedding/local, key, ordered manifest, and rollback tests**

```python
def test_cross_day_cluster_key_and_manifest_replay(cluster_fixture):
    plans = cluster_observations(cluster_fixture.three_members_two_days, FakeEmbedding(cosine=.80))
    expected_key = hashlib.sha256("id-1\nid-2\nid-3".encode()).hexdigest()[:16]
    assert [(p.key, len(p.members), p.method) for p in plans] == [(expected_key, 3, "embedding")]
    first = cluster_fixture.publish(plans[0])
    second = cluster_fixture.maintain_again()
    assert first.manifest.entries[expected_key].page_path == first.page_path
    assert second.results[0] == "manifest_skip"
    assert cluster_fixture.cluster_page_count() == 1

@pytest.mark.parametrize("failure", ["publish_pages_lifecycle", "index_swap", "lint", "manifest_commit", "archive", "stage_exact"])
def test_failed_maintain_restores_preoperation_pages_frontmatter_index_and_manifest(cluster_fixture, failure):
    before = cluster_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True)
    report = cluster_fixture.fail_maintain_at(failure)
    assert report.failed_stage == failure
    assert cluster_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True) == before
    if failure in {"index_swap", "lint"}:
        assert "MANIFEST_COMMITTED" not in report.checkpoints

def test_manifest_hook_uses_the_operation_transaction_and_reverses_manifest_then_index_then_pages(cluster_fixture):
    before = cluster_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True)
    tx = cluster_fixture.transaction(failure_hook=raise_at("manifest.before_replace"))
    with pytest.raises(InjectedFailure, match="manifest.before_replace"):
        cluster_fixture.maintain(tx)
    assert cluster_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True) == before
    assert tx.journal.rollback_order == ("manifest", "index", "pages")
```

- [ ] **Step 2: Run cluster tests and verify module failure**

Run: `pytest tests/test_cluster_maintain.py -q`

Expected: FAIL importing `cluster`; exit 1.

- [ ] **Step 3: Implement sanitized scan and deterministic clustering**

Scan only `observations-[0-9]{8}-[0-9]{6}.jsonl`, validate id/project/text/time, sanitize before storage, hash source ids, bucket by normalized project scope id, then perform stable agglomeration ordered by `(-similarity, left_key, right_key)`. Reject clusters with fewer than 3 members, fewer than 2 dates, or span over 45 days; fall back to 3-gram Jaccard at `>= 0.52` only when embeddings are unavailable.

```python
def cluster_observations(observations, embeddings):
    buckets = bucket_by_project_scope(valid_observations(observations))
    plans = []
    for scope_id, members in sorted(buckets.items()):
        clusters = stable_agglomerate(members, embeddings, cosine=.80, jaccard=.52)
        plans.extend(build_cluster(c) for c in clusters
                     if len(c) >= 3 and utc_date_count(c) >= 2 and span_days(c) <= 45)
    return tuple(sorted(plans, key=lambda p: p.key))
```

- [ ] **Step 4: Render active merge pages and update manifest only after success gates**

The merge page frontmatter includes active status, project scope, cluster key, member count, first/last UTC time, method, hashed ids, uncertainty label, L0 abstract, and `valid_at`. `commit_manifest()` is called only after the orchestrator checkpoints `PAGES_LIFECYCLE_PUBLISHED`, `INDEX_SWAPPED`, and `LINT_PASSED`; it requires `INDEX_SWAPPED` and `LINT_PASSED`, writes a mode-0600 temporary JSON file, `fsync`s file and parent, then renames and checkpoints `MANIFEST_COMMITTED`. It registers its before-image in the same transaction journal before `manifest.before_replace`; any later archive/stage failure uses the one inverse rollback to restore the unchanged pre-operation manifest, index, and pages/frontmatter.

```python
def commit_manifest(path, update, tx):
    require_checkpoints(tx.journal, "INDEX_SWAPPED", "LINT_PASSED")
    current = load_manifest(path)
    if update.cluster_key in current.entries: return ManifestResult("manifest_skip")
    tx.journal.save_before_image(path)
    current.entries[update.cluster_key] = update
    tx.inject("manifest.before_replace")
    atomic_json_replace(path, current.to_dict(), mode=0o600)
    tx.inject("manifest.after_replace")
    tx.journal.checkpoint("MANIFEST_COMMITTED")
    return ManifestResult("committed")
```

- [ ] **Step 5: Run deterministic and failure-injection cases, then commit**

Run: `pytest tests/test_cluster_maintain.py -q`

Expected: cross-day success, one-day/weak/old/span-invalid retention, embedding/local methods, project isolation, stable key, secret/path sanitation, manifest replay, authoritative `INDEX_SWAPPED → LINT_PASSED → MANIFEST_COMMITTED` ordering, and publish/index/lint/manifest/archive/stage failures that restore pages/frontmatter/index/manifest to pre-operation bytes PASS; exit 0.

```bash
git add scripts/automation_core/cluster.py tests/test_cluster_maintain.py
git commit -m "feat: cluster observations automatically"
```

### Task 11: Orchestrate default-auto run and maintain with safe/no-auto exits

**Files:**
- Create: `scripts/automation_core/orchestrator.py`
- Create: `tests/test_auto_modes.py`
- Modify: `scripts/automation_cli.py`
- Modify: `memory-hub.sh`

**Interfaces:**
- Consumes: all Tasks 1–10 services and exact existing stage commands through injected `StageRunner`.
- Produces: `parse_mode(command: str, argv: Sequence[str]) -> ModeOptions`, `run_pipeline(tx: TransactionContext, stages: StageRunner) -> OperationReport`, `maintain_pipeline(tx: TransactionContext, stages: StageRunner) -> OperationReport`, and CLI mode first-line contract. The CLI constructs exactly one `tx = begin_transaction(operation, baseline, failure_hook=None)` before either pipeline; every transactional stage receives that same `tx`.

- [ ] **Step 1: Write failing mode matrix, authoritative order, safe zero-write, and rollback tests**

```python
@pytest.mark.parametrize("command,args,expected", [("run", [], ("auto", True, True)), ("maintain", [], ("auto", True, True)), ("run", ["--safe"], ("safe", False, False)), ("run", ["--no-auto"], ("no-auto", False, False)), ("run", ["--no-auto", "--apply"], ("no-auto", True, False))])
def test_mode_matrix(command, args, expected):
    options = parse_mode(command, args)
    assert (options.mode.value, options.apply, options.commit) == expected

def test_safe_runs_plans_but_has_zero_fixture_writes(auto_fixture):
    before = auto_fixture.snapshot()
    report = auto_fixture.run("maintain", "--safe")
    assert report.stage_names == ["validate", "publish_pages_lifecycle", "index_swap", "lint", "atomic_manifest_commit", "archive", "exact_stage_commit"]
    assert auto_fixture.snapshot() == before

def test_maintain_order_and_checkpointed_failure_rollback(auto_fixture):
    assert MAINTAIN_ORDER == ("validate", "publish_pages_lifecycle", "index_swap", "lint", "atomic_manifest_commit", "archive", "exact_stage_commit")
    for failure in MAINTAIN_ORDER:
        before = auto_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True)
        report = auto_fixture.fail_stage(failure)  # uses the operation's one fixture tx
        assert report.failed_stage == failure
        assert auto_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True) == before
        assert report.checkpoints == MAINTAIN_CHECKPOINTS[:MAINTAIN_ORDER.index(failure)]

def test_preexisting_staged_stops_before_any_maintain_mutation(auto_fixture):
    auto_fixture.stage_user_file("user.md")
    before = auto_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True, cached=True)
    report = auto_fixture.run("maintain")
    assert report.result == "preexisting_staged"
    assert report.stage_names == []
    assert auto_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True, cached=True) == before
    assert auto_fixture.stage_calls() == []
    assert auto_fixture.commit_calls() == []

def test_maintain_performs_one_index_swap_then_finalizes_lifecycle(auto_fixture):
    report = auto_fixture.run("maintain")
    assert report.result == "committed"
    assert auto_fixture.index_swap_calls == 1
    assert auto_fixture.lifecycle_checkpoints()[-2:] == ("INDEX_SWAPPED", "COMMITTED")

def test_maintain_checkpoint_owners_emit_one_complete_nonduplicated_sequence(auto_fixture):
    report = auto_fixture.run("maintain")
    assert report.stage_checkpoints == (
        "VALIDATED", "PAGES_LIFECYCLE_PUBLISHED", "INDEX_SWAPPED", "LINT_PASSED",
        "MANIFEST_COMMITTED", "ARCHIVED", "STAGE_COMMITTED",
    )
    assert report.journal.checkpoint_names() == (
        "VALIDATED", "PAGES_LIFECYCLE_PUBLISHED", "INDEX_SWAPPED", "COMMITTED",
        "LINT_PASSED", "MANIFEST_COMMITTED", "ARCHIVED", "STAGE_COMMITTED",
    )
    assert len(report.journal.checkpoint_names()) == len(set(report.journal.checkpoint_names()))
    assert auto_fixture.lifecycle_checkpoints()[-2:] == ("INDEX_SWAPPED", "COMMITTED")

@pytest.mark.parametrize("point", ["prepare.rename_new", "index.before_swap", "finalize.verify_pair", "manifest.before_replace"])
def test_named_failure_hooks_use_one_root_journal_for_full_inverse_rollback(auto_fixture, point):
    before = auto_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True, cached=True)
    report = auto_fixture.run_with_failure_hook(raise_at(point))
    assert report.error == f"InjectedFailure:{point}"
    assert report.rollback_journal_id == report.operation_journal_id
    assert auto_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True, cached=True) == before
```

- [ ] **Step 2: Run mode tests and verify current defaults/order fail**

Run: `pytest tests/test_auto_modes.py -q`

Expected: FAIL because bare commands are not auto/apply/commit and maintain lacks the stage machine; exit 1.

- [ ] **Step 3: Implement mode parsing and explicit contradiction errors**

```python
def parse_mode(command, argv):
    ns = mode_parser(command).parse_args(argv)
    if ns.safe and (ns.apply or ns.commit):
        raise CliUsageError("--safe cannot be combined with --apply or --commit", exit_code=2)
    if ns.safe:
        return ModeOptions(Mode.SAFE, False, False)
    if ns.no_auto:
        return ModeOptions(Mode.NO_AUTO, bool(ns.apply), bool(ns.commit and ns.apply))
    return ModeOptions(Mode.AUTO, True, True)
```

For `maintain --no-auto`, run only deadlink/timestamp/link stages and write only with explicit `--apply`; for `run --no-auto`, preserve old dry-run unless `--apply` is explicit. Search flags remain unaffected.

- [ ] **Step 4: Implement locked stage machines and failure rollback**

`run_pipeline(tx, stages)` executes capture → distill → scope backfill → autolink → successor page preparation → one index swap → lifecycle finalization → archive → exact commit. The only `MAINTAIN_ORDER` is `validate → publish_pages_lifecycle → index_swap → lint → atomic_manifest_commit → archive → exact_stage_commit`; `validate` contains baseline/planning checks but makes no controlled mutation. `publish_pages_lifecycle` calls only `prepare_successor_pages(plan, tx)` and leaves `tx.journal` `PREPARED`. `index_swap` is the sole call to `atomic_rebuild_index(tx)`: after that one successful swap it records `INDEX_SWAPPED` on the same root journal and calls `finalize_successor_after_index(prepared, tx)`; it must not call any other swap. Safe mode calls each stage's `plan()`/`validate()` only. Checkpoint ownership is unique: the generic loop owns `VALIDATED`, `PAGES_LIFECYCLE_PUBLISHED`, `LINT_PASSED`, `ARCHIVED`, and `STAGE_COMMITTED`; `StageRunner.index_swap_once_and_finalize()` owns `INDEX_SWAPPED`; `commit_manifest()` owns `MANIFEST_COMMITTED`; and lifecycle finalization owns its distinct terminal `COMMITTED`. The two subfunction-owned stage outcomes set `checkpoint_owned=True`, so the generic loop records their operation-report checkpoint but never calls `tx.journal.checkpoint()` for them again. Every successful stage records exactly one of `VALIDATED`、`PAGES_LIFECYCLE_PUBLISHED`、`INDEX_SWAPPED`、`LINT_PASSED`、`MANIFEST_COMMITTED`、`ARCHIVED`、`STAGE_COMMITTED` in `operation-<id>.json`; a non-zero stage calls `rollback_transaction(tx)` and never advances. The one root journal restores operation before-images for pages/frontmatter/index/manifest in reverse order, so a failed manifest/archive/stage cannot leave a changed manifest or alter user preexisting staged paths. Print `mode=<mode> apply=<true|false> operation_id=<id>` before stage output.

```python
MAINTAIN_ORDER = (
    "validate", "publish_pages_lifecycle", "index_swap", "lint",
    "atomic_manifest_commit", "archive", "exact_stage_commit",
)
MAINTAIN_CHECKPOINTS = (
    "VALIDATED", "PAGES_LIFECYCLE_PUBLISHED", "INDEX_SWAPPED", "LINT_PASSED",
    "MANIFEST_COMMITTED", "ARCHIVED", "STAGE_COMMITTED",
)
```

```python
def maintain_pipeline(tx, stages):
    operation = tx.operation
    print(f"mode={operation.mode.value} apply={str(operation.apply).lower()} operation_id={operation.operation_id}")
    with AutomationLock.acquire(operation.data, operation, time.monotonic):
        if tx.journal.baseline.staged:
            return tx.journal.fail("preexisting_staged", StageOutcome.safe_failure("preexisting_staged"))
        for name, checkpoint in zip(MAINTAIN_ORDER, MAINTAIN_CHECKPOINTS, strict=True):
            if operation.mode is Mode.SAFE:
                outcome = stages.plan(name, tx)
            elif name == "index_swap":
                outcome = stages.index_swap_once_and_finalize(tx)
            else:
                outcome = stages.apply(name, tx)
            if not outcome.ok:
                rollback_transaction(tx); return tx.journal.fail(name, outcome)
            if not outcome.checkpoint_owned:
                tx.journal.checkpoint(checkpoint, outcome.safe_summary())
            record_operation_stage(checkpoint, outcome.safe_summary())
        return tx.journal.commit()

def run_pipeline(tx, stages):
    """Use the caller-created transaction; never allocate a journal here."""
    return execute_run_order(tx, stages, on_failure=rollback_transaction)
```

`StageOutcome` defaults `checkpoint_owned=False`; a stage may set it only when the named operation checkpoint was already durably written to the same root journal. `StageRunner.index_swap_once_and_finalize(tx)` registers the index before-image in `tx.journal`, invokes `tx.inject("index.before_swap")`, invokes `atomic_rebuild_index(tx)` exactly once, checkpoints the same root journal as `INDEX_SWAPPED`, invokes `tx.inject("index.after_swap")`, calls `finalize_successor_after_index(prepared, tx)`, and returns `StageOutcome.ok(checkpoint_owned=True)`. The `atomic_manifest_commit` adapter returns `StageOutcome.ok(checkpoint_owned=True)` only after `commit_manifest()` itself writes `MANIFEST_COMMITTED`. It never delegates index rebuilding to lifecycle prepare/finalize. Any error from prepare/index/finalize/manifest/archive/stage returns to the pipeline's one `rollback_transaction(tx)` path.

- [ ] **Step 5: Run all mode/failure cases and commit**

Run: `pytest tests/test_auto_modes.py tests/test_operation_safety.py tests/test_cluster_maintain.py -q && bash -n memory-hub.sh`

Expected: full mode matrix, exit 2 contradictions, one nonduplicated operation-stage sequence `VALIDATED → PAGES_LIFECYCLE_PUBLISHED → INDEX_SWAPPED → LINT_PASSED → MANIFEST_COMMITTED → ARCHIVED → STAGE_COMMITTED`, journal lifecycle tail `INDEX_SWAPPED → COMMITTED`, exactly one index swap before lifecycle finalization, first line, safe no writes, no-auto compatibility, failure stop with complete operation-owned rollback, and lock exit 75 PASS; exit 0.

```bash
git add scripts/automation_core/orchestrator.py scripts/automation_cli.py memory-hub.sh tests/test_auto_modes.py
git commit -m "feat: default run and maintain to full auto"
```

### Task 12: Add complete audit metrics, migration documentation, and help contracts

**Files:**
- Create: `tests/test_metrics_audit.py`
- Create: `docs/roadmap-full-auto-migration.md`
- Modify: `scripts/metrics.sh`
- Modify: `memory-hub.sh`
- Modify: `README.md`
- Modify: `scripts/README-portable-mcp.md`
- Modify: `SECURITY.md`

**Interfaces:**
- Consumes: operation/query/scope/lifecycle/cluster reports created by prior tasks.
- Produces: `collect_automation_metrics(data: Path) -> str`, documented CLI/API/MCP schema and three-phase migration (`read-new-schema code` → `atomic index migration` → `enable default auto`).

- [ ] **Step 1: Write failing Prometheus labels, redaction, report-name, and help tests**

```python
def test_metrics_and_reports_are_complete_and_sanitized(metrics_fixture):
    text = metrics_fixture.run_metrics()
    for family in REQUIRED_FAMILIES:
        assert f"memory_hub_{family}" in text
    for report in metrics_fixture.reports():
        assert str(metrics_fixture.home) not in report.read_text()
        assert "Authorization" not in report.read_text()
        assert "raw observation" not in report.read_text()

def test_help_discloses_new_defaults(help_output):
    assert "default: auto=on, apply=on, commit=on" in help_output
    assert "--safe" in help_output and "--no-auto" in help_output
```

- [ ] **Step 2: Run metrics/help tests and verify required families/default text fail**

Run: `pytest tests/test_metrics_audit.py -q`

Expected: FAIL because automation metric families and new help defaults are absent; exit 1.

- [ ] **Step 3: Implement report-derived Prometheus exposition**

Parse JSON/JSONL defensively, allow only known label values, count malformed report rows separately, and emit the exact families from sections 9 and 11. Never copy free-form error text into labels; use enumerated reason codes.

```python
def metric_label(value: str, allowed: frozenset[str]) -> str:
    return value if value in allowed else "unknown"

for row in iter_report_rows(data / "reports"):
    counters[row.metric][tuple((k, metric_label(v, LABELS[k])) for k, v in row.labels.items())] += row.value
```

- [ ] **Step 4: Write migration, release, API, MCP, and security documentation**

Document old/new behavior matrix, exact flags, all structured result fields, scope semantics (not ACL), successor/cluster thresholds, lock/journal/recovery, staged rollout and rollback, isolated fixture requirement, and the prohibition on live wiki/network/secrets in tests. Update `--help` text in the same commit.

```text
Deployment order:
1. Deploy readers that detect both legacy and new pages/meta columns.
2. Run `memory-hub.sh index` for an atomic schema migration.
3. Run `memory-hub.sh run --safe` and `maintain --safe`; only then enable bare-command auto defaults.
Rollback keeps the validated previous DB until the new index and safe reports pass.
```

- [ ] **Step 5: Run tests, scan documentation contracts, and commit**

Run: `pytest tests/test_metrics_audit.py -q && ./memory-hub.sh --help | rg -q 'auto=on, apply=on, commit=on' && ./memory-hub.sh --help | rg -q -- '--safe.*--no-auto'`

Expected: metric/report safety tests PASS and both help scans exit 0.

```bash
git add scripts/metrics.sh memory-hub.sh tests/test_metrics_audit.py docs/roadmap-full-auto-migration.md README.md scripts/README-portable-mcp.md SECURITY.md
git commit -m "docs: publish full-auto migration contract"
```

### Task 13: Add expand-on/off golden evaluation and full CI regression gates

**Files:**
- Create: `tests/fixtures/roadmap_full_auto/golden.jsonl`
- Create: `tests/fixtures/roadmap_full_auto/wiki/active.md`
- Create: `tests/fixtures/roadmap_full_auto/wiki/successor.md`
- Create: `tests/fixtures/roadmap_full_auto/wiki/scoped.md`
- Modify: `scripts/eval.py`
- Modify: `scripts/verify.sh`
- Create: `tests/test_verify_isolation.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: shared structured search output from Task 6 and tracked fixture corpus.
- Produces: `evaluate(golden: Path, mode: Literal["expand-on", "expand-off"], top: int = 5) -> EvalResult`, `compare_expansion(on: EvalResult, off: EvalResult, floor: float = 0.90) -> EvalComparison`, JSON and Markdown artifacts.

- [ ] **Step 1: Add a failing golden comparison test to the evaluator selfcheck**

```python
def compare_expansion(on, off, floor=.90):
    ratio = 1.0 if off.hit_at_5 == 0 else on.hit_at_5 / off.hit_at_5
    return EvalComparison(on=on, off=off, ratio=ratio, passed=ratio >= floor)

def test_comparison_fails_below_floor():
    assert not compare_expansion(EvalResult(hit_at_5=.80), EvalResult(hit_at_5=1.0), .90).passed
```

- [ ] **Step 2: Run evaluator against the tracked fixture and verify the comparison mode is missing**

Run: `python3 scripts/eval.py --golden tests/fixtures/roadmap_full_auto/golden.jsonl --wiki tests/fixtures/roadmap_full_auto/wiki --compare-expand --report-json /tmp/roadmap-eval.json`

Expected: argparse exits 2 for unknown `--compare-expand`; no report is created.

- [ ] **Step 3: Implement deterministic dual-mode evaluation**

Add `--wiki`, `--data`, `--compare-expand`, `--report-json`, and `--report-md`. Build an isolated temporary index, run each query with `--expand` and `--no-expand`, record ordered paths/hit@5/MRR/planner, and exit 1 when the ratio is below 0.90. The fixture transport forces local planning so CI never calls a network.

```python
on = evaluate(args.golden, args.wiki, mode="expand-on", top=5)
off = evaluate(args.golden, args.wiki, mode="expand-off", top=5)
comparison = compare_expansion(on, off, floor=.90)
write_reports(comparison, args.report_json, args.report_md)
return 0 if comparison.passed else 1
```

- [ ] **Step 4: Expand CI to run syntax, full tests, selfcheck, dual eval, and a fully injected verify fixture**

```yaml
- name: Install test runner
  run: python3 -m pip install pytest
- name: Full auto unit and integration suite
  run: pytest tests/ -q
- name: Fuse and expansion golden gates
  run: |
    MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py
    python3 scripts/eval.py --golden tests/fixtures/roadmap_full_auto/golden.jsonl --wiki tests/fixtures/roadmap_full_auto/wiki --compare-expand --report-json .artifacts/eval.json --report-md .artifacts/eval.md
- name: Isolated verify dependencies
  run: |
    fixture_root="${RUNNER_TEMP}/memory-hub-verify"
    export HOME="$fixture_root/forbidden-home"
    export WIKI_PATH="$fixture_root/wiki"
    export MEMORY_HUB_DATA="$fixture_root/data"
    export CODEX_SESSIONS_DIR="$fixture_root/sessions"
    export CODEX_AUTOMATIONS_DIR="$fixture_root/automations"
    export CODEX_CONFIG_FILE="$fixture_root/codex-config.toml"
    export CODEX_HOOKS_FILE="$fixture_root/hooks.json"
    export AUTOMATIONS_DB="$fixture_root/automations.db"
    python3 -m tests.helpers.full_auto_fixture seed-verify-dependencies --root "$fixture_root" --automations "$CODEX_AUTOMATIONS_DIR" --config "$CODEX_CONFIG_FILE" --hooks "$CODEX_HOOKS_FILE" --db "$AUTOMATIONS_DB" --wiki "$WIKI_PATH"
    ./memory-hub.sh verify | tee .artifacts/verify.txt
    test ! -e "$HOME/.codex"
```

Task 13 owns the `scripts/verify.sh` dependency-injection change, its isolated test, and CI wiring. It consumes Task 1's public `tests.helpers.full_auto_fixture seed-verify-dependencies` CLI without modifying or duplicating the helper. Replace implicit verification locations with `CODEX_AUTOMATIONS_DIR`, `CODEX_CONFIG_FILE`, `CODEX_HOOKS_FILE`, `AUTOMATIONS_DB`, and `WIKI_PATH` (compatibility fallback may remain only for direct non-fixture use), then emit a sanitized resolved-path record. Initialize and seed all verify dependencies under `${RUNNER_TEMP}` before invoking `memory-hub.sh verify`; do not let CI default to runner home. The isolated test fails if any automation/config/hooks/DB/wiki path escapes `fixture_root`; a fixture with those files absent is an expected verify failure, never a reason to weaken verify.

- [ ] **Step 5: Run golden and complete unit regression gates, then commit**

Run: `python3 scripts/eval.py --golden tests/fixtures/roadmap_full_auto/golden.jsonl --wiki tests/fixtures/roadmap_full_auto/wiki --compare-expand --report-json /tmp/roadmap-eval.json --report-md /tmp/roadmap-eval.md && pytest tests/ -q && MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py && python3 -m tests.test_verify_isolation`

Expected: evaluator exit 0 with `ratio >= 0.90`, JSON/Markdown non-empty, full unittest PASS, fuse selfcheck PASS, and isolation test proves empty fixture fails then seeded explicit-variable fixture passes without reading real `$HOME`.

```bash
git add scripts/eval.py scripts/verify.sh .github/workflows/ci.yml tests/fixtures/roadmap_full_auto/golden.jsonl tests/fixtures/roadmap_full_auto/wiki/active.md tests/fixtures/roadmap_full_auto/wiki/successor.md tests/fixtures/roadmap_full_auto/wiki/scoped.md tests/test_verify_isolation.py
git commit -m "test: gate full-auto retrieval quality"
```

### Task 14: Prove the complete behavior through a real isolated CLI fixture

**Files:**
- Create: `tests/test_full_auto_cli.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: all public CLI commands and report artifacts implemented in Tasks 1–13, plus the injected verify dependencies.
- Produces: `python3 -m tests.test_full_auto_cli --evidence-dir PATH`, which creates a temporary home/wiki/data/sessions/automations/config/hooks/DB/Git repo and captures every binary observable under the caller-supplied evidence directory; `python3 -m tests.test_verify_isolation` proves `verify.sh` uses injected paths only.

- [ ] **Step 1: Write the end-to-end test with explicit binary assertions and artifact capture**

```python
def test_full_auto_cli_scenario(self):
    fx = FullAutoFixture.create(self.evidence_dir)
    fx.assert_verify_fails_unseeded()
    fx.seed_verify_dependencies()
    fx.assert_verify_uses_fixture_paths_only()
    fx.seed_scope_pages_and_cross_day_successor_cluster()
    fx.assert_ok("scope-backfill", "--apply")
    fx.assert_all_pages_have_legal_scope()
    fx.assert_ok("index")
    search = fx.assert_ok("search", "fixture lifecycle", "--fuse", "--explain", "--scope", "project", "--scope-id", "fixture-project", "--json")
    self.assertIn(search.json["plan"]["planner"], ("llm", "local"))
    fx.assert_safe_zero_diff("run")
    fx.assert_safe_zero_diff("maintain")
    first = fx.assert_ok("maintain")
    fx.assert_cluster_count(1)
    fx.assert_successor_bidirectional()
    fx.assert_manifest_and_atomic_index()
    fx.assert_cached_equals_operation_whitelist(first.operation_id)
    fx.assert_ok("maintain")
    fx.assert_cluster_count(1)
```

The fixture environment is mandatory and complete: `HOME=fixture_root/forbidden-home` (not created), `WIKI_PATH=fixture_root/wiki`, `MEMORY_HUB_DATA=fixture_root/data`, `CODEX_SESSIONS_DIR=fixture_root/sessions`, `CODEX_AUTOMATIONS_DIR=fixture_root/automations`, `CODEX_CONFIG_FILE=fixture_root/codex-config.toml`, `CODEX_HOOKS_FILE=fixture_root/hooks.json`, and `AUTOMATIONS_DB=fixture_root/automations.db`. `assert_verify_uses_fixture_paths_only()` parses verify's sanitized resolved-path artifact, requires every dependency path to be under `fixture_root`, and requires `$HOME/.codex` to remain absent; it is the binary assertion that the real home was not read.

- [ ] **Step 2: Run the new CLI test and verify the artifact contract fails before harness completion**

Run: `python3 -m tests.test_full_auto_cli --evidence-dir /tmp/roadmap-full-auto-cli-red`

Expected: FAIL because the fixture harness or one required captured artifact is absent; exit 1.

- [ ] **Step 3: Implement fixture creation and per-command evidence capture using Task 13 verify injection**

Task 13 owns the `scripts/verify.sh` dependency-injection change and `tests/test_verify_isolation.py`; Task 14 consumes that public contract. Task 1 solely owns `tests/helpers/full_auto_fixture.py` and its `seed-verify-dependencies` implementation. Task 14 calls that public helper to create the valid `automations/<id>/automation.toml`, config with a memory-hub MCP entry and hook state, hooks JSON, SQLite automation row, and wiki `concepts/`, `queries/`, and `.scripts/fix_deadlinks.py` fixtures; it must not modify or duplicate helper seeding logic. First run verify before seeding and record its non-zero exit; only then call the Task 1 helper and require exit 0. Do not change `verify.sh` policy or suppress its checks to make an empty fixture green.

For each command, save `<nn>-<slug>.stdout`, `<nn>-<slug>.stderr`, and `<nn>-<slug>.exit`; after each state transition save `tree.txt`, `git-status.txt`, `git-cached.txt`, `git-log.txt`, `reports.txt`, `index.sha256`, `index-schema.txt`, `resolved_paths.json`, and `assertions.json`. Initialize Git identity locally only:

```python
def run(self, *args):
    proc = subprocess.run([str(ROOT / "memory-hub.sh"), *args], env=self.env,
                          text=True, capture_output=True, check=False)
    self.capture_command(args, proc)
    return proc
```

- [ ] **Step 4: Execute the exact acceptance command sequence and full regression suite**

Run:

```bash
evidence_dir=".omo/evidence/roadmap-full-auto-cli"
fixture_root="$(mktemp -d)"
export HOME="$fixture_root/forbidden-home"
export WIKI_PATH="$fixture_root/wiki"
export MEMORY_HUB_DATA="$fixture_root/data"
export CODEX_SESSIONS_DIR="$fixture_root/sessions"
export CODEX_AUTOMATIONS_DIR="$fixture_root/automations"
export CODEX_CONFIG_FILE="$fixture_root/codex-config.toml"
export CODEX_HOOKS_FILE="$fixture_root/hooks.json"
export AUTOMATIONS_DB="$fixture_root/automations.db"
python3 -m tests.helpers.full_auto_fixture seed-verify-dependencies --root "$fixture_root" \
  --automations "$CODEX_AUTOMATIONS_DIR" --config "$CODEX_CONFIG_FILE" \
  --hooks "$CODEX_HOOKS_FILE" --db "$AUTOMATIONS_DB" --wiki "$WIKI_PATH"
python3 -m tests.test_full_auto_cli --evidence-dir "$evidence_dir"
pytest tests/
MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py
./memory-hub.sh verify
```

Expected: every command exits 0 after fixture seeding; the deliberate unseeded verify invocation is captured as non-zero; scope fields are legal; explain planner is `llm` or `local`; safe modes leave wiki Git diff byte-identical; automatic maintain yields exactly one cluster page, reciprocal successor fields, committed manifest, atomic index, and cached whitelist equality; replay leaves cluster count at one; pytest, fuse selfcheck, and seeded verify pass with all resolved dependencies below `fixture_root` and no `$HOME/.codex` read.

- [ ] **Step 5: Validate evidence completeness and commit the acceptance harness**

Run: `test -s .omo/evidence/roadmap-full-auto-cli/assertions.json && test -s .omo/evidence/roadmap-full-auto-cli/git-status.txt && test -s .omo/evidence/roadmap-full-auto-cli/index-schema.txt && test -s .omo/evidence/roadmap-full-auto-cli/resolved_paths.json && rg -q '"all_passed": true' .omo/evidence/roadmap-full-auto-cli/assertions.json && rg -q '"real_home_read": false' .omo/evidence/roadmap-full-auto-cli/assertions.json`

Expected: every `test` and `rg` exits 0; no required artifact is empty.

```bash
git add tests/test_full_auto_cli.py .github/workflows/ci.yml
git commit -m "test: prove isolated full-auto CLI workflow"
```

## Final Release Gate

Run from the repository root with explicit isolated paths; do not rely on inherited home defaults:

```bash
fixture_root="$(mktemp -d)"
export HOME="$fixture_root/forbidden-home"
export WIKI_PATH="$fixture_root/wiki"
export MEMORY_HUB_DATA="$fixture_root/data"
export CODEX_SESSIONS_DIR="$fixture_root/sessions"
export CODEX_AUTOMATIONS_DIR="$fixture_root/automations"
export CODEX_CONFIG_FILE="$fixture_root/codex-config.toml"
export CODEX_HOOKS_FILE="$fixture_root/hooks.json"
export AUTOMATIONS_DB="$fixture_root/automations.db"
python3 -m tests.helpers.full_auto_fixture seed-verify-dependencies --root "$fixture_root" \
  --automations "$CODEX_AUTOMATIONS_DIR" --config "$CODEX_CONFIG_FILE" \
  --hooks "$CODEX_HOOKS_FILE" --db "$AUTOMATIONS_DB" --wiki "$WIKI_PATH"
python3 -m tests.test_full_auto_cli --evidence-dir .omo/evidence/roadmap-full-auto-cli
pytest tests/
MH_FUSE_SELFCHECK=1 python3 scripts/fuse.py
./memory-hub.sh verify
git diff --check
```

The release gate is binary: every command exits 0, every required artifact is non-empty, expand-on hit@5 is at least 90% of expand-off, `--safe` produces no wiki/manifest/archive/Git mutation, operation cached paths equal the exact whitelist, lifecycle/cluster replay is idempotent, and no command touches `~/llm-wiki`.

## Authoring Validation Record

- Placeholder and task-structure scan: inline Python scanned the forbidden writing-plans phrases, counted 14 sequential tasks, and required `Files`, `Interfaces`, at least five checkbox steps, exact `git add`, and `git commit` in every task; exit 0, `placeholder_task_structure=PASS`.
- Spec coverage check: inline Python parsed the coverage matrix and required exactly one non-empty row for every section 1–11; exit 0, `spec_coverage_rows=11`, `missing=[]`, `empty=[]`.
- Type/interface consistency check: inline Python compared the declared and implementation signatures for `infer_scope`, `rank_results`, `MemoryService.search`, `MemoryService.ask_context`, `begin_transaction`, `prepare_successor_pages`, `finalize_successor_after_index`, `commit_manifest`, `run_pipeline`, and `maintain_pipeline`; required one `FailureHook = Callable[[str], None]`, one `TransactionContext`, one `OperationJournal.prepare()` root allocation, explicit `tx` signatures in Tasks 8/10/11, and absence of every legacy implicit-journal, local-lifecycle-journal, local-root-constructor, and obsolete fault-parameter form; exit 0, `interface_consistency=PASS`.
- File ownership check: inline Python parsed every task's `Files` block, required at least one owned file, and rejected duplicate `Create` ownership; exit 0, `file_ownership=PASS tasks=14 creates=32`.
- Whitespace/error check: `git diff --check -- docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`; exit 0.
- Scope check: `git status --short`; exit 0 and only `?? docs/superpowers/plans/` was reported in this worktree.

Plan complete and saved to `docs/superpowers/plans/2026-08-31-roadmap-full-auto.md`. Two execution options:

1. Subagent-Driven (recommended) — dispatch a fresh subagent per task, review between tasks, fast iteration; required sub-skill: `superpowers:subagent-driven-development`.
2. Inline Execution — execute tasks in this session using `superpowers:executing-plans`, with batch checkpoints.
