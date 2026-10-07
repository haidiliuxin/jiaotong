"""Bounded checks for typed facts accidentally reversed in public prose.

This checks a few explicit claims whose meaning follows from frozen structured
facts. It does not attempt general natural-language entailment.
"""
import re

VERSION='typed-public-claims-v2'
TEXT_FIELDS=('summary','reason','risk','next_check','programme_summary')
NEGATIVE_KM=re.compile(r'(?<![\d.])[−-]\s*\d+(?:\.\d+)?\s*(?:km|公里|千米)',re.I)
BUDGET_EXHAUSTED=re.compile(r'(?:模型|请求|API)?(?:预算|额度)(?:与[^。；;，,\n]{1,12})?(?:已|已经|都)?(?:用尽|耗尽|花完|已尽)')
STRONGER_TWO=re.compile(r'更强(?:的)?(?:停车)?等级[^。；;，,\n]{0,12}?(?:停车)?2级|(?:停车)?2级[^。；;，,\n]{0,10}?更强')


def issues(value,facts,budget=None, *, scoped_budget=False):
    """Return explicit conflicts; never rewrite the model's original words."""
    result=[]
    texts=[(field,value.get(field,'')) for field in TEXT_FIELDS]
    texts.extend((key+':'+u['hypothesis_id'],u['explanation'])
                 for key in ('hypothesis_updates','programme_updates') for u in value.get(key,[]))
    levels={level for policy in facts.get('executed_policies',[])
            for level in policy.get('parking_levels',[]) if type(level) is int}
    remaining=budget.get('remaining') if isinstance(budget,dict) else None
    for field,text in texts:
        if not isinstance(text,str):continue
        for clause in re.split(r'[。；;\n]',text):
            if re.search(r'邻区|周边',clause):
                for match in NEGATIVE_KM.finditer(clause):
                    before=clause[max(0,match.start()-32):match.start()]
                    if not re.search(r'净(?:过饱和(?:里程)?)?(?:变化|减少|下降|增减)|过饱和(?:里程)?(?:减少|下降)',before):
                        result.append(dict(code='unsigned_gross_metric',field=field,refs=[],quote=match.group(),
                            message='邻区毛新增过饱和是非负里程；负号若指净变化须明确指标，不能把实际新增代价写成负数。'))
            if type(remaining) is int and remaining>1:
                for match in BUDGET_EXHAUSTED.finditer(clause):
                    prefix=clause[:match.start()]
                    if scoped_budget and re.search(r'(?:原生(?:仿真)?(?:场景|工具)?|仿真(?:场景|次数)?|场景)(?:的)?\s*$',prefix):
                        continue
                    result.append(dict(code='model_budget_not_exhausted',field=field,refs=[],quote=match.group(),
                        message=f'本轮模型回复后仍有{remaining-1}次请求额度；不能写模型预算已尽。原生场景预算须单独说明。'))
            if levels=={1}:
                for match in STRONGER_TWO.finditer(clause):
                    result.append(dict(code='parking_strength_reversed',field=field,refs=[],quote=match.group(),
                        message='当前实测停车1级，等级1比等级2治理更强；2级可作为未测强弱对照，不能称为更强等级。'))
    return result


def demand_cost_issues(value,facts,context):
    """Check an explicit all-demand cost claim only when one policy grid is known.

    A failed recommendation does not imply that every demand failed each cost
    component. Ambiguous multi-policy or incomplete grids remain unclassified.
    """
    rows=facts.get('costs',[])
    factors=context.get('task',{}).get('demand_factors',[])
    if (len(factors)<2 or len(rows)!=len(factors)
            or {r.get('demand_factor') for r in rows}!=set(factors)
            or len({r['ref'].split(':')[0] for r in rows})!=1
            or any('_demand_' not in (r.get('run') or '') for r in rows)
            or len({r['run'].rsplit('_demand_',1)[0] for r in rows})!=1):
        return []
    thresholds=context['task']
    max_km=thresholds.get('max_new_oversaturated_km')
    max_time=thresholds.get('max_neighbor_time_increase_pct')
    if type(max_km) not in (int,float) or type(max_time) not in (int,float):
        return []
    texts=[(field,value.get(field,'')) for field in TEXT_FIELDS]
    texts.extend((key+':'+u['hypothesis_id'],u['explanation'])
                 for key in ('hypothesis_updates','programme_updates') for u in value.get(key,[]))
    result=[]
    universal=re.compile(r'(?:两|两个|全部|所有|每个|各)(?:个|种)?需求[^。；;，,\n]{0,12}?(?:均|都)')
    breach=re.compile(r'(?<![未不])超(?:过|出|限)?|未[^。；;，,\n]{0,4}(?:通过|满足|达标)|不(?:合格|达标|满足)')
    for field,text in texts:
        if not isinstance(text,str):continue
        for clause in re.split(r'[。；;，,\n]',text):
            if not universal.search(clause) or not breach.search(clause):continue
            if re.search(r'若|如果|假如|不能声称|不代表|并非',clause):continue
            metric=None;wrong=[]
            if re.search(r'(?:邻区|周边)[^。；;，,\n]{0,16}?(?:毛)?新增过饱和',clause):
                metric='邻区毛新增过饱和'
                wrong=[r for r in rows if type(r.get('neighbor_new_oversaturated_km')) in (int,float)
                       and r['neighbor_new_oversaturated_km']<=max_km]
            elif re.search(r'(?:邻区|周边)[^。；;，,\n]{0,16}?耗时',clause):
                metric='邻区耗时增幅'
                wrong=[r for r in rows if type(r.get('neighbor_time_change_pct')) in (int,float)
                       and r['neighbor_time_change_pct']<=max_time]
            elif (re.search(r'(?:邻区|周边)(?:代价|门槛|约束)',clause)
                  and not re.search(r'候选|area_\d+|[/或及]',clause)):
                metric='邻区两项门槛'
                wrong=[r for r in rows if type(r.get('neighbor_new_oversaturated_km')) in (int,float)
                       and type(r.get('neighbor_time_change_pct')) in (int,float)
                       and r['neighbor_new_oversaturated_km']<=max_km
                       and r['neighbor_time_change_pct']<=max_time]
            if wrong:
                result.append(dict(code='cross_demand_cost_quantifier',field=field,
                    refs=[r['ref'] for r in wrong],quote=clause[:160],
                    message=f'不能声称每个需求的{metric}均超限；列出的需求点该项并未超过冻结门槛。请逐需求写明实际违反的条件。'))
    return result
