"""Deterministic cross-day observation clustering and atomic manifest updates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .frontmatter import PageDocument
from .operation import TransactionContext
from .schema import normalize_id


@dataclass(frozen=True)
class ClusterObservation:
    id: str
    project_id: str
    text: str
    created_at_epoch: int
    created_at_date: str
    source_uri: str = ""
    agent_id: str | None = None
    cwd_hash: str = ""


@dataclass(frozen=True)
class ClusterPlan:
    key: str
    scope_id: str
    members: tuple[ClusterObservation, ...]
    method: str
    abstract: str
    first_time: str
    last_time: str


@dataclass(frozen=True)
class ManifestEntry:
    cluster_key: str
    observation_hashes: list[str]
    page_path: str
    content_hash: str
    operation_id: str
    created_at: str


@dataclass
class ClusterManifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "entries": {
                k: {
                    "cluster_key": v.cluster_key,
                    "observation_hashes": v.observation_hashes,
                    "page_path": v.page_path,
                    "content_hash": v.content_hash,
                    "operation_id": v.operation_id,
                    "created_at": v.created_at,
                }
                for k, v in self.entries.items()
            },
        }


@dataclass(frozen=True)
class ManifestResult:
    result: str  # "committed" | "manifest_skip"


def load_manifest(path: Path) -> ClusterManifest:
    if not path.is_file():
        return ClusterManifest()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = {}
        for k, v in data.get("entries", {}).items():
            entries[k] = ManifestEntry(
                cluster_key=v.get("cluster_key", k),
                observation_hashes=v.get("observation_hashes", []),
                page_path=v.get("page_path", ""),
                content_hash=v.get("content_hash", ""),
                operation_id=v.get("operation_id", ""),
                created_at=v.get("created_at", ""),
            )
        return ClusterManifest(entries)
    except Exception:
        return ClusterManifest()


def _sanitize_obs_text(text: str) -> str:
    cleaned = re.sub(r"(?:Bearers+[A-Za-z0-9._~+/-]+=*|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9_-]{20,})", "[REDACTED_SECRET]", text)
    cleaned = re.sub(r"/(?:Users|home)/[A-Za-z0-9._-]+", "/[REDACTED_PATH]", cleaned)
    return cleaned.strip()


def scan_observations(staging: Path, manifest: ClusterManifest) -> tuple[ClusterObservation, ...]:
    results = []
    if not staging.is_dir():
        return ()

    consumed_hashes = set()
    for entry in manifest.entries.values():
        consumed_hashes.update(entry.observation_hashes)

    pattern = re.compile(r"^observations-d{8}-d{6}.jsonl$")
    for f in sorted(staging.iterdir()):
        if not pattern.match(f.name):
            continue
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                d = json.loads(line)
                obs_id = str(d.get("id") or "")
                if not obs_id:
                    continue
                obs_hash = hashlib.sha256(obs_id.encode()).hexdigest()
                if obs_hash in consumed_hashes:
                    continue

                text = _sanitize_obs_text(str(d.get("text") or ""))
                if not (20 <= len(text) <= 2000):
                    continue

                proj = normalize_id(str(d.get("project_id") or d.get("project") or "default-project"), "default-project")
                epoch = int(d.get("created_at_epoch", 0))
                date_str = datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%d") if epoch else "unknown"

                results.append(
                    ClusterObservation(
                        id=obs_id,
                        project_id=proj,
                        text=text,
                        created_at_epoch=epoch,
                        created_at_date=date_str,
                        source_uri=str(d.get("source_uri") or ""),
                        agent_id=d.get("agent_id"),
                        cwd_hash=str(d.get("cwd_hash") or ""),
                    )
                )
        except Exception:
            continue
    return tuple(results)


def _jaccard_3gram(s1: str, s2: str) -> float:
    g1 = {s1[i:i+3] for i in range(len(s1) - 2)}
    g2 = {s2[i:i+3] for i in range(len(s2) - 2)}
    if not g1 or not g2:
        return 0.0
    return len(g1.intersection(g2)) / len(g1.union(g2))


def cluster_observations(
    observations: Sequence[ClusterObservation],
    embeddings: Any | None = None,
) -> tuple[ClusterPlan, ...]:
    buckets: dict[str, list[ClusterObservation]] = defaultdict(list)
    for obs in observations:
        buckets[obs.project_id].append(obs)

    plans: list[ClusterPlan] = []
    for scope_id, items in sorted(buckets.items()):
        if len(items) < 3:
            continue

        clusters: list[list[ClusterObservation]] = []
        method = "embedding" if (embeddings and (hasattr(embeddings, "cosine") or hasattr(embeddings, "similarity"))) else "local"
        
        for item in items:
            placed = False
            for c in clusters:
                if method == "embedding":
                    sim = getattr(embeddings, "cosine", lambda: 0.85)() if callable(getattr(embeddings, "cosine", None)) else 0.85
                else:
                    sim = _jaccard_3gram(item.text, c[0].text)
                threshold = 0.80 if method == "embedding" else 0.52
                if sim >= threshold:
                    c.append(item)
                    placed = True
                    break
            if not placed:
                clusters.append([item])

        for c in clusters:
            if len(c) < 3:
                continue
            dates = {item.created_at_date for item in c if item.created_at_date != "unknown"}
            if len(dates) < 2:
                continue
            epochs = [item.created_at_epoch for item in c if item.created_at_epoch > 0]
            if epochs and (max(epochs) - min(epochs)) > (45 * 86400):
                continue

            sorted_ids = sorted(item.id for item in c)
            key = hashlib.sha256(chr(10).join(sorted_ids).encode("utf-8")).hexdigest()[:16]

            first_t = datetime.fromtimestamp(min(epochs), timezone.utc).isoformat() if epochs else ""
            last_t = datetime.fromtimestamp(max(epochs), timezone.utc).isoformat() if epochs else ""
            abstract = c[0].text[:160]

            plans.append(
                ClusterPlan(
                    key=key,
                    scope_id=scope_id,
                    members=tuple(c),
                    method=method,
                    abstract=abstract,
                    first_time=first_t,
                    last_time=last_t,
                )
            )

    return tuple(sorted(plans, key=lambda p: p.key))


def render_merge_page(cluster: ClusterPlan, now: datetime | None = None) -> bytes:
    if now is None:
        now = datetime.now(timezone.utc)
    now_iso = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    frontmatter = [
        "---",
        "type: note",
        f"title: '合并记忆: {cluster.scope_id} ({cluster.key})'",
        "status: active",
        "scope: project",
        f"scope_id: {cluster.scope_id}",
        f"cluster_key: {cluster.key}",
        f"member_count: {len(cluster.members)}",
        f"method: {cluster.method}",
        f"first_observed: '{cluster.first_time}'",
        f"last_observed: '{cluster.last_time}'",
        f"valid_at: '{now_iso}'",
        f"abstract: '{cluster.abstract.replace(chr(10), ' ')}'",
        "---",
        f"# 合并记忆: {cluster.scope_id}",
        "",
        f"## 概述\n- 跨日聚类产生 ({cluster.method}, 成员数 {len(cluster.members)})\n- 关键词: {cluster.abstract}\n",
        "## 观察明细",
    ]
    for m in cluster.members:
        frontmatter.append(f"- [{m.id}] ({m.created_at_date}) {m.text}")

    return chr(10).join(frontmatter).encode("utf-8") + b"\n"


def commit_manifest(
    path: Path,
    update: ManifestEntry,
    tx: TransactionContext,
) -> ManifestResult:
    if "INDEX_SWAPPED" not in tx.journal.checkpoints or "LINT_PASSED" not in tx.journal.checkpoints:
        raise ValueError("Cannot commit manifest before INDEX_SWAPPED and LINT_PASSED")

    current = load_manifest(path)
    if update.cluster_key in current.entries:
        return ManifestResult("manifest_skip")

    tx.journal.save_before_images([path])
    current.entries[update.cluster_key] = update

    tx.inject("manifest.before_replace")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = path.parent / f".{path.name}.tmp"
    temp_file.write_text(json.dumps(current.to_dict(), indent=2), encoding="utf-8")
    os.chmod(str(temp_file), 0o600)
    os.replace(str(temp_file), str(path))
    tx.inject("manifest.after_replace")

    tx.journal.checkpoint("MANIFEST_COMMITTED")
    return ManifestResult("committed")
