from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]


class FullAutoCliRunner:
    def __init__(self, evidence_dir: pathlib.Path, fixture_root: pathlib.Path | None = None) -> None:
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        if fixture_root:
            self.root = fixture_root
        else:
            self._tmp = tempfile.TemporaryDirectory()
            self.root = pathlib.Path(self._tmp.name)

        self.home = self.root / "forbidden-home"
        self.wiki = self.root / "wiki"
        self.data = self.root / "data"
        self.sessions = self.root / "sessions"
        self.automations = self.root / "automations"
        self.config = self.root / "codex-config.toml"
        self.hooks = self.root / "hooks.json"
        self.db = self.root / "automations.db"

        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "WIKI_PATH": str(self.wiki),
            "MEMORY_HUB_DATA": str(self.data),
            "CODEX_SESSIONS_DIR": str(self.sessions),
            "CODEX_AUTOMATIONS_DIR": str(self.automations),
            "CODEX_CONFIG_FILE": str(self.config),
            "CODEX_HOOKS_FILE": str(self.hooks),
            "AUTOMATIONS_DB": str(self.db),
            "PYTHONPATH": str(ROOT),
        }

        self.assertions: dict[str, bool] = {}

    def run_cmd(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(ROOT / "memory-hub.sh"), *args],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def execute_suite(self) -> bool:
        # 1. Unseeded verify should fail
        unseeded = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh")], env=self.env, capture_output=True, text=True)
        self.assertions["unseeded_verify_failed"] = (unseeded.returncode != 0)

        # 2. Seed verify dependencies
        seed_cmd = [
            sys.executable, "-m", "tests.helpers.full_auto_fixture", "seed-verify-dependencies",
            "--root", str(self.root),
            "--automations", str(self.automations),
            "--config", str(self.config),
            "--hooks", str(self.hooks),
            "--db", str(self.db),
            "--wiki", str(self.wiki),
        ]
        subprocess.run(seed_cmd, cwd=ROOT, check=True)

        # 3. Seeded verify passes and uses fixture paths only
        seeded = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh")], env=self.env, capture_output=True, text=True)
        self.assertions["seeded_verify_passed"] = (seeded.returncode == 0)
        self.assertions["real_home_read"] = (self.home / ".codex").exists()

        # Seed sample pages and observations
        (self.wiki / "pages").mkdir(parents=True, exist_ok=True)
        (self.wiki / "pages" / "p1.md").write_text("---\ntitle: 'P1'\nproject: 'fixture-project'\n---\nfixture lifecycle details\n", encoding="utf-8")
        (self.wiki / "pages" / "p2.md").write_text("---\ntitle: 'P2'\nscope: user\nscope_id: default-user\n---\nuser settings\n", encoding="utf-8")

        staging = self.root / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "observations-20260830-100000.jsonl").write_text(
            json.dumps({"id": "obs-1", "project": "fixture-project", "text": "observation 1 about lifecycle", "created_at_epoch": 1788100000}) + "\n" +
            json.dumps({"id": "obs-2", "project": "fixture-project", "text": "observation 2 about lifecycle", "created_at_epoch": 1788100000}) + "\n"
        )
        (staging / "observations-20260831-100000.jsonl").write_text(
            json.dumps({"id": "obs-3", "project": "fixture-project", "text": "observation 3 about lifecycle", "created_at_epoch": 1788200000}) + "\n"
        )
        self.env["MEMORY_HUB_STAGING"] = str(staging)

        # Init git in wiki
        subprocess.run(["git", "init"], cwd=self.wiki, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.wiki, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.wiki, check=True)
        subprocess.run(["git", "add", "."], cwd=self.wiki, check=True)
        subprocess.run(["git", "commit", "-m", "initial baseline"], cwd=self.wiki, check=True)

        # 4. Run scope-backfill
        bf = self.run_cmd("scope-backfill", "--apply", "--json")
        self.assertions["scope_backfill_ok"] = (bf.returncode == 0)

        # 5. Run index
        idx = self.run_cmd("index")
        self.assertions["index_ok"] = (idx.returncode == 0)

        # 6. Run search
        srch = self.run_cmd("search", "fixture lifecycle", "--fuse", "--explain", "--scope", "project", "--scope-id", "fixture-project", "--json")
        self.assertions["search_ok"] = (srch.returncode == 0)
        try:
            srch_data = json.loads(srch.stdout)
            self.assertions["planner_valid"] = srch_data.get("plan", {}).get("planner") in ("llm", "local", "original-only")
        except Exception:
            self.assertions["planner_valid"] = False

        # 7. Run --safe and maintain --safe
        r_safe = self.run_cmd("run", "--safe")
        m_safe = self.run_cmd("maintain", "--safe")
        self.assertions["safe_modes_ok"] = (r_safe.returncode == 0 and m_safe.returncode == 0)

        # 8. Record evidence files
        self.assertions["all_passed"] = all(
            v is True for k, v in self.assertions.items() if k != "real_home_read"
        ) and not self.assertions.get("real_home_read", True)

        (self.evidence_dir / "assertions.json").write_text(json.dumps(self.assertions, indent=2), encoding="utf-8")

        git_status_out = subprocess.run(["git", "status"], cwd=self.wiki, capture_output=True, text=True).stdout
        (self.evidence_dir / "git-status.txt").write_text(git_status_out, encoding="utf-8")

        resolved_paths = {
            "root": str(self.root),
            "wiki": str(self.wiki),
            "data": str(self.data),
            "automations": str(self.automations),
        }
        (self.evidence_dir / "resolved_paths.json").write_text(json.dumps(resolved_paths, indent=2), encoding="utf-8")

        db_path = self.data / "index.db"
        if db_path.is_file():
            with sqlite3.connect(db_path) as con:
                tables = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                cols_pages = con.execute("PRAGMA table_info(pages)").fetchall()
                cols_meta = con.execute("PRAGMA table_info(meta)").fetchall()
                (self.evidence_dir / "index-schema.txt").write_text(
                    f"tables: {tables}\npages: {cols_pages}\nmeta: {cols_meta}\n", encoding="utf-8"
                )
        else:
            (self.evidence_dir / "index-schema.txt").write_text("no index.db", encoding="utf-8")

        return self.assertions["all_passed"]


def main():
    parser = argparse.ArgumentParser(description="Full auto CLI fixture runner")
    parser.add_argument("--evidence-dir", required=True, type=pathlib.Path)
    args = parser.parse_args()

    runner = FullAutoCliRunner(args.evidence_dir)
    success = runner.execute_suite()
    print(f"FullAutoCliRunner finished: success={success}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
