"""Continuity suite contract tests for evaluation.experience_v1.continuity."""
import json
from pathlib import Path

import pytest

from scripts.automation_core.experience import ExperienceError


ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "evaluation" / "experience_v1" / "continuity-suite.json"
ENGINEERING = {"jichuan-new-report", "qingshi-new-run", "zhexian-new-cycle", "yinshan-new-server"}
ACTION_BY_KEYWORD = [("霁川", "preserve"), ("青石", "reset"), ("折线", "conditional_update"), ("银杉", "sqlite_backup")]


def _authorize(root, **extra):
    from evaluation.experience_v1 import continuity
    auth = {"free_only": True, "paid_cap": 0, "confirmed_by_owner": True,
            "model": continuity.runner.MODEL, "max_requests": 24, "max_output_tokens_per_request": 80,
            "manifest_sha256": continuity.harness.sha256_file(root / "manifest.json"), **extra}
    (root / "authorization.json").write_text(json.dumps(auth))


def _fake_model(msg, *, max_tokens):
    # Read only the current task segment; history material must not steer
    # the fake model (group C evidence may mention other projects).
    task = msg[-1]["content"].split("当前任务：", 1)[1]
    if "银杉" in task:
        raw = "{not json"
    elif any(key in task for key, _ in ACTION_BY_KEYWORD):
        raw = json.dumps({"action": next(a for key, a in ACTION_BY_KEYWORD if key in task)})
    else:
        raw = "把门打开，给今天多一点一起走走的时间。走走拾便袋每卷15只，带撕线，晚饭后一起出门更轻松。"
    return {"model": "agnes-2.5-flash", "choices": [{"message": {"content": raw}, "finish_reason": "stop"}]}


def test_freeze_prepares_24_rows_without_rubric_leak(tmp_path):
    from evaluation.experience_v1 import continuity
    frozen = tmp_path / "run"
    report = continuity.freeze(frozen, SUITE)
    assert report["requests"] == 24 and report["tasks"] == 8 and report["model_calls"] == 0
    assert len({(frozen / "snapshots" / g / "experience.sqlite3").read_bytes() for g in "ABC"}) == 1
    prepared = json.loads((frozen / "prepared.json").read_text())
    suite = json.loads(SUITE.read_text())
    tasks = {t["task_id"]: t for t in suite["tasks"]}
    assert len(prepared) == 24
    for task_id, task in tasks.items():
        rows = [r for r in prepared if r["task_id"] == task_id]
        assert {r["group"] for r in rows} == {"A", "B", "C"}
        assert len({r["messages"][0]["content"] for r in rows}) == 1
        assert next(r for r in rows if r["group"] == "A")["evidence"] == []
        if task["kind"] == "engineering_action":
            secrets = [task["rubric"]["reference_source"], task["rubric"]["no_memory_abstention"]]
        else:
            secrets = [task["rubric"]["intent"], *task["rubric"]["forbidden_claims"]]
        for row in rows:
            assert row["evidence_chars"] <= 6000
            blob = json.dumps(row, ensure_ascii=False)
            assert all(secret not in blob for secret in secrets), task_id
    for group in ("B", "C"):
        row = next(r for r in prepared if r["task_id"] == "jichuan-new-report" and r["group"] == group)
        assert row["evidence"], group
        assert "不再可直接覆盖" in row["messages"][1]["content"], group
    manifest = json.loads((frozen / "manifest.json").read_text())
    assert manifest["protocol"] == "continuity-1"
    assert {"suite.json", "prepared.json", "tasks.json"} <= set(manifest["files"])


def test_freeze_refuses_overwrite(tmp_path):
    from evaluation.experience_v1 import continuity
    frozen = tmp_path / "run"
    continuity.freeze(frozen, SUITE)
    before = (frozen / "prepared.json").read_bytes()
    with pytest.raises(ExperienceError) as excinfo:
        continuity.freeze(frozen, SUITE)
    assert excinfo.value.code == "FREEZE_EXISTS"
    assert (frozen / "prepared.json").read_bytes() == before


def test_run_requires_authorization_before_any_call(tmp_path):
    from evaluation.experience_v1 import continuity
    frozen = tmp_path / "run"
    continuity.freeze(frozen, SUITE)
    calls = []
    with pytest.raises(ExperienceError) as excinfo:
        continuity.run(frozen, call=lambda *a, **k: calls.append(a))
    assert excinfo.value.code == "AUTHORIZATION_REQUIRED"
    assert not calls and not (frozen / "results").exists()
    _authorize(frozen, max_requests=23)
    with pytest.raises(ExperienceError) as excinfo:
        continuity.run(frozen, call=lambda *a, **k: calls.append(a))
    assert excinfo.value.code == "AUTHORIZATION_LIMIT_EXCEEDED"
    assert not calls and not (frozen / "results").exists()


def test_run_executes_postconditions_and_reports_unknown_cost(tmp_path):
    from evaluation.experience_v1 import continuity
    frozen = tmp_path / "run"
    continuity.freeze(frozen, SUITE)
    _authorize(frozen)
    before = {p: p.read_bytes() for p in (frozen / "snapshots").rglob("*.sqlite3")}
    calls = []

    def model(msg, *, max_tokens):
        assert max_tokens == 80
        calls.append(msg)
        return _fake_model(msg, max_tokens=max_tokens)

    result = continuity.run(frozen, call=model)
    assert result["requests"] == 24 and len(calls) == 24
    assert result["billing_amount"] is None and result["M2_gate"] == "unassessed"
    assert set(result["engineering_first_pass"]) == {"A", "B", "C"}
    rows = [json.loads(p.read_text()) for p in (frozen / "results").glob("*.json")]
    assert len(rows) == 24 and all(r["usage"] is None and r["billing_amount"] is None for r in rows)
    backup = [r for r in rows if r["task_id"] == "yinshan-new-server"]
    assert len(backup) == 3 and all(r.get("execution") is None for r in backup)
    # The fixed executor owns grading: all valid actions must pass their
    # postconditions; the intentionally invalid backup JSON must not execute.
    executed = [r for r in rows if r["task_id"] in ENGINEERING]
    others = [r for r in executed if r["task_id"] != "yinshan-new-server"]
    assert len(executed) == 12 and len(others) == 9
    assert all(r["execution"]["passed"] for r in others)
    assert all(r["execution"]["observations"]["unrelated_preserved"] for r in others)
    creative = [r for r in rows if r["kind"] == "creative_copy"]
    assert len(creative) == 12
    assert all(r["transport_status"] == "complete" and r["raw_answer"].startswith("把门打开") for r in creative)
    assert not any(r["raw_answer"].lstrip().startswith("{") for r in creative)
    assert all(p.read_bytes() == raw for p, raw in before.items())
    with pytest.raises(ExperienceError, match="RUN_EXISTS"):
        continuity.run(frozen, call=model)
