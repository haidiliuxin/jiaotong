"""Question-level findings derived only from this job's completed paired runs.

These are bounded experimental findings, not a certification of arbitrary model
hypotheses or a causal explanation of real-world traffic.
"""
from .decision_analysis import decision_analysis
from .planning_review import footprint
from .selection import select

VERSION='question-evidence-v1'
LEGACY_REFERENCE_SCHEMA='measured-findings-v1'
REFERENCE_SCHEMA='scoped-measured-findings-v2'
REFERENCE_SCHEMAS=(LEGACY_REFERENCE_SCHEMA,REFERENCE_SCHEMA)


def measured_finding_ids(evidence):
    return [f['id'] for f in evidence['findings'] if f['status']!='unmeasured' and f['evidence_refs']]


def hypothesis_finding_ids(hypothesis,evidence,reference_schema=LEGACY_REFERENCE_SCHEMA):
    if reference_schema not in REFERENCE_SCHEMAS:raise ValueError('未知的假设依据引用协议')
    measured=measured_finding_ids(evidence)
    if reference_schema==REFERENCE_SCHEMA and 'evaluation' in hypothesis:
        # This finding already contains every paired case in the frozen
        # metric/policy/study/demand definition. Other findings are context,
        # not additional proof of this particular predicate.
        own='hypothesis.'+hypothesis['id']
        return [f['id'] for f in evidence['findings'] if f['id']==own and f['id'] in measured
                and f.get('evaluation')==hypothesis['evaluation']]
    return measured


def hypothesis_update_schema(hypotheses,evidence,required=False,*,reference_schema=LEGACY_REFERENCE_SCHEMA):
    if reference_schema not in REFERENCE_SCHEMAS:raise ValueError('未知的假设依据引用协议')
    measured=measured_finding_ids(evidence)
    references=dict(type='array',items=dict(type='string'),maxItems=5 if measured else 0,
        description='仅填写已测判断依据；未测缺口写在explanation。没有已测依据时填[]，判断unresolved。')
    if reference_schema==REFERENCE_SCHEMA:references['uniqueItems']=True
    if measured:references['items']['enum']=measured
    properties=dict(hypothesis_id=dict(type='string',enum=[h['id'] for h in hypotheses]),
        judgment=dict(type='string',enum=['supported','contradicted','unresolved']),
        finding_ids=references,explanation=dict(type='string'))
    schema=dict(type='array',minItems=len(hypotheses) if required else 0,maxItems=len(hypotheses),
        description='完整复核时逐项列齐所有假设；没有实测依据时为unresolved。已测依据与未测缺口不能混作同一个引用。',
        items=dict(type='object',properties=properties,required=list(properties),additionalProperties=False))
    # Keep the old sealed schema intact. New branches bind each hypothesis to
    # its own predeclared, measurable question, before the model writes prose.
    if any('evaluation' in h for h in hypotheses):
        import copy
        branches=[]
        for h in hypotheses:
            branch=copy.deepcopy(schema['items'])
            branch['properties']['hypothesis_id']['enum']=[h['id']]
            fact=next((f for f in evidence['findings'] if f['id']=='hypothesis.'+h['id']),None)
            if 'evaluation' in h and fact:
                branch['properties']['judgment']['enum']=[fact['expected_judgment']]
                branch['properties']['finding_ids']['description']='判断必须沿用事前判据；非未知结论必须引用hypothesis.'+h['id']+'。其他代价另作解释，不能改变本项问题。'
            if reference_schema==REFERENCE_SCHEMA:
                allowed=hypothesis_finding_ids(h,evidence,reference_schema)
                refs=branch['properties']['finding_ids']
                refs.update(maxItems=min(5,len(allowed)),uniqueItems=True)
                refs['items']=dict(type='string',**(dict(enum=allowed) if allowed else {}))
                if 'evaluation' in h:
                    if fact and fact['expected_judgment']!='unresolved':refs['minItems']=1
                    refs['description']='仅引用本假设事前定义的配对检验；其他组或其他问题的结果可讨论，但不能充当本判据依据。缺测时填[]并保留未知。'
            branches.append(branch)
        schema['items']=dict(anyOf=branches)
    return schema


def verify_reference_schema(entry):
    """New tools only offer measured references; old sealed replies retain their contract."""
    version=entry.get('finding_reference_schema')
    if version is None:
        if entry.get('input',{}).get('context',{}).get('finding_reference_schema'):
            raise ValueError('假设引用工具版本缺失')
        return
    if version not in REFERENCE_SCHEMAS:raise ValueError('未知的假设依据引用协议')
    context=entry['input']['context']
    if context.get('finding_reference_schema',LEGACY_REFERENCE_SCHEMA)!=version:
        raise ValueError('假设引用工具版本与冻结输入不符')
    if entry['status']!='accepted':return
    context=entry['input']['context']
    for field,hypotheses,evidence in [('hypothesis_updates','initial_hypotheses','research_evidence'),
                                      ('programme_updates','programme_hypotheses','programme_evidence')]:
        if field in entry:
            validate_updates(entry[field],context[hypotheses],context[evidence],measured_only=True,reference_schema=version)


def findings(state):
    result=_findings(state)
    from .review_scope import programme_only
    if not programme_only(state):
        from .hypothesis_contract import findings as criterion_findings
        result['findings'].extend(criterion_findings(state.get('research_hypotheses',[]),{'study_1':state}))
    return result


def _findings(state):
    if state['task'].get('scenario','parking')!='parking':
        from .strategy_evidence import findings as strategy_findings
        return strategy_findings(state)
    analysis=decision_analysis(state)
    policies=analysis['policies']
    measured=[p for p in policies if not p['missing']]
    valid=[p for p in policies if p['feasible']]
    selection=select(valid) if state['task'].get('recommendation_version')=='tolerance-v2' else None
    selected=selection['recommendation'] if selection else None
    def refs(items):
        return list(dict.fromkeys(f"{state['id']}:{name}" for p in items for c in p['checks'] for name in (c['baseline'],c['run'])))
    def item(id,question,status,answer,items=(),measurements=()):
        return dict(id=id,question=question,status=status,answer=answer,evidence_refs=refs(items),measurements=list(measurements))
    result=[item('benefit','已测停车措施能否在约束内改善交通？',
                 'supported' if valid else 'not_supported' if measured else 'unmeasured',
                 f"{len(valid)}项措施在全部约定需求下通过约束。" if valid else
                 '目前已测完整措施均未通过约束，不能外推为所有治理均无效。' if measured else '尚无完成全部需求配对的治理措施。',measured)]
    losses=[]
    by_name={r['name']:r for r in state.get('runs',[]) if r.get('execution',{}).get('status')=='completed'}
    for policy in policies:
        for check in policy['checks']:
            run=by_name[check['run']]
            core=run.get('comparison',{}).get('delta',{}).get('core',{}).get('running_time_change_pct')
            if core is not None and core<0 and run.get('comparison',{}).get('eligible') is False:
                losses.append(dict(run=run['name'],factor=check['factor'],core_time_pct=core,violations=check['violations']))
    result.append(item('tradeoff','局部改善是否伴随不能接受的代价？',
                       'observed' if losses else 'not_observed' if measured else 'unmeasured',
                       f"{len(losses)}个工况虽有核心收益，仍未通过全部约束。" if losses else
                       '已测完整措施中未发现此类反例；仍受当前阈值和需求范围限制。' if measured else '等待治理配对，不能由基准推断代价。',
                       policies if losses else measured,losses))
    # Strength comparison keeps exactly the same treated directions and factors.
    pairs={}
    for p in measured:
        levels={c.get('parking') for c in p['changes']}
        if levels in ({1},{2}):pairs.setdefault(footprint(p['changes']),{})[next(iter(levels))]=p
    contrasts=[];pair_items=[]
    for directions,pair in pairs.items():
        if set(pair)!={1,2}:continue
        admissible=[p for p in pair.values() if p['feasible']]
        comparison=select(admissible) if selection is not None else None
        preferred=next((level for level,p in pair.items() if comparison and p['name']==comparison['recommendation']),None)
        contrasts.append(dict(directions=[list(d) for d in directions],preferred_level=preferred,
                              levels=[dict(level=level,run=p['name'],feasible=p['feasible'],worst=p['worst']) for level,p in sorted(pair.items())]))
        pair_items.extend(pair.values())
    result.append(item('strength','更强的停车1级是否比2级更合适？',
                       'measured' if contrasts else 'unmeasured',
                       f"完成{len(contrasts)}组同方向、全需求强度对照；按固定约束和容差分别筛选，不能假定强度与收益单调。" if contrasts else '未完成同方向的1/2级完整配对，不能比较强弱优劣。',pair_items,contrasts))
    regions=state.get('regions',[])
    regional=[]
    for p in measured:
        area=next((a for a in regions if set(footprint(p['changes']))<=set(map(tuple,a['directions']))),None)
        if area:regional.append(dict(region=area['id'],title=area['title'],run=p['name'],levels=sorted({c['parking'] for c in p['changes']}),feasible=p['feasible']))
    compared=len({p['region'] for p in regional})>=2
    result.append(item('regions','不同区域的已测措施有什么取舍？','measured' if compared else 'unmeasured',
                       '已测至少两个区域；若等级或路段数不同，不能把效果差异单独归因为位置。独立试验也不代表联合实施效果。' if compared else '尚未实测两个不同区域，不能比较位置优劣。',
                       measured if compared else (),regional if compared else ()))
    robust=len(analysis['factors'])>=2
    result.append(item('robustness','同一措施在约定需求波动下是否仍可用？',
                       'supported' if robust and valid else 'not_supported' if robust and measured else 'unmeasured',
                       f"{len(valid)}项措施通过全部{len(analysis['factors'])}个离散需求；不推断未测需求。" if robust and valid else
                       '已测完整措施没有全部需求均合格者。' if robust and measured else '尚无完整的多需求治理证据；单一需求不能证明稳健性。',
                       measured if robust else ()))
    return dict(version=VERSION,findings=result,preferred_tested_policy=selected,
                selection_basis='按预先冻结的约束和容差，仅筛选已完成的同一措施。',
                scope='仅本任务本次已完成配对；不认证任意文字假设，不外推现实因果或未测方案。')


def validate_updates(updates,hypotheses,evidence,final=False,*,measured_only=False,reference_schema=LEGACY_REFERENCE_SCHEMA):
    """Check references, coverage and frozen predicates; legacy prose stays legacy."""
    from .planning_response import ResponseValidationError
    if reference_schema not in REFERENCE_SCHEMAS:raise ValueError('未知的假设依据引用协议')
    if not isinstance(updates,list) or len(updates)>len(hypotheses):
        raise ResponseValidationError('hypothesis_list', '假设复核格式无效')
    ids={h['id'] for h in hypotheses};seen=set()
    facts={f['id']:f for f in evidence['findings']}
    for update in updates:
        if not isinstance(update,dict) or set(update)!={'hypothesis_id','judgment','finding_ids','explanation'}:
            raise ResponseValidationError('hypothesis_fields', '假设复核字段无效')
        id=update['hypothesis_id']
        if not isinstance(id,str) or id not in ids or id in seen:raise ResponseValidationError('hypothesis_id', '假设引用无效或重复')
        seen.add(id)
        if update['judgment'] not in ('supported','contradicted','unresolved'):raise ResponseValidationError('hypothesis_judgment', '假设判断类型无效')
        selected=update['finding_ids']
        if not isinstance(selected,list) or len(selected)>5 or any(not isinstance(k,str) or k not in facts for k in selected):raise ResponseValidationError('finding_refs', '检验结果引用无效')
        if (measured_only or reference_schema==REFERENCE_SCHEMA) and any(k not in measured_finding_ids(evidence) for k in selected):
            raise ResponseValidationError('unmeasured_finding', '判断依据只能引用已测检验；未测结果是证据缺口，应写入解释，不能混入finding_ids')
        if reference_schema==REFERENCE_SCHEMA:
            allowed=hypothesis_finding_ids(next(h for h in hypotheses if h['id']==id),evidence,reference_schema)
            if len(set(selected))!=len(selected) or any(k not in allowed for k in selected):
                raise ResponseValidationError('finding_scope', '判断依据必须属于本假设的冻结配对检验，不能附加其他问题的结果或重复引用')
        if update['judgment']!='unresolved' and (not selected or any(facts[k]['status']=='unmeasured' or not facts[k]['evidence_refs'] for k in selected)):
            raise ResponseValidationError('unmeasured_finding', '未测结果不能支持或否定假设')
        from .llm_client import substantive_text
        if not substantive_text(update['explanation']) or not 1<=len(update['explanation'])<=400:raise ResponseValidationError('hypothesis_explanation', '假设解释无效')
        from .hypothesis_contract import validate_update
        validate_update(update,next(h for h in hypotheses if h['id']==id),facts)
    if final and seen!=ids:raise ResponseValidationError('hypothesis_coverage', '最终复核遗漏初始假设，研究未完成')


def compact_findings(state):
    evidence=findings(state)
    # Numeric details are already in the actual observations; avoid sending
    # duplicate matrices on every paid request. Reports retain the full tables.
    return dict(evidence,findings=[{k:v for k,v in f.items() if k!='measurements'} for f in evidence['findings']])


def latest_review(state):
    from .study_handoff import closes_study
    from .review_scope import programme_only
    evidence=findings(state)
    if programme_only(state):
        last=next((t for t in reversed(state.get('planner_trace', [])) if t.get('status')=='accepted'
                   and closes_study(t['action'], t.get('input', {}).get('context', {}))), None)
        return dict(evidence=evidence, hypotheses=[], updates=[], reviewed=bool(last), review_scope=state['review_scope'])
    last=next((t for t in reversed(state.get('planner_trace',[])) if t.get('status')=='accepted' and 'hypothesis_updates' in t),None)
    return dict(evidence=evidence,hypotheses=state.get('research_hypotheses',[]),
                updates=last['hypothesis_updates'] if last else [],
                reviewed=bool(last and closes_study(last['action'],last.get('input',{}).get('context',{}))))
