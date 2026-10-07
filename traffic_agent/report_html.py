"""Offline HTML presentation for the report's authored lines, without raw HTML.

This deliberately small renderer accepts the report writer's list of blocks,
not arbitrary Markdown documents. A newline inside a task or error remains text
and cannot create a heading, table, link, or executable element.
"""
import html
import re

from .source_context import footer_text


INLINE = re.compile(r'(`[^`\n]+`|\*\*[^*\n]+\*\*)')
RULE = re.compile(r'^:?-{3,}:?$')


def inline(text):
    parts = []
    for piece in INLINE.split(str(text)):
        if piece.startswith('`') and piece.endswith('`'):
            parts.append('<code>'+html.escape(piece[1:-1])+'</code>')
        elif piece.startswith('**') and piece.endswith('**'):
            parts.append('<strong>'+html.escape(piece[2:-2])+'</strong>')
        else:
            parts.append(html.escape(piece))
    return ''.join(parts).replace('\n','<br>')


def cells(line):
    if '\n' in line or not (line.startswith('| ') and line.endswith(' |')):
        return None
    return [value.strip() for value in line[1:-1].split('|')]


def blocks(lines):
    sections, body = [], []
    i = 0
    while i < len(lines):
        line = str(lines[i])
        if not line.strip() or (i == 0 and line.startswith('# ')):
            i += 1
            continue
        if line.startswith('## ') and '\n' not in line:
            title = line[3:]
            anchor = f'section-{len(sections)+1}'
            if sections:
                body.append('</section>')
            sections.append((anchor,title))
            body.append(f'<section aria-labelledby="{anchor}"><h2 id="{anchor}">{inline(title)}</h2>')
            i += 1
            continue
        header = cells(line)
        separator = cells(str(lines[i+1])) if i+1 < len(lines) else None
        if header and separator and len(header)==len(separator) and all(RULE.fullmatch(x) for x in separator):
            body.append('<div class="table-scroll" tabindex="0" role="region" aria-label="可横向滚动的数据表"><table><thead><tr>')
            body.extend('<th scope="col">'+inline(x)+'</th>' for x in header)
            body.append('</tr></thead><tbody>')
            i += 2
            while i < len(lines):
                row = cells(str(lines[i]))
                if not row or len(row) != len(header):
                    break
                body.append('<tr>'+''.join('<td>'+inline(x)+'</td>' for x in row)+'</tr>')
                i += 1
            body.append('</tbody></table></div>')
            continue
        if line.startswith('- '):
            body.append('<ul>')
            while i < len(lines) and str(lines[i]).startswith('- '):
                item = str(lines[i])[2:]
                i += 1
                while i < len(lines) and str(lines[i]).startswith('  '):
                    item += '\n'+str(lines[i]).strip()
                    i += 1
                body.append('<li>'+inline(item)+'</li>')
            body.append('</ul>')
            continue
        body.append('<p>'+inline(line)+'</p>')
        i += 1
    if sections:
        body.append('</section>')
    return sections, ''.join(body)


STYLE = '''
:root{color-scheme:light;--ink:#182c3e;--muted:#5c6c79;--line:#dce5e9;--accent:#087f86}
*{box-sizing:border-box}html{scroll-padding-top:20px}
body{margin:0;background:#edf2f4;color:var(--ink);font:16px/1.8 "Microsoft YaHei",system-ui,sans-serif}
a{color:var(--accent);text-decoration-thickness:1px;text-underline-offset:3px}
.top{padding:28px max(24px,calc((100vw - 1240px)/2));color:#effbff;background:#102c3d}
.brand{font-size:12px;letter-spacing:.16em;color:#8ad9dc}.top h1{font-size:30px;line-height:1.4;margin:8px 0}
.id{font-size:13px;color:#bfd1dd;overflow-wrap:anywhere}.summary{display:flex;flex-wrap:wrap;gap:10px;margin-top:20px}
.stat{padding:10px 16px;border:1px solid #365061;border-radius:8px;min-width:145px}
.stat span{display:block;font-size:12px;color:#bfd1dd}.stat strong{font-size:20px;font-weight:600;overflow-wrap:anywhere}
.scope{font-size:13px;margin:16px 0 0;color:#d0e1eb}.layout{max-width:1288px;margin:auto;display:grid;grid-template-columns:215px minmax(0,1fr);gap:28px;padding:28px 24px 56px}
nav{position:sticky;top:20px;align-self:start;max-height:calc(100vh - 40px);overflow:auto;font-size:13px}
nav strong{display:block;margin-bottom:10px}nav a{display:block;padding:7px 10px;border-left:2px solid var(--line);text-decoration:none;color:var(--muted)}nav a:hover,nav a:focus-visible{border-color:var(--accent);color:var(--accent);background:white}
main{min-width:0;background:white;border:1px solid var(--line);border-radius:12px;padding:24px 32px;box-shadow:0 8px 24px #18384908}
h2{font-size:21px;line-height:1.5;margin:32px 0 15px;padding-top:12px;border-top:1px solid var(--line)}
p,li{overflow-wrap:anywhere}p{margin:12px 0}ul{padding-left:24px}li{margin:8px 0}code{font:13px/1.6 ui-monospace,Consolas,monospace;background:#f0f4f6;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}
.table-scroll{overflow:auto;border:1px solid var(--line);border-radius:7px;margin:16px 0;max-width:100%}table{border-collapse:collapse;width:100%;min-width:620px;font-size:13px;line-height:1.65}
th,td{padding:10px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line);overflow-wrap:normal;max-width:300px}
th{background:#edf4f6;font-weight:600;white-space:normal}tbody tr:nth-child(even){background:#f8fafb}tr:last-child td{border-bottom:0}
.downloads{display:flex;gap:18px;flex-wrap:wrap;margin:24px 0 0;padding-top:20px;border-top:1px solid var(--line);font-size:13px}
.footer{max-width:1240px;margin:0 auto;padding:0 24px 24px;color:var(--muted);font-size:12px}
:focus-visible{outline:3px solid #00a3ad;outline-offset:3px}
@media(max-width:850px){.layout{grid-template-columns:1fr;gap:12px;padding:16px}nav{position:static;max-height:none;display:flex;flex-wrap:wrap;gap:4px}nav strong{flex-basis:100%;margin:0}nav a{padding:4px 8px}main{padding:18px}.top{padding:22px}.top h1{font-size:25px}}
@media print{body{background:white;font-size:10pt}.top{background:white!important;color:#172c3d;padding:0 0 12px}.brand,.id,.scope,.stat span{color:#455764}.top h1{font-size:20pt}.stat{border-color:#bdcbd4;min-width:120px}.stat strong{font-size:14pt}.layout{display:block;padding:0}nav,.downloads{display:none}main{border:0;padding:0;box-shadow:none}h2{font-size:14pt;break-after:avoid}.table-scroll{overflow:visible;border:0}table{min-width:0;font-size:8pt;table-layout:auto}th,td{padding:5px;white-space:normal;max-width:none}thead{display:table-header-group}tr{break-inside:avoid}.footer{padding:12px 0 0}.summary{gap:6px}a{color:inherit;text-decoration:none}}
'''


def render_report_html(lines, state):
    sections, content = blocks(lines)
    completed = state.get('status') == 'completed'
    status = {'completed':'计算已完成','failed':'执行失败','running':'计算中','no_candidate':'未找到可执行候选'}.get(state.get('status'),'尚未完成')
    runs = state.get('runs',[])
    calls = sum(len(r.get('execution',{}).get('modules',[])) for r in runs)
    recommendation = state.get('recommendation','未形成建议') if completed else '尚未形成完整建议'
    meta = [('运行状态',status),('已完成场景',f"{len(runs)} / {state['task']['max_runs']}"),('顶层模块调用',calls),('基准需求建议',recommendation)]
    toc = ''.join(f'<a href="#{anchor}">{html.escape(title)}</a>' for anchor,title in sections)
    stats = ''.join('<div class="stat"><span>'+html.escape(label)+'</span><strong>'+html.escape(str(value))+'</strong></div>' for label,value in meta)
    job_id = html.escape(str(state['id']))
    metrics_link = '<a href="metrics.csv" download>指标 CSV</a>' if any(r.get('metrics') for r in runs) else ''
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="referrer" content="no-referrer">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">'
            f'<title>交通仿真报告 · {job_id}</title><style>{STYLE}</style></head><body>'
            '<header class="top"><div class="brand">LUCE / TRANSPORT DECISION REPORT</div>'
            f'<h1>交通仿真智能体运行报告</h1><div class="id">任务 {job_id}</div><div class="summary">{stats}</div>'
            '<p class="scope">本页读取本任务保存的计算结果。运行完成不等于独立核验通过；治理建议的适用需求、约束与不利影响见正文。</p></header>'
            '<div class="layout"><nav aria-label="报告目录"><strong>报告目录</strong>'+toc+'</nav><main>'+content+
            '<div class="downloads" aria-label="原始证据下载"><a href="report.md" download>报告原文</a>'
            +metrics_link+'<a href="state.json" download>完整任务 JSON</a></div></main></div>'
            f'<footer class="footer">{html.escape(footer_text(state.get("source")))}</footer></body></html>')
