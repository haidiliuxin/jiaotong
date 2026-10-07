"""Readable reports built only from recorded completed model runs."""
import csv
import json
from pathlib import Path

from .source_context import report_notice


def fmt(value):
    return "—" if value is None else f"{value:.5f}" if isinstance(value, float) else str(value)


def professional_impact_lines(state):
    from .professional_review import review
    facts=review(state)
    if not facts['policies']:return []
    scope=facts['evidence_scope']
    repeat={'not_requested':'未要求基准复跑','not_completed':'要求复跑但尚未完成',
            'exact_match':'基准复跑一致','inconsistent_or_unverified':'基准复跑不一致或未经证实'}[scope['repeat_status']]
    lines=['## 局部影响与证据边界','',facts['rule'],'',
           '| 场景（需求） | 核心有向路段中耗时增加的条数 | 正增量 pcu·h | 改善量 pcu·h | 净变化 pcu·h |',
           '| --- | ---: | ---: | ---: | ---: |']
    for item in facts['policies']:
        label=str(item['run']).replace('|','\\|')+f"（×{item['demand_factor']:g}）"
        if item['status']=='measured':
            lines.append(f"| {label} | {item['worsened_link_count']} / {item['core_link_count']} | "
                         f"{fmt(item['gross_added_pcu_h'])} | {fmt(item['gross_saved_pcu_h'])} | {fmt(item['net_change_pcu_h'])} |")
        else:
            lines.append(f"| {label} | 未验证 | — | — | — |")
            lines.append('- '+str(item['run'])+'：'+item['gap'])
        for link in item['largest_increases']:
            lines.append(f"- {item['run']} 增加最多的核心方向：{str(link['name']).replace('|','/')} "
                         f"{link['from_node']}→{link['to_node']}，+{fmt(link['added_pcu_h'])} pcu·h。")
    levels='、'.join(f'×{factor:g}' for factor in scope['demand_levels']) or '无'
    lines += ['',f'已设需求：{levels}；{repeat}；现场标定／独立验证未由本任务记录证明，未计算统计置信区间。',
              facts['boundary'],'','后续证据建议（不会自动追加仿真）：','']
    lines += ['- '+item['action'] for item in facts['next_checks']]
    lines.append('')
    return lines


def comparison_lines(groups):
    """The same measured coverage and separate checks as the planning panel."""
    output=[]
    label=lambda v,yes,no:yes if v is True else no if v is False else '未证实'
    measured=lambda v:f'{v:.6g}' if isinstance(v,float) and 0<abs(v)<.00005 else fmt(v)
    for group in groups:
        if group.get('version')!='study-comparison-v2':continue
        output += ['### '+str(group.get('title') or group.get('road') or '本组实测'),'',
                   '实际完成（由场景记录生成）：'+group['coverage_text'],'',
                   '| 场景 | 本场措施 | 原生控制响应 | 配对核心耗时 | 邻区两项门槛 | 推荐资格 |',
                   '| --- | --- | --- | --- | --- | --- |']
        for factor in group['factors']:
            for policy in factor['policies']:
                checks=policy['checks']
                control='不涉及' if checks['control_applicable'] is False else label(checks['control_passed'],'通过','未通过')
                output.append(f"| {policy['run']}（需求×{factor['demand_factor']:g}） | {policy['label']} | {control} | {label(checks['core_time_decreased'],'下降','未下降')} | {label(checks['neighbor_limits_passed'],'门槛内','超门槛')} | {label(checks['recommendation_admissible'],'通过','不具备')} |")
        output.append('')
        for factor in group['factors']:
            for policy in factor['policies']:
                directions=['禁行'+ '→'.join(map(str,d['closed']))+'；保留'+ '→'.join(map(str,d['retained']))
                            if 'closed' in d else '禁转'+'→'.join(map(str,d['turn'])) for d in policy['control_directions']]
                limits=policy['neighbor_limits']
                output.append(f"- {policy['run']}（需求×{factor['demand_factor']:g}）："+('；'.join(directions)+'。' if directions else '')+
                    f"邻区耗时变化{measured(limits.get('neighbor_time_change_pct'))}%（冻结上限{fmt(limits.get('time_limit_pct'))}%）；"+
                    f"毛新增过饱和{measured(limits.get('neighbor_new_oversaturated_km'))}km（冻结上限{fmt(limits.get('new_oversaturated_limit_km'))}km）。")
        output += ['', '控制响应、配对指标和推荐资格分别记录；各组结果独立，不相加为联合实施收益。','']
    return output


def recommendation_summary(state):
    if state['task']['scenario'] == 'diagnosis':
        return ['本任务仅作现状诊断，没有执行治理，不提供治理收益或稳健推荐。', '']
    if state['status'] != 'completed':
        return ['本任务尚未形成经完整验证的推荐。', '', state.get('error', state.get('reason', '')), '']
    lines = [f"基准需求下推荐：**{state.get('recommendation', '—')}**。", '', state.get('recommendation_rule', ''), '']
    robust = state.get('robust_across_tested_demand')
    lines += [{True: '推荐方案在本次全部需求敏感性测试中通过既定约束。',
               False: '推荐方案未在全部需求水平下通过约束，不能作为跨需求水平的统一推荐。',
               None: ('已测试额外需求；主推荐为基准，不声明统一治理方案稳健性。' if state['sensitivity'] else
                      '本次没有执行额外需求水平，不宣称需求稳健性。')}[robust], '']
    if state.get('recommendation_by_car_demand'):
        lines += ['按已测小汽车需求条件分别推荐：', '']
        lines += [f"- 系数 {factor}：{name}" for factor, name in sorted(state['recommendation_by_car_demand'].items())]
        lines += ['', '以上条件建议仅覆盖已测离散需求水平，不对系数之间做未经仿真的插值推断。', '']
    return lines


def risk_lines(entry):
    from .risk_review import display
    review=display(entry)
    if not review:return ['风险与缺口：'+entry.get('risk','详见调用记录'),'']
    lines=['实测代价（来自观测）：'+review['measured_summary'],'','模型待验证风险：','']
    coverage=review.get('observed_detail_coverage',[])
    if coverage:
        lines+=['该轮模型实际收到的新增过饱和明细：','']
        lines+=['- '+r['ref']+'：'+r['text'] for r in coverage]
        lines+=['','这是静态过饱和清单，不是动态排队或回堵证据。','']
    for row in review['unverified']:
        label='验证所需证据（模型选择类型，提供情况来自本轮输入）：' if row.get('verification_origin') else '还需验证（模型原文）：'
        lines += ['- 待验证假设：'+row['hypothesis'],'- '+label+row['verification_needed'],
                  '- 提出问题所据观测：'+', '.join(row['basis_refs']),'']
    if not review['unverified']:lines += ['模型未额外列出待验证风险，不表示没有风险。','']
    return lines+[review['boundary'],'']


def candidate_comparison_lines(entry):
    from .candidate_comparison import selected
    relation=selected(entry)
    if not relation:return []
    lines=['这次对照改变了什么（由参数生成）：',
           f"参照{relation['reference_ref']}，需求×{relation['reference_demand_factor']}；治理方向数：{relation['reference_direction_count'] if relation['reference_direction_count'] is not None else '未提供'}。",'']
    for role,label in (('chosen','所选候选'),('alternative','暂缓备选')):
        if relation[role]:
            count=relation[role]['direction_count']
            lines+=['- '+label+'：'+(f'{count}条治理方向；' if count is not None else '方向数未提供；')+relation[role]['interpretation']]
            if relation[role].get('demand_summary'):lines+=['  '+relation[role]['demand_summary']]
    return lines+['',relation['scope'],'']


def write_report(job, state):
    lines = ["# 交通仿真智能体运行报告", "", f"任务：{state['id']}", "",
             f"运行状态：**{state['status']}**", "", f"输入需求：{state['task']['request']}", "",
             ("大模型读取本次新观测选择后续停车实验；真实TranStar执行及推荐筛选仍受固定校验约束。" if state.get('planning_protocol') else
              "本报告使用真实TranStar工具调用与固定验证协议。模型规划及组间反馈另存于dispatch.json；治理结论以本组实测为准。"),
             report_notice(state.get("source")), "",
             "## 推荐与适用条件", ""] + recommendation_summary(state) + professional_impact_lines(state)
    from .conclusion_review import assess_state
    conclusion=assess_state(state)
    if state.get('planner_trace'):
        lines += ['## 实测代价与结论核对', '',
                  '**'+('结论待核对：原生执行完成不代表模型解释可靠。' if conclusion['issues'] else '未发现本校验覆盖范围内的冲突。')+'**', '',
                  conclusion['scope'], '',
                  '| 场景 | 约束通过 | 存在实测代价 | 邻区耗时变化% | 邻区毛新增过饱和km | 邻区净变化km |',
                  '| --- | --- | --- | ---: | ---: | ---: |']
        for row in conclusion['costs']:
            lines += [f"| {row['run']} | {'是' if row['constraint_passed'] else '否'} | {'是' if row['has_measured_cost'] else '已提供指标未见增加'} | {fmt(row['neighbor_time_change_pct'])} | {fmt(row['neighbor_new_oversaturated_km'])} | {fmt(row['neighbor_net_oversaturated_km'])} |"]
        lines += ['','毛新增只计本轮新过饱和路段；净变化包含其他路段改善，两者不能互换。','']
        lines += ['- '+i['message'] for i in conclusion['issues']]
        lines += ['']
    if state.get('programme_review'):
        overview=state['programme_review']
        lines += ['## 整个研究任务的复核', '',
                  '**'+('各组实测与整体复核齐全' if overview['complete'] else '整个研究任务仍未完整')+'**', '']
        lines += comparison_lines(overview.get('comparisons',[]))
        lines += ['模型整体解释（原文）：',overview['summary'], '', overview['evidence']['scope'], '']
        for group in overview['evidence']['groups']:
            lines += [f"- {group['title']}：{group['engine']}；"+('约定实测齐全' if group['execution_complete'] else '实测未齐')]
        lines += ['']+['尚未回答：'+gap for gap in overview['unanswered']]+['']
        for update in overview['updates']:
            hypothesis=next(h['hypothesis'] for h in overview['hypotheses'] if h['id']==update['hypothesis_id'])
            lines += [f"- 总任务初始假设：{hypothesis}", f"- 模型判断：{update['judgment']}；{update['explanation']}",
                      '- 所引检验：'+(', '.join(update['finding_ids']) or '没有足够检验'), '']
    if not state.get('programme_review',{}).get('comparisons'):
        from .study_comparison import comparison
        lines += comparison_lines([comparison(state)])
    if state.get('regions'):
        lines += ['## 固定候选区域与本轮观测', '', '各区域并集在治理前冻结；总体改善不能抵消另一区域超过既定门槛的恶化。候选目录未穷举全网。', '',
                  '| 场景 | 区域 | 核心速度 km/h | 区域耗时 pcu·h |', '| --- | --- | ---: | ---: |']
        for run in state['runs']:
            for region in run.get('spatial_observation',{}).get('regions',[]):
                lines.append(f"| {run['name']} | {region['title']} | {fmt(region['weighted_speed_kmh'])} | {fmt(region['running_time_pcu_h'])} |")
        lines.append('')
    if state.get('planner_trace'):
        from .planning_review import coverage, decision_notes, public_stop_basis
        lines += ['## 基于实际观测的模型决策', '']
        for t in state['planner_trace']:
            stop_basis = public_stop_basis(t)
            follow_label='实际执行安排（由所选动作定义生成）' if t.get('next_check_origin') else '后续验证'
            summary_label='所选动作（由执行定义生成）：' if t.get('summary_origin') else ''
            from .choice_rationale import text as choice_reason
            lines += [f"### 决策 {t['ordinal']} · {t['status']}", '', summary_label+t.get('summary', ''), '',
                      stop_basis or choice_reason(t) or t.get('reason', ''), '', f"选择：{t.get('selected', {}).get('title', '停止新增实验')}", '',
                      *risk_lines(t), f"{follow_label}：{t.get('next_check', '未自动重试')}", '',
                      '观测引用：' + ', '.join(t.get('evidence_refs', [])), '']
            if choice_reason(t):
                lines += ['会否定该思路的观测：'+t['selection_basis']['disconfirming_result'],'',
                          '暂缓备选的取舍（效果仍待验证）：'+t['selection_basis']['alternative_tradeoff'],'']
            lines += candidate_comparison_lines(t)
            if t.get('review_correction'):
                from .planning_response import request_usages
                correction=t['review_correction'];first=correction['first_attempt'];usages=request_usages(t)
                title={'parking-arguments-correction-v1':'工具格式修正','candidate-choice-correction-v1':'选点引用修正',
                       'study-review-correction-v1':'本组交接复核修正'}.get(correction['version'],'最终复核修正')
                lines += [f'{title}（与其他决策修正共享一次机会）：首次请求未通过，'+first['validation']['message'],'',
                          f'两次请求均计入原预算：已知{sum(u.get("tokens") or 0 for u in usages)} tokens；'+
                          f'用量未知{sum(u.get("tokens") is None for u in usages)}次。','']
                if correction['version']=='parking-arguments-correction-v1':
                    lines += ['首次仅保留解析诊断，没有可采纳动作；使用同一份观测和动作菜单重新提交，回复仍须通过全部原校验。','']
            if t.get('proposed_execution'):
                lines += ['被拒动作提议（由执行定义生成，未执行）：'+t['proposed_execution']['summary'],'',
                          t['proposed_execution']['next_check'],'']
            spec=t.get('input',{}).get('context',{}).get('action_execution')
            if spec:
                model=(t.get('review_correction',{}).get('input') or t['input'])['model_budget']
                lines += [f'该次决策输入：剩余模型请求{model["remaining"]}次（含本次）；原生场景另计。','']
                for key,label in [('current_study','本组'),('programme_allocated','全任务已分配')]:
                    budget=spec['native_budget'].get(key)
                    if budget:lines += [f'{label}原生场景：已用{budget["used"]}／上限{budget["limit"]}，其中完成{budget["completed"]}，剩余{budget["remaining"]}。','']
            if stop_basis:
                lines += ['模型原始停止理由保存在 state.json 的 planner_trace 中，供逐项追溯。', '']
            lines += ['执行器提示：'+n for n in decision_notes(t)]
            lines.append('')
        lines += ['模型决策摘要不是成功证明；推荐只使用实际执行且通过既定约束的结果。', '']
        reviewed=coverage(state)
        if reviewed:
            if reviewed.get('requirements'):
                lines += ['## 研究完成判定', '', '各项均以本任务实际输出核对；有收益并不等于完成比较。', '']
                lines += [('- [x] ' if c['fulfilled'] else '- [ ] ')+c['label'] for c in reviewed['requirements']]
                lines += ['', '| 需求系数 | 停车等级 | 实测场景 | 核心速度 km/h | 通过约束 |', '| --- | --- | --- | ---: | --- |']
                for row in reviewed['strength_matrix']:
                    for c in row['levels']:
                        lines.append(f"| {row['demand_factor']} | {c['level']} | {c['run'] or '未测'} | {fmt(c['speed_kmh'])} | {'通过' if c.get('comparison',{} ) and c['comparison'].get('eligible') else '未通过或未测'} |")
                lines.append('')
            lines += ['尚未回答：'+n for n in reviewed['unanswered']]
            lines.append('')
    if state.get('research_review'):
        review=state['research_review']
        lines += ['## 初始假设与实测检验', '', '以下检验由本组已完成配对计算；模型对任意文字假设的解释单列，不能视为现实因果证明。', '']
        for finding in review['evidence']['findings']:
            lines += [f"### {finding['question']}", '', f"检验状态：{finding['status']}。{finding['answer']}", '',
                      '配对引用：'+(', '.join(finding['evidence_refs']) or '无完整配对'), '']
            if finding['id']=='factorial':
                lines += ['| 需求 | 措施 | 实测场景 | 全网耗时变化% | 邻区耗时变化% | 通过检查 |',
                          '| --- | --- | --- | ---: | ---: | --- |']
                for row in finding['measurements']:
                    for cell in row['cells']:
                        delta=cell.get('delta') or {}
                        lines.append(f"| {row['factor']} | {cell['title']} | {cell['run'] or '未测'} | {fmt(delta.get('network',{}).get('running_time_change_pct'))} | {fmt(delta.get('neighbor',{}).get('running_time_change_pct'))} | {'通过' if cell['eligible'] else '未通过或未测'} |")
                lines.append('')
        for update in review['updates']:
            hypothesis=next(h['hypothesis'] for h in review['hypotheses'] if h['id']==update['hypothesis_id'])
            lines += [f"- 初始假设：{hypothesis}", f"- 本组模型复核：{update['judgment']}；{update['explanation']}",
                      '- 所引检验：'+(', '.join(update['finding_ids']) or '尚无足够检验'), '']
    if state["task"]["scenario"] == "diagnosis":
        lines += ["## 现状诊断", ""]
        for item in state.get("diagnostic_observations", []):
            lines += [f"### 小汽车需求系数 {item['car_demand_factor']}", "",
                      f"读取 {item['inspected_links']} 条道路方向，过饱和 {item['overloaded_links']} 条。", "",
                      "| 道路 | 路段数 | 路段耗时 pcu·h | 过饱和有向 km |", "| --- | --- | --- | --- |"]
            lines += [f"| {r['name']} | {r['links']} | {fmt(r['time_pcu_h'])} | {fmt(r['overloaded_km'])} |" for r in item["top_roads"]]
            lines.append("")
    if state['task'].get('max_new_network_oversaturated_km') is not None:
        limit = state['task']['max_new_network_oversaturated_km']
        lines += [f'本任务事前设定全网毛新增过饱和有向里程≤{limit:g}km；超过即拒绝候选，'
                  '即使全网过饱和里程净变化为零或下降。该阈值是实验假设，不代表工程标定值。', '']
    if state['task'].get('recommendation_version') == 'tolerance-v2' and state['status'] == 'completed' and state['task']['scenario'] != 'diagnosis':
        from .selection import record_selection
        from .decision_analysis import decision_analysis
        review = (decision_analysis(state)['selection'] if state['task'].get('search_mode') == 'robust' or state.get('model_grid') else
                  record_selection([r for r in state['runs'] if r['demand_factor'] == 1 and r['changes']]))
        lines += ['## 容差推荐与复杂度', '', review['rule'], '',
                  '复杂度为（不同措施数，停车1级措施数），属于排序假设，不是实际费用。', '',
                  '| 候选 | 指标（全网%、邻区%、新增km） | 复杂度 | 与选中项的指标差 |', '| --- | --- | --- | --- |']
        for c in review['candidates']:
            lines.append(f"| {c['name']} | {c['objectives']} | {c['complexity']} | {c['difference_from_selected']} |")
        lines += ['', f"进入近似相同组：{', '.join(review['equivalent_candidates']) or '无'}。", '']
    if state.get("search_trace"):
        from .decision_analysis import decision_analysis
        analysis = decision_analysis(state)
        lines += ["## 主动稳健搜索与可重放决策", "",
                  "每次决策先写入日志再调用TranStar。失败后的路段排序来自联合治理观测，不能解释为单路段因果贡献。",
                  "统一推荐要求同一措施在全部设定需求下通过；条件推荐仅适用于各自已测需求。", "",
                  "| 次序 | 执行场景 | 读取的反馈 | 下一步依据 |", "| --- | --- | --- | --- |"]
        for t in state["search_trace"]:
            lines.append(f"| {t['sequence']} | {t['name']} | {t['observed_run'] or '预定基准/初始试验'} | {t['reason']} |")
        lines += ["", "| 措施 | 合格需求 | 失败需求 | 缺测需求 | 最差全网耗时变化% |", "| --- | --- | --- | --- | ---: |"]
        for p in analysis["policies"]:
            passed = [c["factor"] for c in p["checks"] if c["accepted"]]
            failed = [c["factor"] for c in p["checks"] if not c["accepted"]]
            lines.append(f"| {p['name']} | {passed} | {failed} | {p['missing']} | {fmt(p['worst'][0]) if p['worst'] else '不具备统一推荐资格'} |")
        summary = state.get("robust_search", {})
        lines += ["", f"停止原因：{summary.get('stop_reason', '尚在运行')}。",
                  f"实际完成 {len(state['runs'])} 次 / 预算 {state['task']['max_runs']} 次；未用预算不自动转为额外候选。",
                  "缺测工况不插值、不算作通过；提前停止未测配对不等同于已完成的等预算对照实验。",
                  "协议：search_protocol.json；决策链：state.json/search_trace；核验：verification.json/active_search_audit。", ""]
    from .tradeoffs import tradeoffs
    lines += ['## 不利影响', '', '通过原约束不代表所有指标改善；以下为各候选相对同需求基准的恶化项。过饱和净增不能代替毛新增。', '']
    for item in tradeoffs(state):
        lines.append(f"- {item['scenario']}（OD×{item['demand_factor']}）：" + '；'.join(item['warnings']))
    lines += ["", "## 固定范围指标", "", "下表中时间为1小时窗口内Σ(qL/v)，单位pcu·h；速度为Σ(qL)/Σ(qL/v)，单位km/h。过饱和里程为软件V/C>1的有向公里。", "",
              "| 场景 | 小汽车OD系数 | 范围 | 路段数 | 加权速度 | 行驶时间消耗 | 过饱和km | TimeLink极大值数 |",
              "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: |"]
    table = []
    for run in state["runs"]:
        if not run.get("metrics"):
            continue
        for area, values in run["metrics"].items():
            row = dict(scenario=run["name"], car_demand_factor=run["demand_factor"], scope=area, **values)
            table.append(row)
            lines.append("| " + " | ".join(fmt(x) for x in [run["name"], run["demand_factor"], area,
                         values["directed_links"], values["weighted_speed_kmh"], values["running_time_pcu_h"],
                         values["oversaturated_km"], values["timelink_flagged_links"]]) + " |")
    lines += ["", "## 反馈记录", ""]
    from .planning_review import public_stop_basis
    stop_reasons = {}
    for turn in state.get('planner_trace', []):
        basis = public_stop_basis(turn)
        if basis and turn.get('reason'):
            stop_reasons[turn['reason']] = basis
    for decision in state["decisions"]:
        reason = stop_reasons.get(decision['reason'], decision['reason']).rstrip('。')
        lines += [f"- 观察 {decision['observed_run']}：{reason}。后续候选：{'已安排执行' if decision['will_execute'] else '未安排执行'}。",
                  f"  约束违例：{'；'.join(decision['violations']) or '无'}。"]
        if decision.get("execution_reason"):
            lines.append(f"  执行判断：{decision['execution_reason']}。")
    if state["task"]["scenario"] == "oneway":
        if state.get("hard_access_profile"):
            verified = sum(r.get("hard_access_evidence", {}).get("native_calls", 0) for r in state["runs"])
            lines += ["", "## 新增禁行的执行与输入证据", "",
                      f"采用原生求解前硬闭边适配v{state['hard_access_profile']['version']}，当前已完成并核验{verified}次车型分轮求解。",
                      "每轮保存转换前原始图及阻抗，核对只移除新增禁行边、保留边阻抗与OD不变；保留缩小图原生输出及返回完整边序的映射。",
                      "各车型五轮OD按单元核验，最终车型流量按FT与GEOM端点对应后核对分轮累加。",
                      "该适配只处理本任务显式新增闭边，不代表原模型全部既有禁转生效。禁行响应通过也不代表治理指标合格。", ""]
        lines += ["", "## 单向与停车完整组合", "",
            "采用独立验证的Vehicle1分车型流程、5次多路径增量加载、派生参数3.3。不能与默认分配程序的历史结果交叉计算收益。",
            "S1为同一道路双向停车2级，S2关闭一条机动车方向并保留反向，S3为两者组合。公交线路边及小汽车、摩托车、货车的原有边界和端点接入均检查。",
            "各需求点测齐三项候选及自身基准，按约束筛除并选择；这是一组预定组合，不宣称自适应搜索贡献。",
            "除核心和邻区门槛外，要求全网qL/v严格改善，避免仅用关闭方向的流量减少推荐。未更新的机动车转向表已在新副本移除，不用于禁转验证。", "",
            "| 场景 | 关闭方向 | 小汽车基准→政策 | 出租车基准→政策 | 摩托车基准→政策 | 实际禁行 | 指标约束 |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
        for run in state["runs"]:
            r = run.get("oneway_response")
            if r:
                values = [f"{r['flows'][m]['before']}→{r['flows'][m]['after']}" for m in ("car", "taxi", "motor")]
                lines.append("| " + " | ".join([run["name"], "→".join(map(str, r["closed_direction"])), *values,
                    str(r["passed"]), "；".join(run["comparison"]["violations"]) or "通过"]) + " |")
        lines += ["", f"完整需求组合已完成：{state.get('factorial_design', {}).get('full_demand_grid', False)}。", ""]
    if state["task"]["scenario"] == "circulation":
        if state.get('turn_access_profile'):
            count = sum(r.get('turn_access_evidence', {}).get('native_calls', 0) for r in state['runs'])
            lines += ["", "## 显式转向状态图完整组合", "",
                      f"用户指定转向实验，当前已完成并核验{count}次原生调用，每轮包含适配求解和同输入原版参考各一次。",
                      "指定出口为停车核心，未使用旧自动瓶颈速度门槛选点；基准与政策均在该路口执行既有禁转和禁止掉头。",
                      "四车型转移量来自原生状态边五轮累加，不使用本分支未更新的TVolNode表。原本为零的车型不作为正流量响应证据。",
                      "每需求完整测齐四项方案，另要求全网qL/v严格改善。每轮核对转换成本、OD、物理流量映射、原版参考净流量与最终累加。",
                      f"完整需求组合已完成：{state.get('factorial_design', {}).get('full_demand_grid', False)}。", "",
                      "| 场景 | 小汽车 | 出租车 | 摩托车 | 货车 |",
                      "| --- | --- | --- | --- | --- |"]
            for run in state['runs']:
                for movement in run.get('turn_response', {}).get('movements', []):
                    cells = [f"{movement['flows'][m]['before']:.6f}→{movement['flows'][m]['after']:.6f}" +
                             ("（基准为零，无正响应证据）" if movement['flows'][m]['before'] == 0 else "")
                             for m in ('car', 'taxi', 'motor', 'truck')]
                    lines.append('| ' + ' | '.join([run['name'], *cells]) + ' |')
        lines += ["", "## 微循环与组合实验证据", "",
                  "本任务在通过规则的新验证区域冻结范围，不能与七东线停车任务跨区域直接比较收益。S1为停车2级、S2为单个禁转、S3为二者组合。",
                  "合法替代路径保留进路和出路状态，同时检查全部新增禁转；不使用虚拟连接线或即时掉头。额外绕行≤2km且距离≤原出口路段3倍。",
                  "既有公交线路使用的转向不进入候选；这只保护线路拓扑，不保证公交旅行时间不增加。", "",
                  "| 场景 | 禁转 | 基准小汽车转向量 | 治理后转向量 | 缓存完整且转向响应通过 | 指标约束 |",
                  "| --- | --- | ---: | ---: | --- | --- |"]
        for run in state["runs"]:
            response = run.get("turn_response")
            if response:
                for movement in response["movements"]:
                    lines.append("| " + " | ".join([run["name"], "→".join(map(str, movement["turn"])),
                        fmt(movement["before_car_turn_flow"]), fmt(movement["after_car_turn_flow"]),
                        str(response["passed"]), "；".join(run["comparison"]["violations"]) or "通过"]) + " |")
        if state.get('turn_access_profile'):
            lines += ["", "同需求小汽车基准转移量须>1，政策四车型目标转移均为零，并核对完整ManageNM缓存。相对原版净流量一致不等于全OD路径正确；本证据仅覆盖指定路口，不代表全网既有禁转或现实信号配时。", ""]
        else:
            lines += ["", "软件转向流量验证要求同条件基准>1且禁转后≤0.01，并核对ManageN全部规则进入新生成的ManageNM缓存；不以原本为零的转向作为成功证据。",
                      "该实验验证局部禁转，不声称单向道路组织、现实信号配时、全OD路径追踪或完整3×3组合敏感性已完成。", ""]
    lines += ["", "## 敏感性与复现", "",
              "需求变化仅针对小汽车OD，其他车型及公交线路保持不变。各系数以其自身无治理场景为对照，不能跨系数把需求变化当作治理收益。", ""]
    for item in state["sensitivity"]:
        # New search records already preserve the measured totals in their run
        # manifests; older sensitivity records also cache this derived ratio.
        matched = next(r for r in state["runs"] if r["name"] == item["matched_baseline"])
        primary_base = next(r for r in state["runs"] if r["name"] == "baseline")
        assigned_ratio = matched["execution"]["assigned_car_od_sum"] / primary_base["execution"]["assigned_car_od_sum"]
        lines += [f"- 小汽车OD系数 {item['car_demand_factor']}：内部实际分配需求比值 {assigned_ratio:.8f}；"
                  f"相对 {item['matched_baseline']} 的约束判定：{'通过' if item['comparison']['eligible'] else '未通过'}。"]
        if item.get("adaptation"):
            a = item["adaptation"]
            lines += [f"- 该需求下的反馈：{a['reason']}；最终条件建议为 {a['recommendation']}（{a['status']}）。"]
    if state.get("repeatability"):
        lines += ["", f"相同输入复跑：{state['repeatability']['row_count']}条路段的4类数组逐项相等 = {state['repeatability']['exact_match']}。"]
    lines += ["", "## 模型和指标边界", "",
              "- 采用固定5次多路径增量加载，不能称为均衡收敛或动态交通仿真。",
              "- 管理修正容量与软件V/C分母分别记录；反馈负荷使用前者，不混淆口径。",
              "- TimeLink含极大值时不输出其合计值；行驶时间消耗不含独立核算的全部节点延误，不称全系统总旅行时间。",
              "- 核心区与邻区由新基准一次性确定，所有方案及需求系数使用同一组有向路段。",
              "- 治理等级及约束阈值为实验假设，未验证现实停车承接、管理成本与公众接受度。",
              "", "## 证据文件", "",
              "- `plan.json`：任务卡、能力边界、知识来源。",
              "- `diagnosis.json`、`scopes.json`：候选原因和固定评价范围。",
              "- `events.jsonl`：任务、工具、反馈的时间顺序记录。",
              "- `logs/<场景>/run.json`：真实进程、退出码、输出生成与输入一致性。",
              "- `scenarios/<场景>/edits.json`：原值、新值、OD系数与白名单差异。",
              "- `state.json`：完整指标、所有候选与未通过原因、成对敏感性。",
              "- `metrics.csv`：可直接分析的指标表。", ""]
    (job / "report.md").write_text("\n".join(lines), encoding="utf-8")
    if table:
        with (job / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(table[0]))
            writer.writeheader()
            writer.writerows(table)
    from .report_html import render_report_html
    (job / "report.html").write_text(render_report_html(lines, state), encoding="utf-8")
