"""Explicit model patches for measures and quantifiers; original wording stays fixed."""
import copy
import hashlib
import json
import re
from . import hypothesis_policy_patch as policy
from .llm_client import ModelError,function

VERSION='hypothesis-field-patch-v1'
NAME='correct_hypothesis_fields'
ORIGIN='original_plus_model_field_patch'
INSTRUCTIONS='''你正在修正一次交通研究规划中的判据字段。只调用一次correct_hypothesis_fields。每项假设只能提交工具列出的字段和值；quantifier必须符合保留的原话：“至少一个/项/种”为any，“所有/全部”为all。不得改写原问题、证据需求、指标、研究组或实验条件。程序保留原方案，应用你明确提交的字段后重新校验。尚未仿真，没有治理结果；修正计入原六次总预算，与其他初始修正共用一次机会。'''


def options(check):
    if not isinstance(check,dict) or check.get('version')!='initial-hypothesis-correction-v3' or not check.get('correction_feasible'):
        raise ModelError('缺少可用的字段修正范围；未追加请求',request_sent=False)
    issues=check.get('issues');result={}
    if not isinstance(issues,list) or not 1<=len(issues)<=4:raise ModelError('字段修正范围无效',request_sent=False)
    for issue in issues:
        key=issue.get('hypothesis_id');fields=issue.get('allowed_fields',dict(policies=issue.get('allowed_policies')))
        if not isinstance(key,str) or not re.fullmatch(r'H[1-4]',key) or key in result or not isinstance(fields,dict) or not fields:
            raise ModelError('字段修正范围无效',request_sent=False)
        for field,values in fields.items():
            allowed=policy.POLICIES if field=='policies' else ('all','any') if field=='quantifier' else ()
            if not isinstance(values,list) or not values or any(v not in allowed for v in values):
                raise ModelError('字段修正选项无效',request_sent=False)
        result[key]=copy.deepcopy(fields)
    return result


def tool_for(check):
    return function(NAME,'仅选择列出的判据字段，使其与保留的原问题一致；不重述方案。',{
        key:dict(type='object',properties={k:dict(type='string',enum=v) for k,v in fields.items()},
                 required=list(fields),additionalProperties=False) for key,fields in options(check).items()})


def arguments(reply):
    calls=reply.get('calls') if isinstance(reply,dict) else None
    if not isinstance(calls,list) or len(calls)!=1 or not isinstance(calls[0],dict) or calls[0].get('name')!=NAME:
        raise ModelError('须仅调用一次判据字段工具；未启动仿真')
    try:
        value=json.loads(calls[0]['arguments'],object_pairs_hook=policy.unique_object)
        if not isinstance(value,dict):raise ValueError()
    except (KeyError,ValueError,TypeError):raise ModelError('字段参数无效或重复；未启动仿真') from None
    return value


def validate_selection(value,check):
    allowed=options(check)
    if not isinstance(value,dict) or set(value)!=set(allowed):raise ModelError('修正必须恰好包含全部出错假设')
    for key,fields in value.items():
        if not isinstance(fields,dict) or set(fields)!=set(allowed[key]):raise ModelError('修正含未获准字段或缺项')
        if any(not isinstance(v,str) or v not in allowed[key][k] for k,v in fields.items()):raise ModelError('字段值不在原问题允许范围')
    return copy.deepcopy(value)


def capture(reply,source_id):
    calls=reply.get('calls') if isinstance(reply,dict) else None
    count=len(calls) if isinstance(calls,list) else 0
    receipt=dict(version=VERSION,source_attempt_id=source_id,tool_name=None,tool_call_count=count,
                 arguments_valid=False,selections={},status='received')
    if count==1 and isinstance(calls[0],dict) and calls[0].get('name')==NAME:receipt['tool_name']=NAME
    try:
        values=arguments(reply);receipt['arguments_valid']=True
        receipt['selections']={k:{f:v for f,v in fields.items() if isinstance(v,str) and
            (f=='policies' and v in policy.POLICIES or f=='quantifier' and v in ('any','all'))}
            for k,fields in values.items() if re.fullmatch(r'H[1-4]',k) and isinstance(fields,dict)}
    except ModelError:pass
    return receipt


def apply(reply,context):
    selected=validate_selection(arguments(reply),context['check']);proposal=copy.deepcopy(context['proposal'])
    for key,fields in selected.items():proposal['decision']['assessment'][int(key[1:])-1]['criterion'].update(fields)
    return dict(calls=[dict(name='compose_traffic_plan',arguments=json.dumps(proposal,ensure_ascii=False))],
                metadata=reply.get('metadata'),proposal_origin=ORIGIN)


def verify_receipt(first,last):
    from .planning_attempt import safe_proposal
    r=last.get('policy_patch')
    if (not isinstance(r,dict) or set(r)!={'version','source_attempt_id','tool_name','tool_call_count','arguments_valid','selections','status'}
            or r.get('version')!=VERSION or r.get('source_attempt_id')!=first['id'] or r.get('tool_name')!=NAME
            or r.get('tool_call_count')!=1 or r.get('arguments_valid') is not True or r.get('status')!='applied'
            or last.get('proposal_origin')!=ORIGIN):raise ValueError('缺少实际字段调用或原方案来源')
    selected=validate_selection(r['selections'],first['hypothesis_check']);expected=copy.deepcopy(first['proposal'])
    for key,fields in selected.items():expected['assessment'][int(key[1:])-1]['criterion'].update(fields)
    if safe_proposal(expected)!=last.get('proposal'):raise ValueError('字段修正改写了原方案或未忠实应用')
    usage=last.get('usage') or {};wire=usage.get('wire_api');tool=tool_for(first['hypothesis_check'])
    tools=[dict(type='function',function={k:v for k,v in tool.items() if k!='type'})] if wire=='chat_completions' else [tool]
    schema_sha=hashlib.sha256(json.dumps(tools,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    choice=usage.get('requested_tool_choice') if wire=='chat_completions' else usage.get('request_tool_choice')
    if (wire not in ('chat_completions','responses') or choice!='function:'+NAME or usage.get('request_tool_count')!=1
            or usage.get('tool_call_count')!=1 or usage.get('request_schema_sha256')!=schema_sha):
        raise ValueError('字段回执与实际请求结构不符')


def display(value):
    if not isinstance(value,dict) or value.get('version')!=VERSION or not isinstance(value.get('selections'),dict):return None
    reply=dict(calls=[dict(name=NAME,arguments=json.dumps(value['selections']))])
    return dict(version=VERSION,status=value.get('status') if value.get('status') in ('received','rejected','applied') else 'unknown',
                selections=capture(reply,'')['selections'])
