"""Export the sealed, guarded two-arm comparison without running any model."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import save_json
from traffic_agent.decision_analysis import policy_key
from traffic_agent.guard_challenge import KIND as V1_KIND, STRATEGIES
from traffic_agent.guard_challenge_resilient import KIND as V2_KIND
from traffic_agent.guard_challenge_resilient_v3 import KIND as V3_KIND
from traffic_agent.study_audit import verify

LABELS = dict(fixed_full_singletons='完整单路段固定网格',
              feedback_backtracking_v2='新版反馈回退',
              high='高密度', medium='中密度', low='低密度')


def outcomes(state, selection):
    rows = []
    for region in selection['regions']:
        if region['status'] != 'selected':
            continue
        for strategy in STRATEGIES:
            entry = state['regions'][region['id']]['strategies'][strategy]
            result, summary = entry['result'], entry['summary']
            analysis = result['analysis']
            winner = next((p for p in analysis['policies']
                           if p['name'] == analysis['recommendation']), None)
            selected_names = {check['run'] for check in winner['checks']} if winner else set()
            selected = [r for r in entry['runs'] if r['name'] in selected_names]
            rows.append(dict(
                region=region['id'], roads=region['roads'], strategy=strategy,
                qualified=bool(winner), eligible_count=analysis['eligible_count'],
                recommendation=result['recommendation'],
                changes=winner['changes'] if winner else [],
                core_change=max(r['comparison']['delta']['core']['running_time_change_pct']
                                for r in selected) if selected else None,
                neighbor_change=winner['worst'][1] if winner else None,
                network_change=winner['worst'][0] if winner else None,
                logical_scenes=summary['logical_scenes'], budget=summary['budget'],
                backtracks=summary['backtracks'], risks=result['risks']))
    return rows


def conclusion(fixed, adaptive):
    if adaptive['qualified'] and not fixed['qualified']:
        return '反馈独有合格措施；需核对实际测得候选与固定网格覆盖差异。'
    if fixed['qualified'] and not adaptive['qualified']:
        return '固定策略找到合格措施，反馈策略未找到；本区反馈较差。'
    if not fixed['qualified']:
        return '两策略均无合格统一措施；本区不能声称算法增益。'
    if policy_key(fixed['changes']) == policy_key(adaptive['changes']):
        return '两策略推荐同一措施；本区没有推荐效果增益。'
    if adaptive['network_change'] < fixed['network_change'] - 0.001:
        return '反馈推荐全网耗时指标更优；仍需同时检查邻区、风险和措施复杂度。'
    if fixed['network_change'] < adaptive['network_change'] - 0.001:
        return '固定推荐全网耗时指标更优；本区不能声称反馈胜出。'
    return '两策略均有合格措施且全网指标接近；按预注册容差及局部代价作并列比较。'


def export(job, output):
    state = json.loads((job/'state.json').read_text(encoding='utf-8'))
    if state.get('kind') not in (V1_KIND, V2_KIND, V3_KIND):
        raise ValueError('导出器只接受全网安全门槛同预算挑战')
    verification = verify(job, archived=True, write=False)
    selection = json.loads((job/'selection.json').read_text(encoding='utf-8'))
    rows = outcomes(state, selection)
    output.mkdir(parents=True, exist_ok=False)
    title = {V1_KIND: '全网安全门槛同预算盲区挑战',
             V2_KIND: '全网安全门槛同预算原生恢复挑战',
             V3_KIND: '全网安全门槛同预算车型缓存恢复挑战'}[state['kind']]
    lines = [f'# {title}：评审摘要', '',
             f"任务 `{state['id']}`；{verification['physical_scenes']} 个新物理场景，"
             f"{verification['native_module_calls']} 次真实 TranStar 原生模块调用。"
             '原始数组、输入差异、冻结选区、决策时序、同预算计费和基准复跑均已严格核验。', '',
             '两臂共享首轮，均执行三档需求、全网毛新增过饱和≤0.1 km等相同硬约束，'
             '每区逻辑预算同为16场景。固定臂覆盖原核心每个单路段停车2级方向。', '',
             '| 区域/道路 | 策略 | 合格候选数 | 统一措施 | 最弱核心耗时变化% | 最差邻区变化% | 最差全网变化% | 逻辑场景 |',
             '| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        policy = '保留无治理基准' if not row['qualified'] else '、'.join(
            f"{c['from_node']}→{c['to_node']}：{c['old_parking']}→{c['parking']}级"
            for c in row['changes'])
        values = ['—' if row[k] is None else f'{row[k]:+.6f}'
                  for k in ('core_change', 'neighbor_change', 'network_change')]
        lines.append(f"| {LABELS[row['region']]} / {'、'.join(row['roads'])} | {LABELS[row['strategy']]} | "
                     f"{row['eligible_count']} | {policy} | {' | '.join(values)} | "
                     f"{row['logical_scenes']}/{row['budget']} |")
    lines += ['', '## 按区比较', '']
    by_region = {region['id']: {r['strategy']: r for r in rows if r['region'] == region['id']}
                 for region in selection['regions'] if region['status'] == 'selected'}
    for region in selection['regions']:
        if region['status'] != 'selected':
            lines.append(f"- {LABELS[region['id']]}：本层无合法新区域，保留空缺。")
            continue
        pair = by_region[region['id']]
        fixed, adaptive = (pair[strategy] for strategy in STRATEGIES)
        lines += [f"### {LABELS[region['id']]}：{'、'.join(region['roads'])}", '',
                  conclusion(fixed, adaptive), '',
                  f"反馈回退实际触发 {len(adaptive['backtracks'])} 次；"
                  f"固定/反馈逻辑计费 {fixed['logical_scenes']}/{adaptive['logical_scenes']}。", '']
        for strategy in STRATEGIES:
            entry = state['regions'][region['id']]['strategies'][strategy]
            lines.append(f"**{LABELS[strategy]}**：{entry['summary']['stop_reason']}。")
            for policy in entry['result']['analysis']['policies']:
                status = '三需求全部合格' if policy['feasible'] else '；'.join(policy['reasons']) or '证据不完整'
                lines.append(f"- `{policy['name']}`：{status}；未测需求 {policy['missing']}。")
            if pair[strategy]['risks']:
                risk = pair[strategy]['risks']
                lines.append(f"- 被选措施最差1跳邻区耗时变化 {risk['worst_inner_neighbor_time_change_pct']:+.6f}%；"
                             f"全网毛新增过饱和 {risk['worst_new_network_oversaturated_km']:.6f} km；"
                             f"恶化路段耗时增量合计最高 {risk['worst_gross_time_added_pcu_h']:.6f} pcu·h。")
            lines.append('')
    counts = {strategy: sum(row['qualified'] for row in rows if row['strategy'] == strategy)
              for strategy in STRATEGIES}
    if state['kind'] in (V2_KIND, V3_KIND):
        recovery = verification['native_recovery']
        old_jobs = '三次' if state['kind'] == V3_KIND else '两次'
        lines += ['', '## 执行恢复披露', '',
                  f"本任务完整保留 {recovery['failed_attempts']} 次原生文件打开失败及其"
                  f" {recovery['failed_native_module_calls']} 次原生模块调用；失败未形成策略观测，"
                  f'同输入重试逐一审计。{old_jobs}旧任务已中止，且部分治理输出在本版运行前已知；'
                  '这是执行可靠性修订，不能称完全盲测。']
    lines += ['## 证据边界', '',
              f"固定策略合格区域 {counts[STRATEGIES[0]]} 个；反馈策略合格区域 {counts[STRATEGIES[1]]} 个。"
              '这里只是三个事前区域的计数，不是官方得分、普遍成功率或统计显著性。',
              '路段耗时为Σ(qL/v)，不含全部节点延误。两臂共享物理场景但分别计逻辑预算；'
              '确定性基准复跑不是独立统计样本。', '',
              '审计入口：`python scripts/verify_region_study.py 智能体实验/challenges/'
              f'{state["id"]} --archive --readonly`。', '']
    (output/'评审摘要.md').write_text('\n'.join(lines), encoding='utf-8')
    (output/'完整实验报告.md').write_bytes((job/'report.md').read_bytes())
    save_json(output/'证据摘要.json', dict(job=state['id'], verification=verification,
                                         outcomes=rows, paired_conclusions={
                                             rid: conclusion(pair[STRATEGIES[0]], pair[STRATEGIES[1]])
                                             for rid, pair in by_region.items()},
                                         code=json.loads((job/'execution_context.json').read_text(encoding='utf-8'))['git_commit']))
    plot(rows, output)
    return output


def plot(rows, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font = FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
    plt.rcParams['axes.unicode_minus'] = False
    colors = {'fixed_full_singletons': '#7994c7', 'feedback_backtracking_v2': '#4eb89e'}
    figure, axes = plt.subplots(1, 2, figsize=(12.5, 6),
                                gridspec_kw={'width_ratios': [1.4, 1]})
    positions = list(range(len(rows)))
    for ax in axes:
        ax.set_axisbelow(True)
        ax.grid(axis='x', color='#dce4ec', linewidth=.7)
        ax.spines[['top', 'right']].set_visible(False)
        ax.set_yticks(positions)
        ax.invert_yaxis()
    axes[0].set_yticklabels([f"{LABELS[r['region']]} · {LABELS[r['strategy']]}" for r in rows],
                            fontproperties=font, fontsize=10)
    axes[1].set_yticklabels([])
    benefit_max = max([abs(r['core_change']) for r in rows if r['core_change'] is not None] or [1])
    for index, row in enumerate(rows):
        color = colors[row['strategy']]
        if row['qualified']:
            value = -row['core_change']
            axes[0].barh(index, value, color=color, height=.6)
            axes[0].text(value+benefit_max*.02, index, f'{value:.3f}%', va='center', fontsize=9)
        else:
            axes[0].plot(0, index, 'x', color='#b85b64')
            axes[0].text(benefit_max*.02, index, '无合格措施', fontproperties=font,
                         va='center', fontsize=9, color='#a64d58')
        axes[1].barh(index, 16, color='#e9eff5', height=.6)
        axes[1].barh(index, row['logical_scenes'], color=color, height=.6)
        axes[1].text(row['logical_scenes']+.15, index,
                     f"{row['logical_scenes']}/16", va='center', fontsize=9)
    axes[0].set_xlim(left=-benefit_max*.04, right=benefit_max*1.35)
    axes[1].set_xlim(0, 18)
    axes[0].set_title('统一建议：最弱需求的核心耗时改善', fontproperties=font, fontsize=12)
    axes[1].set_title('实际逻辑场景 / 同一预算', fontproperties=font, fontsize=12)
    axes[0].set_xlabel('核心Σ(qL/v)下降百分比', fontproperties=font)
    axes[1].set_xlabel('共享物理输出仍各自计费', fontproperties=font)
    figure.suptitle('全网安全门槛：强固定网格与反馈回退', fontproperties=font, fontsize=16)
    figure.text(.5, .02, '三个事前区域；本图不等于官方比赛得分', ha='center',
                fontproperties=font, color='#58677a')
    figure.tight_layout(rect=[0, .045, 1, .94])
    figure.savefig(output/'结果对照.png', dpi=170)
    figure.savefig(output/'结果对照.svg')
    plt.close(figure)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(export(args.job.resolve(), args.output.resolve()))
