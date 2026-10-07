"""Observation-only native runs. No interventions or inferred causal effects."""
import math
from collections import defaultdict

from .data import OUTPUTS, save_json, scoped_metrics, scopes


def diagnose_network(rows, task):
    population = [r for r in rows if r["included"] and (not task.road_name or r["name"] == task.road_name)]
    ranked = sorted(population, key=lambda r: (-r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"], r["index"]))
    congested = [r for r in ranked if r["vc"] > 1]
    selected = (congested or ranked)[:task.max_links]
    return dict(selected=selected, candidates=ranked[:30], inspected_links=len(population),
                overloaded_links=len(congested),
                rule="在指定范围读取所有参与分配路段，按qL/v排序；V/C>1标为过饱和，不能据此证明拥堵成因",
                cause="只诊断现状；未执行任何治理措施")


def observation(rows, indices, factor, run):
    roads = defaultdict(lambda: dict(links=0, time_pcu_h=0.0, overloaded_km=0.0))
    for index in indices:
        row = rows[index]
        road = roads[row["name"] or "未命名道路"]
        road["links"] += 1
        road["time_pcu_h"] += row["flow_pcu_h"] * row["length_km"] / row["speed_kmh"]
        road["overloaded_km"] += row["length_km"] if row["vc"] > 1 else 0
    ranking = sorted((dict(name=k, **v) for k, v in roads.items()), key=lambda x: (-x["time_pcu_h"], x["name"]))
    return dict(run=run, car_demand_factor=factor, inspected_links=len(indices), roads_count=len(roads),
                overloaded_links=sum(rows[i]["vc"] > 1 for i in indices), top_roads=ranking[:10])


def analyze(agent, baseline, base, diagnosis):
    task = agent.task
    indices = [r["index"] for r in baseline if r["included"] and (not task.road_name or r["name"] == task.road_name)]
    agent.scope = scopes(baseline, [r["index"] for r in diagnosis["selected"]], task.neighbor_hops)
    save_json(agent.job / "scopes.json", agent.scope)
    base["metrics"] = scoped_metrics(baseline, agent.scope)
    base["core_rows"] = [baseline[i] for i in agent.scope["core"]]
    observations = [observation(baseline, indices, 1.0, "baseline")]
    agent.state["diagnostic_observations"] = observations
    for factor in task.demand_factors:
        if factor == 1:
            continue
        name = "demand_" + format(factor, ".3f").replace(".", "p") + "_baseline"
        rows, record = agent.simulation(name, [], factor)
        response = record["execution"]["assigned_car_od_sum"] / base["execution"]["assigned_car_od_sum"]
        if not math.isclose(response, factor, rel_tol=1e-5, abs_tol=1e-6):
            raise ValueError("诊断需求未按设定比例进入内部小汽车分配")
        observations.append(observation(rows, indices, factor, name))
        agent.persist()
    if task.repeat_baseline:
        repeated, _ = agent.simulation("baseline_repeat", [])
        identical = len(repeated) == len(baseline) and all(all(a[k] == b[k] for a, b in zip(baseline, repeated)) for k in OUTPUTS)
        agent.state["repeatability"] = dict(exact_match=identical, arrays=list(OUTPUTS), row_count=len(repeated), note="确定性重复，不作为独立统计样本")
        if not identical:
            raise ValueError("相同输入诊断基准复跑不一致，停止形成正式结论")
    agent.state.update(status="completed", recommendation="baseline", robust_across_tested_demand=None,
                       recommendation_rule="仅报告本次新仿真的诊断，不推荐未经治理实验验证的措施。")
    agent.event("diagnostic_complete", dict(observations=observations, intervention_executed=False))
