#!/usr/bin/env python3
"""
autolink.py — 新页面自动双链（2026-08-12）。

对给定页面（或 staging/pages 全部）扫描正文 [[wikilink]]，不足 --min 个
有效出链时按两级策略补足，只追加不改正文：
  L1 规则匹配：tags / title 词命中库内已有页面（零成本，立即生效）
  L2 语义匹配：memory-hub embed 向量库余弦 top N（需先跑过 embed index；
     未建库时自动跳过，不影响 L1）

用法:
  python3 autolink.py                      # staging/pages 全部，dry-run
  python3 autolink.py --apply              # 写盘
  python3 autolink.py --file <md> --apply  # 单页
  python3 autolink.py --min 3 --apply      # 每页至少 3 个出链
"""
import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path

HUB = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("MEMORY_HUB_DATA", str(Path.home() / ".memory-hub")))
DB = DATA_DIR / "index.db"
WIKI = Path(os.environ.get("WIKI_PATH", str(Path.home() / "llm-wiki")))
STAGING_PAGES = HUB / "staging" / "pages"

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}|[一-鿿]{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "from", "this", "that", "memoryhub",
    "llm-wiki", "wiki", "page", "note", "concept", "memory",
}
# 只把主目录页作为链接目标（raw/atoms/_archive 是素材与历史，不做目标）
TARGET_DIRS = ("entities", "concepts", "comparisons", "queries", "decisions",
               "communities", "failures", "moc")


def wiki_pages():
    pages = []
    for d in TARGET_DIRS:
        dd = WIKI / d
        if not dd.is_dir():
            continue
        for p in dd.rglob("*.md"):
            pages.append(p.relative_to(WIKI).with_suffix("").as_posix())
    for extra in ("index", "log"):
        if (WIKI / f"{extra}.md").is_file():
            pages.append(extra)
    return sorted(set(pages))


def parse_fm(text):
    fm = {}
    m = FM_RE.match(text)
    if not m:
        return fm
    cur = None
    for line in m.group(1).splitlines():
        mm = re.match(r"^\s+-\s+(\S.*)$", line)
        if mm and cur:
            fm.setdefault(cur, []).append(mm.group(1).strip().strip("'\""))
        elif ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            k, v = k.strip(), v.strip().strip("'\"")
            if v:
                fm[k] = v
                cur = None
            else:
                cur = k
                fm.setdefault(k, [])
    return fm


def valid_outlinks(text, stems, bases, self_stem):
    body = re.sub(r"```.*?```", "", text, flags=re.S)
    body = re.sub(r"`[^`]*`", "", body)
    hits = set()
    for m in LINK_RE.finditer(body):
        t = m.group(1).strip()
        hit = stems.get(t.lower()) or bases.get(os.path.basename(t).lower())
        if hit and hit != self_stem:
            hits.add(hit)
    return hits


def l1_candidates(fm, self_stem, tag_index, word_index, limit=3):
    import collections
    score = collections.Counter()
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    for t in tags:
        for g in tag_index.get(str(t).lower(), []):
            if g != self_stem:
                score[g] += 3
    for w in WORD_RE.findall(str(fm.get("title", "")).lower()):
        if w in STOPWORDS:
            continue
        for g in word_index.get(w, []):
            if g != self_stem:
                score[g] += 1
    return [g for g, _ in score.most_common(limit)]


def l2_candidates(query_text, self_stem, limit=3):
    """embed 向量库余弦 top N；库不存在或 fastembed 不可用返回 []。"""
    if not DB.is_file():
        return []
    try:
        import numpy as np
        from fastembed import TextEmbedding
    except ImportError:
        return []
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT path, v FROM vec").fetchall()
    db.close()
    if not rows:
        return []
    model = TextEmbedding(os.environ.get("MEMORY_HUB_EMBED_MODEL", "BAAI/bge-small-zh-v1.5"))
    mat = np.stack([np.frombuffer(b, dtype="<f4") for _, b in rows])
    qv = np.asarray(list(model.embed([query_text[:2000]]))[0], dtype="<f4")
    qn = qv / (np.linalg.norm(qv) + 1e-9)
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    sims = mn @ qn
    out = []
    for i in np.argsort(-sims):
        p = rows[i][0]
        if p != self_stem and sims[i] >= 0.55 and p in TARGET_PREFIXES:
            out.append(p)
        if len(out) >= limit:
            break
    return out


TARGET_PREFIXES = set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--file")
    ap.add_argument("--min", type=int, default=2)
    ap.add_argument("--no-semantic", action="store_true", help="只用 L1 规则匹配")
    args = ap.parse_args()

    pages = wiki_pages()
    stems = {p.lower(): p for p in pages}
    bases = {}
    for p in pages:
        bases.setdefault(os.path.basename(p).lower(), p)
    TARGET_PREFIXES.update(pages)

    tag_index, word_index = {}, {}
    import collections
    tag_index = collections.defaultdict(list)
    word_index = collections.defaultdict(list)
    for p in pages:
        try:
            text = (WIKI / f"{p}.md").read_text(encoding="utf-8", errors="replace")[:3000]
        except OSError:
            continue
        fm = parse_fm(text)
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        for t in tags:
            t = str(t).lower()
            if t and t not in STOPWORDS:
                tag_index[t].append(p)
        for w in WORD_RE.findall(str(fm.get("title", "")).lower()):
            if w not in STOPWORDS:
                word_index[w].append(p)

    if args.file:
        targets = [Path(args.file)]
    else:
        targets = sorted(STAGING_PAGES.glob("*.md"))
    if not targets:
        print("autolink: 无目标页面")
        return

    for path in targets:
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            self_stem = path.relative_to(WIKI).with_suffix("").as_posix()
        except ValueError:
            self_stem = path.with_suffix("").name
        have = valid_outlinks(text, stems, bases, self_stem)
        need = max(0, args.min - len(have))
        if need == 0:
            print(f"[skip] {path.name}: 已有 {len(have)} 个有效出链")
            continue
        fm = parse_fm(text)
        links = list(have)
        for cand in l1_candidates(fm, self_stem, tag_index, word_index, limit=args.min + 2):
            if cand not in links:
                links.append(cand)
        if not args.no_semantic and len(links) < args.min + 1:
            body = FM_RE.sub("", text)
            for cand in l2_candidates(body, self_stem, limit=args.min + 2):
                if cand not in links:
                    links.append(cand)
        for fallback in ("index", "log"):
            if fallback not in links:
                links.append(fallback)
        new_links = [l for l in links if l not in have][: max(need, 2)]
        block = "\n\n---\n关联: " + " · ".join(f"[[{l}]]" for l in new_links) + "\n"
        if args.apply:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(block)
        print(f"[{'apply' if args.apply else 'dry'}] {path.name}: +{len(new_links)} 链 -> {new_links}")


if __name__ == "__main__":
    main()
