"""Reuse identical facts in the wire copy; keep the saved input complete.

Only secondary summaries can be replaced. Observations, action menus, frozen
programme findings and the adverse-cost table remain inline. References never
point to another replacement, and expansion must reproduce the input exactly.
"""
import copy
import hashlib
import json

LEGACY_VERSION='referenced-planning-input-v1'
VERSION='referenced-planning-input-v2'
SUPPORTED_VERSIONS=(LEGACY_VERSION,VERSION)
MARKER='$input_ref'
PRIMARY=(('observations',),('allowed_actions',),('context','programme_evidence'),
         ('context','measured_conclusion'))
SECONDARY=(('context','completed'),('context','research_evidence'),('context','programme_comparisons'))


def pointer(path):
    return '#/'+ '/'.join(str(x).replace('~','~0').replace('/','~1') for x in path)


def at(root,path):
    for key in path:root=root[key]
    return root


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def nodes(value,path=()):
    yield path,value
    if isinstance(value,dict):
        for k,v in value.items():yield from nodes(v,path+(k,))
    elif isinstance(value,list):
        for i,v in enumerate(value):yield from nodes(v,path+(i,))


def expand(payload):
    """For offline verification and synthetic clients, never to change records."""
    meta=payload.get('shared_input')
    if not meta:return copy.deepcopy(payload)
    if meta.get('version') not in SUPPORTED_VERSIONS:raise ValueError('未知的共享输入协议')
    if meta.get('version')==VERSION and payload.get('context',{}).get('input_protocol')!=VERSION:
        raise ValueError('共享输入版本与冻结输入协议不符')
    for path in PRIMARY:
        try:value=at(payload,path)
        except KeyError:continue
        if any(isinstance(v,dict) and MARKER in v for _,v in nodes(value)):
            raise ValueError('原始事实不能循环引用或引用另一替代项')
    def resolve(value):
        if isinstance(value,dict):
            if MARKER in value:
                ref=value[MARKER]
                if not isinstance(ref,str) or not ref.startswith('#/'):raise ValueError('共享事实引用无效')
                keys=[s.replace('~1','/').replace('~0','~') for s in ref[2:].split('/')]
                if not any(keys[:len(p)]==list(p) for p in PRIMARY):raise ValueError('共享事实只能引用保留的原始观测或检验')
                target=payload
                for key in keys:target=target[int(key)] if isinstance(target,list) else target[key]
                if any(isinstance(v,dict) and MARKER in v for _,v in nodes(target)):
                    raise ValueError('共享事实不能循环引用或引用另一替代项')
                if len(value)>1:
                    if not isinstance(target,dict):raise ValueError('只有事实对象可附加本地字段')
                    return dict(copy.deepcopy(target),**{k:resolve(v) for k,v in value.items() if k!=MARKER})
                return copy.deepcopy(target)
            return {k:resolve(v) for k,v in value.items()}
        if isinstance(value,list):return [resolve(v) for v in value]
        return value
    return resolve({k:v for k,v in payload.items() if k!='shared_input'})


def validate_protocol(payload):
    if payload.get('context',{}).get('input_protocol') not in (None,*SUPPORTED_VERSIONS):
        raise ValueError('未知的共享输入协议')


def serialize(payload):
    """Serialize the actual wire projection, preserving legacy request bytes."""
    validate_protocol(payload)
    if payload.get('context',{}).get('input_protocol')==VERSION:
        return json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return json.dumps(payload,ensure_ascii=False)


def wire_stamp(content):
    raw=content.encode('utf-8')
    return dict(version=VERSION,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())


def share(payload):
    validate_protocol(payload)
    version=payload.get('context',{}).get('input_protocol')
    if version is None:return copy.deepcopy(payload)
    # A reserved field in supplied data is not reinterpreted as a reference.
    if 'shared_input' in payload or any(isinstance(v,dict) and MARKER in v for _,v in nodes(payload)):
        return copy.deepcopy(payload)
    anchors={};by_ref={};findings={}
    for path in PRIMARY:
        try:value=at(payload,path)
        except KeyError:continue
        for suffix,node in nodes(value):
            if not isinstance(node,(dict,list)):continue
            key=canonical(node)
            if len(key.encode('utf-8'))>=240:anchors.setdefault(key,pointer(path+suffix))
            if isinstance(node,dict) and isinstance(node.get('ref'),str):
                by_ref.setdefault(node['ref'],[]).append((pointer(path+suffix),node))
            if path==('context','programme_evidence') and isinstance(node,dict) and isinstance(node.get('id'),str):
                findings[node['id']]=(pointer(path+suffix),node)
    result=copy.deepcopy(payload);count=0
    groups={g['job_id']:g['id'] for g in payload['context'].get('programme_evidence',{}).get('groups',[])
            if isinstance(g.get('job_id'),str) and isinstance(g.get('id'),str)}
    def replace(value,group=None):
        nonlocal count
        group=groups.get(value.get('job_id'),group) if isinstance(value,dict) else group
        if isinstance(value,(dict,list)):
            ref=anchors.get(canonical(value))
            replacements=[{MARKER:ref}] if ref else []
            if isinstance(value,dict):
                # Same scene, with extra details such as newly congested links.
                for location,base in by_ref.get(value.get('ref'),[]):
                    if all(k in value and canonical(value[k])==canonical(v) for k,v in base.items()):
                        replacements.append(dict({MARKER:location},**{k:v for k,v in value.items() if k not in base}))
                # A local finding and its programme copy differ only in their
                # local id/title and possibly additional measurement details.
                id=value.get('id')
                match=findings.get((group+'.'+id) if group and isinstance(id,str) and not id.startswith('hypothesis.') else id)
                if match and set(match[1])<=value.keys() and all(k in value and canonical(value[k])==canonical(v)
                                 for k,v in match[1].items() if k not in ('id','question')):
                    replacements.append(dict({MARKER:match[0]},**{k:v for k,v in value.items()
                        if k not in match[1] or canonical(v)!=canonical(match[1][k])}))
            if replacements:
                replacement=min(replacements,key=lambda r:len(canonical(r).encode()))
                if len(canonical(value).encode())-len(canonical(replacement).encode())>=120:
                    count+=1
                    return replacement
        if isinstance(value,dict):return {k:replace(v,group) for k,v in value.items()}
        if isinstance(value,list):return [replace(v,group) for v in value]
        return value
    for path in SECONDARY:
        try:value=at(payload,path)
        except KeyError:continue
        at(result,path[:-1])[path[-1]]=replace(value,groups.get(payload['context'].get('programme_current_job')))
    if not count:return result
    result['shared_input']=dict(version=version,references=count,
        meaning='含$input_ref的对象复用该JSON路径的完整事实，同行字段补充或覆盖本地名称；不是缺测。原始观测、动作目录、总任务检验和实测代价表保持完整。')
    if canonical(expand(result))!=canonical(payload):raise ValueError('共享输入不能完整还原，停止发送')
    return result if len(canonical(result).encode())<len(canonical(payload).encode()) else copy.deepcopy(payload)
