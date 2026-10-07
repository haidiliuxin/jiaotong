"""Re-read complete native turn pairs and repeat, including all archived stages."""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.circulation import TurnGraph, restrictions
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, read_text, results, save_json
from traffic_agent.oneway import FLOW_FILES
from traffic_agent.turn_access import MODES, verify_run
from traffic_agent.vehicle1 import UNSUPPORTED_TURN_OUTPUTS


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def verify(job):
    state=load(job/'state.json')
    source=Path(state['source'])
    source_inputs=load(job/'source_inputs.json')
    if state['status']!='completed' or input_fingerprints(source)!=source_inputs or not state['source_inputs_unchanged']:
        raise ValueError('完整任务未完成或源输入变化')
    if [r['name'] for r in state['runs']]!=['baseline','turn','turn_repeat']:
        raise ValueError('需要新基准、禁转和政策复跑')
    profile=load(job/'turn_access_profile.json')
    if state['profile']!=profile or profile['version']!=2 or not profile['no_uturn'] or not profile['original_reference_each_stage']:
        raise ValueError('转向配置记录不同')
    turn=state['turn']
    audit=TurnGraph.load(source).validate([turn])
    if audit!=load(job/'graph_audit.json') or not audit['eligible']:
        raise ValueError('公交和图绕行保护不符')
    base_rules=restrictions(source/'data/ManageN.txt')
    summaries=[]
    native_outputs={}
    reference_outputs={}
    for record in state['runs']:
        name=record['name'];scenario=job/record['scenario'];logs=job/'logs'/name
        execution=load(logs/'run.json')
        modules=['M3DataRoadNetwork.exe','M3DataBusNetwork.exe','M3DataTrafficManage.exe','M6AssignVehicle1.exe',
                 'M6TrafficLoadLinkVeh.exe','M6TrafficLoadNodeVeh.exe','M6NodeQueueDelay.exe']
        if (execution!=record['execution'] or execution['status']!='completed'
                or len(execution['modules'])!=7 or any(m['returncode'] for m in execution['modules'])):
            raise ValueError('顶层模型执行记录错误')
        # The runner retains each module's exact executable name in its record.
        observed=[m.get('module',m.get('tool')) for m in execution['modules']]
        if observed!=modules:
            raise ValueError(f'模型调用顺序错误：{observed}')
        inputs=input_fingerprints(scenario)
        if inputs!=load(logs/'inputs_before.json'):
            raise ValueError('场景输入与执行记录不同')
        expected_changes=set() if name=='baseline' else {'data/manageN.txt'}
        if {k for k in inputs.keys()|source_inputs.keys() if inputs.get(k)!=source_inputs.get(k)}!=expected_changes:
            raise ValueError('外层政策差异超出ManageN')
        expected_rules=base_rules | (set() if name=='baseline' else {tuple(turn)})
        if restrictions(scenario/'data/ManageN.txt')!=expected_rules:
            raise ValueError('不是预定新增禁转')
        tokens=list(map(int,read_text(scenario/'cache/ManageNM.dat').split()))
        cached={tuple(tokens[i:i+3]) for i in range(1,len(tokens),3)}
        if len(tokens)!=1+3*tokens[0] or len(cached)!=tokens[0] or cached!=expected_rules:
            raise ValueError('全部规则未进入新管理缓存')
        if any((scenario/path).exists() for path in UNSUPPORTED_TURN_OUTPUTS):
            raise ValueError('残留旧车型转向表，可能误作新证据')
        for relative,stamp in execution['fresh_outputs'].items():
            p=scenario/relative
            if p.stat().st_size!=stamp['bytes'] or p.stat().st_mtime_ns!=stamp['mtime_ns'] or p.stat().st_mtime<datetime.fromisoformat(execution['started']).timestamp()-2:
                raise ValueError('模型输出不是记录的本次新文件')
        added=[] if name=='baseline' else [turn]
        evidence=verify_run(job,name,scenario,profile,added)
        if evidence!=record['evidence'] or evidence!=load(logs/'turn_verification.json'):
            raise ValueError('重新计算的转换和流量核验不符')
        if name!='baseline':
            response={}
            for mode in MODES:
                q0=next((r['flow'] for r in state['runs'][0]['evidence']['modes'][mode]['turn_totals'] if r['turn']==turn),0)
                q1=next((r['flow'] for r in evidence['modes'][mode]['turn_totals'] if r['turn']==turn),0)
                response[mode]=dict(before=q0,after=q1,positive_baseline=q0>1)
            if response!=record['response'] or response['car']['before']<=1 or any(r['after']!=0 for r in response.values()):
                raise ValueError('新增禁转实际响应不符')
        samples={mode:[] for mode in MODES}
        for path in sorted((job/'bridge_runs'/name).glob('*/manifest.json')):
            m=load(path)
            samples[m['mode']].append((m['started'],numeric(path.parent/f"scenario/cache/volume_{m['mode']}.dat"),
                                      numeric(path.parent/f"reference_scenario/cache/volume_{m['mode']}.dat")))
        native_outputs[name]={mode:[v for _,v,_ in sorted(rows)] for mode,rows in samples.items()}
        reference_outputs[name]={mode:[v for _,_,v in sorted(rows)] for mode,rows in samples.items()}
        summaries.append(dict(name=name,native_calls=40,response=record.get('response'),
                              modes={m:{k:v for k,v in evidence['modes'][m].items() if k!='turn_totals'} for m in MODES}))
    policy=job/state['runs'][1]['scenario'];repeat=job/state['runs'][2]['scenario']
    arrays={f:numeric(policy/'result'/f)==numeric(repeat/'result'/f) for f in list(OUTPUTS.values())+list(FLOW_FILES.values())}
    if arrays!=state['runs'][2]['repeat_arrays'] or not all(arrays.values()):
        raise ValueError('九类道路数组复跑不同')
    native_exact=native_outputs['turn']==native_outputs['turn_repeat']
    if not native_exact:
        raise ValueError('策略20次原生数组复跑不同')
    reference_exact=reference_outputs['turn']==reference_outputs['turn_repeat']
    if not reference_exact:
        raise ValueError('策略20次原版参考数组复跑不同')
    result=dict(passed=True,scenarios=3,top_level_calls=21,native_assignment_calls=120,adapted_calls=60,reference_calls=60,
                repeat_nine_arrays_exact=arrays,repeat_native_arrays_exact=native_exact,
                repeat_reference_arrays_exact=reference_exact,runs=summaries,
                limits='固定非质心路口的完整分配接口配对；没有完成S1/S3及需求组合，也不代表全网既有规则或现实收益。')
    supplementary=job/'supplementary_assessment.json'
    if supplementary.exists():
        from traffic_agent.agent import compare
        from traffic_agent.task import Task
        assessment=load(supplementary)
        scope=assessment['scope']
        if scope!=load(Path(assessment['scope_source'])) or assessment['scope_sizes']!={k:len(v) for k,v in scope.items()}:
            raise ValueError('补充评价没有沿用已固定范围')
        actual=compare(results(job/state['runs'][0]['scenario']),results(policy),scope,Task.from_dict(assessment['task_thresholds']))
        if actual!=assessment['comparison']:
            raise ValueError('补充评价指标重新计算不同')
        result['supplementary_metrics_verified']=True
    save_json(job/'verification.json',result)
    print(json.dumps({k:v for k,v in result.items() if k!='runs'},ensure_ascii=False,indent=2))
    return result


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('job',type=Path)
    verify(parser.parse_args().job.resolve())
