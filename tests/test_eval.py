import pathlib, subprocess, sys, tempfile, json
import pytest
ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_eval_default_is_genuine_run(tmp_path):
    # Default eval with a valid fixture golden set must run and produce a report.
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "alpha.md").write_text("needle-in-royal-pine\n", encoding="utf-8")
    golden = tmp_path / "golden.jsonl"
    golden.write_text(json.dumps({"q": "needle-in-royal-pine", "expected": "alpha.md"}) + "\n", encoding="utf-8")
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "eval.py"), "--golden", str(golden), "--wiki", str(wiki),
         "--data", str(tmp_path / "data"), "--report", str(report)],
        env={**__import__("os").environ, "PYTHONPATH": str(ROOT)}, capture_output=True, text=True,
    )
    assert result.returncode in (0, 1), f"eval must return a real verdict: {result.stdout}{result.stderr}"
    assert report.is_file(), "default eval must produce a report"
    payload = json.loads(report.read_text())
    assert payload["total"] == 1
    assert "hit_at_5" in payload
    assert "passed" in payload


def test_eval_missing_golden_fails(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "eval.py"), "--golden", str(tmp_path / "nope.jsonl")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, "eval must fail when golden set is missing"


def test_compare_expand_double_zero_not_passed():
    sys.path.insert(0, str(ROOT))
    from scripts.eval import EvalResult, compare_expansion
    zero = EvalResult(0.0, 0.0, 31, 0, [])
    comp = compare_expansion(zero, zero)
    assert comp.passed is False, "double-zero baselines must not be reported as passing"
