from __future__ import annotations

import json

from scripts.automation_core.experience import record_episode, recall_for_decision


def test_reasons_are_recalled_with_provenance_and_bounded_output(env):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key="case-01")
    result = recall_for_decision(
        db,
        agent,
        task="修改文案，要有感情但不要有负担",
        mode="evidence",
        max_chars=6000,
    )
    assert receipt["event_id"] in [x["episode_id"] for x in result["items"]]
    assert all(x["evidence_refs"] for x in result["items"])
    assert result["limits"]["unit"] == "unicode_chars"
    assert (
        len(json.dumps(result, ensure_ascii=False, separators=(",", ":"))) <= 6000
    )
    assert result["usage"]["model_calls"] == 0


def test_goal_and_entity_keys_both_hit_same_episode(env):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key="case-01")
    for task in (
        "宠物内容文案修改任务",
        "fixture-brand 品牌的新简报",
        "评审反馈里不要愧疚施压",
    ):
        result = recall_for_decision(db, agent, task=task)
        ids = [x["episode_id"] for x in result["items"]]
        assert receipt["event_id"] in ids, f"task={task!r} missed episode"


def test_no_match_returns_empty_items_not_error(env):
    db, owner, agent, payload = env
    record_episode(db, owner, payload, idempotency_key="case-01")
    result = recall_for_decision(db, agent, task="完全无关的数据库迁移任务")
    assert result["items"] == []
    assert result["omitted_count"] == 0


def test_recall_respects_collection_scope(env):
    db, owner, agent, payload = env
    record_episode(db, owner, payload, idempotency_key="case-01")
    restricted = agent.narrow_collections(("other-collection",))
    result = recall_for_decision(db, restricted, task="要有感情但不要有负担")
    assert result["items"] == []


def test_read_evidence_returns_revisioned_content(env):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key="case-01")
    out = agent_read(agent, db, receipt)
    assert out["content"].index("要有感情") >= 0
    assert out["revision"] == receipt["revision"]


def agent_read(agent, db, receipt):
    from scripts.automation_core.experience import read_evidence

    return read_evidence(
        db,
        agent,
        evidence_id=receipt["evidence_ids"][0],
        revision=receipt["revision"],
    )


def test_linked_counterexample_in_candidate_pool_still_gets_its_slot(env):
    from copy import deepcopy
    db,owner,agent,payload=env
    counter=deepcopy(payload)
    counter.update(goal='needle',narrative='independent exception',explicit_reason='independent exception',conditions={})
    negative=record_episode(db,owner,counter,idempotency_key='linked-negative')
    for i in range(3):
        primary=deepcopy(payload)
        primary.update(goal='needle priority marker',narrative='needle priority marker',explicit_reason='needle priority marker',conditions={},counterexample_ids=[negative['event_id']])
        record_episode(db,owner,primary,idempotency_key=f'primary-{i}')
    result=recall_for_decision(db,agent,task='needle priority marker')
    assert len(result['items'])==4
    assert result['items'][-1]['kind']=='counterexample'
    assert result['items'][-1]['episode_id']==negative['event_id']
    assert result["omitted_count"]==0


def test_preconditions_drive_compatibility_verdict(env):
    from copy import deepcopy
    from scripts.automation_core.experience import record_episode, recall_for_decision

    db, owner, agent, payload = env
    guarded = deepcopy(payload)
    guarded.update(
        goal="保护用户修改的生成文档",
        narrative="写入前先核对所有权和版本，再决定覆盖或保留。",
        explicit_reason="版本不符时保留外部修改。",
        conditions={"task_kind": "engineering"},
        preconditions={
            "target": "生成的文档可能被用户编辑过",
            "require": {"environment": "user-edited"},
            "exclude": {"environment": "disposable"},
        },
    )
    receipt = record_episode(db, owner, guarded, idempotency_key="cond-01")
    task = "更新生成文档前检查所有权和版本"

    hit = recall_for_decision(db, agent, task=task, conditions={"environment": "user-edited"})
    item = next(x for x in hit["items"] if x["episode_id"] == receipt["event_id"])
    assert item["compatibility"] == {"status": "compatible", "need_current_check": False, "basis": "caller_supplied_conditions"}

    miss = recall_for_decision(db, agent, task=task, conditions={"environment": "disposable"})
    item = next(x for x in miss["items"] if x["episode_id"] == receipt["event_id"])
    assert item["compatibility"] == {"status": "incompatible", "need_current_check": False, "basis": "caller_supplied_conditions"}

    unknown = recall_for_decision(db, agent, task=task)
    item = next(x for x in unknown["items"] if x["episode_id"] == receipt["event_id"])
    assert item["compatibility"] == {"status": "unknown", "need_current_check": True}


def test_prose_matches_never_count_as_compatible(env):
    from copy import deepcopy
    from scripts.automation_core.experience import record_episode, recall_for_decision

    db, owner, agent, payload = env
    guarded = deepcopy(payload)
    guarded.update(
        goal="保护用户修改的生成文档",
        narrative="写入前先核对所有权和版本，再决定覆盖或保留。",
        conditions={"task_kind": "engineering"},
        preconditions={
            "target": "生成的文档可能被用户编辑过",
            "require": {"environment": "user-edited"},
        },
    )
    receipt = record_episode(db, owner, guarded, idempotency_key="cond-02")
    # Free-text echoes of the precondition description must not flip the verdict.
    result = recall_for_decision(
        db, agent, task="更新生成文档前检查所有权和版本",
        conditions={"note": "目录完全隔离且内容可丢弃，生成的文档可能被用户编辑过"},
    )
    item = next(x for x in result["items"] if x["episode_id"] == receipt["event_id"])
    assert item["compatibility"] == {"status": "unknown", "need_current_check": True}


def test_require_missing_none_and_bool_int_are_not_satisfied(env):
    import pytest
    from copy import deepcopy
    from scripts.automation_core.experience import (
        ExperienceError, record_episode, recall_for_decision,
    )

    db, owner, agent, payload = env
    none_pre = deepcopy(payload)
    none_pre.update(
        goal="protect generated docs",
        narrative="check ownership before overwrite",
        conditions={"task_kind": "engineering"},
        preconditions={"target": "docs may be user edited", "require": {"environment": None}},
    )
    with pytest.raises(ExperienceError) as err:
        record_episode(db, owner, none_pre, idempotency_key="cond-none")
    assert err.value.code == "PAYLOAD_INVALID"

    bool_pre = deepcopy(payload)
    bool_pre.update(
        goal="protect generated docs",
        narrative="check ownership before overwrite",
        conditions={"task_kind": "engineering"},
        preconditions={"target": "docs may be user edited", "require": {"strict": True}},
    )
    receipt = record_episode(db, owner, bool_pre, idempotency_key="cond-bool")
    task = "protect generated docs"
    cases = [
        ({"other": "x"}, "unknown"),          # required key missing entirely
        ({"strict": 1}, "unknown"),           # int must not satisfy bool requirement
        ({"strict": True}, "compatible"),     # exact type and value match
    ]
    for conditions, expected in cases:
        result = recall_for_decision(db, agent, task=task, conditions=conditions)
        item = next(x for x in result["items"] if x["episode_id"] == receipt["event_id"])
        assert item["compatibility"]["status"] == expected, f"conditions={conditions}"


def test_query_conditions_reject_nested_or_nonfinite_values(env):
    import pytest
    from scripts.automation_core.experience import ExperienceError
    db,owner,agent,p=env
    for conditions in ({'x':{'nested':1}}, {'x':float('nan')}, {'x':float('inf')}, {1:'value'}):
        with pytest.raises(ExperienceError):recall_for_decision(db,agent,task='文案',conditions=conditions)


def test_card_keeps_synthetic_and_historical_condition_boundary(env):
    from scripts.automation_core.experience import revise_understanding
    db,owner,agent,p=env
    p['preconditions']={'target':'overwrite only owned output','require':{'owned':True}}
    r=record_episode(db,owner,p,idempotency_key='condition-boundary')
    revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,
        patch={'explicit_reason':'合成更正'},reason='fixture correction',evidence_ids=[r['event_id']])
    item=recall_for_decision(db,agent,task='文案',conditions={'owned':True})['items'][0]
    assert item['synthetic'] is True
    assert item['preconditions']==p['preconditions']
    assert item['compatibility']['basis']=='caller_supplied_conditions'
    assert item['conditions_scope']=='historical_episode_only'
