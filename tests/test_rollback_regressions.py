from tests.test_run_regressions import setup
from scripts.automation_core.operation import rollback_transaction


def test_rollback_removes_new_file(tmp_path):
    fx, tx = setup(tmp_path)
    page = fx.wiki / 'new.md'
    tx.journal.save_before_images([page])
    page.write_text('new')
    rollback_transaction(tx)
    assert not page.exists()


def test_snapshots_with_same_name_are_independent(tmp_path):
    fx, tx = setup(tmp_path)
    first, second = fx.wiki / 'same.md', fx.data / 'same.md'
    first.write_text('wiki-original')
    second.write_text('data-original')
    tx.journal.save_before_images([first, second])
    first.write_text('changed')
    second.write_text('changed')
    rollback_transaction(tx)
    assert first.read_text() == 'wiki-original'
    assert second.read_text() == 'data-original'


def test_repeated_snapshot_keeps_first_original(tmp_path):
    fx, tx = setup(tmp_path)
    page = fx.wiki / 'existing.md'
    page.write_text('original')
    tx.journal.save_before_images([page])
    page.write_text('intermediate')
    tx.journal.save_before_images([page])
    page.write_text('final')
    rollback_transaction(tx)
    assert page.read_text() == 'original'


def test_rollback_preserves_committed_files(tmp_path):
    fx, tx = setup(tmp_path)
    page = fx.wiki / 'existing.md'
    page.write_text('original')
    tx.journal.save_before_images([page])
    page.write_text('committed')
    tx.journal.checkpoint('STAGE_COMMITTED')
    rollback_transaction(tx)
    assert page.read_text() == 'committed'
