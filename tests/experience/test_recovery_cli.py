"""Explicit recovery commands exercised through the existing CLI process."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.automation_core.experience import record_episode, revise_understanding


ROOT = Path(__file__).resolve().parents[2]


def invoke(db, *arguments, owner=True):
    command = [sys.executable, '-m', 'scripts.automation_core.experience.cli',
               '--db', str(db), '--collection', 'fixture']
    if owner:
        command.append('--owner')
    return subprocess.run(command + list(arguments), cwd=ROOT, text=True,
                          capture_output=True, timeout=20,
                          env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})


@pytest.mark.parametrize('goal,reason', [
    ('保护生成文档', '写入时再次比较版本，保护并发人工修改'),
    ('修改宠物内容文案', '本次改为直白表达，不沿用旧稿的温柔口吻'),
])
def test_cli_backup_restore_keeps_latest_correction(env, tmp_path, goal, reason):
    db, owner, _, payload = env
    receipt = record_episode(db, owner, {**payload, 'goal': goal}, idempotency_key='recovery')
    revise_understanding(db, owner, target_id=receipt['event_id'], base_revision=1,
                         patch={'explicit_reason': reason}, reason='fixture correction',
                         evidence_ids=[receipt['event_id']])
    original = db.read_bytes()
    backup = tmp_path / 'snapshot.sqlite3'
    restored = tmp_path / 'restored.sqlite3'
    made = invoke(db, 'backup', '--destination', str(backup))
    assert made.returncode == 0, made.stderr + made.stdout
    assert json.loads(made.stdout)['path'] == str(backup)
    recovered = invoke(backup, 'restore', '--destination', str(restored), '--current-store', str(db))
    assert recovered.returncode == 0, recovered.stderr + recovered.stdout
    assert json.loads(recovered.stdout)['path'] == str(restored)
    found = invoke(restored, 'recall', '--task', goal, owner=False)
    assert found.returncode == 0, found.stderr + found.stdout
    item = json.loads(found.stdout)['items'][0]
    assert (item['episode_id'], item['revision'], item['explicit_reason']) == (
        receipt['event_id'], 2, reason)
    assert item['source_kind'] == 'synthetic_fixture'
    assert db.read_bytes() == original


@pytest.mark.parametrize('command', ['backup', 'restore'])
def test_cli_recovery_requires_management_entry(env, tmp_path, command):
    db, _, _, _ = env
    destination = tmp_path / 'not-created' / 'result.sqlite3'
    denied = invoke(db, command, '--destination', str(destination), owner=False)
    assert denied.returncode == 2
    assert json.loads(denied.stdout)['error']['code'] == 'ACCESS_DENIED'
    assert not destination.parent.exists()
