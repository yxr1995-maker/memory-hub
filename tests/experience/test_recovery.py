"""Recovery boundaries use real SQLite files, including an open WAL writer."""
import hashlib
import os
from pathlib import Path
import sqlite3

import pytest

from scripts.automation_core.experience import (
    AccessContext, ExperienceError, read_evidence, record_episode,
    recall_for_decision, revise_understanding, revoke,
)
from scripts.automation_core.experience.revisions import export_episode, purge


def operation(name):
    from scripts.automation_core.experience import recovery
    return getattr(recovery, name + '_store')


def test_wal_snapshot_preserves_governance_and_detaches_exports(env, tmp_path):
    db, owner, agent, payload = env
    wal = sqlite3.connect(db)
    try:
        wal.execute('PRAGMA journal_mode=WAL')
        wal.execute('PRAGMA wal_autocheckpoint=0')
        wal.execute('BEGIN')
        wal.execute('SELECT count(*) FROM experience_events').fetchone()
        live = record_episode(db, owner, payload, idempotency_key='live')
        revise_understanding(db, owner, target_id=live['event_id'], base_revision=1,
                             patch={'explicit_reason': 'current reason'}, reason='correction',
                             evidence_ids=[live['event_id']])
        source = record_episode(db, owner, payload, idempotency_key='source')
        dependent = record_episode(db, owner, payload, idempotency_key='dependent')
        revise_understanding(db, owner, target_id=dependent['event_id'], base_revision=1,
                             patch={'explicit_reason': 'dependent reason'}, reason='inference',
                             evidence_ids=[source['event_id']])
        revoke(db, owner, target_id=source['event_id'], base_revision=1, reason='withdraw')
        deleted = record_episode(db, owner, payload, idempotency_key='deleted')
        purge(db, owner, target_id=deleted['event_id'], base_revision=1, reason='erase')
        feedback = {'schema_version': 1, 'collection_id': 'fixture', 'event_kind': 'feedback',
                    'target_id': live['event_id'], 'revision': 2, 'action': 'adopted',
                    'source_kind': 'agent_report', 'receipt_id': 'qr-fixture'}
        record_episode(db, agent, feedback, idempotency_key='adoption')
        exported = tmp_path / 'original.md'
        export_episode(db, owner, target_id=live['event_id'], destination=exported)
        original_export = exported.read_bytes()
        wal_file = Path(str(db) + '-wal')
        assert wal_file.stat().st_size > 0
        before = (db.read_bytes(), wal_file.read_bytes())
        backup = tmp_path / 'snapshot.sqlite3'
        restored = tmp_path / 'restored.sqlite3'
        receipt = operation('backup')(db, owner, destination=backup)
        assert receipt['schema_version'] == 2
        assert receipt['sha256'] == hashlib.sha256(backup.read_bytes()).hexdigest()
        assert os.stat(backup).st_mode & 0o777 == 0o600
        original_backup = backup.read_bytes()
        result = operation('restore')(backup, owner, destination=restored, current_store=db)
        assert result['detached_exports_count'] == 1
        assert (db.read_bytes(), wal_file.read_bytes()) == before
        assert backup.read_bytes() == original_backup
        recalled = recall_for_decision(restored, agent, task='文案')
        assert [item['episode_id'] for item in recalled['items']] == [live['event_id']]
        assert recalled['items'][0]['explicit_reason'] == 'current reason'
        assert read_evidence(restored, agent, evidence_id=live['event_id'], revision=1)
        assert read_evidence(restored, agent, evidence_id=dependent['event_id'], revision=2)['review_required']
        with pytest.raises(ExperienceError, match='REVOKED'):
            read_evidence(restored, agent, evidence_id=source['event_id'], revision=1)
        with sqlite3.connect(restored) as connection:
            assert connection.execute('SELECT count(*) FROM experience_feedback').fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM experience_events WHERE status='deleted'").fetchone()[0] == 1
            assert connection.execute('SELECT count(*) FROM experience_exports').fetchone()[0] == 0
        purge(restored, owner, target_id=live['event_id'], base_revision=2, reason='copy erasure')
        assert exported.read_bytes() == original_export
    finally:
        wal.close()


@pytest.mark.parametrize('name', ['backup', 'restore'])
@pytest.mark.parametrize('access', ['agent', 'wrong_collection', 'mixed_collections'])
def test_recovery_rejects_insufficient_access_before_creating_directory(env, tmp_path, name, access):
    db, owner, agent, payload = env
    record_episode(db, owner, payload, idempotency_key='fixture')
    ctx = agent if access == 'agent' else AccessContext('owner', ('other',), 'owner')
    if access == 'mixed_collections':
        other = AccessContext('other-owner', ('other',), 'owner')
        record_episode(db, other, {**payload, 'collection_id': 'other'}, idempotency_key='other')
        ctx = owner
    destination = tmp_path / 'no-directory' / 'copy.sqlite3'
    before = db.read_bytes()
    with pytest.raises(ExperienceError, match='ACCESS_DENIED'):
        operation(name)(db, ctx, destination=destination, **({'current_store': db} if name == 'restore' else {}))
    assert not destination.parent.exists()
    assert db.read_bytes() == before


@pytest.mark.parametrize('name', ['backup', 'restore'])
@pytest.mark.parametrize('conflict', ['file', 'directory', 'dangling_symlink', '-wal', '-shm', '-journal'])
def test_recovery_preserves_existing_destination_and_sidecars(env, tmp_path, name, conflict):
    db, owner, _, _ = env
    destination = tmp_path / 'copy.sqlite3'
    obstacle = destination
    if conflict == 'directory':
        obstacle.mkdir()
    elif conflict == 'dangling_symlink':
        obstacle.symlink_to(tmp_path / 'missing')
    else:
        if conflict.startswith('-'):
            obstacle = Path(str(destination) + conflict)
        obstacle.write_bytes(b'USER_CONTENT')
    with pytest.raises(ExperienceError, match='BACKUP_CONFLICT'):
        operation(name)(db, owner, destination=destination, **({'current_store': db} if name == 'restore' else {}))
    if conflict == 'directory':
        assert obstacle.is_dir()
    elif conflict == 'dangling_symlink':
        assert obstacle.is_symlink()
    else:
        assert obstacle.read_bytes() == b'USER_CONTENT'
    assert not list(tmp_path.glob('.experience-copy-*'))


@pytest.mark.parametrize('problem', ['missing', 'garbage', 'old_schema', 'mismatched_version', 'missing_table'])
def test_recovery_invalid_source_creates_no_output(env, tmp_path, problem):
    db, owner, _, _ = env
    if problem == 'missing':
        db = tmp_path / 'missing.sqlite3'
    elif problem == 'garbage':
        db.write_bytes(b'not a SQLite database')
    else:
        with sqlite3.connect(db) as connection:
            if problem == 'old_schema':
                connection.execute('PRAGMA user_version=1')
                connection.execute('UPDATE store_meta SET version=1')
            elif problem == 'mismatched_version':
                connection.execute('UPDATE store_meta SET version=1')
            else:
                connection.execute('DROP TABLE experience_feedback')
    destination = tmp_path / 'no-directory' / 'result.sqlite3'
    with pytest.raises(ExperienceError):
        operation('restore')(db, owner, destination=destination, current_store=db)
    assert not destination.parent.exists()


def test_atomic_publish_preserves_competing_file(env, tmp_path, monkeypatch):
    from scripts.automation_core.experience import recovery
    db, owner, _, _ = env
    destination = tmp_path / 'copy.sqlite3'
    real_link = os.link

    def race(source, target, *args, **kwargs):
        Path(target).write_text('CONCURRENT_USER_FILE')
        return real_link(source, target, *args, **kwargs)

    monkeypatch.setattr(recovery.os, 'link', race)
    with pytest.raises(ExperienceError, match='BACKUP_CONFLICT'):
        recovery.backup_store(db, owner, destination=destination)
    assert destination.read_text() == 'CONCURRENT_USER_FILE'
    assert not list(tmp_path.glob('.experience-copy-*'))
