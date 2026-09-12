import io,json
from scripts.automation_core.memory_relations import check_relation

EXISTING=[{'path':'decision.md','quote':'I prefer purple report titles.','scope_id':'p'}]

def opener(value):
    def call(*args,**kwargs):
        return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}).encode())
    return call

def test_grounded_semantic_duplicate():
    r=check_relation('I prefer violet report headings.',EXISTING,timeout=1,opener=opener({'relation':'duplicate','path':'decision.md','matched_quote':EXISTING[0]['quote']}))
    assert r['relation']=='duplicate' and r['scope']['coverage']=='complete_input'

def test_unknown_source_fails_closed():
    r=check_relation('I prefer blue report titles.',EXISTING,timeout=1,opener=opener({'relation':'conflict','path':'invented.md','matched_quote':EXISTING[0]['quote']}))
    assert r['relation']=='uncertain'

def test_limited_selection_cannot_claim_new():
    r=check_relation('I prefer blue report titles.',EXISTING,timeout=1,max_existing=0,opener=opener({'relation':'new'}))
    assert r['relation']=='uncertain'

def test_secret_candidate_not_sent():
    def forbidden(*a,**k):raise AssertionError('must not send')
    r=check_relation('TOKEN="fixture secret value"',EXISTING,timeout=1,opener=forbidden)
    assert r['relation']=='uncertain'

def test_gateway_failure_fails_closed():
    def failing(*a,**k):raise TimeoutError()
    assert check_relation('I prefer blue headings.',EXISTING,timeout=1,opener=failing)['relation']=='uncertain'


def test_project_local_memory_cannot_deduplicate_global_preference():
    r=check_relation('I prefer purple titles across all projects.',EXISTING,scope_id='global-user',timeout=1,opener=opener({'relation':'duplicate','path':'decision.md','matched_quote':EXISTING[0]['quote']}))
    assert r['relation']=='uncertain'


def test_mismatched_existing_quote_is_not_grounded():
    r=check_relation('I prefer blue titles.',EXISTING,timeout=1,opener=opener({'relation':'conflict','path':'decision.md','matched_quote':'invented quote'}))
    assert r['relation']=='uncertain'


def test_truncated_gateway_response_is_uncertain():
    def call(*a,**k):return io.BytesIO(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{"relation":"new"}'}}]}).encode())
    assert check_relation('I prefer blue titles.',EXISTING,timeout=1,opener=call)['relation']=='uncertain'


def test_unknown_scope_duplicate_is_reviewable_not_gateway_failure():
    existing=[dict(EXISTING[0],scope_id='unknown')]
    r=check_relation('我长期偏好报告使用紫色标题。',existing,scope_id='p',timeout=1,
        opener=opener({'relation':'duplicate','path':'decision.md','matched_quote':existing[0]['quote']}))
    assert r['relation']=='uncertain'
    assert r['reason']=='incompatible_duplicate_scope'
    assert r['path']=='decision.md' and r['matched_quote']==existing[0]['quote']
