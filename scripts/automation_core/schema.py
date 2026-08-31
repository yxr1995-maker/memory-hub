"""Core schema definitions for memory-hub automation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping
from uuid import UUID


class Mode(Enum):
    AUTO = "auto"
    SAFE = "safe"
    NO_AUTO = "no-auto"


@dataclass(frozen=True)
class OperationContext:
    operation_id: str
    command: str
    mode: Mode
    auto: bool
    apply: bool
    wiki_path: Path
    data_path: Path


@dataclass(frozen=True)
class PageDocument:
    path: Path
    title: str
    tags: list[str]
    frontmatter: Mapping[str, object]
    body: bytes
    frontmatter_lines: tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    id: str
    project_id: str
    provenance_id: str
    source: str
    source_uri: str
    agent_id: str | None
    cwd_hash: str
    text: str
    observed_at: str
    role: str = "unknown"
    type: str = "message"
    created_at_epoch: int = 0


@dataclass(frozen=True)
class ScopeAssignment:
    scope: str
    scope_id: str
    confidence: str
    source: str
    conflict: bool = False


@dataclass(frozen=True)
class IndexSchema:
    page_cols: tuple[str, ...]
    meta_cols: tuple[str, ...]
    supports_scope: bool
    supports_validity: bool


def normalize_id(value: str, fallback: str) -> str:
    def clean(candidate: str) -> str:
        candidate = re.sub(r"[^\u0000-\uFFFF]", "", candidate).strip().lower()
        return re.sub(r"[^a-z0-9._-]+", "-", candidate).strip("-")

    return clean(value) or clean(fallback) or "default"


def new_operation_id(now: datetime, uuid_factory: Callable[[], UUID]) -> str:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid_factory()}"
