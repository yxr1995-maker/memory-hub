import json
import plistlib
from pathlib import Path


def test_doctor_missing_state_is_not_verified(tmp_path):
    from scripts.automation_core.codex_integration import doctor
    result = doctor(tmp_path, tmp_path/'wiki', probe_mcp=False)
    assert result['model_use_verified'] is False
    assert result['hook_observed'] is False
    assert result['settings']['publish'] is False


def test_launchagent_has_single_worker_and_isolated_paths(tmp_path):
    from scripts.automation_core.codex_integration import launchagent
    payload = plistlib.loads(launchagent(Path('/tmp/hub with spaces'), tmp_path, tmp_path/'wiki', '/usr/bin/python3'))
    assert payload['StartInterval'] == 60
    assert payload['ProgramArguments'][-2:] == ['memory-worker', '--once']
    assert payload['EnvironmentVariables']['MEMORY_HUB_DATA'] == str(tmp_path)
    assert payload['EnvironmentVariables']['WIKI_PATH'] == str(tmp_path/'wiki')


def test_switches_are_independent(tmp_path):
    from scripts.automation_core.codex_integration import configure
    result = configure(tmp_path, publish=True)
    assert result == {'recall':True,'capture':True,'publish':True}
    result = configure(tmp_path, capture=False)
    assert result == {'recall':True,'capture':False,'publish':True}
    assert json.loads((tmp_path/'codex-memory-settings.json').read_text()) == result


def test_launchagent_preserves_gateway_configuration(tmp_path, monkeypatch):
    from scripts.automation_core.codex_integration import launchagent
    monkeypatch.setenv('OPENCODEX_URL','http://127.0.0.1:12345/v1')
    monkeypatch.setenv('CLAUDE_MEM_MODEL','fixture/model')
    payload=plistlib.loads(launchagent(Path('/tmp/hub'),tmp_path,tmp_path/'wiki','python3'))
    assert payload['EnvironmentVariables']['OPENCODEX_URL']=='http://127.0.0.1:12345/v1'
    assert payload['EnvironmentVariables']['CLAUDE_MEM_MODEL']=='fixture/model'


def test_doctor_reports_source_and_runtime_separately(tmp_path):
    from scripts.automation_core.codex_integration import doctor
    (tmp_path/'codex-install-state.json').write_text(json.dumps({'plugin_version':'fixture-installed','mcp_verified':True}))
    d=doctor(tmp_path,tmp_path/'wiki',probe_mcp=False)
    assert d['installation']['plugin_version']=='fixture-installed'
    assert not d['model_use_verified']


def test_doctor_acceptance_requires_both_clients_and_evidence(tmp_path):
    from scripts.automation_core.codex_integration import doctor
    record={'clients':{'cli':{'passed':True},'desktop':{'passed':True}}}
    p=tmp_path/'codex-acceptance.json'
    p.write_text(json.dumps(record))
    assert not doctor(tmp_path,tmp_path/'wiki',probe_mcp=False)['model_use_verified']
    for client in record['clients'].values():
        client.update(session_id='fixture-session',recall_session_id='fixture-recall',
                      commit='a'*40,source='decisions/fixture.md',
                      hook_events=['SessionStart','UserPromptSubmit','Stop'],
                      answer='fixture answer',elapsed_seconds=32,verified_at='2026-09-08T00:00:00Z')
    p.write_text(json.dumps(record))
    result=doctor(tmp_path,tmp_path/'wiki',probe_mcp=False)
    assert result['historical_acceptance']
    assert not result['current_build_verified']
    assert not result['model_use_verified']


def test_doctor_index_corruption_is_not_ready(tmp_path):
    from scripts.automation_core.codex_integration import doctor
    (tmp_path/'index.db').write_bytes(b'corrupt index')
    d=doctor(tmp_path,tmp_path/'wiki',probe_mcp=False)
    assert d['index']['status']=='unreadable'


def test_doctor_valid_index_is_ready(tmp_path):
    from scripts.automation_core.codex_integration import doctor
    from scripts.automation_core.indexer import atomic_rebuild_index
    wiki=tmp_path/'wiki';wiki.mkdir()
    atomic_rebuild_index(wiki,tmp_path)
    assert doctor(tmp_path,wiki,probe_mcp=False)['index']['status']=='ready'


def test_mcp_probe_uses_installed_plugin_entry(tmp_path, monkeypatch):
    from scripts.automation_core.codex_integration import mcp_probe_target
    root=tmp_path/'installed plugin';(root/'mcp').mkdir(parents=True)
    (root/'mcp/launch.sh').write_text('#!/bin/sh\n')
    (tmp_path/'codex-install-state.json').write_text(json.dumps({'plugin_root':str(root)}))
    target=mcp_probe_target(tmp_path)
    assert target['command']==str(root/'mcp/launch.sh')
    assert target['source']=='installed_plugin'


def test_doctor_reports_capture_backlog(tmp_path):
    from scripts.automation_core.codex_memory import connect
    from scripts.automation_core.codex_integration import doctor
    data=tmp_path/'data'
    with connect(data) as con:
        con.execute('insert into capture_backlog values(?,?,?,?)',('/fixture/transcript','s','t','/fixture'))
    assert doctor(data,tmp_path/'wiki',probe_mcp=False)['capture_backlog']==1


def test_current_build_requires_matching_hashes_and_installation(tmp_path):
    from scripts.automation_core.codex_integration import doctor
    from scripts.automation_core.indexer import atomic_rebuild_index
    wiki=tmp_path/'wiki';wiki.mkdir()
    atomic_rebuild_index(wiki,tmp_path)
    root=tmp_path/'plugin';(root/'mcp').mkdir(parents=True)
    (root/'mcp/launch.sh').write_text('#!/bin/sh\n')
    initial=doctor(tmp_path,wiki,probe_mcp=False)
    client=dict(passed=True,session_id='s',recall_session_id='r',commit='a'*40,
        source='fixture.md',answer='fixture',elapsed_seconds=1,verified_at='2026-09-08T00:00:00Z',
        hook_events=['SessionStart','UserPromptSubmit','Stop'])
    record=dict(clients={'cli':client,'desktop':client},source_hashes=initial['current_source_hashes'],plugin_version=initial['plugin_version'])
    (tmp_path/'codex-install-state.json').write_text(json.dumps(dict(plugin_root=str(root),plugin_version=initial['plugin_version'],mcp_verified=True)))
    (tmp_path/'codex-acceptance.json').write_text(json.dumps(record))
    assert doctor(tmp_path,wiki,probe_mcp=False)['current_build_verified']
    record['source_hashes']['scripts/automation_core/memory_worker.py']='outdated'
    (tmp_path/'codex-acceptance.json').write_text(json.dumps(record))
    result=doctor(tmp_path,wiki,probe_mcp=False)
    assert result['historical_acceptance'] and not result['model_use_verified']
