"""Pipeline orchestration for default-auto run and maintain operations."""
from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .indexer import IndexBuild, atomic_rebuild_index
from .lifecycle import LifecycleReport, PreparedLifecycle, finalize_successor_after_index, prepare_successor_pages, successor_plan
from .operation import AutomationLock, GitBaseline, LockBusy, OwnedPath, StageReport, TransactionContext, commit_exact, rollback_transaction, stage_exact
from .schema import Mode, OperationContext


MAINTAIN_ORDER = (
    "validate",
    "publish_pages_lifecycle",
    "index_swap",
    "lint",
    "atomic_manifest_commit",
    "archive",
    "exact_stage_commit",
)

MAINTAIN_CHECKPOINTS = (
    "VALIDATED",
    "PAGES_LIFECYCLE_PUBLISHED",
    "INDEX_SWAPPED",
    "LINT_PASSED",
    "MANIFEST_COMMITTED",
    "ARCHIVED",
    "STAGE_COMMITTED",
)


class CliUsageError(Exception):
    def __init__(self, message: str, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class ModeOptions:
    mode: Mode
    apply: bool
    commit: bool


@dataclass(frozen=True)
class StageOutcome:
    ok: bool
    checkpoint_owned: bool = False
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok_outcome(cls, checkpoint_owned: bool = False, message: str = "") -> StageOutcome:
        return cls(ok=True, checkpoint_owned=checkpoint_owned, message=message)

    @classmethod
    def fail_outcome(cls, message: str = "") -> StageOutcome:
        return cls(ok=False, message=message)


@dataclass(frozen=True)
class OperationReport:
    result: str  # "committed" | "safe" | "preexisting_staged" | "failed"
    stage_names: list[str]
    checkpoints: list[str]
    failed_stage: str | None = None
    error: str | None = None
    operation_id: str = ""


class StageRunner:
    def plan(self, stage_name: str, tx: TransactionContext) -> StageOutcome:
        return StageOutcome.ok_outcome()

    def apply(self, stage_name: str, tx: TransactionContext) -> StageOutcome:
        return StageOutcome.ok_outcome()

    def index_swap_once_and_finalize(self, tx: TransactionContext) -> StageOutcome:
        tx.inject("index.before_swap")
        atomic_rebuild_index(tx.operation.wiki_path, tx.operation.data_path)
        tx.journal.checkpoint("INDEX_SWAPPED")
        tx.inject("index.after_swap")
        return StageOutcome.ok_outcome(checkpoint_owned=True)


def parse_mode(command: str, argv: Sequence[str]) -> ModeOptions:
    parser = argparse.ArgumentParser(prog=command, add_help=False)
    parser.add_argument("--safe", action="store_true")
    parser.add_argument("--no-auto", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--commit", action="store_true")
    ns, _ = parser.parse_known_args(argv)

    if ns.safe and (ns.apply or ns.commit):
        raise CliUsageError("--safe cannot be combined with --apply or --commit", exit_code=2)
    if ns.safe:
        return ModeOptions(Mode.SAFE, False, False)
    if ns.no_auto:
        return ModeOptions(Mode.NO_AUTO, bool(ns.apply), bool(ns.commit and ns.apply))
    return ModeOptions(Mode.AUTO, True, True)


def maintain_pipeline(tx: TransactionContext, stages: StageRunner) -> OperationReport:
    operation = tx.operation
    print(f"mode={operation.mode.value} apply={str(operation.apply).lower()} operation_id={operation.operation_id}")

    if tx.journal.baseline.staged:
        return OperationReport(
            result="preexisting_staged",
            stage_names=[],
            checkpoints=[],
            operation_id=operation.operation_id,
        )

    completed_stages: list[str] = []
    checkpoints: list[str] = []

    try:
        for name, checkpoint in zip(MAINTAIN_ORDER, MAINTAIN_CHECKPOINTS, strict=True):
            completed_stages.append(name)
            if operation.mode is Mode.SAFE:
                outcome = stages.plan(name, tx)
            elif name == "index_swap":
                outcome = stages.index_swap_once_and_finalize(tx)
            else:
                outcome = stages.apply(name, tx)

            if not outcome.ok:
                rollback_transaction(tx)
                return OperationReport(
                    result="failed",
                    stage_names=completed_stages,
                    checkpoints=checkpoints,
                    failed_stage=name,
                    error=outcome.message,
                    operation_id=operation.operation_id,
                )

            if not outcome.checkpoint_owned:
                tx.journal.checkpoint(checkpoint)
            checkpoints.append(checkpoint)

        return OperationReport(
            result="committed" if operation.apply else "safe",
            stage_names=completed_stages,
            checkpoints=checkpoints,
            operation_id=operation.operation_id,
        )
    except Exception as exc:
        rollback_transaction(tx)
        return OperationReport(
            result="failed",
            stage_names=completed_stages,
            checkpoints=checkpoints,
            failed_stage=completed_stages[-1] if completed_stages else None,
            error=str(exc),
            operation_id=operation.operation_id,
        )


def run_pipeline(tx: TransactionContext, stages: StageRunner) -> OperationReport:
    operation = tx.operation
    print(f"mode={operation.mode.value} apply={str(operation.apply).lower()} operation_id={operation.operation_id}")
    return maintain_pipeline(tx, stages)
