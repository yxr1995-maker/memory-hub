from __future__ import annotations

import json
import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from scripts.automation_core.frontmatter import parse_page
from scripts.automation_core.schema import Mode, OperationContext, PageDocument
from scripts.automation_core.scope import apply_backfill, infer_scope, plan_backfill
from tests.helpers.full_auto_fixture import write_page


ROOT = Path(__file__).resolve().parents[1]


def operation(wiki: Path, data: Path, *, apply: bool = True) -> OperationContext:
    return OperationContext(
        operation_id="20260831T000000Z-scope-fixture",
        command="scope-backfill",
        mode=Mode.AUTO,
        auto=True,
        apply=apply,
        wiki_path=wiki,
        data_path=data,
    )


def page(tmp_path: Path, relative: str = "notes/page.md", **frontmatter: object) -> PageDocument:
    return parse_page(write_page(tmp_path, relative, frontmatter, "historical body\n"))


def test_six_level_table_and_equal_strong_tie_are_deterministic(tmp_path: Path) -> None:
    cases = (
        (page(tmp_path, "explicit.md", scope="agent", scope_id="kept"), {},
         ("agent", "kept", "high", "explicit")),
        (page(tmp_path, "agent.md", agent_id="Agent 7", scope_hint="agent"), {},
         ("agent", "agent-7", "high", "session_meta")),
        (page(tmp_path, "user/preferences.md"), {},
         ("user", "fixture-user", "high", "path")),
        (page(tmp_path, "project.md", project="Road Map"), {"project_id": "Road Map"},
         ("project", "road-map", "high", "session_meta")),
        (page(tmp_path, "ranked.md"), {"project_candidates": ["beta", "alpha"], "scores": [0.91, 0.60]},
         ("project", "beta", "medium", "path")),
        (page(tmp_path, "fallback.md"), {},
         ("project", "default-project", "low", "fallback")),
    )
    got = [infer_scope(document, provenance, "fixture-user") for document, provenance, _ in cases]
    assert [(x.scope, x.scope_id, x.confidence, x.source) for x in got] == [x[2] for x in cases]

    tie = infer_scope(
        page(tmp_path, "tie.md"),
        {"project_candidates": ["zeta", "alpha"], "scores": [1.0, 1.0]},
        "fixture-user",
    )
    assert (tie.scope_id, tie.confidence, tie.conflict) == ("alpha", "medium", True)


def test_invalid_explicit_scope_falls_through_and_user_id_is_injected(tmp_path: Path) -> None:
    assignment = infer_scope(
        page(tmp_path, "user/preferences.md", scope="root", scope_id="/Users/private"),
        {},
        "***",
    )
    assert (assignment.scope, assignment.scope_id) == ("user", "default-user")
    assert "/Users" not in assignment.scope_id


def test_12900_cursor_batches_apply_idempotently_and_skip_changed(tmp_path: Path) -> None:
    wiki, data = tmp_path / "wiki", tmp_path / "data"
    for index in range(12_900):
        write_page(wiki, f"pages/{index:05d}.md", {"project": "Fixture"}, f"body {index}\n")

    first = plan_backfill(wiki, None, 500, operation(wiki, data))
    assert (len(first.entries), first.next_cursor) == (500, "pages/00499.md")
    write_page(
        wiki,
        "pages/00004.md",
        {"project": "Fixture", "scope": "project", "scope_id": "fixture"},
        "body 4 changed\n",
    )
    report = apply_backfill(first, operation(wiki, data))
    assert report.counts == {"written": 499, "concurrent_change": 1}

    rerun = apply_backfill(
        plan_backfill(wiki, None, 500, operation(wiki, data)),
        operation(wiki, data),
    )
    assert rerun.counts.get("written", 0) == 0
    assert parse_page(wiki / "pages/00000.md").body == b"body 0\n"

    second = plan_backfill(wiki, first.next_cursor, 500, operation(wiki, data))
    assert second.entries[0].relative_path == "pages/00500.md"


def test_dry_run_does_not_write_and_jsonl_report_is_sanitized(tmp_path: Path) -> None:
    wiki, data = tmp_path / "private-home" / "wiki", tmp_path / "data"
    target = write_page(wiki, "notes/a.md", {"project": "Fixture"}, "secret body\n")
    before = target.read_bytes()
    ctx = operation(wiki, data, apply=False)
    report = apply_backfill(plan_backfill(wiki, None, None, ctx), ctx)
    assert target.read_bytes() == before
    assert report.counts == {"planned": 1}
    records = [json.loads(line) for line in report.report_path.read_text().splitlines()]
    rendered = json.dumps(records)
    assert "notes/a.md" in rendered
    assert str(tmp_path) not in rendered
    assert "secret body" not in rendered


def test_symlink_escape_malformed_non_utf8_and_bad_cursor_are_rejected(tmp_path: Path) -> None:
    wiki, data, outside = tmp_path / "wiki", tmp_path / "data", tmp_path / "outside.md"
    wiki.mkdir()
    outside.write_bytes(b"---\nproject: outside\n---\nbody\n")
    (wiki / "escape.md").symlink_to(outside)
    (wiki / "malformed.md").write_bytes(b"---\nscope: project\nno close\n")
    (wiki / "non-utf8.md").write_bytes(b"---\ntitle: \xff\n---\nbody\n")
    plan = plan_backfill(wiki, None, None, operation(wiki, data))
    assert plan.entries == ()
    assert sorted(issue.result for issue in plan.issues) == ["malformed_frontmatter", "malformed_frontmatter", "unsafe_path"]
    with pytest.raises(ValueError):
        plan_backfill(wiki, "../outside.md", None, operation(wiki, data))
    with pytest.raises(ValueError):
        plan_backfill(wiki, "https://example.invalid/x", None, operation(wiki, data))
    with pytest.raises(ValueError):
        plan_backfill(wiki, "bad\x00cursor", None, operation(wiki, data))


def test_before_image_mode_and_failure_rolls_back_in_reverse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wiki, data = tmp_path / "wiki", tmp_path / "data"
    originals = {}
    for name in ("a.md", "b.md", "c.md"):
        target = write_page(wiki, name, {"project": "Fixture"}, f"body {name}\n")
        originals[name] = target.read_bytes()
    plan = plan_backfill(wiki, None, None, operation(wiki, data))

    import scripts.automation_core.scope as scope_module

    real_replace = scope_module.atomic_replace_same_dir
    calls = []

    def fail_on_third(path: Path, content: bytes) -> None:
        calls.append(path.name)
        if path.name == "c.md":
            raise OSError("injected write failure")
        real_replace(path, content)

    monkeypatch.setattr(scope_module, "atomic_replace_same_dir", fail_on_third)
    with pytest.raises(OSError, match="injected write failure"):
        apply_backfill(plan, operation(wiki, data))
    assert [path.name for path in plan.entries] == ["a.md", "b.md", "c.md"]
    assert all((wiki / name).read_bytes() == content for name, content in originals.items())
    before_dir = data / "transactions" / operation(wiki, data).operation_id / "scope-before"
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in before_dir.rglob("*.before"))
    journal = data / "transactions" / operation(wiki, data).operation_id / "scope-journal.jsonl"
    assert journal.is_file() and "sha256" in journal.read_text()


def test_cli_and_distill_entry_points_use_scope_inference(tmp_path: Path) -> None:
    wiki, data, home = tmp_path / "wiki", tmp_path / "data", tmp_path / "home"
    write_page(wiki, "a.md", {"project": "Fixture"}, "body\n")
    env = dict(os.environ, HOME=str(home), WIKI_PATH=str(wiki), MEMORY_HUB_DATA=str(data),
               MEMORY_HUB_USER_SCOPE_ID="fixture-user", PYTHONPATH=str(ROOT))
    dry = subprocess.run(["bash", "memory-hub.sh", "scope-backfill", "--json"], cwd=ROOT,
                         env=env, text=True, capture_output=True)
    assert dry.returncode == 0 and json.loads(dry.stdout)["counts"] == {"planned": 1}
    applied = subprocess.run(["bash", "memory-hub.sh", "scope-backfill", "--apply", "--json"], cwd=ROOT,
                             env=env, text=True, capture_output=True)
    assert applied.returncode == 0 and parse_page(wiki / "a.md").frontmatter["scope"] == "project"

    staging = tmp_path / "staging"
    staging.mkdir()
    observations = staging / "observations-fixture.jsonl"
    observations.write_text(json.dumps({"project_id": "Road Map", "project": "Road Map", "type": "message",
                                        "role": "user", "text": "fixture", "id": "1"}) + "\n")
    distill_env = dict(env, MEMORY_HUB_STAGING=str(staging))
    distilled = subprocess.run(["bash", "scripts/distill.sh", str(observations)], cwd=ROOT,
                               env=distill_env, text=True, capture_output=True)
    assert distilled.returncode == 0, distilled.stderr
    created = next((staging / "pages").glob("*.md"))
    frontmatter = parse_page(created).frontmatter
    assert (frontmatter["scope"], frontmatter["scope_id"], frontmatter["scope_source"]) == (
        "project", "road-map", "session_meta")
