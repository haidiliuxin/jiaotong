"""Export a transparent fixed-vs-feedback comparison from a completed benchmark."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.sans-serif": ["Microsoft YaHei", "SimHei", "DejaVu Sans"],
                     "axes.unicode_minus": False, "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False})


def plot(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    checked = json.loads((job / "verification.json").read_text(encoding="utf-8"))
    if state["status"] != "completed" or not checked["passed"]:
        raise ValueError("只为已完成且核验过的策略实验绘制结论图")
    runs = {r["name"]: r for r in state["runs"]}
    factors = [c["factor"] for c in state["conditions"]]
    fig, axes = plt.subplots(1, 3, figsize=(13, 5), layout="constrained")
    colors = {"fixed": "#8b9da6", "adaptive": "#126682"}
    for ax, area in zip(axes, ["core", "neighbor", "network"]):
        for offset, strategy in [(-.19, "fixed"), (.19, "adaptive")]:
            values = []
            for condition in state["conditions"]:
                chosen = runs[condition["strategies"][strategy]["recommendation"]]
                value = chosen.get("comparison", {}).get("delta", {}).get(area, {}).get("running_time_change_pct", 0)
                values.append(value)
            bars = ax.bar([i+offset for i in range(len(factors))], values, width=.35,
                          label={"fixed": "固定强度搜索", "adaptive": "实测反馈搜索"}[strategy], color=colors[strategy])
            ax.bar_label(bars, labels=[f"{v:.3g}%" for v in values],
                         padding=4 if strategy == "fixed" else 16, fontsize=8)
        ax.set_xticks(range(len(factors)), [f"{f:.1f}" for f in factors])
        ax.axhline(0, color="#486373", linewidth=.8)
        ax.grid(axis="y", alpha=.15)
        ax.set_axisbelow(True)
        ax.set_xlabel("小汽车OD系数")
        ax.set_title({"core": "固定核心区", "neighbor": "固定邻区", "network": "全网"}[area])
        lo, hi = ax.get_ylim()
        padding = max((hi-lo)*.15, .01)
        ax.set_ylim(lo-padding, hi+padding)
    axes[0].set_ylabel("最终建议相对匹配基准的行驶时间变化 %")
    axes[0].legend(fontsize=8)
    fig.suptitle("同一首轮、每种策略最多2项候选｜负值表示改善", fontsize=16)
    fig.supxlabel("仅为冻结案例的三个离散需求点；不合格候选被拒绝，0可表示保留基准；非普遍算法优越性证明", fontsize=10)
    output = job / "figures"
    output.mkdir(exist_ok=True)
    for suffix in ["png", "svg"]:
        fig.savefig(output / f"等预算策略对比.{suffix}", dpi=170, bbox_inches="tight")
    plt.close(fig)
    data = []
    for condition in state["conditions"]:
        row = [f"{condition['factor']:.1f}"]
        for strategy in ["fixed", "adaptive"]:
            selection = condition["strategies"][strategy]
            row += [str(len(selection["candidates"])) + "/2", selection["recommendation"]]
        data.append(row)
    fig, ax = plt.subplots(figsize=(13, 2.8), layout="constrained")
    ax.axis("off")
    table = ax.table(cellText=data, colLabels=["小汽车需求", "固定策略次数/预算", "固定策略建议", "反馈策略次数/预算", "反馈策略建议"],
                     cellLoc="center", loc="center", colWidths=[.10, .16, .29, .16, .29])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.9)
    for (row, col), cell in table.get_celld().items():
        cell.set_edgecolor("#d4e0e6")
        cell.set_facecolor("#deebf1" if row == 0 else "#f5fafc" if row % 2 else "white")
    fig.suptitle(f"预算与最终取舍｜真实执行候选并集共{state['run_count']}个场景", fontsize=14)
    fig.supxlabel("同一候选共享结果，两种策略各计一次逻辑评估；各需求的无治理基准开销相同", fontsize=10)
    for suffix in ["png", "svg"]:
        fig.savefig(output / f"策略预算与推荐.{suffix}", dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    plot(parser.parse_args().job.resolve())
