"""Explicit derived-index recovery must preserve authoritative experience state."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sqlite3
import pytest
from scripts.automation_core.experience import (
    ExperienceError, initialize, record_episode, recall_for_decision, read_evidence,
    revise_understanding, revoke,
)
from scripts.automation_core.experience import store
from scripts.automation_core.experience.revisions import export_episode, purge


def authority(db):
    with sqlite3.connect(db) as connection:
        return {table: connection.execute(f'SELECT * FROM {table} ORDER BY 1,2').fetchall()
                for table in ('experience_events', 'experience_versions', 'experience_feedback', 'experience_exports')}


def complete_state(env, tmp_path):
    db, owner, agent, payload = env
    active = record_episode(db, owner, payload, idempotency_key='creative')
    revised = revise_understanding(db, owner, target_id=active['event_id'], base_revision=1,
                                  patch={'explicit_reason': '当前反馈：明快、直接邀请同行'},
                                  reason='current correction', evidence_ids=[active['event_id']])
    records = []
    for kind in ('engineering', 'revoked', 'deleted', 'review_required'):
        p = deepcopy(payload)
        p['goal'] = '保护用户修改的生成文档' if kind == 'engineering' else '修改宠物内容文案'
        records.append(record_episode(db, owner, p, idempotency_key=kind))
    revoke(db, owner, target_id=records[1]['event_id'], base_revision=1, reason='withdrawn')
    purge(db, owner, target_id=records[2]['event_id'], base_revision=1, reason='privacy')
    with sqlite3.connect(db) as c:
        c.execute('UPDATE experience_versions SET review_required=1 WHERE event_id=?', (records[3]['event_id'],))
    record_episode(db, owner, {'schema_version': 1, 'collection_id': 'fixture', 'event_kind': 'feedback',
                              'target_id': active['event_id'], 'revision': 2, 'action': 'adopted',
                              'source_kind': 'agent_report', 'receipt_id': 'fixture-receipt'}, idempotency_key='feedback')
    export = tmp_path / 'creative.md'
    export_episode(db, owner, target_id=active['event_id'], destination=export)
    return revised, records, export


@pytest.mark.parametrize('damage', ['missing_table', 'empty_keys', 'stale_keys'])
def test_rebuild_recovers_current_engineering_and_creative_without_changing_history(env, tmp_path, damage):
    db, owner, agent, _ = env
    creative, records, export = complete_state(env, tmp_path)
    expected = authority(db)
    export_bytes = export.read_bytes()
    with sqlite3.connect(db) as c:
        c.execute('DROP TABLE experience_keys' if damage == 'missing_table' else 'DELETE FROM experience_keys')
        if damage == 'stale_keys':
            c.execute('INSERT INTO experience_keys VALUES(?,?,?)', (records[1]['event_id'], 'goal', '文案'))
    # Reads do not secretly recreate the missing index, and init is not a repair command.
    if damage == 'missing_table':
        initialize(db)
        with pytest.raises(ExperienceError, match='INDEX_UNAVAILABLE'):
            recall_for_decision(db, agent, task='宠物文案')
    result = store.rebuild_keys(db, owner)
    assert result['episodes_indexed'] == 2 and result['keys_indexed'] > 0
    assert authority(db) == expected and export.read_bytes() == export_bytes
    result = recall_for_decision(db, agent, task='当前反馈 明快 直接邀请同行')
    card = next(item for item in result['items'] if item['episode_id'] == creative['event_id'])
    assert card['revision'] == 2 and card['source_kind'] == 'synthetic_fixture'
    assert any(item['episode_id'] == records[0]['event_id'] for item in recall_for_decision(db, agent, task='保护生成文档')['items'])
    with sqlite3.connect(db) as c:
        ids = {row[0] for row in c.execute('SELECT DISTINCT event_id FROM experience_keys')}
        assert ids == {creative['event_id'], records[0]['event_id']}
        assert c.execute("SELECT tbl_name FROM sqlite_master WHERE name='lookup_keys'").fetchone() == ('experience_keys',)
    assert read_evidence(db, agent, evidence_id=creative['event_id'], revision=1)['revision'] == 1
    assert store.rebuild_keys(db, owner)['episodes_indexed'] == 2
    assert authority(db) == expected


@pytest.mark.parametrize('unauthorized', ['agent', 'partial_owner'])
def test_rebuild_rejects_insufficient_scope_before_persistent_changes(env, unauthorized):
    db, owner, agent, payload = env
    record_episode(db, owner, payload, idempotency_key='visible')
    other = replace(owner, allowed_collections=('other',))
    p = {**payload, 'collection_id': 'other'}
    record_episode(db, other, p, idempotency_key='other')
    before = db.read_bytes()
    with pytest.raises(ExperienceError, match='ACCESS_DENIED'):
        store.rebuild_keys(db, agent if unauthorized == 'agent' else owner)
    assert db.read_bytes() == before


@pytest.mark.parametrize('damage', ['bad_payload', 'missing_version', 'bad_schema'])
def test_authority_damage_rolls_back_even_newly_created_derived_table(env, damage):
    db, owner, _, payload = env
    event = record_episode(db, owner, payload, idempotency_key='one')
    with sqlite3.connect(db) as c:
        c.execute('DROP TABLE experience_keys')
        if damage == 'bad_payload':
            c.execute('UPDATE experience_versions SET payload_json=?', ('{"invalid":true}',))
        elif damage == 'missing_version':
            c.execute('DELETE FROM experience_versions WHERE event_id=?', (event['event_id'],))
        else:
            c.execute('UPDATE store_meta SET version=1')
    before = db.read_bytes()
    with pytest.raises(ExperienceError, match='SCHEMA_MISMATCH'):
        store.rebuild_keys(db, owner)
    assert db.read_bytes() == before


def test_rebuild_failure_is_one_transaction(env, monkeypatch):
    db, owner, _, payload = env
    for key in ('one', 'two'):
        record_episode(db, owner, payload, idempotency_key=key)
    with sqlite3.connect(db) as c:
        c.execute('DELETE FROM experience_keys')
    before = db.read_bytes()
    real = store.index_version

    def failing(connection, event_id, value):
        real(connection, event_id, value)
        raise RuntimeError('injected index failure')

    monkeypatch.setattr(store, 'index_version', failing)
    with pytest.raises(RuntimeError, match='injected index failure'):
        store.rebuild_keys(db, owner)
    assert db.read_bytes() == before


def test_rebuild_does_not_initialize_missing_database(env, tmp_path):
    _, owner, _, _ = env
    missing = tmp_path / 'missing.sqlite3'
    with pytest.raises(ExperienceError, match='NOT_INITIALIZED'):
        store.rebuild_keys(missing, owner)
    assert not missing.exists()


def test_rebuild_replaces_stale_lookup_index(env):
    db, owner, _, payload = env
    record_episode(db, owner, payload, idempotency_key='one')
    with sqlite3.connect(db) as c:
        c.execute('DROP INDEX lookup_keys')
        c.execute('CREATE INDEX lookup_keys ON experience_keys(event_id)')
    store.rebuild_keys(db, owner)
    with sqlite3.connect(db) as c:
        plan = c.execute("EXPLAIN QUERY PLAN SELECT event_id FROM experience_keys WHERE key_group=? AND token=?", ('goal', '宠物')).fetchall()
        assert any('SEARCH' in row[3] and 'lookup_keys' in row[3] for row in plan), plan


def test_rebuild_refuses_a_key_trigger_before_it_can_mutate_authority(env):
    db, owner, _, payload = env
    record_episode(db, owner, payload, idempotency_key='one')
    with sqlite3.connect(db) as c:
        c.execute("CREATE TRIGGER hostile_key_delete AFTER DELETE ON experience_keys BEGIN UPDATE experience_events SET original_json='{}'; END")
    before = db.read_bytes()
    with pytest.raises(ExperienceError, match='SCHEMA_MISMATCH'):
        store.rebuild_keys(db, owner)
    assert db.read_bytes() == before
