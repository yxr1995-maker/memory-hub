"""Frozen holdout harness: independent snapshots, leakage check, dry-run plan."""
import importlib.util
import json
import os
import sqlite3
import stat

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "evaluation" / "experience_v1"


def load_harness():
    spec = importlib.util.spec_from_file_location("experience_harness", EVAL / "harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_freeze_seeds_three_independent_snapshots(tmp_path):
    harness = load_harness()
    report = harness.freeze_tasks(dest=tmp_path)
    snapshots = tmp_path / "snapshots"
    dbs = {group: snapshots / group / "experience.sqlite3" for group in ("A", "B", "C")}
    for group, db in dbs.items():
        assert db.is_file(), f"{group} snapshot missing"
        con = sqlite3.connect(db)
        count = con.execute("SELECT count(*) FROM experience_events WHERE status='active'").fetchone()[0]
        con.close()
        assert count == 12, f"{group} must hold the 12 development cases"
    # Independence: mutating one snapshot must not touch the others.
    from scripts.automation_core.experience import AccessContext, recall_for_decision, revoke
    owner = AccessContext("eval-owner", ("fixture",), "owner", (str(tmp_path),))
    con = sqlite3.connect(dbs["A"])
    event_id = con.execute("SELECT event_id FROM experience_events LIMIT 1").fetchone()[0]
    revision = con.execute("SELECT revision FROM experience_events WHERE event_id=?", (event_id,)).fetchone()[0]
    con.close()
    revoke(dbs["A"], owner, target_id=event_id, base_revision=revision, reason="independence probe")
    con = sqlite3.connect(dbs["B"])
    still_active = con.execute("SELECT count(*) FROM experience_events WHERE status='active'").fetchone()[0]
    con.close()
    assert still_active == 12
    assert report["tasks_frozen"] == 12
    assert report["leaks"] == []


def test_leakage_checker_flags_verbatim_and_clean_tasks(tmp_path):
    harness = load_harness()
    harness.freeze_tasks(dest=tmp_path)
    dev = [json.loads(line) for line in (EVAL / "development.jsonl").read_text().splitlines() if line.strip()]
    leaked_task = {"task_id": "leak-probe", "task_text": f"请处理：{dev[0]['goal']}"}
    leaks = harness.check_leakage([leaked_task], dev)
    assert leaks and leaks[0]["task_id"] == "leak-probe"
    clean = harness.check_leakage(harness.load_holdout_tasks(tmp_path), dev)
    assert clean == []


def test_manifest_holds_hashes_not_key_contents(tmp_path):
    harness = load_harness()
    harness.freeze_tasks(dest=tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    keys = [json.loads(line) for line in (tmp_path / "holdout_keys.jsonl").read_text().splitlines() if line.strip()]
    assert manifest["tasks_sha256"] and manifest["keys_sha256"] and manifest["config_sha256"]
    assert manifest["keys_sha256"] == harness.sha256_file(tmp_path / "holdout_keys.jsonl")
    blob = json.dumps(manifest, ensure_ascii=False)
    assert "expected_dev_index" not in blob, "manifest must not embed grading keys"
    assert len(keys) == 12 and all("expected_dev_index" in k for k in keys)
    mode = stat.S_IMODE((tmp_path / "holdout_keys.jsonl").stat().st_mode)
    assert mode & 0o077 == 0, "grading keys must not be group/other readable"


def test_plan_is_dry_run_and_run_requires_authorization(tmp_path):
    harness = load_harness()
    harness.freeze_tasks(dest=tmp_path)
    plan = harness.build_plan(tmp_path)
    assert plan["groups"] == ["A", "B", "C"] and plan["tasks"] == 12 and plan["runs"] == 36
    assert plan["status"] == "dry_run" and plan["model_calls_made"] == 0
    assert plan["budget"]["paid_cap"] == 0 and plan["budget"]["confirmed"] is False
    assert not (tmp_path / "results").exists()
    import pytest
    from scripts.automation_core.experience import ExperienceError
    with pytest.raises(ExperienceError) as excinfo:
        harness.execute_run(tmp_path, group="C", task_index=0)
    assert excinfo.value.code == "AUTHORIZATION_REQUIRED"
    assert not (tmp_path / "results").exists()


def test_tasks_carry_artifact_specs_and_grader_fails_empty_answers(tmp_path):
    h=load_harness();h.freeze_tasks(tmp_path)
    for t in h.load_holdout_tasks(tmp_path):
        r=h.grade(t,None)
        if t['artifact']['kind']=='engineering_json':assert r['passed'] is False
        else:assert r['mechanical']['failures']


def test_engineering_checker_accepts_only_exact_required_output(tmp_path):
    h=load_harness();h.freeze_tasks(tmp_path);t=h.load_holdout_tasks(tmp_path)[0]
    assert 'dev_index' not in t and 'required_keys' not in t['artifact']
    assert h.grade(t,{'decision':'保留文件','reason':'当前有编辑'})['passed']
    assert not h.grade(t,{'decision':'保留文件','reason':''})['passed']
    assert not h.grade(t,{'decision':'保留文件','reason':'当前有编辑','extra':'x'})['passed']
    # This is a shape check, never interpreted as task success or retrieval credit.
    assert h.grade(t,{'decision':'错误决定','reason':'错误理由'})['passed']


def test_creative_gates_check_intent_forbidden_and_length(tmp_path):
    h=load_harness();h.freeze_tasks(tmp_path);t=h.load_holdout_tasks(tmp_path)[4]
    assert h.grade(t,'x')['mechanical']['failures']
    text='小伴，晚间向您问好。'+('如果想聊聊，随时可以打开对话。'*4)
    r=h.grade(t,text);assert not r['mechanical']['failures'] and 'score' not in r
    assert h.grade(t,text+'愧疚')['mechanical']['forbidden_hits']


def test_injection_helpers_surface_expected_episode(tmp_path):
    from evaluation.experience_v1 import runner
    h=load_harness();h.freeze_tasks(tmp_path);t=h.load_holdout_tasks(tmp_path)[0]
    b=runner.messages(tmp_path,'B',t);c=runner.messages(tmp_path,'C',t)
    assert '原先无条件覆盖' in json.dumps(b,ensure_ascii=False)
    assert '写入前比较所有权' in json.dumps(c,ensure_ascii=False)


def test_execute_run_requires_gateway_url_and_records_usage_and_mechanical_only(tmp_path):
    from evaluation.experience_v1 import runner
    h=load_harness();h.freeze_tasks(tmp_path)
    (tmp_path/'authorization.json').write_text(json.dumps({'free_only':True,'paid_cap':0,'confirmed_by_owner':True,'model':'agnes/agnes-2.5-flash','max_requests':36,'max_output_tokens_per_request':1200,'manifest_sha256':h.sha256_file(tmp_path/'manifest.json')}))
    response={'model':'agnes-2.5-flash','choices':[{'message':{'content':'sample'},'finish_reason':'stop'}],'usage':{'total_tokens':4}}
    runner.run(tmp_path,call=lambda _:response,limit=1)
    r=json.loads(next((tmp_path/'results').glob('*.json')).read_text())
    assert r['usage']['total_tokens']==4 and r['blind_review']=='unknown'


def test_execute_run_rejects_nonfree_model_and_unknown_group(tmp_path):
    h=load_harness();h.freeze_tasks(tmp_path)
    (tmp_path/'authorization.json').write_text(json.dumps({'paid_cap':5,'free_only':False,'confirmed_by_owner':True}))
    from scripts.automation_core.experience import ExperienceError
    with pytest.raises(ExperienceError):h.execute_run(tmp_path,group='C',task_index=0)
    with pytest.raises(ExperienceError):h.execute_run(tmp_path,group='Z',task_index=0)


def test_execute_run_full_group_records_results_usage_mechanical_checks(tmp_path):
    from evaluation.experience_v1 import runner
    h=load_harness();h.freeze_tasks(tmp_path)
    (tmp_path/'authorization.json').write_text(json.dumps({'free_only':True,'paid_cap':0,'confirmed_by_owner':True,'model':'agnes/agnes-2.5-flash','max_requests':36,'max_output_tokens_per_request':1200,'manifest_sha256':h.sha256_file(tmp_path/'manifest.json')}))
    response={'model':'agnes-2.5-flash','choices':[{'message':{'content':'synthetic answer'},'finish_reason':'stop'}],'usage':{'total_tokens':4}}
    report=runner.run(tmp_path,call=lambda _:response)
    assert report['requests']==36
    assert len(list((tmp_path/'results').glob('*.json')))==36
    assert report['product_benefit']=='unverified'


def test_freezing_existing_run_refuses_to_destroy_evidence(tmp_path):
    h=load_harness();h.freeze_tasks(tmp_path)
    manifest=(tmp_path/'manifest.json').read_bytes()
    (tmp_path/'results').mkdir();(tmp_path/'results/kept.json').write_text('{"kept":true}')
    from scripts.automation_core.experience import ExperienceError
    with pytest.raises(ExperienceError,match='FREEZE_EXISTS'):h.freeze_tasks(tmp_path)
    assert (tmp_path/'manifest.json').read_bytes()==manifest
    assert (tmp_path/'results/kept.json').read_text()=='{"kept":true}'
