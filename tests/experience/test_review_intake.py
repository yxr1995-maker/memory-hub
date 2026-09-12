import copy
import hashlib
import json

import pytest

from evaluation.experience_v1 import diagnostics


def fixture(tmp_path, *, status='complete', answer='known words'):
    root = tmp_path/'frozen'; root.mkdir()
    packet = [{'review_id':'c1', 'kind':'creative_copy', 'answer':answer,
               'transport_status':status},
              {'review_id':'n1', 'kind':'negative_transfer', 'answer':'abstain because missing',
               'transport_status':'complete'}]
    path = root/'blind-review.json'; path.write_text(json.dumps(packet))
    review = {'packet_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
              'reviewer':'test-reviewer', 'group_identity_exposed':False,
              'rows':[{'review_id':'c1','fact_accuracy':'pass','intent_score':4,
                       'reason':'test evidence','quotes':['known words']},
                      {'review_id':'n1','decision_pass':True,'reason_pass':None,
                       'historical_source_accuracy':'unknown','reason':'not independently established',
                       'quotes':['abstain']}]}
    return root, tmp_path/'review.json', review


def check(root, path, review):
    path.write_text(json.dumps(review))
    return diagnostics.build_review_report(root, path)


def test_valid_structure_does_not_certify_quality_or_blindness(tmp_path):
    root, path, review = fixture(tmp_path)
    # These files must never be opened by review intake.
    (root/'blind-key.json').write_text('not JSON')
    (root/'results').mkdir()
    result = check(root, path, review)
    assert result['structural_validation'] == 'passed'
    assert result['quality_verdict'] == 'unassessed'
    assert result['blindness'] == 'declared_unverified'
    assert result['new_model_requests'] == 0
    assert result['errors'] == []
    assert 'M2_gate' not in result


@pytest.mark.parametrize('mutation', [
    lambda r:r.update(packet_sha256='wrong'),
    lambda r:r.update(group_identity_exposed=True),
    lambda r:r.pop('group_identity_exposed'),
    lambda r:r.update(reviewer=' '),
    lambda r:r['rows'].pop(),
    lambda r:r['rows'].append(copy.deepcopy(r['rows'][0])),
    lambda r:r['rows'][0].update(review_id='missing'),
    lambda r:r['rows'][0].update(intent_score=True),
    lambda r:r['rows'][0].update(intent_score=6),
    lambda r:r['rows'][0].update(fact_accuracy='excellent'),
    lambda r:r['rows'][0].update(quotes=['made up quote']),
    lambda r:r['rows'][0].update(quotes=[]),
    lambda r:r['rows'][0].update(reason=' '),
    lambda r:r['rows'][1].update(decision_pass=1),
    lambda r:r['rows'][1].pop('reason_pass'),
    lambda r:r['rows'][1].update(historical_source_accuracy=True),
])
def test_invalid_review_is_reported_not_silently_repaired(tmp_path, mutation):
    root, path, review = fixture(tmp_path)
    mutation(review)
    result = check(root, path, review)
    assert result['structural_validation'] == 'failed'
    assert result['errors']
    assert result['quality_verdict'] == 'unassessed'


@pytest.mark.parametrize('raw', ['{', '[]', '{"rows":[],"rows":[]}', '{"score":NaN}'])
def test_invalid_json_or_object_is_reported(tmp_path, raw):
    root, path, _ = fixture(tmp_path); path.write_text(raw)
    result = diagnostics.build_review_report(root, path)
    assert result['structural_validation'] == 'failed'


def test_json_overflow_is_rejected_even_in_extra_metadata(tmp_path):
    root, path, review = fixture(tmp_path)
    raw = json.dumps(review)[:-1] + ',"unused":1e999}'
    path.write_text(raw)
    assert diagnostics.build_review_report(root, path)['structural_validation'] == 'failed'


def test_review_must_be_external_to_frozen_directory_including_symlinks(tmp_path):
    root, _, _ = fixture(tmp_path)
    key = root/'blind-key.json'; key.write_text('must not be read')
    alias = tmp_path/'review-alias.json'; alias.symlink_to(key)
    for path in [key, alias]:
        with pytest.raises(ValueError, match='outside'):
            diagnostics.build_review_report(root, path)


def test_failed_transport_cannot_receive_positive_rating(tmp_path):
    root, path, review = fixture(tmp_path, status='error')
    assert check(root, path, review)['structural_validation'] == 'failed'
    review['rows'][0].update(fact_accuracy='unknown', intent_score=None)
    assert check(root, path, review)['structural_validation'] == 'passed'


def test_missing_answer_only_accepts_unknown_with_no_quote(tmp_path):
    root, path, review = fixture(tmp_path, status='error', answer='')
    review['rows'][0].update(fact_accuracy='unknown', intent_score=None, quotes=[])
    assert check(root, path, review)['structural_validation'] == 'passed'


def test_bad_packet_and_missing_file_are_distinct(tmp_path):
    root, path, review = fixture(tmp_path)
    (root/'blind-review.json').write_text('[{"review_id":"c1"}]')
    assert check(root, path, review)['structural_validation'] == 'failed'
    with pytest.raises(FileNotFoundError):
        diagnostics.build_review_report(root, tmp_path/'absent.json')


def test_cli_is_read_only_and_failed_intake_has_nonzero_exit(tmp_path):
    root, path, review = fixture(tmp_path); path.write_text(json.dumps(review))
    before = (root/'blind-review.json').read_bytes(), path.read_bytes()
    output = tmp_path/'report.json'
    assert diagnostics.main([str(root),'--review',str(path),'--output',str(output)]) == 0
    assert before == ((root/'blind-review.json').read_bytes(), path.read_bytes())
    with pytest.raises(FileExistsError):
        diagnostics.main([str(root),'--review',str(path),'--output',str(output)])
    with pytest.raises(ValueError):
        diagnostics.main([str(root),'--review',str(path),'--output',str(root/'report.json')])
    review['group_identity_exposed'] = True; path.write_text(json.dumps(review))
    assert diagnostics.main([str(root),'--review',str(path),'--output',str(tmp_path/'failed.json')]) == 1
