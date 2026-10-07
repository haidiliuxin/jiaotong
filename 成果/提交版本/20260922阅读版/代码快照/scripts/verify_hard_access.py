"""Verify native filtered graphs, retained outputs, five-round sums and controls."""
import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import OUTPUTS, geometry, input_fingerprints, numeric, read_text, save_json
from traffic_agent.oneway import FLOW_FILES, access_rows, response


def verify(job):
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    state = read(job / "state.json")
    source = Path(state["source"])
    if state["status"] != "completed" or not state["source_inputs_unchanged"] or input_fingerprints(source) != read(job / "source_inputs.json"):
        raise ValueError("任务未完成或源输入变化")
    if [r["name"] for r in state["runs"]] != ["baseline", "oneway", "oneway_repeat"]:
        raise ValueError("缺少配对或策略复跑")
    pair = tuple(state["closed_direction"])
    summaries = []
    base = job / state["runs"][0]["scenario"]
    nine = list(OUTPUTS.values()) + list(FLOW_FILES.values())
    for run in state["runs"]:
        scenario = job / run["scenario"]
        manifest = read(job / "logs" / run["name"] / "run.json")
        if manifest != run["execution"] or manifest["status"] != "completed" or len(manifest["modules"]) != 7 or any(m["returncode"] for m in manifest["modules"]):
            raise ValueError("完整模型执行记录不符")
        if input_fingerprints(scenario) != read(job / "logs" / run["name"] / "inputs_before.json"):
            raise ValueError("场景输入改变")
        for name, stamp in manifest["fresh_outputs"].items():
            p = scenario / name
            if p.stat().st_size != stamp["bytes"] or p.stat().st_mtime_ns != stamp["mtime_ns"] or p.stat().st_mtime < datetime.fromisoformat(manifest["started"]).timestamp()-2:
                raise ValueError("输出不是保存的本次新输出")
        expected_rights = access_rows(source / "data/ManageR1.txt")
        if run["name"] != "baseline":
            changed = list(expected_rights[pair])
            for col in (3,4,6,7):
                if changed[col] != 0:
                    raise ValueError("原方向不可四车型通行")
                changed[col] = 2
            expected_rights[pair] = tuple(changed)
        if access_rows(scenario / "data/ManageR1.txt") != expected_rights:
            raise ValueError("外层路权不符合单方向策略")
        ft = [tuple(map(int,s.split())) for s in read_text(scenario / "cache/FT.dat").splitlines() if s.strip()]
        nodes = [int(x) for x in numeric(scenario / "cache/rnode.dat")]
        target_index = ft.index(pair)
        expected_indices = [i for i in range(len(ft)) if run["name"] == "baseline" or i != target_index]
        files = sorted((job / "bridge_runs" / run["name"]).glob("*/manifest.json"))
        if len(files) != 20:
            raise ValueError("不是20个车型分轮调用")
        grouped = {mode: [] for mode in ("car", "taxi", "motor", "truck")}
        for file in files:
            b = read(file); mode = b["mode"]; folder = file.parent; scratch = folder / "scenario"
            if b["status"] != "completed" or b["returncode"] or b["native_executable_sha256"] != state["native_executable_hashes"][mode]:
                raise ValueError("原生子进程失败或版本错误")
            kept = read(folder / "kept_original_indices.json")
            if kept != expected_indices or b["input_rows"] != len(ft) or b["native_rows"] != len(kept):
                raise ValueError("删除了额外路由边")
            small_ft = [tuple(map(int,s.split())) for s in read_text(scratch / "cache/FT.dat").splitlines() if s.strip()]
            counts = [int(v) for v in numeric(scratch / "cache/n_r.dat")]
            tv = [int(v) for v in numeric(scratch / "cache/tv.dat")]
            origins = [n for n,c in zip(nodes,counts[1:]) for _ in range(c)]
            if small_ft != [ft[i] for i in kept] or list(zip(origins,[nodes[v-1] for v in tv])) != small_ft:
                raise ValueError("实际原生图未体现新增闭边")
            number = numeric(scratch / "data/number.txt")
            if number[:2] != [len(kept),len(nodes)] or len(numeric(scratch / f"cache/Rwm_{mode}.dat")) != len(kept):
                raise ValueError("原生图参数维数不符")
            before = read(folder / "native_inputs.json")
            if any(hashlib.sha256((scratch / p).read_bytes()).hexdigest()!=h for p,h in before.items()):
                raise ValueError("原生输入快照被修改")
            raw = scratch / f"cache/volume_{mode}.dat"
            if hashlib.sha256(raw.read_bytes()).hexdigest() != b["native_output_sha256"]:
                raise ValueError("原生输出被修改")
            values = numeric(raw)
            if len(values) != len(kept) or min(values) < 0:
                raise ValueError("原生流量维数或数值错误")
            full = [0.0] * len(ft)
            for i,value in zip(kept,values):
                full[i] = value
            expanded = folder / "expanded_volume.dat"
            if full != numeric(expanded) or hashlib.sha256(expanded.read_bytes()).hexdigest() != b["expanded_output_sha256"]:
                raise ValueError("完整边序流量不等于原生输出的端点映射")
            if run["name"] != "baseline" and full[target_index] != 0:
                raise ValueError("禁行方向存在分轮流量")
            od_values = numeric(scratch / f"cache/Assign_{mode}.od")
            assigned = sum(od_values)
            if assigned != b["assigned_od_sum"]:
                raise ValueError("分轮OD记录不符")
            grouped[mode].append((b["started"], assigned, full, od_values))
        geometry_rows = geometry(scenario)
        index = {pair:i for i,pair in enumerate(ft)}
        mode_summaries = {}
        for mode, samples in grouped.items():
            samples.sort()
            if len(samples) != 5:
                raise ValueError("车型不是完整五轮")
            original_od = {"car":"ODCar.tsd", "taxi":"ODTaxi.tsd", "motor":"ODMotor.tsd", "truck":"ODTruck.tsd"}[mode]
            demand = numeric(scenario / "result" / original_od)
            # Native Assign_*.od serializes every cell with six decimal places.
            # Check every cell rather than applying an arbitrary tolerance to the sum.
            if any(sample[3] != [float(f"{v*weight/15:.6f}") for v in demand]
                   for sample,weight in zip(samples,(5,4,3,2,1))):
                raise ValueError("某个OD单元不是原需求的六位小数增量")
            od_sum_error = max(abs(sum(s[3][i] for s in samples)-v) for i,v in enumerate(demand))
            if od_sum_error > 1e-9:
                raise ValueError("五轮矩阵逐单元累加未还原完整输入OD")
            summed = [sum(sample[2][i] for sample in samples) for i in range(len(ft))]
            final = numeric(scenario / "result" / FLOW_FILES[mode])
            differences = [abs(summed[index[(r['from_node'],r['to_node'])]] - final[r['index']]) for r in geometry_rows]
            if max(differences) > .00501:
                raise ValueError("最终车型流量不等于五轮原生输出的累加")
            mode_summaries[mode] = dict(native_calls=5, od_weights_over_15=[5,4,3,2,1], od_cells_verified=len(demand),
                                        max_cell_od_sum_difference=od_sum_error, max_sum_difference=max(differences),
                                        target_stage_flows=[s[2][target_index] for s in samples])
        if run["name"] == "baseline":
            if not all(numeric(source / "result" / f)==numeric(scenario / "result" / f) for f in nine):
                raise ValueError("无新增闭边不能复现原程序九类数组")
        else:
            actual = response(base, scenario, pair)
            if actual != run["response"] or not actual["passed"]:
                raise ValueError("完整实际禁行响应不通过")
        if run["name"] == "oneway_repeat":
            previous = job / state["runs"][1]["scenario"]
            if not all(numeric(previous / "result" / f)==numeric(scenario / "result" / f) for f in nine):
                raise ValueError("策略复跑不一致")
        summaries.append(dict(name=run["name"], modes=mode_summaries))
    result = dict(passed=True, scenarios=3, top_level_calls=21, native_assignment_calls=60, runs=summaries,
        limits="只验证显式新增闭边与原生输出映射、分轮OD和九数组对照；保留的原有禁转/路权及全部OD路径合法性尚未证明。删除前完整分轮Rwm只保存摘要，当前保留边原值依据适配代码及原生输入快照，后续应补充可逆重建证据。")
    save_json(job / "verification.json", result)
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    print(json.dumps(verify(parser.parse_args().job), ensure_ascii=False, indent=2))
