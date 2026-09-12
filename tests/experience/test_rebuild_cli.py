import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from scripts.automation_core.experience import record_episode


def test_cli_rebuild_is_explicit_and_recovers_evidence(env, tmp_path):
    db, owner, _, payload = env
    event = record_episode(db, owner, payload, idempotency_key='creative')
    with sqlite3.connect(db) as c:
        c.execute('DROP TABLE experience_keys')
    command = [sys.executable, '-m', 'scripts.automation_core.experience.cli',
               '--db', str(db), '--collection', 'fixture']
    environment = {**os.environ, 'HOME': str(tmp_path), 'OPENCODEX_URL': 'http://127.0.0.1:1/v1'}
    before = db.read_bytes()
    rejected = subprocess.run([*command, 'rebuild-index'], env=environment, capture_output=True, text=True, timeout=20)
    assert rejected.returncode == 2 and json.loads(rejected.stdout)['error']['code'] == 'ACCESS_DENIED'
    assert db.read_bytes() == before
    accepted = subprocess.run([*command, '--owner', 'rebuild-index'], env=environment, capture_output=True, text=True, timeout=20)
    assert accepted.returncode == 0, accepted.stderr + accepted.stdout
    assert json.loads(accepted.stdout)['episodes_indexed'] == 1
    recalled = subprocess.run([*command, 'recall', '--task', '宠物文案'], env=environment, capture_output=True, text=True, timeout=20)
    assert recalled.returncode == 0
    assert json.loads(recalled.stdout)['items'][0]['episode_id'] == event['event_id']
