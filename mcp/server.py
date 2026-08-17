#!/usr/bin/env python3
"""memory-hub MCP server (stdio) — 把 search/ask/inject 包装成 MCP 工具，让 Agent 主动检索记忆。"""
import glob
import json
import collections
import os
import subprocess
from mcp.server.fastmcp import FastMCP

# 项目根目录相对定位（server.py 在 mcp/ 子目录，向上一级即项目根）——不依赖硬编码绝对路径，可移植
HUB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mcp = FastMCP("memory-hub")

ACCESS_LOG = os.path.join(os.environ.get("MEMORY_HUB_DATA", os.path.expanduser("~/.memory-hub")),
                          "access.jsonl")


def _log(kind: str, query: str, refs: list = None) -> None:
    """把 agent 的 MCP 调用追加到 access.jsonl（与 REST 服务共用，供管理面板展示）。失败静默。"""
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


def _md_refs(text: str, limit: int = 8) -> list:
    """从 search/ask 输出解析命中的 wiki 页面路径（[score] path.md 行）。"""
    import re as _re
    out = []
    for m in _re.finditer(r"^\[[^\]]*\]\s+(\S+?\.md)\s*$", text or "", _re.M):
        p = m.group(1)
        if p not in out:
            out.append(p)
        if len(out) >= limit:
            break
    return out


def _run(script: str, *args: str, timeout: int = 60) -> str:
    """执行 memory-hub 脚本并返回输出。异常（超时/脚本不存在等）统一捕获返回错误字符串，不崩溃 MCP 工具。"""
    try:
        r = subprocess.run(
            ["bash", f"{HUB}/scripts/{script}", *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"错误: 执行超时（>{timeout}s）: {script}"
    except Exception as e:  # FileNotFoundError/PermissionError/OSError 等
        return f"错误: {type(e).__name__}: {e}"
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.returncode != 0:
        # 非零退出：保留 stdout（部分结果）+ 附 stderr（错误可观测）
        return (out + f"\n[exit {r.returncode}] {err}").strip() if out else f"错误(exit {r.returncode}): {err or '无输出'}"
    return out or err or "（无输出）"


def _clamp_top(top: int, lo: int = 1, hi: int = 50) -> int:
    """工具参数边界校验：top 限制在合理范围，防极大值拖慢 DB。"""
    try:
        return max(lo, min(int(top), hi))
    except (TypeError, ValueError):
        return lo


@mcp.tool()
def memory_search(query: str, top: int = 10, expand: bool = False, fuse: bool = True) -> str:
    """检索 memory-hub 知识库（默认 FTS5 bm25 + 向量 RRF 融合检索，k=60）。
    query: 检索关键词（可多个，空格分隔）
    top: 结果数上限（默认 10）
    expand: True 时先做 LLM 查询扩展（语义检索，生成相关关键词）
    fuse: True 时使用 fuse.py 融合检索（默认开启），False 走独立 FTS5"""
    args = [query[:500], "--top", str(_clamp_top(top))]
    if expand:
        args.append("--expand")
    if fuse:
        args.append("--fuse")
    out = _run("search.sh", *args, timeout=40 if expand else 20)
    _log("search", query, _md_refs(out))
    return out


@mcp.tool()
def memory_ask(question: str, top: int = 5, expand: bool = False, fuse: bool = True) -> str:
    """基于 memory-hub 知识库问答（FTS5 检索相关页 + 免费模型生成回答，引用来源页面）。
    question: 问题
    top: 检索页数（默认 5）
    expand: True 时检索前先做 LLM 查询扩展
    fuse: True 时使用 fuse.py 融合检索（默认开启），False 走独立 FTS5"""
    args = [question[:500], "--top", str(_clamp_top(top))]
    if expand:
        args.append("--expand")
    if fuse:
        args.append("--fuse")
    out = _run("ask.sh", *args, timeout=90)
    _log("ask", question, _md_refs(out))
    return out


@mcp.tool()
def memory_inject() -> str:
    """获取记忆上下文（知识库最近更新 5 页 + 最新采集观察 10 条 + 统计），用于了解最近记忆。"""
    return _run("inject.sh", timeout=20)


@mcp.tool()
def memory_obs_search(query: str, top: int = 10, project: str = "") -> str:
    """检索原始观察记录（staging 未蒸馏的会话观察，对齐 claude-mem mcp-search 的 search）。
    与 memory_search（检索 llm-wiki 蒸馏页）互补：这里检索原始会话轨迹。
    query: 检索关键词
    top: 结果数上限（默认 10）
    project: 可选，只看指定项目"""
    args = [query[:500], "--top", str(_clamp_top(top))]
    if project:
        args += ["--project", project]
    out = _run("obs_search.sh", *args, timeout=30)
    _log("obs_search", query)
    return out


@mcp.tool()
def memory_projects() -> str:
    """列出所有项目及其观察数（对齐 claude-mem mcp-search 的 list_corpora）。"""
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
    """memory-hub 健康检查/统计（会话数、观察数、知识库页数、索引状态、LLM 代理可达性）。"""
    return _run("status.sh", timeout=20)


if __name__ == "__main__":
    mcp.run()
