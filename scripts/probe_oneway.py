"""Run a fresh matched one-way access trial; never enable production from cache alone."""
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import prepare, input_fingerprints, results, save_json, scopes, scoped_metrics
from traffic_agent.oneway import OneWayNetwork, close_motor_direction, response, REQUIRED_OUTPUTS
from traffic_agent.runner import DEFAULT_SOURCE, EXPERIMENTS, runtime_lock, setup_runtime, validate_source, execute, now
from scripts.vehicle1_parameters import prepare_parameters, DERIVED_FILES, MODAL_OUTPUTS, UNSUPPORTED_TURN_OUTPUTS


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--vehicle1", action="store_true", help="仅诊断：已核对读取格式的635参数及随包分车型程序")
    args = parser.parse_args()
    assignment_module = "M6AssignVehicle1.exe" if args.vehicle1 else "M6AssignVehicle.exe"
    extra_outputs = REQUIRED_OUTPUTS + (MODAL_OUTPUTS if args.vehicle1 else ())
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + ("_vehicle1" if args.vehicle1 else "") + "_oneway")
    job.mkdir(parents=True, exist_ok=False)
    state = {"id": job.name, "status": "created", "started": now(), "runs": [],
             "assignment_module": assignment_module,
             "hypothesis": "ManageR1路段管理禁行码2可将一条方向所有机动车实际流量归零，同时保留反向通行",
             "limits": "独立单向组织验证，不修复ManageN禁转；公交原线路保护；不重新分配车道；未开放普通执行入口"}
    def persist():
        save_json(job / "state.json", state)
    def event(kind, payload):
        entry = {"time": now(), "event": kind, "data": payload}
        with (job / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    persist()
    print(f"PROBE {job}", flush=True)
    with runtime_lock(EXPERIMENTS):
        original = input_fingerprints(DEFAULT_SOURCE)
        save_json(job / "source_inputs.json", original)
        try:
            validate_source(DEFAULT_SOURCE)
            historical = EXPERIMENTS / "jobs/20260908_031854_677ea5/scenarios/baseline/基础路网"
            if input_fingerprints(historical) != original:
                raise ValueError("选点所用既有基准与原场景输入不一致")
            rows = results(historical)
            candidates = [r for r in rows if r["included"] and r["road_class"] in (2, 3)
                          and 0.1 <= r["length_km"] <= 1.5 and r["flow_pcu_h"] >= 500
                          and 1 < r["vc"] <= 4 and r["speed_kmh"] < 40 and r["parking"] in (3, 4)]
            candidates.sort(key=lambda r: (-r["flow_pcu_h"] * r["length_km"] / r["speed_kmh"], r["index"]))
            network = OneWayNetwork.load(DEFAULT_SOURCE)
            screening, selected = [], None
            for row in candidates[:200]:
                audit = network.validate([row["from_node"], row["to_node"]])
                screening.append({"road": row, "audit": audit})
                if audit["eligible"]:
                    selected = row
                    break
            save_json(job / "selection.json", {"selected_before_simulation": now(), "historical_baseline": str(historical),
                      "rule": "城市2/3类、停车3/4、0.1-1.5km、q>=500、1<VC<=4、v<40，按qL/v排序；最多200项，取首个通过全车型接入及公交保护的单方向",
                      "candidate_count": len(candidates), "screening": screening})
            if selected is None:
                state.update(status="no_eligible_candidate", finished=now())
                persist()
                print("No eligible candidate; no simulation launched", flush=True)
                return
            pair = [selected["from_node"], selected["to_node"]]
            state.update(status="running", closed_direction=pair, road_name=selected["name"])
            index = {(r["from_node"], r["to_node"]): r["index"] for r in rows}
            scope = scopes(rows, [index[tuple(pair)], index[tuple(pair[::-1])]], 1)
            save_json(job / "scopes.json", scope)
            persist()
            event("selected", {"closed_direction": pair, "name": selected["name"], "screened": len(screening)})
            runtime = setup_runtime(EXPERIMENTS)
            for name in ("baseline", "oneway"):
                scenario = job / "scenarios" / name / "基础路网"
                # Vehicle1 does not regenerate these matrices. Do not leave inherited
                # turn tables available for later analyses to mistake for fresh output.
                prepare(DEFAULT_SOURCE, scenario, [], extra_outputs=extra_outputs + (UNSUPPORTED_TURN_OUTPUTS if args.vehicle1 else ()))
                derived = prepare_parameters(scenario, runtime / "programs" / assignment_module) if args.vehicle1 else None
                if derived:
                    state["derived_configuration"] = derived
                changes = []
                if name == "oneway":
                    validation = OneWayNetwork.load(scenario).validate(pair)
                    if not validation["eligible"]:
                        raise ValueError("新副本单向图检查不通过")
                    close_motor_direction(scenario / "data/ManageR1.txt", pair)
                    changes = [{"kind": "oneway", "closed_direction": pair, "validation": validation}]
                actual = input_fingerprints(scenario)
                differences = {k for k in original.keys() | actual.keys() if original.get(k) != actual.get(k)}
                expected = ({"data/ManageR1.txt"} if changes else set()) | (DERIVED_FILES if args.vehicle1 else set())
                if differences != expected:
                    raise ValueError("单向试验出现额外输入差异")
                save_json(scenario.parent / "edits.json", {"changes": changes, "input_differences": sorted(differences),
                                                           "derived_configuration": derived})
                state["active_scenario"] = name
                persist()
                actual_rows, manifest = execute(scenario, runtime, job / "logs" / name, 900, event,
                                                required_outputs=extra_outputs, assignment_module=assignment_module)
                state["runs"].append({"name": name, "scenario": str(scenario.relative_to(job)),
                                      "changes": changes, "metrics": scoped_metrics(actual_rows, scope), "execution": manifest})
                persist()
            state["response"] = response(job / state["runs"][0]["scenario"], job / state["runs"][1]["scenario"], pair)
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            state["source_inputs_unchanged"] = input_fingerprints(DEFAULT_SOURCE) == original
            if not state["source_inputs_unchanged"]:
                state.update(status="failed", error="原始输入被修改")
            state["finished"] = now()
            state.pop("active_scenario", None)
            persist()
    print(json.dumps({"probe": str(job), "response": state.get("response")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
