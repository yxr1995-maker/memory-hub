#!/usr/bin/env python3
"""inject.sh regression test for the two P2 review bugs."""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _write(path, text):
    path.write_text(text, encoding="utf-8")


class InjectShTest(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="inject-sh-"))
        self.wiki = self.tmp / "wiki"
        self.wiki.mkdir()
        self.data = self.tmp / "data"
        self.data.mkdir()
        _write(self.wiki / "accepted.md",
               "---\ntitle: Accepted page\nstatus: active\n---\nbody\n")
        _write(self.wiki / "body-only.md",
               "---\ntitle: Active page\nstatus: active\n---\n"
               "Example output:\nstatus: rejected\n")
        _write(self.wiki / "quoted.md",
               "---\ntitle: Quoted rejected\nstatus: \"rejected\"\n---\nbody\n")

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def _run(self):
        env = dict(os.environ, WIKI_PATH=str(self.wiki),
                   MEMORY_HUB_DATA=str(self.data))
        return subprocess.run(["bash", str(ROOT / "scripts" / "inject.sh")],
                              capture_output=True, text=True, timeout=120, env=env)

    def test_count_and_filter_with_index(self):
        from scripts.automation_core.indexer import build_index
        build = build_index(self.wiki, self.data / "index.db")
        self.assertEqual(build.page_count, 2)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\u77e5\u8bc6\u5e93\u9875\u9762: 2", r.stdout)
        self.assertIn("accepted.md", r.stdout)
        self.assertIn("body-only.md", r.stdout)
        self.assertNotIn("quoted.md", r.stdout)

    def test_filter_without_index_db(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\u77e5\u8bc6\u5e93\u9875\u9762: 2", r.stdout)
        self.assertIn("accepted.md", r.stdout)
        self.assertIn("body-only.md", r.stdout)
        self.assertNotIn("quoted.md", r.stdout)

    def _write_all_rejected(self):
        for p in self.wiki.glob("*.md"):
            p.unlink()
        _write(self.wiki / "rejected.md",
               "---\ntitle: Rejected\nstatus: rejected\n---\nbody\n")

    def test_all_rejected_without_index_db(self):
        self._write_all_rejected()
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("rejected.md", r.stdout)
        self.assertIn("\u5019\u9009\u5747\u4e3a rejected", r.stdout)
        self.assertIn("\u77e5\u8bc6\u5e93\u9875\u9762: 0", r.stdout)

    def test_all_rejected_with_index_db(self):
        from scripts.automation_core.indexer import build_index
        self._write_all_rejected()
        build = build_index(self.wiki, self.data / "index.db")
        self.assertEqual(build.page_count, 0)
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("rejected.md", r.stdout)
        self.assertIn("\u5019\u9009\u5747\u4e3a rejected", r.stdout)
        self.assertIn("\u77e5\u8bc6\u5e93\u9875\u9762: 0", r.stdout)

    def test_no_index_count_not_truncated_by_display_limit(self):
        for p in self.wiki.glob("*.md"):
            p.unlink()
        for i in range(6):
            _write(self.wiki / ("p%d.md" % i),
                   "---\ntitle: Page %d\nstatus: active\n---\nbody\n" % i)
        _write(self.wiki / "rejected.md",
               "---\ntitle: Rejected\nstatus: rejected\n---\nbody\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("\u77e5\u8bc6\u5e93\u9875\u9762: 6", r.stdout)
        self.assertNotIn("rejected.md", r.stdout)
        recent = r.stdout.split("\u6700\u8fd1\u66f4\u65b0")[1].split(
            "\u7edf\u8ba1")[0]
        items = [l for l in recent.splitlines() if l.startswith("- ")]
        self.assertEqual(len(items), 5)


if __name__ == "__main__":
    unittest.main()
