"""Shared contracts for the memory-hub automation pipeline."""
from __future__ import annotations

from .schema import (
    Mode,
    Observation,
    OperationContext,
    PageDocument,
    ScopeAssignment,
    new_operation_id,
    normalize_id,
)
from .frontmatter import parse_page, patch_frontmatter
from .provenance import normalize_observation, render_observation_report, normalize_jsonl

__all__ = [
    "Mode",
    "Observation",
    "OperationContext",
    "PageDocument",
    "ScopeAssignment",
    "new_operation_id",
    "normalize_id",
    "normalize_jsonl",
    "normalize_observation",
    "parse_page",
    "patch_frontmatter",
    "render_observation_report",
]
