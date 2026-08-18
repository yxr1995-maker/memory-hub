#!/usr/bin/env python3
"""memory-hub fuse: FTS5(bm25) + 向量检索 融合 (RRF k=60) + 时间衰减(F2)

调用 search.sh(FTS5) 与 embed.py(向量), 对相同 path 取各源最佳 rank 做 RRF 融合,
再按 meta 表 updated 施加指数时间衰减 score * exp(-age_days/tau)。
输出兼容 search.sh 的 `[score] path.md` 行, 供 _md_refs 解析。"""
import argparse
import math
import os
import re
import sqlite3
import subprocess
from collections import OrderedDict
from datetime import datetime, timezone

K = 60
TAU_DEFAULT = 90.0


def _filter_lines(out):
    """保留形如 `[0.123] path.md` 的结果行（兼容 bm25 负分/无前导零，如 [-0.0]）。
    兼容两种源：FTS `[score] path.md` 与 VEC `[score] path.md — title`（截断到 .md 即止）。"""
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[(-?\d*\.?\d+)\]\s+(\S+?\.md)(\s|$)", line)
        if m:
            yield float(m.group(1)), m.group(2)


def _load_meta(db_path):
    """path -> updated 字符串; meta 缺失返回空 dict, 退化为纯 RRF。"""
    meta = {}
    if not db_path or not os.path.isfile(db_path):
        return meta
    try:
        con = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
        try:
            for path, upd in con.execute("SELECT path, updated FROM meta"):
                meta[path] = upd
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return meta


def _load_types(db_path):
    """path -> type; 缺失返回空 dict（无条件 boost、仅 RRF）。"""
    types = {}
    if not db_path or not os.path.isfile(db_path):
        return types
    try:
        con = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
        try:
            for path, t in con.execute("SELECT path, type FROM pages"):
                types[path] = t
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return types


def _ts(updated):
    """解析 updated 为 UTC epoch; 无法解析返回 None。"""
    if not updated:
        return None
    v = updated.strip().strip("'")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(v, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _decay(ordered, meta, tau):
    """RRF 排序叠加时间衰减: score *= exp(-age_days/tau); 同分新页优先。tau<=0 不变。"""
    if tau and tau > 0:
        now = datetime.now(timezone.utc).timestamp()
        scored = []
        for path, s in ordered:
            ts = _ts(meta.get(path)) if path in meta else None
            f = math.exp(-max(0.0, (now - ts) / 86400.0) / tau) if ts is not None else 1.0
            scored.append((path, s * f, f))
        scored.sort(key=lambda kv: (-kv[1], -kv[2], kv[0]))
        return [(p, s) for p, s, _ in scored]
    return ordered


def main():
    ap = argparse.ArgumentParser(description="FTS5 + 向量 RRF 融合检索(含时间衰减)")
    ap.add_argument("query", help="检索关键词")
    ap.add_argument("--top", type=int, default=10, help="返回条数(默认 10)")
    ap.add_argument("--tau", type=float, default=TAU_DEFAULT, help="时间衰减常数(天),默认 90; --tau 0 关闭")
    args = ap.parse_args()

    hub = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    py = "/opt/homebrew/bin/python3" if os.path.exists("/opt/homebrew/bin/python3") else "python3"
    q = args.query

    fts5 = subprocess.run(["bash", hub + "/scripts/search.sh", q, "--top", "20", "--no-fallback"], capture_output=True, text=True).stdout
    vec = subprocess.run([py, hub + "/scripts/embed.py", "search", q, "-n", "20"], capture_output=True, text=True).stdout

    rank = OrderedDict()
    parsed = {}
    for source, out in (("fts5", fts5), ("vec", vec)):
        seen = 0
        for _, path in _filter_lines(out):
            seen += 1
            r = rank.setdefault(path, {})
            r[source] = min(r.get(source, float("inf")), seen)
        parsed[source] = seen
    # 防回归：至少一个源有结果（FTS 空=真无命中，合法退化为向量单源；与源都空才是异常）
    assert parsed.get("fts5", 0) + parsed.get("vec", 0) > 0, "fuse: FTS5 与向量都无解析结果; 检查 search.sh 与 embed.py 输出格式"

    db_path = os.path.join(os.environ.get("MEMORY_HUB_DATA", os.path.expanduser("~/.memory-hub")), "index.db")
    types = _load_types(db_path)
    # type 提升 (A/B 选型): entity/concept x1.8, atom/query/draft x0.7 -> hit@5 0.710->0.903
    ordered = []
    for path, r in rank.items():
        s = sum(1.0 / (K + v) for v in r.values() if v != float("inf"))
        t = types.get(path)
        if t in ("entity", "concept"):
            s *= 1.8
        elif t in ("atom", "query", "draft"):
            s *= 0.7
        ordered.append((path, s))
    ordered.sort(key=lambda kv: (-kv[1], kv[0]))

    decayed = _decay(ordered, _load_meta(db_path), args.tau)

    hearder = "== 混合检索 (RRF k=" + str(K)
    hearder += ", 时间衰减 tau=" + str(int(args.tau)) + "d)" if args.tau and args.tau > 0 else ", 无时间衰减)"
    print(hearder + ": " + q + " ==")
    if not decayed:
        print("无命中")
        return
    for path, s in decayed[: args.top]:
        print("[{:.3f}] {}".format(s, path))


def _selfcheck():
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    meta = {
        "old.md": (now - timedelta(days=200)).strftime("%Y-%m-%d"),
        "new.md": (now - timedelta(days=5)).strftime("%Y-%m-%d"),
    }
    ordered = [("old.md", 1.0), ("new.md", 1.0)]
    res = [p for p, _ in _decay(ordered, meta, 90.0)]
    assert res == ["new.md", "old.md"], res
    res0 = [p for p, _ in _decay(ordered, meta, 0.0)]
    assert res0 == ["old.md", "new.md"], res0
    res_none = [p for p, _ in _decay([("x.md", 1.0)], {}, 90.0)]
    assert res_none == ["x.md"]
    print("fuse._selfcheck OK")


if __name__ == "__main__":
    if os.environ.get("MH_FUSE_SELFCHECK") == "1":
        _selfcheck()
    else:
        main()
