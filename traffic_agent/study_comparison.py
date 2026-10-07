"""Compact, independently paired facts for planning and the public comparison UI.

Every contrast belongs to one study, one demand and one fresh baseline. Missing
outputs and missing control responses stay unknown. No cross-engine arithmetic.
"""
from .conclusion_review import number
from .executed_facts import policy_facts
import copy

LEGACY_VERSION = 'study-comparison-v1'
VERSION = 'study-comparison-v2'
SCOPES = ('core', 'neighbor', 'network')
KINDS = dict(baseline='新基准',baseline_repeat='基准复跑',parking='仅停车',
             traffic_control='仅交通组织',combined='停车＋交通组织')


def time_value(run, scope):
    return number((run or {}).get('metrics', {}).get(scope, {}).get('running_time_pcu_h'))


def comparison(state, *, version=VERSION):
    if version not in (LEGACY_VERSION,VERSION):raise ValueError('未知的实测比较协议')
    job = state['id']
    scenario = state['task']['scenario']
    runs = [r for r in state.get('runs', []) if r.get('metrics') and r.get('execution', {}).get('status') == 'completed']
    factors = []
    for factor in state['task']['demand_factors']:
        local = [r for r in runs if r['demand_factor'] == factor]
        base = next((r for r in local if not r['changes'] and r['name'] != 'baseline_repeat'), None)
        policies = []
        for run in local:
            if not run['changes']:
                continue
            check = run.get('comparison', {})
            control = next((c['kind'] for c in run['changes'] if c.get('kind') in ('oneway', 'turn')), None)
            response = run.get('oneway_response' if control == 'oneway' else 'turn_response', {}).get('passed') if control else None
            measured = bool(base and check.get('delta') and type(check.get('eligible')) is bool)
            status = ('rejected' if measured and (check['eligible'] is False or response is False) else
                      'admissible' if measured and (not control or response is True) else 'unmeasured')
            ref = f"{job}:{run['name']}"
            label = policy_facts([dict(ref=ref, run=run['name'], changes=run['changes'])])[0]['label']
            if label.startswith('无停车等级变更') and control:
                label = '单向禁行' if control == 'oneway' else '禁转'
            elif control == 'turn':
                label += '＋禁转'
            policy = dict(ref=ref, run=run['name'], label=label, policy_label=run.get('policy_label'),
                          status=status, constraint_passed=check.get('eligible') if measured else None,
                          control_response=response, violations=check.get('violations', []))
            for scope in SCOPES:
                policy[scope+'_time_change_pct'] = number(check.get('delta', {}).get(scope, {}).get('running_time_change_pct')) if measured else None
            if version==VERSION:
                from .hypothesis_contract import kind,paired_metric
                core=paired_metric('core_time_decrease',state,base,run)
                neighbor=paired_metric('neighbor_limits',state,base,run)
                policy.update(kind=kind(run['changes']),control_kind=control,executed_changes=copy.deepcopy(run['changes']),
                    control_directions=[dict(closed=copy.deepcopy(c['closed_direction']),retained=list(reversed(c['closed_direction'])))
                                        if c.get('kind')=='oneway' else dict(turn=copy.deepcopy(c['turn']))
                                        for c in run['changes'] if c.get('kind')=='oneway' and c.get('closed_direction')
                                        or c.get('kind')=='turn' and c.get('turn')],
                    checks=dict(control_applicable=bool(control),control_passed=response,
                                core_time_decreased=core['passed'],neighbor_limits_passed=neighbor['passed'],
                                recommendation_admissible={'admissible':True,'rejected':False,'unmeasured':None}[status]),
                    neighbor_limits=neighbor['values'])
            policies.append(policy)
        contrasts = []
        # Only the declared factorial design can establish a matched contrast.
        # Free-form parking candidates from different places are not a factorial.
        if base and scenario in ('oneway', 'circulation'):
            design = state.get('factorial_design', {}).get('policies', {})
            by_label = {r['policy_label']: r for r in local if r.get('policy_label') in design
                        and r['changes'] == design[r['policy_label']]}
            combined = by_label.get('S3_combined')
            for name in ('S1_parking', 'S2_oneway' if scenario == 'oneway' else 'S2_turn'):
                single = by_label.get(name)
                if not combined or not single:
                    continue
                before = next(p for p in policies if p['run'] == single['name'])
                after = next(p for p in policies if p['run'] == combined['name'])
                contrast = dict(single_ref=before['ref'], combined_ref=after['ref'], single_label=before['label'],
                                combined_label=after['label'], single_status=before['status'], combined_status=after['status'])
                for scope in SCOPES:
                    a, b = time_value(single, scope), time_value(combined, scope)
                    contrast[scope+'_time_difference_pcu_h'] = b-a if a is not None and b is not None else None
                contrasts.append(contrast)
        factors.append(dict(demand_factor=factor, baseline_ref=f"{job}:{base['name']}" if base else None,
                            policies=policies, combination_contrasts=contrasts))
    result=dict(version=version, job_id=job, scenario=scenario, road=state['task'].get('road_name'), factors=factors,
                scope='同组、同需求、新基准。措施行列出相对基准的变化；组合差值=组合耗时−单项耗时，负值表示组合更低，单位pcu·h。约束通过与耗时更低分别判断；单需求不能证明稳健性，不跨组混算或推断现实协同。')
    if version==VERSION:
        from .hypothesis_contract import kind
        result['completed_scenes']=[dict(ref=f"{job}:{r['name']}",demand_factor=r['demand_factor'],
            kind=kind(r['changes']) if r['changes'] else 'baseline_repeat' if r['name']=='baseline_repeat' else 'baseline') for r in runs]
        result['coverage_text']=coverage_text(result)
    return result


def coverage_text(comparison):
    if comparison.get('version')!=VERSION:return None
    scenes=comparison.get('completed_scenes',[])
    if not scenes:return '尚无已完成场景'
    counts={k:sum(r['kind']==k for r in scenes) for k in KINDS}
    factors=sorted({r['demand_factor'] for r in scenes})
    return '、'.join(f'{KINDS[k]}{n}场' for k,n in counts.items() if n)+'；已测需求×'+ '/'.join(f'{f:g}' for f in factors)


def programme_comparisons(plan, states):
    from .programme_review import ENGINES
    version=plan.get('study_comparison_protocol',LEGACY_VERSION)
    return [dict(comparison(states[study['job_id']],version=version), study_id=study['id'], title=study['title'],
                 engine=ENGINES[study['task']['scenario']]) for study in plan['studies']]
