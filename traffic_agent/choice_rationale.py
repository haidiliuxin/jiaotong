"""Keep experimental rationale in the model's explicitly untested hypothesis.

The selected observation, hypothesis, falsifier and alternative already carry
the choice rationale. A second free-form reason can accidentally promote that
hypothesis into a proven cause. Closing and handoff decisions retain reasons.
"""
from .choice_evidence import actions
from .planning_response import ResponseValidationError

VERSION='choice-rationale-v1'


def active(payload):
    version=payload.get('context',{}).get('choice_rationale_protocol')
    if version not in (None,VERSION):raise ValueError('未知的试验依据协议')
    if version and not payload['context'].get('choice_basis_protocol'):
        raise ValueError('试验依据协议需要结构化选点依据')
    return version==VERSION


def experiment(payload,action):
    return active(payload) and action in actions(payload)


def constrain(tool,payload):
    if not active(payload):return
    offered=actions(payload)
    if not offered:return
    tool['parameters']['properties']['reason']['description']='实验动作填空字符串；观测引用、待验证假设、否证条件和备选取舍只写selection_basis。结束或转组时填写研究取舍理由。'
    if set(offered)==set(payload['allowed_actions']):
        tool['parameters']['properties']['reason']['enum']=['']
    else:
        tool['parameters'].setdefault('allOf',[]).append({
            'if':dict(properties=dict(action=dict(enum=list(offered)))),
            'then':dict(properties=dict(reason=dict(enum=[''])))})


def validate(value,payload):
    if experiment(payload,value['action']) and value.get('reason')!='':
        raise ResponseValidationError('choice_rationale',
            '实验理由只写入selection_basis的待验证假设、否证条件与备选取舍；reason必须为空，不能另写已证明成因。')


def text(entry):
    if entry.get('status')!='accepted' or not experiment(entry.get('input',{}),entry.get('action')):return None
    basis=entry.get('selection_basis')
    if not isinstance(basis,dict):return None
    return '待检验假设（尚未证实）：'+basis['working_hypothesis']


def verify(entry):
    if entry.get('status')=='accepted':validate(entry,entry['input'])
    else:active(entry['input'])
