"""Typed next actions, separate budget units and original-prose preservation."""
import copy,json,unittest
from unittest.mock import Mock
from traffic_agent.action_execution import (VERSION,LEGACY_VERSION,contract,description,factorial_sequence,
    native_budget,verify_context,verify_entry,wire_contract,generated_values)
from traffic_agent.feedback_planner import PlanningSession,decision_tool,verify_review_permissions
from traffic_agent.task import Task
from test_feedback_planner import reply


class ActionExecutionTests(unittest.TestCase):
    def fixture(self):
        task=Task.from_dict(dict(scenario='oneway',max_runs=4,demand_factors=[1],repeat_baseline=False)).as_dict()
        state=dict(id='j',task=task,run_count=1,runs=[dict(name='baseline',execution=dict(status='completed'))])
        options={'oneway_1_2':dict(title='单向完整对照',closed_direction=[1,2])}
        return state,options

    def choose(self,legacy=False,extra_follow_up=False,extra_summary=False,version=VERSION):
        state,options=self.fixture();observations=[dict(ref='j:baseline',run='baseline',changes=[])]
        context={} if legacy else dict(action_execution=contract(state,options,version=version))
        def respond(instructions,inputs,tools,choice):
            payload=json.loads(inputs[0]['content']);r=reply('oneway_1_2',['j:baseline'],payload)
            args=json.loads(r['calls'][0]['arguments'])
            if extra_follow_up:args['next_check']='接下来先执行单向，然后试停车1级组合。'
            if extra_summary:args['summary']='本轮换区位2，实测停车2级。'
            r['calls'][0]['arguments']=json.dumps(args);return r
        result=PlanningSession(Mock(request=Mock(side_effect=respond)),'原始交通问题').decide(
            'oneway_target','j',observations,options,context,allow_stop=False)
        return state,result

    def test_new_tool_has_one_action_definition_and_generates_provenance(self):
        state,entry=self.choose()
        self.assertEqual(entry['status'],'accepted')
        self.assertNotIn('next_check',decision_tool(entry['input'])['parameters']['properties'])
        self.assertNotIn('summary',decision_tool(entry['input'])['parameters']['properties'])
        self.assertEqual(entry['summary_origin'],VERSION)
        self.assertIn('禁行1→2，保留2→1',entry['summary'])
        self.assertIn('效果等待',entry['summary'])
        self.assertEqual(entry['next_check_origin'],VERSION)
        self.assertIn('停车2级、单向、停车2级＋单向',entry['next_check'])
        self.assertIn('禁行1→2，保留2→1',entry['next_check'])
        self.assertIn('3个原生场景',entry['next_check'])
        self.assertEqual(entry['input']['request'],'原始交通问题')
        verify_review_permissions(entry);verify_context(state,entry['input'])

    def test_conflicting_model_follow_up_is_rejected_not_rewritten(self):
        _,entry=self.choose(extra_follow_up=True)
        self.assertEqual(entry['status'],'rejected')
        self.assertEqual(entry['validation']['code'],'argument_fields')
        self.assertNotIn('next_check_origin',entry)
        self.assertEqual(entry['proposal_excerpt']['next_check'],'接下来先执行单向，然后试停车1级组合。')
        self.assertFalse(entry['proposal_capture']['excerpted'])

    def test_legacy_free_text_interface_and_original_words_stay_unchanged(self):
        _,entry=self.choose(legacy=True,extra_follow_up=True)
        self.assertEqual(entry['status'],'accepted')
        self.assertEqual(entry['next_check'],'接下来先执行单向，然后试停车1级组合。')
        self.assertNotIn('next_check_origin',entry)
        verify_review_permissions(entry)

    def test_v1_retains_its_model_summary_and_generated_follow_up(self):
        state,entry=self.choose(version=LEGACY_VERSION)
        self.assertEqual(entry['status'],'accepted')
        self.assertIn('summary',decision_tool(entry['input'])['parameters']['properties'])
        self.assertEqual(entry['summary'],'实测联合治理增加邻区耗时')
        self.assertNotIn('summary_origin',entry)
        self.assertEqual(entry['next_check_origin'],LEGACY_VERSION)
        verify_review_permissions(entry);verify_context(state,entry['input'])

    def test_unexpected_free_summary_is_preserved_and_never_rewritten_to_pass(self):
        _,entry=self.choose(extra_summary=True)
        self.assertEqual(entry['status'],'rejected')
        self.assertEqual(entry['validation']['code'],'argument_fields')
        self.assertEqual(entry['proposal_excerpt']['summary'],'本轮换区位2，实测停车2级。')
        self.assertNotIn('summary_origin',entry)
        self.assertFalse(entry['proposal_capture']['excerpted'])

    def test_new_action_summary_separates_future_parameters_from_measured_policy(self):
        from test_conclusion_review import observation
        from traffic_agent.conclusion_review import VERSION as CONCLUSION_VERSION
        task=Task.from_dict(dict(scenario='parking',max_runs=3,demand_factors=[1],repeat_baseline=False)).as_dict()
        state=dict(task=task,run_count=2,runs=[dict(execution=dict(status='completed'))]*2)
        options={'parking_next':dict(changes=[dict(from_node=3,to_node=2,parking=2),dict(from_node=2,to_node=4,parking=2)],region='area_2')}
        observed=observation('j:parking_1')
        context=dict(action_execution=contract(state,options),measured_conclusion=dict(version=CONCLUSION_VERSION))
        for wrong_reason in (False,True):
            def respond(instructions,inputs,tools,choice):
                payload=json.loads(inputs[0]['content']);result=reply('parking_next',['j:parking_1'],payload)
                args=json.loads(result['calls'][0]['arguments'])
                args['reason']='实际执行停车2级。' if wrong_reason else '另一区域尚有未测候选，需要新的配对结果。'
                result['calls'][0]['arguments']=json.dumps(args);return result
            client=Mock(request=Mock(side_effect=respond))
            entry=PlanningSession(client,'原始问题').decide('parking','j',[observed],options,context,allow_stop=False)
            with self.subTest(wrong_reason=wrong_reason):
                client.request.assert_called_once()
                if wrong_reason:
                    self.assertEqual(entry['status'],'rejected')
                    self.assertIn('executed_parking_level',{i['code'] for i in entry['conclusion_check']['issues']})
                    self.assertNotIn('summary',entry);self.assertNotIn('summary_origin',entry)
                    self.assertNotIn('next_check',entry)
                    self.assertEqual(entry['proposed_execution']['action'],'parking_next')
                    self.assertIn('3→2（停车2级）',entry['proposed_execution']['summary'])
                    verify_entry(entry)
                    tampered=copy.deepcopy(entry);tampered['proposed_execution']['summary']='已执行并已改善'
                    with self.assertRaisesRegex(ValueError,'被拒动作'):verify_entry(tampered)
                else:
                    self.assertEqual(entry['status'],'accepted')
                    self.assertIn('3→2（停车2级）、2→4（停车2级）',entry['summary'])
                    self.assertIn('等待本次新仿真',entry['summary'])
                    self.assertEqual(entry['input']['observations'][0]['changes'][0]['parking'],1)
                    verify_entry(entry);verify_context(state,entry['input'])

    def test_stop_and_handoff_summaries_do_not_invent_new_results(self):
        state,_=self.fixture()
        studies=[dict(id='study_2',job_id='next',task=state['task'])]
        options={'stop':{},'study_2':{}}
        payload=dict(allowed_actions=options,context=dict(action_execution=contract(state,options,studies)))
        for action,phrase in [('stop','结束新增试验'),('study_2','先计算该组独立新基准')]:
            text=generated_values(dict(action=action,input=payload))['summary']
            self.assertIn(phrase,text)

    def test_generated_text_cannot_be_tampered_or_presented_as_model_prose(self):
        _,entry=self.choose()
        for change in (lambda t:t.update(next_check='停车1级与组合'),lambda t:t.pop('next_check_origin'),
                       lambda t:t.update(summary='已经实测有效'),lambda t:t.pop('summary_origin')):
            candidate=copy.deepcopy(entry);change(candidate)
            with self.assertRaisesRegex(ValueError,'执行说明'):verify_entry(candidate)

    def test_levels_scene_order_and_budget_are_rebuilt_from_actual_task(self):
        state,entry=self.choose()
        mutations=[lambda s:s['actions']['oneway_1_2']['sequence'][0].update(parking_level=1),
                   lambda s:s['actions']['oneway_1_2']['sequence'].reverse(),
                   lambda s:s['native_budget']['current_study'].update(remaining=2)]
        for change in mutations:
            payload=copy.deepcopy(entry['input']);change(payload['context']['action_execution'])
            with self.assertRaisesRegex(ValueError,'实际执行定义'):verify_context(state,payload)
        state['action_execution_protocol']=LEGACY_VERSION
        with self.assertRaisesRegex(ValueError,'协议'):verify_context(state,entry['input'])

    def test_new_study_has_independent_baseline_and_own_fixed_parking_level(self):
        state,options=self.fixture()
        parking=Task.from_dict(dict(scenario='parking',demand_factors=[1],repeat_baseline=False,max_runs=3)).as_dict()
        owner=dict(state,id='p',task=parking,run_count=3,runs=[dict(execution=dict(status='completed'))]*3)
        studies=[dict(id='study_1',job_id='p',task=parking),dict(id='study_2',job_id='j',task=state['task'])]
        unopened=dict(state,run_count=0,runs=[])
        spec=contract(owner,{'study_2':{}},studies,{'p':owner,'j':unopened})
        action=spec['actions']['study_2']
        self.assertTrue(action['independent_baseline'])
        self.assertEqual([r['name'] for r in action['sequence']],['baseline','S1_parking','S2_oneway','S3_combined'])
        self.assertIn('停车2级',description(action))
        self.assertEqual(spec['native_budget']['programme_allocated']['remaining'],4)
        verify_context(owner,dict(allowed_actions={'study_2':{}},context=dict(action_execution=spec)),studies,{'p':owner,'j':unopened})

    def test_failed_native_attempts_use_scenes_without_becoming_completed(self):
        state,_=self.fixture();state['run_count']=2
        budget=native_budget(state)
        self.assertEqual((budget['used'],budget['completed'],budget['remaining']),(2,1,2))
        self.assertEqual(budget['unit'],'native_scene')

    def test_demand_grid_and_repeat_each_cost_a_real_native_scene(self):
        state,_=self.fixture();state['task'].update(demand_factors=[.9,1,1.1],repeat_baseline=True,max_runs=13)
        sequence=factorial_sequence(state['task'])
        self.assertEqual(len(sequence),13)
        self.assertEqual([r['demand_factor'] for r in sequence[:4]],[1]*4)
        self.assertEqual(sequence[-1]['name'],'baseline_repeat')
        self.assertEqual(len(factorial_sequence(state['task'],baseline_done=True)),12)

    def test_wire_sharing_reconstructs_every_action_without_changing_saved_input(self):
        state,options=self.fixture();options['oneway_2_1']=dict(closed_direction=[2,1])
        original=contract(state,options);saved=copy.deepcopy(original)
        wire=wire_contract(original);shared=wire.pop('shared_action_fields')
        for key,row in wire['actions'].items():wire['actions'][key]={**shared,**row}
        self.assertEqual(wire,original)
        self.assertEqual(original,saved)
        self.assertNotIn('closed_direction',shared)
        self.assertNotIn('retained_direction',shared)


if __name__=='__main__':unittest.main()
