"""Create exportable figures from one completed real agent experiment."""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.cm import ScalarMappable

from traffic_agent.data import read_text, results

plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                     "axes.unicode_minus": False, "font.size": 10, "axes.spines.top": False,
                     "axes.spines.right": False, "figure.facecolor": "white", "savefig.facecolor": "white"})


def save(fig, output, name):
    for ext in ("png", "svg"):
        fig.savefig(output / f"{name}.{ext}", dpi=170, bbox_inches="tight")
    plt.close(fig)


def plot(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    if state["status"] != "completed":
        raise ValueError("图表只接受已完成实验，不把运行中结果作最终成果")
    if state["task"]["scenario"] == "oneway":
        from scripts.plot_oneway_experiment import plot as plot_oneway
        return plot_oneway(job)
    if state.get('turn_access_profile'):
        from scripts.plot_turn_experiment import plot as plot_turn
        return plot_turn(job)
    scope = json.loads((job / "scopes.json").read_text(encoding="utf-8"))
    output = job / "figures"
    output.mkdir(exist_ok=True)
    policy = [r for r in state["runs"] if r["name"].startswith("parking_") or r["name"] in ("S1_parking", "S2_turn", "S3_combined")]
    baseline = next(r for r in state["runs"] if r["name"] == "baseline")
    winner = next(r for r in state["runs"] if r["name"] == state["recommendation"])
    main = [baseline] + policy
    colors = ["#708a99", "#3099aa", "#126682", "#b6672c"]
    fig, axes = plt.subplots(2, 3, figsize=(13, 7), layout="constrained")
    for column, area in enumerate(("core", "neighbor", "network")):
        for row, (metric, label) in enumerate((("weighted_speed_kmh", "加权速度（km/h）"), ("running_time_pcu_h", "路段行驶时间消耗（pcu·h）"))):
            ax = axes[row, column]
            values = [r["metrics"][area][metric] for r in main]
            bars = ax.bar(range(len(main)), values, color=colors[:len(main)], width=.58)
            ax.set_xticks(range(len(main)), [r["name"] for r in main], rotation=15)
            ax.set_title({"core": "固定核心区", "neighbor": "固定邻区", "network": "全网"}[area] + " · " + label)
            ax.set_ylim(0, max(values) * 1.2)
            ax.bar_label(bars, labels=[f"{v:.2f}" for v in values], fontsize=9, padding=3)
            ax.grid(axis="y", alpha=.15)
            ax.set_axisbelow(True)
    title = "停车、禁转与组合实验" if state["task"]["scenario"] == "circulation" else "停车治理与反馈实验"
    fig.suptitle(title + "｜核心、邻区和全网分别评价", fontsize=16)
    fig.supxlabel("基准需求 · 1小时窗口 · 5次多路径增量加载 · 赛事调整案例，非实测", fontsize=10)
    save(fig, output, "方案指标对比")

    # The same normalized color scale is used on both network maps.
    base_path, win_path = job / baseline["scenario"], job / winner["scenario"]
    base_rows, win_rows = results(base_path), results(win_path)
    coords = {}
    for line in read_text(base_path / "data/coor.txt").splitlines():
        f = line.split()
        if f:
            coords[int(f[0])] = (float(f[1]), float(f[2]))
    chosen = scope["core"] + scope["neighbor"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained")
    norm = Normalize(vmin=0, vmax=60)
    cmap = plt.get_cmap("viridis")
    for ax, rows, name in zip(axes, (base_rows, win_rows), ("新基准", "推荐方案 " + winner["name"])):
        segments = []
        speeds = []
        for i in chosen:
            r = rows[i]
            a, b = coords[r["from_node"]], coords[r["to_node"]]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            off = (-dy / length * 13, dx / length * 13) if length else (0, 0)
            points = [(p[0] + off[0], p[1] + off[1]) for p in (a, b)]
            segments.append(points)
            speeds.append(r["speed_kmh"])
            if i in scope["core"]:
                ax.plot(*zip(*points), color="#dc7b32", linewidth=7, zorder=1)
                midpoint = tuple((x + y) / 2 for x, y in zip(*points))
                ax.annotate(f"{r['from_node']}→{r['to_node']}", midpoint, xytext=(6, 8 if dx > 0 else -16),
                            textcoords="offset points", fontsize=8, color="#183747",
                            bbox=dict(facecolor="white", edgecolor="none", alpha=.7, pad=1))
        lc = LineCollection(segments, cmap=cmap, norm=norm, linewidths=2.3, zorder=2)
        lc.set_array(speeds)
        ax.add_collection(lc)
        ax.autoscale()
        ax.margins(.12)
        ax.set_aspect("equal")
        ax.set_title(name)
        ax.set_xlabel("案例X坐标（原始单位）")
        ax.ticklabel_format(style="plain", useOffset=False)
        ax.tick_params(labelsize=8)
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, label="路段速度 km/h", shrink=.7)
    fig.suptitle("研究范围与基准 / 治理速度｜橙色描边为固定核心路段", fontsize=15)
    fig.supxlabel("节点直连拓扑示意，非道路真实曲线；双向连线轻微错开显示；统一色标", fontsize=10)
    save(fig, output, "区域运行示意")

    if state.get("factorial_design"):
        diagnosis = json.loads((job / "diagnosis.json").read_text(encoding="utf-8"))
        audit = next(a for a in diagnosis["attempts"] if a["eligible"])["graph_check"]
        check = audit["turns"][0]
        turn, detour = check["turn"], check["replacement"]
        fig, ax = plt.subplots(figsize=(9, 7), layout="constrained")
        nodes = set(detour["nodes"])
        for r in base_rows:
            if r["included"] and (r["from_node"] in nodes or r["to_node"] in nodes):
                a, b = coords[r["from_node"]], coords[r["to_node"]]
                ax.plot([a[0], b[0]], [a[1], b[1]], color="#d0dce1", linewidth=1, zorder=0)
        for a, b in zip(detour["nodes"][1:], detour["nodes"][2:]):
            ax.annotate("", xy=coords[b], xytext=coords[a], arrowprops=dict(arrowstyle="->", color="#126682", lw=2.5, shrinkA=6, shrinkB=6))
        for a, b in zip(turn, turn[1:]):
            ax.plot(*zip(coords[a], coords[b]), color="#dc7b32", linestyle="--", linewidth=2, zorder=1)
        for n in nodes:
            x, y = coords[n]
            ax.scatter(x, y, s=24, color="#183747", zorder=3)
            ax.annotate(str(n), (x, y), xytext=(5, 7), textcoords="offset points", fontsize=10)
        ax.set_aspect("equal")
        ax.ticklabel_format(style="plain", useOffset=False)
        ax.set_title("候选禁转与合法替代路径（拓扑检查）\n禁转 " + "→".join(map(str, turn)), fontsize=15)
        ax.set_xlabel("案例X坐标（原始单位）")
        ax.set_ylabel("案例Y坐标（原始单位）")
        fig.supxlabel(f"橙虚线：受限转向的进出路段；蓝箭头：进入节点后的替代路径\n额外 {detour['extra_km']:.3f} km，距离 {detour['ratio']:.3f} 倍；保持公交线路转向；非实施推荐", fontsize=10)
        save(fig, output, "禁转替代路径")

    if state["sensitivity"]:
        points = [{"factor": 1.0, "comparison": winner.get("comparison")}]
        points += [{"factor": x["car_demand_factor"], "comparison": x["comparison"]} for x in state["sensitivity"]]
        points.sort(key=lambda x: x["factor"])
        fig, axes = plt.subplots(1, 3, figsize=(13, 4), layout="constrained")
        for ax, area in zip(axes, ("core", "neighbor", "network")):
            y = [p["comparison"]["delta"][area]["running_time_change_pct"] if p["comparison"] else 0 for p in points]
            x = [p["factor"] for p in points]
            ax.plot(x, y, marker="o", color="#126682", linewidth=2, label="原推荐措施")
            adapted = []
            for p in points:
                item = next((s for s in state["sensitivity"] if s["car_demand_factor"] == p["factor"]), None)
                adjustment = item.get("adaptation") if item else None
                if adjustment:
                    value = adjustment.get("comparison", {}).get("delta", {}).get(area, {}).get("running_time_change_pct", 0)
                    if adjustment["recommendation"] == item["matched_baseline"]:
                        value = 0
                    adapted.append(value)
                else:
                    adapted.append(y[len(adapted)])
            if any(s.get("adaptation") for s in state["sensitivity"]):
                ax.plot(x, adapted, marker="s", linestyle="--", color="#b6672c", linewidth=1.5, label="反馈后的条件建议")
                if area == "core":
                    ax.legend(fontsize=8)
            ax.axhline(0, color="#708a99", linewidth=1)
            ax.set_xticks(x)
            ax.set_xlabel("小汽车OD系数")
            ax.set_title({"core": "固定核心区", "neighbor": "固定邻区", "network": "全网"}[area])
            ax.set_ylabel("相对匹配基准的行驶时间变化 %")
            ax.grid(alpha=.2)
        fig.suptitle("推荐措施的需求敏感性｜每个系数与自身无治理基准比较", fontsize=14)
        save(fig, output, "需求敏感性")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    plot(parser.parse_args().job.resolve())
