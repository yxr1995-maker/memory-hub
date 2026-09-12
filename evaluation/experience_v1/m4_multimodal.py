"""M4-2 comparison: caption-bound media findability, lexical path only, no model calls.

Two stores over the same ten episodes: without captions, and with captions.
The caption query terms never occur in goal/reason/narrative, so any hit is
attributable to the caption alone.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.automation_core.experience import (  # noqa: E402
    AccessContext, initialize, record_episode, recall_for_decision,
)
from scripts.automation_core.experience.store import tokens  # noqa: E402

SUITE = Path(__file__).resolve().parent / 'm4_caption.jsonl'
OUT = ROOT / 'docs/evidence/experience-v1/m4-multimodal-20260913'
COLLECTION = 'fixture'


def overlap(left, right):
    return len(set(tokens(left)) & set(tokens(right)))


def build(work, rows, with_captions):
    work.mkdir(parents=True, exist_ok=True)
    media = work / 'media'
    media.mkdir(exist_ok=True)
    db = work / 'experience.sqlite3'
    if db.exists():
        db.unlink()
    initialize(db)
    owner = AccessContext('m4-owner', (COLLECTION,), 'owner', (str(work),))
    ids = {}
    for row in rows:
        path = media / f"{row['id']}.bin"
        path.write_bytes(f"media-{row['id']}".encode())
        artifact = {'artifact_id': f"a-{row['id']}", 'media_type': row['media_type'],
                    'location': str(path), 'checksum': hashlib.sha256(path.read_bytes()).hexdigest(),
                    'license': 'read'}
        if row.get('segment'):
            artifact['segment'] = row['segment']
        if with_captions:
            artifact['caption'] = row['caption']
        payload = {'schema_version': 1, 'collection_id': COLLECTION, 'event_kind': 'episode',
                   'synthetic': True, 'source_kind': 'synthetic_fixture',
                   'goal': row['goal'], 'narrative': row['narrative'],
                   'explicit_reason': row['explicit_reason'], 'conditions': row['conditions'],
                   'outcome': {'status': 'observed', 'verification': 'unknown'},
                   'artifacts': [artifact],
                   'source_refs': [{'kind': 'fixture', 'ref': f"m4-caption/{row['id']}"}]}
        receipt = record_episode(db, owner, payload, idempotency_key=row['id'])
        ids[row['id']] = receipt['event_id']
    return db, ids


def measure(db, agent, rows, field, ids):
    hits = {1: 0, 3: 0}
    empty = 0
    per_query = []
    started = time.monotonic()
    for row in rows:
        result = recall_for_decision(db, agent, task=row[field], mode='evidence')
        returned = [item['episode_id'] for item in result['items']]
        target = ids[row['id']]
        rank = returned.index(target) + 1 if target in returned else None
        hits[1] += 1 if rank == 1 else 0
        hits[3] += 1 if rank and rank <= 3 else 0
        empty += 1 if not returned else 0
        per_query.append({'id': row['id'], 'rank': rank, 'returned': len(returned)})
    elapsed = time.monotonic() - started
    total = len(rows)
    return {'queries': total, 'hit_at_1': round(hits[1] / total, 3), 'hit_at_3': round(hits[3] / total, 3),
            'empty_result_share': round(empty / total, 3),
            'seconds_per_query': round(elapsed / total, 4), 'per_query': per_query}


def key_counts(db):
    import sqlite3
    c = sqlite3.connect(db)
    try:
        total = c.execute('SELECT count(*) FROM experience_keys').fetchone()[0]
        artifact = c.execute("SELECT count(*) FROM experience_keys WHERE key_group='artifact'").fetchone()[0]
    finally:
        c.close()
    return total, artifact


def main():
    rows = [json.loads(line) for line in SUITE.read_text().splitlines() if line.strip()]
    overlaps = [overlap(f"{row['goal']} {row['explicit_reason']} {row['narrative']} {json.dumps(row['conditions'])}",
                        row['query']) for row in rows]
    caption_gap = [overlap(row['caption'], row['query']) for row in rows]
    os.environ.pop('MEMORY_HUB_EXPERIENCE_SEMANTIC', None)
    report = {'protocol': 'm4-multimodal-1', 'queries': len(rows),
              'caption_query_overlap_min': min(caption_gap),
              'non_caption_query_overlap_max': max(overlaps), 'model_calls': 0}
    stores = {}
    for label, captions in (('without_caption', False), ('with_caption', True)):
        work = OUT / 'run' / label
        db, ids = build(work, rows, captions)
        agent = AccessContext('m4-agent', (COLLECTION,), 'agent', (str(work),))
        stores[label] = (db, agent, ids)
        report[label] = measure(db, agent, rows, 'query', ids)
        total, artifact = key_counts(db)
        report[label]['keys_total'] = total
        report[label]['keys_artifact_group'] = artifact
        report[label]['store_bytes'] = db.stat().st_size
        report[label]['media_bytes_total'] = sum((work / 'media' / f"{r['id']}.bin").stat().st_size for r in rows)
    for label, (db, agent, ids) in stores.items():
        report[label]['self_query'] = measure(db, agent, rows, 'goal', ids)
    report['pass'] = {
        'caption_query_hit_at_3_ge_0_8': report['with_caption']['hit_at_3'] >= 0.8,
        'no_caption_must_miss': report['without_caption']['empty_result_share'] == 1.0,
        'non_caption_overlap_is_zero': report['non_caption_query_overlap_max'] == 0,
        'self_query_regression_kept': report['with_caption']['self_query']['hit_at_3'] == report['without_caption']['self_query']['hit_at_3'] == 1.0,
        'keys_grow_by_caption_tokens_only': (report['with_caption']['keys_total'] - report['without_caption']['keys_total']) == report['with_caption']['keys_artifact_group'],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    OUT.joinpath('result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('protocol', 'queries', 'non_caption_query_overlap_max', 'pass')},
                     ensure_ascii=False))
    for label in ('without_caption', 'with_caption'):
        section = report[label]
        print(label, {key: section[key] for key in ('hit_at_1', 'hit_at_3', 'empty_result_share',
                                                    'seconds_per_query', 'keys_total', 'keys_artifact_group',
                                                    'store_bytes')},
              'self', section['self_query']['hit_at_3'])


if __name__ == '__main__':
    main()
