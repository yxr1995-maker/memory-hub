import json
import sqlite3
import subprocess
from argparse import Namespace

import pytest

from tests.test_run_pipeline import RunFixture
from scripts.automation_core.orchestrator import RunStageRunner, StageRunner, maintain_pipeline
from scripts.automation_core.operation import begin_transaction, GitBaseline
from scripts.automation_core.schema import OperationContext, Mode


def setup(tmp_path):
    fx = RunFixture(tmp_path)
    op = OperationContext(operation_id='regression', command='run', mode=Mode.AUTO, auto=True,
                          apply=True, wiki_path=fx.wiki, data_path=fx.data)
    tx = begin_transaction(op, GitBaseline.capture(fx.wiki))
    records = [dict(id=f'obs-{i}', text='Repeated project knowledge about reliable pipeline processing',
                    project='fixture-project', created_at_epoch=1788100000 + i * 86400)
               for i in range(3)]
    (fx.staging / 'observations-20260830-120000.jsonl').write_text('\n'.join(map(json.dumps, records)))
    return fx, tx


def test_published_slug_with_md_suffix_enters_commit_whitelist(tmp_path):
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / '.published-this-run').write_text('2026-09-12-test.md|notes||active\n')
    runner = RunStageRunner(staging)
    assert runner._stage_commit_paths() == ['index.md', 'log.md', 'notes/2026-09-12-test.md']


def test_published_candidate_row_uses_recorded_conflict_target(tmp_path):
    staging = tmp_path / 'staging'
    staging.mkdir()
    (staging / '.published-this-run').write_text(
        '2026-09-12-c.md|drafts/memoryhub|atoms/keep.md|candidate\n')
    runner = RunStageRunner(staging)
    assert runner._stage_commit_paths() == ['atoms/keep.md', 'index.md', 'log.md']


def test_cluster_manifest_is_finalized_after_index(tmp_path):
    fx, tx = setup(tmp_path)
    runner = RunStageRunner(fx.staging)
    assert runner.apply('aggregate', tx).ok
    manifest = fx.data / 'cluster-manifest.json'
    assert not manifest.exists()
    assert runner.index_swap_once_and_finalize(tx).ok
    assert len(json.loads(manifest.read_text())['entries']) == 1
    with sqlite3.connect(fx.data / 'index.db') as con:
        assert con.execute("select count(*) from pages where path like 'moc/cluster-%'").fetchone()[0] == 1
    assert runner.apply('aggregate', tx).data['clusters'] == 0


def test_maintain_unimplemented_does_not_claim_commit(tmp_path):
    fx, tx = setup(tmp_path)
    report = maintain_pipeline(tx, StageRunner())
    assert report.result == 'failed'
    assert not (fx.data / 'index.db').exists()


def test_run_maintain_reports_skipped(tmp_path):
    fx, tx = setup(tmp_path)
    outcome = RunStageRunner(fx.staging).apply('maintain', tx)
    assert outcome.data.get('status') == 'skipped'
    assert outcome.data.get('maintained', 0) == 0


def test_cli_llm_reaches_distill(tmp_path, monkeypatch):
    from scripts import automation_cli as cli
    from scripts.automation_core import orchestrator
    fx, tx = setup(tmp_path)
    monkeypatch.setenv('WIKI_PATH', str(fx.wiki))
    monkeypatch.setenv('MEMORY_HUB_DATA', str(fx.data))
    monkeypatch.setattr('sys.argv', ['automation_cli.py', 'run', '--llm'])
    captured = []
    def pipeline(context, runner):
        monkeypatch.setattr(runner, '_run_script', lambda name, args, tx: (captured.append((name,args)) or subprocess.CompletedProcess([],0,'','')))
        runner.apply('distill', context)
        return Namespace(result='committed')
    monkeypatch.setattr(orchestrator, 'run_pipeline', pipeline)
    assert cli._run(Namespace(llm=True)) == 0
    assert captured == [('distill.sh', ['--llm'])]


@pytest.mark.parametrize('existing', [False, True])
def test_pipeline_failure_restores_cluster_and_index(tmp_path, monkeypatch, existing):
    import hashlib
    from scripts.automation_core.orchestrator import run_pipeline
    from scripts.automation_core.indexer import atomic_rebuild_index
    fx, tx = setup(tmp_path)
    key = hashlib.sha256('obs-0\nobs-1\nobs-2'.encode()).hexdigest()[:16]
    target = fx.wiki / f'moc/cluster-{key}.md'
    if existing:
        target.parent.mkdir()
        target.write_text('Original content before failed operation\n')
        atomic_rebuild_index(fx.wiki, fx.data)
    before_pages = {str(p): p.read_bytes() for p in fx.wiki.rglob('*.md')}
    index = fx.data / 'index.db'
    before_index = index.read_bytes() if index.exists() else None
    runner = RunStageRunner(fx.staging)
    monkeypatch.setattr(runner, '_run_script', lambda *args: subprocess.CompletedProcess([],0,'',''))
    def fail(point):
        if point == 'manifest.after_replace':
            raise RuntimeError('injected manifest failure')
    tx.failure_hook = fail
    report = run_pipeline(tx, runner)
    if existing:
        assert target.read_bytes() == b'Original content before failed operation\n'
        payload = json.loads((fx.data / 'reports' / 'latest-operation.json').read_text())
        assert target.relative_to(fx.wiki).as_posix() in payload['stage_data']['aggregate']['skipped']
        return
    assert report.result == 'failed'
    assert report.error == 'injected manifest failure'
    assert {str(p): p.read_bytes() for p in fx.wiki.rglob('*.md')} == before_pages
    assert (index.read_bytes() if index.exists() else None) == before_index
    assert not (fx.data / 'cluster-manifest.json').exists()


def test_commit_stage_failure_rolls_back_committed_wiki_and_index_but_not_staging(tmp_path, monkeypatch):
    from scripts.automation_core.orchestrator import run_pipeline
    from scripts.automation_core.indexer import atomic_rebuild_index

    fx, tx = setup(tmp_path)
    produced_observation = fx.staging / 'observations-20260831-120000.jsonl'
    existing = fx.wiki / 'pages' / 'existing.md'
    baseline_existing = existing.read_bytes()
    atomic_rebuild_index(fx.wiki, fx.data)
    baseline_index = (fx.data / 'index.db').read_bytes()
    baseline_head = fx.head()
    before_staging = {p.name for p in fx.staging.iterdir()}

    runner = RunStageRunner(fx.staging)
    call_log = []
    monkeypatch.setattr(runner, '_run_script', lambda name, args, tx: (call_log.append(name) or subprocess.CompletedProcess([], 0, '', '')))

    original_apply = runner.apply

    def apply_with_archive(stage_name, tx):
        outcome = original_apply(stage_name, tx)
        if stage_name == 'capture':
            produced_observation.write_text('{"id":"late","text":"late failure fixture"}\n')
        if stage_name == 'exact_stage_commit':
            return type(outcome)(False, message='forced post-commit failure')
        if stage_name == 'archive':
            for path in fx.staging.glob('observations-????????-??????.jsonl'):
                target = fx.staging / 'archive' / path.name
                target.parent.mkdir(exist_ok=True)
                path.replace(target)
            (fx.staging / 'pages').mkdir(exist_ok=True)
            (fx.staging / 'pages' / 'candidate.md').write_text('leftover candidate')
        return outcome

    monkeypatch.setattr(runner, 'apply', apply_with_archive)

    def fail(point):
        if point == 'archive.after':
            raise RuntimeError('injected archive failure')

    tx.failure_hook = fail
    report = run_pipeline(tx, runner)

    assert report.result == 'failed'
    assert report.error == 'forced post-commit failure'
    assert 'archive.sh' not in call_log
    assert existing.read_bytes() == baseline_existing
    assert not list(fx.wiki.glob('moc/cluster-*.md'))
    assert (fx.data / 'index.db').read_bytes() == baseline_index
    assert fx.head() == baseline_head
    head_tree = subprocess.run(
        ['git', 'show', '--format=', '--name-only', 'HEAD'],
        cwd=fx.wiki, capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    assert 'pages/existing.md' in head_tree
    assert not any(path.startswith('moc/cluster-') for path in head_tree)
    staging_names = {p.name for p in fx.staging.iterdir()}
    assert 'observations-20260831-120000.jsonl' in staging_names
    assert not list(fx.staging.glob('archive/observations-*.jsonl'))


def test_cross_day_cluster_cli_commit(tmp_path):
    fx, _ = setup(tmp_path)
    fx.seed_session('cross-day-cli')
    proc = fx.cli('run')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    manifest = json.loads((fx.data / 'cluster-manifest.json').read_text())
    assert len(manifest['entries']) == 1
    paths = [v['page_path'] for v in manifest['entries'].values()]
    with sqlite3.connect(fx.data / 'index.db') as con:
        for path in paths:
            assert con.execute('select path from pages where path = ?', (path,)).fetchone()
    report = json.loads((fx.data / 'reports/latest-operation.json').read_text())
    assert report['result'] == 'committed'
    assert report['stage_data']['exact_stage_commit']['commit'] == fx.head()
