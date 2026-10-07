"""Read TranStar link outputs using their documented GEOM row order."""
import argparse
import csv
import json
import math
import re
from pathlib import Path


def read_text(path):
    data = Path(path).read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gb18030")


def numeric(path):
    lines = [line.strip() for line in read_text(path).splitlines() if line.strip()]
    if lines[0].startswith("{"):
        lines = lines[1:]
    values = [float(value) for line in lines for value in line.split()]
    if not all(math.isfinite(v) for v in values):
        raise ValueError(f"Non-finite result: {path}")
    return values


def links(scenario):
    scenario = Path(scenario)
    geometry = [line.split(maxsplit=8) for line in read_text(scenario / "data/geom.txt").splitlines() if line.strip()]
    management = {}
    for line in read_text(scenario / "data/manageL.txt").splitlines():
        fields = line.split()
        if len(fields) == 5 and fields[0].isdigit() and fields[1].isdigit():
            management[(int(fields[0]), int(fields[1]))] = int(fields[3])
    arrays = {key: numeric(scenario / "result" / name) for key, name in {
        "speed_kmh": "SpdLinkVeh.tsd", "flow_pcu_h": "VolLinkVeh.tsd",
        "vc": "VCLinkVeh.tsd", "time_s": "TimeLink.tsd"}.items()}
    if any(len(a) != len(geometry) for a in arrays.values()):
        raise ValueError("Link output lengths do not match GEOM row count")
    rows = []
    for index, g in enumerate(geometry):
        row = dict(index=index, from_node=int(g[0]), to_node=int(g[1]), length_km=float(g[2]) / 1000,
                   road_class=int(g[3]), lanes=float(g[4]), name=g[8] if len(g) > 8 else "")
        row.update({k: a[index] for k, a in arrays.items()})
        row["parking"] = management.get((row["from_node"], row["to_node"]))
        row["included"] = (1 <= row["road_class"] <= 6 and row["lanes"] > 0 and row["length_km"] > 0)
        rows.append(row)
    return rows


def metrics(rows):
    valid = [r for r in rows if r["included"]]
    if any(r["speed_kmh"] <= 0 or r["flow_pcu_h"] < 0 for r in valid):
        raise ValueError("Invalid speed or flow on evaluation links")
    vkt = sum(r["flow_pcu_h"] * r["length_km"] for r in valid)
    hours = sum(r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"] for r in valid)
    total_length = sum(r["length_km"] for r in valid)
    overloaded_length = sum(r["length_km"] for r in valid if r["vc"] > 1)
    time_flagged = [r for r in valid if r["time_s"] >= 1e6]
    return {"included_directed_links": len(valid), "directed_length_km": total_length,
            "weighted_speed_kmh": vkt / hours if hours else None,
            "oversaturated_directed_length_km": overloaded_length,
            "oversaturated_length_pct": 100 * overloaded_length / total_length,
            "link_running_time_pcu_h_for_one_hour": hours,
            "timelink_weighted_pcu_h_for_one_hour": None if time_flagged else sum(r["flow_pcu_h"] * r["time_s"] / 3600 for r in valid),
            "timelink_flagged_directed_links": len(time_flagged),
            "time_quality_note": "TimeLink has >=1e6 second values; do not report its sum as total travel time" if time_flagged else "No >=1e6 second values",
            "transport_pcu_km_for_one_hour": vkt}


def candidates(rows):
    chosen = [r.copy() for r in rows if r["included"] and r["road_class"] in (2, 3)
              and r["parking"] in (3, 4) and 0.1 <= r["length_km"] <= 1.5
              and r["flow_pcu_h"] >= 500 and 1 < r["vc"] <= 4 and 0 < r["speed_kmh"] < 40
              and "匝道" not in r["name"] and not re.fullmatch(r"\d+路", r["name"])]
    for r in chosen:
        r["priority_score"] = r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"]
    return sorted(chosen, key=lambda r: (-r["priority_score"], r["index"]))


def analyze(scenario, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = links(scenario)
    summary = metrics(rows)
    (output / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "candidates.json").write_text(json.dumps(candidates(rows)[:20], ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "links.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("TOP CANDIDATES", json.dumps(candidates(rows)[:5], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("output")
    args = parser.parse_args()
    analyze(args.scenario, args.output)
