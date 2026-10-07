"""Verify that saved model choices preceded and controlled actual experiments."""
from datetime import datetime
from .feedback_planner import digest, run_observations, parking_options, verify_review_permissions, verify_input_wire
from .study_handoff import actions as handoff_actions, closes_study


def verify_planning(state, diagnosis):
    traces = state.get('planner_trace', [])
    actual = {o['ref']: o for o in run_observations(state)}
    by_ref = {f"{state['id']}:{r['name']}": r for r in state['runs']}
    chosen = []
    knowledge_checks = []
    seen = set()
    import json
    for t in traces:
        if t['kind'] == 'oneway_target':
            from .oneway_planning import observations
            actual = {o['ref']: o for o in observations(state)}
        else:
            actual = {o['ref']: o for o in run_observations(state)}
        payload = t['input']
        if digest(payload) != t['observation_digest'] or t['job_id'] != state['id']:
            raise ValueError('模型观测摘要或所属任务被改变')
        refs = payload['evidence_refs']
        if refs != [o['ref'] for o in payload['observations']] or len(set(refs)) != len(refs):
            raise ValueError('模型观测引用无效')
        expected_refs = [r for r in actual if datetime.fromisoformat(by_ref[r]['execution']['finished']) <= datetime.fromisoformat(t['time'])]
        if refs != expected_refs:
            raise ValueError('模型观测遗漏当时已完成的结果')
        for observed in payload['observations']:
            ref = observed['ref']
            if ref not in actual or observed != actual[ref]:
                raise ValueError('模型使用的观测与实际结果不符')
            if datetime.fromisoformat(by_ref[ref]['execution']['finished']) > datetime.fromisoformat(t['time']):
                raise ValueError('模型读取了尚未完成的观测')
        if t['status']=='accepted' and (not t['evidence_refs'] or any(r not in refs for r in t['evidence_refs'])):
            raise ValueError('模型决策缺少有效实测引用')
        if t['status']=='accepted' and (t['action'] not in payload['allowed_actions'] or t['selected'] != payload['allowed_actions'][t['action']]):
            raise ValueError('模型动作不在实际提供的白名单内')
        if t['status']!='accepted' and t['action']!='stop':
            raise ValueError('失败调用仍提出执行动作')
        context=payload['context']
        if context.get('knowledge_retrieval_protocol')!=state.get('knowledge_retrieval_protocol'):
            raise ValueError('决策知识检索协议与冻结任务不符')
        if context.get('knowledge_retrieval_protocol'):
            from .knowledge_retrieval import verify as verify_knowledge
            knowledge_checks.append(verify_knowledge(context['knowledge_retrieval'],
                payload['request'],payload['phase'],(context.get('task') or {}).get('scenario')))
        if context.get('choice_basis_protocol')!=state.get('choice_basis_protocol'):
            raise ValueError('选点观测协议与冻结任务不符')
        if any(context.get(k)!=state.get(k) for k in ('input_protocol','choice_rationale_protocol','risk_review_protocol','argument_correction_protocol')):
            raise ValueError('决策输入或试验依据协议与冻结任务不符')
        if (context.get('finding_reference_schema')!=state.get('finding_reference_schema')
                or state.get('finding_reference_schema') and t.get('finding_reference_schema')!=state['finding_reference_schema']):
            raise ValueError('假设引用协议与冻结任务不符')
        from .review_scope import local_hypotheses, verify_context
        verify_context(state, context)
        hypotheses=local_hypotheses(state)
        verify_review_permissions(t)
        from .action_execution import verify_context as verify_execution_context
        verify_execution_context(dict(state,runs=[by_ref[r] for r in refs],run_count=len(refs)),payload)
        if state.get('conclusion_protocol') or context.get('measured_conclusion'):
            from .conclusion_review import VERSION as CONCLUSION_VERSION,SUPPORTED_VERSIONS,contract,assess_entry
            protocol=state.get('conclusion_protocol',CONCLUSION_VERSION)
            recorded_protocol=context.get('measured_conclusion',{}).get('version')
            if protocol not in SUPPORTED_VERSIONS or (recorded_protocol is not None and recorded_protocol!=protocol):
                raise ValueError('结论核对协议无效')
            if context.get('measured_conclusion')!=contract(payload['observations'],context):
                raise ValueError('实测代价清单与当时观测不符')
            if t['status']=='accepted' and assess_entry(t)['issues']:
                raise ValueError('模型结论没有通过实测代价与未测量项核对')
            if 'conclusion_check' in t and t['conclusion_check']!=assess_entry(t):
                raise ValueError('结论核对记录与原回复不符')
        if state.get('evidence_protocol') and context.get('initial_hypotheses'):
            from .research_evidence import compact_findings,validate_updates
            prefix=dict(state,runs=[by_ref[r] for r in refs])
            if context.get('research_evidence')!=compact_findings(prefix) or context['initial_hypotheses']!=hypotheses:
                raise ValueError('假设检验输入不是当时已完成的配对证据')
            if t['status']=='accepted':
                validate_updates(t.get('hypothesis_updates'),hypotheses,context['research_evidence'],final=closes_study(t['action'],context))
        if t['kind'] == 'oneway_target':
            from .oneway_planning import verify_choice
            verify_choice(state, diagnosis, t)
        elif t['kind'] == 'parking':
            if 'research_depth' in context or 'usable_candidate_found' in context:
                from .study_depth import from_request
                depth=context.get('research_depth')
                if (not isinstance(depth,dict) or
                        depth!=from_request(state['task']['request'],depth.get('version')) or
                        context.get('usable_candidate_found') is not any(
                            by_ref[r].get('comparison',{}).get('eligible') is True for r in refs if by_ref[r].get('changes'))):
                    raise ValueError('本轮研究深度或已有可用方案状态与冻结原问题及实测不符')
            if state.get('evidence_protocol'):
                from .research_evidence import compact_findings,validate_updates
                prefix=dict(state,runs=[by_ref[r] for r in refs])
                context=payload['context']
                if context.get('research_evidence')!=compact_findings(prefix) or context.get('initial_hypotheses')!=hypotheses:
                    raise ValueError('假设检验输入不是当时已完成的配对证据')
                if t['status']=='accepted' and hypotheses:
                    validate_updates(t.get('hypothesis_updates'),hypotheses,context['research_evidence'],final=closes_study(t['action'],context))
            observed_policies = [by_ref[r] for r in refs if by_ref[r]['changes'] and by_ref[r]['demand_factor'] == 1]
            cost = len(state['task']['demand_factors']) if state.get('model_grid') else 1
            reserve = (0 if state.get('model_grid') else 2*(len(state['task']['demand_factors'])-1))+int(state['task']['repeat_baseline'])
            strict=t.get('version') in ('observed-planning-v2','observed-planning-v3')
            if strict:reserve=0  # Repeat and matched baselines precede model choices.
            available = max(0,state['task']['max_runs']-reserve-len(refs)) if len(observed_policies)<=state['task']['max_feedback'] else 0
            if payload['context']['available_policy_runs'] != available:
                raise ValueError('模型动作预算与实际剩余预算不符')
            if payload['context'].get('policy_cost') != cost or payload['context'].get('full_demand_grid') != bool(state.get('model_grid')):
                raise ValueError('候选完整配对成本不符')
            offered = parking_options(diagnosis['selected'], seen) if available >= cost else {}
            if t.get('version')=='observed-planning-v3':
                from .spatial_planning import options
                offered=options(diagnosis,seen,state['task']['max_links']) if available>=cost else {}
            if strict:
                from .planning_review import parking_gate
                prefix=dict(state,runs=[by_ref[r] for r in refs],run_count=len(refs),planner_trace=[])
                reserved=payload['context'].get('reserved_model_requests',0)
                if type(reserved)!=int or reserved<0:raise ValueError('后续研究的模型预留无效')
                offered,guard=parking_gate(prefix,offered,available,cost,max(0,payload['model_budget']['remaining']-reserved),
                    version=context.get('completion_guard',{}).get('version'))
                from .planning_budget import VERSION as ROUTING_VERSION
                transferring=context.get('study_routing_protocol')==ROUTING_VERSION and not context.get('programme_final_group',True)
                expected_stop=guard['stop_allowed'] and not transferring
                if payload['context'].get('completion_guard')!=guard or ('stop' in payload['allowed_actions'])!=expected_stop:
                    raise ValueError('必需比较未完成却允许停止或更改完成门槛')
                if transferring and guard['stop_allowed']:
                    if 'study_handoff' not in context:raise ValueError('缺少本组复核与后续研究衔接')
                elif context.get('study_handoff'):raise ValueError('未完成比较或没有后续组时不能衔接')
            transitions=handoff_actions(context)
            if {k:v for k,v in payload['allowed_actions'].items() if k!='stop' and k not in transitions} != offered:
                raise ValueError('候选菜单越过冻结范围或重复已测方案')
            if t['status']=='accepted' and t['action'] != 'stop' and t['action'] not in transitions:
                chosen.append(t)
                seen.add(json.dumps(t['selected']['changes'],sort_keys=True))
        elif t.get('version') in ('observed-planning-v2','observed-planning-v3') and bool(payload['context']['remaining_studies'])==('stop' in payload['allowed_actions']):
            raise ValueError('未完成的研究组被允许直接跳过')
    if state.get('target_planning_protocol') and diagnosis.get('targets'):
        targets = [t for t in traces if t['kind'] == 'oneway_target']
        if len(targets) != 1:
            raise ValueError('单向研究缺少唯一的事前模型选点')
    policies = [r for r in state['runs'] if r['changes'] and r['demand_factor']==1]
    if state.get('planning_protocol'):
        if len(chosen) != len(policies):
            raise ValueError('模型决定次数与实际执行不符')
        for t,r in zip(chosen,policies):
            paired = [p for p in state['runs'] if p['changes']==r['changes']] if state.get('model_grid') else [r]
            if state.get('model_grid') and (len(paired)!=len(state['task']['demand_factors']) or
                    {p['demand_factor'] for p in paired}!=set(state['task']['demand_factors'])):
                raise ValueError('模型候选没有完成全部需求的配对')
            if any(p['changes'] != t['selected']['changes'] or datetime.fromisoformat(t['finished']) > datetime.fromisoformat(p['execution']['started']) for p in paired):
                raise ValueError('原生执行不是来自之前通过校验的模型选择')
    if state.get('model_grid'):
        from .robust_search import conclusion
        if any(state.get(k)!=v for k,v in conclusion(state).items()):
            raise ValueError('跨需求推荐与已验证结果重算不一致')
    if state.get('planning_protocol') in ('observed-planning-v2','observed-planning-v3'):
        from .planning_review import coverage
        review=coverage(state)
        if state.get('goal_review')!=review:
            raise ValueError('完成判定与实测证据不符')
        if state['status']=='completed' and not review['complete']:
            raise ValueError('研究未完成却标记已完成')
    if state.get('evidence_protocol'):
        from .research_evidence import latest_review
        if state.get('research_review')!=latest_review(state):raise ValueError('研究结论与本次配对事实不符')
        if state['status']=='completed' and state.get('research_hypotheses') and not state['research_review']['reviewed']:
            raise ValueError('本组假设未完整复核却标记完成')
    for entry in traces:verify_input_wire(entry)
    return dict(passed=True, decisions=len(traces), model_selected_policies=len(chosen),
                observations_from_current_job_only=True, actions_precede_execution=True,
                knowledge_retrieval=dict(verified=knowledge_checks.count('verified'),
                                         catalog_changed=knowledge_checks.count('catalog_changed')))
