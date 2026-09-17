"""rejected pages never surface from MemoryService.search on any recall path."""
from __future__ import annotations

import tempfile
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]

from scripts.automation_core.indexer import atomic_rebuild_index
from scripts.automation_core.query_planner import SearchRequest
from scripts.automation_core.service import MemoryService

DROP_TOKEN = "droptokenuniq9371"
KEEP_TOKEN = "keeptokenuniq9371"


def _page(title, token, status="active"):
    return (
        "---\ntitle: " + title + "\ntype: note\nstatus: " + status
        + "\nscope: project\nscope_id: default-project\n---\n\n"
        + token + " body text for " + title + ".\n"
    )


def _svc(wiki: Path, data: Path) -> MemoryService:
    return MemoryService(wiki, data, HUB)


def _paths(resp) -> list:
    return [r.path for r in resp.results]


def _search(svc, token):
    return svc.search(SearchRequest(token, top=5, fuse=False, expand=False))


def test_rejected_hidden_across_recall_paths():
    with tempfile.TemporaryDirectory(prefix="m6-rejected-") as tmp:
        tmp_p = Path(tmp)
        wiki = tmp_p / "wiki"
        data = tmp_p / "data"
        wiki.mkdir()
        data.mkdir()
        (wiki / "keep.md").write_text(_page("keep page", KEEP_TOKEN))
        (wiki / "drop.md").write_text(_page("drop page", DROP_TOKEN))
        atomic_rebuild_index(wiki, data)
        svc = _svc(wiki, data)

        # positive control: active page is returned
        assert "keep.md" in _paths(_search(svc, KEEP_TOKEN))
        assert "drop.md" in _paths(_search(svc, DROP_TOKEN))

        # A: rejected on disk but index still stale -> must not return
        (wiki / "drop.md").write_text(_page("drop page", DROP_TOKEN, status="rejected"))
        assert "drop.md" not in _paths(_search(svc, DROP_TOKEN))
        assert "keep.md" in _paths(_search(svc, KEEP_TOKEN))

        # B: after rebuild (index excludes it; recall falls through to rg) -> must not return
        atomic_rebuild_index(wiki, data)
        assert "drop.md" not in _paths(_search(svc, DROP_TOKEN))
        assert "keep.md" in _paths(_search(svc, KEEP_TOKEN))

        # C: index.db missing entirely (rg fallback + placeholder pages) -> must not return
        (data / "index.db").unlink()
        assert "drop.md" not in _paths(_search(svc, DROP_TOKEN))
        assert "keep.md" in _paths(_search(svc, KEEP_TOKEN))


def test_top1_rejected_promotes_next_active():
    """Filtering happens pre-truncation: a top-ranked rejected page must
    promote the next active page instead of returning empty for top=1."""
    with tempfile.TemporaryDirectory(prefix="m6-rejected-top1-") as tmp:
        tmp_p = Path(tmp)
        wiki = tmp_p / "wiki"
        data = tmp_p / "data"
        wiki.mkdir()
        data.mkdir()
        (wiki / "drop.md").write_text(_page("drop page", DROP_TOKEN))
        long_body = " ".join("filler sentence number %d." % i for i in range(300))
        (wiki / "keep.md").write_text(
            _page("keep page", KEEP_TOKEN) + long_body + " " + DROP_TOKEN + "\n"
        )
        atomic_rebuild_index(wiki, data)
        svc = _svc(wiki, data)

        # bug condition: rejected page outranks the active page
        order = _paths(svc.search(SearchRequest(DROP_TOKEN, top=5, fuse=False, expand=False)))
        assert order[0] == "drop.md"
        assert "keep.md" in order

        # reject on disk; top=1 must promote keep.md, not return empty
        (wiki / "drop.md").write_text(_page("drop page", DROP_TOKEN, status="rejected"))
        got = _paths(svc.search(SearchRequest(DROP_TOKEN, top=1, fuse=False, expand=False)))
        assert got == ["keep.md"]
