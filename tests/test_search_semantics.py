#!/usr/bin/env python3
import os
import pathlib
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
SEARCH = ROOT / "scripts" / "search.sh"


def run_search(wiki: pathlib.Path, data: pathlib.Path, query: str, *args: str):
    return subprocess.run(
        ["bash", str(SEARCH), query, *args],
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data)},
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        wiki = tmp_path / "wiki"
        data = tmp_path / "data"
        wiki.mkdir()
        data.mkdir()
        (wiki / "relevant.md").write_text("exactphrase a needle\n", encoding="utf-8")
        (wiki / "incidental.md").write_text("persistent content a\n", encoding="utf-8")
        db = data / "index.db"
        schema = """
        CREATE VIRTUAL TABLE pages USING fts5(
          path, title, type, tags, abstract, content, tokenize='trigram'
        );
        INSERT INTO pages VALUES
          ('relevant.md', 'relevant', 'note', '', '', 'exactphrase a needle'),
          ('incidental.md', 'incidental', 'note', '', '', 'persistent content a');
        """
        seeded = subprocess.run(["sqlite3", str(db)], input=schema, text=True, capture_output=True, check=False)
        assert seeded.returncode == 0, seeded.stderr

        unrelated = run_search(wiki, data, "zzznonexistent", "--no-fallback")
        assert unrelated.returncode == 0, unrelated.stderr
        assert unrelated.stdout == ""

        related = run_search(wiki, data, "exactphrase", "--no-fallback")
        assert related.returncode == 0, related.stderr
        assert "relevant.md" in related.stdout
        assert "incidental.md" not in related.stdout

        strict_short = run_search(wiki, data, "a", "--no-fallback")
        assert strict_short.returncode == 0, strict_short.stderr
        assert strict_short.stdout == ""

        default_short = run_search(wiki, data, "a", "--top", "1")
        assert default_short.returncode == 0, default_short.stderr
        assert "(rg)" in default_short.stdout
        assert "[" in default_short.stdout

        pipefail = run_search(wiki, data, "needle", "--top", "1", "--no-fts")
        assert pipefail.returncode == 0, pipefail.stderr
        assert "relevant.md" in pipefail.stdout


if __name__ == "__main__":
    main()
