"""Full matched diagnostic using private wrappers around intact native solvers."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, save_json
from traffic_agent.oneway import FLOW_FILES, OneWayNetwork, REQUIRED_OUTPUTS, close_motor_direction, response
from traffic_agent.runner import EXPERIMENTS, execute, now, runtime_lock, setup_runtime
from traffic_agent.vehicle1 import MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    project = Path(__file__).resolve().parents[1]
    source = EXPERIMENTS / "jobs/20260908_063947_a72333/scenarios/demand_1p100_baseline/基础路网"
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_hard_access")
    job.mkdir(parents=True, exist_ok=False)
    pair = (3237, 3236)
    audit = OneWayNetwork.load(source).validate(pair)
    if not audit["eligible"]:
        raise ValueError("原区域图约束不合格")
    before = input_fingerprints(source)
    save_json(job / "source_inputs.json", before)
    save_json(job / "graph_audit.json", audit)
    state = dict(status="created", started=now(), source=str(source), closed_direction=list(pair), runs=[],
        limits="新增策略闭边在单车型原生求解前移除，已有路权/禁转仍沿用原分配配置；这是实验适配，不宣称全部原有规则生效或现实治理成功。")
    save_json(job / "state.json", state)
    print(f"PROBE {job}", flush=True)
    env_names = ("TRAFFIC_AGENT_PYTHON", "TRAFFIC_AGENT_BRIDGE_SCRIPT", "TRAFFIC_AGENT_BRIDGE_CONFIG")
    old_env = {name: os.environ.get(name) for name in env_names}
    with runtime_lock(EXPERIMENTS):
        runtime = setup_runtime(job)
        programs = runtime / "programs"
        native = runtime / "native_programs"
        native.mkdir()
        modes = ("car", "taxi", "motor", "truck")
        hashes = {}
        for mode in modes:
            path = programs / f"NodeAssignModel_{mode}.exe"
            shutil.copy2(path, native / path.name)
            hashes[mode] = hashlib.sha256(path.read_bytes()).hexdigest()
        shutil.copy2(project / "scripts/native_assignment_wrapper.c", programs / "native_assignment_wrapper.c")
        compiled = subprocess.run(["gcc", "-municode", "-static", "-Os", "native_assignment_wrapper.c", "-o", "native_assignment_wrapper.exe"],
            cwd=programs, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
        (job / "wrapper_compile_stdout.bin").write_bytes(compiled.stdout)
        (job / "wrapper_compile_stderr.bin").write_bytes(compiled.stderr)
        if compiled.returncode:
            state.update(status="failed", error="wrapper compilation failed", finished=now())
            save_json(job / "state.json", state)
            raise RuntimeError(compiled.stderr.decode(errors="replace"))
        for mode in modes:
            shutil.copy2(programs / "native_assignment_wrapper.exe", programs / f"NodeAssignModel_{mode}.exe")
        state.update(native_executable_hashes=hashes, wrapper_sha256=hashlib.sha256((programs / "native_assignment_wrapper.exe").read_bytes()).hexdigest(),
                     wrapper_source_sha256=hashlib.sha256((project / "scripts/native_assignment_wrapper.c").read_bytes()).hexdigest())
        config_path = job / "bridge_config.json"
        os.environ.update(TRAFFIC_AGENT_PYTHON=sys.executable, TRAFFIC_AGENT_BRIDGE_SCRIPT=str(project / "traffic_agent/native_access_bridge.py"),
                          TRAFFIC_AGENT_BRIDGE_CONFIG=str(config_path))
        try:
            state["status"] = "running"
            for name in ("baseline", "oneway", "oneway_repeat"):
                scenario = job / "scenarios" / name / "基础路网"
                shutil.copytree(source, scenario)
                extra = tuple(set(REQUIRED_OUTPUTS) | set(MODAL_OUTPUTS))
                for relative in set(extra) | {f"result/{n}" for n in OUTPUTS.values()} | set(UNSUPPORTED_TURN_OUTPUTS):
                    (scenario / relative).unlink(missing_ok=True)
                if name != "baseline":
                    close_motor_direction(scenario / "data/ManageR1.txt", pair)
                actual = input_fingerprints(scenario)
                if {k for k in actual.keys() | before.keys() if actual.get(k) != before.get(k)} != (set() if name == "baseline" else {"data/ManageR1.txt"}):
                    raise ValueError("完整对照出现额外输入变化")
                config = dict(runtime=str(runtime), native_programs=str(native), archive=str(job / "bridge_runs" / name),
                              policy_closures=[] if name == "baseline" else [list(pair)])
                save_json(config_path, config)
                save_json(job / "bridge_configurations" / f"{name}.json", config)
                state["active_scenario"] = name
                save_json(job / "state.json", state)
                def event(kind, payload):
                    with (job / "events.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(dict(time=now(), event=kind, data=payload), ensure_ascii=False) + "\n")
                    if kind == "tool_start":
                        print(json.dumps(dict(event=kind, data=payload), ensure_ascii=False), flush=True)
                _, manifest = execute(scenario, runtime, job / "logs" / name, 900, event,
                                      required_outputs=extra, assignment_module="M6AssignVehicle1.exe")
                bridges = [json.loads(p.read_text(encoding="utf-8")) for p in (job / "bridge_runs" / name).glob("*/manifest.json")]
                if len(bridges) != 20 or any(b["status"] != "completed" for b in bridges) or any(sum(b["mode"]==mode for b in bridges)!=5 for mode in modes):
                    raise ValueError("未完成四种车型各五轮原生桥接")
                record = dict(name=name, scenario=str(scenario.relative_to(job)), execution=manifest, bridge_calls=len(bridges))
                files = list(OUTPUTS.values()) + list(FLOW_FILES.values())
                if name == "baseline":
                    record["all_nine_arrays_equal_original_profile"] = all(numeric(source / "result" / f)==numeric(scenario / "result" / f) for f in files)
                    if not record["all_nine_arrays_equal_original_profile"]:
                        raise ValueError("无闭边桥接未复现原分配配置")
                else:
                    record["response"] = response(job / state["runs"][0]["scenario"], scenario, pair)
                if name == "oneway_repeat":
                    previous = job / state["runs"][1]["scenario"]
                    record["all_nine_arrays_equal_policy"] = all(numeric(previous / "result" / f)==numeric(scenario / "result" / f) for f in files)
                    if not record["all_nine_arrays_equal_policy"]:
                        raise ValueError("硬闭边策略复跑不一致")
                state["runs"].append(record)
                save_json(job / "state.json", state)
                print(json.dumps({k:v for k,v in record.items() if k != "execution"}, ensure_ascii=False), flush=True)
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            state.update(source_inputs_unchanged=input_fingerprints(source)==before, finished=now())
            state.pop("active_scenario", None)
            save_json(job / "state.json", state)


if __name__ == "__main__":
    main()
