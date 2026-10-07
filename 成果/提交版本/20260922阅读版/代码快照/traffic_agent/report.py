"""Readable reports built only from recorded completed model runs."""
import csv
import html
import json
from pathlib import Path


def fmt(value):
    return "—" if value is None else f"{value:.5f}" if isinstance(value, float) else str(value)


def write_report(job, state):
    lines = ["# 交通仿真智能体运行报告", "", f"任务：{state['id']}", "",
             f"运行状态：**{state['status']}**", "", f"输入需求：{state['task']['request']}", "",
             "本程序采用确定性规则推理、知识条目检索及真实TranStar工具调用，不依赖大语言模型服务。",
             "赛事南京案例已做数据调整，以下结果不代表南京真实交通状况。", "",
             "## 推荐与适用条件", ""]
    if state["status"] == "completed":
        lines += [f"基准需求下推荐：**{state.get('recommendation', '—')}**。", "", state.get("recommendation_rule", ""), ""]
        robust = state.get("robust_across_tested_demand")
        lines += [{True: "推荐方案在本次全部需求敏感性测试中通过既定约束。",
                   False: "推荐方案未在全部需求水平下通过约束，不能作为跨需求水平的统一推荐。",
                   None: ("已测试额外需求；主推荐为基准，不声明统一治理方案稳健性。" if state["sensitivity"] else "本次没有执行额外需求水平，不宣称需求稳健性。")}[robust], ""]
        if state.get("recommendation_by_car_demand"):
            lines += ["按已测小汽车需求条件分别推荐：", ""]
            lines += [f"- 系数 {factor}：{name}" for factor, name in sorted(state["recommendation_by_car_demand"].items())]
            lines += ["", "以上条件建议仅覆盖已测离散需求水平，不对系数之间做未经仿真的插值推断。", ""]
    else:
        lines += ["本任务尚未形成经完整验证的推荐。", "", state.get("error", state.get("reason", "")), ""]
    if state['task'].get('recommendation_version') == 'tolerance-v2' and state['status'] == 'completed':
        from .selection import record_selection
        from .decision_analysis import decision_analysis
        review = (decision_analysis(state)['selection'] if state['task'].get('search_mode') == 'robust' else
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
    for decision in state["decisions"]:
        lines += [f"- 观察 {decision['observed_run']}：{decision['reason']}。后续候选是否进入执行：{decision['will_execute']}。",
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
    # A self-contained readable fallback, without remote dependencies.
    (job / "report.html").write_text("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>智能体运行报告</title>"
        "<style>body{max-width:1200px;margin:32px auto;padding:0 24px;font:16px/1.7 system-ui;color:#172c35}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}</style><body><pre>" +
        html.escape("\n".join(lines)) + "</pre></body></html>", encoding="utf-8")
