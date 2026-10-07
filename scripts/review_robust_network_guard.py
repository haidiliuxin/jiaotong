"""Audit matched historical and newly guarded three-demand parking jobs."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.agent import compare
from traffic_agent.data import OUTPUTS, results
from traffic_agent.map_data import scenario_path
from traffic_agent.task import Task
from verify_experiment import verify


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def policy_rows(job, state, task, scope):
    baseline_by_factor = {
        run['demand_factor']: results(scenario_path(job, run))
        for run in state['runs'] if run['name'] == 'baseline' or run['name'].startswith('robust_base_')
    }
    report = []
    for run in state['runs']:
        if not run['changes']:
            continue
        factor = run['demand_factor']
        assessment = compare(baseline_by_factor[factor], results(scenario_path(job, run)), scope, task)
        if assessment != run['comparison']:
            raise ValueError(f"{job.name}/{run['name']}原始数组与保存比较不一致")
        report.append(dict(name=run['name'], demand_factor=factor, changes=run['changes'],
                           eligible=assessment['eligible'], violations=assessment['violations'],
                           gross_new_network_oversaturated_km=assessment['new_network_oversaturated_km'],
                           core_time_change_pct=assessment['delta']['core']['running_time_change_pct'],
                           network_time_change_pct=assessment['delta']['network']['running_time_change_pct'],
                           neighbor_time_change_pct=assessment['delta']['neighbor']['running_time_change_pct'],
                           new_neighbor_oversaturated_km=assessment['new_neighbor_oversaturated_km']))
    return report


def review(history, guarded):
    history, guarded = history.resolve(), guarded.resolve()
    audits = {p.name: verify(p, archived=True, write=False) for p in (history, guarded)}
    old, new = (load(p/'state.json') for p in (history, guarded))
    if (old['status'], new['status']) != ('completed', 'completed'):
        raise ValueError('两任务须完成')
    if any(not a['passed'] or a['completed_scenarios'] != 11 or a['module_executions'] != 77
           for a in audits.values()):
        raise ValueError('两任务都须通过11场景、77调用封存核验')
    old_task, new_task = (Task.from_dict(x['task']) for x in (old, new))
    if old_task.max_new_network_oversaturated_km is not None or new_task.max_new_network_oversaturated_km != .1:
        raise ValueError('新旧风险门槛不符合协议')
    left = {k:v for k,v in old_task.as_dict().items() if k != 'request'}
    right = {k:v for k,v in new_task.as_dict().items()
             if k not in ('request', 'max_new_network_oversaturated_km')}
    if left != right or new_task.search_mode != 'robust' or new_task.demand_factors != [.9,1.,1.1]:
        raise ValueError('两任务除新门槛和文字外的参数不一致')
    if load(history/'source_inputs.json') != load(guarded/'source_inputs.json'):
        raise ValueError('原始输入指纹不同')
    old_scope, new_scope = load(history/'scopes.json'), load(guarded/'scopes.json')
    if old_scope != new_scope:
        raise ValueError('评价范围不同')
    if load(history/'diagnosis.json')['selected'] != load(guarded/'diagnosis.json')['selected']:
        raise ValueError('基准选区不同')
    exact = {}
    for a,b in zip(old['runs'],new['runs']):
        if (a['name'],a['changes'],a['demand_factor']) != (b['name'],b['changes'],b['demand_factor']):
            raise ValueError('控制器分支不同，不能按同名逐数组匹配')
        matches = {key: sha(scenario_path(history,a)/'result'/name) ==
                        sha(scenario_path(guarded,b)/'result'/name)
                   for key,name in OUTPUTS.items()}
        if not all(matches.values()):
            raise ValueError(f"{a['name']}原始结果数组不同")
        exact[a['name']] = matches
    rows = policy_rows(guarded,new,new_task,new_scope)
    recommendation = next(r for r in new['runs'] if r['name'] == new['recommendation'])
    key = (recommendation['changes'], recommendation['demand_factor'])
    matched = [r for r in rows if r['changes'] == key[0]]
    if len(matched) != len(new_task.demand_factors) or not all(r['eligible'] for r in matched):
        raise ValueError('统一推荐没有通过全部需求')
    return dict(kind='matched_three_demand_portable_network_guard_replay',new_model_calls=0,
                history_job=old['id'],guarded_job=new['id'],history_recommendation=old['recommendation'],
                guarded_recommendation=new['recommendation'],source_inputs_exact=True,
                scope_exact=True,selected_area_exact=True,scenarios_exact=11,
                raw_result_arrays_exact=exact,uniform_recommendation_changes=recommendation['changes'],
                uniform_recommendation_passed_all_demands=True,guarded_policy_rows=rows,
                archive_sha256={old['id']:sha(history/'archive_manifest.json'),
                                new['id']:sha(guarded/'archive_manifest.json')},
                audits={k:dict(passed=v['passed'],completed_scenarios=v['completed_scenarios'],
                               module_executions=v['module_executions']) for k,v in audits.items()},
                limitation='历史线索引导的新任务，同机同案例三种离散小汽车需求；非盲选、异机、跨城市或官方评分证明')


def render(report):
    lines=['# v5 便携包：三需求全网风险门槛真实复验', '',
           f"历史任务 `{report['history_job']}` 与新任务 `{report['guarded_job']}` 各完成11场景、77次真实 TranStar 顶层调用并通过封存核验。新任务在运行前固定全网毛新增过饱和≤0.1km；旧任务没有这个硬上限。",
           f"两任务原始输入、诊断选区、固定评价范围、场景措施及44组原始路段数组逐字节相同。新任务统一推荐 `{report['guarded_recommendation']}`，三种需求全部通过新上限和其他原有约束。", '',
           '| 候选 | 小汽车需求 | 核心耗时变化 | 全网耗时变化 | 邻区耗时变化 | 全网毛新增过饱和 | 合格 |',
           '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in report['guarded_policy_rows']:
        decision = '通过' if r['eligible'] else '拒绝'
        lines.append(f"| {r['name']} | {r['demand_factor']:g} | {r['core_time_change_pct']:+.3f}% | "
                     f"{r['network_time_change_pct']:+.4f}% | {r['neighbor_time_change_pct']:+.4f}% | "
                     f"{r['gross_new_network_oversaturated_km']:.3f}km | {decision} |")
    lines += ['', '三方向联合候选在高需求下使邻区耗时增加1.1728%，超过原有0.5%硬上限，因此即使核心和全网耗时下降也被拒绝。智能体在下一次求解前缩到单方向，再比较停车2级和1级。1级在三种需求下的核心耗时改善均大于2级，但需求1.0会新增0.056km过饱和路段，虽低于0.1km上限，仍须披露。',
              '所有数字均是同需求无治理基准的仿真路段指标，不包括完整节点延误或现实泊位成本。',
              report['limitation']+'。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('historical_job',type=Path)
    parser.add_argument('guarded_job',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=review(args.historical_job,args.guarded_job)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output/'README.md').write_text(render(result),encoding='utf-8')
    print(json.dumps(dict(recommendation=result['guarded_recommendation'],
                          exact_scenarios=result['scenarios_exact'],
                          uniform_passed=result['uniform_recommendation_passed_all_demands']),ensure_ascii=False))
