"""Independently verify the matched shortest-incremental diagnostic and OD samples."""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import input_fingerprints, numeric, read_text, save_json, OUTPUTS
from traffic_agent.circulation_agent import turn_response
from traffic_agent.runner import MODULES


def verify(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    if state["status"] != "completed" or [r["name"] for r in state["runs"]] != ["baseline", "turn"]:
        raise ValueError("探针未完整完成配对")
    source = json.loads((job / "source_inputs.json").read_text(encoding="utf-8"))
    method = state.get("method", "shortest")
    assignment_module = state.get("assignment_module", "M6AssignVehicle.exe")
    if assignment_module not in ("M6AssignVehicle.exe", "M6AssignVehicle1.exe"):
        raise ValueError("非预设分配程序")
    expected_modules = tuple(assignment_module if name == "M6AssignVehicle.exe" else name for name in MODULES)
    derived = {f"params/AsignParameter_{mode}.dat" for mode in ("car", "motor", "taxi", "truck")} if method == "shortest" else set()
    manifests = {}
    fingerprints = []
    for run in state["runs"]:
        path = job / run["scenario"]
        manifest = json.loads((job / "logs" / run["name"] / "run.json").read_text(encoding="utf-8"))
        manifests[run["name"]] = manifest
        if manifest["status"] != "completed" or tuple(m["module"] for m in manifest["modules"]) != expected_modules:
            raise ValueError("模型调用不完整")
        if any(m["returncode"] != 0 or m["status"] != "completed" for m in manifest["modules"]):
            raise ValueError("模型失败")
        for relative, stamp in manifest["fresh_outputs"].items():
            actual = (path / relative).stat()
            if actual.st_size != stamp["bytes"] or actual.st_mtime_ns != stamp["mtime_ns"]:
                raise ValueError("探针输出被改动")
        before = json.loads((job / "logs" / run["name"] / "inputs_before.json").read_text(encoding="utf-8"))
        if input_fingerprints(path) != before:
            raise ValueError("探针输入被改动")
        expected = derived | ({"params/Mparam631.txt"} if method == "shortest" else set()) | ({"data/manageN.txt"} if run["name"] == "turn" else set())
        if {k for k in before.keys() | source.keys() if before.get(k) != source.get(k)} != expected:
            raise ValueError("探针输入差异不符")
        fingerprints.append(before)
        for relative in derived:
            if read_text(path / relative).splitlines()[3] != "700":
                raise ValueError("最短路派生参数不符")
        for filename in OUTPUTS.values():
            if len(numeric(path / "result" / filename)) != 32981:
                raise ValueError("结果维数不符")
    if {k for k in fingerprints[0] if fingerprints[0][k] != fingerprints[1][k]} != {"data/manageN.txt"}:
        raise ValueError("有/无禁转配对不再是单变量比较")
    response = turn_response(SimpleNamespace(job=job), *state["runs"])
    if response != state["response"]:
        raise ValueError("探针报告与真实转向结果不符")
    observation = job / "od_stage_observations.json"
    od = None
    if observation.exists():
        samples = json.loads(observation.read_text(encoding="utf-8"))["samples"]
        groups = {}
        for name in {x["scenario"] for x in samples}:
            values = [x["fraction_of_input"] for x in samples if x["scenario"] == name]
            exact = len(values) == 5 and all(math.isclose(v, weight / 15, abs_tol=1e-8) for v, weight in zip(values, [5, 4, 3, 2, 1]))
            groups[name] = {"observed_fractions": values, "sum": sum(values), "matches_5_4_3_2_1_over_15": exact}
        for sample in samples:
            module = next(m for m in manifests[sample["scenario"]]["modules"] if m["module"] == assignment_module)
            if not datetime.fromisoformat(module["started"]) <= datetime.fromisoformat(sample["observed"]) <= datetime.fromisoformat(module["finished"]):
                raise ValueError("OD采样不在实际分配进程期间")
        od = {"by_scenario": groups, "method": method,
              "scope": "各场景分别统计观测序列；完整5阶段权重验证不等于每个OD的累计路径流量验证"}
    report = {"passed": True, "scenarios": 2, "module_executions": 14, "turn_effective": response["passed"],
              "method": method, "assignment_module": assignment_module, "runtime_tmp_during": state.get("runtime_tmp_during"),
              "movements": response["movements"], "od_observation": od,
              "conclusion": "文件和单变量条件通过核验；是否禁转有效由turn_effective单独表示"}
    save_json(job / "verification.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    verify(parser.parse_args().job.resolve())
