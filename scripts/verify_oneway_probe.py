"""Recheck one-way trial inputs, execution, fixed scopes and all motor-mode flows."""
import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import input_fingerprints, results, save_json, scoped_metrics, scopes, OUTPUTS
from traffic_agent.oneway import access_rows, OneWayNetwork, response, REQUIRED_OUTPUTS, MODE_COLUMNS
from traffic_agent.runner import MODULES, DEFAULT_SOURCE
from scripts.vehicle1_parameters import DERIVED_FILES, MODAL_OUTPUTS, VEHICLE1_SHA256


def verify(job):
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    state = read(job / "state.json")
    if state["status"] != "completed" or [r["name"] for r in state["runs"]] != ["baseline", "oneway"]:
        raise ValueError("单向试验尚未完整完成")
    original = read(job / "source_inputs.json")
    assignment_module = state.get("assignment_module", "M6AssignVehicle.exe")
    if assignment_module not in ("M6AssignVehicle.exe", "M6AssignVehicle1.exe"):
        raise ValueError("未许可的分配分支")
    alternate = assignment_module == "M6AssignVehicle1.exe"
    expected_modules = tuple(assignment_module if m == "M6AssignVehicle.exe" else m for m in MODULES)
    if alternate and (state["derived_configuration"]["binary_sha256"] != VEHICLE1_SHA256 or
                      state["derived_configuration"]["parameters"] != [0, 0, 0, 1, 0, 0, 5, .001] or
                      state["derived_configuration"]["derived_theta"] != 3.3):
        raise ValueError("Vehicle1参数依据与已核对配置不符")
    if input_fingerprints(DEFAULT_SOURCE) != original or not state["source_inputs_unchanged"]:
        raise ValueError("原始输入不符")
    selection = read(job / "selection.json")
    if not selection["screening"][-1]["audit"]["eligible"] or any(r["audit"]["eligible"] for r in selection["screening"][:-1]):
        raise ValueError("未选取首个合格候选")
    pair = tuple(state["closed_direction"])
    if selection["screening"][-1]["audit"]["closed_direction"] != list(pair):
        raise ValueError("实际方向与预选方向不同")
    saved_scopes = read(job / "scopes.json")
    metrics = {}
    for run in state["runs"]:
        scenario = job / run["scenario"]
        manifest = read(job / "logs" / run["name"] / "run.json")
        if manifest != run["execution"] or manifest["status"] != "completed":
            raise ValueError("模型完成记录不一致")
        if tuple(m["module"] for m in manifest["modules"]) != expected_modules:
            raise ValueError("七个模型调用不完整")
        if datetime.fromisoformat(selection["selected_before_simulation"]) >= datetime.fromisoformat(manifest["started"]):
            raise ValueError("选点未在模型运行前保存")
        for module in manifest["modules"]:
            if module["returncode"] or module["status"] != "completed":
                raise ValueError("存在失败模型")
            for stream in ("stdout", "stderr"):
                raw = (job / "logs" / run["name"] / f"{module['module']}.{stream}.bin").read_bytes()
                if b"[error]" in raw.lower():
                    raise ValueError("模型原始日志包含错误")
        required = set(REQUIRED_OUTPUTS) | {f"result/{name}" for name in OUTPUTS.values()} | {"cache/CapLinkVehMan.dat", "cache/Assign_car.od"}
        if alternate:
            required |= set(MODAL_OUTPUTS)
        if not required <= set(manifest["fresh_outputs"]):
            raise ValueError("缺失车型或路权新输出证明")
        for relative, stamp in manifest["fresh_outputs"].items():
            actual = (scenario / relative).stat()
            if actual.st_size != stamp["bytes"] or actual.st_mtime_ns != stamp["mtime_ns"]:
                raise ValueError("保存后输出发生改变")
            if actual.st_mtime < datetime.fromisoformat(manifest["started"]).timestamp() - 2:
                raise ValueError("输出早于本次模型")
        actual = input_fingerprints(scenario)
        if actual != read(job / "logs" / run["name"] / "inputs_before.json"):
            raise ValueError("模型运行后输入变化")
        differences = {k for k in actual.keys() | original.keys() if actual.get(k) != original.get(k)}
        expected_files = ({"data/ManageR1.txt"} if run["name"] == "oneway" else set()) | (DERIVED_FILES if alternate else set())
        if differences != expected_files:
            raise ValueError("不符合单变量输入条件")
        if alternate:
            canonical = (scenario / "params/Mparam631.txt").read_bytes().replace(b"Mparam631.txt-", b"Mparam635.txt-", 1)
            if (scenario / "params/Mparam635.txt").read_bytes() != canonical:
                raise ValueError("635内容与核对过的八字段顺序不符")
            for relative in DERIVED_FILES - {"params/Mparam635.txt"}:
                source_bytes = (DEFAULT_SOURCE / relative).read_bytes()
                if (scenario / relative).read_bytes() != source_bytes.replace(b"7.2", b"3.3", 1):
                    raise ValueError("车型派生参数并非仅7.2改3.3")
        rows = results(scenario)
        indices = {(r["from_node"], r["to_node"]): r["index"] for r in rows}
        expected_scopes = scopes(rows, [indices[pair], indices[pair[::-1]]], 1)
        if expected_scopes != saved_scopes:
            raise ValueError("核心方向、邻域或全网范围变化")
        metrics[run["name"]] = scoped_metrics(rows, saved_scopes)
        if metrics[run["name"]] != run["metrics"]:
            raise ValueError("报告指标不符")
    baseline, policy = [job / r["scenario"] for r in state["runs"]]
    base_inputs, policy_inputs = input_fingerprints(baseline), input_fingerprints(policy)
    if {k for k in base_inputs.keys() | policy_inputs.keys() if base_inputs.get(k) != policy_inputs.get(k)} != {"data/ManageR1.txt"}:
        raise ValueError("实际配对差异不止路权")
    before, after = access_rows(baseline / "data/ManageR1.txt"), access_rows(policy / "data/ManageR1.txt")
    expected = dict(before)
    values = list(before[pair])
    for col in MODE_COLUMNS.values():
        if values[col] != 0:
            raise ValueError("原方向并非四类机动车均可通行")
        values[col] = 2
    expected[pair] = tuple(values)
    if after != expected:
        raise ValueError("路权改动超出单条方向四个字段")
    audit = OneWayNetwork.load(baseline).validate(pair)
    if not audit["eligible"] or audit != selection["screening"][-1]["audit"]:
        raise ValueError("公交或全车型边界绕行证明不符")
    actual_response = response(baseline, policy, pair)
    recorded = state["response"]
    expected_record = dict(actual_response)
    if "cache_encoding" not in recorded:
        # Preserve and recheck the first trial's original, documented binary-cache
        # assumption. Report the corrected interpretation separately, never rewrite state.
        from traffic_agent.data import read_text
        ft = [tuple(map(int, s.split())) for s in read_text(policy / "cache/FT.dat").splitlines() if s.strip()]
        cache = [tuple(map(int, s.split())) for s in read_text(policy / "cache/ManageR1.dat").splitlines() if s.strip()]
        binary_match = len(ft) == len(cache) and all(v == tuple(int(x != 0) for x in after[p][2:]) for p, v in zip(ft, cache))
        expected_record.pop("cache_encoding")
        expected_record["cache_matches_all_access_rows"] = binary_match
        expected_record["passed"] = binary_match and all(actual_response[k] for k in (
            "motor_access_codes_correct", "baseline_has_motor_flow", "closed_all_motor_flows_zero", "open_direction_has_motor_flow"))
    if expected_record != recorded:
        raise ValueError("真实车型流量与报告响应不一致")
    changes = {scope: {key: (metrics["oneway"][scope][key] / metrics["baseline"][scope][key] - 1) * 100
                       for key in ("running_time_pcu_h", "transport_pcu_km", "weighted_speed_kmh")}
               for scope in metrics["baseline"]}
    observation = None
    if (job / "od_stage_observations.json").exists():
        samples = read(job / "od_stage_observations.json")["samples"]
        observation = {}
        for name in {s["scenario"] for s in samples}:
            run = next(r for r in state["runs"] if r["name"] == name)
            module = next(m for m in run["execution"]["modules"] if m["module"] == assignment_module)
            subset = [s for s in samples if s["scenario"] == name]
            for sample in subset:
                if not datetime.fromisoformat(module["started"]) <= datetime.fromisoformat(sample["observed"]) <= datetime.fromisoformat(module["finished"]):
                    raise ValueError("OD阶段采样不在分配进程运行期间")
            values = [s["fraction_of_input"] for s in subset]
            complete = len(values) == 5 and all(math.isclose(v, w / 15, abs_tol=1e-8) for v, w in zip(values, (5, 4, 3, 2, 1)))
            observation[name] = {"fractions": values, "sum": sum(values), "complete_five_stage_sequence": complete}
    result = {"passed": True, "scenarios": 2, "module_executions": 14,
              "assignment_module": assignment_module,
              "oneway_effective": actual_response["passed"], "response": actual_response,
              "recorded_response": recorded,
              "changes_percent": changes,
              "od_observation": observation,
              "conclusion": "试验记录核验与禁行响应分别报告；即使关闭道路后核心流量减少，核心qL/v下降也不能单独证明系统收益"}
    save_json(job / "verification.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--require-effective", action="store_true", help="实际禁行未生效时退出1，用于红/绿诊断")
    args = parser.parse_args()
    result = verify(args.job.resolve())
    if args.require_effective and not result["oneway_effective"]:
        sys.exit(1)
