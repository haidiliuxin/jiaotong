"""Execute the frozen study through the existing, licensed TranStar adapter."""
import hashlib
import json
import math
import platform
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .agent import Agent, compare, pct
from .data import (OUTPUTS, PROJECT, input_fingerprints, results, save_json,
                   scoped_metrics, scopes)
from .decision_analysis import decision_analysis, policy_key
from .runner import DEFAULT_SOURCE, EXPERIMENTS, MODULES, now, runtime_lock, setup_runtime, validate_source
from .study import STRATEGIES, candidate_actions, freeze_regions, protocol
from .task import Task

HISTORY = ('20260911_021806_858461', '20260911_024329_7304a4',
           '20260908_100153_bb7198', '20260908_075938_e7444a')


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def study_task():
    return Task.from_dict(dict(request='前瞻三层新区域、相同预算固定策略与实测反馈对照',
                               search_mode='robust', recommendation_version='tolerance-v2',
                               max_runs=10, max_feedback=1, max_links=3,
                               demand_factors=[0.9, 1.0, 1.1], repeat_baseline=True))


def risk_review(before, after, scope):
    inner = scopes(before, scope['core'], 1)
    a, b = scoped_metrics(before, inner)['neighbor'], scoped_metrics(after, inner)['neighbor']
    added, saved, new_km, losses = 0.0, 0.0, 0.0, []
    for i in scope['network']:
        x, y = before[i], after[i]
        delta = x['length_km'] * (y['flow_pcu_h']/y['speed_kmh'] - x['flow_pcu_h']/x['speed_kmh'])
        added += max(0, delta)
        saved += max(0, -delta)
        if x['vc'] <= 1 < y['vc']:
            new_km += x['length_km']
        if delta > 1e-9:
            losses.append(dict(from_node=x['from_node'], to_node=x['to_node'], name=x['name'],
                               time_increase_pcu_h=delta, new_overloaded=x['vc'] <= 1 < y['vc']))
    return dict(inner_neighbor_links=len(inner['neighbor']),
                inner_neighbor_time_change_pct=pct(a['running_time_pcu_h'], b['running_time_pcu_h']),
                new_network_oversaturated_km=new_km, gross_time_added_pcu_h=added,
                gross_time_saved_pcu_h=saved,
                worst_links=sorted(losses, key=lambda x: (-x['time_increase_pcu_h'], x['from_node'], x['to_node']))[:5])


def view_record(record, rows, region, task, before=None, name=None):
    result = dict(name=name or record['name'], physical_run=record['name'], changes=record['changes'],
                  demand_factor=record['demand_factor'], execution=record['execution'],
                  metrics=scoped_metrics(rows, region['scope']),
                  core_rows=[rows[i] for i in region['scope']['core']])
    if before is not None:
        result.update(comparison=compare(before, rows, region['scope'], task),
                      risk_review=risk_review(before, rows, region['scope']))
    return result


def strategy_result(task, records, summary):
    analysis = decision_analysis(dict(status='completed', task=task.as_dict(), runs=records))
    winner = analysis['recommendation']
    result = dict(summary=summary, analysis=analysis, recommendation=winner or 'baseline', risks=None)
    if winner:
        key = policy_key(next(r['changes'] for r in records if r['name'] == winner))
        chosen = [r for r in records if policy_key(r['changes']) == key]
        result['risks'] = dict(
            worst_new_network_oversaturated_km=max(r['risk_review']['new_network_oversaturated_km'] for r in chosen),
            worst_inner_neighbor_time_change_pct=max(r['risk_review']['inner_neighbor_time_change_pct'] for r in chosen),
            worst_gross_time_added_pcu_h=max(r['risk_review']['gross_time_added_pcu_h'] for r in chosen))
    return result


def render_report(state, selection):
    lines = ['# 新区域同预算前瞻对照', '', f"任务：`{state['id']}`；状态：{state['status']}。", '',
             '本轮使用新的 TranStar 原生计算。先登记协议，再按新基准选区；选区和评价范围在任何治理计算之前冻结。',
             '三层是本案例候选停车问题的相对密度，不代表城市地区的普遍分类。全部失败和缺测均保留。', '',
             '| 密度层/道路 | 策略 | 合格统一措施数 | 统一建议 | 最差全网耗时变化% | 最差邻区耗时变化% | 逻辑场景/10 |',
             '| --- | --- | ---: | --- | ---: | ---: | ---: |']
    for region in selection.get('regions', []):
        rid = region['id']
        if region['status'] != 'selected':
            lines.append(f"| {rid} | 无候选 | — | 保留空缺 | — | — | 0 |")
            continue
        for strategy in STRATEGIES:
            entry = state.get('regions', {}).get(rid, {}).get('strategies', {}).get(strategy, {})
            outcome = entry.get('result')
            if not outcome:
                lines.append(f"| {rid} / {'、'.join(region['roads'])} | {strategy} | — | 尚未完成 | — | — | — |")
                continue
            analysis = outcome['analysis']
            chosen = next((p for p in analysis['policies'] if p['name'] == analysis['recommendation']), None)
            worst = chosen['worst'] if chosen else None
            network, neighbor = (f'{worst[0]:+.6f}', f'{worst[1]:+.6f}') if worst else ('—', '—')
            lines.append(f"| {rid} / {'、'.join(region['roads'])} | {strategy} | {analysis['eligible_count']} | "
                         f"{outcome['recommendation']} | {network} | {neighbor} | {outcome['summary']['logical_scenes']} |")
    count = state['run_count']
    lines += ['', f"实际物理场景：{count}；已完成原生模块调用：{sum(len(r['execution']['modules']) for r in state['runs'])}。",
              '每区每策略逻辑场景包括三个需求基准、该策略实际评估的治理场景、一次基准复跑。相同输入共享结果，各策略仍分别计费。',
              '未启动的需求不会获得虚构结果，也不会视作通过。基准回退的表格用“—”表示无合格治理方案。', '',
              '## 选区与风险', '']
    for region in selection.get('regions', []):
        if region['status'] != 'selected':
            lines.append(f"- {region['id']}：{region['reason']}。")
            continue
        rid = region['id']
        lines += [f"### {rid}：{'、'.join(region['roads'])}", '',
                  f"种子 {region['seed']}；候选长度密度 {region['density']:.6f}；核心 {len(region['scope']['core'])} 条，邻区 {len(region['scope']['neighbor'])} 条。", '']
        for strategy in STRATEGIES:
            entry = state.get('regions', {}).get(rid, {}).get('strategies', {}).get(strategy, {})
            outcome = entry.get('result')
            if not outcome:
                continue
            lines.append(f"**{strategy}**：{outcome['summary']['stop_reason']}。")
            if outcome['risks']:
                risk = outcome['risks']
                lines.append(f"所选措施最差1跳邻区耗时变化 {risk['worst_inner_neighbor_time_change_pct']:+.6f}%；"
                             f"毛新增全网过饱和 {risk['worst_new_network_oversaturated_km']:.6f} km；"
                             f"恶化路段耗时增量合计最高 {risk['worst_gross_time_added_pcu_h']:.6f} pcu·h。")
            for policy in outcome['analysis']['policies']:
                reason = '全部三需求通过' if policy['feasible'] else '；'.join(policy['reasons']) or '证据不完整'
                lines.append(f"- `{policy['name']}`：{reason}；未测需求 {policy['missing']}。")
            lines.append('')
    lines += ['## 口径与可核验入口', '',
              '- 路段耗时为 Σ(qL/v)，不包含全部节点延误；有向里程不等于道路中心线长度。',
              '- 原硬约束：邻区耗时增幅≤0.5%，全网≤0.1%，邻区毛新增过饱和≤0.1 km，核心耗时须改善。',
              '- 推荐使用实验前冻结的 tolerance-v2，容差是公开的决策假设，不是统计显著性界限。',
              '- 1跳邻区和全网毛新增过饱和仅作风险复核，未在观察结果后改写通过条件。',
              '- 仅三个预选区域、三个离散需求点；不证明普遍优于固定策略，不证明跨城市有效或全局最优。',
              '- protocol.json：事前规则；selection.json：完整候选及冻结区域；events.jsonl：先决策后调用记录。',
              '- state.json：策略、共享关系、缺测、推荐和风险；scenarios / logs：真实原始输入输出及进程日志。',
              '- code_snapshot / execution_context.json：运行算法快照、程序及源码哈希；verification.json：独立重算核验。', '']
    if state.get('error'):
        lines += [f"运行失败：{state['error']}", '']
    return '\n'.join(lines)


class StudyRunner(Agent):
    def __init__(self, source=DEFAULT_SOURCE, root=EXPERIMENTS):
        self.logical_task = study_task()
        # The transport adapter counts the union of six logical studies. Its
        # execution limit is distinct from the validated ten-scene task card.
        super().__init__(replace(self.logical_task, max_runs=31), source, root)
        self.job = self.root / 'studies' / self.job_id
        self.state.update(kind='prospective_region_study', task=self.logical_task.as_dict(),
                          physical_budget=31, regions={}, trace=[], baseline_names={})

    def run(self):
        self.job.mkdir(parents=True, exist_ok=False)
        selection = {}
        print(f'STUDY {self.job}', flush=True)
        try:
            with runtime_lock(self.root):
                task = self.logical_task
                self.state['source_validation'] = validate_source(self.source)
                self.source_inputs = input_fingerprints(self.source)
                save_json(self.job/'source_inputs.json', self.source_inputs)
                self.runtime = setup_runtime(self.root)
                excluded, exclusions = set(), []
                for identifier in HISTORY:
                    folder = EXPERIMENTS/'jobs'/identifier
                    old_inputs = json.loads((folder/'source_inputs.json').read_text(encoding='utf-8'))
                    if old_inputs['data/geom.txt'] != self.source_inputs['data/geom.txt']:
                        raise ValueError('历史排除范围与本路网不兼容，不自动换成另一套选区规则')
                    old_scope = json.loads((folder/'scopes.json').read_text(encoding='utf-8'))
                    relative = f'exclusions/{identifier}.json'
                    save_json(self.job/relative, old_scope)
                    excluded.update(old_scope['core'] + old_scope['neighbor'])
                    exclusions.append(dict(job=identifier, file=relative, sha256=sha(self.job/relative)))
                frozen = dict(protocol(task, exclusions), registered_at=now())
                save_json(self.job/'protocol.json', frozen)
                code = sorted((PROJECT/'traffic_agent').glob('*.py')) + [PROJECT/'scripts/benchmark_regions.py']
                hashes = {}
                for path in code:
                    relative = path.relative_to(PROJECT).as_posix()
                    destination = self.job/'code_snapshot'/relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
                    hashes[relative] = sha(path)
                context = dict(captured_at=now(), python=sys.version, executable=sys.executable,
                               platform=platform.platform(), source=str(self.source), code_sha256=hashes,
                               git_commit=subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=PROJECT, capture_output=True, text=True, check=True).stdout.strip(),
                               native_sha256={m: sha(self.runtime/'programs'/m) for m in MODULES},
                               source_inputs_sha256=sha(self.job/'source_inputs.json'),
                               protocol_sha256=sha(self.job/'protocol.json'))
                save_json(self.job/'execution_context.json', context)
                self.state['status'] = 'running'
                self.persist()
                baseline, base = self.simulation('baseline', [], 1.0)
                bases, base_records = {1.0: baseline}, {1.0: base}
                selection = freeze_regions(baseline, task, excluded)
                selection.update(frozen_at=now(), baseline='baseline', protocol_sha256=sha(self.job/'protocol.json'))
                save_json(self.job/'selection.json', selection)
                self.event('study_regions_frozen', dict(counts=selection['counts'], regions=[
                    {k: v for k, v in r.items() if k != 'scope'} for r in selection['regions']]))
                for factor in (1.1, 0.9):
                    rows, record = self.simulation(f'baseline_{factor:g}', [], factor)
                    bases[factor], base_records[factor] = rows, record
                self.state['baseline_names'] = {str(f): r['name'] for f, r in base_records.items()}
                cache = {}
                for region in selection['regions']:
                    if region['status'] != 'selected':
                        self.state['regions'][region['id']] = dict(status='no_candidate')
                        continue
                    region_state = dict(status='running', strategies={})
                    self.state['regions'][region['id']] = region_state
                    base_views = {f: view_record(base_records[f], bases[f], region, task) for f in bases}
                    controllers = {s: candidate_actions(s, region, task, base_views) for s in STRATEGIES}
                    observations = {s: None for s in STRATEGIES}
                    for strategy in STRATEGIES:
                        region_state['strategies'][strategy] = dict(runs=list(base_views.values()))
                    while controllers:
                        batch = []
                        for strategy, controller in list(controllers.items()):
                            try:
                                action = controller.send(observations[strategy])
                            except StopIteration as done:
                                region_state['strategies'][strategy]['summary'] = done.value
                                del controllers[strategy]
                                continue
                            trace = dict(action, decided_at=now(), sequence=len(self.state['trace'])+1)
                            self.state['trace'].append(trace)
                            self.event('study_decision', trace)
                            batch.append(trace)
                        self.persist()  # Both next actions exist before either candidate's result.
                        for trace in batch:
                            key = (policy_key(trace['changes']), trace['factor'])
                            shared = key in cache
                            if shared:
                                rows, physical = cache[key]
                            else:
                                name = f"case_{len(cache)+1:03d}"
                                rows, physical = self.simulation(name, trace['changes'], trace['factor'])
                                cache[key] = (rows, physical)
                            actual_factor = physical['execution']['assigned_car_od_sum']/base['execution']['assigned_car_od_sum']
                            if not math.isclose(actual_factor, trace['factor'], rel_tol=1e-5):
                                raise ValueError('本轮需求未按预定比例进入原生分配')
                            view = view_record(physical, rows, region, task, bases[trace['factor']], trace['name'])
                            observations[trace['strategy']] = view
                            region_state['strategies'][trace['strategy']]['runs'].append(view)
                            trace.update(physical_run=physical['name'], shared=shared, observed_at=now())
                            self.event('study_observation', dict(sequence=trace['sequence'], physical_run=physical['name'],
                                                               shared=shared, eligible=view['comparison']['eligible'],
                                                               violations=view['comparison']['violations']))
                            self.persist()
                    region_state['status'] = 'evaluated'
                    self.persist()
                repeated, repeat = self.simulation('baseline_repeat', [], 1.0)
                identical = all(a[k] == b[k] for a, b in zip(baseline, repeated) for k in OUTPUTS) and len(baseline) == len(repeated)
                self.state['repeatability'] = dict(exact_match=identical, arrays=list(OUTPUTS), row_count=len(repeated),
                                                    note='确定性复现核对，不当作独立统计样本')
                if not identical:
                    raise ValueError('基准复跑不一致，禁止正式推荐')
                for region in selection['regions']:
                    if region['status'] != 'selected':
                        continue
                    region_state = self.state['regions'][region['id']]
                    for entry in region_state['strategies'].values():
                        entry['runs'].append(view_record(repeat, repeated, region, task))
                        entry['result'] = strategy_result(task, entry['runs'], entry['summary'])
                    region_state['status'] = 'completed'
                if input_fingerprints(self.source) != self.source_inputs:
                    raise ValueError('原案例输入在研究期间改变')
                if any(sha(PROJECT/name) != expected for name, expected in hashes.items()):
                    raise ValueError('运行算法文件发生改变，不能按原协议出具完成结论')
                if any(sha(self.runtime/'programs'/m) != expected for m, expected in context['native_sha256'].items()):
                    raise ValueError('原生模块在研究期间改变')
                self.state['status'] = 'completed'
        except BaseException as exc:
            self.state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
            self.event('failure', {'error': self.state['error']})
            raise
        finally:
            self.state['finished'] = now()
            self.state.pop('active_scenario', None)
            self.persist()
            (self.job/'report.md').write_text(render_report(self.state, selection), encoding='utf-8')
        return self.job
