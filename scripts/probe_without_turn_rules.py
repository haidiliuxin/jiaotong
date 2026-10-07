"""Diagnostic controls: remove all rules or add one entry at a specified node."""
import json
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, input_fingerprints, numeric, prepare, save_json
from traffic_agent.circulation import restrictions, read_turn_volumes, TurnGraph
from traffic_agent.runner import DEFAULT_SOURCE, EXPERIMENTS, execute, now, runtime_lock, setup_runtime


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--turn", nargs=3, type=int, help="指定时测试新增禁转；省略时移除全部规则")
    parser.add_argument("--new-node", action="store_true", help="要求转向的中间节点原来完全没有禁转，用于节点类型对照")
    args = parser.parse_args()
    if args.new_node and not args.turn:
        parser.error("--new-node必须同时提供--turn")
    base_job = EXPERIMENTS / "probes/20260908_043338_multipath_tmp_turn"
    base_state = json.loads((base_job / "state.json").read_text(encoding="utf-8"))
    if base_state["status"] != "completed":
        raise ValueError("匹配基准仍未完成")
    base_record = base_state["runs"][0]
    base = base_job / base_record["scenario"]
    source = json.loads((base_job / "source_inputs.json").read_text(encoding="utf-8"))
    suffix = "_new_node_turn" if args.new_node else "_new_entry_turn" if args.turn else "_no_turn_rules"
    graph = TurnGraph.load(base) if args.turn else None
    graph_check = graph.validate([args.turn]) if graph else None
    if args.turn and not graph_check["eligible"]:
        raise ValueError("诊断候选未通过公交保护与替代路径检查")
    original_node_rules = sum(t[1] == args.turn[1] for t in graph.banned) if graph else None
    if args.new_node and original_node_rules:
        raise ValueError("中间节点已有禁转，不能用于全新节点对照")
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + suffix)
    job.mkdir(parents=True, exist_ok=False)
    state = {"status": "created", "started": now(), "matched_baseline": str(base_job.relative_to(EXPERIMENTS)),
             "hypothesis": "如果已有ManageN参与当前分配，移除全部禁转应在至少一些道路/转向上产生可检测变化",
             "limits": "仅诊断，不是可推荐交通政策；使用已完成且输入相同的最近匹配基准"}
    if args.turn:
        state["hypothesis"] = "检测未被原规则限制的进口能否响应新增禁转，以区分同进口重复限制问题"
        state["turn"] = args.turn
        state["original_node_rules"] = original_node_rules
        state["graph_check"] = graph_check
    if args.new_node:
        state["hypothesis"] = "如果软件对首次出现禁转的节点作图转换、而已有节点追加规则不改变分配，则新节点对照应产生路段输出变化"
        state["limits"] += "；基准未管理节点转向表为零不能作为无车证据，本对照不凭0到0证明禁转有效"
    def event(kind, payload):
        with (job / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": now(), "event": kind, "data": payload}, ensure_ascii=False) + "\n")
    with runtime_lock(EXPERIMENTS):
        if input_fingerprints(DEFAULT_SOURCE) != source or input_fingerprints(base) != source:
            raise ValueError("基准或源输入发生变化")
        manifest = json.loads((base_job / "logs/baseline/run.json").read_text(encoding="utf-8"))
        for relative, stamp in manifest["fresh_outputs"].items():
            stat = (base / relative).stat()
            if stat.st_size != stamp["bytes"] or stat.st_mtime_ns != stamp["mtime_ns"]:
                raise ValueError("匹配基准输出已被改动")
        runtime = setup_runtime(EXPERIMENTS)
        scenario = job / "scenarios/no_turn_rules/基础路网"
        extra = ("cache/ManageNM.dat", "result/TVolNodecar.tsd")
        changes = [{"kind": "turn", "turn": args.turn}] if args.turn else []
        prepare(DEFAULT_SOURCE, scenario, changes, extra_outputs=extra)
        path = scenario / "data/manageN.txt"
        if not args.turn:
            lines = [line for line in path.read_bytes().splitlines(keepends=True) if line.strip()]
            lines[0] = re.sub(rb"\d+(?=[ \t\r\n]*$)", b"0", lines[0])
            path.write_bytes(b"".join(lines[:2]))
            if restrictions(path):
                raise ValueError("诊断禁转输入未清空")
        actual = input_fingerprints(scenario)
        if {k for k in actual.keys() | source.keys() if actual.get(k) != source.get(k)} != {"data/manageN.txt"}:
            raise ValueError("出现额外输入差异")
        save_json(job / "source_inputs.json", source)
        state.update(status="running", scenario=str(scenario.relative_to(job)), rules_before=len(restrictions(base / "data/manageN.txt")), rules_after=len(restrictions(path)))
        save_json(job / "state.json", state)
        try:
            rows, run = execute(scenario, runtime, job / "logs/no_turn_rules", 900, event, required_outputs=extra)
            if not args.turn and (scenario / "cache/ManageNM.dat").read_text().split() != ["0"]:
                raise ValueError("空禁转未进入缓存")
            changed = {key: sum(a != b for a, b in zip(numeric(base / "result" / name), numeric(scenario / "result" / name))) for key, name in OUTPUTS.items()}
            old, new = read_turn_volumes(base / "result/TVolNodecar.tsd"), read_turn_volumes(scenario / "result/TVolNodecar.tsd")
            pairs = set(TurnGraph.load(DEFAULT_SOURCE).edges)
            invalid = lambda mapping: [list(t) for t in mapping if t[:2] not in pairs or t[1:] not in pairs]
            state.update(status="completed", execution=run, changed_link_entries=changed,
                         changed_car_turn_entries=sum(old[k] != new[k] for k in old.keys() & new.keys()),
                         removed_turn_keys=len(old.keys() - new.keys()), added_turn_keys=len(new.keys() - old.keys()),
                         invalid_car_graph_turns_before=invalid(old), invalid_car_graph_turns_after=invalid(new),
                         original_forbidden_positive=sum(old.get(t, 0) > .01 for t in restrictions(base / "data/manageN.txt")))
            if args.turn:
                from traffic_agent.circulation_agent import turn_response
                from types import SimpleNamespace
                state["response"] = turn_response(SimpleNamespace(job=job), dict(base_record, scenario=str(base)),
                    {"scenario": str(scenario), "changes": changes})
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            state["finished"] = now()
            save_json(job / "state.json", state)
    print(json.dumps({k:v for k,v in state.items() if k != "execution" and not k.startswith("invalid_")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
