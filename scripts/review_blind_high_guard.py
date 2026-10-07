"""Audit a preregistered, baseline-only high-density parking trial.

The previous challenge contributes only a no-treatment baseline for blind
selection and exact-input comparison. No historical policy result is borrowed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS
from traffic_agent.decision_analysis import policy_key
from traffic_agent.map_data import scenario_path
from select_blind_guard_region import BASELINE, CHALLENGE, encoded, freeze, read, sha
from verify_experiment import verify as verify_job


def review(job: Path, frozen: Path, task_card: Path):
    job, frozen, task_card = (path.resolve() for path in (job, frozen, task_card))
    expected, card = freeze()
    saved = read(frozen)
    if saved != expected or read(task_card) != card or sha(task_card) != saved["card_sha256"]:
        raise ValueError("事前选区或任务卡不能从旧基准重算")
    audit = verify_job(job, archived=True, write=False)
    state = read(job / "state.json")
    if (not audit["passed"] or not audit["portable_archive_verified"]
            or state["status"] != "completed"
            or audit["completed_scenarios"] != len(state["runs"])
            or audit["module_executions"] != 7 * len(state["runs"])
            or not all(audit["repeat_arrays_exact"].values())
            or not all(audit["active_search_audit"].values())):
        raise ValueError("新任务封存、原生调用、基准复跑或反馈重放未通过")
    if any(state["task"].get(key) != value for key, value in card.items()):
        raise ValueError("原生任务未使用事前冻结任务卡")
    chosen = [(change["from_node"], change["to_node"]) for change in saved["selected"]["initial"]]
    observed = [(item["from_node"], item["to_node"]) for item in read(job / "diagnosis.json")["selected"]]
    if chosen != observed:
        raise ValueError("原生任务治理方向偏离事前冻结区域")
    scope = read(job / "scopes.json")
    scope_sha = hashlib.sha256(encoded(scope)).hexdigest()
    if (scope_sha != saved["selected"]["scope_sha256"]
            or len(scope["network"]) != saved["selected"]["network_size"]
            or scope["core"] != saved["selected"]["core"]
            or scope["neighbor"] != saved["selected"]["neighbor"]):
        raise ValueError("核心、邻区或全网评价范围偏离事前冻结区域")
    if read(job / "source_inputs.json") != read(CHALLENGE / "source_inputs.json"):
        raise ValueError("模型源输入与选点所用基准不一致")
    baseline = next(run for run in state["runs"] if run["name"] == "baseline")
    base_path = scenario_path(job, baseline)
    array_match = {key: sha(BASELINE / "result" / name) == sha(base_path / "result" / name)
                   for key, name in OUTPUTS.items()}
    rows = []
    policies = {}
    for run in state["runs"]:
        if not run["changes"]:
            continue
        c = run["comparison"]
        key = policy_key(run["changes"])
        policies.setdefault(key, []).append(run)
        rows.append(dict(name=run["name"], demand=run["demand_factor"],
                         directions=[f"{x['from_node']}→{x['to_node']}" for x in run["changes"]],
                         parking_level=run["changes"][0]["parking"],
                         core_time_before_pcu_h=c["before"]["core"]["running_time_pcu_h"],
                         core_time_after_pcu_h=c["after"]["core"]["running_time_pcu_h"],
                         neighbor_time_before_pcu_h=c["before"]["neighbor"]["running_time_pcu_h"],
                         neighbor_time_after_pcu_h=c["after"]["neighbor"]["running_time_pcu_h"],
                         core_time_pct=c["delta"]["core"]["running_time_change_pct"],
                         neighbor_time_pct=c["delta"]["neighbor"]["running_time_change_pct"],
                         network_time_pct=c["delta"]["network"]["running_time_change_pct"],
                         gross_new_network_km=c["new_network_oversaturated_km"],
                         eligible=c["eligible"], violations=c["violations"]))
    winner = next(run for run in state["runs"] if run["name"] == state["recommendation"])
    full_factors = set(card["demand_factors"])
    complete_policies = [dict(changes=runs[0]["changes"],
                              tested=sorted({run["demand_factor"] for run in runs}),
                              all_eligible=all(run["comparison"]["eligible"] for run in runs))
                         for runs in policies.values()
                         if {run["demand_factor"] for run in runs} == full_factors]
    if winner["changes"]:
        matching = next((p for p in complete_policies
                         if policy_key(p["changes"]) == policy_key(winner["changes"])), None)
        if not matching or not matching["all_eligible"] or state["robust_across_tested_demand"] is not True:
            raise ValueError("统一建议未在全部冻结需求下通过")
    elif state["robust_across_tested_demand"] is not None:
        raise ValueError("基准回退被误记为治理方案稳健通过")
    return dict(kind="baseline_only_blind_high_guard_review_v1", new_model_calls=0,
                job=state["id"], preregistration_card_sha256=sha(task_card),
                preregistration_selection_sha256=sha(frozen),
                old_baseline_job=CHALLENGE.name, original_inputs_exact=True,
                baseline_raw_arrays_exact=array_match,
                frozen_scope_exact=True, selected_roads=saved["selected"]["roads"],
                selected_directions=chosen, scope_sizes={k: len(v) for k, v in scope.items()},
                scenarios=len(state["runs"]), native_calls=audit["module_executions"],
                repeat_arrays_exact=audit["repeat_arrays_exact"],
                decision_replay=audit["active_search_audit"],
                recommendation=state["recommendation"], recommended_changes=winner["changes"],
                robust_across_tested_demand=state["robust_across_tested_demand"],
                full_demand_policies=complete_policies, rows=rows,
                search_stop=state["robust_search"],
                archive_manifest_sha256=sha(job / "archive_manifest.json"),
                limitation="事前排除历史治理范围的同案例高密度新区域；没有等预算新旧策略对照、异机或跨城市验证，也不是官方评分")


def render(report):
    candidate = (", ".join(f"{c['from_node']}→{c['to_node']} 停车{c['parking']}级"
                           for c in report["recommended_changes"]) or "维持无治理基准")
    lines = ["# 基准驱动新区域：三需求全网安全门槛真实试验", "",
             f"新任务 `{report['job']}` 完成{report['scenarios']}场景、{report['native_calls']}次TranStar顶层调用，"
             "独立档案核验、搜索决策重放与同输入基准复跑通过。",
             f"事前选定道路：{', '.join(report['selected_roads'])}；"
             f"方向：{', '.join(f'{a}→{b}' for a,b in report['selected_directions'])}。",
             f"统一建议：{candidate}；三需求治理措施通过："
             f"{'是' if report['robust_across_tested_demand'] else '否'}。", "",
             "| 实测候选 | 小汽车需求 | 治理方向 / 强度 | 核心耗时 | 邻区耗时 | 全网耗时 | 全网毛新增过饱和 | 判定 |",
             "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |"]
    for row in report["rows"]:
        lines.append(f"| {row['name']} | {row['demand']:g} | {', '.join(row['directions'])} / 停车{row['parking_level']}级 | "
                     f"{row['core_time_pct']:+.3f}% | {row['neighbor_time_pct']:+.3f}% | "
                     f"{row['network_time_pct']:+.4f}% | {row['gross_new_network_km']:.3f} km | "
                     f"{'通过' if row['eligible'] else '拒绝'} |")
    lines.append("")
    for row in report["rows"]:
        if row["violations"]:
            lines.append(f"- `{row['name']}`拒绝原因：{'; '.join(row['violations'])}。")
    if report["rows"]:
        first = report["rows"][0]
        lines += ["", f"首项失败工况的绝对量：核心路段耗时"
                  f"{first['core_time_before_pcu_h']:.3f}→{first['core_time_after_pcu_h']:.3f} pcu·h，"
                  f"邻区{first['neighbor_time_before_pcu_h']:.3f}→"
                  f"{first['neighbor_time_after_pcu_h']:.3f} pcu·h。"]
    lines += ["", f"搜索停止原因：{report['search_stop']['stop_reason']}。"
              f"预留预算剩余{report['search_stop']['unused_budget']}场景。",
              "冻结源输入和核心、邻区、全网范围均与用于选点的无治理基准一致；"
              f"旧基准与新基准四类原始数组逐字节一致："
              f"{'是' if all(report['baseline_raw_arrays_exact'].values()) else '否'}。",
              "这里的旧任务只提供无治理基准和排除范围，其治理结果既未进入选点也不计入本次调用。"
              "未测需求不视为通过；每项候选均按同需求无治理基准评价。",
              "耗时为路段Σ(qL/v)，未计全部节点延误；毛新增过饱和按新出现的有向路段长度相加，不与别处改善抵消。",
              report["limitation"] + "。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("task_card", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("保留此前复评结果，请使用新的输出目录")
    report = review(args.job, args.selection, args.task_card)
    args.output.mkdir(parents=True)
    (args.output / "comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "README.md").write_text(render(report), encoding="utf-8")
    print(json.dumps(dict(job=report["job"], scenarios=report["scenarios"],
                          calls=report["native_calls"], recommendation=report["recommendation"]),
                     ensure_ascii=False))
