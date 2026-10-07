"""Bounded factual checks alongside, never in place of, original model prose.

Constraint eligibility is not zero externality. These checks cover explicit
measured costs and known missing observables, not arbitrary natural-language
entailment. Historical records are assessed read-only, without recertification.
"""
import math
import re

LEGACY_VERSION = 'measured-conclusion-v1'
POLICY_VERSION = 'measured-conclusion-v2'
NUMERIC_VERSION = 'measured-conclusion-v3'
THRESHOLD_VERSION = 'measured-conclusion-v4'
V5_VERSION = 'measured-conclusion-v5'
V6_VERSION = 'measured-conclusion-v6'
V7_VERSION = 'measured-conclusion-v7'
V8_VERSION = 'measured-conclusion-v8'
V9_VERSION = 'measured-conclusion-v9'
V10_VERSION = 'measured-conclusion-v10'
V11_VERSION = 'measured-conclusion-v11'
V12_VERSION = 'measured-conclusion-v12'
V13_VERSION = 'measured-conclusion-v13'
V14_VERSION = 'measured-conclusion-v14'
V15_VERSION = 'measured-conclusion-v15'
V16_VERSION = 'measured-conclusion-v16'
V17_VERSION = 'measured-conclusion-v17'
VERSION = 'measured-conclusion-v18'
SUPPORTED_VERSIONS = (LEGACY_VERSION, POLICY_VERSION, NUMERIC_VERSION, THRESHOLD_VERSION,
                      V5_VERSION, V6_VERSION, V7_VERSION, V8_VERSION, V9_VERSION, V10_VERSION, V11_VERSION, V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION, VERSION)
EPS = 1e-9
LEGACY_SCOPE = ('只核对已提供的配对指标、明确的零代价表述及已知未测量项；'
         '未发现冲突不等于已证明所有模型解释。批次分配不能证明动态排队回堵或零风险。')
POLICY_SCOPE = ('核对已提供的配对指标、明确停车等级、零代价表述及已知未测量项；'
         'S1/S2/S3仅为场景编号。检查不覆盖任意语言，未发现冲突不等于已证明所有解释。'
         '批次分配不能证明动态排队回堵或零风险。')
SCOPE = ('另核对明确标注范围和pcu·h单位的前后耗时及其百分比，允许显示精度误差；'
         '未来试验不继承其他组的已执行措施。') + POLICY_SCOPE
THRESHOLD_SCOPE = '另核对公开说明中明确声称的邻区数值门槛是否与冻结任务一致。' + SCOPE
CLAIM_SCOPE = '另核对邻区新增里程的符号、模型剩余额度和停车等级强弱的明确公开说法。' + THRESHOLD_SCOPE
DEMAND_SCOPE = '跨需求全称代价表述还需按每个需求的冻结门槛逐项核对。' + CLAIM_SCOPE
REGION_SCOPE = '明确候选区的停车等级仅与该区实际改动场景核对；未试区域不得继承其他候选区的治理等级。' + DEMAND_SCOPE
ACTION_SCOPE = '区分明确否定的选择、执行或测量动作与已执行等级断言；否定不跨转折继承。' + REGION_SCOPE
METRIC_SCOPE = '明确的并列指标等值断言按范围、单位和唯一实测场景分别核对；不将近似值、未知值或通过门槛视为零。' + ACTION_SCOPE
SOURCE_SCOPE = '停车等级按原始观测引用、区域与基准／现状／执行改动来源核对，未来备选不冒充执行。' + METRIC_SCOPE
BUDGET_SCOPE = '预算耗尽断言区分原生场景与模型请求范围，显式原生额度不按模型余额判断。' + SOURCE_SCOPE
SUPPORT_SCOPE = '明确的跨需求资格、最优与唯一性表述另按完整配对证据核对；未知计划需求不作通过认证。' + BUDGET_SCOPE
OBSERVABLES = dict(available=['方向级流量、速度、V/C', '固定核心/邻区/全网行驶时间消耗',
                            '同条件配对变化、邻区毛新增过饱和', '各候选区实际响应及约束判断'],
                   unavailable=['排队长度或排队车辆数', '逐秒轨迹、动态排队回堵', '真实城市因果或零风险证明'],
                   conditional=['全网毛新增过饱和仅在任务开启该检查时提供', '路权及转向响应仅相应适配器提供'])


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def safe_proposal(value):
    """Retain rejected structured replies without retaining credential strings."""
    from .llm_client import redact
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [safe_proposal(v) for v in value]
    if isinstance(value, dict):
        return {k: safe_proposal(v) for k, v in value.items()}
    if type(value) is float and not math.isfinite(value):
        raise ValueError('non-finite model value')
    return value


def cost_facts(observations):
    rows = []
    for o in observations:
        if not o.get('changes') or not o.get('delta') or type(o.get('eligible')) is not bool:
            continue  # No valid paired result; missing is never zero.
        delta = o['delta']
        directions = {(c.get('from_node'), c.get('to_node')) for c in o['changes']}
        roads = list(dict.fromkeys(r['name'] for r in o.get('core_roads', [])
                                  if r.get('name') and (r.get('from_node'), r.get('to_node')) in directions))
        row = dict(ref=o['ref'], run=o.get('run'), demand_factor=o.get('demand_factor'), roads=roads,
                   constraint_passed=o['eligible'], violations=o.get('violations', []))
        for scope in ('core', 'neighbor', 'network'):
            row[scope+'_time_change_pct'] = number(delta.get(scope, {}).get('running_time_change_pct'))
            row[scope+'_net_oversaturated_km'] = number(delta.get(scope, {}).get('oversaturated_km_change'))
        for scope in ('neighbor', 'network'):
            row[scope+'_new_oversaturated_km'] = number(o.get('new_'+scope+'_oversaturated_km'))
        row['has_measured_cost'] = any(v > EPS for k, v in row.items()
                                        if k.endswith(('_time_change_pct', '_oversaturated_km')) and v is not None)
        rows.append(row)
    return rows


def hypothesis_gaps(hypotheses):
    gaps = []
    for h in hypotheses:
        text = h.get('hypothesis', '')+'；'+h.get('evidence_needed', '')
        # The decision interface supplies aggregate road metrics, not queue
        # length or a vehicle trajectory. Native module names do not supply it.
        if re.search(r'排队长度|排队车辆数|逐秒轨迹|动态回堵', text):
            gaps.append(dict(hypothesis_id=h['id'], missing='当前观测未提供排队长度、排队车辆数或逐秒动态回堵证据。'))
    return gaps


def contract(observations, context):
    rows = {r['ref']: r for r in context.get('programme_cost_facts', [])}
    rows.update({r['ref']: r for r in cost_facts(observations)})
    hypotheses = context.get('initial_hypotheses') or context.get('programme_hypotheses') or []
    version = context.get('measured_conclusion', {}).get('version', VERSION)
    if version not in SUPPORTED_VERSIONS:
        raise ValueError('不支持的实测结论协议')
    result = dict(version=version, costs=list(rows.values()), hypothesis_gaps=hypothesis_gaps(hypotheses),
                  scope={LEGACY_VERSION: LEGACY_SCOPE, POLICY_VERSION: POLICY_SCOPE,
                         NUMERIC_VERSION: SCOPE, THRESHOLD_VERSION: THRESHOLD_SCOPE,
                         V5_VERSION: THRESHOLD_SCOPE, V6_VERSION: CLAIM_SCOPE,
                         V7_VERSION: DEMAND_SCOPE, V8_VERSION: DEMAND_SCOPE,
                         V9_VERSION: DEMAND_SCOPE, V10_VERSION: DEMAND_SCOPE, V11_VERSION: REGION_SCOPE, V12_VERSION: ACTION_SCOPE, V13_VERSION: METRIC_SCOPE, V14_VERSION: SOURCE_SCOPE,V15_VERSION: BUDGET_SCOPE,V16_VERSION: BUDGET_SCOPE,V17_VERSION: SUPPORT_SCOPE,VERSION: SUPPORT_SCOPE}[version])
    if version != LEGACY_VERSION:
        from .executed_facts import VERSION as EXECUTED_VERSION, policy_facts
        policies = {r['ref']: r for r in context.get('programme_policy_facts', [])}
        policies.update({r['ref']: r for r in policy_facts(observations)})
        result.update(policy_protocol=EXECUTED_VERSION, executed_policies=list(policies.values()))
    if version in (V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
        from .metric_assertions import catalog
        result['metric_assertions'] = catalog()
    if version in (V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
        from .executed_facts import parking_source_contract
        result['parking_claim_sources'] = parking_source_contract()
    if version in (V16_VERSION,V17_VERSION,VERSION):
        from .evidence_support import ledger,LEGACY_VERSION as SUPPORT_LEGACY,V2_VERSION as SUPPORT_V2,VERSION as SUPPORT_VERSION
        result['evidence_support']=ledger(observations,context,version=SUPPORT_LEGACY if version==V16_VERSION else SUPPORT_V2 if version==V17_VERSION else SUPPORT_VERSION)
    return result


def acknowledgment_schema(facts):
    properties = dict(ref=dict(type='string', enum=[r['ref'] for r in facts['costs']]),
                      constraint_passed=dict(type='boolean'), has_measured_cost=dict(type='boolean'),
                      neighbor_new_oversaturated_km=dict(type=['number', 'null']))
    return dict(type='array', maxItems=len(facts['costs']), items=dict(type='object', properties=properties,
                required=list(properties), additionalProperties=False))


def reviewed_cost_refs_schema(facts):
    """The model attests which runs it reviewed; measurements remain executor data."""
    refs=[r['ref'] for r in facts['costs']]
    return dict(type='array',items=dict(type='string',enum=refs),maxItems=len(refs),uniqueItems=True,
                description='最终复核须列齐全部已测ref。继续试验可填[]；若已逐项核对全部已测ref，也可列齐，但不表示研究完成。数值以measured_conclusion.costs为准。')


def reviewed_cost_ref_issues(value,facts,final,*,allow_early_review=False):
    all_refs={r['ref'] for r in facts['costs']}
    allowed=({frozenset(all_refs)} if final else
             {frozenset(),frozenset(all_refs)} if allow_early_review else {frozenset()})
    actual=value.get('reviewed_cost_refs')
    if (not isinstance(actual,list) or any(not isinstance(ref,str) for ref in actual)
            or len(actual)!=len(set(actual)) or frozenset(actual) not in allowed):
        return ['最终复核须逐项引用全部已测场景；继续试验可不确认或完整确认全部已测场景，不得只确认部分。实测代价以冻结观测为准。'
                if allow_early_review else '最终复核须逐项引用全部已测场景；继续试验时应填空数组。实测代价以冻结观测为准。']
    return []


def expected_acknowledgments(facts):
    return [{k: r[k] for k in ('ref', 'constraint_passed', 'has_measured_cost', 'neighbor_new_oversaturated_km')}
            for r in facts['costs']]


def acknowledgment_issues(value, facts, final):
    expected = expected_acknowledgments(facts) if final else []
    actual = value.get('cost_acknowledgments')
    if not isinstance(actual, list) or len(actual) != len(expected):
        return ['最终复核须逐项确认全部已测措施的约束状态、实际代价和邻区毛新增过饱和；继续实验时填空数组。']
    by_ref = {r['ref']: r for r in expected}
    seen = set()
    for row in actual:
        if not isinstance(row, dict) or not isinstance(row.get('ref'), str) or row['ref'] in seen or row['ref'] not in by_ref:
            return ['实测代价确认存在遗漏、重复或无效场景引用。']
        ref = row['ref']; seen.add(ref); target = by_ref[ref]
        if set(row) != set(target) or any(type(row[k]) is not bool or row[k] != target[k]
                                         for k in ('constraint_passed', 'has_measured_cost')):
            return ['通过约束与存在实测代价必须分别填写，不能互相替代。']
        actual_km = row['neighbor_new_oversaturated_km']; target_km = target['neighbor_new_oversaturated_km']
        if (target_km is None and actual_km is not None) or (target_km is not None and
                (number(actual_km) is None or not math.isclose(actual_km, target_km, abs_tol=EPS, rel_tol=1e-7))):
            return ['邻区毛新增过饱和数值与配对观测不符；不能填净变化、阈值或零来代替。']
    return []


def _affirmed(pattern, text, wide_negation=False, plain_not_prove=False):
    for match in re.finditer(pattern, text, re.I):
        before = text[max(0, match.start()-12):match.start()]
        negated=bool(re.search(r'不能|不得|不应|不代表|不意味着|不等于|并非|不是|避免(?:声称|说)',before))
        if wide_negation and not negated:
            clause=text[max(0, match.start()-80):match.start()].split('，')[-1]
            negated=bool(re.search(r'(?:不能|无法|不足以|未能).{0,32}(?:证明|确认|断言).{0,32}$',clause))
            if plain_not_prove and not negated:
                negated=bool(re.search(r'不(?:会)?(?:证明|确认|断言).{0,32}$',clause))
        if not negated:
            yield match.group()


def prose_issues(value, facts, context, observations=(), proposed_changes=()):
    from .executed_facts import named_rows, level_issues, status_issues, changed_parking_regions
    issues = []
    known_roads = {r['name'] for o in observations for r in o.get('core_roads', []) if r.get('name')}
    regions_by_ref = changed_parking_regions(observations) if facts['version'] in (V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION) else None
    texts = [(k, value.get(k, '')) for k in ('summary', 'reason', 'risk', 'next_check', 'programme_summary')]
    for key in ('hypothesis_updates', 'programme_updates'):
        for u in value.get(key, []):
            texts.append((key+':'+u['hypothesis_id'], u['explanation']))
            gap = next((g for g in facts['hypothesis_gaps'] if g['hypothesis_id'] == u['hypothesis_id']), None)
            if gap and u['judgment'] != 'unresolved':
                issues.append(dict(code='unmeasured_observable', field=key+':'+u['hypothesis_id'], refs=[],
                                   message=gap['missing']+'该假设须保留未解决部分，不能整项判为支持或否定。'))
    for field, text in texts:
        issues.extend(level_issues(text, field, facts.get('executed_policies', []),
            local_job=context.get('programme_current_job') if not field.startswith('programme_') else None,
            known_roads=known_roads,regions_by_ref=regions_by_ref,action_polarity=facts['version'] in (V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION),
            source_observations=observations if facts['version'] in (V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION) else None,proposed_changes=proposed_changes))
        issues.extend(status_issues(text, field, facts['costs'],
            context.get('programme_current_job') if not field.startswith('programme_') else None))
        if facts['version'] in (THRESHOLD_VERSION,V5_VERSION,V6_VERSION,V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
            frozen=context.get('task',{}).get('max_new_oversaturated_km')
            if number(frozen) is not None:
                for match in re.finditer(r'(?:邻区|周边)[^。；;\n]{0,70}?过饱和[^。；;\n]{0,45}?(?:低于|不超过|≤|门槛(?:为|是)?|上限(?:为|是)?)\s*(\d+(?:\.\d+)?)\s*(?:km|公里|千米)(?:门槛|上限)?',text,re.I):
                    if not math.isclose(float(match.group(1)),frozen,abs_tol=EPS):
                        issues.append(dict(code='threshold_changed',field=field,refs=[],quote=match.group(),
                                           message='说明中的邻区过饱和门槛与任务冻结配置不一致；实际观测值不能替代门槛。'))
        for clause in re.split(r'[。；;\n]', text):
            rows = named_rows(clause, facts['costs']) or facts['costs']
            neighbor = [r for r in rows if (r['neighbor_new_oversaturated_km'] or 0) > EPS]
            neighbor_cost = [r for r in rows if any((r[k] or 0) > EPS for k in
                              ('neighbor_time_change_pct', 'neighbor_net_oversaturated_km', 'neighbor_new_oversaturated_km'))]
            network_cost = [r for r in rows if any((r[k] or 0) > EPS for k in
                             ('network_time_change_pct', 'network_net_oversaturated_km', 'network_new_oversaturated_km'))]
            neighbor_named = bool(re.search(r'邻区|周边', clause))
            zero_cost_rows = (network_cost if '全网' in clause and not neighbor_named else
                              neighbor_cost if neighbor_named and '全网' not in clause else
                              [r for r in rows if r['has_measured_cost']])
            patterns = [
                ('zero_neighbor_congestion', r'(?:无|没有|未出现)(?:任何|明显)?(?:邻区|周边)(?:新增)?过饱和(?:溢出)?', neighbor,
                 '实际存在邻区毛新增过饱和；小于门槛仍不是零新增。'),
                ('zero_neighbor_cost', r'(?:无|没有|未出现)(?:任何|明显)?(?:邻区|周边)(?:恶化|代价|不利影响)', neighbor_cost,
                 '实际存在邻区指标增加；通过约束不等于没有周边代价。'),
                ('zero_risk', r'(?:无|没有|零)(?:任何|明显)?(?:周边|邻区)?(?:溢出风险|风险)', rows,
                 '有限批次观测不能证明零风险；需保留实测代价与未测动态影响。'),
                ('zero_measured_cost', r'(?:代价|不利影响)\s*(?:均为|为|是|=|：|:)?\s*(?:0(?:\.0+)?(?![\d.])|零)|零代价',
                 zero_cost_rows,
                 '实际存在正代价，极小增加仍应保留；可说明接近零，不能直接写代价为零。'),
            ]
            for code, pattern, affected, message in patterns:
                matches = list(_affirmed(pattern, clause,
                    facts['version'] in (V5_VERSION,V6_VERSION,V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION),
                    facts['version'] in (V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION)))
                if affected and matches:
                    issues.append(dict(code=code, field=field, refs=[r['ref'] for r in affected],
                                       quote=matches[0], message=message))
    return issues


def assess_entry(entry, strict=None):
    payload = entry.get('input', {}); context = payload.get('context', {})
    facts = contract(payload.get('observations', []), context)
    # Legacy records keep their frozen contract; read-only inspection can still
    # find an explicit label conflict in the observations actually sent then.
    checked_facts = facts
    if facts['version'] == LEGACY_VERSION:
        from .executed_facts import policy_facts
        # Other groups' legacy cost rows identify scope but do not carry action
        # parameters. Unknown levels must not inherit this group's levels.
        policies = {r['ref']: dict(ref=r['ref'],run=r.get('run'),roads=r.get('roads',[]),
                                   parking_levels=[],levels_known=False) for r in facts['costs']}
        policies.update({r['ref']:r for r in policy_facts(payload.get('observations', []))})
        checked_facts = dict(facts, executed_policies=list(policies.values()))
    if entry.get('status')=='rejected' and not entry.get('proposed_decision') and not entry.get('conclusion_check'):
        return dict(version=facts['version'],status='not_reviewed',costs=facts['costs'],hypothesis_gaps=facts['hypothesis_gaps'],
                    issues=[],scope=facts['scope'],unreviewed_reason='工具回复未通过校验，未取得可核对的完整结论；不能推定具体事实冲突。')
    value = entry.get('proposed_decision') or entry
    if (context.get('action_execution') and value is entry
            and entry.get('action') in payload.get('allowed_actions',{})):
        from .action_execution import generated_values
        for field,text in generated_values(entry).items():
            if value.get(field+'_origin')==context['action_execution']['version'] and value.get(field)==text:
                # Only verified compiler output is excluded. Model rationale
                # and old summaries keep their original measured-fact checks.
                value=dict(value,**{field:''})
    proposed_changes=payload.get('allowed_actions',{}).get(value.get('action'),{}).get('changes',[])
    issues = prose_issues(value, checked_facts, context, payload.get('observations', []),proposed_changes)
    from .measured_numbers import issues as numeric_issues
    issues.extend(numeric_issues(value, payload.get('observations', []), context))
    if facts['version'] in (V6_VERSION,V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
        from .public_claims import issues as public_claim_issues
        latest=entry.get('review_correction',{}).get('input') or payload
        issues.extend(public_claim_issues(value,checked_facts,latest.get('model_budget'),scoped_budget=facts['version'] in (V15_VERSION,V16_VERSION,V17_VERSION,VERSION)))
    if facts['version'] in (V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
        from .public_claims import demand_cost_issues
        issues.extend(demand_cost_issues(value,checked_facts,context))
    if facts['version'] in (V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
        from .metric_assertions import issues as metric_issues
        issues.extend(metric_issues(value,checked_facts,context,payload.get('observations',[])))
    if facts['version'] in (V16_VERSION,V17_VERSION,VERSION):
        from .evidence_support import issues as support_issues
        issues.extend(support_issues(value,facts['evidence_support']))
    strict = context.get('measured_conclusion', {}).get('version') in SUPPORTED_VERSIONS if strict is None else strict
    from .study_handoff import closes_study
    final = closes_study(value.get('action'), context)
    if strict and facts['costs']:
        if facts['version'] in (V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION):
            issues.extend(dict(code='cost_review_refs',field='reviewed_cost_refs',refs=[],message=m)
                          for m in reviewed_cost_ref_issues(value,facts,final,
                              allow_early_review=facts['version'] in (V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,VERSION)))
        else:
            issues.extend(dict(code='cost_acknowledgment', field='cost_acknowledgments', refs=[], message=m)
                          for m in acknowledgment_issues(value, facts, final))
    return dict(version=facts['version'], status='needs_review' if issues else 'no_detected_conflict',
                costs=facts['costs'], hypothesis_gaps=facts['hypothesis_gaps'], issues=issues, scope=facts['scope'])


def assess_traces(traces):
    from .executed_facts import policy_facts
    costs = {}; issues = []; gaps = {}; unreviewed=[]; policies={}
    for entry in traces:
        if entry.get('status') not in ('accepted', 'rejected'):
            continue
        check = assess_entry(entry)
        policies.update({r['ref']:r for r in policy_facts(entry.get('input',{}).get('observations',[]))})
        costs.update({r['ref']: r for r in check['costs']})
        gaps.update({g['hypothesis_id']: g for g in check['hypothesis_gaps']})
        issues.extend(dict(i, job_id=entry.get('job_id'), ordinal=entry.get('ordinal')) for i in check['issues'])
        if check.get('unreviewed_reason'):
            unreviewed.append(dict(job_id=entry.get('job_id'),ordinal=entry.get('ordinal'),message=check['unreviewed_reason']))
    result=dict(version=VERSION, status='needs_review' if issues else 'not_reviewed' if unreviewed or not traces else 'no_detected_conflict',
                costs=list(costs.values()), executed_policies=list(policies.values()), issues=issues, hypothesis_gaps=list(gaps.values()), scope=THRESHOLD_SCOPE)
    if unreviewed:result['unreviewed']=unreviewed
    return result


def assess_state(state):
    from .feedback_planner import run_observations
    from .executed_facts import policy_facts
    from .risk_review import VERSION as RISK_VERSION,neighbor_details
    result = assess_traces(state.get('planner_trace', []))
    result['costs'] = cost_facts(run_observations(state)) if state.get('runs') else []
    result['executed_policies'] = policy_facts(run_observations(state)) if state.get('runs') else []
    by_name={r['name']:r for r in state.get('runs',[])}
    for row in result['costs']:
        if state.get('risk_review_protocol')==RISK_VERSION:
            row.update(neighbor_details(by_name[row['run']].get('comparison',{})))
            continue
        links=by_name[row['run']].get('comparison',{}).get('new_neighbor_oversaturated_links',[])
        row['neighbor_new_links']=[{k:r.get(k) for k in ('index','name','from_node','to_node','length_km','vc')}
                                   for r in links[:10]]
        row['neighbor_new_link_count']=len(links)
    return result
