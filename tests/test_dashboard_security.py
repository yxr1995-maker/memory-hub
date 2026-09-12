import importlib.util
import json
import pathlib
import threading
import urllib.error
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('dashboard_test_server', ROOT / 'scripts/server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wiki = tmp_path / 'wiki'
    wiki.mkdir()
    (wiki / 'candidate.md').write_text('---\ntitle: Candidate\nstatus: candidate\n---\nbody')
    for name, value in [('WIKI', str(wiki)), ('DATA_DIR', str(tmp_path / 'data')), ('STAGING', str(tmp_path / 'staging')),
                        ('ACCESS_LOG', str(tmp_path / 'access.jsonl')), ('TRASH_DIR', str(tmp_path / 'trash')), ('SESSIONS_DIR', str(tmp_path / 'sessions'))]:
        monkeypatch.setattr(module, name, value)
    server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', wiki
    server.shutdown(); server.server_close(); thread.join()


def test_cross_origin_read_write_and_rebinding_rejected(endpoint):
    base, wiki = endpoint
    for method in ('GET', 'POST', 'DELETE', 'OPTIONS'):
        request = urllib.request.Request(base + '/api/page?path=candidate.md', method=method,
                                         headers={'Origin': 'https://untrusted.example', 'Content-Type': 'application/json'},
                                         data=b'{"path":"evil.md","content":"bad"}' if method == 'POST' else None)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        assert error.value.code == 403
        assert error.value.headers.get('Access-Control-Allow-Origin') is None
    assert (wiki / 'candidate.md').is_file()
    assert not (wiki / 'evil.md').exists()
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(urllib.request.Request(base + '/health', headers={'Host': 'evil.example:8787'}), timeout=3)
    assert error.value.code == 403


def test_same_origin_ui_and_real_overview(endpoint):
    base, wiki = endpoint
    request = urllib.request.Request(base + '/api/page', data=json.dumps({'path': 'new.md', 'content': 'new content'}).encode(),
                                     headers={'Origin': base, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=3) as response:
        assert response.status == 200
        assert response.headers['Access-Control-Allow-Origin'] == base
    assert (wiki / 'new.md').read_text() == 'new content'
    with urllib.request.urlopen(base + '/api/overview', timeout=3) as response:
        payload = json.load(response)
    assert payload['wiki_pages'] == 2
    assert payload['pending_candidates'] == 1
    assert payload['last_operation'] is None
    assert payload['index_updated_at'] is None
    with urllib.request.urlopen(base + '/', timeout=3) as response:
        html = response.read().decode()
    assert 'innerHTML' not in html
    assert 'textContent' in html and 'addEventListener' in html
    assert '12,962' not in html
