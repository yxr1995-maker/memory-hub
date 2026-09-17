"""Pipeline orchestration for default-auto run and maintain operations.

run stages execute the real capture -> distill -> publish -> index ->
commit -> archive chain via the existing scripts. maintain is scoped to
lifecycle/cluster maintenance; stages without a real implementation fail
loudly instead of reporting a fake success.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .indexer import IndexBuild, atomic_rebuild_index
from .cluster import ManifestEntry, classify_cluster_target, cluster_observations, commit_manifest, load_manifest, render_merge_page, scan_observations
from .lifecycle import LifecycleReport, PreparedLifecycle, finalize_successor_after_index, prepare_successor_pages, successor_plan
from .scope import apply_backfill, plan_backfill
from .operation import AutomationLock, GitBaseline, LockBusy, OwnedPath, StageReport, TransactionContext, commit_exact, rollback_transaction, stage_exact
from .schema import Mode, OperationContext
from .schema import PageDocument
from .frontmatter import parse_page, patch_frontmatter
from .wiki_fixer import report_path, render_report, run_report
from .cluster import ClusterManifest


RUN_ORDER = (
    "validate",
    "capture",
    "aggregate",
    "scope_backfill",
    "distill",
    "publish_pages",
    "index_swap",
    "maintain",
    "exact_stage_commit",
    "archive",
)

RUN_CHECKPOINTS = (
    "VALIDATED",
    "CAPTURED",
    "AGGREGATED",
    "SCOPE_BACKFILLED",
    "DISTILLED",
    "PAGES_PUBLISHED",
    "INDEX_SWAPPED",
    "MAINTAINED",
    "STAGE_COMMITTED",
    "ARCHIVED",
)

MAINTAIN_ORDER = (
    "validate",
    "publish_pages_lifecycle",
    "index_swap",
    "wiki_fixer_report",
    "lint",
    "atomic_manifest_commit",
    "archive",
    "exact_stage_commit",
)

MAINTAIN_CHECKPOINTS = (
    "VALIDATED",
    "PAGES_LIFECYCLE_PUBLISHED",
    "INDEX_SWAPPED",
    "WIKI_FIXER_REPORTED",
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
    def ok_outcome(cls, checkpoint_owned: bool = False, message: str = "", data: dict[str, Any] | None = None) -> StageOutcome:
        return cls(ok=True, checkpoint_owned=checkpoint_owned, message=message, data=data or {})

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
        return StageOutcome.fail_outcome(f"{stage_name}: not implemented")

    def index_swap_once_and_finalize(self, tx: TransactionContext) -> StageOutcome:
        tx.inject("index.before_swap")
        atomic_rebuild_index(tx.operation.wiki_path, tx.operation.data_path)
        tx.journal.checkpoint("INDEX_SWAPPED")
        tx.inject("index.after_swap")
        return StageOutcome.ok_outcome(checkpoint_owned=True)


HUB_ROOT = Path(__file__).resolve().parents[2]


class RunStageRunner(StageRunner):
    """Real stage implementations for the run pipeline.

    Every stage either performs a verifiable side effect (capture file,
    staging pages, published wiki pages, rebuilt index, commit, archived
    observations), reports an explicit skip, or fails loudly.
    """

    def __init__(self, staging: Path | None = None, *, llm: bool = False) -> None:
        self.llm = llm
        self.pending_clusters: list[ManifestEntry] = []
        self.staging = staging or Path(os.environ.get("MEMORY_HUB_STAGING", str(HUB_ROOT / "staging")))

    def index_swap_once_and_finalize(self, tx: TransactionContext) -> StageOutcome:
        tx.journal.save_before_images([tx.operation.data_path / "index.db"])
        outcome = super().index_swap_once_and_finalize(tx)
        manifest_path = tx.operation.data_path / "cluster-manifest.json"
        for entry in self.pending_clusters:
            commit_manifest(manifest_path, entry, tx)
        self.pending_clusters.clear()
        return outcome

    def _script(self, name: str) -> Path:
        return HUB_ROOT / "scripts" / name

    def _script_env(self, tx: TransactionContext) -> dict[str, str]:
        env = dict(os.environ)
        env["WIKI_PATH"] = str(tx.operation.wiki_path)
        env["MEMORY_HUB_DATA"] = str(tx.operation.data_path)
        env["MEMORY_HUB_STAGING"] = str(self.staging)
        return env

    def _run_script(self, name: str, args: Sequence[str], tx: TransactionContext) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(self._script(name)), *args],
            env=self._script_env(tx),
            capture_output=True,
            text=True,
        )

    def _count_pages(self) -> int:
        pages_dir = self.staging / "pages"
        if not pages_dir.is_dir():
            return 0
        return len(list(pages_dir.glob("*.md")))

    def _stage_commit_paths(self) -> list[str]:
        file_path = self.staging / ".published-this-run"
        if not file_path.is_file():
            return []
        paths: set[str] = set()
        for line in file_path.read_text(encoding="utf-8").splitlines():
            slug, directory, rel_target, status = (line.split("|") + ["", "", "", ""])[:4]
            if not slug:
                continue
            # publish.sh records the file name, which already carries ".md".
            slug_path = slug if slug.endswith(".md") else f"{slug}.md"
            if status == "candidate" and rel_target:
                paths.add(rel_target)
            elif directory:
                paths.add(f"{directory}/{slug_path}")
        if paths:
            paths.update({"index.md", "log.md"})
        return sorted(paths)

    def _load_owned(self, tx: TransactionContext) -> list[OwnedPath]:
        owned = []
        for rel in self._stage_commit_paths():
            target = tx.operation.wiki_path / rel
            if target.is_file():
                owned.append(OwnedPath(relative=rel, after_hash=hashlib.sha256(target.read_bytes()).hexdigest()))
        return owned

    def _latest_observations(self) -> Path | None:
        candidates = sorted(self.staging.glob("observations-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0] if candidates else None

    def plan(self, stage_name: str, tx: TransactionContext) -> StageOutcome:
        # SAFE mode: never write; every stage is a dry-run inspection.
        if stage_name == "validate":
            if not tx.operation.wiki_path.is_dir():
                return StageOutcome.fail_outcome(f"wiki not found: {tx.operation.wiki_path}")
            return StageOutcome.ok_outcome(message="validate: wiki present")
        if stage_name == "index_swap":
            return StageOutcome.ok_outcome(message="index_swap: skipped in safe mode")
        if stage_name in ("capture", "aggregate", "scope_backfill", "distill", "maintain", "exact_stage_commit"):
            return StageOutcome.ok_outcome(message=f"{stage_name}: plan only")
        if stage_name == "publish_pages":
            proc = self._run_script("publish.sh", [], tx)
            if proc.returncode != 0:
                return StageOutcome.fail_outcome(f"publish dry-run failed: {proc.stderr.strip()[:500]}")
            return StageOutcome.ok_outcome(message="publish_pages: dry-run ok")
        if stage_name == "archive":
            proc = self._run_script("archive.sh", [], tx)
            if proc.returncode != 0:
                return StageOutcome.fail_outcome(f"archive dry-run failed: {proc.stderr.strip()[:500]}")
            return StageOutcome.ok_outcome(message="archive: dry-run ok")
        return StageOutcome.fail_outcome(f"unknown stage: {stage_name}")

    def apply(self, stage_name: str, tx: TransactionContext) -> StageOutcome:
        if stage_name == "validate":
            if not tx.operation.wiki_path.is_dir():
                return StageOutcome.fail_outcome(f"wiki not found: {tx.operation.wiki_path}")
            return StageOutcome.ok_outcome(message="validate: wiki present")
        if stage_name == "capture":
            proc = self._run_script("capture.sh", [], tx)
            latest = self._latest_observations()
            if proc.returncode != 0:
                return StageOutcome.fail_outcome(f"capture failed: {proc.stderr.strip()[:500]}")
            return StageOutcome.ok_outcome(
                message=f"capture: latest={latest.name if latest else 'none'}",
                data={"observations": latest.name if latest else ""},
            )
        if stage_name == "aggregate":
            manifest_path = tx.operation.data_path / "cluster-manifest.json"
            manifest = load_manifest(manifest_path)
            observations = scan_observations(self.staging, manifest)
            plans = cluster_observations(observations)
            if not tx.operation.apply:
                return StageOutcome.ok_outcome(message=f"aggregate: {len(plans)} plans planned", data={"clusters": len(plans)})
            committed = 0
            written_paths: list[str] = []
            skipped: list[str] = []
            for plan in plans:
                content = render_merge_page(plan)
                rel = f"moc/cluster-{plan.key}.md"
                target = tx.operation.wiki_path / rel
                if classify_cluster_target(target, content, manifest.entries.get(plan.key)) != "write":
                    skipped.append(rel)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                tx.journal.save_before_images([target])
                target.write_bytes(content)
                entry = ManifestEntry(plan.key, [hashlib.sha256(o.id.encode()).hexdigest() for o in plan.members], rel, hashlib.sha256(content).hexdigest(), tx.operation.operation_id, datetime.now(timezone.utc).isoformat())
                self.pending_clusters.append(entry)
                committed += 1
                written_paths.append(rel)
            self.aggregate_paths = tuple(written_paths)
            return StageOutcome.ok_outcome(message=f"aggregate: {committed} merged pages, {len(skipped)} skipped", data={"clusters": committed, "skipped": skipped})
        if stage_name == "scope_backfill":
            if not tx.operation.apply:
                return StageOutcome.ok_outcome(message="scope_backfill: planned only", data={"backfilled": 0})
            plan = plan_backfill(tx.operation.wiki_path, None, None, tx.operation)
            tx.journal.save_before_images([entry.path for entry in plan.entries])
            report = apply_backfill(plan, tx.operation)
            updated = int(report.counts.get("updated", report.counts.get("applied", 0)))
            # Only files this run actually wrote may enter the commit whitelist.
            self.scope_paths = tuple(report.written_paths)
            return StageOutcome.ok_outcome(message=f"scope_backfill: {updated} pages", data={"backfilled": updated})
        if stage_name == "distill":
            proc = self._run_script("distill.sh", ["--llm"] if self.llm else [], tx)
            pages = self._count_pages()
            if proc.returncode != 0:
                return StageOutcome.fail_outcome(f"distill failed: {proc.stderr.strip()[:500]}")
            return StageOutcome.ok_outcome(message=f"distill: {pages} staging pages", data={"staging_pages": pages})
        if stage_name == "publish_pages":
            args = ["--apply"] if tx.operation.apply else []
            proc = self._run_script("publish.sh", args, tx)
            if proc.returncode != 0:
                return StageOutcome.fail_outcome(f"publish failed: {proc.stderr.strip()[:500]}")
            return StageOutcome.ok_outcome(message=f"publish: {'apply' if tx.operation.apply else 'dry-run'} ok")
        if stage_name == "maintain":
            if not tx.operation.apply:
                return StageOutcome.ok_outcome(message="maintain: planned only", data={"maintained": 0})
            return StageOutcome.ok_outcome(message="maintain: skipped (run standalone maintain)", data={"status": "skipped", "maintained": 0})
        if stage_name == "exact_stage_commit":
            if not tx.operation.apply:
                return StageOutcome.ok_outcome(message="stage_commit: skipped (no apply)")
            wiki = tx.operation.wiki_path
            if not (wiki / ".git").exists():
                return StageOutcome.ok_outcome(message="stage_commit: wiki is not a git repository", data={"commit": "not-a-repository"})
            owned = self._load_owned(tx)
            extra = set(getattr(self, 'aggregate_paths', ())) | set(getattr(self, 'scope_paths', ()))
            for rel in sorted(extra):
                target = wiki / rel
                if target.is_file() and rel not in {o.relative for o in owned}:
                    owned.append(OwnedPath(relative=rel, after_hash=hashlib.sha256(target.read_bytes()).hexdigest()))
            # Sanity check: only files that the pipeline just wrote may be committed.
            if not tx.operation.apply and not owned:
                return StageOutcome.ok_outcome(message="stage_commit: nothing to commit", data={"commit": "nothing"})
            if tx.operation.apply and not owned and not extra:
                return StageOutcome.fail_outcome("stage_commit: nothing owned to commit — pipeline produced no wiki changes")
            stage = stage_exact(wiki, tx, owned)
            if stage.result != "exact":
                return StageOutcome.fail_outcome(f"stage_exact failed: {stage.result}")
            commit = commit_exact(wiki, tx, stage)
            if commit.result != "committed":
                return StageOutcome.fail_outcome(f"commit failed: {commit.result}")
            return StageOutcome.ok_outcome(message=f"stage_commit: {commit.commit_hash}", data={"commit": commit.commit_hash})
        if stage_name == "archive":
            args = ["--apply"] if tx.operation.apply else []
            proc = self._run_script("archive.sh", args, tx)
            if proc.returncode != 0:
                return StageOutcome.fail_outcome(f"archive failed: {proc.stderr.strip()[:500]}")
            return StageOutcome.ok_outcome(message="archive: ok")
        return StageOutcome.fail_outcome(f"unknown stage: {stage_name}")


class MaintainStageRunner(StageRunner):
    """One transaction for timestamp repairs and cross-day cluster publication."""

    def __init__(self, staging: Path | None = None, *, commit: bool = True) -> None:
        self.staging = (staging or Path(os.environ.get("MEMORY_HUB_STAGING", str(HUB_ROOT / "staging")))).resolve()
        self.commit = commit
        self.repairs: dict[Path, bytes] = {}
        self.originals: dict[Path, bytes] = {}
        self.plans: list[tuple[Any, Any]] = []
        self.prepared: list[PreparedLifecycle] = []
        self.owned: dict[str, str] = {}
        self.pending: list[ManifestEntry] = []
        self.manifest = ClusterManifest()
        self.manifest_path: Path | None = None
        self.index_swaps = 0
        self.skipped_manual: list[str] = []

    def _target(self, root: Path, path: Path) -> Path:
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("maintenance path escapes its root")
        cursor = path
        while cursor != root and cursor != cursor.parent:
            if cursor.is_symlink():
                raise ValueError("maintenance refuses symlink paths")
            cursor = cursor.parent
        return path

    def _validate(self, tx: TransactionContext) -> StageOutcome:
        wiki, data = tx.operation.wiki_path, tx.operation.data_path
        if not wiki.is_dir():
            return StageOutcome.fail_outcome("wiki not found")
        roots = [wiki.resolve(), data.resolve(), self.staging]
        if any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(roots) for b in roots[i + 1:]):
            return StageOutcome.fail_outcome("wiki, data and staging roots must be separate")
        dirty = set(tx.journal.baseline.unstaged)
        if (wiki / ".git").exists():
            untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                                       cwd=wiki, capture_output=True, text=True, check=True).stdout
            dirty.update(p for p in untracked.split("\0") if p)
        candidates = []
        for path in sorted(wiki.rglob("*.md")):
            rel = path.relative_to(wiki).as_posix()
            parts = path.relative_to(wiki).parts
            if any(part.startswith(".") for part in parts) or {"raw", "_legacy-para", "_archive"}.intersection(parts) or rel in dirty:
                continue
            self._target(wiki, path)
            if not path.read_bytes().startswith(b"---\n"):
                continue
            page = parse_page(path)
            candidates.append(page)
            stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d")
            patch = {key: stamp for key in ("created", "updated") if not page.frontmatter.get(key)}
            if patch:
                self.originals[path] = path.read_bytes()
                self.repairs[path] = patch_frontmatter(page, patch)
        self.manifest_path = self._target(data, data / "manifests" / "cluster-observations-v1.json")
        for path in (self.manifest_path, data / "cluster-manifest.json"):
            self._target(data, path)
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict) or not isinstance(raw.get("entries"), dict):
                    raise ValueError("invalid cluster manifest")
                loaded = load_manifest(path)
                if len(loaded.entries) != len(raw["entries"]):
                    raise ValueError("invalid cluster manifest entries")
                self.manifest.entries.update(loaded.entries)
        if tx.operation.auto or tx.operation.mode is Mode.SAFE:
            observations = scan_observations(self.staging, self.manifest)
            for cluster in cluster_observations(observations):
                content = render_merge_page(cluster)
                target = self._target(wiki, wiki / "moc" / f"cluster-{cluster.key}.md")
                decision = classify_cluster_target(target, content, self.manifest.entries.get(cluster.key))
                if decision == "idempotent":
                    continue
                if decision == "manual":
                    self.skipped_manual.append(target.relative_to(wiki).as_posix())
                    continue
                header, body = content[4:].split(b"\n---\n", 1)
                fields = {"scope": "project", "scope_id": cluster.scope_id, "status": "active",
                          "title": f"合并记忆: {cluster.scope_id} ({cluster.key})"}
                page = PageDocument(target, str(fields["title"]), [], fields, body,
                                    tuple(header.decode().splitlines()))
                # Title-only similarity is insufficient evidence to retire an old page.
                # Only identical scope/title/body candidates enter the existing planner.
                eligible = [p for p in candidates if p.title == page.title and p.body == page.body
                            and p.frontmatter.get("status", "active") == "active"]
                plan = successor_plan(page, eligible)
                self.plans.append((cluster, plan))
        return StageOutcome.ok_outcome(data={"repairs": len(self.repairs), "clusters": len(self.plans), "skipped": list(self.skipped_manual)},
                                       message=f"planned {len(self.repairs)} timestamp repairs, {len(self.plans)} clusters")

    def plan(self, stage_name: str, tx: TransactionContext) -> StageOutcome:
        if stage_name == "validate":
            return self._validate(tx)
        if stage_name == "wiki_fixer_report":
            return self._wiki_fixer_preview(tx)
        return StageOutcome.ok_outcome(message="preview only", data={"status": "planned"})

    def _wiki_fixer_preview(self, tx: TransactionContext) -> StageOutcome:
        # Read-only: counts to stdout via the stage message, no files written.
        dead, stale = run_report(tx.operation.wiki_path, tx.operation.data_path)
        found = sum(len(v) for v in dead.values())
        return StageOutcome.ok_outcome(
            message=f"wiki_fixer_report: {found} dead links in {len(dead)} pages, "
                    f"{len(stale)} stale pages (report only)",
            data={"dead_links": found, "dead_pages": len(dead), "stale_pages": len(stale)},
        )

    def _record_owned(self, path: Path, tx: TransactionContext) -> None:
        self.owned[path.relative_to(tx.operation.wiki_path).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        tx.journal.record_after_images([path])

    def index_swap_once_and_finalize(self, tx: TransactionContext) -> StageOutcome:
        if self.index_swaps:
            return StageOutcome.fail_outcome("index swap already executed")
        path = self._target(tx.operation.data_path, tx.operation.data_path / "index.db")
        tx.journal.save_before_images([path])
        tx.inject("index.before_swap")
        build = atomic_rebuild_index(tx.operation.wiki_path, tx.operation.data_path)
        self.index_swaps += 1
        tx.journal.record_after_images([path])
        tx.journal.checkpoint("INDEX_SWAPPED")
        tx.inject("index.after_swap")
        for prepared in self.prepared:
            finalize_successor_after_index(prepared, tx)
        return StageOutcome.ok_outcome(checkpoint_owned=True,
            data={"swaps": self.index_swaps, "pages": build.page_count}, message="index rebuilt and swapped once")

    def apply(self, stage_name: str, tx: TransactionContext) -> StageOutcome:
        wiki = tx.operation.wiki_path
        if stage_name == "validate":
            return self._validate(tx)
        if stage_name == "publish_pages_lifecycle":
            for path, content in self.repairs.items():
                if path.read_bytes() != self.originals[path]:
                    raise ValueError("repair target changed after planning")
                tx.journal.save_before_images([path])
                path.write_bytes(content)
                self._record_owned(path, tx)
            for cluster, plan in self.plans:
                target = self._target(wiki, plan.new_path)
                content = render_merge_page(cluster)
                if classify_cluster_target(target, content, self.manifest.entries.get(cluster.key)) != "write":
                    self.skipped_manual.append(target.relative_to(wiki).as_posix())
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                temp = target.parent / f".{target.name}.tmp"
                if temp.exists() or temp.is_symlink():
                    raise ValueError("lifecycle temp path is occupied")
                tx.journal.save_before_images([temp])
                prepared = prepare_successor_pages(plan, tx)
                self.prepared.append(prepared)
                self._record_owned(target, tx)
                if plan.old_path:
                    self._record_owned(plan.old_path, tx)
                self.pending.append(ManifestEntry(cluster.key,
                    [hashlib.sha256(m.id.encode()).hexdigest() for m in cluster.members],
                    target.relative_to(wiki).as_posix(), self.owned[target.relative_to(wiki).as_posix()],
                    tx.operation.operation_id, datetime.now(timezone.utc).isoformat()))
            return StageOutcome.ok_outcome(data={"repairs": len(self.repairs), "published": len(self.pending), "skipped": list(self.skipped_manual)})
        if stage_name == "wiki_fixer_report":
            # Read-only inspection: write the report file only, never touch wiki
            # files, never record owned paths, never write the journal.
            dead, stale = run_report(wiki, tx.operation.data_path)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            target = report_path(tx.operation.data_path, day)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render_report(dead, stale, day), encoding="utf-8")
            found = sum(len(v) for v in dead.values())
            return StageOutcome.ok_outcome(
                message=f"wiki_fixer_report: {found} dead links in {len(dead)} pages, "
                        f"{len(stale)} stale pages -> {target.name}",
                data={"dead_links": found, "dead_pages": len(dead),
                      "stale_pages": len(stale), "report": target.name},
            )
        if stage_name == "lint":
            with sqlite3.connect(f"file:{tx.operation.data_path / 'index.db'}?mode=ro", uri=True) as db:
                if db.execute("pragma integrity_check").fetchone()[0] != "ok":
                    raise ValueError("index integrity check failed")
                for rel, expected in self.owned.items():
                    path = self._target(wiki, wiki / rel)
                    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                        raise ValueError("owned page changed before lint")
                    page = parse_page(path)
                    row = db.execute("select status from pages where path=?", (rel,)).fetchone()
                    expected_status = page.frontmatter.get("status") or "active"
                    if expected_status == "fresh":
                        expected_status = "active"
                    elif expected_status not in {"active", "deprecated", "candidate"}:
                        expected_status = "candidate"
                    if not row or row[0] != expected_status:
                        raise ValueError("indexed page lifecycle differs from wiki")
            for prepared in self.prepared:
                plan = prepared.plan
                if plan.old_path:
                    old, new = parse_page(plan.old_path), parse_page(plan.new_path)
                    if (old.frontmatter.get("deprecated_by") != f"[[{plan.new_path.name}]]"
                        or new.frontmatter.get("supersedes") != f"[[{plan.old_path.name}]]"):
                        raise ValueError("invalid successor pair")
            return StageOutcome.ok_outcome(data={"verified_pages": len(self.owned)})
        if stage_name == "atomic_manifest_commit":
            if "LINT_PASSED" not in tx.journal.checkpoints or self.manifest_path is None:
                raise ValueError("manifest requires lint")
            for entry in self.pending:
                temp = self.manifest_path.parent / f".{self.manifest_path.name}.tmp"
                if temp.exists() or temp.is_symlink():
                    raise ValueError("manifest temp path is occupied")
                tx.journal.save_before_images([temp])
                commit_manifest(self.manifest_path, entry, tx)
                tx.journal.record_after_images([self.manifest_path])
                self.manifest.entries[entry.cluster_key] = entry
            return StageOutcome.ok_outcome(data={"consumed_clusters": len(self.pending),
                "manifest_skip": len(self.manifest.entries) - len(self.pending)})
        if stage_name == "archive":
            count = 0
            consumed = {h for entry in self.manifest.entries.values() for h in entry.observation_hashes}
            if tx.operation.auto and self.staging.is_dir():
                for source in sorted(self.staging.iterdir()):
                    if not re.fullmatch(r"observations-\d{8}-\d{6}\.jsonl", source.name):
                        continue
                    self._target(self.staging, source)
                    try:
                        rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
                        hashes = [hashlib.sha256(str(row["id"]).encode()).hexdigest() for row in rows]
                    except (ValueError, KeyError, TypeError):
                        continue
                    if not hashes or not all(h in consumed for h in hashes):
                        continue
                    target = self._target(self.staging, self.staging / "archive" / source.name)
                    if target.exists():
                        raise ValueError("archive target is occupied")
                    tx.journal.save_before_images([source, target])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(source, target)
                    tx.journal.record_after_images([source, target])
                    count += 1
            return StageOutcome.ok_outcome(data={"archived_files": count})
        if stage_name == "exact_stage_commit":
            if not self.commit:
                return StageOutcome.ok_outcome(data={"commit": "disabled"})
            if not (wiki / ".git").exists():
                return StageOutcome.ok_outcome(data={"commit": "not-a-repository"})
            if not self.owned:
                return StageOutcome.ok_outcome(data={"commit": "nothing"})
            stage = stage_exact(wiki, tx, [OwnedPath(rel, digest) for rel, digest in sorted(self.owned.items())])
            if stage.result != "exact":
                raise ValueError(f"exact staging failed: {stage.result}")
            report = commit_exact(wiki, tx, stage)
            if report.result != "committed":
                raise ValueError(f"exact commit failed: {report.result}")
            return StageOutcome.ok_outcome(data={"commit": report.commit_hash})
        return StageOutcome.fail_outcome(f"unknown maintenance stage: {stage_name}")


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
    names: list[str] = []
    checkpoints: list[str] = []
    data: dict[str, Any] = {}
    print(f"mode={operation.mode.value} apply={str(operation.apply).lower()} operation_id={operation.operation_id}")
    if tx.journal.baseline.staged:
        report = OperationReport("preexisting_staged", [], [], error="preexisting staged changes", operation_id=operation.operation_id)
        _write_run_report(tx, report, data)
        return report
    try:
        for name, checkpoint in zip(MAINTAIN_ORDER, MAINTAIN_CHECKPOINTS, strict=True):
            names.append(name)
            if operation.apply:
                tx.inject(f"maint.{name}.before")
                outcome = stages.index_swap_once_and_finalize(tx) if name == "index_swap" else stages.apply(name, tx)
            else:
                outcome = stages.plan(name, tx)
            if not outcome.ok:
                raise RuntimeError(outcome.message)
            data[name] = outcome.data
            if outcome.message:
                print(f"  [{name}] {outcome.message}")
            if operation.apply:
                tx.inject(f"maint.{name}.after")
            if not outcome.checkpoint_owned:
                tx.journal.checkpoint(checkpoint)
            checkpoints.append(checkpoint)
        commit = data.get("exact_stage_commit", {}).get("commit", "")
        result = "safe" if not operation.apply else "applied_no_commit"
        if commit and commit not in ("nothing", "disabled", "not-a-repository"):
            result = "committed"
        report = OperationReport(result, names, checkpoints, operation_id=operation.operation_id)
    except Exception as exc:
        try:
            rollback = rollback_transaction(tx, force=True)
            data["rollback"] = {"success": rollback.success, "restored_count": len(rollback.restored_paths),
                                "conflict_count": len(rollback.conflict_paths)}
        except Exception as recovery_error:
            data["rollback"] = {"success": False, "error_type": type(recovery_error).__name__}
        report = OperationReport("failed", names, checkpoints, names[-1] if names else None,
                                 str(exc), operation.operation_id)
    _write_run_report(tx, report, data)
    print(f"maintain result={report.result}")
    return report


def run_pipeline(tx: TransactionContext, stages: StageRunner) -> OperationReport:
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
    stage_data: dict[str, Any] = {}

    try:
        for name, checkpoint in zip(RUN_ORDER, RUN_CHECKPOINTS, strict=True):
            completed_stages.append(name)
            if operation.mode is Mode.SAFE:
                outcome = stages.plan(name, tx)
            elif name == "index_swap":
                outcome = stages.index_swap_once_and_finalize(tx)
            else:
                outcome = stages.apply(name, tx)

            if not outcome.ok:
                rollback_transaction(tx)
                report = OperationReport(
                    result="failed",
                    stage_names=completed_stages,
                    checkpoints=checkpoints,
                    failed_stage=name,
                    error=outcome.message,
                    operation_id=operation.operation_id,
                )
                _write_run_report(tx, report, stage_data)
                return report

            if outcome.message:
                print(f"  [{name}] {outcome.message}")
            stage_data[name] = outcome.data
            if not outcome.checkpoint_owned:
                tx.journal.checkpoint(checkpoint)
            checkpoints.append(checkpoint)

        commit_hash = str(stage_data.get("exact_stage_commit", {}).get("commit", ""))
        if operation.apply:
            if commit_hash and commit_hash not in ("nothing", "not-a-repository"):
                result = "committed"
            elif commit_hash in ("nothing", "not-a-repository"):
                result = "applied_no_commit"
            else:
                result = "applied_no_commit"
        else:
            result = "safe"

        report = OperationReport(
            result=result,
            stage_names=completed_stages,
            checkpoints=checkpoints,
            operation_id=operation.operation_id,
        )
        _write_run_report(tx, report, stage_data)
        print(f"run result={report.result} commit={commit_hash or '-'}")
        return report
    except Exception as exc:
        rollback_transaction(tx)
        report = OperationReport(
            result="failed",
            stage_names=completed_stages,
            checkpoints=checkpoints,
            failed_stage=completed_stages[-1] if completed_stages else None,
            error=str(exc),
            operation_id=operation.operation_id,
        )
        _write_run_report(tx, report, stage_data)
        return report


def _write_run_report(tx: TransactionContext, report: OperationReport, stage_data: dict[str, Any]) -> Path:
    reports_dir = tx.operation.data_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{tx.operation.command}-{report.operation_id}.json"
    # 待审统计与 /api/pending 同口径，持久化进 run/maintain 报告（M6 评审 #7）。
    # OperationReport 保持 frozen 不动，只在报告写出层附加。
    try:
        from scripts.server import api_pending as _api_pending
        _pend_items = _api_pending()["items"]
        _pending: dict[str, Any] = {"total": len(_pend_items)}
        for _it in _pend_items:
            _pending[_it["source"]] = _pending.get(_it["source"], 0) + 1
    except Exception as exc:
        _pending = {"error": str(exc)[:200]}
    payload = {
        "operation_id": report.operation_id,
        "command": tx.operation.command,
        "mode": tx.operation.mode.value,
        "apply": tx.operation.apply,
        "result": report.result,
        "stage_names": report.stage_names,
        "checkpoints": report.checkpoints,
        "failed_stage": report.failed_stage,
        "error": report.error,
        "pending": _pending,
        "stage_data": stage_data,
        "written_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    # Keep a stable pointer for the dashboard /api/overview.
    latest = reports_dir / "latest-operation.json"
    tmp = reports_dir / ".latest-operation.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(str(tmp), str(latest))
    return path
