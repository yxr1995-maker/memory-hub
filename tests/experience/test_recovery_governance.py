"""Post-backup governance is required before readable recovery."""
import hashlib
import sqlite3

import pytest

from scripts.automation_core.experience import (
    AccessContext, ExperienceError, initialize, read_evidence, record_episode,
    recall_for_decision, revise_understanding, revoke,
)
from scripts.automation_core.experience.recovery import backup_store, restore_store
from scripts.automation_core.experience.revisions import purge


def test_missing_current_state_cannot_publish_a_copy(env, tmp_path):
    db, owner, _, _ = env
    destination = tmp_path / 'absent' / 'copy.sqlite3'
    with pytest.raises(ExperienceError, match='CURRENT_STATE_REQUIRED'):
        restore_store(db, owner, destination=destination)
    assert not destination.parent.exists()


@pytest.mark.parametrize('withdrawal', ['revoke', 'purge'])
def test_later_withdrawal_and_snapshot_dependencies_are_applied(env, tmp_path, withdrawal):
    db, owner, agent, payload = env
    source = record_episode(db, owner, payload, idempotency_key='source')
    dependent = record_episode(db, owner, payload, idempotency_key='dependent')
    revise_understanding(db, owner, target_id=dependent['event_id'], base_revision=1,
                         patch={'explicit_reason': 'derived'}, reason='derive', evidence_ids=[source['event_id']])
    backup = tmp_path / 'backup.sqlite3'
    backup_store(db, owner, destination=backup)
    (revoke if withdrawal == 'revoke' else purge)(
        db, owner, target_id=source['event_id'], base_revision=1, reason='later withdrawal')
    before = db.read_bytes(), backup.read_bytes()
    destination = tmp_path / 'copy.sqlite3'
    receipt = restore_store(backup, owner, destination=destination, current_store=db)
    assert receipt['sha256'] == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert (db.read_bytes(), backup.read_bytes()) == before
    assert recall_for_decision(destination, agent, task='文案')['items'] == []
    # Purge removes the collection scope too; preserve the live store's denial.
    denied = 'REVOKED' if withdrawal == 'revoke' else 'ACCESS_DENIED'
    with pytest.raises(ExperienceError, match=denied):
        read_evidence(db, agent, evidence_id=source['event_id'], revision=1)
    with pytest.raises(ExperienceError, match=denied):
        read_evidence(destination, agent, evidence_id=source['event_id'], revision=1)
    assert read_evidence(destination, agent, evidence_id=dependent['event_id'], revision=2)['review_required']
    if withdrawal == 'purge':
        with sqlite3.connect(destination) as c:
            assert c.execute('SELECT count(*) FROM experience_versions WHERE event_id=?', (source['event_id'],)).fetchone()[0] == 0
            assert c.execute('SELECT original_json FROM experience_events WHERE event_id=?', (source['event_id'],)).fetchone()[0] == '{}'


def test_newer_understanding_marks_old_copy_for_review_without_fast_forward(env, tmp_path):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key='source')
    backup = tmp_path / 'backup.sqlite3'
    backup_store(db, owner, destination=backup)
    revise_understanding(db, owner, target_id=receipt['event_id'], base_revision=1,
                         patch={'explicit_reason': 'new current reason'}, reason='new brief', evidence_ids=[receipt['event_id']])
    destination = tmp_path / 'copy.sqlite3'
    restore_store(backup, owner, destination=destination, current_store=db)
    evidence = read_evidence(destination, agent, evidence_id=receipt['event_id'], revision=1)
    assert evidence['review_required'] is True
    assert payload['explicit_reason'] in evidence['content']
    assert 'new current reason' not in evidence['content']
    assert recall_for_decision(destination, agent, task='文案')['items'] == []


@pytest.mark.parametrize('problem', ['same_file', 'hardlink', 'missing', 'empty', 'older', 'identity', 'same_revision_conflict'])
def test_untrustworthy_governance_does_not_publish(env, tmp_path, problem):
    db, owner, _, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key='source')
    backup = tmp_path / 'backup.sqlite3'
    backup_store(db, owner, destination=backup)
    authority = db
    if problem == 'same_file':
        authority = backup
    elif problem == 'hardlink':
        authority = tmp_path / 'hardlink.sqlite3'; authority.hardlink_to(backup)
    elif problem in ('missing', 'empty'):
        authority = tmp_path / 'other.sqlite3'
        if problem == 'empty': initialize(authority)
    elif problem == 'older':
        revise_understanding(backup, owner, target_id=receipt['event_id'], base_revision=1,
                             patch={'explicit_reason': 'snapshot newer'}, reason='branch', evidence_ids=[receipt['event_id']])
    else:
        with sqlite3.connect(db) as c:
            if problem == 'identity':
                c.execute("UPDATE experience_events SET original_json='{}'")
            else:
                c.execute("UPDATE experience_versions SET payload_json='{}'")
    before = db.read_bytes(), backup.read_bytes()
    destination = tmp_path / 'absent' / 'copy.sqlite3'
    with pytest.raises(ExperienceError):
        restore_store(backup, owner, destination=destination, current_store=authority)
    assert not destination.parent.exists()
    assert (db.read_bytes(), backup.read_bytes()) == before


def test_current_store_requires_all_collection_access(env, tmp_path):
    db, owner, _, payload = env
    record_episode(db, owner, payload, idempotency_key='source')
    backup = tmp_path / 'backup.sqlite3'; backup_store(db, owner, destination=backup)
    other = AccessContext('other', ('other',), 'owner')
    record_episode(db, other, {**payload, 'collection_id': 'other'}, idempotency_key='other')
    destination = tmp_path / 'absent' / 'copy.sqlite3'
    with pytest.raises(ExperienceError, match='ACCESS_DENIED'):
        restore_store(backup, owner, destination=destination, current_store=db)
    assert not destination.parent.exists()


def test_governance_failure_discards_private_copy(env, tmp_path, monkeypatch):
    from scripts.automation_core.experience import recovery
    db, owner, _, payload = env
    record_episode(db, owner, payload, idempotency_key='source')
    backup = tmp_path / 'backup.sqlite3'; backup_store(db, owner, destination=backup)
    before = db.read_bytes(), backup.read_bytes()
    def fail(connection, plan):
        connection.execute('DELETE FROM experience_keys')
        raise RuntimeError('injected governance failure')
    monkeypatch.setattr(recovery, '_apply_governance', fail, raising=False)
    destination = tmp_path / 'copy.sqlite3'
    with pytest.raises(RuntimeError, match='injected governance failure'):
        restore_store(backup, owner, destination=destination, current_store=db)
    assert not destination.exists()
    assert not list(tmp_path.glob('.experience-copy-*'))
    assert (db.read_bytes(), backup.read_bytes()) == before
