"""Resolve only explicitly authorized workspace mappings supplied by host roots."""
import asyncio
import json
import os
from pathlib import Path
from .adapter import from_environment
from .contracts import require
from .host_roots import select_data_root


async def from_context(ctx):
    if os.environ.get('MEMORY_HUB_EXPERIENCE_HOST_ROOTS') != '1':
        return from_environment()
    try:
        from mcp.types import ClientCapabilities, RootsCapability
    except ModuleNotFoundError:
        ClientCapabilities = RootsCapability = None
    runtime=Path(os.environ['MEMORY_HUB_EXPERIENCE_RUNTIME'])
    config=json.loads(runtime.read_text())
    data=None
    cwd_root=select_data_root([Path.cwd().resolve().as_uri()], config.get('workspaces', {}))
    if ClientCapabilities is not None and ctx is not None and ctx.session.check_client_capability(ClientCapabilities(roots=RootsCapability())):
        roots=await asyncio.wait_for(ctx.session.list_roots(),timeout=5)
        data=select_data_root([str(r.uri) for r in roots.roots],config.get('workspaces',{}))
    else:
        # Host-provided roots are authoritative, even when none is authorized.
        # Process cwd is only usable when the host cannot supply roots.
        data=cwd_root
    require(data is not None,'no unique authorized workspace root','WORKSPACE_UNAVAILABLE')
    return from_environment(data)
