from scripts.automation_core.experience import record_episode, revise_understanding, revoke
from scripts.automation_core.experience.adapter import ExperienceAdapter


def test_reference_delivery_refreshes_revision_and_rechecks_revocation(env):
    db, owner, agent, payload = env
    payload['conditions']['rich_context'] = 'Synthetic detail. ' * 700
    receipt = record_episode(db, owner, payload, idempotency_key='reference-session')
    adapter = ExperienceAdapter(db, agent)
    query = {'task': '修改宠物内容文案', 'session_id': 'reference-session'}

    first = adapter.recall_for_decision(**query)
    assert first['items'][0]['presentation'] == 'reference_only'
    assert first['items'][0]['read_required'] is True
    assert first['selected_refs'] == [{'evidence_id': receipt['event_id'], 'revision': 1}]

    repeated = adapter.recall_for_decision(**query)
    assert repeated['items'] == [] and repeated['selected_refs'] == []
    assert repeated['deduplicated_count'] == 1

    revise_understanding(db, owner, target_id=receipt['event_id'], base_revision=1,
                         patch={'explicit_reason': 'Current brief correction.'},
                         reason='synthetic correction', evidence_ids=[receipt['event_id']])
    revised = adapter.recall_for_decision(**query)
    assert revised['items'][0]['presentation'] == 'reference_only'
    assert revised['selected_refs'] == [{'evidence_id': receipt['event_id'], 'revision': 2}]
    refreshed = adapter.recall_for_decision(**query, refresh=True)
    assert refreshed['selected_refs'] == revised['selected_refs']

    revoke(db, owner, target_id=receipt['event_id'], base_revision=2,
           reason='withdraw synthetic source')
    withdrawn = adapter.recall_for_decision(**query)
    assert withdrawn['items'] == [] and withdrawn['selected_refs'] == []
    assert 'ALREADY_DELIVERED' not in withdrawn['degraded_reasons']
