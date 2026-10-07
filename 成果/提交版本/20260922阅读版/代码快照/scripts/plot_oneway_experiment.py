"""Publication figures for a completed, independently verified full one-way grid."""
import json
from scripts.plot_experiment import plt, save


def plot(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    verification = json.loads((job / "verification.json").read_text(encoding="utf-8"))
    if state["status"] != "completed" or not state["factorial_design"]["full_demand_grid"] or not verification["passed"]:
        raise ValueError("需要已完成并核验的完整单向组合")
    factors = sorted(state["task"]["demand_factors"])
    variants = [("S1_parking", "S1 停车", "#15818c"), ("S2_oneway", "S2 单向", "#c77934"), ("S3_combined", "S3 组合", "#6b5d98")]
    fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
    for ax, area, key, title in zip(axes.flat, ("core", "neighbor", "network", None),
            ("running_time_change_pct", "running_time_change_pct", "running_time_change_pct", None),
            ("固定双向核心：qL/v变化（%）", "固定邻区：qL/v变化（%）", "全网：qL/v变化（%）", "邻区新增过饱和有向里程（km）")):
        for label, name, color in variants:
            rows = [next(r for r in state["runs"] if r.get("policy_label") == label and r["demand_factor"] == f) for f in factors]
            values = [r["comparison"]["delta"][area][key] if area else r["comparison"]["new_neighbor_oversaturated_km"] for r in rows]
            ax.plot(factors, values, marker="o", label=name, color=color)
            rejected = [(f, v) for f, v, r in zip(factors, values, rows) if not r["comparison"]["eligible"]]
            if rejected:
                ax.scatter(*zip(*rejected), marker="x", s=75, color=color, zorder=3)
        limit = state["task"]["max_neighbor_time_increase_pct"] if area == "neighbor" else state["task"]["max_new_oversaturated_km"] if area is None else 0
        ax.axhline(limit, linestyle="--", color="#8c9aa1", linewidth=1)
        ax.set_title(title)
        ax.set_xticks(factors)
        ax.set_xlabel("小汽车OD系数")
        ax.grid(alpha=.15)
    axes[0, 0].legend()
    fig.suptitle((state["task"].get("road_name") or "新基准选定道路") + "｜停车、单向和组合的完整需求对照", fontsize=15)
    fig.supxlabel("所有变化均相对同系数新基准；×表示该候选未通过全部约束。Vehicle1固定5次增量；核心收益须结合全网及邻区。", fontsize=9)
    output = job / "figures"
    output.mkdir(exist_ok=True)
    save(fig, output, "单向停车需求组合")
    fig, ax = plt.subplots(figsize=(11, 3.8), layout="constrained")
    ax.axis("off")
    cells = []
    for factor in factors:
        baseline = next(r for r in state["runs"] if r["demand_factor"] == factor and not r["changes"])
        name = state["recommendation_by_car_demand"][str(float(factor))]
        chosen = next(r for r in state["runs"] if r["name"] == name)
        gain = (chosen["metrics"]["network"]["running_time_pcu_h"] / baseline["metrics"]["network"]["running_time_pcu_h"] - 1) * 100
        eligible = [r.get("policy_label") for r in state["runs"] if r["demand_factor"] == factor and r.get("comparison", {}).get("eligible")]
        cells.append([f"{factor:.1f}", "、".join(eligible) or "无", chosen.get("policy_label", "S0 基准"), f"{gain:+.5f}%"])
    table = ax.table(cellText=cells, colLabels=["小汽车OD系数", "合格候选", "条件建议", "全网qL/v变化"],
                     colWidths=[.13, .4, .25, .22], cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2.1)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d6e0e5")
        cell.set_facecolor("#eaf2f5" if row == 0 else "white")
    ax.set_title("同需求内的候选筛除与条件建议", fontsize=15)
    fig.supxlabel("候选全部在同一新基准配置中计算；回退基准不代表治理获得收益。结果只覆盖已测离散需求点。", fontsize=9)
    save(fig, output, "单向组合条件推荐")
