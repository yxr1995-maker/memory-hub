"""M4-3: feedback may reorder equally relevant candidates and nothing more."""
from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.automation_core.experience import record_episode, recall_for_decision
from scripts.automation_core.experience.retrieval import candidates
from scripts.automation_core.experience.store import connect


def episode(payload, goal, **overrides):
    return payload | {'goal': goal, 'narrative': '同一批素材的归档说明。', 'explicit_reason': None,
                      'conditions': {}} | overrides


def feedback(target_id, action, source_kind='agent_report'):
    return {'schema_version': 1, 'collection_id': 'fixture', 'event_kind': 'feedback',
            'target_id': target_id, 'revision': 1, 'action': action, 'source_kind': source_kind,
            'receipt_id': 'receipt-' + action}


def ordered(db, agent, task='zzcommon'):
    return [item['episode_id'] for item in recall_for_decision(db, agent, task=task, mode='evidence')['items']]


@pytest.fixture
def tied(env, monkeypatch):
    """Three episodes that all score exactly one query term."""
    db, owner, agent, payload = env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_UTILITY', '1')
    ids = {name: record_episode(db, owner, episode(payload, f'zzcommon 经历{name}'),
                                idempotency_key=f'tie-{name}')['event_id'] for name in 'ABC'}
    return db, owner, agent, ids


def test_without_feedback_nothing_changes(tied):
    db, owner, agent, ids = tied
    baseline = ordered(db, agent)
    assert sorted(baseline) == sorted(ids.values())
    assert ordered(db, agent) == baseline


def test_adopted_breaks_a_tie_above_rejected_and_neutral(tied):
    db, owner, agent, ids = tied
    record_episode(db, agent, feedback(ids['C'], 'rejected'), idempotency_key='fb-c')
    record_episode(db, agent, feedback(ids['A'], 'adopted'), idempotency_key='fb-a')
    assert ordered(db, agent) == [ids['A'], ids['B'], ids['C']]


def test_owner_validation_outweighs_agent_reported_use(tied):
    db, owner, agent, ids = tied
    record_episode(db, agent, feedback(ids['A'], 'adopted'), idempotency_key='fb-a')
    record_episode(db, owner, feedback(ids['B'], 'validated', 'validator_observed'), idempotency_key='fb-b')
    assert ordered(db, agent) == [ids['B'], ids['A'], ids['C']]


def test_utility_never_outranks_a_higher_relevance_score(env, monkeypatch):
    db, owner, agent, payload = env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_UTILITY', '1')
    strong = record_episode(db, owner, episode(payload, 'zzcommon zzneedle'), idempotency_key='strong')['event_id']
    weak = record_episode(db, owner, episode(payload, 'zzcommon'), idempotency_key='weak')['event_id']
    record_episode(db, owner, feedback(strong, 'rejected'), idempotency_key='fb-strong')
    record_episode(db, owner, feedback(weak, 'validated', 'user_explicit'), idempotency_key='fb-weak')
    assert ordered(db, agent, 'zzcommon zzneedle') == [strong, weak]


def test_utility_never_grants_access_or_hides_evidence(env, monkeypatch):
    db, owner, agent, payload = env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_UTILITY', '1')
    public = record_episode(db, owner, episode(payload, 'zzcommon 公开'), idempotency_key='public')['event_id']
    secret = record_episode(db, replace(owner, allowed_collections=('fixture', 'private')),
                            episode(payload, 'zzcommon 私有', collection_id='private'),
                            idempotency_key='secret')['event_id']
    record_episode(db, replace(owner, allowed_collections=('fixture', 'private')),
                   feedback(secret, 'validated', 'validator_observed') | {'collection_id': 'private'},
                   idempotency_key='fb-secret')
    record_episode(db, owner, feedback(public, 'rejected'), idempotency_key='fb-public')
    result = recall_for_decision(db, agent, task='zzcommon', mode='evidence')
    assert [item['episode_id'] for item in result['items']] == [public]
    with connect(db) as connection:
        assert candidates(connection, agent, 'zzcommon') == [public]


def test_switch_off_restores_the_plain_order(tied, monkeypatch):
    db, owner, agent, ids = tied
    baseline = ordered(db, agent)
    record_episode(db, agent, feedback(ids['C'], 'rejected'), idempotency_key='fb-c')
    record_episode(db, agent, feedback(ids['A'], 'adopted'), idempotency_key='fb-a')
    assert ordered(db, agent) != baseline
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_UTILITY', '0')
    assert ordered(db, agent) == baseline
