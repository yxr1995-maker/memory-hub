import json
import subprocess
from pathlib import Path

import pytest

from evaluation.experience_v1 import paired_host as ph
from scripts.automation_core.experience import ExperienceError
from scripts.automation_core.ranker import RecallHit


SUITE = Path('evaluation/experience_v1/paired-host-suite.json')


def test_hybrid_invalid_mode_refuses_before_writing(tmp_path):
    root = tmp_path/'run'
    with pytest.raises(ExperienceError):
        ph.freeze(root, SUITE, baseline='typo')
    assert not root.exists()


def test_hybrid_uses_existing_offline_index_and_records_consumed_vectors(tmp_path, monkeypatch):
    root = tmp_path/'run'; calls = []
    def index(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, 'indexed fixture', '')
    monkeypatch.setattr(ph.subprocess, 'run', index)
    monkeypatch.setattr(ph.SqliteRecallBackend, 'fts', lambda *a: [])
    monkeypatch.setattr(ph.SqliteRecallBackend, 'vector', lambda *a: [RecallHit('panshi.md', .8, rank=1)])
    ph.freeze(root, SUITE, baseline='hybrid')
    assert len(calls) == 1
    command, kw = calls[0]
    assert command == [ph.sys.executable, str(ph.ROOT/'scripts/embed.py'), 'index']
    assert kw['env']['MEMORY_HUB_DATA'] == str(root/'data')
    assert kw['env']['WIKI_PATH'] == str(root/'wiki')
    assert kw['env']['HF_HUB_OFFLINE'] == '1'
    assert kw['check'] is True and kw['timeout'] == 120
    rows = json.loads((root/'prepared.json').read_text())
    for row in rows:
        if row['group'] == 'B':
            assert row['retrieved'] == ['panshi.md']
            assert row['baseline_diagnostic']['vector_paths'] == ['panshi.md']
            assert row['baseline_diagnostic']['plan']['fuse'] is True
        assert 'vector_paths' not in row['prompt']
        assert 'expected_decision' not in row['prompt']
    assert json.loads((root/'config.json').read_text())['baseline'] == 'hybrid'
    assert 'scripts/embed.py' in json.loads((root/'manifest.json').read_text())['source_sha256']


@pytest.mark.parametrize('failure', ['index', 'vector'])
def test_unavailable_hybrid_never_freezes_a_silent_lexical_fallback(tmp_path, monkeypatch, failure):
    def index(command, **kwargs):
        if failure == 'index': raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(ph.subprocess, 'run', index)
    monkeypatch.setattr(ph.SqliteRecallBackend, 'fts', lambda *a: [])
    monkeypatch.setattr(ph.SqliteRecallBackend, 'vector', lambda *a: [])
    root = tmp_path/'run'
    with pytest.raises((ExperienceError, subprocess.CalledProcessError)):
        ph.freeze(root, SUITE, baseline='hybrid')
    assert not (root/'manifest.json').exists()


def test_default_lexical_does_not_need_vectors_or_fastembed(tmp_path, monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('vector used by default')
    monkeypatch.setattr(ph.SqliteRecallBackend, 'vector', forbidden)
    ph.freeze(tmp_path/'run', SUITE)
    assert json.loads((tmp_path/'run/config.json').read_text())['baseline'] == 'lexical'
