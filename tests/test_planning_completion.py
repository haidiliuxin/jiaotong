"""End-to-end controller checks; no paid requests or native fixture reuse."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics
from traffic_agent.feedback_planner import PlanningSession, parking_options, digest
from traffic_agent.planning_review import coverage, parking_gate, minimum_model_requests, public_stop_basis
from traffic_agent.planning_audit import verify_planning
from traffic_agent.task import Task
from traffic_agent.runner import now
from test_agent import link
from test_feedback_planner import reply


class CompletionTests(unittest.TestCase):
    def test_public_stop_basis_uses_frozen_gate_without_rewriting_model_evidence(self):
        turn=dict(status='accepted',action='stop',reason='required_candidate_comparisons=2已测齐',
                  input=dict(context=dict(completion_guard=dict(stop_allowed=True,
                      required_candidate_comparisons=2,measured_candidate_comparisons=2,
                      strength_comparison_required=False))))
        self.assertIn('已完成2个',public_stop_basis(turn))
        self.assertIn('未测等级不算已验证',public_stop_basis(turn))
        self.assertEqual(turn['reason'],'required_candidate_comparisons=2已测齐')
        self.assertIsNone(public_stop_basis(dict(turn,status='rejected')))
        self.assertIsNone(public_stop_basis(dict(turn,input=dict(context=dict(completion_guard=dict(stop_allowed=False))))))

    def test_strength_obligation_is_explicit_and_old_guard_remains_reproducible(self):
        for required in (False,True):
            task=Task.from_dict(dict(request='比较停车强度' if required else '不比较停车强度；只测一项措施',
                                    max_runs=3,repeat_baseline=False,demand_factors=[1])).as_dict()
            state=dict(id='offline',task=task,runs=[],run_count=0,planning_protocol='observed-planning-v3')
            options=parking_options([link(0,1,2)],set())
            offered,guard=parking_gate(state,options,3,1,4)
            legacy_offered,legacy=parking_gate(state,options,3,1,4,version=None)
            self.assertEqual(offered,legacy_offered)
            self.assertIs(guard['strength_comparison_required'],required)
            self.assertEqual(guard['required_policy_decisions'],2 if required else 1)
            self.assertNotIn('version',legacy)
            self.assertNotIn('strength_comparison_required',legacy)
            self.assertIn('同方向对照',legacy['reason'])
            self.assertIn('要求同方向' if required else '未要求1/2级',guard['reason'])

    def exercise(self, factors=(1,), premature=False, failure=False, final_failure=False, no_gain=False, repeat=False, limit=6,hypotheses=False,omit_review=False,programme=0,omit_programme=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        payloads=[]
        def choose(instructions,inputs,tools,choice):
            p=json.loads(inputs[0]['content']);payloads.append(p)
            options=p['allowed_actions']
            self.assertEqual(tools[0]['parameters']['properties']['action']['enum'],list(options))
            if len(payloads)==2 and failure:raise RuntimeError('test-only timeout')
            if len(payloads)==3 and final_failure:raise RuntimeError('test-only final review timeout')
            if len(payloads)==2 and premature:return reply('stop',p['evidence_refs'][-1:],p)
            if len(payloads)<=2:
                self.assertNotIn('stop',options)
                # Initial direction and strength are genuinely the model's choice.
                action=next(k for k,v in options.items() if len(v['changes'])==1 and v['changes'][0]['parking']==len(payloads))
            else:
                self.assertEqual(list(options),['stop'])
                self.assertTrue(p['context']['goal_review']['execution_complete'])
                action='stop'
            result=reply(action,p['evidence_refs'][-1:],p)
            if hypotheses and 'hypothesis_updates' in tools[0]['parameters']['properties']:
                value=json.loads(result['calls'][0]['arguments'])
                value['hypothesis_updates']=[] if action!='stop' or omit_review else [dict(hypothesis_id='H1',judgment='contradicted' if no_gain else 'supported',finding_ids=['benefit'],explanation='仅依据本次完整配对，检验是否有合格措施。')]
                result['calls'][0]['arguments']=json.dumps(value)
            if programme>1:
                self.assertEqual('programme_updates' in tools[0]['parameters']['properties'],action=='stop')
            if programme>1 and action=='stop' and not omit_programme:
                value=json.loads(result['calls'][0]['arguments'])
                value.update(programme_updates=[],programme_summary='')
                if action=='stop':
                    value.update(programme_updates=[dict(hypothesis_id='H1',judgment='unresolved',finding_ids=['study_2.benefit'],explanation='停车对照已有实测；另一适配器的收益不能混合计算。')],
                                 programme_summary='两组分别依据自己的基准复核，不将停车与转向策略的独立结果解释为联合收益。')
                result['calls'][0]['arguments']=json.dumps(value)
            return result
        client=Mock(request=Mock(side_effect=choose))
        task=Task.from_dict(dict(request='比较停车治理强度，允许无收益',demand_factors=list(factors),repeat_baseline=repeat,
                                 max_runs=3*len(factors)+int(repeat),max_links=2,max_feedback=2,recommendation_version='tolerance-v2'))
        session=PlanningSession(client,task.request,limit=limit,hypotheses=[dict(hypothesis='停车治理能产生合格措施',evidence_needed='新基准与治理配对')] if hypotheses else None)
        a=Agent(task,Path(tmp.name)/'source',Path(tmp.name)/'output',planner=session);a.job.mkdir(parents=True)
        if programme:
            from traffic_agent.programme_review import manifest
            agents=[a]
            if programme>1:
                from test_strategy_evidence import study_state
                other_task=Task.from_dict(dict(scenario='circulation',max_runs=4,repeat_baseline=False,demand_factors=[1]))
                other=Mock(job_id='earlier_group',state=dict(study_state('earlier_group',other_task.as_dict()),
                            finding_reference_schema=session.finding_reference_schema,input_protocol=session.input_protocol,
                            risk_review_protocol=session.risk_review_protocol,argument_correction_protocol=session.argument_correction_protocol))
                agents=[other,a]
            definitions=[dict(title='合成本地控制器检查',task=x.state['task']) for x in agents]
            plan=manifest(agents[0].job_id,definitions,agents,session.hypotheses,{},now())
            session.bind_programme(plan,agents);session.final_group=True
            self.programme_plan=plan;self.programme_states={x.job_id:x.state for x in agents}
            for x in agents:x.state.update(programme_plan_digest=digest(plan),research_hypotheses=session.hypotheses)
        rows=[link(0,1,2),link(1,2,3),link(2,3,4,speed=20,vc=.9)]
        base=dict(name='baseline',changes=[],demand_factor=1,execution=dict(status='completed',finished=now(),assigned_car_od_sum=100))
        a.state.update(run_count=1,runs=[base])
        def simulate(name,changes,factor=1):
            new=copy.deepcopy(rows)
            for row in new:row['flow_pcu_h']*=factor
            for c in changes:
                r=next(r for r in new if r['from_node']==c['from_node'])
                r['speed_kmh']=r['speed_kmh'] if no_gain else 26-c['parking']
            record=dict(name=name,changes=changes,demand_factor=factor,metrics=scoped_metrics(new,a.scope),
                        core_rows=[new[i] for i in a.scope['core']],execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100*factor))
            a.state['run_count']+=1;a.state['runs'].append(record)
            return new,record
        diagnosis=dict(selected=rows[:2])
        with patch.object(a,'simulation',side_effect=simulate),patch.object(a,'event'):
            if len(factors)>1:
                from traffic_agent.model_search import analyze
                analyze(a,rows,base,diagnosis)
            else:a.analyze(rows,base,diagnosis)
        return a,session,payloads,diagnosis

    def test_required_pair_cannot_stop_or_change_directions_and_final_review_reads_all(self):
        a,s,p,d=self.exercise()
        self.assertEqual(a.state['status'],'completed')
        self.assertEqual([r['changes'][0]['parking'] for r in a.state['runs'][1:]],[1,2])
        self.assertEqual(len(p[1]['allowed_actions']),1)
        self.assertEqual(len(p[-1]['observations']),3)
        self.assertTrue(a.state['goal_review']['complete'])
        self.assertEqual(s.used,4)
        self.assertTrue(verify_planning(a.state,d)['passed'])
        corrupt=copy.deepcopy(a.state)
        entry=corrupt['planner_trace'][1]
        entry['input']['allowed_actions']['stop']={}
        entry['observation_digest']=digest(entry['input'])
        with self.assertRaisesRegex(ValueError,'权限|停止'):verify_planning(corrupt,d)

    def test_illegal_early_stop_and_timeout_are_incomplete_without_retry(self):
        for options in (dict(premature=True),dict(failure=True)):
            with self.subTest(options=options):
                a,s,p,d=self.exercise(**options)
                self.assertEqual(a.state['status'],'incomplete')
                self.assertFalse(a.state['goal_review']['complete'])
                self.assertEqual(a.state['run_count'],2)
                self.assertEqual(len(p),2)
                self.assertTrue(s.halted)
                self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_full_grid_and_repeat_are_finished_before_final_model_review(self):
        a,s,p,d=self.exercise(factors=(1,1.1),repeat=True)
        self.assertEqual(a.state['run_count'],7)
        self.assertEqual(a.state['status'],'completed')
        self.assertEqual(len(p[-1]['observations']),7)
        self.assertTrue(verify_planning(a.state,d)['passed'])
        incomplete=copy.deepcopy(a.state)
        incomplete['runs']=[r for r in incomplete['runs'] if not (r['changes'] and r['changes'][0]['parking']==2 and r['demand_factor']==1.1)]
        self.assertFalse(coverage(incomplete)['strength_compared'])
        missing_repeat=copy.deepcopy(a.state)
        missing_repeat['runs']=[r for r in missing_repeat['runs'] if r['name']!='baseline_repeat']
        self.assertFalse(next(c for c in coverage(missing_repeat)['requirements'] if c['id']=='repeat')['fulfilled'])

    def test_no_benefit_is_a_complete_negative_finding_after_required_pair(self):
        a,s,p,d=self.exercise(no_gain=True)
        self.assertEqual(a.state['status'],'completed')
        self.assertEqual(a.state['recommendation'],'baseline')
        self.assertTrue(all(not r['comparison']['eligible'] for r in a.state['runs'][1:]))
        self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_insufficient_model_budget_does_not_start_unfinishable_comparison(self):
        a,s,p,d=self.exercise(limit=3)
        self.assertEqual(a.state['status'],'incomplete')
        self.assertEqual(a.state['run_count'],1)
        self.assertEqual(p,[])
        self.assertFalse(a.state['planner_trace'][0]['requested'])
        self.assertEqual(a.state['planner_trace'][0]['status'],'blocked')

    def test_full_native_comparison_without_final_model_review_is_not_complete(self):
        a,s,p,d=self.exercise(final_failure=True)
        self.assertEqual(a.state['run_count'],3)
        self.assertTrue(a.state['goal_review']['execution_complete'])
        self.assertFalse(a.state['goal_review']['complete'])
        self.assertEqual(a.state['status'],'incomplete')
        self.assertEqual(s.used,4)
        self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_optional_search_cannot_spend_final_review_or_future_group_reserve(self):
        a,s,p,d=self.exercise()
        state=a.state
        options=parking_options(d['selected'],set())
        offered,gate=parking_gate(state,options,10,1,1)
        self.assertEqual(offered,{})
        self.assertTrue(gate['stop_allowed'])
        self.assertEqual(minimum_model_requests([state['task']]),3)
        self.assertEqual(minimum_model_requests([state['task'],state['task']]),7)

    def test_last_parking_review_also_finishes_programme_without_extra_request(self):
        from traffic_agent.programme_review import review
        for count in (1,2):
            with self.subTest(studies=count):
                a,s,p,d=self.exercise(hypotheses=True,programme=count)
                self.assertEqual(len(p),3)
                self.assertEqual(s.used,4)
                self.assertTrue(p[-1]['context']['programme_final_group'])
                self.assertEqual(a.state['status'],'completed')
                result=review(self.programme_plan,self.programme_states,s.trace)
                self.assertTrue(result['complete'])
                self.assertTrue(result['updates'][0]['finding_ids'][0].startswith(f'study_{count}.'))
                self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_last_parking_group_without_global_review_is_incomplete(self):
        from traffic_agent.programme_review import review
        a,s,p,d=self.exercise(hypotheses=True,programme=2,omit_programme=True)
        self.assertEqual(a.state['status'],'incomplete')
        self.assertEqual(a.state['run_count'],3)
        self.assertEqual(len(p),4)
        self.assertEqual(s.used,5)
        self.assertEqual(s.trace[-1]['review_correction']['first_attempt']['validation']['code'],'argument_fields')
        self.assertTrue(s.halted)
        self.assertFalse(review(self.programme_plan,self.programme_states,s.trace)['complete'])


if __name__=='__main__':unittest.main()
