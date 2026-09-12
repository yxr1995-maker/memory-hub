import hashlib
import json
import sqlite3

import pytest

from scripts.automation_core.experience import (
    ExperienceError, read_evidence, recall_for_decision, record_episode,
    revise_understanding,
)


@pytest.mark.parametrize('source_kind', [
    'synthetic_fixture', 'agent_report', 'external_document',
    'validator_observed', 'user_explicit',
])
def test_revision_does_not_change_episode_provenance(env, source_kind):
    db, owner, agent, payload = env
    payload.update(source_kind=source_kind, synthetic=source_kind == 'synthetic_fixture')
    original = record_episode(db, owner, payload, idempotency_key=source_kind)
    revised = revise_understanding(db, owner, target_id=original['event_id'], base_revision=1,
        patch={'explicit_reason': 'A corrected understanding.'}, reason='owner-approved edit',
        evidence_ids=original['evidence_ids'])
    assert revised['source_kind'] == source_kind
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT source_kind FROM experience_versions WHERE event_id=? ORDER BY revision',
            (original['event_id'],)).fetchall() == [(source_kind,), (source_kind,)]
    item = recall_for_decision(db, agent, task=payload['goal'])['items'][0]
    assert item['source_kind'] == source_kind and item['revision'] == 2
    old = read_evidence(db, agent, evidence_id=original['event_id'], revision=1)
    current = read_evidence(db, agent, evidence_id=original['event_id'], revision=2)
    assert json.loads(old['content']) == payload
    assert current['source_kind'] == json.loads(current['content'])['source_kind'] == source_kind
    assert json.loads(current['content'])['outcome']['verification'] == 'unknown'


@pytest.mark.parametrize('rich', [False, True])
def test_legacy_revision_metadata_cannot_elevate_source_on_read(env, rich):
    db, owner, agent, payload = env
    if rich:
        payload['conditions']['rich'] = 'Synthetic background. ' * 700
    receipt = record_episode(db, owner, payload, idempotency_key='legacy')
    revise_understanding(db, owner, target_id=receipt['event_id'], base_revision=1,
        patch={'explicit_reason': 'Synthetic correction.'}, reason='fixture correction',
        evidence_ids=receipt['evidence_ids'])
    # Reproduce the stored metadata emitted by the previous implementation.
    with sqlite3.connect(db) as connection:
        connection.execute('UPDATE experience_versions SET source_kind=? WHERE event_id=? AND revision=2',
                           ('user_explicit', receipt['event_id']))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    item = recall_for_decision(db, agent, task=payload['goal'])['items'][0]
    assert item['source_kind'] == 'synthetic_fixture' and item['synthetic'] is True
    assert (item.get('presentation') == 'reference_only') is rich
    evidence = read_evidence(db, agent, evidence_id=receipt['event_id'], revision=2)
    assert evidence['source_kind'] == 'synthetic_fixture'
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_agent_cannot_use_revision_to_change_source(env):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key='denied')
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    with pytest.raises(ExperienceError) as error:
        revise_understanding(db, agent, target_id=receipt['event_id'], base_revision=1,
            patch={'explicit_reason': 'Self-approved user preference.'}, reason='claim',
            evidence_ids=receipt['evidence_ids'])
    assert error.value.code == 'ACCESS_DENIED'
    with pytest.raises(ExperienceError):
        revise_understanding(db, owner, target_id=receipt['event_id'], base_revision=1,
            patch={'source_kind': 'user_explicit'}, reason='retag', evidence_ids=receipt['evidence_ids'])
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
