"""Staged plugin rollback through the existing CLI, with an isolated HOME."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


ROOT = Path(__file__).resolve().parents[1]


def invoke(workspace, *arguments):
    environment = {**os.environ, 'HOME': str(workspace / 'home'),
                   'MEMORY_HUB_DATA': str(workspace / 'data'),
                   'WIKI_PATH': str(workspace / 'wiki'),
                   'OPENCODEX_URL': 'http://127.0.0.1:1/v1',
                   'PYTHONDONTWRITEBYTECODE': '1'}
    return subprocess.run([sys.executable, str(ROOT / 'scripts/codex_integration.py'),
                           'codex', *arguments], cwd=ROOT, env=environment,
                          capture_output=True, text=True, timeout=20)


def test_cli_staging_and_rollback_preserve_unrelated_configuration(tmp_path):
    source = tmp_path / 'hub/plugins/memory-hub'
    (source / '.codex-plugin').mkdir(parents=True)
    (source / '.codex-plugin/plugin.json').write_text('{"name":"memory-hub"}')
    home = tmp_path / 'home'
    target = home / 'plugin-source'
    target.mkdir(parents=True)
    (target / 'original.txt').write_text('original plugin')
    (home / '.codex').mkdir()
    config = home / '.codex/config.toml'
    config.write_text('custom = "original"\n')
    data = tmp_path / 'data'
    data.mkdir()
    runtime = data / 'codex-runtime.json'
    original = '{"workspaces":{"/project":{"data_path":"/project-data"}}}\n'
    runtime.write_text(original)
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'source': str(source), 'target': str(target)}))
    staged = invoke(tmp_path, 'stage-install', '--plan', str(plan), '--home', str(home))
    assert staged.returncode == 0, staged.stderr + staged.stdout
    result = json.loads(staged.stdout)
    assert target.is_symlink() and result['registered'] is False
    config.write_text('custom = "later user edit"\n')
    restored = invoke(tmp_path, 'unstage-install', '--backup', result['backup'])
    assert restored.returncode == 0, restored.stderr + restored.stdout
    assert (target / 'original.txt').read_text() == 'original plugin'
    assert not target.is_symlink()
    assert runtime.read_text() == original
    assert config.read_text() == 'custom = "later user edit"\n'
    assert not (data / 'codex-memory-settings.json').exists()


def test_cli_rejects_unrecognized_backup_without_changing_files(tmp_path):
    backup = tmp_path / 'old-backup'
    backup.mkdir()
    legacy = backup / 'plan.json'
    legacy.write_text('{"target":"/untrusted","source":"/untrusted-source"}')
    before = legacy.read_bytes()
    rejected = invoke(tmp_path, 'unstage-install', '--backup', str(backup))
    assert rejected.returncode == 2
    assert 'INVALID_INSTALL_RECEIPT' in json.loads(rejected.stdout)['error']
    assert legacy.read_bytes() == before


def test_install_action_cannot_invoke_memory_worker(monkeypatch):
    from scripts.automation_core.codex_integration import main
    from scripts.automation_core import memory_worker

    def forbidden(*args, **kwargs):
        pytest.fail('An install action must not execute a background worker')

    monkeypatch.setattr(memory_worker, 'run_once', forbidden)
    with pytest.raises(SystemExit) as error:
        main(['memory-worker', 'stage-install'])
    assert error.value.code == 2
