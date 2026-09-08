#!/usr/bin/env python3
"""Codex command-hook JSON adapter. Errors never block a conversation."""
import json
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.automation_core.codex_memory import dispatch

def main():
    try:
        raw=sys.stdin.buffer.read(2*1024*1024)
        payload=json.loads(raw)
        if not isinstance(payload,dict):return {}
        if not payload.get('hook_event_name') and len(sys.argv)>1:
            payload['hook_event_name']=sys.argv[1]
        data=Path(os.environ.get('MEMORY_HUB_DATA',str(Path.home()/'.memory-hub')))
        wiki=Path(os.environ.get('WIKI_PATH',str(Path.home()/'llm-wiki')))
        return dispatch(payload,data,wiki)
    except Exception:return {}
if __name__=='__main__':
    print(json.dumps(main(),ensure_ascii=False))
