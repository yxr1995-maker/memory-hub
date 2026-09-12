#!/usr/bin/env python3
"""Resolve MCP paths using the same installation settings as the Hook entry."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


def main() -> int:
    entry = Path(__file__).resolve().parents[1] / 'hooks' / 'codex_hook_entry.py'
    adapter = runpy.run_path(str(entry))
    config = adapter['_load_runtime']()
    hub_value = config.get('hub_root') or os.environ.get('MEMORY_HUB_HUB')
    if not isinstance(hub_value, str) or not hub_value:
        raise ValueError('missing hub_root')
    hub = Path(hub_value).expanduser()
    if not hub.is_absolute() or not (hub / 'mcp' / 'server.py').is_file():
        raise ValueError('invalid hub_root')
    workspace = os.environ.get('MEMORY_HUB_WORKSPACE') or os.getcwd()
    if not Path(workspace).is_absolute():
        raise ValueError('workspace must be absolute')
    data, wiki = adapter['_resolve_paths']({'cwd': workspace}, config)
    if not data.is_absolute() or not wiki.is_absolute():
        raise ValueError('memory paths must be absolute')
    env = dict(os.environ, MEMORY_HUB_DATA=str(data), WIKI_PATH=str(wiki))
    # Distinguish explicit scope from a default derived from the plugin cwd.
    explicit_scope = any(os.environ.get(k) is not None for k in (
        'MEMORY_HUB_DATA', 'MEMORY_HUB_WORKSPACE',
        'MEMORY_HUB_EXPERIENCE_COLLECTIONS', 'MEMORY_HUB_EXPERIENCE_ROOTS'))
    mapped = adapter['_match_workspace'](config.get('workspaces', {}), Path(workspace).resolve())
    runtime_file = adapter['_runtime_file']()
    env['MEMORY_HUB_EXPERIENCE_HOST_ROOTS'] = '1' if runtime_file and not explicit_scope and not mapped else '0'
    if runtime_file:
        env['MEMORY_HUB_EXPERIENCE_RUNTIME'] = str(runtime_file)

    env['PYTHONPATH'] = str(hub) + (os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
    python = config.get('python_path') or sys.executable
    os.execvpe(python, [python, str(hub / 'mcp' / 'server.py')], env)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError):
        print('memory-hub MCP: invalid or unavailable runtime configuration', file=sys.stderr)
        raise SystemExit(1)
