"""A single explicit planning decision; an empty plan is not a fourth outcome."""
from .llm_client import function, ModelError, PlanShapeError

VERSION = 'traffic-plan-decision-v5'


def object_schema(properties):
    return dict(type='object', properties=properties, required=list(properties), additionalProperties=False)


def tool_for(experiment, assessment, alternative, unsupported, clarification_needs=None, clarification_quotes=None,
             allowed_unsupported=None):
    if clarification_needs is None:clarification_needs=['unresolved_location','turn_target']
    if (not isinstance(clarification_needs,list) or len(set(clarification_needs))!=len(clarification_needs)
            or any(n not in ('unresolved_location','turn_target') for n in clarification_needs)):
        raise ValueError('规划追问权限无效')
    question = object_schema(dict(
        need=dict(type='string', enum=clarification_needs),
        source_quote=dict(type='string', description='用户原文中确实无法定位的位置/方位，或明确要求禁转的短句；不能引用数量上限或编造原文'),
        question=dict(type='string', description='围绕该引用提出一个具体定位问题；停车/单向自动选点不缺道路或节点')))
    if clarification_quotes is not None and clarification_needs:
        if (not isinstance(clarification_quotes,dict) or set(clarification_quotes)!=set(clarification_needs) or any(not isinstance(q,list) or not q
                or any(not isinstance(s,str) or not 2<=len(s)<=100 for s in q) for q in clarification_quotes.values())):
            raise ValueError('定位追问缺少可核对的原文片段')
        question=dict(anyOf=[object_schema(dict(
            need=dict(type='string',enum=[need]),
            source_quote=dict(type='string',enum=clarification_quotes[need],description='只能引用实际未解析的原文片段'),
            question=dict(type='string',description='仅询问此片段的未解析位置，不要求重填已知道路或本可自动选择的治理方向')))
            for need in clarification_needs])
    allowed_unsupported=list(unsupported) if allowed_unsupported is None else allowed_unsupported
    if not isinstance(allowed_unsupported,list) or any(x not in unsupported for x in allowed_unsupported):
        raise ValueError('未支持能力权限无效')
    decisions = [
        object_schema(dict(
            intent=dict(type='string', enum=['execute']),
            experiments=dict(type='array', items=experiment, minItems=1, maxItems=5),
            assessment=dict(type='array', items=assessment, minItems=1, maxItems=4),
            alternatives=dict(type='array', items=alternative, minItems=1, maxItems=4))),
        object_schema(dict(
            intent=dict(type='string', enum=['needs_input']),
            questions=dict(type='array', items=question, minItems=1, maxItems=4,
                           description='仅限可核对的定位缺口；预算由非空实验设计交给程序计算，不自行猜测缺口'))),
        object_schema(dict(
            intent=dict(type='string', enum=['unsupported']),
            capabilities=dict(type='array', items=dict(type='string', enum=allowed_unsupported), minItems=1, maxItems=4,
                              description='用户本次必须执行且未接入的底层能力；暂缓的备选不属于能力缺口'))),
    ]
    if not clarification_needs:decisions=[d for d in decisions if d['properties']['intent']['enum']!=['needs_input']]
    if not allowed_unsupported:decisions=[d for d in decisions if d['properties']['intent']['enum']!=['unsupported']]
    return function('compose_traffic_plan', '从本次开放分支提交明确决策；策略与实验设计由模型提出，不预设治理结果。', dict(
        goal=dict(type='string', description='40字以内的中文研究目标'),
        reasoning=dict(type='string', description='简洁说明规划依据、优先次序或为何当前无法执行，不编造观测'),
        decision=dict(anyOf=decisions)))


def canonical_proposal(value):
    """Decode only the current wire contract, then use the established executor.

    Archived canonical plans are read elsewhere; accepting a legacy wire reply
    here would make an ignored/stale schema look like a current successful call.
    """
    if not isinstance(value, dict) or set(value) != {'goal', 'reasoning', 'decision'}:
        raise PlanShapeError('模型未返回当前规划决策结构；未执行仿真')
    decision=value['decision']
    if not isinstance(decision, dict):
        raise PlanShapeError('模型规划缺少明确决策；未执行仿真')
    intent=decision.get('intent')
    fields={'execute': {'intent','assessment','alternatives','experiments'},
            'needs_input': {'intent','questions'}, 'unsupported': {'intent','capabilities'}}
    if not isinstance(intent,str) or intent not in fields or set(decision)!=fields[intent]:
        raise PlanShapeError('模型混用了执行与阻塞字段，或未选择合法规划决策；未执行仿真')
    for key in fields[intent]-{'intent'}:
        items=decision[key]
        if not isinstance(items,list) or not items:
            if key=='experiments':
                raise ModelError('模型选择执行但未提出实验；本次规划无效，未执行仿真、未自动重试')
            raise ModelError('模型决策缺少非空的依据、对照或具体阻塞说明；未执行仿真')
    questions=decision.get('questions',[])
    for item in questions:
        if (not isinstance(item,dict) or set(item)!={'need','source_quote','question'}
                or item['need'] not in ('unresolved_location','turn_target')
                or any(not isinstance(v,str) or not v.strip() or len(v)>300 for v in item.values())):
            raise ModelError('模型追问缺少可核对的定位引用；未要求用户补充、未执行仿真')
    from .question_authoring import normalize
    experiments,assessment=normalize(decision.get('experiments',[]),decision.get('assessment',[]))
    return dict(goal=value['goal'], reasoning=value['reasoning'],
                assessment=assessment, alternatives=decision.get('alternatives',[]),
                experiments=experiments, missing_information=[x['question'] for x in questions],
                unsupported_requests=decision.get('capabilities',[]))
