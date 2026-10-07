"""Local, read-only view of a prospective study and its real paired road outputs."""
import json
import math
import struct
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .data import read_text, results
from .map_data import SHAPE, road_shapes, scenario_path
from .mechanism import parking_response
from .study_runner import risk_review, sha

STATIC = Path(__file__).with_name('static')/'study.html'


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def summary(job):
    state = read(job/'state.json')
    selection = read(job/'selection.json') if (job/'selection.json').exists() else {'regions': []}
    groups = []
    for region in selection['regions']:
        item = {k: v for k, v in region.items() if k != 'scope'}
        item['strategies'] = {}
        if region.get('scope'):
            item['scope_sizes'] = {k: len(v) for k, v in region['scope'].items()}
        for strategy, entry in state['regions'].get(region['id'], {}).get('strategies', {}).items():
            item['strategies'][strategy] = dict(
                result=entry.get('result'), summary=entry.get('summary'),
                runs=[{k: v for k, v in run.items() if k in ('name', 'physical_run', 'changes', 'demand_factor', 'metrics', 'comparison', 'risk_review')}
                      for run in entry['runs'] if run['name'] != 'baseline_repeat'])
        groups.append(item)
    verification = read(job/'verification.json') if (job/'verification.json').exists() else {}
    verified = (verification.get('passed') is True and all(verification.get(f'{name}_sha256') == sha(job/f'{name}.json')
                for name in ('state', 'protocol', 'selection')))
    return dict(id=state['id'], status=state['status'], run_count=state['run_count'],
                completed_scenes=len(state['runs']), native_calls=sum(len(r['execution']['modules']) for r in state['runs']),
                active=state.get('active_scenario'), error=state.get('error'), regions=groups,
                trace=state['trace'], verified=verified,
                repeatability=state.get('repeatability'))


@lru_cache(maxsize=4)
def study_map(job, region_id, strategy, name):
    job = Path(job).resolve()
    state, selection = read(job/'state.json'), read(job/'selection.json')
    region = next((r for r in selection['regions'] if r['id'] == region_id and r['status'] == 'selected'), None)
    if region is None or strategy not in ('fixed', 'feedback'):
        raise ValueError('未知区域或策略')
    entry = state['regions'].get(region_id, {}).get('strategies', {}).get(strategy)
    if not entry:
        raise ValueError('该区域尚未进入治理对照')
    chosen = next((r for r in entry['runs'] if r['name'] == name), None)
    if chosen is None:
        raise ValueError('该场景尚无已完成的真实结果')
    baseline = next(r for r in entry['runs'] if not r['changes'] and r['demand_factor'] == chosen['demand_factor'])
    physical = {r['name']: r for r in state['runs']}
    before_path = scenario_path(job, physical[baseline['physical_run']])
    after_path = scenario_path(job, physical[chosen['physical_run']])
    before, after = results(before_path), results(after_path)
    if len(before) != len(after) or any((a['from_node'], a['to_node']) != (b['from_node'], b['to_node']) for a, b in zip(before, after)):
        raise ValueError('方案与匹配基准路段不一致')
    if (before_path/'data/coor.txt').read_bytes() != (after_path/'data/coor.txt').read_bytes():
        raise ValueError('方案与基准坐标不一致')
    coords = {int(f[0]): [float(f[1]), float(f[2])] for line in read_text(before_path/'data/coor.txt').splitlines() if (f := line.split())}
    shapes, note = {}, '未匹配几何采用案例节点直连'
    if SHAPE.is_file() and SHAPE.with_suffix('.dbf').is_file():
        try:
            shapes = road_shapes(str(SHAPE), SHAPE.stat().st_mtime_ns, SHAPE.with_suffix('.dbf').stat().st_mtime_ns)
        except (ValueError, KeyError, OSError, struct.error) as exc:
            note = f'SHP不可用，使用案例节点直连：{exc}'
    scope = region['scope']
    core, neighbor = set(scope['core']), set(scope['neighbor'])
    treated = {(c['from_node'], c['to_node']) for c in chosen['changes']}
    links, matched = [], 0
    for i in scope['network']:
        a, b = before[i], after[i]
        pair = (a['from_node'], a['to_node'])
        start, end = coords[pair[0]], coords[pair[1]]
        paths = shapes.get(pair)
        is_shape = bool(paths and math.dist(paths[0][0], start) <= 1 and math.dist(paths[-1][-1], end) <= 1)
        matched += is_shape
        links.append(dict(id=i, start=pair[0], end=pair[1], name=a['name'], length=a['length_km'],
                          group='core' if i in core else 'neighbor' if i in neighbor else 'network',
                          treated=pair in treated,
                          paths=paths if is_shape else [[start, end]], geometry='shp' if is_shape else 'nodes',
                          before=[a['speed_kmh'], a['flow_pcu_h'], a['vc'], a['parking']],
                          after=[b['speed_kmh'], b['flow_pcu_h'], b['vc'], b['parking']]))
    return dict(region=region_id, strategy=strategy, run=name, physical_run=chosen['physical_run'],
                baseline=baseline['physical_run'], factor=chosen['demand_factor'], changes=chosen['changes'],
                comparison=chosen.get('comparison'), before=baseline['metrics'], after=chosen['metrics'],
                risk=chosen.get('risk_review') or risk_review(before, after, scope), links=links,
                mechanism=parking_response(before, after, chosen['changes']),
                geometry_note=f'{matched}/{len(links)} 条有向道路匹配案例 SHP；{note}')


def serve(job, port=8782):
    job = Path(job).resolve()
    state = read(job/'state.json')
    if state.get('kind') != 'prospective_region_study':
        raise ValueError('该目录不是前瞻研究任务')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                if parsed.path == '/':
                    content, mime = STATIC.read_bytes(), 'text/html; charset=utf-8'
                elif parsed.path == '/api/study':
                    content, mime = json.dumps(summary(job), ensure_ascii=False, allow_nan=False).encode(), 'application/json; charset=utf-8'
                elif parsed.path == '/api/map':
                    query = parse_qs(parsed.query)
                    if set(query) != {'region', 'strategy', 'run'} or any(len(v) != 1 for v in query.values()):
                        raise ValueError('请指定唯一的区域、策略及场景')
                    data = study_map(str(job), query['region'][0], query['strategy'][0], query['run'][0])
                    content, mime = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode(), 'application/json; charset=utf-8'
                else:
                    return self.send_error(404)
                self.send_response(200)
                self.send_header('Content-Type', mime)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(content)
            except (ValueError, KeyError, StopIteration, OSError) as exc:
                content = json.dumps({'error': str(exc)}, ensure_ascii=False).encode()
                self.send_response(400)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'STUDY DASHBOARD http://127.0.0.1:{server.server_port}/ | {state["id"]}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
