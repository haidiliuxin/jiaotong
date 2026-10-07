"""One bounded correction of a prerequisite, draft budget or hypothesis criterion.

The full assistant output stays in memory solely for its tool-result continuation.
Only safe attempt records and the validator's public feedback are persisted.
"""
import copy
import json
import re
import uuid
from datetime import datetime

from .llm_client import ModelError, PlanShapeError
from .planning_attempt import PlanningAttempt, safe_usage
from .planning_scope import PrerequisiteError,decision_permissions
from .feedback_planner import REQUEST_LIMIT, LEGACY_REQUEST_LIMIT, digest
from . import initial_hypothesis_review as hypothesis_review
from . import hypothesis_policy_patch as policy_patch

LEGACY_VERSION='initial-plan-review-v1'
BUDGET_VERSION='initial-plan-review-v2'
DIRECT_CRITERION_VERSION='initial-plan-review-v3'
POLICY_VERSION='initial-plan-review-v4'
V5_VERSION='initial-plan-review-v5'
V6_VERSION='initial-plan-review-v6'
VERSION='initial-plan-review-v7'


def can_continue(draft):
    calls=(draft.reply or {}).get('calls',[])
    return (len(calls)==1 and calls[0].get('name')=='compose_traffic_plan'
            and isinstance(calls[0].get('call_id'),str) and calls[0]['call_id']
            and isinstance(draft.reply.get('output'),list) and draft.reply['output'])


def correction_feedback(draft,code,explanation,budget_check=None,hypothesis_check=None):
    original_input=json.loads(draft.history[0]['content'])
    scope=original_input['execution_scope']
    model_limit=(hypothesis_check or {}).get('budget_check',{}).get('model',{}).get('limit') or (budget_check or {}).get('model',{}).get('limit') or REQUEST_LIMIT
    value=dict(status='rejected',code=code,explanation=explanation,native_started=False,
               corrections_remaining=0,request_unchanged=True,model_requests_used=1,
               model_request_limit=model_limit,execution_scope=scope,
               next_step=f'依据原始请求、当前执行范围和此校验结果重新选择规划决策。不要改写用户的范围、预算或必须完成的比较。修正请求也计入{model_limit}次总额；实验和治理收益均未产生。')
    if budget_check is not None:value['budget_check']=copy.deepcopy(budget_check)
    if code=='invalid_prerequisite':
        required_design=original_input.get('study_units',{}).get('required_design')
        if required_design:
            value['required_study_design']=copy.deepcopy(required_design)
            value['next_step']=(
                '原问题明确要求多个独立停车候选。按required_study_design另列strategies=[parking]的完整停车研究组，'
                '同组新基准后逐项实测；组合组四格不计入停车候选数量。保持原问题、其他必需研究及总预算，'
                '具体位置、方向、强度和组顺序仍由你决定；修正仍计入原模型请求上限。')
    if hypothesis_check is not None:
        value['hypothesis_check']=copy.deepcopy(hypothesis_check)
        value['next_step']=policy_patch.for_check(hypothesis_check).INSTRUCTIONS
    if code=='invalid_plan_shape':
        value['next_step']='上一工具参数结构不合规。依据原始请求及同一工具JSON schema重新提交一次完整compose_traffic_plan。保持用户道路、需求条件、预算、必须比较项及权限；不把格式错误当成交通信息缺失；未执行仿真。本次与其他初始纠错共用一次机会并计入总请求预算。'
    return value


class DraftClient:
    def __init__(self, client, previous=None, feedback=None):
        self.client,self.previous,self.feedback=client,previous,feedback
        self.reply=None
        self.history=None
        self.policy_patch_context=None

    def request(self,instructions,inputs,tools,choice):
        history=copy.deepcopy(inputs if self.previous is None else self.previous['history'])
        if self.previous is not None:
            calls=self.previous['reply'].get('calls',[])
            outputs=self.previous['reply'].get('output')
            if (len(calls)!=1 or calls[0].get('name')!='compose_traffic_plan'
                    or not isinstance(calls[0].get('call_id'),str) or not isinstance(outputs,list) or not outputs):
                raise ModelError('缺少完整工具调用上下文，不能请求规划修正；未追加模型调用',request_sent=False)
            wire=self.client.config['wire_api']
            history.extend(copy.deepcopy(outputs))
            if wire=='chat_completions':
                history.append(dict(role='tool',tool_call_id=calls[0]['call_id'],content=json.dumps(self.feedback,ensure_ascii=False)))
            elif wire=='responses':
                history.append(dict(type='function_call_output',call_id=calls[0]['call_id'],output=json.dumps(self.feedback,ensure_ascii=False)))
            else:
                raise ModelError('当前接口没有规划修正协议；未追加模型调用',request_sent=False)
            if self.feedback.get('code')==hypothesis_review.CODE:
                patch=policy_patch.for_check(self.feedback['hypothesis_check'])
                self.policy_patch_context=dict(version=patch.VERSION,check=self.feedback['hypothesis_check'],
                    source_attempt_id=self.previous['attempt_id'],proposal=json.loads(calls[0]['arguments']))
                instructions=patch.INSTRUCTIONS
                tools=[patch.tool_for(self.feedback['hypothesis_check'])]
                choice=dict(type='function',name=patch.NAME)
        self.history=history
        self.reply=self.client.request(instructions,history,tools,choice)
        return self.reply


def reviewed_compose(text,source,client,root,compose):
    """No network retry; only one typed validation error may get a new decision."""
    attempts=[];previous=None;feedback=None;usages=[];catalog=None;permissions=None
    session='planning_'+uuid.uuid4().hex
    for ordinal in (1,2):
        draft=DraftClient(client,previous,feedback)
        attempt=PlanningAttempt(draft,root,text)
        attempt.record.update(session_id=session,ordinal=ordinal,phase='initial' if ordinal==1 else 'validation_correction')
        if attempts:
            attempt.record.update(corrects=attempts[-1]['id'],validation_feedback=feedback)
        try:
            if catalog is None:
                from . import natural_plan
                config=natural_plan.limits(text)
                catalog=natural_plan.context_for(source,text)
                permissions=decision_permissions(text,catalog,config['explicit'])
            attempt.record['decision_permissions']=permissions
            result=compose(text,source,attempt,context=catalog,**({'prior_usage':usages} if usages else {}))
            check=result.get('budget_check')
            if check is not None:attempt.record['budget_check']=copy.deepcopy(check)
            if feedback and feedback['code']=='draft_over_budget' and check is not None:
                from .planning_budget import verify_budget_revision
                verify_budget_revision(feedback['budget_check'],check)
            if feedback and feedback['code']==hypothesis_review.CODE:
                policy_patch.for_check(feedback['hypothesis_check']).verify_receipt(attempts[0],attempt.record)
                hypothesis_review.verify_revision(feedback['hypothesis_check'],attempts[0]['proposal'],attempt.record['proposal'],result)
        except Exception as exc:
            if isinstance(exc,PlanShapeError):attempt.record['validation_code']='invalid_plan_shape'
            if isinstance(exc,PrerequisiteError):attempt.record['validation_code']='invalid_prerequisite'
            if isinstance(exc,hypothesis_review.HypothesisDraftError):
                attempt.record.update(validation_code=hypothesis_review.CODE,hypothesis_check=copy.deepcopy(exc.check),
                                      budget_check=copy.deepcopy(exc.check['budget_check']))
            attempt.finish('validation_failed',exc)
            attempts.append(copy.deepcopy(attempt.record))
            criterion_correction=isinstance(exc,hypothesis_review.HypothesisDraftError) and exc.check['correction_feasible']
            if not (isinstance(exc,(PrerequisiteError,PlanShapeError)) or criterion_correction) or ordinal==2 or not attempt.record['requested'] or not draft.reply:
                raise
            if not can_continue(draft):
                raise  # Missing transport history cannot be invented from a saved summary.
            usages.append(safe_usage(attempt.record.get('usage')) or {})
            previous=dict(reply=draft.reply,history=draft.history,attempt_id=attempt.record['id'])
            feedback=(correction_feedback(draft,hypothesis_review.CODE,attempt.record['error'],hypothesis_check=exc.check)
                      if criterion_correction else correction_feedback(draft,'invalid_plan_shape' if isinstance(exc,PlanShapeError) else 'invalid_prerequisite',attempt.record['error']))
            continue
        if check and not check['fits']:attempt.record['validation_code']='draft_over_budget'
        attempt.finish(result['status'],explanation='；'.join(result.get('missing') or [result['reason']]))
        attempts.append(copy.deepcopy(attempt.record))
        if check and check['correction_feasible'] and ordinal==1 and can_continue(draft):
            usages.append(safe_usage(attempt.record.get('usage')) or {})
            previous=dict(reply=draft.reply,history=draft.history,attempt_id=attempt.record['id'])
            feedback=correction_feedback(draft,'draft_over_budget',attempt.record['explanation'],check)
            continue
        # compose() records transport metadata, while this coordinator also links
        # the earlier rejected draft and its exact public validation feedback.
        result.update(planning_attempt_id=attempt.record['id'],initial_review_protocol=VERSION,
                      initial_planning_attempts=attempts,
                      initial_model_usages=[a.get('usage') or {} for a in attempts if a['requested']])
        return result
    raise AssertionError('bounded initial planning loop exhausted')


def evidence(result):
    value={key:result.get(key) for key in ('initial_review_protocol','initial_planning_attempts','initial_model_usages')}
    if result.get('knowledge_retrieval_protocol'):
        value['knowledge_retrieval_protocol']=result['knowledge_retrieval_protocol']
        value['knowledge_retrieval']=result.get('knowledge_retrieval')
    if result.get('decision_permissions') is not None:value['decision_permissions']=result['decision_permissions']
    if result.get('hypothesis_contract_protocol'):
        value.update(hypothesis_contract_protocol=result['hypothesis_contract_protocol'],assessment=result['assessment'])
    if result.get('choice_basis_protocol'):value['choice_basis_protocol']=result['choice_basis_protocol']
    if result.get('question_wording_protocol'):value['question_wording_protocol']=result['question_wording_protocol']
    if result.get('question_authoring_protocol'):value['question_authoring_protocol']=result['question_authoring_protocol']
    if result.get('version') in ('natural-strategy-plan-v6','natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        value['required_criteria']=result.get('required_criteria')
    if result.get('version') in ('natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        value['research_depth']=result.get('research_depth')
    return value


def verify_initial(record,plan):
    """Check new initial-request evidence; keep legacy single-request accounting."""
    if not record.get('initial_review_protocol'):
        if plan.get('initial_planning_digest'):raise ValueError('冻结的初始规划记录缺失')
        return 1
    if record['initial_review_protocol'] not in (LEGACY_VERSION,BUDGET_VERSION,DIRECT_CRITERION_VERSION,POLICY_VERSION,V5_VERSION,V6_VERSION,VERSION) or plan.get('initial_planning_digest')!=digest(evidence(record)):
        raise ValueError('初始规划记账与事前冻结摘要不符')
    if record.get('knowledge_retrieval_protocol'):
        from .knowledge_retrieval import SUPPORTED_VERSIONS,verify as verify_knowledge
        protocol=record['knowledge_retrieval_protocol']
        if (protocol not in SUPPORTED_VERSIONS or
                plan.get('knowledge_retrieval_protocol',protocol)!=protocol or
                (record.get('knowledge_retrieval') or {}).get('version')!=protocol):
            raise ValueError('初始知识检索协议与冻结计划不符')
        verify_knowledge(record.get('knowledge_retrieval'),record['request'],'initial')
    if record.get('version') in ('natural-strategy-plan-v10','natural-strategy-plan-v11'):
        from .question_authoring import VERSION as AUTHORING_VERSION,verify_study_links
        if record.get('question_authoring_protocol')!=AUTHORING_VERSION:
            raise ValueError('初始问题生成来源协议缺失或被改写')
        try:verify_study_links(record['initial_planning_attempts'][-1]['proposal'])
        except (IndexError,KeyError,TypeError,ModelError):
            raise ValueError('初始问题的具名研究引用或原始定义无法重建') from None
    if record.get('version') in ('natural-strategy-plan-v6','natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        from . import study_objectives
        parser_version=(study_objectives.VERSION if record['version']=='natural-strategy-plan-v11'
                        else study_objectives.LEGACY_VERSION)
        if record.get('required_criteria')!=study_objectives.required(record['request'],version=parser_version):
            raise ValueError('原问题的分别检验要求未按原文冻结')
        try:
            study_objectives.verify(record['required_criteria'],record['assessment'])
        except PrerequisiteError:
            raise ValueError('初始评估漏掉原问题明确要求的独立判据') from None
    if record.get('version') in ('natural-strategy-plan-v7','natural-strategy-plan-v8','natural-strategy-plan-v9','natural-strategy-plan-v10','natural-strategy-plan-v11'):
        from . import study_depth
        depth=record.get('research_depth')
        if (not isinstance(depth,dict) or depth.get('version') not in study_depth.SUPPORTED_VERSIONS
                or depth!=study_depth.from_request(record['request'],depth['version'])):
            raise ValueError('原问题的研究深度未按原文冻结')
        try:
            study_depth.verify_plan(record['research_depth'],[e['task'] for e in record['experiments']])
        except PrerequisiteError:
            raise ValueError('初始研究遗漏原问题明确要求的方案或需求对照') from None
    attempts=record.get('initial_planning_attempts')
    if not isinstance(attempts,list) or not 1<=len(attempts)<=2:
        raise ValueError('初始规划请求数缺失或超过修正上限')
    if any(not isinstance(a,dict) or not isinstance(a.get('id'),str) or
           not re.fullmatch(r'preview_[a-f0-9]{32}',a['id']) for a in attempts):
        raise ValueError('初始请求记录或编号无效')
    if len({a['id'] for a in attempts})!=len(attempts):raise ValueError('初始请求编号重复')
    previous_finished=None
    for i,a in enumerate(attempts,1):
        if (a.get('ordinal')!=i or a.get('requested') is not True or a.get('native_started') is not False
                or a.get('phase')!=('initial' if i==1 else 'validation_correction') or a.get('request')!=record['request']):
            raise ValueError('初始规划阶段、原始请求或计费标志不符')
        if record.get('decision_permissions') is not None and a.get('decision_permissions')!=record['decision_permissions']:
            raise ValueError('初始规划工具权限与事前记录不同')
        if not isinstance(a.get('session_id'),str) or not re.fullmatch(r'planning_[a-f0-9]{32}',a['session_id']):
            raise ValueError('初始规划会话编号无效')
        try:
            started,finished,created=[datetime.fromisoformat(v) for v in (a['started'],a['finished'],plan['created'])]
            if not started<=finished<=created or previous_finished is not None and started<previous_finished:
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise ValueError('初始规划记录次序错误或未在研究开始前完成') from None
        previous_finished=finished
    if attempts[-1].get('status')!='ready':raise ValueError('最后一次初始规划未通过执行校验')
    if record.get('choice_basis_protocol') or plan.get('choice_basis_protocol'):
        from .choice_evidence import SUPPORTED_VERSIONS
        if (record.get('choice_basis_protocol') not in SUPPORTED_VERSIONS
                or ('studies' in plan and plan.get('choice_basis_protocol')!=record.get('choice_basis_protocol'))):
            raise ValueError('选点观测协议未在开始前冻结')
    if record.get('hypothesis_contract_protocol') or any('evaluation' in h for h in plan.get('hypotheses',[])):
        from .hypothesis_contract import VERSION as CONTRACT_VERSION,bind,hypotheses
        if record.get('hypothesis_contract_protocol')!=CONTRACT_VERSION:
            raise ValueError('事前假设判据协议缺失或被移除')
        try:
            raw=attempts[-1]['proposal']['assessment']
            bound=bind(raw,record['experiments'],record['compilation'],wording_protocol=record.get('question_wording_protocol'))
            if bound!=record['assessment'] or ('hypotheses' in plan and hypotheses(bound)!=plan['hypotheses']):
                raise ValueError('执行或复核改写了模型初始假设的判据与范围')
        except (KeyError,TypeError,ModelError):
            raise ValueError('初始判据未保留、无效或与执行研究不符') from None
    if len(attempts)==2:
        first,last=attempts
        feedback=last.get('validation_feedback',{})
        budget_correction=record['initial_review_protocol'] in (BUDGET_VERSION,DIRECT_CRITERION_VERSION,POLICY_VERSION,V5_VERSION,V6_VERSION,VERSION) and first.get('validation_code')=='draft_over_budget'
        criterion_correction=record['initial_review_protocol'] in (DIRECT_CRITERION_VERSION,POLICY_VERSION,V5_VERSION,V6_VERSION,VERSION) and first.get('validation_code')==hypothesis_review.CODE
        shape_correction=record['initial_review_protocol']==VERSION and first.get('validation_code')=='invalid_plan_shape'
        code='invalid_plan_shape' if shape_correction else 'draft_over_budget' if budget_correction else hypothesis_review.CODE if criterion_correction else 'invalid_prerequisite'
        if (not isinstance(feedback,dict) or first.get('status')!=('needs_input' if budget_correction else 'validation_failed')
                or first.get('validation_code')!=code or last.get('corrects')!=first['id']
                or last.get('session_id')!=first.get('session_id') or feedback.get('code')!=code
                or feedback.get('explanation')!=first.get('explanation' if budget_correction else 'error') or feedback.get('corrections_remaining')!=0
                or feedback.get('native_started') is not False or feedback.get('request_unchanged') is not True
                or feedback.get('model_requests_used')!=1 or feedback.get('model_request_limit')!=(REQUEST_LIMIT if record['initial_review_protocol'] in (V6_VERSION,VERSION) else LEGACY_REQUEST_LIMIT)
                or feedback.get('execution_scope')!=record.get('execution_scope')):
            raise ValueError('规划修正未关联原始失败和实际校验反馈')
        if criterion_correction:
            try:
                if feedback.get('hypothesis_check')!=first.get('hypothesis_check') or first.get('budget_check')!=first['hypothesis_check']['budget_check']:
                    raise ValueError('判据修正未保留首次具体冲突和实际预算')
                expected_version={DIRECT_CRITERION_VERSION:hypothesis_review.LEGACY_VERSION,
                                  POLICY_VERSION:hypothesis_review.POLICY_VERSION,V5_VERSION:hypothesis_review.VERSION,
                                  V6_VERSION:hypothesis_review.VERSION,VERSION:hypothesis_review.VERSION}[record['initial_review_protocol']]
                if first['hypothesis_check'].get('version')!=expected_version:
                    raise ValueError('判据修正协议与初始规划版本不符')
                if record['initial_review_protocol'] in (POLICY_VERSION,V5_VERSION,V6_VERSION,VERSION):policy_patch.for_check(first['hypothesis_check']).verify_receipt(first,last)
                hypothesis_review.verify_revision(first['hypothesis_check'],first['proposal'],last['proposal'],record)
                if last.get('budget_check')!=record.get('budget_check'):
                    raise ValueError('判据修正后的请求记录和执行预算不同')
            except (KeyError,TypeError,ModelError):
                raise ValueError('判据修正证据缺失或无效') from None
        if budget_correction:
            from .planning_budget import verify_budget_revision,verify_initial_costs
            try:
                if feedback['budget_check']!=first['budget_check'] or not first['budget_check']['correction_feasible']:
                    raise ValueError('预算修正未保留实际校验明细')
                for attempt in attempts:verify_initial_costs(attempt['budget_check'],record['request'])
                verify_budget_revision(first['budget_check'],last['budget_check'])
                if not last['budget_check']['fits']:raise ValueError('修正仍超出预算')
                definitions=record['experiments']
                if len(definitions)!=len(last['budget_check']['groups']):raise ValueError('执行研究与预算校验不同')
                for entry,group in zip(definitions,last['budget_check']['groups']):
                    if (entry['study']!=group['study'] or entry['target_ref']!=group['target_ref'] or
                            any(entry['task'].get(k)!=v for k,v in group['conditions'].items() if k!='completion_required')):
                        raise ValueError('执行条件与修正时的预算校验不同')
            except (KeyError,TypeError):
                raise ValueError('预算修正明细缺失或无效') from None
    patch_expected=(len(attempts)==2 and record['initial_review_protocol'] in (POLICY_VERSION,V5_VERSION,V6_VERSION,VERSION)
                    and attempts[0].get('validation_code')==hypothesis_review.CODE)
    if any(('policy_patch' in a or 'proposal_origin' in a) and not (patch_expected and i==1) for i,a in enumerate(attempts)):
        raise ValueError('字段修正来源不属于本次协议或阶段')
    usages=[a.get('usage') or {} for a in attempts]
    model_usage=safe_usage(record.get('model_usage'))
    # Older attempt filters omitted this optional diagnostic, while model_usage
    # retained the transport reply. Compare accounting without rewriting evidence.
    if isinstance(model_usage,dict) and 'request_thinking_mode' not in usages[-1]:
        model_usage.pop('request_thinking_mode',None)
    if record.get('initial_model_usages')!=usages or model_usage!=usages[-1]:
        raise ValueError('初始规划用量与各次请求记录不符')
    return len(attempts)
