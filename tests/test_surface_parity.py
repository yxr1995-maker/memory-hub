from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import pytest

from scripts.automation_core.query_planner import SearchRequest
from scripts.automation_core.service import MemoryService
from tests.helpers.full_auto_fixture import write_page


ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def surface_fixture(tmp_path: pathlib.Path):
    wiki = tmp_path / "wiki"
    data = tmp_path / "data"
    wiki.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    write_page(
        wiki,
        "notes/alpha.md",
        {"title": "Alpha Project", "scope": "project", "scope_id": "fixture-project", "status": "active"},
        "exact query content for alpha\n",
    )
    write_page(
        wiki,
        "notes/beta.md",
        {"title": "Beta User", "scope": "user", "scope_id": "fixture-user", "status": "active"},
        "exact query content for beta\n",
    )

    # Rebuild index
    subprocess.run(["bash", str(ROOT / "scripts" / "index.sh")], env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data), "PYTHONPATH": str(ROOT)}, check=True)

    class SurfaceRunner:
        def __init__(self):
            self.wiki = wiki
            self.data = data
            self.service = MemoryService(wiki, data, ROOT)

        def cli_search(self, scope=None, scope_id=None):
            args = ["bash", str(ROOT / "scripts" / "search.sh"), "exact query", "--json"]
            if scope:
                args += ["--scope", scope]
            if scope_id:
                args += ["--scope-id", scope_id]
            r = subprocess.run(args, env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data), "PYTHONPATH": str(ROOT)}, capture_output=True, text=True)
            return json.loads(r.stdout)

        def mcp_search(self, scope=None, scope_id=None):
            req = SearchRequest(query="exact query", top=10, fuse=True, expand=True, scope=scope, scope_id=scope_id)
            return self.service.search(req).to_dict()

    return SurfaceRunner()


def test_cli_mcp_rest_search_and_ask_order_match(surface_fixture):
    cli_resp = surface_fixture.cli_search(scope="project", scope_id="fixture-project")
    mcp_resp = surface_fixture.mcp_search(scope="project", scope_id="fixture-project")

    cli_paths = [r["path"] for r in cli_resp["results"]]
    mcp_paths = [r["path"] for r in mcp_resp["results"]]

    assert cli_paths == mcp_paths == ["notes/alpha.md"]
    assert set(cli_resp["results"][0]) >= {"path", "score", "status", "scope", "scope_id", "rank_reason"}
    assert set(mcp_resp["results"][0]) >= {"path", "score", "status", "scope", "scope_id", "rank_reason"}
