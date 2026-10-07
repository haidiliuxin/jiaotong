"""Read actual bus speeds after a completed run; do not infer passenger benefits."""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import geometry, numeric, read_text, save_json
from traffic_agent.circulation import bus_turns


def route_times(path, manifest):
    speed_file = path / "result/SpdLinkBus.tsd"
    stamp = speed_file.stat()
    started = datetime.fromisoformat(manifest["started"]).timestamp()
    finished = datetime.fromisoformat(manifest["finished"]).timestamp()
    if not started - 2 <= stamp.st_mtime <= finished + 2:
        raise ValueError("公交速度不是该次运行新生成的输出")
    rows, speeds = geometry(path), numeric(speed_file)
    if len(rows) != len(speeds) or min(speeds) <= 0:
        raise ValueError("公交速度维数或正值检查失败")
    pairs = {(r["from_node"], r["to_node"]): (r, speed) for r, speed in zip(rows, speeds)}
    bus_turns(path, set(pairs))  # Validate counts, IDs, topology and loop closure.
    times = {}
    for filename in ("BusNodeBus.txt", "BusNodeBRT.txt"):
        lines = [line for line in read_text(path / "data" / filename).splitlines() if line.strip()]
        for line in lines[2:]:
            route, count, *nodes = map(int, line.split())
            entries = [pairs[p] for p in zip(nodes, nodes[1:])]
            flagged = sum(speed <= .01 or not road["included"] for road, speed in entries)
            times[f"{filename}:{route}"] = {
                "minutes": None if flagged else sum(60 * road["length_km"] / speed for road, speed in entries),
                "flagged_segments": flagged, "segments": len(entries)}
    return times, {"file": "result/SpdLinkBus.tsd", "bytes": stamp.st_size, "mtime_ns": stamp.st_mtime_ns}


def audit(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    if state["status"] != "completed":
        raise ValueError("公交影响核验只接受已完成任务")
    runs = state["runs"]
    values, stamps = {}, {}
    for run in runs:
        manifest = json.loads((job / "logs" / run["name"] / "run.json").read_text(encoding="utf-8"))
        values[run["name"]], stamps[run["name"]] = route_times(job / run["scenario"], manifest)
    comparisons = []
    for run in runs:
        if not run["changes"]:
            continue
        base = next(r for r in runs if not r["changes"] and r["demand_factor"] == run["demand_factor"])
        before, after = values[base["name"]], values[run["name"]]
        if before.keys() != after.keys():
            raise ValueError("公交线路集合变化")
        changes = []
        excluded = []
        for route, a in before.items():
            b = after[route]
            if a["minutes"] is None or b["minutes"] is None or a["minutes"] <= 0:
                excluded.append(route)
                continue
            delta = b["minutes"] - a["minutes"]
            if not math.isclose(delta, 0, abs_tol=1e-8):
                changes.append({"route": route, "before_minutes": a["minutes"], "after_minutes": b["minutes"],
                                "change_minutes": delta, "change_pct": delta / a["minutes"] * 100})
        changes.sort(key=lambda c: (-c["change_pct"], c["route"]))
        comparisons.append({"scenario": run["name"], "matched_baseline": base["name"],
                            "route_count": len(before), "excluded_routes": excluded,
                            "routes_slower": sum(c["change_minutes"] > 0 for c in changes),
                            "routes_faster": sum(c["change_minutes"] < 0 for c in changes),
                            "changed_routes": changes})
    result = {"job": state["id"], "metric": "每条固定公交线路Σ(60L/SpdLinkBus)，分钟",
              "freshness": stamps, "comparisons": comparisons,
              "limits": ["这是实验后的补充影响审计，未作为本次预设推荐阈值", "未核算客流、人次收益、候车、换乘和独立停车延误",
                         "速度0.01或包含非有效机动车路段的整条线路列入排除项，不默默删掉困难路段"]}
    save_json(job / "bus_impact.json", result)
    lines = ["# 公交线路补充影响审计", "", result["metric"], "",
             "使用本次新生成的SpdLinkBus.tsd和固定线路，不以机动车速度冒充公交速度。该审计不改变原预设推荐规则。", "",
             "| 场景 | 匹配基准 | 线路总数 | 无有效汇总线路 | 变慢 | 变快 | 最大变慢比例 |", "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for c in comparisons:
        worst = max((r["change_pct"] for r in c["changed_routes"]), default=0)
        lines.append(f"| {c['scenario']} | {c['matched_baseline']} | {c['route_count']} | {len(c['excluded_routes'])} | {c['routes_slower']} | {c['routes_faster']} | {max(0, worst):.5f}% |")
    lines += ["", "## 边界", ""] + ["- " + x for x in result["limits"]]
    lines += ["", "逐线路变化、排除项及新输出时间戳见bus_impact.json。公交转向拓扑保护与运行时间影响是两项不同检查。", ""]
    (job / "bus_impact.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"job": result["job"], "audited_scenarios": len(values), "comparisons": len(comparisons)}, ensure_ascii=False))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    audit(parser.parse_args().job.resolve())
