"""Isolated test fixture shared by the full-auto roadmap tests."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts.automation_core.frontmatter import patch_frontmatter
from scripts.automation_core.schema import Mode, OperationContext, PageDocument

ROOT = Path(__file__).resolve().parents[2]


class InjectedFailure(RuntimeError):
    """Deterministic failure raised by transaction tests."""


@dataclass
class FakeTransport:
    responses: Sequence[object]
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = field(default_factory=list)
    _cursor: int = 0

    def request(self, *args: object, **kwargs: object) -> object:
        self.calls.append((args, kwargs))
        if self._cursor >= len(self.responses):
            raise AssertionError("FakeTransport has no response left")
        response = self.responses[self._cursor]
        self._cursor += 1
        if isinstance(response, BaseException):
            raise response
        return response

    __call__ = request


@dataclass(frozen=True)
class FakeEmbedding:
    cosine: float
    entities: bool = True
    changed: bool = True

    def cosine_similarity(self, _left: object, _right: object) -> float:
        return self.cosine

    def has_shared_entities(self, _left: object, _right: object) -> bool:
        return self.entities

    def changed_enough(self, _left: object, _right: object) -> bool:
        return self.changed


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_page(
    root: Path,
    relative: str,
    frontmatter: Mapping[str, object],
    body: str,
) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    empty = PageDocument(path, path.stem, [], {}, body.encode(), ())
    path.write_bytes(patch_frontmatter(empty, frontmatter))
    return path


def seed_verify_dependencies(
    *,
    root: Path,
    automations: Path,
    config: Path,
    hooks: Path,
    db: Path,
    wiki: Path,
) -> None:
    for path in (root, automations, config.parent, hooks.parent, db.parent, wiki):
        path.mkdir(parents=True, exist_ok=True)

    automation_id = "fixture-memory-hub"
    automation_dir = automations / automation_id
    automation_dir.mkdir(parents=True, exist_ok=True)
    (automation_dir / "automation.toml").write_text(
        "\n".join(
            (
                'version = 1',
                f'id = "{automation_id}"',
                'name = "memory-hub fixture"',
                'prompt = "Run the isolated memory-hub fixture"',
                'status = "PAUSED"',
                'rrule = "FREQ=DAILY"',
                "",
            )
        ),
        encoding="utf-8",
    )

    server = ROOT / "mcp" / "server.py"
    config.write_text(
        "\n".join(
            (
                "[mcp_servers.memory-hub]",
                'command = "python3"',
                f'args = ["{server}"]',
                "",
                "[hooks.state]",
                '"memory-hub@personal" = true',
                "",
            )
        ),
        encoding="utf-8",
    )
    hooks.write_text(json.dumps({"hooks": {}}, indent=2) + "\n", encoding="utf-8")

    if db.exists():
        db.unlink()
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "CREATE TABLE automations (id TEXT PRIMARY KEY, name TEXT, prompt TEXT)"
        )
        connection.execute(
            "INSERT INTO automations(id, name, prompt) VALUES (?, ?, ?)",
            (automation_id, "memory-hub fixture", "memory-hub fixture"),
        )
        connection.commit()
    finally:
        connection.close()

    for directory in (
        wiki / ".scripts",
        wiki / "archive",
        wiki / "concepts",
        wiki / "drafts" / "memoryhub",
        wiki / "queries",
        wiki / "raw",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (wiki / ".scripts" / "fix_deadlinks.py").write_text(
        'print("未解/多候选: 0")\nprint("raw 区死链: 0")\n',
        encoding="utf-8",
    )


@dataclass
class FullAutoFixture:
    root: Path
    evidence_dir: Path
    wiki: Path
    data: Path
    sessions: Path
    automations: Path
    config: Path
    hooks: Path
    db: Path
    env: dict[str, str]

    @classmethod
    def create(cls, evidence_dir: Path) -> "FullAutoFixture":
        root = Path(tempfile.mkdtemp(prefix="memory-hub-full-auto-"))
        evidence_dir.mkdir(parents=True, exist_ok=True)
        wiki, data = root / "wiki", root / "data"
        sessions, automations = root / "sessions", root / "automations"
        for directory in (wiki, data, sessions, automations):
            directory.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(wiki)], check=True)
        fixture_home = root / "home"
        config = root / "codex-config.toml"
        hooks = fixture_home / ".codex/plugins/cache/personal/memory-hub/1.0.0/hooks/hooks.json"
        db = root / "automations.db"
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(fixture_home),
                "CODEX_SESSIONS_DIR": str(sessions),
                "CODEX_AUTOMATION_ROOT": str(automations),
                "CODEX_AUTOMATIONS_DIR": str(automations),
                "CODEX_CONFIG": str(config),
                "CODEX_CONFIG_FILE": str(config),
                "CODEX_HOOKS_FILE": str(hooks),
                "CODEX_DEV_DB": str(db),
                "AUTOMATIONS_DB": str(db),
                "WIKI_PATH": str(wiki),
                "MEMORY_HUB_DATA": str(data),
                "PYTHONPATH": str(ROOT),
            }
        )
        return cls(root, evidence_dir, wiki, data, sessions, automations, config, hooks, db, env)

    def seed_verify_dependencies(self) -> None:
        seed_verify_dependencies(
            root=self.root,
            automations=self.automations,
            config=self.config,
            hooks=self.hooks,
            db=self.db,
            wiki=self.wiki,
        )

    def operation(self, command: str = "maintain", mode: Mode = Mode.AUTO) -> OperationContext:
        return OperationContext(
            "20260831T000000Z-00000000-0000-0000-0000-000000000001",
            command,
            mode,
            mode is Mode.AUTO,
            mode is Mode.AUTO,
            self.wiki,
            self.data,
        )

    def git_baseline(self) -> tuple[str, ...]:
        result = subprocess.run(
            ["git", "-C", str(self.wiki), "diff", "--cached", "--name-only"],
            check=True,
            text=True,
            capture_output=True,
        )
        return tuple(line for line in result.stdout.splitlines() if line)

    def transaction(self, *, failure_hook: object | None = None) -> Any:
        from scripts.automation_core.transaction import begin_transaction

        return begin_transaction(
            self.operation(), self.git_baseline(), failure_hook=failure_hook
        )

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(ROOT / "memory-hub.sh"), *args],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def cleanup(self) -> None:
        shutil.rmtree(self.root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed isolated full-auto fixtures")
    subparsers = parser.add_subparsers(dest="command", required=True)
    seed = subparsers.add_parser("seed-verify-dependencies")
    for name in ("root", "automations", "config", "hooks", "db", "wiki"):
        seed.add_argument(f"--{name}", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "seed-verify-dependencies":
        seed_verify_dependencies(
            root=args.root,
            automations=args.automations,
            config=args.config,
            hooks=args.hooks,
            db=args.db,
            wiki=args.wiki,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
