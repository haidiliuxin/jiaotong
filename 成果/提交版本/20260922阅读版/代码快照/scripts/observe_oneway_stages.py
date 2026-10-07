"""Read-only snapshots of per-iteration modal output in an already live agent job."""
import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.runner import now


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("pid", type=int, help="已确认正在运行该任务的Python进程")
    args = parser.parse_args()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x100000, False, args.pid)
    if not handle:
        raise RuntimeError("指定任务进程不存在或无法观察")
    output = args.job / "stage_observations"
    output.mkdir(exist_ok=False)
    report = {"started": now(), "observed_pid": args.pid, "samples": [],
              "limits": "只读观察；启动前阶段不补造；索引按本场景FT定位并保留原始volume快照供复核"}
    last = {}
    begin = time.monotonic()
    try:
        while time.monotonic() - begin < 1800 and kernel.WaitForSingleObject(handle, 0) == 0x102:
            try:
                state = json.loads((args.job / "state.json").read_text(encoding="utf-8"))
                name = state.get("active_scenario", "")
                if "S2_oneway" not in name and "S3_combined" not in name:
                    time.sleep(.5)
                    continue
                scenario = args.job / "scenarios" / name / "基础路网"
                pair = tuple(state["factorial_design"]["closed_direction"])
                ft = [tuple(map(int, s.split())) for s in read_text(scenario / "cache/FT.dat").splitlines() if s.strip()]
                index = ft.index(pair)
                source_od = sum(numeric(scenario / "result/ODCar.tsd"))
                for mode in ("car", "taxi"):
                    path = scenario / f"cache/volume_{mode}.dat"
                    before = path.stat()
                    key = (name, mode)
                    if before.st_mtime_ns == last.get(key) or time.time() - before.st_mtime < .3:
                        continue
                    raw = path.read_bytes()
                    values = numeric(path)
                    stamp = path.stat()
                    if stamp.st_mtime_ns != before.st_mtime_ns or len(values) != len(ft) or min(values) < 0:
                        continue
                    fraction = sum(numeric(scenario / "cache/Assign_car.od")) / source_od
                    if not 0 < fraction <= 1:
                        continue
                    sample_id = len(report["samples"]) + 1
                    snapshot = f"{sample_id:03d}_{name}_{mode}.dat"
                    (output / snapshot).write_bytes(raw)
                    entry = {"observed": now(), "scenario": name, "mode": mode, "volume_snapshot": snapshot,
                             "volume_mtime_ns": stamp.st_mtime_ns, "values": len(values), "ft_index": index,
                             "closed_direction": list(pair), "target_stage_flow": values[index],
                             "car_od_fraction_at_observation": fraction,
                             "ljt_target": numeric(scenario / f"cache/Ljt_{mode}.dat")[index],
                             "rwm_target_at_ft_position": numeric(scenario / f"cache/Rwm_{mode}.dat")[index]}
                    report["samples"].append(entry)
                    last[key] = stamp.st_mtime_ns
                    save_json(output / "observations.json", report)
                    print(json.dumps(entry, ensure_ascii=False), flush=True)
            except (OSError, ValueError, KeyError, IndexError):
                pass  # Retry incomplete writes; never replace input or model output.
            time.sleep(.3)
    finally:
        kernel.CloseHandle(handle)
        report["finished"] = now()
        save_json(output / "observations.json", report)


if __name__ == "__main__":
    main()
