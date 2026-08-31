from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts.automation_core.indexer import (
    atomic_rebuild_index,
    detect_index_schema,
    load_page_records,
)
from tests.helpers.full_auto_fixture import write_page


ROOT = Path(__file__).resolve().parents[1]


def columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f"pragma table_info({table})").fetchall()]


def test_new_columns_and_old_page_fallback(tmp_path: Path) -> None:
    wiki, data = tmp_path / "wiki", tmp_path / "data"
    write_page(wiki, "legacy.md", {}, "legacy body\n")
    write_page(
        wiki,
        "scoped.md",
        {
            "title": "Scoped Page",
            "scope": "agent",
            "scope_id": "Agent 007",
            "scope_confidence": "high",
            "status": "deprecated",
            "valid_at": "2026-08-01",
            "invalid_at": "2026-08-31",
            "updated": "2026-08-31",
            "last_verified": "2026-08-31",
        },
        "scoped body\n",
    )

    result = atomic_rebuild_index(wiki, data)
    assert result.page_count == 2
    assert result.meta_count == 2

    with sqlite3.connect(result.db_path) as db:
        assert columns(db, "pages") == [
            "path", "title", "type", "tags", "abstract", "content",
            "scope", "scope_id", "scope_confidence", "status"
        ]
        assert columns(db, "meta") == ["path", "updated", "last_verified", "valid_at", "invalid_at"]

        legacy = db.execute("select scope, scope_id, scope_confidence, status from pages where path='legacy.md'").fetchone()
        assert legacy == ("project", "default-project", "low", "active")

        scoped = db.execute("select scope, scope_id, scope_confidence, status from pages where path='scoped.md'").fetchone()
        assert scoped == ("agent", "agent-007", "high", "deprecated")

        scoped_meta = db.execute("select updated, last_verified, valid_at, invalid_at from meta where path='scoped.md'").fetchone()
        assert scoped_meta == ("2026-08-31", "2026-08-31", "2026-08-01", "2026-08-31")

    records = load_page_records(result.db_path)
    assert len(records) == 2
    assert records["scoped.md"].scope_id == "agent-007"
    assert records["scoped.md"].valid_at == "2026-08-01"


def test_failed_rebuild_preserves_legacy_db_hash(tmp_path: Path) -> None:
    wiki, data = tmp_path / "wiki", tmp_path / "data"
    write_page(wiki, "a.md", {"project": "A"}, "a\n")
    write_page(wiki, "b.md", {"project": "B"}, "b\n")

    first = atomic_rebuild_index(wiki, data)
    before_hash = hashlib.sha256((data / "index.db").read_bytes()).hexdigest()

    with pytest.raises(RuntimeError, match="injected index failure"):
        atomic_rebuild_index(wiki, data, failure_after=1)

    after_hash = hashlib.sha256((data / "index.db").read_bytes()).hexdigest()
    assert after_hash == before_hash

