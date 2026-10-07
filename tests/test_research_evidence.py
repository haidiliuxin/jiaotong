import copy
import unittest

import test_planning_completion as helpers
from traffic_agent.agent import compare
from traffic_agent.data import scoped_metrics,scopes
from traffic_agent.feedback_planner import digest
from traffic_agent.planning_audit import verify_planning
from traffic_agent.research_evidence import findings,validate_updates
from traffic_agent.task import Task
from test_agent import link


class ResearchEvidenceTests(unittest.TestCase):
    def state(self,factors=(1,)):
        task=Task.from_dict(dict(max_runs=20,demand_factors=list(factors),repeat_baseline=False,recommendation_version='tolerance-v2'))
        rows=[link(0,1,2),link(1,2,3,speed=20,vc=.9),link(2,30,31)]
        scope=scopes(rows,[0],2)
        state=dict(id='synthetic',task=task.as_dict(),status='running',runs=[])
        for factor in factors:
            base=copy.deepcopy(rows)
            for r in base:r['flow_pcu_h']*=factor
            state['runs'].append(dict(name=f'base_{factor}',changes=[],demand_factor=factor,metrics=scoped_metrics(base,scope),execution=dict(status='completed')))
            for level in (2,1):
                after=copy.deepcopy(base);after[0]['speed_kmh']=25 if level==1 else 18;after[0]['parking']=level
                # Stronger treatment improves core speed, but damages the neighbor.
                if level==1:after[1]['speed_kmh']=10
                changes=[dict(from_node=1,to_node=2,old_parking=3,parking=level)]
                state['runs'].append(dict(name=f'level{level}_{factor}',changes=changes,demand_factor=factor,metrics=scoped_metrics(after,scope),
                                          comparison=compare(base,after,scope,task),execution=dict(status='completed')))
        return state

    def test_stronger_core_speed_does_not_override_neighbor_failure(self):
        evidence=findings(self.state())
        by_id={f['id']:f for f in evidence['findings']}
        self.assertEqual(evidence['preferred_tested_policy'],'level2_1')
        self.assertEqual(by_id['strength']['measurements'][0]['preferred_level'],2)
        self.assertEqual(by_id['tradeoff']['status'],'observed')
        self.assertEqual(by_id['robustness']['status'],'unmeasured')
        self.assertEqual(by_id['regions']['status'],'unmeasured')
        self.assertIn('synthetic:level1_1',by_id['strength']['evidence_refs'])

    def test_missing_demand_or_failed_execution_does_not_become_positive_evidence(self):
        state=self.state((1,1.1))
        state['runs']=[r for r in state['runs'] if not(r['demand_factor']==1.1 and r['changes'])]
        by_id={f['id']:f for f in findings(state)['findings']}
        self.assertEqual(by_id['benefit']['status'],'unmeasured')
        self.assertEqual(by_id['strength']['status'],'unmeasured')
        state=self.state();state['runs'][1]['execution']['status']='failed'
        self.assertNotIn('synthetic:level2_1',str(findings(state)))
        self.assertIsNone(findings(state)['preferred_tested_policy'])

    def test_unmeasured_facts_cannot_support_hypotheses_and_final_review_must_cover_all(self):
        evidence=findings(self.state())
        hypotheses=[dict(id='H1'),dict(id='H2')]
        update=dict(hypothesis_id='H1',judgment='supported',finding_ids=['robustness'],explanation='不允许把单需求外推')
        with self.assertRaisesRegex(ValueError,'未测'):validate_updates([update],hypotheses,evidence)
        update.update(judgment='unresolved')
        validate_updates([update],hypotheses,evidence)
        with self.assertRaisesRegex(ValueError,'遗漏'):validate_updates([update],hypotheses,evidence,final=True)
        update['finding_ids']=['invented']
        with self.assertRaisesRegex(ValueError,'引用'):validate_updates([update],hypotheses,evidence)

    def exercise(self,**kwargs):
        helper=helpers.CompletionTests();self.addCleanup(helper.doCleanups)
        return helper.exercise(hypotheses=True,**kwargs)

    def test_actual_controller_requires_hypothesis_review_and_audits_prefix_facts(self):
        agent,session,payloads,diagnosis=self.exercise()
        self.assertEqual(agent.state['status'],'completed')
        self.assertTrue(agent.state['research_review']['reviewed'])
        self.assertIsNone(agent.state['robust_across_tested_demand'])
        self.assertEqual(payloads[0]['context']['research_evidence']['findings'][0]['status'],'unmeasured')
        self.assertEqual(payloads[-1]['context']['research_evidence']['findings'][0]['status'],'supported')
        self.assertEqual(agent.state['research_review']['updates'][0]['judgment'],'supported')
        self.assertTrue(verify_planning(agent.state,diagnosis)['passed'])
        tampered=copy.deepcopy(agent.state)
        entry=tampered['planner_trace'][0]
        entry['input']['context']['research_evidence']=payloads[-1]['context']['research_evidence']
        entry['observation_digest']=digest(entry['input'])
        with self.assertRaisesRegex(ValueError,'当时'):verify_planning(tampered,diagnosis)

    def test_native_work_without_complete_hypothesis_review_is_incomplete(self):
        agent,session,payloads,diagnosis=self.exercise(omit_review=True)
        self.assertEqual(agent.state['run_count'],3)
        self.assertEqual(agent.state['status'],'incomplete')
        self.assertEqual(agent.state['planner_trace'][-1]['status'],'rejected')
        self.assertEqual(session.used,4)
        self.assertTrue(verify_planning(agent.state,diagnosis)['passed'])

    def test_failed_hypothesis_is_kept_as_negative_result(self):
        agent,session,payloads,diagnosis=self.exercise(no_gain=True)
        self.assertEqual(agent.state['recommendation'],'baseline')
        self.assertEqual(agent.state['research_review']['updates'][0]['judgment'],'contradicted')
        self.assertEqual(agent.state['research_review']['evidence']['findings'][0]['status'],'not_supported')
        self.assertTrue(verify_planning(agent.state,diagnosis)['passed'])


if __name__=='__main__':unittest.main()
