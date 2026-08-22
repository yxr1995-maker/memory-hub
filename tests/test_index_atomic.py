import hashlib
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class IndexAtomicTest(unittest.TestCase):
    def test_rebuild_prunes_stale_vectors_and_failure_preserves_database(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            wiki, data = temp / "wiki", temp / "data"
            (wiki / "raw").mkdir(parents=True)
            data.mkdir()
            (wiki / "a.md").write_text("---\ntitle: 'Alpha'\ntype: concept\ntags:\n  - x\nupdated: 2026-08-23\n---\nalpha body\n")
            (wiki / "b.md").write_text("beta body\n")
            (wiki / "raw" / "ignored.md").write_text("ignored\n")
            db = data / "index.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE vec(path TEXT PRIMARY KEY, title TEXT, dim INT, v BLOB)")
            con.executemany("INSERT INTO vec VALUES(?,?,?,?)", [
                ("a.md", "Alpha", 1, b"a"), ("stale.md", "Stale", 1, b"s")])
            con.commit()
            con.close()
            env = os.environ | {"WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data)}

            rebuilt = subprocess.run([str(ROOT / "scripts/index.sh")], env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.assertEqual(0, rebuilt.returncode, rebuilt.stderr)
            con = sqlite3.connect(db)
            self.assertEqual((2, 2, 1), tuple(con.execute(
                "SELECT (SELECT count(*) FROM pages), (SELECT count(*) FROM meta), (SELECT count(*) FROM vec)"
            ).fetchone()))
            self.assertEqual(("Alpha", "concept", "x", "2026-08-23"), con.execute(
                "SELECT p.title,p.type,p.tags,m.updated FROM pages p JOIN meta m USING(path) WHERE p.path='a.md'"
            ).fetchone())
            con.close()

            before = hashlib.sha256(db.read_bytes()).digest()
            failed = subprocess.run([str(ROOT / "scripts/index.sh")], env=env | {
                "MEMORY_HUB_INDEX_FAIL_AFTER": "1"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertNotEqual(0, failed.returncode)
            self.assertEqual(before, hashlib.sha256(db.read_bytes()).digest())


if __name__ == "__main__":
    unittest.main()
