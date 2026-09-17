"""Shared wikilink resolution (single source for indexer / autolink / wiki_fixer)."""
from __future__ import annotations

import os
import re
from pathlib import Path

LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")

EXCLUDED_DIRS = {"raw", "_archive", "_legacy-para"}


def _excluded(rel_parts, include_raw: bool = False) -> bool:
    excluded = set(EXCLUDED_DIRS)
    if include_raw:
        excluded.discard("raw")
    if excluded.intersection(rel_parts):
        return True
    return any(p.startswith(".") for p in rel_parts)


def build_page_sets(wiki: Path, dirs=None, include_raw: bool = False):
    """Return (stems, bases): lowercase stem/basename -> relative .md path.

    dirs=None scans the whole wiki tree (excluding raw/_archive/_legacy-para
    and dot-directories); otherwise only the given top-level directories.
    """
    wiki = Path(wiki)
    pages: set[str] = set()
    if dirs is None:
        for p in wiki.rglob("*.md"):
            if p.is_symlink():
                continue
            try:
                rel = p.relative_to(wiki)
            except ValueError:
                continue
            if _excluded(rel.parts, include_raw):
                continue
            pages.add(rel.as_posix())
    else:
        for d in dirs:
            dd = wiki / d
            if not dd.is_dir():
                continue
            for p in dd.rglob("*.md"):
                if p.is_symlink():
                    continue
                pages.add(p.relative_to(wiki).as_posix())
    stems = {}
    bases = {}
    for rel_s in sorted(pages):
        stem = rel_s[:-3] if rel_s.endswith(".md") else rel_s
        stems.setdefault(stem.lower(), rel_s)
        bases.setdefault(os.path.basename(stem).lower(), rel_s)
    return stems, bases


def resolve_outlinks(text: str, stems, bases):
    """Existing, non-self-loop-agnostic outlink targets (relative .md paths)."""
    hits = set()
    for t in extract_link_targets(text):
        hit = stems.get(t.lower()) or bases.get(os.path.basename(t).lower())
        if hit:
            hits.add(hit)
    return hits


def extract_link_targets(text: str) -> list[str]:
    """Raw ``[[target]]`` names in encounter order (code spans stripped)."""
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    body = re.sub(r"`[^`]*`", "", body)
    targets: list[str] = []
    seen: set[str] = set()
    for m in LINK_RE.finditer(body):
        t = m.group(1).strip()
        if t and t not in seen:
            seen.add(t)
            targets.append(t)
    return targets
