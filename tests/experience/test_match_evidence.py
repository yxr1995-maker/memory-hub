"""Match explanations must reflect actual authorized index intersections."""
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from scripts.automation_core.experience import (
    recall_for_decision, record_episode, revise_understanding, revoke,
)
from scripts.automation_core.experience.store import tokens


@pytest.mark.parametrize('mode', ['evidence', 'explore'])
def test_reason_only_match_does_not_claim_other_query_terms(env, mode):
    db, owner, agent, p = env
    p.update(goal='旧宣传稿', narrative='保持平实语气', explicit_reason=None, conditions={})
    record_episode(db, owner, p, idempotency_key='generic')
    before = db.read_bytes()
    item = recall_for_decision(db, agent, task='冰岬庆典 热闹语气', mode=mode)['items'][0]
    assert item['retrieval_match'] == {
        'basis': 'lexical_overlap_only', 'matched_terms': {'reason': ['语气']},
        'query_term_count': len(tokens('冰岬庆典 热闹语气')),
        'distinct_matched_term_count': 1, 'terms_truncated': False,
        'not_applicability_verdict': True,
    }
    assert item['compatibility']['status'] == 'unknown'
    assert db.read_bytes() == before


def test_groups_and_latest_revision_without_hidden_collection(env):
    db, owner, agent, p = env
    p.update(goal='goalneedle', narrative='original', explicit_reason='oldneedle',
             conditions={'context': 'conditionneedle'})
    r = record_episode(db, owner, p, idempotency_key='groups')
    hidden = deepcopy(p); hidden['collection_id'] = 'private'; hidden['narrative'] = 'secretneedle'
    record_episode(db, replace(owner, allowed_collections=('private',)), hidden, idempotency_key='private')
    revise_understanding(db, owner, target_id=r['event_id'], base_revision=1,
        patch={'explicit_reason': 'newneedle'}, reason='fixture correction', evidence_ids=[r['event_id']])
    result = recall_for_decision(db, agent, task='goalneedle conditionneedle oldneedle newneedle secretneedle')
    assert len(result['items']) == 1
    item = result['items'][0]
    assert item['revision'] == 2
    assert item['retrieval_match']['matched_terms'] == {
        'goal': ['goalneedle'], 'reason': ['newneedle'], 'condition': ['conditionneedle']}
    assert item['retrieval_match']['distinct_matched_term_count'] == 3
    revoke(db, owner, target_id=r['event_id'], base_revision=2, reason='withdraw')
    assert recall_for_decision(db, agent, task='goalneedle secretneedle')['items'] == []


def test_bounded_terms_preserve_exact_counts_and_budget(env):
    db, owner, agent, p = env
    task = ' '.join(f'needle{i:03}' for i in range(80))
    p.update(goal=task, narrative=task, explicit_reason=None, conditions={})
    record_episode(db, owner, p, idempotency_key='long')
    result = recall_for_decision(db, agent, task=task)
    match = result['items'][0]['retrieval_match']
    assert match['query_term_count'] == match['distinct_matched_term_count'] == 80
    assert match['terms_truncated'] is True
    assert match['matched_terms'] == {'goal': tokens(task)[:8], 'reason': tokens(task)[:8]}
    for budget in (512, 1000, 6000):
        result = recall_for_decision(db, agent, task=task, max_chars=budget)
        assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= budget


def test_linked_counterexample_never_invents_a_lexical_match(env):
    db, owner, agent, p = env
    counter = deepcopy(p)
    counter.update(goal='separate exception', narrative='unrelated detail', explicit_reason=None, conditions={})
    neg = record_episode(db, owner, counter, idempotency_key='exception')
    p.update(goal='needle', narrative='needle', explicit_reason=None, conditions={}, counterexample_ids=[neg['event_id']])
    record_episode(db, owner, p, idempotency_key='primary')
    result = recall_for_decision(db, agent, task='needle')
    counter = next(i for i in result['items'] if i['kind'] == 'counterexample')
    assert counter['retrieval_match'] == {
        'basis': 'explicit_counterexample_link', 'not_applicability_verdict': True}
