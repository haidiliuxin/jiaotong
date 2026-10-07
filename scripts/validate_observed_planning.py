"""One bounded live planning case; no transport retries, probes or historical reuse.

Credentials remain in the local service. Maximum: six model requests and three
fresh native scenes (six for the mixed programme). One initial correction counts toward the six
model requests. Existing evidence directories are never overwritten.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import save_json
from traffic_agent.runner import now
from traffic_agent.llm_settings import redact
from traffic_agent.natural_plan import capabilities

REQUEST = ('请缓解案例道路的停车干扰拥堵，先用新基准定位可治理方向，比较停车治理强度并检查周边影响。'
           '只做停车研究，不安排单向或禁转。最多治理1条；需求系数：1；最多3次仿真，不复跑。'
           '根据新观测决定下一轮，允许无收益时停止。')
REGIONAL_REQUEST = ('请用新基准找出停车干扰拥堵，比较两个不同区域的停车治理效果，判断应优先处理哪里。'
                    '只做停车研究，不比较强度，不安排单向或禁转。最多治理1条；需求系数：1；最多3次仿真，不复跑。'
                    '先依据基准选择第一个区域，再根据实测选择另一区域；保留没有收益或周边恶化的结果。')
PROGRAMME_REQUEST = ('分别研究七东线的停车治理，以及长江村路的单向组织与停车组合。'
                     '两条道路分成独立研究组，每组运行自己的新基准。依据新基准选择治理方向，再读取治理结果复核；'
                     '单向组必须比较停车、单向及组合。各组收益不可相加；保留无收益、不合格和周边代价。'
                     '只比较同一需求，不比较停车强度；需求系数：1；最多治理2条；总预算6次仿真，不复跑。')
CASES = dict(strength=dict(request=REQUEST,native_limit=3),regions=dict(request=REGIONAL_REQUEST,native_limit=3),
             programme=dict(request=PROGRAMME_REQUEST,native_limit=6))


def validate_preflight(value,case):
    """These known representative inputs must not spend quota on false gaps."""
    from traffic_agent.natural_plan import limits
    from traffic_agent.planning_scope import PREREQUISITE_VERSION,unsupported_capabilities
    from traffic_agent.plan_compiler import diagnosis_constraints
    expected_roads=['七东线','长江村路'] if case=='programme' else []
    expected_conditions=limits(CASES[case]['request'])
    diagnostic=diagnosis_constraints(CASES[case]['request'],expected_roads)
    expected_conditions.update(diagnosis_constraints=diagnostic,diagnosis_only=diagnostic['only'])
    if (not isinstance(value,dict) or value.get('version')!='planning-preflight-v1'
            or value.get('request')!=CASES[case]['request'] or value.get('matched_roads')!=expected_roads
            or value.get('conditions')!=expected_conditions
            or value.get('model_requested') is not False or value.get('native_started') is not False
            or not value.get('source_inputs')):
        raise ValueError('Local input preflight differs from the bounded case; no paid request')
    gate=value.get('decision_permissions',{})
    supported=unsupported_capabilities(CASES[case]['request'])
    if (gate.get('version')!=PREREQUISITE_VERSION or gate.get('clarification_needs')!=[]
            or gate.get('clarification_quotes')!={} or gate.get('unsupported_capabilities')!=supported
            or gate.get('intents')!=['execute',*(['unsupported'] if supported else [])]):
        raise ValueError('Known case incorrectly opens a location clarification; no paid request')


def validate_preview(preview,case):
    tasks=[e['task'] for e in preview.get('experiments',[])]
    if (preview.get('status')!='ready' or not tasks or preview['model_budget']['limit']>7
            or any(t['search_mode']!='standard' or t['repeat_baseline'] or t['demand_factors']!=[1.0] for t in tasks)):
        raise ValueError('Model plan does not meet this bounded case; not launched or retried')
    if case=='programme':
        expected={('parking','七东线'):2,('oneway','长江村路'):4}
        if (len(tasks)!=2 or {(t['scenario'],t['road_name']) for t in tasks}!=set(expected)
                or any(t['max_runs']!=expected[(t['scenario'],t['road_name'])] or t['max_links']!=2 for t in tasks)):
            raise ValueError('Mixed case needs two separately paired studies within six scenes; not launched')
    elif (len(tasks)!=1 or tasks[0]['scenario']!='parking' or tasks[0]['max_runs']!=3 or tasks[0]['max_links']!=1):
        raise ValueError('Parking case does not preserve the three-scene comparison')
    if sum(t['max_runs'] for t in tasks)>CASES[case]['native_limit']:
        raise ValueError('Case native budget exceeded')


def verify_case_states(states,case):
    """Case-specific evidence; raw files are separately verified by verify_programme."""
    from traffic_agent.planning_review import coverage
    from traffic_agent.conclusion_review import VERSION as CONCLUSION_VERSION,assess_state
    from traffic_agent.study_handoff import closes_study
    if len(states)!=(2 if case=='programme' else 1):raise ValueError('Study set is incomplete')
    parking_count=0;target_count=0;reviews={};conclusions={};areas=[]
    for state in states.values():
        if state['status']!='completed' or any(r['execution']['status']!='completed' for r in state['runs']):
            raise ValueError('A study is incomplete; no success certificate')
        if state.get('evidence_protocol')!='question-evidence-v1' or not state.get('research_review',{}).get('reviewed'):
            raise ValueError('A study lacks a complete hypothesis review')
        conclusion=assess_state(state);conclusions[state['id']]=conclusion
        if state.get('conclusion_protocol')!=CONCLUSION_VERSION or conclusion['status']!='no_detected_conflict':
            raise ValueError('Measured costs or known unmeasured observables are not correctly reviewed')
        policies=[r for r in state['runs'] if r['changes']]
        if state['task']['scenario']=='parking':
            review=coverage(state);reviews[state['id']]=review
            decisions=[t for t in state.get('planner_trace',[]) if t['status']=='accepted' and t.get('selected',{}).get('changes')]
            wanted=1 if case=='programme' else 2
            if (state.get('planning_protocol')!='observed-planning-v3' or not review['complete']
                    or len(policies)!=wanted or len(decisions)!=wanted or state['run_count']!=wanted+1):
                raise ValueError('Mandatory parking comparisons or final model review are incomplete')
            if any(r['changes']!=t['selected']['changes'] for r,t in zip(policies,decisions)):
                raise ValueError('Executed parking policy differs from the model choice')
            if not any(t['status']=='accepted' and closes_study(t['action'],t['input'].get('context',{}))
                       and len(t['input']['observations'])==len(state['runs']) for t in state['planner_trace']):
                raise ValueError('The model did not review all new parking results')
            if case=='strength' and not review['strength_compared']:
                raise ValueError('Mandatory same-direction strength comparison is incomplete')
            if case=='regions':
                areas=[t['selected'].get('region') for t in decisions]
                if len(set(areas))!=2 or None in areas or policies[0]['changes'][0]['parking']!=policies[1]['changes'][0]['parking']:
                    raise ValueError('Different areas were not compared at the same treatment level')
            if not all(r.get('spatial_observation') for r in state['runs']):
                raise ValueError('An experiment lacks fresh whole-network and regional observations')
            parking_count+=len(decisions)
        elif case=='programme' and state['task']['scenario']=='oneway':
            targets=[t for t in state.get('planner_trace',[]) if t['kind']=='oneway_target' and t['status']=='accepted']
            if (state.get('target_planning_protocol')!='oneway-target-v1' or len(targets)!=1
                    or state['run_count']!=4 or len(policies)!=3 or not state.get('factorial_design',{}).get('full_demand_grid')):
                raise ValueError('One-way target choice or the four-way comparison is incomplete')
            target_count+=1
        else:raise ValueError('Unexpected strategy in the bounded case')
    if case=='programme' and (parking_count!=1 or target_count!=1):raise ValueError('Missing model-selected study')
    return dict(goal_review_by_job=reviews,conclusion_reviews=conclusions,tested_regions=areas,
                model_selected_parking_policies=parking_count,model_selected_oneway_targets=target_count,
                run_count=sum(s['run_count'] for s in states.values()),
                native_modules=sum(len(r['execution']['modules']) for s in states.values() for r in s['runs']),
                recommendations={s['id']:s['recommendation'] for s in states.values()})


def check_capabilities(api):
    actual=api('/api/planning-capabilities')
    expected=capabilities()
    if not isinstance(actual,dict) or any(actual.get(k)!=v for k,v in expected.items()):
        raise ValueError('Local service planning contract differs from this validator; no paid request or native launch')
    return actual


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-api',action='store_true')
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--port',type=int,default=8769)
    parser.add_argument('--case',choices=list(CASES),default='strength')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if not args.live_api or not args.native:parser.error('Requires explicit --live-api --native; requests are chargeable')
    args.output.mkdir(parents=True,exist_ok=False)
    request_text=CASES[args.case]['request']
    record=dict(started=now(),request=request_text,case=args.case,status='started',model_request_limit=6,native_run_limit=CASES[args.case]['native_limit'],
                native_launched=False,initial_preview_submitted=False,new_model_calls=0)
    save_json(args.output/'acceptance.json',record)
    def api(path,body=None):
        request=Request(f'http://127.0.0.1:{args.port}'+path,json.dumps(body).encode() if body is not None else None,
                        {'Content-Type':'application/json'})
        try:
            # Preview may include two sequential calls, each capped at 120 s.
            with urlopen(request,timeout=250 if path=='/api/dispatch/preview' else 65) as response:return json.load(response)
        except HTTPError as exc:
            # This is our local JSON error envelope, not a third-party body.
            try:detail=redact(json.loads(exc.read(2000)).get('error','请求被拒绝'))[:600]
            except (ValueError,AttributeError):detail='本地接口未返回可读错误'
            raise ValueError(f'Local HTTP {exc.code}: {detail}') from None
    try:
        record['server_capabilities']=check_capabilities(api)
        record['input_preflight']=api('/api/dispatch/preflight',dict(request=request_text))
        save_json(args.output/'acceptance.json',record)
        validate_preflight(record['input_preflight'],args.case)
        record.update(initial_preview_submitted=True,new_model_calls=None)
        save_json(args.output/'acceptance.json',record)
        preview=api('/api/dispatch/preview',dict(matcher='model',request=request_text))
        save_json(args.output/'plan.json',preview)
        record['initial_model_usage']=preview.get('model_usage')
        record['initial_model_usages']=preview.get('initial_model_usages')
        if (preview.get('matched_at','')<record['started'] or preview.get('version')!='natural-strategy-plan-v5'
                or preview.get('composition_protocol')!=record['server_capabilities']['composition_protocol']):
            raise ValueError('Not a fresh observed-planning programme')
        validate_preview(preview,args.case)
        launch=api('/api/dispatch/run',dict(ticket=preview['ticket']))
        if launch.get('reused'):raise ValueError('Existing job cannot be reused as new evidence')
        record.update(job_id=launch['id'],job_ids=launch['job_ids'],native_launched=True)
        save_json(args.output/'acceptance.json',record)
        print(json.dumps(dict(job_id=launch['id'],status='native_started')),flush=True)
        deadline=time.monotonic()+2400
        while time.monotonic()<deadline:
            dispatch=next(r for r in api('/api/dispatch') if r['job_id']==launch['id'])
            if dispatch['status'] not in ('starting','running'):
                save_json(args.output/'dispatch.json',dispatch)
                record['model_budget']=dispatch.get('model_budget')
                if dispatch['status']!='completed':raise ValueError('Programme did not finish; evidence retained')
                states={id:api('/api/jobs/'+id)['state'] for id in launch['job_ids']}
                record.update(verify_case_states(states,args.case))
                from traffic_agent.planning_audit import verify_planning
                from traffic_agent.runner import EXPERIMENTS
                record['planning_audits']={id:verify_planning(s,json.loads((EXPERIMENTS/'jobs'/id/'diagnosis.json').read_text('utf-8'))) for id,s in states.items()}
                if (dispatch.get('programme_protocol')!='programme-evidence-v1'
                        or not dispatch.get('programme_review',{}).get('complete')):
                    raise ValueError('The whole programme has no complete evidence-based review')
                from scripts.verify_programme import verify as verify_programme
                record['programme_audit']=verify_programme(launch['id'],EXPERIMENTS,write=False)
                record.update(status='verified',decisions=[{k:t.get(k) for k in ('job_id','ordinal','status','action','summary','reason','risk','next_check','evidence_refs','cost_acknowledgments','reviewed_cost_refs','usage')} for t in dispatch['planning_trace']])
                break
            time.sleep(3)  # Local status reads only, never model polling.
        else:raise TimeoutError('Wait limit reached; native process preserved, no automatic restart')
    except TimeoutError as exc:
        record.update(status='attention_required',error='TimeoutError: '+str(exc))
    except Exception as exc:
        record.update(status='failed',error=type(exc).__name__+': '+str(exc))
    finally:
        from traffic_agent.runner import EXPERIMENTS
        from traffic_agent.planning_attempt import listing as preview_attempts
        attempts=preview_attempts(EXPERIMENTS,limit=None)
        matches=[a for a in attempts if a.get('started','')>=record['started'] and a.get('request')==request_text]
        if matches:
            record['initial_planning_attempts']=matches
            record['new_model_calls']=record.get('model_budget',{}).get('used',sum(a['requested'] for a in matches))
            record['known_initial_tokens']=sum((a.get('usage') or {}).get('tokens') or 0 for a in matches)
            record['initial_unknown_usage']=sum(a['requested'] and (a.get('usage') or {}).get('tokens') is None for a in matches)
        record['finished']=now();save_json(args.output/'acceptance.json',record)
    print(json.dumps({k:v for k,v in record.items() if k!='decisions'},ensure_ascii=False),flush=True)
    return 0 if record['status']=='verified' else 1


if __name__=='__main__':raise SystemExit(main())
