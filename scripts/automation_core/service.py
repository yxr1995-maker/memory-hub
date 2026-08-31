"""Unified search and ask service for memory-hub."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .indexer import IndexedPage, load_page_records
from .query_planner import (
    AuditSink,
    ExpansionTerm,
    L0Snippet,
    QueryPlan,
    SearchRequest,
    plan_query,
)
from .ranker import (
    DEFAULT_TAU,
    MetricSink,
    NullMetrics,
    RecallHit,
    SearchResult,
    rank_results,
    render_human,
)


@dataclass(frozen=True)
class SearchResponse:
    request: SearchRequest
    plan: dict[str, Any]
    results: tuple[SearchResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.request.query,
            "plan": self.plan,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass(frozen=True)
class AskContext:
    results: tuple[SearchResult, ...]
    pages: dict[str, IndexedPage]
    context_text: str
    answer: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_paths": [r.path for r in self.results],
            "answer": self.answer,
            "results": [r.to_dict() for r in self.results],
        }


class SqliteRecallBackend:
    def __init__(self, wiki_path: Path, data_path: Path, hub_path: Path) -> None:
        self.wiki_path = wiki_path
        self.data_path = data_path
        self.hub_path = hub_path
        self.db_path = data_path / "index.db"

    def _ngram_match(self, text: str) -> str:
        terms = []
        for w in text.split():
            for w2 in w.replace("-", " ").split():
                if len(w2) >= 3:
                    clean = re.sub(r'[*|&~()"]', " ", w2).strip()
                    if clean:
                        ngrams = [clean[i:i+3] for i in range(len(clean) - 2)]
                        if ngrams:
                            terms.append(f"({' AND '.join(ngrams)})")
        return " AND ".join(terms)

    def fts(self, query: str, limit: int = 12) -> Sequence[RecallHit]:
        if not self.db_path.is_file():
            return self._rg_fallback(query, limit)
        match_expr = self._ngram_match(query)
        if not match_expr:
            return self._rg_fallback(query, limit)
        try:
            with sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True) as con:
                sql = "SELECT path, bm25(pages), abstract, content FROM pages WHERE pages MATCH ? ORDER BY bm25(pages) LIMIT ?"
                rows = con.execute(sql, (match_expr, limit)).fetchall()
                if not rows:
                    return self._rg_fallback(query, limit)
                return [RecallHit(r[0], float(r[1]), rank=idx+1) for idx, r in enumerate(rows)]
        except Exception:
            return self._rg_fallback(query, limit)

    def vector(self, query: str, limit: int = 12) -> Sequence[RecallHit]:
        embed_script = self.hub_path / "scripts" / "embed.py"
        if not embed_script.is_file():
            return []
        try:
            env = dict(os.environ, WIKI_PATH=str(self.wiki_path), MEMORY_HUB_DATA=str(self.data_path))
            out = subprocess.run(
                [sys.executable, str(embed_script), "search", query, "-n", str(limit)],
                capture_output=True, text=True, env=env, timeout=10
            ).stdout
            hits = []
            for line in out.splitlines():
                m = re.match(r"^\[(-?\d*\.?\d+)\]\s+(\S+?\.md)", line.strip())
                if m:
                    hits.append(RecallHit(m.group(2), float(m.group(1)), rank=len(hits)+1))
            return hits
        except Exception:
            return []

    def _rg_fallback(self, query: str, limit: int = 12) -> Sequence[RecallHit]:
        if not self.wiki_path.is_dir():
            return []
        try:
            cmd = ["rg", "-i", "-l", query, str(self.wiki_path), "-g", "*.md", "-g", "!**/raw/**", "-g", "!**/_legacy-para/**"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            hits = []
            for line in out.splitlines():
                if line.strip():
                    rel = Path(line.strip()).relative_to(self.wiki_path).as_posix()
                    hits.append(RecallHit(rel, 1.0, rank=len(hits)+1))
                    if len(hits) >= limit:
                        break
            return hits
        except Exception:
            return []


class MemoryService:
    def __init__(self, wiki_path: Path, data_path: Path, hub_path: Path) -> None:
        self.wiki_path = wiki_path.resolve()
        self.data_path = data_path.resolve()
        self.hub_path = hub_path.resolve()
        self.db_path = self.data_path / "index.db"
        self.recall = SqliteRecallBackend(self.wiki_path, self.data_path, self.hub_path)
        self.audit = AuditSink(str(self.data_path / "reports" / "query-plan.jsonl"))
        self.metrics = MetricSink()

    def _load_pages(self) -> dict[str, IndexedPage]:
        return load_page_records(self.db_path) if self.db_path.is_file() else {}

    def search(self, request: SearchRequest, tau: float = DEFAULT_TAU) -> SearchResponse:
        # Validate request
        if len(request.query) > 500:
            raise ValueError("Query exceeds 500 characters")
        if request.top < 1 or request.top > 50:
            raise ValueError("Top must be between 1 and 50")
        if request.scope and request.scope not in ("user", "project", "agent"):
            raise ValueError("Invalid scope")

        plan = plan_query(request, self.recall, audit=self.audit)

        recalls: dict[str, Sequence[RecallHit]] = {}
        if request.fuse:
            recalls["original_fts"] = self.recall.fts(request.query, 15)
            recalls["original_vec"] = self.recall.vector(request.query, 15)
            for idx, term in enumerate(plan.expansions):
                recalls[f"expansion_{idx}_fts"] = self.recall.fts(term.text, 10)
                recalls[f"expansion_{idx}_vec"] = self.recall.vector(term.text, 10)
        else:
            recalls["original_fts"] = self.recall.fts(request.query, request.top)
            for idx, term in enumerate(plan.expansions):
                recalls[f"expansion_{idx}_fts"] = self.recall.fts(term.text, request.top)

        pages = self._load_pages()
        results = rank_results(request, plan, recalls, pages, self.metrics, tau=tau)
        self.audit.finish(plan, final_hits=len(results))

        return SearchResponse(request=request, plan=plan.public_explain(request), results=results)

    def ask_context(self, request: SearchRequest) -> AskContext:
        response = self.search(request)
        pages = self._load_pages()
        loaded = {r.path: pages.get(r.path) for r in response.results if r.path in pages}

        # Build context
        blocks = []
        for r in response.results[:3]:
            p = pages.get(r.path)
            title = p.title if p else r.title
            abstract = p.abstract if p else r.abstract
            body = (p.content[:800] if p else "").replace("\n", " ")
            blocks.append(f"页面 {r.path}（{title}）\n摘要: {abstract}\n正文: {body}")
        
        context_text = "\n\n".join(blocks)
        answer = None
        proxy_url = os.environ.get("OPENCODEX_URL", "http://127.0.0.1:10100/v1")
        model = os.environ.get("CLAUDE_MEM_MODEL", "sensenova/sensenova-6.8-flash-lite")
        if context_text.strip():
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是知识库问答助手。基于给定页面内容回答问题，只基于页面内容，不要编造。回答后标注来源页面（用 path）。简洁中文。"},
                    {"role": "user", "content": f"问题: {request.query}\n\n相关页面:\n{context_text}"},
                ],
                "temperature": 0.2,
            }
            try:
                import urllib.request
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(f"{proxy_url}/chat/completions", data=data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=1) as resp:
                    if resp.status == 200:
                        parsed = json.loads(resp.read().decode("utf-8"))
                        answer = parsed.get("choices", [{}])[0].get("message", {}).get("content", "")
            except Exception:
                answer = None

        return AskContext(results=response.results, pages=loaded, context_text=context_text, answer=answer)

