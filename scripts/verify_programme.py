"""Read raw job evidence before certifying a complete multi-study programme."""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import save_json
from traffic_agent.feedback_planner import digest
from traffic_agent.programme_review import verify as verify_planning_programme
from traffic_agent.runner import EXPERIMENTS
from scripts.verify_experiment import verify as verify_job


def verify(id,root=EXPERIMENTS,archived=False,write=True):
    if not re.fullmatch(r'[A-Za-z0-9_]+',id):raise ValueError('研究总任务编号无效')
    root=Path(root);directory=root/'programmes'/id
    plan=json.loads((directory/'plan.json').read_text('utf-8'))
    record=json.loads((directory/'result.json').read_text('utf-8'))
    if record['status']!='completed':raise ValueError('总任务尚未完整，不能出具完整验收结论')
    if plan.get('id')!=id or record.get('job_id')!=id:raise ValueError('总任务目录与记录编号不一致')
    job_ids=[s.get('job_id') for s in plan.get('studies',[])]
    if (not job_ids or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z0-9_]+',k) for k in job_ids)
            or len(set(job_ids))!=len(job_ids)):
        raise ValueError('研究组编号缺失、重复或越界')
    if record!=json.loads((root/'dispatches'/f'{id}.json').read_text('utf-8')):raise ValueError('总任务结果与调度记录不一致')
    states={};native=[]
    for study in plan['studies']:
        job=root/'jobs'/study['job_id']
        for filename,expected in [('programme_plan.json',plan),('dispatch.json',record),('source_inputs.json',plan['source_inputs'])]:
            if json.loads((job/filename).read_text('utf-8'))!=expected:raise ValueError('研究组来源或冻结文件不同：'+filename)
        state=json.loads((job/'state.json').read_text('utf-8'));states[study['job_id']]=state
        saved=json.loads((job/'plan.json').read_text('utf-8'))
        if saved.get('programme_plan_digest')!=digest(plan):raise ValueError('总计划未随本组任务预先冻结')
        if any(datetime.fromisoformat(r['execution']['started'])<=datetime.fromisoformat(plan['created']) for r in state['runs']):
            raise ValueError('总计划在某组原生执行之后才建立')
        if state.get('programme_review')!=record['programme_review']:raise ValueError('组内报告的总任务复核不同')
        if state.get('planner_trace',[])!=[t for t in record.get('planning_trace',[]) if t['job_id']==study['job_id']]:
            raise ValueError('模型总日志与组内日志不同')
        result=verify_job(job,archived=archived,write=False)
        native.append(dict(job_id=study['job_id'],raw_verified=True,archived=archived,verification_kind=result.get('verification_kind')))
    audit=verify_planning_programme(plan,record,states)
    from traffic_agent.initial_planner import verify_initial
    from traffic_agent.planning_response import request_usages
    requests=verify_initial(record,plan)+sum(len(request_usages(t)) for t in record.get('planning_trace',[]))
    budget=record['model_budget']
    if requests!=budget['used'] or requests>budget['limit']:raise ValueError('模型请求记账或总上限不符')
    report=dict(passed=True,programme=id,protocol=plan['version'],native=native,planning=audit,model_requests=requests)
    if write:save_json(directory/'verification.json',report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('id');parser.add_argument('--root',type=Path,default=EXPERIMENTS)
    parser.add_argument('--archive',action='store_true');parser.add_argument('--no-write',action='store_true')
    args=parser.parse_args()
    print(json.dumps(verify(args.id,args.root,args.archive,not args.no_write),ensure_ascii=False,indent=2))
