"""Export the full turn-grid result without conflating conditional and fixed policies."""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.plot_experiment import plt, save, LineCollection, Normalize, ScalarMappable
from traffic_agent.data import read_text, results


def plot(job):
    state = json.loads((job/'state.json').read_text(encoding='utf-8'))
    verification = json.loads((job/'verification.json').read_text(encoding='utf-8'))
    if (state['status'] != 'completed' or not state.get('turn_access_profile')
            or not state['factorial_design']['full_demand_grid'] or not verification['passed']
            or not verification.get('full_demand_grid_verified')):
        raise ValueError('需要已完成并独立核验的状态图禁转完整组合')
    output = job/'figures'
    output.mkdir(exist_ok=True)
    factors = sorted(state['task']['demand_factors'])
    variants = [('S1_parking', 'S1 停车', '#15818c'), ('S2_turn', 'S2 禁转', '#c77934'),
                ('S3_combined', 'S3 组合', '#6b5d98')]
    runs = state['runs']
    lookup = {(r['demand_factor'], r['policy_label']): r for r in runs if r.get('policy_label')}
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout='constrained')
    panels = [('core', '固定出口核心：ΣqL/v变化（%）'), ('neighbor', '固定邻区：ΣqL/v变化（%）'),
              ('network', '全网：ΣqL/v变化（%）'), (None, '邻区新增过饱和有向里程（km）')]
    for ax, (area, title) in zip(axes.flat, panels):
        for i, (label, name, color) in enumerate(variants):
            records = [lookup[f, label] for f in factors]
            values = [r['comparison']['delta'][area]['running_time_change_pct'] if area
                      else r['comparison']['new_neighbor_oversaturated_km'] for r in records]
            positions = [j+(i-1)*.24 for j in range(len(factors))]
            bars = ax.bar(positions, values, width=.22, color=color, label=name)
            for bar, record in zip(bars, records):
                if not record['comparison']['eligible']:
                    bar.set_hatch('///')
                    bar.set_edgecolor('#263a47')
            for x, y, record in zip(positions, values, records):
                if not record['comparison']['eligible']:
                    ax.scatter(x, y, marker='x', s=55, color='#263a47', zorder=3)
        limit = (state['task']['max_neighbor_time_increase_pct'] if area == 'neighbor' else
                 state['task']['max_new_oversaturated_km'] if area is None else 0)
        ax.axhline(limit, linestyle='--', color='#7d8c96', linewidth=1)
        ax.set_xticks(range(len(factors)), [f'{f:g}' for f in factors])
        ax.set_xlabel('小汽车OD系数（独立仿真点）')
        ax.set_title(title)
        ax.grid(axis='y', alpha=.12)
        ax.set_axisbelow(True)
    axes[0, 0].legend(fontsize=9)
    turn = '→'.join(map(str, state['task']['turn']))
    fig.suptitle(f'{turn}｜停车、禁转与组合的完整需求对照', fontsize=15)
    fig.supxlabel('相对各自同需求新基准；斜线/×表示候选未通过全部约束。虚线为对应门槛；核心及全网须严格改善。', fontsize=9)
    save(fig, output, '禁转停车需求组合')

    by_name = {r['name']: r for r in runs}
    primary = by_name[state['recommendation']].get('policy_label')
    names = {key: name for key, name, _ in variants}
    cells = []
    for factor in factors:
        base = next(r for r in runs if not r['changes'] and r['demand_factor'] == factor)
        chosen = by_name[state['recommendation_by_car_demand'][str(float(factor))]]
        eligible = [name for key, name, _ in variants if lookup[factor, key]['comparison']['eligible']]
        gain = 100*(chosen['metrics']['network']['running_time_pcu_h']/base['metrics']['network']['running_time_pcu_h']-1)
        fixed = lookup[factor, primary]['comparison']['eligible'] if primary else None
        cells.append([f'{factor:g}', '、'.join(n.split()[0] for n in eligible) or '无',
                      names.get(chosen.get('policy_label'), 'S0 基准'), f'{gain:+.5f}%',
                      {True: '通过', False: '未通过', None: '主推荐为基准'}[fixed]])
    fig, ax = plt.subplots(figsize=(12, 3.8), layout='constrained')
    ax.axis('off')
    table = ax.table(cellText=cells, colLabels=['小汽车OD系数', '合格候选', '条件建议', '全网ΣqL/v变化', '固定1.0主方案'],
                     colWidths=[.14, .20, .20, .22, .24], loc='center', cellLoc='center')
    style_table(table)
    ax.set_title('条件推荐与固定主方案的适用条件', fontsize=15)
    fig.supxlabel('每个系数以自身基准比较；条件换方案不能掩盖主方案失效。回退基准的治理收益为0。', fontsize=9)
    save(fig, output, '禁转组合条件推荐')

    cells = []
    for factor in factors:
        for label in ('S2_turn', 'S3_combined'):
            record = lookup[factor, label]
            movement, = record['turn_response']['movements']
            flows = movement['flows']
            cells.append([f'{factor:g} / {names[label]}'] + [
                f"{flows[m]['before']:.4f} → {flows[m]['after']:.4f}" +
                (' *' if not flows[m]['positive_baseline'] else '') for m in ('car', 'taxi', 'motor', 'truck')])
    fig, ax = plt.subplots(figsize=(12, 4.8), layout='constrained')
    ax.axis('off')
    table = ax.table(cellText=cells, colLabels=['需求 / 方案', '小汽车', '出租车', '摩托车', '货车'],
                     colWidths=[.19, .22, .21, .21, .17], loc='center', cellLoc='center')
    style_table(table)
    ax.set_title(f'{turn}｜全部需求的实际禁转响应', fontsize=15)
    fig.supxlabel('原生状态转移边五轮累计，分别报告车型；* 表示基准未超过正流量门槛，不作为正响应证据。\n'
                  f"本任务{verification['module_executions']}次顶层调用、{verification['adapted_calls']}次适配与{verification['reference_calls']}次同输入原版参考。", fontsize=9)
    save(fig, output, '禁转完整组合响应')
    plot_scope(job, state, output)
    print(output)


def style_table(table):
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2.1)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor('#d6e0e5')
        cell.set_facecolor('#eaf2f5' if row == 0 else 'white')


def plot_scope(job, state, output):
    scope = json.loads((job/'scopes.json').read_text(encoding='utf-8'))
    base = next(r for r in state['runs'] if r['name'] == 'baseline')
    winner = next(r for r in state['runs'] if r['name'] == state['recommendation'])
    coords = {int(parts[0]): (float(parts[1]), float(parts[2])) for line in
              read_text(job/base['scenario']/'data/coor.txt').splitlines() if (parts := line.split())}
    fig, axes = plt.subplots(1, 2, figsize=(12, 6), layout='constrained')
    norm, cmap = Normalize(vmin=0, vmax=60), plt.get_cmap('viridis')
    for ax, record, label in zip(axes, (base, winner), ('新基准', '1.0需求推荐：'+winner['name'])):
        rows = results(job/record['scenario'])
        segments, speeds = [], []
        for i in scope['core']+scope['neighbor']:
            row = rows[i]
            a, b = coords[row['from_node']], coords[row['to_node']]
            dx, dy = b[0]-a[0], b[1]-a[1]
            length = math.hypot(dx, dy)
            offset = (-dy/length*13, dx/length*13) if length else (0, 0)
            points = [(p[0]+offset[0], p[1]+offset[1]) for p in (a, b)]
            segments.append(points)
            speeds.append(row['speed_kmh'])
            if i in scope['core']:
                ax.plot(*zip(*points), color='#c77934', linewidth=7, zorder=1)
        collection = LineCollection(segments, cmap=cmap, norm=norm, linewidths=2.3, zorder=2)
        collection.set_array(speeds)
        ax.add_collection(collection)
        for node in state['task']['turn']:
            ax.scatter(*coords[node], color='#183747', s=15, zorder=3)
            ax.annotate(str(node), coords[node], xytext=(5, 7), textcoords='offset points', fontsize=9,
                        bbox=dict(facecolor='white', edgecolor='none', alpha=.75, pad=1))
        ax.autoscale()
        ax.margins(.12)
        ax.set_aspect('equal')
        ax.set_title(label)
        ax.set_xlabel('案例X坐标（原始单位）')
        ax.ticklabel_format(style='plain', useOffset=False)
        ax.tick_params(labelsize=8)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, label='路段速度 km/h', shrink=.7)
    fig.suptitle('显式禁转研究范围与推荐方案｜橙色描边为固定出口核心', fontsize=15)
    fig.supxlabel('节点直连拓扑示意，非真实道路曲线；双向线轻微错开，使用统一色标。两图只比较1.0匹配需求。', fontsize=9)
    save(fig, output, '禁转组合区域运行')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('job', type=Path)
    plot(parser.parse_args().job.resolve())
