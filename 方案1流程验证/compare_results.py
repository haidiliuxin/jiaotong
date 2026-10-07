"""Compare a completed intervention with the fixed baseline and record a feedback decision."""
import argparse
import csv
import json
from pathlib import Path
from analyze_results import links, metrics, numeric, read_text

ROOT = Path(__file__).resolve().parent


def capacity(scenario, pair):
    p = ROOT / scenario / "基础路网"
    order = [tuple(map(int, line.split())) for line in read_text(p / "cache/FT.dat").splitlines() if line.strip()]
    return numeric(p / "cache/CapLinkVehMan.dat")[order.index(pair)]


def compare(name):
    proposal = json.loads((ROOT / name / "proposal.json").read_text(encoding="utf-8"))
    pair = (proposal["selected_link"]["from_node"], proposal["selected_link"]["to_node"])
    before, after = links(ROOT / "baseline/基础路网"), links(ROOT / name / "基础路网")
    if [(r["from_node"], r["to_node"], r["included"]) for r in before] != [(r["from_node"], r["to_node"], r["included"]) for r in after]:
        raise ValueError("Fixed network ordering/evaluation mask differs")
    reached = set(pair)
    for _ in range(2):
        expanded = set(reached)
        for r in before:
            if r["from_node"] in reached or r["to_node"] in reached:
                expanded.update((r["from_node"], r["to_node"]))
        reached = expanded
    neighbor_ids = [r["index"] for r in before if r["included"]
                    and r["from_node"] in reached and r["to_node"] in reached
                    and (r["from_node"], r["to_node"]) != pair]
    target_index = proposal["selected_link"]["index"]
    selected_before, selected_after = before[target_index], after[target_index]
    base_capacity_before = numeric(ROOT / "baseline/基础路网/result/CapLinkVeh.tsd")[target_index]
    base_capacity_after = numeric(ROOT / name / "基础路网/result/CapLinkVeh.tsd")[target_index]
    changed = []
    for a, b in zip(before, after):
        if a["included"] and any(a[k] != b[k] for k in ["speed_kmh", "flow_pcu_h", "vc"]):
            changed.append({"index": a["index"], "from_node": a["from_node"], "to_node": a["to_node"], "name": a["name"],
                            "speed_before": a["speed_kmh"], "speed_after": b["speed_kmh"],
                            "flow_before": a["flow_pcu_h"], "flow_after": b["flow_pcu_h"],
                            "vc_before": a["vc"], "vc_after": b["vc"]})
    input_changes = []
    for folder in ["data", "params"]:
        for p in (ROOT / "baseline/基础路网" / folder).iterdir():
            q = ROOT / name / "基础路网" / folder / p.name
            if p.is_file() and q.exists() and p.read_bytes() != q.read_bytes():
                input_changes.append(f"{folder}/{p.name}")
    unchanged_od = all(p.read_bytes() == (ROOT / name / "基础路网/result" / p.name).read_bytes()
                       for p in (ROOT / "baseline/基础路网/result").glob("OD*.tsd"))
    report = {"scenario": name, "target_before": selected_before, "target_after": selected_after,
              "effective_capacity_before": capacity("baseline", pair), "effective_capacity_after": capacity(name, pair),
              "vc_denominator_before": base_capacity_before, "vc_denominator_after": base_capacity_after,
              "vc_definition_note": "VCLinkVeh matches VolLinkVeh/CapLinkVeh, not management-adjusted capacity; report these separately.",
              "flow_over_adjusted_capacity_before": selected_before["flow_pcu_h"] / capacity("baseline", pair),
              "flow_over_adjusted_capacity_after": selected_after["flow_pcu_h"] / capacity(name, pair),
              "whole_network_before": metrics(before), "whole_network_after": metrics(after),
              "neighborhood_definition": "Baseline undirected two-hop node set; induced valid directed links excluding treated direction",
              "neighborhood_indices": neighbor_ids,
              "neighborhood_before": metrics([before[i] for i in neighbor_ids]),
              "neighborhood_after": metrics([after[i] for i in neighbor_ids]),
              "changed_valid_link_count": len(changed), "data_params_files_differing_after_execution": input_changes,
              "all_od_files_unchanged": unchanged_od}
    if selected_after["vc"] > 1 and proposal["new_parking"] == 2:
        report["feedback_decision"] = "Target remains oversaturated; test parking category 1 on the same directed link under identical OD, then compare local and network tradeoffs. No automatic deployment recommendation."
    else:
        report["feedback_decision"] = "Review local/network tradeoffs; finish the bounded pipeline pilot rather than expand the treatment area."
    output = ROOT / "analysis" / name
    output.mkdir(parents=True, exist_ok=True)
    (output / "comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if changed:
        with (output / "changed_links.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(changed[0])); w.writeheader(); w.writerows(changed)
    print(json.dumps({k: v for k, v in report.items() if k != "neighborhood_indices"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("scenario")
    compare(parser.parse_args().scenario)
