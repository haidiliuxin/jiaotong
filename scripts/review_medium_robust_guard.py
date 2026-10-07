"""Compare the sealed medium-region guard run with its preregistered study history.

This is read-only with respect to both native experiment archives. The old
study is a historical lead, not a blind or equal-budget algorithm control.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS
from traffic_agent.decision_analysis import policy_key
from traffic_agent.map_data import scenario_path
from verify_experiment import verify as verify_job


ROOT = Path(__file__).resolve().parents[1]
SELECTION_SHA = "a8d634a59db90c33190161b970ec20458c1835e5e212da935c203a50606ab829"
CARD_SHA = "14c7f72fed4f93e876763a621924b95bda90f2352ec23951d0f5132932362ce1"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def signature(run):
    return policy_key(run["changes"]), run["demand_factor"]


def verify_historical_snapshot(study: Path):
    """Run the strict study audit against its sealed algorithm snapshot."""
    code = """import json, sys
from pathlib import Path
study, project = (Path(x).resolve() for x in sys.argv[1:])
sys.path.insert(0, str(study / 'code_snapshot'))
import traffic_agent
traffic_agent.__path__.append(str(project / 'traffic_agent'))
from traffic_agent.study_audit import verify
report = verify(study, archived=True, write=False)
print(json.dumps({key: report[key] for key in ('passed', 'physical_scenes', 'native_module_calls')}))
"""
    result = subprocess.run([sys.executable, "-c", code, str(study), str(ROOT)],
                            capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def review(study: Path, job: Path, task_card: Path):
    study, job, task_card = study.resolve(), job.resolve(), task_card.resolve()
    old_audit = verify_historical_snapshot(study)
    new_audit = verify_job(job, archived=True, write=False)
    if (not old_audit["passed"] or not new_audit["passed"]
            or old_audit["physical_scenes"] != 22 or old_audit["native_module_calls"] != 154):
        raise ValueError("Historical study or new task archive verification failed")
    old, new = read(study / "state.json"), read(job / "state.json")
    selection = read(study / "selection.json")
    card = read(task_card)
    if (sha(study / "selection.json") != SELECTION_SHA or sha(task_card) != CARD_SHA
            or any(new["task"].get(key) != value for key, value in card.items())):
        raise ValueError("Frozen region selection or task card changed")
    if (new["status"] != "completed" or new["task"]["search_mode"] != "robust"
            or new["task"]["recommendation_version"] != "tolerance-v2"
            or new["task"]["max_new_network_oversaturated_km"] != 0.1
            or new["task"]["demand_factors"] != [0.9, 1.0, 1.1]
            or new_audit["completed_scenarios"] != len(new["runs"])
            or new_audit["module_executions"] != 7 * len(new["runs"])
            or not all(new_audit["repeat_arrays_exact"].values())):
        raise ValueError("New result is incomplete or outside the frozen protocol")
    region = next(x for x in selection["regions"] if x["id"] == "medium")
    selected = [(x["from_node"], x["to_node"]) for x in region["initial"]]
    diagnosis = read(job / "diagnosis.json")
    if selected != [(x["from_node"], x["to_node"]) for x in diagnosis["selected"]]:
        raise ValueError("New task did not keep the frozen three directed links")
    if read(job / "scopes.json") != region["scope"]:
        raise ValueError("Fixed core, neighbor or network assessment scope changed")
    if read(study / "source_inputs.json") != read(job / "source_inputs.json"):
        raise ValueError("Original model inputs differ")
    historical_views = old["regions"]["medium"]["strategies"]["feedback"]["runs"]
    old_physical = {run["name"]: run for run in old["runs"]}
    old_by_key = {signature(view): view for view in historical_views if view["name"] != "baseline_repeat"}
    if len(old_by_key) != len(historical_views) - 1:
        raise ValueError("Historical views are ambiguous")
    matched, unmatched = {}, []
    for run in new["runs"]:
        view = (next((v for v in historical_views if v["name"] == "baseline_repeat"), None)
                if run["name"] == "baseline_repeat" else old_by_key.get(signature(run)))
        if view is None:
            unmatched.append(run["name"])
            continue
        old_path = scenario_path(study, old_physical[view["physical_run"]])
        new_path = scenario_path(job, run)
        arrays = {key: sha(old_path / "result" / name) == sha(new_path / "result" / name)
                  for key, name in OUTPUTS.items()}
        changed_inputs = {name: sha(old_path / name) == sha(new_path / name)
                          for name in ("data/manageL.txt", "result/ODCar.tsd")}
        if not all(arrays.values()) or not all(changed_inputs.values()):
            raise ValueError(f"New and historical source/output bytes differ: {run['name']}")
        matched[run["name"]] = dict(historical_view=view["name"], physical_run=view["physical_run"],
                                     raw_arrays_exact=arrays, changed_inputs_exact=changed_inputs)
    rows = []
    for run in new["runs"]:
        if not run["changes"]:
            continue
        comparison = run["comparison"]
        rows.append(dict(name=run["name"], demand=run["demand_factor"],
                         directions=[f"{x['from_node']}→{x['to_node']}" for x in run["changes"]],
                         parking_level=run["changes"][0]["parking"], eligible=comparison["eligible"],
                         violations=comparison["violations"],
                         core_time_pct=comparison["delta"]["core"]["running_time_change_pct"],
                         neighbor_time_pct=comparison["delta"]["neighbor"]["running_time_change_pct"],
                         network_time_pct=comparison["delta"]["network"]["running_time_change_pct"],
                         gross_new_network_km=comparison["new_network_oversaturated_km"]))
    winner = next(run for run in new["runs"] if run["name"] == new["recommendation"])
    return dict(kind="medium_region_robust_guard_matched_review", new_model_calls=0,
                old_study=old["id"], new_job=new["id"], selected_directions=selected,
                source_inputs_exact=True, frozen_scope_exact=True, matched=matched,
                unmatched_new_scenes=unmatched, recommendation=new["recommendation"],
                recommended_changes=winner["changes"], search_stop=new["robust_search"],
                robust_across_tested_demand=new.get("robust_across_tested_demand"),
                rows=rows, new_scenarios=len(new["runs"]), new_native_calls=new_audit["module_executions"],
                old_study_audit=dict(passed=True, physical_scenes=22, native_calls=154),
                new_archive_sha256=sha(job / "archive_manifest.json"),
                task_card_sha256=sha(task_card), historical_selection_sha256=SELECTION_SHA,
                limitation="历史正例引导的同案例第二研究区复验；不是盲选、等预算新旧算法消融、异机、跨城市或官方评分证明")


def render(report):
    recommended = ", ".join(f"{c['from_node']}→{c['to_node']} 停车{c['parking']}级"
                             for c in report["recommended_changes"]) or "维持基准"
    lines = ["# 中密度研究区三需求安全门槛真实复验", "",
             f"冻结选区来自旧前瞻研究 `{report['old_study']}`；新任务 `{report['new_job']}` 实际完成"
             f"{report['new_scenarios']}场景、{report['new_native_calls']}次 TranStar 顶层调用，严格档案核验通过。",
             f"固定方向：{', '.join(f'{a}→{b}' for a,b in report['selected_directions'])}。"
             f"原始源输入及核心、邻区、全网评价范围均与旧研究一致。",
             f"统一建议：`{report['recommendation']}`，实际方向与强度："
             f"{recommended}；跨所测需求通过："
             f"{'是' if report['robust_across_tested_demand'] else '否'}。", "",
             "| 新任务候选 | 需求 | 治理方向 / 级别 | 核心耗时 | 邻区耗时 | 全网耗时 | 全网毛新增过饱和 | 判定 |",
             "| --- | ---: | --- | ---: | ---: | ---: | ---: | --- |"]
    for row in report["rows"]:
        lines.append(f"| {row['name']} | {row['demand']:g} | "
                     f"{', '.join(row['directions'])} / 停车{row['parking_level']}级 | "
                     f"{row['core_time_pct']:+.3f}% | {row['neighbor_time_pct']:+.3f}% | "
                     f"{row['network_time_pct']:+.4f}% | {row['gross_new_network_km']:.3f} km | "
                     f"{'通过' if row['eligible'] else '拒绝'} |")
    rejected = [row for row in report["rows"] if not row["eligible"]]
    if rejected:
        lines += ["", "拒绝原因：" + "；".join(f"`{row['name']}`：{', '.join(row['violations'])}"
                                               for row in rejected) + "。"]
    lines += ["", "耗时采用路段 Σ(qL/v)，每项均与同需求无治理基准配对；"
              "毛新增过饱和按新出现的有向路段长度相加，不用别处改善抵消，事前硬门槛为0.1 km。",
              f"搜索停止原因：{report['search_stop']['stop_reason']}；"
              f"实际启动候选 {report['search_stop']['candidates_started']} 组，"
              f"剩余场景预算 {report['search_stop']['unused_budget']}。",
              f"与旧研究按实际方向和需求匹配的 {len(report['matched'])} 个场景："
              "四类原始路段数组及两类改变输入文件逐字节一致。"
              f"新任务没有旧研究同条件场景的名称：{', '.join(report['unmatched_new_scenes']) or '无'}。",
              "历史研究用其封存的算法源码快照加载当前严格核验器重放；"
              "直接以现版算法核验旧档案会因版本哈希不同被正确拒绝。",
              "旧研究没有全网新硬门槛，不能用旧任务的历史判定代替新任务实测。"
              "未测需求不视为通过；局部收益与毛新增过饱和均按本需求无治理基准计算。",
              report["limitation"] + "。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("job", type=Path)
    parser.add_argument("task_card", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Keep previous review output; choose a new directory")
    report = review(args.study, args.job, args.task_card)
    args.output.mkdir(parents=True)
    (args.output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "README.md").write_text(render(report), encoding="utf-8")
    print(json.dumps(dict(new_job=report["new_job"], matched=len(report["matched"]),
                          recommendation=report["recommendation"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
