#!/usr/bin/env python3
"""memory-hub usage: 近 N 天使用量摘要。

聚合 access.jsonl（REST/MCP 按 kind 计数）+ codex-memory.db events 表
（按 status 计数、recalled 页数分布）。数据缺失时降级为 0，exit 0。
"""
from __future__ import annotations
import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("MEMORY_HUB_DATA", str(Path.home() / ".memory-hub")))


def parse_ts(raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ts.replace(tzinfo=None) if ts.tzinfo is not None else ts


def within(ts, cutoff) -> bool:
    if ts is None:
        return True
    return ts >= cutoff


def collect(days: int) -> dict:
    data = data_dir()
    cutoff = datetime.now() - timedelta(days=max(days, 0))
    access_total = 0
    by_src_kind: Counter = Counter()
    for name in ("access.jsonl.1", "access.jsonl"):
        p = data / name
        if not p.is_file():
            continue
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    if not within(parse_ts(row.get("ts")), cutoff):
                        continue
                    access_total += 1
                    by_src_kind[(str(row.get("src", "?")), str(row.get("kind", "?")))] += 1
        except OSError:
            continue
    ev_total = 0
    by_status: Counter = Counter()
    recalled_n = 0
    pages_total = 0
    pages_max = 0
    db = data / "codex-memory.db"
    if db.is_file():
        cols = set()
        rows = []
        try:
            con = sqlite3.connect(db, timeout=0.15)
            con.row_factory = sqlite3.Row
            cols = {c[1] for c in con.execute("PRAGMA table_info(events)")}
            if cols:
                if "pages" in cols:
                    qq = "SELECT status, created_at, pages FROM events"
                else:
                    qq = "SELECT status, created_at FROM events"
                try:
                    rows = con.execute(qq).fetchall()
                except sqlite3.Error:
                    rows = []
            con.close()
        except sqlite3.Error:
            pass
        for rr in rows:
            if not within(parse_ts(rr["created_at"]), cutoff):
                continue
            ev_total += 1
            by_status[str(rr["status"])] += 1
            if rr["status"] == "recalled" and "pages" in cols:
                try:
                    pg = int(rr["pages"] or 0)
                except (TypeError, ValueError):
                    pg = 0
                recalled_n += 1
                pages_total += pg
                pages_max = max(pages_max, pg)
    return {"days": days, "access_total": access_total, "by_src_kind": dict(by_src_kind),
            "ev_total": ev_total, "by_status": dict(by_status), "recalled_n": recalled_n,
            "pages_total": pages_total, "pages_max": pages_max}


def fmt_counter(d: dict) -> str:
    if not d:
        return "无"
    return " ".join(f"{k}={v}" for k, v in sorted(d.items()))


def main() -> int:
    ap = argparse.ArgumentParser(prog="memory-hub usage", description="近 N 天使用量摘要")
    ap.add_argument("--days", type=int, default=7)
    args = ap.parse_args()
    s = collect(args.days)
    pairs = {a + "/" + b: c for (a, b), c in s["by_src_kind"].items()}
    bs = s["by_status"]
    avg = (s["pages_total"] / s["recalled_n"]) if s["recalled_n"] else 0
    d = s["days"]
    at = s["access_total"]
    et = s["ev_total"]
    rn = s["recalled_n"]
    pt = s["pages_total"]
    pm = s["pages_max"]
    print(f"usage(近{d}天): access={at}条 {fmt_counter(pairs)}")
    print(f"  events={et}条 {fmt_counter(bs)}")
    print(f"  recalled={rn}次 pages合计={pt} 均值={avg:.1f} 最大={pm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
