# Install, configure, upgrade and uninstall

This is the distribution-facing guide for memory-hub. It covers what actually ships in
the repository, what stays on the machine that produces it, how to configure the moving
parts, and how to get back out. The Chinese quick start lives in
[README.md](../README.md); the experience-memory contract lives in
[experience-v1.md](experience-v1.md); the Codex plugin details live in
[codex-native-memory.md](codex-native-memory.md).

## 1. What ships and what does not

The repository is source plus synthetic fixtures. Nothing under the following paths is
published, and a fresh clone must work without them:

| Path | Status |
|---|---|
| `~/llm-wiki/` | Your Markdown knowledge base. Never in the repository. |
| `~/.memory-hub/` | Runtime state: `index.db`, `experience.sqlite3`, `codex-runtime.json`, settings, access log. |
| `docs/evidence/` | Local audit evidence from past runs (multi-GB). Git-ignored on purpose. |
| `evaluation/golden.jsonl` | Your private retrieval benchmark. Not shipped; `eval` reports `golden set not found` and exits 1 when it is absent. |
| `staging/` | Capture/distill working area, including raw session material. |
| `reports/`, `aml_research/`, `mem0*`, `ui/` build output | Local research and scratch material. |

Shipped and reviewed: `scripts/`, `mcp/`, `plugins/memory-hub/`, `hooks/`, `tests/`, the
experience evaluators in `evaluation/experience_v1/` (harness, actions, suites), `docs/`,
and the synthetic fixtures they read. Code is MIT (see [LICENSE](../LICENSE)); there is no
third-party data in the tree and no credential file of any kind.

## 2. Requirements and compatibility

Runtime: macOS or Linux, `bash`, `jq`, `ripgrep`, `sqlite3`, and Python. The MCP server
additionally needs the `mcp` package in the interpreter named by `codex-runtime.json`.

| Surface | Verified |
|---|---|
| Experience CLI (`init`, `record`, `recall`, `read`) | Python 3.9.6, 3.11.16, 3.14.7 on macOS |
| Full test suite | 3.11.16 Linux (623 passed, 9 skipped), 3.14.7 macOS (632 passed, 6 subtests) |
| CI matrix (`.github/workflows/ci.yml`) | 3.11 and 3.12 on ubuntu-latest and macos-latest |
| Shell portability | The capture jq template is asserted backslash-free: bash 5 collapses doubled backslashes inside `${var//pat/repl}`, which used to make Linux capture emit nothing while macOS stayed green. |

Two platform notes that are easy to get wrong: GNU-only flags are not assumed, and the
absence of `shasum` (a Perl script) on minimal Linux images breaks `capture` unless
`perl` is installed.

## 3. Install

**CLI only.** Clone, then run through the wrapper. Nothing is installed system-wide and no
service starts by itself.

```bash
git clone <your-remote> memory-hub && cd memory-hub
./memory-hub.sh status          # health check: sessions, staging, wiki, proxy
./memory-hub.sh run --safe      # preview the pipeline without writing anything
```

**Experience memory (local store).** The three experience tools read and write
`${MEMORY_HUB_DATA:-~/.memory-hub}/experience.sqlite3`. Initialization is explicit:

```bash
mkdir -p ~/.memory-hub
python3 -m scripts.automation_core.experience.cli --db ~/.memory-hub/experience.sqlite3 --owner init
```

**Codex plugin.** Register `plugins/memory-hub` as a local plugin through Codex's plugin
workflow, then point `~/.memory-hub/codex-runtime.json` at the checkout and the interpreter
(`hub_root`, `python_path`, `data_path`, `wiki_path`, optional per-workspace mappings). The
plugin adds hooks (SessionStart recall rules, UserPromptSubmit retrieval, Stop/SessionEnd
capture) and a stdio MCP server; it does not patch Codex and does not touch Codex's own
memory files. Changes to `plugins/memory-hub/mcp/launch.py` take effect only after the
installed plugin cache copy is refreshed - back it up first, keep the byte-identical
copy, and start a new thread so hooks and tools load again.

**Switches.** The experience scope is off until a scope file exists:

```bash
# ~/.memory-hub/experience-host.json
{"collections": ["codex"], "artifact_roots": [], "profile": false}
```

## 4. Configuration

| Variable | Default | Effect |
|---|---|---|
| `MEMORY_HUB_DATA` | `~/.memory-hub` | Runtime state: index, experience store, settings, access log, backups. |
| `WIKI_PATH` | `~/llm-wiki` | Markdown knowledge base used by index/search/ask/publish. |
| `MEMORY_HUB_STAGING` | `<repo>/staging` | Capture/distill working area. |
| `MEMORY_HUB_EXPERIENCE_HOST_ROOTS` | host-decided | `1` uses the host workspace roots for collections and artifact roots. |
| `MEMORY_HUB_EXPERIENCE_SEMANTIC` | on with a scope file | `1` adds local vector candidates (RRF). `0` restores lexical-only recall. |
| `MEMORY_HUB_EXPERIENCE_UTILITY` | on with a scope file | `1` lets feedback break ties between equally relevant candidates. `0` restores plain relevance order. |
| `MEMORY_HUB_EMBED_MODEL` | `BAAI/bge-small-zh-v1.5` | Embedding model for the derived vector index. |
| `OPENCODEX_URL`, `CLAUDE_MEM_MODEL` | unset | Model gateway used only by explicit `--llm` work and the background worker. |

Capture, distill and automatic publication have their own switches in
`codex-memory-settings.json`; publication defaults to off. Per-workspace mappings in
`codex-runtime.json` isolate test workspaces from the default data root.

## 5. Verify

```bash
./memory-hub.sh status          # local health
./memory-hub.sh verify          # static drift checks (automations, hooks, MCP paths)
python3 -m pytest -q tests/     # full suite; needs the test extras: pip install -r requirements-test.txt
```

A fresh checkout is expected to pass the suite without any of the local-only paths above.
The experience tools can be exercised end to end with the CLI before any host
integration:

```bash
D=$(mktemp -d)
python3 -m scripts.automation_core.experience.cli --db "$D/e.sqlite3" --owner init
python3 -m scripts.automation_core.experience.cli --db "$D/e.sqlite3" --collection fixture \
  --owner record --file episode.json --key demo-1     # episode.json: one JSON line from evaluation/experience_v1/development.jsonl
python3 -m scripts.automation_core.experience.cli --db "$D/e.sqlite3" --collection fixture \
  recall --task '修改文案，保留感情但不要制造负担' --mode explore
```

## 6. Upgrade and rollback

1. Read the diff: `git log --oneline HEAD..origin/main` and the relevant section of
   `docs/experience-v1.md`.
2. Stop the background worker (turn capture and publish off, or unload the LaunchAgent).
3. `git pull`. Knowledge base and stores are outside the repository, so the pull never
   touches them; schema migrations run only from an explicit `init` and refuse to run when
   a backup file already exists.
4. If the plugin launcher changed, copy `plugins/memory-hub/mcp/launch.py` into the plugin
   cache after backing up the previous file, and verify it is byte-identical to the repo.
5. Restart, then run the three verification commands above.

Rollback: restore the launcher backup and `git checkout` the previous revision; set
`MEMORY_HUB_EXPERIENCE_SEMANTIC=0` and `MEMORY_HUB_EXPERIENCE_UTILITY=0` to drop the M4
extensions without touching stored data; empty or delete
`~/.memory-hub/experience-host.json` to disable the whole experience scope and fall back
to ordinary retrieval. No step rewrites past episodes; `rebuild-index` is an explicit
owner command.

## 7. Uninstall

```bash
# 1. Remove the plugin registration through Codex's plugin workflow, then delete its cache copy.
# 2. Disable the hooks (codex-memory-settings.json: capture/publish off) and unload the LaunchAgent if you installed one.
# 3. Delete the runtime state you no longer want:
#    ~/.memory-hub/          index, experience store, settings, backups
# 4. Your Markdown knowledge base (~/llm-wiki) is your own data and is left untouched.
```

Deleting `~/.memory-hub` removes the experience store with it. Published pages, exports and
any media referenced by episodes stay where you put them; export or purge individual
episodes with the owner CLI before deleting the store.

