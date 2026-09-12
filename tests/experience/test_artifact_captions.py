"""M4-2: artifact captions must make media findable without weakening artifact rules."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scripts.automation_core.experience import record_episode, recall_for_decision, semantic
from scripts.automation_core.experience.contracts import ExperienceError
from scripts.automation_core.experience.store import connect, tokens


def media_file(tmp_path, name, content=b'fake-image-bytes'):
    target = tmp_path / name
    target.write_bytes(content)
    return target


def artifacts_for(path):
    return [{'artifact_id': 'a1', 'media_type': 'image', 'location': str(path),
             'checksum': hashlib.sha256(path.read_bytes()).hexdigest(), 'license': 'read'}]


def caption_payload(payload, path, caption=None):
    """Neutral goal/reason/narrative so only the caption can carry the query terms."""
    artifact = artifacts_for(path)[0]
    if caption is not None:
        artifact = artifact | {'caption': caption}
    return payload | {'goal': '宠物用品素材归档', 'narrative': '本周收到供应商发的素材。',
                      'explicit_reason': '素材要能被后续文案复用。', 'conditions': {'task_kind': 'creative'},
                      'artifacts': [artifact]}


def test_caption_makes_media_findable(tmp_path, env):
    db, owner, agent, payload = env
    image = media_file(tmp_path, 'leash.png')
    receipt = record_episode(db, owner, caption_payload(payload, image, '反光条可调长度牵引绳夜间散步实拍'),
                             idempotency_key='caption-1')
    result = recall_for_decision(db, agent, task='反光条 牵引绳 实拍', mode='evidence')
    assert [item['episode_id'] for item in result['items']] == [receipt['event_id']]
    assert result['items'][0]['retrieval_match']['matched_terms'] == {'artifact': tokens('反光条 牵引绳 实拍')}


def test_media_without_caption_stays_invisible(tmp_path, env):
    db, owner, agent, payload = env
    image = media_file(tmp_path, 'leash.png')
    record_episode(db, owner, caption_payload(payload, image), idempotency_key='caption-2')
    result = recall_for_decision(db, agent, task='反光条 牵引绳 实拍', mode='evidence')
    assert [item['episode_id'] for item in result['items']] == []


def test_caption_is_bounded_and_must_be_text(tmp_path, env):
    db, owner, agent, payload = env
    image = media_file(tmp_path, 'leash.png')
    for bad in ('x' * 1001, 42, '', '   '):
        broken = caption_payload(payload, image, bad)
        with pytest.raises(ExperienceError) as excinfo:
            record_episode(db, owner, broken, idempotency_key=f'bad-{type(bad).__name__}-{len(str(bad))}')
        assert excinfo.value.code == 'PAYLOAD_INVALID'


def test_captions_feed_semantic_documents(tmp_path, env, monkeypatch):
    db, owner, agent, payload = env
    monkeypatch.setenv('MEMORY_HUB_EXPERIENCE_SEMANTIC', '1')
    embedded = []

    def fake_embed(texts):
        embedded.extend(texts)
        return [[1.0, 0.0] if '夜跑标识' in text else [0.0, 1.0] for text in texts]

    monkeypatch.setattr(semantic, 'embed_texts', fake_embed)
    image = media_file(tmp_path, 'leash.png')
    receipt = record_episode(db, owner, caption_payload(payload, image, '反光条牵引绳夜跑标识实拍'),
                             idempotency_key='caption-semantic')
    assert any('夜跑标识' in document for document in embedded), 'caption must enter the semantic document'
    with connect(db) as connection:
        row = connection.execute('SELECT vector FROM experience_vectors WHERE event_id=?',
                                 (receipt['event_id'],)).fetchone()
    assert list(semantic._unpack(row['vector'])) == [1.0, 0.0]
    result = recall_for_decision(db, agent, task='夜跑标识', mode='evidence')
    assert receipt['event_id'] in [item['episode_id'] for item in result['items']]


def test_artifact_rules_are_unchanged(tmp_path, env):
    db, owner, agent, payload = env
    outside = Path(tmp_path).parent / 'outside.png'
    outside.write_bytes(b'outside')
    broken = caption_payload(payload, outside, '越界素材')
    with pytest.raises(ExperienceError) as excinfo:
        record_episode(db, owner, broken, idempotency_key='outside')
    assert excinfo.value.code == 'ACCESS_DENIED'
