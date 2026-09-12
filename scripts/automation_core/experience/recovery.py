"""Explicit, collection-authorized snapshots; recovery never replaces a live store."""
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import json
from contextlib import ExitStack

from .contracts import ExperienceError, require, validate_payload
from .store import connect, dumps
from .revisions import invalidate_transitive


_COLUMNS = {
    'store_meta': {'version'},
    'experience_events': {'event_id', 'subject_id', 'collection_id', 'idempotency_key',
                          'content_hash', 'original_json', 'revision', 'status',
                          'receipt_json', 'created_at'},
    'experience_versions': {'event_id', 'revision', 'payload_json', 'reason',
                            'evidence_json', 'source_kind', 'review_required'},
    'experience_keys': {'event_id', 'key_group', 'token'},
    'experience_feedback': {'subject_id', 'collection_id', 'idempotency_key',
                           'payload_json', 'receipt_json', 'target_id'},
    'experience_exports': {'path', 'event_id', 'checksum'},
}
_SIDECARS = ('-wal', '-shm', '-journal')


def _validate_snapshot(connection, ctx):
    require(connection.execute('PRAGMA user_version').fetchone()[0] == 2,
            'recovery requires schema v2; migrate explicitly before backing up', 'SCHEMA_MISMATCH')
    require([row[0] for row in connection.execute('SELECT version FROM store_meta')] == [2],
            'inconsistent schema version', 'SCHEMA_MISMATCH')
    objects = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    require({row[1] for row in objects if row[0] == 'table'} == set(_COLUMNS)
            and not any(row[0] in ('view', 'trigger') for row in objects),
            'unsupported snapshot schema', 'SCHEMA_MISMATCH')
    for table, columns in _COLUMNS.items():
        actual = {row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
        require(actual == columns, 'unsupported snapshot columns', 'SCHEMA_MISMATCH')
    require([row[0] for row in connection.execute('PRAGMA integrity_check')] == ['ok']
            and not connection.execute('PRAGMA foreign_key_check').fetchall(),
            'snapshot integrity check failed', 'STORE_UNAVAILABLE')
    # Deleted rows are empty tombstones whose collection_id is the event ID.
    # They cannot be treated as a new collection, or used to hide unscoped data.
    invalid_deleted = connection.execute("""SELECT 1 FROM experience_events e
        WHERE status='deleted' AND (original_json!='{}' OR receipt_json!='{}'
          OR subject_id!='' OR content_hash!='' OR idempotency_key!='' OR collection_id!=event_id
          OR EXISTS(SELECT 1 FROM experience_versions v WHERE v.event_id=e.event_id)
          OR EXISTS(SELECT 1 FROM experience_keys k WHERE k.event_id=e.event_id)) LIMIT 1""").fetchone()
    require(invalid_deleted is None, 'invalid deleted tombstone', 'SCHEMA_MISMATCH')
    collections = connection.execute("""SELECT collection_id FROM experience_events WHERE status!='deleted'
        UNION SELECT collection_id FROM experience_feedback""").fetchall()
    require(all(ctx.may_access(row[0]) for row in collections),
            'snapshot contains collections outside authorized scope', 'ACCESS_DENIED')
    for table, key in (('experience_feedback', 'target_id'), ('experience_exports', 'event_id')):
        require(connection.execute(f'''SELECT 1 FROM {table} t LEFT JOIN experience_events e
            ON t.{key}=e.event_id WHERE e.event_id IS NULL OR e.status='deleted' LIMIT 1''').fetchone() is None,
                'orphaned snapshot metadata', 'SCHEMA_MISMATCH')


def _unused(destination):
    require(not any(os.path.lexists(str(destination) + suffix) for suffix in ('',) + _SIDECARS),
            'destination or SQLite sidecar already exists', 'BACKUP_CONFLICT')


def _governance_plan(snapshot, current):
    query = '''SELECT e.*,v.payload_json,v.review_required,v.reason,v.source_kind,v.evidence_json
        FROM experience_events e LEFT JOIN experience_versions v
        ON e.event_id=v.event_id AND e.revision=v.revision ORDER BY e.event_id'''
    authority = {row['event_id']: row for row in current.execute(query)}
    plan = []
    for old in snapshot.execute(query):
        live = authority.get(old['event_id'])
        require(live is not None, 'current state does not cover every snapshot event', 'CURRENT_STATE_INCOMPLETE')
        for row in (old, live):
            require(row['status'] in ('active', 'revoked', 'deleted') and type(row['revision']) is int
                    and row['revision'] > 0, 'invalid event state', 'SCHEMA_MISMATCH')
            if row['status'] != 'deleted':
                require(row['payload_json'] is not None and row['review_required'] in (0, 1),
                        'missing current revision', 'SCHEMA_MISMATCH')
                try:
                    payload = json.loads(row['payload_json'])
                    if row['status'] == 'active':
                        validate_payload(payload)
                        require(payload['collection_id'] == row['collection_id'])
                    else:
                        require(payload == {})
                    require(type(json.loads(row['evidence_json'])) is list)
                except (ExperienceError, ValueError, TypeError):
                    raise ExperienceError('SCHEMA_MISMATCH', 'invalid current version') from None
        require(live['revision'] >= old['revision'], 'current governance is older than snapshot', 'CURRENT_STATE_CONFLICT')
        require(not (old['status'] == 'deleted' and live['status'] != 'deleted')
                and not (old['status'] == 'revoked' and live['status'] == 'active'),
                'current state would reactivate withdrawn data', 'CURRENT_STATE_CONFLICT')
        if live['status'] != 'deleted':
            require(all(old[key] == live[key] for key in
                        ('subject_id', 'collection_id', 'idempotency_key', 'content_hash', 'original_json')),
                    'event identity differs', 'CURRENT_STATE_CONFLICT')
        if live['revision'] == old['revision']:
            require(live['status'] == old['status'] and all(live[key] == old[key] for key in
                    ('payload_json', 'reason', 'source_kind', 'evidence_json')),
                    'same revision has conflicting state', 'CURRENT_STATE_CONFLICT')
        plan.append({'event_id': old['event_id'], 'snapshot_revision': old['revision'],
                     'revision': live['revision'], 'status': live['status'],
                     'requires_review': bool(old['review_required'] or live['review_required']
                                             or live['revision'] > old['revision']),
                     'reason': live['reason'], 'source_kind': live['source_kind'],
                     'version_sha256': hashlib.sha256((live['payload_json'] or '').encode()).hexdigest()})
    return plan


def _apply_governance(connection, plan):
    counts = {'deleted': 0, 'revoked': 0, 'review_required': 0}
    for item in plan:
        event_id, status = item['event_id'], item['status']
        if status in ('deleted', 'revoked') or item['requires_review']:
            invalidate_transitive(connection, event_id)
            connection.execute('DELETE FROM experience_keys WHERE event_id=?', (event_id,))
        if status == 'deleted':
            connection.execute('DELETE FROM experience_feedback WHERE target_id=?', (event_id,))
            connection.execute('DELETE FROM experience_versions WHERE event_id=?', (event_id,))
            connection.execute("""UPDATE experience_events SET status='deleted',original_json='{}',
                receipt_json='{}',content_hash='',idempotency_key='',subject_id='',collection_id=?,revision=?
                WHERE event_id=?""", (event_id, item['revision'], event_id))
            counts['deleted'] += 1
        elif status == 'revoked':
            if item['revision'] > item['snapshot_revision']:
                connection.execute('''INSERT INTO experience_versions
                    (event_id,revision,payload_json,reason,source_kind) VALUES(?,?,?,?,?)''',
                    (event_id, item['revision'], '{}', item['reason'], item['source_kind']))
            connection.execute("UPDATE experience_events SET status='revoked',revision=? WHERE event_id=?",
                               (item['revision'], event_id))
            counts['revoked'] += 1
        elif item['requires_review']:
            connection.execute('UPDATE experience_versions SET review_required=1 WHERE event_id=?', (event_id,))
    connection.execute('''DELETE FROM experience_keys WHERE event_id IN (
        SELECT e.event_id FROM experience_events e JOIN experience_versions v
        ON e.event_id=v.event_id AND e.revision=v.revision WHERE v.review_required=1)''')
    counts['review_required'] = connection.execute('''SELECT count(*) FROM experience_events e
        JOIN experience_versions v ON e.event_id=v.event_id AND e.revision=v.revision
        WHERE e.status='active' AND v.review_required=1''').fetchone()[0]
    return counts


def _copy_store(source, ctx, destination, *, restoring, current_store=None):
    require(ctx.role == 'owner', 'management entry required', 'ACCESS_DENIED')
    if restoring:
        require(current_store is not None, 'restore needs the current governance store', 'CURRENT_STATE_REQUIRED')
    destination = Path(destination).absolute()
    _unused(destination)
    temporary = None
    try:
        # Authorization and backup share this read transaction's snapshot.
        with ExitStack() as contexts:
            connection = contexts.enter_context(connect(source))
            _validate_snapshot(connection, ctx)
            plan = None
            if restoring:
                authority = contexts.enter_context(connect(current_store))
                require(not os.path.samefile(source, current_store),
                        'backup cannot attest its own current governance', 'CURRENT_STATE_CONFLICT')
                _validate_snapshot(authority, ctx)
                plan = _governance_plan(connection, authority)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix='.experience-copy-', dir=destination.parent)
            os.close(fd)
            temporary = Path(name)
            target = sqlite3.connect(temporary)
            target.row_factory = sqlite3.Row
            try:
                connection.backup(target)
                _validate_snapshot(target, ctx)
                detached = 0
                if restoring:
                    detached = target.execute('SELECT count(*) FROM experience_exports').fetchone()[0]
                    target.execute('PRAGMA secure_delete=ON')
                    target.execute('DELETE FROM experience_exports')
                    governance_counts = _apply_governance(target, plan)
                    _validate_snapshot(target, ctx)
                    target.commit()
                # Publish a standalone database, with no uncheckpointed sidecars.
                target.execute('PRAGMA journal_mode=DELETE')
                counts = {table: target.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                          for table in _COLUMNS if table != 'store_meta'}
            finally:
                target.close()
        temporary.chmod(0o600)
        with temporary.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            os.fsync(stream.fileno())
        _unused(destination)
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise ExperienceError('BACKUP_CONFLICT', 'destination created concurrently') from None
        result = {
            'path': str(destination), 'schema_version': 2, 'sha256': checksum,
            'counts': counts, 'detached_exports_count': detached,
            'operation': 'restore' if restoring else 'backup',
            'limitations': ['external media are references, not embedded files',
                            'host configuration and source store unchanged',
                            'independent backups and later governance changes are not synchronized'],
        }
        if restoring:
            public_plan = [{key: value for key, value in item.items() if key not in ('reason', 'source_kind')}
                           for item in plan]
            result['governance'] = {'source': str(Path(current_store).absolute()),
                                    'scope': 'explicit current-store read snapshot; no global recency guarantee',
                                    'events_checked': len(plan), 'applied': governance_counts,
                                    'state_sha256': hashlib.sha256(dumps(public_plan).encode()).hexdigest()}
        return result
    except sqlite3.Error as error:
        raise ExperienceError('STORE_UNAVAILABLE', str(error)) from None
    finally:
        if temporary is not None:
            for suffix in ('',) + _SIDECARS:
                Path(str(temporary) + suffix).unlink(missing_ok=True)


def backup_store(db_path, ctx, *, destination):
    """Create a private, consistent v2 snapshot without changing its source."""
    return _copy_store(db_path, ctx, destination, restoring=False)


def restore_store(backup_path, ctx, *, destination, current_store=None):
    """Recover only after applying current governance, without export ownership."""
    return _copy_store(backup_path, ctx, destination, restoring=True, current_store=current_store)
