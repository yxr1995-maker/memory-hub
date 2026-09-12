from __future__ import annotations

import hashlib
import json

from scripts.automation_core.cluster import ClusterManifest, cluster_observations, render_merge_page, scan_observations
from scripts.automation_core.frontmatter import parse_page


def test_scan_deduplicates_requires_project_and_masks_secrets(tmp_path):
    text = "Memory maintenance note Bearer synthetic-token-value and password=synthetic-secret at /Users/fixture/wiki"
    valid = {"id": "one", "project_id": "fixture", "text": text, "created_at_epoch": 1788220800}
    rows = [valid, valid, {**valid, "id": "missing-project", "project_id": ""}]
    (tmp_path / "observations-20260901-120000.jsonl").write_text("\n".join(json.dumps(x) for x in rows))
    found = scan_observations(tmp_path, ClusterManifest())
    assert len(found) == 1
    assert "synthetic-token-value" not in found[0].text
    assert "synthetic-secret" not in found[0].text
    assert "/Users/fixture" not in found[0].text


def test_render_escapes_quotes_and_uses_hashed_member_ids(tmp_path):
    rows = [{"id": f"private-observation-id-{n}", "project_id": "fixture",
             "text": "The user's memory maintenance notes preserve consistent project context.",
             "created_at_epoch": 1788220800 + (86400 if n == 3 else 0)} for n in (1, 2, 3)]
    (tmp_path / "observations-20260901-120000.jsonl").write_text("\n".join(json.dumps(x) for x in rows))
    cluster = cluster_observations(scan_observations(tmp_path, ClusterManifest()))[0]
    content = render_merge_page(cluster)
    target = tmp_path / "merged.md"
    target.write_bytes(content)
    page = parse_page(target)
    assert page.frontmatter["abstract"] == rows[0]["text"]
    assert b"user''s" in content
    assert b"private-observation-id" not in content
    assert hashlib.sha256(rows[0]["id"].encode()).hexdigest().encode() in content
    assert "待核实" in content.decode()
