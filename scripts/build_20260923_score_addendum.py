"""Publish a dated evidence PDF after both v4 extension archives pass audit.

This is a read-only exporter. It never starts or changes a traffic experiment.
It refuses absent, incomplete or mismatched native-run evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_COMMIT = "5c9332494c08da354dc05a1868a9495ceb54d2a7"


def raw(path: Path) -> bytes:
    return path.read_bytes()


def digest(path: Path) -> str:
    return hashlib.sha256(raw(path)).hexdigest()


def read(path: Path) -> dict:
    return json.loads(raw(path))


def checked_extension(label: str, audit_path: Path, compare_path: Path, nested: int):
    audit = read(audit_path)
    compare = read(compare_path)
    if audit.get("status") != "passed" or audit.get("extension") != label:
        raise ValueError(f"{label}: extension audit is incomplete")
    if audit.get("code_commit") != RUNTIME_COMMIT:
        raise ValueError(f"{label}: unexpected runtime commit")
    if not audit.get("source_inputs_unchanged") or not audit["final_integrity"]["passed"]:
        raise ValueError(f"{label}: source/runtime integrity failed")
    if not audit["verification"]["passed"] or audit["verification"]["completed_scenarios"] != 13:
        raise ValueError(f"{label}: incomplete scenario audit")
    if audit["verification"]["module_executions"] != 91 or audit["observed_nested_native_calls"] != nested:
        raise ValueError(f"{label}: unexpected native module count")
    if not all(audit["verification"]["repeat_arrays_exact"].values()):
        raise ValueError(f"{label}: baseline repeat differs")
    if compare.get("status") != "compared" or not compare.get("verification_passed"):
        raise ValueError(f"{label}: historical comparison is not complete")
    if compare.get("completed_scenarios") != 13 or compare.get("compared_arrays") != 117:
        raise ValueError(f"{label}: incomplete historical array comparison")
    if not compare.get("all_arrays_numerically_exact") or not compare.get("scopes_identical"):
        raise ValueError(f"{label}: historical inputs or arrays differ; investigate before publishing")
    if compare.get("right_archive_sha256") != audit["job_archive_sha256"]:
        raise ValueError(f"{label}: comparison references another archive")
    if any(not row.get("inputs_identical") for row in compare["records"]):
        raise ValueError(f"{label}: scenario inputs differ")
    archive = ROOT / "智能体实验" / "portable_runs" / audit["job_id"]
    state_path = archive / "state.json"
    if digest(state_path) != compare["right_state_sha256"]:
        raise ValueError(f"{label}: current archive state is not compared state")
    state = read(state_path)
    if state.get("status") != "completed" or len(state["runs"]) != 13:
        raise ValueError(f"{label}: copied archive incomplete")
    copied = read(audit_path.parent.parent / "copied_archive_verification.json")
    if not copied.get("passed") or copied["completed_scenarios"] != 13:
        raise ValueError(f"{label}: copied archive not independently verified")
    return audit, compare, state, archive


def change(before: dict, after: dict, scope: str) -> float:
    a = before["metrics"][scope]["running_time_pcu_h"]
    b = after["metrics"][scope]["running_time_pcu_h"]
    return 100 * (b / a - 1)


def recommendation_rows(state: dict, old: dict, new: dict):
    runs = {r["name"]: r for r in state["runs"]}
    rows = []
    for factor in (0.9, 1.0, 1.1):
        key = str(factor)
        grid = next(x for x in state["demand_grid"] if x["factor"] == factor)
        baseline = runs[grid["baseline"]]
        picked = runs[new[key]]
        rows.append((factor, old[key], new[key], change(baseline, picked, "core"),
                     change(baseline, picked, "neighbor"), change(baseline, picked, "network")))
    return rows


def validate_other_evidence():
    challenge_path = ROOT / "成果/回退挑战/20260922_034228_3141eb/证据摘要.json"
    prospective_path = ROOT / "成果/前瞻验证/20260922_015104_1bd73b/证据摘要.json"
    video_path = ROOT / "成果/演示/20260922_v4_completed_review/recording.json"
    review_path = video_path.parent / "visual_review.json"
    challenge = read(challenge_path)
    prospective = read(prospective_path)
    video = read(video_path)
    review = read(review_path)
    cv = challenge["verification"]
    pv = prospective["verification"]
    if not (cv["passed"] and cv["physical_scenes"] == 37 and cv["native_module_calls"] == 259):
        raise ValueError("challenge evidence mismatch")
    if not (pv["passed"] and pv["physical_scenes"] == 22 and pv["native_module_calls"] == 154):
        raise ValueError("prospective evidence mismatch")
    if video["status"] != "completed" or video["new_model_calls"] != 0:
        raise ValueError("video evidence mismatch")
    if review["status"] != "passed" or review["duration_seconds"] != 124:
        raise ValueError("video visual review incomplete")
    if review["video_sha256"] != video["video"]["sha256"]:
        raise ValueError("visual review references another video")
    for facts, path in ((challenge, ROOT / "智能体实验/challenges" / challenge["job"]),
                        (prospective, ROOT / "智能体实验/studies" / prospective["job"])):
        for name in ("state", "protocol", "selection"):
            if digest(path / f"{name}.json") != facts["verification"][f"{name}_sha256"]:
                raise ValueError(f"stale {facts['job']} {name}")
    return (challenge_path, challenge), (prospective_path, prospective), (video_path, video), (review_path, review)


def checked_turn_risk(compare: dict):
    path = ROOT / "成果/复评/20260919_v2/review.json"
    report = read(path)
    job = next(x for x in report["jobs"] if x["job"] == "20260908_100153_bb7198")
    if job["source_state_sha256"] != compare["left_state_sha256"] or not job["audit"]["passed"]:
        raise ValueError("high-demand risk review refers to another historical task")
    warnings = {x["run"]: x for x in job["warnings"]}
    names = ("demand_1p100_S2_turn", "demand_1p100_S3_combined")
    gross = [warnings[name]["new_network_overloaded_km"] for name in names]
    net = [warnings[name]["original_delta"]["network"]["oversaturated_km_change"] for name in names]
    if any(x <= 0 for x in gross) or max(gross) - min(gross) > 1e-6:
        raise ValueError("high-demand gross risk inconsistent")
    return path, gross[0], net[0]


def render_pdf(path: Path, one, turn, risk):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    (oa, oc, os, _), (ta, tc, ts, _) = one, turn
    pdfmetrics.registerFont(TTFont("msyh", "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("msyh-bold", "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
    navy = colors.HexColor("#123047")
    teal = colors.HexColor("#087E83")
    styles = {
        "title": ParagraphStyle("title", fontName="msyh-bold", fontSize=20, leading=29, textColor=navy, spaceAfter=10, wordWrap="CJK"),
        "h": ParagraphStyle("h", fontName="msyh-bold", fontSize=12, leading=19, textColor=navy, spaceBefore=9, spaceAfter=6, wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName="msyh", fontSize=9.25, leading=15, spaceAfter=7, wordWrap="CJK"),
        "small": ParagraphStyle("small", fontName="msyh", fontSize=8, leading=12.5, spaceAfter=6, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName="msyh", fontSize=8, leading=12, wordWrap="CJK"),
        "head": ParagraphStyle("head", fontName="msyh-bold", fontSize=8, leading=12, textColor=colors.white, wordWrap="CJK"),
    }

    def p(s, style="body"):
        return Paragraph(html.escape(str(s)).replace("\n", "<br/>"), styles[style])

    def tbl(headers, rows, widths):
        matrix = [[p(x, "head") for x in headers]] + [[p(x, "cell") for x in row] for row in rows]
        table = Table(matrix, colWidths=widths, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#EDF5F6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.7, teal),
            ("GRID", (0, 1), (-1, -1), 0.3, colors.HexColor("#CDDBDE")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        table.spaceAfter = 8
        return table

    def fmt_policy(name):
        if name.endswith("baseline"):
            return "保留基准"
        if name.endswith("S1_parking"):
            return "S1 停车"
        if name.endswith("S2_turn"):
            return "S2 禁转"
        if name.endswith("S2_oneway"):
            return "S2 单向"
        if name.endswith("S3_combined"):
            return "S3 组合"
        return name

    challenge_image = ROOT / "成果/回退挑战/20260922_034228_3141eb/结果对照.png"
    dashboard_image = ROOT / "成果/演示/20260922_v4_completed_review/04.png"
    rows_o = recommendation_rows(os, oc["conditional_recommendations"][0], oc["conditional_recommendations"][1])
    rows_t = recommendation_rows(ts, tc["conditional_recommendations"][0], tc["conditional_recommendations"][1])
    changed_t = sum(old != new for _, old, new, *_ in rows_t)
    changed_o = sum(old != new for _, old, new, *_ in rows_o)
    one_runs = {r["name"]: r for r in os["runs"]}
    oneway_spillover = []
    for grid in os["demand_grid"]:
        base = one_runs[grid["baseline"]]
        oneway_spillover += [change(base, one_runs[name], "neighbor") for name in grid["policies"]
                            if name.endswith(("S2_oneway", "S3_combined"))]

    story = [
        p("路策 | 评分证据与新解压完整验收", "title"),
        p("2026年9月23日增补 · 真实仿真、条件推荐、失败案例和运行边界", "small"),
        p("本增补依据赛题五项标准组织最新证据，不预估评委分数。正式研究报告和操作手册保留9月22日发布时的11页及6页版本；这里补充后来完成的v4完整扩展实跑和新版实机回看。全部结论仅针对赛事调整的南京案例数据库。"),
        p("1  五项评分要求的证据入口", "h"),
        tbl(["赛题上限", "最直接的证据", "需要同时披露的边界"], [
            ["创新性 30", "真实反馈先看结果再增强或缩小措施；三地区同预算前瞻对照；稀疏固定强对照。", "固定策略在较强对照中追平；未证明回退提升最终建议。"],
            ["仿真与效果 20", "每需求独立基准，核心/邻区/全网多指标；13场景单向和禁转全矩阵。", "局部改善可能伴随绕行或新增过饱和；不是现实治理预测。"],
            ["智能体完整性 20", "受限问题解析、候选、原生调用、结果读取、约束筛选与可重放反馈。", "显式禁转和单向是预定组合；不冒充自适应搜索。"],
            ["程序质量 20", "v4独立Python和编译器，新解压两项完整运行，原始数组历史对照。", "同机验证；另一台物理电脑尚未重新求解。"],
            ["展示 10", "可离线PDF、全网双图大厅、2分04秒真实任务回看及中文字幕。", "录像读取已完成任务，录制新增模型调用0。"],
        ], [80, 228, 197]),
        p("详细逐项入口：成果/20260923评分证据导航.md。原始协议、封存档案与失败候选在仓库，视频或表格仅辅助阅读。", "small"),
        p("2  如何判定‘智能体有贡献’", "h"),
        p("闭环证据是先保存任务卡和候选，再启动TranStar原生模块，读取固定范围指标与约束，根据已观察结果决定下一候选或回退。前瞻核验逐步重放决策，并核对策略只使用自身已有观察；共享物理求解仍分别计入逻辑预算。"),
        p("容差v2仅改变数值近似时的选择顺序，不改变模型、输入或硬约束。若两次同条件原始输出相同，才能把推荐差异归于预先声明的规则版本；容差不是来自交通实测或统计误差标定。", "small"),
        PageBreak(),
        p("真实反馈与更强固定对照", "title"),
        p("2026年9月22日三地区前瞻试验：事前从新基准候选分层选区，最多两治理候选、三需求0.9/1/1.1，各需求配自己的基准。22个物理场景、154次原生模块调用，独立核验通过。", "body"),
        tbl(["区域", "稀疏固定", "反馈", "可证实的差别"], [
            ["湖滨路", "无统一合格", "无统一合格", "两策略都保留基准"],
            ["虎桥路 / 象贤路", "无统一合格", "单路段停车2级合格", "反馈缩小范围后找到合格候选"],
            ["毛纺厂路", "单路段停车2级", "单路段停车2级", "最终措施一致"],
        ], [92, 108, 108, 197]),
        p("较强的新挑战另在三个事前区域、每区每策略最多13个逻辑场景内比较稀疏固定、原反馈及回退反馈：37个物理场景、259次原生调用；三策略均为3/3区域找到合格措施。回退多用3次逻辑评估，没有改变最终推荐。不同批次不能当作同条件胜率变化，也不能推断统计显著。"),
        Image(str(challenge_image), width=505, height=264.1),
        p("图为九组策略结果与预算。新浦路反馈核心改善更大，同时1跳邻区和全网局部过饱和代价也更高；通过预定约束不代表每条道路都改善。", "small"),
        PageBreak(),
        p("新解压 v4：完整单向与禁转", "title"),
        p(f"同一份v4原程序包、同一台Windows电脑的新解压目录。单向任务 {oa['job_id']} 与禁转任务 {ta['job_id']} 均在求解前登记，分别完成13场景、91次顶层模块调用；单向260次车型求解，禁转520次适配/参考求解。原案例不变，严格核验、封存、复制档案复核及运行包完整性检查均通过。两者各自与对应历史同条件实验的117组九类路段数组逐项相同。"),
        p("单向：响应成立，因外溢拒绝", "h"),
        tbl(["小汽车需求", "旧规则", "容差v2", "v2核心耗时", "v2邻区耗时"], [
            [f"{f:.1f}", fmt_policy(old), fmt_policy(new), f"{core:+.5f}%", f"{neighbor:+.5f}%"]
            for f, old, new, core, neighbor, _ in rows_o
        ], [83, 96, 96, 115, 115]),
        p(f"单向及组合使邻区耗时增加 {min(oneway_spillover):.1f}% 至 {max(oneway_spillover):.1f}%，不能用核心改善掩盖转移代价。单向条件推荐与旧版在{changed_o}个需求点不同，没有观察到容差带来此例新增治理收益。"),
        p("禁转：真实转向响应与规则差异分开看", "h"),
        tbl(["小汽车需求", "历史旧规则", "v4容差v2", "v2核心耗时", "v2邻区耗时"], [
            [f"{f:.1f}", fmt_policy(old), fmt_policy(new), f"{core:+.5f}%", f"{neighbor:+.5f}%"]
            for f, old, new, core, neighbor, _ in rows_t
        ], [83, 96, 96, 115, 115]),
        p(f"指定转向4797→5990→5991在小汽车、出租车和摩托车正基准流量下归零；货车基准即为零，不计正响应证明。新旧任务相同输入和数组下，v2条件建议在{changed_t}个需求点不同；只将差异归于已登记的规则。高需求S2和S3都毛新增全网过饱和{risk[1]:.3f} km，净增{risk[2]:.3f} km；缓解路段不能抵消新受影响道路。完整指标与转移量请查原档，不能推广至全网既有禁转。"),
        p("指标是1小时路段Σ(qL/v)，不含全部节点延误；有向过饱和里程不是道路中心线长度。五次增量分配不等于均衡收敛或动态排队。", "small"),
        PageBreak(),
        p("现场操作与证据复核", "title"),
        p("1. 完整解压成果/便携主程序/20260922_v4/路策便携主程序_v4.zip到可写目录。\n2. 双击launch.cmd，通过环境检查后打开 http://127.0.0.1:8790/。\n3. 左侧填写任务卡并预览；启动后等待真实模型输出。完成后在中间双图比较同需求基准与方案，在右侧查看反馈、不利影响和原始日志。\n4. 若只复核本报告，先读各完成任务的verification.json和原始档案；不要为观看录像重复提交仿真。\n5. 服务运行的终端按Ctrl+C停止。"),
        Image(str(dashboard_image), width=505, height=350.7),
        p("图为真实v4界面已完成停车任务的浏览器录屏画面；演示任务20260922_052448_fa12d2，4场景、28次顶层调用。2分04秒无声WebM附中文字幕，录制新增模型调用0。", "small"),
        p("证据与尚待完成的工作", "h"),
        p("交付包括正式研究报告、可运行程序与源码、案例数据库和原始实验档案、操作手册、真实演示视频。同目录来源绑定.json记录本PDF引用证据的SHA-256；导出新增模型调用0。新解压两项扩展均在同一物理电脑完成；另一台电脑、另一套兼容数据和现实泊位/实施成本调查仍缺失。若现场环境不同，应按手册先做依赖检查与小预算真实任务，再谈迁移结论。", "small"),
    ]

    def footer(canvas, doc):
        canvas.setStrokeColor(teal)
        canvas.line(44, 33, A4[0] - 44, 33)
        canvas.setFont("msyh", 7.4)
        canvas.setFillColor(navy)
        canvas.drawString(44, 20, "路策交通仿真智能体 / 2026-09-23 证据增补")
        canvas.drawRightString(A4[0] - 44, 20, str(doc.page))

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=44, rightMargin=44,
                            topMargin=38, bottomMargin=45, title="路策评分证据与新解压完整验收增补",
                            author="路策交通仿真智能体项目")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("oneway_audit", type=Path)
    parser.add_argument("oneway_compare", type=Path)
    parser.add_argument("turn_audit", type=Path)
    parser.add_argument("turn_compare", type=Path)
    args = parser.parse_args()
    for key in ("output", "oneway_audit", "oneway_compare", "turn_audit", "turn_compare"):
        setattr(args, key, getattr(args, key).resolve())
    if args.output.exists():
        raise ValueError("output already exists; preserve published PDF")
    one = checked_extension("oneway", args.oneway_audit, args.oneway_compare, 260)
    turn = checked_extension("turn", args.turn_audit, args.turn_compare, 520)
    if one[0]["final_integrity"]["manifest_sha256"] != turn[0]["final_integrity"]["manifest_sha256"]:
        raise ValueError("v4 runtime manifests differ")
    other = validate_other_evidence()
    risk = checked_turn_risk(turn[1])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(args.output, one, turn, risk)
    sources = [args.oneway_audit, args.oneway_compare, args.turn_audit, args.turn_compare]
    sources += [x[0] for x in other] + [risk[0]]
    sources += [ROOT / "成果/回退挑战/20260922_034228_3141eb/结果对照.png",
                ROOT / "成果/演示/20260922_v4_completed_review/04.png"]
    binding = {
        "kind": "score_addendum_evidence_v1",
        "pdf": args.output.name,
        "pdf_sha256": digest(args.output),
        "runtime_code": RUNTIME_COMMIT,
        "runtime_manifest_sha256": one[0]["final_integrity"]["manifest_sha256"],
        "new_model_calls": 0,
        "sources": [{"path": str(p.relative_to(ROOT)), "sha256": digest(p)} for p in sources],
        "oneway_job": one[0]["job_id"],
        "turn_job": turn[0]["job_id"],
        "scope": "同机新解压原生仿真；PDF为已核验结果的只读导出，不是新计算或异机证明",
    }
    (args.output.parent / "来源绑定.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
