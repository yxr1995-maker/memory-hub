import json
from pathlib import Path

from evaluation.experience_v1 import actions, continuity, paired_host


SUITE = Path('evaluation/experience_v1/transfer-suite.json')


def test_transfer_suite_has_new_actions_and_separate_material_families():
    suite = json.loads(SUITE.read_text())
    regular = [t for t in suite['tasks'] if t['kind'] != 'negative_transfer']
    continuity.validate_suite({**suite, 'tasks': regular})
    assert len(suite['tasks']) == 12
    assert {t['action_case'] for t in regular if t['kind'] == 'engineering_action'} == {
        'manifest', 'staged', 'exit_status', 'csv_export'}
    assert len(actions.cases()) == 4
    for task in suite['tasks']:
        assert task['source_family'] != task['target_family']
        assert task['query'] not in {e['payload']['goal'] for e in suite['episodes']}
        assert task['task_text'] not in {e['payload']['narrative'] for e in suite['episodes']}
        if task['kind'] == 'creative_copy':
            assert task['rubric']['facts']
            assert task['rubric']['min_length'] < task['rubric']['max_length']


def test_transfer_suite_uses_existing_freezer_and_keeps_grading_outside_prompt(tmp_path):
    receipt = paired_host.freeze(tmp_path/'frozen', SUITE, structured_output=True)
    assert receipt['sessions'] == 36 and receipt['model_calls'] == 0
    rows = json.loads((tmp_path/'frozen/prepared.json').read_text())
    assert all(r['evidence'] == [] for r in rows if r['group'] == 'A')
    assert paired_host.sha(tmp_path/'frozen/snapshots/B/experience.sqlite3') == paired_host.sha(
        tmp_path/'frozen/snapshots/C/experience.sqlite3')
    for row in rows:
        assert 'expected_decision' not in row['prompt']
        assert 'expected_action' not in row['prompt']
        assert 'source_family' not in row['prompt']
        assert len(paired_host.compact(row['evidence'])) <= 6000
