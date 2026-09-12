from __future__ import annotations

import hashlib
import subprocess

import pytest

from scripts.automation_core.operation import GitBaseline, OwnedPath, begin_transaction, commit_exact, rollback_transaction, stage_exact
from tests.test_operation_safety import OperationFixture


def test_committed_rollback_preserves_unrelated_dirty(tmp_path):
    fx = OperationFixture(tmp_path)
    (fx.repo / "init.md").write_text("unrelated user edits\n")
    tx = fx.transaction()
    target = fx.repo / "owned.md"
    tx.journal.save_before_images([target])
    target.write_text("owned content\n")
    tx.journal.record_after_images([target])
    stage = stage_exact(fx.repo, tx, [OwnedPath("owned.md", hashlib.sha256(target.read_bytes()).hexdigest())])
    commit_exact(fx.repo, tx, stage)
    tx.journal.checkpoint("STAGE_COMMITTED")
    report = rollback_transaction(tx, force=True)
    assert report.success
    assert not target.exists()
    assert (fx.repo / "init.md").read_text() == "unrelated user edits\n"
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=fx.repo).decode().strip() == tx.journal.baseline.head


@pytest.mark.parametrize("committed", [False, True])
def test_later_worktree_edit_is_preserved_and_reports_conflict(tmp_path, committed):
    fx = OperationFixture(tmp_path)
    tx = fx.transaction()
    target = fx.repo / "owned.md"
    tx.journal.save_before_images([target])
    target.write_text("owned content\n")
    tx.journal.record_after_images([target])
    if committed:
        stage = stage_exact(fx.repo, tx, [OwnedPath("owned.md", hashlib.sha256(target.read_bytes()).hexdigest())])
        commit_exact(fx.repo, tx, stage)
    target.write_text("external edit after publication\n")
    report = rollback_transaction(tx, force=True)
    assert not report.success
    assert target.read_text() == "external edit after publication\n"
    assert str(target) in report.conflict_paths


def test_first_commit_without_parent_can_be_rolled_back(tmp_path):
    fx = OperationFixture(tmp_path)
    # Isolated unborn branch: no parent commit is available.
    subprocess.run(["git", "checkout", "--orphan", "unborn"], cwd=fx.repo, check=True, capture_output=True)
    subprocess.run(["git", "rm", "-f", "init.md"], cwd=fx.repo, check=True, capture_output=True)
    tx = begin_transaction(fx.operation(), GitBaseline.capture(fx.repo))
    target = fx.repo / "first.md"
    tx.journal.save_before_images([target])
    target.write_text("first content\n")
    tx.journal.record_after_images([target])
    stage = stage_exact(fx.repo, tx, [OwnedPath("first.md", hashlib.sha256(target.read_bytes()).hexdigest())])
    assert commit_exact(fx.repo, tx, stage).result == "committed"
    assert rollback_transaction(tx).success
    assert not target.exists()
    assert subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=fx.repo, capture_output=True).returncode != 0


def test_later_commit_keeps_own_committed_tree_and_reports_conflict(tmp_path):
    fx = OperationFixture(tmp_path)
    tx = fx.transaction()
    target = fx.repo / "owned.md"
    tx.journal.save_before_images([target])
    target.write_text("owned content\n")
    tx.journal.record_after_images([target])
    stage = stage_exact(fx.repo, tx, [OwnedPath("owned.md", hashlib.sha256(target.read_bytes()).hexdigest())])
    commit_exact(fx.repo, tx, stage)
    (fx.repo / "external.md").write_text("external content\n")
    subprocess.run(["git", "add", "external.md"], cwd=fx.repo, check=True)
    subprocess.run(["git", "commit", "-m", "external"], cwd=fx.repo, check=True, capture_output=True)
    report = rollback_transaction(tx, force=True)
    assert not report.success
    assert target.read_text() == "owned content\n"
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=fx.repo)
