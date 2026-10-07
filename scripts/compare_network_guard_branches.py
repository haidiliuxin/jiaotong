"""Audit two completed parking branches with identical inputs and first policy.

The comparison is retrospective; it does not create a second live policy arm.
"""
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


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(job):
    state = read(job/'state.json')
    task = Task.from_dict(state['task'])
    scope = read(job/'scopes.json')
    baseline = results(scenario_path(job, state['runs'][0]))
    output = []
    for record in state['runs'][1:3]:
        rows = results(scenario_path(job, record))
        expected = compare(baseline, rows, scope, task)
        if expected != record['comparison']:
            raise ValueError(f"{job.name}/{record['name']}保存的比较与原始输出不一致")
        gross = sum(rows[i]['length_km'] for i in scope['network'] if baseline[i]['vc'] <= 1 < rows[i]['vc'])
        if 'new_network_oversaturated_km' in expected and abs(gross-expected['new_network_oversaturated_km']) > 1e-9:
            raise ValueError('毛新增数值核对失败')
        output.append(dict(name=record['name'], changes=record['changes'], eligible=expected['eligible'],
                           gross_new_network_oversaturated_km=gross,
                           core_time_change_pct=expected['delta']['core']['running_time_change_pct'],
                           network_time_change_pct=expected['delta']['network']['running_time_change_pct'],
                           network_net_oversaturated_km=expected['delta']['network']['oversaturated_km_change'],
                           violations=expected['violations']))
    return state, task, output


def compare_jobs(old_job, guarded_job):
    old_job, guarded_job = old_job.resolve(), guarded_job.resolve()
    audits = {job.name: verify(job, archived=True, write=False) for job in (old_job, guarded_job)}
    old, old_task, old_runs = collect(old_job)
    guarded, guarded_task, guarded_runs = collect(guarded_job)
    if old['status'] != 'completed' or guarded['status'] != 'completed':
        raise ValueError('仅比较已完成任务')
    if old_task.max_new_network_oversaturated_km is not None or guarded_task.max_new_network_oversaturated_km is None:
        raise ValueError('对照与门槛任务卡不符合预期')
    left = {k:v for k,v in old_task.as_dict().items() if k != 'request'}
    right = {k:v for k,v in guarded_task.as_dict().items() if k not in ('request','max_new_network_oversaturated_km')}
    if left != right:
        raise ValueError('两任务存在门槛以外的参数差异')
    if read(old_job/'source_inputs.json') != read(guarded_job/'source_inputs.json'):
        raise ValueError('原始输入指纹不同')
    if read(old_job/'scopes.json') != read(guarded_job/'scopes.json'):
        raise ValueError('冻结评价范围不同')
    if read(old_job/'diagnosis.json')['selected'] != read(guarded_job/'diagnosis.json')['selected']:
        raise ValueError('基准诊断选区不同')
    if old_runs[0]['changes'] != guarded_runs[0]['changes']:
        raise ValueError('首轮治理输入不同')
    exact = {}
    for label in ('baseline','parking_1','baseline_repeat'):
        a = next(r for r in old['runs'] if r['name'] == label)
        b = next(r for r in guarded['runs'] if r['name'] == label)
        matches = {name: sha(scenario_path(old_job,a)/'result'/filename) ==
                         sha(scenario_path(guarded_job,b)/'result'/filename)
                   for name, filename in OUTPUTS.items()}
        if not all(matches.values()):
            raise ValueError(f'{label}原始路段数组不一致')
        exact[label] = matches
    if old['run_count'] != guarded['run_count'] or old['run_count'] != 4:
        raise ValueError('两任务场景预算不同')
    return dict(kind='retrospective_matched_branch_comparison',new_model_calls=0,
                old_job=old['id'],guarded_job=guarded['id'],limit_km=guarded_task.max_new_network_oversaturated_km,
                source_archive_sha256={old['id']:sha(old_job/'archive_manifest.json'),
                                       guarded['id']:sha(guarded_job/'archive_manifest.json')},
                audits={name:dict(passed=a['passed'],completed_scenarios=a['completed_scenarios'],
                                  module_executions=a['module_executions']) for name,a in audits.items()},
                source_inputs_exact=True,scope_exact=True,selected_area_exact=True,
                common_output_arrays_exact=exact,old_recommendation=old['recommendation'],
                guarded_recommendation=guarded['recommendation'],old_candidates=old_runs,
                guarded_candidates=guarded_runs,
                limitation='回顾性比较，虽输入和首轮原始数组完全相同且预算同为4场景，旧v4与新版为不同时刻的任务；'
                           '不同第二候选是控制器分支结果，不是事前注册的随机对照或官方评分证明')


def render(report):
    lines = ['# 全网毛新增上限：同输入、同首轮的历史分支对照', '',
             f"旧 v4 任务 `{report['old_job']}` 与新增风险上限任务 `{report['guarded_job']}` 均完成 4 个场景、28 次 TranStar 顶层调用，封存后独立核验通过。",
             '两任务原始源输入指纹、冻结评价范围、基准诊断、首轮措施相同；基准、首轮和基准复跑的四类路段数组逐字节相同。'
             '以下只读对照没有新建模型调用。', '',
             f"旧任务推荐 `{report['old_recommendation']}`；设置全网毛新增≤{report['limit_km']:g}km 后推荐 `{report['guarded_recommendation']}`。",
             '两者名称都叫 `parking_2`，但实际措施不同，必须按任务编号区分。', '',
             '| 任务 | 候选 | 停车措施 | 核心耗时变化 | 全网耗时变化 | 全网毛新增过饱和 | 合格 |',
             '| --- | --- | --- | ---: | ---: | ---: | --- |']
    for label, rows in [('旧v4',report['old_candidates']),('新门槛',report['guarded_candidates'])]:
        for row in rows:
            actions = '、'.join(f"{c['from_node']}→{c['to_node']}（{c['parking']}级）" for c in row['changes'])
            lines.append(f"| {label} | {row['name']} | {actions} | {row['core_time_change_pct']:+.3f}% | "
                         f"{row['network_time_change_pct']:+.4f}% | {row['gross_new_network_oversaturated_km']:.3f} km | {row['eligible']} |")
    lines += ['', '新任务首轮三路段停车2级虽然改善核心，却因全网毛新增0.785km超过0.1km上限而被拒绝；'
              '控制器按实测反馈缩为单路段，后者毛新增为0，仍有核心收益。',
              '旧任务在首轮通过原约束后增强为三路段停车1级；新旧第二候选并非相同政策。',
              '此比较证明安全门槛改变了实际决策分支，未证明交通收益或官方竞赛分数提高。'
              '门槛数值是本次实验假设，且只测小汽车需求系数1.0。', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('old_job',type=Path)
    parser.add_argument('guarded_job',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=compare_jobs(args.old_job,args.guarded_job)
    args.output.mkdir(parents=True,exist_ok=True)
    (args.output/'comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',
                                               encoding='utf-8',newline='\n')
    (args.output/'README.md').write_text(render(report),encoding='utf-8',newline='\n')
    print(json.dumps(dict(old=report['old_recommendation'],guarded=report['guarded_recommendation'],
                          first_gross_km=report['guarded_candidates'][0]['gross_new_network_oversaturated_km'],
                          second_gross_km=report['guarded_candidates'][1]['gross_new_network_oversaturated_km']),
                     ensure_ascii=False))
