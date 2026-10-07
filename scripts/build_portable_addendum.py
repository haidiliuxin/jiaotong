"""Render a three-page evidence addendum after portable web acceptance passes.

Publishing only: this script never calls a traffic model. Inspect every rendered
page before adding the PDF to a release. Existing outputs are never overwritten.
"""
import argparse
import hashlib
import html
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def read(path):
    return Path(path).read_bytes()


def main(output, acceptance):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, PageBreak, Image

    raw = read(acceptance)
    web = json.loads(raw)
    if web.get('status') != 'passed' or not web['verification']['passed']:
        raise ValueError('网页真实验收尚未通过')
    if web['verification']['module_executions'] != 28 or web['verification']['completed_scenarios'] != 4:
        raise ValueError('需要完整四场景28次原生调用')
    if not web['source_inputs_unchanged'] or not web['final_integrity']['passed']:
        raise ValueError('源数据或运行包完整性未通过')
    browser_raw = read(acceptance.parent/'browser_completion.json')
    browser = json.loads(browser_raw)
    if browser.get('completed') is not True or browser.get('job') != web['job_id']:
        raise ValueError('缺少对应任务完成后的网页观察记录')
    challenge = ROOT/'成果/回退挑战/20260922_034228_3141eb'
    facts_raw = read(challenge/'证据摘要.json')
    facts = json.loads(facts_raw)
    # Bind the exported numbers to the sealed state, protocol and selection.
    original = ROOT/'智能体实验/challenges'/facts['job']
    for name in ('state', 'protocol', 'selection'):
        if hashlib.sha256(read(original/f'{name}.json')).hexdigest() != facts['verification'][f'{name}_sha256']:
            raise ValueError(f'挑战摘要已过期：{name}')
    from scripts.report_challenge import outcomes
    if facts['outcomes'] != outcomes(json.loads(read(original/'state.json')),json.loads(read(original/'selection.json'))):
        raise ValueError('挑战汇总与封存状态不一致')
    v3 = json.loads(read(ROOT/'成果/便携主程序/20260922/真实验收v3/acceptance.json'))
    if v3['status'] != 'passed':
        raise ValueError('隔离环境真实验收未通过')
    output.mkdir(parents=True, exist_ok=False)
    (output/'网页验收.json').write_bytes(raw)
    (output/'网页观察.json').write_bytes(browser_raw)
    (output/'挑战证据摘要.json').write_bytes(facts_raw)
    shutil.copyfile(challenge/'结果对照.png', output/'挑战结果.png')
    binding = dict(acceptance=str(acceptance.relative_to(ROOT)), acceptance_sha256=hashlib.sha256(raw).hexdigest(),
                   challenge=facts['job'], challenge_code=facts['code'], runtime_code=web['code_commit'],
                   runtime_manifest_sha256=web['final_integrity']['manifest_sha256'], new_model_calls=0)
    (output/'来源绑定.json').write_text(json.dumps(binding, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    pdfmetrics.registerFont(TTFont('yahei', 'C:/Windows/Fonts/msyh.ttc', subfontIndex=0))
    pdfmetrics.registerFont(TTFont('yahei-bold', 'C:/Windows/Fonts/msyhbd.ttc', subfontIndex=0))
    styles = {
        'title': ParagraphStyle('title', fontName='yahei-bold', fontSize=21, leading=31, spaceAfter=15),
        'h': ParagraphStyle('h', fontName='yahei-bold', fontSize=13, leading=20, spaceBefore=10, spaceAfter=8),
        'body': ParagraphStyle('body', fontName='yahei', fontSize=10, leading=16, spaceAfter=9, wordWrap='CJK'),
        'small': ParagraphStyle('small', fontName='yahei', fontSize=8.5, leading=13, spaceAfter=7, wordWrap='CJK'),
        'cell': ParagraphStyle('cell', fontName='yahei', fontSize=8.5, leading=13, wordWrap='CJK'),
        'head': ParagraphStyle('head', fontName='yahei-bold', fontSize=8.5, leading=13, textColor=colors.white, wordWrap='CJK'),
    }
    def p(text, style='body'):
        return Paragraph(html.escape(str(text)).replace('\n', '<br/>'), styles[style])
    def table(headers, rows, widths):
        t = Table([[p(x, 'head') for x in headers]]+[[p(x, 'cell') for x in row] for row in rows], colWidths=widths, repeatRows=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#253e5c')),
                              ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#edf3f8')]),
                              ('GRID',(0,0),(-1,-1),.4,colors.HexColor('#cbd5e1')),
                              ('VALIGN',(0,0),(-1,-1),'MIDDLE'),('TOPPADDING',(0,0),(-1,-1),6),
                              ('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        t.spaceAfter = 10
        return t
    labels = {'fixed_diverse':'稀疏固定', 'feedback_halving':'原缩减反馈', 'feedback_backtracking':'回退反馈'}
    regions = {'high':'珍珠南路', 'medium':'新浦路', 'low':'临滁路'}
    rows = [[regions[x['region']],labels[x['strategy']],f"{-x['core_change']:.4f}%",
             str(x['eligible']),str(x['logical_scenes'])+'/13'] for x in facts['outcomes']]
    story = [p('回退搜索与便携程序\n验收增补说明','title'),p('2026年9月22日 / 对应最新运行包 v4','small'),
             p('1  更强对照下，回退没有改变最终建议','h'),
             p('新挑战完成37个真实场景、259次TranStar模块调用。三个事前选区中，稀疏固定、原缩减反馈和回退反馈都找到三需求合格措施。原反馈与回退的最终治理道路及停车等级完全相同；回退多用3次逻辑评估，未观察到最终推荐增益。默认停车策略保持原规则。'),
             p('每区每策略最多13个逻辑场景：3个需求基准、最多9个治理场景及1次复跑。小汽车需求系数0.9、1、1.1；其他车型输入不变。相同计算共享原始输出，仍对每个策略分别计费。','small'),
             table(['固定评价核心','策略','最弱核心改善','合格候选数','逻辑场景'],rows,[99,108,104,91,105]),
             p('中密度区域第三候选由“下一步重复既有措施”触发回退，不能描述为失败后的挽救。它找到另一个合格方向，但最终仍选原来的三路段停车1级方案。高、低密度区没有触发回退。'),
             p('本轮区域排除了19份登记的历史范围。先前22场景对照使用另一组区域、每策略10次预算；其固定1/3、反馈2/3结果与本轮3/3不能作为同条件胜率变化比较。这里的3/3是三个区域的观察结果，不是统计保证。','small'),
             PageBreak(),p('2  改善与局部代价同时披露','title')]
    selected = [x for x in facts['outcomes'] if (x['region']=='medium' and x['strategy'] in ('fixed_diverse','feedback_halving')) or (x['region']!='medium' and x['strategy']=='feedback_halving')]
    riskrows = [[regions[x['region']]+' / '+('三策略相同' if x['region']!='medium' else labels[x['strategy']]),
                 f"{-x['core_change']:.4f}%",f"+{x['risks']['worst_inner_neighbor_time_change_pct']:.4f}%",
                 f"{x['risks']['worst_new_network_oversaturated_km']:.4f}"] for x in selected]
    story += [p('下表逐项取三需求中的最差值；不同指标的最差情况可能来自不同需求。新浦路回退反馈的最终措施与原缩减反馈相同，风险也相同。'),
              table(['区域 / 策略','最弱核心改善','1跳邻区耗时','毛新增过饱和 km'],riskrows,[177,110,110,110]),
              p('新浦路反馈方案核心改善更大，但1跳邻区、毛新增过饱和及恶化路段耗时也更高。高密度区即使通过原约束，1跳邻区仍可能增加约3.99%。通过2跳邻区、全网耗时及邻区毛新增过饱和约束，不表示每条道路都改善。'),
              Image(str(output/'挑战结果.png'),width=507,height=265.2),
              p('图：九组完整结果及各自计费预算。耗时采用Σ(qL/v)，不含全部节点延误；停车等级是离散模型类别。案例经过赛事调整，结果不能直接推断南京现实交通。','small'),
              p('独立核验重算原始输入输出、事前选区和69个决策，核对105个逻辑场景、36次共享观察。新基准复跑的速度、流量、饱和度和耗时四数组逐项一致。原始档案保留所有失败候选，不以最终建议替代全轨迹。','small'),
              PageBreak(),p('3  新版便携程序如何使用','title'),
              p('把完整ZIP解压到可写目录后，双击launch.cmd；待完整性与依赖检查通过，浏览器打开http://127.0.0.1:8790。初始地图显示32,060条真实案例道路，仅展示几何；新基准完成后才显示本次交通指标。'),
              p('第一次可按以下步骤操作：','h'),
              p('1. 选择停车治理，输入分析需求；道路留空会根据新基准自动选点。\n2. 做快速验收时取消主动稳健搜索、取消需求波动，预算设4，保留基准复现。\n3. 点击预览任务卡核对配置，再点击开始仿真一次。\n4. 等待4场景完成，查看算法决策、失败原因、逐路段指标及双图对照。\n5. 任务结果保存在智能体实验/jobs。确认任务完成后，在服务窗口按Ctrl+C停止。'),
              table(['真实验收','范围与结果'],[
                  ['v3 / 20260922_050947_9c2251','限制PATH、干扰PYTHONHOME/PYTHONPATH及用户site、不可用外部代理；包内Python和GCC完成4场景、28次调用。'],
                  ['v4 / '+web['job_id'],'通过新包网页提交4场景，完成28次调用；严格核验、封存、档案复核与源案例不变检查通过。'],
                  ['启动与界面','新解压目录含中文和空格；实际包装器编译通过。首次底图、真实结果切换与风险显示已检查；运行源码155项自动测试通过。']], [164,343]),
              p('运行包来源与取证','h'),
              p('运行代码：'+web['code_commit']+'\n运行清单SHA256：'+web['final_integrity']['manifest_sha256'],'small'),
              p('程序包和验收证据：成果/便携主程序/20260922_v4。挑战结论：成果/20260922回退搜索验收.md。原始实验：智能体实验/challenges/20260922_034228_3141eb。完整仓库为私有仓库，获取ZIP需要权限及Git LFS真实文件。','small'),
              p('上述均为同一台电脑的新解压目录验收，尚未证明另一台物理电脑可运行。本版未在新环境重复完整禁转/单向求解，也没有新版实机录像。运行仍遵守原TranStar授权；不修改许可证。','small')]
    def footer(canvas, doc):
        canvas.setFont('yahei',8)
        canvas.setFillColor(colors.HexColor('#475569'))
        canvas.drawString(44,24,'路策交通仿真智能体 / 最新验收增补 / 2026-09-22')
        canvas.drawRightString(A4[0]-44,24,str(doc.page))
    pdf = output/'最新实验与便携操作增补.pdf'
    doc = SimpleDocTemplate(str(pdf), pagesize=A4,leftMargin=44,rightMargin=44,topMargin=36,bottomMargin=42,
                            title='路策最新实验与便携操作增补',author='路策交通仿真智能体项目')
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    print(pdf)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('acceptance',type=Path)
    args = parser.parse_args()
    main(args.output.resolve(),args.acceptance.resolve())
