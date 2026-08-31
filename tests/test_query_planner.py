from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts.automation_core.query_planner import (
    AuditSink,
    ExpansionTerm,
    HttpTransport,
    L0Snippet,
    OpenAICompatiblePlanner,
    QueryPlan,
    RecallBackend,
    SearchRequest,
    local_expand,
    plan_query,
    sanitize_l0,
    validate_expansions,
)


@dataclass
class FakeHit:
    path: str
    abstract: str
    content: str


class FakeRecall:
    def __init__(self, fts_hits: Sequence[FakeHit] | None = None, vec_hits: Sequence[FakeHit] | None = None) -> None:
        self._fts = list(fts_hits or [])
        self._vec = list(vec_hits or [])

    def fts(self, query: str, limit: int = 12) -> Sequence[FakeHit]:
        return self._fts[:limit]

    def vector(self, query: str, limit: int = 12) -> Sequence[FakeHit]:
        return self._vec[:limit]


class HttpStatus(Exception):
    def __init__(self, code: int) -> None:
        self.code = code


class ReadTimeout(Exception):
    pass


class BadJson(Exception):
    pass


class FakeTransport:
    def __init__(self, responses: Sequence[Any]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def post_json(self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float) -> tuple[int, str]:
        self.calls += 1
        if not self.responses:
            return 200, "[]"
        item = self.responses.pop(0)
        if isinstance(item, HttpStatus):
            return item.code, f"Error {item.code}"
        if isinstance(item, ReadTimeout):
            raise TimeoutError("read timeout")
        if isinstance(item, BadJson):
            return 200, "not a json string at all"
        if isinstance(item, Exception):
            raise item
        if isinstance(item, (dict, list)):
            return 200, json.dumps(item)
        if isinstance(item, tuple) and len(item) == 2:
            return item[0], item[1]
        return 200, str(item)


class FixtureContext:
    def __init__(self) -> None:
        self.recall = FakeRecall(
            fts_hits=[
                FakeHit("notes/a.md", "fixture lifecycle tracking in memory-hub", "detailed content a"),
                FakeHit("notes/b.md", "Graphiti temporal deprecation and indexing", "detailed content b"),
            ],
            vec_hits=[
                FakeHit("notes/c.md", "Letta cross-day consolidation and clustering", "detailed content c"),
            ],
        )
        self.audit = AuditSink()


@pytest.fixture
def fixture() -> FixtureContext:
    return FixtureContext()


@pytest.mark.parametrize("failure,reason", [
    (HttpStatus(429), "http_429"),
    (ReadTimeout(), "read_timeout"),
    (BadJson(), "invalid_json"),
])
def test_llm_failure_degrades_once_to_local(failure: Any, reason: str, fixture: FixtureContext) -> None:
    transport = FakeTransport([failure])
    plan = plan_query(SearchRequest("fixture lifecycle"), fixture.recall, transport, fixture.audit)
    assert plan.planner == "local"
    assert plan.fallback_reason == reason
    assert transport.calls == 1
    assert all(2 <= len(term.text) <= 64 for term in plan.expansions)
    assert fixture.audit.last.keys() >= {
        "query_hash", "candidate_paths", "expansions", "planner", "fallback_reason", "latency_ms", "final_hits"
    }
    assert "fixture lifecycle" not in fixture.audit.raw_text


def test_llm_success_returns_llm_planner(fixture: FixtureContext) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps([
                        {"term": "lifecycle", "confidence": 0.95},
                        {"term": "deprecation", "confidence": 0.85},
                    ])
                }
            }
        ]
    }
    transport = FakeTransport([payload])
    plan = plan_query(SearchRequest("fixture search"), fixture.recall, transport, fixture.audit)
    assert plan.planner == "llm"
    assert plan.fallback_reason is None
    assert len(plan.expansions) == 2
    assert plan.expansions[0].text == "lifecycle"


def test_disabled_expand_returns_original_only(fixture: FixtureContext) -> None:
    transport = FakeTransport([])
    plan = plan_query(SearchRequest("fixture", expand=False), fixture.recall, transport, fixture.audit)
    assert plan.planner == "original-only"
    assert plan.fallback_reason == "expand_disabled"
    assert plan.expansions == ()
    assert transport.calls == 0


def test_sanitize_l0_removes_secrets_and_paths() -> None:
    raw = "Bearer secret-token-12345678901234567890 in /Users/private/dir with ghp_123456789012345678901234567890123456"
    sanitized = sanitize_l0(raw)
    assert "secret-token" not in sanitized
    assert "/Users/private" not in sanitized
    assert "ghp_" not in sanitized
    assert "[REDACTED_SECRET]" in sanitized or "[REDACTED_PATH]" in sanitized

