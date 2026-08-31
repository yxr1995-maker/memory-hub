from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import pytest

from scripts.automation_core.orchestrator import (
    MAINTAIN_CHECKPOINTS,
    MAINTAIN_ORDER,
    CliUsageError,
    ModeOptions,
    OperationReport,
    StageOutcome,
    StageRunner,
    maintain_pipeline,
    parse_mode,
    run_pipeline,
)
from scripts.automation_core.operation import GitBaseline, TransactionContext, begin_transaction
from scripts.automation_core.schema import Mode, OperationContext
from tests.helpers.full_auto_fixture import write_page


@pytest.mark.parametrize("command,args,expected", [
    ("run", [], ("auto", True, True)),
    ("maintain", [], ("auto", True, True)),
    ("run", ["--safe"], ("safe", False, False)),
    ("run", ["--no-auto"], ("no-auto", False, False)),
    ("run", ["--no-auto", "--apply"], ("no-auto", True, False)),
])
def test_mode_matrix(command: str, args: list[str], expected: tuple[str, bool, bool]) -> None:
    options = parse_mode(command, args)
    assert (options.mode.value, options.apply, options.commit) == expected


def test_safe_combined_with_apply_raises_exit_2() -> None:
    with pytest.raises(CliUsageError) as exc_info:
        parse_mode("run", ["--safe", "--apply"])
    assert exc_info.value.exit_code == 2


class AutoFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.wiki = tmp_path / "wiki"
        self.data = tmp_path / "data"
        self.wiki.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)
        self.operation_id = "20260831T000000Z-auto-test"

    def transaction(self, mode: Mode = Mode.AUTO, apply: bool = True) -> TransactionContext:
        ctx = OperationContext(
            operation_id=self.operation_id,
            command="maintain",
            mode=mode,
            auto=(mode == Mode.AUTO),
            apply=apply,
            wiki_path=self.wiki,
            data_path=self.data,
        )
        return begin_transaction(ctx, GitBaseline((), ()))

    def run(self, command: str = "maintain", *args: str) -> OperationReport:
        opts = parse_mode(command, list(args))
        tx = self.transaction(mode=opts.mode, apply=opts.apply)
        runner = StageRunner()
        return maintain_pipeline(tx, runner)


@pytest.fixture
def auto_fixture(tmp_path: Path) -> AutoFixture:
    return AutoFixture(tmp_path)


def test_safe_runs_plans_and_returns_safe_report(auto_fixture: AutoFixture) -> None:
    report = auto_fixture.run("maintain", "--safe")
    assert report.result == "safe"
    assert report.stage_names == list(MAINTAIN_ORDER)
