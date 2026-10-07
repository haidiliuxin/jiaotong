"""Model-selected parking policies, each evaluated on the full demand grid."""
import math
from .data import scopes, scoped_metrics, save_json
from .agent import compare
from .robust_search import conclusion, policy_key


def analyze(agent, baseline, base, diagnosis):
    task = agent.task
    agent.scope = scopes(baseline, [r['index'] for r in diagnosis['selected']], task.neighbor_hops)
    save_json(agent.job/'scopes.json', agent.scope)
    base.update(metrics=scoped_metrics(baseline,agent.scope), core_rows=[baseline[i] for i in agent.scope['core']])
    if agent.state.get('regions'):
        from .spatial_planning import observe
        base['spatial_observation']=observe(baseline,agent.state['regions'])
    agent.state.update(planning_protocol='observed-planning-v3', model_grid=True, model_policies=[])
    baselines={1.0:baseline}
    for factor in sorted(task.demand_factors,reverse=True):
        if factor==1:continue
        rows, record=agent.simulation('demand_'+format(factor,'.3f').replace('.','p')+'_baseline',[],factor)
        ratio=record['execution']['assigned_car_od_sum']/base['execution']['assigned_car_od_sum']
        if not math.isclose(ratio,factor,rel_tol=1e-5,abs_tol=1e-6):
            raise ValueError('需求未按设定比例进入本次分配')
        baselines[factor]=rows
    if task.repeat_baseline:agent.verify_repeat(baseline)
    agent.persist()
    seen=set()
    candidates=[]
    for iteration in range(task.max_feedback+2):
        available=max(0,task.max_runs-agent.state['run_count']) if iteration<=task.max_feedback else 0
        changes,decision=agent.planner.parking(agent,diagnosis,candidates,seen,available)
        if not changes:break
        import json
        seen.add(json.dumps(changes,sort_keys=True))
        bundle=dict(changes=changes,decision_ordinal=decision['ordinal'],runs=[])
        agent.state['model_policies'].append(bundle)
        # Every policy consumes a complete matched grid, including unfavorable conditions.
        for factor in sorted(task.demand_factors,reverse=True):
            name=f'model_{iteration+1}_demand_'+format(factor,'.3f').replace('.','p')
            rows,record=agent.simulation(name,changes,factor)
            ratio=record['execution']['assigned_car_od_sum']/base['execution']['assigned_car_od_sum']
            if not math.isclose(ratio,factor,rel_tol=1e-5,abs_tol=1e-6):
                raise ValueError('治理候选的需求配对未进入本次分配')
            record['comparison']=agent.assess(baselines[factor],rows)
            candidates.append(record)
            bundle['runs'].append(name)
            agent.event('model_policy_assessment',dict(name=name,eligible=record['comparison']['eligible'],
                                                      violations=record['comparison']['violations']))
            agent.persist()
    agent.state.update(conclusion(agent.state))
    winner=next(r for r in agent.state['runs'] if r['name']==agent.state['recommendation'])
    for factor in task.demand_factors:
        if factor==1:continue
        matched=next(r for r in agent.state['runs'] if r['demand_factor']==factor and not r['changes'])
        if winner['changes']:
            policy=next(r for r in agent.state['runs'] if r['demand_factor']==factor and policy_key(r['changes'])==policy_key(winner['changes']))
            assessment=policy['comparison']
        else:
            policy=matched
            assessment=compare(baselines[factor],baselines[factor],agent.scope,task)
            assessment.update(eligible=True,violations=[],note='未形成跨需求统一治理建议，保留匹配基准')
        agent.state['sensitivity'].append(dict(car_demand_factor=factor,matched_baseline=matched['name'],
                                              fixed_primary_policy=policy['name'],comparison=assessment))
    from .planning_review import finish_research
    finish_research(agent)
    agent.event('recommendation',dict(scenario=agent.state['recommendation'],robust_across_tested_demand=agent.state['robust_across_tested_demand']))
