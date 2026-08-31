#!/usr/bin/env python3
"""memory-hub MCP server (stdio) — 把 search/ask/inject 包装成 MCP 工具，让 Agent 主动检索记忆。"""
import collections
import glob
import json
import os
import subprocess
from pathlib import Path
try:
    from mcp.server.fastmcp import FastMCP
except (ImportError, AttributeError):
    import sys
    sys.path = [p for p in sys.path if p not in (".", "")]
    from mcp.server.fastmcp import FastMCP

from scripts.automation_core.query_planner import SearchRequest
from scripts.automation_core.service import MemoryService

HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp = FastMCP("memory-hub")

ACCESS_LOG = os.path.join(os.environ.get("MEMORY_HUB_DATA", os.path.expanduser("~/.memory-hub")),
                          "access.jsonl")


def _get_service() -> MemoryService:
    hub = Path(HUB).resolve()
    wiki = Path(os.environ.get("WIKI_PATH", str(Path.home() / "llm-wiki"))).resolve()
    data = Path(os.environ.get("MEMORY_HUB_DATA", str(Path.home() / ".memory-hub"))).resolve()
    return MemoryService(wiki, data, hub)


def _log(kind: str, query: str, refs: list = None) -> None:
    import time as _t
    try:
        os.makedirs(os.path.dirname(ACCESS_LOG), exist_ok=True)
        row = {"ts": _t.strftime("%Y-%m-%dT%H:%M:%S"), "src": "mcp",
               "m": "MCP", "kind": kind, "q": query[:160], "status": 200, "ms": 0}
        if refs:
            row["refs"] = refs[:8]
        with open(ACCESS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _run(script: str, *args: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(
            ["bash", f"{HUB}/scripts/{script}", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"错误: 执行超时（>{timeout}s）: {script}"
    except Exception as e:
        return f"错误: {type(e).__name__}: {e}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        return (out + f"\n[exit {r.returncode}] {err}").strip() if out else f"错误(exit {r.returncode}): {err or '无输出'}"
    return out or err or "（无输出）"


def _clamp_top(top: int, lo: int = 1, hi: int = 50) -> int:
    try:
        return max(lo, min(int(top), hi))
    except (TypeError, ValueError):
        return lo


@mcp.tool()
def memory_search(
    query: str,
    top: int = 10,
    expand: bool = True,
    fuse: bool = True,
    scope: str | None = None,
    scope_id: str | None = None,
    explain: bool = False,
) -> dict:
    """检索 memory-hub 知识库（默认 FTS5 bm25 + 向量 RRF 融合检索，k=60）。"""
    service = _get_service()
    request = SearchRequest(
        query=query[:500],
        top=_clamp_top(top),
        fuse=fuse,
        expand=expand,
        scope=scope,
        scope_id=scope_id,
        explain=explain,
    )
    resp = service.search(request)
    _log("search", query, [r.path for r in resp.results])
    return resp.to_dict()


@mcp.tool()
def memory_ask(
    question: str,
    top: int = 5,
    expand: bool = True,
    fuse: bool = True,
    scope: str | None = None,
    scope_id: str | None = None,
    explain: bool = False,
) -> dict:
    """基于 memory-hub 知识库问答。"""
    service = _get_service()
    request = SearchRequest(
        query=question[:500],
        top=_clamp_top(top),
        fuse=fuse,
        expand=expand,
        scope=scope,
        scope_id=scope_id,
        explain=explain,
    )
    ctx = service.ask_context(request)
    _log("ask", question, [r.path for r in ctx.results])
    return ctx.to_dict()


@mcp.tool()
def memory_inject() -> str:
    """获取记忆上下文。"""
    return _run("inject.sh", timeout=20)


@mcp.tool()
def memory_obs_search(query: str, top: int = 10, project: str = "") -> str:
    """检索原始观察记录。"""
    args = [query[:500], "--top", str(_clamp_top(top))]
    if project:
        args += ["--project", project]
    out = _run("obs_search.sh", *args, timeout=30)
    _log("obs_search", query)
    return out


@mcp.tool()
def memory_projects() -> str:
    """列出所有项目及其观察数。"""
    counts = collections.Counter()
    for f in glob.glob(f"{HUB}/staging/observations-*.jsonl"):
        try:
            with open(f) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        counts[json.loads(line).get("project", "unknown")] += 1
                    except Exception:
                        pass
        except Exception:
            pass
    if not counts:
        return "无观察数据（先运行 capture）"
    lines = [f"{proj}: {n} 条" for proj, n in counts.most_common()]
    return f"共 {len(counts)} 个项目：\n" + "\n".join(lines)


@mcp.tool()
def memory_status() -> str:
    """memory-hub 健康检查/统计。"""
    return _run("status.sh", timeout=20)


if __name__ == "__main__":
    mcp.run()

