"""P2 links edge table: mutual links indexed, dead links and self-loops excluded."""
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.automation_core.indexer import atomic_rebuild_index, build_index


def _wiki(tmp: Path) -> Path:
    wiki = tmp / "wiki"
    wiki.mkdir()
    (wiki / "a.md").write_text(
        "---\ntitle: A\nupdated: 2026-09-01\n---\nbody [[b]] and [[ghost]] and [[a]]\n")
    (wiki / "b.md").write_text(
        "---\ntitle: B\nupdated: 2026-09-01\n---\nbody [[a]]\n")
    (wiki / "c.md").write_text(
        "---\ntitle: C\nupdated: 2026-09-01\n---\nno links here\n")
    return wiki


class LinksIndexTest(unittest.TestCase):
    def test_mutual_links_indexed_dead_and_self_excluded(self):
        with TemporaryDirectory() as temp:
            wiki = _wiki(Path(temp))
            dest = Path(temp) / "index.db"
            build = build_index(wiki, dest)
            self.assertEqual(3, build.page_count)
            con = sqlite3.connect(dest)
            rows = sorted(con.execute("SELECT src_path, dst_path FROM links").fetchall())
            con.close()
            self.assertEqual([("a.md", "b.md"), ("b.md", "a.md")], rows)

    def test_links_dst_index_exists(self):
        with TemporaryDirectory() as temp:
            wiki = _wiki(Path(temp))
            dest = Path(temp) / "index.db"
            build_index(wiki, dest)
            con = sqlite3.connect(dest)
            idx = con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_links_dst'"
            ).fetchone()
            con.close()
            self.assertIsNotNone(idx)

    def test_atomic_rebuild_keeps_links(self):
        with TemporaryDirectory() as temp:
            tmp = Path(temp)
            wiki = _wiki(tmp)
            data = tmp / "data"
            data.mkdir()
            atomic_rebuild_index(wiki, data)
            con = sqlite3.connect(data / "index.db")
            rows = sorted(con.execute("SELECT src_path, dst_path FROM links").fetchall())
            con.close()
            self.assertEqual([("a.md", "b.md"), ("b.md", "a.md")], rows)


if __name__ == "__main__":
    unittest.main()
