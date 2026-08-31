from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from scripts.automation_core.cluster import (
    ClusterManifest,
    ClusterObservation,
    ClusterPlan,
    ManifestEntry,
    cluster_observations,
    commit_manifest,
    load_manifest,
    render_merge_page,
    scan_observations,
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
    def __init__(self, cosine: float = 0.80) -> None:
        self._cosine = cosine

    def cosine(self) -> float:
        return self._cosine


class ClusterFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.wiki = tmp_path / "wiki"
        self.data = tmp_path / "data"
        self.staging = tmp_path / "staging"
        self.wiki.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

        self.manifest_path = self.data / "manifests" / "cluster-observations-v1.json"
        self.three_members_two_days = [
            ClusterObservation("id-1", "p", "text for item 1 in project p", 1788100000, "2026-08-30"),
            ClusterObservation("id-2", "p", "text for item 2 in project p", 1788100000, "2026-08-30"),
            ClusterObservation("id-3", "p", "text for item 3 in project p", 1788200000, "2026-08-31"),
        ]
        (self.data / "index.db").write_text("index-data", encoding="utf-8")

    def transaction(self, failure_hook: FailureHook | None = None) -> TransactionContext:
        ctx = OperationContext(
            operation_id="20260831T000000Z-cluster-test",
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
        if self.manifest_path.is_file():
            snap["manifest"] = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
        return snap

    def publish(self, plan: ClusterPlan) -> Any:
        tx = self.transaction()
        tx.journal.checkpoint("PAGES_LIFECYCLE_PUBLISHED")
        tx.journal.checkpoint("INDEX_SWAPPED")
        tx.journal.checkpoint("LINT_PASSED")
        page_bytes = render_merge_page(plan)
        target = self.wiki / f"merged-{plan.key}.md"
        target.write_bytes(page_bytes)

        entry = ManifestEntry(
            cluster_key=plan.key,
            observation_hashes=[hashlib.sha256(m.id.encode()).hexdigest() for m in plan.members],
            page_path=str(target.relative_to(self.wiki)),
            content_hash=hashlib.sha256(page_bytes).hexdigest(),
            operation_id=tx.operation.operation_id,
            created_at="2026-08-31T12:00:00Z",
        )
        commit_manifest(self.manifest_path, entry, tx)
        
        class Result:
            manifest = load_manifest(self.manifest_path)
            page_path = str(target.relative_to(self.wiki))
        return Result()

    def maintain_again(self) -> Any:
        tx = self.transaction()
        tx.journal.checkpoint("PAGES_LIFECYCLE_PUBLISHED")
        tx.journal.checkpoint("INDEX_SWAPPED")
        tx.journal.checkpoint("LINT_PASSED")
        plans = cluster_observations(self.three_members_two_days, FakeEmbedding(cosine=0.80))
        results = []
        for p in plans:
            entry = ManifestEntry(
                cluster_key=p.key,
                observation_hashes=[],
                page_path="",
                content_hash="",
                operation_id=tx.operation.operation_id,
                created_at="2026-08-31T12:00:00Z",
            )
            res = commit_manifest(self.manifest_path, entry, tx)
            results.append(res.result)
        
        class SecondResult:
            pass
        sr = SecondResult()
        sr.results = results
        return sr

    def cluster_page_count(self) -> int:
        return len(list(self.wiki.glob("merged-*.md")))


@pytest.fixture
def cluster_fixture(tmp_path: Path) -> ClusterFixture:
    return ClusterFixture(tmp_path)


def test_cross_day_cluster_key_and_manifest_replay(cluster_fixture: ClusterFixture) -> None:
    plans = cluster_observations(cluster_fixture.three_members_two_days, FakeEmbedding(cosine=0.80))
    expected_key = hashlib.sha256("id-1\nid-2\nid-3".encode()).hexdigest()[:16]
    assert [(p.key, len(p.members), p.method) for p in plans] == [(expected_key, 3, "embedding")]
    first = cluster_fixture.publish(plans[0])
    second = cluster_fixture.maintain_again()
    assert first.manifest.entries[expected_key].page_path == first.page_path
    assert second.results[0] == "manifest_skip"
    assert cluster_fixture.cluster_page_count() == 1
