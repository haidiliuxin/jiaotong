"""Audit captured increments against raw snapshots and final GEOM-ordered flows."""
import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from traffic_agent.data import geometry, numeric, read_text, save_json


def verify(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    folder = job / "stage_observations"
    observed = json.loads((folder / "observations.json").read_text(encoding="utf-8"))
    if state["status"] != "completed" or not observed.get("finished"):
        raise ValueError("需要任务和只读采样均已结束")
    groups = defaultdict(list)
    for sample in observed["samples"]:
        groups[sample["scenario"], sample["mode"]].append(sample)
    if not groups:
        raise ValueError("没有分轮快照，不能通过核验")
    rows = []
    for (name, mode), samples in groups.items():
        run = next(r for r in state["runs"] if r["name"] == name)
        scenario = job / run["scenario"]
        module = next(m for m in run["execution"]["modules"] if m["module"] == "M6AssignVehicle1.exe")
        start, end = [datetime.fromisoformat(module[k]).timestamp() for k in ("started", "finished")]
        ft = [tuple(map(int, s.split())) for s in read_text(scenario / "cache/FT.dat").splitlines() if s.strip()]
        if len(set(ft)) != len(ft):
            raise ValueError("FT端点不能唯一匹配")
        pair = tuple(state["factorial_design"]["closed_direction"])
        index = ft.index(pair)
        geom = geometry(scenario)
        geom_index = next(r["index"] for r in geom if (r["from_node"], r["to_node"]) == pair)
        filename = {"car": "VolLinkCar.tsd", "taxi": "VolLinkTax.tsd"}[mode]
        final = numeric(scenario / "result" / filename)
        if len(final) != len(geom):
            raise ValueError("最终流量与GEOM维数不符")
        increments, weights = [], []
        for sample in samples:
            path = folder / sample["volume_snapshot"]
            if path.resolve().parent != folder.resolve():
                raise ValueError("快照路径越界")
            values = numeric(path)
            if len(values) != len(ft) or min(values) < 0 or sample["values"] != len(values):
                raise ValueError("快照维数或数值错误")
            if sample["closed_direction"] != list(pair) or sample["ft_index"] != index or values[index] != sample["target_stage_flow"]:
                raise ValueError("记录与原始快照目标不符")
            if not start <= sample["volume_mtime_ns"] / 1e9 <= end:
                raise ValueError("快照源时间不在本次分配模块内")
            stamp = datetime.fromisoformat(sample["observed"]).timestamp()
            if not start <= stamp <= end or sample["volume_mtime_ns"] / 1e9 > stamp:
                raise ValueError("观察时间不在本次分配模块内")
            weight = round(sample["car_od_fraction_at_observation"] * 15)
            if weight not in range(1, 6) or not math.isclose(sample["car_od_fraction_at_observation"], weight / 15, abs_tol=1e-6):
                raise ValueError("观察时OD权重不属于五次增量")
            weights.append(weight)
            increments.append(values)
        complete = weights == [5, 4, 3, 2, 1]
        total = sum(v[index] for v in increments)
        row = dict(scenario=name, mode=mode, sampled_weights_over_15=weights,
                   complete_five_increments=complete, ft_index=index, geom_index=geom_index,
                   sampled_target_flows=[v[index] for v in increments], sampled_target_sum=total,
                   final_target_flow=final[geom_index], residual_observed_before_final=any(v[index] > .01 for v in increments))
        if complete:
            # Reorder via endpoint pairs: cache index must never be assumed to equal GEOM index.
            summed = {edge: sum(v[i] for v in increments) for i, edge in enumerate(ft)}
            errors = [abs(summed[(r["from_node"], r["to_node"])] - final[r["index"]]) for r in geom]
            row.update(target_sum_matches_final_rounding=abs(total - final[geom_index]) <= .00501,
                       full_array_max_sum_difference=max(errors), full_array_difference_over_001=sum(e > .01001 for e in errors))
        rows.append(row)
    if not any(r["complete_five_increments"] for r in rows):
        raise ValueError("没有完整五轮采样，不生成累加诊断结论")
    report = dict(passed=True, meaning="快照及时间、端点索引复核通过；不表示禁行约束通过", rows=rows,
                  limits="OD与阻抗是观察时读取值，不是模型内部原子追踪；仅完整五阶段组可解释累加。有限大阻抗与残余的因果机制尚未证明。")
    save_json(folder / "verification.json", report)
    lines = ["# 单向残余流量的分轮只读采样", "", report["meaning"], "",
             "| 场景 | 车型 | 采样权重（分母15） | 目标分轮流量 | 分轮和 | 最终流量 | 完整采样 |",
             "| --- | --- | --- | --- | ---: | ---: | --- |"]
    for row in rows:
        lines.append(f"| {row['scenario']} | {row['mode']} | {row['sampled_weights_over_15']} | {row['sampled_target_flows']} | {row['sampled_target_sum']:.6f} | {row['final_target_flow']:.2f} | {'是' if row['complete_five_increments'] else '否'} |")
    lines += ["", "目标按每个场景FT定位，最终流量按GEOM端点重排。每份原始快照的目标值、维数、源修改时间与分配模块起止时间均独立复核。完整组还重算全路网分轮和与最终输出的差值，详见verification.json。", "",
              "这项证据把残余定位到分轮volume缓存阶段，不能归因为最终TSD单纯舍入；启动前漏掉的阶段不补造。记录中阻抗9,999,999,999与10,000,000,002的变化只用于提出排查线索，不构成内部阈值机制的证明。", "", report["limits"], ""]
    (folder / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.job), ensure_ascii=False, indent=2))
