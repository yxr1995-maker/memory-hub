"""Feedback-derived utility order (M4-3).

Feedback is a secondary key only: it can reorder candidates that already share the
same relevance score. It never grants access, never hides evidence, and never lifts
an episode above a better match. Weights stay fixed and auditable; an agent's own
report is worth less than an owner-attested result.
"""
from __future__ import annotations

import json
import os
import sqlite3

SWITCH = 'MEMORY_HUB_EXPERIENCE_UTILITY'
WEIGHTS = {'validated': 2, 'adopted': 1, 'modified': 1, 'rejected': -2}


def enabled() -> bool:
    """The host decides; absence of the switch means plain relevance order."""
    return os.environ.get(SWITCH) == '1'


def detail(c, ctx, ids):
    """{event_id: {'score': int, 'actions': {...}}} for already authorized candidates."""
    if not enabled() or not ids or not ctx.allowed_collections:
        return {}
    marks = ','.join('?' for _ in ids)
    try:
        rows = c.execute(f'''SELECT target_id, payload_json FROM experience_feedback
            WHERE target_id IN ({marks})
              AND collection_id IN (SELECT value FROM json_each(?))''',
            [*ids, json.dumps(sorted(ctx.allowed_collections))]).fetchall()
    except sqlite3.Error:
        return {}
    out = {}
    for row in rows:
        try:
            action = json.loads(row['payload_json'])['action']
        except (ValueError, TypeError, KeyError):
            continue
        weight = WEIGHTS.get(action)
        if not weight:
            continue
        entry = out.setdefault(row['target_id'], {'score': 0, 'actions': {}})
        entry['score'] += weight
        entry['actions'][action] = entry['actions'].get(action, 0) + 1
    return out


def rank(c, ctx, order, base):
    """Sort by relevance first, then by feedback utility; ties stay by event id."""
    marks = detail(c, ctx, order)
    if not marks:
        return order
    return sorted(order, key=lambda i: (-base[i], -marks.get(i, {}).get('score', 0), i))

