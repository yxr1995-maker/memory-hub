import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts.automation_core.indexer import atomic_rebuild_index
from scripts.automation_core.query_planner import ExpansionTerm, QueryPlan, SearchRequest, collect_l0
from scripts.automation_core.ranker import RecallHit, accumulate_rrf, normalized_weights
from scripts.automation_core.service import MemoryService


def test_real_channel_keys_preserve_confidence():
    plan = QueryPlan('needle', 'hash', (ExpansionTerm('related', 1),), 'local', None, (), 0)
    scores = accumulate_rrf({'original_fts': [RecallHit('original.md', 1)],
                             'expansion_0_fts': [RecallHit('expanded.md', 1)],
                             'expansion_99_vec': [RecallHit('unknown.md', 1)]}, normalized_weights(plan))
    assert scores['expanded.md'] / scores['original.md'] == pytest.approx(0.3 / 0.7)
    assert 'unknown.md' not in scores


@pytest.fixture
def service(tmp_path):
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    (wiki / 'fact.md').write_text('---\ntitle: Source fact\nabstract: Verified needle summary\nscope: project\nscope_id: project-a\n---\nneedle source body\n')
    data = tmp_path / 'data'
    atomic_rebuild_index(wiki, data)
    return MemoryService(wiki, data, tmp_path / 'no-vector-runtime')


def test_planner_receives_real_l0_and_scope_search(service):
    snippets = collect_l0('needle', service.recall)
    assert snippets[0].text == 'Verified needle summary'
    request = SearchRequest('needle', fuse=False, expand=False, scope='project', scope_id='project-a')
    assert [r.path for r in service.search(request).results] == ['fact.md']
    assert not service.search(SearchRequest('needle', fuse=False, expand=False, scope_id='project-b')).results


def test_ask_http_success_and_explicit_failure(service, monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert 'fact.md' in payload['messages'][1]['content']
            body = json.dumps({'choices': [{'message': {'content': 'Verified answer [fact.md]'}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('OPENCODEX_URL', f'http://127.0.0.1:{server.server_port}/v1')
    request = SearchRequest('needle', fuse=False, expand=False)
    try:
        answer = service.ask_context(request).to_dict()
        assert answer['answer_status'] == 'answered'
        assert answer['context_paths'] == ['fact.md']
        assert 'Verified answer' in answer['answer']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    unavailable = service.ask_context(request).to_dict()
    assert unavailable['answer_status'] == 'unavailable'
    assert unavailable['answer_error'] == 'connection_error'
    assert unavailable['answer'] is None
    empty = service.ask_context(SearchRequest('absentword', fuse=False, expand=False)).to_dict()
    assert empty['answer_status'] == 'no_context'


def test_indexed_deprecated_source_resolves_to_current_page(service):
    (service.wiki_path / 'old.md').write_text('---\ntitle: Previous fact\nstatus: deprecated\ndeprecated_by: "[[fact.md]]"\nscope: project\nscope_id: project-a\n---\nretiredword\n')
    atomic_rebuild_index(service.wiki_path, service.data_path)
    result = service.search(SearchRequest('retiredword', fuse=False, expand=False))
    assert [r.path for r in result.results] == ['fact.md']
    assert result.results[0].status == 'active'
    assert result.results[0].rank_reason['via'] == 'successor'


def test_ask_timeout_is_not_no_context(service, monkeypatch):
    import time

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            time.sleep(0.15)
            self.send_response(200)
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('OPENCODEX_URL', f'http://127.0.0.1:{server.server_port}/v1')
    monkeypatch.setenv('MEMORY_HUB_ASK_TIMEOUT', '0.03')
    try:
        result = service.ask_context(SearchRequest('needle', fuse=False, expand=False)).to_dict()
        assert result['answer_status'] == 'unavailable'
        assert result['answer_error'] == 'timeout'
        assert result['context_paths'] == ['fact.md']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_legacy_fuse_entry_uses_shared_service(service):
    import os
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, WIKI_PATH=str(service.wiki_path), MEMORY_HUB_DATA=str(service.data_path),
               OPENCODEX_URL='http://127.0.0.1:1/v1')
    result = subprocess.run([sys.executable, str(root / 'scripts/fuse.py'), 'needle', '--top', '1'],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'fact.md' in result.stdout


def test_versioned_quality_cases(tmp_path):
    import shutil
    fixture = Path(__file__).parent / 'fixtures/retrieval_quality'
    wiki = tmp_path / 'wiki'
    shutil.copytree(fixture / 'wiki', wiki)
    data = tmp_path / 'data'
    atomic_rebuild_index(wiki, data)
    service = MemoryService(wiki, data, tmp_path / 'no-vector')
    for line in (fixture / 'golden.jsonl').read_text().splitlines():
        row = json.loads(line)
        response = service.search(SearchRequest(row['q'], top=1, expand=False, fuse=False,
                                                scope=row['scope'], scope_id=row['scope_id']))
        paths = [r.path for r in response.results]
        assert paths == ([row['expected']] if row['expected'] else []), row['type']
        if row['type'] == 'candidate':
            assert response.results[0].status == 'candidate'
