import json
import pathlib
import tempfile
import unittest

from scripts.export import collect_pages, export_pages, parse_frontmatter


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.wiki_path = pathlib.Path(self.tmp_dir.name)

        # 创建模拟知识库目录结构和页面
        (self.wiki_path / "concepts").mkdir(parents=True, exist_ok=True)
        (self.wiki_path / "decisions").mkdir(parents=True, exist_ok=True)
        (self.wiki_path / "raw").mkdir(parents=True, exist_ok=True)

        p1 = self.wiki_path / "concepts" / "demo-concept.md"
        p1.write_text(
            "---\n"
            "title: Demo Concept\n"
            "type: concept\n"
            "project: project-a\n"
            'tags: ["agent", "memory"]\n'
            "created: 2026-08-20\n"
            "updated: 2026-08-28\n"
            "---\n\n"
            "# Demo Concept\n\n"
            "This is a demo concept content.\n",
            encoding="utf-8",
        )

        p2 = self.wiki_path / "decisions" / "demo-decision.md"
        p2.write_text(
            "---\n"
            "title: Demo Decision\n"
            "type: decision\n"
            "project: project-b\n"
            'tags: ["architecture"]\n'
            "created: 2026-08-21\n"
            "updated: 2026-08-27\n"
            "---\n\n"
            "# Demo Decision\n\n"
            "This is a decision rationale.\n",
            encoding="utf-8",
        )

        # raw 目录下的文件应被忽略
        p_raw = self.wiki_path / "raw" / "ignored.md"
        p_raw.write_text("raw session dump", encoding="utf-8")

        # index.md 与 log.md 索引文件应被忽略
        (self.wiki_path / "index.md").write_text("# Index", encoding="utf-8")
        (self.wiki_path / "log.md").write_text("# Log", encoding="utf-8")

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_parse_frontmatter(self):
        text = (
            "---\n"
            "title: Test\n"
            "type: failure\n"
            'tags: ["foo", "bar"]\n'
            "---\n"
            "Body line 1\nBody line 2\n"
        )
        meta, body = parse_frontmatter(text)
        self.assertEqual(meta.get("title"), "Test")
        self.assertEqual(meta.get("type"), "failure")
        self.assertEqual(meta.get("tags"), ["foo", "bar"])
        self.assertIn("Body line 1", body)

    def test_collect_pages_all(self):
        pages = collect_pages(self.wiki_path)
        self.assertEqual(len(pages), 2)
        slugs = {p["slug"] for p in pages}
        self.assertEqual(slugs, {"demo-concept", "demo-decision"})

    def test_collect_pages_filter_project(self):
        pages = collect_pages(self.wiki_path, project_filter="project-a")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["slug"], "demo-concept")

    def test_collect_pages_filter_type(self):
        pages = collect_pages(self.wiki_path, type_filter="decision")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0]["slug"], "demo-decision")

    def test_export_jsonl(self):
        pages = collect_pages(self.wiki_path)
        output = export_pages(pages, fmt="jsonl")
        lines = [line for line in output.strip().split("\n") if line.strip()]
        self.assertEqual(len(lines), 2)
        item0 = json.loads(lines[0])
        self.assertIn("slug", item0)
        self.assertIn("frontmatter", item0)
        self.assertIn("body", item0)

    def test_export_json(self):
        pages = collect_pages(self.wiki_path)
        output = export_pages(pages, fmt="json")
        data = json.loads(output)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)

    def test_export_markdown(self):
        pages = collect_pages(self.wiki_path)
        output = export_pages(pages, fmt="markdown")
        self.assertIn("# Memory Hub Knowledge Export", output)
        self.assertIn("## Demo Concept", output)
        self.assertIn("## Demo Decision", output)


if __name__ == "__main__":
    unittest.main()
