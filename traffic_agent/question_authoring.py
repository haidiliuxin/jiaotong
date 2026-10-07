"""Author a measurable question once, with named references to model studies.

This is an explicit new wire form. Legacy prose is never converted into it or
repaired by rendering: it keeps the existing verbatim validation path.
"""
import copy
import re

from . import hypothesis_contract as hc
from .llm_client import ModelError

VERSION = 'named-question-authoring-v1'
KEY_PATTERN = r'^[a-z][a-z0-9_]{0,39}$'
DEFINITION_PROPERTIES = dict(
    test=dict(type='string',enum=[x for x in hc.TESTS if x!='unavailable']),
    study_keys=dict(type='array',items=dict(type='string',pattern=KEY_PATTERN),minItems=1,maxItems=5,uniqueItems=True,
        description='引用本次experiments的key；相对仅停车或组合增益须引用含交通组织的研究，不能引用仅停车组。'),
    policies=dict(type='string',enum=list(hc.POLICIES),description='被检验措施集合；合法搭配见hypothesis_tests[test].allowed_policies。'),
    quantifier=dict(type='string',enum=['any','all'],description='每个指定组至少一个(any)或所有(all)已试措施；同一措施须覆盖全部约定需求。'))
DEFINITION_SCHEMA = dict(type='object',properties=DEFINITION_PROPERTIES,required=list(DEFINITION_PROPERTIES),
                         additionalProperties=False,allOf=hc.policy_constraints())


def wire_schemas(experiment,verbatim):
    study=copy.deepcopy(experiment)
    study['properties']=dict(key=dict(type='string',pattern=KEY_PATTERN,
        description='为本组起唯一且有意义的英文名，供definition.study_keys引用；使用结构化问题时每组必填。'),**study['properties'])
    # Optional only for the still-explicit verbatim form used by old clients.
    question=dict(anyOf=[dict(type='object',properties=dict(definition=DEFINITION_SCHEMA),required=['definition'],
        additionalProperties=False,description='可测问题使用此形式。模型只选择一次指标、研究组、措施集合和量词；展示问题按该定义生成。'),
        dict(copy.deepcopy(verbatim),description='保留自由提问或不可测问题的原文；hypothesis与evidence_needed均逐字检查，不能先提交冲突文字再转为结构化问题。')])
    return study,question


def study_keys(experiments,required=False):
    keys={}
    for i,study in enumerate(experiments,1):
        key=study.get('key') if isinstance(study,dict) else None
        if key is None and not required:continue
        if not isinstance(key,str) or not re.fullmatch(KEY_PATTERN,key) or key in keys:
            raise ModelError('结构化问题需要每个研究组提供不同的有效key；未自动猜测组别')
        keys[key]=i
    return keys


def validate_definition(value):
    if not isinstance(value,dict) or set(value)!=set(DEFINITION_PROPERTIES):
        raise ModelError('结构化问题定义字段不完整或混入自由文字；未改写问题')
    keys=value['study_keys']
    if (not isinstance(keys,list) or not 1<=len(keys)<=5 or any(not isinstance(k,str) or not re.fullmatch(KEY_PATTERN,k) for k in keys)
            or len(set(keys))!=len(keys) or value['test']=='unavailable'):
        raise ModelError('结构化问题的研究引用无效；不可测问题须保留原文与证据缺口')
    hc.validate(dict(test=value['test'],studies=list(range(1,len(keys)+1)),policies=value['policies'],quantifier=value['quantifier']))


def render(definition,keys):
    validate_definition(definition)
    if any(k not in keys for k in definition['study_keys']):
        raise ModelError('结构化问题引用了未提交的研究key；未按名称或位置猜测替代')
    selected={k:keys[k] for k in definition['study_keys']}
    criterion=dict(test=definition['test'],studies=list(selected.values()),
        policies=definition['policies'],quantifier=definition['quantifier'])
    labels=dict(all='本组可检验的全部治理类型',parking='停车措施',traffic_control='交通组织单项',
        combined='组合措施',control_and_combined='交通组织及组合')
    family=labels[criterion['policies']]
    if criterion['policies']=='all' and criterion['test'] in hc.TEST_EXCLUSIONS:family=labels['control_and_combined']
    quantifier='至少一个' if criterion['quantifier']=='any' else '所有'
    predicate=hc.QUESTION_DEFINITIONS[criterion['test']]['predicate']
    scope='、'.join(f'第{i}组' for i in selected.values())
    return dict(hypothesis=f'{scope}分别检验：每组{quantifier}已试措施（{family}）在全部约定需求下{predicate}。',
        evidence_needed='按所选判据配对本组同需求原生观测；所需基准或对照缺失时保留未知。',criterion=criterion,
        authoring=dict(version=VERSION,definition=copy.deepcopy(definition),study_keys=selected))


def normalize(experiments,assessment):
    if (not isinstance(experiments,list) or not isinstance(assessment,list)
            or any(not isinstance(s,dict) for s in experiments)):
        raise ModelError('研究组或问题数组结构无效；未生成问题')
    typed=any(isinstance(h,dict) and 'definition' in h for h in assessment)
    keys=study_keys(experiments,required=typed)
    result=[]
    for h in assessment:
        if isinstance(h,dict) and set(h)=={'definition'}:result.append(render(h['definition'],keys))
        elif isinstance(h,dict) and set(h)=={'hypothesis','evidence_needed','criterion'}:result.append(copy.deepcopy(h))
        else:raise ModelError('问题须明确选择结构化定义或保留原文；不能混合字段或伪造生成来源')
    return [{k:v for k,v in s.items() if k!='key'} for s in experiments],result


def verify_row(row):
    authoring=row.get('authoring')
    if (not isinstance(authoring,dict) or set(authoring)!={'version','definition','study_keys'} or authoring['version']!=VERSION
            or not isinstance(authoring['study_keys'],dict)
            or any(type(i) is not int or not 1<=i<=5 for i in authoring['study_keys'].values())
            or len(set(authoring['study_keys'].values()))!=len(authoring['study_keys'])):
        raise ModelError('结构化问题缺少可核对的原始定义与研究引用')
    expected=render(authoring['definition'],authoring['study_keys'])
    if any(row.get(k)!=v for k,v in expected.items()):
        raise ModelError('结构化问题的原始定义、显示文本或冻结判据被改写')


def verify_study_links(proposal):
    """Rebind the saved definition to its original named study declarations."""
    if (not isinstance(proposal,dict) or not isinstance(proposal.get('studies'),list)
            or not isinstance(proposal.get('assessment'),list) or any(not isinstance(h,dict) for h in proposal['assessment'])):
        raise ModelError('缺少初始问题与原始研究安排')
    keys=study_keys(proposal['studies'],required=any('authoring' in h for h in proposal['assessment']))
    for row in proposal['assessment']:
        if 'authoring' not in row:continue
        verify_row(row)
        expected=render(row['authoring']['definition'],keys)
        if row!=expected:raise ModelError('结构化问题的研究key与原始研究安排不一致')
