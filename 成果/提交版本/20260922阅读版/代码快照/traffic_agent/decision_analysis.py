"""Read-only multi-demand screening of measured policies and spatial cost attribution.

This supplements, rather than rewrites, the experiment's frozen recommendation.
No averaged score may compensate for a failed constraint or missing demand case.
"""
import json
import math


def change_pct(before, after):
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    if not math.isfinite(before) or not math.isfinite(after) or before <= 0:
        return None
    return (after / before - 1) * 100


def policy_key(changes):
    return json.dumps(sorted(changes, key=lambda c: json.dumps(c, sort_keys=True)), sort_keys=True)


def decision_analysis(state, version=None):
    """Constraint-first minimax with a Pareto explanation, using exact policy identity.

    Objectives (all minimized): worst matched network time change %, worst
    neighbor time change %, and worst gross new neighbor overloaded length.
    Dominance never crosses policy feasibility. Missing/ambiguous cases exclude
    a policy; no interpolation, synthetic confidence, or best-repeat cherry-pick.
    """
    version = version or state.get('task', {}).get('recommendation_version', 'legacy-v1')
    if version not in ('legacy-v1', 'tolerance-v2'):
        raise ValueError('未知推荐版本')
    expected = sorted(set(state.get('task', {}).get('demand_factors', [1.0])))
    complete = [r for r in state.get('runs', []) if r.get('metrics') and r.get('execution', {}).get('status') == 'completed']
    bases, groups = {}, {}
    for run in complete:
        factor = run['demand_factor']
        if not run.get('changes'):
            bases.setdefault(factor, []).append(run)
        else:
            groups.setdefault(policy_key(run['changes']), []).append(run)
    policies = []
    for key, runs in groups.items():
        checks, vector, missing, reasons = [], [], [], []
        for factor in expected:
            options = [r for r in runs if r['demand_factor'] == factor]
            baselines = bases.get(factor, [])
            if not options or not baselines:
                missing.append(factor)
                continue
            run, base = options[0], baselines[0]
            ambiguity = any(r['metrics'] != run['metrics'] or r.get('comparison') != run.get('comparison') for r in options[1:])
            ambiguity |= any(r['metrics'] != base['metrics'] for r in baselines[1:])
            assessment = run.get('comparison', {})
            network = change_pct(base['metrics']['network']['running_time_pcu_h'], run['metrics']['network']['running_time_pcu_h'])
            neighbor = change_pct(base['metrics']['neighbor']['running_time_pcu_h'], run['metrics']['neighbor']['running_time_pcu_h'])
            # Empty identical neighbors have zero added cost, not an undefined risk.
            if base['metrics']['neighbor']['running_time_pcu_h'] == run['metrics']['neighbor']['running_time_pcu_h'] == 0:
                neighbor = 0.0
            gross = assessment.get('new_neighbor_oversaturated_km')
            valid = network is not None and neighbor is not None and isinstance(gross, (int, float)) and math.isfinite(gross) and gross >= 0
            violations = list(assessment.get('violations', []))
            if ambiguity:
                violations.append('相同措施或基准的重复结果不一致，不能择优取样')
            if not valid:
                violations.append('缺少可比较的耗时或新增过饱和证据')
            accepted = assessment.get('eligible') is True and valid and not ambiguity and not violations
            if not accepted and not violations:
                violations.append('原实验约束未通过或尚未评估')
            checks.append(dict(factor=factor, run=run['name'], baseline=base['name'], accepted=accepted,
                               network_time_pct=network, neighbor_time_pct=neighbor, new_neighbor_km=gross,
                               violations=violations))
            if accepted:
                vector.append((network, neighbor, gross))
            else:
                reasons.extend(f'需求 × {factor}：{v}' for v in violations)
        feasible = not missing and len(checks) == len(expected) and bool(checks) and all(c['accepted'] for c in checks)
        name = next((r['name'] for r in runs if r['demand_factor'] == 1), runs[0]['name'])
        policies.append(dict(name=name, changes=runs[0]['changes'], checks=checks, missing=missing, reasons=reasons,
                             feasible=feasible, worst=[max(v[i] for v in vector) for i in range(3)] if feasible else None,
                             dominated_by=[], frontier=False))
    feasible = [p for p in policies if p['feasible']]
    # Quantize before comparing: pairwise epsilon comparisons can create cycles
    # (A dominates B, B dominates C, C dominates A) and erase the whole frontier.
    def objective(policy):
        return tuple(round(value, 9) for value in policy['worst'])
    for p in feasible:
        p['dominated_by'] = [q['name'] for q in feasible if q is not p and
                             all(a <= b for a, b in zip(objective(q), objective(p))) and
                             any(a < b for a, b in zip(objective(q), objective(p)))]
        p['frontier'] = not p['dominated_by']
    ordered = sorted((p for p in feasible if p['frontier']), key=lambda p: (*objective(p), len(p['changes']), p['name']))
    final = state.get('status') == 'completed' and len(expected) >= 2
    winner = ordered[0]['name'] if ordered and final else None
    result = dict(version='paired-minimax-v1', factors=expected, policies=policies, recommendation=winner,
                finalized=final, eligible_count=len(feasible), frontier_count=sum(p['frontier'] for p in feasible),
                reason=('单一需求不足以判断跨需求稳健性' if len(expected) < 2 else
                        '实验尚未完成，当前筛选仅供观察' if not final else
                        '所有已测需求均通过原约束，按最差全网耗时增幅优先，再比较邻区代价与新增过饱和' if winner else
                        '没有证据完整且在全部设定需求下通过约束的同一措施，保留各需求基准或原条件建议'),
                rule='先满足全部已设需求的原实验约束；最小化最差全网耗时增幅，其次邻区增幅、邻区新增过饱和里程。仅解释已测措施，不推断未测需求。')
    if version == 'tolerance-v2':
        from .selection import select
        selection = select(feasible)
        result.update(version='paired-tolerance-v2', selection=selection, rule=selection['rule'],
                      recommendation=selection['recommendation'] if final else None)
        if final and selection['recommendation']:
            result['reason'] = '全部已设需求通过原约束；应用预设容差后优先简单措施。精确权衡前沿仍保留供比较。'
    return result


def spatial_effects(links):
    """Signed qL/v cost decomposition; simultaneous gains cannot hide local losses."""
    effects, added, saved, new_km = [], 0.0, 0.0, 0.0
    for link in links:
        before, after = link['before'], link['after']
        delta = link['length'] * (after[1] / after[0] - before[1] / before[0])
        added += max(0, delta)
        saved += max(0, -delta)
        newly = before[2] <= 1 < after[2]
        if newly:
            new_km += link['length']
        effects.append(dict(id=link['id'], name=link['name'], start=link['start'], end=link['end'], group=link['group'],
                            time_change_pcu_h=delta, speed_change=after[0]-before[0], new_overloaded=newly))
    return dict(added_time_pcu_h=added, saved_time_pcu_h=saved, net_time_pcu_h=added-saved,
                new_network_overloaded_km=new_km,
                hotspots=sorted((r for r in effects if r['time_change_pcu_h'] > 1e-9), key=lambda r: (-r['time_change_pcu_h'], r['id']))[:5],
                improvements=sorted((r for r in effects if r['time_change_pcu_h'] < -1e-9), key=lambda r: (r['time_change_pcu_h'], r['id']))[:5])
