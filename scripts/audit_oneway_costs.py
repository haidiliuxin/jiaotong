"""Read-only impedance-stage evidence for a completed single-variable trial."""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json, OUTPUTS


def audit(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    if state["status"] != "completed" or [r["name"] for r in state["runs"]] != ["baseline", "oneway"]:
        raise ValueError("需要完整配对试验")
    baseline, policy = [job / r["scenario"] for r in state["runs"]]
    pair = tuple(state["closed_direction"])
    ft = [tuple(map(int, s.split())) for s in read_text(baseline / "cache/FT.dat").splitlines() if s.strip()]
    other = [tuple(map(int, s.split())) for s in read_text(policy / "cache/FT.dat").splitlines() if s.strip()]
    if ft != other or len(set(ft)) != len(ft):
        raise ValueError("两场景FT顺序不一致或不唯一")
    index = ft.index(pair)
    records = {}
    for filename in ("Ljt_car.dat", "Ljt_taxi.dat", "Ljt_motor.dat", "Ljt_truck.dat", "Ljt_bus.dat"):
        values, stamps = [], []
        for scenario, run in zip((baseline, policy), state["runs"]):
            path = scenario / "cache" / filename
            stamp = path.stat()
            if stamp.st_mtime < datetime.fromisoformat(run["execution"]["started"]).timestamp() - 2:
                raise ValueError("车型阻抗不是本次新生成")
            array = numeric(path)
            if len(array) != len(ft) or min(array) < 0:
                raise ValueError("车型阻抗维数或数值非法")
            values.append(array)
            stamps.append({"bytes": stamp.st_size, "mtime_ns": stamp.st_mtime_ns,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        before, after = values
        changes = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        records[filename] = {"target_before": before[index], "target_after": after[index],
                             "changed_links": len(changes), "changed_pairs": [list(ft[i]) for i in changes],
                             "fresh_file_stamps": stamps}
    arrays = {name: {"length": len(numeric(baseline / "result" / filename)),
                     "exactly_equal": numeric(baseline / "result" / filename) == numeric(policy / "result" / filename)}
              for name, filename in OUTPUTS.items()}
    turn_tables = {}
    for scenario, run in zip((baseline, policy), state["runs"]):
        turn_tables[run["name"]] = {p.name: {"mtime_ns": p.stat().st_mtime_ns,
            "fresh": p.stat().st_mtime >= datetime.fromisoformat(run["execution"]["started"]).timestamp() - 2}
            for p in (scenario / "result").glob("TVolNode*.tsd")}
    report = {"closed_direction": list(pair), "impedance": records, "final_arrays": arrays, "turn_tables": turn_tables,
              "claim": "只定位车型阻抗生成与最终分配输出间的差异，不证明内部读取路径；未改写任何模型文件"}
    save_json(job / "impedance_audit.json", report)
    print(json.dumps({"report": str(job / "impedance_audit.json"), "impedance_changes": {
        k: {key: v[key] for key in ("target_before", "target_after", "changed_links")} for k, v in records.items()},
        "final_arrays": arrays, "turn_tables": turn_tables}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    audit(parser.parse_args().job.resolve())
