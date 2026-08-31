from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.automation_core.frontmatter import parse_page
from scripts.automation_core.indexer import IndexBuild
from scripts.automation_core.lifecycle import (
    InjectedFailure,
    LifecycleOrderError,
    PreparedLifecycle,
    SuccessorPlan,
    finalize_successor_after_index,
    prepare_successor_pages,
    publish_successor_once,
    successor_plan,
)
from scripts.automation_core.operation import (
    FailureHook,
    GitBaseline,
    TransactionContext,
    begin_transaction,
)
from scripts.automation_core.schema import Mode, OperationContext
from tests.helpers.full_auto_fixture import write_page


class FakeEmbedding:
    def __init__(self, sim: float, entities: bool, changed: bool) -> None:
        self._sim = sim
        self._entities = entities
        self._changed = changed

    def similarity(self, doc1: Any, doc2: Any) -> float:
        return self._sim

    def entities_match(self, doc1: Any, doc2: Any) -> bool:
        return self._entities

    def comparable_change(self, doc1: Any, doc2: Any) -> bool:
        return self._changed


class LifecycleFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.wiki = tmp_path / "wiki"
        self.data = tmp_path / "data"
        self.wiki.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)
        self.index_swap_calls = 0

        self.old_path = write_page(
            self.wiki,
            "old.md",
            {"title": "Old Knowledge", "scope": "project", "scope_id": "p", "status": "active"},
            "old content\n",
        )
        self.new_path = write_page(
            self.wiki,
            "new.md",
            {"title": "Old Knowledge Revised", "scope": "project", "scope_id": "p", "status": "active"},
            "new content\n",
        )
        (self.data / "index.db").write_text("index-data", encoding="utf-8")

        self.old = parse_page(self.old_path)
        self.new = parse_page(self.new_path)
        self.plan = SuccessorPlan(
            decision="successor",
            new_path=self.new_path,
            old_path=self.old_path,
            new_page=self.new,
            old_page=self.old,
            similarity=0.92,
            entities_match=True,
            comparable_change=True,
            now=self.now,
        )

    def transaction(self, failure_hook: FailureHook | None = None) -> TransactionContext:
        ctx = OperationContext(
            operation_id="20260831T000000Z-lifecycle-test",
            command="maintain",
            mode=Mode.AUTO,
            auto=True,
            apply=True,
            wiki_path=self.wiki,
            data_path=self.data,
        )
        return begin_transaction(ctx, GitBaseline((), ()), failure_hook=failure_hook)

    def snapshot(self, pages: bool = True, frontmatter: bool = True, index: bool = True, manifest: bool = True) -> dict[str, str]:
        snap = {}
        for p in self.wiki.rglob("*.md"):
            snap[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
        if (self.data / "index.db").is_file():
            snap["index"] = hashlib.sha256((self.data / "index.db").read_bytes()).hexdigest()
        return snap

    def rebuild_index(self, tx: TransactionContext) -> IndexBuild:
        self.index_swap_calls += 1
        return IndexBuild(self.data / "index.db", 2, 2, 0)

    def swap_index_once(self, tx: TransactionContext, prepared: PreparedLifecycle) -> None:
        self.rebuild_index(tx)
        tx.journal.checkpoint("INDEX_SWAPPED")

    def publish_successor_once(self, plan: SuccessorPlan, tx: TransactionContext) -> Any:
        return publish_successor_once(plan, tx, self.rebuild_index)


@pytest.fixture
def lifecycle_fixture(tmp_path: Path) -> LifecycleFixture:
    return LifecycleFixture(tmp_path)


@pytest.mark.parametrize("similarity,entities,changed,decision", [
    (0.88, True, True, "successor"),
    (0.879, True, True, "related-not-successor"),
    (0.95, False, True, "related-not-successor"),
    (0.40, True, True, "independent"),
])
def test_successor_thresholds(similarity: float, entities: bool, changed: bool, decision: str, lifecycle_fixture: LifecycleFixture) -> None:
    plan = successor_plan(lifecycle_fixture.new, [lifecycle_fixture.old], FakeEmbedding(similarity, entities, changed), lifecycle_fixture.now)
    assert plan.decision == decision


def raise_at(name: str) -> FailureHook:
    def hook(point: str) -> None:
        if point == name:
            raise InjectedFailure(point)
    return hook


@pytest.mark.parametrize("point", ["prepare.rename_new", "index.before_swap", "finalize.verify_pair"])
def test_transaction_hook_restores_pair_index_and_manifest_at_each_lifecycle_boundary(lifecycle_fixture: LifecycleFixture, point: str) -> None:
    before = lifecycle_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True)
    tx = lifecycle_fixture.transaction(failure_hook=raise_at(point))
    with pytest.raises(InjectedFailure, match=point):
        lifecycle_fixture.publish_successor_once(lifecycle_fixture.plan, tx)
    assert lifecycle_fixture.snapshot(pages=True, frontmatter=True, index=True, manifest=True) == before
    assert tx.journal.rollback_order == ("manifest", "index", "pages")


def test_prepare_replay_is_noop(lifecycle_fixture: LifecycleFixture) -> None:
    tx = lifecycle_fixture.transaction()
    prepared = prepare_successor_pages(lifecycle_fixture.plan, tx)
    assert prepared.tx is tx
    assert tx.journal.state == "OLD_RENAMED"
    assert tx.journal.checkpoints[-1] == "OLD_RENAMED"
    lifecycle_fixture.swap_index_once(tx, prepared)
    first = finalize_successor_after_index(prepared, tx)
    second = finalize_successor_after_index(prepared, tx)
    assert (first.result, second.result) == ("committed", "idempotent_skip")


def test_prepare_never_swaps_and_finalize_requires_the_callers_index_checkpoint(lifecycle_fixture: LifecycleFixture) -> None:
    tx = lifecycle_fixture.transaction()
    prepared = prepare_successor_pages(lifecycle_fixture.plan, tx)
    assert lifecycle_fixture.index_swap_calls == 0
    with pytest.raises(LifecycleOrderError):
        finalize_successor_after_index(prepared, tx)
    lifecycle_fixture.swap_index_once(tx, prepared)
    assert finalize_successor_after_index(prepared, tx).result == "committed"
    assert lifecycle_fixture.index_swap_calls == 1


def test_standalone_publish_wrapper_swaps_exactly_once(lifecycle_fixture: LifecycleFixture) -> None:
    result = publish_successor_once(lifecycle_fixture.plan, lifecycle_fixture.transaction(), lifecycle_fixture.rebuild_index)
    assert result.result == "committed"
    assert lifecycle_fixture.index_swap_calls == 1
