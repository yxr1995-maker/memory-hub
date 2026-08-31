#!/usr/bin/env python3
"""CLI entry points for deterministic automation operations."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from scripts.automation_core.query_planner import SearchRequest
from scripts.automation_core.ranker import DEFAULT_TAU, render_human
from scripts.automation_core.schema import Mode, OperationContext, new_operation_id
from scripts.automation_core.scope import apply_backfill, plan_backfill
from scripts.automation_core.service import MemoryService


def _get_service() -> MemoryService:
    hub = Path(__file__).resolve().parents[1]
    wiki = Path(os.environ.get("WIKI_PATH", str(Path.home() / "llm-wiki"))).resolve()
    data = Path(os.environ.get("MEMORY_HUB_DATA", str(Path.home() / ".memory-hub"))).resolve()
    return MemoryService(wiki, data, hub)


def _scope_backfill(args: argparse.Namespace) -> int:
    wiki = Path(os.environ.get("WIKI_PATH", str(Path.home() / "llm-wiki"))).resolve()
    data = Path(os.environ.get("MEMORY_HUB_DATA", str(Path(__file__).resolve().parents[1] / "data"))).resolve()
    operation_id = os.environ.get("MEMORY_HUB_OPERATION_ID") or new_operation_id(
        datetime.now(timezone.utc), uuid4
    )
    ctx = OperationContext(
        operation_id=operation_id,
        command="scope-backfill",
        mode=Mode.AUTO,
        auto=True,
        apply=args.apply,
        wiki_path=wiki,
        data_path=data,
    )
    report = apply_backfill(plan_backfill(wiki, args.cursor, args.limit, ctx), ctx)
    payload = {
        "operation_id": operation_id,
        "apply": args.apply,
        "counts": report.counts,
        "next_cursor": report.next_cursor,
        "report": str(report.report_path.relative_to(data)),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(
            f"scope-backfill: {'apply' if args.apply else 'dry-run'} "
            f"counts={json.dumps(report.counts, sort_keys=True)} "
            f"next_cursor={report.next_cursor or '-'} report={payload['report']}"
        )
    return 0


def _search(args: argparse.Namespace) -> int:
    service = _get_service()
    if len(args.query) > 500:
        print("search: query exceeds 500 characters", file=sys.stderr)
        return 2
    if args.top < 1 or args.top > 50:
        print("search: top must be between 1 and 50", file=sys.stderr)
        return 2
    if args.scope and args.scope not in ("user", "project", "agent"):
        print("search: invalid scope", file=sys.stderr)
        return 2

    request = SearchRequest(
        query=args.query,
        top=args.top,
        fuse=args.fuse,
        expand=args.expand,
        scope=args.scope,
        scope_id=args.scope_id,
        explain=args.explain,
    )
    response = service.search(request, tau=args.tau)

    if args.json:
        print(json.dumps(response.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        if args.explain:
            print(f"== Query Plan ({response.plan.get('planner')}): {json.dumps(response.plan)} ==")
        if not response.results:
            print("无命中")
        else:
            print(f"== 检索 ({'fuse' if request.fuse else 'fts'}): {request.query} ==")
            print(render_human(response.results))
    return 0


def _ask(args: argparse.Namespace) -> int:
    service = _get_service()
    if len(args.query) > 500:
        print("ask: query exceeds 500 characters", file=sys.stderr)
        return 2
    if args.top < 1 or args.top > 50:
        print("ask: top must be between 1 and 50", file=sys.stderr)
        return 2
    if args.scope and args.scope not in ("user", "project", "agent"):
        print("ask: invalid scope", file=sys.stderr)
        return 2

    request = SearchRequest(
        query=args.query,
        top=args.top,
        fuse=args.fuse,
        expand=args.expand,
        scope=args.scope,
        scope_id=args.scope_id,
        explain=args.explain,
    )
    ctx = service.ask_context(request)

    if args.json:
        print(json.dumps(ctx.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        if ctx.answer:
            print("== 回答 ==")
            print(ctx.answer)
            print("")
        print("== 引用页面 ==")
        for r in ctx.results:
            print(f"  - {r.path} — {r.title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="memory-hub automation operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # scope-backfill
    scope = subparsers.add_parser("scope-backfill", help="plan or apply deterministic scope backfill")
    scope.add_argument("--apply", action="store_true", help="write scope frontmatter (default: dry-run)")
    scope.add_argument("--limit", type=int)
    scope.add_argument("--cursor")
    scope.add_argument("--json", action="store_true")
    scope.set_defaults(handler=_scope_backfill)

    # search
    search_p = subparsers.add_parser("search", help="unified search operation")
    search_p.add_argument("query", help="query text")
    search_p.add_argument("--top", type=int, default=10)
    search_p.add_argument("--fuse", action="store_true", default=True)
    search_p.add_argument("--no-fuse", dest="fuse", action="store_false")
    search_p.add_argument("--expand", action="store_true", default=True)
    search_p.add_argument("--no-expand", dest="expand", action="store_false")
    search_p.add_argument("--scope", choices=["user", "project", "agent"])
    search_p.add_argument("--scope-id")
    search_p.add_argument("--explain", action="store_true")
    search_p.add_argument("--json", action="store_true")
    search_p.add_argument("--tau", type=float, default=DEFAULT_TAU)
    search_p.add_argument("--raw", action="store_true")
    search_p.add_argument("--all", action="store_true")
    search_p.add_argument("--gbrain", action="store_true")
    search_p.set_defaults(handler=_search)

    # ask
    ask_p = subparsers.add_parser("ask", help="unified ask operation")
    ask_p.add_argument("query", help="question text")
    ask_p.add_argument("--top", type=int, default=5)
    ask_p.add_argument("--fuse", action="store_true", default=True)
    ask_p.add_argument("--no-fuse", dest="fuse", action="store_false")
    ask_p.add_argument("--expand", action="store_true", default=True)
    ask_p.add_argument("--no-expand", dest="expand", action="store_false")
    ask_p.add_argument("--scope", choices=["user", "project", "agent"])
    ask_p.add_argument("--scope-id")
    ask_p.add_argument("--explain", action="store_true")
    ask_p.add_argument("--json", action="store_true")
    ask_p.set_defaults(handler=_ask)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"{args.command}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

