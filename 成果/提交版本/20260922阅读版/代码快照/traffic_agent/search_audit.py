"""Replay the search protocol after raw input/output verification."""
import json
from datetime import datetime

from .data import diagnose, results, scopes
from .robust_search import conclusion, initial_changes, protocol, search_actions
from .task import Task


def replay(task, diagnosis, runs, traces, expected_summary):
    """Only independently verified run observations should enter this function."""
    if not runs or runs[0]["name"] != "baseline" or runs[0]["changes"] or runs[0]["demand_factor"] != 1:
        raise ValueError("主动搜索缺少首项基准")
    if len(traces) != len(runs) - 1:
        raise ValueError("决策链与实际调用次数不符")
    controller = search_actions(task, initial_changes(diagnosis), runs[0])
    sent = None
    for i, trace in enumerate(traces):
        try:
            expected = controller.send(sent)
        except StopIteration:
            raise ValueError("停止条件之后仍有额外调用") from None
        if {k: v for k, v in trace.items() if k != "decided_at"} != expected:
            raise ValueError("下一步决策不能由此前观测和冻结规则重放")
        run = runs[i + 1]
        if (run["name"], run["changes"], run["demand_factor"]) != (expected["name"], expected["changes"], expected["factor"]):
            raise ValueError("实际工具参数与事前决策不同")
        decided = datetime.fromisoformat(trace["decided_at"])
        if not datetime.fromisoformat(runs[i]["execution"]["finished"]) <= decided <= datetime.fromisoformat(run["execution"]["started"]):
            raise ValueError("决策时间不在前次完成与本次开始之间")
        sent = run
    try:
        controller.send(sent)
    except StopIteration as end:
        if end.value != expected_summary:
            raise ValueError("停止原因或搜索预算摘要不符")
    else:
        raise ValueError("决策链在必须执行的配对或复跑之前中断")


def verify_search(job, state, archive=None):
    task = Task.from_dict(state["task"])
    for name in ("report.md", "report.html", "metrics.csv"):
        path = job / name
        if not path.is_file() or not path.stat().st_size:
            raise ValueError(f"缺少完整的报告导出文件：{name}")
    load = lambda path: json.loads((job / path).read_text(encoding="utf-8"))
    if load("search_protocol.json") != protocol(task):
        raise ValueError("搜索协议或任务参数被改变")
    if load("plan.json")["task"] != state["task"]:
        raise ValueError("任务卡与运行参数不同")
    runs = state["runs"]
    mtime = archive["files"]["search_protocol.json"]["mtime_ns"] / 1e9 if archive else (job / "search_protocol.json").stat().st_mtime
    if mtime > datetime.fromisoformat(runs[0]["execution"]["started"]).timestamp():
        raise ValueError("搜索协议不是首项计算前保存")
    baseline = results(job / runs[0]["scenario"])
    diagnosis = diagnose(baseline, task)
    scope = scopes(baseline, [r["index"] for r in diagnosis["selected"]], task.neighbor_hops)
    if diagnosis != load("diagnosis.json") or scope != load("scopes.json"):
        raise ValueError("诊断或评价范围不由真实新基准产生")
    for run in runs:
        rows = results(job / run["scenario"])
        if [rows[i] for i in scope["core"]] != run["core_rows"]:
            raise ValueError("用于反馈排序的路段观测与原生输出不同")
    traces = state["search_trace"]
    replay(task, diagnosis, runs, traces, state["robust_search"])
    events = [json.loads(s) for s in (job / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    decisions = [e for e in events if e["event"] == "search_action"]
    if [e["data"] for e in decisions] != traces:
        raise ValueError("独立事件流与决策链不同")
    for event, trace, run in zip(decisions, traces, runs[1:]):
        if not datetime.fromisoformat(trace["decided_at"]) <= datetime.fromisoformat(event["time"]) <= datetime.fromisoformat(run["execution"]["started"]):
            raise ValueError("决策事件不在模型运行前")
    for key, expected in conclusion(state).items():
        if state.get(key) != expected:
            raise ValueError(f"主动搜索最终选择规则不符：{key}")
    if task.repeat_baseline and state.get("repeatability", {}).get("exact_match") is not True:
        raise ValueError("缺少通过的基准复跑")
    # The same uniform policy must be checked in each sensitivity item. Conditional
    # winners cannot stand in for a failed or absent uniform policy.
    primary = next(r for r in runs if r["name"] == state["recommendation"])
    items = state["sensitivity"]
    if len(items) != len(task.demand_factors) - 1 or {x["car_demand_factor"] for x in items} != set(task.demand_factors) - {1.0}:
        raise ValueError("统一措施需求复核缺失或重复")
    from .agent import compare
    for item in items:
        f = item["car_demand_factor"]
        base = next(r for r in runs if r["demand_factor"] == f and not r["changes"])
        policy = next(r for r in runs if r["demand_factor"] == f and r["changes"] == primary["changes"])
        if primary["changes"]:
            expected = policy["comparison"]
        else:
            rows = results(job / base["scenario"])
            expected = compare(rows, rows, scope, task)
            expected.update(eligible=True, violations=[], note="统一建议保留基准；条件建议不替代统一措施的验证")
        if item != dict(car_demand_factor=f, matched_baseline=base["name"], fixed_primary_policy=policy["name"], comparison=expected):
            raise ValueError("统一措施的需求复核被改写")
    return dict(protocol_frozen_before_first_run=True, raw_observations_verified=True,
                decisions_replayed=len(traces), chronological_decisions=True,
                budget_and_stopping_verified=True, complete_demand_recommendation_verified=True,
                report_exports_present=True)
