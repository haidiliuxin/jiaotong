"""Prospective, equal-budget comparison of guarded parking search and a strong fixed grid.

This is a new protocol. The sealed sparse-backtracking challenge remains v1.
Controllers receive only their own measured observations; selection is baseline-only.
"""
from .decision_analysis import policy_key
from .robust_search import baseline_singletons, revise
from .study import protocol as original_protocol
from .task import Task

VERSION = 'network-guard-equal-budget-v1'
KIND = 'prospective_network_guard_challenge'
STRATEGIES = ('fixed_full_singletons', 'feedback_backtracking_v2')
LOGICAL_BUDGET = 16
MAX_CANDIDATES = 4
MAX_PHYSICAL = 67  # 3 regions * (1 shared + 3 unique per arm) * 3 factors + 4


def challenge_task():
    return Task.from_dict(dict(
        request='未见区域：全网新增过饱和安全门槛下，完整单路段固定网格与新版反馈回退同预算对照',
        search_mode='robust', recommendation_version='tolerance-v2',
        max_runs=LOGICAL_BUDGET, max_feedback=3, max_links=3,
        demand_factors=[0.9, 1.0, 1.1], repeat_baseline=True,
        max_new_network_oversaturated_km=0.1))


def protocol(task, exclusions):
    result = original_protocol(task, exclusions)
    result.pop('fixed')
    result.pop('feedback')
    result.update(
        version=VERSION, strategies=list(STRATEGIES),
        logical_budget_per_region_strategy=LOGICAL_BUDGET,
        candidates_per_strategy=MAX_CANDIDATES,
        max_physical_scenes=MAX_PHYSICAL,
        fixed_full_singletons='治理前固定：全部原核心停车2级，然后原核心每条单独停车2级，按无治理1.0基准qL/v降序；最多四候选',
        feedback_backtracking_v2='首轮相同；违反任一约束则按本策略联合实测核心qL/v收益缩减；单路段失败或缩减措施已测时回到原核心，按基准qL/v尝试未测单路段；全需求通过但有瓶颈才增强停车1级；最多四候选',
        priority='仅用需求1.0无治理基准各原核心qL/v；相同值按from_node、to_node升序',
        safety_gate='每个需求均要求全网毛新增过饱和里程≤0.1km，并保留原邻区/全网耗时及邻区过饱和约束',
        early_rejection='两策略在任一需求违反约束时结束该候选；未测需求为缺测，不视为通过',
        scheduling='同一轮先持久化两策略下一动作，然后运行或共享相同物理场景；控制器仅看本策略观察',
        comparison='每区每策略均计3个需求基准、最多12个治理场景、1次基准复跑；物理共享不减免逻辑计费',
        hypothesis='反馈回退可能用同预算找到固定网格没有的可行或更优措施；也允许打平或输给固定策略')
    result['limitations'].append('本协议与旧挑战的安全门槛及预算不同；不得把跨版本胜率当作算法增益')
    return result


def fixed_policies(region, bases):
    ranked = [entry[2] for entry in baseline_singletons(region['initial'], bases[1.0])]
    proposed = [region['initial']] + [[change] for change in ranked]
    unique = {}
    for changes in proposed:
        unique.setdefault(policy_key(changes), changes)
    return list(unique.values())[:MAX_CANDIDATES]


def candidate_actions(strategy, region, task, bases):
    if strategy not in STRATEGIES:
        raise ValueError('未知对照策略')
    if task.as_dict() != challenge_task().as_dict():
        raise ValueError('对照任务卡不得改变预算、需求或全网安全门槛')
    factors = sorted(task.demand_factors, reverse=True)
    planned = fixed_policies(region, bases)
    alternatives = [entry[2] for entry in baseline_singletons(region['initial'], bases[1.0])]
    changes, ranking, observed = region['initial'], [], []
    reason = '共同首轮：原核心全部停车2级；从高需求开始'
    evaluated, rejected, seen, backtracks = 0, [], set(), []
    order, stop = factors, '达到四候选上限'
    for iteration in range(MAX_CANDIDATES):
        signature = policy_key(changes)
        if signature in seen:
            stop = '措施重复，停止'
            break
        seen.add(signature)
        records, failed = [], None
        for factor in order:
            name = f"{region['id']}_{strategy}_p{iteration+1}_{factor:g}"
            record = yield dict(name=name, strategy=strategy, region=region['id'],
                                policy=iteration+1, changes=changes, factor=factor,
                                reason=reason, observed_runs=list(observed), ranking=ranking)
            evaluated += 1
            records.append(record)
            if not record['comparison']['eligible']:
                failed = record
                rejected.append(dict(policy=iteration+1, failed_factor=factor,
                                     missing=[f for f in factors if f not in [r['demand_factor'] for r in records]]))
                break
        if iteration == MAX_CANDIDATES-1:
            break
        observed.extend(r['name'] for r in records)
        ranking = []
        if strategy == 'fixed_full_singletons':
            if iteration+1 >= len(planned):
                stop = '事前固定的不重复措施已测完'
                break
            changes = planned[iteration+1]
            order, reason = factors, '事前固定单路段网格；顺序不随治理结果改变'
            continue
        if failed:
            proposed, ranking = revise(failed, bases[failed['demand_factor']])
            reason = '本策略约束失败：按联合实测核心qL/v收益缩减；不是单路段因果归因'
            order = [failed['demand_factor']] + [f for f in factors if f != failed['demand_factor']]
            if proposed and policy_key(proposed) not in seen:
                changes = proposed
                continue
            next_change = next((c for c in alternatives if policy_key([c]) not in seen), None)
            if next_change is None:
                stop = '单路段失败或缩减措施重复，原核心方向已测完'
                break
            changes, ranking = [next_change], []
            backtracks.append(dict(after_failed_run=failed['name'],
                                   next_link=[next_change['from_node'], next_change['to_node']]))
            reason = '回到原核心，按无治理基准qL/v尝试未测单路段停车2级'
            continue
        trigger = any((r['metrics']['core']['weighted_speed_kmh'] or 0) < task.target_speed_kmh or
                      any((row['adjusted_load_ratio'] or 0) > 1 for row in r['core_rows']) for r in records)
        if not trigger or not all(c['parking'] == 2 for c in changes):
            stop = '三需求通过且无增强触发条件，或已经增强至停车1级'
            break
        proposed = [dict(c, parking=1) for c in changes]
        if policy_key(proposed) in seen:
            stop = '增强措施重复，停止'
            break
        changes, order = proposed, factors
        reason = '三需求通过但仍有瓶颈，增强停车1级并重新核对全部需求'
    return dict(candidates_started=len(seen), policy_scenes=evaluated,
                logical_scenes=evaluated+4, budget=LOGICAL_BUDGET,
                unused_budget=LOGICAL_BUDGET-4-evaluated,
                early_rejections=rejected, backtracks=backtracks, stop_reason=stop)
