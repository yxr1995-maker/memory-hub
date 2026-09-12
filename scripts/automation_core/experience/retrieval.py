"""Bounded deterministic candidate retrieval and versioned evidence reads."""
from __future__ import annotations
import json
import sqlite3
import uuid
from .contracts import ExperienceError, require, text, check_artifacts
from .store import connect, dumps, tokens, event_row


def candidates(c,ctx,task):
    # Missing derived data is different from no relevant experience.
    try:c.execute('SELECT token FROM experience_keys LIMIT 0')
    except sqlite3.Error:raise ExperienceError('INDEX_UNAVAILABLE') from None
    terms=tokens(task)
    if not terms or not ctx.allowed_collections:return []
    # Bind whole sets without dropping late-sorting terms or exhausting SQL variables.
    query_terms=dumps(terms)
    collections=dumps(ctx.allowed_collections)
    scores={}
    for group in ('goal','reason','condition'):
        rows=c.execute('''SELECT k.event_id,count(*) AS score
            FROM experience_keys k JOIN experience_events e ON k.event_id=e.event_id
            JOIN experience_versions v ON v.event_id=e.event_id AND v.revision=e.revision
            WHERE k.key_group=? AND k.token IN (SELECT value FROM json_each(?))
            AND e.collection_id IN (SELECT value FROM json_each(?))
            AND e.status='active' AND v.review_required=0
            GROUP BY k.event_id ORDER BY score DESC,k.event_id LIMIT 20''',
            [group,query_terms,collections]).fetchall()
        for row in rows:scores[row['event_id']]=scores.get(row['event_id'],0)+row['score']
    lexical_order=sorted(scores,key=lambda i:(-scores[i],i))
    from . import semantic
    if semantic.enabled():
        semantic_order=semantic.semantic_candidates(c,ctx,task)
        if semantic_order:
            return _fuse(lexical_order,semantic_order)[:60]
    return lexical_order[:60]


def _fuse(lexical_order,semantic_order,k=60):
    """Rank fusion over both candidate lists; deterministic tie-break by event id."""
    scores={}
    for order in (lexical_order,semantic_order):
        for position,event_id in enumerate(order):
            scores[event_id]=scores.get(event_id,0.0)+1.0/(k+position+1)
    return sorted(scores,key=lambda i:(-scores[i],i))


def load_versions(c,ids):
    if not ids:return {}
    marks=','.join('?' for _ in ids)
    rows=c.execute(f'''SELECT e.event_id,e.collection_id,e.revision,e.created_at,v.payload_json,v.source_kind,v.review_required
        FROM experience_events e JOIN experience_versions v ON e.event_id=v.event_id AND e.revision=v.revision
        WHERE e.event_id IN ({marks}) AND e.status='active' ''',ids).fetchall()
    return {r['event_id']:r for r in rows}


def card(row,mode,kind='experience',query_conditions=None):
    p=json.loads(row['payload_json'])
    item={'episode_id':row['event_id'],'revision':row['revision'],'source_kind':p['source_kind'],
          'synthetic':p.get('synthetic',False),
          'goal':p['goal'][:400],'explicit_reason':(p.get('explicit_reason') or '')[:600] or None,
          'conditions':p['conditions'],'compatibility':compatibility(p,query_conditions),
          'conditions_scope':'historical_episode_only','preconditions':p.get('preconditions'),
          'outcome':p['outcome'],'kind':kind,'created_at':row['created_at'],
          'evidence_refs':[{'evidence_id':row['event_id'],'revision':row['revision']}],
          'source_refs':p['source_refs'], 'interpretation': {'text':p['interpretation']['text'],'status':'candidate','basis':'inference'} if p.get('interpretation') else None}
    if mode=='evidence':
        item['preview']=p['narrative'][:400]
        item['artifact_refs']=[{'artifact_id':a['artifact_id'],'media_type':a['media_type']} for a in p.get('artifacts',[])]
    return item


def reference_card(item):
    """Keep an expandable version pointer without presenting partial advice."""
    retained=('episode_id','revision','source_kind','synthetic','kind','evidence_refs')
    return {**{key:item[key] for key in retained},
            'presentation':'reference_only','read_required':True,
            'goal_preview':item['goal'][:160],
            'omitted_fields':sorted(set(item)-set(retained))}


def retrieval_match(c,event_id,query_terms):
    """Explain actual indexed overlap for an already authorized selected source."""
    rows=c.execute('''SELECT key_group,token FROM experience_keys
        WHERE event_id=? AND token IN (SELECT value FROM json_each(?))
        ORDER BY key_group,token''',[event_id,dumps(query_terms)]).fetchall()
    groups={};matched=set();truncated=False
    for row in rows:
        matched.add(row['token'])
        terms=groups.setdefault(row['key_group'],[])
        if len(terms)<8:terms.append(row['token'])
        else:truncated=True
    return {'basis':'lexical_overlap_only','matched_terms':groups,
            'query_term_count':len(query_terms),'distinct_matched_term_count':len(matched),
            'terms_truncated':truncated,'not_applicability_verdict':True}


def compatibility(p,query_conditions):
    """Compare explicit structured conditions only; prose is never a verdict."""
    pre=p.get('preconditions')
    if not pre or not query_conditions:
        return {'status':'unknown','need_current_check':True}
    # A known exclusion wins; an unresolved exclusion cannot establish compatibility.
    def matches(a,b):
        # Type-sensitive: bool True must not be satisfied by int 1 (JSON 1 vs true).
        return type(a) is type(b) and a==b
    exclude_map=pre.get('exclude') or {}
    for key,forbidden in exclude_map.items():
        if key in query_conditions and matches(query_conditions[key],forbidden):
            return {'status':'incompatible','need_current_check':False,'basis':'caller_supplied_conditions'}
    if any(k not in query_conditions or type(query_conditions[k]) is not type(v) for k,v in exclude_map.items()):
        return {'status':'unknown','need_current_check':True}
    require_map=pre.get('require') or {}
    if require_map and all(k in query_conditions and matches(query_conditions[k],v) for k,v in require_map.items()):
        return {'status':'compatible','need_current_check':False,'basis':'caller_supplied_conditions'}
    return {'status':'unknown','need_current_check':True}


def recall(db_path,ctx,*,task,mode='evidence',max_chars=6000,conditions=None):
    text(task,limit=10000)
    require(mode in ('evidence','explore'),'invalid presentation mode')
    require(type(max_chars) is int and 512<=max_chars<=6000,'budget must be 512..6000 Unicode characters','INVALID_BUDGET')
    require(conditions is None or type(conditions) is dict,'conditions must be a dict or None','INVALID_BUDGET')
    if conditions is not None:
        require(len(conditions)<=30,'too many current conditions')
        for key,value in conditions.items():
            text(key,limit=100)
            require(value is None or type(value) in (str,int,float,bool),'conditions must be scalar metadata')
        try:
            require(len(dumps(conditions))<=10000,'current conditions too large')
        except (TypeError,ValueError):
            raise ExperienceError('PAYLOAD_INVALID','conditions must be finite JSON') from None
    with connect(db_path) as c:
        ids=candidates(c,ctx,task)
        rows=load_versions(c,ids)
        query_terms=tokens(task)
        selected=[];degraded=[]
        for i in ids:
            if i not in rows:continue
            try:check_artifacts(json.loads(rows[i]['payload_json']),ctx)
            except ExperienceError as e:
                if e.code not in degraded:degraded.append(e.code)
                continue
            item=card(rows[i],mode,query_conditions=conditions)
            item['retrieval_match']=retrieval_match(c,i,query_terms)
            selected.append(item)
            if len(selected)==3:break
        # Counterexamples must be explicitly linked, accessible and active.
        linked=[]
        primary_ids={item['episode_id'] for item in selected}
        for item in selected:
            for ref in json.loads(rows[item['episode_id']]['payload_json']).get('counterexample_ids',[]):
                if ref not in primary_ids and ref not in linked:linked.append(ref)
        for row in load_versions(c,linked).values():
            if not ctx.may_access(row['collection_id']) or row['review_required']:continue
            try:check_artifacts(json.loads(row['payload_json']),ctx)
            except ExperienceError:continue
            item=card(row,mode,'counterexample',query_conditions=conditions)
            item['retrieval_match']={'basis':'explicit_counterexample_link','not_applicability_verdict':True}
            selected.append(item);break
        total=len(set(ids) | {x['episode_id'] for x in selected})
        result={'items':selected,'selected_refs':[], 'revision':1,'source_kind':'retrieved_evidence',
                'receipt_id':'qr-'+uuid.uuid4().hex,
                'limits':{'max_chars':max_chars,'unit':'unicode_chars','candidates':60,'per_group':20,'primary':3,'counterexamples':1},
                'omitted_count':max(0,total-len(selected)),'degraded_reasons':degraded,
                'usage':{'model_calls':0,'candidates_considered':len(ids)},'mode':mode}
        while True:
            result['selected_refs']=[r for i in selected for r in i['evidence_refs']]
            result['omitted_count']=max(0,total-len(selected))
            if len(dumps(result))<=max_chars:return result
            require(bool(selected),'budget too small for required metadata','INVALID_BUDGET')
            if 'OUTPUT_BUDGET' not in result['degraded_reasons']:result['degraded_reasons'].append('OUTPUT_BUDGET')
            if len(selected)==1 and selected[0].get('presentation')!='reference_only':
                selected[0]=reference_card(selected[0])
            else:
                selected.pop()


def read(db_path,ctx,*,evidence_id,revision,offset=0,max_chars=12000):
    text(evidence_id,limit=200)
    require(type(revision) is int and revision>0)
    require(type(offset) is int and offset>=0)
    require(type(max_chars) is int and 0<max_chars<=12000)
    with connect(db_path) as c:
        event_row(c,ctx,evidence_id)
        row=c.execute('SELECT * FROM experience_versions WHERE event_id=? AND revision=?',(evidence_id,revision)).fetchone()
        require(row is not None,'version does not exist','SOURCE_MISSING')
        payload=json.loads(row['payload_json'])
        check_artifacts(payload,ctx)
        content=dumps(payload)
        window=content[offset:offset+max_chars]
        return {'evidence_id':evidence_id,'event_id':evidence_id,'revision':revision,'source_kind':payload['source_kind'],
                'content':window,'offset':offset,'next_offset':offset+len(window) if offset+len(window)<len(content) else None,
                'total_chars':len(content),'artifact_refs':payload.get('artifacts',[]) if offset==0 else [],
                'limits':{'unit':'unicode_chars','max_chars':max_chars},'review_required':bool(row['review_required']),
                'receipt_id':'qr-'+uuid.uuid4().hex,'selected_refs':[{'evidence_id':evidence_id,'revision':revision}],
                'omitted_count':0,'degraded_reasons':[]}
