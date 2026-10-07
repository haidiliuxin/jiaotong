"""Verify isolated native replay; optionally fail on a named forbidden-flow case."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, save_json
from scripts.probe_node_cost import fingerprints


def verify(job):
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    state = read(job / "state.json")
    source = Path(state["source"])
    saved = read(job / "source_files.json")
    if state["status"] != "completed" or not state["source_files_unchanged"] or not state["runtime_config_restored"] or fingerprints(source) != saved:
        raise ValueError("诊断未完成或输入/运行配置不一致")
    expected = numeric(source / "cache/volume_car.dat")
    original_costs = numeric(source / "cache/Rwm_car.dat")
    index = state["ft_index"]
    rows = []
    if len(state["runs"]) < 3 or state["runs"][0]["name"] != "control" or state["runs"][-1]["name"] != "control_repeat":
        raise ValueError("缺少前后相同输入控制")
    for run in state["runs"]:
        path = job / run["scenario"]
        logs = job / "logs" / run["name"]
        before, after = read(logs / "inputs_before.json"), read(logs / "files_after.json")
        actual = fingerprints(path)
        if actual != after or {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)} != {"cache/volume_car.dat"}:
            raise ValueError("原始输出或单模块输入被修改")
        costs = numeric(path / "cache/Rwm_car.dat")
        control = run["name"] in ("control", "control_repeat")
        differences = {k for k in before.keys() | saved.keys() if before.get(k) != saved.get(k)}
        if differences != set(run["input_difference_files"]) or differences != {"cache/volume_car.dat"} | (set() if control else {"cache/Rwm_car.dat"}):
            raise ValueError("输入文件改动超出单变量条件")
        if len(costs) != len(original_costs) or costs[index] != run["cost"] or any(a != b for i,(a,b) in enumerate(zip(costs,original_costs)) if i != index):
            raise ValueError("不止目标路段阻抗改变")
        values = numeric(path / "cache/volume_car.dat")
        if len(values) != len(expected) or min(values) < 0 or (control and values != expected):
            raise ValueError("输出维数错误或控制未复现")
        digest = hashlib.sha256((path / "cache/volume_car.dat").read_bytes()).hexdigest()
        if digest != run["output_sha256"] or values[index] != run["target_flow"] or (values[index] <= .01) != run["forbidden_flow_passed"]:
            raise ValueError("保存的禁行响应不是原始输出")
        if run["status"] != "completed" or run["returncode"] or any(b"[error]" in (logs / f"{s}.bin").read_bytes().lower() for s in ("stdout", "stderr")):
            raise ValueError("模块错误")
        rows.append(dict(name=run["name"], cost=run["cost"], target_flow=values[index], forbidden_flow_passed=values[index] <= .01,
                         changed_vs_control=sum(a!=b for a,b in zip(values,expected))))
    result = dict(passed=True, rows=rows, limits="只核验冻结输入单轮重放及单条阻抗变化；不证明完整分配或治理有效")
    save_json(job / "verification.json", result)
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--require-effective", help="指定一个场景名，其原始目标流量必须不超过0.01，否则退出1")
    args = parser.parse_args()
    result = verify(args.job)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_effective:
        selected = next(r for r in result["rows"] if r["name"] == args.require_effective)
        if not selected["forbidden_flow_passed"]:
            raise SystemExit(1)
