"""Build version-bound offline judging materials; never launch a traffic model.

Optional publishing dependencies: reportlab and pypdf. Core agent and package
verification remain standard-library only. Run prepare, render, inspect every
PDF page, and then seal with a hash-bound visual-QA record.
"""
import argparse
import hashlib
import html
import json
import math
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from traffic_agent.delivery import digest, read_bound_study, seal_package, test_count, verify_package

STUDY = '20260922_015104_1bd73b'
FONT = 'MicrosoftYaHei'
BOLD = 'MicrosoftYaHeiBold'
WIDTH = 507.27


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def prepare(output, test_log):
    from traffic_agent.study_audit import verify
    from traffic_agent.study_dashboard import study_map
    if output.exists():
        raise ValueError('使用新的输出目录，不能覆盖既有提交版本')
    changed = subprocess.check_output(['git', 'status', '--porcelain', '--untracked-files=all', '--', 'traffic_agent', 'scripts', 'tests', 'examples', 'start.ps1'], cwd=ROOT)
    if changed.strip():
        raise ValueError('请先提交发布代码，保证文档的代码版本可检出')
    job = ROOT/'智能体实验/studies'/STUDY
    state, protocol, selection, saved = read_bound_study(job)
    audit = verify(job, archived=True, write=False)
    count = test_count(test_log)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    regions = []
    for region in selection['regions']:
        item = {k: v for k, v in region.items() if k != 'scope'}
        item['scope_sizes'] = {k: len(v) for k, v in region.get('scope', {}).items()}
        item['strategies'] = state['regions'][region['id']]['strategies']
        regions.append(item)
    facts = dict(version=1, study=STUDY, code_commit=commit, generated_at=datetime.now(timezone.utc).isoformat(),
                 new_model_calls=0, audit=audit, test_count=count, source_counts=selection['counts'],
                 repeatability=state['repeatability'], started=state['started'], finished=state['finished'],
                 regions=regions, trace=state['trace'], physical_scenes=len(state['runs']),
                 feasible_regions={strategy: sum(x['strategies'][strategy]['result']['analysis']['eligible_count'] > 0
                                                for x in regions if x['status']=='selected') for strategy in ('fixed','feedback')})
    output.mkdir(parents=True)
    write(output/'事实摘要.json', facts)
    maps = []
    for name in ('medium_feedback_p1_0.9', 'medium_feedback_p2_0.9'):
        data = study_map(str(job), 'medium', 'feedback', name)
        # A focused, fully specified static figure. The interactive hall keeps
        # all network roads; the PDF uses the same frozen core and neighbors.
        data['links'] = [x for x in data['links'] if x['group'] != 'network']
        maps.append(data)
    write(output/'图表数据.json', maps)
    sources = [
        ('E01','赛题原文','工作材料/文本摘录/赛题.txt'),
        ('E02','软件手册','工作材料/文本摘录/城市交通版说明书.txt'),
        ('E03','参数依据与容差','docs/参数依据与决策容差.md'),
        ('E04','前瞻协议',f'智能体实验/studies/{STUDY}/protocol.json'),
        ('E05','事前选区',f'智能体实验/studies/{STUDY}/selection.json'),
        ('E06','研究状态与指标',f'智能体实验/studies/{STUDY}/state.json'),
        ('E07','原始独立核验',f'智能体实验/studies/{STUDY}/verification.json'),
        ('E08','追加决定与观察',f'智能体实验/studies/{STUDY}/events.jsonl'),
        ('E09','完整原始档案清单',f'智能体实验/studies/{STUDY}/archive_manifest.json'),
        ('E10','本版自动测试',test_log.relative_to(ROOT).as_posix()),
        ('E11','复制档案核验','docs/validation/20260922_study_portable.json'),
        ('E12','地图浏览器验收','docs/validation/20260922_browser_check.json'),
        ('E13','禁转历史完整结果','docs/禁转完整组合结果.md'),
        ('E14','单向历史完整结果','docs/硬禁行完整组合结果.md'),
        ('E15','历史容差和范围敏感性','成果/复评/20260919_v2/report.md'),
        ('E16','历史演示范围','成果/演示/README.md'),
        ('E17','启动和异机验收','docs/统一启动与异机验收.md'),
    ]
    index = []
    (output/'证据').mkdir()
    for identifier, title, relative in sources:
        source = ROOT/relative
        data = source.read_bytes()
        target = f'证据/{identifier}_{title}{source.suffix}'
        (output/target).write_bytes(data)
        index.append(dict(id=identifier, title=title, source=relative, packaged=target, bytes=len(data), sha256=digest(data)))
    # A compact source snapshot is useful for review, but the native programs
    # and complete database remain in the full repository.
    code_paths = sorted(p for p in (ROOT/'traffic_agent').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in ('.py','.json','.html'))
    code_paths += sorted((ROOT/'scripts').glob('*.py')) + [ROOT/'start.ps1']
    code_paths += sorted((ROOT/'examples').glob('*.json'))
    for source in code_paths:
        relative = source.relative_to(ROOT).as_posix()
        target = output/'代码快照'/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        data = source.read_bytes()
        index.append(dict(id='CODE', title=source.name, source=relative, packaged=target.relative_to(output).as_posix(), bytes=len(data), sha256=digest(data)))
    write(output/'证据索引.json', index)
    write(output/'本次导出原始核验.json', audit)
    (output/'README.md').write_text(f'''# 路策离线评审阅读包

先打开研究报告.pdf了解研究与证据，再按操作手册.pdf启动完整项目。两份PDF已嵌入图表，无需浏览器、在线字体或Mermaid插件。代码版本：`{commit}`；前瞻任务：`{STUDY}`。

本目录含正式阅读版报告、手册、源码快照和关键证据索引，**不含完整分析数据库、TranStar原生程序及视频文件**。它不是独立可运行安装包。第三方资料和源码保持原有归属，未重新授予许可。

完整运行数据与历史视频来自私有仓库 https://github.com/Tabris-Ayanami/its-transtar-agent ，需有访问权限并执行Git LFS拉取。赛题要求的五类成果及缺项在手册最后一页明确列出。

所有发布数字来自绑定的研究状态。构建阅读包重新读取并核验原始档案，新增模型调用为0；不能把这次导出当成新的仿真或异机重跑。12,891文件原始清单的副本用于定位完整仓库证据，单独持有此阅读包不能完成原始模型档案核验。

在完整项目根目录核对本目录或ZIP：

```powershell
python scripts/verify_submission.py <阅读包目录或ZIP路径>
```

此命令只核对阅读材料摘要和完整性。不是数字签名，也不替代原始实验独立核验。PDF、协议和状态中的版本属于各自运行时期，历史记录保持原样。

仍待补齐：另一台物理电脑实跑、另一套兼容数据集、与本版一致的实机视频，以及主办方另行通知的格式、大小、时长等提交限制。PDF采用通用阅读格式，不声称主办方已经认可本版式。
''', encoding='utf-8')
    return facts


def render(output, font_dir):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Flowable

    if (output/'package_manifest.json').exists():
        raise ValueError('已封存版本不可重新排版')
    pdfmetrics.registerFont(TTFont(FONT, str(font_dir/'msyh.ttc'), subfontIndex=0))
    pdfmetrics.registerFont(TTFont(BOLD, str(font_dir/'msyhbd.ttc'), subfontIndex=0))
    f, maps = read(output/'事实摘要.json'), read(output/'图表数据.json')
    styles = {
        'body': ParagraphStyle('body',fontName=FONT,fontSize=10.5,leading=17.5,spaceAfter=9,wordWrap='CJK',textColor=colors.black),
        'small': ParagraphStyle('small',fontName=FONT,fontSize=8.7,leading=13.5,spaceAfter=7,wordWrap='CJK',textColor=colors.HexColor('#364152')),
        'title': ParagraphStyle('title',fontName=BOLD,fontSize=24,leading=35,spaceAfter=17,textColor=colors.black),
        'h1': ParagraphStyle('h1',fontName=BOLD,fontSize=17,leading=26,spaceAfter=14,textColor=colors.black),
        'h2': ParagraphStyle('h2',fontName=BOLD,fontSize=12.5,leading=20,spaceBefore=7,spaceAfter=7,textColor=colors.black),
        'cell': ParagraphStyle('cell',fontName=FONT,fontSize=9.3,leading=14,wordWrap='CJK'),
        'head': ParagraphStyle('head',fontName=BOLD,fontSize=9.3,leading=14,wordWrap='CJK',textColor=colors.white),
        'code': ParagraphStyle('code',fontName=FONT,fontSize=8.3,leading=13.5,spaceAfter=8,wordWrap='CJK'),
    }
    def para(text, style='body'):
        return Paragraph(html.escape(str(text)).replace('\n','<br/>'), styles[style])
    def heading(text): return para(text,'h2')
    def table(headers, rows, widths, align=()):
        cells = [[para(x,'head') for x in headers]] + [[para(x,'cell') for x in row] for row in rows]
        t=Table(cells,colWidths=[WIDTH*w/sum(widths) for w in widths],repeatRows=1,hAlign='LEFT')
        rules=[('BACKGROUND',(0,0),(-1,0),colors.HexColor('#253e5c')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f1f5f9')]),('GRID',(0,0),(-1,-1),.45,colors.HexColor('#D9D9D9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]
        for col in align: rules.append(('ALIGN',(col,0),(col,-1),'CENTER'))
        t.setStyle(TableStyle(rules)); t.spaceAfter=10; return t
    def page(title, *content): return [para(title,'h1'), *content, PageBreak()]
    def small(text): return para(text,'small')
    def command(text): return para(text,'code')
    def footer(canvas, doc):
        canvas.setFont(FONT,8)
        canvas.setFillColor(colors.HexColor('#4b5563'))
        canvas.drawString(44,25,f'路策交通仿真智能体  |  代码 {f["code_commit"][:7]}  |  2026年9月22日')
        canvas.drawRightString(A4[0]-44,25,str(doc.page))
    def build(name, story):
        if isinstance(story[-1],PageBreak): story.pop()
        doc=SimpleDocTemplate(str(output/name),pagesize=A4,leftMargin=44,rightMargin=44,topMargin=39,bottomMargin=42,
                              title='路策交通仿真智能体'+name[:-4],author='路策交通仿真智能体项目',pageCompression=1)
        doc.build(story,onFirstPage=footer,onLaterPages=footer)

    class Pipeline(Flowable):
        def __init__(self): super().__init__(); self.width=WIDTH; self.height=160
        def draw(self):
            c=self.canv
            labels=[('任务与知识规则','解析问题  校验预算'),('新基准与固定范围','候选生成  匹配需求'),('真实TranStar计算','七个原生模块与日志'),('指标与约束判断','核心  邻区  全网'),('实测反馈与再求解','缩小  增强  撤回'),('推荐与证据输出','完整需求  复跑  核验')]
            for i,(title,sub) in enumerate(labels):
                col=i%3; row=i//3; x=col*173; y=93-row*81
                c.setStrokeColor(colors.HexColor('#73849a')); c.setLineWidth(.6)
                c.rect(x,y,160,59,fill=0,stroke=1)
                c.setFillColor(colors.black); c.setFont(BOLD,10.5); c.drawCentredString(x+80,y+36,title)
                c.setFont(FONT,8.5); c.drawCentredString(x+80,y+17,sub)
                if col<2:
                    c.line(x+162,y+29,x+171,y+29); c.line(x+168,y+32,x+171,y+29); c.line(x+168,y+26,x+171,y+29)
            c.line(426,93,426,82); c.line(426,82,80,82); c.line(80,82,80,72)
            c.line(77,76,80,72); c.line(83,76,80,72)

    class ComparisonChart(Flowable):
        def __init__(self): super().__init__(); self.width=WIDTH; self.height=215
        def draw(self):
            c=self.canv
            for i,(strategy,color,label) in enumerate([('fixed','#466b98','固定策略'),('feedback','#1d8574','反馈策略')]):
                c.setFillColor(colors.HexColor(color)); c.rect(178+i*94,195,9,9,stroke=0,fill=1)
                c.setFillColor(colors.black); c.setFont(FONT,9); c.drawString(191+i*94,195,label)
            for panel in (0,1):
                x0=24+panel*260; y0=35; chartw=213; scale=53 if panel==0 else 10.6
                c.setFont(BOLD,10); c.drawCentredString(x0+chartw/2,177,'合格统一措施数' if panel==0 else '实际逻辑场景数  上限10')
                for tick in ([0,1,2] if panel==0 else [0,5,10]):
                    y=y0+tick*scale; c.setStrokeColor(colors.HexColor('#dce3ea')); c.line(x0,y,x0+chartw,y)
                    c.setFillColor(colors.black); c.setFont(FONT,8); c.drawRightString(x0-5,y-3,str(tick))
                for j,r in enumerate(f['regions']):
                    for i,(strategy,color) in enumerate([('fixed','#466b98'),('feedback','#1d8574')]):
                        result=r['strategies'][strategy]['result']; value=result['analysis']['eligible_count'] if panel==0 else result['summary']['logical_scenes']
                        x=x0+21+j*70+i*24
                        c.setFillColor(colors.HexColor(color)); c.rect(x,y0,21,value*scale,stroke=0,fill=1)
                        c.setFillColor(colors.black); c.setFont(FONT,9); c.drawCentredString(x+10.5,y0+value*scale+7,str(value))
                    c.setFont(FONT,8.7); c.drawCentredString(x0+44+j*70,17,{'high':'湖滨路','medium':'虎桥路等','low':'毛纺厂路'}[r['id']])

    class PairedMap(Flowable):
        def __init__(self): super().__init__(); self.width=WIDTH; self.height=246
        def draw(self):
            c=self.canv
            points=[p for x in maps[0]['links'] for line in x['paths'] for p in line]
            xmin,xmax=min(p[0] for p in points),max(p[0] for p in points)
            ymin,ymax=min(p[1] for p in points),max(p[1] for p in points)
            w,h=246,196; scale=min((w-28)/(xmax-xmin),(h-20)/(ymax-ymin))
            for panel,data in enumerate(maps):
                x0=panel*261; y0=28
                def xy(p): return (x0+w/2+(p[0]-(xmin+xmax)/2)*scale,y0+h/2+(p[1]-(ymin+ymax)/2)*scale)
                c.setFillColor(colors.HexColor('#f5f8fb')); c.rect(x0,y0,w,h,stroke=0,fill=1)
                c.setFont(BOLD,10); c.setFillColor(colors.black)
                c.drawCentredString(x0+w/2,233,'首轮三条治理  低需求失败' if panel==0 else '反馈缩为一条  低需求通过')
                for link in sorted(data['links'],key=lambda x:x['group']=='core'):
                    for path in link['paths']:
                        pts=[xy(p) for p in path]
                        dx,dy=pts[-1][0]-pts[0][0],pts[-1][1]-pts[0][1]; d=math.hypot(dx,dy) or 1
                        pts=[(x-dy/d*.9,y+dx/d*.9) for x,y in pts]
                        p=c.beginPath(); p.moveTo(*pts[0])
                        for point in pts[1:]: p.lineTo(*point)
                        if link['group']=='core':
                            c.setStrokeColor(colors.HexColor('#8b6fb4')); c.setLineWidth(4); c.drawPath(p)
                        a,b=link['before'],link['after']; delta=link['length']*(b[1]/b[0]-a[1]/a[0])
                        color='#17806d' if delta<-.01 else '#c8514d' if delta>.01 else '#9eafbe'
                        c.setStrokeColor(colors.HexColor(color)); c.setLineWidth(1.3); c.drawPath(p)
                        if link['treated']:
                            mid=pts[len(pts)//2]; c.setFillColor(colors.black)
                            diamond=c.beginPath(); diamond.moveTo(mid[0],mid[1]+3)
                            for v in [(3,0),(0,-3),(-3,0)]: diamond.lineTo(mid[0]+v[0],mid[1]+v[1])
                            diamond.close(); c.drawPath(diamond,fill=1,stroke=0)
            for i,(color,label) in enumerate([('#17806d','耗时减少'),('#c8514d','耗时增加'),('#9eafbe','绝对变化≤0.01'),('#8b6fb4','固定核心'),('#000000','本次措施')]):
                c.setFillColor(colors.HexColor(color))
                if i==4:
                    p=c.beginPath(); p.moveTo(i*103+5,12); p.lineTo(i*103+9,8); p.lineTo(i*103+5,4); p.lineTo(i*103+1,8); p.close(); c.drawPath(p,stroke=0,fill=1)
                else: c.rect(i*103,7,10,3,stroke=0,fill=1)
                c.setFillColor(colors.black); c.setFont(FONT,8); c.drawString(i*103+14,5,label)

    medium=next(r for r in f['regions'] if r['id']=='medium')
    winner=medium['strategies']['feedback']['result']['analysis']['recommendation']
    selected=next(p for p in medium['strategies']['feedback']['result']['analysis']['policies'] if p['name']==winner)
    matched=[next(r for r in medium['strategies']['feedback']['runs'] if r['name']==check['run']) for check in selected['checks']]
    one=next(r for r in matched if r['demand_factor']==1)
    pct=lambda x:f'{x:+.6f}%'
    story=[para('路策交通仿真智能体','title'),para('城市拥堵治理决策支持研究报告','h1'),
           para('以实测反馈调整停车措施的前瞻区域对照研究'),
           small(f'代码版本 {f["code_commit"][:7]}    实验 {STUDY}    2026年9月22日'),
           heading('摘要'),
           para(f'路策将任务解析、候选生成、TranStar调用、约束评价和反馈调整连接为可追溯流程。在事前选定的三个区域、每区每策略最多10个逻辑场景的相同预算下，反馈策略在{f["feasible_regions"]["feedback"]}个区域找到跨需求合格措施，固定策略在{f["feasible_regions"]["fixed"]}个区域找到。新增贡献来自虎桥路与象贤路：首轮三条道路治理引起邻区恶化，智能体缩为一条后，在0.9、1.0、1.1小汽车需求下全部通过预设约束。'),
           para('该方案的固定核心路段耗时下降6.23%至7.68%。湖滨路未找到合格方案，毛纺厂路两策略持平，失败和局部代价均保留。结论只适用于赛事调整案例中的选区和离散需求，不代表南京实测交通或普遍成功率。'),
           para(f'前瞻实验实际完成{f["physical_scenes"]}个物理场景、{f["audit"]["native_module_calls"]}次原生模块调用；32,981条记录的四类基准输出复跑一致。本版{f["test_count"]}项自动测试通过。导出报告时重新核验原始档案，未新增模型调用。'),
           table(['评分关注点','本报告阅读位置'],[['方案创新性 30分','第2至4节：机制、反馈与相同预算对照'],['仿真合理性与效果 20分','第3至8节：口径、参数、敏感性和代价'],['智能体完整性 20分','第2节及第6节：真实调用和反馈轨迹'],['程序质量与可运行性 20分','第9节及操作手册：启动、验证与复现'],['成果展示 10分','第5至7节：完整比较、地图与失败解释']],[150,357]),
           small('表中分值来自赛题E01，用于定位证据，不是自评得分或官方给分。'),PageBreak()]
    story+=page('1 研究对象与作用机制',
        para('研究聚焦道路停车干扰与局部交通组织。赛事提供的南京案例经过数值调整，因此本研究回答的是特定模型与输入下措施如何改变分配及指标。分析窗口采用一小时流量口径，需求系数只缩放小汽车OD，其他车型输入保持不变。'),
        heading('从干扰假设到可检查的模型响应'),
        para('停车干扰可能降低有效通行能力。程序修改有向道路管理文件ManageL的停车类别，经原管理预处理模块生成修正容量，再由原生交通分配重新计算流量、速度和负荷。停车类别改变只表达离散情景，实际容量增量由模型给出，不能按类别差线性推算。'),
        table(['类别','手册含义','本系统中的用途'],[['0','无影响 严格禁止停车','控制器不自动选择'],['1','轻微影响 需求少且规范','更强治理假设，需要停车需求承接条件'],['2','中度影响 需求较大且规范','首轮规范停车情景'],['3至4','较严重或严重影响','自动候选的原停车类别']],[45,170,292]),
        small('来源E02表2-12，参数对应和现实条件见E03。类别5为阻塞，不在当前自动停车候选范围内。'),
        para('候选条件为城市2/3类道路、原停车3/4、长度0.1至1.5km、流量至少500pcu/h、1<V/C≤4、速度小于40km/h，且路名有效、非匝道。这些是公开的聚焦规则，未标定为最优门槛。极端高负荷和短连接线被过滤，可能产生选择偏差。'),
        heading('容量上升仍可能需要撤回'),
        para('湖滨路154→2607在高需求首轮中，修正容量由335.53升至412.96pcu/h，观察速度仍为9km/h，核心耗时未改善。程序保留这个反例并拒绝推荐。反例说明需要同时核查模型联动与交通收益，不能把成功写入字段当作治理成功。'),
        para('规范停车需要泊位承接、步行距离、收费与执法条件。当前缺少可信的现实数据，报告不填写成本或净社会收益，也不将联合仿真中的逐路段收益解释成单点干预的因果贡献。'))
    story+=page('2 智能体结构与反馈闭环',Pipeline(),
        small('图1 规则智能体的实际流程。反馈返回求解环节；归档保留每一步输入、决定和观察。'),
        para('任务卡JSON是执行依据，中文输入通过受限规则模板转换；系统不声称通用语言理解，也不依赖大语言模型服务。知识规则记录软件字段、指标口径、候选约束和能力边界。前端与命令行共用执行控制器。'),
        para('每个场景从原案例复制，仅修改白名单字段。道路、公交、管理三个预处理模块后，依次执行机动车分配、路段负荷、节点负荷和节点排队延误，共七个原有模块。任务独立配置与操作系统持有的锁保证串行求解，原软件授权检查保留。'),
        heading('实际改变下一步计算的规则'),
        para('首轮使用冻结核心的停车2级。任一需求失败后，候选不再具备统一推荐资格；若有多条措施，按该次联合治理的路段qL/v减少量保留前半，至少一条，重新测试。若三个需求均通过但仍有速度或修正负荷瓶颈，才测试1级。失败的单路段或无增强条件时停止。'),
        para('前瞻对照仅允许两项措施候选。固定策略第二项始终为全部核心停车1级；反馈策略的第二项取决于本策略已经完成的观察。两策略当轮决定一起登记后，才开始求解或读取该轮结果，独立核验能够逐步重放这些决定。'),
        table(['组件','对应实现'],[['解析和规则','task.py  knowledge.json'],['指标和固定范围','data.py  study.py'],['调用与反馈','runner.py  agent.py  study_runner.py'],['独立核验与展示','study_audit.py  study_dashboard.py']],[125,382]),
        small('代码快照随阅读包提供；原生程序、完整数据库和历史输出须从完整仓库取得。'))
    story+=page('3 指标约束与推荐规则',
        table(['指标','定义与单位','解释'],[['加权速度','Σ(qL) ÷ Σ(qL/v)\nkm/h','按运输量加权，不直接平均速度'],['路段耗时','Σ(qL/v)\npcu·h','不含独立完整节点延误'],['过饱和里程','Σ L×I(软件V/C>1)\n有向km','双向分别计，不是中心线长度'],['毛新增过饱和','基准V/C≤1且方案>1的有向长度','不被别处缓解抵消']],[91,168,248]),
        small('q为pcu/h，L为km，v为km/h。TimeLink仅作输出核对；发现标志值时不拼凑全系统总旅行时间。'),
        heading('冻结的通过条件'),
        table(['范围','前瞻停车实验硬约束'],[['固定核心','路段耗时严格改善'],['两跳邻区','耗时增幅≤0.5%，毛新增过饱和≤0.1km'],['全网','耗时增幅≤0.1%'],['跨需求','同一措施在0.9、1.0、1.1全部通过且无缺测']],[95,412]),
        para('这些是实验假设，不是工程规范。核心、邻区与全网在基准时固定。反馈缩小干预不会缩小评价范围，不能通过删除恶化路段获得合格结论。显式禁转和单向历史实验另有全网严格改善约束，不与停车规则混算。'),
        heading('微小差异不无条件压倒措施简单'),
        para('tolerance-v2先筛除失败与缺测候选，再依次保留距当前最优全网变化率不超过0.001个百分点、邻区不超过0.01个百分点、邻区新增过饱和不超过0.001km的候选，最后优先较少措施、较少停车1级措施，按名称稳定排序。每步使用固定最优值作锚，避免近似关系连锁扩张。'),
        para('容差是公开的决策分辨率假设，不是由数值噪声估计的统计阈值。确定性复跑相同不能证明模型无误差。历史无版本任务继续使用旧规则；新规则的复评保存在独立目录，原推荐、输入和日志保持原样。[E03 E15]'))
    regionrows=[]
    for r in f['regions']:
        regionrows.append([{'high':'高','medium':'中','low':'低'}[r['id']], '、'.join(r['roads']),f'{r["density"]:.6f}',f'{r["seed"][0]}→{r["seed"][1]}',f'{r["scope_sizes"]["core"]} / {r["scope_sizes"]["neighbor"]}'])
    story+=page('4 前瞻选区与相同预算设计',
        para(f'新基准提供{f["source_counts"]["candidates"]}条完整候选，去除重复核心集合后有{f["source_counts"]["seeds"]}个种子；{f["source_counts"]["legal_seeds"]}个与登记的四个历史范围不相交。候选长度密度为最终范围内候选有向长度占有效道路有向长度的比例。该密度不是现实停车密度。'),
        para('不同密度取值排序后均分为三层，同密度不拆开。按高、中、低顺序，在每层以种子节点编号稳定选择第一个与已选范围不重叠的区域。空层保留缺项；本轮三层均选出区域。范围和名单在任何治理仿真前冻结，不按结果补选。[E04 E05]'),
        table(['层','区域','候选密度','种子','核心 / 邻区'],regionrows,[28,132,86,113,148]),
        small('核心与邻区均按有向路段计。历史排除范围限于登记的停车主实验、环洲路、红景路禁转和长江村路单向实验。'),
        heading('最多10个逻辑场景的构成'),
        table(['组成','每区每策略预算','公平规则'],[['三个需求基准','3','共享真实计算，各策略均计费'],['两项候选乘三个需求','最多6','任一需求失败即提前停止该候选'],['新的基准复跑','1','必须真正重算，不复用缓存']],[126,114,267]),
        para('同措施、需求、输入和程序只实际求解一次，所有引用策略仍各记一次逻辑场景。未用预算不转给另一策略。三个区域六个策略最多60个逻辑场景，理论最多31个去重物理场景。实际共52个逻辑场景、22个物理场景和154次模块调用。'),
        para('28条动作决定对应18个不同治理场景和10次共享治理观察。每项候选按1.1、1.0、0.9顺序测试；缺测不记为通过。重复基准仅验证确定性，不作为独立样本扩大统计量。'))
    allrows=[]
    for r in f['regions']:
        for strategy in ('fixed','feedback'):
            res=r['strategies'][strategy]['result']; a=res['analysis']
            allrows.append(['、'.join(r['roads']), '固定' if strategy=='fixed' else '反馈',str(a['eligible_count']), '基准回退' if not a['eligible_count'] else '单条停车2级',str(res['summary']['logical_scenes'])+'/10'])
    story+=page('5 全部区域的对照结果',
        table(['区域','策略','合格数','统一建议','逻辑场景'],allrows,[137,55,60,145,110]),
        Spacer(1,14),ComparisonChart(),
        small('图2 同预算下的合格措施数量和实际场景使用。合格数是措施数，不能误读为独立成功样本数。'),
        para('反馈策略在两个区域找到合格统一措施，固定策略在一个区域找到。差异来自中密度区对外溢失败的反馈调整；低密度两策略均找到两项合格措施，高密度均回退基准。报告保留全部事前区域，没有只展示成功点。'),
        para('固定策略是同核心两档强度搜索，尚不是最强的所有静态或优化方法。当前结果支持此预算、此候选规则与此比较对象下的反馈贡献，不证明规则全局最优，也不能由三个区域推断普遍成功率。'))
    detail=[]
    for r in matched:
        d=r['comparison']['delta']
        detail.append([str(r['demand_factor']),pct(d['core']['running_time_change_pct']),pct(d['neighbor']['running_time_change_pct']),pct(d['network']['running_time_change_pct'])])
    metricrows=[]
    for key,label,unit in [('weighted_speed_kmh','加权速度','km/h'),('running_time_pcu_h','路段耗时','pcu·h'),('oversaturated_km','过饱和有向里程','km')]:
        metricrows.append([label,unit,f'{one["comparison"]["before"]["core"][key]:.6f}',f'{one["metrics"]["core"][key]:.6f}'])
    story+=page('6 实测反馈如何解决邻区失败',
        para('虎桥路与象贤路的固定核心为74→645、645→74、645→782，共三条，邻区47条。首轮三条同时停车3→2，在需求1.1和1.0通过，但0.9时邻区耗时增加3.168144%，超过0.5%上限。固定加强到1级仍增加3.154824%，应当拒绝。'),
        para('反馈只利用本策略已经观察到的联合治理结果，保留74→645一条停车2级，并从高需求重新测齐。三个需求均通过，统一推荐为medium_feedback_p2_1。下面的核心统计始终覆盖原三条道路。'),
        table(['小汽车需求','核心耗时变化','邻区耗时变化','全网耗时变化'],detail,[90,139,139,139]),
        small('均相对同需求的独立无治理基准。变化率为百分数；不是百分点的速度变化。'),
        heading('基准需求下的多指标对照'),
        table(['固定核心指标','单位','无治理基准','统一反馈方案'],metricrows,[143,68,148,148]),
        para('核心加权速度与耗时改善，但核心过饱和里程没有减少，不能写成拥堵消除。治理方向74→645的管理修正容量由1172.46升至1279.04pcu/h，速度由24.23升至29.37km/h；联合分配同时改变相邻方向和全网流量。'),
        para('所选措施在三个需求下没有毛新增全网过饱和，但个别道路耗时增加，恶化路段增量合计最高0.346639pcu·h。总收益与局部代价同时保留，规则通过不等于每条道路都受益。[E06 E07 E08]'))
    story+=page('7 同需求地图与不利变化',PairedMap(),
        small('图3 两图均为小汽车需求0.9，使用相同基准、相同裁切范围和色彩规则。几何来自案例SHP；双向道路稍作屏幕偏移。固定核心为紫色底线，菱形标示实际治理方向。耗时色彩的0.01pcu·h阈值仅用于显示，不改变数值评价。'),
        para('左图呈现首轮三条道路共同治理，右图呈现反馈后仅治理一个方向。缩小措施后，紫色固定核心仍保持三条，所以比较没有把恶化道路从评价中剔除。地图只裁切固定核心与邻区；全网指标仍使用全部32,060条有效有向道路。'),
        table(['低需求0.9','首轮三条停车2级','反馈单条停车2级'],[['邻区耗时变化','+3.168144%','-0.002309%'],['全网耗时变化','-0.032774%','-0.002715%'],['统一推荐资格','失去资格','三个需求均通过']],[157,175,175]),
        para('首轮全网总耗时改善更多，但邻区损失越过预设边界。反馈方案放弃部分全网收益以满足局部约束。该取舍应当清楚展示，不能仅把右图包装为所有指标都更优。'),
        para('完整交互地图还支持需求、策略、图层和前后切换，点击道路查看速度、流量、软件V/C、停车类别与容量联动。原始几何匹配检查为32,060/32,060；截图和动画不参与指标计算。[E12]'))
    story+=page('8 失败边界与敏感性',
        heading('湖滨路的无效结果'),
        para('容量提高后观察速度仍未提高；固定加强和反馈缩小都没有得到严格的核心耗时改善。两策略均停止并保留基准，各使用6个逻辑场景。剩余预算没有用来事后换点，未计算需求明确保留缺测。'),
        heading('毛纺厂路的微小收益与近邻代价'),
        para('两策略都找到停车2级和1级两项合格措施，均选择温和的2级。所选措施最弱核心耗时下降2.584582%，最差两跳邻区耗时增加0.037939%，一跳邻区增加0.017002%，全网最差改善约0.000006%。1级的微小全网优势处于事前容差内，因此没有驱动更强措施。'),
        para('这里的“通过”只表示满足既定上限。恶化路段耗时增量合计最高0.484986pcu·h；这类代价不能用净收益抹去，也不能把接近零的全网变化说成显著提升。'),
        heading('历史探索性敏感性与新前瞻证据分开'),
        para('9月19日对四份已知历史实验实施120项范围与阈值再分析及16项容差扫描。显式禁转在一跳邻区口径下失去统一资格；停车主动主实验把新增邻区过饱和上限收紧到0.05km后改选停车2级。这表明推荐依赖研究范围和阈值。[E15]'),
        para('该历史复评没有重新求解交通模型，也没有更改原实验约束。9月22日前瞻研究则在看治理结果前固定新区域和规则，真正执行了新场景。两类证据都保留各自版本，不能把事后扫描当作独立盲测。'),
        table(['证据能支持什么','当前仍不能支持什么'],[['三种离散需求下同一措施的合格性','连续需求区间、跨城市或其他模型普遍有效'],['相同输入输出的确定性复现','模型已校准、统计显著或没有结构误差'],['联合仿真中的路段响应','每条措施独立的因果贡献或现实最佳方案']],[244,263]))
    story+=page('9 扩展能力与程序验证',
        heading('禁转扩展保留版本与实际响应'),
        para('显式4797→5990→5991禁转通过原生状态图适配接入四车型五轮增量分配。历史完整组合共13场景、91次顶层模块调用、260次适配及260次同输入原版参考求解。三个有正基准转移量的车型归零，货车基准为零不作为正例；九类道路数组复跑一致。[E13]'),
        para('旧冻结规则三需求均选S3组合；9月19日v2只读复评改选S2单独禁转，因为S3最差全网优势只有0.000034921个百分点，近似相同时无需叠加停车。高需求S2和S3都毛新增约12.588473km全网过饱和，不能只展示净增约0.860446km。[E15]'),
        heading('单向反例验证拒绝能力'),
        para('长江村路新增方向3237→3236硬闭边后，目标方向实际流量归零；13场景、91次顶层调用和260次车型分轮求解已核验。然而单向及组合使邻区耗时增加约167%至175%，全部被拒绝。接口生效与政策有效必须分别验证。[E14]'),
        heading('可运行性与原始证据'),
        para(f'本版{f["test_count"]}项自动测试通过，包括不可用外部代理环境下的本地HTTP测试，以及过期核验、未完成实验、缺失材料、LFS占位和同长度篡改的拒绝测试。前瞻任务独立审计核对七个进程、退出码、原始输出流、新鲜文件、停车逐行修改、OD逐元素缩放、模型内部需求、固定范围指标、动作时序、共享计费及报告一致性。'),
        para('归档保存12,891个文件的内容摘要。字节复制件在新的文件时间戳下通过档案模式核验；同机复制不能代替另一台电脑求解。历史清洁Python环境和新的模型runtime曾完成四场景28次真实调用；9月19日启动器也完成新四场景28次调用，两者不并入本轮154次。'),
        para('软件授权、缺失程序、编译器、输入错误和预算不足均明确报错。施工、公交优先、信号和错峰仍有各自未验证的能力门槛，不将停车流程冒充这些方向。'))
    entries=[(x['id'],x['title'],x['source']) for x in read(output/'证据索引.json') if x['id']!='CODE']
    story+=page('10 证据索引与后续验证',
        para('本阅读包中的证据文件使用E编号，便于离线定位。完整原始数组、程序与数据库位于仓库对应路径，文件级SHA256见证据索引.json；报告中的研究数字绑定到E06，并由E07及本次导出核验复核。'),
        *[small(f'{identifier}  {title}\n{path}') for identifier,title,path in entries],
        para('剩余验证包括另一台物理电脑实跑、另一套兼容数据集和与本版一致的新实机演示录像。现实应用还须补充停车承接、实施成本及交通行为校准。正式提交应再核对主办方另行通知的格式、文件大小和视频时长；本PDF采用通用阅读格式。'))
    build('研究报告.pdf',story)

    manual=[para('路策交通仿真智能体','title'),para('操作与复现手册','h1'),small(f'对应代码 {f["code_commit"][:7]}    前瞻任务 {STUDY}'),
        para('本手册帮助评审与协作者区分阅读结果、核验档案和重新运行模型。先检查本机环境，再按需要选择入口；查看报告或地图不会自动产生新的仿真。'),
        table(['目标','入口','调用模型'],[['离线了解项目','研究报告.pdf','否'],['交互查看三地区结果','serve_region_study.py  端口8782','否'],['查看和新建普通任务','start.ps1  端口8769','提交任务后'],['检查原始档案','verify_region_study.py --archive','否'],['重新执行三地区研究','benchmark_regions.py','是']],[144,276,87]),
        heading('运行条件'),
        para('完整项目需要Windows、Python 3.12、已通过Git LFS拉取的原生程序和南京案例，以及TranStar正常授权。停车主线不需要gcc；显式禁转与单向扩展需要兼容Windows的MinGW gcc。源码快照或本阅读包不能替代完整模型数据。'),
        command('git lfs install\ngit clone https://github.com/Tabris-Ayanami/its-transtar-agent.git\ncd its-transtar-agent\ngit lfs pull'),
        para('仓库为私有仓库，需要已有访问权限。若复核本版，可检出下列代码提交后再次执行git lfs pull。不要覆盖尚未保存的本地工作。'),
        command(f'git checkout {f["code_commit"]}'),
        small('上述Git命令从完整仓库获取程序与数据库。阅读包保留材料归属，不另行授予第三方软件许可。'),PageBreak()]
    manual+=page('1 启动与第一次真实运行',
        para('在项目根目录的PowerShell窗口执行以下命令。start.ps1优先使用项目.venv，其次使用Python启动器的3.12版本，最后检查PATH中的Python。'),
        command('.\\start.ps1 -Mode check\n.\\start.ps1'),
        para('启动大地图工作台后打开http://127.0.0.1:8769。若端口占用，改用-Port 8771；程序不会结束未知服务。依赖检查通过只说明所需文件和环境可用，不代表授权和真实求解成功。'),
        command('.\\start.ps1 -Mode run'),
        para('这条命令真正运行默认四场景停车快速验收：新基准、两档停车治理和新的基准复跑。历史同机参考耗时约9分钟，实际速度取决于设备与授权状态。程序输出JOB目录，结果写入智能体实验/jobs，不覆盖原输入。'),
        command('python scripts/verify_experiment.py 智能体实验/jobs/<实际JOB编号> --seal'),
        para('将尖括号整体替换为控制台打印的实际编号。核对四场景、28次模块调用和复跑结果；网页显示完成后仍应检查独立核验。单需求快速验收不证明跨需求稳健。'),
        heading('脚本执行策略与Python路径'),
        para('若PowerShell不允许运行本地脚本，直接使用等价Python入口，无需修改系统执行策略。使用的解释器应为Python 3.12。'),
        command('py -3.12 -m traffic_agent.startup --mode check\npy -3.12 -m traffic_agent.startup --mode serve --port 8769'),
        para('启动记录保存在智能体实验/startup，包含代码提交、Python路径、依赖、任务卡、输出目录、剩余空间和当前代码摘要。代理绕过只在启动进程内应用于本地地址，不修改用户全局网络设置。'))
    manual+=page('2 地图查看与证据核对',
        command(f'$study = "智能体实验/studies/{STUDY}"\npython scripts/serve_region_study.py $study --port 8782'),
        para('打开http://127.0.0.1:8782。此入口只读，不占求解锁，也不会提交新模型任务。左侧选择区域、固定或反馈策略、已经完成的场景及需求，中央显示案例真实路网，右侧给出约束、风险和决策依据。'),
        para('建议查看路径：选择中密度的虎桥路与象贤路，切换反馈策略，点击“查看统一建议”。会定位单条停车2级的方案；三个需求均已通过。然后切换0.9需求的三条首轮方案，可看到邻区增加3.1681%的拒绝原因。'),
        para('紫色表示冻结评价核心，实际治理标记表示本次修改道路。缩为单条后，固定核心仍为三条。点击有向道路查看同需求的前后速度、流量、V/C和停车类别；双向道路略作显示偏移，以便选中方向。'),
        table(['画面提示','如何理解'],[['原始数据与决策链核验通过','对应独立核验，元数据摘要仍匹配；完整原始复核用下面命令'],['某需求通过','只证明这一场景；统一建议要求三个需求全部通过'],['回退基准','没有证据完整且合格的统一措施，属于有效停止结果'],['局部代价','近邻耗时、毛新增过饱和及恶化道路增量仍须查看']],[157,350]),
        command('python scripts/verify_region_study.py $study --archive --readonly'),
        para('克隆后文件时间戳会改变，应使用档案模式。核验器读取封存摘要和全部原始输入输出，重算指标与决定，耗时取决于磁盘。--readonly保持档案原样。只有本阅读包时缺少原始数组，不能用它执行完整实验核验。'),
        para('关闭浏览器标签页不会停止服务。在启动只读地图的终端按Ctrl+C即可停止；仿真工作台另有启动窗口。若有正在执行的模型任务，先等待它结束并核验，不要把停止网页当作安全取消模型。'))
    manual+=page('3 新任务与前瞻实验',
        para('普通大地图工作台支持新建任务卡。先检查右侧已选实验的实际参数，避免把左侧新建草稿当成历史任务参数。模板与预览不会直接调用模型，明确提交任务后才开始运行。'),
        heading('运行已有主动反馈样例'),
        command('python -m traffic_agent check --task examples/parking_robust.json\npython -m traffic_agent run --task examples/parking_robust.json'),
        para('此历史样例固定三条主实验道路，预算11、最多2次反馈，未显式声明新版推荐规则，因此保留legacy-v1语义。若新建v2任务，在复制的JSON中明确设recommendation_version为tolerance-v2；不要修改已封存任务卡。网页新建任务采用v2。'),
        heading('重新运行三地区相同预算研究'),
        command('python scripts/benchmark_regions.py'),
        para('该命令使用本版冻结的前瞻规则，新建智能体实验/studies/<新编号>。先运行新基准，再从完整候选分层选区；随后执行固定与反馈对照，并强制新复跑。历史本机参考约50分钟、22个场景；其他输入可能出现空层或不同提前停止，不能要求重跑恰好相同场景数作为唯一成功条件。'),
        para('运行时保持算法源码、原输入和程序不变，不同时提交另一个模型任务。前瞻控制器会核对运行代码快照和程序摘要，原生模块持有同一模型锁。任务结束后，使用新编号独立核验和封存。'),
        command('python scripts/verify_region_study.py 智能体实验/studies/<新编号> --seal'),
        heading('显式禁转与单向扩展'),
        command('.\\start.ps1 -Mode check -Task examples/turn_access_grid.json `\n  -CompilerBin C:\\mingw64\\bin'),
        para('编译器目录须替换为本机实际位置。检查通过后，依相应任务JSON执行完整流程并检查真实流量响应。停车通过不能替代禁转验收；施工、公交优先、信号和错峰目前也不能直接当作已验证模板。'))
    manual+=page('4 故障处理与异机验收',
        table(['症状','定位与处理'],[['LFS指针或文件缺失','在完整仓库执行git lfs pull；不要把同名占位文本当成可执行程序'],['Python版本不符','安装或选择3.12；可用start.ps1 -Python指定完整解释器路径'],['MinGW缺失或目标不符','用-CompilerBin指定兼容Windows的MinGW bin；停车主线不需要gcc'],['端口占用','更换-Port，或在已确认的原服务终端按Ctrl+C；不要结束未知进程'],['另一个任务正在使用模型','检查原任务控制台和实际进程，等待结束；单独存在锁文件不等于正在运行'],['授权或模型错误','保留控制台、run.json和原输出，按原软件方式处理授权；不要改输出补成成功'],['档案内容改变','保存副本排查Git LFS和缺失文件；使用对应代码版本与原档案，不重新生成旧核验以掩盖差异']],[143,364]),
        heading('另一台物理电脑的记录顺序'),
        para('记录设备和Windows、Python、Git提交及LFS情况；运行依赖检查；真实执行四场景快速验收；保留新JOB、开始结束时间和失败记录；进行独立核验；比较四类数组、范围、措施和推荐。若展示禁转，再执行该扩展的完整流程。'),
        para('异机记录应放入新目录，不能覆盖同机历史记录。目前没有另一台物理电脑上的新版真实求解证据。复制目录核验和同机隔离Python环境分别证明传输一致性与环境独立性，不能替代这一项。'),
        small('操作手册中的命令需在完整项目中执行。尖括号为待替换参数，不是可直接复制执行的路径。'))
    manual+=page('5 提交材料与演示核对',
        table(['赛题要求','本版位置与状态'],[['研究报告','研究报告.pdf；嵌入图表，含全部区域与失败结果'],['源代码或可执行程序','代码快照供审阅；完整仓库含原生程序，运行遵守授权'],['分析数据库','完整仓库的原南京案例和智能体实验；阅读包仅含关键证据'],['操作说明','本操作手册PDF，以及完整仓库docs目录'],['实机演示视频','完整仓库成果/演示含9月8日三段WebM；早于新版，不能冒充本版录屏']],[137,370]),
        heading('本版演示的建议叙事'),
        para('先展示任务输入与冻结范围，再展示真实启动及任务编号；完成后切换同一任务，说明原生日志、首轮失败、实际反馈和统一结果。地图同时显示固定核心与实际措施，最后说明局部代价和失败回退。若剪去计算等待，应在视频中明确标注，保持同一任务ID。'),
        para('本版只完成地图功能检查与报告排版，不声称已补录新视频。已有录像用于说明历史版本的真实运行，视频元数据和对应任务见证据E16。'),
        heading('发布前核对'),
        para('核对报告、手册、代码提交、任务编号与测试数量一致；确认LFS已拉取、PDF能离线打开且图表嵌入；确保完整数据库与视频的交付位置明确；检查材料没有把同机复制说成异机求解、把核验说成新仿真、把旧视频说成新版。'),
        command('python scripts/verify_submission.py <阅读包目录或ZIP路径>'),
        para('阅读包核验只检查文件存在、字节数和SHA256，不运行模型。正式上传前仍需核对主办方另行通知的命名、格式、时长、大小和报名信息。当前未获得这些额外限制，不将通用PDF版式当作官方规定。'),
        para('材料范围：赛事调整案例的仿真结果，非现实南京治理成效。需要补充的验证：异机实跑、另一套兼容数据集、本版实机视频及现实停车承接资料。'))
    build('操作手册.pdf',manual)


def seal(output, qa):
    from pypdf import PdfReader
    if (output/'package_manifest.json').exists():
        raise ValueError('阅读包已封存')
    record = read(qa)
    facts = read(output/'事实摘要.json')
    for item in read(output/'证据索引.json'):
        source, copied = ROOT/item['source'], output/item['packaged']
        if digest(source.read_bytes()) != item['sha256'] or digest(copied.read_bytes()) != item['sha256']:
            raise ValueError(f'发布来源或副本已经改变：{item["source"]}')
    for name in ('研究报告.pdf','操作手册.pdf'):
        data=(output/name).read_bytes(); pdf=PdfReader(output/name); pages=len(pdf.pages)
        check=record['artifacts'][name]
        if check['sha256']!=digest(data) or check['inspected_pages']!=list(range(1,pages+1)) or check['passed'] is not True:
            raise ValueError(f'缺少对应最终PDF的逐页视觉检查：{name}')
        text='\n'.join(p.extract_text() for p in pdf.pages)
        if STUDY not in text or facts['code_commit'][:7] not in text or '\ufffd' in text:
            raise ValueError(f'PDF任务、版本或文字检查失败：{name}')
    shutil.copyfile(qa,output/'排版核验.json')
    result=seal_package(output,dict(code_commit=facts['code_commit'],study=STUDY,created_at=datetime.now(timezone.utc).isoformat(),new_model_calls=0))
    target=output.with_suffix('.zip')
    with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=7) as z:
        for p in sorted(output.rglob('*')):
            if p.is_file(): z.write(p,p.relative_to(output).as_posix())
    verify_package(target)
    return dict(result,zip=str(target),zip_bytes=target.stat().st_size)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=('prepare','render','seal'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--test-log',type=Path,default=ROOT/'docs/validation/20260922_submission_tests.log')
    parser.add_argument('--font-dir',type=Path,default=Path('C:/Windows/Fonts'))
    parser.add_argument('--qa',type=Path)
    args=parser.parse_args()
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    if args.phase=='prepare':
        facts=prepare(args.output.resolve(),args.test_log.resolve())
        print(json.dumps(dict(study=STUDY,tests=facts['test_count'],native_calls=facts['audit']['native_module_calls'],new_model_calls=0),ensure_ascii=False))
    elif args.phase=='render':
        render(args.output.resolve(),args.font_dir.resolve()); print('PDFs rendered; inspect every page before sealing.')
    else:
        if not args.qa: parser.error('seal requires --qa')
        print(json.dumps(seal(args.output.resolve(),args.qa),ensure_ascii=False,indent=2))
