"""Read-only map of paired model outputs, with explicitly matched local SHP geometry."""
import json
import math
import struct
from functools import lru_cache
from pathlib import Path

from .data import PROJECT, read_text, results
from .decision_analysis import spatial_effects

SHAPE = PROJECT / '南京市案例数据库/南京路网shp/Roads.shp'


@lru_cache(maxsize=2)
def road_shapes(filename, shp_mtime, dbf_mtime):
    """Read the shipped 2D PolyLine records; ESRI shapefile technical description.

    https://www.esri.com/library/whitepapers/pdfs/shapefile.pdf
    DBF endpoint identifiers, not record position, join roads to the model.
    """
    path = Path(filename)
    raw, dbf = path.read_bytes(), path.with_suffix('.dbf').read_bytes()
    if len(raw) < 100 or struct.unpack_from('>i', raw)[0] != 9994 or struct.unpack_from('<ii', raw, 28) != (1000, 3):
        raise ValueError('地图只支持案例中的二维PolyLine SHP')
    count = struct.unpack_from('<I', dbf, 4)[0]
    header, width = struct.unpack_from('<HH', dbf, 8)
    fields, offset = {}, 1
    for pos in range(32, header - 1, 32):
        descriptor = dbf[pos:pos + 32]
        name = descriptor[:11].split(b'\0')[0].decode('gb18030')
        fields[name] = (offset, descriptor[16])
        offset += descriptor[16]
    if offset != width or len(dbf) < header + count * width:
        raise ValueError('地图DBF记录不完整')
    output, pos = {}, 100
    for row in range(count):
        _, words = struct.unpack_from('>ii', raw, pos)
        content = raw[pos + 8:pos + 8 + words * 2]
        pos += 8 + words * 2
        record = dbf[header + row * width:header + (row + 1) * width]
        if record[:1] == b'*' or struct.unpack_from('<i', content)[0] == 0:
            continue
        if struct.unpack_from('<i', content)[0] != 3:
            raise ValueError('地图包含非折线记录')
        parts, points = struct.unpack_from('<ii', content, 36)
        if parts < 1 or points < 2 or len(content) != 44 + parts * 4 + points * 16:
            raise ValueError('地图折线维数异常')
        starts = list(struct.unpack_from(f'<{parts}i', content, 44)) + [points]
        if starts[0] != 0 or any(a >= b for a, b in zip(starts, starts[1:])):
            raise ValueError('地图折线分段异常')
        coords = [list(struct.unpack_from('<dd', content, 44 + parts * 4 + i * 16)) for i in range(points)]
        if not all(math.isfinite(v) for point in coords for v in point):
            raise ValueError('地图含非法坐标')
        pair = tuple(int(record[fields[k][0]:sum(fields[k])]) for k in ('起点', '终点'))
        if pair in output:
            raise ValueError('地图存在重复有向路段')
        output[pair] = [coords[a:b] for a, b in zip(starts, starts[1:])]
    if pos != len(raw):
        raise ValueError('SHP与DBF记录数不一致')
    return output


def paired_run(state, name):
    complete = [r for r in state['runs'] if r.get('metrics') and r.get('execution', {}).get('status') == 'completed']
    selected = next((r for r in complete if r['name'] == name), None)
    if selected is None:
        raise ValueError('请先选择一个已完成并产生指标的场景')
    baseline = next((r for r in complete if not r['changes'] and r['demand_factor'] == selected['demand_factor']), None)
    if baseline is None:
        raise ValueError('该需求尚无已完成的匹配基准')
    return baseline, selected


def scenario_path(job, record):
    path = (job / record['scenario']).resolve()
    if not path.is_relative_to(job.resolve()):
        raise ValueError('场景路径越出当前任务')
    return path


def map_data(job, name, shape=SHAPE):
    state = json.loads((job / 'state.json').read_text(encoding='utf-8'))
    baseline, selected = paired_run(state, name)
    before_path, after_path = scenario_path(job, baseline), scenario_path(job, selected)
    before, after = results(before_path), results(after_path)
    scopes = json.loads((job / 'scopes.json').read_text(encoding='utf-8'))
    core, neighbor, network = (set(scopes[k]) for k in ('core', 'neighbor', 'network'))
    coords = {int(f[0]): [float(f[1]), float(f[2])] for line in read_text(before_path/'data/coor.txt').splitlines() if (f := line.split())}
    if len(before) != len(after) or any((a['from_node'], a['to_node']) != (b['from_node'], b['to_node']) for a, b in zip(before, after)):
        raise ValueError('基准与方案路段顺序不一致')
    if (before_path/'data/coor.txt').read_bytes() != (after_path/'data/coor.txt').read_bytes():
        raise ValueError('基准与方案坐标不一致')
    shapes, geometry_note = {}, '未匹配SHP的路段按案例节点直连，非真实道路曲线'
    if shape.is_file() and shape.with_suffix('.dbf').is_file():
        try:
            shapes = road_shapes(str(shape), shape.stat().st_mtime_ns, shape.with_suffix('.dbf').stat().st_mtime_ns)
        except (ValueError, KeyError, OSError, struct.error) as exc:
            geometry_note = f'SHP不可用，采用节点直连：{exc}'
    links, matched = [], 0
    for a, b in zip(before, after):
        if a['index'] not in network:
            continue
        pair = (a['from_node'], a['to_node'])
        start, end = coords[pair[0]], coords[pair[1]]
        paths = shapes.get(pair)
        if paths and math.dist(paths[0][0], start) <= 1 and math.dist(paths[-1][-1], end) <= 1:
            matched += 1
            source = 'shp'
        else:
            paths, source = [[start, end]], 'nodes'
        links.append(dict(id=a['index'], start=pair[0], end=pair[1], name=a['name'], length=a['length_km'],
                          group='core' if a['index'] in core else 'neighbor' if a['index'] in neighbor else 'network',
                          paths=paths, geometry=source,
                          before=[a['speed_kmh'], a['flow_pcu_h'], a['vc'], a['parking']],
                          after=[b['speed_kmh'], b['flow_pcu_h'], b['vc'], b['parking']]))
    if not links:
        raise ValueError('当前任务没有可显示的评价路段')
    # The local PRJ is meaningful only when every displayed model edge matches it.
    projected = matched == len(links) and shape.with_suffix('.prj').is_file() and 'WGS_1984_UTM_Zone_50N' in read_text(shape.with_suffix('.prj'))
    return dict(job=state['id'], selected=selected['name'], baseline=baseline['name'], factor=selected['demand_factor'],
                eligible=selected.get('comparison', {}).get('eligible'), changes=selected['changes'], turn=state['task'].get('turn'),
                links=links, matched_shapes=matched, coordinate_system='WGS 84 / UTM 50N · 米' if projected else '案例平面坐标',
                meters=projected, geometry_note=f'{matched}/{len(links)}条有向道路匹配案例SHP；'+geometry_note,
                metrics={'before': baseline['metrics'], 'after': selected['metrics']}, spatial=spatial_effects(links))
