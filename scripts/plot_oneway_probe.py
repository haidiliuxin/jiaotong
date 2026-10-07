"""Export the trial direction, an endpoint detour and five separate flow responses."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.plot_experiment import plt, save
from traffic_agent.data import geometry, read_text


def plot(job):
    state = json.loads((job / "state.json").read_text(encoding="utf-8"))
    verification = json.loads((job / "verification.json").read_text(encoding="utf-8"))
    if state["status"] != "completed" or not verification["passed"]:
        raise ValueError("只绘制完整且已核验的试验")
    audit = json.loads((job / "selection.json").read_text(encoding="utf-8"))["screening"][-1]["audit"]
    pair = state["closed_direction"]
    check = next(c for c in audit["modal_boundary_checks"]["car"] if c["entry"][0] == c["exit"][0] == -1)
    detour = [p[1] for p in check["replacement"]["states"]]
    scenario = job / state["runs"][0]["scenario"]
    coords = {}
    for line in read_text(scenario / "data/coor.txt").splitlines():
        fields = line.split()
        if fields:
            coords[int(fields[0])] = tuple(map(float, fields[1:3]))
    nodes = set(detour) | set(pair)
    xs, ys = zip(*(coords[n] for n in nodes))
    margin = max(max(xs) - min(xs), max(ys) - min(ys)) * .3
    fig, axes = plt.subplots(1, 2, figsize=(13, 6), layout="constrained")
    ax = axes[0]
    for row in geometry(scenario):
        a, b = coords[row["from_node"]], coords[row["to_node"]]
        if all(min(xs) - margin <= p[0] <= max(xs) + margin and min(ys) - margin <= p[1] <= max(ys) + margin for p in (a, b)):
            ax.plot(*zip(a, b), color="#cfd8dc", linewidth=1, zorder=0)
    ax.plot(*zip(*(coords[n] for n in detour)), color="#178477", linewidth=3, label="端点绕行示例")
    for a, b in zip(detour, detour[1:]):
        ax.annotate("", coords[b], coords[a], arrowprops=dict(arrowstyle="->", color="#178477", lw=2))
    ax.annotate("", coords[pair[1]], coords[pair[0]], arrowprops=dict(arrowstyle="->", color="#c7463e", lw=3))
    for n in nodes:
        ax.scatter(*coords[n], s=20, color="#203d49", zorder=3)
        ax.annotate(str(n), coords[n], xytext=(5, 6), textcoords="offset points", fontsize=9)
    ax.plot([], [], color="#c7463e", linewidth=3, label=f"禁行 {pair[0]}→{pair[1]}；反向保留")
    ax.set_aspect("equal")
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.tick_params(labelsize=8)
    ax.set_title(f"红景路 {pair[0]}→{pair[1]} 禁行，反向保留\n绿色：端点绕行示例", fontsize=11)
    ax.set_xlabel("案例坐标；节点直连示意")
    ax = axes[1]
    modes = [("car", "小汽车"), ("taxi", "出租车"), ("motor", "摩托车"), ("truck", "货车"), ("bus", "公交车")]
    values = state["response"]["flows"]
    for offset, field, label, color in ((-.19, "before", "新基准", "#718d9b"), (.19, "after", "单向试验", "#178477")):
        bars = ax.bar([i + offset for i in range(5)], [values[m][field] for m, _ in modes], width=.36, label=label, color=color)
        ax.bar_label(bars, labels=[f"{values[m][field]:.2f}" for m, _ in modes], padding=3 if field == "before" else 16, fontsize=9)
    ax.set_xticks(range(5), [label for _, label in modes])
    ax.set_ylim(0, max(v["before"] for v in values.values()) * 1.2)
    ax.set_ylabel("对应VolLink文件原始车型流量（不跨车型合计）")
    ax.set_title("禁行方向：五类机动车实际输出")
    ax.legend()
    ax.grid(axis="y", alpha=.15)
    ax.set_axisbelow(True)
    effective = "通过" if verification["oneway_effective"] else "未通过"
    fig.suptitle(f"单向路权联动验证｜实际禁行响应{effective}", fontsize=16)
    fig.supxlabel("654条公交线路边保护；三类通行图均检查全部原边界及端点接入。图中只展示一条路径，禁行生效不等于治理收益。", fontsize=9)
    output = job / "figures"
    output.mkdir(exist_ok=True)
    save(fig, output, "单向组织与车型响应")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    plot(parser.parse_args().job.resolve())
