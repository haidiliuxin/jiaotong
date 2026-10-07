"""Independently audit full-OD, source-terminal native turn diagnostics.

Reconstruct graph semantics without importing the transformation builder.
--require-preserved makes demand-balance failures red even when processes finish.
"""
import argparse
import hashlib
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rounding(value):
    return .5*10**(math.floor(math.log10(abs(value)))-5) if value else 0.0


def balances(ft, values):
    result, bounds = {}, {}
    for (u,v),q in zip(ft,values):
        for node,sign in ((u,-1),(v,1)):
            result[node]=result.get(node,0.0)+sign*q
            bounds[node]=bounds.get(node,0.0)+rounding(q)
    return result,bounds


def compare_balance(ft,a,b):
    av,ae=balances(ft,a);bv,be=balances(ft,b)
    failures=[dict(node=n,delta=av[n]-bv[n],rounding_bound=ae[n]+be[n])
              for n in av if abs(av[n]-bv[n])>ae[n]+be[n]+1e-7]
    return dict(passed=not failures,failed_nodes=len(failures),
                max_balance_difference=max(abs(av[n]-bv[n]) for n in av),
                failures=sorted(failures,key=lambda r:-abs(r['delta'])))


def verify(job,require_preserved=False):
    state=load(job/'state.json')
    if state['status']!='completed' or not state.get('source_only'):
        raise ValueError('只核验已完成的完整OD出发端诊断')
    if state['executable_sha256']!='660890a9b72de6a592f77896107147abfb26983186f388eb44e64c24135ac063':
        raise ValueError('不是已验证的原版小汽车求解器')
    expected_names=['control','split_full','banned_full','repeat_full']
    if [r['name'] for r in state['runs']]!=expected_names:
        raise ValueError('缺少控制、拆分、禁转或复跑')
    original=job/'scenarios/control'
    ft=[tuple(map(int,l.split())) for l in read_text(original/'cache/FT.dat').splitlines()]
    nodes=[int(v) for v in numeric(original/'cache/rnode.dat')]
    costs=[Decimal(l.strip()) for l in read_text(original/'cache/Rwm_car.dat').splitlines()]
    source_hashes=load(job/'source_files.json')
    for name,h in source_hashes.items():
        if hashlib.sha256((original/name).read_bytes()).hexdigest()!=h:
            raise ValueError('控制没有原样重放保存的源输入输出')
    via=state['turn'][1]
    if nodes.index(via)+1 in [int(x) for x in numeric(original/'cache/szn.dat')[1:]]:
        raise ValueError('受控节点是质心，当前出发端模型不适用')
    incoming={u:i for i,(u,v) in enumerate(ft) if v==via}
    outgoing={v:i for i,(u,v) in enumerate(ft) if u==via}
    zones=[list(map(int,l.split())) for l in read_text(original/'cache/NodeInZone.dat').splitlines()]
    membership=[i for i,row in enumerate(zones) if nodes.index(via)+1 in row[1:]]
    if len(membership)!=1 or any(row[0]!=len(row)-1 for row in zones):
        raise ValueError('出发节点小区成员不一致')
    zone=membership[0];od=numeric(original/'cache/Assign_car.od')
    if len(od)!=len(zones)**2:raise ValueError('OD矩阵维数不符')
    expected_source_flow=sum(od[zone*len(zones):(zone+1)*len(zones)])/zones[zone][0]
    all_physical,all_raw,evidence={},{},[]
    changed_files={'data/number.txt','cache/rnode.dat','cache/n_r.dat','cache/tv.dat','cache/FT.dat','cache/Rwm_car.dat'}
    for record in state['runs']:
        name=record['name'];scenario=job/record['scenario'];logs=job/'logs'/name
        if record['status']!='completed' or record['returncode']!=0 or not isinstance(record.get('pid'),int):
            raise ValueError('缺少成功原生进程记录')
        before=load(logs/'inputs_before.json');after=load(logs/'files_after.json')
        actual={p.relative_to(scenario).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in scenario.rglob('*') if p.is_file()}
        if actual!=after or set(after)!=set(before)|{'cache/volume_car.dat'} or any(after[k]!=v for k,v in before.items()):
            raise ValueError('实际文件与前后记录不符或原生程序改写输入')
        flow=numeric(scenario/'cache/volume_car.dat')
        if min(flow)<0:raise ValueError('负流量')
        all_raw[name]=flow
        if name=='control':
            if len(flow)!=len(ft):raise ValueError('控制输出维数错误')
            all_physical[name]=flow
            continue
        for path in set(before)-changed_files:
            if (scenario/path).read_bytes()!=(original/path).read_bytes():
                raise ValueError(f'不允许改变OD、小区成员、质心或其他参数：{path}')
        g=load(logs/'graph_mapping.json');edges=g['edges']
        ip={int(k):v for k,v in g['inports'].items()};op={int(k):v for k,v in g['outports'].items()}
        ports=list(ip.values())+list(op.values())
        if set(ip)!=set(incoming) or set(op)!=set(outgoing) or len(set(ports))!=len(ports) or set(ports)&set(nodes):
            raise ValueError('进口/出口状态不完整或编号冲突')
        native_nodes=[int(x) for x in numeric(scenario/'cache/rnode.dat')]
        if native_nodes[:len(nodes)]!=nodes or set(native_nodes[len(nodes):])!=set(ports):
            raise ValueError('旧节点与小区内部编号没有保持')
        expected={}
        layout=g.get('cost_layout','half_both')
        if layout not in ('half_both','half_outgoing'):raise ValueError('未知成本布局')
        for i,(u,v) in enumerate(ft):
            cost=costs[i]/2 if u==via or (v==via and layout=='half_both') else costs[i]
            expected[(op[v] if u==via else u,ip[u] if v==via else v)]=(i,None,None,cost)
        for u,ii in incoming.items():
            for v,oi in outgoing.items():
                if name=='banned_full' and [u,via,v]==state['turn']:continue
                c=(costs[ii]+costs[oi])/2 if layout=='half_both' else costs[oi]/2
                expected[(ip[u],op[v])]=(None,[u,via,v],None,c)
        for v,oi in outgoing.items():
            expected[(via,op[v])]=(None,None,dict(role='source',physical_index=oi),costs[oi]/2)
        nft=[tuple(map(int,l.split())) for l in read_text(scenario/'cache/FT.dat').splitlines()]
        nc=[Decimal(l.strip()) for l in read_text(scenario/'cache/Rwm_car.dat').splitlines()]
        if len(nft)!=len(expected) or len(flow)!=len(nft) or len(edges)!=len(nft) or set(nft)!=set(expected):
            raise ValueError('原生边集合不等于道路边、合法转移和单独出发接入')
        physical=[None]*len(ft);movements=[];terminal_flow=0.0;terminal_bound=0.0
        for pair,cost,e,q in zip(nft,nc,edges,flow):
            pi,turn,terminal,expected_cost=expected[pair]
            if (e['u'],e['v'])!=pair or e['physical_index']!=pi or e['turn']!=turn or e['terminal']!=terminal or cost!=expected_cost:
                raise ValueError('边映射或保持完整路径成本的拆分不符')
            if pi is not None:physical[pi]=q
            elif turn:movements.append(dict(turn=turn,flow=q))
            else:terminal_flow+=q;terminal_bound+=rounding(q)
        counts=[int(x) for x in numeric(scenario/'cache/n_r.dat')]
        tv=[int(x) for x in numeric(scenario/'cache/tv.dat')]
        if counts[0]!=len(native_nodes) or len(counts)!=len(native_nodes)+1 or any(v<0 for v in counts[1:]):
            raise ValueError('出度维数错误')
        origins=[n for n,count in zip(native_nodes,counts[1:]) for _ in range(count)]
        if any(v<1 or v>len(native_nodes) for v in tv) or list(zip(origins,[native_nodes[v-1] for v in tv]))!=nft:
            raise ValueError('实际邻接不能重建边集合')
        number=read_text(scenario/'data/number.txt').splitlines()
        if list(map(int,number[:2]))!=[len(nft),len(native_nodes)] or number[2:]!=read_text(original/'data/number.txt').splitlines()[2:]:
            raise ValueError('number维数或其他字段错误')
        if physical!=load(logs/'physical_flows.json') or movements!=load(logs/'turn_flows.json'):
            raise ValueError('保存的道路/转向量不能由原生输出重建')
        bal,bound=balances(nft,flow)
        synthetic_fail=[n for n in ports if abs(bal[n])>bound[n]+1e-7]
        target=next((t['flow'] for t in movements if t['turn']==state['turn']),None)
        if record['target_transition_flow']!=target:raise ValueError('目标转向摘要不符')
        evidence.append(dict(name=name,target_flow=target,native_rows=len(flow),
                             synthetic_balance_passed=not synthetic_fail,synthetic_failed_nodes=synthetic_fail,
                             max_synthetic_balance=max(abs(bal[n]) for n in ports),
                             source_terminal_flow=terminal_flow,source_terminal_rounding_bound=terminal_bound,
                             source_terminal_expected=expected_source_flow,
                             source_terminal_passed=abs(terminal_flow-expected_source_flow)<=terminal_bound+1e-7))
        all_physical[name]=physical
    exact=all_raw['split_full']==all_raw['repeat_full']
    if not exact:raise ValueError('拆分基准原生全数组复跑不同')
    response=evidence[0]['target_flow'] is not None and evidence[0]['target_flow']>1 and evidence[1]['target_flow'] is None
    baseline_balance=compare_balance(ft,all_physical['split_full'],all_physical['control'])
    policy_balance=compare_balance(ft,all_physical['banned_full'],all_physical['split_full'])
    report=dict(execution_and_transform_verified=True,native_calls=len(state['runs']),repeat_raw_arrays_exact=exact,
                turn_response_passed=response,verified_runs=evidence,
                baseline_node_balance=baseline_balance,policy_node_balance=policy_balance,
                baseline_changed_edges=sum(a!=b for a,b in zip(all_physical['split_full'],all_physical['control'])),
                baseline_max_flow_difference=max(abs(a-b) for a,b in zip(all_physical['split_full'],all_physical['control'])),
                policy_changed_edges=sum(a!=b for a,b in zip(all_physical['split_full'],all_physical['banned_full'])),
                policy_max_flow_difference=max(abs(a-b) for a,b in zip(all_physical['split_full'],all_physical['banned_full'])),
                limitations='只验证冻结单轮小汽车的新增禁转；并非全部车型、全部OD路径、全部既有禁转或完整迭代验收。六位有效数字用于输出舍入界限，不放宽禁转门槛。')
    report['preservation_passed']=response and exact and baseline_balance['passed'] and policy_balance['passed'] and all(e['synthetic_balance_passed'] and e['source_terminal_passed'] for e in evidence)
    save_json(job/'terminal_verification.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('baseline_node_balance','policy_node_balance','verified_runs')},ensure_ascii=False,indent=2))
    if require_preserved and not report['preservation_passed']:
        raise ValueError('禁转响应或原节点/新增状态节点流量保持检查失败')
    return report


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('job',type=Path)
    parser.add_argument('--require-preserved',action='store_true')
    args=parser.parse_args();verify(args.job.resolve(),args.require_preserved)
