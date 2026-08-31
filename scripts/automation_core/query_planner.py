"""Two-stage L0 recall and LLM/local query planner."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .schema import normalize_id


_CREDENTIAL_PATTERN = re.compile(r"(?:Bearers+[A-Za-z0-9._~+/-]+=*|ghp_[A-Za-z0-9]{36}|sk-[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{32,})")
_PATH_PATTERN = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    top: int = 10
    fuse: bool = True
    expand: bool = True
    scope: str | None = None
    scope_id: str | None = None
    explain: bool = False


@dataclass(frozen=True)
class L0Snippet:
    path: str
    text: str


@dataclass(frozen=True)
class ExpansionTerm:
    text: str
    confidence: float


@dataclass(frozen=True)
class QueryPlan:
    query: str
    query_hash: str
    expansions: tuple[ExpansionTerm, ...]
    planner: str  # "llm" | "local" | "original-only"
    fallback_reason: str | None
    l0_snippets: tuple[L0Snippet, ...]
    latency_ms: float

    def public_explain(self, request: SearchRequest | None = None) -> dict[str, object]:
        return {
            "query": self.query,
            "query_hash": self.query_hash,
            "planner": self.planner,
            "fallback_reason": self.fallback_reason,
            "expansions": [{"term": t.text, "confidence": t.confidence} for t in self.expansions],
            "expand": request.expand if request else bool(self.expansions),
            "fuse": request.fuse if request else True,
        }


class RecallHitProtocol(Protocol):
    path: str
    abstract: str
    content: str


class RecallBackend(Protocol):
    def fts(self, query: str, limit: int = 12) -> Sequence[RecallHitProtocol]: ...
    def vector(self, query: str, limit: int = 12) -> Sequence[RecallHitProtocol]: ...


class HttpTransport(Protocol):
    def post_json(self, url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout: float) -> tuple[int, str]: ...


class AuditSink:
    def __init__(self, output_path: str | None = None) -> None:
        self.output_path = output_path
        self.records: list[dict[str, Any]] = []
        self.last: dict[str, Any] = {}
        self.raw_text: str = ""

    def record(self, entry: dict[str, Any]) -> None:
        self.last = dict(entry)
        self.records.append(entry)
        serialized = json.dumps(entry, ensure_ascii=False)
        self.raw_text += serialized + "\n"
        if self.output_path:
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            with open(self.output_path, "a", encoding="utf-8") as f:
                f.write(serialized + "\n")

    def finish(self, plan: QueryPlan, final_hits: int = 0) -> None:
        self.record({
            "query_hash": plan.query_hash,
            "candidate_paths": [s.path for s in plan.l0_snippets],
            "expansions": [{"term": t.text, "confidence": t.confidence} for t in plan.expansions],
            "planner": plan.planner,
            "fallback_reason": plan.fallback_reason,
            "latency_ms": plan.latency_ms,
            "final_hits": final_hits,
        })


def sanitize_l0(text: str, max_chars: int = 320) -> str:
    cleaned = _CREDENTIAL_PATTERN.sub("[REDACTED_SECRET]", text)
    cleaned = _PATH_PATTERN.sub("/[REDACTED_PATH]", cleaned)
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars]


def _first_nonempty_paragraph(content: str) -> str:
    for block in content.split("\n\n"):
        b = block.strip()
        if b and not b.startswith("---"):
            return b
    return content[:320]


def collect_l0(query: str, recall: RecallBackend) -> tuple[L0Snippet, ...]:
    fts_hits = list(recall.fts(query, 12)) if recall else []
    vec_hits = list(recall.vector(query, 12)) if recall else []
    seen = set()
    candidates = []
    for hit in fts_hits + vec_hits:
        if hit.path not in seen:
            seen.add(hit.path)
            candidates.append(hit)
    snippets = []
    for c in candidates[:6]:
        abstract_text = getattr(c, "abstract", "") or _first_nonempty_paragraph(getattr(c, "content", ""))
        snippets.append(L0Snippet(c.path, sanitize_l0(abstract_text, 320)))
    return tuple(snippets)


def valid_term(term: str, query: str) -> bool:
    t = term.strip()
    if not (2 <= len(t) <= 64):
        return False
    if t.lower() == query.strip().lower():
        return False
    if any(c in t for c in "\x00\n\r\t"):
        return False
    if "://" in t or _PATH_PATTERN.search(t) or _CREDENTIAL_PATTERN.search(t):
        return False
    return True


def dedupe_terms(terms: Sequence[ExpansionTerm]) -> list[ExpansionTerm]:
    seen = set()
    result = []
    for item in terms:
        key = item.text.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def validate_expansions(raw: Any, query: str) -> tuple[ExpansionTerm, ...]:
    if not isinstance(raw, list) or len(raw) > 4:
        return ()
    checked = []
    for x in raw:
        if isinstance(x, dict) and "term" in x and "confidence" in x:
            try:
                term_str = str(x["term"])
                conf = float(x["confidence"])
                if valid_term(term_str, query):
                    checked.append(ExpansionTerm(term_str, conf))
            except (TypeError, ValueError):
                continue
    deduped = dedupe_terms(checked)
    return tuple(sorted(deduped, key=lambda t: (-t.confidence, t.text)))


def local_expand(query: str, snippets: Sequence[L0Snippet], limit: int = 4) -> tuple[ExpansionTerm, ...]:
    corpus = " ".join(s.text for s in snippets)
    if not corpus.strip():
        return ()
    
    words = re.findall(r"[\u4e00-\u9fa5]{2,8}|[A-Za-z0-9_-]{2,30}", corpus)
    counts = Counter(words)
    candidates = []
    q_lower = query.lower()
    for word, count in counts.items():
        if not valid_term(word, query):
            continue
        score = float(count)
        if any(c in q_lower for c in word.lower()):
            score *= 1.5
        candidates.append((word, score))
    
    candidates.sort(key=lambda item: (-item[1], item[0]))
    terms = []
    for word, score in candidates[:limit]:
        conf = min(1.0, max(0.1, round(score / max(1.0, counts.most_common(1)[0][1] if counts else 1.0), 2)))
        terms.append(ExpansionTerm(word, conf))
    return tuple(terms)


class OpenAICompatiblePlanner:
    def __init__(self, proxy_url: str, model: str, transport: HttpTransport | None = None) -> None:
        self.proxy_url = proxy_url.rstrip("/")
        self.model = model
        self.transport = transport

    def expand(
        self,
        query: str,
        query_hash: str,
        snippets: Sequence[L0Snippet],
        connect_timeout: float = 0.5,
        read_timeout: float = 1.0,
    ) -> tuple[tuple[ExpansionTerm, ...], str | None]:
        if not snippets:
            return (), "empty_l0"
        context_text = "\n".join(f"- {s.text}" for s in snippets)
        sys_prompt = 'You are a query expansion planner. Given a query and L0 snippet abstracts, output a JSON array of up to 4 search keyword objects: [{"term": "keyword", "confidence": 0.9}]. Output JSON only.'
        user_prompt = f"Query: {query}\nContext abstracts:\n{context_text}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        url = f"{self.proxy_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        try:
            if self.transport:
                status, body = self.transport.post_json(url, payload, headers, read_timeout)
            else:
                import urllib.request
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=read_timeout) as resp:
                    status = resp.status
                    body = resp.read().decode("utf-8", errors="replace")
            
            if status != 200:
                return (), f"http_{status}"
            parsed = json.loads(body)
            content = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
            match = re.search(r"\[.*?\]", content, re.DOTALL)
            if not match:
                return (), "invalid_json"
            raw_list = json.loads(match.group(0))
            expansions = validate_expansions(raw_list, query)
            if not expansions:
                return (), "invalid_json"
            return expansions, None
        except Exception as exc:
            err_name = type(exc).__name__.lower()
            if "timeout" in err_name:
                return (), "read_timeout"
            return (), "invalid_json"


def plan_query(
    request: SearchRequest,
    recall: RecallBackend,
    transport: HttpTransport | None = None,
    audit: AuditSink | None = None,
) -> QueryPlan:
    start_time = time.monotonic()
    query_hash = hashlib.sha256(request.query.strip().encode("utf-8")).hexdigest()
    
    if not request.expand:
        plan = QueryPlan(
            query=request.query,
            query_hash=query_hash,
            expansions=(),
            planner="original-only",
            fallback_reason="expand_disabled",
            l0_snippets=(),
            latency_ms=round((time.monotonic() - start_time) * 1000, 2),
        )
        if audit:
            audit.finish(plan, 0)
        return plan

    snippets = collect_l0(request.query, recall)
    if not snippets:
        plan = QueryPlan(
            query=request.query,
            query_hash=query_hash,
            expansions=(),
            planner="original-only",
            fallback_reason="empty_l0",
            l0_snippets=(),
            latency_ms=round((time.monotonic() - start_time) * 1000, 2),
        )
        if audit:
            audit.finish(plan, 0)
        return plan

    proxy_url = os.environ.get("OPENCODEX_URL", "http://127.0.0.1:10100/v1")
    model = os.environ.get("CLAUDE_MEM_EXPAND_MODEL", "sensenova/sensenova-6.8-flash-lite")
    planner_client = OpenAICompatiblePlanner(proxy_url, model, transport=transport)

    llm_expansions, failure_reason = planner_client.expand(request.query, query_hash, snippets)
    if llm_expansions:
        plan = QueryPlan(
            query=request.query,
            query_hash=query_hash,
            expansions=llm_expansions,
            planner="llm",
            fallback_reason=None,
            l0_snippets=snippets,
            latency_ms=round((time.monotonic() - start_time) * 1000, 2),
        )
    else:
        local_terms = local_expand(request.query, snippets, limit=4)
        plan = QueryPlan(
            query=request.query,
            query_hash=query_hash,
            expansions=local_terms,
            planner="local" if local_terms else "original-only",
            fallback_reason=failure_reason or "local_fallback",
            l0_snippets=snippets,
            latency_ms=round((time.monotonic() - start_time) * 1000, 2),
        )

    if audit:
        audit.finish(plan, 0)
    return plan

