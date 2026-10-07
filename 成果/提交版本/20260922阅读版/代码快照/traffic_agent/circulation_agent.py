"""Measured S0/S1/S2/S3 experiments with explicit rejection of unsafe policies."""
import math

from .circulation import TurnGraph, read_turn_volumes, restrictions
from .data import OUTPUTS, diagnose, read_text, save_json, scoped_metrics, scopes


def diagnose_circulation(agent, baseline, base):
    graph = TurnGraph.load(agent.source)
    volumes = read_turn_volumes(agent.job / base["scenario"] / "result/TVolNodecar.tsd")
    parking = diagnose(baseline, agent.task)
    # Explicit roads/links remain hard constraints; automatic selection searches only
    # the published top 30 parking bottlenecks, then freezes the selected new region.
    anchors = parking["selected"] if agent.task.links else parking["candidates"]
    attempts, selected, turn = [], [], None
    for road in anchors:
        pair = (road["from_node"], road["to_node"])
        possible = [(frm, pair[0], pair[1]) for frm, to in graph.edges if to == pair[0] and frm != pair[1]]
        possible.sort(key=lambda t: (-volumes.get(t, 0), t))
        for candidate in possible:
            audit = graph.validate([candidate])
            flow = volumes.get(candidate)
            eligible = audit["eligible"] and flow is not None and flow > 1
            attempts.append({"anchor": list(pair), "turn": list(candidate), "baseline_car_turn_flow": flow,
                             "eligible": eligible, "graph_check": audit,
                             "measurement_gate": "基准转向量必须>1，避免用原本为零的条目证明禁转生效"})
            if eligible:
                selected, turn = [road], list(candidate)
                break
        if selected:
            break
    return {"selected": selected, "turn": turn, "attempts": attempts,
            "rule": "停车瓶颈qL/v降序前30条内，选首个满足公交保护、全禁转状态绕行<=2km且<=3倍、基准小汽车转向量>1的转向；同一瓶颈按转向量降序",
            "scope_note": "这是独立验证区域，不沿用七东线停车实验的范围或收益；道路/显式路段约束不放宽",
            "cause": "候选成因与禁转效果均为待仿真检验的假设，候选可完全退回基准"}


def turn_response(agent, before_record, after_record):
    before = agent.job / before_record["scenario"]
    after = agent.job / after_record["scenario"]
    expected = restrictions(after / "data/manageN.txt")
    tokens = read_text(after / "cache/ManageNM.dat").split()
    count = int(tokens[0])
    flat = list(map(int, tokens[1:]))
    if len(flat) != count * 3:
        raise ValueError("ManageNM禁转缓存数量不符")
    cached = {tuple(flat[i:i + 3]) for i in range(0, len(flat), 3)}
    if len(cached) != count or cached != expected:
        raise ValueError("ManageN禁转未完整进入新生成的ManageNM缓存")
    old = read_turn_volumes(before / "result/TVolNodecar.tsd")
    new = read_turn_volumes(after / "result/TVolNodecar.tsd")
    checks = []
    for edit in after_record["changes"]:
        if edit.get("kind") != "turn":
            continue
        turn = tuple(edit["turn"])
        a, b = old.get(turn), new.get(turn)
        checks.append({"turn": list(turn), "before_car_turn_flow": a, "after_car_turn_flow": b,
                       "passed": a is not None and a > 1 and b is not None and b <= 0.01})
    graph = TurnGraph.load(before).validate([c["turn"] for c in checks]) if checks else None
    return {"passed": all(c["passed"] for c in checks) and (graph is None or graph["eligible"]),
            "cache_rules_exact": True, "cached_rule_count": count, "movements": checks, "graph": graph,
            "limit": "验证软件矩阵所报禁转量从非零降至0.01以下；未追踪每个OD路径或证明实际交叉口执法效果"}


def analyze_circulation(agent, baseline, base, diagnosis):
    from .agent import compare, recommend
    selected = diagnosis["selected"][0]
    agent.scope = scopes(baseline, [selected["index"]], agent.task.neighbor_hops)
    save_json(agent.job / "scopes.json", agent.scope)
    base.update(metrics=scoped_metrics(baseline, agent.scope), core_rows=[baseline[selected["index"]]])
    parking = {"from_node": selected["from_node"], "to_node": selected["to_node"],
               "old_parking": selected["parking"], "parking": 2}
    turn = {"kind": "turn", "turn": diagnosis["turn"]}
    candidates = []

    def assess(rows, record, matched_rows, matched_record):
        record["comparison"] = compare(matched_rows, rows, agent.scope, agent.task)
        if any(c.get("kind") == "turn" for c in record["changes"]):
            record["turn_response"] = turn_response(agent, matched_record, record)
            if not record["turn_response"]["passed"]:
                record["comparison"]["eligible"] = False
                record["comparison"]["violations"].append("软件转向量或公交/绕行检查未通过，不能推荐禁转")
        agent.persist()

    for name, changes in [("S1_parking", [parking]), ("S2_turn", [turn]), ("S3_combined", [parking, turn])]:
        rows, record = agent.simulation(name, changes)
        assess(rows, record, baseline, base)
        candidates.append(record)
        agent.event("factorial_assessment", {"scenario": name, "eligible": record["comparison"]["eligible"],
                                             "violations": record["comparison"]["violations"],
                                             "turn_response": record.get("turn_response")})

    winner = recommend(candidates, agent.task.selection_mode)
    agent.state["recommendation"] = winner["name"] if winner else "baseline"
    agent.state["recommendation_rule"] = "同一新基准和固定研究区比较S1停车2级、S2单禁转、S3组合；先通过图约束、转向生效及核心/邻区/全网指标，再最小化全网路段行驶时间；否则保留基准"
    agent.state["turn_adapter_verified"] = all(r.get("turn_response", {}).get("passed", True) for r in candidates)
    agent.state["factorial_design"] = {"parking_level": 2, "turn": diagnosis["turn"],
                                       "shared_baseline": "baseline", "scope": "新验证区域", "full_demand_grid": False}
    # Feedback is evidence driven: remove the turn if it adds harm to parking,
    # and retain measured rejected alternatives in the report rather than retrying.
    for record in candidates[1:]:
        agent.state["decisions"].append({"observed_run": record["name"],
            "reason": ("该转向候选通过约束，纳入与停车单项的比较" if record["comparison"]["eligible"] else "该转向候选未通过约束，撤回其推荐，保留已测停车单项或基准"),
            "violations": record["comparison"]["violations"], "will_execute": False})

    if agent.task.repeat_baseline:
        rows, record = agent.simulation("baseline_repeat", [])
        exact = len(rows) == len(baseline) and all(a[k] == b[k] for a, b in zip(rows, baseline) for k in OUTPUTS)
        agent.state["repeatability"] = {"exact_match": exact, "arrays": list(OUTPUTS), "row_count": len(rows)}
        if not exact:
            raise ValueError("相同输入基准复跑不一致")
    chosen = winner["changes"] if winner else []
    choices = {"1.0": agent.state["recommendation"]}
    for factor in agent.task.demand_factors:
        if factor == 1:
            continue
        tag = format(factor, ".3f").replace(".", "p")
        b_rows, b = agent.simulation(f"demand_{tag}_baseline", [], factor)
        response = b["execution"]["assigned_car_od_sum"] / base["execution"]["assigned_car_od_sum"]
        if not math.isclose(response, factor, rel_tol=1e-5, abs_tol=1e-6):
            raise ValueError("小汽车需求系数未进入内部OD")
        if chosen:
            rows, record = agent.simulation(f"demand_{tag}_policy", chosen, factor)
            assess(rows, record, b_rows, b)
            comparison = record["comparison"]
            choices[str(float(factor))] = record["name"] if comparison["eligible"] else b["name"]
        else:
            comparison = compare(b_rows, b_rows, agent.scope, agent.task)
            comparison.update(eligible=True, violations=[], note="保留匹配基准")
            choices[str(float(factor))] = b["name"]
        agent.state["sensitivity"].append({"car_demand_factor": factor, "matched_baseline": b["name"],
                                           "assigned_od_factor_verified": response, "comparison": comparison})
        agent.persist()
    agent.state["recommendation_by_car_demand"] = choices
    agent.state["robust_across_tested_demand"] = all(x["comparison"]["eligible"] for x in agent.state["sensitivity"]) if agent.state["sensitivity"] else None
    agent.state["status"] = "completed"
    agent.persist()
