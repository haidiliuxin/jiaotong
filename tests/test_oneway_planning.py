"""Synthetic controller/native-boundary checks; no API or TranStar execution."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics
from traffic_agent.feedback_planner import PlanningSession, digest
from traffic_agent.oneway_agent import diagnose_oneway, analyze_oneway
from traffic_agent.oneway_planning import choose, menu, observations, selected_diagnosis
from traffic_agent.planning_audit import verify_planning
from traffic_agent.runner import now
from traffic_agent.task import Task
from test_agent import link
from test_feedback_planner import reply


def roads():
    rows = [link(i, a, b, speed=10+i, flow=900-i*10) for i, (a,b) in
            enumerate([(1,2),(2,1),(3,4),(4,3),(5,6),(6,5)])]
    for i,r in enumerate(rows):
        r['name'] = '甲路' if i<2 else '乙路' if i<4 else '丙路'
    return rows


def graph_check(pair):
    return dict(eligible=True, closed_direction=list(pair), bus_routes_checked=3,
                modal_boundary_checks={m:[dict(replacement=dict(extra_km=.2,ratio=1.4))]
                                       for m in ('car','motor','truck')})


class OneWayPlanningTests(unittest.TestCase):
    def diagnosis(self, rows=None, **task_fields):
        rows = rows or roads()
        task = Task.from_dict(dict(scenario='oneway', demand_factors=[1], repeat_baseline=False, max_runs=4, **task_fields))
        a = SimpleNamespace(source=Path('unused'), job=Path('unused'), task=task)
        with patch('traffic_agent.oneway_agent.OneWayNetwork.load', return_value=Mock(validate=Mock(side_effect=graph_check))), \
             patch('traffic_agent.oneway_agent.numeric', return_value=[400]*len(rows)):
            return diagnose_oneway(a, rows, dict(scenario='baseline'), record_evidence=False, portfolio=True)

    def test_fresh_catalogue_is_bounded_and_explicit_scope_does_not_expand(self):
        d = self.diagnosis()
        self.assertEqual(len(d['targets']), 4)
        self.assertEqual(d['selected'], [])
        self.assertEqual(list(menu(d)), ['oneway_1_2','oneway_2_1','oneway_3_4','oneway_4_3'])
        d = self.diagnosis(road_name='乙路', links=[[3,4]])
        self.assertEqual(list(menu(d)), ['oneway_3_4'])
        row = menu(d)['oneway_3_4']
        self.assertEqual(row['roads'][0]['speed_kmh'], 12)
        self.assertEqual(row['baseline_car_flow'], 400)
        self.assertEqual(row['topology']['modal_detours']['truck']['max_ratio'], 1.4)
        self.assertNotIn('benefit', row)

    def test_graph_or_reverse_parking_failure_never_enters_model_menu(self):
        rows = roads(); rows[1]['parking']=1
        d = self.diagnosis(rows)
        self.assertNotIn('oneway_1_2', menu(d))
        task=Task.from_dict(dict(scenario='oneway', demand_factors=[1], repeat_baseline=False, max_runs=4))
        a=SimpleNamespace(source=Path('unused'),job=Path('unused'),task=task)
        with patch('traffic_agent.oneway_agent.OneWayNetwork.load',return_value=Mock(validate=Mock(return_value=dict(eligible=False)))), \
             patch('traffic_agent.oneway_agent.numeric',return_value=[400]*len(rows)):
            d=diagnose_oneway(a,rows,dict(scenario='baseline'),record_evidence=False,portfolio=True)
        self.assertEqual(d['targets'], [])

    def exercise(self, action='oneway_3_4', failure=False, limit=6, reserved=0, factors=(1,), programme=False, full_run=False, direction_text=None):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        task=Task.from_dict(dict(request='比较停车与单向组织',scenario='oneway',demand_factors=list(factors),
                                repeat_baseline=False,max_runs=4*len(factors),recommendation_version='tolerance-v2'))
        payloads=[]
        def decide(instructions,inputs,tools,tool_choice):
            payload=json.loads(inputs[0]['content']);payloads.append(payload)
            if failure:raise RuntimeError('synthetic timeout')
            value=reply(action if payload['phase']=='oneway_target' else 'stop',payload['evidence_refs'][-1:],payload)
            if direction_text and payload['phase']=='oneway_target':
                args=json.loads(value['calls'][0]['arguments']);args['reason']=direction_text
                value['calls'][0]['arguments']=json.dumps(args)
            if 'hypothesis_updates' in tools[0]['parameters']['properties']:
                args=json.loads(value['calls'][0]['arguments'])
                args['hypothesis_updates']=[dict(hypothesis_id='H1',judgment='unresolved',finding_ids=['combination'],
                    explanation='仅复核本组配对与约束，不能证明所有位置都有效。')]
                value['calls'][0]['arguments']=json.dumps(args)
            return value
        session=PlanningSession(Mock(request=Mock(side_effect=decide)),task.request,limit=limit,
                                hypotheses=[dict(hypothesis='单向与停车组合可能改善拥堵',evidence_needed='需要同条件配对检验')])
        session.reserved_requests=reserved
        a=Agent(task,Path(tmp.name)/'source',Path(tmp.name)/'out',planner=session)
        if not full_run:a.job.mkdir(parents=True)
        if programme:
            from traffic_agent.programme_review import manifest
            self.programme=manifest(a.job_id,[dict(title='合成单向选点',task=task.as_dict())],[a],session.hypotheses,{},now())
            session.bind_programme(self.programme,[a]);session.final_group=True
            a.state.update(programme_plan_digest=digest(self.programme),programme_protocol=self.programme['version'],
                           evidence_protocol='question-evidence-v1',research_hypotheses=session.hypotheses)
        baseline=roads(); d=self.diagnosis(baseline)
        base=dict(name='baseline',changes=[],demand_factor=1,scenario='baseline',
                  execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100))
        if not full_run:a.state.update(runs=[base],run_count=1,status='running')
        def simulate(name,changes,factor=1):
            rows=copy.deepcopy(baseline)
            if name=='baseline':
                a.state['runs'].append(base);a.state['run_count']+=1
                return rows,base
            for row in rows:
                row['flow_pcu_h']*=factor
                if changes and row['index'] in a.scope['core']:row['speed_kmh']+=5
            record=dict(name=name,changes=changes,demand_factor=factor,scenario=name,metrics=scoped_metrics(rows,a.scope),
                        core_rows=[rows[i] for i in a.scope['core']],
                        execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100*factor))
            a.state['runs'].append(record);a.state['run_count']+=1
            return rows,record
        with patch.object(a,'event'),patch.object(a,'simulation',side_effect=simulate), \
                patch('traffic_agent.oneway_agent.response',return_value=dict(passed=True,flows=dict(car=dict(before=400)))), \
                patch('traffic_agent.agent.validate_source',return_value={}), \
                patch('traffic_agent.agent.input_fingerprints',return_value={}), \
                patch('traffic_agent.hard_access.install',return_value=(Path(tmp.name)/'runtime',dict(version='synthetic'))), \
                patch('traffic_agent.oneway_agent.OneWayNetwork.load',return_value=Mock(validate=Mock(side_effect=graph_check))), \
                patch('traffic_agent.oneway_agent.numeric',return_value=[400]*len(baseline)), \
                patch('traffic_agent.report.write_report'):
            if full_run:
                a.run()
                d=json.loads((a.job/'diagnosis.json').read_text('utf-8'))
            else:d=choose(a,baseline,base,d)
            if a.state['status']!='incomplete':
                if not full_run:analyze_oneway(a,baseline,base,d)
                session.route(a,[],[])
        return a,d,session,payloads

    def test_model_nonfirst_target_controls_every_native_policy_and_demand(self):
        a,d,s,p=self.exercise(factors=(1,1.1),programme=True)
        self.assertEqual(d['selected_target'],'oneway_3_4')
        self.assertEqual(a.scope['core'],[2,3])
        self.assertEqual(a.state['run_count'],8)
        self.assertEqual([x['phase'] for x in p],['oneway_target','route'])
        self.assertEqual(s.used,3)  # initial composition + target + existing final review
        self.assertNotIn('stop',p[0]['allowed_actions'])
        self.assertEqual(set(p[0]['observations'][0]['metrics']),{'network'})
        self.assertEqual(p[0]['observations'],observations(a.state))
        spec=s.trace[0]['input']['context']['action_execution'];action=spec['actions']['oneway_3_4']
        self.assertEqual([(r['name'],r['demand_factor']) for r in a.state['runs'][1:]],
                         [(r['name'],r['demand_factor']) for r in action['sequence']])
        self.assertEqual(action['closed_direction'],[3,4])
        self.assertEqual(action['retained_direction'],[4,3])
        self.assertEqual(spec['native_budget']['current_study']['remaining'],7)
        self.assertEqual(s.trace[0]['next_check_origin'],'action-execution-v2')
        self.assertIn('停车2级',s.trace[0]['next_check'])
        for r in a.state['runs']:
            for c in r['changes']:
                if c.get('kind')=='oneway':self.assertEqual(c['closed_direction'],[3,4])
                else:
                    self.assertIn((c['from_node'],c['to_node']),((3,4),(4,3)))
                    cell=next(x for x in action['sequence'] if x['name']==r['name'])
                    self.assertEqual(c['parking'],cell['parking_level'])
        self.assertTrue(verify_planning(a.state,d)['passed'])
        from traffic_agent.programme_review import review,verify
        states={a.job_id:a.state}
        record=dict(job_id=a.job_id,job_ids=[a.job_id],status='completed',planning_trace=s.trace,
                    programme_review=review(self.programme,states,s.trace),model_budget=s.budget())
        self.assertTrue(record['programme_review']['complete'])
        self.assertTrue(verify(self.programme,record,states)['passed'])

    def test_no_paid_retry_rule_fallback_or_policy_after_invalid_model_choice(self):
        for kwargs in (dict(failure=True),dict(action='oneway_99_100'),dict(action='stop')):
            with self.subTest(**kwargs):
                a,d,s,p=self.exercise(**kwargs)
                self.assertEqual(a.state['status'],'incomplete')
                self.assertEqual(a.state['run_count'],1)
                self.assertEqual(d['selected'],[])
                self.assertEqual(len(p),1)
                self.assertEqual(s.used,2)
                self.assertTrue(verify_planning(a.state,d)['passed'])
                self.assertNotIn('factorial_design',a.state)

    def test_complete_agent_entry_executes_model_target_and_keeps_failure_incomplete(self):
        for failure in (False,True):
            a,d,s,p=self.exercise(full_run=True,failure=failure)
            frozen=json.loads((a.job/'plan.json').read_text('utf-8'))
            self.assertEqual(frozen['target_planning_protocol'],'oneway-target-v1')
            self.assertNotIn('spatial_protocol',frozen)
            self.assertNotIn('planning_protocol',a.state)
            self.assertEqual(a.state['status'],'incomplete' if failure else 'completed')
            self.assertEqual(a.state['run_count'],1 if failure else 4)
            self.assertEqual(d['selected'],[] if failure else [roads()[2],roads()[3]])
            self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_failed_target_with_programme_evidence_retains_an_unreviewed_record(self):
        for kwargs in (dict(failure=True),dict(action='oneway_99_100'),dict(limit=1)):
            with self.subTest(**kwargs):
                a,d,s,p=self.exercise(programme=True,**kwargs)
                self.assertEqual(a.state['status'],'incomplete')
                self.assertEqual(a.state['research_review']['updates'],[])
                self.assertFalse(a.state['research_review']['reviewed'])
                self.assertFalse(a.state['research_review']['evidence']['execution_complete'])
                self.assertTrue(verify_planning(a.state,d)['passed'])
                self.assertEqual(a.state['run_count'],1)

    def test_direction_clauses_gate_actual_controller_dispatch(self):
        from traffic_agent.direction_claims import VERSION
        for text,accepted in [('选择3→4禁行方向（反向保留），收益尚未测。',True),
                              ('选择3→4保留通行（反向禁行），收益尚未测。',False)]:
            with self.subTest(text=text):
                a,d,s,p=self.exercise(full_run=True,direction_text=text)
                self.assertEqual(p[0]['context']['direction_semantics_protocol'],VERSION)
                self.assertEqual(a.state['run_count'],4 if accepted else 1)
                self.assertEqual(s.trace[0]['status'],'accepted' if accepted else 'rejected')
                if accepted:self.assertEqual(s.trace[0]['reason'],text)
                else:self.assertEqual(s.trace[0]['validation']['code'],'direction_reversed')
                self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_budget_reserves_final_review_and_all_remaining_groups(self):
        a,d,s,p=self.exercise(limit=3,reserved=1)
        self.assertEqual(p,[])
        self.assertEqual(a.state['status'],'incomplete')
        self.assertEqual(s.used,1)
        self.assertEqual(s.trace[0]['status'],'blocked')
        self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_audit_rejects_changed_target_observations_and_execution_before_choice(self):
        a,d,s,p=self.exercise(factors=(1,1.1))
        mutations=[lambda b:b['factorial_design'].update(closed_direction=[1,2]),
                   lambda b:b['oneway_target_catalogue']['options']['oneway_3_4']['roads'][0].update(speed_kmh=999),
                   lambda b:b['runs'][1]['execution'].update(started=b['runs'][0]['execution']['started']),
                   lambda b:next(r for r in b['runs'] if not r['changes'] and r['demand_factor']==1.1)['execution'].update(started=b['runs'][0]['execution']['started']),
                   lambda b:b.update(planner_trace=b['planner_trace'][1:])]
        for change in mutations:
            b=copy.deepcopy(a.state);change(b)
            with self.subTest(change=change),self.assertRaises(ValueError):verify_planning(b,d)
        altered=selected_diagnosis({**d,'selected':[]},'oneway_1_2')
        with self.assertRaisesRegex(ValueError,'模型选择'):verify_planning(a.state,altered)


if __name__=='__main__':unittest.main()
