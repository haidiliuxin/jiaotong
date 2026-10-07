"""Explicit single-junction requests with a matched four-policy demand grid."""
import math

from .circulation import TurnGraph, restrictions
from .data import OUTPUTS, geometry, numeric, read_text, save_json, scoped_metrics, scopes
from .oneway import FLOW_FILES
from .turn_access import MODES


def validate_explicit_turn(source, task):
    turn = task.turn
    graph = TurnGraph.load(source)
    if not graph.validate([turn])["eligible"]:
        raise ValueError("指定转向未通过公交和绕行约束，不自动换点")
    row = next(r for r in geometry(source) if [r['from_node'], r['to_node']] == turn[1:])
    if not row['included'] or row['parking'] not in (3, 4) or (task.road_name and row['name'] != task.road_name):
        raise ValueError("指定出口不是可进行停车2级对照的有效路段，或不符合道路约束")
    # The transform also rechecks actual native centroid input each stage.
    nodes = list(map(int, read_text(source/'cache/rnode.dat').split()))
    centroids = list(map(int, read_text(source/'cache/szn.dat').split()))[1:]
    if nodes.index(turn[1]) + 1 in centroids:
        raise ValueError("当前状态图不能拆分质心路口")
    return sorted(t for t in restrictions(source/'data/manageN.txt') if t[1] == turn[1])


def total(record, mode, turn):
    rows = record['turn_access_evidence']['modes'][mode]['turn_totals']
    return next((r['flow'] for r in rows if r['turn'] == list(turn)), 0.0)


def diagnose_turn(agent, baseline, base):
    validate_explicit_turn(agent.source, agent.task)
    turn = agent.task.turn
    row = next(r for r in baseline if [r['from_node'], r['to_node']] == turn[1:])
    flow = total(base, 'car', turn)
    audit = TurnGraph.load(agent.source).validate([turn])
    return dict(selected=[row] if flow > 1 and audit['eligible'] else [], turn=turn,
                attempts=[dict(turn=turn, baseline_car_turn_flow=flow, graph_check=audit,
                               eligible=flow > 1 and audit['eligible'])],
                rule='用户明确指定一项转向；出口是停车3/4级有效道路且满足道路约束；新基准原生小汽车转移量>1，公交与绕行图检查通过',
                scope_note='出口为核心，按任务跳数冻结邻区；显式实验不要求旧自动瓶颈速度<40，也不声称自动选点',
                cause='停车、禁转及组合的收益为待仿真检验假设，可以完全退回基准')


def response(agent, base, record):
    path = agent.job / record['scenario']
    expected = restrictions(path/'data/manageN.txt')
    tokens = list(map(int, read_text(path/'cache/ManageNM.dat').split()))
    cached = {tuple(tokens[i:i+3]) for i in range(1, len(tokens), 3)}
    if len(tokens) != 1 + tokens[0]*3 or len(cached) != tokens[0] or cached != expected:
        raise ValueError('ManageNM未完整反映当前禁转规则')
    checks = []
    for edit in record['changes']:
        if edit.get('kind') != 'turn':
            continue
        turn = edit['turn']
        flows = {m: dict(before=total(base, m, turn), after=total(record, m, turn)) for m in MODES}
        for values in flows.values():
            values['positive_baseline'] = values['before'] > 1
        passed = flows['car']['before'] > 1 and all(f['after'] == 0 for f in flows.values())
        checks.append(dict(turn=turn, flows=flows, before_car_turn_flow=flows['car']['before'],
                           after_car_turn_flow=flows['car']['after'], passed=passed))
    audit = TurnGraph.load(agent.job/base['scenario']).validate([r['turn'] for r in checks])
    return dict(passed=bool(checks) and all(r['passed'] for r in checks) and audit['eligible'],
                cache_rules_exact=True, cached_rule_count=len(cached), movements=checks, graph=audit,
                limit='四车型原生转移边五轮累加；货车等原本为零的车型不算正响应，不使用旧TVolNode表')


def assess_turn(agent, baseline, rows, base, record):
    from .agent import compare
    assessment = compare(baseline, rows, agent.scope, agent.task)
    if assessment['after']['network']['running_time_pcu_h'] >= assessment['before']['network']['running_time_pcu_h'] - 1e-9:
        assessment['violations'].append('全网路段行驶时间未改善，不能只用目标出口流量减少推荐')
    if any(c.get('kind') == 'turn' for c in record['changes']):
        record['turn_response'] = response(agent, base, record)
        if not record['turn_response']['passed']:
            assessment['violations'].append('四车型实际禁转或基准小汽车正流量门槛未通过')
    assessment['eligible'] = not assessment['violations']
    record['comparison'] = assessment
    return assessment


def policies(selected, turn):
    parking = dict(from_node=selected['from_node'], to_node=selected['to_node'], old_parking=selected['parking'], parking=2)
    ban = dict(kind='turn', turn=turn)
    return {'S1_parking': [parking], 'S2_turn': [ban], 'S3_combined': [parking, ban]}


def analyze_turn(agent, baseline, base, diagnosis):
    from .agent import recommend
    selected = diagnosis['selected'][0]
    agent.scope = scopes(baseline, [selected['index']], agent.task.neighbor_hops)
    save_json(agent.job/'scopes.json', agent.scope)
    base.update(metrics=scoped_metrics(baseline, agent.scope), core_rows=[selected])
    variants = policies(selected, diagnosis['turn'])
    design = dict(kind='turn_access', turn=diagnosis['turn'], parking_level=2, policies=variants,
                  demand_factors=agent.task.demand_factors, planned_full_grid=True, full_demand_grid=False,
                  required_runs=4*len(agent.task.demand_factors)+int(agent.task.repeat_baseline),
                  assignment='Vehicle1 multipath incremental 5; theta 3.3', selection='explicit user turn, validated new baseline',
                  turn_access=agent.state['turn_access_profile'])
    agent.state['factorial_design'] = design
    save_json(agent.job/'factorial_protocol.json', design)
    agent.persist()
    choices, grid, main_label, all_response = {}, [], None, True
    for factor in [1.0] + [f for f in agent.task.demand_factors if f != 1]:
        prefix = '' if factor == 1 else 'demand_' + format(factor, '.3f').replace('.', 'p') + '_'
        b_rows, b = (baseline, base) if factor == 1 else agent.simulation(prefix+'baseline', [], factor)
        od_factor = b['execution']['assigned_car_od_sum']/base['execution']['assigned_car_od_sum']
        if not math.isclose(od_factor, factor, rel_tol=1e-5, abs_tol=1e-6):
            raise ValueError('小汽车需求系数未进入实际分配OD')
        candidates = []
        for label, changes in variants.items():
            rows, record = agent.simulation(prefix+label, changes, factor)
            record['policy_label'] = label
            check = assess_turn(agent, b_rows, rows, b, record)
            all_response = all_response and record.get('turn_response', {}).get('passed', True)
            decision = dict(observed_run=record['name'], will_execute=False, violations=check['violations'],
                            reason='候选通过约束，纳入同需求比较' if check['eligible'] else '撤回该候选推荐，保留其他候选或匹配基准',
                            execution_reason='继续测齐预定组合；这是候选筛除，不声称自适应搜索')
            agent.state['decisions'].append(decision)
            agent.event('factorial_assessment', decision)
            candidates.append(record)
            agent.persist()
        winner = recommend(candidates, agent.task.selection_mode)
        chosen = winner['name'] if winner else b['name']
        choices[str(float(factor))] = chosen
        grid.append(dict(factor=factor, baseline=b['name'], policies=[r['name'] for r in candidates], recommendation=chosen))
        if factor == 1:
            agent.state['recommendation'] = chosen
            main_label = winner['policy_label'] if winner else None
        else:
            fixed = next((r for r in candidates if r['policy_label'] == main_label), None)
            comparison = fixed['comparison'] if fixed else dict(eligible=True, violations=[], note='主推荐为基准，无政策稳健性收益声明')
            agent.state['sensitivity'].append(dict(car_demand_factor=factor, matched_baseline=b['name'],
                assigned_od_factor_verified=od_factor, comparison=comparison, conditional_recommendation=chosen,
                fixed_primary_policy=fixed['name'] if fixed else b['name']))
        agent.state.update(demand_grid=grid, recommendation_by_car_demand=choices)
        agent.persist()
    if agent.task.repeat_baseline:
        _, record = agent.simulation('baseline_repeat', [])
        first, repeat = agent.job/base['scenario'], agent.job/record['scenario']
        arrays = {f: numeric(first/'result'/f) == numeric(repeat/'result'/f)
                  for f in list(OUTPUTS.values()) + list(FLOW_FILES.values())}
        exact = all(arrays.values()) and all(base['turn_access_evidence']['modes'][m]['turn_totals'] ==
                                           record['turn_access_evidence']['modes'][m]['turn_totals'] for m in MODES)
        agent.state['repeatability'] = dict(exact_match=exact, arrays=arrays, row_count=len(baseline), turn_totals_exact=exact)
        if not exact:
            raise ValueError('新基准九类道路数组或原生转移量复跑不同')
    design['full_demand_grid'] = len(grid) == len(agent.task.demand_factors)
    agent.state.update(status='completed', turn_adapter_verified=all_response,
        recommendation_rule='在每个匹配需求基准下验证实际禁转、固定核心与邻区约束，并要求全网qL/v严格改善，再选择全网耗时最少候选；无合格项保留基准。显式转向和预定组合不声称自动选点或搜索贡献。',
        robust_across_tested_demand=all(s['comparison']['eligible'] for s in agent.state['sensitivity']) if main_label and agent.state['sensitivity'] else None)
    agent.persist()
