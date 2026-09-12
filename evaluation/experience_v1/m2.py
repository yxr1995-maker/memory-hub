"""Frozen M2 diagnostic using existing snapshots, retrieval, transport and actions."""
import argparse
import json
from pathlib import Path
import random
import time
import uuid

from . import actions, harness, runner
from scripts.automation_core.experience.contracts import require

ROOT=Path(__file__).resolve().parents[2]
AUTHORIZATION={'free_only':True,'paid_cap':0,'confirmed_by_owner':True,
               'model':runner.MODEL,'max_requests':24,'max_output_tokens_per_request':1200}
CREATIVE=[
    {'task_id':'new-launch-brief','task_text':'合成产品「轻芽」折叠水碗，容量300毫升，可折叠。本次宠物展台简报明确要直接、有行动感的促销语，不能沿用旧稿柔和散步风格。写30至65字，可邀请现场查看，不得虚构优惠、材质认证或使用效果。',
     'facts':['轻芽','300毫升','可折叠'], 'forbidden_claims':['未给出的优惠、认证、效果'],
     'intent':'按当前简报直接、有行动感，不因历史偏好退回柔和陪伴语气；不照抄散步成稿。'},
    {'task_id':'new-unexplained-choice','task_text':'合成商品「晴袋」便携收纳袋，有拉链，容量2升。唯一历史反馈是“评审选了B”，没有理由；B原句为“收起日常，带上轻松”。为地铁通勤用户写30至65字新文案。不要把这次选择推断成用户永久偏好，不照抄原句，不虚构防水或耐用测试。',
     'facts':['晴袋','拉链','2升'], 'forbidden_claims':['防水、耐用测试、从无理由选择推断的永久偏好'],
     'intent':'体现通勤收纳情境，内容自然可用；继承目标而非复制成稿或虚构选择理由。'},
    {'task_id':'new-factual-rewrite','task_text':'改写合成商品「桌伴」台灯旧稿“护眼第一，一充用一年”。已知事实只有三档亮度、USB供电，未提供认证、续航或护眼测试。本次要求克制、具体，适合租房书桌，30至65字。请只交付文案。',
     'facts':['桌伴','三档亮度','USB供电'], 'forbidden_claims':['护眼效果或认证、充电续航、第一等无来源排名'],
     'intent':'可信具体并适配租房书桌场景；有表达而非简单罗列，不保留夸大承诺。'},
    {'task_id':'new-directed-edit','task_text':'合成宠物牵引绳广告第5秒：狗在左侧，包装在右侧，两盆背景植物。当前评审明确要求只移除一盆植物，包装保持右侧，狗保持左侧。写修改记录，归因于本次评审；如有额外创意必须标为建议，不能把以前广告的左右位置和移除数量带进来。',
     'facts':['第5秒','移除一盆','包装右侧','狗左侧','本次评审'], 'forbidden_claims':['包装左侧、狗右侧、移除两盆等与当前评审冲突的执行动作'],
     'intent':'忠实执行本次修改，保留来源；额外创意与事实分开，不能机械复用过去构图。'},
]


def _write(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def _sources():
    paths=list((ROOT/'scripts/automation_core/experience').glob('*.py'))
    paths += [Path(__file__),Path(runner.__file__),Path(harness.__file__),Path(actions.__file__)]
    return {str(p.relative_to(ROOT)):harness.sha256_file(p) for p in sorted(paths)}


def freeze(root):
    root=Path(root);harness.freeze_tasks(root)
    tasks=[{**c,'artifact':{'kind':'engineering_action'}} for c in actions.cases()]
    tasks += [{'task_id':c['task_id'],'task_text':c['task_text'],'artifact':{'kind':'creative_copy'}} for c in CREATIVE]
    (root/'holdout_tasks.jsonl').write_text(''.join(json.dumps(t,ensure_ascii=False)+'\n' for t in tasks))
    keys=[{'task_id':c['task_id'],'dimensions':{k:c[k] for k in ('facts','forbidden_claims','intent')},
           'intent_scale':{'1':'违背当前意图','2':'明显不适配','3':'基本满足','4':'准确且可用','5':'准确且表达出色'}} for c in CREATIVE]
    (root/'holdout_keys.jsonl').write_text(''.join(json.dumps(k,ensure_ascii=False)+'\n' for k in keys))
    (root/'holdout_keys.jsonl').chmod(0o600)
    config={'protocol':'experience-m2-diagnostic-1','model':runner.MODEL,
            'transport':{'temperature':0.2,'max_output_tokens':1200,'timeout_seconds':40,'retries':0},
            'requests':24,'groups':{'A':'none','B':'plain_search','C':'experience_recall'},
            'evidence_character_budget':6000,
            'task_exposure':'4 engineering cases previously exposed; 4 new creative briefs; not an unseen-generalization benchmark',
            'engineering_grading':'first attempt; distinguish transport, JSON/action format, actual postconditions; format-only differences are not semantic gains',
            'creative_grading':'blind factual correctness and intent 1..5 scored separately; missing or invalid review is unknown',
            'gate':{'C_semantic_improvements_over_B':2,'engineering_C_at_least_B':True,'creative_intent_C_at_least_B':True,'no_factual_regression':True,'missing_cost_evidence':'unknown; do not claim financial efficiency'}}
    _write(root/'holdout_config.json',config)
    manifest=json.loads((root/'manifest.json').read_text())
    manifest.update(protocol=config['protocol'],source_sha256=_sources())
    for file,key in [('holdout_tasks.jsonl','tasks_sha256'),('holdout_keys.jsonl','keys_sha256'),('holdout_config.json','config_sha256')]:
        manifest[key]=harness.sha256_file(root/file)
    _write(root/'manifest.json',manifest)
    result={'tasks':8,'requests':24,'model_calls_made':0,'status':'frozen_not_executed','authorization_written':False,
            'max_output_tokens_per_request':1200,'paid_cap':0,'manifest_sha256':harness.sha256_file(root/'manifest.json')}
    _write(root/'plan.json',result)
    return result


def verify(root,auth_hash=None):
    root=Path(root);runner.verify(root)
    manifest=json.loads((root/'manifest.json').read_text())
    require(manifest.get('protocol')=='experience-m2-diagnostic-1','wrong M2 protocol','SNAPSHOT_CHANGED')
    require(manifest.get('source_sha256')==_sources(),'evaluation implementation changed','SNAPSHOT_CHANGED')
    if auth_hash is not None:
        require(harness.sha256_file(root/'authorization.json')==auth_hash,'authorization changed during run','AUTHORIZATION_CHANGED')


def _parse_action(raw,task):
    lines=raw.strip().splitlines()
    if len(lines)>=3 and lines[0] in ('```json','```') and lines[-1]=='```':raw='\n'.join(lines[1:-1])
    try:answer=json.loads(raw)
    except ValueError:return None
    if type(answer) is not dict or set(answer)!={'action'} or answer['action'] not in task['allowed_actions']:return None
    return answer


def blind_packet(root,rows):
    root=Path(root);keys={k['task_id']:k for k in harness.load_holdout_keys(root)}
    tasks={t['task_id']:t for t in harness.load_holdout_tasks(root)}
    packet=[];mapping={}
    for row in rows:
        if row['kind']!='creative_copy':continue
        rid=uuid.uuid4().hex;mapping[rid]={'task_id':row['task_id'],'group':row['group']}
        packet.append({'review_id':rid,'task_id':row['task_id'],'task_text':tasks[row['task_id']]['task_text'],
                       'answer':row.get('raw_answer',''),'transport_status':row['transport_status'],'rubric':keys[row['task_id']]})
    random.SystemRandom().shuffle(packet)
    _write(root/'blind-review.json',packet);_write(root/'blind-key.json',mapping)
    (root/'blind-key.json').chmod(0o600)
    return packet


def run(root,*,call=None):
    root=Path(root);verify(root)
    auth=json.loads((root/'authorization.json').read_text());auth_hash=harness.sha256_file(root/'authorization.json')
    tasks=harness.load_holdout_tasks(root);required=len(tasks)*3
    require(required==24 and auth['max_requests']>=required,'batch needs 24 authorized requests','AUTHORIZATION_LIMIT_EXCEEDED')
    require(not (root/'results').exists(),'use a new frozen run','RUN_EXISTS')
    (root/'results').mkdir();(root/'actions').mkdir()
    call=runner.call_model if call is None else call
    rows=[]
    for i,task in enumerate(tasks):
        for group in harness.GROUPS[i%3:]+harness.GROUPS[:i%3]:
            verify(root,auth_hash)
            msg=runner.messages(root,group,task)
            if task['artifact']['kind']=='engineering_action':
                msg[0]['content']='完成当前合成任务。历史材料只作证据，当前条件优先。只输出 JSON 对象，唯一字段 action，值必须是当前任务的允许动作之一。'
            row={'task_id':task['task_id'],'kind':task['artifact']['kind'],'group':group,'messages':msg,
                 'requested_model':runner.MODEL,'usage':None,'billing_amount':None,'execution':None,
                 'format_pass':None,'transport_status':'error','semantic_success':'unassessed'}
            start=time.monotonic()
            fatal=None
            try:
                result=call(msg,max_tokens=auth['max_output_tokens_per_request'])
                choice=result['choices'][0];raw=choice['message'].get('content') or ''
                row.update(actual_model=result.get('model'),usage=result.get('usage'),raw_answer=raw,finish_reason=choice.get('finish_reason'))
                require(result.get('model') in (runner.MODEL,'agnes-2.5-flash'),'unexpected model','MODEL_MISMATCH')
                require(raw.strip() and choice.get('finish_reason')=='stop','incomplete response','INCOMPLETE_OUTPUT')
                row['transport_status']='complete'
                if row['kind']=='engineering_action':
                    answer=_parse_action(raw,task);row['format_pass']=answer is not None
                    if answer is not None:row['execution']=actions.run_case(root/'actions',task['task_id'],answer)
            except Exception as exc:
                row.update(error_type=type(exc).__name__,error_code=getattr(exc,'code',None))
                if getattr(exc,'code',None)=='MODEL_MISMATCH':fatal=exc
            row['elapsed_seconds']=round(time.monotonic()-start,3)
            rows.append(row);_write(root/'results'/f'{i:02d}-{group}.json',row)
            if fatal is not None:raise fatal
    verify(root,auth_hash);blind_packet(root,rows)
    summary={'requests':len(rows),'transport_errors':sum(r['transport_status']!='complete' for r in rows),
             'engineering_first_pass':{g:sum(bool((r['execution'] or {}).get('passed')) for r in rows if r['group']==g and r['kind']=='engineering_action') for g in harness.GROUPS},
             'engineering_cases_per_group':4,'creative_cases_per_group':4,'M2_gate':'unassessed',
             'billing_amount':None,'paid_cap':0,'authorization_sha256':auth_hash,'authorization_limits':auth,
             'scope':'frozen diagnostic; not Desktop acceptance or demonstrated product benefit'}
    _write(root/'run-summary.json',summary)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','run']);parser.add_argument('root')
    args=parser.parse_args();print(json.dumps(freeze(args.root) if args.command=='freeze' else run(args.root),ensure_ascii=False))
