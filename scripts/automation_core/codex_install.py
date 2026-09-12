"""Reversible staging of an existing local Codex plugin source."""
from __future__ import annotations
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

HUB = Path(__file__).resolve().parents[2]
FILES = {'runtime.json': 'codex-runtime.json', 'settings.json': 'codex-memory-settings.json'}


def _read(path):
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('configuration must be a JSON object')
    return value


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode()


def _atomic_bytes(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.install-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), mode)
        os.replace(name, path)
    finally:
        if os.path.lexists(name):
            os.unlink(name)


def _atomic(path, value):
    _atomic_bytes(path, _bytes(value))


def _state(path):
    if path.is_symlink():
        return {'kind': 'link', 'link': str(path.readlink())}
    if not path.exists():
        return {'kind': 'missing'}
    mode = stat.S_IMODE(path.stat().st_mode)
    if path.is_file():
        return {'kind': 'file', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'mode': mode}
    if path.is_dir():
        return {'kind': 'directory', 'mode': mode,
                'entries': {child.name: _state(child) for child in sorted(path.iterdir())}}
    raise ValueError('unsupported filesystem object')


def _file_state(content):
    return {'kind': 'file', 'sha256': hashlib.sha256(content).hexdigest(), 'mode': 0o600}


def _path(value):
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or path.parent.resolve() != path.parent:
        raise ValueError('absolute paths without symlinked parents required')
    return path


def _overlap(a, b):
    return a == b or a in b.parents or b in a.parents


def installation_plan(catalog: dict, hub: Path) -> dict:
    entries = [e for e in catalog.get('installed', []) if e.get('pluginId') == 'memory-hub@personal']
    if len(entries) != 1 or entries[0].get('source', {}).get('source') != 'local':
        raise ValueError('exactly one local memory-hub source required')
    target = _path(entries[0]['source']['path'])
    return {'target': str(target), 'source': str(hub / 'plugins/memory-hub'), 'publish_enabled': False}


def _runtime_values(data, hub, python, wiki):
    runtime = _read(data / 'codex-runtime.json')
    runtime.update(hub_root=str(hub.resolve()), python_path=python or sys.executable)
    runtime.setdefault('data_path', str(data))
    runtime.setdefault('wiki_path', os.environ.get('WIKI_PATH', str(Path.home() / 'llm-wiki')))
    if wiki is not None:
        runtime['wiki_path'] = str(wiki)
    cfg = {'recall': True, 'capture': True, 'publish': False}
    cfg.update({k: v for k, v in _read(data / 'codex-memory-settings.json').items()
                if k in cfg and isinstance(v, bool)})
    cfg['publish'] = False
    return runtime, cfg


def write_runtime(data: Path, hub: Path = HUB, python: str | None = None, wiki: Path | None = None):
    runtime, settings = _runtime_values(data, hub, python, wiki)
    _atomic(data / 'codex-runtime.json', runtime)
    _atomic(data / 'codex-memory-settings.json', settings)
    return runtime


def _valid_state(value, allowed):
    if not isinstance(value, dict) or value.get('kind') not in allowed:
        return False
    kind = value['kind']
    if kind == 'missing':
        return set(value) == {'kind'}
    if kind == 'link':
        return set(value) == {'kind', 'link'} and isinstance(value['link'], str) and bool(value['link'])
    if type(value.get('mode')) is not int or not 0 <= value['mode'] <= 0o7777:
        return False
    if kind == 'file':
        digest = value.get('sha256')
        return (set(value) == {'kind', 'mode', 'sha256'} and isinstance(digest, str)
                and len(digest) == 64 and all(c in '0123456789abcdef' for c in digest))
    entries = value.get('entries')
    return (set(value) == {'kind', 'mode', 'entries'} and isinstance(entries, dict)
            and all(isinstance(name, str) and name not in ('', '.', '..') and '/' not in name
                    and _valid_state(item, {'missing', 'file', 'link', 'directory'})
                    for name, item in entries.items()))


def _load_receipt(backup):
    try:
        backup = _path(backup)
        if backup.is_symlink() or backup.parent.name != 'backups':
            raise ValueError('invalid backup location')
        receipt = _read(backup / 'receipt.json')
        if (set(receipt) != {'version', 'status', 'target', 'source', 'data', 'before', 'after'}
                or type(receipt['version']) is not int or receipt['version'] != 1
                or receipt['status'] not in ('prepared', 'staged', 'reverting', 'reverted')):
            raise ValueError('incomplete receipt')
        target, source, data = (_path(receipt[key]) for key in ('target', 'source', 'data'))
        if backup.parent.parent != data or _overlap(target, source) or _overlap(target, data):
            raise ValueError('overlapping receipt paths')
        for phase in ('before', 'after'):
            values = receipt[phase]
            if not isinstance(values, dict) or set(values) != {'target', *FILES}:
                raise ValueError('incomplete states')
            if not _valid_state(values['target'], {'missing', 'link', 'directory'}):
                raise ValueError('invalid target state')
            if any(not _valid_state(values[name], {'missing', 'file'}) for name in FILES):
                raise ValueError('invalid file state')
        if receipt['after']['target'] != {'kind': 'link', 'link': str(source)}:
            raise ValueError('invalid installed link')
        if any(receipt['after'][name]['kind'] != 'file' for name in FILES):
            raise ValueError('invalid installed file')
        return receipt
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise ValueError('INVALID_INSTALL_RECEIPT: ' + str(error)) from error


def _restore(backup, receipt, *, partial):
    target, data = Path(receipt['target']), Path(receipt['data'])
    before, after = receipt['before'], receipt['after']
    paths = {'target': target, **{name: data / filename for name, filename in FILES.items()}}
    current = {name: _state(path) for name, path in paths.items()}
    saved = backup / 'plugin-source'
    # Check every current object and backup before restoring any one of them.
    for name, value in current.items():
        acceptable = value == after[name] or partial and value == before[name]
        if name == 'target' and partial and value == {'kind': 'missing'}:
            acceptable = True  # Unlink/rename may have succeeded before link creation failed.
        if not acceptable:
            raise ValueError('INSTALL_CONFLICT: ' + name + ' changed since staging')
    contents = {}
    for name in FILES:
        if before[name]['kind'] == 'file':
            saved_file = backup / name
            if saved_file.is_symlink() or not saved_file.is_file():
                raise ValueError('INSTALL_CONFLICT: missing backup ' + name)
            contents[name] = saved_file.read_bytes()
            if hashlib.sha256(contents[name]).hexdigest() != before[name]['sha256']:
                raise ValueError('INSTALL_CONFLICT: changed backup ' + name)
    if before['target']['kind'] == 'directory' and current['target'] != before['target']:
        if _state(saved) != before['target']:
            raise ValueError('INSTALL_CONFLICT: saved plugin directory changed')
    receipt['status'] = 'reverting'
    _atomic(backup / 'receipt.json', receipt)
    if current['target'] != before['target']:
        if target.is_symlink():
            target.unlink()
        original = before['target']
        if original['kind'] == 'directory':
            saved.rename(target)
        elif original['kind'] == 'link':
            target.symlink_to(original['link'], target_is_directory=True)
    for name, filename in FILES.items():
        if current[name] == before[name]:
            continue
        path = data / filename
        if before[name]['kind'] == 'missing':
            path.unlink()
        else:
            _atomic_bytes(path, contents[name], before[name]['mode'])
    receipt['status'] = 'reverted'
    _atomic(backup / 'receipt.json', receipt)
    return {'status': 'reverted', 'backup': str(backup), 'target': str(target)}


def unstage_install(backup: Path) -> dict:
    backup = Path(backup)
    receipt = _load_receipt(backup)
    if receipt['status'] == 'reverted':
        return {'status': 'already_reverted', 'backup': str(backup)}
    return _restore(backup, receipt, partial=receipt['status'] != 'staged')


def stage_install(plan, data: Path | None = None, python: str | None = None,
                  *, home: Path | None = None, wiki: Path | None = None):
    # Preserve the existing runtime-only preparation API; no plugin registration.
    if isinstance(plan, Path):
        runtime = write_runtime(plan, python=python, wiki=wiki or plan.parent / 'wiki')
        return {'runtime_path': str(plan / 'codex-runtime.json'), 'runtime': runtime}
    target, source = _path(plan['target']), _path(plan['source'])
    source = source.resolve()
    data = _path(data or Path.home() / '.memory-hub')
    if data.is_symlink() or _overlap(target, source) or _overlap(target, data):
        raise ValueError('unsafe source/data replacement')
    if _read(source / '.codex-plugin/plugin.json').get('name') != 'memory-hub':
        raise ValueError('invalid plugin')
    before = {'target': _state(target), **{name: _state(data / file) for name, file in FILES.items()}}
    if before['target']['kind'] not in ('missing', 'link', 'directory'):
        raise ValueError('target must be missing, a link or a directory')
    if any(before[name]['kind'] not in ('missing', 'file') for name in FILES):
        raise ValueError('runtime/settings must be ordinary files')
    runtime, settings = _runtime_values(data, source.parents[1], python, wiki)
    after = {'target': {'kind': 'link', 'link': str(source)},
             'runtime.json': _file_state(_bytes(runtime)), 'settings.json': _file_state(_bytes(settings))}
    backups = data / 'backups'
    if backups.is_symlink():
        raise ValueError('backup parent cannot be a symlink')
    backups.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='codex-install-', dir=backups))
    for name, file in FILES.items():
        if before[name]['kind'] == 'file':
            content = (data / file).read_bytes()
            if hashlib.sha256(content).hexdigest() != before[name]['sha256']:
                raise ValueError('INSTALL_CONFLICT: configuration changed during preparation')
            _atomic_bytes(backup / name, content)
    receipt = {'version': 1, 'status': 'prepared', 'target': str(target), 'source': str(source),
               'data': str(data), 'before': before, 'after': after}
    _atomic(backup / 'receipt.json', receipt)
    try:
        if _state(target) != before['target']:
            raise ValueError('INSTALL_CONFLICT: target changed during preparation')
        if target.is_symlink():
            target.unlink()
        elif target.is_dir():
            target.rename(backup / 'plugin-source')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
        for name, filename in FILES.items():
            if _state(data / filename) != before[name]:
                raise ValueError('INSTALL_CONFLICT: configuration changed during preparation')
        write_runtime(data, source.parents[1], python, wiki)
        if any(_state(data / file) != after[name] for name, file in FILES.items()):
            raise ValueError('INSTALL_CONFLICT: configuration changed during staging')
        receipt['status'] = 'staged'
        _atomic(backup / 'receipt.json', receipt)
    except Exception:
        _restore(backup, receipt, partial=True)
        raise
    return backup
