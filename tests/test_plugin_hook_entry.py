import json
import os
import subprocess
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parents[1]
ENTRY = HUB / "plugins" / "memory-hub" / "hooks" / "codex_hook_entry.py"


def _run(payload: dict, env: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        env={**os.environ, **env},
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr.decode()
    return json.loads(proc.stdout.decode() or "{}")


def test_session_start_emits_rules(tmp_path):
    env = {"MEMORY_HUB_DATA": str(tmp_path / "data"), "WIKI_PATH": str(tmp_path / "wiki")}
    result = _run({"hook_event_name": "SessionStart", "session_id": "s", "turn_id": "t",
                   "cwd": str(tmp_path)}, env)
    ctx = result["hookSpecificOutput"]["additionalContext"]
    assert "历史记忆仅为参考资料" in ctx


def test_workspace_mapping_selects_data_dir(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    ws_data = tmp_path / "ws-data"
    runtime = tmp_path / "rt.json"
    runtime.write_text(json.dumps({
        "data_path": str(tmp_path / "default-data"),
        "workspaces": {str(ws): {"data_path": str(ws_data), "wiki_path": str(tmp_path / "wiki")}},
    }))
    transcript = ws / "t.jsonl"
    record = {"type": "response_item", "payload": {"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "workspace mapping check"}]}}
    transcript.write_text(json.dumps(record) + "\n")
    env = {"MEMORY_HUB_RUNTIME": str(runtime), "MEMORY_HUB_DATA": "", "WIKI_PATH": ""}
    # Explicit env vars must be absent (not empty) to allow mapping; strip them.
    env = {k: v for k, v in env.items() if v}
    payload = {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t",
               "transcript_path": str(transcript), "cwd": str(ws)}
    _run(payload, env)
    assert (ws_data / "codex-memory.db").is_file()
    assert not (tmp_path / "default-data" / "codex-memory.db").exists()


def test_explicit_env_overrides_workspace_mapping(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    env_data = tmp_path / "env-data"
    runtime = tmp_path / "rt.json"
    runtime.write_text(json.dumps({
        "workspaces": {str(ws): {"data_path": str(tmp_path / "ws-data")}},
    }))
    transcript = ws / "t.jsonl"
    record = {"type": "response_item", "payload": {"type": "message", "role": "user",
              "content": [{"type": "input_text", "text": "env override check"}]}}
    transcript.write_text(json.dumps(record) + "\n")
    env = {"MEMORY_HUB_RUNTIME": str(runtime),
           "MEMORY_HUB_DATA": str(env_data), "WIKI_PATH": str(tmp_path / "wiki")}
    payload = {"hook_event_name": "Stop", "session_id": "s", "turn_id": "t",
               "transcript_path": str(transcript), "cwd": str(ws)}
    _run(payload, env)
    assert (env_data / "codex-memory.db").is_file()
    assert not (tmp_path / "ws-data" / "codex-memory.db").exists()


def test_invalid_json_returns_empty_object(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(ENTRY)],
        input=b"not json",
        capture_output=True,
        env={**os.environ, "MEMORY_HUB_DATA": str(tmp_path / "d"), "WIKI_PATH": str(tmp_path / "w")},
        timeout=15,
    )
    assert proc.returncode == 0
    assert json.loads(proc.stdout.decode()) == {}


def test_argv_fallback_event_name(tmp_path):
    env = {"MEMORY_HUB_DATA": str(tmp_path / "data"), "WIKI_PATH": str(tmp_path / "wiki")}
    proc = subprocess.run(
        [sys.executable, str(ENTRY), "SessionStart"],
        input=json.dumps({"session_id": "s", "turn_id": "t", "cwd": str(tmp_path)}).encode(),
        capture_output=True, env={**os.environ, **env}, timeout=15,
    )
    result = json.loads(proc.stdout.decode())
    assert result["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_mcp_launcher_uses_runtime_data_and_wiki_from_non_repo_cwd(tmp_path):
    fake=tmp_path/'fake hub';(fake/'mcp').mkdir(parents=True)
    (fake/'mcp'/'server.py').write_text('import json,os; print(json.dumps({k:os.environ.get(k) for k in ["MEMORY_HUB_DATA","WIKI_PATH"]}))')
    workspace=tmp_path/'workspace with spaces';workspace.mkdir()
    runtime=tmp_path/'runtime.json'
    runtime.write_text(json.dumps({'hub_root':str(fake),'python_path':sys.executable,'data_path':str(tmp_path/'default data'),'wiki_path':str(tmp_path/'default wiki'),'workspaces':{str(workspace):{'data_path':str(tmp_path/'workspace data'),'wiki_path':str(tmp_path/'workspace wiki')}}}))
    env={k:v for k,v in os.environ.items() if k not in ('MEMORY_HUB_DATA','WIKI_PATH','MEMORY_HUB_WORKSPACE')}
    env['MEMORY_HUB_RUNTIME']=str(runtime)
    p=subprocess.run([str(HUB/'plugins/memory-hub/mcp/launch.sh')],cwd=workspace,env=env,capture_output=True,text=True,timeout=15)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)=={'MEMORY_HUB_DATA':str(tmp_path/'workspace data'),'WIKI_PATH':str(tmp_path/'workspace wiki')}


def test_mcp_launcher_preserves_explicit_paths(tmp_path):
    fake=tmp_path/'hub';(fake/'mcp').mkdir(parents=True)
    (fake/'mcp'/'server.py').write_text('import json,os; print(json.dumps({k:os.environ.get(k) for k in ["MEMORY_HUB_DATA","WIKI_PATH"]}))')
    runtime=tmp_path/'runtime.json';runtime.write_text(json.dumps({'hub_root':str(fake),'python_path':sys.executable,'data_path':'/wrong/data','wiki_path':'/wrong/wiki'}))
    env={**os.environ,'MEMORY_HUB_RUNTIME':str(runtime),'MEMORY_HUB_DATA':str(tmp_path/'explicit data'),'WIKI_PATH':str(tmp_path/'explicit wiki')}
    p=subprocess.run([str(HUB/'plugins/memory-hub/mcp/launch.sh')],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=15)
    assert p.returncode==0,p.stderr
    assert json.loads(p.stdout)=={'MEMORY_HUB_DATA':str(tmp_path/'explicit data'),'WIKI_PATH':str(tmp_path/'explicit wiki')}
