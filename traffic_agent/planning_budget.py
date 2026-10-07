"""Request reservations for complete studies, including review and routing.

The model still chooses the first study and each feasible subsequent study.
An unaffordable next step is deferred, never removed from the programme.
"""
import copy
import re

from .planning_review import required_policies

LEGACY_VERSION='study-routing-budget-v1'
VERSION='study-routing-budget-v2'
LEGACY_INITIAL_VERSION='initial-plan-budget-v1'
V2_INITIAL_VERSION='initial-plan-budget-v2'
INITIAL_VERSION='initial-plan-budget-v3'


def component_omission(index,tasks,entries,request):
    """Permission to withdraw an extra draft, never reuse another engine's output.

    Ambiguous intent stays protected. Only an explicit named-road request for
    a complete combination can cover its unrequested standalone parking draft.
    """
    from .llm_settings import redact
    task=tasks[index];entry=entries[index];road=task.get('road_name')
    if (task['scenario']!='parking' or entry['study']!='standard' or not road
            or entry['target_ref']!='auto' or task.get('search_mode','standard')!='standard'
            or required_policies(task)!=1):return None
    # Explicit separate tests/engines, sequencing, or intensity comparisons are
    # research questions of their own; a factorial parking cell cannot replace them.
    if re.search(r'(?:单独|独立|额外|另外|另做|另行|再研究|再评估|还要|另加)[^。；;\n]{0,14}停车|停车[^。；;\n]{0,14}(?:单独|独立|另做|另行)|'
                 r'基础分配|Vehicle|引擎|适配器|先停车|先治理停车|停车后',request,re.I):return None
    road_names={t.get('road_name') for t in tasks if t.get('road_name')}
    shared=('demand_factors','repeat_baseline','max_links','neighbor_hops','max_neighbor_time_increase_pct',
            'max_network_time_increase_pct','max_new_oversaturated_km','max_new_network_oversaturated_km')
    for j,(other,definition) in enumerate(zip(tasks,entries)):
        if (other['scenario'] not in ('oneway','circulation') or definition['study']!='standard'
                or other.get('road_name')!=road or any(task.get(k)!=other.get(k) for k in shared)):continue
        control='单向' if other['scenario']=='oneway' else '禁转'
        for match in re.finditer(re.escape(road),request):
            clause=re.split(r'[。；;\n]',request[match.start():],maxsplit=1)[0]
            following=[clause.find(name,len(road)) for name in road_names if name!=road]
            end=min([p for p in following if p>=0]+[len(clause)])
            quote=clause[:end]
            if len(quote)>180 or not all(word in quote for word in (control,'停车','组合')):continue
            if re.search(r'不|别|勿|无(?:需|须)|暂缓|排除|禁止|拒绝',quote):continue
            return dict(kind='extra_factorial_component',covered_by_ordinal=j+1,source_quote=redact(quote),
                reason='原文要求该道路的完整组合研究；本组是额外独立停车提议。可由模型撤回，组合组仍保留自己的新基准、停车、路权及组合四项对照；不借用本组结果。')
    return None


def group_requests(task,following=False,version=VERSION):
    if task['scenario']=='parking':
        return required_policies(task)+1+int(following and version==LEGACY_VERSION)
    return 2 if task['scenario']=='oneway' else 1  # Target+review/route, or review/route only.


def minimum_remaining(tasks,version=VERSION):
    if not tasks:return 0
    parking=sum(t['scenario']=='parking' for t in tasks)
    return sum(group_requests(t,version=version) for t in tasks)+(max(0,parking-1) if version==LEGACY_VERSION else 0)


def starting_with(tasks,index=0,version=VERSION):
    if not tasks:return 0
    remaining=[t for i,t in enumerate(tasks) if i!=index]
    return group_requests(tasks[index],bool(remaining),version)+minimum_remaining(remaining,version)


def reserved_after_current(task,pending,version=VERSION):
    return minimum_remaining(pending,version)+int(version==LEGACY_VERSION and task['scenario']=='parking' and bool(pending))


def route_guard(studies,remaining_before_decision,version=VERSION):
    available=max(0,remaining_before_decision-1)  # This route/review is also chargeable.
    tasks=[s['task'] for s in studies]
    choices={s['id']:dict(title=s['title'],required_requests=starting_with(tasks,i,version),
                           feasible=starting_with(tasks,i,version)<=available) for i,s in enumerate(studies)}
    return dict(version=version,requests_after_this_decision=available,
                minimum_remaining_requests=minimum_remaining(tasks,version),choices=choices,
                note='暂不开放的组仍须完成；只限制本轮顺序，为后续必需实验和复核保留请求。')


def initial_costs(tasks,entries,config,used,request,*,version=INITIAL_VERSION):
    """Price a validated draft; never turn the lower bound into an execution plan.

    A correction may withdraw explicitly marked extra drafts. Required studies
    keep their conditions and independent baselines; no output is shared.
    """
    from .feedback_planner import REQUEST_LIMIT,LEGACY_REQUEST_LIMIT
    from .planning_studies import minimum_runs
    if version not in (INITIAL_VERSION,V2_INITIAL_VERSION,LEGACY_INITIAL_VERSION):raise ValueError('未知初始预算协议')
    request_limit=REQUEST_LIMIT if version==INITIAL_VERSION else LEGACY_REQUEST_LIMIT
    groups=[]
    for i,(task,entry) in enumerate(zip(tasks,entries),1):
        supplementary=(task['scenario']=='diagnosis' and not config['diagnosis_only']
                       and not re.search(r'体检|诊断|现状',request)
                       and any(t['scenario']!='diagnosis' and t['road_name']==task['road_name']
                               and set(task['demand_factors'])<=set(t['demand_factors']) for t in tasks))
        conditions={k:copy.deepcopy(v) for k,v in task.items() if k not in ('request','max_runs')}
        groups.append(dict(ordinal=i,title=entry['title'],study=entry['study'],target_ref=entry['target_ref'],
                           conditions=conditions,native_runs=minimum_runs(task),model_requests=group_requests(task),
                           assignment_module='M6AssignVehicle1.exe' if task['scenario'] in ('oneway','circulation') else 'M6AssignVehicle.exe',
                           may_omit=supplementary))
        if version in (V2_INITIAL_VERSION,INITIAL_VERSION):
            omission=component_omission(i-1,tasks,entries,request)
            if omission:groups[-1].update(may_omit=True,omission=omission)
    retained=[t for t,g in zip(tasks,groups) if not g['may_omit']]
    native=sum(g['native_runs'] for g in groups)
    model=used+starting_with(tasks)
    fits=native<=config['total_runs'] and model<=request_limit
    can_correct=(not fits and used==1 and bool(retained)
                 and sum(minimum_runs(t) for t in retained)<=config['total_runs']
                 and used+1+minimum_remaining(retained)<=request_limit)
    rule='所有治理设计与必需体检保持原条件；仅可调整顺序或撤回未要求且已有同范围治理基准的额外体检。不同引擎仍各跑新基准，不借用体检结果。'
    if version in (V2_INITIAL_VERSION,INITIAL_VERSION):
        rule='保护必需研究及原条件。仅可由模型撤回明确标记的额外体检，或原文只要求完整组合且未要求独立比较的额外停车提议；组合四项对照仍需独立新基准。不会自动删组、合并引擎或共享结果。'
    result=dict(version=version,groups=groups,
                native=dict(required=native,limit=config['total_runs']),
                model=dict(required=model,used=used,limit=request_limit),
                fits=fits,correction_feasible=can_correct,
                rule=rule)
    if config.get('budget_parse_protocol'):result['budget_parse_protocol']=config['budget_parse_protocol']
    return result


def verify_budget_revision(first,current):
    """A fresh reply cannot buy affordability by weakening an accepted design."""
    def signature(group):
        return {k:group[k] for k in ('study','target_ref','conditions')}
    available=[signature(g) for g in first['groups']]
    revised=[signature(g) for g in current['groups']]
    protected=[signature(g) for g in first['groups'] if not g['may_omit']]
    if (any(g not in available for g in revised) or any(g not in revised for g in protected)
            or current['version']!=first['version']
            or current.get('budget_parse_protocol')!=first.get('budget_parse_protocol')
            or current['native']['limit']!=first['native']['limit']
            or current['model']['limit']!=first['model']['limit']
            or current['model']['used']!=first['model']['used']+1):
        raise ValueError('预算修正改变了需保留的研究、条件或预算；未启动仿真')


def verify_initial_costs(check,request):
    """Recalculate archived costs/omission permissions instead of trusting flags."""
    from .natural_plan import limits,LEGACY_BUDGET_PARSE
    groups=check['groups']
    tasks=[dict(g['conditions'],request=request) for g in groups]
    entries=[{k:g[k] for k in ('study','title','target_ref')} for g in groups]
    config=limits(request,budget_protocol=check.get('budget_parse_protocol',LEGACY_BUDGET_PARSE))
    if check!=initial_costs(tasks,entries,config,check['model']['used'],request,version=check['version']):
        raise ValueError('初始规划成本与实际研究条件不符')
