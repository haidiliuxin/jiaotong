"""Prospective region selection and a replayable, equal-budget policy controller.

These functions cannot run the model or inspect other strategies' observations.
The selection uses baseline traffic only; negative outcomes never replace a region.
"""
from .data import parking_candidates, scopes
from .decision_analysis import policy_key
from .robust_search import revise
from .selection import RULE, TOLERANCES

VERSION = 'prospective-three-region-v1'
STRATEGIES = ('fixed', 'feedback')
STRATA = ('high', 'medium', 'low')


def protocol(task, exclusions):
    return dict(
        version=VERSION, task=task.as_dict(), exclusions=exclusions,
        selection=dict(
            candidate_rule='完整停车候选：沿用parking_candidates；不使用界面前30条截断',
            core='每个候选依次作种子；种子2跳内按基准qL/v降序补至最多3条；种子必选',
            exclusion='最终核心加2跳邻区与登记的历史范围不相交；相同核心集合只保留节点编号最小种子',
            density='最终核心加2跳邻区内全部合法候选有向长度/该范围有效道路有向长度；四舍五入12位',
            strata='密度不同取值升序；第i项归floor(3*i/n)层，0低1中2高；同密度不拆分',
            order=list(STRATA), within_stratum='按种子from_node、to_node升序，取首个与已选范围不相交者',
            empty='无可用候选的层保留空缺，不跨层补点，不依据治理输出换点'),
        logical_budget_per_region_strategy=10, candidates_per_strategy=2,
        max_physical_scenes=31, demand_order=[1.1, 1.0, 0.9],
        fixed='首轮同核心停车2级；第二轮同核心停车1级，无论首轮通过或失败',
        feedback='首轮相同；失败则按该轮联合治理qL/v减少量保留前半；全需求通过且仍有瓶颈才增强至1级；否则停止',
        early_rejection='两策略均在任一需求违反原约束时终止该候选，其他需求保持缺测',
        pairing='每个需求独立无治理基准；范围在1.0新基准后、任何政策运行前冻结',
        sharing='同措施同需求同原输入同程序只物理运行一次；每个引用策略仍计一次逻辑场景；末尾复跑强制新执行',
        scheduling='每轮先保存两策略下一步决定，再计算或读取缓存；生成器只能收到本策略已有观察',
        selection_rule=RULE, selection_tolerances=list(TOLERANCES),
        risk_review='额外报告1跳邻区、毛新增全网过饱和和恶化热点；只作警示，不改变本轮冻结硬约束',
        limitations=['同一调整案例内的区域迁移，非跨城市验证', '联合路段收益非单路段因果贡献',
                     '确定性基准复跑非独立统计样本', '共享物理调用不伪装为独立重复'])


def freeze_regions(rows, task, excluded_indices):
    candidates = parking_candidates(rows, task)
    by_index = {r['index']: r for r in candidates}
    excluded = set(excluded_indices)
    seeds, seen = [], set()
    # Seed numbering is independent of the effectiveness of any policy.
    for seed in sorted(candidates, key=lambda r: (r['from_node'], r['to_node'])):
        nearby = set(scopes(rows, [seed['index']], task.neighbor_hops)['neighbor'])
        chosen = [seed] + [r for r in candidates if r['index'] in nearby][:task.max_links - 1]
        core_key = tuple(sorted(r['index'] for r in chosen))
        if core_key in seen:
            continue
        seen.add(core_key)
        scope = scopes(rows, list(core_key), task.neighbor_hops)
        footprint = set(scope['core']) | set(scope['neighbor'])
        total = sum(rows[i]['length_km'] for i in sorted(footprint))
        density = round(sum(by_index[i]['length_km'] for i in sorted(footprint & by_index.keys())) / total, 12)
        seeds.append(dict(seed=[seed['from_node'], seed['to_node']], core=scope['core'],
                          neighbor=scope['neighbor'], density=density,
                          candidate_count=len(footprint & by_index.keys()),
                          directed_length_km=total, overlaps_history=bool(footprint & excluded)))
    legal = [s for s in seeds if not s['overlaps_history']]
    values = sorted({s['density'] for s in legal})
    layers = {v: ('low', 'medium', 'high')[3 * i // len(values)] for i, v in enumerate(values)}
    for s in legal:
        s['stratum'] = layers[s['density']]
    regions, used = [], set()
    for stratum in STRATA:
        options = [s for s in legal if s['stratum'] == stratum and
                   not (set(s['core']) | set(s['neighbor'])) & used]
        if not options:
            regions.append(dict(id=stratum, status='no_candidate', reason='本层无与历史及已选范围不重叠的合法种子'))
            continue
        selected = options[0]
        scope = scopes(rows, selected['core'], task.neighbor_hops)
        used.update(scope['core'] + scope['neighbor'])
        changes = [dict(from_node=rows[i]['from_node'], to_node=rows[i]['to_node'],
                        old_parking=rows[i]['parking'], parking=2) for i in scope['core']]
        regions.append(dict(id=stratum, status='selected', seed=selected['seed'], density=selected['density'],
                            candidate_count=selected['candidate_count'], scope=scope, initial=changes,
                            roads=sorted({rows[i]['name'] for i in scope['core']})))
    return dict(candidates=candidates, seeds=seeds, density_values=values,
                counts=dict(candidates=len(candidates), seeds=len(seeds), legal_seeds=len(legal)), regions=regions)


def candidate_actions(strategy, region, task, bases):
    """Each send() observes only the preceding action of this strategy.

    Three baselines plus the reserved repeat cost four of the ten logical scenes.
    Two candidates each have room for all three factors, even on early rejection.
    """
    if strategy not in STRATEGIES:
        raise ValueError('未知对照策略')
    factors = sorted(task.demand_factors, reverse=True)
    initial = region['initial']
    changes, ranking, observed = initial, [], []
    reason = '相同首轮：冻结核心停车2级，先核对高需求'
    evaluated, rejected, seen = 0, [], set()
    stop = '达到两候选上限'
    for iteration in range(2):
        key = policy_key(changes)
        if key in seen:
            stop = '措施重复，停止'
            break
        seen.add(key)
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
        if iteration == 1:
            break
        observed = [r['name'] for r in records]
        if strategy == 'fixed':
            changes = [dict(c, parking=1) for c in initial]
            reason = '预定固定策略：同核心停车1级；不根据首轮结果改变措施'
        elif failed:
            changes, ranking = revise(failed, bases[failed['demand_factor']])
            if not changes:
                stop = '单路段仍失败，停止该方向并保留基准'
                break
            reason = '本策略实测失败：按联合治理路段qL/v减少量保留前半；非因果归因'
        else:
            trigger = any((r['metrics']['core']['weighted_speed_kmh'] or 0) < task.target_speed_kmh or
                          any((v['adjusted_load_ratio'] or 0) > 1 for v in r['core_rows']) for r in records)
            if not trigger:
                stop = '全部需求已通过且无预定增强触发条件'
                break
            changes = [dict(c, parking=1) for c in changes]
            reason = '全部需求通过但仍有瓶颈：增强至停车1级并重新核对全部需求'
    return dict(candidates_started=len(seen), policy_scenes=evaluated, logical_scenes=evaluated+4,
                budget=10, unused_budget=6-evaluated, early_rejections=rejected, stop_reason=stop)
