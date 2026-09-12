import json
from pathlib import Path

import pytest


def suite_file(tmp_path):
    suite = json.loads(Path('evaluation/experience_v1/continuity-suite.json').read_text())
    suite['tasks'] += [dict(task_id=f'negative-{n}', query='missing-case',
        task_text='没有历史时不要猜测。只输出decision和reason。', kind='negative_transfer',
        allowed_decisions=['abstain', 'use_current'],
        rubric={'expected_decision': 'abstain'}) for n in range(4)]
    suite['episodes'][0]['payload']['conditions']['background'] = 'fixture details ' * 600
    p = tmp_path / 'suite.json'; p.write_text(json.dumps(suite, ensure_ascii=False))
    return p


def authorize(root):
    from evaluation.experience_v1 import paired_host as ph
    auth = {'free_only': True, 'paid_cap': 0, 'confirmed_by_owner': True,
            'model': ph.MODEL, 'max_sessions': 36, 'max_seconds_per_session': 120,
            'manifest_sha256': ph.sha(root / 'manifest.json')}
    (root / 'authorization.json').write_text(json.dumps(auth))


def test_freeze_balances_sources_and_expansion_without_leaking_rubric(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root = tmp_path / 'run'
    receipt = ph.freeze(root, suite_file(tmp_path))
    assert receipt['sessions'] == 36 and receipt['model_calls'] == 0
    rows = json.loads((root / 'prepared.json').read_text())
    assert len(rows) == 36
    assert ph.sha(root/'snapshots/B/experience.sqlite3') == ph.sha(root/'snapshots/C/experience.sqlite3')
    assert all(r['evidence'] == [] for r in rows if r['group'] == 'A')
    assert all(len(ph.compact(r['evidence'])) <= 6000 for r in rows)
    assert all('expected_decision' not in r['prompt'] for r in rows)
    # The large source is not silently thrown away by the ordinary baseline.
    row = next(r for r in rows if r['group']=='B' and r['task_id']=='jichuan-new-report')
    assert row['evidence'] and row['evidence'][0]['evidence_refs'][0]['revision'] == 2
    with pytest.raises(Exception):
        ph.freeze(root, suite_file(tmp_path))


def test_authorization_and_frozen_source_checked_before_host_start(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root = tmp_path/'run'; ph.freeze(root, suite_file(tmp_path))
    calls = []
    with pytest.raises(Exception): ph.run(root, invoke=lambda *a: calls.append(a))
    assert calls == []
    authorize(root)
    (root/'prepared.json').write_text('[]')
    with pytest.raises(Exception): ph.run(root, invoke=lambda *a: calls.append(a))
    assert calls == []


def test_run_keeps_transport_format_and_postconditions_separate(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root=tmp_path/'run';ph.freeze(root,suite_file(tmp_path));authorize(root)
    count=[]
    def fake(cell, prompt, seconds):
        count.append((cell,seconds))
        return {'exit_code':0,'timed_out':False,'raw_answer':'{"action":"preserve"}',
                'events':[],'usage':None,'elapsed_seconds':0.01}
    summary=ph.run(root,invoke=fake)
    assert len(count)==36 and all(seconds==120 for _,seconds in count)
    assert summary['sessions_started']==36 and summary['billing_amount'] is None
    assert summary['M2_gate']=='unassessed'
    rows=[json.loads(p.read_text()) for p in (root/'results').glob('*.json')]
    assert sum(r.get('execution',{}).get('passed',False) for r in rows if r.get('execution'))==3
    assert all(r.get('decision_matches') is None for r in rows if r['kind']=='negative_transfer')
    with pytest.raises(Exception):ph.run(root,invoke=fake)


def test_transport_failure_stops_batch_without_replacing_missing_cells(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root=tmp_path/'run';ph.freeze(root,suite_file(tmp_path));authorize(root)
    calls=[]
    def failed(*args):
        calls.append(args)
        return {'exit_code':1,'timed_out':False,'raw_answer':'','events':[],
                'usage':None,'elapsed_seconds':1,'error':'HTTP 429'}
    summary=ph.run(root,invoke=failed)
    assert len(calls)==1
    assert summary['sessions_started']==1 and summary['unattempted']==35
    assert summary['transport_errors']==1 and summary['billing_amount'] is None


def test_shipped_suite_freezes_all_domains_and_withdrawal(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    suite_path=Path('evaluation/experience_v1/paired-host-suite.json')
    suite=json.loads(suite_path.read_text())
    assert len(suite['tasks']) == 12
    assert all(sum(t['kind']==kind for t in suite['tasks'])==4 for kind in
               ['engineering_action','creative_copy','negative_transfer'])
    assert any(e.get('revoke') for e in suite['episodes'])
    root=tmp_path/'run'; ph.freeze(root,suite_path)
    rows=json.loads((root/'prepared.json').read_text())
    for task_id in ['lanxi-report-refresh','nanyu-display']:
        for group in ['B','C']:
            row=next(r for r in rows if r['task_id']==task_id and r['group']==group)
            assert row['evidence'][0]['presentation']=='reference_only'
            assert row['evidence'][0]['evidence_refs'][0]['revision']==2
    for task_id in ['fengyu-withdrawn','unknown-project']:
        assert all(not r['evidence'] for r in rows if r['task_id']==task_id)


def test_structured_output_freezes_same_choices_without_grading_keys(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root=tmp_path/'run';ph.freeze(root,suite_file(tmp_path),structured_output=True)
    rows=json.loads((root/'prepared.json').read_text())
    assert json.loads((root/'config.json').read_text())['structured_output'] is True
    for task_id in {r['task_id'] for r in rows}:
        group=[r for r in rows if r['task_id']==task_id]
        assert len({ph.compact(r.get('output_schema')) for r in group})==1
        row=group[0]
        if row['kind']=='creative_copy':
            assert 'output_schema' not in row
            continue
        schema=row['output_schema']
        assert schema['type']=='object' and schema['additionalProperties'] is False
        assert 'expected_decision' not in ph.compact(schema)
        prop='action' if row['kind']=='engineering_action' else 'decision'
        assert len(schema['properties'][prop]['enum'])>=2
        if prop=='decision':
            assert schema['required']==['decision','reason']
            assert schema['properties']['reason']['minLength']==1
        else:
            assert schema['required']==['action']
    authorize(root)
    def fake(cell,prompt,seconds):
        number=int(cell.name);expected=rows[number].get('output_schema')
        path=cell/'output-schema.json'
        assert path.exists()==(expected is not None)
        if expected:assert json.loads(path.read_text())==expected
        return {'exit_code':0,'timed_out':False,'raw_answer':'{"action":"preserve"}',
                'events':[],'usage':None,'elapsed_seconds':0.01}
    ph.run(root,invoke=fake)


@pytest.mark.parametrize('with_schema',[True,False])
def test_native_cli_receives_schema_only_when_frozen(tmp_path,monkeypatch,with_schema):
    import subprocess
    from types import SimpleNamespace
    from evaluation.experience_v1 import paired_host as ph
    cell=tmp_path/'cell';cell.mkdir()
    (cell/'host.json').write_text(json.dumps({'python_path':'python3','codex_path':'codex',
                                            'codex_sha256':'fixture'}))
    schema=cell/'output-schema.json'
    if with_schema:schema.write_text('{"type":"object"}')
    calls=[]
    def popen(command,**kwargs):
        calls.append(command)
        Path(command[command.index('-o')+1]).write_text('{"action":"preserve"}')
        return SimpleNamespace(pid=987654321,returncode=0,wait=lambda timeout=None:0)
    monkeypatch.setattr(ph.subprocess,'Popen',popen)
    monkeypatch.setattr(ph.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a[0],0))
    result=ph.invoke_cli(cell,'synthetic prompt',10)
    assert result['exit_code']==0
    assert ('--output-schema' in calls[0]) is with_schema
    if with_schema:assert calls[0][calls[0].index('--output-schema')+1]==str(schema.resolve())


def test_engineering_clarification_separates_host_readonly_from_task(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root = tmp_path / 'run'
    ph.freeze(root, suite_file(tmp_path))
    rows = json.loads((root / 'prepared.json').read_text())
    eng = [r for r in rows if r['kind'] == 'engineering_action']
    other = [r for r in rows if r['kind'] != 'engineering_action']
    assert len(eng) == 12 and len(other) == 24
    clarification = '只为后续固定执行器选择允许动作，执行器会在独立合成目录落实并核验；当前对话不实际改文件，不能用宿主只读权限代替任务条件。'
    for r in eng:
        assert clarification in r['prompt']
        assert 'expected_decision' not in r['prompt'] and 'expected_action' not in r['prompt']
    assert len({r['prompt'].split(clarification)[0][-60:] for r in eng}) >= 1
    prompts_by_task = {}
    for r in eng:
        prompts_by_task.setdefault(r['task_id'], {})[r['group']] = clarification in r['prompt']
    for task_id, groups in prompts_by_task.items():
        assert set(groups) == {'A', 'B', 'C'} and all(groups.values())
    for r in other:
        assert clarification not in r['prompt']


def _auth_for(root, model):
    from evaluation.experience_v1 import paired_host as ph
    auth = {'free_only': True, 'paid_cap': 0, 'confirmed_by_owner': True,
            'model': model, 'max_sessions': 36, 'max_seconds_per_session': 120,
            'manifest_sha256': ph.sha(root / 'manifest.json')}
    (root / 'authorization.json').write_text(json.dumps(auth))


def test_freeze_accepts_authorized_free_sensenova_model(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    new_model = 'sensenova/sensenova-6.8-flash-lite'
    assert new_model in ph.ALLOWED_MODELS
    root = tmp_path / 'run-sn'
    ph.freeze(root, suite_file(tmp_path), model=new_model)
    cfg = json.loads((root / 'config.json').read_text())
    assert cfg['model'] == new_model and cfg['host']['model'] == new_model
    _auth_for(root, new_model)
    ph.verify(root)
    _auth_for(root, ph.MODEL)
    try:
        ph.verify(root)
    except Exception:
        pass
    else:
        raise AssertionError('mismatched model must be rejected')


def test_freeze_model_option_host_verify_and_cli(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from evaluation.experience_v1 import paired_host as ph
    new_model = 'agnes/agnes-3.0-flash'
    # freeze with 3.0 writes config + host
    root = tmp_path / 'run3'
    ph.freeze(root, suite_file(tmp_path), model=new_model)
    cfg = json.loads((root / 'config.json').read_text())
    assert cfg['model'] == new_model
    assert cfg['host']['model'] == new_model
    # authorized 3.0 verifies, mismatched 2.5 rejected
    _auth_for(root, new_model)
    ph.verify(root)
    _auth_for(root, ph.MODEL)
    try:
        ph.verify(root)
    except Exception:
        pass
    else:
        raise AssertionError('mismatched model must be rejected')
    # fake native CLI receives 3.0
    cell = tmp_path / 'cell3'
    cell.mkdir()
    (cell / 'host.json').write_text(json.dumps({'python_path': 'python3', 'codex_path': 'codex',
                                                 'codex_sha256': 'fixture', 'model': new_model}))
    seen = {}
    def popen(command, **kwargs):
        seen['command'] = command
        Path(command[command.index('-o') + 1]).write_text('{"action":"preserve"}')
        return SimpleNamespace(pid=111, returncode=0, wait=lambda timeout=None: 0)
    monkeypatch.setattr(ph.subprocess, 'Popen', popen)
    monkeypatch.setattr(ph.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a[0], 0))
    ph.invoke_cli(cell, 'synthetic prompt', 10)
    home_cfg = (cell / 'codex-home' / 'config.toml').read_text()
    assert new_model in home_cfg


def test_freeze_rejects_unsupported_model_before_start(tmp_path):
    from evaluation.experience_v1 import paired_host as ph
    root = tmp_path / 'run-bad'
    try:
        ph.freeze(root, suite_file(tmp_path), model='gpt-99')
    except Exception:
        pass
    else:
        raise AssertionError('unsupported model must be rejected')
    assert not (root / 'manifest.json').exists()
