from scripts.automation_core.experience import record_episode, revise_understanding, revoke
from scripts.automation_core.experience.adapter import ExperienceAdapter


def test_session_dedup_and_revision_refresh(env):
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='one')
    adapter=ExperienceAdapter(db,agent)
    first=adapter.recall_for_decision(task='文案',session_id='session-a')
    assert first['items']
    again=adapter.recall_for_decision(task='文案',session_id='session-a')
    assert again['items']==[] and again['deduplicated_count']==1
    assert adapter.recall_for_decision(task='文案',session_id='session-b')['items']
    revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,patch={'explicit_reason':'新理由'},reason='correction',evidence_ids=[r['event_id']])
    assert adapter.recall_for_decision(task='文案',session_id='session-a')['items'][0]['revision']==2
    revoke(db,owner,target_id=r['event_id'],base_revision=2,reason='withdraw')
    assert adapter.recall_for_decision(task='文案',session_id='session-a')['items']==[]


def test_stateless_calls_never_suppressed(env):
    db,owner,agent,p=env
    record_episode(db,owner,p,idempotency_key='one')
    adapter=ExperienceAdapter(db,agent)
    assert adapter.recall_for_decision(task='文案')['items']
    assert adapter.recall_for_decision(task='文案')['items']


def test_session_cache_does_not_retain_plaintext_evidence(env):
    db,owner,agent,p=env
    marker='synthetic-sensitive-marker'
    p['explicit_reason']=marker
    record_episode(db,owner,p,idempotency_key='private')
    adapter=ExperienceAdapter(db,agent)
    adapter.recall_for_decision(task='文案',session_id='session')
    assert marker not in repr(adapter._seen)
