"""Build a source-bound two-page score addendum for Xingpu backtracking."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOB = '20260923_083136_743057'
OLD = '20260923_075629_706ef0'
CARD_SHA = '8867273f99594fe99c942616964a47052cc0fff956da8e63181ae401dfa86aa9'
SOURCES = {
    'task': ROOT / 'examples/parking_robust_guard_backtrack_xingpu.json',
    'comparison': ROOT / '成果/复评/20260923_xingpu_backtrack/comparison.json',
    'package': ROOT / '成果/实测档案/20260923_xingpu_backtrack_dedup/package.json',
    'extraction': ROOT / '成果/实测档案/20260923_xingpu_backtrack_dedup/extraction_check.json',
    'zip': ROOT / '成果/实测档案/20260923_xingpu_backtrack_dedup/兴浦路回退三需求无损去重任务包.zip',
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def checked_evidence():
    task, comparison = read(SOURCES['task']), read(SOURCES['comparison'])
    package, extraction = read(SOURCES['package']), read(SOURCES['extraction'])
    if (digest(SOURCES['task']) != CARD_SHA
            or not task.get('backtrack_on_singleton_failure')
            or task['links'] != [[1179, 1180], [1180, 1181], [1181, 1180]]
            or task['demand_factors'] != [0.9, 1.0, 1.1]
            or task['max_runs'] != 11 or task['max_feedback'] != 3
            or task['max_new_network_oversaturated_km'] != 0.1):
        raise ValueError('冻结任务卡或实验门槛改变')
    if (comparison['new_job'] != JOB or comparison['old_job'] != OLD
            or comparison['preregistered_task_sha256'] != CARD_SHA
            or comparison['old_recommendation'] != 'baseline'
            or comparison['new_recommendation'] != 'robust_p4_1'
            or comparison['recommended_changes'] != [dict(from_node=1181, to_node=1180,
                                                           old_parking=3, parking=2)]
            or comparison['old_scenarios'] != 6 or comparison['new_scenarios'] != 10
            or comparison['new_native_calls'] != 70
            or not comparison['original_source_inputs_exact']
            or not comparison['frozen_scope_exact']
            or len(comparison['shared_native_raw_arrays_exact']) != 6
            or not all(all(x.values()) for x in comparison['shared_native_raw_arrays_exact'].values())
            or len(comparison['search_stop']['backtracks']) != 2
            or comparison['search_stop']['actual_runs'] != 10
            or not all(comparison['repeat_arrays_exact'].values())
            or not all(comparison['decision_replay'].values())):
        raise ValueError('逐候选复评或原始数组核验不完整')
    rows = {row['name']: row for row in comparison['rows']}
    if (set(rows) != {f'robust_p{n}_1.1' for n in (1, 2, 3, 4)}
            | {'robust_p4_1', 'robust_p4_0.9'}
            or any(rows[f'robust_p{n}_1.1']['eligible'] for n in (1, 2, 3))
            or any(not rows[f'robust_p4_{f:g}']['eligible']
                   or rows[f'robust_p4_{f:g}']['gross_new_network_km'] != 0
                   or rows[f'robust_p4_{f:g}']['core_time_pct'] >= 0
                   for f in (0.9, 1.0, 1.1))):
        raise ValueError('失败候选或三需求通过结果改变')
    if (package['format'] != 'sealed-job-content-addressed-v1'
            or package['job'] != JOB or package['completed_scenarios'] != 10
            or package['module_executions'] != 70
            or package['files'] != 5853 or package['unique_objects'] != 914
            or package['source_archive_sha256'] != comparison['new_archive_sha256']
            or package['zip_sha256'] != digest(SOURCES['zip'])
            or extraction['status'] != 'passed'
            or extraction['format'] != package['format']
            or extraction['archive_sha256'] != package['zip_sha256']
            or extraction['files'] != package['files']
            or extraction['unique_objects'] != package['unique_objects']
            or extraction['completed_scenarios'] != 10
            or extraction['module_executions'] != 70
            or not extraction['portable_archive_verified']
            or extraction['new_model_calls'] != 0):
        raise ValueError('发布ZIP或异目录只读核验不完整')
    return comparison, rows


def render_pdf(output, comparison, rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Table, TableStyle

    pdfmetrics.registerFont(TTFont('msyh', 'C:/Windows/Fonts/msyh.ttc', subfontIndex=0))
    pdfmetrics.registerFont(TTFont('msyh-bold', 'C:/Windows/Fonts/msyhbd.ttc', subfontIndex=0))
    navy, teal = colors.HexColor('#102B3B'), colors.HexColor('#087D80')
    amber, light, ink = colors.HexColor('#BD6B26'), colors.HexColor('#EAF3F4'), colors.HexColor('#283F4C')
    styles = {
        'title': ParagraphStyle('title', fontName='msyh-bold', fontSize=19, leading=27,
                                textColor=navy, spaceAfter=8, wordWrap='CJK'),
        'h': ParagraphStyle('h', fontName='msyh-bold', fontSize=11.4, leading=17,
                            textColor=navy, spaceBefore=9, spaceAfter=5, wordWrap='CJK'),
        'body': ParagraphStyle('body', fontName='msyh', fontSize=9.1, leading=14.5,
                               textColor=ink, spaceAfter=7, wordWrap='CJK'),
        'small': ParagraphStyle('small', fontName='msyh', fontSize=8.1, leading=12.5,
                                textColor=ink, spaceAfter=7, wordWrap='CJK'),
        'cell': ParagraphStyle('cell', fontName='msyh', fontSize=8, leading=12,
                               textColor=ink, wordWrap='CJK'),
        'head': ParagraphStyle('head', fontName='msyh-bold', fontSize=8, leading=12,
                               textColor=colors.white, wordWrap='CJK'),
        'metric': ParagraphStyle('metric', fontName='msyh-bold', fontSize=13, leading=18,
                                 textColor=teal, alignment=1),
        'label': ParagraphStyle('label', fontName='msyh', fontSize=7.5, leading=11,
                                textColor=ink, alignment=1),
        'warning': ParagraphStyle('warning', fontName='msyh-bold', fontSize=9, leading=15,
                                  textColor=amber, wordWrap='CJK'),
    }

    def p(value, style='body'):
        return Paragraph(html.escape(str(value)).replace('\n', '<br/>'), styles[style])

    def table(headers, values, widths):
        item = Table([[p(x, 'head') for x in headers]]
                     + [[p(x, 'cell') for x in row] for row in values],
                     colWidths=widths, repeatRows=1, hAlign='LEFT')
        item.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), navy),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light]),
            ('GRID', (0, 0), (-1, -1), .35, colors.HexColor('#BBD0D3')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))
        item.spaceAfter = 8
        return item

    cards = Table([[p('10', 'metric'), p('70', 'metric'), p('3', 'metric'), p('0.1 km', 'metric')],
                   [p('真实场景', 'label'), p('TranStar顶层调用', 'label'),
                    p('配对需求工况', 'label'), p('事前风险上限', 'label')]],
                  colWidths=[126.5] * 4)
    cards.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), light),
        ('BOX', (0, 0), (-1, -1), .6, colors.HexColor('#BBD0D3')),
        ('INNERGRID', (0, 0), (-1, -1), .35, colors.HexColor('#BBD0D3')),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
    ]))
    cards.spaceAfter = 12

    metrics = []
    for factor in (0.9, 1.0, 1.1):
        row = rows[f'robust_p4_{factor:g}']
        metrics.append([f'{factor:.1f}', f"{row['core_time_pct']:+.3f}%",
                        f"{row['neighbor_time_pct']:+.3f}%",
                        f"{row['network_time_pct']:+.4f}%",
                        f"{row['gross_new_network_km']:.3f} km"])
    failed = rows['robust_p3_1.1']
    near_miss = Table([[p(f"1179→1180单方向在高需求下核心耗时{failed['core_time_pct']:+.3f}%，"
                          f"毛新增过饱和{failed['gross_new_network_km']:.3f} km，"
                          f"但邻区耗时{failed['neighbor_time_pct']:+.3f}%超过固定的0.5%上限，仍被拒绝。",
                          'warning')]], colWidths=[506])
    near_miss.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF3E7')),
        ('BOX', (0, 0), (-1, -1), .7, amber),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
        ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
    ]))
    near_miss.spaceAfter = 10

    story = [
        p('路策 | 兴浦路失败回退实测', 'title'),
        p('2026-09-23 三需求新增复评 · 已封存真实TranStar仿真', 'small'),
        cards,
        p('上一轮盲选高密度区域时，联合治理和首个单方向均被安全门槛拒绝，旧控制器停在6/11场景。'
          '本次在这个已知负例上冻结新规则：单方向失败后，按无治理基准的固定次序尝试原核心未测方向，'
          '每个治理结果都与本需求无治理基准配对。'),
        p('统一建议：仅将 1181→1180 改为停车2级', 'h'),
        table(['小汽车需求', '核心路段耗时', '邻区路段耗时', '全网路段耗时', '毛新增过饱和'],
              metrics, [83, 107, 107, 108, 101]),
        p('负值表示路段Σ(qL/v)下降。三个预设需求点均通过冻结的邻区、全网耗时及毛新增过饱和门槛；'
          '此表不包括全部节点延误，也不代表现实实施成本。', 'small'),
        near_miss,
        p('为什么没有选择核心收益更大的方案', 'h'),
        table(['高需求1.1候选', '核心耗时', '邻区耗时', '毛新增过饱和', '结论'], [
            ['三方向联合2级', f"{rows['robust_p1_1.1']['core_time_pct']:+.3f}%",
             f"{rows['robust_p1_1.1']['neighbor_time_pct']:+.3f}%",
             f"{rows['robust_p1_1.1']['gross_new_network_km']:.3f} km", '拒绝'],
            ['1180→1181停车2级', f"{rows['robust_p2_1.1']['core_time_pct']:+.3f}%",
             f"{rows['robust_p2_1.1']['neighbor_time_pct']:+.3f}%",
             f"{rows['robust_p2_1.1']['gross_new_network_km']:.3f} km", '拒绝'],
            ['1179→1180停车2级', f"{rows['robust_p3_1.1']['core_time_pct']:+.3f}%",
             f"{rows['robust_p3_1.1']['neighbor_time_pct']:+.3f}%",
             f"{rows['robust_p3_1.1']['gross_new_network_km']:.3f} km", '拒绝'],
            ['1181→1180停车2级', f"{rows['robust_p4_1.1']['core_time_pct']:+.3f}%",
             f"{rows['robust_p4_1.1']['neighbor_time_pct']:+.3f}%",
             f"{rows['robust_p4_1.1']['gross_new_network_km']:.3f} km", '通过'],
        ], [118, 97, 97, 119, 75]),
        p('方案必须先满足硬门槛，再比较收益。首轮看似更强，但高需求邻区耗时+25.653%、'
          '全网毛新增0.468 km，不能只以核心改善替代全网安全检查。', 'small'),
        PageBreak(),
        p('从失败反馈到安全统一建议', 'title'),
        p('规则先冻结，再调用真实模型', 'h'),
        table(['阶段', '算法动作与观察', '下一步'], [
            ['三需求基准', '1.0、1.1、0.9分别运行无治理基准；固定核心3条、邻区15条', '高需求先测'],
            ['联合候选', '三方向停车2级；需求1.1邻区+25.653%，毛新增0.468 km', '拒绝，缩减'],
            ['首个单方向', '1180→1181停车2级；仍有邻区+24.720%，毛新增0.468 km', '拒绝，触发回退'],
            ['第二方向', '1179→1180停车2级；邻区+0.516%，高于0.5%上限', '拒绝，继续回退'],
            ['第三方向', '1181→1180停车2级；需求1.1、1.0、0.9全部通过', '统一推荐'],
            ['复跑与审计', '同输入基准四类各32,981行原始数组完全一致', '封存，独立重放'],
        ], [91, 293, 122]),
        p('前后任务可比性', 'h'),
        p(f'旧负例{OLD}与新任务{JOB}的源输入、诊断和评价范围相同；两任务共有的6个场景，'
          '四类原始路段数组共24组逐字节一致。新任务独立执行10场景、70次原生顶层调用；'
          '9条搜索决策均可按保存的先前观察重放，两次回退实际执行，未把缺测视为通过。'),
        p('可独立查验的材料', 'h'),
        p('1. examples/parking_robust_guard_backtrack_xingpu.json：运行前冻结任务卡。\n'
          '2. docs/20260923兴浦路单路段回退预注册.md：边界修订及中止记录。\n'
          '3. 成果/复评/20260923_xingpu_backtrack/comparison.json：逐候选、逐需求指标和原始数组对照。\n'
          '4. 成果/实测档案/20260923_xingpu_backtrack_dedup/兴浦路回退三需求无损去重任务包.zip：'
          '原始输入输出、日志和封存清单；通过路径映射可无损复原5853个文件。\n'
          '5. 同目录 extraction_check.json：独立目录完整复原后的只读核验。', 'small'),
        p('证据与评分边界', 'h'),
        p('这是已经知道上一轮失败的同一区域开发性复测，反馈候选上限也从2提高到3。'
          '它证明本次固定任务中补测方向找到三需求合格措施，不能估计未知区域命中率、'
          '公平算法胜率、另一城市或现实道路的治理效果，也不预估评委分数。', 'small'),
        p('本增补不改写此前正式报告及PDF的发布时间和数字。代码版规则、完整案例与'
          'TranStar原生程序在仓库及运行包中，PDF仅供评审阅读。', 'small'),
    ]

    def footer(canvas, document):
        canvas.setStrokeColor(teal)
        canvas.line(44, 33, A4[0] - 44, 33)
        canvas.setFont('msyh', 7.5)
        canvas.setFillColor(navy)
        canvas.drawString(44, 20, '路策交通仿真智能体 / 2026-09-23 兴浦路开发性复测')
        canvas.drawRightString(A4[0] - 44, 20, str(document.page))

    doc = SimpleDocTemplate(str(output), pagesize=A4, leftMargin=44, rightMargin=45,
                            topMargin=38, bottomMargin=45,
                            title='路策兴浦路单方向失败回退三需求增补', author='路策交通仿真智能体项目')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('不能覆盖已有PDF')
    comparison, rows = checked_evidence()
    output.parent.mkdir(parents=True, exist_ok=True)
    render_pdf(output, comparison, rows)
    binding = dict(kind='xingpu_backtrack_score_addendum_v1', pdf=output.name,
                   pdf_sha256=digest(output), job=JOB, old_job=OLD,
                   task_sha256=CARD_SHA, scenarios=10, transtar_top_level_calls=70,
                   new_model_calls_during_export=0,
                   sources=[dict(path=path.relative_to(ROOT).as_posix(), sha256=digest(path))
                            for path in SOURCES.values()],
                   limitation='同一区域已知负例上的开发性复测，不是盲测或官方评分')
    (output.parent / '兴浦路回退增补来源绑定.json').write_text(
        json.dumps(binding, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(pdf=str(output), sha256=binding['pdf_sha256'],
                          sources=len(binding['sources']), new_model_calls=0), ensure_ascii=False))


if __name__ == '__main__':
    main()
