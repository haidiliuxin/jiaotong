"""Frozen single-round diagnostic of explicit turn transitions in the vendor solver.

This does not replace the agent's assignment adapter or synthesize final TSDs.
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


def split_graph(source, target, turn, forbid=False, no_uturn=False, cost_layout="zero"):
    nodes = [int(x) for x in numeric(source / "cache/rnode.dat")]
    ft = [tuple(map(int, line.split())) for line in read_text(source / "cache/FT.dat").splitlines() if line.strip()]
    counts = [int(x) for x in numeric(source / "cache/n_r.dat")]
    tv = [int(x) for x in numeric(source / "cache/tv.dat")]
    origins = [n for n, count in zip(nodes, counts[1:]) for _ in range(count)]
    if counts[0] != len(nodes) or list(zip(origins, [nodes[v-1] for v in tv])) != ft:
        raise ValueError("原始节点/出度/邻接不能重建FT")
    frm, via, to = turn
    if (frm, via) not in ft or (via, to) not in ft or len(set(turn)) != 3:
        raise ValueError("试验禁转不是合法三节点转向")
    centroids = {nodes[int(v)-1] for v in numeric(source / "cache/szn.dat")[1:]}
    if via in centroids:
        raise ValueError("当前诊断不拆分OD质心")
    incoming = [u for u, v in ft if v == via]
    outgoing = [v for u, v in ft if u == via]
    if via in incoming or via in outgoing:
        raise ValueError("当前诊断不支持自环")
    # Reuse the old noncentroid node for the first incoming state. All other
    # internal indices (including every OD centroid) retain their original value.
    fresh = max(nodes) + 1
    inports = {incoming[0]: via}
    for u in incoming[1:]:
        inports[u] = fresh
        nodes.append(fresh)
        fresh += 1
    outports = {}
    for v in outgoing:
        outports[v] = fresh
        nodes.append(fresh)
        fresh += 1
    costs = (source / "cache/Rwm_car.dat").read_bytes().splitlines(keepends=True)
    if len(costs) != len(ft):
        raise ValueError("阻抗维数不符")
    if cost_layout not in ("zero", "half_outgoing"):
        raise ValueError("未知拆分阻抗布局")
    outgoing_indices = {v: i for i, (u, v) in enumerate(ft) if u == via}
    halves = {v: str(Decimal(costs[i].decode('ascii').strip()) / 2).encode('ascii') + b'\r\n'
              for v, i in outgoing_indices.items()}
    edges = []
    for i, (u, v) in enumerate(ft):
        edges.append(dict(u=outports[v] if u == via else u,
                          v=inports[u] if v == via else v, physical_index=i, turn=None))
    for u in incoming:
        for v in outgoing:
            if (no_uturn and u == v) or (forbid and (u, via, v) == tuple(turn)):
                continue
            edges.append(dict(u=inports[u], v=outports[v], physical_index=None, turn=[u, via, v]))
    internal = {node: i+1 for i, node in enumerate(nodes)}
    edges.sort(key=lambda e: (internal[e['u']], internal[e['v']]))
    out_counts = {node: 0 for node in nodes}
    for edge in edges:
        out_counts[edge['u']] += 1
    if any(count == 0 for count in out_counts.values()):
        raise ValueError("拆分产生零出度节点，需另行验证原生兼容性")
    def write(name, lines):
        (target / name).write_bytes(('\r\n'.join(map(str, lines))+'\r\n').encode('ascii'))
    numbers = read_text(source / "data/number.txt").splitlines()
    write("data/number.txt", [len(edges), len(nodes), *numbers[2:]])
    write("cache/rnode.dat", nodes)
    write("cache/n_r.dat", [len(nodes), *out_counts.values()])
    write("cache/tv.dat", [internal[e['v']] for e in edges])
    write("cache/FT.dat", [f"{e['u']} {e['v']}" for e in edges])
    native_costs = []
    for e in edges:
        i = e['physical_index']
        if i is None:
            native_costs.append(halves[e['turn'][2]] if cost_layout == 'half_outgoing' else b'0\r\n')
        elif cost_layout == 'half_outgoing' and ft[i][0] == via:
            native_costs.append(halves[ft[i][1]])
        else:
            native_costs.append(costs[i])
    (target / "cache/Rwm_car.dat").write_bytes(b''.join(native_costs))
    return dict(original_nodes=len(nodes)-len(incoming)-len(outgoing)+1, native_nodes=len(nodes),
                original_edges=len(ft), native_edges=len(edges), inports=inports, outports=outports,
                forbidden_turn=list(turn) if forbid else None, no_uturn=no_uturn, edges=edges,
                original_centroid_indices_unchanged=True, cost_layout=cost_layout)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    baseline = EXPERIMENTS / "jobs/20260908_075938_e7444a/bridge_runs/baseline"
    parser.add_argument("--source", type=Path)
    parser.add_argument("--turn", type=int, nargs=3, default=[4797, 5990, 5991])
    parser.add_argument("--cost-layout", choices=['zero', 'half_outgoing'], default='zero')
    parser.add_argument("--zone-control", action='store_true', help='Only test whether NodeInZone uses the via-node membership')
    args = parser.parse_args()
    source = (args.source or sorted(baseline.glob('*_car_*'))[0] / 'scenario').resolve()
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_native_turn")
    job.mkdir(parents=True, exist_ok=False)
    state = dict(status="created", started=now(), source=str(source), turn=args.turn, runs=[], cost_layout=args.cost_layout,
                 zone_control=args.zone_control,
                 scope="冻结单轮小汽车原生构图诊断；不代表全部车型/完整迭代/既有禁转均已适配；NodeInZone保持原样，其实际使用仍需核验。")
    source_hashes = fingerprints(source)
    save_json(job / "source_files.json", source_hashes)
    save_json(job / "state.json", state)
    print(f"PROBE {job}", flush=True)
    active = None
    with runtime_lock(EXPERIMENTS):
        runtime = setup_runtime(job)
        exe = runtime / "programs/NodeAssignModel_car.exe"
        config = runtime / "files/runinfo_vc6.txt"
        original_config = config.read_bytes()
        state['executable_sha256'] = hashlib.sha256(exe.read_bytes()).hexdigest()
        try:
            trials = [('control', False, False, False),
                    ('split_open', True, False, False), ('split_no_uturn', True, True, False),
                    ('split_banned', True, True, True), ('split_repeat', True, True, False)]
            if args.zone_control:
                trials = [(name, False, False, False) for name in ['control', 'without_via_membership', 'control_repeat']]
            for name, split, no_uturn, forbid in trials:
                scenario = job / 'scenarios' / name
                shutil.copytree(source, scenario)
                (scenario / 'cache/volume_car.dat').unlink()
                if name == 'without_via_membership':
                    nodes = [int(x) for x in numeric(source/'cache/rnode.dat')]
                    via_index = nodes.index(args.turn[1]) + 1
                    lines = read_text(source/'cache/NodeInZone.dat').splitlines()
                    replaced = 0
                    for i, line in enumerate(lines):
                        row = list(map(int, line.split()))
                        if row[0] != len(row)-1:
                            raise ValueError('NodeInZone数量错误')
                        if via_index in row[1:]:
                            members = [n for n in row[1:] if n != via_index]
                            lines[i] = ' '.join(map(str, [len(members), *members]))
                            replaced += 1
                    if replaced != 1:
                        raise ValueError('待测节点必须唯一属于一个小区')
                    (scenario/'cache/NodeInZone.dat').write_bytes(('\r\n'.join(lines)+'\r\n').encode('ascii'))
                graph = split_graph(source, scenario, args.turn, forbid, no_uturn, args.cost_layout) if split else None
                logs = job / 'logs' / name
                logs.mkdir(parents=True)
                if graph:
                    save_json(logs / 'graph_mapping.json', graph)
                before = fingerprints(scenario)
                save_json(logs / 'inputs_before.json', before)
                record = dict(name=name, scenario=str(scenario.relative_to(job)), status='running', started=now())
                state['status'] = 'running'
                state['runs'].append(record)
                config.write_bytes(('scenario_dir\r\n'+scenario.as_posix()+'\r\n').encode('gb18030'))
                stamp = time.time_ns()
                with (logs/'stdout.bin').open('wb') as out, (logs/'stderr.bin').open('wb') as err:
                    active = subprocess.Popen([str(exe)], cwd=runtime/'programs', stdin=subprocess.DEVNULL,
                                              stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
                    record['pid'] = active.pid
                    save_json(job/'state.json', state)
                    record['returncode'] = active.wait(timeout=60)
                output = scenario/'cache/volume_car.dat'
                if record['returncode'] or not output.exists() or output.stat().st_mtime_ns < stamp or any(
                        b'[error]' in (logs/f'{stream}.bin').read_bytes().lower() for stream in ('stdout','stderr')):
                    raise ValueError('原生程序失败或未生成新输出')
                after = fingerprints(scenario)
                if {p for p in before.keys() | after.keys() if before.get(p)!=after.get(p)} != {'cache/volume_car.dat'}:
                    raise ValueError('原生程序改写了输入')
                flow = numeric(output)
                original = numeric(source/'cache/volume_car.dat')
                expected_rows = graph['native_edges'] if graph else len(original)
                if len(flow) != expected_rows or min(flow) < 0:
                    raise ValueError('原生输出维数或数值无效')
                physical = [None]*len(original)
                turns = []
                if graph:
                    for e, value in zip(graph['edges'], flow):
                        if e['physical_index'] is not None:
                            physical[e['physical_index']] = value
                        else:
                            turns.append({'turn': e['turn'], 'flow': value})
                else:
                    physical = flow
                assert all(v is not None for v in physical)
                if not split and name != 'without_via_membership' and physical != original:
                    raise ValueError('无拆分控制未精确复现冻结输出')
                save_json(logs/'physical_flows.json', physical)
                save_json(logs/'turn_flows.json', turns)
                target = next((t['flow'] for t in turns if t['turn']==args.turn), None)
                record.update(status='completed', finished=now(), native_rows=len(flow), target_transition_flow=target,
                              physical_changed_vs_original=sum(a!=b for a,b in zip(physical,original)),
                              max_physical_difference=max(abs(a-b) for a,b in zip(physical,original)))
                save_json(logs/'files_after.json', after)
                save_json(job/'state.json', state)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            state['status'] = 'completed'
        except BaseException as exc:
            state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            raise
        finally:
            if active is not None and active.poll() is None:
                active.kill()
                active.wait(timeout=10)
            config.write_bytes(original_config)
            state.update(source_files_unchanged=fingerprints(source)==source_hashes, finished=now())
            save_json(job/'state.json', state)


if __name__ == '__main__':
    main()
