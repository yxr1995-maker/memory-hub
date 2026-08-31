"""Automatic successor planning and recoverable pair writes."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .frontmatter import parse_page, patch_frontmatter
from .indexer import IndexBuild
from .operation import FailureHook, TransactionContext, rollback_transaction
from .schema import PageDocument


class InjectedFailure(Exception):
    pass


class LifecycleOrderError(Exception):
    pass


@dataclass(frozen=True)
class CandidateScore:
    candidate: PageDocument
    semantic: float
    lexical: float
    entities_match: bool
    comparable_change: bool


@dataclass(frozen=True)
class SuccessorPlan:
    decision: str  # "successor" | "related-not-successor" | "independent"
    new_path: Path
    old_path: Path | None
    new_page: PageDocument
    old_page: PageDocument | None
    similarity: float
    entities_match: bool
    comparable_change: bool
    now: datetime


@dataclass(frozen=True)
class PreparedLifecycle:
    tx: TransactionContext
    plan: SuccessorPlan


@dataclass(frozen=True)
class LifecycleReport:
    result: str  # "committed" | "idempotent_skip" | "concurrent_successor"


class EmbeddingBackend:
    def similarity(self, doc1: PageDocument, doc2: PageDocument) -> float:
        return 0.0

    def entities_match(self, doc1: PageDocument, doc2: PageDocument) -> bool:
        return True

    def comparable_change(self, doc1: PageDocument, doc2: PageDocument) -> bool:
        return True


def lexical_successor_score(doc1: PageDocument, doc2: PageDocument) -> float:
    t1 = set(doc1.title.lower().split())
    t2 = set(doc2.title.lower().split())
    if not t1 or not t2:
        return 0.0
    return len(t1.intersection(t2)) / max(len(t1), len(t2))


def successor_plan(
    new_page: PageDocument,
    candidates: Sequence[PageDocument],
    embeddings: Any | None = None,
    now: datetime | None = None,
) -> SuccessorPlan:
    if now is None:
        now = datetime.now(timezone.utc)
    if not candidates:
        return SuccessorPlan(
            decision="independent",
            new_path=new_page.path,
            old_path=None,
            new_page=new_page,
            old_page=None,
            similarity=0.0,
            entities_match=False,
            comparable_change=False,
            now=now,
        )

    # Narrow down by same scope / namespace
    new_scope = new_page.frontmatter.get("scope", "project")
    new_scope_id = new_page.frontmatter.get("scope_id", "default-project")

    scored_candidates = []
    for cand in candidates:
        cand_scope = cand.frontmatter.get("scope", "project")
        cand_scope_id = cand.frontmatter.get("scope_id", "default-project")
        if (new_scope, new_scope_id) != (cand_scope, cand_scope_id):
            continue

        if embeddings:
            sim = embeddings.similarity(new_page, cand) if hasattr(embeddings, "similarity") else 0.0
            ent = embeddings.entities_match(new_page, cand) if hasattr(embeddings, "entities_match") else True
            chg = embeddings.comparable_change(new_page, cand) if hasattr(embeddings, "comparable_change") else True
        else:
            sim = lexical_successor_score(new_page, cand)
            ent = True
            chg = True

        scored_candidates.append(CandidateScore(cand, sim, sim, ent, chg))

    if not scored_candidates:
        return SuccessorPlan("independent", new_page.path, None, new_page, None, 0.0, False, False, now)

    best = max(scored_candidates, key=lambda s: s.semantic)
    if embeddings:
        if best.semantic >= 0.88 and best.entities_match and best.comparable_change:
            decision = "successor"
        elif 0.72 <= best.semantic < 0.88 or not best.entities_match:
            decision = "related-not-successor"
        else:
            decision = "independent"
    else:
        if best.lexical >= 0.92 and best.entities_match and best.comparable_change:
            decision = "successor"
        elif 0.72 <= best.lexical < 0.92:
            decision = "related-not-successor"
        else:
            decision = "independent"

    old_target = best.candidate.path if decision == "successor" else None
    old_page = best.candidate if decision == "successor" else None
    return SuccessorPlan(
        decision=decision,
        new_path=new_page.path,
        old_path=old_target,
        new_page=new_page,
        old_page=old_page,
        similarity=best.semantic,
        entities_match=best.entities_match,
        comparable_change=best.comparable_change,
        now=now,
    )


def validate_successor_graph(plan: SuccessorPlan, pages: Mapping[str, PageDocument]) -> None:
    if plan.decision != "successor" or not plan.old_path:
        return
    old_rel = str(plan.old_path)
    new_rel = str(plan.new_path)
    if old_rel == new_rel:
        raise ValueError("Cannot supersede self")
    
    # Check for cycles
    visited = {new_rel, old_rel}
    curr = old_rel
    while curr in pages:
        p = pages[curr]
        dep_by = str(p.frontmatter.get("deprecated_by", "")).strip(" '[]")
        if not dep_by:
            break
        if dep_by in visited:
            raise ValueError(f"Cycle detected in successor graph: {dep_by}")
        visited.add(dep_by)
        curr = dep_by


def prepare_successor_pages(plan: SuccessorPlan, tx: TransactionContext) -> PreparedLifecycle:
    tx.journal.register_lifecycle(plan)
    tx.inject("prepare.before_images")
    
    paths_to_save = [plan.new_path]
    if plan.old_path:
        paths_to_save.append(plan.old_path)
    tx.journal.save_before_images(paths_to_save)
    tx.journal.checkpoint("PREPARED")

    now_iso = plan.now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if plan.decision == "successor" and plan.old_path and plan.old_page:
        old_rel = plan.old_path.name
        new_rel = plan.new_path.name

        # Patch new page
        new_patch = {
            "status": "active",
            "supersedes": f"[[{old_rel}]]",
            "valid_at": now_iso,
        }
        new_bytes = patch_frontmatter(plan.new_page, new_patch)
        new_temp = tx.journal.write_verified_temp(plan.new_path, new_bytes)
        tx.journal.checkpoint("NEW_TEMP_VERIFIED")

        # Patch old page
        old_patch = {
            "status": "deprecated",
            "deprecated_by": f"[[{new_rel}]]",
            "invalid_at": now_iso,
        }
        old_bytes = patch_frontmatter(plan.old_page, old_patch)
        old_temp = tx.journal.write_verified_temp(plan.old_path, old_bytes)
        tx.journal.checkpoint("OLD_TEMP_VERIFIED")

        tx.inject("prepare.rename_new")
        if new_temp.is_file():
            os.replace(str(new_temp), str(plan.new_path))
        tx.journal.rename_new()
        tx.journal.checkpoint("NEW_RENAMED")

        tx.inject("prepare.rename_old")
        if old_temp.is_file():
            os.replace(str(old_temp), str(plan.old_path))
        tx.journal.rename_old()
        tx.journal.checkpoint("OLD_RENAMED")
    else:
        # Independent active page
        new_patch = {"status": "active", "valid_at": now_iso}
        new_bytes = patch_frontmatter(plan.new_page, new_patch)
        new_temp = tx.journal.write_verified_temp(plan.new_path, new_bytes)
        tx.journal.checkpoint("NEW_TEMP_VERIFIED")
        tx.inject("prepare.rename_new")
        if new_temp.is_file():
            os.replace(str(new_temp), str(plan.new_path))
        tx.journal.rename_new()
        tx.journal.checkpoint("NEW_RENAMED")

    return PreparedLifecycle(tx, plan)


def finalize_successor_after_index(prepared: PreparedLifecycle, tx: TransactionContext) -> LifecycleReport:
    if prepared.tx is not tx:
        raise ValueError("Transaction mismatch")
    if "INDEX_SWAPPED" not in tx.journal.checkpoints:
        raise LifecycleOrderError("Cannot finalize lifecycle before INDEX_SWAPPED")
    
    if "COMMITTED" in tx.journal.checkpoints:
        return LifecycleReport("idempotent_skip")

    tx.inject("finalize.verify_pair")
    tx.journal.checkpoint("COMMITTED")
    return LifecycleReport("committed")


def publish_successor_once(
    plan: SuccessorPlan,
    tx: TransactionContext,
    rebuild_index: Callable[[TransactionContext], IndexBuild],
) -> LifecycleReport:
    try:
        prepared = prepare_successor_pages(plan, tx)
        tx.inject("index.before_swap")
        rebuild_index(tx)
        tx.journal.checkpoint("INDEX_SWAPPED")
        tx.inject("index.after_swap")
        return finalize_successor_after_index(prepared, tx)
    except Exception:
        rollback_transaction(tx)
        raise
