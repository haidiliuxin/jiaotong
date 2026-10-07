"""Budgeted, observation-driven parking search over paired demand conditions.

The generator contains no model or filesystem calls; its decisions can be replayed
from independently checked observations. No missing case is treated as a success.
"""
import math

from .data import OUTPUTS, save_json, scoped_metrics, scopes
from .decision_analysis import decision_analysis, policy_key
from .runner import now


VERSION = "adaptive-paired-search-v1"
BACKTRACK_VERSION = "adaptive-paired-search-v2"


def protocol(task):
    result = dict(version=BACKTRACK_VERSION if task.backtrack_on_singleton_failure else VERSION,
                task=task.as_dict(),
                order="首个候选按需求系数降序；反馈候选先复核上一失败需求，再按降序补齐",
                rejection="任一已测需求违反固定约束即淘汰统一候选，其余未测需求明确保留缺测",
                revision="失败时按本次联合治理中各治理路段的qL/v减少量排序，保留前半（向下取整，至少一条）；同值按节点编号",
                strength="全部需求通过且任一工况核心速度未达目标或管理修正负荷>1，才试验停车1级",
                budget="初始候选加max_feedback个反馈候选；启动新候选前预留全部需求及基准复跑；禁止重复措施",
                selection="全部需求原约束通过后采用paired-minimax-v1；无完整合格措施则统一建议为基准",
                limits="路段收益排序是联合仿真观察，不是单路段因果贡献；仅验证预设离散需求，不证明全局最优")
    if task.recommendation_version == 'tolerance-v2':
        from .selection import RULE, TOLERANCES
        result.update(selection=RULE, selection_tolerances=list(TOLERANCES))
    if task.backtrack_on_singleton_failure:
        result['backtracking'] = (
            '单路段违反约束或缩减措施已测时，按需求1.0无治理基准的各原核心路段qL/v降序，'
            '同值按节点编号，试验尚未测过的原核心单路段停车2级；先复核失败需求，'
            '再按需求降序补齐。每个新候选仍须预留完整需求网格和基准复跑；'
            '基准qL/v仅用于搜索次序，不是措施收益或因果贡献。'
        )
    return result


def initial_changes(diagnosis):
    return [dict(from_node=r["from_node"], to_node=r["to_node"], old_parking=r["parking"], parking=2)
            for r in diagnosis["selected"]]


def revise(failed, base):
    changes = failed["changes"]
    if len(changes) <= 1:
        return None, []
    before = {(r["from_node"], r["to_node"]): r for r in base["core_rows"]}
    after = {(r["from_node"], r["to_node"]): r for r in failed["core_rows"]}
    ranking = []
    for c in changes:
        key = (c["from_node"], c["to_node"])
        a, b = before[key], after[key]
        saved = a["length_km"] * (a["flow_pcu_h"] / a["speed_kmh"] - b["flow_pcu_h"] / b["speed_kmh"])
        ranking.append(dict(from_node=key[0], to_node=key[1], saved_time_pcu_h=saved))
    ranking.sort(key=lambda r: (-r["saved_time_pcu_h"], r["from_node"], r["to_node"]))
    lookup = {(c["from_node"], c["to_node"]): c for c in changes}
    selected = [lookup[(r["from_node"], r["to_node"])] for r in ranking[:max(1, len(changes) // 2)]]
    return selected, ranking


def baseline_singletons(initial, base):
    """Frozen untreated-demand-1.0 priority; never rank by treated outcomes."""
    rows = {(r['from_node'], r['to_node']): r for r in base['core_rows']}
    ranked = []
    for change in initial:
        key = (change['from_node'], change['to_node'])
        row = rows[key]
        pressure = row['length_km'] * row['flow_pcu_h'] / row['speed_kmh']
        ranked.append((pressure, key, change))
    ranked.sort(key=lambda item: (-item[0], item[1][0], item[1][1]))
    return ranked


def search_actions(task, changes, base):
    """Yield the exact next tool arguments; send back one measured run record."""
    version = BACKTRACK_VERSION if task.backtrack_on_singleton_failure else VERSION
    initial = list(changes)
    alternatives = baseline_singletons(initial, base) if task.backtrack_on_singleton_failure else []
    backtracks = []
    factors = sorted(task.demand_factors, reverse=True)
    bases = {1.0: base}
    used, sequence = 1, 0

    def action(name, changes, factor, kind, reason, observed=None, ranking=None):
        nonlocal used, sequence
        used += 1
        sequence += 1
        return dict(sequence=sequence, name=name, changes=changes, factor=factor, kind=kind,
                    reason=reason, observed_run=observed, ranking=ranking or [],
                    remaining_after=task.max_runs - used)

    for f in factors:
        if f != 1:
            bases[f] = yield action(f"robust_base_{f:g}", [], f, "baseline", "建立同需求无治理基准，评价范围保持固定")
    order, seen, skipped = factors, set(), []
    reason, observed, ranking = "初始假设：候选路段采用停车2级，先检查高需求", None, []
    stop = "达到预定反馈候选数上限"
    for iteration in range(task.max_feedback + 1):
        if used + len(factors) + int(task.repeat_baseline) > task.max_runs:
            stop = "剩余预算不足以覆盖新候选全部需求及基准复跑，停止新增候选"
            break
        signature = policy_key(changes)
        if signature in seen:
            stop = "反馈措施已测，停止重复计算"
            break
        seen.add(signature)
        records, failed = [], None
        for f in order:
            record = yield action(f"robust_p{iteration + 1}_{f:g}", changes, f, "policy", reason, observed, ranking)
            records.append(record)
            if not record["comparison"]["eligible"]:
                failed = record
                skipped.append(dict(policy=record["name"], failed_factor=f,
                                    unmeasured_factors=[x for x in factors if x not in [r["demand_factor"] for r in records]]))
                break
        if failed:
            if task.backtrack_on_singleton_failure and iteration == task.max_feedback:
                stop = "达到预定反馈候选数上限"
                break
            if (task.backtrack_on_singleton_failure and
                    used + len(factors) + int(task.repeat_baseline) > task.max_runs):
                stop = "剩余预算不足以覆盖新候选全部需求及基准复跑，停止新增候选"
                break
            revised, ranking = revise(failed, bases[failed["demand_factor"]])
            observed = failed["name"]
            if task.backtrack_on_singleton_failure and (
                not revised or policy_key(revised) in seen
            ):
                next_link = next(((pressure, key, change) for pressure, key, change in alternatives
                                  if policy_key([change]) not in seen), None)
                if next_link:
                    pressure, key, change = next_link
                    changes, ranking = [change], []
                    backtracks.append(dict(after_failed_run=observed,
                                           from_policy=signature,
                                           next_link=[key[0], key[1]],
                                           untreated_base_ql_over_v_pcu_h=pressure))
                    reason = ("单路段约束失败或缩减措施已测；按无治理1.0基准qL/v的固定次序"
                              "尝试未测原核心方向，先复核失败需求；该排序不代表因果收益")
                else:
                    stop = "单路段措施仍违反约束，原核心方向已试尽"
                    break
            elif not revised:
                stop = "单路段措施仍违反约束，停止该方向搜索"
                break
            else:
                changes = revised
                reason = "已测约束失败；按联合治理的路段耗时减少量保留前半，并在失败需求复核（非因果归因）"
            order = [failed["demand_factor"]] + [f for f in factors if f != failed["demand_factor"]]
        else:
            trigger = next((r for r in records if (r["metrics"]["core"]["weighted_speed_kmh"] or 0) < task.target_speed_kmh
                            or any((x["adjusted_load_ratio"] or 0) > 1 for x in r["core_rows"])), None)
            if not trigger or not all(c["parking"] == 2 for c in changes):
                stop = "已测措施满足停止条件或已达到停车1级，停止增强"
                break
            changes = [dict(c, parking=1) for c in changes]
            observed, ranking = trigger["name"], []
            reason = "全部需求通过，但已测工况仍有速度或管理修正负荷瓶颈；试验停车1级并重新核对全部需求"
            order = factors
    if task.repeat_baseline:
        yield action("baseline_repeat", [], 1.0, "repeat", "执行预留的同输入复跑，核对四类原生输出数组")
    result = dict(version=version, stop_reason=stop, early_rejections=skipped,
                  candidates_started=len(seen), actual_runs=used,
                  unused_budget=task.max_runs - used)
    if task.backtrack_on_singleton_failure:
        result['backtracks'] = backtracks
    return result


def conclusion(state):
    """Common frozen selection, also replayed by the evidence verifier."""
    from .agent import recommend
    analysis = decision_analysis(dict(state, status="completed"))
    choices = {}
    for f in state["task"]["demand_factors"]:
        candidates = [r for r in state["runs"] if r["demand_factor"] == f and r["changes"]]
        winner = recommend(candidates, state['task'].get('recommendation_version', 'legacy-v1'))
        base = next(r for r in state["runs"] if r["demand_factor"] == f and not r["changes"])
        choices[str(float(f))] = winner["name"] if winner else base["name"]
    return dict(recommendation=analysis["recommendation"] or "baseline",
                robust_across_tested_demand=True if analysis["recommendation"] else None,
                recommendation_rule=analysis["rule"], recommendation_by_car_demand=choices)


def analyze(agent, baseline, base, diagnosis):
    from .agent import compare
    agent.scope = scopes(baseline, [r["index"] for r in diagnosis["selected"]], agent.task.neighbor_hops)
    save_json(agent.job / "scopes.json", agent.scope)
    base["metrics"] = scoped_metrics(baseline, agent.scope)
    base["core_rows"] = [baseline[i] for i in agent.scope["core"]]
    baselines = {1.0: baseline}
    controller = search_actions(agent.task, initial_changes(diagnosis), base)
    agent.state["search_trace"] = []
    record = None
    while True:
        try:
            action = controller.send(record)
        except StopIteration as end:
            agent.state["robust_search"] = end.value
            break
        trace = dict(action, decided_at=now())
        agent.state["search_trace"].append(trace)
        agent.event("search_action", trace)
        agent.persist()  # Evidence of the decision exists before its model call.
        rows, record = agent.simulation(action["name"], action["changes"], action["factor"])
        response = record["execution"]["assigned_car_od_sum"] / base["execution"]["assigned_car_od_sum"]
        if not math.isclose(response, action["factor"], rel_tol=1e-5, abs_tol=1e-6):
            raise ValueError("需求没有按预定比例进入小汽车分配")
        if action["kind"] == "policy":
            record["comparison"] = compare(baselines[action["factor"]], rows, agent.scope, agent.task)
            agent.event("search_assessment", dict(name=record["name"], eligible=record["comparison"]["eligible"],
                                                 violations=record["comparison"]["violations"]))
        elif action["kind"] == "repeat":
            identical = len(rows) == len(baseline) and all(a[k] == b[k] for a, b in zip(baseline, rows) for k in OUTPUTS)
            agent.state["repeatability"] = dict(exact_match=identical, arrays=list(OUTPUTS), row_count=len(rows),
                                                note="确定性重复，不作为独立统计样本")
            if not identical:
                raise ValueError("同输入基准复跑不一致，禁止正式推荐")
        else:
            baselines[action["factor"]] = rows
        agent.persist()
    agent.state.update(conclusion(agent.state))
    winner = next(r for r in agent.state["runs"] if r["name"] == agent.state["recommendation"])
    for f in agent.task.demand_factors:
        if f == 1:
            continue
        matched = next(r for r in agent.state["runs"] if r["demand_factor"] == f and not r["changes"])
        if winner["changes"]:
            policy = next(r for r in agent.state["runs"] if r["demand_factor"] == f and policy_key(r["changes"]) == policy_key(winner["changes"]))
            assessment = policy["comparison"]
        else:
            policy = matched
            assessment = compare(baselines[f], baselines[f], agent.scope, agent.task)
            assessment.update(eligible=True, violations=[], note="统一建议保留基准；条件建议不替代统一措施的验证")
        agent.state["sensitivity"].append(dict(car_demand_factor=f, matched_baseline=matched["name"],
                                              fixed_primary_policy=policy["name"], comparison=assessment))
    agent.state["status"] = "completed"
    agent.event("search_stop", agent.state["robust_search"])
    agent.event("recommendation", dict(scenario=agent.state["recommendation"],
                                       robust_across_tested_demand=agent.state["robust_across_tested_demand"]))
