"""One-variable diagnostic: shortest-path incremental vs existing multipath setup.

Not a production adapter or traffic-policy recommendation. Keeps both matched runs.
"""
import json
import hashlib
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import prepare, input_fingerprints, read_text, save_json, OUTPUTS
from traffic_agent.runner import DEFAULT_SOURCE, EXPERIMENTS, runtime_lock, setup_runtime, validate_source, execute, now
from traffic_agent.circulation import TurnGraph
from traffic_agent.circulation_agent import turn_response


def shortest_incremental(path):
    lines = path.read_bytes().splitlines(keepends=True)
    indices = [i for i, line in enumerate(lines) if re.fullmatch(rb"[ \t]*\d+(?:\.\d+)?[ \t\r\n]*", line)]
    values = [float(lines[i].strip()) for i in indices]
    if values[:7] != [0, 0, 0, 1, 0, 0, 5] or len(values) != 8:
        raise ValueError("探针仅接受已验证的原始多路径增量5次配置")
    for slot, value in ((1, 1), (3, 0)):
        index = indices[slot]
        lines[index] = re.sub(rb"\d+(?:\.\d+)?", str(value).encode(), lines[index], count=1)
    path.write_bytes(b"".join(lines))


def normalize_shortest_parameters(scenario):
    """Use the exact algorithm-derived values observed in the first failed probe."""
    changed = set()
    for mode in ("car", "motor", "taxi", "truck"):
        relative = f"params/AsignParameter_{mode}.dat"
        path = scenario / relative
        lines = path.read_bytes().splitlines(keepends=True)
        if len(lines) != 5 or lines[3].strip() != b"7.2":
            raise ValueError("原始分配派生参数不是已观察的7.2配置")
        lines[3] = lines[3].replace(b"7.2", b"700", 1)
        path.write_bytes(b"".join(lines))
        changed.add(relative)
    return changed


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=["shortest", "multipath"], default="shortest")
    parser.add_argument("--runtime-tmp", action="store_true", help="验证原软件的临时路网目录是否为必需依赖")
    parser.add_argument("--assignment-module", choices=["M6AssignVehicle.exe", "M6AssignVehicle1.exe"],
                        default="M6AssignVehicle.exe", help="只在隔离诊断中选择随软件提供的分配程序，不改变正式入口")
    args = parser.parse_args()
    root = EXPERIMENTS
    alternate = args.assignment_module == "M6AssignVehicle1.exe"
    job = root / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + args.method + ("_tmp" if args.runtime_tmp else "") + ("_vehicle1" if alternate else "") + "_turn")
    job.mkdir(parents=True, exist_ok=False)
    turn = [4797, 5990, 5991]
    state = {"id": job.name, "status": "created", "started": now(), "runs": [],
             "hypothesis": "当前多路径增量算法未响应禁转；只改为最短路增量5次后再作有/无禁转配对",
             "limits": "诊断探针，不改变正式智能体分配方法；不得与旧多路径基准直接计算政策收益"}
    state["method"] = args.method
    state["assignment_module"] = args.assignment_module
    if args.runtime_tmp:
        state["hypothesis"] = "隔离运行环境缺少原软件tmp目录，无法保存禁转展开图；补齐目录后进行同算法有/无禁转配对"
    if alternate:
        state["hypothesis"] = "随软件提供的Vehicle1调用分车型模块，可能避开默认程序内的空禁转列表；用新匹配基准与禁转配对检验"
        state["limits"] += "；替代程序名称不证明兼容或有效，输入、新输出和转向响应要求均保留"
    def persist():
        save_json(job / "state.json", state)
    def event(kind, payload):
        entry = {"time": now(), "event": kind, "data": payload}
        with (job / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    persist()
    print(f"PROBE {job}", flush=True)
    with runtime_lock(root):
        validate_source(DEFAULT_SOURCE)
        original = input_fingerprints(DEFAULT_SOURCE)
        save_json(job / "source_inputs.json", original)
        runtime = setup_runtime(root)
        executable = runtime / "programs" / args.assignment_module
        state["assignment_binary"] = {"name": executable.name, "bytes": executable.stat().st_size,
                                      "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}
        state["runtime_tmp_before"] = (runtime / "tmp").is_dir()
        if args.runtime_tmp:
            (runtime / "tmp").mkdir(exist_ok=True)
        state["runtime_tmp_during"] = (runtime / "tmp").is_dir()
        if not TurnGraph.load(DEFAULT_SOURCE).validate([turn])["eligible"]:
            raise ValueError("探针转向不满足图约束")
        try:
            state["status"] = "running"
            for name, changes in [("baseline", []), ("turn", [{"kind": "turn", "turn": turn}])]:
                scenario = job / "scenarios" / name / "基础路网"
                extra = ("cache/ManageNM.dat", "result/TVolNodecar.tsd")
                edits = prepare(DEFAULT_SOURCE, scenario, changes, extra_outputs=extra)
                derived = set()
                if args.method == "shortest":
                    shortest_incremental(scenario / "params/Mparam631.txt")
                    derived = normalize_shortest_parameters(scenario)
                actual = input_fingerprints(scenario)
                diff = {k for k in original.keys() | actual.keys() if original.get(k) != actual.get(k)}
                expected = ({"params/Mparam631.txt"} if args.method == "shortest" else set()) | derived | ({"data/manageN.txt"} if changes else set())
                if diff != expected:
                    raise ValueError("探针输入出现额外差异")
                save_json(scenario.parent / "edits.json", {"changes": edits, "input_differences": sorted(diff),
                                                           "assignment": args.method + "_incremental_5",
                                                           "derived_assignment_value": 700 if args.method == "shortest" else 7.2,
                                                           "derived_evidence": "20260908_041801_shortest_turn: shortest method changes 7.2 to 700"})
                state["active_scenario"] = name
                persist()
                rows, manifest = execute(scenario, runtime, job / "logs" / name, 900, event, required_outputs=extra,
                                         assignment_module=args.assignment_module)
                record = {"name": name, "scenario": str(scenario.relative_to(job)), "changes": changes,
                          "demand_factor": 1.0, "execution": manifest}
                state["runs"].append(record)
                state["runtime_tmp_files"] = [{"name": p.name, "bytes": p.stat().st_size} for p in (runtime / "tmp").glob("*") if p.is_file()]
                if changes:
                    state["response"] = turn_response(SimpleNamespace(job=job), state["runs"][0], record)
                persist()
            if input_fingerprints(DEFAULT_SOURCE) != original:
                raise ValueError("原始输入被修改")
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            state["finished"] = now()
            state.pop("active_scenario", None)
            persist()
    print(json.dumps({"probe": str(job), "response": state.get("response")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
