"""Create a two-page, source-bound PDF for the sealed medium-region guard run."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOB = "20260923_070135_0f0c08"
HISTORY = "20260922_015104_1bd73b"
CARD_SHA = "14c7f72fed4f93e876763a621924b95bda90f2352ec23951d0f5132932362ce1"
SOURCES = {
    "task": ROOT / "examples/parking_robust_guard_medium_region.json",
    "selection": ROOT / f"智能体实验/studies/{HISTORY}/selection.json",
    "comparison": ROOT / "成果/复评/20260923_medium_robust_guard/comparison.json",
    "package": ROOT / "成果/实测档案/20260923_medium_robust_guard/package.json",
    "extraction": ROOT / "成果/实测档案/20260923_medium_robust_guard/extraction_check.json",
    "zip": ROOT / "成果/实测档案/20260923_medium_robust_guard/中密度三需求安全门槛真实任务档案.zip",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked_evidence():
    task, comparison = read(SOURCES["task"]), read(SOURCES["comparison"])
    package, extraction = read(SOURCES["package"]), read(SOURCES["extraction"])
    if (digest(SOURCES["task"]) != CARD_SHA or task["search_mode"] != "robust"
            or task["demand_factors"] != [0.9, 1.0, 1.1]
            or task["max_new_network_oversaturated_km"] != 0.1
            or task["max_runs"] != 11 or task["max_feedback"] != 2):
        raise ValueError("冻结任务卡改变")
    if (comparison["new_job"] != JOB or comparison["old_study"] != HISTORY
            or comparison["task_card_sha256"] != CARD_SHA
            or comparison["historical_selection_sha256"] != digest(SOURCES["selection"])
            or comparison["new_scenarios"] != 10 or comparison["new_native_calls"] != 70
            or comparison["recommendation"] != "robust_p2_1"
            or comparison["recommended_changes"] != [{"from_node": 74, "to_node": 645,
                                                         "old_parking": 3, "parking": 2}]
            or not comparison["robust_across_tested_demand"]
            or not comparison["source_inputs_exact"] or not comparison["frozen_scope_exact"]
            or comparison["unmatched_new_scenes"] or len(comparison["matched"]) != 10
            or any(not all(m["raw_arrays_exact"].values())
                   or not all(m["changed_inputs_exact"].values())
                   for m in comparison["matched"].values())):
        raise ValueError("逐需求复评或旧研究对照不完整")
    rows = {row["name"]: row for row in comparison["rows"]}
    if (set(rows) != {f"robust_p{policy}_{factor:g}" for policy in (1, 2)
                     for factor in (0.9, 1.0, 1.1)}
            or rows["robust_p1_0.9"]["eligible"]
            or rows["robust_p1_0.9"]["neighbor_time_pct"] <= 0.5
            or rows["robust_p1_0.9"]["gross_new_network_km"] <= 0.1
            or any(not rows[f"robust_p2_{factor:g}"]["eligible"]
                   or rows[f"robust_p2_{factor:g}"]["gross_new_network_km"] != 0
                   or rows[f"robust_p2_{factor:g}"]["core_time_pct"] >= 0
                   for factor in (0.9, 1.0, 1.1))):
        raise ValueError("风险拒绝或统一建议的三需求结果不符")
    if (package["job"] != JOB or package["completed_scenarios"] != 10
            or package["module_executions"] != 70 or package["files"] != 5853
            or package["source_archive_sha256"] != comparison["new_archive_sha256"]
            or package["zip_sha256"] != digest(SOURCES["zip"])
            or extraction["status"] != "passed"
            or extraction["archive_sha256"] != package["zip_sha256"]
            or extraction["completed_scenarios"] != 10
            or extraction["module_executions"] != 70
            or not all(extraction["repeat_arrays_exact"].values())
            or not extraction["portable_archive_verified"]
            or extraction["new_model_calls"] != 0):
        raise ValueError("发布ZIP或异目录复核不完整")
    return comparison, rows


def render_pdf(output: Path, comparison: dict, rows: dict):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    pdfmetrics.registerFont(TTFont("msyh", "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("msyh-bold", "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
    navy, teal = colors.HexColor("#102B3B"), colors.HexColor("#087D80")
    amber, light, ink = colors.HexColor("#BD6B26"), colors.HexColor("#EAF3F4"), colors.HexColor("#283F4C")
    styles = {
        "title": ParagraphStyle("title", fontName="msyh-bold", fontSize=20, leading=29,
                                textColor=navy, spaceAfter=8, wordWrap="CJK"),
        "h": ParagraphStyle("h", fontName="msyh-bold", fontSize=11.5, leading=18,
                            textColor=navy, spaceBefore=9, spaceAfter=5, wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName="msyh", fontSize=9.2, leading=15,
                               textColor=ink, spaceAfter=7, wordWrap="CJK"),
        "small": ParagraphStyle("small", fontName="msyh", fontSize=8.1, leading=12.6,
                                textColor=ink, spaceAfter=7, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName="msyh", fontSize=8.1, leading=12,
                               textColor=ink, wordWrap="CJK"),
        "head": ParagraphStyle("head", fontName="msyh-bold", fontSize=8.1, leading=12,
                               textColor=colors.white, wordWrap="CJK"),
        "metric": ParagraphStyle("metric", fontName="msyh-bold", fontSize=13, leading=18,
                                 textColor=teal, alignment=1),
        "label": ParagraphStyle("label", fontName="msyh", fontSize=7.5, leading=11,
                                textColor=ink, alignment=1),
        "warning": ParagraphStyle("warning", fontName="msyh-bold", fontSize=9, leading=15,
                                  textColor=amber, wordWrap="CJK"),
    }

    def p(value, style="body"):
        return Paragraph(html.escape(str(value)).replace("\n", "<br/>"), styles[style])

    def table(headers, values, widths):
        cells = [[p(x, "head") for x in headers]] + [[p(x, "cell") for x in row] for row in values]
        item = Table(cells, colWidths=widths, repeatRows=1, hAlign="LEFT")
        item.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, light]),
            ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#BBD0D3")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        item.spaceAfter = 8
        return item

    cards = Table([[p("10", "metric"), p("70", "metric"), p("3", "metric"), p("0.1 km", "metric")],
                   [p("真实场景", "label"), p("TranStar顶层调用", "label"),
                    p("独立需求工况", "label"), p("冻结风险上限", "label")]],
                  colWidths=[126.5] * 4)
    cards.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light),
        ("BOX", (0, 0), (-1, -1), .6, colors.HexColor("#BBD0D3")),
        ("INNERGRID", (0, 0), (-1, -1), .35, colors.HexColor("#BBD0D3")),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
    ]))
    cards.spaceAfter = 12
    metrics = []
    for factor in (0.9, 1.0, 1.1):
        row = rows[f"robust_p2_{factor:g}"]
        metrics.append([f"{factor:.1f}", f"{row['core_time_pct']:+.3f}%",
                        f"{row['neighbor_time_pct']:+.3f}%",
                        f"{row['network_time_pct']:+.4f}%",
                        f"{row['gross_new_network_km']:.3f} km"])
    failed = rows["robust_p1_0.9"]
    risk = Table([[p(f"首轮联合治理在低需求0.9下：邻区耗时{failed['neighbor_time_pct']:+.3f}%，"
                     f"全网毛新增过饱和{failed['gross_new_network_km']:.3f} km。即使核心耗时下降，仍按事前门槛拒绝。",
                     "warning")]], colWidths=[506])
    risk.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF3E7")),
                              ("BOX", (0, 0), (-1, -1), .7, amber),
                              ("LEFTPADDING", (0, 0), (-1, -1), 10),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                              ("TOPPADDING", (0, 0), (-1, -1), 8),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    risk.spaceAfter = 10
    story = [
        p("路策 | 中密度区域安全回退", "title"),
        p("2026-09-23 新增复验 · 已封存真实TranStar仿真", "small"),
        cards,
        p("这次研究沿用事前固定的虎桥路、象贤路中密度研究区和0.1 km全网毛新增过饱和上限。"
          "智能体依据真实输出先否决三方向联合措施，再缩至单方向；每个需求都与自己的无治理基准配对。"),
        p("统一建议：仅将 74→645 改为停车2级", "h"),
        table(["小汽车需求", "核心路段耗时", "邻区路段耗时", "全网路段耗时", "毛新增过饱和"],
              metrics, [83, 107, 107, 108, 101]),
        p("负值表示路段Σ(qL/v)下降。三需求均通过固定约束，核心耗时下降且全网毛新增过饱和均为0；"
          "这里的耗时不含全部节点延误，也不是现实治理成本或官方比赛得分。", "small"),
        risk,
        p("为什么不是收益更大的三方向方案", "h"),
        table(["需求", "联合治理核心耗时", "联合治理邻区耗时", "全网毛新增过饱和", "结论"], [
            [f"{factor:.1f}", f"{rows[f'robust_p1_{factor:g}']['core_time_pct']:+.3f}%",
             f"{rows[f'robust_p1_{factor:g}']['neighbor_time_pct']:+.3f}%",
             f"{rows[f'robust_p1_{factor:g}']['gross_new_network_km']:.3f} km",
             "通过" if rows[f"robust_p1_{factor:g}"]["eligible"] else "拒绝"]
            for factor in (0.9, 1.0, 1.1)
        ], [56, 122, 124, 130, 74]),
        p("统一措施必须在全部已测需求点合格。联合方案在高、中需求虽有更大核心收益，"
          "低需求出现邻区外溢和4.182 km毛新增过饱和，不能把三个需求的条件建议拼成一个统一建议。", "small"),
        PageBreak(),
        p("从失败输出到统一建议", "title"),
        p("真实模型调用、反馈决定与预算", "h"),
        table(["顺序", "智能体动作与原生输出", "判定"], [
            ["1 基准", "需求1.0、1.1、0.9分别建立无治理基准", "固定同一核心、邻区和全网评价范围"],
            ["2 首轮", "三方向停车2级；低需求邻区+3.168%，毛新增4.182 km", "拒绝整组统一候选"],
            ["3 反馈", "按失败工况的路段耗时变化缩为74→645停车2级", "重新验证0.9、1.1、1.0，三点均通过"],
            ["4 复跑", "同输入基准重算，四类各32,981行原始数组", "逐字节一致；剩余预算1场景"],
        ], [72, 278, 156]),
        p("真实结果与历史线索如何区分", "h"),
        p(f"新任务{JOB}独立完成10场景、70次TranStar顶层调用，并通过输入、输出、约束、"
          "事前搜索动作和封存清单的重算。旧前瞻研究用于预先选择该区域；同条件10场景的40组原始路段数组"
          "及关键修改输入逐字节一致。旧研究没有这次全网硬门槛，且审计时加载其封存算法源码快照；"
          "历史任务不计入本次调用数。"),
        p("可直接复核的来源", "h"),
        p("1. examples/parking_robust_guard_medium_region.json：运行前冻结任务卡。\n"
          "2. 成果/复评/20260923_medium_robust_guard/comparison.json：逐需求结果与40组原始数组对照。\n"
          "3. 成果/实测档案/20260923_medium_robust_guard/中密度三需求安全门槛真实任务档案.zip："
          "原始输入、输出、日志、决策和封存清单。\n"
          "4. 同目录 extraction_check.json：ZIP独立解压后的只读核验。", "small"),
        p("研究边界", "h"),
        p("这是有历史正例线索的同机、同一赛事调整案例、第二研究区复验，不是盲选、等预算新旧算法消融、"
          "异机、跨城市或现实泊位承接验证。三个离散需求点之外的需求不可外推；"
          "本页只增补可查证据，不预估评委分数。", "small"),
        p("本增补承接此前正式研究报告和三需求v5增补；不改写它们发布时的数字。"
          "原始模型程序与数据仍从仓库及Git LFS获取，本PDF不包含TranStar软件。", "small"),
    ]

    def footer(canvas, document):
        canvas.setStrokeColor(teal)
        canvas.line(44, 33, A4[0] - 44, 33)
        canvas.setFont("msyh", 7.5)
        canvas.setFillColor(navy)
        canvas.drawString(44, 20, "路策交通仿真智能体 / 2026-09-23 中密度区域增补")
        canvas.drawRightString(A4[0] - 44, 20, str(document.page))

    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=44, rightMargin=45,
                            topMargin=38, bottomMargin=45,
                            title="路策中密度区域三需求安全回退增补", author="路策交通仿真智能体项目")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("保留已发布PDF，请使用新的输出路径")
    comparison, rows = checked_evidence()
    output.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(output, comparison, rows)
    binding = dict(kind="medium_robust_guard_score_addendum_v1", pdf=output.name,
                   pdf_sha256=digest(output), job=JOB, historical_study=HISTORY,
                   task_sha256=CARD_SHA, scenarios=10, transtar_top_level_calls=70,
                   new_model_calls_during_export=0,
                   sources=[dict(path=path.relative_to(ROOT).as_posix(), sha256=digest(path))
                            for path in SOURCES.values()],
                   scope="同机同案例三种离散小汽车需求；非盲选、官方评分或异机验证")
    (output.parent / "中密度增补来源绑定.json").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(pdf=str(output), sha256=binding["pdf_sha256"],
                          sources=len(binding["sources"]), new_model_calls=0), ensure_ascii=False))


if __name__ == "__main__":
    main()
