"""Isolate shipped car assignment with frozen final-round inputs; vary one cost."""
import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.runner import EXPERIMENTS, now, runtime_lock, setup_runtime


def fingerprints(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file()}


def replace_cost(path, index, cost):
    lines = path.read_bytes().splitlines(keepends=True)
    if len(lines) != 32981 or any(len(line.split()) != 1 for line in lines):
        raise ValueError("需要每条边单独一行的阻抗文件")
    old = float(lines[index])
    newline = b"\r\n" if lines[index].endswith(b"\r\n") else b"\n" if lines[index].endswith(b"\n") else b""
    lines[index] = format(cost, ".17g").encode("ascii") + newline
    path.write_bytes(b"".join(lines))
    return old


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=EXPERIMENTS / "jobs/20260908_063947_a72333/scenarios/demand_1p100_S2_oneway/基础路网")
    parser.add_argument("--costs", type=float, nargs="+", default=[9999999999, 1e12])
    args = parser.parse_args()
    if not args.costs or any(not math.isfinite(x) or x <= 0 for x in args.costs):
        raise ValueError("阻抗必须有限且为正")
    source = args.source.resolve()
    ft = [tuple(map(int, s.split())) for s in read_text(source / "cache/FT.dat").splitlines() if s.strip()]
    pair = (3237, 3236)
    index = ft.index(pair)
    tv = numeric(source / "cache/tv.dat")
    nodes = numeric(source / "cache/rnode.dat")
    counts = numeric(source / "cache/n_r.dat")
    origins = [int(node) for node, count in zip(nodes, counts[1:]) for _ in range(int(count))]
    if (counts[0] != len(nodes) or len(counts) != len(nodes) + 1 or len(tv) != len(origins)
            or any(v != int(v) or not 1 <= v <= len(nodes) for v in tv)
            or list(zip(origins, [int(nodes[int(v) - 1]) for v in tv])) != ft):
        raise ValueError("TV、rnode、n_r不能重建完整FT，不能假定其阻抗索引")
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_node_cost")
    job.mkdir(parents=True, exist_ok=False)
    state = dict(status="created", started=now(), source=str(source), closed_direction=list(pair), ft_index=index,
                 runs=[], limits="仅冻结末轮输入的单车型分配诊断，不是全流程治理或OD守恒结论；不修改厂商程序和既有实验")
    save_json(job / "state.json", state)
    print(f"PROBE {job}", flush=True)
    with runtime_lock(EXPERIMENTS):
        runtime = setup_runtime(EXPERIMENTS)
        config = runtime / "files/runinfo_vc6.txt"
        original_config = config.read_bytes()
        source_hashes = fingerprints(source)
        save_json(job / "source_files.json", source_hashes)
        expected = numeric(source / "cache/volume_car.dat")
        original_cost = numeric(source / "cache/Rwm_car.dat")[index]
        state.update(status="running", original_cost=original_cost,
                     executable_sha256=hashlib.sha256((runtime / "programs/NodeAssignModel_car.exe").read_bytes()).hexdigest())
        trials = [("control", None)] + [(f"cost_{i+1}", value) for i, value in enumerate(args.costs)] + [("control_repeat", None)]
        active = None
        try:
            for name, cost in trials:
                scenario = job / "scenarios" / name / "基础路网"
                shutil.copytree(source, scenario)
                if cost is not None:
                    replace_cost(scenario / "cache/Rwm_car.dat", index, cost)
                # Only the fresh single-module output is removed in this new copy.
                output = scenario / "cache/volume_car.dat"
                output.unlink()
                before = fingerprints(scenario)
                expected_changes = {"cache/volume_car.dat"} | ({"cache/Rwm_car.dat"} if cost is not None else set())
                actual_changes = {k for k in before.keys() | source_hashes.keys() if before.get(k) != source_hashes.get(k)}
                if actual_changes != expected_changes:
                    raise ValueError("试验输入差异超出单条Rwm与移除旧输出")
                config.write_bytes(("scenario_dir\r\n" + scenario.as_posix() + "\r\n").encode("gb18030"))
                logs = job / "logs" / name
                logs.mkdir(parents=True)
                save_json(logs / "inputs_before.json", before)
                record = dict(name=name, scenario=str(scenario.relative_to(job)), cost=original_cost if cost is None else cost,
                              status="running", started=now(), input_difference_files=sorted(actual_changes))
                state["runs"].append(record)
                save_json(job / "state.json", state)
                stamp = time.time_ns()
                with (logs / "stdout.bin").open("wb") as out, (logs / "stderr.bin").open("wb") as err:
                    active = subprocess.Popen([str(runtime / "programs/NodeAssignModel_car.exe")], cwd=runtime / "programs",
                        stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
                    record["pid"] = active.pid
                    save_json(job / "state.json", state)
                    record["returncode"] = active.wait(timeout=60)
                record["finished"] = now()
                text = "\n".join((logs / f"{s}.bin").read_bytes().decode("gb18030", errors="replace") for s in ("stdout", "stderr"))
                (logs / "output.txt").write_text(text, encoding="utf-8")
                if record["returncode"] or "[error]" in text.lower() or not output.is_file() or output.stat().st_mtime_ns < stamp:
                    raise ValueError("单车型程序未正常生成本次输出")
                values = numeric(output)
                if len(values) != len(ft) or min(values) < 0:
                    raise ValueError("分配输出维数或数值不符")
                after = fingerprints(scenario)
                changed = {k for k in after.keys() | before.keys() if before.get(k) != after.get(k)}
                record.update(status="completed", target_flow=values[index], forbidden_flow_passed=values[index] <= .01,
                    changed_during_run=sorted(changed), changed_vs_original_volume=sum(a != b for a,b in zip(values, expected)),
                    max_volume_difference=max(abs(a-b) for a,b in zip(values, expected)), output_sha256=after["cache/volume_car.dat"])
                if changed != {"cache/volume_car.dat"}:
                    raise ValueError(f"单模块改写了其他输入：{changed}")
                if cost is None and values != expected:
                    raise ValueError("冻结末轮直接调用未复现原volume，停止阻抗实验")
                save_json(logs / "files_after.json", after)
                save_json(job / "state.json", state)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if active is not None and active.poll() is None:
                subprocess.run(["taskkill", "/PID", str(active.pid), "/T", "/F"], capture_output=True,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                active.wait(timeout=10)
            config.write_bytes(original_config)
            state["source_files_unchanged"] = fingerprints(source) == source_hashes
            state["runtime_config_restored"] = config.read_bytes() == original_config
            state["finished"] = now()
            save_json(job / "state.json", state)


if __name__ == "__main__":
    main()
