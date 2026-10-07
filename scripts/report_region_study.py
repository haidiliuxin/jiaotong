"""Export a compact, offline judging brief from a verified prospective study."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import PROJECT, results, save_json
from traffic_agent.mechanism import parking_response
from traffic_agent.study import STRATEGIES


def export(job, output):
    from traffic_agent.delivery import read_bound_study
    from traffic_agent.study_audit import verify
    # Re-read the raw archive before generating a new report. A historical
    # passed flag alone must never certify modified evidence.
    read_bound_study(job)
    verify(job.resolve(), archived=True, write=False)
    state = json.loads((job/'state.json').read_text(encoding='utf-8'))
    selection = json.loads((job/'selection.json').read_text(encoding='utf-8'))
    verification = json.loads((job/'verification.json').read_text(encoding='utf-8'))
    if state['status'] != 'completed' or not verification['passed']:
        raise ValueError('只导出已完成且已独立核验的研究；此脚本本身不是核验器')
    output.mkdir(parents=True, exist_ok=False)
    regions = [r for r in selection['regions'] if r['status']=='selected']
    labels = {'high': '高密度', 'medium': '中密度', 'low': '低密度', 'fixed': '固定', 'feedback': '反馈'}
    counts = {s: sum(state['regions'][r['id']]['strategies'][s]['result']['analysis']['eligible_count'] > 0 for r in regions) for s in STRATEGIES}
    lines = ['# 三地区前瞻对照：评审摘要', '',
             f"任务 `{state['id']}`；新的真实计算共 **{verification['physical_scenes']} 个物理场景、{verification['native_module_calls']} 次 TranStar 原生模块调用**，原始数据重算、决策重放和基准复跑核验通过。", '',
             f"在事前选定的 {len(regions)} 个新区域中，固定策略在 {counts['fixed']} 个区域找到统一合格措施，反馈策略在 {counts['feedback']} 个区域找到统一合格措施。以下保留全部区域；这个计数不代表普遍成功率。", '',
             '| 区域 | 策略 | 合格措施数 | 统一建议 | 最弱核心耗时变化% | 最差邻区耗时变化% | 最差全网耗时变化% | 场景/预算 |',
             '| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |']
    for region in regions:
        for strategy in STRATEGIES:
            entry = state['regions'][region['id']]['strategies'][strategy]
            outcome = entry['result']
            analysis = outcome['analysis']
            winner = next((p for p in analysis['policies'] if p['name'] == analysis['recommendation']), None)
            if winner:
                names = {c['run'] for c in winner['checks']}
                core = max(r['comparison']['delta']['core']['running_time_change_pct'] for r in entry['runs'] if r['name'] in names)
                policy = f"{len(winner['changes'])}条 / 停车{winner['changes'][0]['parking']}级"
                values = [f'{v:+.6f}' for v in (core, winner['worst'][1], winner['worst'][0])]
            else:
                policy, values = '回退基准', ['—']*3
            lines.append(f"| {'、'.join(region['roads'])} | {labels[strategy]} | {analysis['eligible_count']} | {policy} | "
                         f"{' | '.join(values)} | {outcome['summary']['logical_scenes']}/10 |")
    lines += ['', '![全部地区结果与实际逻辑预算](结果对照.png)', '',
              '## 先选点，再观察治理结果', '',
              f"新基准产生 {selection['counts']['candidates']} 条完整停车候选、{selection['counts']['seeds']} 个不同核心种子，其中 {selection['counts']['legal_seeds']} 个种子与登记的四个历史研究范围不重叠。先按候选长度密度分层，再以节点编号稳定选区。范围与名单在任何治理仿真之前保存。", '',
              '| 层 | 道路 | 密度 | 核心/邻区有向路段数 | 种子 |', '| --- | --- | ---: | ---: | --- |']
    for r in selection['regions']:
        if r['status'] == 'selected':
            lines.append(f"| {labels[r['id']]} | {'、'.join(r['roads'])} | {r['density']:.6f} | {len(r['scope']['core'])}/{len(r['scope']['neighbor'])} | {r['seed'][0]}→{r['seed'][1]} |")
        else:
            lines.append(f"| {labels[r['id']]} | 无候选，不补选 | — | — | — |")
    lines += ['', '## 智能体的贡献如何核对', '',
              '- 两策略同核心停车2级起步。固定策略第二轮固定使用全核心停车1级；反馈策略依据首轮实测结果缩减范围或增强。',
              '- 每次动作先登记，之后才运行或读取该轮结果。反馈输入限定为本策略已完成的观察，核验器能逐步重放全部决定。',
              '- 缩小治理措施不缩小评价范围。反馈若仅改一条道路，仍按原先冻结的完整核心、邻区与全网评价。地图分别标出固定核心和本次实际改动。',
              '- 任一需求失败即淘汰该统一候选，未计算的其他需求明确缺测。没有合格候选则回退，绝不放宽约束补成成功。',
              f"- 六个区域策略实际合计 {verification['decisions_replayed']['logical_scenes']} 个逻辑场景；去重后 {verification['physical_scenes']} 个物理场景。每个引用策略都计费，末尾基准复跑强制新执行。", '',
              '## 必须一起呈现的代价', '']
    for region in regions:
        for strategy in STRATEGIES:
            outcome = state['regions'][region['id']]['strategies'][strategy]['result']
            risk = outcome['risks']
            if risk:
                lines.append(f"- {'、'.join(region['roads'])}／{labels[strategy]}：所选统一措施最差1跳邻区耗时变化 {risk['worst_inner_neighbor_time_change_pct']:+.6f}%，"
                             f"毛新增全网过饱和 {risk['worst_new_network_oversaturated_km']:.6f} km，恶化路段耗时增量合计最高 {risk['worst_gross_time_added_pcu_h']:.6f} pcu·h。")
            else:
                lines.append(f"- {'、'.join(region['roads'])}／{labels[strategy]}：无统一合格措施。各失败原因和缺测需求保留在完整报告及地图对照厅。")
    mechanisms, by_name = {}, {r['name']: r for r in state['runs']}
    bases = {float(f): results(job/by_name[n]['scenario']) for f, n in state['baseline_names'].items()}
    for record in state['runs']:
        if record['changes']:
            mechanisms[record['name']] = parking_response(bases[record['demand_factor']], results(job/record['scenario']), record['changes'])
    save_json(output/'模型联动观察.json', mechanisms)
    lines += ['', '## 模型生效与治理有效分开检查', '',
              '“模型联动观察.json”逐路段记录停车类别、管理修正容量、速度、残余负荷及联合治理qL/v收益。容量上升而速度没有上升时，不能把参数响应当成治理收益。道路收益来自联合仿真，不是独立单点干预的因果贡献。', '',
              '## 评分项的证据入口', '',
              '| 赛题评分项 | 本轮补充证据 |', '| --- | --- |',
              '| 创新性（30分） | 三地区相同预算对照；实测反馈动作可重放；失败和回退完整保留 |',
              '| 仿真合理性与效果（20分） | 全需求独立匹配基准；原阈值冻结；近邻及全网代价；确定性复跑 |',
              '| 智能体完整性（20分） | 自动完整候选、分层选区、工具调用、策略反馈、约束评估、报告 |',
              '| 程序质量与可运行性（20分） | 原生进程日志、输入白名单、逐元素OD、原始结果与动作审计 |',
              '| 展示（10分） | 新地图对照厅、全部区域比较、失败说明、参数响应与实际效果分开展示 |', '',
              '这些是证据对应关系，不是官方给分或预估分数。', '',
              '## 重跑与查看', '',
              '```powershell', r'.\.venv\Scripts\python.exe scripts\benchmark_regions.py',
              f'.\\.venv\\Scripts\\python.exe scripts\\verify_region_study.py 智能体实验\\studies\\{state["id"]} --archive --readonly',
              f'.\\.venv\\Scripts\\python.exe scripts\\serve_region_study.py 智能体实验\\studies\\{state["id"]} --port 8782', '```', '',
              '打开 http://127.0.0.1:8782/ 查看地图。停止只读服务时在其启动终端按 Ctrl+C。', '',
              '独立核验和本地重新求解已经区分；另一台电脑真实重跑、另一套兼容数据集验证、与本版一致的新实机视频和正式提交格式仍须补齐。', '',
              '结论仅适用于赛事调整案例中的事前选区和0.9/1.0/1.1离散需求。耗时为路段Σ(qL/v)，不包含全部节点延误；有向里程不是中心线长度。不能声称跨城市普遍有效、统计显著、现实最佳或全局最优。', '']
    (output/'评审摘要.md').write_text('\n'.join(lines), encoding='utf-8')
    (output/'完整实验报告.md').write_text((job/'report.md').read_text(encoding='utf-8'), encoding='utf-8')
    save_json(output/'证据摘要.json', dict(job=state['id'], verification=verification, feasible_regions=counts,
                                         source='智能体实验/studies/'+state['id'], scope='同一赛事调整案例内区域迁移'))
    plot_summary(state, regions, output)
    return output


def plot_summary(state, regions, output):
    labels = {'fixed': '固定', 'feedback': '反馈'}
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        plt.rcParams['font.family'] = FontProperties(fname=str(font)).get_name()
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    fig.subplots_adjust(left=.06, right=.99, bottom=.18, top=.74, wspace=.22)
    for i, (field, title, maximum) in enumerate((('eligible', '完整合格的统一措施数', 2), ('budget', '实际逻辑场景数 / 每策略上限10', 10))):
        ax = axes[i]
        for strategy, offset, color in (('fixed', -.18, '#426599'), ('feedback', .18, '#168271')):
            values = []
            for r in regions:
                outcome = state['regions'][r['id']]['strategies'][strategy]['result']
                values.append(outcome['analysis']['eligible_count'] if field=='eligible' else outcome['summary']['logical_scenes'])
            bars = ax.bar([x+offset for x in range(len(regions))], values, .33, label=labels[strategy], color=color)
            ax.bar_label(bars, padding=4, fontsize=10)
        ax.set_xticks(range(len(regions)), [r['roads'][0]+('\n等' if len(r['roads'])>1 else '') for r in regions])
        ax.set_ylim(0, maximum+(.5 if i==0 else 2))
        ax.set_yticks(range(maximum+1) if i==0 else range(0, maximum+1, 2))
        ax.set_title(title, fontsize=12, pad=14)
        ax.spines[['right', 'top']].set_visible(False)
        ax.set_axisbelow(True)
        ax.grid(axis='y', alpha=.2)
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, legend_labels, loc='upper center', bbox_to_anchor=(.5, .92), ncol=2, frameon=False)
    fig.suptitle('事前冻结三个新区域 · 相同候选预算 · 原始输出核验', fontsize=14)
    fig.savefig(output/'结果对照.png', dpi=180)
    fig.savefig(output/'结果对照.svg')
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('job', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(export(args.job, args.output or PROJECT/'成果/前瞻验证'/args.job.name))
