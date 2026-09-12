"""Regression: the capture jq template must survive bash 5 substitution rules."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _clean_jq() -> str:
    script = (ROOT / "scripts" / "capture.sh").read_text(encoding="utf-8")
    match = re.search(r"CLEAN_JQ='([^']*)'", script)
    assert match, "CLEAN_JQ is missing from scripts/capture.sh"
    return match.group(1)


def test_clean_filter_stays_backslash_free() -> None:
    # bash 5 treats a backslash in the replacement of the placeholder substitution as
    # an escape, so "\\s" reached jq as the invalid escape "\s" and capture produced
    # zero observations on Linux while macOS (bash 3.2) stayed green.
    clean = _clean_jq()
    assert "\\" not in clean, clean
    assert "[[:space:]]" in clean


def test_generated_filter_compiles_and_fills_every_placeholder(tmp_path) -> None:
    script = (ROOT / "scripts" / "capture.sh").read_text(encoding="utf-8")
    template = re.search(r"EXTRACT_JQ='(.+?)'\n", script, re.S)
    assert template, "EXTRACT_JQ is missing from scripts/capture.sh"
    generated = template.group(1).replace("__CLEAN_JQ_PLACEHOLDER__", _clean_jq())
    assert "__CLEAN_JQ_PLACEHOLDER__" not in generated
    filter_file = tmp_path / "filter.jq"
    filter_file.write_text(generated, encoding="utf-8")
    compiled = subprocess.run(["jq", "-n", "-f", str(filter_file), "--arg", "proj", "fixture",
                               "--argjson", "since", "0"], text=True,
                              capture_output=True, check=False)
    assert compiled.returncode == 0, compiled.stderr
