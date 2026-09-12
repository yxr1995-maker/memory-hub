"""Source attribution guidance is public MCP metadata, not model QA."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.experience.test_end_to_end import require_mcp_python

HUB = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('profile', ['0', '1'])
def test_public_mcp_guidance_preserves_evidence_provenance(tmp_path, profile):
    env = {k: v for k, v in os.environ.items() if not k.startswith('MEMORY_HUB_')}
    env.update(PYTHONPATH=str(HUB), PYTHONDONTWRITEBYTECODE='1',
        WIKI_PATH=str(tmp_path / 'wiki'), MEMORY_HUB_DATA=str(tmp_path / 'data'),
        MEMORY_HUB_EXPERIENCE_PROFILE=profile, MEMORY_HUB_EXPERIENCE_COLLECTIONS='[]')
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': '2024-11-05', 'capabilities': {},
            'clientInfo': {'name': 'provenance-contract', 'version': '1'}}},
        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
    ]
    result = subprocess.run([require_mcp_python(), str(HUB / 'mcp/server.py')],
        input=''.join(json.dumps(row) + '\n' for row in requests),
        text=True, capture_output=True, env=env, timeout=30)
    assert result.returncode == 0, result.stderr
    messages = {row['id']: row for line in result.stdout.splitlines()
                if (row := json.loads(line)).get('id')}
    tools = {tool['name']: tool for tool in messages[2]['result']['tools']}
    for guidance in (messages[1]['result']['instructions'], tools['read_evidence']['description']):
        assert 'source_kind' in guidance
        assert 'synthetic_fixture' in guidance and '合成材料' in guidance
        assert '不是用户真实反馈' in guidance
        assert '修订不改变来源类别' in guidance
        assert '不得从旧回答补来源' in guidance
    if profile == '0':
        assert {'memory_search', 'memory_ask'} <= tools.keys()
    else:
        assert set(tools) == {'recall_for_decision', 'read_evidence', 'record_episode'}
    assert not (tmp_path / 'data').exists()
