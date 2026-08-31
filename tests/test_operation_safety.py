from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.automation_core.operation import (
    AutomationLock,
    GitBaseline,
    LockBusy,
    OwnedPath,
    begin_transaction,
    commit_exact,
    rollback_transaction,
    stage_exact,
)
from scripts.automation_core.schema import Mode, OperationContext
from tests.helpers.full_auto_fixture import write_page


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


class OperationFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.repo = tmp_path / "wiki"
        self.data = tmp_path / "data"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.operation_id = "20260831T000000Z-op-test"

        # Init git repo
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)

        # Baseline commit
        write_page(self.repo, "init.md", {}, "init\n")
        subprocess.run(["git", "add", "init.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True)

        # Seed index.db
        (self.data / "index.db").write_text("index-data", encoding="utf-8")

    def operation(self, op_id: str | None = None) -> OperationContext:
        return OperationContext(
            operation_id=op_id or self.operation_id,
            command="maintain",
            mode=Mode.AUTO,
            auto=True,
            apply=True,
            wiki_path=self.repo,
            data_path=self.data,
        )

    def lock(self, pid: int, age_minutes: float = 0.0) -> None:
        lock_dir = self.data / "locks" / "automation.lock"
        lock_dir.mkdir(parents=True, exist_ok=True)
        info = {
            "pid": pid,
            "host": "localhost",
            "operation_id": "prior-op",
            "start_monotonic": 0.0,
            "start_utc": "2026-08-31T00:00:00Z",
        }
        (lock_dir / "info.json").write_text(str(info).replace("'", '"'), encoding="utf-8")
        if age_minutes > 0:
            mtime = os.path.getmtime(str(lock_dir)) - (age_minutes * 60.0 + 10)
            os.utime(str(lock_dir / "info.json"), (mtime, mtime))
            os.utime(str(lock_dir), (mtime, mtime))

    def acquire(self) -> AutomationLock:
        return AutomationLock.acquire(self.data, self.operation())

    def stage_user_file(self, rel: str) -> None:
        write_page(self.repo, rel, {}, "user content\n")
        subprocess.run(["git", "add", rel], cwd=self.repo, check=True)

    def transaction(self) -> Any:
        baseline = GitBaseline.capture(self.repo)
        return begin_transaction(self.operation(), baseline)

    def next_transaction(self) -> Any:
        baseline = GitBaseline.capture(self.repo)
        return begin_transaction(self.operation("next-op"), baseline)

    def cached_paths(self) -> list[str]:
        out = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=self.repo, capture_output=True, text=True).stdout
        return [l.strip() for l in out.splitlines() if l.strip()]

    def owned(self, rel: str, content: str = "content\n") -> OwnedPath:
        target = write_page(self.repo, rel, {}, content)
        return OwnedPath(rel, sha256_file(target))


@pytest.fixture
def operation_fixture(tmp_path: Path) -> OperationFixture:
    return OperationFixture(tmp_path)


def test_live_lock_exits_75_and_stale_dead_lock_is_archived(operation_fixture: OperationFixture) -> None:
    operation_fixture.lock(pid=os.getpid(), age_minutes=31.0)
    with pytest.raises(LockBusy) as exc_info:
        operation_fixture.acquire()
    assert exc_info.value.exit_code == 75

    operation_fixture.lock(pid=999_999, age_minutes=31.0)
    acquired = operation_fixture.acquire()
    assert acquired.operation_id == operation_fixture.operation_id
    assert list((operation_fixture.data / "locks" / "archive").iterdir())


def test_preexisting_staged_safely_blocks_operation_without_index_or_git_mutation(operation_fixture: OperationFixture) -> None:
    operation_fixture.stage_user_file("user.md")
    before_index = sha256_file(operation_fixture.data / "index.db")
    tx = operation_fixture.transaction()
    report = stage_exact(operation_fixture.repo, tx, [operation_fixture.owned("new.md")])
    assert report.result == "preexisting_staged"
    assert operation_fixture.cached_paths() == ["user.md"]
    assert sha256_file(operation_fixture.data / "index.db") == before_index


def test_empty_baseline_requires_cached_to_equal_verified_whitelist(operation_fixture: OperationFixture) -> None:
    tx = operation_fixture.transaction()
    report = stage_exact(operation_fixture.repo, tx, [operation_fixture.owned("new.md")])
    assert report.result == "exact"
    assert operation_fixture.cached_paths() == ["new.md"]
    commit_report = commit_exact(operation_fixture.repo, tx, report)
    assert commit_report.result == "committed"
    assert operation_fixture.cached_paths() == []

    # Racing modification / unverified extra file staged while baseline was clean
    tx2 = operation_fixture.next_transaction()
    operation_fixture.stage_user_file("racer.md")
    failed = stage_exact(operation_fixture.repo, tx2, [operation_fixture.owned("next.md")])
    assert failed.result == "whitelist_mismatch"
    assert "next.md" not in operation_fixture.cached_paths()
