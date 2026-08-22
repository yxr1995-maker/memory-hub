#!/usr/bin/env python3
"""memory-hub eval (F3): self-eval harness.
Reads evaluation/golden.jsonl, runs search --fuse --top N per query, computes hit@N and MRR,
groups by A/C/D/G, writes reports/eval-<date>.md. Stdlib only."""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

_RE = re.compile(r"^\[-?\d*\.?\d+\]\s+(\S+?\.md)$")
_HEADER_RE = re.compile(r"^== 混合检索 \(RRF k=\d+.*\): .+ ==$")

def parse(out):
    res=[]
    for raw in out.splitlines():
        m=_RE.match(raw.strip())
        if m:
            res.append(m.group(1))
        elif raw.strip() not in ("", "无命中") and not _HEADER_RE.match(raw.strip()):
            raise ValueError("search output violates the [score] path.md contract")
    return res

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--golden", default=None)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--report", default=None)
    args=ap.parse_args()
    hub=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    golden=args.golden or os.path.join(hub,"evaluation","golden.jsonl")
    date=datetime.now().strftime("%Y-%m-%d")
    report=args.report or os.path.join(hub,"reports","eval-"+date+".md")
    rows=[]
    with open(golden,encoding="utf-8") as fh:
        for line in fh:
            line=line.strip()
            if line: rows.append(json.loads(line))
    if not rows:
        print("eval: empty golden "+golden, file=sys.stderr); sys.exit(1)
    sh=os.path.join(hub,"scripts","search.sh")
    grouped=defaultdict(list)
    detail=[]
    ah=0; am=0.0; n=len(rows)
    for r in rows:
        q=r["q"]; exp=r["expected"]; typ=r.get("type","A")
        try:
            proc=subprocess.run(["bash",sh,q,"--fuse","--top",str(args.top)],
                                capture_output=True,text=True,timeout=90)
        except subprocess.TimeoutExpired:
            print("eval: search timed out for "+repr(q), file=sys.stderr); return 1
        if proc.returncode:
            print("eval: search failed for %r (exit %d)" % (q,proc.returncode), file=sys.stderr); return 1
        try:
            paths=parse(proc.stdout)
        except ValueError:
            print("eval: invalid search output for "+repr(q), file=sys.stderr); return 1
        rank=paths.index(exp)+1 if exp in paths else None
        hit=1 if rank else 0
        mrr=1.0/rank if rank else 0.0
        ah+=hit; am+=mrr
        grouped[typ].append((hit,mrr))
        detail.append((q,exp,rank,hit,typ))
    L=[]
    L.append("# memory-hub retrieval self-eval (F3)")
    L.append("")
    L.append("- date: "+date)
    L.append("- top="+str(args.top))
    L.append("- samples: "+str(n))
    L.append("- retrieval only, not generation")
    L.append("")
    L.append("## Overall")
    L.append("")
    L.append("- hit@%d: %d/%d = %.3f" % (args.top,ah,n,ah/n))
    L.append("- MRR: %.4f" % (am/n))
    L.append("")
    L.append("## By type (A fact / C time / D governance / G rules)")
    L.append("")
    for typ in sorted(grouped):
        g=grouped[typ]; h=sum(x[0] for x in g); m=sum(x[1] for x in g)
        L.append("- %s (n=%d): hit@%d %d/%d = %.3f, MRR %.4f" % (typ,len(g),args.top,h,len(g),h/len(g),m/len(g)))
    L.append("")
    L.append("## Detail")
    L.append("")
    L.append("| # | type | hit | rank | query | expected |")
    L.append("|---|------|-----|------|-------|----------|")
    for i,(q,exp,rank,hit,typ) in enumerate(detail,1):
        qs=q if len(q)<=40 else q[:37]+"..."
        L.append("| %d | %s | %d | %s | %s | %s |" % (i,typ,hit,rank if rank else "-",qs,exp))
    os.makedirs(os.path.dirname(report), exist_ok=True)
    with open(report,"w",encoding="utf-8") as fh:
        fh.write("\n".join(L)+"\n")
    print("eval: wrote %s  hit@%d=%d/%d (%.3f)  MRR=%.4f" % (report,args.top,ah,n,ah/n,am/n))

if __name__=="__main__":
    sys.exit(main() or 0)
