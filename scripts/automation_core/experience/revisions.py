"""Explicit owner governance. Immutable source versions survive corrections."""
from pathlib import Path
import hashlib
import json
import uuid
from .contracts import require, text, validate_payload, check_artifacts
from .store import connect, dumps, event_row, validate_links, index_version


def owner(ctx):require(ctx.role=='owner','management entry required','ACCESS_DENIED')


def current(c,ctx,target_id,base_revision):
    row=event_row(c,ctx,target_id)
    require(type(base_revision) is int and row['revision']==base_revision,'stale base revision','VERSION_CONFLICT')
    return row


def revise_understanding(db_path,ctx,*,target_id,base_revision,patch,reason,evidence_ids):
    owner(ctx);text(reason);require(type(patch) is dict and bool(patch))
    require(not patch.keys()-{'explicit_reason','conditions','interpretation','counterexample_ids','preconditions'},'only understanding fields may be revised')
    require(type(evidence_ids) is list and bool(evidence_ids))
    with connect(db_path,write=True) as c:
        row=current(c,ctx,target_id,base_revision);validate_links(c,ctx,evidence_ids)
        old=c.execute('SELECT payload_json FROM experience_versions WHERE event_id=? AND revision=?',(target_id,base_revision)).fetchone()
        payload=json.loads(old[0]);payload.update(patch);validate_payload(payload);check_artifacts(payload,ctx)
        validate_links(c,ctx,payload.get('counterexample_ids',[]))
        revision=base_revision+1
        c.execute('INSERT INTO experience_versions(event_id,revision,payload_json,reason,evidence_json,source_kind) VALUES(?,?,?,?,?,?)',
                  (target_id,revision,dumps(payload),reason,dumps(evidence_ids),payload['source_kind']))
        c.execute('UPDATE experience_events SET revision=? WHERE event_id=? AND revision=?',(revision,target_id,base_revision))
        index_version(c,target_id,payload)
        invalidate_transitive(c,target_id,exclude=target_id)
        return {'event_id':target_id,'revision':revision,'evidence_ids':[target_id],'source_kind':payload['source_kind']}


def invalidate_transitive(c,target_id,exclude=None):
    # Breadth-first over reverse dependency edges; visited set terminates cycles.
    seen={target_id}|({exclude} if exclude else set())
    frontier=[target_id]
    while frontier:
        matched=[]
        # Current-version edges only: released dependencies must not reflag.
        rows=c.execute('''SELECT v.event_id,v.evidence_json FROM experience_versions v
            JOIN experience_events e ON e.event_id=v.event_id AND e.revision=v.revision''').fetchall()
        for row in rows:
            if row['event_id'] in seen:continue
            if any(f in json.loads(row['evidence_json']) for f in frontier):
                if row['event_id'] not in matched:matched.append(row['event_id'])
        for event_id in matched:
            seen.add(event_id)
            c.execute('UPDATE experience_versions SET review_required=1 WHERE event_id=?',(event_id,))
        frontier=matched


def revoke(db_path,ctx,*,target_id,base_revision,reason):
    owner(ctx);text(reason)
    with connect(db_path,write=True) as c:
        current(c,ctx,target_id,base_revision)
        c.execute('INSERT INTO experience_versions(event_id,revision,payload_json,reason,source_kind) VALUES(?,?,?,?,?)',(target_id,base_revision+1,'{}',reason,'user_explicit'))
        c.execute("UPDATE experience_events SET status='revoked',revision=revision+1 WHERE event_id=?",(target_id,))
        c.execute('DELETE FROM experience_keys WHERE event_id=?',(target_id,))
        invalidate_transitive(c,target_id)
        return {'event_id':target_id,'revision':base_revision+1,'status':'revoked','reason':reason,'privacy_deleted':False}


def feedback(db_path,ctx,payload,idempotency_key):
    required={'schema_version','collection_id','event_kind','target_id','revision','action','source_kind','receipt_id'}
    require(type(payload) is dict and set(payload)==required)
    require(type(payload['schema_version']) is int and payload['schema_version']==1)
    require(type(payload['revision']) is int and payload['revision']>0)
    for k in required-{'schema_version','revision'}:text(payload[k],limit=300)
    require(payload['event_kind']=='feedback')
    require(payload['action'] in ('displayed','adopted','modified','validated','corrected','rejected'))
    require(ctx.may_access(payload['collection_id']),'collection outside scope','ACCESS_DENIED')
    require(ctx.may_write_source_kind(payload['source_kind']),'source elevation','ACCESS_DENIED')
    if payload['action'] in ('validated','corrected'):
        require(ctx.role=='owner' and payload['source_kind'] in ('validator_observed','user_explicit'),'trusted result required','ACCESS_DENIED')
    text(idempotency_key,limit=200)
    with connect(db_path,write=True) as c:
        row=event_row(c,ctx,payload['target_id'])
        require(row['collection_id']==payload['collection_id'],'feedback scope mismatch','ACCESS_DENIED')
        require(c.execute('SELECT 1 FROM experience_versions WHERE event_id=? AND revision=?',(payload['target_id'],payload['revision'])).fetchone() is not None,'unknown revision','SOURCE_MISSING')
        old=c.execute('SELECT payload_json,receipt_json FROM experience_feedback WHERE subject_id=? AND collection_id=? AND idempotency_key=?',(ctx.subject_id,payload['collection_id'],idempotency_key)).fetchone()
        if old:
            require(old[0]==dumps(payload),'feedback idempotency conflict','IDEMPOTENCY_CONFLICT');return json.loads(old[1])
        receipt={'event_id':'fb-'+uuid.uuid4().hex,'revision':payload['revision'],'evidence_ids':[payload['target_id']],
                 'action':payload['action'],'verification':'observed' if payload['action']=='validated' else 'unknown'}
        c.execute('INSERT INTO experience_feedback VALUES(?,?,?,?,?,?)',(ctx.subject_id,payload['collection_id'],idempotency_key,dumps(payload),dumps(receipt),payload['target_id']))
        return receipt


def export_episode(db_path,ctx,*,target_id,destination):
    """A read-only view with explicit file ownership and edit conflict detection."""
    owner(ctx);destination=Path(destination).absolute()
    with connect(db_path,write=True) as c:
        row=event_row(c,ctx,target_id)
        version=c.execute('SELECT payload_json FROM experience_versions WHERE event_id=? AND revision=?',(target_id,row['revision'])).fetchone()
        old=c.execute('SELECT event_id,checksum FROM experience_exports WHERE path=?',(str(destination),)).fetchone()
        if destination.exists():
            require(old is not None and old[0]==target_id and hashlib.sha256(destination.read_bytes()).hexdigest()==old[1],'export edited or not owned','EXPORT_CONFLICT')
        content=f'<!-- experience export {target_id} revision {row["revision"]}; read-only view -->\n```json\n{version[0]}\n```\n'
        destination.parent.mkdir(parents=True,exist_ok=True)
        # Exclusive temporary sibling followed by atomic rename.
        temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.tmp')
        temp.write_text(content);temp.replace(destination)
        checksum=hashlib.sha256(content.encode()).hexdigest()
        c.execute('INSERT OR REPLACE INTO experience_exports VALUES(?,?,?)',(str(destination),target_id,checksum))
        return {'path':str(destination),'revision':row['revision'],'checksum':checksum}


def purge(db_path,ctx,*,target_id,base_revision,reason):
    """Local privacy erasure; external artifacts and backups remain user-owned."""
    owner(ctx);text(reason)
    with connect(db_path,write=True) as c:
        row=c.execute('SELECT collection_id,revision,status FROM experience_events WHERE event_id=?',(target_id,)).fetchone()
        require(row is not None and row['status']!='deleted','unknown source','SOURCE_MISSING')
        require(ctx.may_access(row['collection_id']),'outside authorized scope','ACCESS_DENIED')
        require(type(base_revision) is int and base_revision==row['revision'],'stale revision','VERSION_CONFLICT')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'experience_exports' in tables:
            exports=c.execute('SELECT path,checksum FROM experience_exports WHERE event_id=?',(target_id,)).fetchall()
            for row in exports:
                p=Path(row['path'])
                require(not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()==row['checksum'],'export was edited; cannot delete user edits','EXPORT_CONFLICT')
            for row in exports:
                p=Path(row['path'])
                if p.exists():p.unlink()
            c.execute('DELETE FROM experience_exports WHERE event_id=?',(target_id,))
        invalidate_transitive(c,target_id)
        if 'experience_feedback' in tables:c.execute('DELETE FROM experience_feedback WHERE target_id=?',(target_id,))
        c.execute('DELETE FROM experience_keys WHERE event_id=?',(target_id,))
        c.execute('DELETE FROM experience_versions WHERE event_id=?',(target_id,))
        c.execute("UPDATE experience_events SET status='deleted',original_json='{}',content_hash='',receipt_json='{}',idempotency_key='',subject_id='',collection_id=?,revision=revision+1 WHERE event_id=?",(target_id,target_id))
        return {'event_id':target_id,'status':'deleted','limitations':['external referenced media untouched','host conversations and backups not erased']}
