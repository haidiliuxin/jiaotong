"""Geometry-only startup map. Never reads results or runs a traffic model."""
import json
import math
import re
import struct
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .data import geometry, read_text
from .map_data import road_shapes, shape_for_source
from .runner import DEFAULT_SOURCE
from .source_context import display_dataset


def reference_map(source, shape=None):
    source = Path(source)
    shape = Path(shape) if shape is not None else shape_for_source(source)
    coords={}
    for line in read_text(source/'data/coor.txt').splitlines():
        if not line.strip():
            continue
        fields=line.split()
        if len(fields)<3:
            raise ValueError('案例节点坐标字段不足')
        node=int(fields[0]); point=list(map(float,fields[1:3]))
        if node in coords or not all(math.isfinite(v) for v in point):
            raise ValueError('案例节点坐标重复或非有限')
        coords[node]=point
    shapes={}
    note = ('此输入案例未配置专用SHP，使用节点直连' if shape is None
            else '未匹配SHP的路段使用案例节点直连')
    if shape is not None and shape.is_file() and shape.with_suffix('.dbf').is_file():
        try:
            shapes=road_shapes(str(shape),shape.stat().st_mtime_ns,shape.with_suffix('.dbf').stat().st_mtime_ns)
        except (ValueError,KeyError,OSError,struct.error) as exc:
            note=f'SHP不可用，采用案例节点直连：{exc}'
    links=[]; matched=0
    for row in geometry(source):
        if not row['included']:
            continue
        pair=(row['from_node'],row['to_node'])
        if not all(node in coords for node in pair):
            raise ValueError('案例道路端点缺少坐标')
        start,end=(coords[n] for n in pair)
        paths=shapes.get(pair)
        if paths and math.dist(paths[0][0],start)<=1 and math.dist(paths[-1][-1],end)<=1:
            matched+=1; origin='shp'
        else:
            paths=[[start,end]]; origin='nodes'
        links.append(dict(id=row['index'],start=pair[0],end=pair[1],name=row['name'],
                          length=row['length_km'],paths=paths,geometry=origin,group='network'))
    if not links:
        raise ValueError('案例没有可显示道路')
    projected = (shape is not None and matched == len(links) and shape.with_suffix('.prj').is_file()
                 and 'WGS_1984_UTM_Zone_50N' in read_text(shape.with_suffix('.prj')))
    return dict(reference=True,dataset=display_dataset(source),links=links,matched_shapes=matched,meters=projected,
                coordinate_system='WGS 84 / UTM 50N · 米' if projected else '案例平面坐标',
                geometry_note=note,new_model_calls=0,
                meaning='案例道路几何；未读取任何历史或本次交通结果，不含速度、流量、饱和度或治理效果')


def with_reference_map(handler, source, root):
    """Add a read-only route for the configured source or a saved job's source."""
    source, root = Path(source).resolve(), Path(root).resolve()
    class ReferenceHandler(handler):
        def do_GET(self):
            request = urlparse(self.path)
            if request.path == '/api/reference-map':
                try:
                    job_id = parse_qs(request.query).get('job', [None])[0]
                    selected_source = source
                    if job_id is not None:
                        if not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{6}', job_id):
                            raise ValueError('无效任务编号')
                        state_file = root/'jobs'/job_id/'state.json'
                        if not state_file.is_file():
                            return self.respond(404, dict(error='没有找到该任务'))
                        state = json.loads(state_file.read_text(encoding='utf-8'))
                        selected_source = Path(state.get('source', DEFAULT_SOURCE)).resolve()
                    return self.respond(200, reference_map(selected_source))
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    return self.respond(400,dict(error=str(exc)))
            return super().do_GET()
    return ReferenceHandler
