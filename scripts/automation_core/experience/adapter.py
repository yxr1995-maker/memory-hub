"""One adapter for existing MCP and explicitly configured callers."""
from dataclasses import dataclass, field
from collections import OrderedDict
from threading import RLock
from functools import lru_cache
import json
import hashlib
import os
from pathlib import Path
from . import AccessContext, record_episode, recall_for_decision, read_evidence
from .contracts import require, text
from .store import dumps


@dataclass(frozen=True)
class ExperienceAdapter:
    db_path: Path
    context: AccessContext
    _seen: OrderedDict = field(default_factory=OrderedDict,compare=False,repr=False)
    _lock: object = field(default_factory=RLock,compare=False,repr=False)

    def record_episode(self,payload,*,idempotency_key):
        return record_episode(self.db_path,self.context,payload,idempotency_key=idempotency_key)

    def recall_for_decision(self,*,session_id=None,refresh=False,**kwargs):
        # This cache holds only delivery fingerprints. Always re-read current ACL,
        # revocation and source integrity before deciding whether to suppress output.
        require(type(refresh) is bool)
        if session_id is None:
            return recall_for_decision(self.db_path,self.context,**kwargs)
        text(session_id,limit=200)
        with self._lock:
            result=recall_for_decision(self.db_path,self.context,**kwargs)
            key=hashlib.sha256(dumps([session_id,kwargs]).encode()).hexdigest()
            signature=hashlib.sha256(dumps({'items':result['items'],'degraded_reasons':result['degraded_reasons']}).encode()).hexdigest()
            previous=self._seen.get(key)
            if not refresh and previous==signature and result['items']:
                count=len(result['items'])
                result['items']=[];result['selected_refs']=[]
                result['deduplicated_count']=count
                result['degraded_reasons'].append('ALREADY_DELIVERED')
            else:
                self._seen[key]=signature
                self._seen.move_to_end(key)
                while len(self._seen)>128:self._seen.popitem(last=False)
            # Suppression only shrinks populated payloads; retain the whole-JSON cap.
            require(len(dumps(result))<=kwargs.get('max_chars',6000),'dedup metadata exceeds budget','INVALID_BUDGET')
            return result

    def read_evidence(self,**kwargs):
        return read_evidence(self.db_path,self.context,**kwargs)


def from_environment(data_path=None):
    # Launch configuration is trusted. Tool arguments never select role or roots.
    env_collections=os.environ.get('MEMORY_HUB_EXPERIENCE_COLLECTIONS')
    env_roots=os.environ.get('MEMORY_HUB_EXPERIENCE_ROOTS')
    settings=host_settings(data_path)
    collections=json.loads(env_collections) if env_collections is not None else settings.get('collections',[])
    roots=json.loads(env_roots) if env_roots is not None else settings.get('artifact_roots',[])
    require(type(collections) is list and type(roots) is list,'invalid server scope configuration')
    data=Path(data_path) if data_path is not None else Path(os.environ.get('MEMORY_HUB_DATA',str(Path.home()/'.memory-hub')))
    return _configured_adapter(str(data.absolute()),tuple(collections),tuple(roots))


def host_settings(data_path=None):
    """Read the host scope file for the selected data root; absent file means empty scope."""
    data=Path(data_path) if data_path is not None else Path(os.environ.get('MEMORY_HUB_DATA',str(Path.home()/'.memory-hub')))
    path=data/'experience-host.json'
    if not path.is_file():
        return {'collections':[],'artifact_roots':[],'profile':False}
    settings=json.loads(path.read_text())
    require(type(settings) is dict and not settings.keys()-{'collections','artifact_roots','profile'},'invalid host settings file')
    settings.setdefault('collections',[])
    settings.setdefault('artifact_roots',[])
    settings.setdefault('profile',False)
    require(type(settings['profile']) is bool,'invalid profile setting')
    require(type(settings['collections']) is list and type(settings['artifact_roots']) is list,'invalid host scope')
    AccessContext('host-settings',settings['collections'],'agent',settings['artifact_roots'])
    return settings


@lru_cache(maxsize=8)
def _configured_adapter(data,collections,roots):
    return ExperienceAdapter(Path(data)/'experience.sqlite3',AccessContext('mcp-agent',collections,'agent',roots))
