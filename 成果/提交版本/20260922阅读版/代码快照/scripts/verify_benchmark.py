"""Recompute budget comparison from files and prove second choices preceded results."""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.benchmark_feedback import design_next, ANCHORS
from traffic_agent.agent import compare, recommend
from traffic_agent.data import OUTPUTS, changed_input_files, input_fingerprints, numeric, results, save_json, scoped_metrics
from traffic_agent.runner import MODULES
from traffic_agent.task import Task


def verify(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    protocol = json.loads((job / "protocol.json").read_text(encoding="utf-8"))
    if state["status"] != "completed":
        raise ValueError("策略比较尚未完整完成")
    task = Task.from_dict(state["task"])
    source = json.loads((job / "source_inputs.json").read_text(encoding="utf-8"))
    scope = json.loads((job / "scopes.json").read_text(encoding="utf-8"))
    runs = {r["name"]: r for r in state["runs"]}
    if len(runs) != state["run_count"] or len(runs) > 12 or protocol["policy_budget_per_strategy_per_factor"] != 2:
        raise ValueError("预算或实际调用次数不符")
    if protocol["task"] != state["task"] or [c["factor"] for c in state["conditions"]] != protocol["factors"]:
        raise ValueError("任务或需求条件偏离预设协议")
    manifests, all_rows = {}, {}
    for name, run in runs.items():
        path = job / run["scenario"]
        manifest = json.loads((job / "logs" / name / "run.json").read_text(encoding="utf-8"))
        manifests[name] = manifest
        if manifest["status"] != "completed" or tuple(m["module"] for m in manifest["modules"]) != MODULES or any(m["returncode"] != 0 for m in manifest["modules"]):
            raise ValueError("模型执行不完整")
        for relative, stamp in manifest["fresh_outputs"].items():
            stat = (path / relative).stat()
            if stat.st_size != stamp["bytes"] or stat.st_mtime_ns != stamp["mtime_ns"]:
                raise ValueError("保存结果变化")
        before = json.loads((job / "logs" / name / "inputs_before.json").read_text(encoding="utf-8"))
        if input_fingerprints(path) != before:
            raise ValueError("保存输入变化")
        diff = {k for k in before.keys() | source.keys() if before.get(k) != source.get(k)}
        if diff != changed_input_files(run["changes"], run["demand_factor"]):
            raise ValueError("存在未允许的输入差异")
        all_rows[name] = results(path)
        if scoped_metrics(all_rows[name], scope) != run["metrics"]:
            raise ValueError("报告指标不符")
    by_factor = {c["factor"]: runs[c["matched_baseline"]] for c in state["conditions"]}
    main_base = by_factor[1.0]
    main_sum = sum(numeric(job / main_base["scenario"] / "result/ODCar.tsd"))
    initial = [dict(from_node=a, to_node=b, old_parking=3, parking=2) for a,b in ANCHORS]
    decisions = [json.loads(line) for line in (job / "decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    evidence = []
    for condition, logged in zip(state["conditions"], decisions):
        factor = condition["factor"]
        base = by_factor[factor]
        for run in (r for r in runs.values() if r["demand_factor"] == factor):
            ratio = sum(numeric(job / run["scenario"] / "result/ODCar.tsd")) / main_sum
            if not math.isclose(ratio, factor, rel_tol=1e-8) or not math.isclose(run["execution"]["assigned_car_od_sum"] / main_base["execution"]["assigned_car_od_sum"], factor, rel_tol=1e-5):
                raise ValueError("需求系数不符")
            if run["changes"] and compare(all_rows[base["name"]], all_rows[run["name"]], scope, task) != run["comparison"]:
                raise ValueError("约束判定与文件不符")
        first_name = condition["strategies"]["fixed"]["candidates"][0]
        first = runs[first_name]
        if first["changes"] != initial or condition["strategies"]["adaptive"]["candidates"][0] != first_name:
            raise ValueError("两种策略没有共享相同首轮")
        recomputed = design_next(first, all_rows[base["name"]], scope, task, initial)
        if any(logged[k] != v for k,v in recomputed.items()) or logged != condition["decision"]:
            raise ValueError("第二轮候选不来自约定首轮反馈")
        for strategy, selection in condition["strategies"].items():
            names = selection["candidates"]
            if not 1 <= len(names) <= 2 or len(set(names)) != len(names):
                raise ValueError("策略候选超预算或重复计数")
            if len(names) == 2:
                if runs[names[1]]["changes"] != logged[strategy]:
                    raise ValueError("实际第二轮政策偏离事先决定")
                if datetime.fromisoformat(logged["made_at"]) >= datetime.fromisoformat(manifests[names[1]]["started"]):
                    raise ValueError("第二轮决定晚于其实际仿真开始")
            elif logged[strategy] is not None:
                raise ValueError("应执行的第二轮候选被漏掉")
            chosen = recommend([runs[n] for n in names])
            if selection["recommendation"] != (chosen["name"] if chosen else base["name"]):
                raise ValueError("策略推荐没有遵循同一评价规则")
        evidence.append({"factor": factor, "strategies": condition["strategies"], "decision_before_second_simulation": True})
    if len(decisions) != len(state["conditions"]):
        raise ValueError("决定记录数量不符")
    report = {"passed": True, "unique_simulations": len(runs), "module_executions": 7*len(runs),
              "budget_per_strategy_per_factor": 2, "conditions": evidence,
              "limits": "共享候选各计一次逻辑评估；确定性案例比较，不证明普遍算法优越性"}
    save_json(job / "verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    verify(parser.parse_args().job.resolve())
