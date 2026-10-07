"""Compare the preregistered Xingpu backtrack run against its sealed predecessor."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS
from traffic_agent.decision_analysis import policy_key
from traffic_agent.map_data import scenario_path
from verify_experiment import verify


ROOT = Path(__file__).resolve().parents[1]
OLD_JOB = ROOT / '智能体实验/jobs/20260923_075629_706ef0'
CARD = ROOT / 'examples/parking_robust_guard_backtrack_xingpu.json'
EXPECTED_CARD_SHA = '8867273f99594fe99c942616964a47052cc0fff956da8e63181ae401dfa86aa9'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review(job):
    job = job.resolve()
    if sha(CARD) != EXPECTED_CARD_SHA:
        raise ValueError('预注册任务卡的摘要被改变')
    old, new = read(OLD_JOB / 'state.json'), read(job / 'state.json')
    if old['status'] != 'completed' or new['status'] != 'completed':
        raise ValueError('两个任务都必须已完成')
    if old['task'].get('backtrack_on_singleton_failure') or new['task'].get('backtrack_on_singleton_failure') is not True:
        raise ValueError('新旧搜索版本开关不符合预注册')
    card = read(CARD)
    if any(new['task'].get(key) != val for key, val in card.items()):
        raise ValueError('实际任务与预注册卡不一致')
    if any(old['task'].get(key) != new['task'].get(key) for key in
           ('links', 'max_runs', 'demand_factors', 'max_new_network_oversaturated_km',
            'max_neighbor_time_increase_pct', 'max_network_time_increase_pct',
            'neighbor_hops', 'recommendation_version')):
        raise ValueError('新旧任务的区域、预算、需求或硬门槛改变')
    old_audit, new_audit = verify(OLD_JOB, archived=True, write=False), verify(job, archived=True, write=False)
    for audit in (old_audit, new_audit):
        if (not audit['passed'] or not audit['portable_archive_verified']
                or not all(audit['repeat_arrays_exact'].values())
                or not all(audit['active_search_audit'].values())
                or audit['module_executions'] != 7 * audit['completed_scenarios']):
            raise ValueError('封存、原生调用、基准复跑或决策重放失败')
    if read(OLD_JOB / 'source_inputs.json') != read(job / 'source_inputs.json'):
        raise ValueError('模型输入源改变')
    if read(OLD_JOB / 'scopes.json') != read(job / 'scopes.json'):
        raise ValueError('评价范围改变')
    if read(OLD_JOB / 'diagnosis.json') != read(job / 'diagnosis.json'):
        raise ValueError('初次基准诊断改变')
    old_runs = {r['name']: r for r in old['runs']}
    shared = {}
    for run in new['runs']:
        other = old_runs.get(run['name'])
        if other is None:
            continue
        if run['changes'] != other['changes'] or run['demand_factor'] != other['demand_factor']:
            raise ValueError(f"相同名称的任务调用参数改变：{run['name']}")
        same = {key: sha(scenario_path(job, run) / 'result' / name)
                      == sha(scenario_path(OLD_JOB, other) / 'result' / name)
                for key, name in OUTPUTS.items()}
        if not all(same.values()):
            raise ValueError(f"相同参数的模型原始数组改变：{run['name']}")
        shared[run['name']] = same
    if set(shared) != set(old_runs):
        raise ValueError('旧任务已测场景未全部在新任务中按相同输入复核')
    if new['robust_search']['version'] != 'adaptive-paired-search-v2' or old['robust_search']['version'] != 'adaptive-paired-search-v1':
        raise ValueError('搜索协议版本与预期不符')
    if len(new['robust_search']['backtracks']) < 1:
        raise ValueError('本任务未实际触发失败后的回退')
    rows, policies = [], {}
    for run in new['runs']:
        if not run['changes']:
            continue
        c = run['comparison']
        policies.setdefault(policy_key(run['changes']), []).append(run)
        rows.append(dict(name=run['name'], demand=run['demand_factor'],
                         directions=[f"{x['from_node']}→{x['to_node']}" for x in run['changes']],
                         parking_level=run['changes'][0]['parking'],
                         core_time_pct=c['delta']['core']['running_time_change_pct'],
                         neighbor_time_pct=c['delta']['neighbor']['running_time_change_pct'],
                         network_time_pct=c['delta']['network']['running_time_change_pct'],
                         gross_new_network_km=c['new_network_oversaturated_km'],
                         eligible=c['eligible'], violations=c['violations']))
    complete = [dict(changes=runs[0]['changes'],
                     factors=sorted({r['demand_factor'] for r in runs}),
                     all_eligible=all(r['comparison']['eligible'] for r in runs))
                for runs in policies.values()
                if {r['demand_factor'] for r in runs} == set(card['demand_factors'])]
    winner = next(r for r in new['runs'] if r['name'] == new['recommendation'])
    if winner['changes']:
        if not any(policy_key(x['changes']) == policy_key(winner['changes']) and x['all_eligible'] for x in complete):
            raise ValueError('统一建议不是完整三需求安全合格措施')
    elif new['robust_across_tested_demand'] is not None:
        raise ValueError('基准保留被误写成治理通过')
    return dict(kind='preregistered_same_region_backtrack_development_review_v1',
                new_model_calls_during_review=0, old_job=old['id'], new_job=new['id'],
                preregistered_task_sha256=sha(CARD),
                old_archive_sha256=sha(OLD_JOB / 'archive_manifest.json'),
                new_archive_sha256=sha(job / 'archive_manifest.json'),
                original_source_inputs_exact=True, frozen_scope_exact=True,
                shared_native_raw_arrays_exact=shared,
                old_scenarios=len(old['runs']), new_scenarios=len(new['runs']),
                new_native_calls=new_audit['module_executions'],
                decision_replay=new_audit['active_search_audit'],
                repeat_arrays_exact=new_audit['repeat_arrays_exact'],
                old_recommendation=old['recommendation'], new_recommendation=new['recommendation'],
                recommended_changes=winner['changes'],
                full_demand_eligible_policies=[p for p in complete if p['all_eligible']],
                search_stop=new['robust_search'], rows=rows,
                limitation='同一已知负例上的算法开发性复测；反馈上限也由2改为3，不能据此估计盲测得分、统计泛化或现实实施效果')


def render(result):
    lines = ['# 兴浦路单路段失败回退：真实模型复测', '',
             f"新任务 `{result['new_job']}`：{result['new_scenarios']}场景、"
             f"{result['new_native_calls']}次TranStar顶层调用；旧任务 `{result['old_job']}`："
             f"{result['old_scenarios']}场景。两任务严格封存核验、决策回放、基准复跑通过。",
             f"新旧同名同参数的{len(result['shared_native_raw_arrays_exact'])}个场景，四类原生路段数组逐字节一致。",
             f"旧统一建议：`{result['old_recommendation']}`；新统一建议：`{result['new_recommendation']}`。", '',
             '| 新任务候选 | 需求 | 方向 / 强度 | 核心耗时 | 邻区耗时 | 全网耗时 | 全网毛新增过饱和 | 判定 |',
             '| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |']
    for row in result['rows']:
        lines.append(f"| {row['name']} | {row['demand']:g} | {', '.join(row['directions'])} / 停车{row['parking_level']}级 | "
                     f"{row['core_time_pct']:+.3f}% | {row['neighbor_time_pct']:+.3f}% | "
                     f"{row['network_time_pct']:+.4f}% | {row['gross_new_network_km']:.3f} km | "
                     f"{'通过' if row['eligible'] else '拒绝'} |")
    lines.append('')
    for row in result['rows']:
        if row['violations']:
            lines.append(f"- `{row['name']}`：{'; '.join(row['violations'])}。")
    lines += ['', f"实际回退次数：{len(result['search_stop']['backtracks'])}；"
              f"停止原因：{result['search_stop']['stop_reason']}；"
              f"剩余预算：{result['search_stop']['unused_budget']}场景。",
              '每次回退都只用无治理基准qL/v排次序；未测需求不算通过。耗时为路段Σ(qL/v)，'
              '毛新增过饱和为新出现的有向路段里程，均不等于现实完整延误。',
              result['limitation'] + '。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('不能覆盖已有复评结果')
    result = review(args.job)
    args.output.mkdir(parents=True)
    (args.output / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (args.output / 'README.md').write_text(render(result), encoding='utf-8')
    print(json.dumps(dict(job=result['new_job'], scenes=result['new_scenarios'],
                          recommendation=result['new_recommendation']), ensure_ascii=False))
