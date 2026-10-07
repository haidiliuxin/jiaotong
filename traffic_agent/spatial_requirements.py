"""Executable obligations for an explicitly requested cross-area comparison."""
import re


def required_regions(request):
    # Generic 'protect the neighborhood' is not an instruction to treat it.
    text=re.sub(r'不(?:用|必|要)?(?:比较|对比|评估)[^，。；;]*', '', request)
    match=re.search(r'(?:比较|对比|评估).{0,10}?([二两三四234](?:个|处)|不同).{0,6}(?:区域|片区|拥堵点)',text)
    if not match:return 1
    return {'二':2,'两':2,'三':3,'四':4,'2':2,'3':3,'4':4}.get(match[1][0],2)


def task_regions(task):
    return task.get('comparison_regions') or required_regions(task.get('request',''))


def progress(state, strength):
    from .planning_review import footprint
    required=task_regions(state['task'])
    factors=state['task'].get('demand_factors',[1])
    policies=[r for r in state.get('runs',[]) if r.get('changes') and r.get('execution',{}).get('status')=='completed']
    anchors={}
    for r in policies:
        fp=set(footprint(r['changes']))
        area=next((a['id'] for a in state.get('regions',[]) if fp and fp<=set(map(tuple,a['directions']))),None)
        if area:anchors.setdefault(area,r)
    selected=list(anchors.items())[:required]
    common_level=selected[0][1]['changes'][0]['parking'] if selected else None
    pending=[]
    complete=[]
    for area,r in selected:
        needed={1,2} if strength else {common_level}
        measured={level for level in needed if all(any(p['demand_factor']==f and footprint(p['changes'])==footprint(r['changes']) and {c['parking'] for c in p['changes']}=={level} for p in policies) for f in factors)}
        if measured==needed:complete.append(area)
        for level in sorted(needed-measured):pending.append(dict(area=area,level=level,footprint=footprint(r['changes'])))
    missing=max(0,required-len(selected))
    return dict(required=required,anchors=anchors,completed=complete,pending=pending,
                remaining=missing*(2 if strength else 1)+len(pending),
                enough_areas=len(state.get('regions',[]))>=required)


def restrict(options, state, strength):
    from .planning_review import footprint
    p=progress(state,strength)
    if not p['enough_areas']:return {},p
    if p['pending']:
        next_=p['pending'][0]
        options={k:v for k,v in options.items() if v.get('region')==next_['area'] and footprint(v['changes'])==next_['footprint'] and {c['parking'] for c in v['changes']}=={next_['level']}}
    elif len(p['anchors'])<p['required']:
        options={k:v for k,v in options.items() if v.get('region') not in p['anchors']}
        if p['anchors'] and not strength:
            level=next(iter(p['anchors'].values()))['changes'][0]['parking']
            options={k:v for k,v in options.items() if {c['parking'] for c in v['changes']}=={level}}
    return options,p
