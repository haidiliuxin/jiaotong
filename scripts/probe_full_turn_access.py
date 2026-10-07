"""Run a new matched baseline, added turn ban and repeat with four native modes."""
import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.circulation import TurnGraph, append_restrictions, restrictions
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, save_json
from traffic_agent.oneway import FLOW_FILES
from traffic_agent.runner import EXPERIMENTS, execute, now, runtime_lock
from traffic_agent.turn_access import configure, install, verify_run
from traffic_agent.vehicle1 import MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--resume',type=Path,help='Resume only the verified baseline / unstarted policy preparation case')
    args=parser.parse_args()
    source=EXPERIMENTS/'jobs/20260908_075938_e7444a/scenarios/baseline/基础路网'
    turn=(4797,5990,5991)
    audit=TurnGraph.load(source).validate([turn])
    if not audit['eligible']:
        raise ValueError('新增禁转未通过公交和绕行图约束')
    base_turns=sorted(t for t in restrictions(source/'data/ManageN.txt') if t[1]==turn[1])
    before=input_fingerprints(source)
    if args.resume:
        job=args.resume.resolve()
        state=json.loads((job/'state.json').read_text(encoding='utf-8'))
        if (job.parent!=(EXPERIMENTS/'probes').resolve() or state['status']!='failed'
                or [r['name'] for r in state['runs']]!=['baseline'] or state['turn']!=list(turn)
                or (job/'logs/turn/run.json').exists() or (job/'bridge_runs/turn').exists()
                or json.loads((job/'source_inputs.json').read_text(encoding='utf-8'))!=before):
            raise ValueError('仅续跑已验证基准、政策尚未启动的准备阶段失败')
    else:
        job=EXPERIMENTS/'probes'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_full_turn_access')
        job.mkdir(parents=True,exist_ok=False)
        save_json(job/'source_inputs.json',before)
        save_json(job/'graph_audit.json',audit)
        state=dict(status='created',started=now(),source=str(source),turn=list(turn),base_turns=base_turns,runs=[],
                   limits='固定路口在基准和政策中应用既有禁转及禁止掉头，政策再新增一条禁转；需新匹配基准。')
        save_json(job/'state.json',state)
    print(f'PROBE {job}',flush=True)
    with runtime_lock(EXPERIMENTS):
        try:
            if args.resume:
                runtime=job/'runtime'
                profile=json.loads((job/'turn_access_profile.json').read_text(encoding='utf-8'))
                baseline=state['runs'][0]
                if profile!=state['profile'] or verify_run(job,'baseline',job/baseline['scenario'],profile,[])!=baseline['evidence']:
                    raise ValueError('已完成基准无法重新核验')
                save_json(job/'state_before_resume.json',state)
                state['resume']=dict(time=now(),reason='修正Windows实际manageN.txt文件名大小写；基准重新核验，政策从未启动')
                state.pop('error',None)
                state.pop('finished',None)
            else:
                runtime,profile=install(job,turn[1],base_turns)
            state['profile']=profile
            state['status']='running'
            for name in ('baseline','turn','turn_repeat'):
                if any(r['name']==name for r in state['runs']):
                    continue
                added=[] if name=='baseline' else [turn]
                scenario=job/'scenarios'/name/'基础路网'
                prepared=args.resume and name=='turn' and scenario.exists()
                if not prepared:
                    shutil.copytree(source,scenario)
                required=tuple(sorted(set(MODAL_OUTPUTS)|{'cache/ManageNM.dat'}|{'result/'+f for f in FLOW_FILES.values()}))
                for path in set(required)|{'result/'+f for f in OUTPUTS.values()}|set(UNSUPPORTED_TURN_OUTPUTS):
                    (scenario/path).unlink(missing_ok=True)
                if added and not prepared:
                    append_restrictions(scenario/'data/ManageN.txt',added)
                current=input_fingerprints(scenario)
                if {k for k in current.keys()|before.keys() if current.get(k)!=before.get(k)} != ({'data/manageN.txt'} if added else set()):
                    raise ValueError('新增禁转之外的输入发生变化')
                if restrictions(scenario/'data/ManageN.txt') != restrictions(source/'data/ManageN.txt')|set(added):
                    raise ValueError('准备的副本不是预定禁转规则')
                environment=configure(job,runtime,name,profile,added)
                state['active_scenario']=name
                save_json(job/'state.json',state)
                def event(kind,payload):
                    with (job/'events.jsonl').open('a',encoding='utf-8') as stream:
                        stream.write(json.dumps(dict(time=now(),event=kind,data=payload),ensure_ascii=False)+'\n')
                    if kind=='tool_start':
                        print(json.dumps(dict(event=kind,data=payload),ensure_ascii=False),flush=True)
                _,execution=execute(scenario,runtime,job/'logs'/name,900,event,required_outputs=required,
                                    assignment_module='M6AssignVehicle1.exe',bridge_environment=environment)
                evidence=verify_run(job,name,scenario,profile,added)
                save_json(job/'logs'/name/'turn_verification.json',evidence)
                record=dict(name=name,scenario=str(scenario.relative_to(job)),execution=execution,evidence=evidence)
                if name!='baseline':
                    base=state['runs'][0]['evidence']
                    response={}
                    for mode in FLOW_FILES.keys()-{'bus'}:
                        q0=next((r['flow'] for r in base['modes'][mode]['turn_totals'] if r['turn']==list(turn)),0)
                        q1=next((r['flow'] for r in evidence['modes'][mode]['turn_totals'] if r['turn']==list(turn)),0)
                        response[mode]=dict(before=q0,after=q1,positive_baseline=q0>1)
                    record['response']=response
                    if response['car']['before']<=1 or any(r['after']!=0 for r in response.values()):
                        raise ValueError('基准正流量和实际新增禁转响应未通过')
                if name=='turn_repeat':
                    previous=job/state['runs'][1]['scenario']
                    repeat={f:numeric(previous/'result'/f)==numeric(scenario/'result'/f) for f in list(OUTPUTS.values())+list(FLOW_FILES.values())}
                    record['repeat_arrays']=repeat
                    if not all(repeat.values()) or any(evidence['modes'][m]['turn_totals']!=state['runs'][1]['evidence']['modes'][m]['turn_totals'] for m in evidence['modes']):
                        raise ValueError('策略复跑的道路数组或转移量不同')
                state['runs'].append(record)
                save_json(job/'state.json',state)
                print(json.dumps(dict(event='turn_access_verified',scenario=name,native_calls=evidence['native_calls'],response=record.get('response')),ensure_ascii=False),flush=True)
            state['status']='completed'
        except BaseException as exc:
            state.update(status='failed',error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            state.update(source_inputs_unchanged=input_fingerprints(source)==before,finished=now())
            save_json(job/'state.json',state)


if __name__=='__main__':
    main()
