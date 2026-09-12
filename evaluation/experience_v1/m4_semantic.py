"""M4-1 comparison: lexical vs semantic experience recall on paraphrases.

Local-only: embeddings come from fastembed, no LLM calls, no network.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.automation_core.experience import (  # noqa: E402
    AccessContext, initialize, record_episode, recall_for_decision,
)
from scripts.automation_core.experience.store import rebuild_keys, tokens  # noqa: E402

SUITE = Path(__file__).resolve().parent / 'm4_paraphrase.jsonl'
OUT = ROOT / 'docs/evidence/experience-v1/m4-semantic-20260913'
COLLECTION = 'fixture'
K = 3


def overlap(left, right):
    return len(set(tokens(left)) & set(tokens(right)))


def measure(db, agent, rows, label):
    hits = {1: 0, 3: 0}
    per_query = []
    started = time.monotonic()
    for row in rows:
        result = recall_for_decision(db, agent, task=row['query'], mode='evidence')
        ids = [item['episode_id'] for item in result['items']]
        rank = ids.index(row['event_id']) + 1 if row['event_id'] in ids else None
        hits[1] += 1 if rank == 1 else 0
        hits[3] += 1 if rank and rank <= 3 else 0
        per_query.append({'id': row['id'], 'rank': rank, 'returned': len(ids)})
    elapsed = round(time.monotonic() - started, 3)
    total = len(rows)
    return {'label': label, 'queries': total,
            'hit_at_1': round(hits[1] / total, 3), 'hit_at_3': round(hits[3] / total, 3),
            'seconds_total': elapsed, 'seconds_per_query': round(elapsed / total, 4),
            'per_query': per_query}


def subset(measurement, rows, predicate):
    keep = {row['id'] for row in rows if predicate(row)}
    return [item for item in measurement['per_query'] if item['id'] in keep]


def hit_at(items, k):
    if not items:
        return None
    return round(sum(1 for item in items if item['rank'] and item['rank'] <= k) / len(items), 3)


def main():
    import os
    work = OUT / 'run'
    work.mkdir(parents=True, exist_ok=True)
    db = work / 'experience.sqlite3'
    if db.exists():
        db.unlink()
    initialize(db)
    owner = AccessContext('m4-owner', (COLLECTION,), 'owner', (str(work),))
    agent = AccessContext('m4-agent', (COLLECTION,), 'agent', (str(work),))
    rows = [json.loads(line) for line in SUITE.read_text().splitlines() if line.strip()]
    overlap_stats = []
    for row in rows:
        episode = row['episode']
        text = f"{episode['goal']} {episode['explicit_reason']} {episode['narrative']}"
        row['overlap'] = overlap(text, row['query'])
        overlap_stats.append(row['overlap'])
        receipt = record_episode(db, owner, episode, idempotency_key=row['id'])
        row['event_id'] = receipt['event_id']
    os.environ.pop('MEMORY_HUB_EXPERIENCE_SEMANTIC', None)
    lexical = measure(db, agent, rows, 'lexical')
    os.environ['MEMORY_HUB_EXPERIENCE_SEMANTIC'] = '1'
    started = time.monotonic()
    rebuild = rebuild_keys(db, owner)
    index_seconds = round(time.monotonic() - started, 3)
    semantic = measure(db, agent, rows, 'semantic')
    self_rows = [{'id': row['id'], 'event_id': row['event_id'], 'query': row['episode']['goal'],
                  'overlap': row['overlap']} for row in rows]
    os.environ.pop('MEMORY_HUB_EXPERIENCE_SEMANTIC', None)
    self_lexical = measure(db, agent, self_rows, 'self-lexical')
    os.environ['MEMORY_HUB_EXPERIENCE_SEMANTIC'] = '1'
    self_semantic = measure(db, agent, self_rows, 'self-semantic')
    zero = [row for row in rows if row['overlap'] == 0]
    report = {'protocol': 'm4-semantic-1', 'model': os.environ.get('MEMORY_HUB_EMBED_MODEL', 'BAAI/bge-small-zh-v1.5'),
              'queries': len(rows), 'lexical_overlap_max': max(overlap_stats),
              'lexical_overlap_zero_share': round(sum(1 for value in overlap_stats if value == 0) / len(rows), 3),
              'zero_overlap_queries': len(zero),
              'zero_overlap': {'lexical_hit_at_3': hit_at(subset(lexical, rows, lambda r: r['overlap'] == 0), 3),
                               'semantic_hit_at_3': hit_at(subset(semantic, rows, lambda r: r['overlap'] == 0), 3),
                               'lexical_hit_at_1': hit_at(subset(lexical, rows, lambda r: r['overlap'] == 0), 1),
                               'semantic_hit_at_1': hit_at(subset(semantic, rows, lambda r: r['overlap'] == 0), 1)},
              'overlap_by_query': {row['id']: row['overlap'] for row in rows},
              'self_query_regression': {'lexical_hit_at_3': hit_at(self_lexical['per_query'], 3),
                                        'semantic_hit_at_3': hit_at(self_semantic['per_query'], 3)},
              'index_seconds': index_seconds, 'rebuild': rebuild,
              'store_bytes': db.stat().st_size, 'lexical': lexical, 'semantic': semantic}
    OUT.joinpath('result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('model', 'queries', 'lexical_overlap_max', 'lexical_overlap_zero_share',
                                             'index_seconds', 'store_bytes')}, ensure_ascii=False))
    for key in ('lexical', 'semantic'):
        print(key, {k: report[key][k] for k in ('hit_at_1', 'hit_at_3', 'seconds_per_query')})


if __name__ == '__main__':
    main()
