"""Atomic SQLite FTS5 indexer supporting scope and lifecycle metadata."""
from __future__ import annotations

import argparse
import os
import sqlite3
import stat
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .frontmatter import parse_page
from .schema import IndexSchema, normalize_id


PAGES_DDL = """CREATE VIRTUAL TABLE pages USING fts5(
  path, title, type, tags, abstract, content,
  scope, scope_id, scope_confidence, status,
  tokenize='trigram'
);"""

META_DDL = """CREATE TABLE meta(
  path TEXT PRIMARY KEY,
  updated TEXT,
  last_verified TEXT,
  valid_at TEXT,
  invalid_at TEXT
);"""

VEC_DDL = """CREATE TABLE vec(
  path TEXT PRIMARY KEY,
  title TEXT,
  dim INT,
  v BLOB
);"""

_LEGAL_STATUSES = {"active", "deprecated", "candidate"}


@dataclass(frozen=True)
class IndexBuild:
    db_path: Path
    page_count: int
    meta_count: int
    vec_count: int


@dataclass(frozen=True)
class IndexedPage:
    path: str
    title: str
    type: str
    tags: str
    abstract: str
    content: str
    scope: str
    scope_id: str
    scope_confidence: str
    status: str
    updated: str
    last_verified: str
    valid_at: str
    invalid_at: str


def detect_index_schema(db: Path) -> IndexSchema:
    if not db.is_file():
        return IndexSchema((), (), supports_scope=False, supports_validity=False)
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as con:
        page_cols = tuple(row[1] for row in con.execute("pragma table_info(pages)"))
        meta_cols = tuple(row[1] for row in con.execute("pragma table_info(meta)"))
    return IndexSchema(
        page_cols=page_cols,
        meta_cols=meta_cols,
        supports_scope="scope" in page_cols,
        supports_validity="valid_at" in meta_cols,
    )


def load_page_records(db: Path, paths: Sequence[str] | None = None) -> dict[str, IndexedPage]:
    if not db.is_file():
        return {}
    schema = detect_index_schema(db)
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as con:
        if schema.supports_scope and schema.supports_validity:
            sql = """
                SELECT p.path, p.title, p.type, p.tags, p.abstract, p.content,
                       p.scope, p.scope_id, p.scope_confidence, p.status,
                       m.updated, m.last_verified, m.valid_at, m.invalid_at
                FROM pages p JOIN meta m ON p.path = m.path
            """
            params: list[object] = []
            if paths is not None:
                placeholders = ",".join("?" for _ in paths)
                sql += f" WHERE p.path IN ({placeholders})"
                params.extend(paths)
            rows = con.execute(sql, params).fetchall()
            return {
                r[0]: IndexedPage(
                    path=r[0], title=r[1], type=r[2], tags=r[3], abstract=r[4], content=r[5],
                    scope=r[6], scope_id=r[7], scope_confidence=r[8], status=r[9],
                    updated=r[10] or "", last_verified=r[11] or "",
                    valid_at=r[12] or "", invalid_at=r[13] or ""
                )
                for r in rows
            }
        else:
            # Legacy index fallback
            sql = """
                SELECT p.path, p.title, p.type, p.tags, p.abstract, p.content,
                       m.updated, m.last_verified
                FROM pages p JOIN meta m ON p.path = m.path
            """
            params = []
            if paths is not None:
                placeholders = ",".join("?" for _ in paths)
                sql += f" WHERE p.path IN ({placeholders})"
                params.extend(paths)
            rows = con.execute(sql, params).fetchall()
            return {
                r[0]: IndexedPage(
                    path=r[0], title=r[1], type=r[2], tags=r[3], abstract=r[4], content=r[5],
                    scope="project", scope_id="default-project", scope_confidence="low", status="active",
                    updated=r[6] or "", last_verified=r[7] or "",
                    valid_at="", invalid_at=""
                )
                for r in rows
            }


def build_index(
    wiki: Path,
    destination: Path,
    include_raw: bool = False,
    failure_after: int | None = None,
    source_db: Path | None = None,
) -> IndexBuild:
    wiki = wiki.resolve(strict=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(destination)
    try:
        con.executescript(PAGES_DDL + "\n" + META_DDL + "\n" + VEC_DDL)
        page_rows = []
        meta_rows = []
        excluded = {"_legacy-para", "_archive"} | (set() if include_raw else {"raw"})

        paths = sorted(wiki.rglob("*.md"), key=lambda p: p.relative_to(wiki).as_posix())
        for path in paths:
            if path.is_symlink():
                continue
            rel = path.relative_to(wiki)
            if excluded.intersection(rel.parts):
                continue
            try:
                doc = parse_page(path)
                fm = doc.frontmatter
                title = str(fm.get("title") or doc.title or path.stem)
                ptype = str(fm.get("type") or "unknown")
                tags = " ".join(doc.tags)
                abstract = str(fm.get("abstract") or "")
                body = doc.body.decode("utf-8", errors="replace")
            except Exception:
                try:
                    raw_bytes = path.read_bytes()
                    body = raw_bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue
                fm = {}
                title = path.stem
                ptype = "unknown"
                tags = ""
                abstract = ""

            rel_s = rel.as_posix()
            scope = str(fm.get("scope") or "project")
            scope_id = normalize_id(str(fm.get("scope_id") or "default-project"), "default-project")
            scope_confidence = str(fm.get("scope_confidence") or "low")
            status = str(fm.get("status") or "active")
            if status not in _LEGAL_STATUSES:
                status = "active"

            updated = str(fm.get("updated") or "")
            last_verified = str(fm.get("last_verified") or "")
            valid_at = str(fm.get("valid_at") or "")
            invalid_at = str(fm.get("invalid_at") or "")

            page_rows.append((rel_s, title, ptype, tags, abstract, body,
                              scope, scope_id, scope_confidence, status))
            meta_rows.append((rel_s, updated, last_verified, valid_at, invalid_at))

            if failure_after and len(page_rows) >= failure_after:
                raise RuntimeError("injected index failure")

        with con:
            con.executemany("INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?)", page_rows)
            con.executemany("INSERT INTO meta VALUES(?,?,?,?,?)", meta_rows)
            if source_db and source_db.is_file():
                con.execute("ATTACH DATABASE ? AS old", (str(source_db.resolve()),))
                if con.execute("SELECT 1 FROM old.sqlite_master WHERE type='table' AND name='vec'").fetchone():
                    con.execute("INSERT INTO vec SELECT v.* FROM old.vec v JOIN pages p ON p.path=v.path")

        page_count = con.execute("SELECT count(*) FROM pages").fetchone()[0]
        meta_count = con.execute("SELECT count(*) FROM meta").fetchone()[0]
        vec_count = con.execute("SELECT count(*) FROM vec").fetchone()[0]
        stale = con.execute("SELECT count(*) FROM vec v LEFT JOIN pages p ON p.path=v.path WHERE p.path IS NULL").fetchone()[0]
        if page_count != meta_count or stale:
            raise RuntimeError(f"inconsistent index: pages={page_count} meta={meta_count} stale_vec={stale}")
        con.execute("PRAGMA optimize")
        con.close()
        return IndexBuild(destination, page_count, meta_count, vec_count)
    except Exception:
        con.close()
        if destination.exists():
            destination.unlink()
        raise


def atomic_rebuild_index(
    wiki: Path,
    data: Path,
    include_raw: bool = False,
    failure_after: int | None = None,
) -> IndexBuild:
    data = data.resolve()
    data.mkdir(parents=True, exist_ok=True)
    db_target = data / "index.db"
    if failure_after is None:
        fail_env = os.environ.get("MEMORY_HUB_INDEX_FAIL_AFTER")
        if fail_env:
            try:
                failure_after = int(fail_env)
            except ValueError:
                pass

    descriptor, tmp_name = tempfile.mkstemp(prefix=".index.", suffix=".db", dir=data)
    os.close(descriptor)
    tmp_path = Path(tmp_name)
    try:
        build = build_index(
            wiki,
            tmp_path,
            include_raw=include_raw,
            failure_after=failure_after,
            source_db=db_target if db_target.is_file() else None,
        )
        fd = os.open(tmp_path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, db_target)
        dir_fd = os.open(data, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return IndexBuild(db_target, build.page_count, build.meta_count, build.vec_count)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild SQLite FTS5 index for memory-hub")
    parser.add_argument("--wiki", type=Path, required=True, help="Wiki root directory")
    parser.add_argument("--data", type=Path, required=True, help="Data directory")
    parser.add_argument("--with-raw", action="store_true", help="Include raw/ directory")
    args = parser.parse_args()

    build = atomic_rebuild_index(args.wiki, args.data, include_raw=args.with_raw)
    print(build.page_count)


if __name__ == "__main__":
    main()

