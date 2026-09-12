"""Explicit local management CLI; never called implicitly by a read."""
import argparse
import json
import os
from pathlib import Path
import sys
from . import AccessContext, ExperienceError, initialize, record_episode, recall_for_decision, read_evidence


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=Path(os.environ.get('MEMORY_HUB_DATA',str(Path.home()/'.memory-hub')))/'experience.sqlite3')
    p.add_argument('--collection',action='append',default=[])
    p.add_argument('--artifact-root',action='append',default=[])
    p.add_argument('--subject',default='local-cli')
    p.add_argument('--owner',action='store_true',help='Explicit trusted local management entry; never an MCP argument')
    sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('init')
    sub.add_parser('rebuild-index',help='Rebuild derived keys from current versions; owner with all collections required')
    q=sub.add_parser('record');q.add_argument('--file',type=Path,required=True);q.add_argument('--key',required=True)
    q=sub.add_parser('recall');q.add_argument('--task',required=True);q.add_argument('--mode',choices=['evidence','explore'],default='evidence');q.add_argument('--max-chars',type=int,default=6000);q.add_argument('--conditions')
    q=sub.add_parser('read');q.add_argument('--id',required=True);q.add_argument('--revision',type=int,required=True);q.add_argument('--offset',type=int,default=0);q.add_argument('--max-chars',type=int,default=12000)
    q=sub.add_parser('revise');q.add_argument('--id',required=True);q.add_argument('--base-revision',type=int,required=True);q.add_argument('--patch',type=Path,required=True);q.add_argument('--reason',required=True);q.add_argument('--evidence-id',action='append',required=True)
    for command in ('revoke','purge'):
        q=sub.add_parser(command);q.add_argument('--id',required=True);q.add_argument('--base-revision',type=int,required=True);q.add_argument('--reason',required=True)
    q=sub.add_parser('export');q.add_argument('--id',required=True);q.add_argument('--destination',type=Path,required=True)
    for command in ('backup','restore'):
        q=sub.add_parser(command,help='Copy --db to a new destination; explicit owner entry only')
        q.add_argument('--destination',type=Path,required=True)
        if command=='restore':
            q.add_argument('--current-store',type=Path,help='Current governance database; required before a restored copy can be read')
    a=p.parse_args(argv)
    try:
        ctx=AccessContext(a.subject,tuple(a.collection),'owner' if a.owner else 'agent',tuple(a.artifact_root))
        if a.command=='init':
            if not a.owner:raise ExperienceError('ACCESS_DENIED','initialization requires explicit management entry')
            initialize(a.db);result={'initialized':True}
        elif a.command=='rebuild-index':
            from .store import rebuild_keys
            result=rebuild_keys(a.db,ctx)
        elif a.command=='record':result=record_episode(a.db,ctx,json.loads(a.file.read_text()),idempotency_key=a.key)
        elif a.command=='recall':result=recall_for_decision(a.db,ctx,task=a.task,mode=a.mode,max_chars=a.max_chars,conditions=json.loads(a.conditions) if getattr(a,'conditions',None) else None)
        elif a.command=='revise':
            from .revisions import revise_understanding
            result=revise_understanding(a.db,ctx,target_id=a.id,base_revision=a.base_revision,patch=json.loads(a.patch.read_text()),reason=a.reason,evidence_ids=a.evidence_id)
        elif a.command in ('revoke','purge'):
            from .revisions import revoke,purge
            result=(revoke if a.command=='revoke' else purge)(a.db,ctx,target_id=a.id,base_revision=a.base_revision,reason=a.reason)
        elif a.command=='export':
            from .revisions import export_episode
            result=export_episode(a.db,ctx,target_id=a.id,destination=a.destination)
        elif a.command in ('backup','restore'):
            from .recovery import backup_store,restore_store
            result=(backup_store(a.db,ctx,destination=a.destination) if a.command=='backup'
                    else restore_store(a.db,ctx,destination=a.destination,current_store=a.current_store))
        else:result=read_evidence(a.db,ctx,evidence_id=a.id,revision=a.revision,offset=a.offset,max_chars=a.max_chars)
        print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
        return 0
    except (ExperienceError,OSError,ValueError) as e:
        print(json.dumps({'error':{'code':getattr(e,'code','PAYLOAD_INVALID'),'message':str(e)}},ensure_ascii=False))
        return 2


if __name__=='__main__':raise SystemExit(main())
