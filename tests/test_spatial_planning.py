import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from traffic_agent.agent import Agent,compare
from traffic_agent.data import scoped_metrics,scopes
from traffic_agent.feedback_planner import PlanningSession
from traffic_agent.planning_audit import verify_planning
from traffic_agent.spatial_planning import diagnose_portfolio,observe,assess,options
from traffic_agent.task import Task
from traffic_agent.runner import now
from test_agent import link
from test_feedback_planner import reply


class SpatialPlanningTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(link(0,1,2,speed=8,flow=900),name='甲路'),
                   dict(link(1,20,21),name='乙路'),dict(link(2,30,31),name='丙路')]
        self.task=Task.from_dict(dict(request='根据实测比较不同位置，避免拥堵转移',max_links=1,max_runs=3,
                                     demand_factors=[1],repeat_baseline=False,recommendation_version='tolerance-v2'))

    def test_candidate_areas_expand_without_expanding_per_action_limit(self):
        d=diagnose_portfolio(self.rows,self.task)
        self.assertEqual([r['indices'] for r in d['regions']],[[0],[1],[2]])
        self.assertEqual(len(d['selected']),3)
        menu=options(d,set(),1)
        self.assertEqual(len(menu),6)
        self.assertTrue(all(len(v['changes'])<=1 for v in menu.values()))
        self.assertEqual({v['region'] for v in menu.values()},{'area_1','area_2','area_3'})

    def test_explicit_road_and_direction_remain_binding(self):
        task=Task.from_dict(dict(self.task.as_dict(),road_name='乙路',links=[[20,21]]))
        d=diagnose_portfolio(self.rows,task)
        self.assertEqual([r['index'] for r in d['selected']],[1])
        self.assertTrue(all(v['changes'][0]['from_node']==20 for v in options(d,set(),1).values()))

    def test_full_default_catalog_has_disjoint_directions_and_bounded_actions(self):
        rows=[dict(link(i,100*(i//3)+i%3,100*(i//3)+i%3+1),name=f'片区{i//3}') for i in range(12)]
        task=Task.from_dict(dict(self.task.as_dict(),max_links=3))
        d=diagnose_portfolio(rows,task)
        self.assertEqual(len(d['regions']),4)
        self.assertEqual(len(d['selected']),12)
        self.assertEqual(len({r['index'] for r in d['selected']}),12)
        self.assertTrue(all(1<=len(v['changes'])<=3 for v in options(d,set(),3).values()))

    def test_other_studies_keep_their_native_protocol_in_frozen_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            task=Task.from_dict(dict(scenario='oneway',max_runs=4,demand_factors=[1],repeat_baseline=False))
            a=Agent(task,Path(tmp)/'source',Path(tmp)/'output',planner=Mock(limit=6))
            with patch('traffic_agent.agent.validate_source',side_effect=ValueError('local preflight test')),patch.object(a,'event'),patch('traffic_agent.report.write_report'):
                with self.assertRaises(ValueError):a.run()
            frozen=json.loads((a.job/'plan.json').read_text('utf-8'))
            self.assertNotIn('spatial_protocol',frozen)
            self.assertTrue(any('S1' in step for step in frozen['steps']))

    def test_regions_do_not_overlap_and_whole_network_observation_includes_outside(self):
        rows=self.rows+[dict(link(3,2,3),name='甲路相邻'),dict(link(4,80,81),name='目录外',road_class=4)]
        d=diagnose_portfolio(rows,self.task)
        self.assertEqual([r['indices'] for r in d['regions']],[[0],[1],[2]])
        obs=observe(rows,d['regions'])
        self.assertEqual(obs['inspected_links'],5)
        self.assertTrue(any(r['index']==4 for r in obs['outside_hotspots']))

    def test_area_loss_cannot_be_hidden_by_aggregate_core_gain(self):
        d=diagnose_portfolio(self.rows,self.task);scope=scopes(self.rows,[0,1,2],2)
        after=copy.deepcopy(self.rows);after[0]['speed_kmh']=25;after[1]['speed_kmh']=9
        self.assertTrue(compare(self.rows,after,scope,self.task)['eligible'])
        result=assess(self.rows,after,scope,self.task,d['regions'])
        self.assertFalse(result['eligible'])
        self.assertFalse(result['regional_checks'][1]['passed'])

    def test_fresh_area_failure_causes_model_to_execute_another_area(self):
        self.run_spatial_controller()

    def test_consolidated_plan_still_executes_two_areas_and_complete_hypothesis_review(self):
        self.run_spatial_controller(compiled=True)

    def run_spatial_controller(self,compiled=False):
        with tempfile.TemporaryDirectory() as tmp:
            initial=None
            if compiled:
                from traffic_agent.natural_plan import compose
                from test_natural_plan import experiment,model_reply
                request='比较两个不同区域的停车治理，不比较强度；最多治理1条；需求系数：1；最多3次仿真，不复跑'
                initial=compose(request,Path(tmp),Mock(request=Mock(return_value=model_reply([experiment(['parking']),experiment(['parking'])]))),
                                dict(matched_roads=[],road_names={'甲路','乙路','丙路'},targets={}))
                self.assertEqual(len(initial['experiments']),1)
                self.task=Task.from_dict(initial['task'])
            d=diagnose_portfolio(self.rows,self.task)
            payloads=[]
            def choose(instructions,inputs,tools,choice):
                p=json.loads(inputs[0]['content']);payloads.append(p)
                self.assertEqual(p['observations'][-1]['spatial']['inspected_links'],3)
                if len(payloads)==1:target='area_1'
                elif len(payloads)==2:
                    self.assertFalse(p['observations'][-1]['eligible'])
                    self.assertFalse(p['observations'][-1]['regional_checks'][1]['passed'])
                    target='area_2'
                else:target=None
                action=next(k for k,v in p['allowed_actions'].items() if v.get('region')==target and v.get('changes',[{}])[0].get('parking')==2) if target else 'stop'
                response=reply(action,p['evidence_refs'][-1:],p)
                if compiled:
                    values=json.loads(response['calls'][0]['arguments'])
                    facts={f['hypothesis_id']:f for f in p['context']['research_evidence']['findings'] if f.get('evaluation')}
                    values['hypothesis_updates']=[] if action!='stop' else [dict(hypothesis_id=h['id'],judgment='unresolved',
                        finding_ids=[] if h['id'] in facts and facts[h['id']]['status']=='unmeasured' else ['regions'],
                        explanation='合成流程已完成两个不同区域的配对，现实因果假设仍缺少独立证据。') for h in s.hypotheses]
                    response['calls'][0]['arguments']=json.dumps(values)
                return response
            s=PlanningSession(Mock(request=Mock(side_effect=choose)),self.task.request,hypotheses=initial['assessment'] if compiled else None)
            a=Agent(self.task,Path(tmp)/'source',Path(tmp)/'output',planner=s);a.job.mkdir(parents=True)
            if compiled:
                from traffic_agent.programme_review import manifest,review,verify
                from traffic_agent.feedback_planner import digest
                plan=manifest(a.job_id,initial['experiments'],[a],s.hypotheses,{},now(),model_limit=s.limit)
                s.bind_programme(plan,[a]);s.final_group=True
                a.state.update(programme_protocol=plan['version'],programme_plan_digest=digest(plan),research_hypotheses=s.hypotheses)
            base=dict(name='baseline',changes=[],demand_factor=1,execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100))
            a.state.update(regions=d['regions'],runs=[base],run_count=1)
            def simulate(name,changes,factor=1):
                rows=copy.deepcopy(self.rows)
                for c in changes:next(r for r in rows if r['from_node']==c['from_node'])['speed_kmh']=25
                if changes[0]['from_node']==1:rows[1]['speed_kmh']=9
                record=dict(name=name,changes=changes,demand_factor=factor,metrics=scoped_metrics(rows,a.scope),
                            core_rows=[rows[i] for i in a.scope['core']],spatial_observation=observe(rows,d['regions']),
                            execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100))
                a.state['runs'].append(record);a.state['run_count']+=1
                return rows,record
            with patch.object(a,'simulation',side_effect=simulate),patch.object(a,'event'):
                a.analyze(self.rows,base,d)
            self.assertEqual([r['changes'][0]['from_node'] for r in a.state['runs'][1:]],[1,20])
            self.assertEqual(a.state['recommendation'],'parking_2')
            self.assertEqual(a.state['status'],'completed')
            self.assertTrue(verify_planning(a.state,d)['passed'])
            self.assertNotIn('strength_matrix',payloads[-1]['context']['goal_review'])
            self.assertNotIn('timelink_sum_pcu_h',payloads[-1]['observations'][0]['metrics']['core'])
            if compiled:
                self.assertEqual(a.state['run_count'],3)
                self.assertEqual(s.used,4)
                overview=review(plan,{a.job_id:a.state},s.trace)
                self.assertTrue(overview['complete'])
                record=dict(job_id=a.job_id,job_ids=[a.job_id],planning_trace=s.trace,programme_review=overview,status=a.state['status'],model_budget=s.budget())
                self.assertTrue(verify(plan,record,{a.job_id:a.state})['passed'])


if __name__=='__main__':unittest.main()
