#!/usr/bin/env python3
"""memory-hub REST server (stdlib only): 供外部 agent 查询记忆向量/知识库。

用法:
  server.py [--port 8787] [--host 127.0.0.1]

路由:
  GET /health   存活探针
  GET /status   健康统计（status.sh）
  GET /search?q=...&top=3&expand=1   检索（search.sh）
  GET /ask?q=...&top=5&expand=1      问答（ask.sh）
  GET /metrics  Prometheus 文本（metrics.sh）

 管理 API（供 codex++ 用户脚本 ui/memory-hub-admin.js 使用，全部 JSON + CORS）:
  GET    /api/overview                总览统计（页面/观察/索引/耗时）
  GET    /api/pages?type=&tag=&q=&offset=&limit=   页面列表（frontmatter 元数据，分页）
  GET    /api/page?path=              页面全文 + 解析后的 frontmatter
  POST   /api/page  {path, content}   写入页面（限 wiki 内 .md，≤2MB）
  DELETE /api/page?path=              移入 ~/.memory-hub/trash（可恢复，不真删）
  GET    /api/tags                    标签聚合计数
  GET    /api/observations?q=&project=&offset=&limit=  staging 原始观察（新→旧，分页）
"""
import argparse
import ipaddress
import socket
import glob
import json
import os
import re
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIKI = os.environ.get("WIKI_PATH", os.path.expanduser("~/llm-wiki"))
DATA_DIR = os.environ.get("MEMORY_HUB_DATA", os.path.expanduser("~/.memory-hub"))
TRASH_DIR = os.path.join(DATA_DIR, "trash")
STAGING = os.path.join(HUB, "staging")
SESSIONS_DIR = os.environ.get("CODEX_SESSIONS_DIR", os.path.expanduser("~/.codex/sessions"))
MAX_BODY = 2 * 1024 * 1024
EXCLUDE_DIRS = {"raw", "_legacy-para", "_archive"}
ACCESS_LOG = os.path.join(DATA_DIR, "access.jsonl")
ACCESS_MAX = 2 * 1024 * 1024  # 超过则轮转为 .1
OBSIDIAN_CLI = "/usr/local/bin/obsidian"

try:
    import yaml  # type: ignore
except Exception:
    yaml = None


class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer variant that binds an IPv6 sockaddr."""

    address_family = socket.AF_INET6


def resolve_loopback_bind_target(host: str, port: int) -> tuple[int, tuple]:
    """Resolve *host* and return a sockaddr only when every result is loopback.

    The resolved sockaddr is subsequently used directly for ``bind``. This
    prevents a hostname from being checked as loopback and then resolving to a
    different address when the HTTP server starts.
    """
    if not host or "\x00" in host:
        raise ValueError("--host 必须是可解析的 loopback 地址（如 127.0.0.1、localhost 或 ::1）")
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"--host 无法解析：{host!r}（仅允许 loopback 地址）") from exc

    valid_results = []
    rejected = []
    for family, _socktype, _proto, _canonname, sockaddr in results:
        if family not in (socket.AF_INET, socket.AF_INET6):
            rejected.append(repr(sockaddr))
            continue
        address = sockaddr[0]
        try:
            is_loopback = ipaddress.ip_address(address).is_loopback
        except ValueError:
            is_loopback = False
        if is_loopback:
            valid_results.append((family, sockaddr))
        else:
            rejected.append(address)

    if not valid_results or rejected:
        resolved = ", ".join(rejected) or "无有效地址"
        raise ValueError(
            f"拒绝监听非 loopback 地址：{host!r} 解析为 {resolved}；"
            "仅允许 127.0.0.1、localhost、::1 或其他纯 loopback 地址"
        )
    return valid_results[0]


def create_loopback_server(family: int, sockaddr: tuple) -> ThreadingHTTPServer:
    """Create an HTTP server for an already-validated loopback sockaddr."""
    server_class = IPv6ThreadingHTTPServer if family == socket.AF_INET6 else ThreadingHTTPServer
    return server_class(sockaddr, Handler)


def run_script(name: str, *args: str, timeout: int = 60) -> tuple[str, int]:
    try:
        r = subprocess.run(
            ["bash", f"{HUB}/scripts/{name}", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"错误: 执行超时（>{timeout}s）: {name}", 504
    except Exception as e:
        return f"错误: {type(e).__name__}: {e}", 500
    out = (r.stdout or "").strip()
    if r.returncode != 0:
        return (out + f"\n[exit {r.returncode}] {r.stderr.strip() or ''}").strip() or f"错误(exit {r.returncode})", 500
    return out or "（无输出）", 200


# ---------- 管理 API 辅助 ----------

def parse_frontmatter(text: str) -> dict:
    """解析 markdown frontmatter；优先 pyyaml，缺失时用迷你解析器（标量 + dash 列表）。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    if yaml:
        try:
            data = yaml.safe_load(block)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    meta: dict = {}
    key = ""
    for line in block.splitlines():
        if line.startswith("  - ") and key:
            meta.setdefault(key, []).append(line[4:].strip().strip("'\""))
        elif ":" in line and not line.startswith((" ", "\t")):
            k, _, v = line.partition(":")
            key = k.strip()
            v = v.strip().strip("'\"")
            meta[key] = v if v else []
    return meta


# rel_path -> (mtime, size, meta)；页面扫描缓存，按 mtime+size 失效
_PAGE_CACHE: dict = {}


def scan_pages() -> list:
    """遍历 wiki 全部 .md（排除 raw/_archive/_legacy-para），返回元数据列表，按 updated 新→旧。"""
    items = []
    for root, dirs, files in os.walk(WIKI):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for fn in files:
            if not fn.endswith(".md") or fn.endswith(".bak"):
                continue
            full = os.path.join(root, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, WIKI)
            cached = _PAGE_CACHE.get(rel)
            if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
                meta = cached[2]
            else:
                try:
                    with open(full, encoding="utf-8", errors="replace") as f:
                        meta = parse_frontmatter(f.read(8192))
                except OSError:
                    continue
                _PAGE_CACHE[rel] = (st.st_mtime, st.st_size, meta)
            tags = meta.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            items.append({
                "path": rel,
                "title": str(meta.get("title") or fn[:-3]),
                "type": str(meta.get("type") or os.path.dirname(rel) or "note"),
                "tags": [str(t) for t in tags],
                "updated": str(meta.get("updated") or ""),
                "abstract": str(meta.get("abstract") or ""),
                "size": st.st_size,
                "mtime": int(st.st_mtime),
            })
    items.sort(key=lambda x: (x["updated"], x["mtime"]), reverse=True)
    return items


def safe_wiki_path(rel: str):
    """把相对路径限制在 WIKI 内且必须是 .md，防路径穿越。"""
    if not rel or "\x00" in rel or rel.startswith(("/", "~")):
        return None
    base = os.path.realpath(WIKI)
    full = os.path.realpath(os.path.join(base, rel))
    if full.startswith(base + os.sep) and full.endswith(".md"):
        return full
    return None


def api_overview() -> dict:
    pages = scan_pages()
    by_type: dict = {}
    for p in pages:
        by_type[p["type"]] = by_type.get(p["type"], 0) + 1
    obs_files = sorted(glob.glob(os.path.join(STAGING, "observations-*.jsonl")),
                       key=os.path.getmtime, reverse=True)
    obs_lines = 0
    for f in obs_files:
        try:
            with open(f, "rb") as fh:
                obs_lines += sum(1 for _ in fh)
        except OSError:
            pass
    last_capture_age = None
    if obs_files:
        last_capture_age = int(time.time() - os.path.getmtime(obs_files[0]))
    db = os.path.join(DATA_DIR, "index.db")
    db_bytes = os.path.getsize(db) if os.path.isfile(db) else 0
    sessions_recent = 0
    cutoff = time.time() - 3 * 86400
    for root, _dirs, files in os.walk(SESSIONS_DIR):
        for fn in files:
            if fn.endswith(".jsonl"):
                try:
                    if os.path.getmtime(os.path.join(root, fn)) >= cutoff:
                        sessions_recent += 1
                except OSError:
                    pass
    timings: dict = {}
    tsv = os.path.join(DATA_DIR, "timings.tsv")
    if os.path.isfile(tsv):
        try:
            with open(tsv, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 3:
                        t = timings.setdefault(parts[1], {"count": 0, "total_ms": 0.0, "last_ms": 0.0})
                        t["count"] += 1
                        t["total_ms"] += float(parts[2])
                        t["last_ms"] = float(parts[2])
        except (OSError, ValueError):
            pass
    return {
        "wiki_pages": len(pages),
        "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "observations_files": len(obs_files),
        "observations_lines": obs_lines,
        "last_capture_age_seconds": last_capture_age,
        "index_db_bytes": db_bytes,
        "sessions_recent": sessions_recent,
        "timings": timings,
    }


def _recent_sessions() -> tuple[int, dict | None]:
    """近 3 天 .jsonl 会话数 + 最新文件信息。"""
    count = 0
    latest_mtime = -1
    latest_path = ""
    cutoff = time.time() - 3 * 86400
    try:
        for root, _dirs, files in os.walk(SESSIONS_DIR):
            for fn in files:
                if not fn.endswith(".jsonl"):
                    continue
                full = os.path.join(root, fn)
                try:
                    m = os.path.getmtime(full)
                except OSError:
                    continue
                if m >= cutoff:
                    count += 1
                if m > latest_mtime:
                    latest_mtime = m
                    latest_path = os.path.relpath(full, SESSIONS_DIR)
    except OSError:
        pass
    if not latest_path:
        return count, None
    return count, {
        "file": latest_path,
        "age_seconds": int(time.time() - latest_mtime),
    }


def _sqlite_count(path: str, table: str) -> int:
    try:
        import sqlite3  # stdlib
        with sqlite3.connect(path, timeout=5) as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()
            return int(row[0] or 0)
    except Exception:
        return 0


def _llm_proxy_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 10100), timeout=1.5):
            return True
    except Exception:
        return False


def _collect_realtime_lines(obs_files: list) -> int:
    lines = 0
    rt = os.path.join(STAGING, "observations-realtime.jsonl")
    if rt in obs_files:
        try:
            with open(rt, "rb") as fh:
                lines = sum(1 for _ in fh)
        except OSError:
            pass
    return lines


def _wiki_recent_pages(n: int = 5) -> list:
    """wiki 最近 mtime 更新的 .md 页面（排除 .obsidian 与 EXCLUDE_DIRS）。"""
    items: list = []
    try:
        for root, dirs, files in os.walk(WIKI):
            dirs[:] = [d for d in dirs
                       if d not in EXCLUDE_DIRS and d != ".obsidian"]
            for fn in files:
                if not fn.endswith(".md") or fn.endswith(".bak"):
                    continue
                full = os.path.join(root, fn)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                rel = os.path.relpath(full, WIKI)
                items.append({"path": rel, "mtime": int(st.st_mtime)})
    except OSError:
        return []
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:n]


def api_vitals() -> dict:
    """供管理面板「状态」页图像化使用的聚合指标（stdlib only）。"""
    pages = scan_pages()
    obs_files = sorted(glob.glob(os.path.join(STAGING, "observations-*.jsonl")),
                       key=os.path.getmtime, reverse=True)

    sessions_recent, latest_session = _recent_sessions()
    last_capture_age = None
    if obs_files:
        last_capture_age = int(time.time() - os.path.getmtime(obs_files[0]))

    db_bytes = 0
    db = os.path.join(DATA_DIR, "index.db")
    if os.path.isfile(db):
        try:
            db_bytes = os.path.getsize(db)
        except OSError:
            db_bytes = 0

    claude_mem_rows = 0
    cm = os.path.expanduser("~/.claude-mem/data/claude-mem.db")
    if os.path.isfile(cm):
        claude_mem_rows = _sqlite_count(cm, "observations")

    timings: dict = {}
    tsv = os.path.join(DATA_DIR, "timings.tsv")
    if os.path.isfile(tsv):
        try:
            with open(tsv, encoding="utf-8", errors="replace") as f:
                for line in f:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) >= 3:
                        t = timings.setdefault(parts[1],
                                               {"count": 0, "total_ms": 0.0, "last_ms": 0.0})
                        t["count"] += 1
                        t["total_ms"] += float(parts[2])
                        t["last_ms"] = float(parts[2])
        except (OSError, ValueError):
            pass

    return {
        "llm_proxy": _llm_proxy_reachable(),
        "claude_mem_rows": int(claude_mem_rows),
        "sessions_recent": int(sessions_recent),
        "latest_session": latest_session,
        "obs_files": len(obs_files),
        "realtime_lines": _collect_realtime_lines(obs_files),
        "wiki_pages": len(pages),
        "index_db_bytes": int(db_bytes),
        "last_capture_age_seconds": last_capture_age,
        "timings": timings,
        "recent_pages": _wiki_recent_pages(5),
    }


def api_pages(q: dict) -> dict:
    first = lambda k, d="": (q.get(k, [d])[0] or d)
    ftype, ftag, fq = first("type"), first("tag"), first("q").lower()
    fdir = first("dir")
    try:
        offset = max(0, int(first("offset", "0")))
        limit = min(200, max(1, int(first("limit", "50"))))
    except ValueError:
        offset, limit = 0, 50
    items = scan_pages()
    if fdir:
        items = [p for p in items if (os.path.dirname(p["path"]) or ".") == fdir]
    if ftype:
        items = [p for p in items if p["type"] == ftype]
    if ftag:
        items = [p for p in items if ftag in p["tags"]]
    if fq:
        items = [p for p in items
                 if fq in p["title"].lower() or fq in p["abstract"].lower()
                 or fq in p["path"].lower() or any(fq in t.lower() for t in p["tags"])]
    total = len(items)
    return {"total": total, "offset": offset, "limit": limit,
            "items": items[offset:offset + limit]}


def api_tree() -> dict:
    """vault 目录树（对齐 Obsidian 文件管理器）：每个目录的页面数。"""
    counts: dict = {}
    for p in scan_pages():
        d = os.path.dirname(p["path"]) or "."
        counts[d] = counts.get(d, 0) + 1
    folders = [{"dir": k, "count": v} for k, v in sorted(counts.items())]
    return {"folders": folders, "total": sum(counts.values())}


def api_observations(q: dict) -> dict:
    first = lambda k, d="": (q.get(k, [d])[0] or d)
    fq, fproj = first("q").lower(), first("project")
    try:
        offset = max(0, int(first("offset", "0")))
        limit = min(200, max(1, int(first("limit", "50"))))
    except ValueError:
        offset, limit = 0, 50
    files = sorted(glob.glob(os.path.join(STAGING, "observations-*.jsonl")),
                   key=os.path.getmtime, reverse=True)
    out, skipped = [], 0
    for f in files:
        if len(out) > limit:
            break
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in reversed(lines):  # 文件内也是新→旧
            try:
                ob = json.loads(line)
            except ValueError:
                continue
            if fproj and ob.get("project") != fproj:
                continue
            text = str(ob.get("text") or "")
            if fq and fq not in text.lower():
                continue
            if skipped < offset:
                skipped += 1
                continue
            out.append({
                "project": ob.get("project"),
                "role": ob.get("role"), "type": ob.get("type"),
                "created_at": ob.get("created_at"), "text": text[:300],
            })
            if len(out) > limit:
                break
    return {"items": out[:limit], "has_more": len(out) > limit,
            "offset": offset, "limit": limit}


# ---------- 调用日志（agent 可观测性）----------

SKIP_LOG_PATHS = {"/health", "/", "/api/calls"}  # 健康轮询不记，减少噪音


def access_kind(method: str, path: str) -> str:
    if path == "/search":
        return "search"
    if path == "/ask":
        return "ask"
    if path == "/api/page":
        return {"GET": "page-read", "POST": "page-write", "DELETE": "page-delete"}.get(method, "page")
    if path.startswith("/api/"):
        return path[5:]
    return path.strip("/") or "root"


_RESULT_PATH_RE = re.compile(r"^\[[^\]]*\]\s+(\S+?\.md)\s*$", re.M)


def extract_md_refs(text: str, limit: int = 8) -> list:
    """从 search/ask 脚本输出里解析命中的 wiki 页面路径。"""
    out = []
    for m in _RESULT_PATH_RE.finditer(text or ""):
        p = m.group(1)
        if p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out


def log_access(method: str, path: str, query: str, status: int, ms: float,
               refs: list = None) -> None:
    if path in SKIP_LOG_PATHS:
        return
    try:
        if os.path.isfile(ACCESS_LOG) and os.path.getsize(ACCESS_LOG) > ACCESS_MAX:
            os.replace(ACCESS_LOG, ACCESS_LOG + ".1")
        os.makedirs(DATA_DIR, exist_ok=True)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "src": "rest",
            "m": method,
            "kind": access_kind(method, path),
            "q": query[:160],
            "status": status,
            "ms": round(ms, 1),
        }
        if refs:
            row["refs"] = refs[:8]
        with open(ACCESS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def access_hot_map() -> dict:
    """聚合 access.jsonl 里的 refs → {页面路径: agent 调用次数}（图谱热度层）。"""
    hot: dict = {}
    for f in (ACCESS_LOG + ".1", ACCESS_LOG):
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    for r in row.get("refs") or []:
                        hot[r] = hot.get(r, 0) + 1
        except OSError:
            continue
    return hot


def api_calls(q: dict) -> dict:
    first = lambda k, d="": (q.get(k, [d])[0] or d)
    fkind = first("kind")
    try:
        offset = max(0, int(first("offset", "0")))
        limit = min(200, max(1, int(first("limit", "50"))))
    except ValueError:
        offset, limit = 0, 50
    rows = []
    for f in (ACCESS_LOG + ".1", ACCESS_LOG):
        if not os.path.isfile(f):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            continue
    rows.reverse()  # 新→旧（.1 旧文件在前、主文件在后，整体反转即最新优先）
    if fkind:
        rows = [r for r in rows if r.get("kind") == fkind or
                (fkind == "mcp" and str(r.get("src")) == "mcp")]
    total = len(rows)
    return {"total": total, "offset": offset, "limit": limit,
            "items": rows[offset:offset + limit]}


# ---------- 图谱（Obsidian 风格 [[wikilink]] 关系图）----------

_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
_LINKS_CACHE: dict = {}  # rel -> (mtime, size, [targets])


def page_links(rel: str, st) -> list:
    cached = _LINKS_CACHE.get(rel)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    try:
        with open(os.path.join(WIKI, rel), encoding="utf-8", errors="replace") as f:
            body = f.read()
    except OSError:
        return []
    targets = [m.group(1).strip() for m in _LINK_RE.finditer(body)]
    _LINKS_CACHE[rel] = (st.st_mtime, st.st_size, targets)
    return targets


def obsidian_graph_shot():
    """激活运行中 Obsidian 的图谱视图并截屏。返回 (png_bytes, err)。"""
    if not os.path.isfile(OBSIDIAN_CLI):
        return None, "obsidian CLI 不存在（/usr/local/bin/obsidian）"
    vault = os.path.basename(os.path.realpath(WIKI))

    def _eval(code: str) -> None:
        try:
            subprocess.run([OBSIDIAN_CLI, "eval", f"code={code}"],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    try:
        # 尽力让图谱视图前置（Obsidian 未运行时此调用会失败，忽略）
        subprocess.run([OBSIDIAN_CLI, "command", "id=graph:open", f"vault={vault}"],
                       capture_output=True, timeout=10)
    except Exception:
        pass
    # 收起左右侧栏，让截图只剩纯图谱；截完在 finally 里恢复
    _eval('app.workspace.leftSplit.collapse(); app.workspace.rightSplit.collapse(); "ok"')
    time.sleep(1.2)  # 等视图切换与力导向渲染
    tmpdir = tempfile.gettempdir()
    before = set(glob.glob(os.path.join(tmpdir, "obsidian-screenshot-*")))
    try:
        r = subprocess.run([OBSIDIAN_CLI, "dev:screenshot"],
                           capture_output=True, text=True, timeout=15)
    except Exception as e:
        return None, f"截图失败：{type(e).__name__}: {e}"
    finally:
        _eval('app.workspace.leftSplit.expand(); app.workspace.rightSplit.expand(); "ok"')
    path = (r.stdout or "").strip()
    if not os.path.isfile(path):
        new = sorted(set(glob.glob(os.path.join(tmpdir, "obsidian-screenshot-*"))) - before,
                     key=os.path.getmtime, reverse=True)
        path = new[0] if new else ""
    if not path or not os.path.isfile(path):
        return None, "未找到截图输出（Obsidian 是否在运行？）"
    try:
        with open(path, "rb") as f:
            return f.read(), None
    except OSError as e:
        return None, f"读取截图失败：{e}"


def api_graph(include_atoms: bool) -> dict:
    pages = scan_pages()
    by_stem = {p["path"][:-3]: p for p in pages if p["path"].endswith(".md")}
    by_name: dict = {}
    for p in pages:
        if p["path"].endswith(".md"):
            by_name.setdefault(os.path.basename(p["path"])[:-3], p)
        if p["title"]:
            by_name.setdefault(p["title"], p)
    nodes, ids = [], set()
    hot = access_hot_map()
    for p in pages:
        if not include_atoms and p["type"] == "atom":
            continue
        ids.add(p["path"])
        nodes.append({"id": p["path"], "title": p["title"], "type": p["type"],
                      "size": p["size"], "hot": hot.get(p["path"], 0)})
    edges, seen = [], set()
    for p in pages:
        src = p["path"]
        if src not in ids:
            continue
        try:
            st = os.stat(os.path.join(WIKI, src))
        except OSError:
            continue
        for t in page_links(src, st):
            tgt = by_stem.get(t) or by_name.get(t)
            if not tgt or tgt["path"] == src or tgt["path"] not in ids:
                continue
            key = (src, tgt["path"])
            if key not in seen:
                seen.add(key)
                edges.append({"source": src, "target": tgt["path"]})
    if include_atoms:
        # atom 只保留有连线的；3000+ 个孤立 atom 会把图谱变成噪点
        linked = {e["source"] for e in edges} | {e["target"] for e in edges}
        nodes = [n for n in nodes if n["type"] != "atom" or n["id"] in linked]
    # 对齐 vault 的 .obsidian/graph.json 配置：showTags 时把标签渲染为节点（与 Obsidian 图谱一致）
    obs_cfg: dict = {}
    try:
        with open(os.path.join(WIKI, ".obsidian", "graph.json"), encoding="utf-8") as f:
            obs_cfg = json.load(f)
    except (OSError, ValueError):
        pass
    show_tags = bool(obs_cfg.get("showTags"))
    if show_tags:
        by_id = {n["id"]: n for n in nodes}
        tag_nodes: dict = {}
        for p in pages:
            if p["path"] not in by_id:
                continue
            for t in p["tags"]:
                tid = "tag:" + t
                if tid not in tag_nodes:
                    tag_nodes[tid] = {"id": tid, "title": "#" + t,
                                      "type": "tag", "size": 0, "hot": 0}
                key = (p["path"], tid)
                if key not in seen:
                    seen.add(key)
                    edges.append({"source": p["path"], "target": tid})
        nodes.extend(tag_nodes.values())
    return {"nodes": nodes, "edges": edges,
            "obsidian": {"showTags": show_tags,
                         "hideUnresolved": bool(obs_cfg.get("hideUnresolved")),
                         "showOrphans": bool(obs_cfg.get("showOrphans"))}}

class Handler(BaseHTTPRequestHandler):
    # send_response 包一层以捕获状态码，供访问日志使用
    def send_response(self, code, message=None):
        self._mh_status = code
        super().send_response(code, message)

    def _with_log(self, method: str, fn):
        t0 = time.time()
        self._mh_refs = None
        try:
            fn()
        finally:
            u = urlparse(self.path)
            log_access(method, u.path, u.query,
                       getattr(self, "_mh_status", 0),
                       (time.time() - t0) * 1000,
                       refs=self._mh_refs)

    def do_GET(self):
        self._with_log("GET", self._do_GET)

    def do_POST(self):
        self._with_log("POST", self._do_POST)

    def do_DELETE(self):
        self._with_log("DELETE", self._do_DELETE)

    def _do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        first = lambda k, d="": (q.get(k, [d])[0] or d)

        if u.path == "/health":
            return self._json({"status": "ok"})
        if u.path == "/":
            return self._json({
                "name": "memory-hub REST",
                "管理面板预览": "http://127.0.0.1:8902/dev-harness.html",
                "端点": ["/health", "/status", "/search?q=&top=", "/ask?q=", "/metrics",
                        "/api/vitals", "/api/overview", "/api/pages?type=&tag=&q=&offset=&limit=",
                        "/api/page?path= (GET/POST/DELETE)", "/api/tags",
                        "/api/observations?q=&project=&offset=&limit=",
                        "/api/calls?kind=&offset=&limit=", "/api/graph?include_atoms="],
            })
        if u.path == "/status":
            return self._text(*run_script("status.sh", timeout=20))
        if u.path == "/search":
            args = [first("q")[:500], "--top", first("top", "10")]
            if first("expand", "0") in ("1", "true", "yes"):
                args.append("--expand")
            if first("fuse", "1") in ("1", "true", "yes"):
                args.append("--fuse")
            body, code = run_script("search.sh", *args, timeout=40)
            self._mh_refs = extract_md_refs(body)
            return self._text(body, code)
        if u.path == "/ask":
            args = [first("q")[:500], "--top", first("top", "5")]
            if first("expand", "0") in ("1", "true", "yes"):
                args.append("--expand")
            if first("fuse", "1") in ("1", "true", "yes"):
                args.append("--fuse")
            body, code = run_script("ask.sh", *args, timeout=90)
            self._mh_refs = extract_md_refs(body)
            return self._text(body, code)
        if u.path == "/metrics":
            return self._text(*run_script("metrics.sh", timeout=20))
        # ----- 管理 API（JSON）-----
        if u.path == "/api/overview":
            return self._json(api_overview())
        if u.path == "/api/vitals":
            return self._json(api_vitals())
        if u.path == "/api/pages":
            return self._json(api_pages(q))
        if u.path == "/api/tree":
            return self._json(api_tree())
        if u.path == "/api/page":
            full = safe_wiki_path(first("path"))
            if not full or not os.path.isfile(full):
                return self._json({"error": "页面不存在或路径非法"}, 404)
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError as e:
                return self._json({"error": f"读取失败: {e}"}, 500)
            rel = os.path.relpath(full, os.path.realpath(WIKI))
            return self._json({"path": rel, "content": content,
                               "meta": parse_frontmatter(content)})
        if u.path == "/api/tags":
            counts: dict = {}
            for p in scan_pages():
                for t in p["tags"]:
                    counts[t] = counts.get(t, 0) + 1
            tags = [{"tag": k, "count": v}
                    for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
            return self._json({"tags": tags, "total": len(tags)})
        if u.path == "/api/observations":
            return self._json(api_observations(q))
        if u.path == "/api/calls":
            return self._json(api_calls(q))
        if u.path == "/api/graph":
            return self._json(api_graph(first("include_atoms") in ("1", "true", "yes")))
        if u.path == "/api/obsidian/graph-shot":
            png, err = obsidian_graph_shot()
            if err:
                return self._json({"error": err}, 503)
            return self._bin(png, "image/png")
        self._json({"error": "not found"}, 404)

    def _do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/page":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > MAX_BODY:
                return self._json({"error": "请求体过大（>2MB）"}, 413)
            try:
                payload = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json({"error": "JSON 解析失败"}, 400)
            full = safe_wiki_path(str(payload.get("path") or ""))
            if not full:
                return self._json({"error": "路径非法：必须在 wiki 内且为 .md"}, 400)
            content = str(payload.get("content") or "")
            try:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
            except OSError as e:
                return self._json({"error": f"写入失败: {e}"}, 500)
            rel = os.path.relpath(full, os.path.realpath(WIKI))
            _PAGE_CACHE.pop(rel, None)
            self._mh_refs = [rel]
            return self._json({"ok": True, "bytes": len(content.encode("utf-8"))})
        self._json({"error": "not found"}, 404)

    def _do_DELETE(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/page":
            full = safe_wiki_path((q.get("path", [""])[0] or ""))
            if not full or not os.path.isfile(full):
                return self._json({"error": "页面不存在或路径非法"}, 404)
            rel = os.path.relpath(full, os.path.realpath(WIKI))
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dest = os.path.join(TRASH_DIR, f"{stamp}-{rel.replace(os.sep, '__')}")
            try:
                os.makedirs(TRASH_DIR, exist_ok=True)
                os.replace(full, dest)
            except OSError as e:
                return self._json({"error": f"移除失败: {e}"}, 500)
            _PAGE_CACHE.pop(rel, None)
            self._mh_refs = [rel]
            return self._json({"ok": True, "trash": dest,
                               "hint": "已移入回收站（非永久删除），可从该路径恢复"})
        self._json({"error": "not found"}, 404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cors(self):
        # 仅监听 127.0.0.1；放开 CORS 供 Codex 渲染进程内的用户脚本跨源调用
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _text(self, body: str, code: int = 200):
        data = body.encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _bin(self, data: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")


def main():
    ap = argparse.ArgumentParser(description="memory-hub REST server")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1",
                    help="仅允许 loopback 地址（默认：127.0.0.1）")
    args = ap.parse_args()
    try:
        family, sockaddr = resolve_loopback_bind_target(args.host, args.port)
    except ValueError as exc:
        ap.error(str(exc))
    srv = create_loopback_server(family, sockaddr)
    bound_host, bound_port = srv.server_address[:2]
    display_host = f"[{bound_host}]" if family == socket.AF_INET6 else bound_host
    print(f"memory-hub REST: http://{display_host}:{bound_port}  (Ctrl-C 退出)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
