"""Compare turn-state terminal layouts with frozen, unmodified vendor solving.

Supports an origin-zone partition diagnostic and the later full-OD source-only
layout. This script remains a single-car-increment probe, not an agent adapter.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.runner import EXPERIMENTS, now, runtime_lock, setup_runtime
from scripts.probe_node_cost import fingerprints

TURN = (4797, 5990, 5991)


def build(source, target, role, banned, terminal_self_loop=False, cost_layout='half_both'):
    nodes = [int(x) for x in numeric(source/'cache/rnode.dat')]
    original_count = len(nodes)
    ft = [tuple(map(int,l.split())) for l in read_text(source/'cache/FT.dat').splitlines()]
    costs = [Decimal(l.strip()) for l in read_text(source/'cache/Rwm_car.dat').splitlines()]
    frm, via, to = TURN
    if any(c <= 0 for i,c in enumerate(costs) if via in ft[i]):
        raise ValueError('拆分交叉口的实际路段成本必须为正')
    incoming = {u:i for i,(u,v) in enumerate(ft) if v==via}
    outgoing = {v:i for i,(u,v) in enumerate(ft) if u==via}
    if cost_layout == 'half_outgoing' and role != 'source':
        raise ValueError('当前只在出发端单独比较出口成本拆分')
    fresh = max(nodes)+1
    ports = []
    for neighbors in (incoming, outgoing):
        table = {}
        for neighbor in neighbors:
            table[neighbor] = fresh
            nodes.append(fresh)
            fresh += 1
        ports.append(table)
    ip,op = ports
    edges = []
    for i,(u,v) in enumerate(ft):
        edges.append(dict(u=op[v] if u==via else u, v=ip[u] if v==via else v,
                          physical_index=i, turn=None, terminal=None,
                          cost=str(costs[i]/2 if u==via or (v==via and cost_layout=='half_both') else costs[i])))
    for u,ii in incoming.items():
        for v,oi in outgoing.items():
            if banned and (u,via,v)==TURN:
                continue
            edges.append(dict(u=ip[u],v=op[v],physical_index=None,turn=[u,via,v],terminal=None,
                              cost=str((costs[ii]+costs[oi])/2 if cost_layout=='half_both' else costs[oi]/2)))
    for neighbor,i in (incoming.items() if role=='sink' else outgoing.items()):
        edges.append(dict(u=ip[neighbor] if role=='sink' else via,
                          v=via if role=='sink' else op[neighbor],physical_index=None,turn=None,
                          terminal=dict(role=role, physical_index=i),cost=str(costs[i]/2)))
    if role == 'sink' and terminal_self_loop:
        # A positive self-loop gives the absorbing terminal a nonempty adjacency
        # without creating a path back into the road graph. Its actual flow must
        # be checked independently; this is a compatibility hypothesis.
        edges.append(dict(u=via,v=via,physical_index=None,turn=None,
                          terminal=dict(role='self_loop',physical_index=None),cost='1'))
    internal={node:i+1 for i,node in enumerate(nodes)}
    edges.sort(key=lambda e:(internal[e['u']],internal[e['v']]))
    counts=dict.fromkeys(nodes,0)
    for e in edges:counts[e['u']]+=1
    def write(name,values):
        (target/name).write_bytes(('\r\n'.join(map(str,values))+'\r\n').encode('ascii'))
    numbers=read_text(source/'data/number.txt').splitlines()
    write('data/number.txt',[len(edges),len(nodes),*numbers[2:]])
    write('cache/rnode.dat',nodes)
    write('cache/n_r.dat',[len(nodes),*counts.values()])
    write('cache/tv.dat',[internal[e['v']] for e in edges])
    write('cache/FT.dat',[f"{e['u']} {e['v']}" for e in edges])
    write('cache/Rwm_car.dat',[e['cost'] for e in edges])
    return dict(role=role,banned=banned,turn=list(TURN),original_nodes=original_count,
                original_edges=len(ft),native_nodes=len(nodes),native_edges=len(edges),
                inports=ip,outports=op,edges=edges,terminal_self_loop=terminal_self_loop,cost_layout=cost_layout)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('--terminal-self-loop',action='store_true')
    parser.add_argument('--source-only',action='store_true',help='Keep full OD and model the noncentroid via as an origin terminal only')
    parser.add_argument('--cost-layout',choices=['half_both','half_outgoing'],default='half_both')
    args=parser.parse_args()
    if args.cost_layout=='half_outgoing' and not args.source_only:
        raise ValueError('只拆出口成本的试验必须显式指定--source-only')
    source=sorted((EXPERIMENTS/'jobs/20260908_075938_e7444a/bridge_runs/baseline').glob('*_car_*'))[0]/'scenario'
    source=source.resolve()
    nodes=[int(x) for x in numeric(source/'cache/rnode.dat')]
    via=nodes.index(TURN[1])+1
    if via in [int(v) for v in numeric(source/'cache/szn.dat')[1:]]:
        raise ValueError('当前诊断不拆分指定质心')
    zones=[list(map(int,l.split())) for l in read_text(source/'cache/NodeInZone.dat').splitlines()]
    memberships=[i for i,row in enumerate(zones) if via in row[1:]]
    if len(memberships)!=1:raise ValueError('转向节点必须唯一属于一个小区')
    zone=memberships[0]
    od_lines=(source/'cache/Assign_car.od').read_bytes().splitlines(keepends=True)
    if len(od_lines)!=len(zones)**2 or any(len(l.split())!=1 for l in od_lines):
        raise ValueError('需要按起点行展开、每单元一行的OD矩阵')
    if Decimal(od_lines[zone*len(zones)+zone].decode('ascii').strip())!=0:
        raise ValueError('本次二分诊断仅支持受控小区的区内OD为零')
    job=EXPERIMENTS/'probes'/(datetime.now().strftime('%Y%m%d_%H%M%S')+'_turn_terminals')
    job.mkdir(parents=True,exist_ok=False)
    source_hashes=fingerprints(source)
    save_json(job/'source_files.json',source_hashes)
    state=dict(status='created',started=now(),source=str(source),turn=list(TURN),zone_index=zone,
               terminal_self_loop=args.terminal_self_loop,source_only=args.source_only,cost_layout=args.cost_layout,
               zone_node_count=zones[zone][0],runs=[],
               scope=('冻结单轮小汽车，完整OD原样保留，非质心交叉口设置独立出发接入；' if args.source_only else
                      '冻结单轮小汽车；源小区整行OD与其余行分别求解；原小区成员和OD单元总量不变；')+
                     '不更新最终TSD或正式适配器。')
    save_json(job/'state.json',state)
    print(f'PROBE {job}',flush=True)
    active=None
    with runtime_lock(EXPERIMENTS):
        runtime=setup_runtime(job)
        exe=runtime/'programs/NodeAssignModel_car.exe'
        config=runtime/'files/runinfo_vc6.txt'
        original_config=config.read_bytes()
        state['executable_sha256']=hashlib.sha256(exe.read_bytes()).hexdigest()
        try:
            trials=[('control',None,None,False)]
            for policy,split,banned in [('partition',False,False),('split',True,False),('banned',True,True),('repeat',True,False)]:
                for role in ('source','sink'):
                    trials.append((policy+'_'+role,role,split,banned))
            if args.source_only:
                trials=[('control',None,None,False),('split_full','source',True,False),
                        ('banned_full','source',True,True),('repeat_full','source',True,False)]
            for name,role,split,banned in trials:
                scenario=job/'scenarios'/name
                shutil.copytree(source,scenario)
                (scenario/'cache/volume_car.dat').unlink()
                if role and not args.source_only:
                    zero=b'0.000000\r\n'
                    (scenario/'cache/Assign_car.od').write_bytes(b''.join(
                        line if ((i//len(zones)==zone)==(role=='source')) else zero for i,line in enumerate(od_lines)))
                graph=build(source,scenario,role,banned,args.terminal_self_loop,args.cost_layout) if split else None
                logs=job/'logs'/name
                logs.mkdir(parents=True)
                if graph:save_json(logs/'graph_mapping.json',graph)
                before=fingerprints(scenario)
                save_json(logs/'inputs_before.json',before)
                config.write_bytes(('scenario_dir\r\n'+scenario.as_posix()+'\r\n').encode('gb18030'))
                record=dict(name=name,scenario=str(scenario.relative_to(job)),status='running',started=now(),role=role,split=bool(split),banned=banned)
                state['runs'].append(record)
                state['status']='running'
                stamp=time.time_ns()
                with (logs/'stdout.bin').open('wb') as out,(logs/'stderr.bin').open('wb') as err:
                    active=subprocess.Popen([str(exe)],cwd=runtime/'programs',stdin=subprocess.DEVNULL,
                                            stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
                    record['pid']=active.pid
                    save_json(job/'state.json',state)
                    record['returncode']=active.wait(timeout=60)
                output=scenario/'cache/volume_car.dat'
                if record['returncode'] or not output.exists() or output.stat().st_mtime_ns<stamp or any(
                    b'[error]' in (logs/f'{stream}.bin').read_bytes().lower() for stream in ('stdout','stderr')):
                    raise ValueError('原生程序失败或未生成新输出')
                after=fingerprints(scenario)
                if {p for p in before.keys()|after.keys() if before.get(p)!=after.get(p)}!={'cache/volume_car.dat'}:
                    raise ValueError('原生程序改写输入')
                flow=numeric(output)
                original=numeric(source/'cache/volume_car.dat')
                if len(flow)!=(graph['native_edges'] if graph else len(original)) or min(flow)<0:
                    raise ValueError('原生输出维数或数值错误')
                physical=[None]*len(original)
                movements=[]
                if graph:
                    for e,q in zip(graph['edges'],flow):
                        if e['physical_index'] is not None:physical[e['physical_index']]=q
                        elif e['turn']:movements.append(dict(turn=e['turn'],flow=q))
                else:physical=flow
                assert all(v is not None for v in physical)
                if name=='control' and physical!=original:raise ValueError('冻结控制无法重现')
                save_json(logs/'physical_flows.json',physical)
                save_json(logs/'turn_flows.json',movements)
                save_json(logs/'files_after.json',after)
                record.update(status='completed',finished=now(),native_rows=len(flow),
                              target_transition_flow=next((t['flow'] for t in movements if t['turn']==list(TURN)),None))
                save_json(job/'state.json',state)
                print(json.dumps(record,ensure_ascii=False),flush=True)
            state['status']='completed'
        except BaseException as exc:
            state.update(status='failed',error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            if active is not None and active.poll() is None:
                active.kill();active.wait(timeout=10)
            config.write_bytes(original_config)
            state.update(source_files_unchanged=fingerprints(source)==source_hashes,finished=now())
            save_json(job/'state.json',state)


if __name__=='__main__':main()
