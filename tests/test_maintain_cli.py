"""Exercise maintain through the public CLI using isolated wiki/data/staging roots."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from tests.helpers.full_auto_fixture import write_page

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli_fixture(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    wiki, data, staging = (tmp_path / name for name in ("wiki", "data", "staging"))
    for path in (wiki, data, staging, tmp_path / "home"):
        path.mkdir()
    env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), HOME=str(tmp_path / "home"), WIKI_PATH=str(wiki),
               MEMORY_HUB_DATA=str(data), MEMORY_HUB_STAGING=str(staging),
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    env.pop("MEMORY_HUB_OPERATION_ID", None)
    for args in (("init",), ("config", "user.name", "Fixture"),
                 ("config", "user.email", "fixture@example.invalid")):
        subprocess.run(["git", *args], cwd=wiki, env=env, check=True, capture_output=True)
    write_page(wiki, "notes/baseline.md", {"title": "Baseline", "type": "note",
               "status": "active", "scope": "project", "scope_id": "other",
               "created": "2026-09-01", "updated": "2026-09-01"}, "Baseline unrelated content.\n")
    subprocess.run(["git", "add", "notes/baseline.md"], cwd=wiki, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=wiki, env=env, check=True, capture_output=True)
    for day, ids in (("20260901", (1, 2)), ("20260902", (3,))):
        rows = [{"id": f"maintain-{n}", "project_id": "fixture-project",
                 "text": f"Memory maintenance preserves exact owned changes and isolated indexes item {n}.",
                 "created_at_epoch": 1788220800 + (86400 if n == 3 else 0)} for n in ids]
        (staging / f"observations-{day}-120000.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return wiki, data, staging, env


def invoke(fixture, *args: str, command: str = "maintain") -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(ROOT / "memory-hub.sh"), command, *args],
                          env=fixture[3], cwd=ROOT, text=True, capture_output=True, timeout=45)


def controlled_snapshot(fixture) -> dict[str, str]:
    wiki, data, staging, _ = fixture
    result = {}
    for base in (wiki, staging):
        for path in base.rglob("*"):
            if path.is_file():
                result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (data / "index.db", data / "cluster-manifest.json",
                 data / "manifests" / "cluster-observations-v1.json"):
        if path.is_file():
            result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@pytest.mark.parametrize("flags", [("--safe",), ("--no-auto",)])
def test_preview_preserves_controlled_state(cli_fixture, flags):
    before = controlled_snapshot(cli_fixture)
    result = invoke(cli_fixture, *flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert controlled_snapshot(cli_fixture) == before
    assert not (cli_fixture[1] / "locks" / "automation.lock").exists()


def test_auto_commit_and_replay_via_shell(cli_fixture):
    wiki, data, _, env = cli_fixture
    first = invoke(cli_fixture)
    assert first.returncode == 0, first.stdout + first.stderr
    pages = list(wiki.glob("moc/cluster-*.md"))
    assert len(pages) == 1
    with sqlite3.connect(f"file:{data / 'index.db'}?mode=ro", uri=True) as db:
        assert db.execute("pragma integrity_check").fetchone()[0] == "ok"
        assert db.execute("select count(*) from pages where path = ?",
                          (str(pages[0].relative_to(wiki)),)).fetchone()[0] == 1
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=wiki, env=env)
    manifest_path = data / "manifests" / "cluster-observations-v1.json"
    manifest = manifest_path.read_bytes()
    assert len(json.loads(manifest)["entries"]) == 1
    second = invoke(cli_fixture)
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(list(wiki.glob("moc/cluster-*.md"))) == 1
    assert manifest_path.read_bytes() == manifest
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=wiki, env=env) == head
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=wiki, env=env)
    report = json.loads((data / "reports" / "latest-operation.json").read_text())
    assert report["command"] == "maintain"
    assert report["result"] == "applied_no_commit"


@pytest.mark.parametrize("flags", [("--safe", "--apply"), ("--safe", "--commit")])
@pytest.mark.parametrize("command", ["maintain", "run"])
def test_invalid_mode_has_exit_2_without_traceback(cli_fixture, flags, command):
    before = controlled_snapshot(cli_fixture)
    result = invoke(cli_fixture, *flags, command=command)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert controlled_snapshot(cli_fixture) == before


@pytest.mark.parametrize("command", ["maintain", "run"])
def test_shared_automation_lock_blocks_both_writers(cli_fixture, command):
    lock = cli_fixture[1] / "locks" / "automation.lock"
    lock.mkdir(parents=True)
    (lock / "info.json").write_text(json.dumps({"pid": os.getpid(), "operation_id": "other"}))
    before = controlled_snapshot(cli_fixture)
    result = invoke(cli_fixture, command=command)
    assert result.returncode == 75, result.stdout + result.stderr
    assert controlled_snapshot(cli_fixture) == before
    assert lock.is_dir()


def test_staged_changes_block_before_wiki_mutation(cli_fixture):
    wiki, _, _, env = cli_fixture
    (wiki / "user.md").write_text("user staged content\n")
    subprocess.run(["git", "add", "user.md"], cwd=wiki, env=env, check=True)
    before = controlled_snapshot(cli_fixture)
    result = invoke(cli_fixture)
    assert result.returncode == 1, result.stdout + result.stderr
    assert controlled_snapshot(cli_fixture) == before
    assert not (cli_fixture[1] / "locks" / "automation.lock").exists()


def test_no_auto_apply_repairs_without_clustering_or_commit(cli_fixture):
    wiki, data, _, env = cli_fixture
    page = write_page(wiki, "notes/repair.md", {"title": "Repair", "type": "note"}, "Repair fixture.\n")
    subprocess.run(["git", "add", "notes/repair.md"], cwd=wiki, env=env, check=True)
    subprocess.run(["git", "commit", "-m", "repair fixture"], cwd=wiki, env=env, check=True, capture_output=True)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=wiki, env=env)
    before = page.read_bytes()
    result = invoke(cli_fixture, "--no-auto", "--apply")
    assert result.returncode == 0, result.stdout + result.stderr
    assert page.read_bytes() != before
    assert not list(wiki.glob("moc/cluster-*.md"))
    assert not (data / "manifests" / "cluster-observations-v1.json").exists()
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=wiki, env=env) == head
    assert not subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=wiki, env=env)


def test_non_git_wiki_reports_applied_without_commit(cli_fixture):
    import shutil
    wiki, data, _, _ = cli_fixture
    shutil.rmtree(wiki / ".git")
    result = invoke(cli_fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((data / "reports" / "latest-operation.json").read_text())
    assert report["result"] == "applied_no_commit"
    assert report["stage_data"]["exact_stage_commit"]["commit"] == "not-a-repository"
