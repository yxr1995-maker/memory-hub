"""Optional semantic candidates for experience recall (M4-1).

Vectors are a derived, rebuildable index: never authoritative, never required.
Embeddings come from the same local model used by scripts/embed.py; when the
embedder is missing the caller keeps lexical behaviour unchanged.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
from pathlib import Path

VECTOR_TABLE = ('CREATE TABLE IF NOT EXISTS experience_vectors('
                'event_id TEXT NOT NULL,revision INTEGER NOT NULL,model TEXT NOT NULL,'
                'dim INTEGER NOT NULL,vector BLOB NOT NULL,PRIMARY KEY(event_id,revision))')
DEFAULT_MODEL = 'BAAI/bge-small-zh-v1.5'
SWITCH = 'MEMORY_HUB_EXPERIENCE_SEMANTIC'
_embedder = None
_embedder_failed = False


def enabled() -> bool:
    """The host decides; absence of the switch means the simple baseline."""
    return os.environ.get(SWITCH) == '1'


def model_name() -> str:
    return os.environ.get('MEMORY_HUB_EMBED_MODEL', DEFAULT_MODEL)


def embedder():
    """Lazy local embedder; None when fastembed or its cached model is unavailable."""
    global _embedder, _embedder_failed
    if _embedder is not None or _embedder_failed:
        return _embedder
    try:
        from fastembed import TextEmbedding
        cache = os.environ.get('FASTEMBED_CACHE_PATH') or str(Path.home() / '.cache' / 'fastembed')
        _embedder = TextEmbedding(model_name(), cache_dir=cache)
    except Exception:
        _embedder_failed = True
        return None
    return _embedder


def embed_texts(texts):
    """Return float vectors or None; never raises for callers."""
    model = embedder()
    if model is None:
        return None
    try:
        return [[float(value) for value in vector] for vector in model.embed(list(texts))]
    except Exception:
        return None


def _pack(vector):
    return struct.pack('<%df' % len(vector), *vector)


def _unpack(blob):
    return struct.unpack('<%df' % (len(blob) // 4), blob)


def _document(payload) -> str:
    parts=[
        str(payload['goal']),
        str(payload.get('explicit_reason') or ''),
        str(payload['narrative']),
        json.dumps(payload['conditions'], ensure_ascii=False, sort_keys=True),
    ]
    artifacts=_artifact_document(payload)
    if artifacts:parts.append(artifacts)
    return ' '.join(parts)


def _artifact_document(payload) -> str:
    """Media context for the derived vector; still no pixel decoding."""
    parts=[]
    for a in payload.get('artifacts',[]):
        parts.extend((str(a['media_type']),str(a.get('segment') or ''),str(a.get('caption') or '')))
    return ' '.join(part for part in parts if part)


def index_vectors(c, event_id, revision, payload) -> bool:
    """Refresh one version's derived vector; silent no-op without an embedder."""
    if not enabled():
        return False
    vectors = embed_texts([_document(payload)])
    if not vectors:
        return False
    c.execute(VECTOR_TABLE)
    c.execute('DELETE FROM experience_vectors WHERE event_id=?', (event_id,))
    c.execute('INSERT INTO experience_vectors VALUES(?,?,?,?,?)',
              (event_id, int(revision), model_name(), len(vectors[0]), _pack(vectors[0])))
    return True


def semantic_candidates(c, ctx, task, limit=20):
    """Cosine top-k over authorized active versions; [] when unavailable."""
    if not enabled() or not ctx.allowed_collections:
        return []
    vectors = embed_texts([task])
    if not vectors:
        return []
    query = vectors[0]
    query_norm = math.sqrt(sum(value * value for value in query)) or 1.0
    try:
        rows = c.execute(
            '''SELECT v.event_id, v.vector FROM experience_vectors v
               JOIN experience_events e ON e.event_id = v.event_id
               JOIN experience_versions x ON x.event_id = v.event_id AND x.revision = v.revision
               WHERE e.status='active' AND x.review_required=0
                 AND e.collection_id IN (SELECT value FROM json_each(?))''',
            (json.dumps(sorted(ctx.allowed_collections)),)).fetchall()
    except sqlite3.Error:
        return []
    scored = []
    for row in rows:
        candidate = _unpack(row['vector'])
        if len(candidate) != len(query):
            continue
        dot = sum(a * b for a, b in zip(query, candidate))
        norm = math.sqrt(sum(value * value for value in candidate)) or 1.0
        scored.append((dot / (query_norm * norm), row['event_id']))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [event_id for _, event_id in scored[:limit]]
