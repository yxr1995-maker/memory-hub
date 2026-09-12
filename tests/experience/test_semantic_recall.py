"""M4-1: semantic candidates must find what lexical keys miss, without weakening safety."""
from __future__ import annotations

from dataclasses import replace

import pytest

from scripts.automation_core.experience import record_episode, recall_for_decision, semantic


def fake_embedder(vocab):
    """Deterministic stand-in: keyword -> one-hot; keeps the suite hermetic."""
    dim = len(vocab)

    def embed(texts):
        out = []
        for text in texts:
            vec = [0.0] * dim
            for keyword, index in vocab.items():
                if keyword in text:
                    vec[index] = 1.0
            norm = sum(value * value for value in vec) ** 0.5 or 1.0
            out.append([value / norm for value in vec])
        return out

    return embed


@pytest.fixture
def semantic_env(env, monkeypatch):
    db, owner, agent, payload = env
    monkeypatch.setattr(semantic, 'embed_texts', fake_embedder(
        {'退款': 0, 'refund': 0, '退货': 1, 'return': 1, '文案': 2, 'copy': 2}))
    return db, owner, agent, payload


def _refund_payload(payload):
    return payload | {
        'goal': '退款流程需要人工复核',
        'narrative': '大额退款先由人工复核再放行。',
        'explicit_reason': '金额超过阈值时必须人工确认。',
        'conditions': {'task_kind': 'ops'},
    }


def test_cross_language_paraphrase_is_invisible_to_lexical_recall(env):
    db, owner, agent, payload = env
    record_episode(db, owner, _refund_payload(payload), idempotency_key='lexical-gap')
    result = recall_for_decision(db, agent, task='refund approval', mode='evidence')
    assert [item['episode_id'] for item in result['items']] == []


def test_semantic_candidates_find_the_paraphrase_when_enabled(semantic_env, monkeypatch):
    db, owner, agent, payload = semantic_env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_SEMANTIC', '1')
    receipt = record_episode(db, owner, _refund_payload(payload), idempotency_key='semantic-hit')
    result = recall_for_decision(db, agent, task='refund approval', mode='evidence')
    assert receipt['event_id'] in [item['episode_id'] for item in result['items']]
    assert result['usage']['model_calls'] == 0


def test_semantic_recall_never_leaves_the_authorized_collections(semantic_env, monkeypatch):
    db, owner, agent, payload = semantic_env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_SEMANTIC', '1')
    secret = _refund_payload(payload) | {'collection_id': 'private'}
    record_episode(db, replace(owner, allowed_collections=('private',)), secret, idempotency_key='secret')
    public = record_episode(db, owner, _refund_payload(payload), idempotency_key='public')
    result = recall_for_decision(db, agent, task='refund approval', mode='evidence')
    ids = [item['episode_id'] for item in result['items']]
    assert public['event_id'] in ids
    assert not any(item['source_refs'][0].get('ref') == 'development/creative-01' and item['episode_id'] != public['event_id'] for item in result['items'])


def test_semantic_mode_falls_back_to_lexical_when_the_embedder_is_gone(env, monkeypatch):
    db, owner, agent, payload = env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_SEMANTIC', '1')
    monkeypatch.setattr(semantic, 'embed_texts', lambda texts: None)
    receipt = record_episode(db, owner, payload, idempotency_key='fallback')
    result = recall_for_decision(db, agent, task='修改文案，要有感情但不要有负担', mode='evidence')
    assert receipt['event_id'] in [item['episode_id'] for item in result['items']]


def test_lexical_results_are_unchanged_while_semantic_is_off(env, monkeypatch):
    db, owner, agent, payload = env
    monkeypatch.delenv('MEMORY_HUB_EXPERIENCE_SEMANTIC', raising=False)
    receipt = record_episode(db, owner, payload, idempotency_key='lexical-order')
    before = recall_for_decision(db, agent, task='修改文案，要有感情但不要有负担', mode='evidence')
    monkeypatch.setattr(semantic, 'embed_texts', fake_embedder({'文案': 0}))
    after = recall_for_decision(db, agent, task='修改文案，要有感情但不要有负担', mode='evidence')
    assert [item['episode_id'] for item in before['items']] == [item['episode_id'] for item in after['items']]
    assert before['items'][0]['episode_id'] == receipt['event_id']

