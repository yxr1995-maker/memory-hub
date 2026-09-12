"""Regression coverage for long briefs and incomplete applicability metadata."""
import json
import sqlite3

import pytest

from scripts.automation_core.experience import record_episode, recall_for_decision
from scripts.automation_core.experience.retrieval import candidates
from scripts.automation_core.experience.store import connect


@pytest.mark.parametrize("field,needle", [
    ("goal", "保护用户修改的生成文档"),
    ("explicit_reason", "要有感情但不要让人有负担"),
    ("conditions", "版本所有权"),
    ("goal", "zzversion zzownership"),
])
@pytest.mark.parametrize("position", ["before", "after"])
def test_long_brief_keeps_relevant_terms_in_every_key_group(env, field, needle, position):
    db, owner, agent, payload = env
    payload.update(goal="neutral", narrative="neutral", explicit_reason=None, conditions={})
    payload[field] = {"context": needle} if field == "conditions" else needle
    receipt = record_episode(db, owner, payload, idempotency_key="long-brief")
    noise = " ".join(f"build_key_{i:03d}" for i in range(220))
    task = f"{needle}\n{noise}" if position == "before" else f"{noise}\n{needle}"
    before = db.read_bytes()

    result = recall_for_decision(db, agent, task=task)

    assert receipt["event_id"] in [item["episode_id"] for item in result["items"]]
    assert result["usage"]["candidates_considered"] <= 60
    assert len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 6000
    assert db.read_bytes() == before
    assert recall_for_decision(db, agent.narrow_collections(("other",)), task=task)["items"] == []


def test_long_brief_does_not_consume_one_sql_parameter_per_token(env):
    db, owner, agent, payload = env
    payload.update(goal="zzownership", narrative="neutral", explicit_reason=None, conditions={})
    receipt = record_episode(db, owner, payload, idempotency_key="parameter-limit")
    task = " ".join(f"build_{i:04d}" for i in range(900)) + " zzownership"
    with connect(db) as connection:
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 64)
        assert candidates(connection, agent, task) == [receipt["event_id"]]


@pytest.mark.parametrize("current", [
    {"owned": True},
    {"owned": True, "user_modified": None},
    {"owned": True, "user_modified": "unknown"},
    {"owned": True, "user_modified": 1},
])
def test_unresolved_exclusion_cannot_report_compatible(env, current):
    db, owner, agent, payload = env
    payload["preconditions"] = {
        "target": "generated document",
        "require": {"owned": True},
        "exclude": {"user_modified": True},
    }
    record_episode(db, owner, payload, idempotency_key="unresolved-exclusion")
    item = recall_for_decision(db, agent, task="文案", conditions=current)["items"][0]
    assert item["compatibility"] == {"status": "unknown", "need_current_check": True}


@pytest.mark.parametrize("current,expected", [
    ({"owned": True, "user_modified": False}, "compatible"),
    ({"owned": True, "user_modified": True}, "incompatible"),
    ({"user_modified": True}, "incompatible"),
])
def test_known_exclusion_keeps_explicit_outcomes(env, current, expected):
    db, owner, agent, payload = env
    payload["preconditions"] = {
        "target": "generated document",
        "require": {"owned": True},
        "exclude": {"user_modified": True},
    }
    record_episode(db, owner, payload, idempotency_key="known-exclusion")
    item = recall_for_decision(db, agent, task="文案", conditions=current)["items"][0]
    assert item["compatibility"]["status"] == expected
    assert item["compatibility"]["need_current_check"] is False


def test_precondition_terms_are_indexed_as_condition_evidence(env):
    """Precondition words must hit retrieval with conditions={} and neutral prose."""
    from copy import deepcopy

    db, owner, agent, payload = env
    pre = {
        "target": "版本所有权",
        "require": {"environment": "managed-workspace"},
        "exclude": {"environment": "disposable-workspace"},
    }
    guarded = deepcopy(payload)
    guarded.update(
        goal="neutral goal",
        narrative="neutral narrative",
        explicit_reason=None,
        conditions={},
        preconditions=pre,
    )
    receipt = record_episode(db, owner, guarded, idempotency_key="pre-index")

    for term in ("版本所有权", "managed-workspace"):
        result = recall_for_decision(db, agent, task=term)
        ids = [item["episode_id"] for item in result["items"]]
        assert receipt["event_id"] in ids, f"term={term!r} missed episode"
        item = next(x for x in result["items"] if x["episode_id"] == receipt["event_id"])
        assert item["preconditions"] == pre
        assert item["compatibility"]["status"] == "unknown"
        assert item["compatibility"]["need_current_check"] is True

    # Exclusion metadata still only rules via caller-supplied conditions,
    # never via prose echoing the excluded value.
    hit = recall_for_decision(
        db, agent, task="disposable-workspace",
        conditions={"environment": "managed-workspace"},
    )
    item = next(x for x in hit["items"] if x["episode_id"] == receipt["event_id"])
    assert item["compatibility"]["status"] == "compatible"
    assert item["compatibility"]["basis"] == "caller_supplied_conditions"


def test_precondition_target_terms_are_retrievable(env):
    from copy import deepcopy

    db, owner, agent, payload = env
    guarded = deepcopy(payload)
    guarded.update(
        goal="neutral goal",
        narrative="neutral narrative",
        explicit_reason=None,
        conditions={},
        preconditions={"target": "overwrite owned docs", "require": {"owned": True}},
    )
    receipt = record_episode(db, owner, guarded, idempotency_key="pre-target")
    result = recall_for_decision(db, agent, task="overwrite owned docs")
    ids = [item["episode_id"] for item in result["items"]]
    assert receipt["event_id"] in ids


def test_precondition_excluded_terms_are_retrievable(env):
    from copy import deepcopy

    db, owner, agent, payload = env
    guarded = deepcopy(payload)
    guarded.update(
        goal="neutral goal",
        narrative="neutral narrative",
        explicit_reason=None,
        conditions={},
        preconditions={
            "target": "keep user edits",
            "exclude": {"environment": "zzdisposable"},
        },
    )
    receipt = record_episode(db, owner, guarded, idempotency_key="pre-exclude")
    result = recall_for_decision(db, agent, task="zzdisposable environment")
    ids = [item["episode_id"] for item in result["items"]]
    assert receipt["event_id"] in ids
