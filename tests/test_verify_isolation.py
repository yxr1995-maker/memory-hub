from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_verify_isolation(tmp_path: Path) -> None:
    fixture_root = tmp_path / "verify-fixture"
    home = fixture_root / "forbidden-home"
    wiki = fixture_root / "wiki"
    data = fixture_root / "data"
    sessions = fixture_root / "sessions"
    automations = fixture_root / "automations"
    config = fixture_root / "codex-config.toml"
    hooks = fixture_root / "hooks.json"
    db = fixture_root / "automations.db"

    env = {
        **os.environ,
        "HOME": str(home),
        "WIKI_PATH": str(wiki),
        "MEMORY_HUB_DATA": str(data),
        "CODEX_SESSIONS_DIR": str(sessions),
        "CODEX_AUTOMATIONS_DIR": str(automations),
        "CODEX_CONFIG_FILE": str(config),
        "CODEX_HOOKS_FILE": str(hooks),
        "AUTOMATIONS_DB": str(db),
        "PYTHONPATH": str(ROOT),
    }

    # 1. Unseeded verify should fail
    unseeded = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh")], env=env, capture_output=True, text=True)
    assert unseeded.returncode != 0

    # 2. Seed verify dependencies using full_auto_fixture
    seed_cmd = [
        "python3", "-m", "tests.helpers.full_auto_fixture", "seed-verify-dependencies",
        "--root", str(fixture_root),
        "--automations", str(automations),
        "--config", str(config),
        "--hooks", str(hooks),
        "--db", str(db),
        "--wiki", str(wiki),
    ]
    subprocess.run(seed_cmd, cwd=ROOT, check=True)

    # 3. Seeded verify must pass
    seeded = subprocess.run(["bash", str(ROOT / "scripts" / "verify.sh")], env=env, capture_output=True, text=True)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert "== verify: 全部通过 ==" in seeded.stdout
    assert not (home / ".codex").exists()


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        test_verify_isolation(Path(tmp))
    print("test_verify_isolation PASS")
