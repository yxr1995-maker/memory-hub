import pytest
from evaluation.experience_v1 import actions


@pytest.mark.parametrize('case,good,bad', [
    ('manifest','prune_manifest','prune_all'),
    ('staged','stage_then_replace','direct_write'),
    ('exit_status','check_exit','trust_log'),
    ('csv_export','csv_writer','join_commas'),
])
def test_transfer_postconditions_reject_wrong_actions(tmp_path, case, good, bad):
    result = actions.run_case(tmp_path, case, {'action':good})
    assert result['passed'] and result['observations']['unrelated_preserved']
    assert not actions.run_case(tmp_path, case, {'action':bad})['passed']
    other = 'always_fail' if case == 'exit_status' else 'skip'
    assert not actions.run_case(tmp_path, case, {'action':other})['passed']


def test_transfer_cases_are_opt_in_and_unknown_action_never_writes(tmp_path):
    assert len(actions.cases()) == 4
    assert len(actions.cases(include_transfer=True)) == 8
    assert not actions.run_case(tmp_path, 'manifest', {'action':'../../outside'})['passed']
    assert not list(tmp_path.iterdir())
