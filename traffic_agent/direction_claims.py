"""Bind explicit direction predicates to local node pairs or reverse references.

This finite grammar detects clear reversed claims, not arbitrary entailment.
Parentheses and colon-led clauses have their own referents; an operation named
in an introductory clause must not attach to a node pair in the next clause.
"""
import re

VERSION='oneway-direction-v4'
PAIR=re.compile(r'(\d+)→(\d+)')
VERB=re.compile(r'禁止通行|保持通行|开放通行|保留通行|单向通行|保留|关闭|禁行|封闭')
CLOSE={'禁止通行','关闭','禁行','封闭'}


def clauses(text):
    text=re.sub(r'[^\S\n]+','',text).replace('->','→').replace('—>','→')
    # "关闭：A→B" is an explicit predicate heading. A longer introduction
    # such as "禁行后需核对：" does not have that grammatical role.
    text=re.sub(r'(^|[，,。；;、\n（）()])((?:拟|计划|请|应)?(?:关闭|禁行|禁止通行|封闭|保留通行|保持通行|开放通行|保留)(?:方向)?)[：:](?=\d+→)',
                r'\1\2',text)
    return re.split(r'[，,。；;、\n（）():：]|但|然而|随后',text)


def claims(value,selected):
    pair=selected.get('closed_direction')
    if not isinstance(pair,list) or len(pair)!=2:return []
    closed=tuple(pair);opened=closed[::-1]
    fields=[(k,value.get(k)) for k in ('summary','reason','risk','next_check')]
    basis=value.get('selection_basis')
    if isinstance(basis,dict):fields.extend(('selection_basis.'+k,v) for k,v in basis.items())
    result=[]
    for field,text in fields:
        if not isinstance(text,str):continue
        for clause in clauses(text):
            refs=[dict(start=m.start(),end=m.end(),pair=(int(m[1]),int(m[2])),kind='explicit') for m in PAIR.finditer(clause)]
            for m in re.finditer(r'反向|相反方向',clause):
                # In "反向 B→A", the written pair is the full reference.
                if any(0<=r['start']-m.end()<=3 or m.start()==r['end'] for r in refs):continue
                refs.append(dict(start=m.start(),end=m.end(),pair=opened,kind='reverse'))
            for m in VERB.finditer(clause):
                verb=m.group()
                # A destination such as "导向保留方向" describes another edge,
                # not the subject of the preceding closure predicate.
                if (verb=='保留' and clause[m.end():].startswith('方向')
                        and re.search(r'(?:导向|转向|转移至|引导至|分流至|改走)$',clause[:m.start()])):continue
                before=[r for r in refs if r['end']<=m.start() and m.start()-r['end']<=8]
                after=[r for r in refs if r['start']>=m.end() and r['start']-m.end()<=8]
                if verb=='单向通行':
                    before=[r for r in before if m.start()-r['end']<=2]
                    after=[r for r in after if r['start']-m.end()<=2]
                # Bare 保留 takes a following object, including an adjectivally
                # qualified reverse direction. Completed 保留通行 is a predicate.
                objects=[r for r in after if not re.search(r'通行|关闭|禁行|且|而|后',clause[m.end():r['start']])]
                if verb=='保留' and objects:ref=min(objects,key=lambda r:r['start'])
                elif before or after:
                    ref=min(before+after,key=lambda r:(min(abs(m.start()-r['end']),abs(r['start']-m.end())),r['kind']!='explicit'))
                else:continue
                result.append(dict(field=field,clause=clause,direction=list(ref['pair']),
                    role='closed' if verb in CLOSE else 'retained',reference_kind=ref['kind'],predicate=verb))
    return result


def match(value,selected):
    pair=selected.get('closed_direction')
    if not isinstance(pair,list) or len(pair)!=2:return True
    expected={tuple(pair):'closed',tuple(pair[::-1]):'retained'}
    return all(expected.get(tuple(c['direction']),c['role'])==c['role'] for c in claims(value,selected))
