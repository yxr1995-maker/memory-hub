"""Automation locking, transaction journal, rollback coordinator, and exact staging."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .schema import OperationContext


def _git_paths(repo: Path, args: Sequence[str], *, check: bool = True) -> list[str]:
    """Repository-relative paths from a git command, NUL-separated.

    Without ``-z`` git quotes non-ASCII paths (core.quotePath default), which
    would never compare equal to workspace paths.
    """
    out = subprocess.run(
        ["git", *args, "-z"], cwd=repo, capture_output=True, text=True,
        errors="surrogateescape", check=check,
    ).stdout
    return [part for part in out.split("\0") if part]


class LockBusy(Exception):
    def __init__(self, message: str = "automation lock is held", exit_code: int = 75) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class LockHolder:
    pid: int
    host: str
    operation_id: str
    start_monotonic: float
    start_utc: str
    age_minutes: float


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_untrusted_holder(lock_dir: Path) -> LockHolder:
    info_file = lock_dir / "info.json"
    mtime = info_file.stat().st_mtime if info_file.is_file() else lock_dir.stat().st_mtime
    age_min = max(0.0, (time.time() - mtime) / 60.0)
    if info_file.is_file():
        try:
            data = json.loads(info_file.read_text(encoding="utf-8"))
            return LockHolder(
                pid=int(data.get("pid", 0)),
                host=str(data.get("host", "")),
                operation_id=str(data.get("operation_id", "")),
                start_monotonic=float(data.get("start_monotonic", 0.0)),
                start_utc=str(data.get("start_utc", "")),
                age_minutes=age_min,
            )
        except Exception:
            pass
    return LockHolder(pid=0, host="", operation_id="", start_monotonic=0.0, start_utc="", age_minutes=age_min)


class AutomationLock:
    def __init__(self, data_path: Path, operation: OperationContext) -> None:
        self.data_path = data_path
        self.operation = operation
        self.lock_dir = data_path / "locks" / "automation.lock"
        self.operation_id = operation.operation_id

    @classmethod
    def acquire(
        cls,
        data: Path,
        operation: OperationContext,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> AutomationLock:
        lock_dir = data / "locks" / "automation.lock"
        lock_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            lock_dir.mkdir()
        except FileExistsError:
            holder = _read_untrusted_holder(lock_dir)
            if _pid_alive(holder.pid) or holder.age_minutes <= 30.0:
                raise LockBusy(f"Lock busy: held by operation {holder.operation_id} (pid={holder.pid})", exit_code=75)
            # Archive stale lock
            archive_dir = data / "locks" / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            dest = archive_dir / f"{stamp}-{holder.operation_id or 'unknown'}"
            shutil.move(str(lock_dir), str(dest))
            lock_dir.mkdir()

        # Write lock info
        info = {
            "pid": os.getpid(),
            "host": os.uname().nodename if hasattr(os, "uname") else "localhost",
            "operation_id": operation.operation_id,
            "start_monotonic": now_monotonic(),
            "start_utc": datetime.now(timezone.utc).isoformat(),
        }
        (lock_dir / "info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        return cls(data, operation)

    def release(self) -> None:
        if self.lock_dir.exists():
            shutil.rmtree(str(self.lock_dir), ignore_errors=True)


@dataclass(frozen=True)
class GitBaseline:
    staged: tuple[str, ...]
    unstaged: tuple[str, ...]
    head: str = ""

    @classmethod
    def capture(cls, repo: Path) -> GitBaseline:
        if not (repo / ".git").exists():
            return cls(staged=(), unstaged=())
        try:
            out_staged = _git_paths(repo, ["diff", "--cached", "--name-only"])
            out_unstaged = _git_paths(repo, ["diff", "--name-only"])
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo, capture_output=True, text=True, check=False,
            ).stdout.strip()
            return cls(
                staged=tuple(sorted(out_staged)),
                unstaged=tuple(sorted(out_unstaged)),
                head=head,
            )
        except Exception:
            return cls(staged=(), unstaged=())


@dataclass(frozen=True)
class OwnedPath:
    relative: str
    after_hash: str


FailureHook = Callable[[str], None]


class OperationJournal:
    def __init__(self, operation: OperationContext, baseline: GitBaseline) -> None:
        self.operation = operation
        self.baseline = baseline
        self.state: str = "INIT"
        self.checkpoints: list[str] = []
        self.before_images: dict[str, Path | None] = {}
        self.after_hashes: dict[str, str] = {}
        self.own_commit_hash: str | None = None
        self.parent_commit_hash: str | None = None
        self.own_commit_paths: tuple[str, ...] = ()
        self.staged_paths: list[str] = []
        self.rollback_order: tuple[str, ...] = ("manifest", "index", "pages")
        self.registered_paths: list[str] = []
        self.tx_dir = operation.data_path / "transactions" / operation.operation_id
        self.tx_dir.mkdir(parents=True, exist_ok=True)
        self.journal_file = self.tx_dir / "operation-journal.jsonl"

    @classmethod
    def prepare(cls, operation: OperationContext, baseline: GitBaseline) -> OperationJournal:
        journal = cls(operation, baseline)
        journal._append_record({"event": "prepared", "operation_id": operation.operation_id})
        return journal

    def _append_record(self, record: dict[str, Any]) -> None:
        with self.journal_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def checkpoint(self, name: str) -> None:
        self.checkpoints.append(name)
        self.state = name
        self._append_record({"event": "checkpoint", "name": name})

    def record_own_commit(self, repo: Path, commit_hash: str, paths: Sequence[str] = ()) -> None:
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", commit_hash],
            cwd=repo, capture_output=True, text=True, check=True,
        ).stdout.split()
        parent = parents[1] if len(parents) > 1 else ""
        self.own_commit_hash = commit_hash
        self.parent_commit_hash = parent
        self.own_commit_paths = tuple(sorted({p.strip() for p in paths if p.strip()}))
        self._append_record(
            {"event": "own_commit", "commit": commit_hash, "parent": parent, "paths": list(self.own_commit_paths)}
        )

    def record_after_images(self, paths: Sequence[Path]) -> None:
        for path in paths:
            key = str(path.absolute())
            digest = _sha256(path)
            self.after_hashes[key] = digest
            self._append_record({"event": "after_image", "path": key, "hash": digest})

    def registered_git_paths(self) -> tuple[str, ...]:
        return tuple(self.registered_paths)

    def record_staged_paths(self, rels: Sequence[str]) -> None:
        self.staged_paths.extend(rel for rel in rels if rel not in self.staged_paths)
        self._append_record({"event": "staged_paths", "paths": list(self.staged_paths)})

    def save_before_images(self, paths: Sequence[Path]) -> None:
        before_dir = self.tx_dir / "before-images"
        before_dir.mkdir(parents=True, exist_ok=True)
        for path in paths:
            rel = str(path.absolute())
            if rel in self.before_images:
                continue
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError(f"Cannot snapshot non-regular file: {path}")
            if path.is_file():
                dest = before_dir / f"{hashlib.sha256(rel.encode()).hexdigest()}.before"
                shutil.copy2(str(path), str(dest))
                os.chmod(str(dest), 0o600)
                self.before_images[rel] = dest
                self.registered_paths.append(rel)
                self._append_record({"event": "before_image", "path": rel, "dest": str(dest)})
            else:
                self.before_images[rel] = None
                self.registered_paths.append(rel)
                self._append_record({"event": "before_image", "path": rel, "dest": None})

    def register_lifecycle(self, plan: Any) -> None:
        self._append_record({"event": "register_lifecycle", "plan": str(plan)})

    def write_verified_temp(self, target: Path, content: bytes) -> Path:
        temp_file = target.parent / f".{target.name}.tmp"
        temp_file.write_bytes(content)
        self.record_after_images([temp_file])
        self._append_record({"event": "temp_written", "path": str(target)})
        return temp_file

    def rename_new(self) -> None:
        self._append_record({"event": "rename_new"})

    def rename_old(self) -> None:
        self._append_record({"event": "rename_old"})


@dataclass
class TransactionContext:
    operation: OperationContext
    journal: OperationJournal
    failure_hook: FailureHook | None = None

    def inject(self, point: str) -> None:
        if self.failure_hook:
            self.failure_hook(point)


def begin_transaction(
    operation: OperationContext,
    baseline: GitBaseline,
    failure_hook: FailureHook | None = None,
) -> TransactionContext:
    journal = OperationJournal.prepare(operation, baseline)
    return TransactionContext(operation=operation, journal=journal, failure_hook=failure_hook)


@dataclass(frozen=True)
class StageReport:
    result: str  # "exact" | "preexisting_staged" | "whitelist_mismatch" | "whitelist_hash_mismatch"
    cached: tuple[str, ...]
    verified: tuple[str, ...]


@dataclass(frozen=True)
class CommitReport:
    result: str  # "committed" | "skipped" | "not-a-repository"
    commit_hash: str


@dataclass(frozen=True)
class RollbackReport:
    success: bool
    restored_paths: tuple[str, ...]
    restored: bool = True
    conflict_paths: tuple[str, ...] = ()


def _sha256(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_exact(
    repo: Path,
    tx: TransactionContext,
    whitelist: Sequence[OwnedPath],
) -> StageReport:
    baseline = set(tx.journal.baseline.staged)
    if baseline:
        return StageReport("preexisting_staged", tuple(sorted(baseline)), ())
    
    verified_paths = set()
    for item in whitelist:
        target = repo / item.relative
        if _sha256(target) == item.after_hash:
            verified_paths.add(item.relative)
        else:
            return StageReport("whitelist_hash_mismatch", (), tuple(sorted(verified_paths)))
    
    staged_by_op = set()
    for rel in sorted(verified_paths):
        subprocess.run(["git", "add", "--", rel], cwd=repo, check=True)
        staged_by_op.add(rel)
    tx.journal.record_staged_paths(sorted(staged_by_op))
    
    cached = set(_git_paths(repo, ["diff", "--cached", "--name-only"]))
    
    if cached != verified_paths:
        # Unstage only operation-added paths
        for rel in sorted(staged_by_op):
            subprocess.run(["git", "restore", "--staged", "--", rel], cwd=repo, check=False)
        return StageReport("whitelist_mismatch", tuple(sorted(cached)), tuple(sorted(verified_paths)))
    
    return StageReport("exact", tuple(sorted(cached)), tuple(sorted(verified_paths)))


def commit_exact(
    repo: Path,
    tx: TransactionContext,
    stage: StageReport,
) -> CommitReport:
    if stage.result != "exact":
        return CommitReport("skipped", "")
    if not (repo / ".git").exists():
        return CommitReport("not-a-repository", "")
    
    msg = f"chore(wiki): memory-hub maintain {tx.operation.operation_id}"
    proc = subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        # Commit hooks (or anything else) rejected the commit: leave the repo
        # without this operation's staging. Unstaged files stay as-is so the
        # failure report reflects the actual workspace state.
        for rel in tx.journal.staged_paths:
            subprocess.run(
                ["git", "restore", "--staged", "--", rel],
                cwd=repo, check=False, capture_output=True,
            )
        raise RuntimeError(f"git commit failed: {proc.stderr.strip()[:300]}")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    tx.journal.record_own_commit(repo, head, stage.cached)
    return CommitReport("committed", head)


def rollback_transaction(tx: TransactionContext, *, force: bool = False) -> RollbackReport:
    restored = []
    skipped_conflicts: list[str] = []
    if "STAGE_COMMITTED" in tx.journal.checkpoints and not force:
        return RollbackReport(success=True, restored_paths=())
    externally_modified: set[str] = set()
    for path, expected in tx.journal.after_hashes.items():
        target = Path(path)
        before = tx.journal.before_images.get(path)
        before_hash = _sha256(before) if before else ""
        if target.is_symlink() or _sha256(target) not in (expected, before_hash):
            externally_modified.add(path)
    if tx.journal.own_commit_hash or tx.journal.before_images:
        repo = tx.operation.wiki_path
        if (repo / ".git").exists():
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo, capture_output=True, text=True, check=False,
            ).stdout.strip()
            if tx.journal.own_commit_hash and head == tx.journal.own_commit_hash:
                own_conflicts = [str((repo / rel).absolute()) for rel in tx.journal.own_commit_paths
                                 if str((repo / rel).absolute()) in externally_modified]
                if own_conflicts:
                    return RollbackReport(False, (), False, tuple(own_conflicts))
                # Move HEAD back to the recorded parent without touching index or
                # worktree; committed-path restore below undoes the operation's
                # own tree changes, so unrelated staged work is preserved.
                if tx.journal.parent_commit_hash:
                    subprocess.run(["git", "update-ref", "HEAD", tx.journal.parent_commit_hash, head],
                                   cwd=repo, check=True)
                else:
                    subprocess.run(["git", "update-ref", "-d", "HEAD", head], cwd=repo, check=True)
                for rel in tx.journal.own_commit_paths:
                    in_parent = subprocess.run(
                        ["git", "cat-file", "-e", f"HEAD:{rel}"],
                        cwd=repo, check=False, capture_output=True,
                    ).returncode == 0
                    if in_parent:
                        subprocess.run(
                            ["git", "checkout", "HEAD", "--", rel],
                            cwd=repo, check=False, capture_output=True,
                        )
                    else:
                        subprocess.run(
                            ["git", "rm", "-f", "--", rel],
                            cwd=repo, check=False, capture_output=True,
                        )
            elif head:
                if tx.journal.own_commit_hash:
                    # A later commit includes our committed tree. Restoring its
                    # pages/index/manifest would leave history and files divergent.
                    conflicts = tuple(str((repo / rel).absolute()) for rel in tx.journal.own_commit_paths)
                    return RollbackReport(False, (), False, conflicts or ("git-head",))
                # Later history exists on top of (or after) the operation:
                # files that subsequent commits changed must not be
                # overwritten by before images.
                base = tx.journal.own_commit_hash or tx.journal.baseline.head
                for rel in _git_paths(repo, ["diff", "--name-only", base, "HEAD"], check=False):
                    if rel:
                        changed = repo / rel
                        externally_modified.add(str(changed.absolute()))
                        externally_modified.add(str(changed.resolve()))
        for rel in tx.journal.staged_paths:
            if str((repo / rel).absolute()) in externally_modified:
                continue
            subprocess.run(
                ["git", "restore", "--staged", "--", rel],
                cwd=repo, check=False, capture_output=True,
            )
    # Reverse restoration: manifest -> index -> pages
    for orig_path, before_path in reversed(list(tx.journal.before_images.items())):
        if orig_path in externally_modified or str(Path(orig_path).resolve()) in externally_modified:
            skipped_conflicts.append(orig_path)
            continue
        target = Path(orig_path)
        if before_path is None:
            target.unlink(missing_ok=True)
            restored.append(orig_path)
        elif before_path.is_file():
            shutil.copy2(str(before_path), str(target))
            restored.append(orig_path)
    return RollbackReport(
        success=not skipped_conflicts,
        restored_paths=tuple(restored),
        restored=not skipped_conflicts,
        conflict_paths=tuple(skipped_conflicts),
    )
