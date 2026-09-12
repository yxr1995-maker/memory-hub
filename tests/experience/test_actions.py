import pytest
from evaluation.experience_v1 import actions

@pytest.mark.parametrize('case,good,bad',[('edited','preserve','overwrite'),('scratch','reset','backup_and_reset'),('race','conditional_update','overwrite'),('backup','sqlite_backup','empty_backup')])
def test_actual_postconditions(tmp_path,case,good,bad):
    assert actions.run_case(tmp_path,case,{'action':good})['passed']
    assert not actions.run_case(tmp_path,case,{'action':bad})['passed']

def test_unknown_actions_never_execute(tmp_path):
    assert not actions.run_case(tmp_path,'edited',{'action':'../../escape'})['passed']
    assert not list(tmp_path.iterdir())
