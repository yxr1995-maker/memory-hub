from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FAMILIES = [
    "scope_backfill_total",
    "query_plan_total",
    "successor_total",
    "auto_operation_total",
]


class MetricsFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.wiki = tmp_path / "wiki"
        self.data = tmp_path / "data"
        self.home.mkdir(parents=True, exist_ok=True)
        self.wiki.mkdir(parents=True, exist_ok=True)
        self.data.mkdir(parents=True, exist_ok=True)

        reports = self.data / "reports"
        reports.mkdir(parents=True, exist_ok=True)

        (reports / "scope-1.jsonl").write_text(
            json.dumps({"scope": "project", "confidence": "high", "result": "written", "conflict": False}) + "\n"
        )
        (reports / "query-plan-1.jsonl").write_text(
            json.dumps({"planner": "local", "fallback_reason": "none", "expansions": [{"term": "test", "confidence": 0.9}]}) + "\n"
        )
        (reports / "lifecycle-1.jsonl").write_text(
            json.dumps({"decision": "successor", "result": "committed"}) + "\n"
        )
        (reports / "operation-1.json").write_text(
            json.dumps({"command": "maintain", "mode": "auto", "result": "committed"})
        )

    def run_metrics(self) -> str:
        env = {
            **os.environ,
            "HOME": str(self.home),
            "WIKI_PATH": str(self.wiki),
            "MEMORY_HUB_DATA": str(self.data),
        }
        r = subprocess.run(["bash", str(ROOT / "scripts" / "metrics.sh")], env=env, capture_output=True, text=True, check=True)
        return r.stdout

    def reports(self) -> list[Path]:
        return list((self.data / "reports").glob("*"))


@pytest.fixture
def metrics_fixture(tmp_path: Path) -> MetricsFixture:
    return MetricsFixture(tmp_path)


def test_metrics_and_reports_are_complete_and_sanitized(metrics_fixture: MetricsFixture) -> None:
    text = metrics_fixture.run_metrics()
    for family in REQUIRED_FAMILIES:
        assert f"memory_hub_{family}" in text
    for report in metrics_fixture.reports():
        assert str(metrics_fixture.home) not in report.read_text()
        assert "Authorization" not in report.read_text()
        assert "raw observation" not in report.read_text()


def test_help_discloses_new_defaults() -> None:
    r = subprocess.run(["bash", str(ROOT / "memory-hub.sh"), "--help"], capture_output=True, text=True, check=True)
    assert "default: auto=on, apply=on, commit=on" in r.stdout
    assert "--safe" in r.stdout and "--no-auto" in r.stdout
