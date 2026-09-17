"""P2 one-hop neighbor injection + stale decay."""
from __future__ import annotations

from datetime import date, timedelta

from scripts.automation_core.indexer import IndexedPage
from scripts.automation_core.query_planner import ExpansionTerm, QueryPlan, SearchRequest
from scripts.automation_core.ranker import RecallHit, rank_results


def _iso(days_ago: int = 0) -> str:
    """ISO date relative to today so freshness assertions never rot."""
    return (date.today() - timedelta(days=days_ago)).isoformat()


def make_page(path, status="active", scope="project", scope_id="p",
               scope_confidence="high", title="Title", ptype="note",
               updated=None, deprecated_by=""):
    if updated is None:
        updated = _iso(0)
    content = "---\nstatus: " + status + "\n"
    if deprecated_by:
        content += "deprecated_by: '" + deprecated_by + "'\n"
    content += "---\nbody of " + path + "\n"
    return IndexedPage(
        path=path, title=title, type=ptype, tags="", abstract="abstract",
        content=content, scope=scope, scope_id=scope_id,
        scope_confidence=scope_confidence, status=status,
        updated=updated, last_verified=updated,
        valid_at=updated, invalid_at="",
    )


def _plan():
    return QueryPlan(query="q", query_hash="h", expansions=(),
                     planner="original-only", fallback_reason=None,
                     l0_snippets=(), latency_ms=1.0)


def test_zero_overlap_neighbor_is_injected():
    recalls = {"original": [RecallHit("hub.md", 0.9, 1)]}
    pages = {
        "hub.md": make_page("hub.md"),
        "spoke.md": make_page("spoke.md"),
    }
    links = {"hub.md": ["spoke.md"], "spoke.md": ["hub.md"]}
    ranked = rank_results(SearchRequest("completely unrelated query words", top=5),
                          _plan(), recalls, pages, links=links)
    paths = [r.path for r in ranked]
    assert paths[0] == "hub.md"
    assert "spoke.md" in paths
    spoke = next(r for r in ranked if r.path == "spoke.md")
    assert spoke.rank_reason.get("via") == "neighbor"


def test_no_links_no_injection():
    recalls = {"original": [RecallHit("hub.md", 0.9, 1)]}
    pages = {
        "hub.md": make_page("hub.md"),
        "spoke.md": make_page("spoke.md"),
    }
    ranked = rank_results(SearchRequest("q", top=5), _plan(), recalls, pages)
    assert [r.path for r in ranked] == ["hub.md"]


def test_deprecated_neighbor_folds_to_successor():
    recalls = {"original": [RecallHit("hub.md", 0.9, 1)]}
    pages = {
        "hub.md": make_page("hub.md"),
        "old.md": make_page("old.md", status="deprecated", deprecated_by="[[new.md]]"),
        "new.md": make_page("new.md"),
    }
    links = {"hub.md": ["old.md"], "old.md": ["hub.md"]}
    ranked = rank_results(SearchRequest("q", top=5), _plan(), recalls, pages, links=links)
    paths = [r.path for r in ranked]
    assert "new.md" in paths
    assert "old.md" not in paths


def test_scope_filter_applies_to_injected():
    recalls = {"original": [RecallHit("hub.md", 0.9, 1)]}
    pages = {
        "hub.md": make_page("hub.md", scope_id="p"),
        "spoke.md": make_page("spoke.md", scope_id="other"),
    }
    links = {"hub.md": ["spoke.md"], "spoke.md": ["hub.md"]}
    ranked = rank_results(SearchRequest("q", top=5, scope="project", scope_id="p"),
                          _plan(), recalls, pages, links=links)
    assert [r.path for r in ranked] == ["hub.md"]


def test_stale_page_ranks_after_fresh_with_equal_base():
    recalls = {
        "original_fts": [RecallHit("old.md", 0.9, 1)],
        "original_vec": [RecallHit("new.md", 0.9, 1)],
    }
    pages = {
        "old.md": make_page("old.md", updated=_iso(200)),
        "new.md": make_page("new.md", updated=_iso(0)),
    }
    ranked = rank_results(SearchRequest("q", top=5), _plan(), recalls, pages, tau=0)
    assert [r.path for r in ranked] == ["new.md", "old.md"]
