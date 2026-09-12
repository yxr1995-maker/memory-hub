from __future__ import annotations

import pytest

from scripts.automation_core.experience import AccessContext, initialize


def make_payload(**overrides):
    payload = {
        "schema_version": 1,
        "collection_id": "fixture",
        "event_kind": "episode",
        "synthetic": True,
        "goal": "修改宠物内容文案",
        "narrative": "在同一简报下比较两稿，评审选择了不用愧疚施压的一稿。",
        "explicit_reason": "要有感情，但不要让人有负担。",
        "source_kind": "synthetic_fixture",
        "conditions": {"task_kind": "creative", "brand": "fixture-brand"},
        "outcome": {"status": "observed", "verification": "unknown"},
        "artifacts": [],
        "source_refs": [{"kind": "fixture", "ref": "development/creative-01"}],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "experience.sqlite3"
    initialize(db)
    owner = AccessContext(
        subject_id="fixture-owner",
        allowed_collections=("fixture",),
        role="owner",
        artifact_roots=(str(tmp_path),),
    )
    agent = AccessContext(
        subject_id="fixture-agent",
        allowed_collections=("fixture",),
        role="agent",
        artifact_roots=(str(tmp_path),),
    )
    return db, owner, agent, make_payload()

