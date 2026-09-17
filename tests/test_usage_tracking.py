"""Task U: 使用量埋点 — 旧库迁移 / pages 计数 / usage 聚合 / status 降级。"""
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from scripts.automation_core import codex_memory as cm
from scripts import usage


def _old_events_db(data: Path):
    data.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(data / "codex-memory.db")
    con.execute("CREATE TABLE events(id TEXT PRIMARY KEY,event TEXT NOT NULL,session_id TEXT NOT NULL,turn_id TEXT NOT NULL,status TEXT NOT NULL,latency_ms REAL NOT NULL,created_at TEXT NOT NULL)")
    con.commit()
    con.close()


def test_old_events_table_migrates_and_dispatch_survives(tmp_path):
    data = tmp_path / "data"
    _old_events_db(data)
    with cm.connect(data) as con:
        assert "pages" in {r[1] for r in con.execute("PRAGMA table_info(events)")} 
    cm.dispatch({"hook_event_name": "SessionStart", "session_id": "s", "turn_id": "t"}, data, tmp_path / "wiki")
    with cm.connect(data) as con:
        row = con.execute("SELECT status, pages FROM events").fetchone()
    assert row["status"] == "rules" and row["pages"] == 0


def test_recalled_pages_counted(tmp_path, monkeypatch):
    data = tmp_path / "data"
    ctx = "<memory_hub_context>\n\n来源: a; 当前项目: p; 日期: d\nAAA\n\n来源: b; 跨项目历史: q\nBBB\n\n来源: c; 日期: e\nCCC"
    monkeypatch.setattr(cm, "_recall", lambda *a, **k: ctx)
    cm.dispatch({"hook_event_name": "UserPromptSubmit", "prompt": "x", "session_id": "s", "turn_id": "t1"}, data, tmp_path / "wiki")
    monkeypatch.setattr(cm, "_recall", lambda *a, **k: "")
    cm.dispatch({"hook_event_name": "UserPromptSubmit", "prompt": "x", "session_id": "s", "turn_id": "t2"}, data, tmp_path / "wiki")
    with cm.connect(data) as con:
        got = {r["status"]: r["pages"] for r in con.execute("SELECT status, pages FROM events")}
    assert got == {"recalled": 3, "no_match": 0}


def test_usage_collect_fixture(tmp_path, monkeypatch):
    now = datetime.now()
    ts = now.strftime("%Y-%m-%dT%H:%M:%S")
    rows = [
        {"ts": ts, "src": "rest", "m": "GET", "kind": "search", "q": "a", "status": 200, "ms": 1},
        {"ts": ts, "src": "rest", "m": "GET", "kind": "ask", "q": "b", "status": 200, "ms": 2},
        {"ts": ts, "src": "mcp", "m": "MCP", "kind": "search", "q": "c", "status": 200, "ms": 0},
        {"ts": "2020-01-01T00:00:00", "src": "rest", "m": "GET", "kind": "search", "q": "old", "status": 200, "ms": 1},
    ]
    (tmp_path / "access.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    con = sqlite3.connect(tmp_path / "codex-memory.db")
    con.execute("CREATE TABLE events(id TEXT PRIMARY KEY,event TEXT,session_id TEXT,turn_id TEXT,status TEXT,latency_ms REAL,created_at TEXT,pages INTEGER NOT NULL DEFAULT 0)")
    con.executemany("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", [
        ("k1", "UserPromptSubmit", "s", "t", "recalled", 1.0, now.isoformat(), 3),
        ("k2", "UserPromptSubmit", "s", "t", "recalled", 1.0, now.isoformat(), 5),
        ("k3", "UserPromptSubmit", "s", "t", "no_match", 1.0, now.isoformat(), 0),
        ("k4", "UserPromptSubmit", "s", "t", "recalled", 1.0, "2020-01-01T00:00:00", 9),
    ])
    con.commit()
    con.close()
    monkeypatch.setenv("MEMORY_HUB_DATA", str(tmp_path))
    s = usage.collect(7)
    assert s["access_total"] == 3
    assert s["by_src_kind"] == {("rest", "search"): 1, ("rest", "ask"): 1, ("mcp", "search"): 1}
    assert s["ev_total"] == 3
    assert s["by_status"] == {"recalled": 2, "no_match": 1}
    assert (s["recalled_n"], s["pages_total"], s["pages_max"]) == (2, 8, 5)


def test_status_degrades_without_data(tmp_path):
    hub = Path(__file__).resolve().parents[1]
    env = dict(os.environ, MEMORY_HUB_DATA=str(tmp_path / "empty"), HOME=str(tmp_path / "home"),
               WIKI_PATH=str(tmp_path / "wiki-missing"), CODEX_SESSIONS_DIR=str(tmp_path / "sess-missing"),
               CLAUDE_MEM_DB=str(tmp_path / "nodb"), OPENCODEX_URL="http://127.0.0.1:1/v1")
    r = subprocess.run(["bash", "scripts/status.sh"], cwd=hub, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0
    assert "usage(近7天)" in r.stdout
