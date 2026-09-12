import json
import runpy
from pathlib import Path
import pytest

HUB=Path(__file__).resolve().parents[2]

def test_launcher_defaults_to_host_scope_file_for_any_workspace(tmp_path,monkeypatch):
    import os
    for k in list(os.environ):
        if k.startswith('MEMORY_HUB_') or k=='WIKI_PATH':monkeypatch.delenv(k)
    default=tmp_path/'default';default.mkdir()
    (default/'experience-host.json').write_text('{"collections":["codex"],"artifact_roots":[],"profile":false}')
    runtime=tmp_path/'runtime.json'
    runtime.write_text(json.dumps({'hub_root':str(HUB),'data_path':str(default),'wiki_path':str(tmp_path/'wiki'),'workspaces':{}}))
    monkeypatch.setenv('MEMORY_HUB_RUNTIME',str(runtime));monkeypatch.chdir(tmp_path)
    seen=[];monkeypatch.setattr(os,'execvpe',lambda exe,args,env:seen.append(env))
    runpy.run_path(str(HUB/'plugins/memory-hub/mcp/launch.py'))['main']()
    env=seen[0]
    assert env['MEMORY_HUB_EXPERIENCE_HOST_ROOTS']=='0'
    assert env['MEMORY_HUB_DATA']==str(default)


@pytest.mark.parametrize('explicit', [False,True])
def test_launcher_marks_only_implicit_default_for_host_roots(tmp_path,monkeypatch,explicit):
    import os
    for k in list(os.environ):
        if k.startswith('MEMORY_HUB_') or k=='WIKI_PATH':monkeypatch.delenv(k)
    runtime=tmp_path/'runtime.json'
    runtime.write_text(json.dumps({'hub_root':str(HUB),'data_path':str(tmp_path/'default'),'wiki_path':str(tmp_path/'wiki'),'workspaces':{str(tmp_path/'workspace'):{'data_path':str(tmp_path/'isolated')}}}))
    monkeypatch.setenv('MEMORY_HUB_RUNTIME',str(runtime));monkeypatch.chdir(tmp_path)
    if explicit:monkeypatch.setenv('MEMORY_HUB_DATA',str(tmp_path/'explicit'))
    seen=[];monkeypatch.setattr(os,'execvpe',lambda exe,args,env:seen.append(env))
    runpy.run_path(str(HUB/'plugins/memory-hub/mcp/launch.py'))['main']()
    env=seen[0]
    assert env['MEMORY_HUB_EXPERIENCE_HOST_ROOTS']==('0' if explicit else '1')
    assert env['MEMORY_HUB_EXPERIENCE_RUNTIME']==str(runtime)
    assert env['MEMORY_HUB_DATA']==str(tmp_path/('explicit' if explicit else 'default'))
