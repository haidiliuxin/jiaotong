"""Private turn runtime and independent checks of graph, OD and native volumes."""
import json
import math
import sys
from decimal import Decimal
from pathlib import Path, PureWindowsPath

from .circulation import restrictions
from .data import PROJECT, geometry, numeric, read_text, save_json
from .hard_access import digest, install_runtime
from .oneway import FLOW_FILES

MODES = ('car', 'taxi', 'motor', 'truck')


def install(job, via, base_turns):
    runtime, metadata = install_runtime(job)
    profile = dict(version=2, **metadata, via=via, base_turns=sorted(map(list, base_turns)), no_uturn=True,
                   original_reference_each_stage=True,
                   bridge_source_sha256=digest((PROJECT/'traffic_agent/native_turn_bridge.py').read_bytes()),
                   graph_source_sha256=digest((PROJECT/'traffic_agent/turn_graph.py').read_bytes()),
                   limits='只在指定非质心交叉口执行原有及新增禁转和禁止掉头；其他交叉口仍沿用原配置；不是全网既有规则验收。')
    save_json(job/'turn_access_profile.json', profile)
    return runtime, profile


def configure(job, runtime, name, profile, added_turns):
    config = dict(runtime=str(runtime), native_programs=str(runtime/'native_programs'),
                  archive=str(job/'bridge_runs'/name), via=profile['via'],
                  base_turns=profile['base_turns'], added_turns=sorted(map(list, added_turns)))
    path = job/'bridge_configurations'/f'{name}.json'
    save_json(path, config)
    return dict(TRAFFIC_AGENT_PYTHON=sys.executable,
                TRAFFIC_AGENT_BRIDGE_SCRIPT=str(PROJECT/'traffic_agent/native_turn_bridge.py'),
                TRAFFIC_AGENT_BRIDGE_CONFIG=str(path))


def rounding(q):
    return .5*10**(math.floor(math.log10(q))-5) if q else 0.0


def verify_transform(folder):
    def load(name):
        return json.loads((folder/name).read_text(encoding='utf-8'))
    m = load('manifest.json')
    if m['schema_version'] != 2 or m['mode'] not in MODES or not m['no_uturn']:
        raise ValueError('不支持的转向转换版本或车型')
    mode, via = m['mode'], m['via']
    raw = {name: (folder/'original_inputs'/name).read_bytes() for name in m['input_hashes']}
    if any(digest(data) != m['input_hashes'][name] for name, data in raw.items()):
        raise ValueError('原始输入字节与本轮摘要不同')
    scratch = folder/'scenario'
    changed = {'data/number.txt', 'cache/rnode.dat', 'cache/n_r.dat', 'cache/tv.dat', 'cache/FT.dat', f'cache/Rwm_{mode}.dat'}
    native = {name: (scratch/name).read_bytes() for name in raw}
    if any(native[name] != data for name, data in raw.items() if name not in changed):
        raise ValueError('OD、小区、质心或其他参数被改变')
    if load('native_inputs.json') != {name: digest(data) for name, data in native.items()}:
        raise ValueError('原生输入发生变化')
    nodes = list(map(int, raw['cache/rnode.dat'].split()))
    ft = [tuple(map(int, line.split())) for line in raw['cache/FT.dat'].splitlines()]
    n = list(map(int, raw['cache/n_r.dat'].split()))
    tv = list(map(int, raw['cache/tv.dat'].split()))
    if (n[0] != len(nodes) or len(n) != len(nodes)+1 or len(set(ft)) != len(ft)
            or any(v < 1 or v > len(nodes) for v in tv)
            or list(zip([u for u,c in zip(nodes,n[1:]) for _ in range(c)], [nodes[v-1] for v in tv])) != ft):
        raise ValueError('原始内部邻接不匹配FT')
    if nodes.index(via)+1 in list(map(int, raw['cache/szn.dat'].split()))[1:]:
        raise ValueError('不能将质心当作仅出发端')
    costs = [Decimal(line.decode().strip()) for line in raw[f'cache/Rwm_{mode}.dat'].splitlines()]
    if len(costs) != len(ft):
        raise ValueError('原阻抗维数错误')
    g = load('graph_mapping.json')
    ip = {int(k): v for k, v in g['inports'].items()}
    op = {int(k): v for k, v in g['outports'].items()}
    incoming = {u: i for i,(u,v) in enumerate(ft) if v == via}
    outgoing = {v: i for i,(u,v) in enumerate(ft) if u == via}
    ports = list(ip.values())+list(op.values())
    nn = list(map(int, native['cache/rnode.dat'].split()))
    if (set(ip) != set(incoming) or set(op) != set(outgoing) or len(set(ports)) != len(ports)
            or set(ports) & set(nodes) or nn[:len(nodes)] != nodes or set(nn[len(nodes):]) != set(ports)):
        raise ValueError('状态节点映射或原节点内部编号错误')
    bans = set(map(tuple, m['base_turns']+m['added_turns'])) | {(u,via,u) for u in incoming if u in outgoing}
    if any(v != via or u not in incoming or w not in outgoing for u,v,w in bans):
        raise ValueError('禁转不属于当前真实交叉口')
    if g['banned_turns'] != sorted(map(list,bans)):
        raise ValueError('规则映射不一致')
    expected = {}
    for i,(u,v) in enumerate(ft):
        expected[(op[v] if u == via else u, ip[u] if v == via else v)] = (i, None, None, costs[i]/2 if u == via else costs[i])
    for u in incoming:
        for v, i in outgoing.items():
            if (u,via,v) not in bans:
                expected[(ip[u],op[v])] = (None, [u,via,v], None, costs[i]/2)
    for v,i in outgoing.items():
        expected[(via,op[v])] = (None, None, v, costs[i]/2)
    nft = [tuple(map(int,line.split())) for line in native['cache/FT.dat'].splitlines()]
    nc = [Decimal(line.decode().strip()) for line in native[f'cache/Rwm_{mode}.dat'].splitlines()]
    edges = g['edges']
    if len(nft) != len(expected) or set(nft) != set(expected) or len(nc) != len(nft) or len(edges) != len(nft):
        raise ValueError('实际求解图不是预期的合法转移集合')
    counts = list(map(int, native['cache/n_r.dat'].split()))
    ntv = list(map(int, native['cache/tv.dat'].split()))
    if (counts[0] != len(nn) or len(counts) != len(nn)+1 or min(counts[1:]) < 0
            or any(v < 1 or v > len(nn) for v in ntv)
            or list(zip([u for u,c in zip(nn,counts[1:]) for _ in range(c)], [nn[v-1] for v in ntv])) != nft):
        raise ValueError('派生出度和邻接不匹配FT')
    num = native['data/number.txt'].splitlines()
    if list(map(int,num[:2])) != [len(nft),len(nn)] or num[2:] != raw['data/number.txt'].splitlines()[2:]:
        raise ValueError('派生图规模字段错误')
    output = scratch/f'cache/volume_{mode}.dat'
    if digest(output.read_bytes()) != m['native_output_sha256']:
        raise ValueError('原生输出摘要错误')
    values = numeric(output)
    lines = output.read_bytes().splitlines(keepends=True)
    if len(values) != len(nft) or len(lines) != len(values) or min(values) < 0:
        raise ValueError('原生输出维数或数值错误')
    physical = [None]*len(ft)
    movements = []
    balance = dict.fromkeys(ports, 0.0)
    bounds = dict.fromkeys(ports, 0.0)
    production = error_bound = 0.0
    for pair,cost,e,q,line in zip(nft,nc,edges,values,lines):
        pi,turn,source_exit,expected_cost = expected[pair]
        if ((e['u'],e['v']) != pair or e['physical_index'] != pi or e['turn'] != turn
                or e['source_exit'] != source_exit or cost != expected_cost):
            raise ValueError('实际道路/转移/出发映射或路径成本错误')
        if pi is not None:
            physical[pi] = line
        elif turn:
            movements.append(dict(turn=turn, flow=q))
        else:
            production += q
            error_bound += rounding(q)
        for node,sign in ((pair[0],-1),(pair[1],1)):
            if node in balance:
                balance[node] += sign*q
                bounds[node] += rounding(q)
    if any(line is None for line in physical):
        raise ValueError('物理道路不完整')
    expanded = folder/'expanded_volume.dat'
    if b''.join(physical) != expanded.read_bytes() or digest(expanded.read_bytes()) != m['expanded_output_sha256']:
        raise ValueError('返回序列没有保持原生道路输出字节')
    if movements != load('turn_flows.json'):
        raise ValueError('转移流量记录不符')
    failures = [node for node in ports if abs(balance[node]) > bounds[node]+1e-7]
    if failures:
        raise ValueError(f'新增状态节点的流量不守恒：{failures}')
    zones = [list(map(int,line.split())) for line in raw['cache/NodeInZone.dat'].splitlines()]
    membership = [i for i,row in enumerate(zones) if nodes.index(via)+1 in row[1:]]
    if len(membership) != 1 or any(row[0] != len(row)-1 for row in zones):
        raise ValueError('需求接入节点的小区成员不一致')
    zone = membership[0]
    od = numeric(scratch/f'cache/Assign_{mode}.od')
    if len(od) != len(zones)**2:
        raise ValueError('本轮OD维数错误')
    expected_production = sum(od[zone*len(zones):(zone+1)*len(zones)])/zones[zone][0]
    reference = folder/'reference_scenario'
    if m['reference_returncode'] or any((reference/name).read_bytes() != data for name,data in raw.items()):
        raise ValueError('同输入原生参考失败或原始参数被改写')
    reference_output = reference/f'cache/volume_{mode}.dat'
    if digest(reference_output.read_bytes()) != m['reference_output_sha256']:
        raise ValueError('原生参考输出摘要不同')
    reference_flow = numeric(reference_output)
    full_flow = numeric(expanded)
    if len(reference_flow) != len(ft) or min(reference_flow) < 0:
        raise ValueError('原生参考维数或数值错误')
    def node_balances(vector):
        net, precision = dict.fromkeys(nodes,0.0), dict.fromkeys(nodes,0.0)
        for (u,v),q in zip(ft,vector):
            net[u] -= q; net[v] += q
            precision[u] += rounding(q); precision[v] += rounding(q)
        return net,precision
    reference_net,reference_error = node_balances(reference_flow)
    adapted_net,adapted_error = node_balances(full_flow)
    lost = [node for node in nodes if abs(adapted_net[node]-reference_net[node]) > adapted_error[node]+reference_error[node]+1e-7]
    if lost:
        raise ValueError(f'{mode}原节点相对同输入参考的净流量变化超出舍入界限：{lost[:12]}')
    if abs(production+reference_net[via]) > error_bound+reference_error[via]+1e-7:
        raise ValueError('出发端未保持本轮原生参考的实际接入量')
    evidence = dict(mode=mode, native_rows=len(values), original_rows=len(ft),
                    max_state_balance=max(map(abs,balance.values())),
                    source_flow=production, reference_source_net=-reference_net[via],
                    uniform_share_hypothesis=expected_production,
                    source_rounding_bound=error_bound, input_transform_verified=True,
                    original_node_balances_preserved=True,
                    max_original_balance_difference=max(abs(adapted_net[n]-reference_net[n]) for n in nodes))
    return m, ft, numeric(expanded), od, movements, evidence


def verify_run(job, name, scenario, profile, added_turns, recorded_scenario=None):
    files = sorted((job/'bridge_runs'/name).glob('*/manifest.json'))
    if len(files) != 20:
        raise ValueError('需要四车型各五轮原生求解')
    added = sorted(map(list, added_turns))
    if {t for t in restrictions(scenario/'data/ManageN.txt') if t[1] == profile['via']} != set(map(tuple, profile['base_turns']+added)):
        raise ValueError('场景策略不同于固定协议')
    grouped = {mode: [] for mode in MODES}
    outer_ft = [tuple(map(int,l.split())) for l in read_text(scenario/'cache/FT.dat').splitlines()]
    for path in files:
        m,ft,physical,od,turns,evidence = verify_transform(path.parent)
        mode = m['mode']
        if (m['status'] != 'completed' or m['returncode'] or m['base_turns'] != profile['base_turns']
                or m['added_turns'] != added or m['via'] != profile['via'] or ft != outer_ft
                or (PureWindowsPath(m['outer_scenario']) != PureWindowsPath(recorded_scenario) if recorded_scenario
                    else Path(m['outer_scenario']).resolve() != scenario.resolve())
                or m['native_executable_sha256'] != profile['native_executable_hashes'][mode]
                or m['bridge_source_sha256'] != profile['bridge_source_sha256']
                or m['graph_source_sha256'] != profile['graph_source_sha256']):
            raise ValueError('原生调用版本、规则或原始FT不同')
        grouped[mode].append((m['started'],physical,od,turns,evidence))
    geom = geometry(scenario)
    ft_index = {pair:i for i,pair in enumerate(outer_ft)}
    summaries = {}
    for mode,samples in grouped.items():
        samples.sort()
        if len(samples) != 5:
            raise ValueError('车型调用不是五輪')
        od_file = {'car':'ODCar.tsd','taxi':'ODTaxi.tsd','motor':'ODMotor.tsd','truck':'ODTruck.tsd'}[mode]
        demand = numeric(scenario/'result'/od_file)
        if any(sample[2] != [float(f'{v*w/15:.6f}') for v in demand] for sample,w in zip(samples,(5,4,3,2,1))):
            raise ValueError('分轮OD不是预定六位小数增量')
        od_error = max(abs(sum(s[2][i] for s in samples)-v) for i,v in enumerate(demand))
        if od_error > 2.50001e-6:
            raise ValueError('五轮OD未在序列化精度内还原')
        final = numeric(scenario/'result'/FLOW_FILES[mode])
        total = [sum(s[1][i] for s in samples) for i in range(len(outer_ft))]
        volume_error = max(abs(total[ft_index[(r['from_node'],r['to_node'])]]-final[r['index']]) for r in geom)
        if volume_error > .00501:
            raise ValueError('最终车型流量不能由原生五轮物理道路输出重建')
        turns = {}
        for sample in samples:
            for row in sample[3]:
                key = tuple(row['turn'])
                turns[key] = turns.get(key,0)+row['flow']
        summaries[mode] = dict(calls=5, max_od_cell_sum_difference=od_error,
                               max_final_volume_difference=volume_error,
                               stages=[s[4] for s in samples],
                               turn_totals=[dict(turn=list(t),flow=q) for t,q in sorted(turns.items())])
    return dict(passed=True,native_calls=40,adapted_calls=20,reference_calls=20,via=profile['via'],base_turns=profile['base_turns'],
                added_turns=added,no_uturn=True,modes=summaries,
                limits='转移量由原生状态边逐轮累加，仅覆盖被拆分路口；不冒充厂商全网转向表。')
