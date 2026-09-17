"""M6 P0 wiki-fixer lite: dead-link + stale report, read-only guarantees."""
from __future__ import annotations
import itertools
import os
import subprocess
from datetime import date, timedelta, timezone, datetime
from pathlib import Path
import pytest
from scripts.automation_core.indexer import atomic_rebuild_index
from scripts.automation_core.operation import GitBaseline, begin_transaction
from scripts.automation_core.orchestrator import MaintainStageRunner, maintain_pipeline
from scripts.automation_core.schema import Mode, OperationContext
from scripts.automation_core.wiki_fixer import run_report, scan_dead_links, scan_stale
from tests.helpers.full_auto_fixture import write_page
SEQ = itertools.count(1)
FM = {"type": "note", "status": "active", "scope": "project", "scope_id": "other"}
def _iso(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()
def _roots(tmp_path: Path):
    wiki, data, staging = (tmp_path / n for n in ("wiki", "data", "staging"))
    for p in (wiki, data, staging):
        p.mkdir()
    (tmp_path / "home").mkdir(exist_ok=True)
    env = dict(PATH=os.environ.get("PATH", "/usr/bin:/bin"), HOME=str(tmp_path / "home"),
               GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    for args in (("init",), ("config", "user.name", "Fx"), ("config", "user.email", "f@x.invalid")):
        subprocess.run(["git", *args], cwd=wiki, env=env, check=True, capture_output=True)
    return wiki, data, staging, env
def _seed(wiki: Path, fresh_days: int = 1, old_days: int = 200) -> None:
    write_page(wiki, "concepts/real.md", dict(FM, title="Real", created=_iso(400), updated=_iso(fresh_days)), "Real body.\n")
    write_page(wiki, "concepts/hub.md", dict(FM, title="Hub", created=_iso(400), updated=_iso(fresh_days)),
               "See [[real]] and [[ghost-page]] and [[real]] again.\n")
    write_page(wiki, "concepts/old.md", dict(FM, title="Old", created=_iso(400), updated=_iso(old_days)), "Old body.\n")
def _commit_all(wiki: Path, env: dict) -> None:
    subprocess.run(["git", "add", "-A"], cwd=wiki, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=wiki, env=env, check=True, capture_output=True)
def _status(wiki: Path, env: dict) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=wiki, env=env, check=True, capture_output=True, text=True).stdout.strip()
def _tx(wiki: Path, data: Path, staging: Path, *, safe: bool):
    op = OperationContext(operation_id=f"wiki-fixer-{next(SEQ):04d}", command="maintain",
                          mode=Mode.SAFE if safe else Mode.AUTO, auto=not safe, apply=not safe,
                          wiki_path=wiki, data_path=data)
    return begin_transaction(op, GitBaseline.capture(wiki))
def test_scan_dead_links_unit(tmp_path: Path) -> None:
    wiki, data, staging, env = _roots(tmp_path)
    _seed(wiki)
    dead = scan_dead_links(wiki)
    assert dead == {"concepts/hub.md": ["ghost-page"]}
def test_scan_stale_boundaries_and_fallback_chain(tmp_path: Path) -> None:
    wiki, data, staging, env = _roots(tmp_path)
    write_page(wiki, "concepts/edge179.md", dict(FM, title="E179", created=_iso(400), updated=_iso(179)), "x\n")
    write_page(wiki, "concepts/edge181.md", dict(FM, title="E181", created=_iso(400), updated=_iso(181)), "x\n")
    write_page(wiki, "concepts/lv.md", dict(FM, title="LV", created=_iso(400), updated=_iso(400), last_verified=_iso(1)), "x\n")
    write_page(wiki, "concepts/va.md", dict(FM, title="VA", created=_iso(400), updated=_iso(400), valid_at=_iso(2)), "x\n")
    write_page(wiki, "concepts/old.md", dict(FM, title="Old", created=_iso(400), updated=_iso(300)), "x\n")
    atomic_rebuild_index(wiki, data)
    stale = scan_stale(wiki, data)
    paths = [r[0] for r in stale]
    assert "concepts/edge181.md" in paths
    assert "concepts/edge179.md" not in paths
    assert "concepts/lv.md" not in paths
    assert "concepts/va.md" not in paths
    assert "concepts/old.md" in paths
    assert stale[0][0] == "concepts/old.md"
def test_scan_stale_fallback_without_index_db(tmp_path: Path) -> None:
    wiki, data, staging, env = _roots(tmp_path)
    write_page(wiki, "concepts/old.md", dict(FM, title="Old", created=_iso(400), updated=_iso(300)), "x\n")
    assert not (data / "index.db").exists()
    stale = scan_stale(wiki, data)
    assert [r[0] for r in stale] == ["concepts/old.md"]
def test_safe_mode_counts_and_clean_tree(tmp_path: Path, capsys) -> None:
    wiki, data, staging, env = _roots(tmp_path)
    _seed(wiki)
    _commit_all(wiki, env)
    atomic_rebuild_index(wiki, data)
    before = _status(wiki, env)
    tx = _tx(wiki, data, staging, safe=True)
    report = maintain_pipeline(tx, MaintainStageRunner(staging=staging))
    assert report.result == "safe"
    out = capsys.readouterr().out
    assert "wiki_fixer_report" in out
    assert "1 dead links" in out
    assert "1 stale pages" in out
    assert _status(wiki, env) == before == ""
    assert list((data / "reports").glob("wiki-fixer-*.md")) == []
def test_apply_writes_report_and_leaves_wiki_untouched(tmp_path: Path) -> None:
    wiki, data, staging, env = _roots(tmp_path)
    _seed(wiki)
    _commit_all(wiki, env)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wiki, env=env, check=True, capture_output=True, text=True).stdout.strip()
    tx = _tx(wiki, data, staging, safe=False)
    report = maintain_pipeline(tx, MaintainStageRunner(staging=staging))
    assert report.result in ("safe", "applied_no_commit", "committed")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = data / "reports" / f"wiki-fixer-{day}.md"
    assert target.is_file()
    text = target.read_text(encoding="utf-8")
    assert "concepts/hub.md" in text and "ghost-page" in text
    assert "concepts/old.md" in text
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wiki, env=env, check=True, capture_output=True, text=True).stdout.strip()
    assert after == head
    assert _status(wiki, env) == ""
    dead, stale = run_report(wiki, data)
    assert dead == {"concepts/hub.md": ["ghost-page"]}
    assert [r[0] for r in stale] == ["concepts/old.md"]
def test_non_target_dirs_page_is_not_dead_link(tmp_path: Path) -> None:
    wiki, data, staging, env = _roots(tmp_path)
    write_page(wiki, "drafts/memoryhub/x.md", dict(FM, title="X", created=_iso(10), updated=_iso(1)), "draft body\n")
    write_page(wiki, "concepts/hub.md", dict(FM, title="Hub", created=_iso(10), updated=_iso(1)),
               "See [[drafts/memoryhub/x]] and [[nonexistent/y]].\n")
    dead = scan_dead_links(wiki)
    assert dead == {"concepts/hub.md": ["nonexistent/y"]}
