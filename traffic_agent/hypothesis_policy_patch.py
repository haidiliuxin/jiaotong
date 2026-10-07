"""A model-selected field patch; the original wire proposal stays in memory.

No full-plan replay and no inferred replacement. Public receipts distinguish the
actual patch call from the locally compiled proposal, including rejected calls.
"""
import copy
import hashlib
import json
import re

from .hypothesis_contract import POLICIES
from .llm_client import ModelError, function

VERSION='hypothesis-policy-patch-v1'
NAME='correct_hypothesis_policies'
ORIGIN='original_plus_model_policy_patch'
INSTRUCTIONS='''你正在修正一次交通研究规划的检验对象。只调用一次correct_hypothesis_policies；参数仅为本次工具列出的假设编号及其允许的policies枚举。不要重写方案、假设文字或其他字段，不调用compose_traffic_plan。原方案由程序保存，仅应用你明确提交的字段选择，然后重新校验完整方案。原失败和本次用量均保留；这次用掉唯一的初始修正机会，计入原六次请求总额。当前尚未启动交通仿真，没有治理结果。'''


def for_check(check):
    if check.get('version')=='initial-hypothesis-correction-v3' and any('allowed_fields' in i for i in check.get('issues',[])):
        from . import hypothesis_field_patch
        return hypothesis_field_patch
    from . import hypothesis_policy_patch
    return hypothesis_policy_patch


def for_version(version):
    from . import hypothesis_field_patch,hypothesis_policy_patch
    return {VERSION:hypothesis_policy_patch,hypothesis_field_patch.VERSION:hypothesis_field_patch}.get(version) if isinstance(version,str) else None


def options(check):
    issues=check.get('issues') if isinstance(check,dict) else None
    if not isinstance(issues,list) or not 1<=len(issues)<=4 or check.get('correction_feasible') is not True:
        raise ModelError('缺少可用的判据修正范围；未追加请求',request_sent=False)
    allowed={}
    for issue in issues:
        if not isinstance(issue,dict):raise ModelError('判据修正范围无效；未追加请求',request_sent=False)
        key=issue.get('hypothesis_id');values=issue.get('allowed_policies')
        if (not isinstance(key,str) or not re.fullmatch(r'H[1-4]',key) or key in allowed
                or not isinstance(values,list) or not values or any(v not in POLICIES for v in values)):
            raise ModelError('判据修正范围无效；未追加请求',request_sent=False)
        allowed[key]=list(values)
    return allowed


def tool_for(check):
    return function(NAME,'只选择出错假设的被检验措施类型；原方案所有其他内容由程序保持。',
                    {key:dict(type='string',enum=values) for key,values in options(check).items()})


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON field')
        result[key]=value
    return result


def arguments(reply):
    calls=reply.get('calls') if isinstance(reply,dict) else None
    if not isinstance(calls,list) or len(calls)!=1 or not isinstance(calls[0],dict) or calls[0].get('name')!=NAME:
        raise ModelError('修正须仅调用一次判据字段工具；未接受整份重写方案，未启动仿真')
    try:
        value=json.loads(calls[0]['arguments'],object_pairs_hook=unique_object)
        if not isinstance(value,dict):raise ValueError()
    except (KeyError,ValueError,TypeError):
        raise ModelError('判据字段修正参数无效或含重复字段；未启动仿真') from None
    return value


def validate_selection(value,check):
    allowed=options(check)
    if not isinstance(value,dict) or set(value)!=set(allowed):
        raise ModelError('修正只能且必须包含全部出错假设编号；不接受其他方案字段，未启动仿真')
    if any(not isinstance(v,str) or v not in allowed[key] for key,v in value.items()):
        raise ModelError('修正的检验对象不在该假设允许范围内；未启动仿真')
    return copy.deepcopy(value)


def capture(reply,source_id):
    """Only known identifiers/enums, never raw provider text or unknown values."""
    calls=reply.get('calls') if isinstance(reply,dict) else None
    count=len(calls) if isinstance(calls,list) else 0
    receipt=dict(version=VERSION,source_attempt_id=source_id,tool_name=None,
                 tool_call_count=count,arguments_valid=False,selections={},status='received')
    if count==1 and isinstance(calls[0],dict) and calls[0].get('name')==NAME:receipt['tool_name']=NAME
    try:
        value=arguments(reply)
        receipt['arguments_valid']=True
        receipt['selections']={k:v for k,v in value.items() if re.fullmatch(r'H[1-4]',k) and isinstance(v,str) and v in POLICIES}
    except ModelError:pass
    return receipt


def apply(reply,context):
    selected=validate_selection(arguments(reply),context['check'])
    proposal=copy.deepcopy(context['proposal'])
    for key,value in selected.items():proposal['decision']['assessment'][int(key[1:])-1]['criterion']['policies']=value
    # This is a local parser adapter, not another provider response or request.
    return dict(calls=[dict(name='compose_traffic_plan',arguments=json.dumps(proposal,ensure_ascii=False))],
                metadata=reply.get('metadata'),proposal_origin=ORIGIN)


def verify_receipt(first,last):
    """Reconstruct the public proposal, even when an audit digest is resealed."""
    from .planning_attempt import safe_proposal
    receipt=last.get('policy_patch')
    if (not isinstance(receipt,dict) or set(receipt)!={'version','source_attempt_id','tool_name','tool_call_count',
            'arguments_valid','selections','status'} or receipt.get('version')!=VERSION
            or receipt.get('source_attempt_id')!=first['id'] or receipt.get('tool_name')!=NAME
            or receipt.get('tool_call_count')!=1 or receipt.get('arguments_valid') is not True
            or receipt.get('status')!='applied' or last.get('proposal_origin')!=ORIGIN):
        raise ValueError('缺少实际字段修正调用或原方案来源记录')
    selected=validate_selection(receipt['selections'],first['hypothesis_check'])
    expected=copy.deepcopy(first['proposal'])
    for key,value in selected.items():expected['assessment'][int(key[1:])-1]['criterion']['policies']=value
    if safe_proposal(expected)!=last.get('proposal'):
        raise ValueError('编译方案与原提议加模型字段选择不符')
    usage=last.get('usage') or {};tool=tool_for(first['hypothesis_check'])
    wire=usage.get('wire_api')
    tools=([dict(type='function',function={k:v for k,v in tool.items() if k!='type'})]
           if wire=='chat_completions' else [tool])
    schema_sha=hashlib.sha256(json.dumps(tools,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
    choice=usage.get('requested_tool_choice') if wire=='chat_completions' else usage.get('request_tool_choice')
    if (wire not in ('chat_completions','responses') or choice!='function:'+NAME or usage.get('request_tool_count')!=1
            or usage.get('tool_call_count')!=1 or usage.get('request_schema_sha256')!=schema_sha):
        raise ValueError('字段修正回执与实际请求工具或结构不符')


def display(value):
    if not isinstance(value,dict) or value.get('version')!=VERSION:return None
    selected=value.get('selections')
    if not isinstance(selected,dict):return None
    return dict(version=VERSION,status=value.get('status') if value.get('status') in ('received','rejected','applied') else 'unknown',
                selections={k:v for k,v in selected.items() if isinstance(k,str) and re.fullmatch(r'H[1-4]',k) and isinstance(v,str) and v in POLICIES})
