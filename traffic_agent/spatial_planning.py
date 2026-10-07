"""Fresh spatial evidence and a frozen multi-area parking search space.

This is batch analysis, not a continuously advancing traffic environment.
Areas and evaluation indices are frozen before any policy result is available.
"""
from .data import diagnose, parking_candidates, neighborhood, metrics

VERSION = 'spatial-portfolio-v1'
REGION_LIMIT = 4
DIRECTIONS_PER_REGION = 3


def diagnose_portfolio(rows, task):
    original = diagnose(rows, task)
    population = parking_candidates(rows, task)
    groups = []
    if task.links:
        groups = [original['selected']] if original['selected'] else []
    else:
        excluded = set()
        for seed in population:
            if seed['index'] in excluded:
                continue
            nearby = set(neighborhood(rows, [seed['index']], task.neighbor_hops))
            group = [seed] + [r for r in population if r['index'] in nearby and r['index'] not in excluded][:min(task.max_links, DIRECTIONS_PER_REGION)-1]
            groups.append(group)
            excluded.update(nearby | {r['index'] for r in group})
            if len(groups) == REGION_LIMIT:
                break
    regions = [dict(id=f'area_{i+1}', title=' / '.join(dict.fromkeys(r['name'] for r in group)),
                    indices=[r['index'] for r in group], directions=[[r['from_node'],r['to_node']] for r in group]) for i, group in enumerate(groups)]
    return dict(original, selected=[r for group in groups for r in group], regions=regions,
                spatial_protocol=VERSION, total_candidates=len(population),
                rule='按新基准qL/v选至多4个分散候选区，每区至多3个合法方向，治理方向不重复；显式方向保持原约束。各区并集固定为比较核心，模型每次最多治理任务限定条数。',
                coverage_note='所有参与分配路段均读取；候选区是有界搜索目录，不代表全网所有可治理方向都已穷举。')


def observe(rows, regions):
    """Numeric observations only, with compact whole-network outside-area facts."""
    valid = [r for r in rows if r['included']]
    covered = {i for region in regions for i in region['indices']}
    outside = sorted((r for r in valid if r['index'] not in covered and r['vc'] > 1),
                     key=lambda r: (-r['flow_pcu_h']*r['length_km']/r['speed_kmh'],r['index']))
    return dict(inspected_links=len(valid), overloaded_links=sum(r['vc']>1 for r in valid),
                regions=[dict(region, **compact_metrics([rows[i] for i in region['indices']]),
                              roads=[dict(index=rows[i]['index'],name=rows[i]['name'],from_node=rows[i]['from_node'],to_node=rows[i]['to_node'],
                                          speed_kmh=rows[i]['speed_kmh'],vc=rows[i]['vc'],parking=rows[i]['parking']) for i in region['indices']]) for region in regions],
                outside_hotspots=[dict(index=r['index'],name=r['name'],from_node=r['from_node'],to_node=r['to_node'],
                                       speed_kmh=r['speed_kmh'],vc=r['vc']) for r in outside[:5]])


def compact_metrics(rows):
    full=metrics(rows)
    return {k:full[k] for k in ('directed_links','weighted_speed_kmh','running_time_pcu_h','oversaturated_km')}


def assess(before, after, scope, task, regions):
    from .agent import compare, pct
    result=compare(before,after,scope,task)
    checks=[]
    for region in regions:
        a=compact_metrics([before[i] for i in region['indices']])
        b=compact_metrics([after[i] for i in region['indices']])
        change=pct(a['running_time_pcu_h'],b['running_time_pcu_h'])
        passed=change is not None and change<=task.max_neighbor_time_increase_pct+1e-9
        checks.append(dict(region=region['id'],before=a,after=b,time_change_pct=change,passed=passed))
        if not passed:
            result['violations'].append(f"候选区域{region['id']}耗时增幅超过{task.max_neighbor_time_increase_pct}%：其他区域改善不能抵消")
    result.update(regional_checks=checks,eligible=not result['violations'])
    return result


def options(diagnosis, seen, max_links):
    from .feedback_planner import parking_options
    if not diagnosis.get('regions'):
        return parking_options(diagnosis['selected'],seen)
    pool={r['index']:r for r in diagnosis['selected']}
    choices={}
    for region in diagnosis['regions']:
        for key,choice in parking_options([pool[i] for i in region['indices']],seen).items():
            if len(choice['changes'])<=max_links:
                choices[key]=dict(choice,region=region['id'],title=f"{region['title']} · {choice['title']}")
    return choices
