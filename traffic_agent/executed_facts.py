"""Measured action labels and bounded checks of explicit parking-level claims.

Scenario ordinals (S1/S2/S3) are identifiers, never parking levels. This does
not attempt unrestricted natural-language entailment or infer missing data.
"""
import re

VERSION = 'executed-policy-facts-v1'


def policy_facts(observations):
    rows = []
    for o in observations:
        changes = o.get('changes') or []
        if not changes:
            continue
        directions = {(c.get('from_node'), c.get('to_node')) for c in changes if 'parking' in c}
        closed = [c['closed_direction'] for c in changes if c.get('kind') == 'oneway' and c.get('closed_direction')]
        directions.update(tuple(d) for d in closed)
        directions.update(tuple(reversed(d)) for d in closed)
        roads = list(dict.fromkeys(r['name'] for r in o.get('core_roads', []) if r.get('name')
                                 and (r.get('from_node'), r.get('to_node')) in directions))
        levels = sorted({c['parking'] for c in changes if type(c.get('parking')) is int})
        rows.append(dict(ref=o['ref'], run=o.get('run'), roads=roads, parking_levels=levels,
                         closed_directions=closed,
                         label=('停车' + '/'.join(str(v) for v in levels) + '级' if levels else '无停车等级变更')
                               + ('＋单向禁行' if closed else '')))
    return rows


def named_rows(clause, rows):
    """Exact scene references take precedence over a road shared by many scenes."""
    for kind in ('ref', 'run', 'alias', 'road'):
        found = []
        for row in rows:
            if kind == 'road':
                matched = any(name in clause for name in row.get('roads', []))
            else:
                name = row.get(kind)
                if kind == 'alias':
                    m = re.match(r'^(S[123])(?:_|$)', row.get('run') or '')
                    name = m.group(1) if m else None
                matched = bool(name and re.search(r'(?<![A-Za-z0-9_])' + re.escape(name) + r'(?![A-Za-z0-9_])', clause))
            if matched:
                found.append(row)
        if found:
            road_scoped = [r for r in found if any(name in clause for name in r.get('roads', []))]
            if len(found) > 1 and road_scoped:
                return road_scoped
            return found
    return []


def changed_parking_regions(observations):
    """Map measured parking edits to the frozen spatial region directions."""
    result = {}
    for observation in observations:
        edits = {(change.get('from_node'), change.get('to_node')) for change in observation.get('changes', [])
                 if type(change.get('parking')) is int}
        regions = (observation.get('spatial') or {}).get('regions', [])
        result[observation['ref']] = [region['id'] for region in regions
                                      if region.get('id') and any(tuple(direction) in edits
                                                                  for direction in (region.get('directions') or []))]
    return result


ACTION_PREDICATES = {
    'selection': r'选择|选用|选定|采用|选',
    'execution': r'执行|实施|运行|安排',
    'measurement': r'测试|试验|比较|验证|测',
}


def action_statement(before):
    """Classify a directly attached action predicate, not a distant negation.

    The categories describe whether a level is being denied as a selected,
    executed or measured action. They never infer a transport outcome. A later
    contrasting predicate starts its own statement; double negation is not a
    denial of the action.
    """
    tail = re.split(r'但是|然而|而是|并且|随后|然后|但|却|而', before)[-1]
    for action, predicate in ACTION_PREDICATES.items():
        match = re.search(r'(?P<negative>(?:(?:并|尚|暂|仍|还|从)?未(?:曾)?|(?:并)?没有|不曾|还没))'
                          r'(?:实际|最终|继续|直接)?(?:' + predicate + r')(?:了|过)?\s*$', tail)
        if match:
            double_negative = re.search(r'不是|并非|并不是', tail[max(0,match.start()-5):match.start()])
            return dict(action=action,polarity='affirmed' if double_negative else 'negated',prefix=tail)
    return dict(action=None,polarity='unspecified',prefix=tail)


def parking_source_contract():
    return dict(version='parking-claim-sources-v1', sources={
        'baseline': '本组无治理基准的区域道路parking字段',
        'observed': '明确场景及区域的道路parking字段；未治理区域不继承其他区域的改动',
        'executed': '所指区域已执行changes中的parking字段',
        'proposed': '未来或条件措施，不作为已执行等级断言'},
        boundary='仅核对可定位的场景与区域；跨组、跨需求或混合等级歧义保留未判定，不认证任意自然语言。')


def _region_names(clause):
    """Normalize explicit region identifiers, never a count of directions."""
    return {'area_'+number for pair in re.findall(
        r'(?<![A-Za-z0-9_])area_(\d+)(?![A-Za-z0-9_])|(?:候选区|区域|面积|区)\s*(\d+)',
        clause) for number in pair if number}


def _parking_source(observations, region_ids, scene_refs, before, after, local_job, level, proposed_changes):
    """Bind a level to the original observation and its actual spatial edits.

    Each trial is an independent scenario. We do not pretend the latest trial
    is a cumulative city state. Without an explicit scene, an already treated
    region refers to its measured edits; an untreated region uses the unchanged
    observed road state. Ambiguous groups/demands/levels remain unverified.
    """
    baseline = bool(re.search(r'基准|治理前|原先|原来|原为|从\s*$|由\s*$', before))
    if not region_ids and not scene_refs and not baseline:
        return None  # Retain the existing bounded scene/road check.
    observations = [o for o in observations if
                    (o['ref'] in scene_refs if scene_refs else
                     not local_job or o['ref'].split(':')[0] == local_job)]
    selected = [(o, r) for o in observations for r in (o.get('spatial') or {}).get('regions', [])
                if not region_ids or r.get('id') in region_ids]
    if not selected and not region_ids:
        return None
    bound = dict(source='unresolved', refs=[], levels=[], region_ids=sorted(region_ids))
    if len({o['ref'].split(':')[0] for o, _ in selected}) != 1:
        return bound
    # A hypothetical response to a level is not a claim it was implemented.
    asserted_action = bool(re.search(r'已(?:经)?(?:测|试|执行|实施|运行|选择|选定)|实际(?:执行|实施)|测得', before))
    if not asserted_action and re.match(r'\s*(?:可能|或许|有望|若|如)', after):
        return dict(bound, source='proposed')
    state_asserted = bool(re.search(r'仍|现状|同为|为\s*$|是\s*$', before))
    directions = {tuple(d) for _, region in selected for d in region.get('directions', [])}
    proposed_levels = {c['parking'] for c in proposed_changes if type(c.get('parking')) is int
                       and (c.get('from_node'), c.get('to_node')) in directions}
    if not asserted_action and not baseline and not state_asserted and level in proposed_levels:
        return dict(bound, source='proposed')
    edits = []
    for observation, region in selected:
        directions = {tuple(d) for d in region.get('directions', [])}
        changes = [c for c in observation.get('changes', []) if
                   (c.get('from_node'), c.get('to_node')) in directions and type(c.get('parking')) is int]
        if changes:
            edits.append((observation, [c['parking'] for c in changes]))
    if asserted_action or (edits and not baseline and not scene_refs and not state_asserted):
        source, values = 'executed', edits
        if asserted_action and not values:
            return dict(bound, source=source, missing=True, refs=list(dict.fromkeys(o['ref'] for o, _ in selected)))
    else:
        if not baseline and not scene_refs and not state_asserted:
            return bound
        source = 'baseline' if baseline or (selected and all(not o.get('changes') for o, _ in selected)) else 'observed'
        if state_asserted and edits and not baseline and not scene_refs:
            edited_refs = {o['ref'] for o, _ in edits}
            selected = [(o, r) for o, r in selected if o['ref'] in edited_refs]
        values = [(o, [road['parking'] for road in r.get('roads', []) if type(road.get('parking')) is int])
                  for o, r in selected if not baseline or not o.get('changes')]
    # Incomplete spatial road values or disagreement across scenarios cannot
    # establish a unique level. Never substitute another region's action.
    levels = {v for _, row in values for v in row}
    if not values or any(not row for _, row in values) or len(levels) != 1:
        return bound
    return dict(bound, source=source, refs=list(dict.fromkeys(o['ref'] for o, _ in values)), levels=sorted(levels))


def level_issues(text, field, rows, local_job=None, known_roads=(), *, regions_by_ref=None, action_polarity=False,
                 source_observations=None, proposed_changes=()):
    issues = []
    digits = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4}
    pattern = r'停车(?:治理|等级|措施)?\s*(?:为|降至|降为|改为|设为)?\s*([0-4零一二三四])\s*级|([0-4零一二三四])\s*级停车'
    for sentence in re.split(r'[。；;\n]', text):
        scope = None
        region_ids, scene_refs = set(), set()
        for clause in re.split(r'[，,]', sentence):
            named = named_rows(clause, rows)
            mentioned = [road for road in known_roads if road in clause]
            if named or mentioned:
                scope = named  # An explicitly named unmeasured road clears prior scope.
            if source_observations is not None:
                region_ids = _region_names(clause) or (set() if mentioned else region_ids)
                explicit = named_rows(clause, [dict(ref=o['ref'], run=o.get('run')) for o in source_observations])
                if explicit:
                    scene_refs = {r['ref'] for r in explicit}
            candidates = scope if scope is not None else ([r for r in rows if r['ref'].split(':')[0] == local_job] if local_job else rows)
            if regions_by_ref is not None:
                named_regions=set(re.findall(r'(?<![A-Za-z0-9_])area_\d+(?![A-Za-z0-9_])',clause))
                if named_regions:
                    candidates=[row for row in candidates if named_regions.intersection(regions_by_ref.get(row['ref'],()))]
            if source_observations is None and (not candidates or any(r.get('levels_known') is False for r in candidates)):
                continue
            for match in re.finditer(pattern, clause):
                before, after = clause[:match.start()], clause[match.end():]
                if action_polarity:
                    statement=action_statement(before)
                    if statement['polarity']=='negated':continue
                    before='' if statement['polarity']=='affirmed' else statement['prefix']
                if re.search(r'(?:然后|后续|随后|再)(?:测|测试|试验)\s*$', before):
                    continue  # An explicit next experiment, not "测得/已测试".
                if re.search(r'不能|不得|不要|不应|不代表|并非|不是|未测|未试|尚未|没有测试|待测|计划|拟|追加|若|如果|假如|错写|(?:^|[，,；;\s])(?:测|测试|试验)(?!得|结果)|接下来|将测|准备测', before[-24:]):
                    continue
                if re.search(r'尚未|未测|未试|未比较|待验证|待测', after[:18]):
                    continue
                if re.search(r'(?:与|和|及|/)[01234一二三四]级', after[:8]):
                    continue  # A comparison of levels is not an executed-level assertion.
                raw = match.group(1) or match.group(2)
                level = int(raw) if raw.isdigit() else digits[raw]
                binding = (_parking_source(source_observations, region_ids, scene_refs, before, after, local_job, level, proposed_changes)
                           if source_observations is not None else None)
                if binding is not None:
                    if binding.get('missing'):
                        issues.append(dict(code='unmeasured_parking_action', field=field, refs=binding['refs'],
                            quote=match.group(), fact_source=binding['source'], region_ids=binding['region_ids'],
                            message='所指区域没有对应的已执行停车改动，不能将现状或备选写成已经实测。'))
                    elif binding['levels'] and level not in binding['levels']:
                        actual='/'.join(map(str,binding['levels']))
                        source_label='已执行改动' if binding['source']=='executed' else '道路观测'
                        issues.append(dict(code='executed_parking_level' if binding['source']=='executed' else 'observed_parking_level',
                            field=field, refs=binding['refs'], quote=match.group(), fact_source=binding['source'],
                            region_ids=binding['region_ids'], message=f'所指区域的{source_label}为停车{actual}级，文字写成{level}级。'))
                    continue
                if not candidates or any(r.get('levels_known') is False for r in candidates):
                    continue
                known = sorted({v for row in candidates for v in row['parking_levels']})
                if known and level not in known:
                    issues.append(dict(code='executed_parking_level', field=field,
                        refs=[r['ref'] for r in candidates if r['parking_levels']], quote=match.group(),
                        message=f'该实测场景实际执行停车{"/".join(map(str, known))}级，文字写成{level}级；S1/S2/S3是场景编号，不是停车等级。'))
    return issues


def status_issues(text, field, rows, local_job=None):
    """Check explicit shared pass/fail assertions for identifiable measured scenes.

    This is deliberately narrower than interpreting free-form recommendations.
    An unqualified programme-wide sentence never inherits the current road.
    """
    issues = []
    pattern = r'(?:均|都|全部|同为|同样)(?:已|是)?(?:未通过|不通过|不合格|失败|通过|合格)'
    for clause in re.split(r'[。；;，,\n]', text):
        road_rows = [r for r in rows if any(n in clause for n in r.get('roads', []))]
        scoped = road_rows or ([r for r in rows if r['ref'].split(':')[0] == local_job] if local_job else rows)
        # Exact IDs can identify scenes even when several studies share a road.
        named = named_rows(clause, scoped)
        if named and not any((r.get('run') or '') in clause or r['ref'] in clause or
                             (re.match(r'^S[123]_', r.get('run') or '') and re.search(
                                 r'(?<![A-Za-z0-9_])'+r['run'].split('_')[0]+r'(?![A-Za-z0-9_])', clause)) for r in named):
            named = []  # A road alone is not a claim about every treatment on it.
        if not named:
            if len({r['ref'].split(':')[0] for r in scoped}) != 1:
                continue
            roles = {'S1_parking': r'停车', 'S2_oneway': r'单向', 'S2_turn': r'禁转', 'S3_combined': r'组合|叠加'}
            named = [r for r in scoped if any(re.search(role, clause) and
                     re.search(r'(?:^|_)'+label+r'$', r.get('run') or '') for label, role in roles.items())]
        if not named:
            continue
        for match in re.finditer(pattern, clause):
            before = clause[max(0, match.start()-24):match.start()]
            if re.search(r'不能|不得|不应|并非|不是|不代表|避免|未测|待测|若|如果|假如|可能', before):
                continue
            claimed = not any(s in match.group() for s in ('未通过', '不通过', '不合格', '失败'))
            wrong = [r for r in named if type(r.get('constraint_passed')) is bool and r['constraint_passed'] != claimed]
            if wrong:
                issues.append(dict(code='measured_constraint_status', field=field, refs=[r['ref'] for r in wrong],
                    quote=match.group(), message='所指措施的实测约束状态不同，不能概括为同时通过或同为失败；逐项保留单项与组合的结果。'))
    return issues
