import json
from pathlib import Path
import pytest


def test_stage_install_writes_runtime_with_cwd_data(tmp_path):
    from scripts.automation_core.codex_install import stage_install
    data = tmp_path / "data"
    result = stage_install(data)
    runtime = json.loads((data / "codex-runtime.json").read_text())
    assert runtime["data_path"] == str(data)
    assert runtime["wiki_path"] == str(tmp_path / "wiki")
    assert result["runtime_path"] == str(data / "codex-runtime.json")


def test_stage_install_python_path_is_current_interpreter(tmp_path):
    from scripts.automation_core.codex_install import stage_install
    result = stage_install(tmp_path / "data")
    runtime = json.loads((tmp_path / "data" / "codex-runtime.json").read_text())
    assert runtime["python_path"] == __import__("sys").executable


def test_stage_install_preserves_existing_workspaces(tmp_path):
    from scripts.automation_core.codex_install import stage_install
    data = tmp_path / "data"
    data.mkdir()
    (data / "codex-runtime.json").write_text(json.dumps({
        "workspaces": {"/some/ws": {"data_path": "/other/data"}},
    }))
    result = stage_install(data, wiki=tmp_path / "wiki2")
    runtime = json.loads((data / "codex-runtime.json").read_text())
    assert runtime["workspaces"] == {"/some/ws": {"data_path": "/other/data"}}
    assert runtime["wiki_path"] == str(tmp_path / "wiki2")


def test_stage_install_creates_missing_data_dir(tmp_path):
    from scripts.automation_core.codex_install import stage_install
    data = tmp_path / "nested" / "data"
    stage_install(data)
    assert (data / "codex-runtime.json").is_file()


def test_install_plan_refuses_nonlocal_source(tmp_path):
    from scripts.automation_core.codex_install import installation_plan
    with pytest.raises(ValueError):
        installation_plan({'installed':[{'pluginId':'memory-hub@personal','source':{'source':'git','url':'x'}}]},tmp_path)


def test_install_plan_uses_registered_source(tmp_path):
    from scripts.automation_core.codex_install import installation_plan
    p=installation_plan({'installed':[{'pluginId':'memory-hub@personal','source':{'source':'local','path':str(tmp_path/'source')}}]},tmp_path)
    assert p['target']==str(tmp_path/'source')
    assert p['publish_enabled'] is False


def test_safe_install_backups_and_preserves_settings(tmp_path):
    from scripts.automation_core.codex_install import stage_install
    hub=tmp_path/'hub';source=hub/'plugins/memory-hub';(source/'.codex-plugin').mkdir(parents=True)
    (source/'.codex-plugin/plugin.json').write_text('{"name":"memory-hub"}')
    home=tmp_path/'home';target=home/'plugins/memory-hub';target.mkdir(parents=True);(target/'old').write_text('old')
    data=tmp_path/'data';data.mkdir()
    old={'wiki_path':'/custom/wiki','workspaces':{'/fixture':{'data_path':'/fixture-data'}}}
    (data/'codex-runtime.json').write_text(json.dumps(old))
    (data/'codex-memory-settings.json').write_text('{"recall":false,"capture":false,"publish":true}')
    backup=stage_install({'target':str(target),'source':str(source)},data,'/usr/bin/python3',home=home)
    assert target.is_symlink() and target.resolve()==source.resolve()
    assert (backup/'plugin-source/old').read_text()=='old'
    runtime=json.loads((data/'codex-runtime.json').read_text())
    assert runtime['wiki_path']==old['wiki_path'] and runtime['workspaces']==old['workspaces']
    assert json.loads((data/'codex-memory-settings.json').read_text())=={'recall':False,'capture':False,'publish':False}
