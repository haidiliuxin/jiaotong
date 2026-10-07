"""Describe what a proposed experiment changes, without choosing it.

The reference is the latest completed trial already present in this decision.
Relations describe input differences, never effects or a causal mechanism.
"""
import json
import math


def valid_factor(value):
    return type(value) in (int,float) and math.isfinite(value) and value>0


def demand_design(payload,option,planned,factor,recent_ref):
    """Keep planned conditions separate from completed observations in this group."""
    factors=sorted(set(planned)) if (isinstance(planned,list) and planned and all(valid_factor(f) for f in planned)) else None
    reference=factor if valid_factor(factor) else None
    same=(reference in factors) if reference is not None and factors is not None else None
    owner=recent_ref.rsplit(':',1)[0]
    observed=[o for o in payload['observations'] if isinstance(o.get('ref'),str)
              and o['ref'].rsplit(':',1)[0]==owner]
    proposed=signature(option.get('changes',[]));conditions=[]
    for f in factors or []:
        baselines=[o['ref'] for o in observed if o.get('demand_factor')==f and not o.get('changes')
                   and o.get('run')!='baseline_repeat']
        trials=[o for o in observed if o.get('demand_factor')==f and proposed is not None
                and signature(o.get('changes',[]))==proposed]
        paired=[o['ref'] for o in trials if isinstance(o.get('delta'),dict) and o['delta']]
        conditions.append(dict(demand_factor=f,baseline_refs=baselines,
            candidate_result_refs=[o['ref'] for o in trials],paired_metric_refs=paired,
            result_status='candidate_parameters_unknown' if proposed is None else
                          'paired_metrics_provided' if paired else 'observed_without_pair' if trials else 'no_completed_result'))
    return dict(planned_factors=factors,reference_factor=reference,includes_reference_factor=same,
                candidate_parameters_known=proposed is not None,conditions=conditions)


def demand_summary(design):
    number=lambda n:str(n).removesuffix('.0')
    factors=design['planned_factors'];reference=design['reference_factor']
    text='计划需求'+('×'+'、×'.join(number(f) for f in factors) if factors is not None else '未提供')
    text+='；参照需求'+('×'+number(reference) if reference is not None else '未提供')+'。'
    same=design['includes_reference_factor']
    text+=('计划包含参照需求；已有结果另列。' if same is True else
           '计划未包含参照需求；不能作该需求对照。' if same is False else '是否包含参照需求未知。')
    for c in design['conditions']:
        text+='需求×'+number(c['demand_factor'])+'：'+('基准已提供；' if c['baseline_refs'] else '本轮未提供同需求基准；')
        if not design['candidate_parameters_known']:text+='候选参数不完整，无法核对已有结果。'
        elif c['result_status']=='no_completed_result':text+='尚无该候选的已完成结果。'
        elif c['result_status']=='observed_without_pair':text+='已提供候选结果，未提供配对指标。'
        else:text+='已提供候选结果及配对指标；不代表收益或约束通过。'
    return text


def signature(changes):
    result={}
    for c in changes:
        if not isinstance(c,dict) or any(type(c.get(k)) is not int for k in ('from_node','to_node','parking')):
            return None
        key=(c['from_node'],c['to_node'])
        if key in result or c['parking'] not in (1,2):return None
        result[key]=c['parking']
    return result or None


def relations(before,after):
    if not before or not after:return 'unknown','unknown'
    old=set(before);new=set(after)
    direction=('same' if old==new else 'narrower' if new<old else 'broader' if old<new
               else 'relocated' if not old&new else 'partly_changed')
    old_levels=set(before.values());new_levels=set(after.values())
    if len(old_levels)==len(new_levels)==1:
        a=next(iter(old_levels));b=next(iter(new_levels))
        level='same' if a==b else 'weaker' if b>a else 'stronger'
    elif direction=='same' and before==after:level='same'
    else:level='mixed'
    return direction,level


def interpretation(direction,level,matching_demand):
    direction_labels=dict(same='治理方向相同',narrower='缩小治理范围',broader='扩大治理范围',
                          relocated='换到不同治理方向',partly_changed='部分治理方向改变',unknown='治理方向资料不完整')
    level_labels=dict(same='等级不变',weaker='治理等级减弱',stronger='治理等级增强',
                      mixed='等级变化不能归为统一强弱',unknown='等级资料不完整')
    text=direction_labels[direction]+'；'+level_labels[level]+'。'
    if matching_demand is not True:
        return text+('本动作未安排参照试验的需求条件，不能作同需求方案对照。' if matching_demand is False
                     else '本轮未提供完整执行需求定义，是否能作同需求对照仍未知。')
    if 'unknown' in (direction,level):return text+'资料不足，不能确定这次对照可分离的变化。'
    if direction=='same' and level=='same':return text+'该需求下措施参数重复，不能当成另一个不同候选。'
    if direction=='same':return text+'可比较同方向的等级调整；实际收益和代价仍需实跑，不证明现实机制。'
    if level=='same':return text+'可比较同等级的治理范围或方向组合；不能分离其中单一路段的贡献。'
    return text+'可比较两个完整方案，但位置／范围和等级同时改变，不能单独归因于其中一项。'


def build(payload,choices,recent_ref,include_execution_evidence=False):
    if payload['phase']!='parking' or not recent_ref or not choices:return None
    trial=next((o for o in payload['observations'] if o['ref']==recent_ref),None)
    if not trial or not trial.get('changes') or not trial.get('delta'):return None
    before=signature(trial['changes']);factor=trial.get('demand_factor')
    execution=payload['context'].get('action_execution',{}).get('actions',{})
    groups={};designs={}
    for action,option in choices.items():
        direction,level=relations(before,signature(option.get('changes',[])))
        planned=execution.get(action,{}).get('demand_factors')
        known=(isinstance(planned,list) and bool(planned) and type(factor) in (int,float)
               and all(type(f) in (int,float) for f in planned))
        matching=(factor in planned) if known else None
        key=(direction,level,matching)
        value=dict(direction_relation=direction,level_relation=level,
            matching_demand=matching,actions=[],interpretation=interpretation(*key))
        if include_execution_evidence:
            design=demand_design(payload,option,planned,factor,recent_ref)
            design_key=json.dumps(design,sort_keys=True,allow_nan=False)
            shared=designs.setdefault(design_key,dict(id='demand_'+str(len(designs)+1),**design,summary=demand_summary(design)))
            key=(direction,level,design_key)
            value.pop('matching_demand')
            value.update(demand_design_id=shared['id'],
                interpretation=interpretation(direction,level,design['includes_reference_factor']))
        group=groups.setdefault(key,value)
        group['actions'].append(action)
    reference_factor=factor if not include_execution_evidence or valid_factor(factor) else None
    result=dict(reference_ref=recent_ref,reference_demand_factor=reference_factor,groups=list(groups.values()),
        scope='仅比较本轮动作参数与最近已完成试验；不排序、不预测效果。保持同组冻结评价范围；尚未执行的候选仍待验证。')
    if include_execution_evidence:result['demand_designs']=list(designs.values())
    return result


def selected(entry):
    """Identical structured descriptions for the panel and the saved report."""
    if entry.get('status')!='accepted' or not entry.get('selection_basis'):return None
    relation=entry.get('input',{}).get('context',{}).get('choice_observations',{}).get('recent_comparisons')
    if not relation:return None
    payload=entry['input'];reference=next((o for o in payload['observations'] if o['ref']==relation['reference_ref']),{})
    previous=signature(reference.get('changes',[]))
    result=dict(reference_ref=relation['reference_ref'],reference_demand_factor=relation['reference_demand_factor'],
                reference_direction_count=len(previous) if previous else None,scope=relation['scope'])
    for role,action in (('chosen',entry['action']),('alternative',entry['selection_basis'].get('alternative_action'))):
        proposed=signature(payload['allowed_actions'].get(action,{}).get('changes',[]))
        result[role]=next((dict(g,action=action,direction_count=len(proposed) if proposed else None)
                          for g in relation['groups'] if action in g['actions']),None)
        if result[role]:
            design=next((d for d in relation.get('demand_designs',[]) if d['id']==result[role].get('demand_design_id')),None)
            if design:result[role].update(demand_design=design,demand_summary=design['summary'])
    return result
