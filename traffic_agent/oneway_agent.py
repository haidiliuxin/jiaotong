"""Full matched demand grid for parking, one motor direction and their combination."""
import math

from .data import OUTPUTS, numeric, save_json, scoped_metrics, scopes
from .oneway import OneWayNetwork, response
from .action_execution import PARKING_LEVEL, factorial_policies, factors, prefix


def diagnose_oneway(agent, baseline, base, record_evidence=True, portfolio=False):
    from .agent import knowledge
    evidence = knowledge("oneway")
    if record_evidence:
        save_json(agent.job / "knowledge_used.json", evidence)
        agent.event("oneway_policy_knowledge", evidence)
    graph = OneWayNetwork.load(agent.source)
    car = numeric(agent.job / base["scenario"] / "result/VolLinkCar.tsd")
    candidates = [r for r in baseline if r["included"] and r["road_class"] in (2, 3)
                  and r["parking"] in (3, 4) and .1 <= r["length_km"] <= 1.5
                  and r["flow_pcu_h"] >= 500 and 1 < r["vc"] <= 4 and r["speed_kmh"] < 40
                  and car[r["index"]] > 1 and (agent.task.road_name is None or r["name"] == agent.task.road_name)]
    candidates.sort(key=lambda r: (-r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"], r["index"]))
    if agent.task.links:
        candidates = [r for r in candidates if [r["from_node"], r["to_node"]] == agent.task.links[0]]
    by_pair = {(r["from_node"], r["to_node"]): r for r in baseline}
    attempts, selected, targets = [], [], []
    for row in candidates[:200]:
        pair = (row["from_node"], row["to_node"])
        audit = graph.validate(pair)
        reverse = by_pair.get(pair[::-1])
        parking_ok = reverse is not None and reverse["parking"] in (3, 4)
        eligible = audit["eligible"] and parking_ok
        attempts.append({"closed_direction": list(pair), "baseline_veh_flow": row["flow_pcu_h"],
                         "baseline_car_flow": car[row["index"]], "eligible": eligible,
                         "reverse_parking_eligible": parking_ok, "graph": audit})
        if eligible:
            if not portfolio:
                selected = [row, reverse]
                break
            from .oneway_planning import TARGET_LIMIT
            targets.append(dict(id=f'oneway_{pair[0]}_{pair[1]}', selected=[row, reverse],
                                car_flow=car[row['index']], graph=audit))
            if len(targets) >= TARGET_LIMIT:
                break
    result = {"selected": selected, "attempts": attempts, "candidate_count": len(candidates),
            "rule": "新Vehicle1基准：城市2/3类、停车3/4、0.1-1.5km、q>=500、1<VC<=4、v<40、car>1；按qL/v前200项，首个公交/三类图边界及反向停车通过的方向；显式道路与方向不放宽",
            "cause": "单向与停车效果为待仿真检验的假设；固定双向范围，不能把封闭后的核心流量下降直接当全网收益"}
    if portfolio:
        result.update(targets=targets, rule=result['rule'].replace('首个公交', '至多4个公交').replace('的方向；', '的方向供模型选择；'))
    return result


def assess_oneway(agent, baseline, rows, base, record):
    from .agent import compare
    assessment = compare(baseline, rows, agent.scope, agent.task)
    if assessment["after"]["network"]["running_time_pcu_h"] >= assessment["before"]["network"]["running_time_pcu_h"] - 1e-9:
        assessment["violations"].append("全网路段行驶时间未改善，不能仅靠关闭方向的核心流量减少推荐")
    edits = [c for c in record["changes"] if c.get("kind") == "oneway"]
    if edits:
        measured = response(agent.job / base["scenario"], agent.job / record["scenario"], edits[0]["closed_direction"])
        record["oneway_response"] = measured
        if not measured["passed"] or measured["flows"]["car"]["before"] <= 1:
            assessment["violations"].append("实际单向禁行或基准小汽车非零量门槛未通过")
    assessment["eligible"] = not assessment["violations"]
    record["comparison"] = assessment
    return assessment


def analyze_oneway(agent, baseline, base, diagnosis):
    from .agent import recommend
    selected = diagnosis["selected"]
    pair = [selected[0]["from_node"], selected[0]["to_node"]]
    agent.scope = scopes(baseline, [r["index"] for r in selected], agent.task.neighbor_hops)
    save_json(agent.job / "scopes.json", agent.scope)
    base.update(metrics=scoped_metrics(baseline, agent.scope), core_rows=selected)
    variants = list(factorial_policies('oneway',selected).items())
    design = {"kind": "oneway", "closed_direction": pair, "parking_level": PARKING_LEVEL, "policies": dict(variants),
              "demand_factors": agent.task.demand_factors, "planned_full_grid": True, "full_demand_grid": False,
              "required_runs": 4 * len(agent.task.demand_factors) + int(agent.task.repeat_baseline),
              "assignment": "Vehicle1 multipath incremental 5; theta 3.3", "selection": "new baseline before any policy run"}
    if agent.state.get("hard_access_profile"):
        design["hard_access"] = {"version": agent.state["hard_access_profile"]["version"],
                                 "scope": "explicit additional policy closures only", "original_transform_inputs_saved": True}
    if diagnosis.get('selected_target'):
        design.update(target_planning_protocol=agent.state['target_planning_protocol'],
                      target_id=diagnosis['selected_target'], selection='model choice after fresh Vehicle1 baseline; full matched grid follows')
    agent.state["factorial_design"] = design
    save_json(agent.job / "factorial_protocol.json", design)
    agent.persist()
    main_name, choices, grid, main_label = "baseline", {}, [], None
    all_response = True
    for factor in factors(agent.task.as_dict()):
        scene_prefix = prefix(factor)
        b_rows, b = (baseline, base) if factor == 1 else agent.simulation(scene_prefix + "baseline", [], factor)
        od_factor = b["execution"]["assigned_car_od_sum"] / base["execution"]["assigned_car_od_sum"]
        if not math.isclose(od_factor, factor, rel_tol=1e-5, abs_tol=1e-6):
            raise ValueError("需求系数未进入实际小汽车分配OD")
        candidates = []
        for label, edits in variants:
            rows, record = agent.simulation(scene_prefix + label, edits, factor)
            record["policy_label"] = label
            check = assess_oneway(agent, b_rows, rows, b, record)
            all_response = all_response and record.get("oneway_response", {}).get("passed", True)
            decision = {"observed_run": record["name"], "will_execute": False,
                        "reason": "候选通过约束，进入同需求方案比较" if check["eligible"] else "撤回该候选推荐，保留其他已测单项、组合或匹配基准",
                        "violations": check["violations"], "execution_reason": "完整预定组合仍需测齐；这条记录表示候选筛除，不追加未经预定的措施"}
            agent.state["decisions"].append(decision)
            agent.event("factorial_assessment", decision)
            candidates.append(record)
            agent.persist()
        winner = recommend(candidates, agent.task.selection_mode)
        chosen = winner["name"] if winner else b["name"]
        choices[str(float(factor))] = chosen
        grid.append({"factor": factor, "baseline": b["name"], "policies": [r["name"] for r in candidates], "recommendation": chosen})
        if factor == 1:
            main_name = chosen
            main_label = winner["policy_label"] if winner else None
            agent.state["recommendation"] = chosen
        else:
            fixed = next((r for r in candidates if r["policy_label"] == main_label), None)
            comparison = fixed["comparison"] if fixed else {"eligible": True, "violations": [], "note": "主推荐为基准，无政策稳健性收益声明"}
            agent.state["sensitivity"].append({"car_demand_factor": factor, "matched_baseline": b["name"],
                "assigned_od_factor_verified": od_factor, "comparison": comparison,
                "conditional_recommendation": chosen, "fixed_primary_policy": fixed["name"] if fixed else b["name"]})
        agent.state["demand_grid"] = grid
        agent.state["recommendation_by_car_demand"] = choices
        agent.persist()
    if agent.task.repeat_baseline:
        rows, record = agent.simulation("baseline_repeat", [])
        exact = len(rows) == len(baseline) and all(a[k] == b[k] for a, b in zip(rows, baseline) for k in OUTPUTS)
        agent.state["repeatability"] = {"exact_match": exact, "arrays": list(OUTPUTS), "row_count": len(rows)}
        if not exact:
            raise ValueError("相同输入Vehicle1基准复跑不一致")
    agent.state.update(status="completed", recommendation=main_name, oneway_adapter_verified=all_response,
        recommendation_rule="每个需求系数先验证单向实际响应及固定核心、邻区约束，并要求全网qL/v严格改善，再选全网耗时最少方案；无合格项保留该需求基准。全组合为预定实验，不声称自适应搜索或跨程序收益。",
        robust_across_tested_demand=all(x["comparison"]["eligible"] for x in agent.state["sensitivity"]) if main_label and agent.state["sensitivity"] else None)
    design["full_demand_grid"] = len(grid) == len(agent.task.demand_factors)
    agent.persist()
