"""Explicit applicability evidence must be searchable without becoming approval."""
import json
import sqlite3

import pytest

from scripts.automation_core.experience import (
    AccessContext, ExperienceError, recall_for_decision, record_episode,
    read_evidence, revise_understanding, revoke,
)
from scripts.automation_core.experience.store import rebuild_keys


def conditional(payload):
    return {**payload, "goal": "neutral", "narrative": "original material",
            "explicit_reason": None, "conditions": {"brand": "legacy_brand"},
            "preconditions": {"target": "版本所有权",
                              "require": {"workspace_kind": "managed_workspace"},
                              "exclude": {"workspace_kind": "disposable_workspace"}}}


@pytest.mark.parametrize("query", ["版本所有权", "managed_workspace", "disposable_workspace", "workspace_kind"])
def test_explicit_precondition_entries_are_searchable_without_implied_compatibility(env, query):
    db, owner, agent, payload = env
    event = record_episode(db, owner, conditional(payload), idempotency_key="preconditions")
    before = db.read_bytes()
    result = recall_for_decision(db, agent, task=query)
    assert [i["episode_id"] for i in result["items"]] == [event["event_id"]]
    assert result["items"][0]["compatibility"] == {"status": "unknown", "need_current_check": True}
    assert db.read_bytes() == before
    assert recall_for_decision(db, agent.narrow_collections(("other",)), task=query)["items"] == []


@pytest.mark.parametrize("query", ["null", "target", "require", "exclude"])
def test_precondition_wrappers_do_not_add_generic_search_terms(env, query):
    db, owner, agent, payload = env
    guarded = conditional(payload)
    record_episode(db, owner, guarded, idempotency_key="guarded")
    without = {k: v for k, v in guarded.items() if k != "preconditions"}
    record_episode(db, owner, without, idempotency_key="unguarded")
    assert recall_for_decision(db, agent, task=query)["items"] == []
    assert len(recall_for_decision(db, agent, task="legacy_brand")["items"]) == 2


def test_revision_replaces_condition_keys_but_keeps_old_evidence(env):
    db, owner, agent, payload = env
    event = record_episode(db, owner, conditional(payload), idempotency_key="versioned")
    revise_understanding(db, owner, target_id=event["event_id"], base_revision=1,
                         patch={"preconditions": {"target": "并发校验", "require": {"mode": "current_mode"}}},
                         reason="synthetic correction", evidence_ids=[event["event_id"]])
    assert recall_for_decision(db, agent, task="版本所有权 managed_workspace")["items"] == []
    result = recall_for_decision(db, agent, task="并发校验 current_mode", conditions={"mode": "current_mode"})
    assert result["items"][0]["revision"] == 2
    assert result["items"][0]["compatibility"]["status"] == "compatible"
    old = read_evidence(db, agent, evidence_id=event["event_id"], revision=1)
    assert json.loads(old["content"])["preconditions"]["target"] == "版本所有权"


def test_old_keys_change_only_on_authorized_explicit_rebuild(env):
    db, owner, agent, payload = env
    event = record_episode(db, owner, conditional(payload), idempotency_key="legacy")
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM experience_keys WHERE key_group='condition' AND token NOT IN ('brand','legacy_brand')")
    before = db.read_bytes()
    assert recall_for_decision(db, agent, task="版本所有权")["items"] == []
    assert db.read_bytes() == before
    with pytest.raises(ExperienceError) as error:
        rebuild_keys(db, agent)
    assert error.value.code == "ACCESS_DENIED" and db.read_bytes() == before
    rebuild_keys(db, owner)
    assert recall_for_decision(db, agent, task="版本所有权")["items"][0]["episode_id"] == event["event_id"]


def test_rebuild_keeps_withdrawal_review_and_collection_boundaries(env):
    db, owner, agent, payload = env
    broad = AccessContext("fixture-owner", ("fixture", "private"), "owner", owner.artifact_roots)
    ids = {}
    for label in ("active", "revoked", "review", "private"):
        p = conditional(payload)
        if label == "private":
            p["collection_id"] = "private"
        ids[label] = record_episode(db, broad, p, idempotency_key=label)["event_id"]
    revoke(db, broad, target_id=ids["revoked"], base_revision=1, reason="synthetic withdrawal")
    # Build an already flagged current version as an isolated recovery fixture.
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE experience_versions SET review_required=1 WHERE event_id=?", (ids["review"],))
    before = db.read_bytes()
    with pytest.raises(ExperienceError) as error:
        rebuild_keys(db, owner)
    assert error.value.code == "ACCESS_DENIED" and db.read_bytes() == before
    rebuild_keys(db, broad)
    items = recall_for_decision(db, agent, task="版本所有权")["items"]
    assert [item["episode_id"] for item in items] == [ids["active"]]
