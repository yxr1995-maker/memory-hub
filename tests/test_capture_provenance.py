from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from scripts.automation_core.frontmatter import parse_page, patch_frontmatter
from scripts.automation_core.provenance import (
    normalize_jsonl,
    normalize_observation,
    render_observation_report,
)
from scripts.automation_core.schema import normalize_id, new_operation_id
from tests.helpers.full_auto_fixture import FullAutoFixture, sha256, write_page


def _write_page(root: Path, relative: str, content: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_provenance_is_stable_and_body_patch_is_exact(tmp_path: Path) -> None:
    raw = {
        "id": "42",
        "project": " Memory Hub ",
        "text": "safe fact",
        "created_at": "2026-08-31T00:00:00Z",
    }
    session = {
        "session_id": "s-1",
        "cwd": "/Users/real-user/private/memory-hub",
        "agent_id": "Agent 7",
    }

    first = normalize_observation(raw, "codex", session)
    second = normalize_observation(raw, "codex", session)

    assert first.provenance_id == second.provenance_id
    assert first.project_id == "memory-hub"
    assert first.agent_id == "agent-7"
    assert first.cwd_hash == hashlib.sha256(session["cwd"].encode()).hexdigest()
    jsonl = json.dumps(asdict(first), ensure_ascii=False) + "\n"
    report = render_observation_report(first)
    assert "/Users/real-user" not in jsonl and "/Users/real-user" not in report
    assert "session_cwd" not in jsonl

    page = parse_page(
        _write_page(tmp_path, "a.md", b"---\ntitle: A\n---\nhistorical body\n")
    )
    rendered = patch_frontmatter(
        page, {"scope": "project", "scope_id": "memory-hub"}
    )
    assert rendered.split(b"---\n", 2)[2] == b"historical body\n"


def test_schema_normalization_and_operation_id_are_deterministic() -> None:
    assert normalize_id("  M\u00e9mory / HUB___ ", "fallback") == "m-mory-hub___"
    assert normalize_id("***", "Fallback Value") == "fallback-value"
    now = datetime(2026, 8, 31, 1, 2, 3, tzinfo=timezone.utc)
    operation_id = new_operation_id(
        now, lambda: UUID("00000000-0000-0000-0000-000000000001")
    )
    assert operation_id == "20260831T010203Z-00000000-0000-0000-0000-000000000001"


def test_parse_page_rejects_malformed_or_unsafe_frontmatter(
    tmp_path: Path,
) -> None:
    contents = (
        b"title: missing-open\n---\nbody\n",
        b"---\ntitle: missing-close\nbody\n",
        b"---\nscope: project\nscope: agent\n---\nbody\n",
        b"---\ntitle: \xff\n---\nbody\n",
    )
    for index, content in enumerate(contents):
        path = _write_page(tmp_path, f"bad-{index}.md", content)
        try:
            parse_page(path)
        except (UnicodeDecodeError, ValueError):
            continue
        raise AssertionError(f"malformed frontmatter was accepted: case {index}")


def test_patch_frontmatter_renders_canonical_scalars_and_lists(tmp_path: Path) -> None:
    path = _write_page(
        tmp_path,
        "page.md",
        b"---\ntitle: 'Keep me'\ntags:\n  - existing\n---\n\x00body bytes\n",
    )
    document = parse_page(path)
    rendered = patch_frontmatter(
        document,
        {
            "scope": "project",
            "scope_id": "a'b",
            "scope_conflict": False,
            "sources": ["codex://sessions/s-1", "plain"],
        },
    )
    assert rendered == (
        b"---\n"
        b"title: 'Keep me'\n"
        b"tags:\n"
        b"  - existing\n"
        b"scope: project\n"
        b"scope_id: 'a''b'\n"
        b"scope_conflict: false\n"
        b"sources:\n"
        b"  - 'codex://sessions/s-1'\n"
        b"  - plain\n"
        b"---\n"
        b"\x00body bytes\n"
    )


def test_normalize_jsonl_filters_private_session_metadata() -> None:
    raw = {
        "id": "42",
        "project": "Memory Hub",
        "text": "safe fact",
        "created_at": "2026-08-31T00:00:00Z",
        "session_meta": {
            "session_id": "s-1",
            "cwd": "/Users/real-user/private/memory-hub",
            "agent_id": "Agent 7",
        },
    }
    sink = io.StringIO()
    assert normalize_jsonl(io.StringIO(json.dumps(raw) + "\n"), sink, "codex") == 1
    record = json.loads(sink.getvalue())
    assert record["project_id"] == "memory-hub"
    assert record["source_uri"] == "codex://sessions/s-1"
    assert record["observed_at"] == "2026-08-31T00:00:00Z"
    assert "session_meta" not in record
    assert "/Users/real-user" not in sink.getvalue()


def test_full_auto_fixture_seeds_verify_dependencies(tmp_path: Path) -> None:
    fixture = FullAutoFixture.create(tmp_path / "evidence")
    try:
        fixture.seed_verify_dependencies()
        assert (fixture.automations / "fixture-memory-hub/automation.toml").is_file()
        assert fixture.config.is_file() and fixture.hooks.is_file() and fixture.db.is_file()
        assert (fixture.wiki / ".scripts/fix_deadlinks.py").is_file()
        page = write_page(fixture.wiki, "concepts/a.md", {"title": "A"}, "body\n")
        assert len(sha256(page)) == 64
        verify = fixture.run("verify")
        assert verify.returncode == 0, verify.stdout + verify.stderr
    finally:
        fixture.cleanup()


def test_capture_and_distill_use_normalized_provenance(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    staging = tmp_path / "staging"
    wiki = tmp_path / "wiki"
    data = tmp_path / "data"
    for directory in (sessions, staging, wiki, data):
        directory.mkdir()
    private_cwd = "/Users/real-user/private/memory-hub"
    session_file = sessions / "session.jsonl"
    session_file.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": "session-1", "cwd": private_cwd},
                    }
                ),
                json.dumps(
                    {
                        "type": "response_item",
                        "timestamp": "2026-08-31T00:00:00Z",
                        "payload": {
                            "role": "user",
                            "content": [{"type": "input_text", "text": "safe fact"}],
                        },
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        {
            "CODEX_SESSIONS_DIR": str(sessions),
            "MEMORY_HUB_STAGING": str(staging),
            "MEMORY_HUB_DATA": str(data),
            "WIKI_PATH": str(wiki),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        }
    )
    capture = subprocess.run(
        ["bash", "scripts/capture.sh", "--all"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert capture.returncode == 0, capture.stderr
    output = next(staging.glob("observations-*.jsonl"))
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["project_id"] == "memory-hub"
    assert record["source_uri"] == "codex://sessions/session-1"
    assert private_cwd not in output.read_text(encoding="utf-8")
    assert "session_meta" not in record

    distill = subprocess.run(
        ["bash", "scripts/distill.sh", str(output)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert distill.returncode == 0, distill.stderr
    page = next((staging / "pages").glob("*.md"))
    rendered = page.read_text(encoding="utf-8")
    assert "memory-hub" in rendered
    assert record["provenance_id"] in rendered
    assert record["cwd_hash"] in rendered
    assert private_cwd not in rendered
