import importlib.util
from pathlib import Path
import pytest
from scripts.automation_core.experience import ExperienceError
from scripts.automation_core.experience.adapter import ExperienceAdapter


def test_adapter_context_cannot_be_self_elevated(env):
    db,owner,agent,p=env
    adapter=ExperienceAdapter(db,agent)
    p['source_kind']='user_explicit'
    with pytest.raises(ExperienceError) as e:adapter.record_episode(p,idempotency_key='forge')
    assert e.value.code=='ACCESS_DENIED'


def test_unconfigured_server_denies_record(env,monkeypatch):
    from scripts.automation_core.experience.adapter import from_environment
    monkeypatch.delenv('MEMORY_HUB_EXPERIENCE_COLLECTIONS',raising=False)
    monkeypatch.setenv('MEMORY_HUB_DATA',str(env[0].parent))
    adapter=from_environment()
    with pytest.raises(ExperienceError) as e:adapter.record_episode(env[3],idempotency_key='x')
    assert e.value.code=='ACCESS_DENIED'


def test_workspace_settings_are_scoped_to_selected_data_root(tmp_path,monkeypatch):
    import json
    from scripts.automation_core.experience.adapter import from_environment, host_settings
    monkeypatch.delenv('MEMORY_HUB_EXPERIENCE_COLLECTIONS',raising=False)
    monkeypatch.delenv('MEMORY_HUB_EXPERIENCE_ROOTS',raising=False)
    monkeypatch.delenv('MEMORY_HUB_EXPERIENCE_PROFILE',raising=False)
    first=tmp_path/'isolated';first.mkdir()
    (first/'experience-host.json').write_text(json.dumps({'collections':['fixture'],'artifact_roots':[str(first)],'profile':True}))
    monkeypatch.setenv('MEMORY_HUB_DATA',str(first))
    assert from_environment().context.allowed_collections==('fixture',)
    assert host_settings()['profile'] is True
    monkeypatch.setenv('MEMORY_HUB_DATA',str(tmp_path/'other'))
    assert from_environment().context.allowed_collections==()
    assert host_settings()['profile'] is False

@pytest.mark.parametrize('settings', [
    {'profile':'true'}, {'collections':[{}]}, {'artifact_roots':['relative']},
    {'collections':['fixture'],'role':'owner'},
])
def test_invalid_host_settings_fail_closed(tmp_path,monkeypatch,settings):
    import json
    from scripts.automation_core.experience.adapter import host_settings
    monkeypatch.setenv('MEMORY_HUB_DATA',str(tmp_path))
    (tmp_path/'experience-host.json').write_text(json.dumps(settings))
    with pytest.raises(ExperienceError):host_settings()
