#!/usr/bin/env python3
"""Plugin hook entry: workspace-aware dispatch for memory-hub.

Reads the Codex hook JSON from stdin, maps payload cwd to a workspace-specific
data/wiki pair using a runtime file, then calls codex_memory.dispatch in-process.

Runtime file lookup order (first hit wins):
  1. $MEMORY_HUB_RUNTIME (explicit path to runtime JSON)
  2. $MEMORY_HUB_DATA/codex-runtime.json
  3. ~/.memory-hub/codex-runtime.json

Runtime JSON shape:
  {"hub_root": "...", "python_path": "...",
   "data_path": "...", "wiki_path": "...",
   "workspaces": {"/abs/cwd": {"data_path": "...", "wiki_path": "..."}}}

Environment variables MEMORY_HUB_DATA / WIKI_PATH, when explicitly set, always
override both the workspace mapping and the top-level runtime defaults.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _runtime_file() -> Path | None:
    explicit = os.environ.get("MEMORY_HUB_RUNTIME")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    data_env = os.environ.get("MEMORY_HUB_DATA")
    candidates = []
    if data_env:
        candidates.append(Path(data_env).expanduser() / "codex-runtime.json")
    candidates.append(Path.home() / ".memory-hub" / "codex-runtime.json")
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_runtime() -> dict:
    f = _runtime_file()
    return _read_json(f) if f else {}


def _is_within(cwd: Path, root: Path) -> bool:
    try:
        cwd.relative_to(root)
        return True
    except ValueError:
        return False


def _match_workspace(workspaces: dict, cwd: Path) -> dict:
    best: tuple[int, dict] | None = None
    for key, value in (workspaces or {}).items():
        if not isinstance(value, dict):
            continue
        root = Path(os.path.expanduser(str(key)))
        if not root.is_absolute():
            continue
        try:
            root = root.resolve()
        except OSError:
            continue
        if cwd == root or _is_within(cwd, root):
            depth = len(root.parts)
            if best is None or depth > best[0]:
                best = (depth, value)
    return best[1] if best else {}


def _resolve_hub(cfg: dict) -> Path | None:
    """Locate the memory-hub repo for importing codex_memory.

    Priority: runtime hub_root, then plugin-source layout (parents[3]),
    then MEMORY_HUB_HUB env. Returns None when no valid repo is found.
    """
    candidates = []
    if cfg.get("hub_root"):
        candidates.append(Path(os.path.expanduser(str(cfg["hub_root"]))))
    candidates.append(Path(__file__).resolve().parents[3])
    if os.environ.get("MEMORY_HUB_HUB"):
        candidates.append(Path(os.path.expanduser(os.environ["MEMORY_HUB_HUB"])))
    for c in candidates:
        try:
            if (c / "scripts" / "automation_core" / "codex_memory.py").is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def _resolve_paths(payload: dict, cfg: dict) -> tuple[Path, Path]:
    raw_cwd = payload.get("cwd") or os.getcwd()
    try:
        cwd = Path(str(raw_cwd)).expanduser().resolve()
    except OSError:
        cwd = Path(os.getcwd()).resolve()

    workspace = _match_workspace(cfg.get("workspaces") or {}, cwd)

    data = (os.environ.get("MEMORY_HUB_DATA")
            or workspace.get("data_path") or cfg.get("data_path")
            or str(Path.home() / ".memory-hub"))
    wiki = (os.environ.get("WIKI_PATH")
            or workspace.get("wiki_path") or cfg.get("wiki_path")
            or str(Path.home() / "llm-wiki"))
    return Path(data).expanduser(), Path(wiki).expanduser()


def _dispatch_safely(payload: dict, cfg: dict, data: Path, wiki: Path) -> dict:
    hub = _resolve_hub(cfg)
    if hub is None:
        return {}
    if str(hub) not in sys.path:
        sys.path.insert(0, str(hub))
    try:
        from scripts.automation_core.codex_memory import dispatch
        return dispatch(payload, data, wiki)
    except Exception:
        return {}


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024)
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        if not payload.get("hook_event_name") and len(sys.argv) > 1:
            payload["hook_event_name"] = sys.argv[1]
        cfg = _load_runtime()
        data, wiki = _resolve_paths(payload, cfg)
        result = _dispatch_safely(payload, cfg, data, wiki)
    except Exception:
        result = {}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
