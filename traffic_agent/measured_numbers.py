"""Check explicit, scoped before/after time claims against completed observations.

Only labelled pcu·h pairs are parsed, with tolerance from the written precision.
This is not a general language judge: ambiguous/unmeasured references stay unknown,
and future plans or quoted corrections are not treated as measured assertions.
"""
import math
import re

NUMBER = r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
HEAD = re.compile(r'(?P<scope>核心(?:治理区|范围|区域|区)?|相邻影响区|邻区|周边(?:范围|区域|区)?|全网|网络|(?:候选区\s*)?area_\d+)\s*(?:行驶时间消耗|行驶耗时|运行耗时|耗时)')
PAIR = re.compile(r'(?P<before>'+NUMBER+r')\s*(?:→|->|降至|降为|增至|升至|到)\s*(?P<after>'+NUMBER+r')\s*pcu\s*[·⋅•.\-]?\s*h\b', re.I)
PERCENT = re.compile(r'(?P<direction>下降|降低|减少|降|增加|上升|增|升)\s*(?:约|了)?\s*(?P<value>'+NUMBER+r')\s*[%％]')
NAMES = dict(core='核心范围', neighbor='邻区', network='全网')


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _written(raw):
    value = float(raw.replace(',', ''))
    decimals = len(raw.partition('.')[2])
    return value, 0.5 * 10 ** -decimals + 1e-8


def _nonassertion(text):
    return bool(re.search(r'不能|不得|不要|不应|并非|不是|错写|误写|预计|预测|假设|若|如果|假如|尚未|待测|计划|拟测', text))


def paired_facts(observations):
    """No cross-demand, cross-job or policy-to-policy baseline substitution."""
    from .executed_facts import policy_facts
    rows = []
    for observed in observations:
        if not observed.get('changes'):
            continue
        metadata = policy_facts([observed])[0]
        base = next((o for o in observations if not o.get('changes')
                     and 'repeat' not in (o.get('run') or '')
                     and o.get('ref', '').split(':')[0] == observed['ref'].split(':')[0]
                     and o.get('demand_factor') == observed.get('demand_factor')), None)
        if base:
            for scope in NAMES:
                before = _number(base.get('metrics', {}).get(scope, {}).get('running_time_pcu_h'))
                after = _number(observed.get('metrics', {}).get(scope, {}).get('running_time_pcu_h'))
                if before is not None and after is not None:
                    rows.append(dict(metadata, scope=scope, before=before, after=after))
        for region in observed.get('regional_checks', []):
            before = _number(region.get('before', {}).get('running_time_pcu_h'))
            after = _number(region.get('after', {}).get('running_time_pcu_h'))
            if before is not None and after is not None and re.fullmatch(r'area_\d+', region.get('region', '')):
                rows.append(dict(metadata, scope=region['region'], before=before, after=after))
    return rows


def issues(value, observations, context):
    from .executed_facts import named_rows, policy_facts
    facts = paired_facts(observations)
    metadata = {r['ref']: r for r in context.get('programme_cost_facts', [])}
    metadata.update({r['ref']: r for r in policy_facts(observations)})
    result = []
    texts = [(k, value.get(k, '')) for k in ('summary', 'reason', 'risk', 'next_check', 'programme_summary')]
    texts += [(key+':'+u['hypothesis_id'], u['explanation'])
              for key in ('hypothesis_updates', 'programme_updates') for u in value.get(key, [])]
    for field, text in texts:
        for clause in re.split(r'[。；;，\n]|(?<!\d),|,(?!\d)', text):
            heads = list(HEAD.finditer(clause))
            for i, head in enumerate(heads):
                end = heads[i+1].start() if i+1 < len(heads) else len(clause)
                claim = clause[head.end():end]
                if _nonassertion(clause[:head.end()]) or _nonassertion(claim):
                    continue
                pair = PAIR.search(claim)
                if not pair:
                    continue
                label = head['scope']
                scope = ('core' if label.startswith('核心') else 'network' if label in ('全网', '网络') else
                         re.search(r'area_\d+', label).group() if 'area_' in label else 'neighbor')
                named = named_rows(clause, list(metadata.values()))
                refs = {r['ref'] for r in named} if named else set(value.get('evidence_refs', []))
                candidates = [r for r in facts if r['scope'] == scope and r['ref'] in refs]
                # Two explicitly named policies may describe a direct contrast,
                # not a baseline pair. The arithmetic check still applies.
                unique_ref = len({r['ref'] for r in candidates}) == 1
                before, before_tol = _written(pair['before'])
                after, after_tol = _written(pair['after'])
                quote = head.group()+claim[:pair.end()]
                if unique_ref and not any(abs(r['before']-before) <= before_tol and
                                          abs(r['after']-after) <= after_tol for r in candidates):
                    expected = candidates[0]
                    result.append(dict(code='measured_time_scope', field=field, refs=[expected['ref']], quote=quote,
                        message=f"{NAMES.get(scope, scope)}同需求实测耗时为{expected['before']:.3f}→{expected['after']:.3f} pcu·h，与原文不符；候选区、核心、邻区和全网必须分别引用。"))
                    continue
                pct = PERCENT.search(claim[:pair.start()])
                if pct and before-before_tol > 0:
                    written, tolerance = _written(pct['value'])
                    if pct['direction'] in ('下降', '降低', '减少', '降'):
                        written = -written
                    low = ((after-after_tol)/(before+before_tol)-1)*100
                    high = ((after+after_tol)/(before-before_tol)-1)*100
                    if written+tolerance < low or written-tolerance > high:
                        result.append(dict(code='measured_time_arithmetic', field=field, refs=sorted(refs), quote=quote,
                            message='所写前后耗时与变化百分比不一致（已考虑显示精度）；须使用同一范围和同一对照，不能混合不同范围的绝对值和比例。'))
    return result
