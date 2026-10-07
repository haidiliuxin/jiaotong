"""Read-only timed observation of incremental OD files in a live diagnostic probe."""
import argparse
import ctypes
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, save_json
from traffic_agent.runner import now


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("probe", type=Path)
    parser.add_argument("pid", type=int, help="已确认运行的探针Python进程")
    args = parser.parse_args()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
    kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel.OpenProcess(0x100000, False, args.pid)
    if not handle:
        raise RuntimeError("指定探针进程已不存在或无法观察")
    samples, last, originals = [], {}, {}
    started = time.monotonic()
    output = args.probe / "od_stage_observations.json"
    try:
        while time.monotonic() - started < 600 and kernel.WaitForSingleObject(handle, 0) == 0x102:
            try:
                state = json.loads((args.probe / "state.json").read_text(encoding="utf-8"))
                name = state.get("active_scenario")
                if name:
                    path = args.probe / "scenarios" / name / "基础路网"
                    if name not in originals:
                        originals[name] = sum(numeric(path / "result/ODCar.tsd"))
                    values = numeric(path / "cache/Assign_car.od")
                    if len(values) == 332 ** 2:
                        total = sum(values)
                        if total != last.get(name):
                            last[name] = total
                            entry = {"observed": now(), "scenario": name, "car_od_sum": total,
                                     "fraction_of_input": total / originals[name], "rows": len(values)}
                            samples.append(entry)
                            save_json(output, {"samples": samples, "note": "只记录观察期内完整矩阵的变化；启动之前的阶段不作推断"})
                            print(json.dumps(entry), flush=True)
            except (OSError, ValueError):
                pass  # A file actively being written is retried; never substitute it.
            time.sleep(.5)
    finally:
        kernel.CloseHandle(handle)
    save_json(output, {"samples": samples, "finished": now(), "note": "只读进程存活期采样；未覆盖全部阶段时不推断总分配量"})


if __name__ == "__main__":
    main()
