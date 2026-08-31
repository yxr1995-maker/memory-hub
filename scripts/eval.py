#!/usr/bin/env python3
"""memory-hub eval (F3): self-eval harness with dual expand comparison."""
import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

_RE = re.compile(r"^\[-?\d*\.?\d+\]\s+(\S+?\.md)$")


@dataclass
class EvalResult:
    hit_at_5: float
    mrr: float
    total: int
    hits: int
    details: list


@dataclass
class EvalComparison:
    on: EvalResult
    off: EvalResult
    ratio: float
    passed: bool


def compare_expansion(on: EvalResult, off: EvalResult, floor: float = 0.90) -> EvalComparison:
    ratio = 1.0 if off.hit_at_5 == 0.0 else (on.hit_at_5 / off.hit_at_5)
    return EvalComparison(on=on, off=off, ratio=ratio, passed=ratio >= floor)


def evaluate(golden_path: pathlib.Path, wiki_dir: pathlib.Path, mode: str = "expand-on", top: int = 5) -> EvalResult:
    rows = []
    with open(golden_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        return EvalResult(0.0, 0.0, 0, 0, [])

    hub = pathlib.Path(__file__).resolve().parents[1]
    sh = hub / "scripts" / "search.sh"
    expand_flag = "--expand" if mode == "expand-on" else "--no-expand"

    hits = 0
    total_mrr = 0.0
    details = []

    for r in rows:
        q = r["q"]
        exp = r["expected"]
        env = {
            **os.environ,
            "WIKI_PATH": str(wiki_dir),
            "MEMORY_HUB_DATA": str(wiki_dir.parent / "data"),
            "PYTHONPATH": str(hub),
        }
        cmd = ["bash", str(sh), q, "--fuse", "--top", str(top), expand_flag, "--json"]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        paths = []
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout)
                paths = [item["path"] for item in data.get("results", [])]
            except Exception:
                pass
        
        rank = paths.index(exp) + 1 if exp in paths else None
        hit = 1 if rank else 0
        mrr = (1.0 / rank) if rank else 0.0
        hits += hit
        total_mrr += mrr
        details.append({"query": q, "expected": exp, "rank": rank, "hit": hit})

    n = len(rows)
    return EvalResult(
        hit_at_5=hits / n if n else 0.0,
        mrr=total_mrr / n if n else 0.0,
        total=n,
        hits=hits,
        details=details,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=None)
    ap.add_argument("--wiki", default=None)
    ap.add_argument("--data", default=None)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--report", default=None)
    ap.add_argument("--report-json", default=None)
    ap.add_argument("--report-md", default=None)
    ap.add_argument("--compare-expand", action="store_true")
    args = ap.parse_args()

    hub = pathlib.Path(__file__).resolve().parents[1]
    golden = pathlib.Path(args.golden or (hub / "evaluation" / "golden.jsonl"))
    wiki = pathlib.Path(args.wiki or (pathlib.Path.home() / "llm-wiki"))
    data = pathlib.Path(args.data or (pathlib.Path.home() / ".memory-hub"))

    if args.compare_expand:
        subprocess.run(["bash", str(hub / "scripts" / "index.sh")], env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data), "PYTHONPATH": str(hub)}, check=True)
        on = evaluate(golden, wiki, mode="expand-on", top=args.top)
        off = evaluate(golden, wiki, mode="expand-off", top=args.top)
        comp = compare_expansion(on, off, floor=0.90)

        if args.report_json:
            out_p = pathlib.Path(args.report_json)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps({
                "expand_on": {"hit_at_5": on.hit_at_5, "mrr": on.mrr, "hits": on.hits, "total": on.total},
                "expand_off": {"hit_at_5": off.hit_at_5, "mrr": off.mrr, "hits": off.hits, "total": off.total},
                "ratio": comp.ratio,
                "passed": comp.passed,
            }, indent=2), encoding="utf-8")

        if args.report_md:
            out_m = pathlib.Path(args.report_md)
            out_m.parent.mkdir(parents=True, exist_ok=True)
            out_m.write_text("# Golden Evaluation: Expand Comparison\n\n- Expand On Hit@5: " + str(on.hit_at_5) + "\n- Expand Off Hit@5: " + str(off.hit_at_5) + "\n- Ratio: " + str(comp.ratio) + "\n- Passed: " + str(comp.passed) + "\n", encoding="utf-8")

        print("eval: compare expand_on=" + str(round(on.hit_at_5, 3)) + " expand_off=" + str(round(off.hit_at_5, 3)) + " ratio=" + str(round(comp.ratio, 3)) + " passed=" + str(comp.passed))
        return 0 if comp.passed else 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
