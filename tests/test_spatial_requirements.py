"""Cross-area completion, feedback execution and audit; all data is synthetic."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics
from traffic_agent.feedback_planner import PlanningSession,digest
from traffic_agent.planning_audit import verify_planning
from traffic_agent.planning_review import coverage
from traffic_agent.runner import now
from traffic_agent.spatial_planning import diagnose_portfolio,observe
from traffic_agent.spatial_requirements import required_regions
from traffic_agent.task import Task
from test_agent import link
from test_feedback_planner import reply


class RegionalCompletionTests(unittest.TestCase):
    def exercise(self,strength=False,factors=(1,),available_areas=3,premature=False):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        request='比较两个不同区域的停车治理'+('并比较治理强度' if strength else '')
        policy_count=4 if strength else 2
        task=Task.from_dict(dict(request=request,demand_factors=list(factors),repeat_baseline=False,max_links=1,
                                max_runs=(policy_count+1)*len(factors),max_feedback=policy_count-1,recommendation_version='tolerance-v2'))
        rows=[dict(link(i,i*10+1,i*10+2),name=f'区域{i+1}道路') for i in range(available_areas)]
        diagnosis=diagnose_portfolio(rows,task)
        payloads=[]
        def choose(instructions,inputs,tools,tool):
            payload=json.loads(inputs[0]['content']);payloads.append(payload)
            menu=payload['allowed_actions']
            if premature and len(payloads)==2:return reply('stop',payload['evidence_refs'][-1:])
            if len(payloads)<=policy_count:
                self.assertNotIn('stop',menu)
                self.assertGreater(len(menu),0)
                action=next(iter(menu))
                if not strength and len(payloads)==2:
                    self.assertTrue(all(v['region']!=payloads[0]['allowed_actions'][next(iter(payloads[0]['allowed_actions']))]['region'] for v in menu.values()))
            else:
                self.assertEqual(list(menu),['stop']);action='stop'
            return reply(action,payload['evidence_refs'][-1:],payload)
        planner=PlanningSession(Mock(request=Mock(side_effect=choose)),request)
        a=Agent(task,Path(tmp.name)/'source',Path(tmp.name)/'output',planner=planner);a.job.mkdir(parents=True)
        base=dict(name='baseline',changes=[],demand_factor=1,execution=dict(status='completed',finished=now(),assigned_car_od_sum=100))
        a.state.update(regions=diagnosis['regions'],run_count=1,runs=[base])
        def simulate(name,changes,factor=1):
            measured=copy.deepcopy(rows)
            for road in measured:road['flow_pcu_h']*=factor
            for c in changes:next(r for r in measured if r['from_node']==c['from_node'])['speed_kmh']=26-c['parking']
            record=dict(name=name,changes=changes,demand_factor=factor,metrics=scoped_metrics(measured,a.scope),
                        core_rows=[measured[i] for i in a.scope['core']],spatial_observation=observe(measured,a.state['regions']),
                        execution=dict(status='completed',started=now(),finished=now(),assigned_car_od_sum=100*factor))
            a.state['runs'].append(record);a.state['run_count']+=1
            return measured,record
        with patch.object(a,'simulation',side_effect=simulate),patch.object(a,'event'):
            if len(factors)>1:
                from traffic_agent.model_search import analyze
                analyze(a,rows,base,diagnosis)
            else:a.analyze(rows,base,diagnosis)
        return a,planner,payloads,diagnosis

    def test_explicit_region_count_and_negation(self):
        self.assertEqual(required_regions('比较两个不同区域'),2)
        self.assertEqual(required_regions('对比三个片区'),3)
        self.assertEqual(required_regions('评估4个区域'),4)
        self.assertEqual(required_regions('不比较不同区域，只保护周边'),1)

    def test_two_areas_must_execute_same_level_then_review_all(self):
        a,s,p,d=self.exercise()
        self.assertEqual(a.state['status'],'completed')
        self.assertEqual(a.state['run_count'],3)
        policies=a.state['runs'][1:]
        self.assertNotEqual(policies[0]['changes'][0]['from_node'],policies[1]['changes'][0]['from_node'])
        self.assertEqual(policies[0]['changes'][0]['parking'],policies[1]['changes'][0]['parking'])
        self.assertEqual(s.used,4)
        self.assertTrue(verify_planning(a.state,d)['passed'])
        corrupt=copy.deepcopy(a.state)
        corrupt['runs'][2]['changes'][0]['parking']=1
        self.assertFalse(coverage(corrupt)['execution_complete'])

    def test_cross_area_strength_grid_completes_each_area_and_demand(self):
        a,s,p,d=self.exercise(strength=True,factors=(1,1.1))
        self.assertEqual(a.state['status'],'completed')
        self.assertEqual(a.state['run_count'],10)
        self.assertEqual(s.used,6)
        self.assertEqual(len(a.state['goal_review']['strength_matrix']),4)
        self.assertEqual({x['region'] for x in a.state['goal_review']['strength_matrix']},{'area_1','area_2'})
        self.assertTrue(verify_planning(a.state,d)['passed'])
        incomplete=copy.deepcopy(a.state);incomplete['runs'].pop()
        self.assertFalse(coverage(incomplete)['complete'])
        self.assertFalse(coverage(incomplete)['strength_compared'])

    def test_insufficient_geographic_candidates_do_not_spend_model_calls(self):
        a,s,p,d=self.exercise(available_areas=1)
        self.assertEqual(a.state['status'],'incomplete')
        self.assertEqual(a.state['run_count'],1)
        self.assertEqual(p,[])
        self.assertEqual(s.used,1)
        self.assertTrue(verify_planning(a.state,d)['passed'])

    def test_early_stop_and_tampered_menu_cannot_claim_completion(self):
        a,s,p,d=self.exercise(premature=True)
        self.assertEqual(a.state['status'],'incomplete')
        self.assertEqual(a.state['run_count'],2)
        self.assertTrue(verify_planning(a.state,d)['passed'])
        corrupt=copy.deepcopy(a.state)
        entry=corrupt['planner_trace'][1]
        entry['input']['allowed_actions']['stop']={}
        entry['observation_digest']=digest(entry['input'])
        with self.assertRaisesRegex(ValueError,'权限|停止'):verify_planning(corrupt,d)


if __name__=='__main__':unittest.main()
