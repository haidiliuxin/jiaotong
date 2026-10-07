"""Offline result-integrity tests. Metrics here are synthetic unless noted."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from traffic_agent.conclusion_review import (VERSION, V5_VERSION, V6_VERSION, V7_VERSION, V8_VERSION, V9_VERSION, cost_facts, contract, assess_entry,
    acknowledgment_issues, expected_acknowledgments, reviewed_cost_ref_issues, assess_state)
from traffic_agent.feedback_planner import PlanningSession,decision_tool,instructions_for
from traffic_agent.dispatch import Dispatcher
from test_feedback_planner import reply


def observation(ref='j:policy', new=.056, net=.056, eligible=True, road='乙路'):
    return dict(ref=ref,run=ref.split(':')[-1],demand_factor=1,eligible=eligible,
                changes=[dict(from_node=1,to_node=2,parking=1)],
                core_roads=[dict(name=road,from_node=1,to_node=2)],
                delta=dict(core=dict(running_time_change_pct=-16),neighbor=dict(running_time_change_pct=.011,
                           oversaturated_km_change=net),network=dict(running_time_change_pct=-.06,oversaturated_km_change=.056)),
                new_neighbor_oversaturated_km=new)


def entry(text='存在实测收益，但仍有周边代价。', observations=None):
    return dict(status='accepted',action='stop',job_id='j',ordinal=3,summary=text,
                input=dict(observations=observations or [observation()],context={}))


class ConclusionTests(unittest.TestCase):
    def test_v7_rejects_universal_cost_claim_not_true_at_each_demand(self):
        high=observation('j:model_1_demand_1p000',new=2.594,eligible=False)
        low=observation('j:model_1_demand_0p900',new=0,eligible=False)
        low['demand_factor']=.9
        low['delta']['neighbor']['running_time_change_pct']=-.18
        observations=[high,low]
        value=entry('两需求均因邻区新增过饱和超门槛被拒。',observations)
        context=value['input']['context']
        context.update(task=dict(demand_factors=[.9,1.0],max_new_oversaturated_km=.1,
                                 max_neighbor_time_increase_pct=.5),
                       measured_conclusion=dict(version=V8_VERSION))
        value['cost_acknowledgments']=expected_acknowledgments(contract(observations,context))
        self.assertIn('cross_demand_cost_quantifier',{i['code'] for i in assess_entry(value)['issues']})
        value['summary']='两需求均未通过全部原定约束，需求1.0邻区毛新增2.594km，需求0.9邻区毛新增0km。'
        self.assertNotIn('cross_demand_cost_quantifier',{i['code'] for i in assess_entry(value)['issues']})
        value['summary']='两需求均因邻区耗时超限而失败。'
        self.assertIn('cross_demand_cost_quantifier',{i['code'] for i in assess_entry(value)['issues']})
        value['summary']='两需求均未同时满足邻区门槛。'
        self.assertIn('cross_demand_cost_quantifier',{i['code'] for i in assess_entry(value)['issues']})
        low['ref']='j:model_2_demand_0p900'
        low['run']='model_2_demand_0p900'
        self.assertNotIn('cross_demand_cost_quantifier',{i['code'] for i in assess_entry(value)['issues']})
        low['ref']='j:model_1_demand_0p900'
        low['run']='model_1_demand_0p900'
        context['measured_conclusion']['version']=V6_VERSION
        self.assertNotIn('cross_demand_cost_quantifier',{i['code'] for i in assess_entry(value)['issues']})

    def test_v6_false_demand_claim_is_flagged_only_in_read_view(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'dispatches').mkdir()
            high=observation('j:model_1_demand_1p000',new=2.594,eligible=False)
            low=observation('j:model_1_demand_0p900',new=0,eligible=False)
            low['demand_factor']=.9
            final=entry('两需求均因邻区新增过饱和超门槛被拒。',[high,low])
            ctx=final['input']['context']
            ctx.update(task=dict(demand_factors=[.9,1.0],max_new_oversaturated_km=.1,
                                 max_neighbor_time_increase_pct=.5),
                       measured_conclusion=dict(version=V6_VERSION))
            final['cost_acknowledgments']=expected_acknowledgments(contract([high,low],ctx))
            path=root/'dispatches/j.json'
            path.write_text(json.dumps(dict(job_id='j',status='completed',planning_trace=[final],
                progress=[dict(job_id='j',status='completed')]),ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            displayed=Dispatcher(root,root,Mock()).listing()[0]
            self.assertEqual(displayed['recorded_status'],'completed')
            self.assertEqual(displayed['status'],'incomplete')
            self.assertEqual(displayed['public_claim_review']['issues'][0]['code'],'cross_demand_cost_quantifier')
            self.assertEqual(path.read_bytes(),before)

    def test_read_view_flags_old_public_claim_without_rewriting_verified_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'dispatches').mkdir()
            final=entry('区1停车1级邻区+0.51%/-2.797km超限。',
                        observations=[observation(new=2.797,eligible=False)])
            final['input']['context']['measured_conclusion']=dict(version=V5_VERSION)
            final['input']['model_budget']=dict(remaining=3)
            original=dict(job_id='j',status='completed',planning_trace=[final],
                          progress=[dict(job_id='j',status='completed')])
            path=root/'dispatches/j.json'
            path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            displayed=Dispatcher(root,root,Mock()).listing()[0]
            self.assertEqual(displayed['recorded_status'],'completed')
            self.assertEqual(displayed['status'],'incomplete')
            self.assertEqual(displayed['progress'][0]['status'],'incomplete')
            self.assertEqual(displayed['public_claim_review']['issues'][0]['code'],'unsigned_gross_metric')
            self.assertEqual(path.read_bytes(),before)

    def test_v6_typed_public_claims_use_actual_cost_budget_and_level(self):
        cases=[('区1停车1级邻区+0.51%/-2.797km超限','unsigned_gross_metric'),
               ('模型预算已尽，结束复核','model_budget_not_exhausted'),
               ('预算与仿真次数已用尽，结束复核','model_budget_not_exhausted'),
               ('计划改测更强等级（2级对比未做）','parking_strength_reversed')]
        for text,code in cases:
            with self.subTest(code=code):
                value=entry(text,observations=[observation(new=2.797,eligible=False)])
                value['input']['model_budget']=dict(remaining=3)
                self.assertIn(code,{x['code'] for x in assess_entry(value)['issues']})
                value['input']['context']['measured_conclusion']=dict(version=V5_VERSION)
                self.assertNotIn(code,{x['code'] for x in assess_entry(value)['issues']})

    def test_v6_typed_claims_allow_explicit_net_change_and_remaining_budget(self):
        value=entry('邻区过饱和净变化−0.201km，毛新增仍为2.797km；停车2级只是未测的减弱治理对照。',
                    observations=[observation(new=2.797,net=-.201,eligible=False)])
        value['input']['model_budget']=dict(remaining=1)
        self.assertFalse([x for x in assess_entry(value)['issues'] if x['code'] in
                          ('unsigned_gross_metric','model_budget_not_exhausted','parking_strength_reversed')])
        value['summary']='邻区净过饱和里程变化−0.201km，毛新增仍为2.797km。'
        self.assertFalse([x for x in assess_entry(value)['issues'] if x['code']=='unsigned_gross_metric'])

    def test_rejected_reply_without_validated_content_cannot_prove_a_factual_conflict(self):
        from traffic_agent.conclusion_review import assess_traces
        rejected=entry();rejected.update(status='rejected',reason='模型工具响应未通过本地校验')
        rejected.pop('summary');rejected['input']['context']['measured_conclusion']=contract(rejected['input']['observations'],{})
        before=copy.deepcopy(rejected)
        result=assess_traces([rejected])
        self.assertEqual(result['status'],'not_reviewed')
        self.assertEqual(result['issues'],[])
        self.assertEqual(result['costs'][0]['neighbor_new_oversaturated_km'],.056)
        self.assertIn('不能推定',result['unreviewed'][0]['message'])
        self.assertEqual(rejected,before)

    def test_saved_programme_gap_from_missing_reply_is_corrected_only_in_read_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dispatches').mkdir()
            rejected=entry();rejected.update(status='rejected');rejected.pop('summary')
            rejected['input']['context']['measured_conclusion']=contract(rejected['input']['observations'],{})
            old='模型结论与实测代价或已知未测量项存在冲突，结论待核对。'
            original=dict(job_id='j',status='incomplete',planning_trace=[rejected],programme_review=dict(
                complete=False,reviewed=False,unanswered=['第二组未执行。',old]))
            path=root/'dispatches/j.json';path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes();result=Dispatcher(root,root,Mock()).listing()[0]
            self.assertEqual(result['status'],'incomplete')
            self.assertNotIn(old,result['programme_review']['unanswered'])
            self.assertIn('第二组未执行。',result['programme_review']['unanswered'])
            self.assertIn(old,result['programme_review']['recorded_unanswered'])
            self.assertEqual(path.read_bytes(),before)

    def test_initial_planning_discloses_observable_limits_and_result_protocol(self):
        from traffic_agent.natural_plan import capabilities
        actual=capabilities()
        self.assertEqual(actual['conclusion_protocol'],VERSION)
        self.assertIn('排队长度或排队车辆数',actual['observables']['unavailable'])

    def test_within_threshold_cost_is_not_zero_and_missing_is_not_zero(self):
        row=cost_facts([observation()])[0]
        self.assertTrue(row['constraint_passed'])
        self.assertTrue(row['has_measured_cost'])
        self.assertEqual(row['neighbor_new_oversaturated_km'],.056)
        self.assertIsNone(row['network_new_oversaturated_km'])
        self.assertEqual(cost_facts([dict(ref='j:unfinished',changes=[{}])]),[])

    def test_display_exposes_actual_adverse_road_without_altering_prompt_contract(self):
        observed=observation()
        link=dict(index=7,name='邻接路',from_node=4,to_node=5,length_km=.056,vc=1.001)
        state=dict(id='j',runs=[dict(name='policy',demand_factor=1,changes=observed['changes'],
                   metrics={'core':{}},execution=dict(status='completed'),comparison=dict(delta=observed['delta'],
                   eligible=True,new_neighbor_oversaturated_km=.056,new_neighbor_oversaturated_links=[link]))])
        before=copy.deepcopy(state)
        result=assess_state(state)
        self.assertEqual(result['costs'][0]['neighbor_new_links'],[link])
        self.assertEqual(state,before)

    def test_gross_new_is_retained_even_when_net_congestion_falls(self):
        row=cost_facts([observation(new=.056,net=-.201)])[0]
        self.assertEqual(row['neighbor_new_oversaturated_km'],.056)
        self.assertEqual(row['neighbor_net_oversaturated_km'],-.201)
        value=dict(cost_acknowledgments=[dict(ref='j:policy',constraint_passed=True,has_measured_cost=True,
                                            neighbor_new_oversaturated_km=-.201)])
        self.assertIn('净变化',acknowledgment_issues(value,dict(costs=[row]),True)[0])

    def test_acknowledgment_requires_each_run_without_aliasing_boolean_number(self):
        facts=contract([observation(),observation('j:second')],{})
        good=dict(cost_acknowledgments=expected_acknowledgments(facts))
        self.assertEqual(acknowledgment_issues(good,facts,True),[])
        for key,bad in [('has_measured_cost',False),('constraint_passed',1),('neighbor_new_oversaturated_km',0),('neighbor_new_oversaturated_km',True)]:
            value=copy.deepcopy(good);value['cost_acknowledgments'][0][key]=bad
            self.assertTrue(acknowledgment_issues(value,facts,True),key)
        self.assertTrue(acknowledgment_issues(dict(cost_acknowledgments=good['cost_acknowledgments'][:1]),facts,True))
        self.assertTrue(acknowledgment_issues(dict(cost_acknowledgments=[good['cost_acknowledgments'][0]]*2),facts,True))
        self.assertEqual(acknowledgment_issues(dict(cost_acknowledgments=[]),facts,False),[])

    def test_v9_reviews_each_ref_without_recopying_measured_fields(self):
        observations=[observation(),observation('j:second',new=0,net=-.1)]
        facts=contract(observations,dict(measured_conclusion=dict(version=V9_VERSION)))
        refs=[r['ref'] for r in facts['costs']]
        self.assertEqual(reviewed_cost_ref_issues(dict(reviewed_cost_refs=refs),facts,True),[])
        for bad in (refs[:1],[refs[0],refs[0]],[refs[0],'j:unmeasured']):
            self.assertTrue(reviewed_cost_ref_issues(dict(reviewed_cost_refs=bad),facts,True))
        self.assertEqual(reviewed_cost_ref_issues(dict(reviewed_cost_refs=[]),facts,False),[])
        row=entry('邻区仍有实测代价。',observations)
        row['input']['context']['measured_conclusion']=dict(version=V9_VERSION)
        row['reviewed_cost_refs']=refs
        self.assertEqual(assess_entry(row)['issues'],[])
        payload=dict(allowed_actions={'stop':dict(title='完成复核')},evidence_refs=refs,
                     context=dict(measured_conclusion=facts,review_permissions=dict(programme_review_actions=[])))
        props=decision_tool(payload)['parameters']['properties']
        self.assertEqual(props['reviewed_cost_refs']['minItems'],2)
        self.assertNotIn('cost_acknowledgments',props)
        self.assertIn('reviewed_cost_refs',instructions_for(payload))
        self.assertIn('必须填[]',instructions_for(payload))
        old=dict(payload,context=dict(payload['context'],measured_conclusion=contract(
            observations,dict(measured_conclusion=dict(version=V8_VERSION)))))
        old_props=decision_tool(old)['parameters']['properties']
        self.assertIn('cost_acknowledgments',old_props)
        self.assertNotIn('reviewed_cost_refs',old_props)
        self.assertIn('cost_acknowledgments',instructions_for(old))
        self.assertNotIn('reviewed_cost_refs',instructions_for(old))

    def test_v10_allows_complete_early_cost_review_but_not_partial_review(self):
        observations=[observation(),observation('j:second',new=0,net=-.1)]
        facts=contract(observations,dict(measured_conclusion=dict(version=VERSION)))
        refs=[r['ref'] for r in facts['costs']]
        for candidate in ([],refs):
            self.assertEqual(reviewed_cost_ref_issues(
                dict(reviewed_cost_refs=candidate),facts,False,allow_early_review=True),[])
        for candidate in (refs[:1],[refs[0],refs[0]],[refs[0],'j:unmeasured']):
            self.assertTrue(reviewed_cost_ref_issues(
                dict(reviewed_cost_refs=candidate),facts,False,allow_early_review=True))
        self.assertTrue(reviewed_cost_ref_issues(
            dict(reviewed_cost_refs=refs),facts,False))  # Frozen v9 still rejects early review.
        continuing=entry('已核对两项实测代价，继续比较候选。',observations)
        continuing.update(action='parking_next',reviewed_cost_refs=refs)
        continuing['input']['context']['measured_conclusion']=facts
        self.assertEqual(assess_entry(continuing)['issues'],[])
        continuing['input']['context']['measured_conclusion']=dict(facts,version=V9_VERSION)
        self.assertIn('cost_review_refs',{i['code'] for i in assess_entry(continuing)['issues']})

    def test_false_zero_claims_are_flagged_without_changing_original(self):
        original=entry('两项均无邻区新增过饱和(阈值0.1km内)，无周边恶化，无溢出风险。')
        before=copy.deepcopy(original);result=assess_entry(original)
        self.assertEqual({i['code'] for i in result['issues']},{'zero_neighbor_congestion','zero_neighbor_cost','zero_risk'})
        self.assertEqual(original,before)
        self.assertEqual(assess_entry(entry('邻区毛新增0.056km，通过0.1km门槛，仍需保留代价，不能说无溢出风险。'))['issues'],[])
        self.assertEqual(assess_entry(entry('批次分配不证明动态排队回堵或零风险。'))['issues'],[])
        old=entry('批次分配不证明动态排队回堵或零风险。')
        old['input']['context']['measured_conclusion']=dict(version=V7_VERSION)
        self.assertIn('zero_risk',{i['code'] for i in assess_entry(old)['issues']})
        self.assertIn('zero_risk',{i['code'] for i in assess_entry(entry('已证明零风险。'))['issues']})

    def test_named_zero_cost_for_one_region_does_not_use_other_regions_increase(self):
        observations=[observation('j:area1',new=0,net=-.201,road='甲路'),observation('j:area2',road='乙路')]
        self.assertEqual(assess_entry(entry('甲路无邻区新增过饱和；乙路新增0.056km。',observations))['issues'],[])
        self.assertTrue(assess_entry(entry('两项均无邻区新增过饱和。',observations))['issues'])

    def test_queue_length_cannot_be_certified_by_speed_or_a_valid_finding_reference(self):
        e=entry();e['input']['context']['initial_hypotheses']=[dict(id='H1',hypothesis='缓解停车拥堵',evidence_needed='排队长度减少')]
        e['hypothesis_updates']=[dict(hypothesis_id='H1',judgment='supported',finding_ids=['benefit'],explanation='速度提高，故假设成立。')]
        self.assertEqual(assess_entry(e)['issues'][0]['code'],'unmeasured_observable')
        e['hypothesis_updates'][0]['judgment']='unresolved'
        self.assertEqual(assess_entry(e)['issues'],[])

    def test_conflicting_final_reply_halts_once_and_retains_safe_original(self):
        calls=[]
        def choose(instructions,inputs,tools,choice):
            payload=json.loads(inputs[0]['content']);calls.append(payload)
            response=reply('stop',['j:policy'],payload)
            args=json.loads(response['calls'][0]['arguments']);args['summary']='无邻区新增过饱和，无周边恶化。'
            response['calls'][0]['arguments']=json.dumps(args)
            return response
        session=PlanningSession(Mock(request=Mock(side_effect=choose)),'复核代价')
        value=session.decide('route','j',[observation()],{},dict(measured_conclusion=dict(version=V8_VERSION)))
        self.assertEqual(value['status'],'rejected');self.assertTrue(session.halted)
        self.assertEqual(value['proposed_decision']['summary'],'无邻区新增过饱和，无周边恶化。')
        self.assertEqual(value['conclusion_check'],assess_entry(value))
        self.assertNotIn('selected',value)
        session.decide('route','j',[observation()],{},dict(measured_conclusion=dict(version=V8_VERSION)))
        self.assertEqual(len(calls),1);self.assertEqual(session.used,2)

    def test_correct_final_reply_and_malformed_ack_have_different_outcomes(self):
        for malformed in (False,True):
            def choose(instructions,inputs,tools,choice):
                payload=json.loads(inputs[0]['content']);response=reply('stop',['j:policy'],payload)
                value=json.loads(response['calls'][0]['arguments'])
                value['summary']='通过约束，但邻区毛新增0.056km；保留不利影响与未测内容。'
                if malformed:value['cost_acknowledgments'][0]['has_measured_cost']=False
                response['calls'][0]['arguments']=json.dumps(value);return response
            session=PlanningSession(Mock(request=Mock(side_effect=choose)),'保留代价')
            result=session.decide('route','j',[observation()],{},dict(measured_conclusion=dict(version=V8_VERSION)))
            self.assertEqual(result['status'],'rejected' if malformed else 'accepted')

    def test_old_dispatch_status_is_derived_without_rewriting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dispatches').mkdir()
            path=root/'dispatches/j.json'
            path.write_text(json.dumps(dict(job_id='j',status='completed',planning_trace=[entry('无周边恶化。')]),ensure_ascii=False),encoding='utf-8')
            before=path.read_bytes()
            listed=Dispatcher(root,root,Mock()).listing()[0]
            self.assertEqual(listed['status'],'incomplete');self.assertEqual(listed['recorded_status'],'completed')
            self.assertEqual(listed['conclusion_review']['status'],'needs_review')
            self.assertEqual(path.read_bytes(),before)

    def test_old_group_and_final_turn_cannot_keep_a_completion_label_after_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dispatches').mkdir()
            path=root/'dispatches/j.json'
            state_path=root/'jobs/j/state.json';state_path.parent.mkdir(parents=True)
            trace=entry('无周边恶化。')
            original=dict(job_id='j',status='completed',planning_trace=[trace],progress=[
                dict(job_id='j',status='completed',review=dict(goal_review=dict(complete=True))),
                dict(job_id='other',status='completed',review=dict(goal_review=dict(complete=True)))])
            path.write_text(json.dumps(original,ensure_ascii=False),encoding='utf-8')
            state_path.write_text(json.dumps(dict(id='j',runs=[],planner_trace=[trace])),encoding='utf-8')
            before={p:p.read_bytes() for p in (path,state_path)}
            row=Dispatcher(root,root,Mock()).listing()[0]
            self.assertEqual(row['status'],'incomplete')
            self.assertEqual(row['progress'][0]['status'],'incomplete')
            self.assertEqual(row['progress'][0]['recorded_status'],'completed')
            self.assertEqual(row['progress'][0]['review']['conclusion_review']['status'],'needs_review')
            self.assertEqual(row['progress'][1]['status'],'completed')
            self.assertEqual(row['planning_trace'][0]['conclusion_review']['status'],'needs_review')
            self.assertEqual(row['planning_trace'][0]['summary'],trace['summary'])
            self.assertTrue(all(p.read_bytes()==data for p,data in before.items()))

    def test_audit_rejects_erasing_measured_cost_in_an_accepted_final_reply(self):
        from test_planning_completion import CompletionTests
        from traffic_agent.planning_audit import verify_planning
        helper=CompletionTests();self.addCleanup(helper.doCleanups)
        agent,session,payloads,diagnosis=helper.exercise()
        self.assertTrue(verify_planning(agent.state,diagnosis)['passed'])
        altered=copy.deepcopy(agent.state)
        final=altered['planner_trace'][-1]
        final['reviewed_cost_refs'][0]='j:unmeasured'
        # Recomputing the local check cannot make a fabricated acknowledgment valid.
        final['conclusion_check']=assess_entry(final)
        with self.assertRaisesRegex(ValueError,'实测代价'):
            verify_planning(altered,diagnosis)
        from traffic_agent.feedback_planner import digest
        removed=copy.deepcopy(agent.state);trace=removed['planner_trace'][-1]
        del trace['input']['context']['measured_conclusion'];trace['observation_digest']=digest(trace['input'])
        with self.assertRaisesRegex(ValueError,'实测代价|阶段提示'):
            verify_planning(removed,diagnosis)


if __name__=='__main__':unittest.main()
