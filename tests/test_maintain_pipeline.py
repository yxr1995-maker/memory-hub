"""Real MaintainStageRunner pipeline tests: failure injection, rollback, dirty protection, replay.

These tests exercise maintain_pipeline with the real MaintainStageRunner over
isolated wiki/data/staging roots. Failure injection uses the documented hook
points: maint.<stage>.before / maint.<stage>.after plus index.before_swap /
index.after_swap. Rollback internals (force=True) belong to the operation
layer; these tests assert observable end state only.
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest

from scripts.automation_core.operation import GitBaseline, begin_transaction
from scripts.automation_core.orchestrator import MaintainStageRunner, maintain_pipeline
from scripts.automation_core.schema import Mode, OperationContext

from tests.helpers.full_auto_fixture import write_page

MANIFEST_REL = Path("manifests") / "cluster-observations-v1.json"
OPERATION_SEQ = itertools.count(1)
EXPECTED_OBSERVATIONS = {
    "observations-20260901-120000.jsonl",
    "observations-20260902-120000.jsonl",
}


class InjectedFailure(RuntimeError):
    pass


def raise_at(point: str, *actions: Callable[[], None]) -> Callable[[str], None]:
    def hook(name: str) -> None:
        if name == point:
            for action in actions:
                action()
            raise InjectedFailure(point)

    return hook


@dataclass
class MaintainFixture:
    wiki: Path
    data: Path
    staging: Path
    env: dict

    def transaction(self, failure_hook: Callable[[str], None] | None = None):
        operation = OperationContext(
            operation_id=f"maint-pipeline-{next(OPERATION_SEQ):04d}",
            command="maintain",
            mode=Mode.AUTO,
            auto=True,
            apply=True,
            wiki_path=self.wiki,
            data_path=self.data,
        )
        return begin_transaction(operation, GitBaseline.capture(self.wiki), failure_hook)

    def runner(self, **kwargs) -> MaintainStageRunner:
        return MaintainStageRunner(staging=self.staging, **kwargs)

    def manifest(self) -> Path:
        return self.data / MANIFEST_REL

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=self.wiki, env=self.env,
            check=check, capture_output=True, text=True,
        )

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").stdout.strip()

    def status(self) -> str:
        return self.git("status", "--porcelain").stdout.strip()

    def cluster_pages(self) -> list[Path]:
        return sorted(self.wiki.glob("moc/cluster-*.md"))

    def observation_names(self) -> set[str]:
        return {p.name for p in self.staging.glob("observations-*.jsonl")}


@pytest.fixture
def fx(tmp_path: Path) -> MaintainFixture:
    wiki, data, staging = (tmp_path / name for name in ("wiki", "data", "staging"))
    for path in (wiki, data, staging, tmp_path / "home"):
        path.mkdir()
    env = dict(
        PATH=os.environ.get("PATH", "/usr/bin:/bin"),
        HOME=str(tmp_path / "home"),
        WIKI_PATH=str(wiki),
        MEMORY_HUB_DATA=str(data),
        MEMORY_HUB_STAGING=str(staging),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
    )
    env.pop("MEMORY_HUB_OPERATION_ID", None)
    for args in (("init",), ("config", "user.name", "Fixture"),
                 ("config", "user.email", "fixture@example.invalid")):
        subprocess.run(["git", *args], cwd=wiki, env=env, check=True, capture_output=True)
    write_page(wiki, "notes/baseline.md", {"title": "Baseline", "type": "note",
               "status": "active", "scope": "project", "scope_id": "other",
               "created": "2026-09-01", "updated": "2026-09-01"}, "Baseline unrelated content.\n")
    fixture_env = dict(env)
    fixture = MaintainFixture(wiki=wiki, data=data, staging=staging, env=fixture_env)
    fixture.git("add", "notes/baseline.md")
    fixture.git("commit", "-m", "fixture")
    for day, ids in (("20260901", (1, 2)), ("20260902", (3,))):
        rows = [{"id": f"maintain-{n}", "project_id": "fixture-project",
                 "text": f"Memory maintenance preserves exact owned changes and isolated indexes item {n}.",
                 "created_at_epoch": 1788220800 + (86400 if n == 3 else 0)} for n in ids]
        (staging / f"observations-{day}-120000.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return fixture


def test_auto_run_commits_then_replay_is_idempotent(fx: MaintainFixture) -> None:
    baseline = fx.head()
    first = maintain_pipeline(fx.transaction(), fx.runner())
    assert first.result == "committed", first.error
    committed_head = fx.head()
    assert committed_head != baseline
    pages = fx.cluster_pages()
    assert len(pages) == 1
    page_rel = pages[0].relative_to(fx.wiki).as_posix()
    with sqlite3.connect(f"file:{fx.data / 'index.db'}?mode=ro", uri=True) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select count(*) from pages where path = ?", (page_rel,)).fetchone()[0] == 1
    manifest_bytes = fx.manifest().read_bytes()
    assert len(json.loads(manifest_bytes)["entries"]) == 1
    assert fx.status() == ""

    second = maintain_pipeline(fx.transaction(), fx.runner())
    assert second.result == "applied_no_commit", second.error
    assert fx.cluster_pages() == pages
    assert fx.manifest().read_bytes() == manifest_bytes
    assert fx.head() == committed_head
    assert fx.status() == ""


def test_failure_before_index_swap_restores_pages_and_keeps_observations(fx: MaintainFixture) -> None:
    tx = fx.transaction(raise_at("index.before_swap"))
    report = maintain_pipeline(tx, fx.runner())
    assert report.result == "failed", report.error
    assert report.failed_stage == "index_swap"
    assert fx.cluster_pages() == []
    assert not (fx.data / "index.db").exists()
    assert not fx.manifest().exists()
    assert fx.status() == ""
    assert fx.observation_names() == EXPECTED_OBSERVATIONS


def test_failure_after_manifest_write_restores_manifest_index_and_pages(fx: MaintainFixture) -> None:
    tx = fx.transaction(raise_at("maint.atomic_manifest_commit.after"))
    report = maintain_pipeline(tx, fx.runner())
    assert report.result == "failed", report.error
    assert report.failed_stage == "atomic_manifest_commit"
    assert fx.cluster_pages() == []
    assert not (fx.data / "index.db").exists()
    assert not fx.manifest().exists()
    assert fx.status() == ""
    assert fx.observation_names() == EXPECTED_OBSERVATIONS
    assert any(key.endswith("index.db") for key in tx.journal.before_images)


@pytest.mark.parametrize(
    "failure_point",
    [
        "maint.lint.before",
        "maint.lint.after",
        "maint.archive.after",
        "maint.exact_stage_commit.before",
    ],
)
def test_late_failure_points_rollback_to_clean_baseline(fx: MaintainFixture, failure_point: str) -> None:
    baseline = fx.head()
    tx = fx.transaction(raise_at(failure_point))
    report = maintain_pipeline(tx, fx.runner())
    assert report.result == "failed", report.error
    assert fx.head() == baseline
    assert fx.status() == ""
    assert fx.cluster_pages() == []
    assert not (fx.data / "index.db").exists()
    assert not fx.manifest().exists()
    assert fx.observation_names() == EXPECTED_OBSERVATIONS


def test_external_commit_during_failure_is_preserved(fx: MaintainFixture) -> None:
    baseline = fx.head()

    def hook(name: str) -> None:
        if name == "maint.atomic_manifest_commit.before":
            (fx.wiki / "user.md").write_text("user external note\n")
            fx.git("add", "user.md")
            fx.git("commit", "-m", "external commit")
            raise InjectedFailure(name)

    tx = fx.transaction(hook)
    report = maintain_pipeline(tx, fx.runner())
    assert report.result == "failed", report.error
    assert fx.head() != baseline
    assert fx.git("log", "-1", "--pretty=%s").stdout.strip() == "external commit"
    assert (fx.wiki / "user.md").read_text() == "user external note\n"
    assert fx.cluster_pages() == []
    assert fx.status() == ""


def test_rollback_preserves_unrelated_unstaged_changes(fx: MaintainFixture) -> None:
    baseline_page = fx.wiki / "notes" / "baseline.md"
    baseline_page.write_text("user edited baseline locally\n")
    tx = fx.transaction(raise_at("index.before_swap"))
    report = maintain_pipeline(tx, fx.runner())
    assert report.result == "failed", report.error
    assert baseline_page.read_text() == "user edited baseline locally\n"
    assert " M notes/baseline.md" in fx.git("status", "--porcelain").stdout
    assert fx.cluster_pages() == []


def test_preexisting_staged_changes_block_without_mutation(fx: MaintainFixture) -> None:
    baseline = fx.head()
    (fx.wiki / "user.md").write_text("staged by user\n")
    fx.git("add", "user.md")
    tx = fx.transaction()
    report = maintain_pipeline(tx, fx.runner())
    assert report.result == "preexisting_staged"
    assert report.stage_names == []
    assert fx.head() == baseline
    assert (fx.wiki / "user.md").read_text() == "staged by user\n"
    assert fx.git("diff", "--cached", "--name-only").stdout.strip() == "user.md"
    assert fx.cluster_pages() == []
