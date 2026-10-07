"""Equal policy-evaluation budgets: outcome feedback vs fixed strength search."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.agent import Agent, compare, feedback, recommend
from traffic_agent.data import geometry, input_fingerprints, save_json, scopes
from traffic_agent.runner import EXPERIMENTS, now, runtime_lock, setup_runtime, validate_source
from traffic_agent.task import Task

ANCHORS = [(3244, 3245), (3245, 3244), (3240, 3239)]


def design_next(first, baseline, scope, task, initial):
    proposed, reason = feedback(first, baseline, scope, task)
    return {"fixed": [dict(c, parking=1) for c in initial], "adaptive": proposed,
            "reason": reason, "observed_run": first["name"],
            "note": "此决策在读取任何第二轮候选结果之前保存；固定策略始终试验全部路段1级"}


def write_report(job, state):
    lines = ["# 相同候选评估预算的策略比较", "", f"状态：{state['status']}", "",
             "两种策略共享一个同需求基准和相同首轮（3路段停车2级），每种策略最多评估2项治理候选。固定策略第二轮始终为3路段1级；反馈策略仅根据首轮实测指标增强、缩小或停止。",
             "相同候选只实际执行一次并共享结果，两种策略各计一次逻辑评估。基准计费相同；停止时实际候选数少于2，明确报告实际消耗，不称运行次数完全相等。",
             "仅检验这一冻结区域和已测小汽车需求，不作普遍算法优越性或统计显著性结论。", "",
             "| 小汽车需求 | 策略 | 候选次数/上限 | 最终建议 | 相对匹配基准全网时间变化 | 核心时间变化 |", "| ---: | --- | ---: | --- | ---: | ---: |"]
    by_name = {r["name"]: r for r in state["runs"]}
    for condition in state["conditions"]:
        for strategy, selection in condition["strategies"].items():
            r = by_name[selection["recommendation"]]
            delta = r.get("comparison", {}).get("delta", {})
            network = delta.get("network", {}).get("running_time_change_pct", 0)
            core = delta.get("core", {}).get("running_time_change_pct", 0)
            lines.append(f"| {condition['factor']} | {strategy} | {len(selection['candidates'])}/2 | {r['name']} | {network:.6f}% | {core:.6f}% |")
    lines += ["", "## 预先约定与边界", "", "- 固定核心3条、邻区39条、全网有效机动车路段；两个策略使用完全相同指标与约束。",
              "- 邻区时间增幅≤0.5%、全网≤0.1%、邻区新增过饱和有向里程≤0.1km、核心时间须改善。",
              "- 通过约束后最小化全网路段行驶时间；不合格候选保留记录，未选到合格方案则保留各自匹配基准。",
              "- 使用既有多路径增量5次；已知ManageN联动未通过独立验证，结论限当前固定软件配置。",
              "- 总执行次数是多个策略候选并集的次数；不得把共享计算伪装成两套独立随机重复。", "",
              "协议见protocol.json；第二轮决定见decisions.jsonl；原始仿真见scenarios和logs；逐策略计数、指标及推荐见state.json。", ""]
    if state.get("error"):
        lines += ["失败原因：" + state["error"], ""]
    (job / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    task = Task.from_dict({"request": "相同两候选预算下比较固定强度搜索与实测反馈；冻结原七东线3路段，需求0.9/1/1.1",
                           "links": [list(p) for p in ANCHORS], "max_runs": 12, "max_feedback": 1, "repeat_baseline": False})
    agent = Agent(task)
    agent.job = EXPERIMENTS / "benchmarks" / agent.job_id
    agent.job.mkdir(parents=True, exist_ok=False)
    agent.state.update(kind="feedback_benchmark", conditions=[])
    protocol = {"created": now(), "task": task.as_dict(), "anchors": ANCHORS, "policy_budget_per_strategy_per_factor": 2,
                "factors": [.9, 1.0, 1.1], "fixed": "all anchors parking 2, then all anchors parking 1",
                "adaptive": "same parking 2 first, then feedback() using only that first observed outcome",
                "shared_candidates_count_once_for_each_strategy": True,
                "stop_rule": "no eligible policy -> matched baseline; never relax constraints after observing results"}
    save_json(agent.job / "protocol.json", protocol)
    agent.persist()
    print(f"BENCHMARK {agent.job}", flush=True)
    try:
        with runtime_lock(EXPERIMENTS):
            validate_source(agent.source)
            agent.source_inputs = input_fingerprints(agent.source)
            save_json(agent.job / "source_inputs.json", agent.source_inputs)
            agent.runtime = setup_runtime(EXPERIMENTS)
            rows = geometry(agent.source)
            pairs = {(r["from_node"], r["to_node"]): r for r in rows}
            selected = [pairs[p] for p in ANCHORS]
            if any(r["parking"] != 3 or not r["included"] for r in selected):
                raise ValueError("冻结研究区已变化，不能继续预设协议")
            agent.scope = scopes(rows, [r["index"] for r in selected], task.neighbor_hops)
            save_json(agent.job / "scopes.json", agent.scope)
            initial = [dict(from_node=r["from_node"], to_node=r["to_node"], old_parking=3, parking=2) for r in selected]
            agent.state["status"] = "running"
            for factor in protocol["factors"]:
                tag = format(factor, ".3f").replace(".", "p")
                baseline, base = agent.simulation(f"f{tag}_baseline", [], factor)
                first_rows, first = agent.simulation(f"f{tag}_first", initial, factor)
                first["comparison"] = compare(baseline, first_rows, agent.scope, task)
                decision = design_next(first, baseline, agent.scope, task, initial)
                decision.update(factor=factor, made_at=now())
                with (agent.job / "decisions.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(decision, ensure_ascii=False) + "\n")
                agent.event("benchmark_next_candidates", decision)
                agent.persist()
                second_rows, fixed_second = agent.simulation(f"f{tag}_fixed_second", decision["fixed"], factor)
                fixed_second["comparison"] = compare(baseline, second_rows, agent.scope, task)
                adaptive = [first]
                if decision["adaptive"] == decision["fixed"]:
                    adaptive.append(fixed_second)
                elif decision["adaptive"] is not None:
                    later_rows, later = agent.simulation(f"f{tag}_adaptive_second", decision["adaptive"], factor)
                    later["comparison"] = compare(baseline, later_rows, agent.scope, task)
                    adaptive.append(later)
                strategies = {}
                for name, candidates in [("fixed", [first, fixed_second]), ("adaptive", adaptive)]:
                    winner = recommend(candidates)
                    strategies[name] = {"candidates": [c["name"] for c in candidates], "budget": 2,
                                        "recommendation": winner["name"] if winner else base["name"]}
                agent.state["conditions"].append({"factor": factor, "matched_baseline": base["name"],
                                                 "decision": decision, "strategies": strategies})
                agent.persist()
            if input_fingerprints(agent.source) != agent.source_inputs:
                raise ValueError("源输入变化")
            agent.state["status"] = "completed"
    except BaseException as exc:
        agent.state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        agent.state["finished"] = now()
        agent.state.pop("active_scenario", None)
        agent.persist()
        write_report(agent.job, agent.state)
    print(json.dumps({"benchmark": str(agent.job), "unique_runs": agent.state["run_count"], "conditions": agent.state["conditions"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
