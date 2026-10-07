"""Test a hard closure by removing one edge before native car routing, not after."""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import numeric, read_text, save_json
from traffic_agent.runner import EXPERIMENTS, now, runtime_lock, setup_runtime
from scripts.probe_node_cost import fingerprints


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    source = EXPERIMENTS / "probes/20260908_071807_capture_increment/captured_car_increment/基础路网"
    job = EXPERIMENTS / "probes" / (datetime.now().strftime("%Y%m%d_%H%M%S") + "_node_pruning")
    job.mkdir(parents=True, exist_ok=False)
    source_files = fingerprints(source)
    ft = [tuple(map(int,s.split())) for s in read_text(source / "cache/FT.dat").splitlines() if s.strip()]
    pair = (3237, 3236)
    removed = ft.index(pair)
    source_volume = numeric(source / "cache/volume_car.dat")
    nodes = [int(x) for x in numeric(source / "cache/rnode.dat")]
    counts = [int(x) for x in numeric(source / "cache/n_r.dat")]
    tv = [int(x) for x in numeric(source / "cache/tv.dat")]
    origins = [node for node, count in zip(nodes,counts[1:]) for _ in range(count)]
    if list(zip(origins,[nodes[i-1] for i in tv])) != ft:
        raise ValueError("原始内部路由图不能重建FT")
    state = dict(status="created", started=now(), source=str(source), closed_direction=list(pair), removed_ft_index=removed,
                 runs=[], limits="单轮单车型硬删边诊断；没有运行全流程或修改最终TSD。展开数组中的零是不存在边的表示，须结合原始求解、流量转移和后续全流程验证。")
    save_json(job / "source_files.json", source_files)
    save_json(job / "state.json", state)
    print(f"PROBE {job}", flush=True)
    active = None
    with runtime_lock(EXPERIMENTS):
        runtime = setup_runtime(EXPERIMENTS)
        config = runtime / "files/runinfo_vc6.txt"
        original_config = config.read_bytes()
        state["executable_sha256"] = hashlib.sha256((runtime / "programs/NodeAssignModel_car.exe").read_bytes()).hexdigest()
        try:
            state["status"] = "running"
            for name in ("control", "pruned", "control_repeat"):
                scenario = job / "scenarios" / name / "基础路网"
                shutil.copytree(source, scenario)
                kept = [i for i in range(len(ft)) if name != "pruned" or i != removed]
                if name == "pruned":
                    for relative in ("cache/FT.dat", "cache/tv.dat", "cache/Rwm_car.dat"):
                        lines = (scenario / relative).read_bytes().splitlines(keepends=True)
                        if len(lines) != len(ft):
                            raise ValueError("删边输入维数错误")
                        (scenario / relative).write_bytes(b"".join(lines[i] for i in kept))
                    path = scenario / "data/number.txt"
                    lines = path.read_bytes().splitlines(keepends=True)
                    if int(lines[0]) != len(ft) or int(lines[1]) != len(nodes):
                        raise ValueError("number.txt不是原边数和节点数")
                    lines[0] = f"{len(kept)}\r\n".encode("ascii")
                    path.write_bytes(b"".join(lines))
                    path = scenario / "cache/n_r.dat"
                    lines = path.read_bytes().splitlines(keepends=True)
                    origin = nodes.index(pair[0])
                    lines[origin+1] = f"{counts[origin+1]-1}\r\n".encode("ascii")
                    path.write_bytes(b"".join(lines))
                output = scenario / "cache/volume_car.dat"
                output.unlink()
                before = fingerprints(scenario)
                expected_changes = {"cache/volume_car.dat"} | ({"cache/FT.dat", "cache/tv.dat", "cache/Rwm_car.dat", "data/number.txt", "cache/n_r.dat"} if name == "pruned" else set())
                if {k for k in before.keys() | source_files.keys() if before.get(k) != source_files.get(k)} != expected_changes:
                    raise ValueError("删边派生输入文件超出预定集合")
                logs = job / "logs" / name
                logs.mkdir(parents=True)
                save_json(logs / "inputs_before.json", before)
                save_json(logs / "kept_original_indices.json", kept)
                record = dict(name=name, scenario=str(scenario.relative_to(job)), started=now(), status="running", input_edges=len(kept))
                state["runs"].append(record)
                config.write_bytes(("scenario_dir\r\n" + scenario.as_posix() + "\r\n").encode("gb18030"))
                stamp = time.time_ns()
                with (logs / "stdout.bin").open("wb") as out, (logs / "stderr.bin").open("wb") as err:
                    active = subprocess.Popen([str(runtime / "programs/NodeAssignModel_car.exe")], cwd=runtime / "programs", stdin=subprocess.DEVNULL,
                                              stdout=out, stderr=err, creationflags=subprocess.CREATE_NO_WINDOW)
                    record["pid"] = active.pid
                    save_json(job / "state.json", state)
                    record["returncode"] = active.wait(timeout=60)
                record["finished"] = now()
                text = "\n".join((logs / f"{s}.bin").read_bytes().decode("gb18030", errors="replace") for s in ("stdout", "stderr"))
                (logs / "output.txt").write_text(text, encoding="utf-8")
                if record["returncode"] or "[error]" in text.lower() or not output.exists() or output.stat().st_mtime_ns < stamp:
                    raise ValueError("原生求解失败或没有新输出")
                values = numeric(output)
                if len(values) != len(kept) or min(values) < 0:
                    raise ValueError("原生输出没有匹配缩小后的边集合")
                after = fingerprints(scenario)
                if {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)} != {"cache/volume_car.dat"}:
                    raise ValueError("原生程序改写了其他输入")
                expanded = [0.0] * len(ft)
                for index, value in zip(kept, values):
                    expanded[index] = value
                if name != "pruned" and expanded != source_volume:
                    raise ValueError("前后控制没有复现已捕获的完整数组")
                changed = [dict(from_node=ft[i][0], to_node=ft[i][1], before=a, after=b, delta=b-a)
                           for i,(a,b) in enumerate(zip(source_volume,expanded)) if a != b]
                changed.sort(key=lambda r: -abs(r["delta"]))
                # Independent node balances expose loss or redistribution; no benefit claim from a forced zero.
                balances = {node: [0.0,0.0] for node in nodes}
                for (u,v), a,b in zip(ft,source_volume,expanded):
                    balances[u][0] -= a; balances[v][0] += a
                    balances[u][1] -= b; balances[v][1] += b
                centroid_indices = [int(x)-1 for x in numeric(source / "cache/szn.dat")[1:]]
                centroids = {nodes[i] for i in centroid_indices}
                balance_delta = [dict(node=n,before=a,after=b,delta=b-a,centroid=n in centroids)
                                 for n,(a,b) in balances.items() if abs(a-b) > .00001]
                record.update(status="completed", raw_output_rows=len(values), target_in_solver_graph=removed in kept,
                    expanded_target_flow=expanded[removed], changed_edges=len(changed), total_link_flow_before=sum(source_volume),
                    total_link_flow_after=sum(expanded), max_noncentroid_balance_change=max(abs(a-b) for n,(a,b) in balances.items() if n not in centroids))
                save_json(logs / "files_after.json", after)
                save_json(logs / "changed_edges.json", changed)
                save_json(logs / "node_balance_changes.json", balance_delta)
                save_json(job / "state.json", state)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            state["status"] = "completed"
        except BaseException as exc:
            state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            if active is not None and active.poll() is None:
                subprocess.run(["taskkill", "/PID", str(active.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=30)
                active.wait(timeout=10)
            config.write_bytes(original_config)
            state.update(source_files_unchanged=fingerprints(source)==source_files, runtime_config_restored=config.read_bytes()==original_config, finished=now())
            save_json(job / "state.json", state)


if __name__ == "__main__":
    main()
