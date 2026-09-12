import json
from pathlib import Path

import pytest

from scripts.automation_core.experience import ExperienceError


def test_continuity_uses_real_plain_search_and_same_current_conditions(tmp_path, monkeypatch):
    from evaluation.experience_v1 import continuity
    from scripts.automation_core.service import MemoryService

    source = Path(__file__).resolve().parents[2] / "evaluation/experience_v1/continuity-suite.json"
    suite = tmp_path / "suite.json"
    data = json.loads(source.read_text())
    for task in data["tasks"]:
        task["rubric"]["private_marker"] = "GRADING_SECRET_NOT_FOR_PROMPTS"
    suite.write_text(json.dumps(data, ensure_ascii=False))
    seen = []
    original = MemoryService.search

    def observe(self, request, **kwargs):
        seen.append(request)
        return original(self, request, **kwargs)

    monkeypatch.setattr(MemoryService, "search", observe)
    frozen = tmp_path / "run"
    continuity.freeze(frozen, suite)
    assert len(seen) == 8
    assert all(r.top == 3 and not r.expand and not r.fuse for r in seen)
    prepared = json.loads((frozen / "prepared.json").read_text())
    assert len(prepared) == 24
    for task in json.loads(suite.read_text())["tasks"]:
        rows = [r for r in prepared if r["task_id"] == task["task_id"]]
        assert {r["group"] for r in rows} == {"A", "B", "C"}
        assert len({r["messages"][0]["content"] for r in rows}) == 1
        assert len({r["messages"][1]["content"].split("当前任务：")[1] for r in rows}) == 1
        assert all(r["evidence_chars"] <= 6000 for r in rows)
        assert next(r for r in rows if r["group"] == "A")["evidence"] == []
        for group in ("B", "C"):
            row = next(r for r in rows if r["group"] == group)
            assert row["evidence"], (task["task_id"], group)
            assert all("GRADING_SECRET_NOT_FOR_PROMPTS" not in m["content"] for m in row["messages"])
    for group in ("B", "C"):
        row = next(r for r in prepared if r["task_id"] == "heye-new-display" and r["group"] == group)
        assert "最近确认：本次展台" in row["messages"][1]["content"]
    assert len({(frozen / "snapshots" / g / "experience.sqlite3").read_bytes() for g in ("A", "B", "C")}) == 1


def authorized_run(tmp_path):
    from evaluation.experience_v1 import continuity
    suite = Path(__file__).resolve().parents[2] / "evaluation/experience_v1/continuity-suite.json"
    root = tmp_path / "run"
    receipt = continuity.freeze(root, suite)
    auth = {"free_only": True, "paid_cap": 0, "confirmed_by_owner": True,
            "model": continuity.runner.MODEL, "max_requests": 24,
            "max_output_tokens_per_request": 80, "manifest_sha256": receipt["manifest_sha256"]}
    (root / "authorization.json").write_text(json.dumps(auth))
    return continuity, root, auth


@pytest.mark.parametrize("path", ["prepared.json", "suite.json", "tasks.json", "wiki/heye.md", "snapshots/C/experience.sqlite3"])
def test_frozen_content_change_stops_before_request(tmp_path, path):
    continuity, root, _ = authorized_run(tmp_path)
    target = root / path
    target.write_bytes(target.read_bytes() + b" ")
    calls = []
    with pytest.raises(ExperienceError, match="SNAPSHOT_CHANGED"):
        continuity.run(root, call=lambda *a, **k: calls.append(a))
    assert calls == []
    assert not (root / "results").exists()


def test_authorization_change_stops_before_second_call(tmp_path):
    continuity, root, auth = authorized_run(tmp_path)
    calls = []

    def changing(messages, **kw):
        calls.append(kw)
        auth["max_output_tokens_per_request"] = 79
        (root / "authorization.json").write_text(json.dumps(auth))
        return {"model": continuity.runner.MODEL,
                "choices": [{"message": {"content": '{"action":"preserve"}'}, "finish_reason": "stop"}]}

    with pytest.raises(ExperienceError, match="AUTHORIZATION_CHANGED"):
        continuity.run(root, call=changing)
    assert calls == [{"max_tokens": 80}]
    assert len(list((root / "results").glob("*.json"))) == 1


def test_wrong_model_is_recorded_and_stops_immediately(tmp_path):
    continuity, root, _ = authorized_run(tmp_path)
    calls = []

    def wrong(messages, **kw):
        calls.append(kw)
        return {"model": "wrong", "choices": [{"message": {"content": '{"action":"preserve"}'}, "finish_reason": "stop"}]}

    with pytest.raises(ExperienceError, match="MODEL_MISMATCH"):
        continuity.run(root, call=wrong)
    assert len(calls) == 1
    row = json.loads(next((root / "results").glob("*.json")).read_text())
    assert row["transport_status"] == "error" and row["execution"] is None


def test_source_change_stops_before_call(tmp_path, monkeypatch):
    continuity, root, _ = authorized_run(tmp_path)
    monkeypatch.setattr(continuity, "sources", lambda: {"changed": "source"})
    calls = []
    with pytest.raises(ExperienceError, match="SNAPSHOT_CHANGED"):
        continuity.run(root, call=lambda *a, **k: calls.append(a))
    assert not calls
