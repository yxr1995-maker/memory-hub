from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.automation_core.experience import (
    AccessContext,
    ExperienceError,
    read_evidence,
    recall_for_decision,
    record_episode,
)


def _noise_payload(collection_id: str, goal: str, index: int) -> dict:
    payload = {
        "schema_version": 1,
        "collection_id": collection_id,
        "event_kind": "episode",
        "synthetic": True,
        "goal": goal,
        "narrative": f"noise narrative {index}",
        "explicit_reason": "noise reason",
        "source_kind": "synthetic_fixture",
        "conditions": {"task_kind": "noise"},
        "outcome": {"status": "observed", "verification": "unknown"},
        "artifacts": [],
        "source_refs": [{"kind": "fixture", "ref": f"noise/{index}"}],
    }
    return payload


def test_cross_collection_candidates_may_not_consume_recall_quota(env):
    """Candidates must be scoped by the caller's collections before the
    60-candidate budget is applied; out-of-scope episodes must not crowd
    out in-scope ones."""
    db, owner, agent, payload = env
    broad_owner = AccessContext(
        subject_id="broad-owner",
        allowed_collections=("fixture", "other"),
        role="owner",
        artifact_roots=owner.artifact_roots,
    )
    for i in range(60):
        record_episode(
            db,
            broad_owner,
            _noise_payload("other", "alpha shared goal", i),
            idempotency_key=f"noise-{i:03d}",
        )
    target = deepcopy(payload)
    target["goal"] = "alpha 任务目标"
    receipt = record_episode(db, owner, target, idempotency_key="case-alpha")
    result = recall_for_decision(db, agent, task="alpha 任务目标")
    ids = [x["episode_id"] for x in result["items"]]
    assert receipt["event_id"] in ids


def test_read_evidence_rejects_negative_offset(env):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key="case-01")
    with pytest.raises(ExperienceError) as err:
        read_evidence(
            db,
            agent,
            evidence_id=receipt["evidence_ids"][0],
            revision=receipt["revision"],
            offset=-5,
        )
    assert err.value.code == "PAYLOAD_INVALID"


def test_read_evidence_rejects_non_positive_max_chars(env):
    db, owner, agent, payload = env
    receipt = record_episode(db, owner, payload, idempotency_key="case-01")
    evidence_id = receipt["evidence_ids"][0]
    for bad in (0, -1):
        with pytest.raises(ExperienceError) as err:
            read_evidence(
                db,
                agent,
                evidence_id=evidence_id,
                revision=receipt["revision"],
                max_chars=bad,
            )
        assert err.value.code == "PAYLOAD_INVALID"


def test_read_evidence_unknown_id_is_source_missing(env):
    db, _, agent, payload = env
    with pytest.raises(ExperienceError) as err:
        read_evidence(db, agent, evidence_id="unknown", revision=1)
    assert err.value.code == "SOURCE_MISSING"

@pytest.mark.parametrize('patch', [{'trusted':True}, {'active':True}, {'conditions':{'x':{'trusted':True}}}, {'source_refs':[]}, {'goal':[]}, {'schema_version':True}])
def test_strict_payload(env, patch):
    db, owner, _, payload = env
    payload.update(patch)
    with pytest.raises(ExperienceError):
        record_episode(db, owner, payload, idempotency_key='bad')


def test_agent_cannot_forge_validator(env):
    db, _, agent, payload = env
    payload['source_kind']='validator_observed'
    with pytest.raises(ExperienceError) as e:
        record_episode(db, agent, payload, idempotency_key='forged')
    assert e.value.code=='ACCESS_DENIED'


def test_budget_covers_metadata(env):
    import json
    db, owner, agent, payload = env
    record_episode(db, owner, payload, idempotency_key='x')
    out=recall_for_decision(db, agent, task='文案', max_chars=700)
    assert len(json.dumps(out,ensure_ascii=False,separators=(',',':')))<=700


def test_direct_evidence_acl(env):
    db, owner, agent, payload=env
    receipt=record_episode(db,owner,payload,idempotency_key='x')
    denied=agent.narrow_collections([])
    with pytest.raises(ExperienceError) as e:
        read_evidence(db,denied,evidence_id=receipt['evidence_ids'][0],revision=1)
    assert e.value.code=='ACCESS_DENIED'


def test_artifact_integrity(env, tmp_path):
    import hashlib
    db,owner,agent,payload=env
    f=tmp_path/'draft.txt';f.write_text('version A')
    payload['artifacts']=[dict(artifact_id='a',media_type='text',location=str(f),checksum=hashlib.sha256(f.read_bytes()).hexdigest(),license='read')]
    receipt=record_episode(db,owner,payload,idempotency_key='media')
    f.write_text('edited')
    with pytest.raises(ExperienceError) as e:
        read_evidence(db,agent,evidence_id=receipt['evidence_ids'][0],revision=1)
    assert e.value.code=='SOURCE_CHANGED'


def test_reads_do_not_write(env):
    import hashlib
    db,owner,agent,payload=env
    r=record_episode(db,owner,payload,idempotency_key='x')
    before={str(p):(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in db.parent.iterdir() if p.is_file()}
    recall_for_decision(db,agent,task='文案')
    read_evidence(db,agent,evidence_id=r['evidence_ids'][0],revision=1)
    after={str(p):(p.stat().st_mtime_ns,hashlib.sha256(p.read_bytes()).hexdigest()) for p in db.parent.iterdir() if p.is_file()}
    assert before==after


def test_concurrent_retries_single_event(env):
    from concurrent.futures import ThreadPoolExecutor
    db,owner,_,payload=env
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:record_episode(db,owner,payload,idempotency_key='race'),range(12)))
    assert len({r['event_id'] for r in results})==1
    assert all(r==results[0] for r in results)


def test_subject_idempotency_scope(env):
    from dataclasses import replace
    db,owner,_,payload=env
    a=record_episode(db,owner,payload,idempotency_key='same')
    b=record_episode(db,replace(owner,subject_id='another'),payload,idempotency_key='same')
    assert a['event_id']!=b['event_id']


def test_media_symlink_cannot_escape(env,tmp_path):
    import hashlib
    from dataclasses import replace
    db,owner,_,payload=env
    root=tmp_path/'allowed';root.mkdir()
    secret=tmp_path/'outside';secret.write_text('private')
    link=root/'link';link.symlink_to(secret)
    payload['artifacts']=[dict(artifact_id='a',media_type='text',location=str(link),checksum=hashlib.sha256(secret.read_bytes()).hexdigest(),license='read')]
    with pytest.raises(ExperienceError) as e:
        record_episode(db,replace(owner,artifact_roots=(str(root),)),payload,idempotency_key='escape')
    assert e.value.code=='ACCESS_DENIED'


def test_index_unavailable_is_not_empty(env):
    import sqlite3
    db,_,agent,_=env
    with sqlite3.connect(db) as c:c.execute('DROP TABLE experience_keys')
    with pytest.raises(ExperienceError) as e:recall_for_decision(db,agent,task='文案')
    assert e.value.code=='INDEX_UNAVAILABLE'


def test_many_collections_still_have_global_candidate_budget(env):
    db,owner,_,payload=env
    collections=tuple('c'+str(i) for i in range(5))
    ctx=AccessContext('owner',collections,'owner')
    for i in range(100):
        p=deepcopy(payload);p['collection_id']=collections[i%5];p['goal']='alpha';p['narrative']='beta';p['conditions']={'entity':'gamma'}
        record_episode(db,ctx,p,idempotency_key=str(i))
    r=recall_for_decision(db,ctx,task='alpha beta gamma')
    assert r['usage']['candidates_considered']<=60
    assert len(r['items'])<=3


def test_large_card_degrades_without_poisoning_collection(env):
    import json
    from scripts.automation_core.experience import read_evidence
    db,owner,agent,p=env
    p['conditions']={'long_context':'a'*80000}
    receipt=record_episode(db,owner,p,idempotency_key='large')
    result=recall_for_decision(db,agent,task='文案')
    assert len(result['items'])==1 and result['omitted_count']==0
    assert result['items'][0]['read_required'] is True
    assert 'conditions' in result['items'][0]['omitted_fields']
    assert result['selected_refs']==[{'evidence_id':receipt['event_id'],'revision':1}]
    assert len(json.dumps(result,ensure_ascii=False,separators=(',',':')))<=6000
    offset=0;pieces=[]
    while True:
        page=read_evidence(db,agent,evidence_id=receipt['event_id'],revision=1,offset=offset)
        pieces.append(page['content'])
        if page['next_offset'] is None:break
        offset=page['next_offset']
    assert json.loads(''.join(pieces))['conditions']==p['conditions']


def test_public_api_does_not_export_unchecked_record():
    import scripts.automation_core.experience as api
    assert not hasattr(api,'record')


def test_concurrent_initialization_is_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from scripts.automation_core.experience import initialize
    db=tmp_path/'new.sqlite3'
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _:initialize(db),range(12)))==[None]*12
