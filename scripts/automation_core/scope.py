"""Deterministic scope inference and resumable frontmatter backfill."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .frontmatter import parse_page, patch_frontmatter
from .schema import OperationContext, PageDocument, ScopeAssignment, normalize_id


_SCOPES = {"user", "project", "agent"}
_CONFIDENCES = {"high", "medium", "low"}
_SOURCES = {"explicit", "session_meta", "path", "content", "fallback"}
_EXTERNAL_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


@dataclass(frozen=True)
class Fingerprint:
    inode: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class BackfillEntry:
    path: Path
    relative_path: str
    planned_fingerprint: Fingerprint
    assignment: ScopeAssignment
    original_bytes_hash: str

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class BackfillIssue:
    relative_path: str
    result: str
    error_category: str


@dataclass(frozen=True)
class BackfillPlan:
    entries: tuple[BackfillEntry, ...]
    next_cursor: str | None
    issues: tuple[BackfillIssue, ...] = ()


@dataclass(frozen=True)
class BackfillReport:
    counts: dict[str, int]
    next_cursor: str | None
    report_path: Path


@dataclass(frozen=True)
class _Evidence:
    scope: str
    scope_id: str
    confidence: str
    source: str
    priority: int
    score: float
    strong: bool


def _assignment(evidence: _Evidence, conflict: bool) -> ScopeAssignment:
    return ScopeAssignment(
        evidence.scope,
        evidence.scope_id,
        evidence.confidence,
        evidence.source,
        conflict,
    )


def _valid_explicit(frontmatter: Mapping[str, object]) -> ScopeAssignment | None:
    scope = str(frontmatter.get("scope", "")).strip()
    scope_id = str(frontmatter.get("scope_id", "")).strip()
    if scope not in _SCOPES or not scope_id:
        return None
    return ScopeAssignment(scope, normalize_id(scope_id, "default"), "high", "explicit", False)


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _marked_agent(page: PageDocument, provenance: Mapping[str, object]) -> bool:
    values = (
        page.frontmatter.get("scope_hint"),
        page.frontmatter.get("page_rule"),
        provenance.get("scope_hint"),
        provenance.get("page_rule"),
    )
    return any(str(value).strip().lower() == "agent" for value in values) or bool(
        provenance.get("agent_rule")
    )


def _project_candidates(provenance: Mapping[str, object]) -> list[tuple[str, float]]:
    candidates = _strings(provenance.get("project_candidates"))
    raw_scores = provenance.get("scores", [])
    scores: list[float] = []
    if isinstance(raw_scores, Sequence) and not isinstance(raw_scores, (str, bytes, bytearray)):
        for value in raw_scores:
            try:
                scores.append(float(value))
            except (TypeError, ValueError):
                scores.append(0.0)
    result = []
    for index, candidate in enumerate(candidates):
        result.append((normalize_id(candidate, "default-project"), scores[index] if index < len(scores) else 0.0))
    return result


def infer_scope(
    page: PageDocument,
    provenance: Mapping[str, object],
    user_scope_id: str,
) -> ScopeAssignment:
    """Apply the six-level scope table and always return an assignment."""
    explicit = _valid_explicit(page.frontmatter)
    if explicit is not None:
        return explicit

    evidence: list[_Evidence] = []
    agent_raw = page.frontmatter.get("agent_id") or provenance.get("agent_id")
    if agent_raw and _marked_agent(page, provenance):
        evidence.append(
            _Evidence("agent", normalize_id(str(agent_raw), "agent"), "high", "session_meta", 2, 1.0, True)
        )

    normalized_user = normalize_id(user_scope_id, "default-user")
    path_parts = {part.lower() for part in page.path.parts}
    user_path = "user" in path_parts
    body_text = page.body.decode("utf-8", errors="ignore")
    user_content = bool(
        page.frontmatter.get("global_user_preference")
        or provenance.get("global_user_preference")
        or provenance.get("user_scope_reference") == user_scope_id
        or (user_scope_id and user_scope_id in body_text)
    )
    if user_path or user_content:
        evidence.append(
            _Evidence(
                "user", normalized_user, "high", "path" if user_path else "content", 3, 1.0, True
            )
        )

    direct_projects = []
    for value in (
        page.frontmatter.get("project"),
        page.frontmatter.get("project_id"),
        provenance.get("project"),
        provenance.get("project_id"),
        provenance.get("session_project"),
    ):
        if value:
            direct_projects.append(normalize_id(str(value), "default-project"))
    for project_id in sorted(set(direct_projects)):
        evidence.append(
            _Evidence("project", project_id, "high", "session_meta", 4, 1.0, True)
        )

    ranked = _project_candidates(provenance)
    if ranked:
        best_score = max(score for _, score in ranked)
        best_ids = sorted({project_id for project_id, score in ranked if score == best_score})
        runner_up = max((score for _, score in ranked if score < best_score), default=float("-inf"))
        if len(best_ids) > 1:
            for project_id in best_ids:
                evidence.append(
                    _Evidence("project", project_id, "medium", "path", 4, best_score, True)
                )
        elif runner_up == float("-inf") or best_score - runner_up >= 0.20:
            evidence.append(
                _Evidence("project", best_ids[0], "medium", "path", 5, best_score, False)
            )

    if not evidence:
        return ScopeAssignment("project", "default-project", "low", "fallback", False)

    winner = min(evidence, key=lambda item: (item.priority, -item.score, item.scope, item.scope_id))
    tied = [item for item in evidence if (item.priority, item.score) == (winner.priority, winner.score)]
    conflict = len({(item.scope, item.scope_id) for item in evidence if item.strong}) > 1
    if len({(item.scope, item.scope_id) for item in tied}) > 1:
        winner = min(tied, key=lambda item: (item.scope_id, item.scope))
        winner = _Evidence(
            winner.scope, winner.scope_id, "medium", winner.source, winner.priority, winner.score, winner.strong
        )
    return _assignment(winner, conflict)


def _validate_relative(value: str, *, label: str) -> str:
    if "\x00" in value or _EXTERNAL_URI.match(value):
        raise ValueError(f"invalid {label}")
    pure = PurePosixPath(value)
    if pure.is_absolute() or not value or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"invalid {label}")
    return pure.as_posix()


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def fingerprint(path: Path) -> Fingerprint:
    if path.is_symlink() or not path.is_file():
        raise ValueError("unsafe path")
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("unsafe path")
    content = path.read_bytes()
    return Fingerprint(info.st_ino, info.st_mtime_ns, hashlib.sha256(content).hexdigest())


def plan_backfill(
    wiki: Path,
    cursor: str | None,
    limit: int | None,
    ctx: OperationContext,
) -> BackfillPlan:
    wiki = wiki.resolve(strict=True)
    if wiki != ctx.wiki_path.resolve(strict=True):
        raise ValueError("wiki does not match operation context")
    if cursor is not None:
        cursor = _validate_relative(cursor, label="cursor")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")

    paths = sorted(wiki.rglob("*.md"), key=lambda item: item.relative_to(wiki).as_posix())
    selected: list[tuple[Path, str]] = []
    for path in paths:
        relative = path.relative_to(wiki).as_posix()
        if cursor is not None and relative <= cursor:
            continue
        selected.append((path, relative))
        if limit is not None and len(selected) >= limit:
            break

    entries: list[BackfillEntry] = []
    issues: list[BackfillIssue] = []
    user_scope_id = os.environ.get("MEMORY_HUB_USER_SCOPE_ID", "default-user")
    for path, relative in selected:
        try:
            _validate_relative(relative, label="path")
            if path.is_symlink() or not _under_root(path, wiki):
                raise ValueError("unsafe path")
            planned = fingerprint(path)
            document = parse_page(path)
        except (OSError, UnicodeError, ValueError) as exc:
            result = "unsafe_path" if "unsafe path" in str(exc) else "malformed_frontmatter"
            issues.append(BackfillIssue(relative, result, type(exc).__name__))
            continue
        if _valid_explicit(document.frontmatter) is not None:
            continue
        assignment = infer_scope(document, document.frontmatter, user_scope_id)
        entries.append(BackfillEntry(path, relative, planned, assignment, planned.sha256))

    next_cursor = selected[-1][1] if selected else cursor
    return BackfillPlan(tuple(entries), next_cursor, tuple(issues))


def _scope_fields(assignment: ScopeAssignment) -> dict[str, object]:
    if assignment.scope not in _SCOPES or assignment.confidence not in _CONFIDENCES or assignment.source not in _SOURCES:
        raise ValueError("invalid scope assignment")
    return {
        "scope": assignment.scope,
        "scope_id": assignment.scope_id,
        "scope_confidence": assignment.confidence,
        "scope_source": assignment.source,
        "scope_conflict": assignment.conflict,
    }


def atomic_replace_same_dir(path: Path, content: bytes) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("unsafe path")
    mode = stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(content)
            sink.flush()
            os.fsync(sink.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _report_path(ctx: OperationContext) -> Path:
    reports = ctx.data_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    return reports / f"scope-{ctx.operation_id}.jsonl"


def _append_record(path: Path, record: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as sink:
        sink.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        sink.flush()
        os.fsync(sink.fileno())


def _before_path(ctx: OperationContext, entry: BackfillEntry) -> Path:
    root = ctx.data_path / "transactions" / ctx.operation_id / "scope-before"
    relative = PurePosixPath(entry.relative_path)
    target = root.joinpath(*relative.parts).with_suffix(Path(relative.name).suffix + ".before")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _save_before_image(ctx: OperationContext, entry: BackfillEntry, content: bytes) -> Path:
    target = _before_path(ctx, entry)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as sink:
            sink.write(content)
            sink.flush()
            os.fsync(sink.fileno())
    except BaseException:
        os.close(descriptor)
        raise
    journal = ctx.data_path / "transactions" / ctx.operation_id / "scope-journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    _append_record(journal, {"path": entry.relative_path, "sha256": entry.original_bytes_hash})
    return target


def apply_backfill(plan: BackfillPlan, ctx: OperationContext) -> BackfillReport:
    report_path = _report_path(ctx)
    counts: Counter[str] = Counter()
    for issue in plan.issues:
        counts[issue.result] += 1
        _append_record(
            report_path,
            {"path": issue.relative_path, "result": issue.result, "error_category": issue.error_category},
        )
    if not ctx.apply:
        for entry in plan.entries:
            counts["planned"] += 1
            _append_record(
                report_path,
                {"path": entry.relative_path, "result": "planned", "sha256": entry.original_bytes_hash,
                 "scope": entry.assignment.scope, "scope_id": entry.assignment.scope_id},
            )
        return BackfillReport(dict(counts), plan.next_cursor, report_path)

    written: list[tuple[BackfillEntry, Path]] = []
    try:
        for entry in plan.entries:
            if not _under_root(entry.path, ctx.wiki_path) or fingerprint(entry.path) != entry.planned_fingerprint:
                counts["concurrent_change"] += 1
                _append_record(report_path, {"path": entry.relative_path, "result": "concurrent_change"})
                continue
            document = parse_page(entry.path)
            rendered = patch_frontmatter(document, _scope_fields(entry.assignment))
            if rendered == entry.path.read_bytes():
                counts["unchanged"] += 1
                _append_record(report_path, {"path": entry.relative_path, "result": "unchanged"})
                continue
            before = _save_before_image(ctx, entry, entry.path.read_bytes())
            atomic_replace_same_dir(entry.path, rendered)
            written.append((entry, before))
            counts["written"] += 1
            _append_record(
                report_path,
                {"path": entry.relative_path, "result": "written", "before_sha256": entry.original_bytes_hash,
                 "after_sha256": hashlib.sha256(rendered).hexdigest(), "scope": entry.assignment.scope,
                 "scope_id": entry.assignment.scope_id, "confidence": entry.assignment.confidence,
                 "source": entry.assignment.source, "conflict": entry.assignment.conflict},
            )
    except Exception as exc:
        _append_record(report_path, {"result": "write_failure", "error_category": type(exc).__name__})
        for entry, before in reversed(written):
            atomic_replace_same_dir(entry.path, before.read_bytes())
            _append_record(report_path, {"path": entry.relative_path, "result": "rolled_back"})
        raise
    return BackfillReport(dict(counts), plan.next_cursor, report_path)


__all__ = [
    "BackfillEntry",
    "BackfillIssue",
    "BackfillPlan",
    "BackfillReport",
    "apply_backfill",
    "atomic_replace_same_dir",
    "fingerprint",
    "infer_scope",
    "plan_backfill",
]
