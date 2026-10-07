"""Read-only local impact and evidence-boundary appraisal of saved runs.

This does not change frozen eligibility or pretend to estimate field accuracy.
Gross increases and savings use the same qL/v pcu-hour measure as core metrics.
"""
import math

VERSION = 'professional-impact-v1'


def numeric(value, *, positive=False):
    return type(value) in (int, float) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def core_links(run):
    rows = run.get('core_rows')
    if not isinstance(rows, list) or not rows:
        return None, '缺少完整核心路段观测'
    result = {}
    for row in rows:
        if not isinstance(row, dict) or row.get('included') is not True:
            return None, '核心路段观测不完整'
        index = row.get('index')
        if (type(index) is not int or index in result or
                any(type(row.get(k)) is not int for k in ('from_node', 'to_node')) or
                not numeric(row.get('length_km'), positive=True) or
                not numeric(row.get('flow_pcu_h')) or
                not numeric(row.get('speed_kmh'), positive=True)):
            return None, '核心路段标识或流量、长度、速度无效'
        value = row['flow_pcu_h'] * row['length_km'] / row['speed_kmh']
        if not math.isfinite(value):
            return None, '核心路段耗时不可计算'
        result[index] = (row, value)
    total = (run.get('metrics') or {}).get('core', {}).get('running_time_pcu_h')
    if not numeric(total) or not math.isclose(
            total, sum(value for _, value in result.values()), rel_tol=1e-8, abs_tol=1e-8):
        return None, '核心路段耗时与场景汇总不一致'
    return result, None


def paired(base,run):
    before, gap = core_links(base)
    if gap:
        return dict(status='unverified', gap='基准：' + gap)
    after, gap = core_links(run)
    if gap:
        return dict(status='unverified', gap='治理：' + gap)
    if set(before) != set(after) or any(
            (before[index][0]['from_node'], before[index][0]['to_node'], before[index][0]['length_km']) !=
            (after[index][0]['from_node'], after[index][0]['to_node'], after[index][0]['length_km'])
            for index in before):
        return dict(status='unverified', gap='基准与治理的核心有向路段不一致')
    added = saved = 0.0
    increases = []
    for index, (base_row, base_cost) in before.items():
        row, cost = after[index]
        delta = cost - base_cost
        if delta > 0:
            added += delta
            increases.append(dict(index=index, name=row.get('name') or base_row.get('name') or '',
                                  from_node=row['from_node'], to_node=row['to_node'],
                                  before_pcu_h=base_cost, after_pcu_h=cost, added_pcu_h=delta))
        elif delta < 0:
            saved -= delta
    increases.sort(key=lambda row: (-row['added_pcu_h'], row['index']))
    return dict(status='measured', gap=None, core_link_count=len(before),
                worsened_link_count=len(increases), gross_added_pcu_h=added,
                gross_saved_pcu_h=saved, net_change_pcu_h=added - saved,
                largest_increases=increases[:3])


def review(state):
    """Keep per-link harm visible even when the aggregate core improves."""
    task = state.get('task') or {}
    runs = [r for r in state.get('runs', []) if isinstance(r, dict) and
            (r.get('execution') or {}).get('status') == 'completed' and r.get('metrics')]
    baselines = {}
    for run in runs:
        if not run.get('changes'):
            baselines.setdefault(run.get('demand_factor'), []).append(run)
    policies = []
    for run in runs:
        if not run.get('changes'):
            continue
        factor = run.get('demand_factor')
        bases = baselines.get(factor, [])
        item = dict(run=run.get('name'), demand_factor=factor,
                    baseline_ref=bases[0].get('name') if bases else None,
                    constraint_passed=(run.get('comparison') or {}).get('eligible'))
        if not bases:
            item.update(status='unverified', gap='缺少同需求的新基准')
        elif any(b.get('core_rows')!=bases[0].get('core_rows') or
                 (b.get('metrics') or {}).get('core')!=(bases[0].get('metrics') or {}).get('core')
                 for b in bases[1:]):
            item.update(status='unverified', gap='重复基准的核心路段观测不一致，不能择优配对')
        else:
            item.update(paired(bases[0], run))
        if item['status'] == 'unverified':
            item.update(core_link_count=None, worsened_link_count=None, gross_added_pcu_h=None,
                        gross_saved_pcu_h=None, net_change_pcu_h=None, largest_increases=[])
        policies.append(item)
    repeats = [r for r in runs if r.get('name') == 'baseline_repeat' and not r.get('changes')]
    repeated = bool(repeats)
    repeatability = state.get('repeatability') or {}
    baseline_consistent = all(
        b.get('core_rows') == bases[0].get('core_rows') and
        (b.get('metrics') or {}).get('core') == (bases[0].get('metrics') or {}).get('core')
        for bases in baselines.values() for b in bases[1:])
    repeat_status = ('not_requested' if not task.get('repeat_baseline') else
                     'exact_match' if repeated and baseline_consistent and
                     repeatability.get('exact_match') is True else
                     'inconsistent_or_unverified' if repeated else 'not_completed')
    next_checks = []
    if any(p['status'] == 'unverified' for p in policies):
        next_checks.append(dict(id='repair_pair', kind='record_review',
            action='先核对同需求基准、完整核心路段和汇总指标；缺测或冲突时不得用净收益作局部判断。'))
    if any(p['status'] == 'measured' and p['worsened_link_count'] for p in policies):
        next_checks.append(dict(id='local_loss', kind='planning_review',
            action='逐条检查耗时增加的核心方向，再判断是否需要追加局部保护约束或新备选试验；现有原定门槛保持不变。'))
    if len(task.get('demand_factors', [])) < 2:
        next_checks.append(dict(id='demand_sensitivity', kind='new_simulations_required',
            action='若要判断需求波动稳健性，另按预先指定的需求水平，对同一措施和各自新基准完成配对仿真。'))
    if repeat_status != 'exact_match':
        next_checks.append(dict(id='repeatability', kind='new_simulations_required',
            action='若要核对同输入计算一致性，补做基准复跑；一次或确定性复跑都不能当作统计置信区间。'))
    next_checks.append(dict(id='field_validation', kind='external_evidence_required',
        action='若用于现实决策，另以现场流量和旅行时间等独立观测校核模型；本任务没有建立现实预测精度。'))
    return dict(version=VERSION, policies=policies, evidence_scope=dict(
        demand_levels=list(task.get('demand_factors', [])), baseline_repeated=repeated,
        repeat_status=repeat_status, field_validation='not_established_from_job_record',
        statistical_confidence='not_estimated'), next_checks=next_checks,
        rule='基于同需求原生场景的核心有向路段流量×长度÷速度，分别求正增量与改善量；净改善不能隐藏某条路段的实际增加。',
        boundary='这是批次仿真的描述性配对核查，不是新增推荐硬门槛；无现场标定记录或统计重复时，不估计现实效果或置信区间。')
