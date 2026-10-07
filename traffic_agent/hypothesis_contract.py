"""Pre-execution, bounded measurement contracts selected by the planner.

The model owns the question and its declared interpretation. This module checks
that interpretation against paired observations, not arbitrary prose or causes.
Legacy hypotheses without a contract are never retroactively reinterpreted.
"""
import copy
import math

from .decision_analysis import policy_key, change_pct
from .llm_client import ModelError
from .llm_settings import redact

VERSION = 'hypothesis-measurement-v1'
TESTS = {
    'core_time_decrease': '冻结核心范围的总耗时低于同需求基准；与是否通过推荐约束分开判断。',
    'region_no_worsening': '每个冻结候选区域的耗时均不高于同需求无治理基准；严格不增加，与允许小幅增长的区域推荐约束分开判断。',
    'admissible': '本组全部原定约束与原生控制响应检查均通过；不等于没有代价。',
    'neighbor_limits': '邻区耗时增幅和毛新增过饱和均不超过本组原定门槛；不包括核心内其他候选子区。',
    'core_advantage_over_parking': '交通组织单项或组合的核心总耗时严格低于本组仅停车；全部类型在此指交通组织和组合，不把仅停车与自身相比。',
    'combination_core_advantage': '组合的核心总耗时分别严格低于本组停车单项和交通组织单项；不等于更适合推荐或已证明协同成因。',
    'control_response': '原生禁行或禁转控制响应通过；与核心交通收益、周边代价及推荐资格分别判断。',
    'unavailable': '本接口不能直接检验该问题，保留原问题和证据缺口，不用其他指标替代。',
}
QUESTION_PROTOCOL = 'typed-initial-questions-v1'
QUESTION_DEFINITIONS = {
    'core_time_decrease': dict(metric='core_running_time', comparator='untreated_baseline', relation='strictly_lower',
        predicate='冻结核心范围总耗时严格低于同需求无治理基准'),
    'region_no_worsening': dict(metric='each_frozen_region_running_time', comparator='untreated_baseline', relation='none_higher',
        predicate='每个冻结候选区域耗时均不高于同需求无治理基准（严格零增长）'),
    'admissible': dict(metric='recommendation_eligibility', comparator='all_frozen_requirements', relation='all_pass',
        predicate='通过该组全部原定推荐约束及所需控制响应检查'),
    'neighbor_limits': dict(metric='neighbor_time_and_gross_new_oversaturation', comparator='frozen_neighbor_limits', relation='within_both_limits',
        predicate='邻区耗时增幅和毛新增过饱和均不超过该组原定门槛'),
    'core_advantage_over_parking': dict(metric='core_running_time', comparator='parking_only', relation='strictly_lower',
        predicate='冻结核心总耗时严格低于同需求的仅停车措施'),
    'combination_core_advantage': dict(metric='core_running_time', comparator='both_single_measures', relation='strictly_lower_than_each',
        predicate='冻结核心总耗时分别严格低于同需求的两个单项措施'),
    'control_response': dict(metric='native_control_response', comparator='adapter_response_check', relation='passed',
        predicate='原生禁行或禁转控制确实生效（与核心收益和推荐资格分别判断）'),
    'unavailable': dict(metric=None, comparator=None, relation=None,
        predicate='本接口不能直接判定原问题，保留证据缺口。'),
}


def question_catalog():
    """One metric/reference definition for initial selection and frozen questions."""
    return {test:dict(definition,allowed_policies=list(POLICY_OPTIONS[test]))
            for test,definition in QUESTION_DEFINITIONS.items()}
POLICY_FAMILIES = {
    'all': dict(label='停车、交通组织及组合',members=('parking','traffic_control','combined')),
    'parking': dict(label='仅停车',members=('parking',)),
    'traffic_control': dict(label='仅单向或禁转交通组织',members=('traffic_control',)),
    'combined': dict(label='仅停车与交通组织组合',members=('combined',)),
    'control_and_combined': dict(label='交通组织及组合，不含仅停车',members=('traffic_control','combined')),
}
POLICIES = tuple(POLICY_FAMILIES)
TEST_EXCLUSIONS = {'core_advantage_over_parking': ('parking',),
                   'control_response': ('parking',)}


def effective_policies(family, test):
    """The same typed family membership used on the wire and measured runs."""
    return tuple(member for member in POLICY_FAMILIES[family]['members']
                 if member not in TEST_EXCLUSIONS.get(test, ()))


def policy_catalog():
    return dict(version='hypothesis-policy-family-v1',
                families={name:dict(label=value['label'],members=list(value['members']))
                          for name,value in POLICY_FAMILIES.items()},
                test_exclusions={test:list(members) for test,members in TEST_EXCLUSIONS.items()})


POLICY_OPTIONS = {test:list(POLICIES) for test in TESTS}
POLICY_OPTIONS['core_advantage_over_parking']=['all','traffic_control','combined','control_and_combined']
POLICY_OPTIONS['combination_core_advantage']=['combined']
POLICY_OPTIONS['control_response']=['all','traffic_control','combined','control_and_combined']


def policy_constraints():
    """Share metric/family restrictions across numbered and named questions."""
    return [{'if':dict(properties=dict(test=dict(const=test))),
             'then':dict(properties=dict(policies=dict(enum=list(options))))}
            for test,options in POLICY_OPTIONS.items() if options!=list(POLICIES)]


PROPERTIES = dict(
    test=dict(type='string', enum=list(TESTS), description='选择与原问题一致的可观测判据，无法对应时用unavailable，不能偷换问题。'),
    studies=dict(type='array', items=dict(type='integer', minimum=1, maximum=5), minItems=1, maxItems=5,
                 description='只引用本次最终提交的experiments数组位置，从1连续编号；提交2组时只能用1或2，不能计入已删除的草稿组。各组在自己的新基准内检验。'),
    policies=dict(type='string', enum=list(POLICIES), description='被检验的措施，不是比较基准；按输入hypothesis_policy_contract.families的members选择。traffic_control仅含单向或禁转单项，不含组合；control_and_combined含交通组织及组合。test_exclusions标明按检验类型排除的比较基准。'),
    quantifier=dict(type='string', enum=['any', 'all'],
                    description='每个指定组：any为至少一个已试措施，all为所有已试措施；同一措施须在全部约定需求通过。各指定组须分别满足，不平均、不跨引擎合并。'))
SCHEMA = dict(type='object', properties=PROPERTIES, required=list(PROPERTIES), additionalProperties=False,
              allOf=policy_constraints())


def validate_shape(value):
    if (not isinstance(value, dict) or set(value) != set(PROPERTIES)
            or not isinstance(value.get('test'),str) or value.get('test') not in TESTS or value.get('policies') not in POLICIES
            or value.get('quantifier') not in ('any', 'all')):
        raise ModelError('初始假设缺少有效的观测判据；未执行仿真')
    ids = value['studies']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 5 or any(type(i) is not int or not 1 <= i <= 5 for i in ids)
            or len(ids) != len(set(ids))):
        raise ModelError('假设必须明确引用不重复的研究组编号')


def validate(value):
    validate_shape(value)
    if value['policies'] not in POLICY_OPTIONS[value['test']]:
        errors=dict(combination_core_advantage='组合增益判据只能指向组合措施',
                    core_advantage_over_parking='额外收益不能把仅停车与其自身比较',
                    control_response='原生禁行响应不能用仅停车措施检验')
        raise ModelError(errors.get(value['test'],'检验指标与措施集合不相容'))


def bind(assessment, entries, compilation, *, wording_protocol=None):
    """Resolve original proposal indices after safe study consolidation."""
    source_map = {s: i for i, entry in enumerate(entries) for s in entry['source_studies']}
    for action in compilation.get('actions', []):
        source_map[action['source_study']] = source_map[action['retained_source_study']]
    result = []
    for h in assessment:
        if 'authoring' in h:
            from .question_authoring import verify_row
            verify_row(h)
        c = h['criterion']; validate(c)
        if any(s not in source_map for s in c['studies']):
            raise ModelError('假设引用了没有安排的研究组')
        scope = []
        for i in sorted({source_map[s] for s in c['studies']}):
            task = entries[i]['task']; scenario = task['scenario']
            if c['test'] != 'unavailable':
                if scenario == 'diagnosis':
                    raise ModelError('只诊断组没有治理对照，不能检验治理收益判据')
                if scenario == 'parking' and (c['policies'] in ('traffic_control', 'combined','control_and_combined') or c['test'] in ('core_advantage_over_parking','control_response')):
                    raise ModelError('假设选择的措施不在该研究组的对照中')
            # Archived v5 accidentally omitted these values. Preserve its replay;
            # v6 freezes both limits again instead of reinterpreting old records.
            scope.append(dict(study_id=f'study_{i+1}', scenario=scenario, road=task.get('road_name'),
                              demand_factors=copy.deepcopy(task['demand_factors']),
                              **(dict(max_neighbor_time_increase_pct=task['max_neighbor_time_increase_pct'],
                                      max_new_oversaturated_km=task['max_new_oversaturated_km'])
                                 if wording_protocol and wording_protocol.endswith(('-v3','-v4','-v6')) and c['test']=='neighbor_limits'
                                 and {'max_neighbor_time_increase_pct','max_new_oversaturated_km'} <= task.keys() else {})))
        evaluation=dict(version=VERSION,test=c['test'],policies=c['policies'],quantifier=c['quantifier'],scope=scope,rule=TESTS[c['test']])
        if wording_protocol=='bounded-question-wording-v6':
            evaluation['definition']=dict(version=QUESTION_PROTOCOL,**QUESTION_DEFINITIONS[c['test']])
        if wording_protocol:
            from .question_wording import interpretation
            evaluation['interpretation']=interpretation(h,evaluation,[e['task'].get('road_name') for e in entries],wording_protocol)
        result.append(dict(h,evaluation=evaluation))
    return result


def hypotheses(assessment):
    return [dict(id=f'H{i+1}', hypothesis=redact(h['hypothesis']), evidence_needed=redact(h['evidence_needed']),
                 **({k: copy.deepcopy(h[k]) for k in ('criterion', 'evaluation')} if 'evaluation' in h else {}),
                 **(dict(authoring=copy.deepcopy(h['authoring'])) if 'authoring' in h else {}))
            for i, h in enumerate(assessment)]


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def all_of(values):
    return False if False in values else True if values and all(v is True for v in values) else None


def time(run, scope):
    value = (run or {}).get('metrics', {}).get(scope, {}).get('running_time_pcu_h')
    return value if finite(value) and value >= 0 else None


def unique(runs, *, baseline=False):
    if not runs:
        return None
    keys = ('metrics',) if baseline else ('metrics', 'comparison', 'oneway_response', 'turn_response')
    # A repeat with different measurements cannot be cherry-picked.
    return runs[0] if all(all(r.get(k) == runs[0].get(k) for k in keys) for r in runs[1:]) else None


def kind(changes):
    parking = any('parking' in c for c in changes)
    control = any(c.get('kind') in ('oneway', 'turn') for c in changes)
    return 'combined' if parking and control else 'traffic_control' if control else 'parking'


def paired_metric(test, state, base, run):
    """Observed paired metrics, independently of whether a control succeeded."""
    if not base or not run:return dict(passed=None,values={})
    if test=='core_time_decrease':
        a,b=time(base,'core'),time(run,'core');value=change_pct(a,b)
        return dict(passed=value<0 if value is not None else None,
                    values=dict(baseline_pcu_h=a,candidate_pcu_h=b,core_time_change_pct=value))
    if test=='region_no_worsening':
        expected=[r.get('id') for r in state.get('regions',[]) if isinstance(r,dict)]
        checks=run.get('comparison',{}).get('regional_checks')
        if (not expected or len(set(expected))!=len(expected) or not isinstance(checks,list)):
            return dict(passed=None,values=dict(expected_regions=expected,increased_regions=[],unverified_regions=expected))
        measured={}; duplicates=set()
        for check in checks:
            if not isinstance(check,dict):continue
            region=check.get('region')
            if not isinstance(region,str):continue
            if region in measured:duplicates.add(region)
            measured[region]=check
        increased=[];unverified=[];changes={}
        for region in expected:
            check=measured.get(region,{})
            before=check.get('before',{});after=check.get('after',{})
            before=before.get('running_time_pcu_h') if isinstance(before,dict) else None
            after=after.get('running_time_pcu_h') if isinstance(after,dict) else None
            delta=(change_pct(before,after) if region not in duplicates and finite(before) and finite(after)
                   and before>0 and after>=0 else None)
            changes[region]=delta
            if delta is None or not finite(check.get('time_change_pct')) or abs(delta-check['time_change_pct'])>1e-6:
                unverified.append(region)
            elif after>before:
                increased.append(region)
        outcomes=[False for _ in increased]+[None for _ in unverified]
        if not outcomes:outcomes=[True]
        return dict(passed=all_of(outcomes),values=dict(expected_regions=expected,
                    time_change_pct_by_region=changes,increased_regions=increased,unverified_regions=unverified))
    if test=='neighbor_limits':
        a,b=time(base,'neighbor'),time(run,'neighbor')
        change=0.0 if a==b==0 else change_pct(a,b)
        gross=run.get('comparison',{}).get('new_neighbor_oversaturated_km')
        limit=state['task'].get('max_neighbor_time_increase_pct');km=state['task'].get('max_new_oversaturated_km')
        return dict(passed=all_of([change<=limit+1e-9 if change is not None and finite(limit) else None,
                                  gross<=km+1e-9 if finite(gross) and gross>=0 and finite(km) else None]),
                    values=dict(neighbor_time_change_pct=change,neighbor_new_oversaturated_km=gross,
                                time_limit_pct=limit,new_oversaturated_limit_km=km))
    raise ValueError('未知的配对指标')


def cell(test, state, base, run, singles=()):
    refs = [f"{state['id']}:{r['name']}" for r in (base, run, *singles) if r]
    result = dict(passed=None, evidence_refs=list(dict.fromkeys(refs)), values={})
    if not base or not run:
        return result
    check = run.get('comparison', {})
    control = next((c['kind'] for c in run['changes'] if c.get('kind') in ('oneway', 'turn')), None)
    response = run.get('oneway_response' if control == 'oneway' else 'turn_response', {}).get('passed') if control else True
    if test == 'admissible':
        eligible = check.get('eligible')
        result['values'] = dict(constraint_passed=eligible, control_response=response)
        result['passed'] = all_of([eligible if type(eligible) is bool else None, response if type(response) is bool else None])
        return result
    if test == 'control_response':
        result['values'] = dict(control_response=response if type(response) is bool else None)
        result['passed'] = response if control and type(response) is bool else None
        if not control:result['gap'] = '仅停车措施没有原生禁行响应可检验。'
        return result
    if response is not True:
        result['gap'] = '控制响应未证实，不能把输出变化归于已实施该措施。'
        return result
    if test in ('core_time_decrease','region_no_worsening','neighbor_limits'):
        result.update(paired_metric(test,state,base,run))
    elif test in ('combination_core_advantage','core_advantage_over_parking'):
        b = time(run, 'core')
        values = [time(r, 'core') for r in singles]
        responses = [r.get('oneway_response' if state['task']['scenario'] == 'oneway' else 'turn_response', {}).get('passed')
                     for r in singles if r and kind(r['changes']) == 'traffic_control']
        differences = [b-a if b is not None and a is not None else None for a in values]
        result['values'] = (dict(combination_minus_singles_pcu_h=differences) if test=='combination_core_advantage'
                            else dict(candidate_minus_parking_pcu_h=differences[0] if differences else None))
        if (len(differences) == 2 and responses == [True] if test=='combination_core_advantage' else len(differences)==1):
            result['passed'] = all_of([d < 0 if d is not None else None for d in differences])
    return result


def study(evaluation, scope, state):
    result = dict(study_id=scope['study_id'], road=scope['road'], passed=None, policies=[])
    task = state['task']
    if any(task.get(k) != scope[v] for k, v in [('scenario', 'scenario'), ('road_name', 'road'), ('demand_factors', 'demand_factors')]):
        result['gap'] = '实测任务与事前冻结的假设范围不同。'
        return result
    if evaluation['test'] == 'unavailable':
        result['gap'] = TESTS['unavailable']
        return result
    runs = [r for r in state.get('runs', []) if r.get('metrics') and r.get('execution', {}).get('status') == 'completed']
    families = {}
    if task['scenario'] in ('oneway', 'circulation'):
        labels = dict(S1_parking='parking', S2_oneway='traffic_control', S2_turn='traffic_control', S3_combined='combined')
        for label in ('S1_parking', 'S2_oneway' if task['scenario'] == 'oneway' else 'S2_turn', 'S3_combined'):
            design = state.get('factorial_design', {}).get('policies', {}).get(label)
            families[label] = (labels[label], [r for r in runs if r.get('policy_label') == label and r['changes'] == design])
    else:
        for r in runs:
            if r.get('changes'):
                key = policy_key(r['changes'])
                families.setdefault(key, ('parking', []))[1].append(r)
    for family, (policy, measured) in families.items():
        accepted = effective_policies(evaluation['policies'],evaluation['test'])
        if policy not in accepted:
            continue
        cases = []
        for factor in scope['demand_factors']:
            base = unique([r for r in runs if not r['changes'] and r['demand_factor'] == factor], baseline=True)
            run = unique([r for r in measured if r['demand_factor'] == factor])
            single_labels=('S1_parking', 'S2_oneway', 'S2_turn') if evaluation['test']=='combination_core_advantage' else ('S1_parking',)
            singles = [unique([r for r in rows if r['demand_factor'] == factor]) for k, (_, rows) in families.items()
                       if k in single_labels] if evaluation['test'] in ('combination_core_advantage','core_advantage_over_parking') else []
            cases.append(dict(cell(evaluation['test'], state, base, run, singles), demand_factor=factor))
        result['policies'].append(dict(policy=policy, runs=[r['name'] for r in measured],
                                       passed=all_of([c['passed'] for c in cases]), cases=cases))
    values = [p['passed'] for p in result['policies']]
    from .planning_review import coverage
    from .strategy_evidence import findings
    complete = (coverage(state)['execution_complete'] if task['scenario'] == 'parking' else findings(state)['execution_complete'])
    # Existential witness / universal counterexample already settles that side.
    # The reverse needs the declared experiment coverage, not an empty set.
    if evaluation['quantifier'] == 'any':
        result['passed'] = True if True in values else False if complete and values and all(v is False for v in values) else None
    else:
        result['passed'] = False if False in values else True if complete and values and all(v is True for v in values) else None
    return result


def finding(hypothesis, states):
    evaluation = hypothesis['evaluation']
    if evaluation.get('version') != VERSION:
        raise ValueError('未知的事前假设观测协议')
    rows = [study(evaluation, scope, states[scope['study_id']]) if scope['study_id'] in states else
            dict(study_id=scope['study_id'], road=scope['road'], passed=None, policies=[], gap='该组尚无本次观测。')
            for scope in evaluation['scope']]
    outcome = all_of([r['passed'] for r in rows])
    judgment = 'supported' if outcome is True else 'contradicted' if outcome is False else 'unresolved'
    refs = list(dict.fromkeys(ref for row in rows for p in row['policies'] for c in p['cases'] for ref in c['evidence_refs']))
    return dict(id='hypothesis.'+hypothesis['id'], hypothesis_id=hypothesis['id'], question=evaluation.get('interpretation',{}).get('question',hypothesis['hypothesis']),
                **(dict(model_question=hypothesis['hypothesis']) if evaluation.get('interpretation') else {}),
                status='unmeasured' if outcome is None else 'supported' if outcome else 'not_supported',
                expected_judgment=judgment, evaluation=copy.deepcopy(evaluation),
                answer={'supported':'已测结果满足事前判据；不附加推荐资格或因果解释。',
                        'contradicted':'已测结果不满足事前判据；原问题保持，不改写成较弱主张。',
                        'unresolved':'该判据仍有缺测、歧义或能力缺口，不能支持或否定。'}[judgment],
                evidence_refs=refs, measurements=rows)


def findings(hypotheses, states):
    return [finding(h, states) for h in hypotheses if 'evaluation' in h]


def validate_update(update, hypothesis, facts):
    if 'evaluation' not in hypothesis:
        return
    from .planning_response import ResponseValidationError
    id = 'hypothesis.'+hypothesis['id']
    f = facts.get(id)
    if not f or f.get('evaluation') != hypothesis['evaluation']:
        raise ResponseValidationError('hypothesis_contract', '缺少与事前假设范围一致的判据检验')
    if update['judgment'] != f['expected_judgment']:
        raise ResponseValidationError('hypothesis_contract', f"{hypothesis['id']}的事前判据要求{f['expected_judgment']}；核心收益、推荐资格与全称反例不得互换")
    if update['judgment'] != 'unresolved' and id not in update['finding_ids']:
        raise ResponseValidationError('hypothesis_contract', '必须引用本假设自己的判据实测，不能用另一研究或另一问题的结果替代')
