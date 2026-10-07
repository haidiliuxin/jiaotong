"""Preregistered sparse fixed baseline and bounded feedback backtracking.

Versioned separately: historical studies continue to use study.py unchanged.
Every controller receives only its own observations, never another arm's cache.
"""
from .decision_analysis import policy_key
from .robust_search import revise
from .study import protocol as original_protocol
from .task import Task

VERSION = 'sparse-backtracking-challenge-v1'
KIND = 'prospective_backtracking_challenge'
STRATEGIES = ('fixed_diverse', 'feedback_halving', 'feedback_backtracking')
LOGICAL_BUDGET = 13
MAX_CANDIDATES = 3
MAX_PHYSICAL = 67  # 3 regions * (1 shared + 2 unique * 3 arms) * 3 factors + 4


def challenge_task():
    return Task.from_dict(dict(request='新区域稀疏固定策略、原反馈与回退反馈的同预算对照',
                               search_mode='robust', recommendation_version='tolerance-v2',
                               max_runs=LOGICAL_BUDGET, max_feedback=2, max_links=3,
                               demand_factors=[0.9, 1.0, 1.1], repeat_baseline=True))


def protocol(task, exclusions):
    result = original_protocol(task, exclusions)
    result.pop('fixed')
    result.pop('feedback')
    result.update(version=VERSION, strategies=list(STRATEGIES),
                  logical_budget_per_region_strategy=LOGICAL_BUDGET,
                  candidates_per_strategy=MAX_CANDIDATES, max_physical_scenes=MAX_PHYSICAL,
                  fixed_diverse='事前固定序列：全部核心停车2级、基准qL/v最高单路段2级、次高单路段2级；去重，不足三项再补全部核心1级',
                  feedback_halving='同首轮；失败按本策略联合实测qL/v收益保留前半；全部需求通过但仍有瓶颈则增强至1级；单路段失败或措施重复停止；上限三候选',
                  feedback_backtracking='同原反馈；无法继续缩减或建议措施重复时，回到原冻结核心，按基准qL/v降序尝试尚未单独测过的停车2级方向；最多三候选',
                  priority='只使用1.0基准核心qL/v，降序；相同值按from_node、to_node升序',
                  early_rejection='三策略均在任一需求违反原约束时终止该候选，其余需求记缺测；共享结果仍逐策略计费',
                  scheduling='每轮先保存所有存活策略下一步决定，再计算或读取缓存；控制器只观察本策略已有结果',
                  known_winner='试探失败不删除已通过三需求的措施；最终按原tolerance-v2从本策略全部实测方案统一推荐',
                  hypothesis='回退可能避免过早停止；对照已包含稀疏单路段方案，允许固定策略打平或胜出；不预设反馈必胜')
    result['limitations'].append('与旧实验预算不同，不直接把新旧胜率解释为回退增益；只比较本轮同预算三策略')
    return result


def priority(region, bases):
    rows = {(r['from_node'], r['to_node']): r for r in bases[1.0]['core_rows']}
    def key(change):
        row = rows[change['from_node'], change['to_node']]
        return (-row['flow_pcu_h'] * row['length_km'] / row['speed_kmh'],
                change['from_node'], change['to_node'])
    return sorted((dict(c, parking=2) for c in region['initial']), key=key)


def fixed_policies(region, bases):
    ranked = priority(region, bases)
    proposed = [region['initial']] + [[c] for c in ranked[:2]]
    proposed.append([dict(c, parking=1) for c in region['initial']])
    unique = {}
    for changes in proposed:
        unique.setdefault(policy_key(changes), changes)
    return list(unique.values())[:MAX_CANDIDATES]


def candidate_actions(strategy, region, task, bases):
    if strategy not in STRATEGIES:
        raise ValueError('未知挑战策略')
    if task.max_runs != LOGICAL_BUDGET or set(task.demand_factors) != {0.9, 1.0, 1.1}:
        raise ValueError('挑战协议要求13场景及三个预定需求')
    factors = sorted(task.demand_factors, reverse=True)
    planned = fixed_policies(region, bases)
    ranked = priority(region, bases)
    changes, ranking, observed = region['initial'], [], []
    reason = '共同首轮：冻结核心全部停车2级'
    evaluated, rejected, seen, backtracks = 0, [], set(), []
    stop = '达到三候选上限'
    for iteration in range(MAX_CANDIDATES):
        seen.add(policy_key(changes))
        records, failed = [], None
        for factor in factors:
            name = f"{region['id']}_{strategy}_p{iteration+1}_{factor:g}"
            record = yield dict(name=name, strategy=strategy, region=region['id'], policy=iteration+1,
                                changes=changes, factor=factor, reason=reason,
                                observed_runs=list(observed), ranking=ranking)
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
        if strategy == 'fixed_diverse':
            if iteration+1 == len(planned):
                stop = '事前固定的不重复措施已用尽'
                break
            changes = planned[iteration+1]
            reason = '事前固定稀疏候选；顺序不随治理结果改变'
            continue
        if failed:
            proposed, ranking = revise(failed, bases[failed['demand_factor']])
            reason = '本策略实测失败：按联合治理qL/v收益保留前半；非单路段因果归因'
        else:
            trigger = any((r['metrics']['core']['weighted_speed_kmh'] or 0) < task.target_speed_kmh or
                          any((v['adjusted_load_ratio'] or 0) > 1 for v in r['core_rows']) for r in records)
            if not trigger:
                stop = '全部需求已通过且无预定增强触发条件'
                break
            proposed = [dict(c, parking=1) for c in changes]
            reason = '三需求通过但仍有瓶颈：增强至1级并从高需求重新验证'
        if proposed and policy_key(proposed) not in seen:
            changes = proposed
            continue
        if strategy == 'feedback_backtracking':
            alternatives = [[c] for c in ranked if policy_key([c]) not in seen]
            if alternatives:
                changes = alternatives[0]
                backtracks.append(dict(after_policy=iteration+1, changes=changes))
                reason = '当前方向无法继续：回退到原核心，按事前基准优先级尝试未单独测试的2级方向'
                # The failed shrink ranking does not rank the alternative branch.
                ranking = []
                continue
        stop = '单路段失败或措施重复，且本策略没有可继续的方向'
        break
    return dict(candidates_started=len(seen), policy_scenes=evaluated, logical_scenes=evaluated+4,
                budget=LOGICAL_BUDGET, unused_budget=LOGICAL_BUDGET-4-evaluated,
                early_rejections=rejected, backtracks=backtracks, stop_reason=stop)
