"""Read-only comparison of sealed four- and six-scene network-guard jobs."""
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


def review(old_job, new_job):
    old_job, new_job = old_job.resolve(), new_job.resolve()
    audits = {p.name: verify(p, archived=True, write=False) for p in (old_job, new_job)}
    old, new = (load(p/'state.json') for p in (old_job, new_job))
    if old['status'] != 'completed' or new['status'] != 'completed':
        raise ValueError('两任务都必须完成')
    if (old['run_count'], new['run_count']) != (4, 6):
        raise ValueError('任务场景预算与协议不符')
    if any(not audit['passed'] for audit in audits.values()):
        raise ValueError('封存任务核验失败')
    old_task, new_task = (Task.from_dict(state['task']) for state in (old, new))
    relevant = lambda task: {k:v for k,v in task.as_dict().items()
                             if k not in ('request', 'max_runs', 'max_feedback')}
    if relevant(old_task) != relevant(new_task) or new_task.max_new_network_oversaturated_km != .1:
        raise ValueError('任务卡除事前指定的预算外发生变化')
    if load(old_job/'source_inputs.json') != load(new_job/'source_inputs.json'):
        raise ValueError('源输入指纹不一致')
    old_scope, new_scope = load(old_job/'scopes.json'), load(new_job/'scopes.json')
    if old_scope != new_scope:
        raise ValueError('固定评价范围不一致')
    if load(old_job/'diagnosis.json')['selected'] != load(new_job/'diagnosis.json')['selected']:
        raise ValueError('基准诊断选区不一致')
    common = {}
    for name in ('baseline', 'parking_1', 'parking_2', 'baseline_repeat'):
        a = next(r for r in old['runs'] if r['name'] == name)
        b = next(r for r in new['runs'] if r['name'] == name)
        if a['changes'] != b['changes']:
            raise ValueError(f'{name}措施不一致')
        matches = {key: sha(scenario_path(old_job,a)/'result'/filename) ==
                        sha(scenario_path(new_job,b)/'result'/filename)
                   for key, filename in OUTPUTS.items()}
        if not all(matches.values()):
            raise ValueError(f'{name}原始数组不一致')
        common[name] = matches
    first_changes = new['runs'][1]['changes']
    if len(first_changes) != 3:
        raise ValueError('联合候选入选方向数不符')
    if [r['changes'] for r in new['runs'][2:5]] != [[c] for c in first_changes]:
        raise ValueError('逐方向试验顺序不是冻结选区原顺序')
    if [d['next_changes'] for d in new['decisions'][:3]] != [[c] for c in first_changes]:
        raise ValueError('反馈决策没有在下一次运行前记录逐方向候选')
    baseline = results(scenario_path(new_job,new['runs'][0]))
    candidates = []
    for record in new['runs'][1:5]:
        expected = compare(baseline, results(scenario_path(new_job,record)), new_scope, new_task)
        if expected != record['comparison']:
            raise ValueError(f"{record['name']}约束与原始数组不一致")
        candidates.append(dict(name=record['name'], changes=record['changes'],
                               eligible=expected['eligible'], violations=expected['violations'],
                               gross_new_network_oversaturated_km=expected['new_network_oversaturated_km'],
                               core_time_change_pct=expected['delta']['core']['running_time_change_pct'],
                               network_time_change_pct=expected['delta']['network']['running_time_change_pct'],
                               neighbor_time_change_pct=expected['delta']['neighbor']['running_time_change_pct']))
    return dict(kind='prospective_singleton_scan_audit', new_model_calls=0,
                old_job=old['id'], new_job=new['id'], old_recommendation=old['recommendation'],
                new_recommendation=new['recommendation'], old_scenarios=4, new_scenarios=6,
                old_top_level_calls=28, new_top_level_calls=42, extra_top_level_calls=14,
                source_inputs_exact=True, scope_exact=True, selected_area_exact=True,
                common_raw_arrays_exact=common, ordered_singleton_decisions=True,
                candidates=candidates, audits={k:dict(passed=v['passed'],
                   completed_scenarios=v['completed_scenarios'],module_executions=v['module_executions'])
                   for k,v in audits.items()},
                archive_sha256={old['id']:sha(old_job/'archive_manifest.json'),
                                new['id']:sha(new_job/'archive_manifest.json')},
                limitation='新增14次模型调用后的候选覆盖比较，不是等预算算法消融；只测同一案例、需求1.0，不能推断官方评分或跨需求稳健')


def render(report):
    lines=['# 全网风险门槛下逐方向候选实测', '',
           f"旧任务 `{report['old_job']}` 用4场景28次顶层调用；新任务 `{report['new_job']}` 用6场景42次调用。两者输入、选区、评价范围以及共同四场景的16组原始路段数组完全相同。",
           f"旧任务建议 `{report['old_recommendation']}`；新任务建议 `{report['new_recommendation']}`。候选名只在各自任务内有效，比较时须看具体方向。", '',
           '| 新任务候选 | 停车2级方向 | 核心耗时变化 | 全网耗时变化 | 全网毛新增过饱和 | 合格 |',
           '| --- | --- | ---: | ---: | ---: | --- |']
    for c in report['candidates']:
        actions='、'.join(f"{x['from_node']}→{x['to_node']}" for x in c['changes'])
        lines.append(f"| {c['name']} | {actions} | {c['core_time_change_pct']:+.3f}% | "
                     f"{c['network_time_change_pct']:+.4f}% | "
                     f"{c['gross_new_network_oversaturated_km']:.3f} km | {c['eligible']} |")
    lines += ['', '这一轮多花14次原生顶层调用，验证原诊断的三个方向各自效果。'
              '两条单路段合格，但原先首个方向在既定规则下仍是最终建议；额外搜索没有改善此任务的最终方案。',
              report['limitation'], '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_job',type=Path)
    parser.add_argument('new_job',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=review(args.old_job,args.new_job)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'review.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (args.output/'README.md').write_text(render(report),encoding='utf-8')
    print(json.dumps(dict(old=report['old_recommendation'],new=report['new_recommendation'],
                          candidates=[(x['name'],x['eligible']) for x in report['candidates']]),ensure_ascii=False))
