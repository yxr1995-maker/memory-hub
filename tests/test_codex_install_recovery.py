"""Stage/unstage must preserve user-owned state, including failure paths."""
import json
from pathlib import Path
import pytest
from scripts.automation_core import codex_install as install


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'hub/plugins/memory-hub'
    (source / '.codex-plugin').mkdir(parents=True)
    (source / '.codex-plugin/plugin.json').write_text('{"name":"memory-hub"}')
    data = tmp_path / 'data'
    data.mkdir()
    (data / 'codex-runtime.json').write_text('{"workspaces":{"/other":{}}}\n')
    (data / 'codex-runtime.json').chmod(0o640)
    target = tmp_path / 'target'
    return {'source': str(source), 'target': str(target)}, data


def snapshot(path):
    if path.is_symlink():
        return ('link', str(path.readlink()))
    if path.is_dir():
        return ('dir', path.stat().st_ino, {str(p.relative_to(path)): snapshot(p) for p in path.iterdir()})
    if path.is_file():
        return ('file', path.read_bytes(), path.stat().st_mode & 0o777)
    return ('missing',)


def state(plan, data):
    return [snapshot(Path(plan['target'])), snapshot(data / 'codex-runtime.json'),
            snapshot(data / 'codex-memory-settings.json')]


@pytest.mark.parametrize('kind', ['missing', 'link', 'directory'])
def test_roundtrip_retains_original_object_and_settings(workspace, kind):
    plan, data = workspace
    target = Path(plan['target'])
    if kind == 'link':
        target.symlink_to('relative-missing-link')
    if kind == 'directory':
        target.mkdir()
        (target / 'empty').mkdir()
        (target / 'original').write_bytes(b'original')
        (target / 'dangling').symlink_to('missing')
    before = state(plan, data)
    backup = install.stage_install(plan, data)
    assert target.is_symlink()
    assert (backup.stat().st_mode & 0o777) == 0o700
    receipt = backup / 'receipt.json'
    assert receipt.is_file() and (receipt.stat().st_mode & 0o777) == 0o600
    assert install.unstage_install(backup)['status'] == 'reverted'
    assert state(plan, data) == before
    assert install.unstage_install(backup)['status'] == 'already_reverted'


@pytest.mark.parametrize('changed', ['runtime', 'settings', 'link', 'saved_directory', 'backup'])
def test_conflicts_never_partially_restore(workspace, changed):
    plan, data = workspace
    target = Path(plan['target'])
    target.mkdir()
    (target / 'old').write_text('old')
    backup = install.stage_install(plan, data)
    if changed in ('runtime', 'settings'):
        name = 'codex-runtime.json' if changed == 'runtime' else 'codex-memory-settings.json'
        (data / name).write_text('{"user":"later edit"}')
    elif changed == 'link':
        target.unlink()
        target.symlink_to('other')
    elif changed == 'saved_directory':
        (backup / 'plugin-source/old').write_text('later edit')
    else:
        (backup / 'runtime.json').write_text('corrupted backup')
    before = state(plan, data)
    with pytest.raises(ValueError, match='INSTALL_CONFLICT'):
        install.unstage_install(backup)
    assert state(plan, data) == before


@pytest.mark.parametrize('failure', ['runtime_write', 'final_receipt', 'link_creation'])
def test_stage_failure_restores_exact_pre_stage_state(workspace, monkeypatch, failure):
    plan, data = workspace
    target = Path(plan['target'])
    target.mkdir()
    (target / 'old').write_text('old')
    before = state(plan, data)
    atomic = install._atomic
    symlink = Path.symlink_to

    def fail_atomic(path, value):
        if (failure == 'runtime_write' and path.name == 'codex-memory-settings.json'
                or failure == 'final_receipt' and path.name == 'receipt.json' and value.get('status') == 'staged'):
            raise OSError('injected failure')
        return atomic(path, value)

    def fail_link(path, *args, **kwargs):
        if failure == 'link_creation' and path == target:
            raise OSError('injected failure')
        return symlink(path, *args, **kwargs)

    monkeypatch.setattr(install, '_atomic', fail_atomic)
    monkeypatch.setattr(Path, 'symlink_to', fail_link)
    with pytest.raises(OSError, match='injected failure'):
        install.stage_install(plan, data)
    assert state(plan, data) == before


def test_failed_stage_does_not_erase_an_external_edit(workspace, monkeypatch):
    plan, data = workspace
    write = install.write_runtime

    def fail(*args, **kwargs):
        write(*args, **kwargs)
        (data / 'codex-runtime.json').write_text('{"external":"keep"}')
        raise OSError('late failure')

    monkeypatch.setattr(install, 'write_runtime', fail)
    with pytest.raises(ValueError, match='INSTALL_CONFLICT'):
        install.stage_install(plan, data)
    assert (data / 'codex-runtime.json').read_text() == '{"external":"keep"}'


@pytest.mark.parametrize('bad', ['file_target', 'overlap', 'symlink_parent', 'invalid_json', 'data_inside_target', 'relative_source'])
def test_stage_rejects_invalid_state_before_mutating(workspace, bad):
    plan, data = workspace
    if bad == 'file_target':
        Path(plan['target']).write_text('keep')
    elif bad == 'overlap':
        plan['target'] = str(Path(plan['source']).parent)
    elif bad == 'symlink_parent':
        alias = data.parent / 'alias'
        alias.symlink_to(data.parent / 'hub', target_is_directory=True)
        plan['target'] = str(alias / 'plugins/other')
    elif bad == 'invalid_json':
        (data / 'codex-runtime.json').write_text('broken JSON')
    elif bad == 'data_inside_target':
        plan['target'] = str(data.parent)
    else:
        plan['source'] = 'relative/path'
    before = state(plan, data)
    with pytest.raises(ValueError):
        install.stage_install(plan, data)
    assert state(plan, data) == before
    assert not (data / 'backups').exists()


def test_unstage_ignores_unrelated_global_configuration(workspace):
    plan, data = workspace
    home = data.parent / 'home'
    config, marketplace = home / '.codex/config.toml', home / '.agents/plugins/marketplace.json'
    for path in (config, marketplace):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('before')
    backup = install.stage_install(plan, data, home=home)
    for path in (config, marketplace):
        path.write_text('later user edit')
    install.unstage_install(backup)
    assert all(path.read_text() == 'later user edit' for path in (config, marketplace))
    assert not (backup / 'config.toml').exists() and not (backup / 'marketplace.json').exists()


@pytest.mark.parametrize('value', [{}, {'target': '/tmp/old', 'source': '/tmp/old-source'}, {'version': 1}])
def test_unstage_rejects_incomplete_receipts_without_writes(tmp_path, value):
    (tmp_path / 'receipt.json').write_text(json.dumps(value))
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match='INVALID_INSTALL_RECEIPT'):
        install.unstage_install(tmp_path)
    assert snapshot(tmp_path) == before
