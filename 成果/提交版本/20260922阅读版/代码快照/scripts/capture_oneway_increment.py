"""Rerun one archived policy unchanged and capture car inputs after increment two."""
import hashlib
import json
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, save_json
from traffic_agent.oneway import REQUIRED_OUTPUTS
from traffic_agent.runner import EXPERIMENTS, execute, now, runtime_lock, setup_runtime
from traffic_agent.vehicle1 import MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS

CAPTURE_FILES = ("data/number.txt", "params/AsignParameter_car.dat", "cache/NodeInZone.dat",
    "cache/Shortest_No.dat", "cache/rnode.dat", "cache/tv.dat", "cache/n_r.dat", "cache/szn.dat",
    "cache/Assign_car.od", "cache/Rwm_car.dat", "cache/volume_car.dat", "cache/FT.dat")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    source = EXPERIMENTS / "jobs/20260908_063947_a72333/scenarios/demand_1p100_S2_oneway/基础路网"
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_capture_increment")
    job.mkdir(parents=True, exist_ok=False)
    state = dict(status="created", started=now(), source=str(source), capture=None,
        limits="完整同输入复跑用于获取第二轮单车型可重放输入；这是诊断数据，不是新治理收益")
    save_json(job / "state.json", state)
    print(f"PROBE {job}", flush=True)
    stop = threading.Event()
    capture = {}
    with runtime_lock(EXPERIMENTS):
        scenario = job / "scenarios/repeat/基础路网"
        shutil.copytree(source, scenario)
        extra = tuple(set(REQUIRED_OUTPUTS) | set(MODAL_OUTPUTS))
        for relative in set(extra) | {f"result/{n}" for n in OUTPUTS.values()} | set(UNSUPPORTED_TURN_OUTPUTS):
            (scenario / relative).unlink(missing_ok=True)
        before = input_fingerprints(source)
        if input_fingerprints(scenario) != before:
            raise ValueError("复跑政策输入不相同")
        save_json(job / "source_inputs.json", before)
        source_total = sum(numeric(source / "result/ODCar.tsd"))

        def observe():
            try:
                while not stop.wait(.1):
                    try:
                        od = scenario / "cache/Assign_car.od"
                        volume = scenario / "cache/volume_car.dat"
                        rwm = scenario / "cache/Rwm_car.dat"
                        if not volume.exists() or time.time() - volume.stat().st_mtime < .2:
                            continue
                        if volume.stat().st_mtime_ns < max(od.stat().st_mtime_ns, rwm.stat().st_mtime_ns):
                            continue  # Do not pair the preceding volume with a new round's OD.
                        fraction = sum(numeric(od)) / source_total
                        if abs(fraction - 4 / 15) > 1e-6:
                            continue
                        raw = {n: (scenario / n).read_bytes() for n in CAPTURE_FILES}
                        if len(numeric(volume)) != 32981 or any((scenario / n).read_bytes() != data for n, data in raw.items()):
                            continue
                        stamps = {n: (scenario / n).stat().st_mtime_ns for n in raw}
                        dest = job / "captured_car_increment/基础路网"
                        for name, data in raw.items():
                            path = dest / name
                            path.parent.mkdir(parents=True, exist_ok=True)
                            path.write_bytes(data)
                        capture.update(observed=now(), fraction=fraction, source_mtime_ns=stamps,
                            hashes={n: hashlib.sha256(data).hexdigest() for n, data in raw.items()},
                            fixture=str(dest.relative_to(job)), target_stage_flow=numeric(dest / "cache/volume_car.dat")[8900],
                            limits="只读稳定窗口快照，需通过独立NodeAssign重放全数组相同验证输入集合充分；不是内部原子追踪")
                        save_json(job / "capture.json", capture)
                        print(json.dumps({"event": "captured", **capture}, ensure_ascii=False), flush=True)
                        return
                    except (OSError, ValueError):
                        continue
            except BaseException as exc:
                capture["error"] = f"{type(exc).__name__}: {exc}"

        watcher = threading.Thread(target=observe)
        watcher.start()
        try:
            state["status"] = "running"
            save_json(job / "state.json", state)
            def event(kind, payload):
                with (job / "events.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(dict(time=now(), event=kind, data=payload), ensure_ascii=False) + "\n")
                if kind == "tool_start":
                    print(json.dumps(dict(event=kind, data=payload), ensure_ascii=False), flush=True)
            _, manifest = execute(scenario, setup_runtime(EXPERIMENTS), job / "logs/repeat", 900, event,
                                  required_outputs=extra, assignment_module="M6AssignVehicle1.exe")
            state["execution"] = manifest
            state["four_arrays_equal_archived_policy"] = {key: numeric(source / "result" / name) == numeric(scenario / "result" / name)
                                                          for key, name in OUTPUTS.items()}
            if not all(state["four_arrays_equal_archived_policy"].values()) or not capture.get("hashes"):
                raise ValueError("复跑不一致或没有捕获第二轮")
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            stop.set()
            watcher.join(timeout=5)
            state.update(capture=capture, source_inputs_unchanged=input_fingerprints(source) == before, finished=now())
            save_json(job / "state.json", state)


if __name__ == "__main__":
    main()
