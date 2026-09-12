import json
from pathlib import Path

import pytest

from evaluation.experience_v1 import m2
from scripts.automation_core.experience import ExperienceError


def authorize(root, **extra):
    auth={**m2.AUTHORIZATION, 'manifest_sha256':m2.harness.sha256_file(root/'manifest.json'), **extra}
    (root/'authorization.json').write_text(json.dumps(auth))


def fake_model(msg, *, max_tokens):
    assert max_tokens == 37
    task=msg[-1]['content'].split('当前任务：')[-1]
    chosen=next((c for c in m2.actions.cases() if c['task_text']==task),None)
    answer=json.dumps({'action':{'edited':'preserve','scratch':'reset','race':'conditional_update','backup':'sqlite_backup'}[chosen['task_id']]}) if chosen else '合成创作回答'
    return {'model':m2.runner.MODEL,'choices':[{'message':{'content':answer},'finish_reason':'stop'}]}


def test_freeze_covers_actions_and_new_creative_before_calls(tmp_path):
    result=m2.freeze(tmp_path)
    tasks=m2.harness.load_holdout_tasks(tmp_path)
    assert result['tasks']==8 and result['requests']==24
    assert sum(t['artifact']['kind']=='engineering_action' for t in tasks)==4
    old_ids={t['task_id'] for t in m2.harness.HOLDOUT_TASKS}
    assert all(t['task_id'] not in old_ids for t in tasks)
    with pytest.raises(ExperienceError,match='FREEZE_EXISTS'):m2.freeze(tmp_path)
    assert not (tmp_path/'results').exists()


def test_run_executes_postconditions_and_keeps_unknown_cost(tmp_path):
    m2.freeze(tmp_path);authorize(tmp_path,max_output_tokens_per_request=37)
    before={p:p.read_bytes() for p in (tmp_path/'snapshots').rglob('*.sqlite3')}
    result=m2.run(tmp_path,call=fake_model)
    assert result['requests']==24
    assert result['engineering_first_pass']=={'A':4,'B':4,'C':4}
    assert result['billing_amount'] is None and result['M2_gate']=='unassessed'
    rows=[json.loads(p.read_text()) for p in (tmp_path/'results').glob('*.json')]
    assert len(rows)==24 and all(r['usage'] is None for r in rows)
    executed=[r for r in rows if r['kind']=='engineering_action']
    assert all(r['execution']['observations']['unrelated_preserved'] for r in executed)
    assert all(p.read_bytes()==raw for p,raw in before.items())
    packet=json.loads((tmp_path/'blind-review.json').read_text())
    assert len(packet)==12 and all('group' not in r for r in packet)
    with pytest.raises(ExperienceError,match='RUN_EXISTS'):m2.run(tmp_path,call=fake_model)


def test_insufficient_authorization_and_tampering_make_no_calls(tmp_path):
    m2.freeze(tmp_path);authorize(tmp_path,max_requests=23);calls=[]
    with pytest.raises(ExperienceError,match='AUTHORIZATION'):
        m2.run(tmp_path,call=lambda *a,**k:calls.append(a))
    assert not calls and not (tmp_path/'results').exists()
    authorize(tmp_path)
    manifest=tmp_path/'manifest.json';content=json.loads(manifest.read_text())
    content['source_sha256']['evaluation/experience_v1/m2.py']='changed'
    manifest.write_text(json.dumps(content));authorize(tmp_path)
    with pytest.raises(ExperienceError,match='SNAPSHOT_CHANGED'):
        m2.run(tmp_path,call=lambda *a,**k:calls.append(a))
    assert not calls and not (tmp_path/'results').exists()


def test_invalid_json_is_format_failure_and_does_not_execute(tmp_path):
    m2.freeze(tmp_path);authorize(tmp_path)
    calls=[]
    def wrong(*a,**k):
        calls.append(1)
        return {'model':m2.runner.MODEL,'choices':[{'message':{'content':'I would preserve the file'},'finish_reason':'stop'}]}
    m2.run(tmp_path,call=wrong)
    rows=[json.loads(p.read_text()) for p in (tmp_path/'results').glob('*.json')]
    assert len(calls)==24
    eng=[r for r in rows if r['kind']=='engineering_action']
    assert all(r['format_pass'] is False and r['execution'] is None for r in eng)
    assert not list((tmp_path/'actions').iterdir())


def test_authorization_change_stops_before_second_model_call(tmp_path):
    m2.freeze(tmp_path);authorize(tmp_path,max_output_tokens_per_request=37);calls=[]
    def change(msg,**kw):
        calls.append(1);authorize(tmp_path,max_output_tokens_per_request=1)
        return fake_model(msg,**kw)
    with pytest.raises(ExperienceError,match='AUTHORIZATION_CHANGED'):m2.run(tmp_path,call=change)
    assert len(calls)==1


def test_unrecognized_response_model_is_not_success(tmp_path):
    m2.freeze(tmp_path);authorize(tmp_path)
    calls=[]
    def wrong(*a,**kw):
        calls.append(1)
        return {'model':'unexpected','choices':[{'message':{'content':'{}'},'finish_reason':'stop'}]}
    with pytest.raises(ExperienceError,match='MODEL_MISMATCH'):m2.run(tmp_path,call=wrong)
    rows=[json.loads(p.read_text()) for p in (tmp_path/'results').glob('*.json')]
    assert len(calls)==1 and len(rows)==1 and rows[0]['execution'] is None
    assert rows[0]['transport_status']=='error'


def test_default_transport_sends_authorized_output_cap(tmp_path,monkeypatch):
    import io
    m2.freeze(tmp_path);authorize(tmp_path,max_output_tokens_per_request=37);bodies=[]
    def capture(req,timeout):
        body=json.loads(req.data);bodies.append(body)
        assert timeout==40
        return io.BytesIO(json.dumps(fake_model(body['messages'],max_tokens=body['max_tokens'])).encode())
    monkeypatch.setattr(m2.runner.urllib.request,'urlopen',capture)
    report=m2.run(tmp_path)
    assert len(bodies)==24 and all(b['max_tokens']==37 for b in bodies)
    assert report['engineering_first_pass']=={'A':4,'B':4,'C':4}
