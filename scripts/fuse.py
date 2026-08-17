#!/usr/bin/env python3
"""memory-hub fuse: FTS5(bm25) + 向量检索 融合 (RRF k=60)

调用 search.sh(FTS5) 与 embed.py(向量), 对相同 path 取各源最佳 rank 做 RRF 融合。
输出兼容 search.sh 的 `[score] path.md` 行, 供 MCP _md_refs 正则解析。
"""
import argparse
import os
import re
import subprocess
from collections import OrderedDict

K = 60


def _filter_lines(out: str):
    """保留形如 `[0.123] path.md` 的结果行, 丢弃 `== 标题 ==` / `[N 处] rg` / snippet。"""
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[(\d+\.\d+)\]\s+(\S+?\.md)", line)
        if m:
            yield float(m.group(1)), m.group(2)


def main():
    ap = argparse.ArgumentParser(description="FTS5 + 向量 RRF 融合检索")
    ap.add_argument("query", help="检索关键词")
    ap.add_argument("--top", type=int, default=10, help="返回条数(默认 10)")
    args = ap.parse_args()

    hub = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py = "/opt/homebrew/bin/python3" if os.path.exists("/opt/homebrew/bin/python3") else "python3"
    q = args.query

    fts5 = subprocess.run(
        ["bash", f"{hub}/scripts/search.sh", q, "--top", "20"],
        capture_output=True, text=True).stdout
    vec = subprocess.run(
        [py, f"{hub}/scripts/embed.py", "search", q, "-n", "20"],
        capture_output=True, text=True).stdout

    # path -> 每源排名 (rank 从 1 起)。同 path 多源并存, 后续 RRF 各算一份。
    rank = OrderedDict()
    for source, out in (("fts5", fts5), ("vec", vec)):
        seen = 0
        for _, path in _filter_lines(out):
            seen += 1
            r = rank.setdefault(path, {})
            r[source] = min(r.get(source, float("inf")), seen)

    # RRF 融合: score = sum over sources of 1/(K + rank)
    ordered = []
    for path, r in rank.items():
        s = sum(1.0 / (K + v) for v in r.values() if v != float("inf"))
        ordered.append((path, s))
    ordered.sort(key=lambda kv: (-kv[1], kv[0]))

    print(f"== 混合检索 (RRF k={K}): {q} ==")
    if not ordered:
        print("无命中")
        return
    for path, s in ordered[: args.top]:
        print(f"[{s:.3f}] {path}")


if __name__ == "__main__":
    main()

