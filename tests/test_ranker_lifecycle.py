from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import pytest

from scripts.automation_core.indexer import IndexedPage
from scripts.automation_core.query_planner import ExpansionTerm, QueryPlan, SearchRequest
from scripts.automation_core.ranker import (
    MetricSink,
    NullMetrics,
    RecallHit,
    SearchResult,
    normalized_weights,
    rank_results,
    render_human,
    resolve_successor,
)


class Scope(Enum):
    PROJECT = "project"
    USER = "user"
    AGENT = "agent"


def make_page(
    path: str,
    status: str = "active",
    scope: str = "project",
    scope_id: str = "p",
    scope_confidence: str = "high",
    title: str = "Title",
    ptype: str = "note",
    updated: str = "2026-08-31",
    deprecated_by: str = "",
) -> IndexedPage:
    content = f"---\nstatus: {status}\n"
    if deprecated_by:
        content += f"deprecated_by: '{deprecated_by}'\n"
    content += f"---\nbody of {path}\n"
    return IndexedPage(
        path=path,
        title=title,
        type=ptype,
        tags="",
        abstract="abstract",
        content=content,
        scope=scope,
        scope_id=scope_id,
        scope_confidence=scope_confidence,
        status=status,
        updated=updated,
        last_verified=updated,
        valid_at=updated,
        invalid_at="",
    )


def test_rrf_weights_scope_and_successor_replacement() -> None:
    plan = QueryPlan(
        query="q",
        query_hash="hash1",
        expansions=(ExpansionTerm("x", 0.30),),
        planner="llm",
        fallback_reason=None,
        l0_snippets=(),
        latency_ms=10.0,
    )
    recalls = {
        "original_fts": [RecallHit("old.md", 0.9, 1), RecallHit("active.md", 0.8, 2)],
        "expansion_0": [RecallHit("new.md", 0.85, 1)],
    }
    pages = {
        "old.md": make_page("old.md", status="deprecated", deprecated_by="[[new.md]]", scope_id="p"),
        "new.md": make_page("new.md", status="active", scope_id="p"),
        "active.md": make_page("active.md", status="active", scope_id="p"),
    }
    metrics = MetricSink()
    request = SearchRequest("q", top=2, scope=Scope.PROJECT, scope_id="p")

    ranked = rank_results(request, plan, recalls, pages, metrics)
    assert len(ranked) == 2
    assert [r.path for r in ranked] == ["new.md", "active.md"]
    assert ranked[0].rank_reason["via"] == "successor"
    assert all((r.scope, r.scope_id) == ("project", "p") for r in ranked)


def test_cycle_is_only_used_as_shortfall_and_emits_metric() -> None:
    plan = QueryPlan(
        query="q",
        query_hash="hash2",
        expansions=(),
        planner="original-only",
        fallback_reason=None,
        l0_snippets=(),
        latency_ms=5.0,
    )
    recalls = {
        "original": [RecallHit("active.md", 0.9, 1), RecallHit("cycle_a.md", 0.8, 2)],
    }
    pages = {
        "active.md": make_page("active.md", status="active"),
        "cycle_a.md": make_page("cycle_a.md", status="deprecated", deprecated_by="[[cycle_b.md]]"),
        "cycle_b.md": make_page("cycle_b.md", status="deprecated", deprecated_by="[[cycle_a.md]]"),
    }
    metrics = MetricSink()
    request = SearchRequest("q", top=2)

    ranked = rank_results(request, plan, recalls, pages, metrics)
    assert len(ranked) == 2
    assert [r.status for r in ranked] == ["active", "lifecycle_error"]
    assert metrics.value("memory_hub_lifecycle_cycle_total") >= 1


def test_render_human_formatting() -> None:
    results = [
        SearchResult("notes/a.md", 0.1234, "active", "project", "p", "high", "A", "", "note", {}),
        SearchResult("notes/b.md", 0.0567, "active", "project", "p", "high", "B", "", "note", {}),
    ]
    text = render_human(results)
    assert "[0.123] notes/a.md" in text
    assert "[0.057] notes/b.md" in text

