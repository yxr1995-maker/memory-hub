#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import tempfile
import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SEARCH = ROOT / "scripts" / "search.sh"
ASK = ROOT / "scripts" / "ask.sh"


def run_cmd(script: pathlib.Path, wiki: pathlib.Path, data: pathlib.Path, *args: str):
    return subprocess.run(
        ["bash", str(script), *args],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data), "PYTHONPATH": str(ROOT)},
    )


@pytest.fixture
def cli_fixture(tmp_path: pathlib.Path):
    wiki = tmp_path / "wiki"
    data = tmp_path / "data"
    wiki.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    (wiki / "relevant.md").write_text("---\ntitle: 'Relevant'\nscope: project\nscope_id: fixture-project\n---\nexactphrase a needle\n", encoding="utf-8")
    (wiki / "incidental.md").write_text("---\ntitle: 'Incidental'\nscope: project\nscope_id: other-project\n---\npersistent content a\n", encoding="utf-8")
    
    # Run index
    subprocess.run(["bash", str(ROOT / "scripts" / "index.sh")], env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data), "PYTHONPATH": str(ROOT)}, check=True)
    
    class Runner:
        def run_search(self, *args: str):
            return run_cmd(SEARCH, wiki, data, *args)
        def run_ask(self, *args: str):
            return run_cmd(ASK, wiki, data, *args)
    return Runner()


def test_cli_defaults_and_ask_share_ranked_paths(cli_fixture):
    search = cli_fixture.run_search("exactphrase", "--json")
    assert search.returncode == 0, search.stderr
    search_data = json.loads(search.stdout)
    assert search_data["plan"]["fuse"] is True

    ask = cli_fixture.run_ask("exactphrase", "--json")
    assert ask.returncode == 0, ask.stderr
    ask_data = json.loads(ask.stdout)
    assert ask_data["context_paths"] == [item["path"] for item in search_data["results"][:len(ask_data["context_paths"])]]

    disabled = cli_fixture.run_search("exactphrase", "--no-fuse", "--json")
    assert disabled.returncode == 0
    disabled_data = json.loads(disabled.stdout)
    assert disabled_data["plan"]["fuse"] is False


def test_search_validation_errors(cli_fixture):
    # Query too long
    long_q = cli_fixture.run_search("a" * 501)
    assert long_q.returncode == 2

    # Top out of bounds
    bad_top = cli_fixture.run_search("test", "--top", "0")
    assert bad_top.returncode == 2

    # Invalid scope
    bad_scope = cli_fixture.run_search("test", "--scope", "invalid_scope")
    assert bad_scope.returncode == 2


def test_scope_filtering(cli_fixture):
    search_scoped = cli_fixture.run_search("exactphrase", "--scope", "project", "--scope-id", "fixture-project", "--json")
    assert search_scoped.returncode == 0
    scoped_data = json.loads(search_scoped.stdout)
    assert all(r["scope"] == "project" and r["scope_id"] == "fixture-project" for r in scoped_data["results"])

