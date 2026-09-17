"""Authoritative experience store. Explicit initialization; read-only reads."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import re
import sqlite3
import uuid
import os
import tempfile
from .contracts import ExperienceError, require, text, validate_payload

KEY_TABLE = '''CREATE TABLE IF NOT EXISTS experience_keys(
 event_id TEXT NOT NULL REFERENCES experience_events(event_id), key_group TEXT NOT NULL,
 token TEXT NOT NULL, PRIMARY KEY(event_id,key_group,token))'''
KEY_INDEX = 'CREATE INDEX IF NOT EXISTS lookup_keys ON experience_keys(key_group,token,event_id)'

SCHEMA = '''
CREATE TABLE store_meta(version INTEGER NOT NULL);
INSERT INTO store_meta VALUES(2);
CREATE TABLE experience_events(
 event_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL, collection_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL, content_hash TEXT NOT NULL, original_json TEXT NOT NULL,
 revision INTEGER NOT NULL, status TEXT NOT NULL, receipt_json TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
 UNIQUE(subject_id,collection_id,idempotency_key));
CREATE TABLE experience_versions(
 event_id TEXT NOT NULL REFERENCES experience_events(event_id), revision INTEGER NOT NULL,
 payload_json TEXT NOT NULL, reason TEXT, evidence_json TEXT NOT NULL DEFAULT '[]',
 source_kind TEXT NOT NULL, review_required INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(event_id,revision));
CREATE TABLE experience_keys(
 event_id TEXT NOT NULL REFERENCES experience_events(event_id), key_group TEXT NOT NULL,
 token TEXT NOT NULL, PRIMARY KEY(event_id,key_group,token));
CREATE INDEX lookup_keys ON experience_keys(key_group,token,event_id);
CREATE TABLE experience_feedback(subject_id TEXT,collection_id TEXT,idempotency_key TEXT,payload_json TEXT,receipt_json TEXT,target_id TEXT,PRIMARY KEY(subject_id,collection_id,idempotency_key));
CREATE TABLE experience_exports(path TEXT PRIMARY KEY,event_id TEXT,checksum TEXT);
PRAGMA user_version=2;
'''


def dumps(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def initialize(db_path: Path):
    db_path=Path(db_path)
    if db_path.exists():
        with connect(db_path) as source:
            version=source.execute('PRAGMA user_version').fetchone()[0]
            if version==2:return
            backup=db_path.with_name(db_path.name+'.v1-backup')
            require(not backup.exists(),'migration backup already exists','BACKUP_CONFLICT')
            fd=os.open(backup,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
            with sqlite3.connect(backup) as destination:source.backup(destination)
            backup.chmod(0o600)
        with connect(db_path,write=True) as c:
            c.execute('CREATE TABLE IF NOT EXISTS experience_feedback(subject_id TEXT,collection_id TEXT,idempotency_key TEXT,payload_json TEXT,receipt_json TEXT,target_id TEXT,PRIMARY KEY(subject_id,collection_id,idempotency_key))')
            c.execute('CREATE TABLE IF NOT EXISTS experience_exports(path TEXT PRIMARY KEY,event_id TEXT,checksum TEXT)')
            c.execute('UPDATE store_meta SET version=2')
            c.execute('PRAGMA user_version=2')
        return
    db_path.parent.mkdir(parents=True,exist_ok=True)
    # Build privately, then publish a complete database without overwriting a racer.
    fd,name=tempfile.mkstemp(prefix='.experience-init-',dir=db_path.parent)
    os.close(fd)
    temp=Path(name)
    c=sqlite3.connect(temp)
    try:
        c.executescript('BEGIN IMMEDIATE;'+SCHEMA+'COMMIT;')
        c.close()
        try:os.link(temp,db_path)
        except FileExistsError:
            with connect(db_path):pass
    finally:
        c.close()
        temp.unlink(missing_ok=True)


@contextmanager
def connect(db_path: Path, *, write=False):
    p=Path(db_path).absolute()
    require(p.is_file(),'experience store has not been initialized','NOT_INITIALIZED')
    c=None
    try:
        c=sqlite3.connect(p.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=10)
        c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        require(c.execute('PRAGMA user_version').fetchone()[0] in (1,2),'not an experience store','SCHEMA_MISMATCH')
        try:
            require(c.execute('SELECT version FROM store_meta').fetchone()[0] in (1,2),'unsupported version','SCHEMA_MISMATCH')
        except sqlite3.Error:
            raise ExperienceError('SCHEMA_MISMATCH') from None
        if write:
            c.execute('PRAGMA secure_delete=ON')
            c.execute('BEGIN IMMEDIATE')
        else:c.execute('BEGIN')
        yield c
        if write:c.commit()
    except sqlite3.Error as e:
        if c is not None and write:c.rollback()
        raise ExperienceError('STORE_UNAVAILABLE',str(e)) from None
    finally:
        if c is not None:c.close()


def tokens(value):
    words=re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+',value.lower())
    out=set()
    for word in words:
        if re.fullmatch(r'[\u4e00-\u9fff]+',word):
            for n in (2,3):out.update(word[i:i+n] for i in range(len(word)-n+1))
        elif len(word)>=2:out.add(word)
    return sorted(out)


def index_version(c,event_id,payload):
    c.execute('DELETE FROM experience_keys WHERE event_id=?',(event_id,))
    condition_parts=[dumps(payload['conditions'])]
    if pre:=payload.get('preconditions'):
        condition_parts.extend((pre['target'],dumps(pre.get('require') or {}),dumps(pre.get('exclude') or {})))
    groups={'goal':payload['goal'], 'reason':(payload.get('explicit_reason') or '')+' '+payload['narrative'],
            'condition':' '.join(condition_parts),
            'artifact':artifact_captions(payload)}
    c.executemany('INSERT INTO experience_keys VALUES(?,?,?)',[(event_id,g,t) for g,s in groups.items() for t in tokens(s)])
    from . import semantic
    if semantic.enabled():
        row=c.execute('SELECT max(revision) FROM experience_versions WHERE event_id=?',(event_id,)).fetchone()
        if row and row[0]:
            semantic.index_vectors(c,event_id,int(row[0]),payload)


def artifact_captions(payload):
    """Optional bounded captions are the only artifact text that becomes a lexical key."""
    return ' '.join(a['caption'] for a in payload.get('artifacts',[]) if a.get('caption'))


def rebuild_keys(db_path,ctx):
    """Recreate derived keys explicitly; never rewrite source/history or reactivate it."""
    require(ctx.role=='owner','management entry required','ACCESS_DENIED')
    with connect(db_path,write=True) as c:
        require(c.execute('PRAGMA user_version').fetchone()[0]==2
                and [row[0] for row in c.execute('SELECT version FROM store_meta')]==[2],
                'rebuild requires schema v2; migrate explicitly','SCHEMA_MISMATCH')
        collections=c.execute("""SELECT collection_id FROM experience_events WHERE status!='deleted'
            UNION SELECT collection_id FROM experience_feedback""").fetchall()
        require(all(ctx.may_access(row[0]) for row in collections),
                'rebuild requires access to all stored collections','ACCESS_DENIED')
        require(c.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND tbl_name='experience_keys'").fetchone() is None,
                'unexpected derived-index trigger','SCHEMA_MISMATCH')
        require(c.execute("SELECT 1 FROM experience_events WHERE status NOT IN ('active','revoked','deleted')").fetchone() is None,
                'unknown episode status','SCHEMA_MISMATCH')
        # Both DDL and replacement keys belong to this write transaction.
        c.execute(KEY_TABLE)
        index=c.execute("SELECT type,tbl_name FROM sqlite_master WHERE name='lookup_keys'").fetchone()
        require(index is None or tuple(index)==('index','experience_keys'),
                'lookup name belongs to another object','SCHEMA_MISMATCH')
        c.execute('DROP INDEX IF EXISTS lookup_keys')
        c.execute(KEY_INDEX)
        c.execute('DELETE FROM experience_keys')
        count=0
        rows=c.execute("""SELECT e.event_id,e.collection_id,v.payload_json,v.review_required
            FROM experience_events e LEFT JOIN experience_versions v
            ON v.event_id=e.event_id AND v.revision=e.revision
            WHERE e.status='active' ORDER BY e.event_id""")
        for row in rows:
            require(row['payload_json'] is not None and row['review_required'] in (0,1),
                    'missing or invalid current revision','SCHEMA_MISMATCH')
            try:
                payload=json.loads(row['payload_json'])
                validate_payload(payload)
                require(payload['collection_id']==row['collection_id'],'revision collection mismatch')
            except (ValueError,TypeError,ExperienceError):
                raise ExperienceError('SCHEMA_MISMATCH','invalid current revision') from None
            if row['review_required']:
                continue
            index_version(c,row['event_id'],payload)
            count+=1
        return {'operation':'rebuild-index','schema_version':2,'episodes_indexed':count,
                'keys_indexed':c.execute('SELECT count(*) FROM experience_keys').fetchone()[0]}


def event_row(c,ctx,event_id):
    row=c.execute('SELECT * FROM experience_events WHERE event_id=?',(event_id,)).fetchone()
    require(row is not None,'unknown episode','SOURCE_MISSING')
    require(ctx.may_access(row['collection_id']),'collection outside caller scope','ACCESS_DENIED')
    require(row['status']!='revoked','source withdrawn','REVOKED')
    require(row['status']!='deleted','source deleted','SOURCE_MISSING')
    return row


def validate_links(c,ctx,ids):
    for event_id in ids:event_row(c,ctx,event_id)


def record(db_path,ctx,payload,idempotency_key):
    text(idempotency_key,limit=200)
    encoded=dumps(payload); digest=hashlib.sha256(encoded.encode()).hexdigest()
    with connect(db_path,write=True) as c:
        row=c.execute('SELECT * FROM experience_events WHERE subject_id=? AND collection_id=? AND idempotency_key=?',
                      (ctx.subject_id,payload['collection_id'],idempotency_key)).fetchone()
        if row:
            require(row['content_hash']==digest,'same key with different payload','IDEMPOTENCY_CONFLICT')
            require(row['status'] not in ('revoked','deleted'),'withdrawn original event','REVOKED')
            return json.loads(row['receipt_json'])
        validate_links(c,ctx,payload.get('counterexample_ids',[]))
        event_id='ep-'+uuid.uuid4().hex
        receipt={'event_id':event_id,'revision':1,'evidence_ids':[event_id],'source_kind':payload['source_kind']}
        c.execute('INSERT INTO experience_events(event_id,subject_id,collection_id,idempotency_key,content_hash,original_json,revision,status,receipt_json) VALUES(?,?,?,?,?,?,1,?,?)',
                  (event_id,ctx.subject_id,payload['collection_id'],idempotency_key,digest,encoded,'active',dumps(receipt)))
        c.execute('INSERT INTO experience_versions(event_id,revision,payload_json,source_kind) VALUES(?,1,?,?)',(event_id,encoded,payload['source_kind']))
        index_version(c,event_id,payload)
        return receipt


def clear_review_required(db_path, event_id):
    """Approve a pending episode: clear review_required on all its revisions."""
    text(event_id, limit=200)
    with connect(db_path, write=True) as c:
        row = c.execute('SELECT 1 FROM experience_events WHERE event_id=?', (event_id,)).fetchone()
        require(row is not None, 'unknown episode', 'SOURCE_MISSING')
        c.execute('UPDATE experience_versions SET review_required=0 WHERE event_id=?', (event_id,))
        return {'event_id': event_id, 'review_required': 0}
