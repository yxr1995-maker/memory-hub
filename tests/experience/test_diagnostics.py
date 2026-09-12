import copy
import hashlib
import json

import pytest

from evaluation.experience_v1 import actions


def row(raw, **updates):
    return dict(task_id='scratch', kind='engineering_action', group='C',
                transport_status='complete', format_pass=False, execution=None,
                semantic_success='unassessed', raw_answer=raw, **updates)


def test_wrong_fenced_action_is_diagnostic_only():
    from evaluation.experience_v1.diagnostics import diagnose
    source = row('```json\n{"action":"preserve"}\n```\nExplanation follows.')
    before = copy.deepcopy(source)
    result = diagnose(source, actions.cases()[1])
    assert result['declared_action'] == 'preserve'
    assert result['declared_action_agreement'] is False
    assert result['expected_action'] == 'reset'
    assert result['format_pass'] is False
    assert result['executed'] is False and result['first_pass'] is False
    assert result['semantic_success'] == 'unassessed'
    assert source == before


@pytest.mark.parametrize('raw', [
    '```json\n{"action":"reset"}\n```\n```json\n{"action":"preserve"}\n```',
    '{"action":"reset", "action":"preserve"}',
    '```json\n{"action":"reset", "action":"preserve"}\n```',
    '{"action": ["reset"]}', '{"action":"arbitrary"}',
    '[{"action":"reset"}]', '{"action":{"action":"reset"}}',
    '```json\n{"action":"reset"}\n```\n{"action":"preserve"}',
    '{"action":"reset"} trailing text',
])
def test_ambiguous_or_invalid_actions_remain_unknown(raw):
    from evaluation.experience_v1.diagnostics import diagnose
    result = diagnose(row(raw), actions.cases()[1])
    assert result['declared_action'] is None
    assert result['declared_action_agreement'] is None
    assert result['first_pass'] is False


def test_transport_failure_stays_unknown_and_executed_success_stays_separate():
    from evaluation.experience_v1.diagnostics import diagnose
    source = row('{"action":"reset"}')
    source['transport_status'] = 'error'
    assert diagnose(source, actions.cases()[1])['declared_action'] is None
    source.update(transport_status='complete', format_pass=True,
                  execution={'passed':True, 'action':'reset', 'observations':{'content':'NEW'}})
    result = diagnose(source, actions.cases()[1])
    assert result['declared_action_agreement'] is True
    assert result['first_pass'] is True
    assert result['postconditions'] == {'content':'NEW'}


def test_expected_action_requires_exact_task_contract():
    from evaluation.experience_v1.diagnostics import diagnose
    changed = {**actions.cases()[1], 'task_text':'Keep this file.'}
    result = diagnose(row('{"action":"reset"}'), changed)
    assert result['declared_action'] == 'reset'
    assert result['expected_action'] is None
    assert result['declared_action_agreement'] is None


def test_offline_cli_does_not_mutate_frozen_inputs_or_overwrite(tmp_path):
    from evaluation.experience_v1.diagnostics import main
    root = tmp_path/'frozen'; (root/'results').mkdir(parents=True)
    (root/'holdout_tasks.jsonl').write_text(json.dumps(actions.cases()[1])+'\n')
    source_hash = hashlib.sha256(__import__('pathlib').Path(actions.__file__).read_bytes()).hexdigest()
    (root/'manifest.json').write_text(json.dumps({'source_sha256':{'evaluation/experience_v1/actions.py':source_hash}}))
    result_path=root/'results/01-C.json'
    result_path.write_text(json.dumps(row('```json\n{"action":"preserve"}\n```\nExplanation.')))
    before = {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    out = tmp_path/'report.json'
    assert main([str(root), '--output', str(out)]) == 0
    report = json.loads(out.read_text())
    assert report['rows'][0]['source_sha256'] == hashlib.sha256(before[str(result_path)]).hexdigest()
    assert report['rows'][0]['expected_action'] == 'reset'
    assert report['new_model_requests'] == 0
    assert report['engineering_first_pass'] == {'A':0,'B':0,'C':0}
    with pytest.raises(FileExistsError): main([str(root), '--output', str(out)])
    with pytest.raises(ValueError): main([str(root), '--output', str(root/'new.json')])
    assert before == {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_changed_action_source_never_assigns_expected_action(tmp_path):
    from evaluation.experience_v1.diagnostics import build_report
    root=tmp_path; (root/'results').mkdir()
    (root/'holdout_tasks.jsonl').write_text(json.dumps(actions.cases()[1])+'\n')
    (root/'manifest.json').write_text(json.dumps({'source_sha256':{'evaluation/experience_v1/actions.py':'changed'}}))
    (root/'results/01-C.json').write_text(json.dumps(row('{"action":"reset"}')))
    report=build_report(root)
    assert report['action_source_matches_frozen'] is False
    assert report['rows'][0]['expected_action'] is None
