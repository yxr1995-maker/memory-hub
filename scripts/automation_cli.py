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

from scripts.automation_core.schema import Mode, OperationContext, new_operation_id
from scripts.automation_core.scope import apply_backfill, plan_backfill


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="memory-hub automation operations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scope = subparsers.add_parser("scope-backfill", help="plan or apply deterministic scope backfill")
    scope.add_argument("--apply", action="store_true", help="write scope frontmatter (default: dry-run)")
    scope.add_argument("--limit", type=int)
    scope.add_argument("--cursor")
    scope.add_argument("--json", action="store_true")
    scope.set_defaults(handler=_scope_backfill)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"scope-backfill: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
