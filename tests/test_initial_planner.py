"""Local transport fixtures: no external API, credentials or native execution."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import URLError

from traffic_agent.dispatch import Dispatcher
from traffic_agent.feedback_planner import PlanningSession, digest
from traffic_agent.initial_planner import evidence, verify_initial
from traffic_agent.llm_client import ChatCompletionsClient, ResponsesClient, ModelError
from traffic_agent.llm_settings import DEFAULTS
from traffic_agent.planning_attempt import listing
from traffic_agent.runner import now
from traffic_agent.task import Task
from test_natural_plan import CATALOG, experiment, model_reply
from test_feedback_planner import reply as feedback_reply
from scripts.validate_observed_planning import PROGRAMME_REQUEST

REQUEST='比较两个不同区域的停车治理效果，不比较强度，最多治理1条，需求系数：1；最多3次仿真，不复跑'
CAT=dict(CATALOG,matched_roads=[],targets={})
PRIVATE='UNIT_PRIVATE_REASONING_ONLY'
MIXED_CAT=dict(matched_roads=['七东线','长江村路'],road_names={'七东线','长江村路'},targets={})


def mixed_studies(extra_diagnosis=False):
    studies=[experiment(['parking'],road='七东线'),experiment(['parking','oneway'],road='长江村路')]
    return [experiment([],road='七东线',study='diagnosis'),studies[0],
            experiment([],road='长江村路',study='diagnosis'),studies[1]] if extra_diagnosis else studies


def wrong_prerequisite():
    return model_reply([],missing_information=[dict(need='unresolved_location',
        source_quote='比较两个不同区域的停车治理效果',question='请先提供两个道路名称。')])


def wire_response(value,wire='chat_completions',ordinal=1,tokens=100):
    call=value['calls'][0]
    if wire=='chat_completions':
        data=dict(id=f'local_{ordinal}',model='deepseek-v4-flash',choices=[dict(finish_reason='tool_calls',
            message=dict(role='assistant',content=None,reasoning_content=PRIVATE,tool_calls=[dict(
                id=f'call_{ordinal}',type='function',function=dict(name=call['name'],arguments=call['arguments']))]))])
    else:
        data=dict(id=f'local_{ordinal}',status='completed',output=[dict(type='reasoning',id=f'reason_{ordinal}',summary=[],encrypted_content=PRIVATE),
            dict(type='function_call',call_id=f'call_{ordinal}',name=call['name'],arguments=call['arguments'])])
    if tokens is not None:data['usage']=dict(total_tokens=tokens)
    return io.BytesIO(json.dumps(data).encode())


class InitialPlannerTests(unittest.TestCase):
    def test_generic_congestion_invalid_turn_target_gets_one_bounded_correction(self):
        request='帮我治理一下环洲路的拥堵情况\n'
        catalog=dict(matched_roads=['环洲路'],road_names={'环洲路'},targets={
            'T_1_2_3':dict(kind='turn',nodes=[1,2,3],road='环洲路',topology_checked=True)})
        for invalid in ('auto','T_invented'):
            with self.subTest(target=invalid):
                first=[experiment(['parking'],road='环洲路'),experiment(['parking','oneway'],road='环洲路'),
                       experiment(['parking','turn'],road='环洲路',target=invalid)]
                corrected=copy.deepcopy(first);corrected[-1]['target_ref']='T_1_2_3'
                self.setup_preview([model_reply(first),model_reply(corrected)],catalog=catalog)
                result=self.preview(request)
                self.assertEqual(result['status'],'ready')
                self.assertEqual(result['request'],request)
                self.assertEqual(result['experiments'][-1]['task']['turn'],[1,2,3])
                self.assertEqual(result['model_budget']['used'],2)
                self.assertLessEqual(result['model_budget']['minimum_required'],7)
                self.assertLessEqual(result['allocated_runs'],15)
                self.assertEqual([a['status'] for a in result['initial_planning_attempts']],['validation_failed','ready'])
                self.assertEqual(result['initial_planning_attempts'][0]['validation_code'],'invalid_prerequisite')
                feedback=result['initial_planning_attempts'][1]['validation_feedback']
                self.assertIn('禁转',feedback['explanation'])
                self.assertEqual(feedback['execution_scope']['target_resolution']['turn']['available_refs'],['T_1_2_3'])
                self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
                self.opener.open.assert_called();self.assertEqual(self.opener.open.call_count,2)
                self.native.assert_not_called()
                patch.stopall()

    def test_unrequested_invalid_turn_can_be_withdrawn_without_questioning_user(self):
        request='帮我治理一下环洲路的拥堵情况'
        catalog=dict(matched_roads=['环洲路'],road_names={'环洲路'},targets={})
        self.setup_preview([model_reply([experiment(['parking','turn'],road='环洲路')]),
                            model_reply([experiment(['parking'],road='环洲路')])],catalog=catalog)
        result=self.preview(request)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['request'],request)
        self.assertEqual(result['task']['scenario'],'parking')
        self.assertFalse(result['missing'])
        self.assertEqual(result['model_budget']['used'],2)
        self.native.assert_not_called()

    def test_repeated_invalid_turn_target_stops_at_two_requests(self):
        request='帮我治理一下环洲路的拥堵情况'
        catalog=dict(matched_roads=['环洲路'],road_names={'环洲路'},targets={})
        invalid=model_reply([experiment(['parking','turn'],road='环洲路')])
        self.setup_preview([invalid,invalid,model_reply([experiment(['parking'],road='环洲路')])],catalog=catalog)
        with self.assertRaisesRegex(ModelError,'禁转'):self.preview(request)
        self.assertEqual(self.opener.open.call_count,2)
        self.assertEqual([r['status'] for r in self.records()],['validation_failed','validation_failed'])
        self.native.assert_not_called()

    def test_turn_mentions_keep_strict_stop_without_withdrawing_user_conditions(self):
        catalog=dict(matched_roads=['环洲路'],road_names={'环洲路'},targets={
            'T_1_2_3':dict(kind='turn',nodes=[1,2,3],road='环洲路',topology_checked=True)})
        for condition in ('必须研究停车和禁转组合','不要研究禁转，只研究停车','转向：1→2→3'):
            request='请治理环洲路拥堵，'+condition+'，最多15次仿真，不复跑'
            with self.subTest(condition=condition):
                self.setup_preview([model_reply([experiment(['parking','turn'],road='环洲路')]),
                                    model_reply([experiment(['parking'],road='环洲路')])],catalog=catalog)
                with self.assertRaisesRegex(ModelError,'禁转目标无效'):self.preview(request)
                self.assertEqual(self.opener.open.call_count,1)
                self.assertEqual(len(self.records()),1)
                self.native.assert_not_called()
                patch.stopall()

    def test_optional_transport_annotation_does_not_invalidate_older_accounting(self):
        self.setup_preview([model_reply([experiment(['parking'])])])
        result=self.preview(REQUEST)
        result['model_usage']['request_thinking_mode']='disabled'
        plan=dict(created=now(),initial_planning_digest=digest(evidence(result)))
        self.assertEqual(verify_initial(result,plan),1)
        # The compatibility projection cannot hide token-count differences.
        result['model_usage']['tokens']+=1
        plan['initial_planning_digest']=digest(evidence(result))
        with self.assertRaisesRegex(ValueError,'初始规划用量'):
            verify_initial(result,plan)

    def test_unrelated_unsupported_branch_gets_one_bounded_repair(self):
        request='长江村路停车拥堵，比较至少两个停车备选方案，最多3次仿真，不复跑'
        self.setup_preview([model_reply([],unsupported_requests=['transit']),
                            model_reply([experiment(['parking'])])],catalog=CATALOG)
        result=self.preview(request)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['experiments'][0]['task']['comparison_policies'],2)
        self.assertEqual([x['status'] for x in result['initial_planning_attempts']],['validation_failed','ready'])
        self.assertEqual(result['initial_planning_attempts'][0]['validation_code'],'invalid_prerequisite')
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.native.assert_not_called()

    def test_demand_depth_gets_bounded_pre_execution_correction(self):
        request='长江村路停车治理，检验需求变化下是否仍有效，最多10次仿真，不复跑'
        self.setup_preview([model_reply([experiment(['parking'])]),
                            model_reply([experiment(['parking'],demand_profile='variation')])],catalog=CATALOG)
        result=self.preview(request)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['experiments'][0]['task']['demand_factors'],[0.9,1.0,1.1])
        self.assertEqual([a['status'] for a in result['initial_planning_attempts']],['validation_failed','ready'])
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.native.assert_not_called()

    def test_missing_explicit_measurement_gets_one_full_plan_correction(self):
        request=('独立研究长江村路的停车治理、单向组织及两者组合。分别检验原生禁行控制是否生效、'
                 '冻结核心耗时是否下降、邻区代价是否在原定门槛内。最多4次仿真，不复跑。')
        assessments=[
            dict(hypothesis='长江村路单向与组合的原生禁行控制生效',evidence_needed='原生方向流量响应',
                 criterion=dict(test='control_response',studies=[1],policies='control_and_combined',quantifier='all')),
            dict(hypothesis='长江村路停车、单向及组合每种措施核心耗时均下降',evidence_needed='同需求核心耗时',
                 criterion=dict(test='core_time_decrease',studies=[1],policies='all',quantifier='all')),
            dict(hypothesis='长江村路停车、单向及组合每种措施邻区代价均在原定门槛内',evidence_needed='邻区配对指标',
                 criterion=dict(test='neighbor_limits',studies=[1],policies='all',quantifier='all'))]
        self.setup_preview([model_reply([experiment()]),model_reply([experiment()],assessment=assessments)],catalog=CATALOG)
        result=self.preview(request)
        self.assertEqual(result['status'],'ready')
        self.assertEqual([a['status'] for a in result['initial_planning_attempts']],['validation_failed','ready'])
        self.assertEqual(result['initial_planning_attempts'][0]['validation_code'],'invalid_prerequisite')
        self.assertEqual(result['request'],request)
        self.assertEqual([x['criterion']['test'] for x in result['assessment']],
                         ['control_response','core_time_decrease','neighbor_limits'])
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
        self.native.assert_not_called()
        self.assertEqual(self.opener.open.call_count,2)

    def setup_preview(self,values,wire='chat_completions',tokens=None,catalog=CAT):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.opener=Mock()
        self.opener.open.side_effect=[v if isinstance(v,Exception) else wire_response(v,wire,i+1,100 if tokens is None else tokens[i]) for i,v in enumerate(values)]
        config=dict(DEFAULTS,wire_api=wire)
        self.client=(ChatCompletionsClient if wire=='chat_completions' else ResponsesClient)(config,'UNIT_TEST_ONLY',self.opener)
        self.manager=Dispatcher(self.root/'source',self.root,Mock(revision=Mock(return_value=1),credentials=Mock(return_value=(config,'UNIT_TEST_ONLY'))))
        for target,value in [('traffic_agent.dispatch.client_for',self.client),('traffic_agent.dispatch.input_fingerprints',{}),
                             ('traffic_agent.natural_plan.context_for',catalog)]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        self.native=patch('traffic_agent.dispatch.Agent').start();self.addCleanup(patch.stopall)
        return self.manager

    def preview(self,text=REQUEST):
        return self.manager.preview(dict(request=text,matcher='model'))

    def records(self):
        return sorted([json.loads(p.read_text('utf-8')) for p in (self.root/'planning_attempts').glob('*.json')],key=lambda a:a['ordinal'])

    def test_correction_round_trips_actual_tool_history_and_preserves_failed_draft(self):
        for wire in ['chat_completions','responses']:
            with self.subTest(wire=wire):
                self.setup_preview([wrong_prerequisite(),model_reply([experiment(['parking'])])],wire,tokens=[80,120])
                result=self.preview();self.native.assert_not_called()
                self.assertEqual(result['status'],'ready')
                self.assertEqual(result['task']['comparison_regions'],2)
                self.assertEqual(result['allocated_runs'],3)
                self.assertEqual(result['model_budget'],dict(limit=7,used=2,remaining=5,minimum_required=5,known_tokens=200,unknown_usage=0))
                self.assertFalse((self.root/'jobs').exists())
                bodies=[json.loads(c.args[0].data) for c in self.opener.open.call_args_list]
                self.assertEqual(len(bodies),2)
                first,second=[b['messages'][1:] if wire=='chat_completions' else b['input'] for b in bodies]
                self.assertEqual(second[0],first[0]);self.assertIn(PRIVATE,json.dumps(second))
                feedback=json.loads(second[-1]['content' if wire=='chat_completions' else 'output'])
                self.assertEqual(feedback['code'],'invalid_prerequisite')
                self.assertFalse(feedback['native_started'])
                self.assertEqual(feedback['execution_scope'],result['execution_scope'])
                self.assertEqual(feedback['model_requests_used'],1)
                self.assertEqual(second[-1]['tool_call_id' if wire=='chat_completions' else 'call_id'],'call_1')
                self.assertEqual(bodies[0]['tools'],bodies[1]['tools'])
                self.assertEqual(bodies[1]['tool_choice'],'required' if wire=='chat_completions' else dict(type='function',name='compose_traffic_plan'))
                records=self.records()
                self.assertEqual([r['status'] for r in records],['validation_failed','ready'])
                self.assertEqual(records[1]['corrects'],records[0]['id'])
                self.assertEqual(result['initial_planning_attempts'],records)
                stored=json.dumps(result)+''.join(p.read_text('utf-8') for p in self.root.rglob('*.json'))
                self.assertNotIn(PRIVATE,stored);self.assertNotIn('UNIT_TEST_ONLY',stored)
                self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
                # An identical preview uses its existing ticket; no third request.
                before={p:p.read_bytes() for p in (self.root/'planning_attempts').glob('*.json')}
                self.assertEqual(self.preview(),result)
                self.assertEqual(self.opener.open.call_count,2)
                self.assertEqual(before,{p:p.read_bytes() for p in before})
                patch.stopall()

    def test_second_invalid_decision_stops_without_a_third_call_or_native_launch(self):
        self.setup_preview([wrong_prerequisite(),wrong_prerequisite(),model_reply([experiment(['parking'])])])
        with self.assertRaisesRegex(ModelError,'不对应本次开放的定位缺口'):self.preview()
        self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
        self.assertEqual([r['status'] for r in self.records()],['validation_failed','validation_failed'])
        shown=listing(self.root)
        self.assertEqual({r['phase'] for r in shown},{'initial','validation_correction'})
        self.assertTrue(all(r['requested'] for r in shown))

    def test_network_placeholder_and_missing_transport_history_do_not_retry(self):
        cases=[URLError('local network fixture'),model_reply([experiment(['parking'])],goal='test')]
        for value in cases:
            with self.subTest(value=type(value).__name__):
                self.setup_preview([value,model_reply([experiment(['parking'])])])
                with self.assertRaises(ModelError):self.preview()
                self.assertEqual(self.opener.open.call_count,1);self.native.assert_not_called()
                self.assertEqual(len(self.records()),1)
                patch.stopall()
        self.setup_preview([])
        self.client.request=Mock(return_value=wrong_prerequisite())  # No call ID/output to continue.
        with self.assertRaises(ModelError):self.preview()
        self.client.request.assert_called_once();self.native.assert_not_called()

    def test_real_prerequisite_or_unsupported_capability_never_triggers_correction(self):
        for text,value,status in [('请研究学校门口拥堵',model_reply([],missing_information=[dict(need='unresolved_location',source_quote='请研究学校门口拥堵',question='请明确学校位置')]), 'needs_input'),
                                  ('请优化信号配时',model_reply([],unsupported_requests=['signal']),'needs_validation')]:
            with self.subTest(status=status):
                self.setup_preview([value])
                self.assertEqual(self.preview(text)['status'],status)
                self.assertEqual(self.opener.open.call_count,1);self.native.assert_not_called()
                patch.stopall()

    def test_seventh_request_slot_preserves_required_comparisons_after_correction(self):
        self.setup_preview([wrong_prerequisite(),model_reply([experiment(['parking'])])])
        text=REQUEST.replace('不比较强度','并比较强度').replace('最多3次','最多5次')
        result=self.preview(text)
        self.assertEqual(result['status'],'ready');self.assertEqual(result['model_budget']['minimum_required'],7)
        self.assertEqual(result['model_budget']['limit'],7);self.assertEqual(result['budget'],5)
        self.assertEqual(len(result['initial_model_usages']),2)
        self.native.assert_not_called();self.assertEqual(self.opener.open.call_count,2)

    def test_unknown_usage_and_correction_both_count_toward_six_request_limit(self):
        self.setup_preview([wrong_prerequisite(),model_reply([experiment(['parking'])])],tokens=[None,120])
        result=self.preview()
        client=Mock(request=Mock(return_value=feedback_reply('stop',['j:baseline'])))
        session=PlanningSession(client,REQUEST,initial_usages=result['initial_model_usages'],limit=6,risk_review_protocol=None)
        for _ in range(5):session.decide('route','j',[dict(ref='j:baseline')],{})
        self.assertEqual(client.request.call_count,4)
        self.assertEqual(session.used,6)
        self.assertEqual(session.budget()['known_tokens'],440)
        self.assertEqual(session.budget()['unknown_usage'],1)
        self.assertFalse(session.trace[-1]['requested'])

    def test_launch_freezes_both_initial_requests_before_any_native_work(self):
        self.setup_preview([wrong_prerequisite(),model_reply([experiment(['parking'])])])
        result=self.preview()
        agent=Mock(job_id='LOCAL_INITIAL_REVIEW',job=self.root/'jobs/LOCAL_INITIAL_REVIEW',
                   task=Task.from_dict(result['task']),state=dict(status='created',runs=[],run_count=0))
        self.native.return_value=agent
        agent.run.side_effect=RuntimeError('synthetic stop before native execution')
        launched=self.manager.launch(result['ticket']);self.manager.active.join(5)
        self.assertFalse(self.manager.active.is_alive())
        saved=json.loads((self.root/'dispatches'/f"{launched['id']}.json").read_text('utf-8'))
        plan=json.loads((self.root/'programmes'/launched['id']/'plan.json').read_text('utf-8'))
        self.assertEqual(saved['status'],'failed')
        self.assertEqual(saved['model_budget']['used'],2)
        self.assertEqual(agent.planner.used,2)
        self.assertEqual(verify_initial(saved,plan),2)
        self.assertEqual(self.manager.listing()[0]['initial_planning_attempts'],result['initial_planning_attempts'])
        self.assertEqual(self.opener.open.call_count,2)

    def test_initial_audit_rejects_missing_evidence_wrong_chronology_or_changed_usage(self):
        self.setup_preview([wrong_prerequisite(),model_reply([experiment(['parking'])])])
        result=self.preview();plan=dict(created=now(),initial_planning_digest=digest(evidence(result)))
        self.assertEqual(verify_initial({},{}),1)  # Historical one-call protocol.
        bad=copy.deepcopy(result);bad.pop('initial_review_protocol')
        with self.assertRaises(ValueError):verify_initial(bad,plan)
        mutations=[lambda r:r['initial_planning_attempts'].append(r['initial_planning_attempts'][0]),
            lambda r:r['initial_planning_attempts'].__setitem__(0,None),
            lambda r:r['initial_planning_attempts'][1].update(corrects='different'),
            lambda r:r['initial_planning_attempts'][1].update(started='2000-01-01T00:00:00+00:00'),
            lambda r:r['initial_planning_attempts'][1].update(session_id=None),
            lambda r:r['initial_planning_attempts'][1].update(validation_feedback={}),
            lambda r:r['initial_model_usages'][0].update(tokens=999)]
        for mutate in mutations:
            bad=json.loads(json.dumps(result));mutate(bad)
            with self.assertRaises(ValueError):verify_initial(bad,plan)
            with self.assertRaises(ValueError):verify_initial(bad,dict(plan,initial_planning_digest=digest(evidence(bad))))

    def test_budget_correction_preserves_controls_and_has_real_tool_result_history(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                self.setup_preview([model_reply(mixed_studies(True)),model_reply(mixed_studies())],wire,catalog=MIXED_CAT)
                result=self.preview(PROGRAMME_REQUEST);records=self.records()
                self.assertEqual(result['status'],'ready');self.native.assert_not_called()
                self.assertEqual(self.opener.open.call_count,2)
                first,last=[r['budget_check'] for r in records]
                self.assertEqual(first['native'],dict(required=7,limit=6))
                self.assertEqual([g['native_runs'] for g in first['groups']],[2,1,4])
                self.assertEqual([g['may_omit'] for g in first['groups']],[False,True,False])
                self.assertEqual([g['assignment_module'] for g in last['groups']],['M6AssignVehicle.exe','M6AssignVehicle1.exe'])
                self.assertEqual(last['native'],dict(required=6,limit=6))
                self.assertEqual(last['model'],dict(required=6,used=2,limit=7))
                self.assertEqual(result['allocated_runs'],6)
                self.assertEqual(result['model_budget']['minimum_required'],6)
                body=json.loads(self.opener.open.call_args.args[0].data)
                messages=body['messages'][1:] if wire=='chat_completions' else body['input']
                feedback=json.loads(messages[-1]['content' if wire=='chat_completions' else 'output'])
                self.assertEqual(feedback['code'],'draft_over_budget')
                self.assertEqual(feedback['budget_check'],first)
                self.assertEqual(feedback['model_requests_used'],1)
                self.assertIn(PRIVATE,json.dumps(messages))
                self.assertNotIn(PRIVATE,json.dumps(records))
                self.assertEqual([r['status'] for r in records],['needs_input','ready'])
                self.assertEqual([len(r['proposal']['studies']) for r in records],[4,2])
                self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
                self.assertEqual(listing(self.root)[0]['budget_check']['native']['required'],6)
                patch.stopall()

    def test_extra_factorial_parking_draft_can_be_withdrawn_by_a_new_model_reply(self):
        for wire in ('chat_completions','responses'):
            with self.subTest(wire=wire):
                draft=[mixed_studies()[0],experiment(['parking'],road='长江村路'),mixed_studies()[1]]
                self.setup_preview([model_reply(draft),model_reply(mixed_studies())],wire,catalog=MIXED_CAT)
                result=self.preview(PROGRAMME_REQUEST);first,last=self.records()
                self.assertEqual(result['status'],'ready');self.native.assert_not_called()
                check=first['budget_check']
                self.assertEqual(check['native'],dict(required=8,limit=6))
                self.assertEqual([g['may_omit'] for g in check['groups']],[False,True,False])
                omission=check['groups'][1]['omission']
                self.assertIn(omission['source_quote'],PROGRAMME_REQUEST)
                self.assertEqual(omission['covered_by_ordinal'],3)
                self.assertEqual([g['conditions']['scenario'] for g in last['budget_check']['groups']],['parking','oneway'])
                self.assertEqual(result['allocated_runs'],6)
                self.assertEqual(result['model_budget']['minimum_required'],6)
                self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)
                for changed in ('quote','cover','permission'):
                    bad=copy.deepcopy(result);f,l=bad['initial_planning_attempts']
                    row=f['budget_check']['groups'][1]
                    if changed=='quote':row['omission']['source_quote']='不是原文'
                    elif changed=='cover':row['omission']['covered_by_ordinal']=1
                    else:f['budget_check']['groups'][0]['may_omit']=True
                    l['validation_feedback']['budget_check']=copy.deepcopy(f['budget_check'])
                    with self.subTest(changed=changed),self.assertRaises(ValueError):
                        verify_initial(bad,dict(created=now(),initial_planning_digest=digest(evidence(bad))))
                first_request=json.loads(self.opener.open.call_args_list[0].args[0].data)
                messages=first_request['messages'][1:] if wire=='chat_completions' else first_request['input']
                units=json.loads(messages[0]['content'])['study_units']
                self.assertEqual([u['native_scenes_at_given_factors'] for u in units['units']],[1,2,4,4])
                self.assertTrue(all(u['complete_within_one_group'] and not u['uses_other_group_baseline_or_target'] for u in units['units']))
                patch.stopall()

    def test_explicit_extra_research_or_ambiguous_combination_stays_protected(self):
        draft=[mixed_studies()[0],experiment(['parking'],road='长江村路'),mixed_studies()[1]]
        texts=[PROGRAMME_REQUEST+' 另外独立研究长江村路停车。',
            PROGRAMME_REQUEST+' 再研究长江村路的停车。',
            PROGRAMME_REQUEST+' 要比较不同引擎的停车分配。',
            PROGRAMME_REQUEST.replace('单向组织与停车组合','单向组织'),
            PROGRAMME_REQUEST.replace('单向组织与停车组合','不研究单向组织与停车组合'),
            PROGRAMME_REQUEST.replace('单向组织与停车组合','不采用单向组织与停车组合'),
            PROGRAMME_REQUEST.replace('不比较停车强度','比较停车强度'),
            PROGRAMME_REQUEST.replace('总预算6','总预算5')]
        for text in texts:
            with self.subTest(text=text):
                self.setup_preview([model_reply(draft)],catalog=MIXED_CAT)
                result=self.preview(text)
                self.assertEqual(result['status'],'needs_input')
                self.assertFalse(result['budget_check']['correction_feasible'])
                self.opener.open.assert_called_once();self.native.assert_not_called()
                patch.stopall()

    def test_budget_repair_stops_after_one_correction_and_never_launches_an_unfit_plan(self):
        self.setup_preview([model_reply(mixed_studies(True))]*3,catalog=MIXED_CAT)
        result=self.preview(PROGRAMME_REQUEST)
        self.assertEqual(result['status'],'needs_input')
        self.assertEqual(result['experiments'],[])
        self.assertEqual(len(result['initial_planning_attempts']),2)
        self.assertEqual(self.opener.open.call_count,2)
        with self.assertRaisesRegex(ValueError,'未通过'):self.manager.launch(result['ticket'])
        self.native.assert_not_called()

    def test_component_permission_requires_same_conditions_and_keeps_legacy_costs(self):
        from traffic_agent.natural_plan import limits
        from traffic_agent.planning_budget import initial_costs,verify_initial_costs,LEGACY_INITIAL_VERSION
        draft=[mixed_studies()[0],experiment(['parking'],road='长江村路'),mixed_studies()[1]]
        self.setup_preview([model_reply(draft),model_reply(mixed_studies())],catalog=MIXED_CAT)
        self.preview(PROGRAMME_REQUEST)
        first=self.records()[0]['budget_check'];config=limits(PROGRAMME_REQUEST)
        tasks=[dict(g['conditions'],request=PROGRAMME_REQUEST) for g in first['groups']]
        entries=[{k:g[k] for k in ('study','title','target_ref')} for g in first['groups']]
        legacy=initial_costs(tasks,entries,config,1,PROGRAMME_REQUEST,version=LEGACY_INITIAL_VERSION)
        verify_initial_costs(legacy,PROGRAMME_REQUEST)
        self.assertEqual([g['may_omit'] for g in legacy['groups']],[False]*3)
        self.assertFalse(legacy['correction_feasible'])
        for field,value in [('demand_factors',[.9,1]),('max_links',5),('repeat_baseline',True),
                            ('neighbor_hops',3),('max_neighbor_time_increase_pct',0),
                            ('max_new_network_oversaturated_km',0)]:
            with self.subTest(field=field):
                changed=copy.deepcopy(tasks);changed[1][field]=value
                check=initial_costs(changed,entries,config,1,PROGRAMME_REQUEST)
                self.assertFalse(check['groups'][1]['may_omit'])
                self.assertFalse(check['correction_feasible'])
        changed=copy.deepcopy(entries);changed[1]['target_ref']='L1'
        self.assertFalse(initial_costs(tasks,changed,config,1,PROGRAMME_REQUEST)['groups'][1]['may_omit'])
        turn_tasks=copy.deepcopy(tasks);turn_tasks[-1]['scenario']='circulation'
        turn_entries=copy.deepcopy(entries);turn_entries[-1]['target_ref']='T1'
        turn_request=PROGRAMME_REQUEST.replace('单向','禁转')
        turn_check=initial_costs(turn_tasks,turn_entries,config,1,turn_request)
        self.assertTrue(turn_check['groups'][1]['may_omit'])
        self.assertTrue(turn_check['correction_feasible'])
        self.assertEqual(turn_check['native'],dict(required=8,limit=6))
        self.native.assert_not_called()

    def test_impossible_budget_explicit_diagnosis_and_missing_history_do_not_spend_correction(self):
        for text in (PROGRAMME_REQUEST.replace('总预算6','总预算5'),PROGRAMME_REQUEST+'另要求长江村路独立体检。'):
            self.setup_preview([model_reply(mixed_studies(True))],catalog=MIXED_CAT)
            result=self.preview(text)
            self.assertEqual(result['status'],'needs_input')
            self.assertFalse(result['budget_check']['correction_feasible'])
            self.assertEqual(self.opener.open.call_count,1);self.native.assert_not_called()
            patch.stopall()
        self.setup_preview([],catalog=MIXED_CAT)
        self.client.request=Mock(return_value=model_reply(mixed_studies(True)))
        self.assertEqual(self.preview(PROGRAMME_REQUEST)['status'],'needs_input')
        self.client.request.assert_called_once();self.native.assert_not_called()

    def test_budget_repair_cannot_drop_controls_or_weaken_a_research_design(self):
        alternatives=[mixed_studies()[:1]+[experiment(['parking'],road='长江村路')],
                      mixed_studies()[:1]+[experiment([],road='长江村路',study='diagnosis')]]
        for revised in alternatives:
            self.setup_preview([model_reply(mixed_studies(True)),model_reply(revised)],catalog=MIXED_CAT)
            with self.assertRaisesRegex(ValueError,'需保留的研究'):self.preview(PROGRAMME_REQUEST)
            self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
            self.assertEqual(self.records()[-1]['status'],'validation_failed')
            patch.stopall()

    def test_budget_correction_failure_and_prerequisite_correction_share_one_slot(self):
        self.setup_preview([model_reply(mixed_studies(True)),URLError('local network fixture')],catalog=MIXED_CAT)
        with self.assertRaises(ModelError):self.preview(PROGRAMME_REQUEST)
        self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()
        self.assertEqual(self.records()[-1]['status'],'model_failed')
        patch.stopall()
        self.setup_preview([wrong_prerequisite(),model_reply(mixed_studies(True))],catalog=MIXED_CAT)
        result=self.preview(PROGRAMME_REQUEST)
        self.assertEqual(result['status'],'needs_input')
        self.assertEqual(self.opener.open.call_count,2);self.native.assert_not_called()

    def test_budget_audit_recalculates_permissions_costs_and_execution_conditions(self):
        self.setup_preview([model_reply(mixed_studies(True)),model_reply(mixed_studies())],catalog=MIXED_CAT)
        result=self.preview(PROGRAMME_REQUEST)
        for changed in ('cost','omission','protected','executed','protocol'):
            bad=copy.deepcopy(result);first,last=bad['initial_planning_attempts']
            if changed=='cost':last['budget_check']['model']['required']-=1
            elif changed=='omission':first['budget_check']['groups'][0]['may_omit']=True
            elif changed=='protected':last['budget_check']['groups'][-1]['conditions']['scenario']='parking'
            elif changed=='executed':bad['experiments'][-1]['task']['max_links']=5
            else:bad['initial_review_protocol']='initial-plan-review-v1'
            last['validation_feedback']['budget_check']=copy.deepcopy(first['budget_check'])
            with self.subTest(changed=changed),self.assertRaises(ValueError):
                verify_initial(bad,dict(created=now(),initial_planning_digest=digest(evidence(bad))))

    def test_legacy_prerequisite_audit_remains_valid_under_original_protocol(self):
        self.setup_preview([wrong_prerequisite(),model_reply([experiment(['parking'])])])
        result=self.preview();result['initial_review_protocol']='initial-plan-review-v1'
        result['initial_planning_attempts'][1]['validation_feedback']['model_request_limit']=6
        self.assertEqual(verify_initial(result,dict(created=now(),initial_planning_digest=digest(evidence(result)))),2)


if __name__=='__main__':unittest.main()
