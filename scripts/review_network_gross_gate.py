"""Read-only retrospective stress test of a predeclared gross-network gate.

This does not rerun TranStar or rewrite the archived recommendation. The
threshold is chosen after the original experiment and has no prospective claim.
"""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.agent import compare
from traffic_agent.data import results
from traffic_agent.decision_analysis import decision_analysis
from traffic_agent.map_data import scenario_path
from traffic_agent.selection import record_selection
from traffic_agent.task import Task


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(job, limit):
    job = Path(job).resolve()
    state_path, archive_path = job/'state.json', job/'archive_manifest.json'
    source_hashes = {'state': sha(state_path), 'archive_manifest': sha(archive_path)}
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if state['status'] != 'completed' or state['task']['scenario'] != 'circulation':
        raise ValueError('本分析只接受已完成的显式禁转完整任务')
    original_task = Task.from_dict(state['task'])
    if original_task.max_new_network_oversaturated_km is not None:
        raise ValueError('原任务已启用全网毛新增上限，不属于历史规则对照')
    guarded_task = Task.from_dict(dict(original_task.as_dict(), max_new_network_oversaturated_km=limit))
    scope = json.loads((job/'scopes.json').read_text(encoding='utf-8'))
    by_factor = {r['demand_factor']: r for r in state['runs'] if not r['changes'] and r['name'] != 'baseline_repeat'}
    bases = {f: results(scenario_path(job, r)) for f, r in by_factor.items()}
    revised = copy.deepcopy(state)
    revised['task'] = guarded_task.as_dict()
    rows = []
    for record in revised['runs']:
        if not record['changes']:
            continue
        factor = record['demand_factor']
        before, after = bases[factor], results(scenario_path(job, record))
        old = compare(before, after, scope, original_task)
        saved = record['comparison']
        if saved['violations'][:len(old['violations'])] != old['violations']:
            raise ValueError(f"{record['name']}历史约束与原始数组不一致")
        extra = saved['violations'][len(old['violations']):]
        if any(saved[k] != value for k, value in old.items() if k not in ('violations', 'eligible')):
            raise ValueError(f"{record['name']}历史指标与原始数组不一致")
        if saved['eligible'] != (not saved['violations']):
            raise ValueError(f"{record['name']}历史合格状态不一致")
        new = compare(before, after, scope, guarded_task)
        new['violations'].extend(extra)
        new['eligible'] = not new['violations']
        record['comparison'] = new
        rows.append(dict(name=record['name'], factor=factor,
                         recorded_eligible=saved['eligible'], guarded_eligible=new['eligible'],
                         new_network_oversaturated_km=new['new_network_oversaturated_km'],
                         net_network_oversaturated_km=new['delta']['network']['oversaturated_km_change'],
                         added_violations=[v for v in new['violations'] if v not in saved['violations']],
                         other_recorded_violations=saved['violations']))
    selected = {}
    for factor, baseline in by_factor.items():
        candidates = [r for r in revised['runs'] if r['changes'] and r['demand_factor'] == factor]
        winner = record_selection(candidates)['recommendation']
        selected[str(factor)] = winner or baseline['name']
    analysis = decision_analysis(revised, 'tolerance-v2')
    if {'state': sha(state_path), 'archive_manifest': sha(archive_path)} != source_hashes:
        raise ValueError('只读分析期间来源档案发生变化')
    return dict(kind='retrospective_gross_network_gate', source_job=state['id'],
                source_hashes=source_hashes, threshold_km=limit, new_model_calls=0,
                original_recorded_recommendation=state['recommendation'],
                original_conditional=state['recommendation_by_car_demand'],
                guarded_conditional=selected, guarded_unified=analysis['recommendation'],
                candidates=rows, limitations='回顾性阈值试算；没有新原生求解，也不是事前注册的治理效果提升实验')


def render(report):
    lines = ['# 全网毛新增过饱和门槛：历史禁转任务回顾性试算', '',
             f"来源任务 `{report['source_job']}` 已完成；本次仅重读封存的原始路段数组，TranStar新增调用数为0。",
             f"试算上限为 {report['threshold_km']:g} km；这是看到历史结果后的安全阈值示例，不是事前实验，也不证明得分提高。", '',
             f"原任务基准需求建议：`{report['original_recorded_recommendation']}`；试算后的统一建议：`{report['guarded_unified'] or '保留基准'}`。", '',
             '| 小汽车需求 | 原任务条件建议 | 加门槛后的条件建议 |', '| ---: | --- | --- |']
    for factor, chosen in sorted(report['guarded_conditional'].items(), key=lambda x: float(x[0])):
        lines.append(f"| {factor} | {report['original_conditional'][factor]} | {chosen} |")
    lines += ['', '| 候选 | 需求 | 全网毛新增 km | 全网净变化 km | 原合格 | 加门槛后合格 | 新增拒绝原因 |',
              '| --- | ---: | ---: | ---: | --- | --- | --- |']
    for row in report['candidates']:
        lines.append(f"| {row['name']} | {row['factor']} | {row['new_network_oversaturated_km']:.6f} | "
                     f"{row['net_network_oversaturated_km']:+.6f} | {row['recorded_eligible']} | "
                     f"{row['guarded_eligible']} | {'；'.join(row['added_violations']) or '无'} |")
    lines += ['', '毛新增只统计基准 V/C≤1、治理后 V/C>1 的有向道路长度。'
              '其他道路从过饱和恢复不能抵消它；来源任务的指标、响应和推荐均未改动。',
              '此试算只展示风险约束会如何改变选择，不能把保留基准解释为治理带来新的交通收益。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('--limit-km', type=float, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    task = Task.from_dict({'max_new_network_oversaturated_km': args.limit_km})
    result = review(args.job, task.max_new_network_oversaturated_km)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'review.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                                           encoding='utf-8', newline='\n')
    (args.output/'README.md').write_text(render(result), encoding='utf-8', newline='\n')
    print(json.dumps(dict(source_job=result['source_job'], guarded_conditional=result['guarded_conditional'],
                          guarded_unified=result['guarded_unified'], new_model_calls=0), ensure_ascii=False))
