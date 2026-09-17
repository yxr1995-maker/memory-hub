"""Wiki Fixer lite: read-only dead-link + stale-page inspection (M6 P0).

Never modifies wiki files, never records owned paths, never touches the
journal. SAFE mode only reports counts to stdout (via the stage message);
apply mode additionally writes a markdown report under ``$DATA/reports/``.

Link parsing is single-sourced from :mod:`links` (extract_link_targets /
build_page_sets); staleness policy from :mod:`staleness`.
"""
from __future__ import annotations
import os
import sqlite3
from datetime import date
from pathlib import Path
from .frontmatter import parse_page
from .links import build_page_sets, extract_link_targets
from .staleness import effective_date, stale_days
REPORT_PREFIX = "wiki-fixer-"
def scan_dead_links(wiki: Path) -> dict[str, list[str]]:
    stems, bases = build_page_sets(Path(wiki))
    dead: dict[str, list[str]] = {}
    for rel in sorted(set(stems.values())):
        try:
            text = (Path(wiki) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        missing = [t for t in extract_link_targets(text)
                   if stems.get(t.lower()) is None
                   and bases.get(os.path.basename(t).lower()) is None]
        if missing:
            dead[rel] = sorted(missing)
    return dead
def scan_stale(wiki: Path, data: Path, *, limit_days: int | None = None, today: date | None = None) -> list[tuple[str, str, int]]:
    days = stale_days() if limit_days is None else limit_days
    now = today or date.today()
    rows: dict[str, dict[str, str]] = {}
    db_path = Path(data) / "index.db"
    if db_path.is_file():
        try:
            with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True) as con:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type=\u0027table\u0027").fetchall()}
                if "meta" in tables:
                    cols = [r[1] for r in con.execute("pragma table_info(meta)").fetchall()]
                    wanted = [c for c in ("path", "updated", "last_verified", "valid_at") if c in cols]
                    if "path" in wanted:
                        query = "SELECT " + ", ".join(wanted) + " FROM meta"
                        for record in con.execute(query).fetchall():
                            entry = dict(zip(wanted, [str(v or "") for v in record]))
                            rows[entry["path"]] = entry
        except sqlite3.Error:
            rows = {}
    if not rows:
        stems, _ = build_page_sets(Path(wiki))
        for rel in sorted(set(stems.values())):
            try:
                page = parse_page(Path(wiki) / rel)
            except Exception:
                continue
            fm = page.frontmatter
            rows[rel] = {"path": rel, "updated": str(fm.get("updated") or ""),
                         "last_verified": str(fm.get("last_verified") or ""),
                         "valid_at": str(fm.get("valid_at") or "")}
    stale: list[tuple[str, str, int]] = []
    for rel, entry in rows.items():
        eff = effective_date(entry.get("last_verified", ""), entry.get("valid_at", ""), entry.get("updated", ""))
        if eff is None:
            continue
        age = (now - eff).days
        if age > days:
            stale.append((rel, eff.isoformat(), age))
    stale.sort(key=lambda item: (item[2], item[0]), reverse=True)
    return stale
def render_report(dead: dict[str, list[str]], stale: list[tuple[str, str, int]], day: str) -> str:
    lines = ["---", f"date: {day}", "kind: wiki-fixer-report", "---", "",
             "# wiki-fixer \u5de1\u68c0\u62a5\u544a", "", f"- date: {day}",
             f"- \u6b7b\u94fe: {sum(len(v) for v in dead.values())} \u6761\uff08{len(dead)} \u9875\uff09",
             f"- \u8fc7\u671f: {len(stale)} \u9875", "",
             "## \u6b7b\u94fe\uff08\u6e90\u9875 \u2192 \u6b7b\u94fe\u76ee\u6807\uff09", ""]
    if not dead:
        lines.append("- \u65e0\u6b7b\u94fe")
    else:
        for source in sorted(dead):
            for target in dead[source]:
                lines.append(f"- {source} \u2192 {target}")
    lines += ["", "## \u8fc7\u671f\u9875\uff08\u6700\u65e7\u5728\u524d\uff1a\u8def\u5f84 | \u65e5\u671f | \u5929\u6570\uff09", ""]
    if not stale:
        lines.append("- \u65e0\u8fc7\u671f\u9875")
    else:
        for rel, day_str, age in stale:
            lines.append(f"- {rel} | {day_str} | {age} \u5929")
    lines.append("")
    return "\n".join(lines)
def report_path(data: Path, day: str) -> Path:
    return Path(data) / "reports" / f"{REPORT_PREFIX}{day}.md"
def run_report(wiki: Path, data: Path, *, limit_days: int | None = None, today: date | None = None) -> tuple[dict[str, list[str]], list[tuple[str, str, int]]]:
    days = stale_days() if limit_days is None else limit_days
    return scan_dead_links(Path(wiki)), scan_stale(Path(wiki), Path(data), limit_days=days, today=today)
