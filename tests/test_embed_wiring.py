"""Task B embed wiring tests."""
from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from pathlib import Path

from scripts import automation_cli as cli

ROOT = Path(__file__).resolve().parents[1]
EMBED = str(ROOT / "scripts" / "embed.py")

def _dirs(tp, mp):
    w = tp / "wiki"
    d = tp / "data"
    w.mkdir()
    d.mkdir()
    mp.setenv("WIKI_PATH", str(w))
    mp.setenv("MEMORY_HUB_DATA", str(d))
    return str(w), str(d)

class Spy:
    def __init__(self, rc=0, err=""):
        self.calls = []
        self.rc = rc
        self.err = err
    def __call__(self, cmd, **kw):
        self.calls.append(dict(cmd=cmd, **kw))
        return subprocess.CompletedProcess(cmd, self.rc, "", self.err)

def _pipe(mp, name, result):
    from scripts.automation_core import orchestrator
    def fake(tx, runner):
        return Namespace(result=result, error=None)
    mp.setattr(orchestrator, name, fake)

def _spy(mp, **kw):
    s = Spy(**kw)
    mp.setattr(cli.subprocess, "run", s)
    return s

def test_run_apply_triggers_embed(tmp_path, monkeypatch):
    w, d = _dirs(tmp_path, monkeypatch)
    _pipe(monkeypatch, "run_pipeline", "committed")
    s = _spy(monkeypatch)
    ns = Namespace(safe=False, no_auto=False, apply=True, commit=False, llm=False)
    assert cli._run(ns) == 0
    assert len(s.calls) == 1
    c = s.calls[0]
    assert c["cmd"] == [sys.executable, EMBED, "index"]
    assert c["env"]["WIKI_PATH"] == w
    assert c["env"]["MEMORY_HUB_DATA"] == d
    assert c["timeout"] == 60

def test_maintain_apply_triggers_embed(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    _pipe(monkeypatch, "maintain_pipeline", "applied_no_commit")
    s = _spy(monkeypatch)
    ns = Namespace(safe=False, no_auto=False, apply=True, commit=False)
    assert cli._maintain(ns) == 0
    assert len(s.calls) == 1
    assert s.calls[0]["cmd"] == [sys.executable, EMBED, "index"]
    assert s.calls[0]["timeout"] == 60

def test_safe_never_triggers(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    _pipe(monkeypatch, "run_pipeline", "safe")
    s = _spy(monkeypatch)
    assert cli._run(Namespace(safe=True, no_auto=False, apply=False, commit=False, llm=False)) == 0
    assert s.calls == []
    _pipe(monkeypatch, "maintain_pipeline", "safe")
    assert cli._maintain(Namespace(safe=True, no_auto=False, apply=False, commit=False)) == 0
    assert s.calls == []

def test_embed_failure_warn_only(tmp_path, monkeypatch, capsys):
    _dirs(tmp_path, monkeypatch)
    _pipe(monkeypatch, "run_pipeline", "committed")
    _spy(monkeypatch, rc=1, err="boom")
    ns = Namespace(safe=False, no_auto=False, apply=True, commit=False, llm=False)
    assert cli._run(ns) == 0
    e = capsys.readouterr().err
    assert "embed" in e and "1" in e

def test_failed_pipeline_skips_embed(tmp_path, monkeypatch):
    _dirs(tmp_path, monkeypatch)
    _pipe(monkeypatch, "run_pipeline", "failed")
    s = _spy(monkeypatch)
    ns = Namespace(safe=False, no_auto=False, apply=True, commit=False, llm=False)
    assert cli._run(ns) == 1
    assert s.calls == []
