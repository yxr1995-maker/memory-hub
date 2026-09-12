import json
from copy import deepcopy
import pytest
from scripts.automation_core.experience import (record_episode,recall_for_decision,read_evidence,revise_understanding,revoke,ExperienceError)


def test_correction_versions_and_conflict(env):
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='x')
    updated=revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,patch={'explicit_reason':'本次简报要求直接，不沿用温柔表达'},reason='current correction',evidence_ids=r['evidence_ids'])
    assert updated['revision']==2
    recalled=recall_for_decision(db,agent,task='文案')
    assert recalled['items'][0]['explicit_reason']=='本次简报要求直接，不沿用温柔表达'
    assert p['explicit_reason'] in read_evidence(db,agent,evidence_id=r['event_id'],revision=1)['content']
    with pytest.raises(ExperienceError) as e:
        revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,patch={'explicit_reason':'stale'},reason='stale',evidence_ids=r['evidence_ids'])
    assert e.value.code=='VERSION_CONFLICT'


def test_revision_patch_allows_preconditions(env):
    from scripts.automation_core.experience import record_episode, revise_understanding, recall_for_decision
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='pre-rev')
    pre={'target':'docs may be user edited','require':{'environment':'user-edited'},'exclude':{'environment':'disposable'}}
    revised=revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,
        patch={'preconditions':pre},reason='scope the guard to user-edited docs',evidence_ids=[r['event_id']])
    assert revised['revision']==2
    item=recall_for_decision(db,agent,task='文案',conditions={'environment':'user-edited'})['items'][0]
    assert item['preconditions']==pre
    assert item['compatibility']['status']=='compatible'


def test_revision_rejects_invalid_preconditions_without_side_effect(env):
    from scripts.automation_core.experience import ExperienceError, read_evidence
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='pre-invalid')
    for bad in ({'require':{'environment':'user-edited'}},{'target':'ok','require':{'strict':None}}):
        with pytest.raises(ExperienceError) as e:
            revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,
                patch={'preconditions':bad},reason='invalid guard',evidence_ids=[r['event_id']])
        assert e.value.code=='PAYLOAD_INVALID'
    # Rejected patches leave the active revision and stored payload untouched.
    from scripts.automation_core.experience import recall_for_decision
    item=recall_for_decision(db,agent,task='文案')['items'][0]
    assert item['episode_id']==r['event_id'] and item['revision']==1
    assert 'preconditions' not in read_evidence(db,agent,evidence_id=r['event_id'],revision=1)['content']
    assert read_evidence(db,agent,evidence_id=r['event_id'],revision=1)['review_required'] is False


def test_agent_cannot_revise_preconditions(env):
    from scripts.automation_core.experience import ExperienceError
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='pre-agent')
    with pytest.raises(ExperienceError) as e:
        revise_understanding(db,agent,target_id=r['event_id'],base_revision=1,
            patch={'preconditions':{'target':'x'}},reason='self approve',evidence_ids=[r['event_id']])
    assert e.value.code=='ACCESS_DENIED'


def test_precondition_revision_keeps_historical_version_readable(env):
    from scripts.automation_core.experience import read_evidence
    db,owner,agent,p=env
    import json as _json
    gentle={'target':'tone','require':{'tone':'gentle'}}
    direct={'target':'tone','require':{'tone':'direct'}}
    r=record_episode(db,owner,{**p,'preconditions':gentle},idempotency_key='pre-history')
    revised=revise_understanding(db,owner,target_id=r['event_id'],base_revision=1,
        patch={'preconditions':direct},reason='brief demands direct tone',evidence_ids=[r['event_id']])
    old=_json.loads(read_evidence(db,owner,evidence_id=r['event_id'],revision=1)['content'])
    assert old['preconditions']==gentle
    new=_json.loads(read_evidence(db,owner,evidence_id=r['event_id'],revision=2)['content'])
    assert new['preconditions']==direct


def test_revoke_blocks_historical_direct_read(env):
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='x')
    revoke(db,owner,target_id=r['event_id'],base_revision=1,reason='withdraw')
    assert recall_for_decision(db,agent,task='文案')['items']==[]
    with pytest.raises(ExperienceError) as e:read_evidence(db,agent,evidence_id=r['event_id'],revision=1)
    assert e.value.code=='REVOKED'


def test_agent_cannot_govern(env):
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='x')
    with pytest.raises(ExperienceError) as e:revoke(db,agent,target_id=r['event_id'],base_revision=1,reason='self approve')
    assert e.value.code=='ACCESS_DENIED'


def test_feedback_is_explicit_and_not_success(env):
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='x')
    feedback={'schema_version':1,'collection_id':'fixture','event_kind':'feedback','target_id':r['event_id'],'revision':1,'action':'adopted','source_kind':'agent_report','receipt_id':'qr-example'}
    receipt=record_episode(db,agent,feedback,idempotency_key='feedback')
    assert receipt['verification']=='unknown'
    assert record_episode(db,agent,feedback,idempotency_key='feedback')==receipt
    feedback['action']='validated'
    with pytest.raises(ExperienceError) as e:record_episode(db,agent,feedback,idempotency_key='forge')
    assert e.value.code=='ACCESS_DENIED'


def test_source_revocation_invalidates_only_dependent_understanding(env):
    db,owner,agent,p=env
    source=record_episode(db,owner,p,idempotency_key='source')
    other=record_episode(db,owner,p,idempotency_key='dependent')
    revise_understanding(db,owner,target_id=other['event_id'],base_revision=1,patch={'explicit_reason':'based on source'},reason='inference',evidence_ids=[source['event_id']])
    revoke(db,owner,target_id=source['event_id'],base_revision=1,reason='source wrong')
    assert recall_for_decision(db,agent,task='文案')['items']==[]
    assert read_evidence(db,agent,evidence_id=other['event_id'],revision=2)['review_required'] is True


def test_privacy_purge_does_not_delete_external_shared_media(env,tmp_path):
    import hashlib
    from scripts.automation_core.experience.revisions import purge
    db,owner,_,p=env
    f=tmp_path/'shared';f.write_text('external asset')
    p['artifacts']=[dict(artifact_id='shared',media_type='text',location=str(f),checksum=hashlib.sha256(f.read_bytes()).hexdigest(),license='reference')]
    a=record_episode(db,owner,p,idempotency_key='a');b=record_episode(db,owner,p,idempotency_key='b')
    purge(db,owner,target_id=a['event_id'],base_revision=1,reason='privacy')
    assert f.exists()
    assert read_evidence(db,owner,evidence_id=b['event_id'],revision=1)
    with pytest.raises(ExperienceError):read_evidence(db,owner,evidence_id=a['event_id'],revision=1)


def test_edited_export_conflicts(env,tmp_path):
    from scripts.automation_core.experience.revisions import export_episode
    db,owner,_,p=env
    r=record_episode(db,owner,p,idempotency_key='x');dest=tmp_path/'view.md'
    export_episode(db,owner,target_id=r['event_id'],destination=dest)
    dest.write_text('user edit')
    with pytest.raises(ExperienceError) as e:export_episode(db,owner,target_id=r['event_id'],destination=dest)
    assert e.value.code=='EXPORT_CONFLICT'
    assert dest.read_text()=='user edit'


def test_explicit_migration_uses_consistent_backup(env):
    import sqlite3
    from scripts.automation_core.experience import initialize
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='original')
    with sqlite3.connect(db) as c:
        c.executescript('DROP TABLE experience_feedback;DROP TABLE experience_exports;UPDATE store_meta SET version=1;PRAGMA user_version=1;')
    assert read_evidence(db,agent,evidence_id=r['event_id'],revision=1)
    initialize(db)
    backup=db.with_name(db.name+'.v1-backup')
    assert backup.is_file()
    with sqlite3.connect(backup) as c:
        assert c.execute('SELECT count(*) FROM experience_events').fetchone()[0]==1
    assert read_evidence(db,agent,evidence_id=r['event_id'],revision=1)


def test_purge_after_revoke(env):
    from scripts.automation_core.experience.revisions import purge
    db,owner,agent,p=env
    r=record_episode(db,owner,p,idempotency_key='x')
    revoke(db,owner,target_id=r['event_id'],base_revision=1,reason='stop recommending')
    assert purge(db,owner,target_id=r['event_id'],base_revision=2,reason='privacy')['status']=='deleted'
