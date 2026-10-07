"""Produce evidence-bound comparisons without starting or changing any model."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.challenge import KIND, STRATEGIES
from traffic_agent.data import save_json
from traffic_agent.decision_analysis import policy_key
from traffic_agent.study_audit import verify

LABELS = dict(fixed_diverse='稀疏固定策略', feedback_halving='原缩减反馈',
              feedback_backtracking='回退反馈', high='高密度', medium='中密度', low='低密度')


def outcomes(state, selection):
    rows = []
    for region in selection['regions']:
        if region['status'] != 'selected':
            continue
        for strategy in STRATEGIES:
            entry = state['regions'][region['id']]['strategies'][strategy]
            result, summary = entry['result'], entry['summary']
            analysis = result['analysis']
            winner = next((p for p in analysis['policies'] if p['name'] == analysis['recommendation']), None)
            selected = [r for r in entry['runs'] if winner and r['name'] in {c['run'] for c in winner['checks']}]
            rows.append(dict(region=region['id'], roads=region['roads'], strategy=strategy,
                             qualified=bool(winner), eligible=analysis['eligible_count'],
                             recommendation=result['recommendation'], changes=winner['changes'] if winner else [],
                             core_change=max(r['comparison']['delta']['core']['running_time_change_pct'] for r in selected) if selected else None,
                             neighbor_change=winner['worst'][1] if winner else None,
                             network_change=winner['worst'][0] if winner else None,
                             logical_scenes=summary['logical_scenes'], budget=summary['budget'],
                             backtracks=summary['backtracks'], risks=result['risks']))
    return rows


def export(job, output):
    state = json.loads((job/'state.json').read_text(encoding='utf-8'))
    if state.get('kind') != KIND:
        raise ValueError('此导出器仅适用于三策略回退挑战')
    # Recompute the full sealed archive, not just a historical passed flag.
    verification = verify(job, archived=True, write=False)
    selection = json.loads((job/'selection.json').read_text(encoding='utf-8'))
    rows = outcomes(state, selection)
    output.mkdir(parents=True, exist_ok=False)
    counts = {s: sum(r['qualified'] for r in rows if r['strategy'] == s) for s in STRATEGIES}
    lines = ['# 回退搜索与更强固定策略：评审摘要', '',
             f"任务 `{state['id']}`；{verification['physical_scenes']} 个新物理场景，"
             f"{verification['native_module_calls']} 次真实 TranStar 原生模块调用。原始数组、输入修改、选区、时序、决策重放与基准复跑核验通过。", '',
             '本轮固定策略事前已包含单路段治理方案。三种策略具有相同的13场景上限、相同首轮、相同需求点及原硬约束。不能用两轮实验的不同预算来解释回退增益。', '',
             '| 策略 | 找到跨需求合格措施的区域数 | 实际逻辑场景总数 |',
             '| --- | ---: | ---: |']
    for strategy in STRATEGIES:
        arm = [r for r in rows if r['strategy'] == strategy]
        lines.append(f"| {LABELS[strategy]} | {counts[strategy]} / {len(arm)} | {sum(r['logical_scenes'] for r in arm)} |")
    lines += ['', '这只是三个事前选定区域的结果计数，不是普遍成功率，也不是比赛给分。', '',
              '![全部区域的治理收益与逻辑预算](结果对照.png)', '',
              '| 区域/道路 | 策略 | 合格措施数 | 统一措施 | 最弱核心耗时变化% | 最差邻区变化% | 最差全网变化% | 逻辑场景 |',
              '| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |']
    for row in rows:
        policy = '保留无治理基准'
        if row['qualified']:
            policy = '、'.join(f"{c['from_node']}→{c['to_node']}：{c['old_parking']}→{c['parking']}级" for c in row['changes'])
        values = ['—' if row[k] is None else f'{row[k]:+.6f}' for k in ('core_change','neighbor_change','network_change')]
        lines.append(f"| {LABELS[row['region']]} / {'、'.join(row['roads'])} | {LABELS[row['strategy']]} | {row['eligible']} | {policy} | "
                     f"{' | '.join(values)} | {row['logical_scenes']}/{row['budget']} |")
    lines += ['', '## 回退到底做了什么', '']
    for region in selection['regions']:
        if region['status'] != 'selected':
            lines += [f"- {LABELS[region['id']]}：无候选，保留空缺。"]
            continue
        rid = region['id']
        entry = state['regions'][rid]['strategies']['feedback_backtracking']
        traces = [t for t in state['trace'] if t['region'] == rid and t['strategy'] == 'feedback_backtracking']
        branches = entry['summary']['backtracks']
        lines += [f"### {LABELS[rid]}：{'、'.join(region['roads'])}", '']
        if not branches:
            lines.append('没有触发回退，不能把本区域结果归功于回退机制。')
        for branch in branches:
            policy = branch['after_policy']+1
            actions = [t for t in traces if t['policy'] == policy]
            measured = [r for r in entry['runs'] if r['name'] in {t['name'] for t in actions}]
            passed = len(measured) == 3 and all(r['comparison']['eligible'] for r in measured)
            direction = '、'.join(f"{c['from_node']}→{c['to_node']}" for c in branch['changes'])
            lines.append(f"- 候选{branch['after_policy']}后，改试{direction}停车2级；实际评估{len(measured)}个需求，"
                         f"{'三需求全部通过' if passed else '存在失败或缺测'}。")
            for record in measured:
                comparison = record['comparison']
                reason = '通过原硬约束' if comparison['eligible'] else '；'.join(comparison['violations'])
                lines.append(f"  - ×{record['demand_factor']}，`{record['physical_run']}`：{reason}。")
        fixed = next(r for r in rows if r['region']==rid and r['strategy']=='fixed_diverse')
        old = next(r for r in rows if r['region']==rid and r['strategy']=='feedback_halving')
        new = next(r for r in rows if r['region']==rid and r['strategy']=='feedback_backtracking')
        if new['qualified'] and not old['qualified']:
            lines.append('回退反馈在该区域找到了原缩减反馈未找到的合格措施。')
        elif not new['qualified']:
            lines.append('回退反馈最终仍无合格统一措施；保留失败结果，不扩大范围或放宽约束。')
        elif policy_key(new['changes']) == policy_key(old['changes']):
            lines.append(f"原缩减反馈与回退反馈推荐相同措施；实际逻辑场景分别为{old['logical_scenes']}和{new['logical_scenes']}。")
            if new['logical_scenes'] > old['logical_scenes']:
                lines.append('本区域额外评估增加了候选覆盖，但未改变最终建议，不能记为推荐效果提升。')
        else:
            lines.append('原缩减反馈也有合格措施；应继续比较统一推荐和实际代价，不能只凭合格数量宣称算法更优。')
        if fixed['qualified']:
            lines.append('预先包含稀疏措施的固定策略在该区域同样找到合格措施。')
        lines.append('')
    lines += ['## 同时披露局部代价', '']
    for row in rows:
        if row['risks']:
            risk = row['risks']
            lines.append(f"- {LABELS[row['region']]} / {LABELS[row['strategy']]}：最差1跳邻区耗时"
                         f"{risk['worst_inner_neighbor_time_change_pct']:+.6f}%，毛新增全网过饱和"
                         f"{risk['worst_new_network_oversaturated_km']:.6f} km，恶化路段耗时增量合计最高"
                         f"{risk['worst_gross_time_added_pcu_h']:.6f} pcu·h。")
    lines += ['', '## 评分证据与局限', '',
              '- 反馈能力：实测反馈后的回退动作、下一步生成时序、完整决策重放；重复措施也可能触发回退，不把这种情况写成失败后的挽救。',
              '- 对照合理性：固定策略已有稀疏措施；每策略相同预算、统一硬约束、完整评价核心和三需求配对。',
              '- 可复现性：选区前登记19份历史范围；新区域在治理前冻结；模型输入白名单、原生进程日志、代码快照与四数组复跑。',
              '- 路段耗时为Σ(qL/v)，不包含全部节点延误。冻结核心内缩小措施不会缩小评价范围。',
              '- 本轮仍属同一赛事调整案例的区域验证，不能证明跨城市、统计显著、现实最优或必然提高比赛分数。异机真实求解与本版新视频仍需补齐。', '',
              '## 核验入口', '', '```powershell',
              f'.venv/Scripts/python.exe scripts/verify_region_study.py 智能体实验/challenges/{state["id"]} --archive --readonly',
              '```', '', '完整实验报告列出每个失败候选、缺测需求、统一推荐和风险；证据摘要保留本次原始数据重算摘要及代码版本引用。', '']
    (output/'评审摘要.md').write_text('\n'.join(lines), encoding='utf-8')
    (output/'完整实验报告.md').write_bytes((job/'report.md').read_bytes())
    save_json(output/'证据摘要.json', dict(job=state['id'], verification=verification, outcomes=rows,
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
    colors = {'fixed_diverse':'#8197ce','feedback_halving':'#e2b36d','feedback_backtracking':'#53a98d'}
    fig, axes = plt.subplots(1,2,figsize=(13,6.8), gridspec_kw={'width_ratios':[1.35,1]})
    positions = list(range(len(rows)))
    for ax in axes:
        ax.set_axisbelow(True)
        ax.grid(axis='x', color='#dbe1e8', linewidth=.7)
        ax.spines[['top','right']].set_visible(False)
        ax.set_yticks(positions)
        ax.invert_yaxis()
    axes[0].set_yticklabels([f"{LABELS[r['region']]} · {LABELS[r['strategy']]}" for r in rows],fontproperties=font,fontsize=10)
    axes[1].set_yticklabels([])
    max_benefit = max([abs(r['core_change']) for r in rows if r['core_change'] is not None] or [1])
    for y,row in enumerate(rows):
        c=colors[row['strategy']]
        if row['qualified']:
            benefit=-row['core_change']
            axes[0].barh(y,benefit,color=c,height=.58)
            axes[0].text(benefit+max_benefit*.02,y,f'{benefit:.3f}%',va='center',fontsize=9)
        else:
            axes[0].plot(0,y,'x',color='#b15b62')
            axes[0].text(max_benefit*.025,y,'无合格措施',fontproperties=font,va='center',fontsize=9,color='#9b4c55')
        axes[1].barh(y,13,color='#edf0f4',height=.58)
        axes[1].barh(y,row['logical_scenes'],color=c,height=.58)
        axes[1].text(row['logical_scenes']+.18,y,f"{row['logical_scenes']}/13",va='center',fontsize=9)
    axes[0].set_xlim(left=-max_benefit*.04,right=max_benefit*1.32)
    axes[1].set_xlim(0,15)
    axes[0].set_title('统一建议：三个需求中最弱的核心耗时降幅',fontproperties=font,fontsize=12,pad=14)
    axes[1].set_title('实际逻辑场景 / 同一预算上限',fontproperties=font,fontsize=12,pad=14)
    axes[0].set_xlabel('核心路段Σ(qL/v)下降百分比；无合格措施不填造收益',fontproperties=font,fontsize=10)
    axes[1].set_xlabel('共享结果仍各自计费；包含三个基准和一次复跑',fontproperties=font,fontsize=10)
    fig.suptitle('更强固定对照与回退搜索 · 全部事前区域',fontproperties=font,fontsize=16,y=.98)
    fig.text(.5,.02,'赛事调整案例；确定性复跑不是独立样本；此图不等于比赛得分',ha='center',fontproperties=font,fontsize=10,color='#566173')
    fig.tight_layout(rect=[0,.045,1,.94])
    fig.savefig(output/'结果对照.png',dpi=170)
    fig.savefig(output/'结果对照.svg')
    plt.close(fig)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='严格重算封存的回退挑战并导出摘要和图表')
    parser.add_argument('job',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    print(export(args.job.resolve(),args.output.resolve()))
