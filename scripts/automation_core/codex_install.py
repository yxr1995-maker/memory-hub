"""Reversible staging of an existing local Codex plugin source."""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HUB=Path(__file__).resolve().parents[2]


def _read(path):
    try:
        value=json.loads(path.read_text())
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}


def _atomic(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=path.parent,prefix='.install-')
    with os.fdopen(fd,'w') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    os.replace(name,path)


def installation_plan(catalog:dict,hub:Path)->dict:
    entries=[e for e in catalog.get('installed',[]) if e.get('pluginId')=='memory-hub@personal']
    if len(entries)!=1 or entries[0].get('source',{}).get('source')!='local':
        raise ValueError('exactly one local memory-hub source required')
    target=Path(entries[0]['source']['path'])
    if not target.is_absolute():raise ValueError('source must be absolute')
    return {'target':str(target),'source':str(hub/'plugins/memory-hub'),'publish_enabled':False}


def write_runtime(data:Path,hub:Path=HUB,python:str|None=None,wiki:Path|None=None):
    runtime=_read(data/'codex-runtime.json')
    runtime.update(hub_root=str(hub.resolve()),python_path=python or sys.executable)
    runtime.setdefault('data_path',str(data))
    runtime.setdefault('wiki_path',os.environ.get('WIKI_PATH',str(Path.home()/'llm-wiki')))
    if wiki is not None:runtime['wiki_path']=str(wiki)
    _atomic(data/'codex-runtime.json',runtime)
    cfg={'recall':True,'capture':True,'publish':False}
    cfg.update({k:v for k,v in _read(data/'codex-memory-settings.json').items() if k in cfg and isinstance(v,bool)})
    cfg['publish']=False
    _atomic(data/'codex-memory-settings.json',cfg)
    return runtime


def stage_install(plan,data:Path|None=None,python:str|None=None,*,home:Path|None=None,wiki:Path|None=None):
    # Retain the runtime-only preparation API; it never registers a plugin.
    if isinstance(plan,Path):
        runtime=write_runtime(plan,python=python,wiki=wiki or plan.parent/'wiki')
        return {'runtime_path':str(plan/'codex-runtime.json'),'runtime':runtime}
    home=home or Path.home();target=Path(plan['target']);source=Path(plan['source']).resolve()
    if not target.is_absolute() or target==source:raise ValueError('unsafe source replacement')
    if _read(source/'.codex-plugin/plugin.json').get('name')!='memory-hub':raise ValueError('invalid plugin')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=data/'backups'/('codex-install-'+stamp);backup.mkdir(parents=True)
    for name,path in [('config.toml',home/'.codex/config.toml'),('marketplace.json',home/'.agents/plugins/marketplace.json'),('runtime.json',data/'codex-runtime.json'),('settings.json',data/'codex-memory-settings.json')]:
        if path.is_file():shutil.copy2(path,backup/name);(backup/name).chmod(0o600)
    if target.is_symlink():
        (backup/'source-link.txt').write_text(str(target.readlink()));target.unlink()
    elif target.exists():
        shutil.copytree(target,backup/'plugin-source')
        target.rename(target.with_name(target.name+'.backup-'+stamp))
    target.parent.mkdir(parents=True,exist_ok=True);target.symlink_to(source,target_is_directory=True)
    write_runtime(data,source.parents[1],python,wiki)
    _atomic(backup/'plan.json',plan)
    return backup
