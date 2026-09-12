import sys, pathlib
_ROOT = str(pathlib.Path(__file__).resolve().parents[1])
if _ROOT not in sys.path: sys.path.insert(0, _ROOT)
#!/usr/bin/env python3
"""memory-hub fuse: FTS5(bm25) + 向量检索 融合 (RRF k=60) + 时间衰减(F2)"""
import argparse
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from scripts.automation_core.indexer import IndexedPage, load_page_records
from scripts.automation_core.query_planner import ExpansionTerm, QueryPlan, SearchRequest
from scripts.automation_core.ranker import (
    DEFAULT_TAU,
    NullMetrics,
    RecallHit,
    normalized_weights,
    rank_results,
    render_human,
)


def _filter_lines(out):
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[(-?\d*\.?\d+)\]\s+(\S+?\.md)(\s|$)", line)
        if m:
            yield float(m.group(1)), m.group(2)


def main():
    ap = argparse.ArgumentParser(description="FTS5 + 向量 RRF 融合检索(含时间衰减)")
    ap.add_argument("query", help="检索关键词")
    ap.add_argument("--top", type=int, default=10, help="返回条数(默认 10)")
    ap.add_argument("--tau", type=float, default=DEFAULT_TAU, help="时间衰减常数(天),默认 90; --tau 0 关闭")
    args = ap.parse_args()

    from scripts.automation_core.service import MemoryService
    hub = pathlib.Path(__file__).resolve().parents[1]
    wiki = pathlib.Path(os.environ.get("WIKI_PATH", str(pathlib.Path.home() / "llm-wiki")))
    data = pathlib.Path(os.environ.get("MEMORY_HUB_DATA", str(pathlib.Path.home() / ".memory-hub")))
    q = args.query
    request = SearchRequest(query=q, top=args.top, fuse=True, expand=False)
    results = MemoryService(wiki, data, hub).search(request, tau=args.tau).results

    header = "== 混合检索 (RRF k=60"
    header += f", 时间衰减 tau={int(args.tau)}d)" if args.tau and args.tau > 0 else ", 无时间衰减)"
    print(f"{header}: {q} ==")
    if not results:
        print("无命中")
        return
    for r in results:
        print(f"[{r.score:.3f}] {r.path}")


def plan_with_expansion():
    return QueryPlan(
        query="q", query_hash="h",
        expansions=(ExpansionTerm("x", 0.3),),
        planner="llm", fallback_reason=None, l0_snippets=(), latency_ms=0.0
    )


def selfcheck_request():
    return SearchRequest(query="q", top=2)


def selfcheck_plan():
    return plan_with_expansion()


def selfcheck_recalls():
    return {
        "original": [RecallHit("old.md", 0.9, 1), RecallHit("active.md", 0.8, 2)],
        "expansion_0": [RecallHit("new.md", 0.85, 1)],
    }


def selfcheck_pages():
    now = datetime.now(timezone.utc)
    return {
        "old.md": IndexedPage(
            path="old.md", title="Old", type="note", tags="", abstract="",
            content="---\nstatus: deprecated\ndeprecated_by: '[[new.md]]'\n---\n",
            scope="project", scope_id="default-project", scope_confidence="high", status="deprecated",
            updated=(now - timedelta(days=200)).strftime("%Y-%m-%d"),
            last_verified="", valid_at="", invalid_at="",
        ),
        "new.md": IndexedPage(
            path="new.md", title="New", type="note", tags="", abstract="",
            content="---\nstatus: active\n---\n",
            scope="project", scope_id="default-project", scope_confidence="high", status="active",
            updated=(now - timedelta(days=5)).strftime("%Y-%m-%d"),
            last_verified="", valid_at="", invalid_at="",
        ),
        "active.md": IndexedPage(
            path="active.md", title="Active", type="note", tags="", abstract="",
            content="---\nstatus: active\n---\n",
            scope="project", scope_id="default-project", scope_confidence="high", status="active",
            updated=(now - timedelta(days=10)).strftime("%Y-%m-%d"),
            last_verified="", valid_at="", invalid_at="",
        ),
    }


def _selfcheck():
    weights = normalized_weights(plan_with_expansion())
    assert weights == {"original": 0.70, "expansion_0": 0.30}, weights
    ranked = rank_results(selfcheck_request(), selfcheck_plan(), selfcheck_recalls(), selfcheck_pages(), NullMetrics())
    assert [r.path for r in ranked] == ["new.md", "active.md"], [r.path for r in ranked]
    print("fuse._selfcheck OK")


if __name__ == "__main__":
    if os.environ.get("MH_FUSE_SELFCHECK") == "1":
        _selfcheck()
    else:
        main()

