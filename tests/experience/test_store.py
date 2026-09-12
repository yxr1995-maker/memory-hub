from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.automation_core.experience import ExperienceError, record_episode


def test_retry_is_idempotent_and_changed_payload_conflicts(env):
    db, owner, _, payload = env
    first = record_episode(db, owner, payload, idempotency_key="case-01")
    second = record_episode(db, owner, payload, idempotency_key="case-01")
    assert first["event_id"] == second["event_id"]
    assert first["revision"] == second["revision"]
    changed = deepcopy(payload)
    changed["explicit_reason"] = "不同反馈"
    with pytest.raises(ExperienceError) as err:
        record_episode(db, owner, changed, idempotency_key="case-01")
    assert err.value.code == "IDEMPOTENCY_CONFLICT"


def test_agent_cannot_self_certify_user_preference(env):
    db, _, agent, payload = env
    payload = deepcopy(payload)
    payload["source_kind"] = "user_explicit"
    with pytest.raises(ExperienceError) as err:
        record_episode(db, agent, payload, idempotency_key="forged-01")
    assert err.value.code == "ACCESS_DENIED"


def test_owner_can_record_user_explicit(env):
    db, owner, _, payload = env
    payload = deepcopy(payload)
    payload["source_kind"] = "user_explicit"
    receipt = record_episode(db, owner, payload, idempotency_key="user-01")
    assert receipt["event_id"]
    assert receipt["revision"] >= 1
    assert receipt["evidence_ids"]


def test_first_read_without_db_is_not_initialized(tmp_path):
    db = tmp_path / "missing.sqlite3"
    _, agent, _ = (None, None, None)  # placeholder to keep structure clear
    from scripts.automation_core.experience import AccessContext, recall_for_decision

    agent = AccessContext(
        subject_id="a",
        allowed_collections=("fixture",),
        role="agent",
        artifact_roots=(str(tmp_path),),
    )
    with pytest.raises(ExperienceError) as err:
        recall_for_decision(db, agent, task="任意任务")
    assert err.value.code == "NOT_INITIALIZED"


def test_payload_validation_rejects_missing_required_fields(env):
    db, owner, _, payload = env
    bad = deepcopy(payload)
    del bad["narrative"]
    with pytest.raises(ExperienceError) as err:
        record_episode(db, owner, bad, idempotency_key="bad-01")
    assert err.value.code == "PAYLOAD_INVALID"


def test_collection_outside_allowed_is_denied(env):
    db, owner, _, payload = env
    payload = deepcopy(payload)
    payload["collection_id"] = "other-collection"
    with pytest.raises(ExperienceError) as err:
        record_episode(db, owner, payload, idempotency_key="scope-01")
    assert err.value.code == "ACCESS_DENIED"

