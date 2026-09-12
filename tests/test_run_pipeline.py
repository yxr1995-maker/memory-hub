"""End-to-end evidence tests for the real run pipeline.

These tests drive the actual CLI against an isolated fixture wiki, git repo,
staging area and data dir. A run is only "done" when new content is
searchable through the rebuilt index and the git HEAD actually moved.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RunFixture:
    def __init__(self, tmp_path: pathlib.Path) -> None:
        self.root = tmp_path
        self.wiki = tmp_path / "wiki"
        self.data = tmp_path / "data"
        self.staging = tmp_path / "staging"
        self.sessions = tmp_path / "sessions"
        for p in (self.wiki, self.data, self.staging, self.sessions):
            p.mkdir(parents=True, exist_ok=True)

        (self.wiki / "pages").mkdir(parents=True, exist_ok=True)
        (self.wiki / "pages" / "existing.md").write_text(
            "---\ntitle: 'Existing'\nproject: 'fixture-project'\n---\nexisting knowledge\n",
            encoding="utf-8",
        )

        subprocess.run(["git", "init"], cwd=self.wiki, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.wiki, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.wiki, check=True)
        subprocess.run(["git", "add", "."], cwd=self.wiki, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.wiki, check=True, capture_output=True)
        self.baseline_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.wiki, capture_output=True, text=True, check=True
        ).stdout.strip()

        self.env = {
            **os.environ,
            "HOME": str(tmp_path / "home"),
            "WIKI_PATH": str(self.wiki),
            "MEMORY_HUB_DATA": str(self.data),
            "MEMORY_HUB_STAGING": str(self.staging),
            "CODEX_SESSIONS_DIR": str(self.sessions),
            "PYTHONPATH": str(ROOT),
        }
        (tmp_path / "home").mkdir(parents=True, exist_ok=True)

    def seed_session(self, marker: str) -> None:
        now = datetime.now(timezone.utc)
        session_dir = self.sessions / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
        session_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"fixture observation {marker}"}],
            },
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        (session_dir / "rollout-fixture.jsonl").write_text(
            json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(ROOT / "memory-hub.sh"), *args],
            env=self.env, capture_output=True, text=True, timeout=180,
        )

    def head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.wiki, capture_output=True, text=True, check=True
        ).stdout.strip()


@pytest.fixture
def fx(tmp_path: pathlib.Path) -> RunFixture:
    return RunFixture(tmp_path)


def test_run_safe_writes_nothing(fx: RunFixture) -> None:
    fx.seed_session("safe-mode")
    proc = fx.cli("run", "--safe")
    assert proc.returncode == 0, proc.stderr
    assert "mode=safe" in proc.stdout
    assert fx.head() == fx.baseline_head
    assert not list(fx.staging.glob("observations-*.jsonl")), "safe mode must not capture"
    assert not list(fx.wiki.rglob("drafts/memoryhub/*.md")), "safe mode must not publish"
    reports = list((fx.data / "reports").glob("run-*.json"))
    assert len(reports) == 1
    payload = json.loads(reports[0].read_text(encoding="utf-8"))
    assert payload["result"] == "safe"
    assert payload["failed_stage"] is None


def test_run_apply_full_chain_and_searchable(fx: RunFixture) -> None:
    fx.seed_session("apply-mode-xyzzy")
    proc = fx.cli("run")
    assert proc.returncode == 0, proc.stderr

    # capture produced a new observation file in our isolated staging
    obs_files = list(fx.staging.glob("observations-*.jsonl"))
    assert obs_files, "run must capture at least one observations file"

    # publish applied real pages into the wiki
    published = list(fx.wiki.rglob("drafts/memoryhub/*.md"))
    assert published, "run must publish distilled pages"

    # HEAD moved: report committed, not a fake success
    assert fx.head() != fx.baseline_head, "run apply must move git HEAD"

    # report carries a real commit hash
    reports = sorted((fx.data / "reports").glob("run-*.json"))
    payload = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert payload["result"] == "committed"
    commit = payload["stage_data"]["exact_stage_commit"]["commit"]
    assert commit == fx.head()

    # index rebuilt and content searchable through the real index
    db = fx.data / "index.db"
    assert db.is_file()
    with sqlite3.connect(db) as con:
        hits = con.execute(
            "select path from pages where content LIKE ?", ("%apply-mode-xyzzy%",)
        ).fetchall()
    assert hits, "newly captured content must be searchable in the rebuilt index"


def test_run_failure_reports_failed_stage(fx: RunFixture) -> None:
    fx.wiki.rename(fx.root / "wiki-missing")
    proc = fx.cli("run")
    assert proc.returncode == 1
    reports = sorted((fx.data / "reports").glob("run-*.json"))
    payload = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert payload["result"] == "failed"
    assert payload["failed_stage"] == "validate"
