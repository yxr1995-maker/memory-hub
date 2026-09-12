import json
import pytest
from evaluation.experience_v1 import harness
from scripts.automation_core.experience import ExperienceError


def setup(tmp_path):
    harness.freeze_tasks(tmp_path)
    write_full_auth(tmp_path)
    return tmp_path


FULL_AUTH={'free_only':True,'paid_cap':0,'confirmed_by_owner':True,'model':'agnes/agnes-2.5-flash','max_requests':36,'max_output_tokens_per_request':1200}

def write_full_auth(tmp_path,**overrides):
    payload={**FULL_AUTH,**overrides,'manifest_sha256':harness.sha256_file(tmp_path/'manifest.json')}
    (tmp_path/'authorization.json').write_text(json.dumps(payload))
    return tmp_path


def test_run_auth_and_frozen_tampering(tmp_path):
    from evaluation.experience_v1 import runner
    harness.freeze_tasks(tmp_path)
    (tmp_path/'authorization.json').write_text(json.dumps({'free_only':True,'paid_cap':0,'confirmed_by_owner':True}))
    with pytest.raises(ExperienceError,match='AUTHORIZATION'):runner.run(tmp_path,call=lambda _:None)
    write_full_auth(tmp_path)
    (tmp_path/'holdout_tasks.jsonl').write_text('{}\n')
    with pytest.raises(ExperienceError,match='SNAPSHOT_CHANGED'):runner.run(tmp_path,call=lambda _:None)


def test_groups_same_sources_no_grading_keys_and_no_writes(tmp_path):
    from evaluation.experience_v1 import runner
    setup(tmp_path);before={p:harness.sha256_file(p) for p in (tmp_path/'snapshots').rglob('*.sqlite3')}
    task=harness.load_holdout_tasks(tmp_path)[0]
    a=runner.messages(tmp_path,'A',task);b=runner.messages(tmp_path,'B',task);c=runner.messages(tmp_path,'C',task)
    assert a[1]['content'].startswith('历史材料：[]')
    assert len(b[1]['content'])>len(a[1]['content']) and len(c[1]['content'])>len(a[1]['content'])
    assert 'expected_dev_index' not in json.dumps([a,b,c])
    calls=[]
    def fake(msg):
        calls.append(msg)
        return {'model':'agnes-2.5-flash','choices':[{'message':{'content':'{"decision":"保留","reason":"有用户编辑"}'},'finish_reason':'stop'}],'usage':{'total_tokens':30}}
    r=runner.run(tmp_path,call=fake)
    assert r['requests']==36 and len(calls)==36
    assert before=={p:harness.sha256_file(p) for p in before}
    with pytest.raises(ExperienceError,match='RUN_EXISTS'):runner.run(tmp_path,call=fake)
    assert len(calls)==36


def test_failed_request_is_recorded_without_retry(tmp_path):
    from evaluation.experience_v1 import runner
    setup(tmp_path);calls=[]
    def failure(msg):calls.append(msg);raise TimeoutError('synthetic failure')
    r=runner.run(tmp_path,call=failure,limit=1)
    assert r['requests']==1 and r['failed']==1 and len(calls)==1
    row=json.loads(next((tmp_path/'results').glob('*.json')).read_text())
    assert row['status']=='error' and row['error_type']=='TimeoutError' and row['usage'] is None


BT='```'

def test_run_records_mechanical_checks_and_strips_full_fences(tmp_path):
    from evaluation.experience_v1 import runner
    setup(tmp_path)
    def call(msg):
        def resp(content):return {'model':'agnes-2.5-flash','choices':[{'message':{'content':content},'finish_reason':'stop'}],'usage':{'total_tokens':4}}
        if 'decision 和 reason' in msg[0]['content']:return resp(BT+'json\n{"decision":"保留文件","reason":"当前有编辑"}\n'+BT)
        user=msg[-1]['content']
        if '小伴' in user:text='小伴，晚间向您问好。'+('如果想聊聊，随时可以打开对话。'*4)
        elif '暖途' in user:text='暖途保温杯，350毫升带提绳，通勤路上把家的温暖握在手里，随时喝到合适的温度。'
        elif '净流' in user:text='净流净水壶，1.5升容量，滤芯可更换，日常使用简单方便，适合家庭桌面与办公室场景。'
        else:text='第3秒画面调整为：包装放左侧三分之一，狗留在右侧，移除两件背景道具，保持原有构图平衡。'
        return resp(text)
    report=runner.run(tmp_path,call=call)
    rows={p.name:json.loads(p.read_text()) for p in (tmp_path/'results').glob('*.json')}
    good=rows['00-A.json']
    assert good['status']=='complete'
    assert good['parse_mode']=='fenced' and good['parsed_answer']=={'decision':'保留文件','reason':'当前有编辑'}
    assert good['mechanical_checks']['passed'] is True and good['mechanical_checks']['failures']==[]
    assert good['raw_answer'].startswith(BT+'json')
    assert report['mechanical_failures']==0 and json.loads((tmp_path/'run-summary.json').read_text())['mechanical_failures']==0


@pytest.mark.parametrize('answer,passed', [('```json\n{"decision":"keep","reason":"edits"}\n```',True),('not JSON',False)])
def test_transport_completion_is_not_task_success(tmp_path,answer,passed):
    from evaluation.experience_v1 import runner
    setup(tmp_path)
    def fake(_):
        return {'model':'agnes-2.5-flash','choices':[{'message':{'content':answer},'finish_reason':'stop'}]}
    runner.run(tmp_path,call=fake,limit=1)
    row=json.loads(next((tmp_path/'results').glob('*.json')).read_text())
    assert row['status']=='complete' and row['raw_answer']==answer
    assert row['task_success']=='unverified'
    assert row['mechanical_checks']['passed'] is passed


def test_authorization_model_mismatch_is_rejected(tmp_path):
    from evaluation.experience_v1 import runner
    setup(tmp_path)
    write_full_auth(tmp_path,model='some/paid-model')
    with pytest.raises(ExperienceError,match='AUTHORIZATION'):
        runner.run(tmp_path,call=lambda _:None)
    assert not (tmp_path/'results').exists()


def test_authorization_max_requests_rejected_before_any_call(tmp_path):
    from evaluation.experience_v1 import runner
    setup(tmp_path);calls=[]
    write_full_auth(tmp_path,max_requests=1)
    with pytest.raises(ExperienceError,match='AUTHORIZATION'):
        runner.run(tmp_path,call=lambda msg: calls.append(msg),limit=2)
    assert calls==[] and not (tmp_path/'results').exists()


def test_authorization_invalid_budget_fields_are_rejected(tmp_path_factory):
    from evaluation.experience_v1 import runner
    for overrides in ({'max_requests':0},{'max_requests':True},{'max_requests':'36'},{'max_output_tokens_per_request':True},{'max_output_tokens_per_request':1201},{'max_output_tokens_per_request':'x'}):
        tmp_path=tmp_path_factory.mktemp('auth-invalid')
        setup(tmp_path)
        write_full_auth(tmp_path,**overrides)
        with pytest.raises(ExperienceError,match='AUTHORIZATION'):
            from evaluation.experience_v1 import runner as _r
            _r.run(tmp_path,call=lambda _:None)
        assert not (tmp_path/'results').exists()


def test_request_body_output_cap_matches_authorization(tmp_path,monkeypatch):
    import io
    from evaluation.experience_v1 import runner
    setup(tmp_path);bodies=[]
    def capture(req,timeout):
        bodies.append(json.loads(req.data))
        assert timeout==40
        return io.BytesIO(json.dumps({'model':runner.MODEL,'choices':[{'message':{'content':'{"decision":"keep","reason":"edits"}'},'finish_reason':'stop'}]}).encode())
    monkeypatch.setattr(runner.urllib.request,'urlopen',capture)
    write_full_auth(tmp_path,max_requests=1,max_output_tokens_per_request=17)
    result=runner.run(tmp_path,limit=1)
    assert len(bodies)==1 and bodies[0]['max_tokens']==17
    assert result['billing_amount'] is None
    row=json.loads((tmp_path/'results/00-A.json').read_text())
    assert row['usage'] is None


@pytest.mark.parametrize('field',['model','max_requests','max_output_tokens_per_request'])
def test_missing_explicit_authorization_field_rejected(tmp_path,field):
    from evaluation.experience_v1 import runner
    setup(tmp_path)
    p=tmp_path/'authorization.json';auth=json.loads(p.read_text());auth.pop(field);p.write_text(json.dumps(auth))
    with pytest.raises(ExperienceError,match='AUTHORIZATION'):
        runner.run(tmp_path,call=lambda _:None,limit=1)
    assert not (tmp_path/'results').exists()
