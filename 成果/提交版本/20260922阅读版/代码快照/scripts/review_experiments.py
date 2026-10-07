"""Re-evaluate intact archives; write all new analysis into a separate directory."""
import argparse
import copy
import hashlib
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.agent import compare
from traffic_agent.data import PROJECT, results, scopes, save_json, scoped_metrics
from traffic_agent.decision_analysis import decision_analysis
from traffic_agent.selection import select, TOLERANCES, record_selection
from traffic_agent.runner import now
from traffic_agent.task import Task
from verify_experiment import verify


def warnings(before, after, network):
    added = saved = new_km = 0.0
    roads = []
    for i in network:
        a, b = before[i], after[i]
        change = a['length_km'] * (b['flow_pcu_h']/b['speed_kmh'] - a['flow_pcu_h']/a['speed_kmh'])
        added += max(change, 0)
        saved += max(-change, 0)
        if a['vc'] <= 1 < b['vc']:
            new_km += a['length_km']
        if change > 0:
            roads.append(dict(from_node=a['from_node'], to_node=a['to_node'], name=a['name'], time_increase_pcu_h=change))
    roads.sort(key=lambda r: (-r['time_increase_pcu_h'], r['from_node'], r['to_node']))
    return dict(new_network_overloaded_km=new_km, added_time_pcu_h=added, saved_time_pcu_h=saved,
                net_time_pcu_h=added-saved, worst_roads=roads[:5])


def reassess(c, task, extra):
    c = copy.deepcopy(c)
    violations = list(extra)
    for area, limit in [('neighbor', task.max_neighbor_time_increase_pct), ('network', task.max_network_time_increase_pct)]:
        x = c['delta'][area]['running_time_change_pct']
        if x is None or x > limit + 1e-9:
            violations.append(f'{area}路段行驶时间增幅超过{limit}%')
    if c['new_neighbor_oversaturated_km'] > task.max_new_oversaturated_km + 1e-9:
        violations.append(f'邻区新增过饱和有向里程超过{task.max_new_oversaturated_km}km')
    if not c['after']['core']['running_time_pcu_h'] < c['before']['core']['running_time_pcu_h'] - 1e-9:
        violations.append('固定核心区路段行驶时间未改善')
    c.update(eligible=not violations, violations=violations)
    return c


def review(job, protocol):
    archived = (job/'archive_manifest.json').is_file()
    audit = verify(job, archived=archived, write=False)
    state = json.loads((job/'state.json').read_text(encoding='utf-8'))
    task = Task.from_dict(state['task'])
    frozen = json.loads((job/'scopes.json').read_text(encoding='utf-8'))
    bases = {r['demand_factor']: r for r in state['runs'] if not r['changes'] and r['name'] != 'baseline_repeat'}
    raw_bases = {f: results(job/r['scenario']) for f, r in bases.items()}
    variants = {h: dict(state, runs=[]) for h in protocol['neighbor_hops']}
    impacts, extra = [], {}
    for r in state['runs']:
        raw = results(job/r['scenario']) if r['changes'] else raw_bases[r['demand_factor']]
        before = raw_bases[r['demand_factor']]
        if r['changes']:
            original = compare(before, raw, frozen, task)
            extra[r['name']] = [v for v in r['comparison']['violations'] if v not in original['violations']]
            impacts.append(dict(run=r['name'], factor=r['demand_factor'], original_eligible=r['comparison']['eligible'],
                                original_delta=r['comparison']['delta'], **warnings(before, raw, frozen['network'])))
        for hops, variant in variants.items():
            scope = scopes(raw_bases[1], frozen['core'], hops)
            updated = dict(r, metrics=scoped_metrics(raw, scope))
            if r['changes']:
                updated['comparison'] = reassess(compare(before, raw, scope, task), task, extra[r['name']])
            variant['runs'].append(updated)
    scans = []
    cases = [('original', {})] + [(f'{k}={value}', {k: value}) for k, values in protocol['one_at_a_time_limits'].items() for value in values]
    for hops, variant in variants.items():
        for name, updates in cases:
            settings = Task.from_dict(dict(task.as_dict(), neighbor_hops=hops, **updates))
            view = dict(variant, task=settings.as_dict(), runs=[dict(r) for r in variant['runs']])
            for r in view['runs']:
                if r['changes']:
                    r['comparison'] = reassess(r['comparison'], settings, extra[r['name']])
            analysis = decision_analysis(view, 'tolerance-v2')
            scans.append(dict(neighbor_hops=hops, variant=name, recommendation=analysis['recommendation'],
                              eligible=[p['name'] for p in analysis['policies'] if p['feasible']]))
    old, new = decision_analysis(state, 'legacy-v1'), decision_analysis(state, 'tolerance-v2')
    tolerance_scan = []
    for multiplier in protocol['tolerance_multipliers']:
        chosen = select([p for p in new['policies'] if p['feasible']], [x*multiplier for x in TOLERANCES])
        tolerance_scan.append(dict(multiplier=multiplier, **chosen))
    return dict(job=job.name, source_state_sha256=hashlib.sha256((job/'state.json').read_bytes()).hexdigest(),
                archive_manifest_sha256=hashlib.sha256((job/'archive_manifest.json').read_bytes()).hexdigest() if archived else None,
                audit_mode='portable_archive' if archived else 'strict_original_directory',
                audit=audit, original_recorded_recommendation=state['recommendation'], old_analysis=old, new_analysis=new,
                conditional_v2={str(f): record_selection([r for r in state['runs'] if r['changes'] and r['demand_factor']==f]) for f in task.demand_factors},
                warnings=impacts, threshold_scope_scan=scans, tolerance_scan=tolerance_scan)


def render(report):
    lines = ['# 交接意见优化：历史结果再分析', '', '这是已知结果的回顾性分析，未重新调用TranStar。原实验、约束判定和报告全部保留。', '',
             '源数据先核验实际进程证据及原始输出指标；有封存清单的检查字节一致性，其余严格核验原运行目录及时间戳。逐项方式见review.json/audit_mode。', '',
             '## 新旧推荐对照', '', '| 任务 | 原任务冻结建议 | 旧跨需求筛选 | 容差v2跨需求筛选 |', '| --- | --- | --- | --- |']
    for r in report['jobs']:
        lines.append(f"| {r['job']} | {r['original_recorded_recommendation']} | {r['old_analysis']['recommendation'] or '保留基准'} | {r['new_analysis']['recommendation'] or '保留基准'} |")
    lines += ['', '容差：[0.001个百分点、0.01个百分点、0.001km]；不是输出误差或统计显著性估计。复杂度按不同措施数、停车1级措施数依次排序。',
              '精确Pareto前沿与近似相同组分开显示；微小优势可以被简单措施取代，但失败或缺测候选不能被容差放行。', '']
    for r in report['jobs']:
        lines += [f"## {r['job']}", '', '| 合格候选 | 最差全网变化% | 最差邻区变化% | 最差新增邻区km | 复杂度 |', '| --- | ---: | ---: | ---: | --- |']
        for c in r['new_analysis']['selection']['candidates']:
            lines.append(f"| {c['name']} | {c['objectives'][0]:.9f} | {c['objectives'][1]:.9f} | {c['objectives'][2]:.9f} | {c['complexity']} |")
        lines += ['', '### 局部恶化披露', '', '新增全网过饱和为毛增量，不被其他路段改善抵消；耗时仍仅为路段Σ(qL/v)。', '',
                  '| 场景 | 原约束 | 核心耗时变化% | 邻区耗时变化% | 全网耗时变化% | 新增全网过饱和km | 所有恶化路段耗时增量pcu·h | 最差路段增量pcu·h |',
                  '| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |']
        for w in r['warnings']:
            d=w['original_delta']; worst=w['worst_roads'][0] if w['worst_roads'] else None
            road=f"{worst['from_node']}→{worst['to_node']}：{worst['time_increase_pcu_h']:.6f}" if worst else '无'
            lines.append(f"| {w['run']} | {'通过' if w['original_eligible'] else '拒绝'} | {d['core']['running_time_change_pct']:.6f} | {d['neighbor']['running_time_change_pct']:.6f} | {d['network']['running_time_change_pct']:.6f} | {w['new_network_overloaded_km']:.6f} | {w['added_time_pcu_h']:.6f} | {road} |")
        lines += ['', '### 范围与阈值扫描', '', '| 邻区跳数 | 阈值变体（其余保持原值） | 统一推荐 | 合格候选数 |', '| ---: | --- | --- | ---: |']
        for row in r['threshold_scope_scan']:
            lines.append(f"| {row['neighbor_hops']} | {row['variant']} | {row['recommendation'] or '保留基准'} | {len(row['eligible'])} |")
        lines += ['', '### 容差敏感性', '', '| 默认容差倍数 | 统一推荐 | 近似相同组 |', '| ---: | --- | --- |']
        for row in r['tolerance_scan']:
            lines.append(f"| {row['multiplier']} | {row['recommendation'] or '保留基准'} | {', '.join(row['equivalent_candidates']) or '无'} |")
    lines += ['', '## 边界', '', '固定原核心和已测措施，仅改变评价范围或判定规则；不会补齐主动搜索提前拒绝后缺失的需求点。',
              '全网风险是新增披露项，未事后改变原任务合格标准；新硬约束须在新任务仿真前预设。',
              '这不是另一台机器的复跑，也不是新区域盲测。原始证据索引、所有候选和每项扫描可查同目录review.json。', '']
    return '\n'.join(lines)


def offline_html(markdown):
    # Actual offline tables, no remote JS/fonts and no literal Markdown table dump.
    blocks, in_table = [], False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if set(line.replace('|', '').replace(' ', '').replace(':', '')) == {'-'}:
                continue
            if not in_table:
                blocks.append('<div class="scroll"><table>'); in_table=True
            blocks.append('<tr>'+''.join('<td>'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>')
            continue
        if in_table:
            blocks.append('</table></div>'); in_table=False
        if line.startswith('#'):
            level=len(line)-len(line.lstrip('#'))
            blocks.append(f'<h{level}>'+html.escape(line[level:].strip())+f'</h{level}>')
        elif line:
            blocks.append('<p>'+html.escape(line)+'</p>')
    if in_table: blocks.append('</table></div>')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>历史结果再分析</title><style>body{font:16px/1.7 system-ui;max-width:1280px;margin:40px auto;padding:0 24px;color:#172c35}.scroll{overflow:auto}table{border-collapse:collapse;font-size:14px;width:100%}td{border:1px solid #ccd6da;padding:8px}tr:first-child{background:#edf3f5;font-weight:600}h2{margin-top:48px}@media print{body{margin:0;font-size:10pt}.scroll{overflow:visible}table{font-size:8pt}tr{break-inside:avoid}}</style><body>'+''.join(blocks)+'</body></html>'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--protocol', type=Path, default=PROJECT/'examples/review_protocol_20260919.json')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true', help='仅继续本次未完成的再分析，不覆盖已完成报告')
    args=parser.parse_args()
    protocol=json.loads(args.protocol.read_text(encoding='utf-8'))
    if args.resume:
        if (args.output/'report.md').exists():
            raise ValueError('报告已完成，拒绝覆盖；请使用新目录')
        frozen=json.loads((args.output/'protocol.json').read_text(encoding='utf-8'))
        frozen.pop('analysis_started')
        if frozen != protocol:
            raise ValueError('恢复协议与原冻结协议不同')
        report=json.loads((args.output/'review.json').read_text(encoding='utf-8'))
        if [r['job'] for r in report['jobs']] != protocol['jobs'][:len(report['jobs'])]:
            raise ValueError('已分析任务不是原协议前缀')
        for r in report['jobs']:
            source=PROJECT/'智能体实验/jobs'/r['job']/'state.json'
            if hashlib.sha256(source.read_bytes()).hexdigest() != r['source_state_sha256']:
                raise ValueError('已分析任务状态改变')
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        save_json(args.output/'protocol.json', dict(protocol, analysis_started=now()))
        report=dict(version=protocol['version'], started=now(), jobs=[])
    for name in protocol['jobs'][len(report['jobs']):]:
        print('REVIEW '+name, flush=True)
        report['jobs'].append(review(PROJECT/'智能体实验/jobs'/name, protocol))
        save_json(args.output/'review.json', report)
    report['finished']=now(); save_json(args.output/'review.json', report)
    document=render(report)
    (args.output/'report.md').write_text(document, encoding='utf-8')
    (args.output/'report.html').write_text(offline_html(document), encoding='utf-8')
    print('COMPLETE '+str(args.output), flush=True)


if __name__=='__main__':
    main()
