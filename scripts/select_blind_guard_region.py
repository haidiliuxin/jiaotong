"""Freeze an untested parking region from baseline-only evidence.

This selector never reads any policy result. The previously sealed challenge
provides only the native no-treatment baseline, while registered scope files
exclude roads already assessed. Selection is deterministic and verifiable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, PROJECT, input_fingerprints, results
from traffic_agent.runner import DEFAULT_SOURCE
from traffic_agent.study import freeze_regions
from traffic_agent.task import Task


ROOT = Path(__file__).resolve().parents[1]
CHALLENGE = ROOT / "智能体实验/challenges/20260922_034228_3141eb"
REGISTRY = ROOT / "config/challenge_history.json"
SELECTION = CHALLENGE / "selection.json"
BASELINE = CHALLENGE / "scenarios/baseline/基础路网"
ARCHIVE = CHALLENGE / "archive_manifest.json"
STRATUM = "high"


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def freeze():
    baseline = BASELINE.resolve()
    if not baseline.is_relative_to(CHALLENGE.resolve()) or not baseline.is_dir():
        raise ValueError("封存无治理基准缺失")
    archive = read(ARCHIVE)
    relative = baseline.relative_to(CHALLENGE).as_posix()
    baseline_outputs = {}
    for name in OUTPUTS.values():
        path = baseline / "result" / name
        key = f"{relative}/result/{name}"
        actual = sha(path)
        if actual != archive["files"][key]["sha256"]:
            raise ValueError(f"封存基准原始数组改变：{key}")
        baseline_outputs[key] = actual
    source = read(CHALLENGE / "source_inputs.json")
    if source != input_fingerprints(DEFAULT_SOURCE):
        raise ValueError("当前赛事案例源输入与封存基准不同")
    registry, previous = read(REGISTRY), read(SELECTION)
    if len(registry["entries"]) != 19:
        raise ValueError("事前历史排除清单不是预期19项")
    excluded = set()
    for entry in registry["entries"]:
        if entry["geometry_sha256"] != source["data/geom.txt"]:
            raise ValueError("历史范围与当前路网不兼容")
        excluded.update(entry["scope"]["core"] + entry["scope"]["neighbor"])
    prior = [region for region in previous["regions"] if region["status"] == "selected"]
    if len(prior) != 3:
        raise ValueError("上一轮挑战区域清单不完整")
    for region in prior:
        excluded.update(region["scope"]["core"] + region["scope"]["neighbor"])
    task = Task.from_dict(dict(request="只用封存无治理基准与历史排除范围确定新区域",
                               scenario="parking", search_mode="robust",
                               recommendation_version="tolerance-v2", max_links=3,
                               max_runs=11, max_feedback=2,
                               demand_factors=[0.9, 1.0, 1.1], repeat_baseline=True,
                               max_new_network_oversaturated_km=0.1))
    selection = freeze_regions(results(baseline), task, excluded)
    region = next(region for region in selection["regions"] if region["id"] == STRATUM)
    if region["status"] != "selected" or not region["initial"]:
        raise ValueError("高密度层没有可预注册的新区域")
    footprint = set(region["scope"]["core"] + region["scope"]["neighbor"])
    if footprint & excluded:
        raise ValueError("新区域与登记历史范围重叠")
    links = [[change["from_node"], change["to_node"]] for change in region["initial"]]
    card = dict(request="历史登记治理范围未覆盖的高密度新区域：三需求主动稳健停车与全网毛新增过饱和安全门槛",
                scenario="parking", search_mode="robust", recommendation_version="tolerance-v2",
                links=links, max_links=3, max_runs=11, max_feedback=2,
                demand_factors=[0.9, 1.0, 1.1], repeat_baseline=True,
                max_new_network_oversaturated_km=0.1)
    report = dict(kind="baseline_only_blind_region_selection_v1", stratum=STRATUM,
                  baseline_job=CHALLENGE.name, baseline_archive_sha256=sha(ARCHIVE),
                  baseline_raw_arrays=baseline_outputs,
                  source_inputs_sha256=sha(CHALLENGE / "source_inputs.json"),
                  registry_sha256=sha(REGISTRY), previous_selection_sha256=sha(SELECTION),
                  selection_code_sha256={str(path.relative_to(ROOT)).replace("\\", "/"): sha(path)
                                         for path in (ROOT / "traffic_agent/study.py", ROOT / "traffic_agent/data.py")},
                  excluded_registered_scopes=len(registry["entries"]),
                  excluded_previous_challenge_regions=[region["id"] for region in prior],
                  excluded_directed_indices=len(excluded),
                  legal_seeds=selection["counts"]["legal_seeds"],
                  all_region_selections=[dict(id=item["id"], status=item["status"],
                                              seed=item.get("seed")) for item in selection["regions"]],
                  full_selection_sha256=hashlib.sha256(encoded(selection)).hexdigest(),
                  selected=dict(seed=region["seed"], density=region["density"],
                                roads=region["roads"], initial=region["initial"],
                                candidate_count=region["candidate_count"],
                                core=region["scope"]["core"], neighbor=region["scope"]["neighbor"],
                                network_size=len(region["scope"]["network"]),
                                scope_sha256=hashlib.sha256(encoded(region["scope"])).hexdigest()),
                  card_sha256=hashlib.sha256(encoded(card)).hexdigest(),
                  selection_rule="从高密度层按freeze_regions固定种子顺序取首个不与19项登记历史范围及上一挑战3区重叠的区域；不读取任何治理结果",
                  targeted_policy_outcomes_used=False,
                  limitation="同一赛事调整案例的基准驱动新区域；选点未读取该区域的定向治理结果，但不是跨城市或随机抽样")
    return report, card


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--task", type=Path, required=True)
    args = parser.parse_args()
    report, card = freeze()
    selection, task = args.selection.resolve(), args.task.resolve()
    if args.write:
        if selection.exists() or task.exists():
            raise ValueError("事前选区或任务卡已存在，不覆盖")
        selection.parent.mkdir(parents=True, exist_ok=True)
        task.parent.mkdir(parents=True, exist_ok=True)
        selection.write_bytes(encoded(report))
        task.write_bytes(encoded(card))
    elif read(selection) != report or read(task) != card or sha(task) != report["card_sha256"]:
        raise ValueError("保存的事前选区或任务卡不能从基准复算")
    print(json.dumps(dict(status="frozen" if args.write else "verified", stratum=STRATUM,
                          seed=report["selected"]["seed"], roads=report["selected"]["roads"],
                          links=card["links"], legal_seeds=report["legal_seeds"],
                          card_sha256=report["card_sha256"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
