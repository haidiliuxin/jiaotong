"""Safe, bounded diagnostics for public tool arguments, never raw responses."""
import json
import math
import copy
from datetime import datetime

from .llm_settings import redact

VERSION = 'planning-response-check-v1'
CORRECTION_VERSION = 'final-review-correction-v1'
HANDOFF_CORRECTION_VERSION = 'study-review-correction-v1'
CHOICE_CORRECTION_VERSION = 'candidate-choice-correction-v1'
ARGUMENT_CORRECTION_VERSION = 'parking-arguments-correction-v1'


def request_usages(entry):
    """One logical decision may contain two billed, independently kept replies."""
    first = entry.get('review_correction', {}).get('first_attempt')
    return ([first.get('usage', {})] if first and first.get('requested') else []) + (
        [entry.get('usage', {})] if entry.get('requested') else [])


def _final_correction_eligible(entry):
    """Only a fully captured final review over completed native comparisons."""
    payload = entry['input']; context = payload['context']
    groups = context.get('programme_evidence', {'findings': []}).get('groups', [])
    proposal = entry.get('proposal_excerpt', {})
    capture = entry.get('proposal_capture', {})
    budget = payload['model_budget']
    return bool(entry.get('requested') is True and entry.get('status') == 'rejected'
        and entry.get('validation', {}).get('status') == 'failed'
        and entry.get('validation', {}).get('code') not in (None, 'transport', 'local_validation', 'tool_call', 'arguments_json')
        and entry.get('kind') in ('parking', 'route') and set(payload['allowed_actions']) == {'stop'}
        and context.get('programme_final_group') is True and groups
        and all(g.get('execution_complete') is True for g in groups)
        and context.get('reserved_model_requests', 0) == 0 and payload['evidence_refs']
        and proposal.get('action') == 'stop' and capture.get('excerpted') is False
        and not entry.get('review_correction') and budget['remaining'] >= 2)


def handoff_reserve(entry):
    """Reserve every remaining study, including its final review, before repair."""
    payload = entry['input']; context = payload['context']
    action = entry.get('proposal_excerpt', {}).get('action')
    if not isinstance(action, str):
        return None
    route = context.get('route_budget', {})
    choice = route.get('choices', {}).get(action, {})
    from .planning_budget import VERSION as ROUTING_VERSION
    required = choice.get('required_requests')
    reserved = context.get('reserved_model_requests', 0)
    if (route.get('version') != ROUTING_VERSION or choice.get('feasible') is not True
            or type(required) is not int or required < 1 or type(reserved) is not int or reserved < 0):
        return None
    return max(required, reserved)


def _handoff_correction_eligible(entry):
    """A complete local review may fix prose, never its selected next study."""
    from .study_handoff import actions
    payload = entry['input']; context = payload['context']
    action = entry.get('proposal_excerpt', {}).get('action')
    if not isinstance(action, str):
        return False
    reserve = handoff_reserve(entry)
    groups = context.get('programme_evidence', {}).get('groups', [])
    owner = [g for g in groups if g.get('job_id') == entry.get('job_id')]
    closes = ((entry.get('kind') == 'parking' and action in actions(context))
              or (entry.get('kind') == 'route' and context.get('require_hypothesis_review') is True))
    return bool(context.get('study_review_correction_protocol') == HANDOFF_CORRECTION_VERSION
        and context.get('programme_final_group') is False and len(owner) == 1
        and owner[0].get('execution_complete') is True and any(not g.get('execution_complete') for g in groups)
        and closes and action in payload['allowed_actions'] and action != 'stop'
        and entry.get('requested') is True and entry.get('status') == 'rejected'
        and entry.get('validation', {}).get('status') == 'failed'
        and entry.get('validation', {}).get('code') == 'measured_conclusion'
        and entry.get('conclusion_check', {}).get('issues')
        and entry.get('proposal_capture', {}).get('excerpted') is False
        and not entry.get('review_correction') and reserve is not None
        and payload['model_budget']['remaining'] >= reserve + 2)


def _choice_correction_eligible(entry):
    """Repair a missing citation once, retaining the chosen experiment and alternative."""
    from .choice_evidence import SUPPORTED_VERSIONS, actions
    payload=entry['input']; context=payload['context']
    proposal=entry.get('proposal_excerpt', {})
    basis=proposal.get('selection_basis')
    offered=actions(payload)
    action=proposal.get('action')
    alternative=basis.get('alternative_action') if isinstance(basis,dict) else None
    message=entry.get('validation',{}).get('message')
    missing=('缺少当前所选候选自己的新基准观测',
             '后续试验必须读取最近一次已完成配对反馈，不能只重复基准理由',
             '备选取舍必须引用该备选实际可见的观测')
    reserved=context.get('reserved_model_requests',0)
    return bool(context.get('choice_basis_protocol') in SUPPORTED_VERSIONS
        and entry.get('kind')=='parking' and entry.get('requested') is True
        and entry.get('status')=='rejected' and entry.get('validation',{}).get('code')=='choice_basis'
        and message in missing and isinstance(action,str) and action in offered
        and isinstance(basis,dict) and isinstance(alternative,str)
        and alternative in offered and alternative!=action
        and entry.get('proposal_capture',{}).get('excerpted') is False
        and not entry.get('review_correction') and type(reserved) is int and reserved>=0
        and payload['model_budget']['remaining']>=reserved+3)


def argument_reserve(entry):
    """Keep the remaining parking decisions/review and later studies funded."""
    context=entry['input']['context']
    required=context.get('completion_guard',{}).get('required_model_requests')
    reserved=context.get('reserved_model_requests')
    if type(required) is not int or required<0 or type(reserved) is not int or reserved<0:
        return None
    return max(1,required)-1+reserved


def _argument_correction_eligible(entry):
    """Reissue an undecodable parking decision; never infer its missing fields."""
    payload=entry['input']; context=payload['context']
    diagnostic=entry.get('arguments_diagnostic',{})
    length=diagnostic.get('length'); offset=diagnostic.get('offset')
    reserve=argument_reserve(entry)
    return bool(context.get('argument_correction_protocol')==ARGUMENT_CORRECTION_VERSION
        and entry.get('kind')==payload.get('phase')=='parking'
        and entry.get('requested') is True and entry.get('status')=='rejected'
        and entry.get('validation')==dict(version=VERSION,status='failed',code='arguments_json',
            message='工具参数不是有效的有限JSON对象')
        and diagnostic.get('version')=='tool-arguments-diagnostic-v1'
        and diagnostic.get('kind')=='json_syntax' and type(length) is int and length>0
        and type(offset) is int and 0<=offset<=length
        and set(diagnostic)=={'version','kind','length','offset'}
        and set(entry)<=set(('version','ordinal','time','kind','job_id','review_protocol','finding_reference_schema',
            'input','observation_digest','requested','status','instructions_sha256','input_wire','usage',
            'validation','arguments_diagnostic','action','finished','reason'))
        and entry.get('action')=='stop'
        and not any(k in entry for k in ('proposal_excerpt','proposal_capture','selected','proposed_decision'))
        and not entry.get('review_correction') and payload.get('evidence_refs')
        and payload['allowed_actions'] and all(a=='stop' or a.startswith('parking_') for a in payload['allowed_actions'])
        and reserve is not None and payload['model_budget']['remaining']>=reserve+2)


def correction_eligible(entry):
    return (_final_correction_eligible(entry) or _handoff_correction_eligible(entry)
            or _choice_correction_eligible(entry) or _argument_correction_eligible(entry))


def correction_protocol(entry):
    if _argument_correction_eligible(entry):return ARGUMENT_CORRECTION_VERSION
    if _choice_correction_eligible(entry):return CHOICE_CORRECTION_VERSION
    return HANDOFF_CORRECTION_VERSION if _handoff_correction_eligible(entry) else CORRECTION_VERSION


def validate_correction_action(value, correction):
    if correction and correction.get('version') == HANDOFF_CORRECTION_VERSION:
        require(isinstance(value, dict) and value.get('action') == correction['first_attempt']['proposal_excerpt']['action'],
                'correction_action', '本组复核修正只能保留原选定的下一研究组；不能借修正更换动作或追加实验')
    if correction and correction.get('version') == CHOICE_CORRECTION_VERSION:
        first=correction['first_attempt']['proposal_excerpt']
        require(isinstance(value,dict) and value.get('action')==first['action']
                and isinstance(value.get('selection_basis'),dict)
                and value['selection_basis'].get('alternative_action')==first['selection_basis']['alternative_action'],
                'correction_action', '观测引用修正只能保留原选动作和备选；不能借修正更换实验')


def correction_feedback(first, payload):
    from .research_evidence import measured_finding_ids
    context = payload['context']
    protocol = correction_protocol(dict(first, input=payload))
    if protocol==ARGUMENT_CORRECTION_VERSION:
        return dict(version=protocol,correction=1,maximum=1,validation=copy.deepcopy(first['validation']),
            arguments_diagnostic=copy.deepcopy(first['arguments_diagnostic']),
            original_failure_evidence='parser_diagnostic_only',
            reserved_after_correction=argument_reserve(dict(first,input=payload)),
            instruction='首次工具参数无法解析，未采纳任何动作。依据同一份观测、门槛、假设和动作菜单重新提交完整工具JSON对象；正确转义字符串并闭合数组与对象。动作和取舍仍由你依据证据选择，输出将经过全部原校验。此次修正与其他决策修正共享一次机会，计入总预算，再次失败即停止。')
    result = dict(version=protocol, correction=1, maximum=1,
        validation=copy.deepcopy(first['validation']), previous_proposal=copy.deepcopy(first['proposal_excerpt']),
        conclusion_issues=copy.deepcopy(first.get('conclusion_check', {}).get('issues', [])),
        allowed_finding_ids=dict(hypothesis_updates=measured_finding_ids(context.get('research_evidence', {'findings': []})),
            programme_updates=measured_finding_ids(context.get('programme_evidence', {'findings': []}))),
        instruction='依据校验反馈重新提交完整复核。观测、假设、门槛和动作菜单不变；不得追加实验或把未测缺口改成实测。判断由你依据证据作出。此次修正计入原总预算，再次失败即停止。')
    if protocol == HANDOFF_CORRECTION_VERSION:
        result.update(retained_action=first['proposal_excerpt']['action'],
            reserved_after_correction=handoff_reserve(dict(first, input=payload)),
            instruction='本组实测已齐，但交接复核的解释或代价确认未通过。依据具体错误和同一份观测重新提交完整字段，保留retained_action指定的下一组；不得改实测、假设、阈值或追加本组实验。后续组仍须各跑新基准。此次修正与最终复核共享一次机会，计入原总预算；再次失败即停止。')
    if protocol == CHOICE_CORRECTION_VERSION:
        proposal=first['proposal_excerpt']; basis=proposal['selection_basis']
        from .choice_evidence import observations as choice_observations,VERSION as CHOICE_VERSION,V3_VERSION as CHOICE_V3_VERSION,V4_VERSION as CHOICE_V4_VERSION,V5_VERSION as CHOICE_V5_VERSION,V6_VERSION as CHOICE_V6_VERSION
        facts=choice_observations(payload)
        required={proposal['action'],basis['alternative_action']}
        citation_instruction=(
            'observed_ids须引用所选动作自己的新基准；alternative_observed_id须单独引用备选自己的新基准或同区域实测反馈。'
            '若有recent_trial，顶层evidence_refs须引用其对应的实际场景ref，无须在observed_ids重复。'
            if facts['version'] in (CHOICE_V4_VERSION,CHOICE_V5_VERSION,CHOICE_V6_VERSION,CHOICE_VERSION) else
            'observed_ids须引用所选动作自己的新基准；alternative_observed_id须单独引用备选自己的新基准或同区域实测反馈，'
            '若有recent_trial还须把最新配对反馈放在observed_ids。'
            if facts['version']==CHOICE_V3_VERSION else
            'observed_ids须引用所选及备选各自可见的新基准，若有recent_trial还须引用该实际反馈。')
        result.update(retained_action=proposal['action'],retained_alternative_action=basis['alternative_action'],
            relevant_observation_ids=[s['id'] for s in facts['sources'] if required.intersection(s['actions'])],
            recent_trial=facts['recent_trial'],
            instruction='本轮动作尚未执行；仅因选点观测引用缺漏而修正一次。保留retained_action和retained_alternative_action，完整重交工具字段；'+citation_instruction+'不得改实测、门槛或选定实验。修正计入总预算；再次失败即停止。')
    return result


def correction_payload(payload, first):
    result = copy.deepcopy(payload)
    budget = result['model_budget']; usage = first.get('usage', {})
    budget.update(used=budget['used']+1, remaining=max(0, budget['remaining']-1),
        known_tokens=budget['known_tokens']+(usage.get('tokens') or 0),
        unknown_usage=budget['unknown_usage']+int(usage.get('tokens') is None))
    result['review_feedback'] = correction_feedback(first, payload)
    return result


def verify_correction(entry):
    """Replay the rejection and prove both requests used the same frozen facts."""
    correction = entry.get('review_correction')
    if correction is None:
        return
    from .feedback_planner import digest, decision_tool, apply_proposal
    if correction.get('version') not in (CORRECTION_VERSION, HANDOFF_CORRECTION_VERSION, CHOICE_CORRECTION_VERSION, ARGUMENT_CORRECTION_VERSION) or correction.get('requested') is not True:
        raise ValueError('复核修正协议或请求记录无效')
    first = correction.get('first_attempt', {})
    original = dict(first, input=copy.deepcopy(entry['input']))
    if not correction_eligible(original) or correction.get('version') != correction_protocol(original):
        raise ValueError('复核修正超出权限、预算或缺少完整首轮失败')
    for key in ('version', 'ordinal', 'time', 'kind', 'job_id', 'review_protocol', 'finding_reference_schema', 'observation_digest'):
        if first.get(key) != entry.get(key):
            raise ValueError('首轮复核身份或观测摘要被改变')
    if (digest(original['input']) != original['observation_digest']
            or correction.get('input') != correction_payload(entry['input'], first)
            or correction.get('observation_digest') != digest(correction['input'])):
        raise ValueError('修正请求改变了冻结观测、反馈或调用记账')
    if (not entry.get('requested') or entry.get('status') not in ('accepted', 'rejected')
            or not datetime.fromisoformat(first['time']) <= datetime.fromisoformat(first['finished'])
            <= datetime.fromisoformat(correction['time']) <= datetime.fromisoformat(entry['finished'])):
        raise ValueError('最终复核两次请求的状态或时间顺序无效')
    tool = decision_tool(entry['input'])
    if correction['version']!=ARGUMENT_CORRECTION_VERSION:
        proposal = copy.deepcopy(first['proposal_excerpt'])
        from .action_execution import capture_schema
        _, capture = capture_proposal(proposal, capture_schema(entry['input'],tool['parameters']))
        if capture != first['proposal_capture']:
            raise ValueError('首轮公开提议的完整性记录不符')
        probe = dict(input=entry['input'])
        try:
            apply_proposal(probe, proposal, tool)
        except ResponseValidationError as exc:
            if (first['validation'] != dict(version=VERSION, status='failed', code=exc.code, message=str(exc))
                    or first.get('conclusion_check') != probe.get('conclusion_check')):
                raise ValueError('首轮失败原因与保留的公开提议不符') from None
        else:
            raise ValueError('首轮提议没有对应的可修正校验错误')
    # Undecodable arguments are deliberately not retained. That first attempt
    # is audited as parser diagnostics only, not as a replayable proposal.
    if entry['status']=='accepted':
        final={k:copy.deepcopy(entry[k]) for k in tool['parameters']['properties'] if k in entry}
        validate_correction_action(final, correction)
        checked=dict(input=copy.deepcopy(entry['input']))
        apply_proposal(checked,final,tool)
        if any(entry.get(k)!=checked.get(k) for k in ('validation','selected','conclusion_check')):
            raise ValueError('修正后复核的校验结果与实际提议不符')


class ResponseValidationError(ValueError):
    def __init__(self, code, message, diagnostic=None):
        super().__init__(message)
        self.code = code
        self.diagnostic = diagnostic


def require(condition, code, message):
    if not condition:
        raise ResponseValidationError(code, message)


def decode_arguments(raw):
    def non_finite(_):
        raise ValueError('non-finite')
    try:
        return json.loads(raw, parse_constant=non_finite)
    except json.JSONDecodeError as exc:
        diagnostic=dict(version='tool-arguments-diagnostic-v1',kind='json_syntax',
                        length=len(raw),offset=exc.pos)
    except ValueError:
        diagnostic=dict(version='tool-arguments-diagnostic-v1',kind='non_finite',
                        length=len(raw) if isinstance(raw,str) else None)
    except TypeError:
        diagnostic=dict(version='tool-arguments-diagnostic-v1',kind='not_text',length=None)
    raise ResponseValidationError('arguments_json', '工具参数不是有效的有限JSON对象',diagnostic) from None


def capture_proposal(value, schema):
    """Only schema-named public fields survive; diagnostics contain no values.

    Invalid syntax is never captured here. Unknown fields (including hidden
    reasoning), excessive nesting/items/text and credential-like strings do not
    enter the snapshot. A separate capture record discloses any omissions.
    """
    report = dict(version=VERSION, omitted_fields=0, omitted_items=0,
                  shortened_strings=0, redacted_strings=0, invalid_values=0)

    def walk(item, spec, depth=0):
        if depth > 6:
            report['invalid_values'] += 1
            return None
        if isinstance(item, dict):
            allowed = spec.get('properties', {})
            if 'anyOf' in spec:
                # Only declared public properties across typed object branches.
                # This captures an invalid judgment for bounded repair without
                # accepting it or dropping the rest of the original response.
                allowed = {k:v for branch in spec['anyOf'] for k,v in branch.get('properties',{}).items()}
            report['omitted_fields'] += len(set(item)-set(allowed))
            return {key: walk(item[key], child, depth+1)
                    for key, child in allowed.items() if key in item}
        if isinstance(item, list):
            limit = min(20, max(1, spec.get('maxItems', 20)))
            report['omitted_items'] += max(0, len(item)-limit)
            return [walk(v, spec.get('items', {}), depth+1) for v in item[:limit]]
        if isinstance(item, str):
            safe = redact(item)
            report['redacted_strings'] += safe != item
            report['shortened_strings'] += len(safe) > 2048
            return safe[:2048]
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        report['invalid_values'] += 1
        return None

    captured = walk(value, schema)
    report['excerpted'] = any(v for k, v in report.items() if k != 'version')
    return captured, report
