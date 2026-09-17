from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from scripts.automation_core.cluster import (
    ClusterObservation,
    ClusterPlan,
    classify_cluster_target,
    member_link,
    render_merge_page,
)
from scripts.automation_core.indexer import atomic_rebuild_index
from scripts.automation_core.operation import GitBaseline, begin_transaction
from scripts.automation_core.orchestrator import MaintainStageRunner, RunStageRunner
from scripts.automation_core.schema import Mode, OperationContext


def _members():
    return [
        ClusterObservation("t-1", "p", "Memory maintenance preserves exact owned changes and isolated indexes item 1.", 1788100000, "2026-08-30"),
        ClusterObservation("t-2", "p", "Memory maintenance preserves exact owned changes and isolated indexes item 2.", 1788100000, "2026-08-30"),
        ClusterObservation("t-3", "p", "Memory maintenance preserves exact owned changes and isolated indexes item 3.", 1788200000, "2026-08-31"),
    ]


def _plan(members=None):
    members = members or _members()
    key = hashlib.sha256(chr(10).join(sorted(m.id for m in members)).encode()).hexdigest()[:16]
    return ClusterPlan(key, "p", tuple(members), "local", "keyword abstract", "f", "l"), key


def test_render_moc_frontmatter_and_short_hash_fallback():
    plan, key = _plan()
    text = render_merge_page(plan).decode()
    assert "\ntype: moc\n" in text
    assert "generator: memory-hub-cluster" in text
    assert f"cluster_key: {key}" in text
    assert "member_count: 3" in text
    assert "scope: project" in text and "scope_id: p" in text
    for mid in ("t-1", "t-2", "t-3"):
        short = hashlib.sha256(mid.encode()).hexdigest()[:12]
        assert f"- [{short}]" in text


def test_render_member_wikilink_from_text_and_source_uri():
    linked = ClusterObservation("x-1", "p", "See decisions/target for the rollout notes detail.", 1788100000, "2026-08-30", source_uri="decisions/target.md")
    assert member_link(linked) == "decisions/target"
    inline = ClusterObservation("x-2", "p", "Related context in [[decisions/other]] for followup discussion notes.", 1788100000, "2026-08-30")
    assert member_link(inline) == "decisions/other"
    shell = ClusterObservation("x-3", "p", "exec_command check for [[$# -eq 0]] branch in publish scriptInteract.", 1788100000, "2026-08-30")
    assert member_link(shell) is None
    plain = ClusterObservation("x-4", "p", "Memory maintenance preserves exact owned changes and isolated indexes item 9.", 1788100000, "2026-08-30")
    assert member_link(plain) is None
    plan, _ = _plan([linked, inline, plain])
    text = render_merge_page(plan).decode()
    assert "- [[decisions/target]]" in text
    assert "- [[decisions/other]]" in text


def test_classify_cluster_target_states(tmp_path: Path):
    from datetime import datetime, timezone
    from scripts.automation_core.cluster import GENERATOR, ManifestEntry
    plan, key = _plan()
    old_render = render_merge_page(plan, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
    new_render = render_merge_page(plan, now=datetime(2026, 9, 18, tzinfo=timezone.utc))
    assert old_render != new_render
    target = tmp_path / "moc" / "cluster-abc.md"
    assert classify_cluster_target(target, old_render) == "write"
    target.parent.mkdir(parents=True)
    target.write_bytes(old_render)
    assert classify_cluster_target(target, new_render) == "idempotent"
    stale = old_render.replace(b"isolated indexes item 1.", b"a rewritten stale body line.")
    target.write_bytes(stale)
    assert classify_cluster_target(target, new_render) == "rewrite"
    other = ManifestEntry("deadbeefdeadbeef", [], "moc/cluster-deadbeefdeadbeef.md", "0" * 64, "op", "2026-08-01T00:00:00+00:00")
    assert classify_cluster_target(target, new_render, other) == "manual"
    hand = b"---\ntitle: Hand edited\ntype: moc\n---\nHand edited body.\n"
    target.write_bytes(hand)
    assert classify_cluster_target(target, new_render) == "manual"


def test_member_link_gates_on_existing_pages():
    from scripts.automation_core.cluster import member_link as _ml
    linked_text = "Pipeline review followup notes reference decisions/ghost for context."
    m = ClusterObservation("g-1", "p", linked_text[:20] + " [[decisions/ghost]] " + linked_text[20:], 1788100000, "2026-08-30")
    assert _ml(m) == "decisions/ghost"
    assert _ml(m, is_page=lambda t: False) is None
    assert _ml(m, is_page=lambda t: t == "decisions/ghost") == "decisions/ghost"
    plan, _ = _plan([m])
    gated = render_merge_page(plan, is_page=lambda t: False).decode()
    assert "\n- [[" not in gated
    assert hashlib.sha256(b"g-1").hexdigest()[:12] in gated


def _run_tx(tmp_path: Path, command: str):
    wiki, data, staging = (tmp_path / n for n in ("wiki", "data", "staging"))
    for d in (wiki, data, staging):
        d.mkdir(parents=True, exist_ok=True)
    ctx = OperationContext(operation_id="20260831T000000Z-moc-test", command=command,
                           mode=Mode.AUTO, auto=True, apply=True, wiki_path=wiki, data_path=data)
    return begin_transaction(ctx, GitBaseline((), ())), wiki, data, staging


def test_aggregate_writes_moc_page_and_index_edge(tmp_path: Path):
    tx, wiki, data, staging = _run_tx(tmp_path, "run")
    (wiki / "decisions").mkdir(parents=True)
    (wiki / "decisions" / "target.md").write_text("---\ntitle: Target\ntype: decision\n---\nTarget body.\n", encoding="utf-8")
    rows = [{"id": f"e2e-{i}", "project_id": "e2e-project",
             "text": "Pipeline reliability review notes for stage release [[decisions/target]] item %d." % i,
             "created_at_epoch": 1788100000 + (86400 if i == 2 else 0)} for i in range(3)]
    (staging / "observations-20260830-120000.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    runner = RunStageRunner(staging)
    outcome = runner.apply("aggregate", tx)
    assert outcome.ok, outcome.message
    assert outcome.data["clusters"] == 1
    pages = list(wiki.glob("moc/cluster-*.md"))
    assert len(pages) == 1
    body = pages[0].read_text(encoding="utf-8")
    assert "generator: memory-hub-cluster" in body
    assert "- [[decisions/target]]" in body
    atomic_rebuild_index(wiki, data)
    rel = pages[0].relative_to(wiki).as_posix()
    with sqlite3.connect(f"file:{data / 'index.db'}?mode=ro", uri=True) as db:
        assert db.execute("select count(*) from pages where path = ?", (rel,)).fetchone()[0] == 1
        assert db.execute("select count(*) from links where src_path = ?", (rel,)).fetchone()[0] >= 1


def test_aggregate_rerun_never_overwrites(tmp_path: Path):
    tx, wiki, data, staging = _run_tx(tmp_path, "run")
    rows = [{"id": f"rw-{i}", "project_id": "rw-project",
             "text": "Memory maintenance preserves exact owned changes and isolated indexes item %d." % i,
             "created_at_epoch": 1788100000 + (86400 if i == 2 else 0)} for i in range(3)]
    (staging / "observations-20260830-120000.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    runner = RunStageRunner(staging)
    assert runner.apply("aggregate", tx).ok
    page = list(wiki.glob("moc/cluster-*.md"))[0]
    before = page.read_bytes()
    second = runner.apply("aggregate", tx)
    assert second.ok
    assert page.read_bytes() == before
    assert second.data["skipped"] == []


def test_maintain_skips_manual_page_and_reports(tmp_path: Path):
    tx, wiki, data, staging = _run_tx(tmp_path, "maintain")
    rows = [{"id": f"t-{i}", "project_id": "p",
             "text": "Memory maintenance preserves exact owned changes and isolated indexes item %d." % i,
             "created_at_epoch": 1788100000 + (86400 if i == 3 else 0)} for i in (1, 2, 3)]
    (staging / "observations-20260830-120000.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    key = hashlib.sha256("t-1\nt-2\nt-3".encode()).hexdigest()[:16]
    manual = wiki / "moc" / f"cluster-{key}.md"
    manual.parent.mkdir(parents=True)
    hand = "---\ntitle: Hand edited\ntype: moc\ncreated: '2026-09-01'\nupdated: '2026-09-01'\n---\nHand edited body.\n"
    manual.write_text(hand, encoding="utf-8")
    runner = MaintainStageRunner(staging=staging)
    planned = runner.apply("validate", tx)
    assert planned.ok, planned.message
    assert manual.relative_to(wiki).as_posix() in planned.data["skipped"]
    published = runner.apply("publish_pages_lifecycle", tx)
    assert published.ok
    assert published.data["published"] == 0
    assert manual.read_text(encoding="utf-8") == hand



def test_aggregate_rewrites_stale_auto_page(tmp_path: Path):
    from scripts.automation_core.cluster import GENERATOR
    tx, wiki, data, staging = _run_tx(tmp_path, "run")
    rows = [{"id": f"sa-{i}", "project_id": "sa-project",
             "text": "Memory maintenance preserves exact owned changes and isolated indexes item %d." % i,
             "created_at_epoch": 1788100000 + (86400 if i == 2 else 0)} for i in range(3)]
    (staging / "observations-20260830-120000.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    key = hashlib.sha256("sa-0\nsa-1\nsa-2".encode()).hexdigest()[:16]
    stale = (wiki / "moc" / f"cluster-{key}.md")
    stale.parent.mkdir(parents=True)
    stale.write_text("---\ntype: moc\ngenerator: " + GENERATOR + "\ntitle: Stale\n" +
                     "status: active\nscope: project\nscope_id: sa-project\n" +
                     f"cluster_key: {key}\nmember_count: 3\ncreated: '2026-08-01'\nupdated: '2026-08-01'\n---\n" +
                     "# stale\n\nCompletely stale body text here.\n", encoding="utf-8")
    outcome = RunStageRunner(staging).apply("aggregate", tx)
    assert outcome.ok, outcome.message
    assert outcome.data["clusters"] == 1
    assert outcome.data["skipped"] == []
    body = stale.read_text(encoding="utf-8")
    assert "Completely stale body" not in body
    assert "generator: " + GENERATOR in body


def test_maintain_refreshes_stale_auto_page(tmp_path: Path):
    from scripts.automation_core.cluster import GENERATOR
    tx, wiki, data, staging = _run_tx(tmp_path, "maintain")
    rows = [{"id": f"t-{i}", "project_id": "p",
             "text": "Memory maintenance preserves exact owned changes and isolated indexes item %d." % i,
             "created_at_epoch": 1788100000 + (86400 if i == 3 else 0)} for i in (1, 2, 3)]
    (staging / "observations-20260830-120000.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    key = hashlib.sha256("t-1\nt-2\nt-3".encode()).hexdigest()[:16]
    page = wiki / "moc" / f"cluster-{key}.md"
    page.parent.mkdir(parents=True)
    page.write_text("---\ntype: moc\ngenerator: " + GENERATOR + "\ntitle: Stale\n" +
                    "status: active\nscope: project\nscope_id: p\n" +
                    f"cluster_key: {key}\nmember_count: 3\ncreated: '2026-08-01'\nupdated: '2026-08-01'\n---\n" +
                    "# stale\n\nCompletely stale body text here.\n", encoding="utf-8")
    manifest_dir = data / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "cluster-observations-v1.json").write_text(json.dumps({
        "version": 1, "entries": {key: {
            "cluster_key": key, "observation_hashes": ["unrelated"],
            "page_path": f"moc/cluster-{key}.md", "content_hash": "0" * 64,
            "operation_id": "prior", "created_at": "2026-08-01T00:00:00+00:00"}}}), encoding="utf-8")
    runner = MaintainStageRunner(staging=staging)
    planned = runner.apply("validate", tx)
    assert planned.ok, planned.message
    assert planned.data["clusters"] == 1
    assert planned.data["skipped"] == []
    published = runner.apply("publish_pages_lifecycle", tx)
    assert published.ok
    assert published.data["published"] == 1
    assert published.data["skipped"] == []
    body = page.read_text(encoding="utf-8")
    assert "Completely stale body" not in body
    assert "status: active" in body
    assert "deprecated_by" not in body
