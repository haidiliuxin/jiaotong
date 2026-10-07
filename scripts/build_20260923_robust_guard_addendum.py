"""Build a source-bound PDF addendum for the sealed v5 three-demand guard run.

This reads published evidence only. It never starts TranStar or changes a job.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOB = "20260923_055121_7c20dd"
HISTORY = "20260911_021806_858461"
TASK_SHA = "2543fb496e76a313d387a1d5d6e8387438d1a33b9c585061c04b3ee8de8c3ba3"
V5_CODE = "5ac930a675a1b536e93ee5c54c5249ab9cb7fdb9"
SOURCES = {
    "task": ROOT / "examples/parking_robust_network_guard.json",
    "acceptance": ROOT / "成果/便携主程序/20260923_v5/三需求安全门槛验收/acceptance.json",
    "ui": ROOT / "成果/便携主程序/20260923_v5/三需求安全门槛验收/browser_ui.json",
    "comparison": ROOT / "成果/复评/20260923_robust_network_guard/comparison.json",
    "archive_package": ROOT / "成果/实测档案/20260923_robust_network_guard/package.json",
    "archive_extraction": ROOT / "成果/实测档案/20260923_robust_network_guard/extraction_check.json",
    "archive_zip": ROOT / "成果/实测档案/20260923_robust_network_guard/三需求安全门槛真实任务档案.zip",
    "recording": ROOT / "成果/演示/20260923_v5_robust_guard_review/recording.json",
    "playback": ROOT / "成果/演示/20260923_v5_robust_guard_review/visual_check.json",
    "video": ROOT / "成果/演示/20260923_v5_robust_guard_review/已完成真实任务回看.webm",
    "screenshot": ROOT / "成果/演示/20260923_v5_robust_guard_review/05.png",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked_evidence():
    data = {name: read(path) for name, path in SOURCES.items()
            if path.suffix.lower() == ".json"}
    task, acceptance, comparison = data["task"], data["acceptance"], data["comparison"]
    package, extraction = data["archive_package"], data["archive_extraction"]
    recording, playback = data["recording"], data["playback"]
    if digest(SOURCES["task"]) != TASK_SHA or acceptance["task_sha256"] != TASK_SHA:
        raise ValueError("The frozen card differs from the audited task")
    if (task["search_mode"] != "robust" or task["demand_factors"] != [0.9, 1.0, 1.1]
            or task["max_new_network_oversaturated_km"] != 0.1
            or task["max_runs"] != 11 or task["max_feedback"] != 2):
        raise ValueError("The frozen three-demand protocol changed")
    verified = acceptance["verification"]
    if (acceptance["status"] != "passed" or acceptance["job_id"] != JOB
            or acceptance["runtime_code_commit"] != V5_CODE
            or acceptance["observed_model_calls"] != 77 or acceptance["new_model_calls"] != 0
            or not acceptance["initial_integrity"]["passed"] or not acceptance["final_integrity"]["passed"]
            or not verified["passed"] or not verified["portable_archive_verified"]
            or verified["completed_scenarios"] != 11 or verified["module_executions"] != 77
            or not all(verified["repeat_arrays_exact"].values())
            or not verified["active_search_audit"]["complete_demand_recommendation_verified"]):
        raise ValueError("Portable v5 native-run acceptance is incomplete")
    if (data["ui"]["status"] != "passed" or data["ui"]["new_model_calls"] != 0
            or data["ui"]["job_id"] != JOB or data["ui"]["preview_limit_km"] != 0.1
            or data["ui"]["console_errors"]):
        raise ValueError("Portable dashboard inspection is incomplete")
    exact = comparison["raw_result_arrays_exact"]
    if (comparison["history_job"] != HISTORY or comparison["guarded_job"] != JOB
            or comparison["guarded_recommendation"] != "robust_p3_1"
            or comparison["scenarios_exact"] != 11 or len(exact) != 11
            or any(set(matches) != {"speed_kmh", "flow_pcu_h", "vc", "time_s"}
                   or not all(matches.values()) for matches in exact.values())
            or not comparison["source_inputs_exact"] or not comparison["scope_exact"]
            or not comparison["uniform_recommendation_passed_all_demands"]):
        raise ValueError("Matched historical and guarded array comparison is incomplete")
    if (package["job"] != JOB or package["completed_scenarios"] != 11
            or package["module_executions"] != 77 or package["files"] != 6437
            or package["source_archive_sha256"] != acceptance["job_archive_sha256"]
            or digest(SOURCES["archive_zip"]) != package["zip_sha256"]
            or extraction["status"] != "passed"
            or extraction["archive_sha256"] != package["zip_sha256"]
            or extraction["independent_review_sha256"] != digest(SOURCES["comparison"])
            or not extraction["portable_archive_verified"]):
        raise ValueError("Published raw ZIP or independent extraction does not match")
    if (recording["job_id"] != JOB or recording["archive_sha256"] != package["source_archive_sha256"]
            or recording["status"] != "recorded_pending_visual_review"
            or recording["new_model_calls"] != 0 or len(recording["scenes"]) != 8
            or recording["errors"] or recording["blocked_requests"]
            or digest(SOURCES["video"]) != recording["video"]["sha256"]
            or playback["status"] != "passed" or playback["video_sha256"] != recording["video"]["sha256"]
            or playback["metadata"]["cues"] != 8 or len(playback["chapterSeeks"]) != 8
            or not playback["screenshots_present"] or playback["errors"]):
        raise ValueError("The video or browser playback evidence changed")
    rows = {row["name"]: row for row in comparison["guarded_policy_rows"]}
    expected = {"robust_p1_1.1", "robust_p2_0.9", "robust_p2_1", "robust_p2_1.1",
                "robust_p3_0.9", "robust_p3_1", "robust_p3_1.1"}
    if set(rows) != expected or rows["robust_p1_1.1"]["eligible"] is not False:
        raise ValueError("Unexpected candidate grid or missing rejection")
    if (rows["robust_p1_1.1"]["neighbor_time_change_pct"] <= 0.5
            or rows["robust_p1_1.1"]["gross_new_network_oversaturated_km"] != 0):
        raise ValueError("Joint-candidate rejection does not match the published reason")
    for factor in (0.9, 1.0, 1.1):
        for level in (2, 3):
            row = rows[f"robust_p{level}_{factor:g}"]
            if (row["demand_factor"] != factor or not row["eligible"]
                    or row["core_time_change_pct"] >= 0 or row["network_time_change_pct"] >= 0
                    or row["gross_new_network_oversaturated_km"] > 0.1 + 1e-9):
                raise ValueError("The published single-direction grid is not uniformly feasible")
    if abs(rows["robust_p3_1"]["gross_new_network_oversaturated_km"] - 0.056) > 1e-9:
        raise ValueError("The local 1.0-demand cost was lost")
    return data, rows


def render_pdf(output: Path, data: dict, rows: dict):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    pdfmetrics.registerFont(TTFont("msyh", "C:/Windows/Fonts/msyh.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("msyh-bold", "C:/Windows/Fonts/msyhbd.ttc", subfontIndex=0))
    navy, teal = colors.HexColor("#102B3B"), colors.HexColor("#087D80")
    amber, light, ink = colors.HexColor("#BD6B26"), colors.HexColor("#EAF3F4"), colors.HexColor("#283F4C")
    styles = {
        "title": ParagraphStyle("title", fontName="msyh-bold", fontSize=20, leading=29,
                                textColor=navy, spaceAfter=8, wordWrap="CJK"),
        "h": ParagraphStyle("h", fontName="msyh-bold", fontSize=12, leading=18,
                            textColor=navy, spaceBefore=8, spaceAfter=5, wordWrap="CJK"),
        "body": ParagraphStyle("body", fontName="msyh", fontSize=9.2, leading=15,
                               textColor=ink, spaceAfter=7, wordWrap="CJK"),
        "small": ParagraphStyle("small", fontName="msyh", fontSize=8, leading=12.5,
                                textColor=ink, spaceAfter=6, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName="msyh", fontSize=8.1, leading=12,
                               textColor=ink, wordWrap="CJK"),
        "head": ParagraphStyle("head", fontName="msyh-bold", fontSize=8.1, leading=12,
                               textColor=colors.white, wordWrap="CJK"),
        "metric": ParagraphStyle("metric", fontName="msyh-bold", fontSize=12, leading=18,
                                 textColor=teal, alignment=1, wordWrap="CJK"),
        "label": ParagraphStyle("label", fontName="msyh", fontSize=7.6, leading=12,
                                textColor=ink, alignment=1, wordWrap="CJK"),
        "warning": ParagraphStyle("warning", fontName="msyh-bold", fontSize=9, leading=15,
                                  textColor=amber, wordWrap="CJK"),
    }

    def p(value, kind="body"):
        return Paragraph(html.escape(str(value)).replace("\n", "<br/>"), styles[kind])

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

    cards = Table([[p("11", "metric"), p("77", "metric"), p("3", "metric"), p("0.1 km", "metric")],
                   [p("真实场景", "label"), p("TranStar 顶层调用", "label"),
                    p("独立需求工况", "label"), p("预先固定风险上限", "label")]],
                  colWidths=[126.5] * 4)
    cards.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), light),
        ("BOX", (0, 0), (-1, -1), .6, colors.HexColor("#BBD0D3")),
        ("INNERGRID", (0, 0), (-1, -1), .35, colors.HexColor("#BBD0D3")),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 7),
    ]))
    cards.spaceAfter = 13
    metrics = []
    for factor in (0.9, 1.0, 1.1):
        row = rows[f"robust_p3_{factor:g}"]
        metrics.append([f"{factor:.1f}", f"{row['core_time_change_pct']:+.3f}%",
                        f"{row['network_time_change_pct']:+.4f}%",
                        f"{row['neighbor_time_change_pct']:+.4f}%",
                        f"{row['gross_new_network_oversaturated_km']:.3f} km"])
    tradeoffs = []
    for factor in (0.9, 1.0, 1.1):
        moderate, stronger = (rows[f"robust_p{level}_{factor:g}"] for level in (2, 3))
        tradeoffs.append([f"{factor:.1f}", f"{moderate['core_time_change_pct']:+.3f}%",
                          f"{stronger['core_time_change_pct']:+.3f}%",
                          f"{moderate['gross_new_network_oversaturated_km']:.3f} / "
                          f"{stronger['gross_new_network_oversaturated_km']:.3f} km"])
    warning = Table([[p("局部代价：需求1.0时新增0.056 km全网过饱和有向路段。它低于0.1 km上限，但不是零风险。", "warning")]], colWidths=[506])
    warning.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FFF3E7")),
                                 ("BOX", (0, 0), (-1, -1), .7, amber),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 10),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8),
                                 ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    warning.spaceAfter = 12
    story = [
        p("路策 | 三需求全网风险门槛复验", "title"),
        p("2026-09-23 增补 · v5便携主程序 · 已封存真实仿真", "small"),
        cards,
        p("这次复验在事前固定的0.1 km全网毛新增过饱和上限下，让智能体按实际TranStar输出缩小治理范围，再比较停车强度。任务完成11场景、77次顶层模块调用；各需求独立配对自己的无治理基准。", "body"),
        p("统一建议：仅将 3244→3245 改为停车1级", "h"),
        table(["小汽车需求", "核心路段耗时", "全网路段耗时", "邻区路段耗时", "全网毛新增过饱和"],
              metrics, [78, 103, 105, 105, 115]),
        warning,
        p("同方向两种治理强度：核心收益与风险一起比较", "h"),
        table(["需求", "停车2级：核心耗时", "停车1级：核心耗时", "两级毛新增过饱和：2级 / 1级"],
              tradeoffs, [58, 132, 132, 184]),
        p("负值表示路段Σ(qL/v)下降。停车1级在三个需求下核心收益均高于2级，全网耗时也都下降；这不是完整节点延误、现实泊位成本或官方比赛分数。", "small"),
        PageBreak(),
        p("智能体为何改变措施", "title"),
        p("同一冻结评价范围内，三方向联合停车2级首先在高需求1.1下试验：核心耗时下降35.703%、全网耗时下降0.0599%，但邻区耗时上升1.1728%，超过原有0.5%硬上限。智能体保留失败记录，在下一次模型调用前缩至单方向，而没有用核心改善掩盖外溢。"),
        table(["步骤", "实测或决策", "结果"], [
            ["1 联合试验", "三方向停车2级，高需求1.1；邻区耗时+1.1728%", "拒绝；其余需求不冒充已测"],
            ["2 缩小范围", "仅3244→3245停车2级；补齐0.9、1.0、1.1", "三档需求均通过"],
            ["3 增强强度", "同方向停车1级；重新补齐三档需求", "统一建议，披露1.0局部代价"],
            ["4 复现封存", "同输入基准复跑，四类各32,981行原始路段数组", "逐项完全一致"],
        ], [82, 275, 149]),
        p("可核对的真实调用与独立复评", "h"),
        p(f"冻结任务卡SHA-256：{TASK_SHA[:16]}…；v5包源码提交：{V5_CODE[:12]}。便携任务{JOB}完成后，包内独立核验、封存、档案复核与另一目录解压复核均通过。新旧同输入任务各11场景，其44组原始路段数组逐字节一致；旧任务本身没有新硬约束，不能替代本次实跑。"),
        p("五项评分要求的新增证据", "h"),
        table(["评分方向", "本轮可查的事实", "限制"], [
            ["方案创新性", "真实失败触发下一次措施缩减；三需求完整复核", "有历史线索，不是盲选实验"],
            ["仿真与效果", "核心、邻区、全网及毛新增过饱和同表披露", "仅同一案例三个离散需求"],
            ["智能体完整性", "任务卡、事前动作、原生运行、约束筛选和统一推荐可重放", "确定性规则，不保证全局最优"],
            ["程序可运行性", "v5新解压目录完整运行；原始档案异目录复核", "仍未完成另一台物理电脑重算"],
            ["成果展示", "大地图回看、八章节中文字幕和来源绑定", "录像只读完成任务，新增调用0"],
        ], [98, 250, 158]),
        p("本增补承接2026-09-22正式研究报告及2026-09-23前一份v4证据增补，不改写它们发布时的数字。分值是赛题权重，以上只列新增证据，不预估评委得分。", "small"),
        PageBreak(),
        p("评审现场：地图、录像与原始档案", "title"),
        p("图为v5已完成任务的真实浏览器画面：基准需求双图与右侧算法决策同时可见。左侧表单属于新实验配置，所选任务的实际参数应在右上角“实验档案”核对。", "small"),
        Image(str(SOURCES["screenshot"]), width=506, height=351.4),
        p("图中路网与指标来自已保存的原生结果，不是示意线路。回看视频约2分19秒、1440×1000，八段中文字幕与八个章节播放检查通过；录制前后档案核验均通过，录制新增TranStar调用0。", "small"),
        p("独立核验入口", "h"),
        p("1. 成果/复评/20260923_robust_network_guard/comparison.json：逐候选、逐需求指标与44组原始数组对照。\n2. 成果/实测档案/20260923_robust_network_guard/三需求安全门槛真实任务档案.zip：11场景的输入、输出、日志和封存清单。解压后运行 python scripts/verify_experiment.py <任务目录> --archive。\n3. 成果/演示/20260923_v5_robust_guard_review/播放说明.html：八章节字幕回看。", "small"),
        p("数据库经过赛事调整，不代表现实南京交通；小汽车需求变化不代表所有车型同比变化。另一台电脑、另一套兼容数据集、停车承接与实施成本仍需单独验证。完整原始数据和v5程序从仓库及Git LFS获取，本PDF不包含模型程序。", "small"),
    ]

    def footer(canvas, document):
        canvas.setStrokeColor(teal)
        canvas.line(44, 33, A4[0] - 44, 33)
        canvas.setFont("msyh", 7.5)
        canvas.setFillColor(navy)
        canvas.drawString(44, 20, "路策交通仿真智能体 / 2026-09-23 三需求增补")
        canvas.drawRightString(A4[0] - 44, 20, str(document.page))

    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=44, rightMargin=45,
                            topMargin=38, bottomMargin=45,
                            title="路策三需求全网风险门槛复验增补", author="路策交通仿真智能体项目")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Preserve an existing published PDF; use a new output path")
    data, rows = checked_evidence()
    output.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(output, data, rows)
    binding = {
        "kind": "robust_network_guard_score_addendum_v1",
        "pdf": output.name,
        "pdf_sha256": digest(output),
        "job": JOB,
        "history_job": HISTORY,
        "task_sha256": TASK_SHA,
        "v5_runtime_code": V5_CODE,
        "scenarios": 11,
        "transtar_top_level_calls": 77,
        "new_model_calls_during_export": 0,
        "sources": [{"path": path.relative_to(ROOT).as_posix(), "sha256": digest(path)}
                    for path in SOURCES.values()],
        "scope": "同机同案例三种离散小汽车需求，非官方评分、异机或现实实施证明",
    }
    (output.parent / "来源绑定.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pdf": str(output), "sha256": binding["pdf_sha256"],
                      "source_count": len(binding["sources"]), "new_model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
