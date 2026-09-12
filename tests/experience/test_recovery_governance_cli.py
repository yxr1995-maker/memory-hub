"""Restored CLI copies must not revive withdrawals made after the backup."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.automation_core.experience import record_episode, revoke
from scripts.automation_core.experience.recovery import backup_store
from scripts.automation_core.experience.revisions import purge


ROOT = Path(__file__).resolve().parents[2]


def invoke(db, *args):
    return subprocess.run(
        [sys.executable, '-m', 'scripts.automation_core.experience.cli',
         '--db', str(db), '--collection', 'fixture', '--owner', *args],
        cwd=ROOT, capture_output=True, text=True, timeout=20,
        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'},
    )


def test_cli_restore_without_current_governance_creates_nothing(env, tmp_path):
    db, owner, _, payload = env
    record_episode(db, owner, payload, idempotency_key='original')
    backup = tmp_path / 'old.sqlite3'
    backup_store(db, owner, destination=backup)
    destination = tmp_path / 'absent' / 'restored.sqlite3'
    result = invoke(backup, 'restore', '--destination', str(destination))
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stdout)['error']['code'] == 'CURRENT_STATE_REQUIRED'
    assert not destination.parent.exists()


@pytest.mark.parametrize('action,goal,expected', [
    ('revoke', '工程文档保护', 'REVOKED'),
    ('purge', '创作宠物改稿', 'ACCESS_DENIED'),
])
def test_cli_restore_applies_later_withdrawal(env, tmp_path, action, goal, expected):
    db, owner, _, payload = env
    receipt = record_episode(db, owner, {**payload, 'goal': goal}, idempotency_key='original')
    backup = tmp_path / 'old.sqlite3'
    backup_store(db, owner, destination=backup)
    (revoke if action == 'revoke' else purge)(
        db, owner, target_id=receipt['event_id'], base_revision=1, reason='withdraw fixture',
    )
    before = db.read_bytes(), backup.read_bytes()
    live_read = invoke(db, 'read', '--id', receipt['event_id'], '--revision', '1')
    assert live_read.returncode == 2
    assert json.loads(live_read.stdout)['error']['code'] == expected
    destination = tmp_path / 'restored.sqlite3'
    result = invoke(backup, 'restore', '--destination', str(destination), '--current-store', str(db))
    assert result.returncode == 0, result.stdout + result.stderr
    read = invoke(destination, 'read', '--id', receipt['event_id'], '--revision', '1')
    assert read.returncode == 2, read.stdout + read.stderr
    assert json.loads(read.stdout)['error']['code'] == expected
    recall = invoke(destination, 'recall', '--task', goal)
    assert recall.returncode == 0, recall.stdout + recall.stderr
    assert json.loads(recall.stdout)['items'] == []
    assert (db.read_bytes(), backup.read_bytes()) == before
