import json
import os
from pathlib import Path
import subprocess
import pytest
from tests.experience.test_end_to_end import require_mcp_python

HUB=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('host_roots', ['unmapped', 'empty', 'multiple'])
def test_explicit_host_roots_never_fall_back_to_authorized_cwd(tmp_path, monkeypatch, host_roots):
    import asyncio
    import sys
    from types import SimpleNamespace
    from scripts.automation_core.experience import ExperienceError
    from scripts.automation_core.experience.host_context import from_context

    # The scope decision can be tested without requiring the optional MCP SDK.
    monkeypatch.setitem(sys.modules, 'mcp.types', SimpleNamespace(
        ClientCapabilities=lambda **kw: kw, RootsCapability=lambda: object()))
    work=tmp_path/'workspace';work.mkdir()
    runtime=tmp_path/'runtime.json'
    runtime.write_text(json.dumps({'workspaces':{str(work):{'data_path':str(tmp_path/'data')}}}))
    monkeypatch.chdir(work)
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_HOST_ROOTS','1')
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_RUNTIME',str(runtime))
    uris={'unmapped':[(tmp_path/'other').as_uri()], 'empty':[],
          'multiple':[work.as_uri(),(tmp_path/'other').as_uri()]}[host_roots]
    class Session:
        def check_client_capability(self, capability):return True
        async def list_roots(self):
            return SimpleNamespace(roots=[SimpleNamespace(uri=u) for u in uris])
    with pytest.raises(ExperienceError,match='WORKSPACE_UNAVAILABLE'):
        asyncio.run(from_context(SimpleNamespace(session=Session())))
    assert not (tmp_path/'data').exists()


def test_roots_select_authorized_store_and_recheck_changes(env,tmp_path):
    db,owner,agent,payload=env
    workspace=tmp_path/'workspace';workspace.mkdir()
    (db.parent/'experience-host.json').write_text(json.dumps({'collections':['fixture'],'artifact_roots':[str(tmp_path)],'profile':True}))
    runtime=tmp_path/'runtime.json';runtime.write_text(json.dumps({'workspaces':{str(workspace):{'data_path':str(db.parent)}}}))
    request=tmp_path/'payload.json';request.write_text(json.dumps(payload))
    code='''
import asyncio,json,os,sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import ListRootsResult,Root
from pathlib import Path
async def main():
    roots=[Path(os.environ['TEST_WORKSPACE']).as_uri()]
    async def list_roots(ctx):return ListRootsResult(roots=[Root(uri=u) for u in roots])
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[os.environ['TEST_SERVER']],env=dict(os.environ))) as (r,w):
        async with ClientSession(r,w,list_roots_callback=list_roots) as c:
            await c.initialize()
            schema=await c.list_tools()
            assert all('ctx' not in t.inputSchema.get('properties',{}) for t in schema.tools)
            p=json.loads(Path(os.environ['TEST_PAYLOAD']).read_text())
            first=await c.call_tool('record_episode',{'payload':p,'idempotency_key':'roots-fixture'})
            assert not first.isError,first
            recall=await c.call_tool('recall_for_decision',{'task':'文案'})
            assert not recall.isError,recall
            value=json.loads(recall.content[0].text)
            assert len(value['items'])==1
            roots[:]=[Path(os.environ['TEST_WORKSPACE']).with_name('unmapped').as_uri()]
            denied=await c.call_tool('recall_for_decision',{'task':'文案'})
            assert denied.isError,denied
            print(json.dumps({'record':True,'recall':True,'scope_change_denied':True,'ctx_hidden':True}))
asyncio.run(main())
'''
    environ={**os.environ,'PYTHONPATH':str(HUB),'MEMORY_HUB_DATA':str(tmp_path/'uninitialized-default'),
      'MEMORY_HUB_EXPERIENCE_HOST_ROOTS':'1','MEMORY_HUB_EXPERIENCE_RUNTIME':str(runtime),
      'TEST_WORKSPACE':str(workspace),'TEST_SERVER':str(HUB/'mcp/server.py'),'TEST_PAYLOAD':str(request)}
    for name in ('MEMORY_HUB_EXPERIENCE_COLLECTIONS','MEMORY_HUB_EXPERIENCE_ROOTS'):environ.pop(name,None)
    r=subprocess.run([require_mcp_python(),'-c',code],env=environ,text=True,capture_output=True,timeout=30)
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['scope_change_denied']
    assert not (tmp_path/'uninitialized-default').exists()


def test_client_without_roots_capability_is_denied(tmp_path):
    isolated=tmp_path/'isolated';isolated.mkdir();default=tmp_path/'uninitialized-default';default.mkdir()
    (isolated/'experience-host.json').write_text(json.dumps({'collections':['fixture'],'artifact_roots':[],'profile':True}))
    runtime=tmp_path/'runtime.json';runtime.write_text(json.dumps({'workspaces':{str(tmp_path/'workspace'):{'data_path':str(isolated)}}}))
    code='''
import asyncio,json,os,sys
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
async def main():
    async with stdio_client(StdioServerParameters(command=sys.executable,args=[os.environ['TEST_SERVER']],env=dict(os.environ))) as (r,w):
        async with ClientSession(r,w) as c:
            await c.initialize()
            result=await c.call_tool('recall_for_decision',{'task':'x'})
            assert result.isError,result
            print(json.dumps({'denied':True}))
asyncio.run(main())
'''
    environ={**os.environ,'PYTHONPATH':str(HUB),'MEMORY_HUB_DATA':str(default),'MEMORY_HUB_EXPERIENCE_HOST_ROOTS':'1','MEMORY_HUB_EXPERIENCE_RUNTIME':str(runtime),'TEST_SERVER':str(HUB/'mcp/server.py')}
    for name in ('MEMORY_HUB_EXPERIENCE_COLLECTIONS','MEMORY_HUB_EXPERIENCE_ROOTS'):environ.pop(name,None)
    r=subprocess.run([require_mcp_python(),'-c',code],env=environ,text=True,capture_output=True,timeout=30)
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['denied'] and not (default/'experience.sqlite3').exists()


def test_current_workspace_cwd_matches_mapping_without_roots(tmp_path, monkeypatch):
    from scripts.automation_core.experience.host_context import from_context
    work=tmp_path/'workspace';work.mkdir();(tmp_path/'data').mkdir()
    (tmp_path/'runtime.json').write_text(json.dumps({'workspaces':{str(work):{'data_path':str(tmp_path/'data')}}}))
    old=Path.cwd();os.chdir(work)
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_HOST_ROOTS','1')
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_RUNTIME',str(tmp_path/'runtime.json'))
    try:
        import asyncio
        async def probe():
            return await from_context(type('Ctx',(),{'session':type('S',(),{'check_client_capability':lambda self,cap:False})()})())
        adapter=asyncio.run(probe())
        assert adapter.db_path==tmp_path/'data'/'experience.sqlite3'
    finally:os.chdir(old)
