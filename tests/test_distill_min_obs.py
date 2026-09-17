"""M8-R 蒸馏减产：distill.sh 项目观察数低于 MIN_OBS 时跳过产页。

边界：默认 MIN_OBS=3，1-2 条观察跳过、3 条产页；MEMORY_HUB_MIN_OBS 可覆盖。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run_distill(
    staging: pathlib.Path,
    extra_env: dict[str, str],
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    wiki = staging.parent / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    home = staging.parent / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "WIKI_PATH": str(wiki),
        "MEMORY_HUB_STAGING": str(staging),
        "PYTHONPATH": str(ROOT),
        **extra_env,
    }
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "distill.sh"), *(extra_args or [])],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_observations(staging: pathlib.Path, counts: dict[str, int]) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    for name in staging.glob("observations-*.jsonl"):
        name.unlink()
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    records = [
        {"project": p, "type": "message", "role": "user", "text": f"{p} obs {i}", "id": f"{p}-{i}"}
        for p, n in counts.items()
        for i in range(1, n + 1)
    ]
    (staging / f"observations-{day}-000000.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def test_default_min_obs_skips_projects_with_1_or_2_observations(tmp_path: pathlib.Path) -> None:
    staging = tmp_path / "staging"
    _write_observations(staging, {"tiny-1": 1, "tiny-2": 2})
    proc = _run_distill(staging, {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "跳过项目 'tiny-1'" in proc.stdout
    assert "跳过项目 'tiny-2'" in proc.stdout
    assert list((staging / "pages").glob("*.md")) == [], "1-2 条观察（<MIN_OBS=3）不应产页"


def test_default_min_obs_produces_page_with_3_observations(tmp_path: pathlib.Path) -> None:
    staging = tmp_path / "staging"
    _write_observations(staging, {"full-3": 3})
    proc = _run_distill(staging, {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    pages = list((staging / "pages").glob("*.md"))
    assert len(pages) == 1, f"3 条观察（==MIN_OBS）应产 1 页: {proc.stdout}"
    assert "本页观察: 3 条" in pages[0].read_text(encoding="utf-8")


def test_all_projects_below_min_obs_yields_zero_pages_exit_zero(tmp_path: pathlib.Path) -> None:
    staging = tmp_path / "staging"
    _write_observations(staging, {"a": 1, "b": 2})
    proc = _run_distill(staging, {"MEMORY_HUB_MIN_OBS": "5"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "共 0 页" in proc.stdout
    assert list((staging / "pages").glob("*.md")) == []


def test_min_obs_env_override(tmp_path: pathlib.Path) -> None:
    staging = tmp_path / "staging"
    _write_observations(staging, {"one": 1, "two": 2})

    proc = _run_distill(staging, {"MEMORY_HUB_MIN_OBS": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(list((staging / "pages").glob("*.md"))) == 2, "MIN_OBS=1 时单条观察应产页"

    proc = _run_distill(staging, {"MEMORY_HUB_MIN_OBS": "3"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "跳过项目 'one'" in proc.stdout
    assert "跳过项目 'two'" in proc.stdout
    assert list((staging / "pages").glob("*.md")) == [], "MIN_OBS=3 时 1/2 条观察的两个项目都跳过，产 0 页"


def test_llm_mode_follows_same_min_obs_rule(tmp_path: pathlib.Path) -> None:
    # --llm 走同一门槛：死端口代理使 LLM 摘要降级，跳过/产页判定不受影响
    env = {"OPENCODEX_URL": "http://127.0.0.1:1/v1"}
    staging = tmp_path / "staging"
    _write_observations(staging, {"tiny": 2, "full": 3})
    proc = _run_distill(staging, env, extra_args=["--llm"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "跳过项目 'tiny'" in proc.stdout
    pages = list((staging / "pages").glob("*.md"))
    assert len(pages) == 1 and "full" in pages[0].name, f"--llm 门槛应与默认一致: {proc.stdout}"
