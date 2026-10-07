"""Independently check a completed single-scenario ManageN diagnostic control."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, geometry, input_fingerprints, numeric, save_json
from traffic_agent.circulation import restrictions, read_turn_volumes
from traffic_agent.runner import DEFAULT_SOURCE, EXPERIMENTS, MODULES


def verify(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    if state["status"] != "completed":
        raise ValueError("只核验已完成的单场景控制；不改写历史失败")
    base_job = EXPERIMENTS / state["matched_baseline"]
    base_state = json.loads((base_job / "state.json").read_text(encoding="utf-8"))
    if base_state["status"] != "completed":
        raise ValueError("匹配基准未完成")
    base_record = next(r for r in base_state["runs"] if r["name"] == "baseline")
    base, after = base_job / base_record["scenario"], job / state["scenario"]
    source = json.loads((job / "source_inputs.json").read_text(encoding="utf-8"))
    if input_fingerprints(DEFAULT_SOURCE) != source or input_fingerprints(base) != source:
        raise ValueError("源输入或匹配基准输入改变")
    actual = input_fingerprints(after)
    if {k for k in actual.keys() | source.keys() if actual.get(k) != source.get(k)} != {"data/manageN.txt"}:
        raise ValueError("不止ManageN发生输入变化")
    for scenario, manifest in [(base, base_record["execution"]), (after, state["execution"])]:
        if manifest["status"] != "completed" or manifest["input_changes_during_run"]:
            raise ValueError("模型运行或输入一致性失败")
        if [m["module"] for m in manifest["modules"]] != list(MODULES):
            raise ValueError("模块链不完整")
        if any(m["status"] != "completed" or m["returncode"] != 0 for m in manifest["modules"]):
            raise ValueError("模块未全部正常完成")
        for relative, stamp in manifest["fresh_outputs"].items():
            stat = (scenario / relative).stat()
            if stat.st_size != stamp["bytes"] or stat.st_mtime_ns != stamp["mtime_ns"]:
                raise ValueError("保存输出与运行记录不一致")
    if json.loads((job / "logs/no_turn_rules/run.json").read_text(encoding="utf-8")) != state["execution"]:
        raise ValueError("模块日志与控制状态不一致")
    changed = {}
    for field, name in OUTPUTS.items():
        a, b = numeric(base / "result" / name), numeric(after / "result" / name)
        if len(a) != len(b) or len(a) != len(geometry(base)):
            raise ValueError("路段数组维数不一致")
        changed[field] = sum(x != y for x, y in zip(a, b))
    if changed != state["changed_link_entries"]:
        raise ValueError("输出变化计数不一致")
    before_rules, after_rules = restrictions(base / "data/manageN.txt"), restrictions(after / "data/manageN.txt")
    if "turn" in state:
        if after_rules != before_rules | {tuple(state["turn"])}:
            raise ValueError("不是单条新增禁转")
    elif after_rules:
        raise ValueError("控制并未清空规则")
    cache = list(map(int, (after / "cache/ManageNM.dat").read_text().split()))
    if len(cache) != 1 + 3 * cache[0] or {tuple(cache[i:i+3]) for i in range(1, len(cache), 3)} != after_rules:
        raise ValueError("禁转缓存不匹配")
    movement = None
    if "turn" in state:
        turn = tuple(state["turn"])
        old, new = read_turn_volumes(base / "result/TVolNodecar.tsd"), read_turn_volumes(after / "result/TVolNodecar.tsd")
        movement = {"turn": list(turn), "before": old.get(turn), "after": new.get(turn)}
    result = {"passed": True, "new_module_executions": 7, "reused_baseline_module_executions": 7,
              "only_changed_input": "data/manageN.txt", "changed_link_entries": changed,
              "movement": movement,
              "limits": "通过仅表示输入输出与实际执行证据一致；路段变化或零基准转向均不证明禁转正确生效"}
    save_json(job / "verification.json", result)
    return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    print(json.dumps(verify(parser.parse_args().job.resolve()), ensure_ascii=False, indent=2))
