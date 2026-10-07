"""Frozen research programmes and evidence across independently paired studies."""
import copy
from datetime import datetime

from .feedback_planner import digest,verify_review_permissions,verify_input_wire
from .research_evidence import findings,validate_updates,REFERENCE_SCHEMA,LEGACY_REFERENCE_SCHEMA
from .planning_response import request_usages, CORRECTION_VERSION, HANDOFF_CORRECTION_VERSION, ARGUMENT_CORRECTION_VERSION

VERSION='programme-evidence-v1'
ENGINES={'parking':'TranStar 基础分配','diagnosis':'TranStar 基础分配（只诊断）',
         'oneway':'TranStar Vehicle1＋道路禁行适配','circulation':'TranStar Vehicle1＋转向状态图适配'}


def manifest(id,definitions,agents,hypotheses,source_inputs,created,*,model_limit=6,initial_requests=1,choice_basis_protocol=None,knowledge_retrieval_protocol=None):
    from .planning_budget import VERSION as ROUTING_VERSION
    from .review_scope import contract as review_scope
    from .action_execution import VERSION as EXECUTION_VERSION
    from .study_comparison import VERSION as COMPARISON_VERSION
    from .planning_payload import VERSION as INPUT_VERSION
    from .choice_rationale import VERSION as RATIONALE_VERSION
    from .risk_review import VERSION as RISK_VERSION
    return dict(version=VERSION,id=id,created=created,hypotheses=copy.deepcopy(hypotheses),
                finding_reference_schema=REFERENCE_SCHEMA,
                input_protocol=INPUT_VERSION,
                risk_review_protocol=RISK_VERSION,
                argument_correction_protocol=ARGUMENT_CORRECTION_VERSION,
                **(dict(choice_rationale_protocol=RATIONALE_VERSION) if choice_basis_protocol else {}),
                study_comparison_protocol=COMPARISON_VERSION,
                action_execution_protocol=EXECUTION_VERSION,
                **(dict(choice_basis_protocol=choice_basis_protocol) if choice_basis_protocol else {}),
                **(dict(knowledge_retrieval_protocol=knowledge_retrieval_protocol) if knowledge_retrieval_protocol else {}),
                review_scope=review_scope(len(definitions)),
                study_review_correction_protocol=HANDOFF_CORRECTION_VERSION,
                routing_budget=dict(version=ROUTING_VERSION,model_limit=model_limit,initial_requests=initial_requests),
                source_inputs=copy.deepcopy(source_inputs),
                studies=[dict(id=f'study_{i+1}',job_id=a.job_id,title=d['title'],task=copy.deepcopy(d['task']),
                              runs=d['task']['max_runs']) for i,(d,a) in enumerate(zip(definitions,agents))])


def native_complete(state,evidence):
    task=state['task']
    if task['scenario']=='parking':
        from .planning_review import coverage
        complete=coverage(state)['execution_complete']
        complete &= any(f['id']=='benefit' and f['status']!='unmeasured' for f in evidence['findings'])
    else:complete=evidence.get('execution_complete',False)
    if task.get('repeat_baseline'):
        complete &= state.get('repeatability',{}).get('exact_match') is True and any(
            r['name']=='baseline_repeat' and r.get('execution',{}).get('status')=='completed' for r in state.get('runs',[]))
    return bool(complete)


def evidence(plan,states,compact=True):
    groups=[];facts=[]
    for study in plan['studies']:
        state=states[study['job_id']]
        local=findings(state)
        local_facts=[]
        for finding in local['findings']:
            if finding['id'].startswith('hypothesis.'):continue
            item=copy.deepcopy(finding)
            item['id']=study['id']+'.'+item['id']
            item['question']=study['title']+'：'+item['question']
            if compact:item.pop('measurements',None)
            local_facts.append(item)
        facts.extend(local_facts)
        groups.append(dict(id=study['id'],job_id=study['job_id'],title=study['title'],
                           scenario=study['task']['scenario'],engine=ENGINES[study['task']['scenario']],
                           execution_complete=native_complete(state,local),finding_ids=[f['id'] for f in local_facts]))
    from .hypothesis_contract import findings as criterion_findings
    criteria=criterion_findings(plan['hypotheses'],{s['id']:states[s['job_id']] for s in plan['studies']})
    if compact:criteria=[{k:v for k,v in f.items() if k!='measurements'} for f in criteria]
    facts.extend(criteria)
    return dict(version=VERSION,programme_id=plan['id'],groups=groups,findings=facts,
                scope='各组采用各自的新基准与固定范围。跨组可以比较约束是否通过及证据缺口；不能混合收益、绝对值排名或推断联合实施效果。')


def context(plan,states,owner,final_group):
    result=dict(programme_id=plan['id'],programme_plan_digest=digest(plan),
                programme_evidence=evidence(plan,states),programme_hypotheses=copy.deepcopy(plan['hypotheses']),
                programme_final_group=bool(final_group),programme_current_job=owner)
    if plan.get('review_scope'):
        from .study_comparison import programme_comparisons
        result.update(review_scope=copy.deepcopy(plan['review_scope']),
                      programme_comparisons=programme_comparisons(plan, states))
    if plan.get('study_review_correction_protocol'):
        result['study_review_correction_protocol']=plan['study_review_correction_protocol']
    from .planning_budget import VERSION as ROUTING_VERSION
    if plan.get('routing_budget',{}).get('version')==ROUTING_VERSION:
        result['study_routing_protocol']=ROUTING_VERSION
    return result


def review(plan,states,traces):
    measured=evidence(plan,states,compact=False)
    last=next((t for t in reversed(traces) if t.get('input',{}).get('context',{}).get('programme_final_group')),None)
    reviewed=bool(last and last['status']=='accepted' and last['action']=='stop')
    updates=[];summary='尚未完成整个研究任务的模型复核。'
    if reviewed:
        if len(plan['studies'])==1:
            prefix=plan['studies'][0]['id']+'.'
            updates=[dict(u,finding_ids=[k if k.startswith('hypothesis.') else prefix+k for k in u['finding_ids']]) for u in last.get('hypothesis_updates',[])]
            summary=last.get('summary','')
        else:
            updates=last.get('programme_updates',[])
            summary=last.get('programme_summary','')
        try:validate_updates(updates,plan['hypotheses'],measured,final=True,reference_schema=plan.get('finding_reference_schema',LEGACY_REFERENCE_SCHEMA))
        except ValueError:reviewed=False
    gaps=[g['title']+'：约定的原生对照仍未齐。' for g in measured['groups'] if not g['execution_complete']]
    gaps.extend(g['title']+'：本组执行或复核未正常完成。' for g in measured['groups']
                if states[g['job_id']]['status'] in ('failed','incomplete','needs_validation'))
    if not reviewed:gaps.append('整个研究任务尚无完整模型复核。')
    if any(t.get('input',{}).get('context',{}).get('measured_conclusion') for t in traces):
        from .conclusion_review import assess_traces
        if assess_traces(traces)['issues']:gaps.append('模型结论与实测代价或已知未测量项存在冲突，结论待核对。')
    result=dict(version=VERSION,evidence=measured,hypotheses=plan['hypotheses'],updates=updates,
                summary=summary,reviewed=reviewed,complete=not gaps,unanswered=gaps)
    from .study_comparison import VERSION as COMPARISON_VERSION,programme_comparisons
    if plan.get('study_comparison_protocol')==COMPARISON_VERSION:
        result['comparisons']=programme_comparisons(plan,states)
    return result


def verify(plan,record,states):
    """Rebuild every cross-study prompt from outputs available at that moment.

    Raw native files must be checked separately by verify_experiment. This audit
    cannot turn mocked metrics or an archived old run into new native evidence.
    """
    if plan['version']!=VERSION or plan['id']!=record['job_id']:
        raise ValueError('研究总任务标识或协议不符')
    expected={s['job_id'] for s in plan['studies']}
    if plan.get('study_review_correction_protocol') not in (None,HANDOFF_CORRECTION_VERSION):
        raise ValueError('总任务交接复核修正协议无效')
    if plan.get('review_scope'):
        from .review_scope import contract as review_scope
        if plan['review_scope'] != review_scope(len(plan['studies'])):
            raise ValueError('总任务假设复核范围无效')
    if set(states)!=expected or set(record['job_ids'])!=expected:raise ValueError('研究组缺失或越界')
    for study in plan['studies']:
        s=states[study['job_id']]
        if (s['task']!=study['task'] or s.get('programme_plan_digest')!=digest(plan)
                or s.get('research_hypotheses')!=plan['hypotheses']
                or s.get('review_scope')!=plan.get('review_scope')):
            raise ValueError('研究组任务卡或预先冻结的总计划不符')
        if s.get('choice_basis_protocol')!=plan.get('choice_basis_protocol'):
            raise ValueError('选点观测协议与冻结总任务不符')
        if s.get('knowledge_retrieval_protocol')!=plan.get('knowledge_retrieval_protocol'):
            raise ValueError('知识检索协议与冻结总任务不符')
        if s.get('finding_reference_schema')!=plan.get('finding_reference_schema'):
            raise ValueError('假设引用协议与冻结总任务不符')
        if any(s.get(k)!=plan.get(k) for k in ('input_protocol','choice_rationale_protocol','risk_review_protocol','argument_correction_protocol')):
            raise ValueError('决策输入或试验依据协议与冻结总任务不符')
    traces=record.get('planning_trace',[])
    corrections=[i for i,t in enumerate(traces) if t.get('review_correction')]
    if len(corrections)>1 or any(traces[i]['review_correction'].get('version')==CORRECTION_VERSION
                               and i!=len(traces)-1 for i in corrections):
        raise ValueError('交接与最终复核只能共享一次修正；最终复核之后不能继续新增调度')
    usages=copy.deepcopy(record.get('initial_model_usages')) if corrections else None
    if corrections and not isinstance(usages,list):
        raise ValueError('最终复核修正缺少初始用量记账')
    routing=plan.get('routing_budget')
    if routing:
        from .planning_budget import VERSION as ROUTING_VERSION,LEGACY_VERSION,starting_with
        from .feedback_planner import REQUEST_LIMIT
        if (routing.get('version') not in (ROUTING_VERSION,LEGACY_VERSION) or type(routing.get('model_limit')) is not int
                or not 1<=routing['model_limit']<=REQUEST_LIMIT or type(routing.get('initial_requests')) is not int
                or not 1<=routing['initial_requests']<=2):raise ValueError('研究请求预算未正确冻结')
        if routing['initial_requests']+starting_with([s['task'] for s in plan['studies']],version=routing['version'])>routing['model_limit']:
            raise ValueError('首组顺序无法完成冻结的全部研究及复核')
        used=routing['initial_requests']
        if record.get('initial_model_usages') is not None and len(record['initial_model_usages'])!=used:
            raise ValueError('首组预算与初始调用记账不符')
    for entry in traces:
        if usages is not None:
            ledger=entry['input']['model_budget']
            if (ledger['known_tokens']!=sum(u.get('tokens') or 0 for u in usages)
                    or ledger['unknown_usage']!=sum(u.get('tokens') is None for u in usages)):
                raise ValueError('逐轮模型用量遗漏了先前请求')
            usages.extend(request_usages(entry))
        if routing:
            budget=entry['input']['model_budget']
            if (budget['used']!=used or budget['limit']!=routing['model_limit']
                    or budget['remaining']!=max(0,routing['model_limit']-used)):
                raise ValueError('逐轮模型预算与冻结总额或实际调用次序不符')
            used+=len(request_usages(entry))
            if used>routing['model_limit']:raise ValueError('模型请求超过冻结上限')
        ctx=entry['input']['context']
        if ctx.get('choice_basis_protocol')!=plan.get('choice_basis_protocol'):
            raise ValueError('逐轮选点观测协议被变更')
        if ctx.get('finding_reference_schema')!=plan.get('finding_reference_schema'):
            raise ValueError('逐轮假设引用协议被变更')
        if any(ctx.get(k)!=plan.get(k) for k in ('input_protocol','choice_rationale_protocol','risk_review_protocol','argument_correction_protocol')):
            raise ValueError('逐轮决策输入或试验依据协议被变更')
        if ctx.get('study_review_correction_protocol')!=plan.get('study_review_correction_protocol'):
            raise ValueError('交接复核修正权限与冻结总任务不符')
        if (ctx.get('programme_id')!=plan['id'] or ctx.get('programme_plan_digest')!=digest(plan)
                or ctx.get('programme_current_job')!=entry['job_id']):
            raise ValueError('模型未读取冻结的研究总计划')
        observed={}
        time=datetime.fromisoformat(entry['time'])
        for id,state in states.items():
            prefix=copy.deepcopy(state)
            prefix['runs']=[r for r in state.get('runs',[]) if r.get('execution',{}).get('status')=='completed'
                            and datetime.fromisoformat(r['execution']['finished'])<=time]
            prefix['run_count']=len(prefix['runs'])
            observed[id]=prefix
        verify_review_permissions(entry)
        if plan.get('action_execution_protocol') or ctx.get('action_execution'):
            from .action_execution import verify_context,VERSION as EXECUTION_VERSION
            if plan.get('action_execution_protocol')!=EXECUTION_VERSION or ctx.get('action_execution',{}).get('version')!=EXECUTION_VERSION:
                raise ValueError('动作执行定义与冻结总任务协议不符')
            verify_context(observed[entry['job_id']],entry['input'],plan['studies'],observed)
        if ctx.get('review_scope') != plan.get('review_scope'):
            raise ValueError('模型假设复核范围与冻结计划不符')
        if plan.get('review_scope'):
            from .study_comparison import programme_comparisons
            if ctx.get('programme_comparisons') != programme_comparisons(plan, observed):
                raise ValueError('分组实测对照缺失、被改写或包含未来结果')
        if ctx.get('programme_evidence')!=evidence(plan,observed) or ctx.get('programme_hypotheses')!=plan['hypotheses']:
            raise ValueError('跨组复核引用了未来结果、漏掉现有证据或改写了假设')
        from .conclusion_review import VERSION as CONCLUSION_VERSION,V6_VERSION,V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION
        if 'programme_cost_facts' in ctx or ctx.get('measured_conclusion',{}).get('version') in (V6_VERSION,V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,CONCLUSION_VERSION):
            from .conclusion_review import cost_facts,contract,assess_entry
            from .feedback_planner import run_observations
            costs=[r for study in plan['studies'] for r in cost_facts(run_observations(observed[study['job_id']]))]
            if ctx.get('programme_cost_facts')!=costs or ctx.get('measured_conclusion')!=contract(entry['input']['observations'],ctx):
                raise ValueError('跨组代价清单缺失或包含未来实测')
            if ctx.get('measured_conclusion',{}).get('version') in ('measured-conclusion-v2','measured-conclusion-v5',V6_VERSION,V7_VERSION,V8_VERSION,V9_VERSION,V10_VERSION,V11_VERSION,V12_VERSION,V13_VERSION,V14_VERSION,V15_VERSION,V16_VERSION,V17_VERSION,CONCLUSION_VERSION):
                from .executed_facts import policy_facts
                policies=[r for study in plan['studies'] for r in policy_facts(run_observations(observed[study['job_id']]))]
                if ctx.get('programme_policy_facts')!=policies:
                    raise ValueError('跨组实际措施清单缺失、被改写或包含未来实测')
            if entry['status']=='accepted' and assess_entry(entry)['issues']:
                raise ValueError('跨组结论没有通过实测代价核对')
        pending=[]
        for study in plan['studies']:
            if study['job_id']==entry['job_id']:continue
            started=any(datetime.fromisoformat(r['execution']['started'])<=time for r in states[study['job_id']].get('runs',[]))
            if not started:pending.append(study)
        if ctx.get('programme_final_group')!= (not pending):raise ValueError('未执行研究组被当作最终复核')
        if routing and entry['kind'] in ('parking','oneway_target'):
            from .planning_budget import reserved_after_current
            owner=next(s for s in plan['studies'] if s['job_id']==entry['job_id'])
            expected_reserve=reserved_after_current(owner['task'],[s['task'] for s in pending],routing['version'])
            if ctx.get('reserved_model_requests')!=expected_reserve:
                raise ValueError('未为剩余必需研究与复核保留正确预算')
        handoff=False
        if routing and routing['version']==ROUTING_VERSION:
            if ctx.get('study_routing_protocol')!=ROUTING_VERSION:raise ValueError('研究衔接协议与冻结计划不符')
            if entry['kind']=='parking' and pending:
                if 'stop' in entry['input']['allowed_actions']:raise ValueError('仍有必做研究却开放结束整个任务')
                from .study_handoff import route_menu,contract as handoff_contract
                expected_handoff=native_complete(observed[entry['job_id']],findings(observed[entry['job_id']]))
                routes,guard=route_menu(pending,entry['input']['model_budget']['remaining'],routing['version'])
                if expected_handoff:
                    if (ctx.get('study_handoff')!=handoff_contract(routes) or ctx.get('route_budget')!=guard
                            or ctx.get('remaining_studies')!=len(pending)):
                        raise ValueError('本组复核衔接菜单或预算与当时剩余研究不符')
                    actual={k:v for k,v in entry['input']['allowed_actions'].items() if k.startswith('study_')}
                    if actual!=routes:raise ValueError('本组复核衔接遗漏或越过冻结研究组')
                    handoff=entry.get('action') in routes
                elif ctx.get('study_handoff') or any(k.startswith('study_') for k in entry['input']['allowed_actions']):
                    raise ValueError('本组必需对照未齐就开放后续研究')
            elif ctx.get('study_handoff'):raise ValueError('当前研究没有可衔接的后续组')
        elif ctx.get('study_handoff'):raise ValueError('旧预算协议不允许新的研究衔接动作')
        if entry['kind']=='route':
            options={s['id']:dict(title=s['title'],road=s['task'].get('road_name'),scenario=s['task']['scenario'],runs=s['runs']) for s in pending}
            if routing:
                from .planning_budget import route_guard
                guard=route_guard(pending,entry['input']['model_budget']['remaining'],routing['version'])
                if ctx.get('route_budget')!=guard:raise ValueError('后续研究的可执行顺序与当时预算不符')
                options={k:v for k,v in options.items() if guard['choices'][k]['feasible']}
            actual={k:v for k,v in entry['input']['allowed_actions'].items() if k!='stop'}
            if actual!=options:raise ValueError('跨组调度菜单遗漏或重复研究组')
        if entry['kind']=='route' or handoff:
            if entry['status']=='accepted' and entry['action']!='stop':
                target=next(s for s in pending if s['id']==entry['action'])
                runs=states[target['job_id']].get('runs',[])
                if runs and min(datetime.fromisoformat(r['execution']['started']) for r in runs)<datetime.fromisoformat(entry['finished']):
                    raise ValueError('后续研究在模型选择之前启动')
                starts=[(min(datetime.fromisoformat(r['execution']['started']) for r in states[s['job_id']]['runs']),s['id'])
                        for s in pending if states[s['job_id']].get('runs')]
                if starts and min(starts)[1]!=target['id']:raise ValueError('实际下一组没有遵循模型选择')
        if entry['status']=='accepted' and entry['action']=='stop' and ctx['programme_final_group'] and len(plan['studies'])>1:
            validate_updates(entry.get('programme_updates'),plan['hypotheses'],ctx['programme_evidence'],final=True)
            from .llm_client import substantive_text
            if not substantive_text(entry.get('programme_summary')):raise ValueError('总任务复核没有有效说明')
    if routing:
        budget=record.get('model_budget',{})
        if (budget.get('used')!=used or budget.get('limit')!=routing['model_limit']
                or budget.get('remaining')!=max(0,routing['model_limit']-used)):
            raise ValueError('最终模型预算与实际调用记账不符')
        if usages is not None and (budget['known_tokens']!=sum(u.get('tokens') or 0 for u in usages)
                or budget['unknown_usage']!=sum(u.get('tokens') is None for u in usages)):
            raise ValueError('最终模型用量未包含两次复核请求')
    if record.get('programme_review')!=review(plan,states,traces):raise ValueError('总任务结论与逐组实际证据不符')
    if record['status']=='completed' and not record['programme_review']['complete']:raise ValueError('未完整研究被标记完成')
    for entry in traces:verify_input_wire(entry)
    return dict(passed=True,groups=len(states),decisions=len(traces),no_cross_engine_pooling=True,
                observations_checked_at_decision_time=True,request_reservations_checked=bool(routing))
