"""Fresh stdio clients exercise the actual existing MCP server without a host install."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import pytest

HUB=Path(__file__).resolve().parents[2]


def require_mcp_python():
    executable=os.environ.get("EXPERIENCE_MCP_PYTHON",shutil.which("python3") or sys.executable)
    probe=subprocess.run([executable,"-c","from mcp.server.fastmcp import FastMCP"],capture_output=True,text=True,timeout=10)
    if probe.returncode:
        pytest.skip("MCP SDK unavailable in protocol interpreter; protocol coverage not verified")
    return executable


def rpc_session(env,calls):
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'experience-fixture','version':'1'}}},
              {'jsonrpc':'2.0','method':'notifications/initialized'},
              {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
    messages.extend({'jsonrpc':'2.0','id':i+3,'method':'tools/call','params':{'name':name,'arguments':args}} for i,(name,args) in enumerate(calls))
    # System Python already has MCP; no dependency installation.
    r=subprocess.run([require_mcp_python(),str(HUB/'mcp/server.py')],input=''.join(json.dumps(x)+'\n' for x in messages),capture_output=True,text=True,env=env,timeout=30)
    assert r.returncode==0,r.stderr
    output={v['id']:v for line in r.stdout.splitlines() if (v:=json.loads(line)).get('id')}
    assert output[1]['result']['instructions']
    names={x['name'] for x in output[2]['result']['tools']}
    assert {'recall_for_decision','record_episode','read_evidence','memory_search','memory_ask'}<=names
    return [output[i+3]['result'] for i in range(len(calls))]


def structured(result):
    assert not result.get('isError'),result
    return result.get('structuredContent') or json.loads(result['content'][0]['text'])


def test_fresh_mcp_sessions_observe_correction(env,tmp_path):
    from scripts.automation_core.experience import record_episode,revise_understanding,revoke
    db,owner,agent,payload=env
    environ={**os.environ,'MEMORY_HUB_DATA':str(db.parent),'WIKI_PATH':str(tmp_path/'unused-wiki'),
             'PYTHONPATH':str(HUB),'PYTHONDONTWRITEBYTECODE':'1','MEMORY_HUB_EXPERIENCE_COLLECTIONS':'["fixture"]','MEMORY_HUB_EXPERIENCE_ROOTS':json.dumps([str(tmp_path)])}
    first=rpc_session(environ,[('record_episode',{'payload':payload,'idempotency_key':'mcp-first'})])
    receipt=structured(first[0])
    second=rpc_session(environ,[('recall_for_decision',{'task':'文案有感情但不要有负担'}),('read_evidence',{'evidence_id':receipt['event_id'],'revision':1})])
    assert structured(second[0])['items'][0]['explicit_reason']==payload['explicit_reason']
    correction=revise_understanding(db,owner,target_id=receipt['event_id'],base_revision=1,patch={'explicit_reason':'当前简报要直接表达，不再沿用旧风格'},reason='synthetic owner correction',evidence_ids=[receipt['event_id']])
    third=rpc_session(environ,[('recall_for_decision',{'task':'文案有感情但不要有负担'}),('record_episode',{'payload':{**payload,'source_kind':'user_explicit'},'idempotency_key':'forged'})])
    assert structured(third[0])['items'][0]['revision']==2
    assert structured(third[0])['items'][0]['explicit_reason']=='当前简报要直接表达，不再沿用旧风格'
    assert third[1]['isError'] is True
    revoke(db,owner,target_id=receipt['event_id'],base_revision=2,reason='withdraw fixture')
    fourth=rpc_session(environ,[('recall_for_decision',{'task':'文案'}),('read_evidence',{'evidence_id':receipt['event_id'],'revision':1})])
    assert structured(fourth[0])['items']==[]
    assert fourth[1]['isError'] is True
    assert not (tmp_path/'access.jsonl').exists()


@pytest.mark.parametrize("profile_source", ["environment", "workspace_file"])
def test_experimental_profile_has_only_three_tools(tmp_path,profile_source):
    env={**os.environ,'PYTHONPATH':str(HUB),'PYTHONDONTWRITEBYTECODE':'1','MEMORY_HUB_DATA':str(tmp_path),
         'MEMORY_HUB_EXPERIENCE_PROFILE':'1','MEMORY_HUB_EXPERIENCE_COLLECTIONS':'[]'}
    if profile_source == 'workspace_file':
        env.pop('MEMORY_HUB_EXPERIENCE_PROFILE')
        (tmp_path/'experience-host.json').write_text(json.dumps({'collections':[],'artifact_roots':[],'profile':True}))
    messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'fixture','version':'1'}}},
              {'jsonrpc':'2.0','method':'notifications/initialized'}, {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{}}]
    r=subprocess.run([require_mcp_python(),str(HUB/'mcp/server.py')],input=''.join(json.dumps(x)+'\n' for x in messages),capture_output=True,text=True,env=env,timeout=30)
    assert r.returncode==0,r.stderr
    responses={v['id']:v for l in r.stdout.splitlines() if (v:=json.loads(l)).get('id')}
    assert {t['name'] for t in responses[2]['result']['tools']}=={'record_episode','read_evidence','recall_for_decision'}
    schemas={t['name']:t['inputSchema']['properties'] for t in responses[2]['result']['tools']}
    recall=schemas['recall_for_decision'];read=schemas['read_evidence']
    assert recall['max_chars']['minimum']==512 and recall['max_chars']['maximum']==6000
    assert recall['mode']['enum']==['evidence','explore']
    assert read['max_chars']['minimum']==1 and read['max_chars']['maximum']==12000
    assert read['revision']['minimum']==1 and read['offset']['minimum']==0
    assert not (tmp_path/'experience.sqlite3').exists()


def test_protocol_fixture_reports_missing_sdk_instead_of_claiming_pass(monkeypatch):
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs: subprocess.CompletedProcess(args,1,'','SDK missing'))
    with pytest.raises(pytest.skip.Exception,match='MCP SDK'):
        require_mcp_python()


def test_mcp_session_delivery_is_optional_and_refreshable(env,tmp_path):
    from scripts.automation_core.experience import record_episode
    db,owner,agent,p=env
    record_episode(db,owner,p,idempotency_key='session-mcp')
    environ={**os.environ,'MEMORY_HUB_DATA':str(db.parent),'WIKI_PATH':str(tmp_path/'unused'),
             'PYTHONPATH':str(HUB),'MEMORY_HUB_EXPERIENCE_COLLECTIONS':'["fixture"]',
             'MEMORY_HUB_EXPERIENCE_PROFILE':'0'}
    args={'task':'文案','session_id':'synthetic-session'}
    result=rpc_session(environ,[('recall_for_decision',args),('recall_for_decision',args),
                              ('recall_for_decision',{**args,'refresh':True})])
    assert structured(result[0])['items']
    assert structured(result[1])['items']==[]
    assert structured(result[2])['items']
