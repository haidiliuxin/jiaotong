"""Check software turn tables against real GEOM endpoint IDs and link car flows."""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import geometry, numeric, save_json
from traffic_agent.circulation import read_turn_volumes, restrictions


def audit(scenario):
    rows = geometry(scenario)
    pairs = {(r["from_node"], r["to_node"]): r for r in rows}
    flows = numeric(scenario / "result/VolLinkCar.tsd")
    if len(flows) != len(rows):
        raise ValueError("小汽车流量与GEOM维数不符")
    q = {(r["from_node"], r["to_node"]): value for r, value in zip(rows, flows)}
    turns = read_turn_volumes(scenario / "result/TVolNodecar.tsd")
    incoming, outgoing, count_in, count_out = defaultdict(float), defaultdict(float), defaultdict(int), defaultdict(int)
    invalid = []
    for (frm, via, to), value in turns.items():
        if (frm, via) not in pairs or (via, to) not in pairs:
            invalid.append({"turn": [frm, via, to], "flow": value})
            continue
        incoming[frm, via] += value
        outgoing[via, to] += value
        count_in[frm, via] += 1
        count_out[via, to] += 1
    discrepancy = []
    for pair, flow in q.items():
        # Report inconsistencies as observations, not automatic conservation failures:
        # OD origins/destinations and software table coverage require interpretation.
        checks = {}
        for label, values, counts in (("as_incoming", incoming, count_in), ("as_outgoing", outgoing, count_out)):
            tolerance = max(.05, counts[pair] * .011)
            if abs(values[pair] - flow) > tolerance:
                checks[label] = {"table_sum": values[pair], "difference": values[pair] - flow}
        if checks:
            discrepancy.append({"link": list(pair), "link_car_flow": flow, "checks": checks})
    bans = restrictions(scenario / "data/manageN.txt")
    positive_bans = [{"turn": list(t), "table_flow": turns[t]} for t in sorted(bans) if turns.get(t, 0) > .01]
    managed_nodes = {t[1] for t in bans}
    coverage = {}
    for label, managed in (("with_input_turn_restrictions", True), ("without_input_turn_restrictions", False)):
        group = {t: value for t, value in turns.items() if (t[1] in managed_nodes) == managed
                 and t[:2] in pairs and t[1:] in pairs}
        coverage[label] = {"nodes_in_table": len({t[1] for t in group}),
                           "valid_turn_entries": len(group),
                           "positive_turn_entries": sum(value > .01 for value in group.values()),
                           "nodes_with_positive_turn_entries": len({t[1] for t, value in group.items() if value > .01})}
    return {"turn_entries": len(turns), "invalid_topology_entries": len(invalid),
            "invalid_topology_nonzero": sum(x["flow"] > .01 for x in invalid), "invalid_entries": invalid,
            "links_with_table_sum_difference": len(discrepancy), "link_differences": discrepancy,
            "banned_turns_reported_positive": len(positive_bans), "positive_bans": positive_bans,
            "coverage_by_management": coverage,
            "limits": "非路网端点是确定的表格拓扑异常；流量汇总差异须结合OD源汇与表格覆盖解释，不直接判定所有路径错误"}


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = audit(args.scenario.resolve())
    save_json(args.output.resolve(), result)
    print(json.dumps({k:v for k,v in result.items() if not isinstance(v,list)}, ensure_ascii=False, indent=2))
