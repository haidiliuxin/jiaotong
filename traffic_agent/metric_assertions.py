"""Resolve bounded numeric assertions to typed measured fields.

Subjects may be coordinated, but their units and observed values never merge.
Only explicit equality claims with an unambiguous measured reference are
checked. This is not unrestricted language entailment or a recommendation rule.
"""
import math
import re

VERSION = 'measured-metric-assertions-v1'
SCOPES = {'核心': 'core', '邻区': 'neighbor', '周边': 'neighbor', '全网': 'network'}
METRICS = {
    'time_change_pct': dict(label='耗时变化', unit='%',
        pattern=r'(?:总)?耗时(?:增幅|变化率|变化|增加率)'),
    'net_oversaturated_km': dict(label='过饱和净变化', unit='km',
        pattern=r'过饱和(?:有向)?(?:里程)?净变化|净过饱和(?:里程)?变化'),
    'new_oversaturated_km': dict(label='毛新增过饱和', unit='km',
        pattern=r'(?:毛)?新增过饱和(?:有向)?(?:里程)?'),
}
SUBJECT = re.compile(r'(?P<scope>核心(?:范围)?|邻区|周边|全网)?\s*(?:的)?\s*(?:'
                     + '|'.join(f'(?P<{key}>{data["pattern"]})' for key, data in METRICS.items()) + ')')
CONNECTOR = re.compile(r'\s*(?:与|和|及|、|以及)\s*')
EQUALITY = re.compile(r'\s*(?P<all>均|都)?\s*(?P<approx1>近似|大约|约)?\s*'
                      r'(?:为|是|等于|=|：|:)\s*(?P<approx2>近似|大约|约)?\s*'
                      r'(?P<number>零|[+−-]?\d+(?:\.\d+)?)'
                      r'\s*(?P<unit>[%％]|km|公里|千米)?(?![\d.])', re.I)
NONASSERTION = re.compile(r'不能|不得|不应|无法|不足以|并非|不是|尚未|未能|未证明|不代表|不等于|'
                          r'错写|误写|若|如果|假如|预计|预测|假设|计划|待测|拟测')
FAMILIES = {
    'parking': re.compile(r'(?:仅)?停车(?:\d级)?(?:方案|单项|措施)'),
    'traffic_control': re.compile(r'(?:仅)?(?:单向|禁转)(?:方案|单项|措施)'),
    'combined': re.compile(r'组合(?:方案|措施)?'),
}


def catalog():
    """A compact vocabulary over existing cost_facts keys, not copied metrics."""
    return dict(version=VERSION, scopes=SCOPES,
                metrics={key:{k:v for k,v in data.items() if k != 'pattern'} for key,data in METRICS.items()},
                rule='范围+指标对应costs同名字段。明确零值须为实际零；近似值按所写精度核对。并列指标分别判断，未测值不视为零。')


def parse(text):
    """Emit scoped subject/predicate/value records; ambiguous syntax is ignored."""
    if not isinstance(text, str):
        return
    # Scope and polarity do not leak through contrast or sentence boundaries.
    for clause in re.split(r'[。；;，,\n]|但是|然而|不过|而是|但', text):
        subjects = list(SUBJECT.finditer(clause))
        i = 0
        while i < len(subjects):
            group = [subjects[i]]
            while i+1 < len(subjects) and CONNECTOR.fullmatch(clause[group[-1].end():subjects[i+1].start()]):
                i += 1
                group.append(subjects[i])
            i += 1
            equality = EQUALITY.match(clause, group[-1].end())
            if not equality or len(group) > 1 and not equality['all']:
                continue
            if re.match(r'\s*(?:至|到|~|～|—)', clause[equality.end():]):
                continue  # An interval is not equality to its first endpoint.
            prefix = clause[:group[0].start()]
            if NONASSERTION.search(prefix) or re.search(r'设定|设置|门槛|阈值|上限', prefix):
                continue
            scope = None
            for subject in group:
                if subject['scope']:
                    scope = SCOPES.get(subject['scope'][:2])
                if scope is None:
                    continue
                metric = next(key for key in METRICS if subject[key])
                raw = equality['number'].replace('−', '-')
                numeric = 0.0 if raw == '零' else float(raw)
                if not math.isfinite(numeric):
                    continue
                unit = equality['unit']
                if unit:
                    unit = '%' if unit in ('%', '％') else 'km'
                approximate = bool(equality['approx1'] or equality['approx2'])
                # A shared unitless zero is meaningful for mixed units; other
                # mixed-unit values need separate, explicit predicates.
                if not unit and numeric != 0 and len({METRICS[k]['unit'] for k in METRICS
                        if any(s[k] for s in group)}) > 1:
                    continue
                yield dict(scope=scope, metric=metric, value=numeric, unit=unit or METRICS[metric]['unit'],
                           approximate=approximate, decimals=len(raw.partition('.')[2]),
                           quote=clause[group[0].start():equality.end()].strip(), clause=clause,
                           prefix=prefix)


def _candidates(claim, rows, value, context, field):
    from .executed_facts import named_rows
    local = context.get('programme_current_job') if not field.startswith('programme_') else None
    scoped = [r for r in rows if not local or r['ref'].split(':')[0] == local]
    named = named_rows(claim['prefix'], scoped)
    if named:
        scoped = named
    elif re.search(r'\bS\d+|area_\d+', claim['prefix']):
        return []  # An unknown explicit target cannot inherit another result.
    else:
        refs = set(value.get('evidence_refs', []))
        if refs:
            scoped = [r for r in scoped if r['ref'] in refs]
    families = [key for key, pattern in FAMILIES.items() if pattern.search(claim['prefix'])]
    if len(families) == 1:
        scoped = [r for r in scoped if r.get('policy_family') == families[0]]
    elif len(families) > 1:
        return []
    demand = re.search(r'需求\s*[×xX]\s*(\d+(?:\.\d+)?)', claim['prefix'])
    if demand:
        scoped = [r for r in scoped if r.get('demand_factor') == float(demand[1])]
    # A single declaration does not silently become a universal statement over
    # other studies, candidate areas or demand factors.
    return scoped if len(scoped) == 1 else []


def issues(value, facts, context, observations=()):
    from .hypothesis_contract import kind
    metadata = {r['ref']: r for r in facts.get('executed_policies', [])}
    families = {o['ref']: kind(o['changes']) for o in observations if o.get('changes')}
    rows = [dict(r, **{k:v for k,v in metadata.get(r['ref'], {}).items() if k not in r},
                 policy_family=families.get(r['ref'])) for r in facts.get('costs', [])]
    texts = [(k,value.get(k,'')) for k in ('summary','reason','risk','next_check','programme_summary')]
    texts += [(k+':'+u['hypothesis_id'],u['explanation']) for k in ('hypothesis_updates','programme_updates')
              for u in value.get(k,[])]
    result = []
    for field, text in texts:
        for claim in parse(text):
            candidates = _candidates(claim, rows, value, context, field)
            if not candidates:
                continue
            row = candidates[0]; key = claim['scope']+'_'+claim['metric']
            actual = row.get(key); unit = METRICS[claim['metric']]['unit']
            label = {'core':'核心','neighbor':'邻区','network':'全网'}[claim['scope']] + METRICS[claim['metric']]['label']
            if type(actual) not in (int,float) or not math.isfinite(actual):
                result.append(dict(code='unmeasured_metric_assertion',field=field,refs=[row['ref']],quote=claim['quote'],
                    metric=key,claimed_value=claim['value'],claimed_unit=claim['unit'],observed_value=None,unit=unit,
                    message=f'{row.get("run") or row["ref"]} 未提供{label}的有效观测，不能填零或其他数值代替。'))
                continue
            tolerance = .5 * 10 ** -claim['decimals'] + 1e-10
            matches = (actual == 0 if claim['value'] == 0 and not claim['approximate'] else
                       abs(actual-claim['value']) <= tolerance)
            if matches and claim['unit'] == unit:
                continue
            result.append(dict(code='measured_metric_assertion',field=field,refs=[row['ref']],quote=claim['quote'],
                metric=key,claimed_value=claim['value'],claimed_unit=claim['unit'],observed_value=actual,unit=unit,
                message=f'{row.get("run") or row["ref"]} 的{label}实测为 {actual:+.8g}{unit}，与说明不符；'
                        '并列指标须分别核对，极小非零值不能直接写成零。'))
    return result
