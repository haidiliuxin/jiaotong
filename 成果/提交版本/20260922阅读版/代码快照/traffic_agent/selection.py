"""Versioned decision tolerances, not uncertainty estimates or physical costs."""
import math

LEGACY = 'legacy-v1'
VERSION = 'tolerance-v2'
# Percentage POINTS for time changes; km for gross new neighbor overload.
TOLERANCES = (0.001, 0.01, 0.001)
RULE = ('先通过原约束；依次保留距当前最优全网增幅不超过0.001个百分点、'
        '邻区增幅不超过0.01个百分点、新增邻区过饱和不超过0.001km的候选；'
        '近似相同时优先少措施、少停车1级措施，再按名称稳定排序。容差为决策假设，不是统计显著性。')


def complexity(changes):
    """Each distinct parking direction, turn prohibition or closure is one action.

    The second ordinal key favors fewer parking level-1 actions, without
    interpreting the parking category as a linear capacity or monetary cost.
    """
    import json
    unique = {json.dumps(c, sort_keys=True): c for c in changes}
    return [len(unique), sum(c.get('parking') == 1 for c in unique.values())]


def select(policies, tolerances=TOLERANCES):
    """Anchor each gate to its pool's exact minimum; never chain pairwise ties.

    All input policies must already satisfy the original hard constraints.
    Exact Pareto dominance remains descriptive: a negligible improvement does
    not force the more complex policy. Returned evidence makes this explicit.
    """
    if len(tolerances) != 3 or any(not math.isfinite(x) or x < 0 for x in tolerances):
        raise ValueError('容差必须是三个非负有限数')
    pool = list(policies)
    if any(len(p['worst']) != 3 or any(not math.isfinite(x) for x in p['worst']) for p in pool):
        raise ValueError('候选指标必须是三个有限数')
    stages = []
    for dimension, tolerance in enumerate(tolerances):
        if not pool:
            break
        anchor = min(p['worst'][dimension] for p in pool)
        pool = [p for p in pool if p['worst'][dimension] - anchor <= tolerance + 1e-12]
        stages.append(dict(dimension=dimension, anchor=anchor, tolerance=tolerance,
                           retained=[p['name'] for p in pool]))
    ordered = sorted(pool, key=lambda p: (*complexity(p['changes']), p['name']))
    winner = ordered[0] if ordered else None
    return dict(version=VERSION, tolerances=list(tolerances), stages=stages,
                equivalent_candidates=[p['name'] for p in ordered],
                recommendation=winner['name'] if winner else None,
                candidates=[dict(name=p['name'], objectives=p['worst'], complexity=complexity(p['changes']),
                                 difference_from_selected=[x-y for x, y in zip(p['worst'], winner['worst'])] if winner else None)
                            for p in policies], rule=RULE)


def record_selection(records):
    candidates = []
    for r in records:
        c = r.get('comparison', {})
        if c.get('eligible') is not True or c.get('violations'):
            continue
        vector = [c.get('delta', {}).get(s, {}).get('running_time_change_pct') for s in ('network', 'neighbor')]
        vector.append(c.get('new_neighbor_oversaturated_km'))
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
            continue
        candidates.append(dict(name=r['name'], changes=r['changes'], worst=vector))
    return select(candidates)
