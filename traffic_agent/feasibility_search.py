"""Menu-bound hypotheses after a failed feasibility trial, never effect claims."""
VERSION='failed-feasibility-search-v1'


def pairs(changes):
    return {(c['from_node'],c['to_node']) for c in changes}


def build(payload):
    context=payload['context']
    if (payload['phase']=='parking' and context.get('knowledge_retrieval_protocol')=='curated-knowledge-rag-v3'
            and context.get('research_depth',{}).get('intent')=='demand_robustness'):
        return robust_guidance(payload)
    if (payload['phase']!='parking' or context.get('research_depth',{}).get('intent')!='find_one'
            or context.get('completion_guard',{}).get('strength_comparison_required')):return None
    trials=[o for o in payload['observations'] if o.get('changes') and o.get('delta') is not None]
    if not trials or any(o.get('eligible') is True for o in trials):return None
    failed=trials[-1]
    if failed.get('eligible') is not False:return None
    previous=pairs(failed['changes'])
    reduced=[];different=[];same=[]
    for key,action in sorted(payload['allowed_actions'].items()):
        changes=action.get('changes',[])
        if not changes or any(c.get('kind','parking')!='parking' for c in changes):continue
        proposed=pairs(changes)
        if proposed < previous:reduced.append(key)
        elif proposed==previous:same.append(key)
        else:different.append(key)
    if not reduced and not different:return None
    return dict(version=VERSION,failed_ref=failed['ref'],violations=failed.get('violations',[]),
        reduced_scope_actions=reduced,different_scope_actions=different,same_scope_actions=same,
        unmeasured=True,boundary='这些菜单动作尚未验证；缩小范围或换区域只是待检验假设，不能推断收益或约束通过。')


def robust_guidance(payload):
    from .evidence_support import ledger
    facts=ledger(payload['observations'],payload['context'])
    if facts['eligible_policy_count']:return None
    failed=next((p for p in reversed(facts['policies']) if p['failed_demand_factors']),None)
    if failed is None:return None
    previous=pairs(failed['changes']);reduced=[];same=[];different=[]
    for key,action in sorted(payload['allowed_actions'].items()):
        changes=action.get('changes',[])
        if not changes or any(c.get('kind','parking')!='parking' for c in changes):continue
        proposed=pairs(changes)
        if proposed<previous:reduced.append(key)
        elif proposed==previous:same.append(key)
        else:different.append(key)
    if not (reduced or same or different):return None
    return dict(version='failed-robust-search-v1',policy_grid=facts['policies'],
        failed_demand_factors=failed['failed_demand_factors'],required_demand_factors=facts['required_demand_factors'],
        robust_eligible=False,reduced_scope_actions=reduced,same_scope_actions=same,different_scope_actions=different,
        unmeasured=True,boundary='低需求通过不能抵消高需求失败；新候选须覆盖冻结全部需求并使用各自匹配基准。缩小范围、换区域或减弱等级均只是待验证假设；不改门槛，不承诺能找到合格方案。')
