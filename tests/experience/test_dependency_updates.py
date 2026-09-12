"""Revision dependency propagation: revising an understanding flags dependents."""
import pytest

from scripts.automation_core.experience import (
    read_evidence,
    recall_for_decision,
    record_episode,
    revise_understanding,
    ExperienceError,
)


def test_revision_flags_dependent_for_review(env):
    db, owner, agent, p = env
    source = record_episode(db, owner, p, idempotency_key="source")
    dependent = record_episode(db, owner, p, idempotency_key="dependent")
    revise_understanding(
        db, owner, target_id=dependent["event_id"], base_revision=1,
        patch={"explicit_reason": "基于来源的理解"}, reason="inference",
        evidence_ids=[source["event_id"]])
    revise_understanding(
        db, owner, target_id=source["event_id"], base_revision=1,
        patch={"explicit_reason": "来源理解已更正"}, reason="source correction",
        evidence_ids=[source["event_id"]])
    items = recall_for_decision(db, agent, task="文案")["items"]
    assert dependent["event_id"] not in {i["episode_id"] for i in items}
    assert read_evidence(db, agent, evidence_id=dependent["event_id"], revision=2)["review_required"] is True


def test_self_revision_stays_recommendable(env):
    db, owner, agent, p = env
    r = record_episode(db, owner, p, idempotency_key="self")
    revise_understanding(
        db, owner, target_id=r["event_id"], base_revision=1,
        patch={"explicit_reason": "自我修正"}, reason="self correction",
        evidence_ids=[r["event_id"]])
    items = recall_for_decision(db, agent, task="文案")["items"]
    assert [i["episode_id"] for i in items] == [r["event_id"]]
    assert read_evidence(db, agent, evidence_id=r["event_id"], revision=2)["review_required"] is False


def test_owner_revalidation_restores_dependent(env):
    db, owner, agent, p = env
    source = record_episode(db, owner, p, idempotency_key="source")
    dependent = record_episode(db, owner, p, idempotency_key="dependent")
    revise_understanding(
        db, owner, target_id=dependent["event_id"], base_revision=1,
        patch={"explicit_reason": "基于来源的理解"}, reason="inference",
        evidence_ids=[source["event_id"]])
    revise_understanding(
        db, owner, target_id=source["event_id"], base_revision=1,
        patch={"explicit_reason": "来源理解已更正"}, reason="source correction",
        evidence_ids=[source["event_id"]])
    revise_understanding(
        db, owner, target_id=dependent["event_id"], base_revision=2,
        patch={"explicit_reason": "对齐更正后的来源"},
        reason="revalidate against corrected source", evidence_ids=[source["event_id"]])
    by_id = {i["episode_id"]: i for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert by_id[dependent["event_id"]]["explicit_reason"] == "对齐更正后的来源"
    assert read_evidence(db, agent, evidence_id=dependent["event_id"], revision=3)["review_required"] is False


def test_transitive_revision_flags_chain_but_not_unrelated(env):
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    c = record_episode(db, owner, p, idempotency_key="c")
    d = record_episode(db, owner, p, idempotency_key="d")
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 A 的理解"}, reason="inference",
        evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=c["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 B 的理解"}, reason="inference",
        evidence_ids=[b["event_id"]])
    revise_understanding(
        db, owner, target_id=a["event_id"], base_revision=1,
        patch={"explicit_reason": "A 已更正"}, reason="source correction",
        evidence_ids=[a["event_id"]])
    ids = {i["episode_id"] for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert a["event_id"] in ids
    assert d["event_id"] in ids
    assert b["event_id"] not in ids
    assert c["event_id"] not in ids
    assert read_evidence(db, agent, evidence_id=c["event_id"], revision=2)["review_required"] is True


def test_cyclic_dependencies_terminate_and_flag_whole_cycle(env):
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    c = record_episode(db, owner, p, idempotency_key="c")
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 A"}, reason="inference", evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=c["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 B"}, reason="inference", evidence_ids=[b["event_id"]])
    revise_understanding(
        db, owner, target_id=a["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 C"}, reason="inference", evidence_ids=[c["event_id"]])
    # a -> b -> c -> a cycle: revising a must flag b and c, then stop.
    revise_understanding(
        db, owner, target_id=a["event_id"], base_revision=2,
        patch={"explicit_reason": "A 循环内更正"}, reason="cycle correction",
        evidence_ids=[c["event_id"]])
    ids = {i["episode_id"] for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert a["event_id"] in ids
    assert b["event_id"] not in ids
    assert c["event_id"] not in ids
    assert read_evidence(db, agent, evidence_id=c["event_id"], revision=2)["review_required"] is True


def test_stale_revision_conflict_leaves_no_side_effects(env):
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    c = record_episode(db, owner, p, idempotency_key="c")
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 A"}, reason="inference", evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=c["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 B"}, reason="inference", evidence_ids=[b["event_id"]])
    with pytest.raises(ExperienceError) as e:
        revise_understanding(
            db, owner, target_id=b["event_id"], base_revision=1,
            patch={"explicit_reason": "stale base"}, reason="stale",
            evidence_ids=[b["event_id"]])
    assert e.value.code == "VERSION_CONFLICT"
    ids = {i["episode_id"] for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert ids == {a["event_id"], b["event_id"], c["event_id"]}
    assert read_evidence(db, agent, evidence_id=c["event_id"], revision=2)["review_required"] is False


def test_multi_revision_dependent_flags_current_version(env):
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    for base in (1, 2):
        revise_understanding(
            db, owner, target_id=b["event_id"], base_revision=base,
            patch={"explicit_reason": f"基于 A 的第 {base} 版理解"}, reason="inference",
            evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=a["event_id"], base_revision=1,
        patch={"explicit_reason": "A 已更正"}, reason="source correction",
        evidence_ids=[a["event_id"]])
    ids = {i["episode_id"]: i for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert b["event_id"] not in ids
    current = read_evidence(db, agent, evidence_id=b["event_id"], revision=3)
    assert current["review_required"] is True


def test_revoke_propagates_to_transitive_dependents(env):
    from scripts.automation_core.experience import revoke
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    c = record_episode(db, owner, p, idempotency_key="c")
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 A"}, reason="inference", evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=c["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 B"}, reason="inference", evidence_ids=[b["event_id"]])
    revoke(db, owner, target_id=a["event_id"], base_revision=1, reason="withdraw source")
    ids = {i["episode_id"] for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert b["event_id"] not in ids
    assert c["event_id"] not in ids
    assert read_evidence(db, agent, evidence_id=c["event_id"], revision=2)["review_required"] is True


def test_purge_propagates_to_transitive_dependents(env, tmp_path):
    from scripts.automation_core.experience.revisions import purge
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    c = record_episode(db, owner, p, idempotency_key="c")
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 A"}, reason="inference", evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=c["event_id"], base_revision=1,
        patch={"explicit_reason": "基于 B"}, reason="inference", evidence_ids=[b["event_id"]])
    purge(db, owner, target_id=a["event_id"], base_revision=1, reason="privacy")
    ids = {i["episode_id"] for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert c["event_id"] not in ids
    assert read_evidence(db, agent, evidence_id=c["event_id"], revision=2)["review_required"] is True


def test_released_dependency_not_reflagged_by_old_version(env):
    db, owner, agent, p = env
    a = record_episode(db, owner, p, idempotency_key="a")
    b = record_episode(db, owner, p, idempotency_key="b")
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=1,
        patch={"explicit_reason": "曾基于 A"}, reason="inference", evidence_ids=[a["event_id"]])
    revise_understanding(
        db, owner, target_id=b["event_id"], base_revision=2,
        patch={"explicit_reason": "已独立于 A"}, reason="dependency released",
        evidence_ids=[b["event_id"]])
    revise_understanding(
        db, owner, target_id=a["event_id"], base_revision=1,
        patch={"explicit_reason": "A 已更正"}, reason="source correction",
        evidence_ids=[a["event_id"]])
    ids = {i["episode_id"] for i in recall_for_decision(db, agent, task="文案")["items"]}
    assert b["event_id"] in ids
    assert read_evidence(db, agent, evidence_id=b["event_id"], revision=3)["review_required"] is False
