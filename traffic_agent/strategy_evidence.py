"""Within-adapter evidence for diagnosis and the matched four-policy studies.

Never compare absolute outputs across parking, Vehicle1 or turn-state engines.
The interaction term is a within-experiment contrast, not real-world causality.
"""
from .decision_analysis import decision_analysis, policy_key
from .selection import select


def findings(state):
    scenario=state['task']['scenario']
    runs=[r for r in state.get('runs',[]) if r.get('metrics') and r.get('execution',{}).get('status')=='completed']
    factors=state['task']['demand_factors']
    bases={f:next((r for r in runs if not r['changes'] and r['demand_factor']==f),None) for f in factors}
    def refs(items):return [f"{state['id']}:{r['name']}" for r in items]
    def item(id,question,status,answer,rows=(),measurements=()):
        return dict(id=id,question=question,status=status,answer=answer,evidence_refs=refs(rows),measurements=list(measurements))
    complete_bases=[r for r in bases.values() if r]
    baseline_complete=all(bases.values())
    common=dict(version='question-evidence-v1',kind=scenario,preferred_tested_policy=None,
                scope='仅本组实际完成的同条件对照；不跨引擎合并收益，不外推现实因果。')
    if scenario=='diagnosis':
        measured=bool(complete_bases)
        return dict(common,execution_complete=baseline_complete,findings=[
            item('diagnosis','本次诊断实际覆盖了什么？','measured' if measured else 'unmeasured',
                 f"已读取{len(complete_bases)}/{len(factors)}个约定需求的现状输出；道路排名不能证明拥堵成因。",complete_bases,
                 [dict(factor=r['demand_factor'],network=r['metrics']['network']) for r in complete_bases]),
            item('benefit','是否已经验证治理收益？','unmeasured','本组只诊断，没有执行治理，不能证明任一措施有效。')])
    if scenario not in ('oneway','circulation'):
        raise ValueError('不支持的策略证据类型')
    action='单向' if scenario=='oneway' else '禁转'
    response_key='oneway_response' if scenario=='oneway' else 'turn_response'
    control_kind='oneway' if scenario=='oneway' else 'turn'
    labels=('S1_parking','S2_oneway' if scenario=='oneway' else 'S2_turn','S3_combined')
    titles=dict(zip(labels,('停车',action,'停车＋'+action)))
    design=state.get('factorial_design',{}).get('policies',{})
    grid=[]
    paired=[]
    responses=[]
    for factor,base in bases.items():
        cells=[]
        for label in labels:
            r=next((r for r in runs if r.get('policy_label')==label and r['demand_factor']==factor
                    and label in design and r['changes']==design[label]),None)
            check=(r or {}).get('comparison',{})
            actual=(r or {}).get(response_key,{}).get('passed') if label!=labels[0] else None
            cells.append(dict(label=label,title=titles[label],run=r['name'] if r else None,
                              eligible=bool(r and check.get('eligible') is True and (label==labels[0] or actual is True)),
                              delta=check.get('delta'),violations=check.get('violations',[]),control_response=actual))
            if r and base:
                paired.append(r)
                if label!=labels[0]:responses.append(dict(run=r['name'],factor=factor,passed=actual))
        row=dict(factor=factor,baseline=base['name'] if base else None,cells=cells,interaction_pcu_h=None)
        if base and all(c['run'] for c in cells):
            times=[next(r for r in runs if r['name']==c['run'])['metrics']['network']['running_time_pcu_h'] for c in cells]
            row['interaction_pcu_h']=times[2]-times[0]-times[1]+base['metrics']['network']['running_time_pcu_h']
        grid.append(row)
    full=baseline_complete and all(all(c['run'] for c in row['cells']) for row in grid)
    analysis=decision_analysis(state)
    for policy in analysis['policies']:
        policy['feasible'] &= policy_key(policy['changes']) in {policy_key(c) for c in design.values()}
        controlled=any(c.get('kind')==control_kind for c in policy['changes'])
        if controlled:
            relevant=[r for r in paired if policy_key(r['changes'])==policy_key(policy['changes'])]
            policy['feasible'] &= bool(relevant) and all(r.get(response_key,{}).get('passed') is True for r in relevant)
    valid=[p for p in analysis['policies'] if p['feasible']]
    selected=select(valid)['recommendation'] if state['task'].get('recommendation_version')=='tolerance-v2' else None
    all_refs=list({r['name']:r for r in complete_bases+paired}.values())
    adverse=[row['factor'] for row in grid if all(c['run'] for c in row['cells']) and not row['cells'][2]['eligible']]
    required_response=2*len(factors)
    response_status=('not_supported' if any(r['passed'] is False for r in responses) else
                     'supported' if len(responses)==required_response and all(r['passed'] is True for r in responses) else 'unmeasured')
    result=[
        item('factorial','停车、交通组织与组合是否完成公平对照？','measured' if full else 'unmeasured',
             '同组各需求均有基准、停车、交通组织和组合。' if full else '尚未测齐四项同需求对照；缺测不能补推。',all_refs,grid),
        item('response',f'{action}是否真的进入原生交通分配？',response_status,
             '全部约定控制场景的原生响应检查通过；这不等同于治理有效。' if response_status=='supported' else
             '至少一个已测场景未通过原生控制响应；不能把参数写入当作生效。' if response_status=='not_supported' else
             '原生控制响应证据不足。',all_refs,responses),
        item('benefit','哪些措施在约定需求下通过约束？','supported' if valid else 'not_supported' if full else 'unmeasured',
             f"{len(valid)}项相同措施通过全部需求约束与实际控制检查。" if valid else
             '完整已测措施均未通过；保留匹配基准。' if full else '没有完成全部需求且通过检查的措施。',all_refs,
             [dict(run=p['name'],changes=p['changes'],worst=p['worst']) for p in valid]),
        item('combination','叠加措施是否一定更合适？','observed' if adverse else 'measured' if full else 'unmeasured',
             f"组合在需求{adverse}未通过约束；不能因措施更多而推荐。" if adverse else
             '完整组合对照已测；应比较各单项与组合的约束和代价，不能假定收益可直接相加。' if full else '等待完整组合配对。',all_refs,grid),
        item('robustness','同一措施在需求波动下是否仍然可用？',
             'supported' if len(factors)>1 and valid else 'not_supported' if len(factors)>1 and full else 'unmeasured',
             f"仅在已测的{len(factors)}个需求下核对同一措施；不平均抵消失败。" if len(factors)>1 and full else
             '单需求或缺测不能证明跨需求稳健性。',all_refs if len(factors)>1 else ())]
    return dict(common,execution_complete=full,preferred_tested_policy=selected,findings=result,
                selection_basis='各组独立满足约束后比较；组合额外项=T组合−T停车−T交通组织+T基准，单位pcu·h，不是因果证明。')
