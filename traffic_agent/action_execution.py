"""One source for an action's native steps and its public follow-up description.

The model selects an action and explains its research choice. It does not have
to transcribe the executor's fixed levels, order, or scene/request budgets.
Legacy payloads without this protocol retain their original prose interface.
"""
import copy

LEGACY_VERSION='action-execution-v1'
VERSION='action-execution-v2'
SUPPORTED_VERSIONS=(LEGACY_VERSION,VERSION)
PARKING_LEVEL=2
CONTROL={'oneway':('S2_oneway','单向'),'circulation':('S2_turn','禁转')}


def factors(task):
    return [1.0]+[f for f in task['demand_factors'] if f!=1]


def prefix(factor):
    return '' if factor==1 else 'demand_'+format(factor,'.3f').replace('.','p')+'_'


def factorial_policies(scenario,selected,turn=None):
    parking=[dict(from_node=r['from_node'],to_node=r['to_node'],old_parking=r['parking'],parking=PARKING_LEVEL)
             for r in selected]
    label,_=CONTROL[scenario]
    control=(dict(kind='oneway',closed_direction=[selected[0]['from_node'],selected[0]['to_node']])
             if scenario=='oneway' else dict(kind='turn',turn=copy.deepcopy(turn)))
    return {'S1_parking':parking,label:[control],'S3_combined':parking+[control]}


def factorial_sequence(task,baseline_done=False):
    label,control=CONTROL[task['scenario']]
    sequence=[]
    for factor in factors(task):
        cells=[('baseline','无治理基准',None),('S1_parking','停车',PARKING_LEVEL),
               (label,control,None),('S3_combined','停车＋'+control,PARKING_LEVEL)]
        for name,policy,level in cells:
            if baseline_done and factor==1 and name=='baseline':continue
            sequence.append(dict(name=prefix(factor)+name,policy=policy,demand_factor=factor,parking_level=level))
    if task.get('repeat_baseline'):
        sequence.append(dict(name='baseline_repeat',policy='复跑无治理基准',demand_factor=1.0,parking_level=None))
    return sequence


def study_action(task):
    scenario=task['scenario']
    item=dict(kind='start_study',scenario=scenario,independent_baseline=True,
              demand_factors=factors(task),native_scene_limit=task['max_runs'])
    if scenario in CONTROL:
        item.update(sequence=factorial_sequence(task),
            target_selection='model_after_fresh_baseline' if scenario=='oneway' else 'explicit_turn_after_validation')
        if scenario=='circulation':item['turn']=copy.deepcopy(task.get('turn'))
    elif scenario=='parking':
        from .planning_review import required_policies
        item.update(mode='adaptive_candidates',minimum_candidates=required_policies(task),
                    parking_level_selected_by_model=True,repeat_baseline=bool(task.get('repeat_baseline')))
    else:item.update(mode='diagnosis',repeat_baseline=bool(task.get('repeat_baseline')))
    return item


def native_budget(state):
    used=state['run_count'];limit=state['task']['max_runs']
    completed=sum(r.get('execution',{}).get('status')=='completed' for r in state.get('runs',[]))
    return dict(unit='native_scene',limit=limit,used=used,completed=completed,remaining=max(0,limit-used))


def contract(state,options,studies=(),states=None,*,version=VERSION):
    if version not in SUPPORTED_VERSIONS:raise ValueError('未知动作执行协议')
    task=state['task'];plans={};by_id={s['id']:s for s in studies}
    for action,option in options.items():
        if action=='stop':plans[action]=dict(kind='finish_review',native_scenes=0)
        elif action in by_id:plans[action]=study_action(by_id[action]['task'])
        elif 'closed_direction' in option:
            pair=copy.deepcopy(option['closed_direction'])
            plans[action]=dict(kind='factorial_target',scenario='oneway',closed_direction=pair,
                retained_direction=pair[::-1],sequence=factorial_sequence(task,baseline_done=True),
                next_model_decision='after_complete_matched_grid')
        elif option.get('changes'):
            tested_factors=factors(task) if state.get('model_grid') else [1.0]
            plans[action]=dict(kind='parking_candidate',demand_factors=tested_factors,
                parking_levels=sorted({c['parking'] for c in option['changes']}),
                native_scenes=len(tested_factors),next_model_decision='after_candidate_results')
        else:raise ValueError('动作缺少可重建的实际执行定义')
    budgets=dict(current_study=native_budget(state))
    if studies and states is not None:
        rows=[native_budget(states[s['job_id']]) for s in studies]
        budgets['programme_allocated']=dict(unit='native_scene',**{
            k:sum(r[k] for r in rows) for k in ('limit','used','completed','remaining')})
    return dict(version=version,native_budget=budgets,actions=plans)


def attach(agent,options,context,allow_stop,future=(),planner=None):
    planner=planner or agent.planner
    studies=planner.programme_plan['studies'] if planner.programme_plan else list(future)
    menu={**({'stop':{}} if allow_stop else {}),**options}
    context['action_execution']=contract(agent.state,menu,studies,planner.programme_states if planner.programme_plan else None)
    agent.state['action_execution_protocol']=VERSION


def description(item):
    kind=item['kind']
    if kind=='finish_review':return '不再追加原生场景；依据已经返回的观测复核结论、代价与未验证范围。'
    if kind=='parking_candidate':
        levels='/'.join(map(str,item['parking_levels']));demand='/'.join(f'{f:g}' for f in item['demand_factors'])
        return f'在所选方向执行停车{levels}级，需求系数{demand}，共{item["native_scenes"]}个治理场景；结果返回后再由模型选择下一步。'
    scenario=item['scenario']
    if scenario in CONTROL:
        control=CONTROL[scenario][1]
        lead=('下一组先建立独立新基准，再由模型选择合法方向。' if scenario=='oneway' else '下一组建立独立新基准并核验指定转向。') if kind=='start_study' else ''
        direction=('禁行'+'→'.join(map(str,item['closed_direction']))+'，保留'+'→'.join(map(str,item['retained_direction']))+'；') if kind=='factorial_target' else ''
        return (lead+direction+f'每个需求依次计算停车{PARKING_LEVEL}级、{control}、停车{PARKING_LEVEL}级＋{control}；'
                f'本动作安排共{len(item["sequence"])}个原生场景（含所需基准／复跑），全部返回后由模型复核。尚无这些新场景的效果。')
    demand='/'.join(f'{f:g}' for f in item['demand_factors'])
    if scenario=='parking':
        return f'下一组建立独立新基准（需求系数{demand}），由模型按新观测选择位置、范围和等级；至少比较{item["minimum_candidates"]}个候选，上限{item["native_scene_limit"]}个场景。'
    return f'下一组在需求系数{demand}计算独立新基准，仅诊断现状；上限{item["native_scene_limit"]}个场景。'


def generated_follow_up(entry):
    spec=entry['input']['context'].get('action_execution')
    if spec is None:return None
    if spec.get('version') not in SUPPORTED_VERSIONS:raise ValueError('未知动作执行协议')
    return description(spec['actions'][entry['action']])


def generated_names(payload):
    spec=payload.get('context',{}).get('action_execution')
    if spec is None:return ()
    if spec.get('version') not in SUPPORTED_VERSIONS:raise ValueError('未知动作执行协议')
    return ('next_check','summary') if spec['version']==VERSION else ('next_check',)


def generated_values(entry):
    """Describe the selected menu action, never infer tense from model prose."""
    names=generated_names(entry['input'])
    if not names:return {}
    values=dict(next_check=generated_follow_up(entry))
    if 'summary' not in names:return values
    item=entry['input']['context']['action_execution']['actions'][entry['action']]
    option=entry['input']['allowed_actions'][entry['action']]
    if item['kind']=='parking_candidate':
        directions='、'.join(f'{c["from_node"]}→{c["to_node"]}（停车{c["parking"]}级）' for c in option['changes'])
        summary=f'本轮选择待执行：{directions}；效果等待本次新仿真。'
    elif item['kind']=='factorial_target':
        closed='→'.join(map(str,item['closed_direction']));retained='→'.join(map(str,item['retained_direction']))
        summary=f'本轮选择单向对照：禁行{closed}，保留{retained}；效果等待停车、单向及组合的新仿真。'
    elif item['kind']=='start_study':
        label={'parking':'停车候选','oneway':'停车与单向','circulation':'停车与禁转','diagnosis':'路网诊断'}[item['scenario']]
        summary=f'本轮选择下一组{label}研究；先计算该组独立新基准。'
    else:summary='本轮选择结束新增试验，复核已有观测和未验证范围。'
    values['summary']=summary
    return values


def reject_generated(entry):
    """Keep a rejected definition with its proposed action, not terminal stop."""
    spec=entry['input'].get('context',{}).get('action_execution',{})
    if spec.get('version')!=VERSION or entry.get('summary_origin')!=VERSION:return
    values=generated_values(entry)
    entry['proposed_execution']=dict(version=VERSION,action=entry['action'],**values)
    for field in values:
        entry.pop(field,None);entry.pop(field+'_origin',None)


def verify_entry(entry):
    spec=entry['input']['context'].get('action_execution')
    if spec is None:return
    if spec.get('version') not in SUPPORTED_VERSIONS or set(spec.get('actions',{}))!=set(entry['input']['allowed_actions']):
        raise ValueError('动作执行定义与菜单权限不符')
    if entry['status']=='accepted' and entry.get('action') not in spec['actions']:
        raise ValueError('所选动作不在执行定义内')
    if entry['status']=='accepted':
        for field,value in generated_values(entry).items():
            if entry.get(field+'_origin')!=spec['version'] or entry.get(field)!=value:
                raise ValueError('动作执行说明不符或冒充模型原话')
    proposed=entry.get('proposed_execution')
    if proposed is not None:
        raw=entry.get('proposed_decision') or entry.get('proposal_excerpt') or {}
        if (entry['status']!='rejected' or spec['version']!=VERSION or proposed.get('action')!=raw.get('action')
                or proposed.get('action') not in spec['actions']):
            raise ValueError('被拒动作定义缺少原始提议来源')
        expected=dict(version=VERSION,action=proposed['action'],
            **generated_values(dict(input=entry['input'],action=proposed['action'])))
        if proposed!=expected:raise ValueError('被拒动作执行说明与原提议不符')


def verify_context(state,payload,studies=None,states=None):
    spec=payload['context'].get('action_execution')
    if spec is None:
        if state.get('action_execution_protocol'):raise ValueError('缺少冻结的动作执行定义')
        return
    version=state.get('action_execution_protocol',spec.get('version'))
    if version!=spec.get('version'):raise ValueError('动作执行协议与冻结任务不符')
    if studies is None:
        # Programme verification separately binds future-study definitions and
        # total allocations to the frozen programme and every group's prefix.
        options={k:v for k,v in payload['allowed_actions'].items() if not k.startswith('study_')}
        expected=contract(state,options,version=version)
        actual=dict(version=spec['version'],native_budget=dict(current_study=spec['native_budget']['current_study']),
                    actions={k:v for k,v in spec['actions'].items() if k in options})
    else:
        expected=contract(state,payload['allowed_actions'],studies,states,version=version)
        actual=spec
    if actual!=expected:raise ValueError('动作等级、顺序或原生预算与实际执行定义不符')


def capture_schema(payload,schema):
    """Keep unexpected public prose, without dropping it to make a reply pass."""
    from .risk_review import capture_schema as risk_capture_schema
    schema=risk_capture_schema(payload,schema)
    if not payload['context'].get('action_execution'):return schema
    schema=copy.deepcopy(schema)
    for field in generated_names(payload):schema['properties'][field]=dict(type='string')
    return schema


def wire_contract(spec):
    """Share exactly equal action fields; never drop parameters or evidence."""
    result=copy.deepcopy(spec);rows=list(result['actions'].values())
    if len(rows)<2:return result
    common={k:copy.deepcopy(v) for k,v in rows[0].items() if k!='kind'
            and all(k in r and r[k]==v for r in rows[1:])}
    if common:
        result['shared_action_fields']=common
        for row in rows:
            for key in common:del row[key]
    return result
