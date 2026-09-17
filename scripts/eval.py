#!/usr/bin/env python3
"""memory-hub eval (F3): self-eval harness with dual expand comparison."""
import argparse
import json
import os
import pathlib
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

# Absolute quality floor: even when expansion does not regress (ratio >= 0.90),
# retrieval must still hit a minimum share of the golden set.
MIN_HIT_AT_5 = 0.50
MIN_MRR = 0.20


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
    passed = (
        ratio >= floor
        and on.hit_at_5 >= MIN_HIT_AT_5
        and on.mrr >= MIN_MRR
        and off.hit_at_5 >= MIN_HIT_AT_5
        and off.mrr >= MIN_MRR
    )
    return EvalComparison(on=on, off=off, ratio=ratio, passed=passed)


def evaluate(golden_path: pathlib.Path, wiki_dir: pathlib.Path, data_dir: pathlib.Path,
             mode: str = "expand-on", top: int = 5) -> EvalResult:
    rows = []
    with open(golden_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if not rows:
        raise SystemExit(f"eval: golden set is empty: {golden_path}")

    hub = pathlib.Path(__file__).resolve().parents[1]
    sh = hub / "scripts" / "search.sh"
    if not sh.exists():
        raise SystemExit(f"eval: search.sh not found: {sh}")
    expand_flag = "--expand" if mode == "expand-on" else "--no-expand"

    hits = 0
    total_mrr = 0.0
    details = []
    search_failures = 0

    for r in rows:
        q = r["q"]
        exp = r.get("expected")  # None/"" means the correct answer is no hit
        env = {
            **os.environ,
            "WIKI_PATH": str(wiki_dir),
            "MEMORY_HUB_DATA": str(data_dir),
            "PYTHONPATH": str(hub),
        }
        cmd = ["bash", str(sh), q, "--fuse", "--top", str(top), expand_flag, "--json"]
        if r.get("scope"):
            cmd += ["--scope", r["scope"]]
        if r.get("scope_id"):
            cmd += ["--scope-id", r["scope_id"]]
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
        paths = None
        if proc.returncode == 0:
            try:
                data = json.loads(proc.stdout)
                paths = [item["path"] for item in data.get("results", [])]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                search_failures += 1
                details.append({"query": q, "expected": exp, "rank": None, "hit": 0,
                                "type": r.get("type", "?"),
                                "error": f"search returned unparseable JSON: {exc}"})
                continue
        else:
            search_failures += 1
            details.append({"query": q, "expected": exp, "rank": None, "hit": 0,
                            "type": r.get("type", "?"),
                            "error": proc.stderr.strip()[:200]})
            continue

        if exp:
            rank = paths.index(exp) + 1 if exp in paths else None
        else:
            # expected empty: hit when nothing is returned
            rank = 1 if not paths else None

        hit = 1 if rank else 0
        mrr = (1.0 / rank) if rank else 0.0
        hits += hit
        total_mrr += mrr
        details.append({"query": q, "expected": exp, "type": r.get("type", "?"),
                        "rank": rank, "hit": hit})

    n = len(rows)
    if search_failures == n:
        raise SystemExit(
            f"eval: all {n} searches failed; refusing to report a zero score "
            f"as a retrieval regression (first error: {details[0].get('error', 'unknown')})"
        )
    return EvalResult(
        hit_at_5=hits / n if n else 0.0,
        mrr=total_mrr / n if n else 0.0,
        total=n,
        hits=hits,
        details=details,
    )


def _by_type(details: list) -> dict:
    groups = defaultdict(list)
    for d in details:
        groups[d.get("type", "?")].append(d)
    out = {}
    for t, ds in sorted(groups.items()):
        hits = sum(1 for d in ds if d["hit"])
        out[t] = {"hits": hits, "total": len(ds), "hit_at_5": hits / len(ds)}
    return out


def _git_sha(path: pathlib.Path) -> str:
    try:
        proc = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _meta(golden: pathlib.Path, wiki: pathlib.Path, data: pathlib.Path, top: int) -> dict:
    m = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "code_sha": _git_sha(pathlib.Path(__file__).resolve().parents[1]),
        "golden": str(golden),
        "golden_sha": _git_sha(golden.parent),
        "wiki": str(wiki),
        "data": str(data),
        "top": top,
        "floors": {"ratio": 0.90, "min_hit_at_5": MIN_HIT_AT_5, "min_mrr": MIN_MRR},
    }
    marker = data / "index.db"
    if marker.exists():
        m["index_mtime"] = datetime.fromtimestamp(marker.stat().st_mtime).isoformat(timespec="seconds")
    return m


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

    if not golden.exists():
        print(f"eval: golden set not found: {golden}", file=sys.stderr)
        return 1

    index_sh = hub / "scripts" / "index.sh"
    if index_sh.exists():
        subprocess.run(
            ["bash", str(index_sh)],
            env={**os.environ, "WIKI_PATH": str(wiki), "MEMORY_HUB_DATA": str(data), "PYTHONPATH": str(hub)},
            check=True,
        )
    meta = _meta(golden, wiki, data, args.top)

    if args.compare_expand:
        on = evaluate(golden, wiki, data, mode="expand-on", top=args.top)
        off = evaluate(golden, wiki, data, mode="expand-off", top=args.top)
        comp = compare_expansion(on, off, floor=0.90)
        payload = {
            "meta": meta,
            "expand_on": {"hit_at_5": on.hit_at_5, "mrr": on.mrr, "hits": on.hits, "total": on.total,
                          "by_type": _by_type(on.details)},
            "expand_off": {"hit_at_5": off.hit_at_5, "mrr": off.mrr, "hits": off.hits, "total": off.total,
                           "by_type": _by_type(off.details)},
            "ratio": comp.ratio,
            "passed": comp.passed,
        }
        report_path = args.report or args.report_json
        if report_path:
            out_p = pathlib.Path(report_path)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        if args.report_md:
            out_m = pathlib.Path(args.report_md)
            out_m.parent.mkdir(parents=True, exist_ok=True)
            out_m.write_text(
                "# Golden Evaluation: Expand Comparison\n\n"
                + json.dumps(meta, indent=2, ensure_ascii=False) + "\n\n"
                + "- Expand On Hit@5: " + str(on.hit_at_5) + "\n"
                + "- Expand Off Hit@5: " + str(off.hit_at_5) + "\n"
                + "- Ratio: " + str(comp.ratio) + "\n"
                + "- Passed: " + str(comp.passed) + "\n",
                encoding="utf-8",
            )
        print("eval: compare expand_on=" + str(round(on.hit_at_5, 3))
              + " expand_off=" + str(round(off.hit_at_5, 3))
              + " ratio=" + str(round(comp.ratio, 3))
              + " passed=" + str(comp.passed))
        return 0 if comp.passed else 1

    # default: run the evaluation once (expand on) and apply absolute floors
    res = evaluate(golden, wiki, data, mode="expand-on", top=args.top)
    passed = res.hit_at_5 >= MIN_HIT_AT_5 and res.mrr >= MIN_MRR
    payload = {
        "meta": meta,
        "hit_at_5": res.hit_at_5,
        "mrr": res.mrr,
        "hits": res.hits,
        "total": res.total,
        "by_type": _by_type(res.details),
        "details": res.details,
        "passed": passed,
    }
    report_path = args.report or args.report_json
    if report_path:
        out_p = pathlib.Path(report_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"eval: hit@{args.top}: {res.hits}/{res.total} mrr={round(res.mrr, 4)} passed={passed}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
