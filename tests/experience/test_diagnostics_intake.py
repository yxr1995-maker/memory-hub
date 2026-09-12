import json

import pytest

from evaluation.experience_v1 import actions


def row(raw, **updates):
    base = dict(task_id='scratch', kind='engineering_action', group='C',
                transport_status='complete', format_pass=False, execution=None,
                semantic_success='unassessed', raw_answer=raw)
    base.update(updates)
    return base


def _frozen_root(tmp_path):
    root = tmp_path/'frozen'; (root/'results').mkdir(parents=True)
    (root/'holdout_tasks.jsonl').write_text(json.dumps(actions.cases()[1])+'\n')
    (root/'manifest.json').write_text(json.dumps({}))
    return root


def test_intake_requires_core_row_fields():
    from evaluation.experience_v1.diagnostics import diagnose
    with pytest.raises(ValueError, match='task_id'):
        diagnose({'kind': 'engineering_action', 'group': 'C'}, actions.cases()[1])
    with pytest.raises(ValueError, match='group'):
        diagnose({'kind': 'engineering_action', 'task_id': 'scratch'}, actions.cases()[1])


def test_result_referencing_unknown_task_is_intake_error(tmp_path):
    from evaluation.experience_v1.diagnostics import build_report
    root = _frozen_root(tmp_path)
    (root/'results/01-C.json').write_text(json.dumps(row('{"action":"reset"}', task_id='nope')))
    with pytest.raises(ValueError, match='nope'):
        build_report(root)


def test_result_missing_required_fields_is_intake_error(tmp_path):
    from evaluation.experience_v1.diagnostics import build_report
    root = _frozen_root(tmp_path)
    bad = row('{"action":"reset"}'); del bad['group']
    (root/'results/01-C.json').write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='group'):
        build_report(root)


def test_malformed_result_file_names_source(tmp_path):
    from evaluation.experience_v1.diagnostics import build_report
    root = _frozen_root(tmp_path)
    (root/'results/01-C.json').write_text('{not json')
    with pytest.raises(ValueError, match='01-C'):
        build_report(root)


def test_duplicate_holdout_task_id_is_intake_error(tmp_path):
    from evaluation.experience_v1.diagnostics import build_report
    root = tmp_path; (root/'results').mkdir()
    (root/'holdout_tasks.jsonl').write_text(
        json.dumps(actions.cases()[1])+'\n'+json.dumps(actions.cases()[1])+'\n')
    (root/'manifest.json').write_text(json.dumps({}))
    with pytest.raises(ValueError, match='duplicate'):
        build_report(root)
