#!/usr/bin/env python3
"""memory-hub embed: 语义/向量索引 + 检索 (fastembed + SQLite BLOB)

用法:
  embed.py index          增量向量化 ~/llm-wiki 中未入库的 markdown 页 → index.db `vec` 表
  embed.py search "词" -n N   查询向量化 + 余弦相似度 top N
  embed.py model           打印当前向量模型信息

零依赖脚本:fastembed(本地 onnx)+ numpy + sqlite3。向量存 BLOB(float32),检索用 numpy 批量余弦。
"""
import argparse
import math
import os
import sqlite3
import time
from pathlib import Path
import struct
import sys
from pathlib import Path

# 与 index.sh 同源的数据目录/知识库路径
DATA_DIR = Path(os.environ.get("MEMORY_HUB_DATA", str(Path.home() / ".memory-hub")))
DB = DATA_DIR / "index.db"
WIKI = Path(os.environ.get("WIKI_PATH", str(Path.home() / "llm-wiki")))
MODEL_NAME = os.environ.get("MEMORY_HUB_EMBED_MODEL", "BAAI/bge-small-zh-v1.5")

# 与 index.sh 相同的排除规则
EXCLUDES = ("/raw/", "/_legacy-para/", "/_archive/", "/.git/")


def load_model():
    from fastembed import TextEmbedding

    return TextEmbedding(MODEL_NAME)


def pk_get_batch(items, n=32):
    """分批,避免一次吃太多内存。"""
    for i in range(0, len(items), n):
        yield items[i : i + n]


def pages_to_embed():
    """扫描 wiki,返回未入库的 (rel_path, title, text_for_embedding)。"""
    if not WIKI.is_dir():
        print(f"错误: 知识库不存在: {WIKI}", file=sys.stderr)
        sys.exit(1)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS vec(path TEXT PRIMARY KEY, title TEXT, dim INT, v BLOB)")
    indexed = {r[0] for r in db.execute("SELECT path FROM vec")}
    db.close()

    todo = []
    for f in WIKI.rglob("*.md"):
        rel = str(f.relative_to(WIKI))
        if any(x in "/" + rel + "/" for x in EXCLUDES):
            continue
        if rel in indexed:
            continue
        raw = f.read_text(encoding="utf-8", errors="ignore")
        # 提取 title(取 frontmatter title 或文件名),正文去掉 frontmatter
        title = ""
        lines = raw.split("\n")
        if lines and lines[0].strip() == "---":
            for ln in lines[1:]:
                if ln.strip() == "---":
                    break
                if ln.startswith("title:"):
                    title = ln.split(":", 1)[1].strip().strip("'\"")
        if not title:
            title = f.stem
        # 正文:第二个 --- 之后
        body = raw
        if lines and lines[0].strip() == "---":
            try:
                second = raw.index("\n---", 1)
                body = raw[second + 4 :]
            except ValueError:
                pass
        text = (title + "\n" + body)[:1500]
        todo.append((rel, title, text))
    return todo


def cmd_index():
    model = load_model()
    todo = pages_to_embed()
    if not todo:
        print("embed: 无新页面需向量化(已全部入库)")
        return
    dim = len(list(model.embed([""]))[0])
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS vec(path TEXT PRIMARY KEY, title TEXT, dim INT, v BLOB)")
    for batch in pk_get_batch(todo):
        texts = [t[2] for t in batch]
        vecs = list(model.embed(texts))
        for (rel, title, _), v in zip(batch, vecs):
            blob = v.astype("<f4").tobytes()
            db.execute(
                "INSERT OR REPLACE INTO vec(path,title,dim,v) VALUES(?,?,?,?)",
                (rel, title, dim, blob),
            )
        db.commit()
        print(f"embed: +{len(batch)} 页 (累计 {len(batch)} 本批)")
    total = db.execute("SELECT count(*) FROM vec").fetchone()[0]
    print(f"embed: 索引完成,共 {total} 页 -> {DB}")
    db.close()


def cmd_search(query, top):
    model = load_model()
    if not DB.is_file():
        print(f"错误: 向量索引不存在(先运行: memory-hub.sh embed index): {DB}", file=sys.stderr)
        sys.exit(1)
    db = sqlite3.connect(DB)
    rows = db.execute("SELECT path, title, dim, v FROM vec").fetchall()
    db.close()
    if not rows:
        print("embed: 向量索引为空(先运行: memory-hub.sh embed index)", file=sys.stderr)
        sys.exit(1)
    dim = rows[0][2]
    # 全库向量 -> numpy 矩阵
    import numpy as np

    mat = np.empty((len(rows), dim), dtype="<f4")
    for i, (_, _, _, blob) in enumerate(rows):
        mat[i] = np.frombuffer(blob, dtype="<f4")
    qv = np.asarray(list(model.embed([query]))[0], dtype="<f4")
    # 余弦相似度 = 归一化后点积
    qnorm = qv / (np.linalg.norm(qv) + 1e-9)
    matnorm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    sims = matnorm @ qnorm
    order = np.argsort(-sims)[:top]
    print(f"== 向量检索 ({MODEL_NAME}): {query} (top {top}) ==")
    for i in order:
        print(f"[{sims[i]:.3f}] {rows[i][0]} — {rows[i][1]}")


def cmd_model():
    model = load_model()
    print(f"向量模型: {MODEL_NAME} (维度 {model.embedding_size})")


def main():
    ap = argparse.ArgumentParser(description="memory-hub 语义/向量索引与检索")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("index")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-n", "--top", type=int, default=10)
    sub.add_parser("model")
    args = ap.parse_args()
    if args.cmd == "index":
        cmd_index()
    elif args.cmd == "search":
        cmd_search(args.query, args.top)
    elif args.cmd == "model":
        cmd_model()


if __name__ == "__main__":
    start = time.time()
    try:
        main()
        rc = 0
    except SystemExit as e:
        rc = int(e.code or 0)
        raise
    except Exception:
        rc = 1
        raise
    finally:
        data_dir = os.environ.get("MEMORY_HUB_DATA") or str(Path.home() / ".memory-hub")
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        ms = int((time.time() - start) * 1000)
        with open(Path(data_dir) / "timings.tsv", "a") as f:
            f.write(f"{int(time.time())}\tembed\t{ms}\t{rc}\n")
