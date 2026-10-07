"""Choose a native one-way experiment target from fresh, validated observations.

The model chooses a location, not graph permissions or a claimed benefit. Its
choice is frozen before any of the matched parking/closure/combined policies.
"""
import copy

from .data import save_json, scoped_metrics, scopes
from .direction_claims import VERSION as DIRECTION_VERSION
from .action_execution import PARKING_LEVEL
from .feedback_planner import digest
from .runner import now

VERSION = 'oneway-target-v1'
TARGET_LIMIT = 4


def menu(diagnosis):
    options = {}
    for target in diagnosis.get('targets', []):
        row = target['selected'][0]
        graph = target['graph']
        detours = {mode: dict(boundaries=len(checks),
                             max_extra_km=max((c['replacement']['extra_km'] for c in checks), default=0),
                             max_ratio=max((c['replacement']['ratio'] for c in checks), default=1))
                   for mode, checks in graph['modal_boundary_checks'].items()}
        options[target['id']] = dict(title=f"{row['name']} {row['from_node']}→{row['to_node']} · 单向与停车对照",
            closed_direction=[row['from_node'], row['to_node']], baseline_car_flow=target['car_flow'],
            roads=[{k: r[k] for k in ('index', 'name', 'from_node', 'to_node', 'speed_kmh', 'vc', 'flow_pcu_h', 'parking', 'length_km')}
                   for r in target['selected']],
            topology=dict(eligible=graph['eligible'], bus_routes_checked=graph['bus_routes_checked'],
                          modal_detours=detours),
            experiment=f'仅在所选位置分别计算停车{PARKING_LEVEL}级、单向、两者组合；尚无任何治理收益。')
    return options


def observations(state):
    """Network-only baseline view, unchanged when the chosen core is frozen."""
    return [dict(ref=f"{state['id']}:{r['name']}", run=r['name'], demand_factor=r['demand_factor'], changes=[],
                 metrics=dict(network={k: r['metrics']['network'][k] for k in
                    ('directed_links', 'weighted_speed_kmh', 'running_time_pcu_h', 'oversaturated_km')}))
            for r in state['runs'] if r['name'] == 'baseline' and r.get('metrics')
            and r.get('execution', {}).get('status') == 'completed']


def selected_diagnosis(diagnosis, action):
    result = copy.deepcopy(diagnosis)
    target = next((x for x in result.get('targets', []) if x['id'] == action), None)
    if target is None:
        raise ValueError('模型单向目标不在新基准合法目录内')
    result.update(selected=target['selected'], selected_target=target['id'])
    return result


def choose(agent, baseline, base, diagnosis):
    planner = agent.planner
    # Until a choice succeeds, only the full-network baseline is scoped.
    agent.scope = scopes(baseline, [], agent.task.neighbor_hops)
    save_json(agent.job / 'scopes.json', agent.scope)
    base.update(metrics=scoped_metrics(baseline, agent.scope), core_rows=[])
    catalog = dict(version=VERSION, created=now(), diagnosis_digest=digest(diagnosis), options=menu(diagnosis))
    agent.state.update(target_planning_protocol=VERSION, oneway_target_catalogue=catalog)
    save_json(agent.job / 'oneway_target_catalogue.json', catalog)
    required_runs = 4 * len(agent.task.demand_factors) + int(agent.task.repeat_baseline)
    allowed = catalog['options'] if (planner.limit-planner.used-planner.reserved_requests >= 2
                                    and agent.task.max_runs >= required_runs) else {}
    context = dict(target_catalogue_digest=digest(catalog), reserved_model_requests=planner.reserved_requests,
                   required_native_runs=required_runs,
                   direction_semantics_protocol=DIRECTION_VERSION,
                   scope='候选仅来自本次新基准，未读取治理结果。模型选择一个方向后冻结双向核心及邻区，测齐本位置全部单项、组合和约定需求；未选位置不宣称已验证。',
                   **planner.programme_context(agent.job_id))
    from .action_execution import attach
    attach(agent,allowed,context,False)
    entry = planner.decide('oneway_target', agent.job_id, observations(agent.state), allowed, context, allow_stop=False)
    agent.state.setdefault('planner_trace', []).append(entry)
    agent.state['planning_budget'] = planner.budget()
    agent.event('model_decision', {k: v for k, v in entry.items() if k != 'input'})
    if entry['status'] != 'accepted':
        agent.state.update(status='incomplete', recommendation='baseline',
                           reason='单向目标尚未取得有效模型选择；未执行治理，也未回退为规则选点。')
        if agent.state.get('evidence_protocol'):
            from .research_evidence import latest_review
            agent.state['research_review']=latest_review(agent.state)
        agent.persist()
        return diagnosis
    chosen = selected_diagnosis(diagnosis, entry['action'])
    agent.persist()
    return chosen


def verify_choice(state, diagnosis, trace):
    """Consistency checks; the native verifier separately rebuilds candidates."""
    if state.get('target_planning_protocol') != VERSION:
        raise ValueError('单向模型选点协议不符')
    raw = {k: copy.deepcopy(v) for k, v in diagnosis.items() if k != 'selected_target'}
    raw['selected'] = []
    catalog = state.get('oneway_target_catalogue', {})
    if (catalog.get('version') != VERSION or catalog.get('diagnosis_digest') != digest(raw)
            or catalog.get('options') != menu(raw)):
        raise ValueError('单向候选目录或新基准诊断不符')
    payload = trace['input']; context = payload['context']
    reserved = context.get('reserved_model_requests')
    if type(reserved) != int or reserved < 0:
        raise ValueError('单向目标选择的模型预留无效')
    required = 4 * len(state['task']['demand_factors']) + int(state['task']['repeat_baseline'])
    options = menu(raw) if payload['model_budget']['remaining']-reserved >= 2 and state['task']['max_runs'] >= required else {}
    if (context.get('target_catalogue_digest') != digest(catalog) or context.get('required_native_runs') != required
            or payload['allowed_actions'] != options):
        raise ValueError('单向动作目录越界、允许跳过或没有预留完整对照及复核')
    from datetime import datetime
    if not datetime.fromisoformat(catalog['created']) <= datetime.fromisoformat(trace['time']):
        raise ValueError('单向候选目录晚于模型选择')
    if trace['status'] == 'accepted':
        if selected_diagnosis(raw, trace['action']) != diagnosis:
            raise ValueError('实际单向选点不是模型选择')
        pair = trace['selected']['closed_direction']
        design = state.get('factorial_design', {})
        if (design.get('closed_direction') != pair or design.get('target_planning_protocol') != VERSION
                or design.get('target_id') != trace['action']):
            raise ValueError('组合对照没有采用模型选择的单向目标')
        for run in state['runs']:
            if run['name'] != 'baseline' and datetime.fromisoformat(run['execution']['started']) < datetime.fromisoformat(trace['finished']):
                raise ValueError('单向后续场景在模型选择完成之前启动')
    elif diagnosis.get('selected') or any(r['changes'] for r in state['runs']):
        raise ValueError('模型选点失败后仍执行单向治理')
