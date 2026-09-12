"""M4-3 comparison: feedback utility as a tie-break, never as authority.

Local only: no model calls, no network, no new runtime. The expected order below is
an independent restatement of the frozen weights, not a call into the implementation.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.automation_core.experience import (  # noqa: E402
    AccessContext, initialize, record_episode, recall_for_decision,
)
from scripts.automation_core.experience.retrieval import candidates  # noqa: E402
from scripts.automation_core.experience.store import connect  # noqa: E402

SUITE = Path(__file__).resolve().parent / 'm4_utility.jsonl'
OUT = ROOT / 'docs/evidence/experience-v1/m4-utility-20260913'
COLLECTION = 'fixture'
QUERY = 'zzcommon'
# Frozen weights, restated here so the expectation does not import the implementation.
WEIGHTS = {'validated': 2, 'adopted': 1, 'modified': 1, 'rejected': -2}


def switch(value):
    if value is None:
        os.environ.pop('MEMORY_HUB_EXPERIENCE_UTILITY', None)
    else:
        os.environ['MEMORY_HUB_EXPERIENCE_UTILITY'] = value


def build(work):
    work.mkdir(parents=True, exist_ok=True)
    db = work / 'experience.sqlite3'
    if db.exists():
        db.unlink()
    initialize(db)
    return db


def episode_payload(goal, collection=COLLECTION):
    return {'schema_version': 1, 'collection_id': collection, 'event_kind': 'episode',
            'synthetic': True, 'source_kind': 'synthetic_fixture',
            'goal': goal, 'narrative': '同一批素材的归档说明。', 'explicit_reason': None,
            'conditions': {}, 'outcome': {'status': 'observed', 'verification': 'unknown'},
            'source_refs': [{'kind': 'fixture', 'ref': 'm4-utility/' + goal}]}


def feedback_payload(target_id, action, source_kind, collection=COLLECTION):
    return {'schema_version': 1, 'collection_id': collection, 'event_kind': 'feedback',
            'target_id': target_id, 'revision': 1, 'action': action, 'source_kind': source_kind,
            'receipt_id': 'receipt-' + target_id}


def order(db, agent):
    with connect(db) as c:
        return candidates(c, agent, QUERY)


def top3(db, agent):
    return [item['episode_id'] for item in recall_for_decision(db, agent, task=QUERY, mode='evidence')['items']]


def timed(fn, times=20):
    started = time.monotonic()
    for _ in range(times):
        fn()
    return round((time.monotonic() - started) / times * 1000, 3)


def main():
    rows = [json.loads(line) for line in SUITE.read_text().splitlines() if line.strip()]
    report = {'protocol': 'm4-utility-1', 'queries': 1, 'model_calls': 0, 'weights': WEIGHTS}
    db = build(OUT / 'run')
    agent = AccessContext('m4-agent', (COLLECTION,), 'agent')
    owner = AccessContext('m4-owner', (COLLECTION,), 'owner')
    ids = {row['id']: record_episode(db, owner, episode_payload(row['goal']), idempotency_key=row['id'])['event_id']
           for row in rows}

    switch(None)
    plain = order(db, agent)
    report['plain_order'] = plain
    report['plain_top3'] = top3(db, agent)
    report['milliseconds_plain'] = timed(lambda: order(db, agent))

    switch('1')
    report['with_switch_without_feedback'] = {'order_identical': order(db, agent) == plain,
                                              'milliseconds': timed(lambda: order(db, agent))}

    for row in rows:
        if not row['action']:
            continue
        source_kind = ('validator_observed' if row['action'] == 'validated' else
                       'user_explicit' if row['by'] == 'owner' else 'agent_report')
        actor = owner if row['by'] == 'owner' else agent
        record_episode(db, actor, feedback_payload(ids[row['id']], row['action'], source_kind),
                       idempotency_key='fb-' + row['id'])
    actual = order(db, agent)
    action_of = {ids[row['id']]: row['action'] for row in rows}
    expected = sorted(plain, key=lambda i: (-WEIGHTS.get(action_of.get(i), 0), plain.index(i)))
    report['with_feedback'] = {
        'order': actual, 'expected': expected, 'order_matches_frozen_weights': actual == expected,
        'recall_top3_after': top3(db, agent),
        'rejected_still_returned': ids['util-05'] in actual,
        'validated_moves_to_front': actual[0] == ids['util-01'],
        'rejected_moves_to_back': actual[-1] == ids['util-05'],
    }
    with connect(db) as c:
        card = next(item for item in recall_for_decision(db, agent, task=QUERY, mode='evidence')['items']
                    if item['episode_id'] == ids['util-01'])
    report['card_exposes_utility'] = card.get('feedback_utility') == {
        'score': 2, 'actions': {'validated': 1}, 'basis': 'feedback_tie_break_only'}

    # Observable at the tool surface: promoting the episodes that the plain order
    # left outside the three-card budget changes which cards come back.
    promoted = [ids[row['id']] for row in rows if ids[row['id']] not in report['plain_top3']]
    for index, event_id in enumerate(promoted):
        record_episode(db, owner, feedback_payload(event_id, 'validated', 'validator_observed'),
                       idempotency_key=f'fb-promote-{index}')
    promoted_top3 = top3(db, agent)
    report['promotion'] = {'promoted_ids': promoted, 'top3_after_promotion': promoted_top3,
                           'membership_changed': set(promoted_top3) != set(report['plain_top3'])}
    after_promotion = order(db, agent)

    dominance = build(OUT / 'run-dominance')
    strong = record_episode(dominance, owner, episode_payload('zzcommon zzneedle'),
                            idempotency_key='strong')['event_id']
    weak = record_episode(dominance, owner, episode_payload('zzcommon'), idempotency_key='weak')['event_id']
    record_episode(dominance, owner, feedback_payload(strong, 'rejected', 'user_explicit'), idempotency_key='fb-s')
    record_episode(dominance, owner, feedback_payload(weak, 'validated', 'validator_observed'), idempotency_key='fb-w')
    with connect(dominance) as c:
        dominant = candidates(c, agent, 'zzcommon zzneedle')
    report['relevance_dominance'] = {'order': dominant, 'rejected_two_term_match_first': dominant[0] == strong,
                                     'validated_one_term_match_second': dominant[1] == weak}

    scoped = build(OUT / 'run-scope')
    scoped_owner = replace(owner, allowed_collections=(COLLECTION, 'private'))
    public = record_episode(scoped, scoped_owner, episode_payload('zzcommon 公开'), idempotency_key='public')['event_id']
    secret = record_episode(scoped, scoped_owner, episode_payload('zzcommon 私有', 'private'),
                            idempotency_key='secret')['event_id']
    record_episode(scoped, scoped_owner, feedback_payload(secret, 'validated', 'validator_observed', 'private'),
                   idempotency_key='fb-secret')
    record_episode(scoped, scoped_owner, feedback_payload(public, 'rejected', 'user_explicit'), idempotency_key='fb-public')
    scoped_order = order(scoped, agent)
    report['authorization'] = {'order': scoped_order, 'hidden_high_utility_stays_hidden': secret not in scoped_order,
                               'rejected_public_still_returned': public in scoped_order}

    switch('0')
    report['switch_off_restores_plain_order'] = order(db, agent) == plain
    switch('1')
    report['switch_on_order_stable'] = order(db, agent) == after_promotion
    switch(None)

    report['pass'] = {
        'no_feedback_identity': report['with_switch_without_feedback']['order_identical'],
        'tie_break_matches_frozen_weights': report['with_feedback']['order_matches_frozen_weights'],
        'validated_first_rejected_last': (report['with_feedback']['validated_moves_to_front']
                                          and report['with_feedback']['rejected_moves_to_back']),
        'rejected_evidence_still_returned': report['with_feedback']['rejected_still_returned'],
        'relevance_dominance': (report['relevance_dominance']['rejected_two_term_match_first']
                                and report['relevance_dominance']['validated_one_term_match_second']),
        'authorization_unchanged': (report['authorization']['hidden_high_utility_stays_hidden']
                                    and report['authorization']['rejected_public_still_returned']),
        'reversible': report['switch_off_restores_plain_order'] and report['switch_on_order_stable'],
        'card_exposes_utility': report['card_exposes_utility'],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    OUT.joinpath('result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'pass': report['pass'], 'plain_top3': report['plain_top3'],
                      'after_top3': report['with_feedback']['recall_top3_after'],
                      'ms_plain': report['milliseconds_plain'],
                      'ms_utility': report['with_switch_without_feedback']['milliseconds']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
