"""Public experiment rationale tied to the current observed candidate menu.

References point at fields already supplied in the prompt. Values are displayed
from those fields, not repeated or retyped by the model. Hypotheses and proposed
falsifiers stay explicitly unverified. Legacy decisions retain their contract.
"""
from .llm_client import substantive_text
from .planning_response import ResponseValidationError

LEGACY_VERSION = 'observed-choice-basis-v1'
V2_VERSION = 'observed-choice-basis-v2'
V3_VERSION = 'observed-choice-basis-v3'
V4_VERSION = 'observed-choice-basis-v4'
V5_VERSION = 'observed-choice-basis-v5'
V6_VERSION = 'observed-choice-basis-v6'
VERSION = 'observed-choice-basis-v7'
SUPPORTED_VERSIONS = (LEGACY_VERSION, V2_VERSION, V3_VERSION, V4_VERSION, V5_VERSION, V6_VERSION, VERSION)
UNITS = dict(speed_kmh='km/h', vc='ratio', flow_pcu_h='pcu/h', baseline_car_flow='veh/h',
             running_time_pcu_h='pcu·h', time_change_pct='%', new_neighbor_oversaturated_km='km',
             max_extra_km='km', parking='level')


def actions(payload):
    phase=payload['phase']
    return {k:v for k,v in payload['allowed_actions'].items() if
            (phase=='parking' and v.get('changes')) or (phase=='oneway_target' and v.get('closed_direction'))}


def _pairs(changes):
    return {(c['from_node'],c['to_node']) for c in changes
            if isinstance(c,dict) and type(c.get('from_node')) is int and type(c.get('to_node')) is int}


def _related_trial_actions(payload, trial, choices):
    """A measured trial can inform a different action in its measured region.

    The relation supports comparing options; it never labels the other action
    as executed and never replaces the chosen action's own baseline citation.
    """
    changed=_pairs(trial.get('changes',[]))
    if not changed:return []
    regions=set()
    checked={c.get('region') for c in trial.get('regional_checks',[]) if isinstance(c,dict)}
    for baseline in payload['observations']:
        if (baseline.get('changes') or baseline.get('run')=='baseline_repeat'
                or baseline.get('demand_factor')!=trial.get('demand_factor')):
            continue
        for region in baseline.get('spatial',{}).get('regions',[]):
            if region.get('id') in checked and changed <= _pairs(region.get('roads',[])):
                regions.add(region['id'])
    related=[]
    for key,choice in choices.items():
        region=choice.get('region')
        if region is not None:
            if region in regions:related.append(key)
        else:
            proposed=_pairs(choice.get('changes',[]))
            if proposed and proposed <= changed:related.append(key)
    return related


def observations(payload):
    """Small reference index; all metric values remain in their original fields."""
    version=payload.get('context',{}).get('choice_basis_protocol') or VERSION
    if version not in SUPPORTED_VERSIONS:raise ValueError('未知的选点观测协议')
    choices=actions(payload);sources=[];recent=None
    for o in payload['observations']:
        ref=o['ref']
        if o.get('changes'):
            if choices and o.get('delta'):
                recent='trial:'+ref
                source=dict(id=recent,kind='trial',ref=ref,actions=[])
                if version in (V2_VERSION,V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,VERSION) and payload['phase']=='parking':
                    source['related_actions']=_related_trial_actions(payload,o,choices)
                sources.append(source)
            continue
        if o.get('run')=='baseline_repeat':continue
        if payload['phase']=='oneway_target':
            sources.extend(dict(id='target:'+ref+':'+key,kind='target',ref=ref,action=key,actions=[key]) for key in choices)
            continue
        if payload['phase']!='parking':continue
        regions=o.get('spatial',{}).get('regions',[])
        for region in regions:
            allowed=[key for key,choice in choices.items() if choice.get('region')==region['id']]
            if allowed:sources.append(dict(id='area:'+ref+':'+region['id'],kind='area',ref=ref,region=region['id'],actions=allowed))
        for road in o.get('core_roads',[]):
            pair=[road['from_node'],road['to_node']]
            allowed=[key for key,choice in choices.items() if not choice.get('region') and
                     any([c['from_node'],c['to_node']]==pair for c in choice['changes'])]
            if allowed:sources.append(dict(id='road:'+ref+':'+':'.join(map(str,pair)),kind='road',ref=ref,direction=pair,actions=allowed))
    result=dict(version=version,sources=sources,units=dict(UNITS),
                recent_trial=recent if payload['phase']=='parking' else None,
                scope=('仅引用本轮已提供的基准、候选及配对反馈。候选图检查不证明治理收益；小汽车量为veh/h，汇总当量流量为pcu/h。' if version==LEGACY_VERSION else
                       '所选动作须引用自己的新基准；同区域已试方案的反馈可解释暂缓备选，但不能证明备选效果。小汽车量为veh/h，汇总当量流量为pcu/h。'))
    if version in (V6_VERSION,VERSION):
        from .candidate_comparison import build
        comparison=build(payload,choices,next((s['ref'] for s in sources if s['id']==recent),None),
                         include_execution_evidence=version==VERSION)
        if comparison:result['recent_comparisons']=comparison
    return result


def covered(payload):
    """Do not bill a model call whose candidate observations are unavailable."""
    sources=payload['context']['choice_observations']['sources']
    return (len({s['id'] for s in sources})==len(sources)
            and all(any(key in s['actions'] for s in sources) for key in actions(payload)))


def schema(payload):
    offered=actions(payload)
    if not offered:return None
    facts=payload['context']['choice_observations']
    citation=('包含当前所选候选的观测；若有备选也引用它的观测，后续停车试验还须引用recent_trial。数值不需重抄。'
              if facts['version']==LEGACY_VERSION else
              '所选动作须引用自己的新基准；最近配对反馈由顶层evidence_refs引用其ref，无需在此重复。备选观测单独填alternative_observed_id。'
              if facts['version'] in (V4_VERSION,V5_VERSION,V6_VERSION,VERSION) else
              '所选动作须引用自己的新基准；后续停车试验还须引用recent_trial。备选的观测单独填alternative_observed_id，数值不需重抄。'
              if facts['version']==V3_VERSION else
              '所选动作须引用自己的新基准；暂缓备选可引用其自身基准，或同区域已试方案的反馈。后续停车试验还须引用recent_trial。未试备选不能称已验证，数值不需重抄。')
    properties=dict(observed_ids=dict(type='array',items=dict(type='string',enum=[s['id'] for s in facts['sources']]),
                          minItems=1,maxItems=6,description=citation),
        working_hypothesis=dict(type='string',description='尚未验证的机制或收益假设，不是已观测事实。'),
        disconfirming_result=dict(type='string',description='后续可观测的什么结果会否定本轮思路；不能依赖接口未提供的动态量。'),
        alternative_action=dict(type=['string','null'],enum=[*offered,None],description='比较一个不同的合法候选；只有一个候选时填null。未选不代表已证实无效。'),
        alternative_tradeoff=dict(type='string',description='为何本轮暂缓该备选；明确其效果仍未实测。没有备选则说明菜单限制。'))
    if facts['version'] in (V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,VERSION):
        properties['alternative_observed_id']=dict(type=['string','null'],enum=[s['id'] for s in facts['sources']]+[None],
            description='alternative_action对应的实际可见观测ID；可选该备选自己的新基准，或同区域已试措施反馈。没有备选填null。与所选动作的observed_ids分别填写。')
    if facts['version'] in (V5_VERSION,V6_VERSION,VERSION):
        # Nullability follows the same visible menu used by validate(). No
        # candidate, effect or observation is supplied on the model's behalf.
        for field in ('alternative_action','alternative_observed_id'):
            properties[field]['type']='string' if len(offered)>1 else 'null'
            properties[field]['enum']=([v for v in properties[field]['enum'] if v is not None]
                                       if len(offered)>1 else [None])
    basis=dict(type='object',properties=properties,required=list(properties),additionalProperties=False)
    if facts['version'] in (V5_VERSION,V6_VERSION,VERSION) and set(offered)==set(payload['allowed_actions']):
        return dict(basis,description='本轮只开放实验动作，必须提供观测依据、待验证假设和备选取舍。')
    return dict(anyOf=[dict(type='null'),basis],
        description='选择实验时必须提供观测依据和待验证假设；结束或转入下一组时必须为null。')


def validate(value,payload):
    offered=actions(payload);choice=value['action'];basis=value.get('selection_basis')
    if not offered:return
    def require(ok,message):
        if not ok:raise ResponseValidationError('choice_basis',message)
    if choice not in offered:
        require(basis is None,'结束或交接时不能附加尚未选择的本组试验依据')
        return
    facts=payload['context']['choice_observations']
    fields={'observed_ids','working_hypothesis','disconfirming_result','alternative_action','alternative_tradeoff'}
    if facts['version'] in (V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,VERSION):fields.add('alternative_observed_id')
    require(isinstance(basis,dict) and set(basis)==fields,'选点必须区分实际观测、待验证假设、否证条件及备选取舍')
    for field in ('working_hypothesis','disconfirming_result','alternative_tradeoff'):
        require(substantive_text(basis[field]) and len(basis[field])<=300,'选点解释须为有效且简短的公开依据')
    sources={s['id']:s for s in facts['sources']}
    selected=basis['observed_ids']
    require(isinstance(selected,list) and 1<=len(selected)<=6 and all(isinstance(s,str) and s in sources for s in selected),
            '选点依据必须引用本轮实际提供的候选或反馈')
    require(len(set(selected))==len(selected),'观测依据不能重复凑数')
    require(any(choice in sources[s]['actions'] for s in selected),'缺少当前所选候选自己的新基准观测')
    if facts['recent_trial']:
        if facts['version'] in (V4_VERSION,V5_VERSION,V6_VERSION,VERSION):
            require(sources[facts['recent_trial']]['ref'] in value.get('evidence_refs',[]),
                    '后续试验必须在evidence_refs引用最近一次已完成配对反馈')
        else:
            require(facts['recent_trial'] in selected,'后续试验必须读取最近一次已完成配对反馈，不能只重复基准理由')
    alternative=basis['alternative_action']
    if len(offered)>1:
        require(isinstance(alternative,str) and alternative in offered and alternative!=choice,'须比较一个不同的合法备选，不能虚构或与自身比较')
        alternative_ids=[basis['alternative_observed_id']] if facts['version'] in (V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,VERSION) else selected
        require(any(isinstance(s,str) and s in sources and
                    (alternative in sources[s]['actions'] or
                     (facts['version'] in (V2_VERSION,V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,VERSION) and alternative in sources[s].get('related_actions',[])))
                    for s in alternative_ids), '备选取舍必须引用该备选实际可见的观测')
    else:
        require(alternative is None,'本轮只有一个合法候选，不能虚构其他备选')
        if facts['version'] in (V3_VERSION,V4_VERSION,V5_VERSION,V6_VERSION,VERSION):require(basis['alternative_observed_id'] is None,'没有备选时不得填写备选观测')


def verify(entry):
    payload=entry['input'];context=payload['context']
    version=context.get('choice_basis_protocol')
    if version is None:
        if 'choice_observations' in context or 'selection_basis' in entry:
            raise ValueError('旧选点记录不能事后追加新的观测依据协议')
        return
    if version not in SUPPORTED_VERSIONS or context.get('choice_observations')!=observations(payload):
        raise ValueError('选点依据索引被改写、遗漏或不是本轮实际观测')
    if entry.get('status')=='accepted':
        if not covered(payload):raise ValueError('候选缺少本轮观测，不能接受选点')
        if not actions(payload) and 'selection_basis' in entry:raise ValueError('没有试验候选的回合不能追加选点依据')
        if actions(payload) and 'selection_basis' not in entry:raise ValueError('缺少本轮公开选点依据')
        validate(entry,payload)
