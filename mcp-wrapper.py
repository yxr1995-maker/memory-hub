#!/usr/bin/env python3
"""Memory Hub MCP wrapper - avoids naming conflict with mcp/ directory"""
import sys
import os

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Rename conflicting module
import importlib.util
spec = importlib.util.spec_from_file_location("mcp_server_lib", "/Users/earan/Documents/memory-hub/mcp/server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

# Run the server
if hasattr(module, 'mcp') and hasattr(module.mcp, 'run'):
    module.mcp.run()
elif hasattr(module, 'mcp'):
    # FastMCP instance
    import subprocess
    subprocess.run([sys.executable, "-m", "mcp.server", "--transport", "stdio"])
