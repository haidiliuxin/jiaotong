"""Inspect the new contract against sealed observations, with no model/native I/O.

The reply is a declared structural fixture, not a recovered or generated model
reply. Original rejected content remains unknown and the original run incomplete.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.feedback_planner import PlanningSession,run_observations,digest
from traffic_agent.planning_budget import VERSION as BUDGET_VERSION,LEGACY_VERSION,starting_with,reserved_after_current
from traffic_agent.study_handoff import VERSION as HANDOFF_VERSION
from traffic_agent.task import Task


def inspect(snapshot):
    snapshot=Path(snapshot)
    before=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    with zipfile.ZipFile(snapshot) as archive:
        manifest=json.loads(archive.read('incomplete_snapshot_manifest.json'))
        checked=[]
        def load(name):
            data=archive.read(name);expected=manifest['files'][name]
            if len(data)!=expected['bytes'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:
                raise ValueError('封存输入与原清单不符')
            checked.append(name)
            return json.loads(data)
        owner=manifest['job_ids'][0]
        original=load(f'智能体实验/dispatches/{owner}.json')
        plan=load(f'智能体实验/programmes/{owner}/plan.json')
        state=load(f'智能体实验/jobs/{owner}/state.json')
        diagnosis=load(f'智能体实验/jobs/{owner}/diagnosis.json')
        last=original['planning_trace'][-1]
        if not (original['status']=='incomplete' and last['kind']=='parking' and last['status']=='rejected'
                and list(last['input']['allowed_actions'])==['stop'] and len(plan['studies'])>1):
            raise ValueError('检查仅适用停车末轮复核失败且后续组尚未启动的封存案例')
        pending=plan['studies'][1:]
        if any(f"智能体实验/jobs/{s['job_id']}/state.json" in archive.namelist() for s in pending):
            raise ValueError('后续组已有执行记录，不能作为未启动案例检查')
    observations=run_observations(state)
    if observations!=last['input']['observations']:raise ValueError('档案观测不完整或包含该回合之后的输出')
    variants=[]
    for omit_review in (False,True):
        current_plan=copy.deepcopy(plan)
        current_plan['routing_budget']['version']=BUDGET_VERSION
        working=copy.deepcopy(state);working['planner_trace']=[]
        current=SimpleNamespace(job_id=owner,state=working,task=Task.from_dict(state['task']),
                                persist=lambda:None,event=lambda *args:None)
        agents=[current]+[SimpleNamespace(job_id=s['job_id'],state=dict(id=s['job_id'],task=s['task'],
                            runs=[],run_count=0,status='queued')) for s in pending]
        calls=[]
        class StructuralFixture:
            def request(self,instructions,inputs,tools,choice):
                payload=json.loads(inputs[0]['content']);calls.append(payload)
                if payload['observations']!=observations:raise AssertionError('Actual observations changed')
                if any(g['execution_complete'] for g in payload['context']['programme_evidence']['groups'][1:]):
                    raise AssertionError('Future study was presented as completed')
                allowed=payload['allowed_actions']
                if list(allowed)!=[s['id'] for s in pending]:raise AssertionError('Unexpected handoff menu')
                ctx=payload['context']
                value=dict(action=next(iter(allowed)),summary='本地结构检查读取封存的配对实测；这不是模型回复。',
                    reason='仅核对本组复核与下一组菜单，不形成新的治理建议。',
                    risk='原任务仍未完成，离线通过不能证明新模型已作出正确决策。',
                    next_check='下一组仍须独立建立新基准并进行约定对照。',evidence_refs=payload['evidence_refs'][-1:],
                    hypothesis_updates=[] if omit_review else [dict(hypothesis_id=h['id'],judgment='unresolved',finding_ids=[],
                        explanation='这是结构校验样例，不替模型判断初始假设。') for h in ctx['initial_hypotheses']],
                    cost_acknowledgments=[{k:r[k] for k in ('ref','constraint_passed','has_measured_cost','neighbor_new_oversaturated_km')}
                                          for r in ctx['measured_conclusion']['costs']])
                return dict(calls=[dict(name='choose_next_experiment',arguments=json.dumps(value,ensure_ascii=False))],
                            metadata=dict(tokens=0,response_model='offline-structural-fixture'))
        planner=PlanningSession(StructuralFixture(),original['request'],hypotheses=plan['hypotheses'])
        planner.used=last['input']['model_budget']['used']
        planner.bind_programme(current_plan,agents)
        planner.pending_studies=copy.deepcopy(pending)
        planner.reserved_requests=reserved_after_current(state['task'],[s['task'] for s in pending])
        _,entry=planner.parking(current,diagnosis,[],set(),0)
        if len(calls)!=1:raise AssertionError('Unexpected fixture call count')
        expected='rejected' if omit_review else 'accepted'
        if entry['status']!=expected:raise AssertionError('Structural fixture did not reach expected validation')
        if omit_review and ('selected' in entry or entry['validation']['code']!='hypothesis_coverage'):
            raise AssertionError('Incomplete review could select a study')
        variants.append(dict(fixture='missing_review' if omit_review else 'complete_fields',status=entry['status'],
            validation=entry['validation'],allowed_actions=list(entry['input']['allowed_actions']),
            route_budget=entry['input']['context']['route_budget'],
            observations_unchanged=entry['input']['observations']==observations,
            observations_sha256=digest(observations),future_results_supplied=False,
            selected_study=entry.get('selected',{}).get('title')))
    after=hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if before!=after:raise AssertionError('Original snapshot changed')
    tasks=[s['task'] for s in plan['studies']]
    return dict(kind='offline_handoff_contract_inspection_v1',handoff_protocol=HANDOFF_VERSION,
                original_job=owner,original_status=original['status'],original_snapshot_sha256=before,
                original_files_checked=checked,original_snapshot_unchanged=True,
                original_exact_rejection_still_unknown=True,paid_calls=0,new_native_scenes=0,
                minimum_request_slots=dict(previous=1+starting_with(tasks,version=LEGACY_VERSION),current=1+starting_with(tasks)),
                variants=variants,meaning='只用封存实测检查新协议；回复为明确标注的结构样例。没有恢复原模型回复、调用服务、续跑原任务或取得新的仿真结果；原研究仍未完成。')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('检查输出已存在，不覆盖已有证据')
    result=inspect(args.snapshot)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(dict(passed=True,paid_calls=0,new_native_scenes=0,original_status=result['original_status']),ensure_ascii=False))


if __name__=='__main__':main()
