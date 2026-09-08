"""Installation diagnostics and explicit switches for native Codex memory."""
from __future__ import annotations

import argparse
import hashlib
import asyncio
import json
import os
import re
import plistlib
import sqlite3
import sys
import tempfile
from pathlib import Path

HUB = Path(__file__).resolve().parents[2]


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def configure(data: Path, **changes: bool) -> dict:
    result = {'recall': True, 'capture': True, 'publish': False}
    result.update({k:v for k,v in _read(data/'codex-memory-settings.json').items() if k in result and isinstance(v,bool)})
    if any(k not in result or not isinstance(v, bool) for k,v in changes.items()):
        raise ValueError('invalid memory switch')
    result.update(changes)
    data.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.settings-', dir=data)
    with os.fdopen(fd,'w') as f:
        json.dump(result,f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(name, data/'codex-memory-settings.json')
    return result


def launchagent(hub: Path, data: Path, wiki: Path, python: str) -> bytes:
    return plistlib.dumps({
        'Label':'com.memory-hub.codex-memory',
        'ProgramArguments':[python,str(hub/'scripts/codex_integration.py'),'memory-worker','--once'],
        'StartInterval':60,'RunAtLoad':True,
        'WorkingDirectory':str(hub),
        'EnvironmentVariables':{'MEMORY_HUB_DATA':str(data),'WIKI_PATH':str(wiki),
                                'PYTHONPATH':str(hub),'PATH':os.environ.get('PATH','/usr/bin:/bin'),
                                **{k:os.environ[k] for k in ('OPENCODEX_URL','CLAUDE_MEM_MODEL') if k in os.environ}},
        'StandardOutPath':str(data/'codex-worker.stdout.log'),
        'StandardErrorPath':str(data/'codex-worker.stderr.log'),
        'ProcessType':'Background',
    })


def mcp_probe_target(data: Path) -> dict:
    installed = _read(data/'codex-install-state.json').get('plugin_root')
    if installed:
        root = Path(installed)
        if not root.is_absolute() or not (root/'mcp/launch.sh').is_file():
            raise ValueError('installed plugin launcher missing')
        return {'command':str(root/'mcp/launch.sh'),'args':[], 'source':'installed_plugin'}
    return {'command':sys.executable,'args':[str(HUB/'mcp/server.py')],'source':'source_checkout'}


async def _mcp_probe(wiki: Path, data: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    # Isolate protocol probe access logs from the user's memory database.
    with tempfile.TemporaryDirectory(prefix='memoryhub-mcp-probe-') as scratch:
        env = dict(os.environ, PYTHONPATH=str(HUB), WIKI_PATH=str(Path(scratch)/'wiki'), MEMORY_HUB_DATA=scratch)
        target = mcp_probe_target(data)
        if target['source'] == 'installed_plugin':
            env['MEMORY_HUB_RUNTIME'] = str(data/'codex-runtime.json')
        params = StdioServerParameters(command=target['command'],args=target['args'],env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = [tool.name for tool in listed.tools]
                # A read-only empty-index search tests dispatch without model calls.
                result = await session.call_tool('memory_search',{'query':'memoryhub-protocol-probe','expand':False,'fuse':False,'top':1})
                return {'status':'ok' if not result.isError else 'call_failed','tools':names,
                        'source':target['source']}


def doctor(data: Path, wiki: Path, *, probe_mcp: bool = True) -> dict:
    switches = {'recall':True,'capture':True,'publish':False}
    switches.update({k:v for k,v in _read(data/'codex-memory-settings.json').items() if k in switches and isinstance(v,bool)})
    result = {'settings':switches,'hook_observed':False,'model_use_verified':False,
              'plugin_version':_read(HUB/'plugins/memory-hub/.codex-plugin/plugin.json').get('version'),
              'installation':_read(data/'codex-install-state.json'),
              'queue':{},'capture_backlog':0,'recent_hooks':[], 'worker':_read(data/'codex-worker-state.json'),
              'index_exists':(data/'index.db').is_file(), 'mcp':{'status':'not_probed'}}
    acceptance = _read(data/'codex-acceptance.json')
    result['acceptance'] = acceptance
    def accepted(client):
        if not isinstance(client, dict) or client.get('passed') is not True:
            return False
        required = ('session_id','recall_session_id','source','answer','verified_at')
        return (all(isinstance(client.get(k),str) and client[k] for k in required)
                and client['session_id'] != client['recall_session_id']
                and bool(re.fullmatch(r'[0-9a-f]{40}',str(client.get('commit',''))))
                and isinstance(client.get('elapsed_seconds'),(int,float))
                and client['elapsed_seconds'] >= 0
                and {'SessionStart','UserPromptSubmit','Stop'}.issubset(client.get('hook_events',[])))
    clients = acceptance.get('clients',{})
    result['historical_acceptance'] = isinstance(clients,dict) and all(accepted(clients.get(k)) for k in ('cli','desktop'))
    result['index'] = {'status':'missing'}
    if result['index_exists']:
        try:
            with sqlite3.connect(f'file:{(data/"index.db").resolve()}?mode=ro',uri=True) as con:
                healthy = con.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
                tables = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                result['index'] = {'status':'ready' if healthy and 'pages' in tables else 'invalid_schema',
                                   'mtime':(data/'index.db').stat().st_mtime}
        except sqlite3.Error:
            result['index'] = {'status':'unreadable'}
    db = data/'codex-memory.db'
    if db.exists():
        try:
            with sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True) as con:
                con.row_factory = sqlite3.Row
                if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='capture_backlog'").fetchone():
                    result['capture_backlog']=con.execute('select count(*) from capture_backlog').fetchone()[0]
                result['queue'] = dict(con.execute('select status,count(*) from queue group by status').fetchall())
                result['recent_hooks'] = [dict(row) for row in con.execute('select * from events order by created_at desc limit 8')]
                result['hook_observed'] = bool(result['recent_hooks'])
        except sqlite3.Error:
            result['database_error'] = 'unreadable_schema'
    if probe_mcp:
        try:
            result['mcp'] = asyncio.run(asyncio.wait_for(_mcp_probe(wiki,data),timeout=15))
        except Exception as exc:
            result['mcp'] = {'status':'failed','error':type(exc).__name__}
    core=('scripts/automation_core/codex_memory.py','scripts/automation_core/memory_worker.py',
          'scripts/automation_core/codex_integration.py','plugins/memory-hub/hooks/hooks.json')
    hashes={p:hashlib.sha256((HUB/p).read_bytes()).hexdigest() for p in core if (HUB/p).is_file()}
    result['current_source_hashes']=hashes
    installation=result['installation']
    root=Path(installation.get('plugin_root',''))
    result['current_build_verified']=bool(result['historical_acceptance'] and len(hashes)==len(core)
        and acceptance.get('source_hashes')==hashes and result['index']['status']=='ready'
        and installation.get('mcp_verified') is True and root.is_absolute()
        and (root/'mcp/launch.sh').is_file()
        and acceptance.get('plugin_version')==installation.get('plugin_version')==result['plugin_version'])
    result['model_use_verified']=result['current_build_verified']
    # Historical records do not establish use of the current build.
    return result


def main(argv=None) -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('command',choices=['codex','memory-worker'])
    parser.add_argument('action',nargs='?',default='doctor',choices=['doctor','configure','launchagent'])
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--retry-failed',action='store_true')
    for key in ('recall','capture','publish'):
        parser.add_argument('--'+key,choices=['on','off'])
    args=parser.parse_args(argv)
    data=Path(os.environ.get('MEMORY_HUB_DATA',str(Path.home()/'.memory-hub'))).resolve()
    wiki=Path(os.environ.get('WIKI_PATH',str(Path.home()/'llm-wiki'))).resolve()
    if args.command=='memory-worker':
        from .memory_worker import run_once
        result=run_once(wiki,data,retry_failed=args.retry_failed)
    elif args.action=='configure':
        result=configure(data,**{key:getattr(args,key)=='on' for key in ('recall','capture','publish') if getattr(args,key) is not None})
    elif args.action=='launchagent':
        sys.stdout.buffer.write(launchagent(HUB,data,wiki,sys.executable))
        return 0
    else:
        result=doctor(data,wiki)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result.get('result')=='failed' else 0
