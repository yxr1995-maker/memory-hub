"""Centralized weighted RRF, scope filtering, and lifecycle-aware ranking."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .indexer import IndexedPage
from .query_planner import QueryPlan, SearchRequest


K_RRF = 60
DEFAULT_TAU = 90.0

_CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1}
_STATUS_ORDER = {"active": 4, "candidate": 3, "lifecycle_error": 2, "deprecated": 1}


@dataclass(frozen=True)
class RecallHit:
    path: str
    score: float
    rank: int = 1


@dataclass(frozen=True)
class SearchResult:
    path: str
    score: float
    status: str
    scope: str
    scope_id: str
    scope_confidence: str
    title: str
    abstract: str
    type: str
    rank_reason: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "score": round(self.score, 4),
            "status": self.status,
            "scope": self.scope,
            "scope_id": self.scope_id,
            "scope_confidence": self.scope_confidence,
            "title": self.title,
            "abstract": self.abstract,
            "type": self.type,
            "rank_reason": self.rank_reason,
        }


@dataclass(frozen=True)
class LifecycleResolution:
    original_path: str
    resolved_path: str
    status: str
    via: str | None = None
    cycle: bool = False
    error: str | None = None


class MetricSink:
    def __init__(self) -> None:
        self.metrics: dict[str, int] = defaultdict(int)

    def increment(self, name: str, amount: int = 1) -> None:
        self.metrics[name] += amount

    def value(self, name: str) -> int:
        return self.metrics.get(name, 0)


class NullMetrics(MetricSink):
    pass


def _parse_ts(value: str) -> float | None:
    if not value:
        return None
    v = value.strip().strip("'")
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def normalized_weights(plan: QueryPlan) -> dict[str, float]:
    if not plan.expansions:
        return {"original": 1.0}
    total_conf = sum(t.confidence for t in plan.expansions)
    weights = {"original": 0.70}
    if total_conf > 0:
        for index, term in enumerate(plan.expansions):
            weights[f"expansion_{index}"] = round(0.30 * (term.confidence / total_conf), 4)
    else:
        weights = {"original": 1.0}
    return weights


_WIKILINK_RE = re.compile(r"\[\[(.*?)\]\]")


def _extract_wikilink_target(text: str) -> str:
    m = _WIKILINK_RE.search(text)
    if m:
        target = m.group(1).strip()
        if not target.endswith(".md"):
            target += ".md"
        return target
    t = text.strip()
    if t and not t.endswith(".md"):
        t += ".md"
    return t


def resolve_successor(
    path: str,
    pages: Mapping[str, IndexedPage],
) -> LifecycleResolution:
    curr = path
    visited = [curr]
    while True:
        page = pages.get(curr)
        if not page:
            return LifecycleResolution(path, curr, "lifecycle_error", error="missing_page")
        if page.status != "deprecated":
            via = "successor" if curr != path else None
            return LifecycleResolution(path, curr, page.status, via=via)
        
        dep_by = getattr(page, "deprecated_by", "") or ""
        if not dep_by:
            m = re.search(r"deprecated_by:\s*['\"]?(\[\[.*?\]\]|\S+)['\"]?", page.content)
            if m:
                dep_by = m.group(1)
        if not dep_by:
            return LifecycleResolution(path, curr, "deprecated", via=None)
        
        next_target = _extract_wikilink_target(dep_by)
        if not next_target or next_target in visited:
            return LifecycleResolution(path, path, "lifecycle_error", cycle=True, error="cycle")
        visited.append(next_target)
        curr = next_target


def accumulate_rrf(
    recalls: Mapping[str, Sequence[RecallHit]],
    weights: Mapping[str, float],
    k: int = K_RRF,
) -> dict[str, float]:
    scores: dict[str, float] = defaultdict(float)
    for source_key, hits in recalls.items():
        weight = 1.0
        if source_key.startswith("expansion_") and source_key in weights:
            weight = weights[source_key]
        elif source_key.startswith("original") and "original" in weights:
            weight = weights["original"]
        elif source_key in weights:
            weight = weights[source_key]

        for index, hit in enumerate(hits):
            rank = hit.rank if hit.rank > 0 else (index + 1)
            scores[hit.path] += weight * (1.0 / (k + rank))
    return dict(scores)


def rank_results(
    request: SearchRequest,
    plan: QueryPlan,
    recalls: Mapping[str, Sequence[RecallHit]],
    pages: Mapping[str, IndexedPage],
    metrics: MetricSink | None = None,
    tau: float = DEFAULT_TAU,
) -> tuple[SearchResult, ...]:
    if metrics is None:
        metrics = NullMetrics()

    weights = normalized_weights(plan)
    raw_scores = accumulate_rrf(recalls, weights, k=K_RRF)
    now_epoch = datetime.now(timezone.utc).timestamp()

    # Map raw paths to resolved targets, accumulating scores
    target_scores: dict[str, float] = defaultdict(float)
    target_resolutions: dict[str, LifecycleResolution] = {}
    target_reasons: dict[str, dict[str, Any]] = {}

    for path, base_score in raw_scores.items():
        res = resolve_successor(path, pages)
        if res.cycle:
            metrics.increment("memory_hub_lifecycle_cycle_total")
        target = res.resolved_path
        target_scores[target] += base_score
        if target not in target_resolutions or res.via == "successor":
            target_resolutions[target] = res
            target_reasons[target] = {
                "base_score": round(target_scores[target], 4),
                "status": res.status,
                "via": res.via,
            }

    valid_candidates: list[SearchResult] = []
    error_candidates: list[SearchResult] = []

    for target_path, base_score in target_scores.items():
        page = pages.get(target_path)
        if not page:
            page = IndexedPage(
                path=target_path, title=target_path.split("/")[-1].replace(".md", ""),
                type="unknown", tags="", abstract="", content="",
                scope="project", scope_id="default-project", scope_confidence="low", status="active",
                updated="", last_verified="", valid_at="", invalid_at=""
            )
        
        score = base_score
        if page.type in ("entity", "concept"):
            score *= 1.8
        elif page.type in ("atom", "query", "draft"):
            score *= 0.7

        ts = _parse_ts(page.valid_at or page.updated)
        if tau and tau > 0 and ts is not None:
            decay_factor = math.exp(-max(0.0, (now_epoch - ts) / 86400.0) / tau)
            score *= decay_factor

        res = target_resolutions.get(target_path, LifecycleResolution(target_path, target_path, page.status))
        reason = target_reasons.get(target_path, {"base_score": round(base_score, 4), "status": res.status, "via": res.via})
        reason["final_score"] = round(score, 4)

        req_scope = request.scope.value if hasattr(request.scope, "value") else request.scope
        if req_scope and page.scope != str(req_scope).lower():
            continue
        if request.scope_id and page.scope_id != str(request.scope_id):
            continue

        item = SearchResult(
            path=target_path,
            score=score,
            status=res.status,
            scope=page.scope,
            scope_id=page.scope_id,
            scope_confidence=page.scope_confidence,
            title=page.title,
            abstract=page.abstract,
            type=page.type,
            rank_reason=reason,
        )
        if res.status in ("active", "candidate"):
            valid_candidates.append(item)
        else:
            error_candidates.append(item)

    def _sort_key(item: SearchResult) -> tuple[int, float, int, float, str]:
        page = pages.get(item.path)
        ts = _parse_ts(page.valid_at or page.updated) if page else 0.0
        return (
            -_STATUS_ORDER.get(item.status, 0),
            -item.score,
            -_CONFIDENCE_ORDER.get(item.scope_confidence, 0),
            -(ts or 0.0),
            item.path,
        )

    valid_candidates.sort(key=_sort_key)
    error_candidates.sort(key=_sort_key)

    combined = valid_candidates + error_candidates
    return tuple(combined[:request.top])


def render_human(results: Sequence[SearchResult]) -> str:
    lines = []
    for r in results:
        lines.append(f"[{r.score:.3f}] {r.path}")
    return "\n".join(lines)

