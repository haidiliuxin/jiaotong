"""Build a static reading pack from authored Markdown and allowlisted evidence.

No model or native execution. Full native archives are deliberately separate.
"""
import argparse
import csv
import hashlib
import html
import io
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PACK=ROOT/'成果/提交版本/20261007当前主线阅读版'
JOB='20261007_010958_039964'
CSS='''
*{box-sizing:border-box}body{margin:0;background:#eef1f4;color:#242a32;font:16px/1.8 "Microsoft YaHei",sans-serif}
main{max-width:1120px;margin:32px auto;background:#fff;padding:42px 54px}h1,h2,h3{color:#000;line-height:1.4}
h1{font-size:28px;border-bottom:1px solid #d9d9d9;padding-bottom:20px}h2{font-size:22px;margin-top:36px}
p{margin:14px 0}table{width:100%;border-collapse:collapse;margin:20px 0;font-size:14px;line-height:1.65}
th,td{padding:11px 13px;border:1px solid #d9d9d9;vertical-align:middle;text-align:left;overflow-wrap:anywhere}
th{background:#18364b;color:white}tr:nth-child(even){background:#f3f6f8}a{color:#125fa3}code{font-family:Consolas,monospace;overflow-wrap:anywhere}
pre{white-space:pre-wrap;background:#f4f5f6;padding:18px;border:1px solid #d9d9d9;font-size:13px}
nav{font-size:14px;display:flex;flex-wrap:wrap;gap:18px;border-bottom:1px solid #d9d9d9;padding-bottom:14px}
@media(max-width:800px){main{padding:22px;margin:0}table{font-size:12px}th,td{padding:6px}}
@media print{body{background:#fff;font-size:10pt}main{padding:0;margin:0;max-width:none}nav{display:none}h1{font-size:20pt}h2{font-size:14pt;break-after:avoid}table{font-size:8pt}tr{break-inside:avoid}thead{display:table-header-group}pre{font-size:8pt}@page{size:A4;margin:18mm}}
'''

def inline(text):
    parts=re.split(r'(`[^`]+`)',text)
    return ''.join('<code>'+html.escape(p[1:-1])+'</code>' if p.startswith('`') else html.escape(p) for p in parts)

def render(source):
    lines=source.splitlines();out=[];i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip():i+=1;continue
        if line.startswith('```'):
            i+=1;block=[]
            while i<len(lines) and not lines[i].startswith('```'):block.append(lines[i]);i+=1
            if i==len(lines):raise ValueError('Unclosed code block')
            out.append('<pre><code>'+html.escape('\n'.join(block))+'</code></pre>');i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                rows.append([s.strip() for s in lines[i].strip().strip('|').split('|')]);i+=1
            if len(rows)<2 or not all(re.fullmatch(r':?-+:?',s) for s in rows[1]):raise ValueError('Malformed table')
            if any(len(r)!=len(rows[0]) for r in rows[2:]):raise ValueError('Table column mismatch')
            out.append('<table><thead><tr>'+''.join('<th>'+inline(s)+'</th>' for s in rows[0])+'</tr></thead><tbody>'+
                       ''.join('<tr>'+''.join('<td>'+inline(s)+'</td>' for s in r)+'</tr>' for r in rows[2:])+'</tbody></table>');continue
        if line.startswith('#'):
            m=re.fullmatch(r'(#{1,6}) (.+)',line)
            if not m:raise ValueError('Malformed heading')
            n=len(m[1]);out.append(f'<h{n}>'+inline(m[2])+f'</h{n}>');i+=1;continue
        para=[]
        while i<len(lines) and lines[i].strip() and not lines[i].startswith(('#','|','```')):para.append(lines[i]);i+=1
        out.append('<p>'+inline(' '.join(para))+'</p>')
    return '\n'.join(out)

def write_json(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')

def build(archive=None):
    evidence=PACK/'证据';evidence.mkdir(exist_ok=True)
    state=json.loads((ROOT/'智能体实验/jobs'/JOB/'state.json').read_text('utf-8'))
    receipt=json.loads((ROOT/'docs/validation/20261007_professional_live.json').read_text('utf-8'))
    if state['status']!='completed' or len(state['runs'])!=9 or not receipt['programme_audit']['passed']:
        raise ValueError('Expected completed and verified nine-scene task')
    write_json(evidence/'最新闭环摘要.json',receipt)
    rag=json.loads((ROOT/'docs/validation/20261007_professional_acceptance.json').read_text('utf-8'))
    write_json(evidence/'RAG验收结果.json',rag)
    rows=[]
    for run in state['runs']:
        row=dict(run=run['name'],demand_factor=run['demand_factor'])
        for region in ('core','neighbor','network'):
            for key in ('directed_links','directed_length_km','weighted_speed_kmh','running_time_pcu_h','oversaturated_km'):
                row[region+'_'+key]=run['metrics'][region][key]
        comp=run.get('comparison') or {}
        row['eligible']=comp.get('eligible')
        row['neighbor_new_oversaturated_km']=comp.get('new_neighbor_oversaturated_km')
        for region in ('core','neighbor','network'):
            row[region+'_time_change_pct']=(comp.get('delta',{}).get(region) or {}).get('running_time_change_pct')
        row['violations']='；'.join(comp.get('violations',[]))
        rows.append(row)
    write_json(evidence/'最新场景指标.json',dict(job_id=JOB,source='original state.json metrics and comparison, allowlisted fields',rows=rows))
    buf=io.StringIO(newline='');writer=csv.DictWriter(buf,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    (evidence/'最新场景指标.csv').write_text(buf.getvalue(),encoding='utf-8-sig',newline='')
    md_files=sorted(PACK.glob('*.md'));nav=''.join('<a href="'+html.escape(p.with_suffix('.html').name)+'">'+html.escape(p.stem)+'</a>' for p in md_files)
    for p in md_files:
        source=p.read_text('utf-8');title=source.splitlines()[0].lstrip('# ')
        page='<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>'+CSS+'</style></head><body><main><nav>'+nav+'</nav>'+render(source)+'</main></body></html>'
        p.with_suffix('.html').write_text(page,encoding='utf-8',newline='\n')
    files=sorted(p for p in PACK.rglob('*') if p.is_file() and p.name!='manifest.json')
    manifest=dict(version='competition-reading-pack-v1',functional_commit='6f0b4a8a',job_id=JOB,
                  boundary='Reading documents and allowlisted summaries only. No credentials, executable, complete native database or new video.',
                  files=[dict(path=p.relative_to(PACK).as_posix(),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in files])
    write_json(PACK/'manifest.json',manifest)
    if archive:
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in sorted(PACK.rglob('*')):
                if p.is_file():z.write(p,'20261007当前主线阅读版/'+p.relative_to(PACK).as_posix())
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Archive corruption')
            for item in manifest['files']:
                if hashlib.sha256(z.read('20261007当前主线阅读版/'+item['path'])).hexdigest()!=item['sha256']:raise ValueError('Archive hash mismatch')
    print(json.dumps(dict(pack=str(PACK),files=len(files)+1,archive=str(archive) if archive else None,hashes_verified=True),ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--archive',type=Path);args=parser.parse_args()
    build(args.archive)
