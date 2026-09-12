import hashlib
import json
from copy import deepcopy

import pytest

from scripts.automation_core.experience import (
    ExperienceError, read_evidence, recall_for_decision, record_episode,
    revise_understanding, revoke,
)


@pytest.mark.parametrize('mode', ['evidence', 'explore'])
def test_rich_episode_keeps_reference_and_full_versioned_source(env, mode):
    db, owner, agent, payload = env
    payload['conditions']['rich_context'] = 'Synthetic context. ' * 700
    payload['explicit_reason'] = 'Earlier observations. ' * 80 + 'FINAL: current correction wins.'
    receipt = record_episode(db, owner, payload, idempotency_key=mode)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    result = recall_for_decision(db, agent, task='修改宠物内容文案', mode=mode)
    assert len(result['items']) == 1
    item = result['items'][0]
    assert item['presentation'] == 'reference_only' and item['read_required'] is True
    assert item['episode_id'] == receipt['event_id'] and item['revision'] == 1
    assert item['synthetic'] is True and item['source_kind'] == 'synthetic_fixture'
    assert {'goal', 'explicit_reason', 'conditions', 'preconditions', 'outcome'} <= set(item['omitted_fields'])
    assert 'explicit_reason' not in item and 'conditions' not in item
    assert 'preview' not in item and 'artifact_refs' not in item
    assert result['selected_refs'] == [{'evidence_id': receipt['event_id'], 'revision': 1}]
    assert result['omitted_count'] == 0
    assert 'OUTPUT_BUDGET' in result['degraded_reasons']
    assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= 6000
    pieces, offset = [], 0
    while True:
        page = read_evidence(db, agent, evidence_id=receipt['event_id'], revision=1,
                             offset=offset, max_chars=1700)
        pieces.append(page['content'])
        if page['next_offset'] is None:
            break
        assert page['next_offset'] > offset
        offset = page['next_offset']
    assert json.loads(''.join(pieces)) == payload
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before

    revised = revise_understanding(db, owner, target_id=receipt['event_id'], base_revision=1,
                                  patch={'explicit_reason': 'New explicit correction.'},
                                  reason='synthetic correction', evidence_ids=[receipt['event_id']])
    latest = recall_for_decision(db, agent, task='修改宠物内容文案', mode=mode)
    assert latest['selected_refs'] == [{'evidence_id': receipt['event_id'], 'revision': 2}]
    assert latest['items'][0]['read_required'] is True
    revoke(db, owner, target_id=receipt['event_id'], base_revision=revised['revision'],
           reason='withdraw synthetic source')
    assert recall_for_decision(db, agent, task='修改宠物内容文案', mode=mode)['items'] == []
    with pytest.raises(ExperienceError) as error:
        read_evidence(db, agent, evidence_id=receipt['event_id'], revision=1)
    assert error.value.code == 'REVOKED'


def test_normal_card_shape_stays_unchanged(env):
    db, owner, agent, payload = env
    record_episode(db, owner, payload, idempotency_key='normal')
    result = recall_for_decision(db, agent, task='修改宠物内容文案')
    item = result['items'][0]
    assert item['explicit_reason'] == payload['explicit_reason']
    assert item['conditions'] == payload['conditions']
    assert not {'presentation', 'read_required', 'omitted_fields', 'goal_preview'} & item.keys()
    assert result['degraded_reasons'] == []


def test_tiny_budget_still_bounds_whole_json(env):
    db, owner, agent, payload = env
    payload['conditions']['rich_context'] = 'Synthetic context. ' * 700
    record_episode(db, owner, payload, idempotency_key='tiny')
    result = recall_for_decision(db, agent, task='修改宠物内容文案', max_chars=512)
    assert len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) <= 512
    assert result['items'] == [] and result['selected_refs'] == []
    assert result['degraded_reasons'] == ['OUTPUT_BUDGET']


def test_drop_lower_ranked_cards_before_reference_fallback(env):
    db, owner, agent, payload = env
    top = deepcopy(payload)
    top.update(goal='ranking budget needle', narrative='ranking budget needle',
               explicit_reason='ranking budget needle', conditions={'rich': 'Context. ' * 1600})
    first = record_episode(db, owner, top, idempotency_key='first')
    for index in range(2):
        lower = deepcopy(payload)
        lower.update(goal='needle', narrative='Other detail.', explicit_reason=None, conditions={})
        record_episode(db, owner, lower, idempotency_key=f'lower-{index}')
    result = recall_for_decision(db, agent, task='ranking budget needle')
    assert [x['episode_id'] for x in result['items']] == [first['event_id']]
    assert result['items'][0]['presentation'] == 'reference_only'
    assert result['omitted_count'] == 2
    assert result['usage']['candidates_considered'] == 3
