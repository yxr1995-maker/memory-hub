#!/usr/bin/env python3
import datetime
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, check=False, **kwargs)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        hub = tmp / "hub"
        scripts = hub / "scripts"
        scripts.mkdir(parents=True)
        for name in ("distill.sh", "publish.sh", "lib.sh", "eval.py", "secure_replace.py"):
            shutil.copy2(ROOT / "scripts" / name, scripts / name)
        staging = hub / "staging"
        staging.mkdir()
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
        source = staging / f"observations-{day}-000000.jsonl"
        source.write_text(json.dumps({"project": "test-proj", "type": "message", "role": "user", "text": "fresh", "id": "1"}) + "\n", encoding="utf-8")
        wiki = tmp / "wiki"
        target_dir = wiki / "drafts" / "memoryhub"
        target_dir.mkdir(parents=True)
        (wiki / "index.md").write_text("", encoding="utf-8")
        (wiki / "log.md").write_text("", encoding="utf-8")
        (target_dir / "old-memoryhub-test-proj.md").write_text("# historical project page\n", encoding="utf-8")
        env = {**os.environ, "WIKI_PATH": str(wiki)}

        assert run("bash", str(scripts / "distill.sh"), str(source), env=env).returncode == 0
        page = next((staging / "pages").glob("*.md"))
        assert "status: fresh" in page.read_text(encoding="utf-8")
        assert run("bash", str(scripts / "publish.sh"), "--apply", env=env).returncode == 0
        target = target_dir / page.name
        fresh = target.read_text(encoding="utf-8")

        day_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        candidate_source = staging / f"observations-{day}-000001.jsonl"
        candidate_source.write_text("\n".join(json.dumps({"project": p, "type": "message", "role": "user", "text": p, "id": p}) for p in ("candidate-a", "candidate-b")) + "\n", encoding="utf-8")
        candidate_a = target_dir / f"{day_iso}-memoryhub-candidate-a.md"
        candidate_b = target_dir / f"{day_iso}-memoryhub-candidate-b.md"
        old_a = "---\nabstract: 'old A'\n---\n"
        old_b = "---\nabstract: 'old B'\n---\n"
        candidate_a.write_text(old_a, encoding="utf-8")
        candidate_b.write_text(old_b, encoding="utf-8")
        assert run("bash", str(scripts / "distill.sh"), str(candidate_source), env=env).returncode == 0
        staged_a = staging / "pages" / candidate_a.name
        staged_b = staging / "pages" / candidate_b.name
        a_text = staged_a.read_text(encoding="utf-8")
        b_text = staged_b.read_text(encoding="utf-8")
        assert "status: candidate" in a_text and "status: candidate" in b_text
        default_a = f"conflict_target: 'drafts/memoryhub/{staged_a.name}'"
        default_b = f"conflict_target: 'drafts/memoryhub/{staged_b.name}'"
        assert default_a in a_text and default_b in b_text
        cross_target = wiki / "drafts" / "review" / "legacy-a.md"
        cross_target.parent.mkdir(parents=True)
        cross_target.write_text("old cross target\n", encoding="utf-8")
        a_text = a_text.replace(default_a, "conflict_target: 'drafts/review/legacy-a.md'")
        staged_a.write_text(a_text, encoding="utf-8")
        assert run("bash", str(scripts / "publish.sh"), "--accept-candidate", staged_a.name, env=env).returncode == 2
        assert candidate_a.read_text(encoding="utf-8") == old_a
        assert candidate_b.read_text(encoding="utf-8") == old_b
        assert run("bash", str(scripts / "publish.sh"), "--apply", env=env).returncode == 0
        assert candidate_a.read_text(encoding="utf-8") == old_a
        assert candidate_b.read_text(encoding="utf-8") == old_b
        assert run("bash", str(scripts / "publish.sh"), "--apply", "--accept-candidate", staged_a.name, env=env).returncode == 0
        assert candidate_a.read_text(encoding="utf-8") == old_a
        assert candidate_b.read_text(encoding="utf-8") == old_b
        assert not staged_a.exists()
        assert staged_b.read_text(encoding="utf-8") == b_text
        assert cross_target.read_text(encoding="utf-8") == a_text

        for bad_target in ("archive/old.md", "../outside.md"):
            bad_b = b_text.replace(default_b, f"conflict_target: '{bad_target}'")
            staged_b.write_text(bad_b, encoding="utf-8")
            assert run("bash", str(scripts / "publish.sh"), "--apply", "--accept-candidate", staged_b.name, env=env).returncode == 2
            assert candidate_b.read_text(encoding="utf-8") == old_b
            assert staged_b.read_text(encoding="utf-8") == bad_b
        staged_b.write_text(b_text, encoding="utf-8")

        # The replacement stays bound to the opened directory if its path is swapped.
        race_root = wiki / "drafts" / "race"
        race_root.mkdir()
        race_target = race_root / "target.md"
        race_target.write_text("trusted old\n", encoding="utf-8")
        outside = tmp / "outside"
        outside.mkdir()
        outside_target = outside / "target.md"
        outside_target.write_text("outside unchanged\n", encoding="utf-8")
        large_source = tmp / "large.md"
        large_source.write_bytes(b"replacement\n" * 8_000_000)
        proc = subprocess.Popen(
            [sys.executable, str(scripts / "secure_replace.py"), str(wiki), "drafts/race/target.md", str(large_source)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(10_000):
            if next(race_root.glob(".memory-hub-publish.*"), None):
                break
        else:
            proc.kill()
            raise AssertionError("secure replace did not open its trusted parent")
        moved_race_root = wiki / "drafts" / "race-opened"
        race_root.rename(moved_race_root)
        race_root.symlink_to(outside, target_is_directory=True)
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 0, (stdout, stderr)
        assert outside_target.read_text(encoding="utf-8") == "outside unchanged\n"
        assert (moved_race_root / "target.md").read_bytes() == large_source.read_bytes()

        symlink_parent = wiki / "drafts" / "linked"
        symlink_parent.symlink_to(outside, target_is_directory=True)
        proc = run(sys.executable, str(scripts / "secure_replace.py"), str(wiki), "drafts/linked/target.md", str(large_source))
        assert proc.returncode == 1
        assert outside_target.read_text(encoding="utf-8") == "outside unchanged\n"
        linked_target = wiki / "drafts" / "race-opened" / "linked-target.md"
        linked_target.symlink_to(outside_target)
        proc = run(sys.executable, str(scripts / "secure_replace.py"), str(wiki), "drafts/race-opened/linked-target.md", str(large_source))
        assert proc.returncode == 1
        assert outside_target.read_text(encoding="utf-8") == "outside unchanged\n"

        (scripts / "search.sh").write_text("#!/usr/bin/env bash\nprintf 'not a search result\\n'\n", encoding="utf-8")
        golden = tmp / "golden.jsonl"
        golden.write_text(json.dumps({"q": "q", "expected": "wanted.md"}) + "\n", encoding="utf-8")
        report = tmp / "eval.md"
        proc = run(sys.executable, str(scripts / "eval.py"), "--golden", str(golden), "--report", str(report))
        assert proc.returncode == 1
        assert not report.exists()
        (scripts / "search.sh").write_text("#!/usr/bin/env bash\nprintf '== 混合检索 (RRF k=60): q ==\\n[0.123] wanted.md\\n'\n", encoding="utf-8")
        proc = run(sys.executable, str(scripts / "eval.py"), "--golden", str(golden), "--report", str(report))
        assert proc.returncode == 0
        assert "hit@5: 1/1" in report.read_text(encoding="utf-8")

        query_wiki = tmp / "query-wiki"
        (query_wiki / "notes").mkdir(parents=True)
        (query_wiki / "notes" / "page.md").write_text("needle\n", encoding="utf-8")
        proc = run("bash", str(ROOT / "scripts" / "search.sh"), "needle", "--top", "1", "--no-fts", env={**os.environ, "WIKI_PATH": str(query_wiki)})
        assert proc.returncode == 0, proc.stderr
        assert "[1 处] notes/page.md" in proc.stdout


if __name__ == "__main__":
    main()
