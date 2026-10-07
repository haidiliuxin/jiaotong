import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from traffic_agent.study_depth import LEGACY_VERSION,DEMAND_VERSION,VERSION,from_request,verify_plan
from traffic_agent.planning_scope import PrerequisiteError
from traffic_agent.natural_plan import planning_inputs
from traffic_agent.planning_studies import study_units
from traffic_agent.initial_planner import correction_feedback
from test_natural_plan import CATALOG


class StudyDepthTests(unittest.TestCase):
    def test_explicit_depth_and_negations(self):
        self.assertEqual(from_request('尽快找到一个可用方案就停')['intent'],'find_one')
        self.assertEqual(from_request('比较至少三个停车备选方案')['minimum_candidates'],3)
        self.assertEqual(from_request('检验需求变化下是否仍有效')['intent'],'demand_robustness')
        self.assertEqual(from_request('检验同一停车措施在小汽车需求系数0.9和1.0下是否稳健')['intent'],
                         'demand_robustness')
        self.assertEqual(from_request('检验同一停车措施在小汽车需求系数0.9和1.0下是否稳健',
                                      LEGACY_VERSION)['intent'],'adaptive')
        self.assertEqual(from_request('需求系数0.9和1.0')['version'],VERSION)
        self.assertEqual(from_request('需求系数1.0')['intent'],'adaptive')
        self.assertEqual(from_request('需求系数0.9和1.0',DEMAND_VERSION)['version'],DEMAND_VERSION)
        with self.assertRaisesRegex(PrerequisiteError,'研究深度冲突'):
            from_request('需求系数0.9和1.0，但不做需求变化检验')
        skipped=from_request('不比较多个停车方案，也不做需求变化检验')
        self.assertEqual(skipped['minimum_candidates'],1)
        self.assertFalse(skipped['demand_validation'])
        self.assertEqual(from_request('停车、单向及组合分别检验核心收益')['minimum_candidates'],1)

    def test_parking_comparison_keeps_treatment_domain(self):
        depth=from_request('比较至少三个停车治理方案，再看单向组织')
        self.assertEqual(depth['minimum_candidates'],3)
        self.assertEqual(depth['candidate_domain'],'parking')
        with self.assertRaisesRegex(PrerequisiteError,'停车备选'):
            verify_plan(depth,[dict(scenario='oneway',demand_factors=[1.0])])
        with self.assertRaisesRegex(PrerequisiteError,'停车备选'):
            verify_plan(depth,[dict(scenario='parking',demand_factors=[1.0]),
                               dict(scenario='oneway',demand_factors=[1.0])])
        verify_plan(depth,[dict(scenario='parking',demand_factors=[1.0],comparison_policies=3),
                           dict(scenario='oneway',demand_factors=[1.0])])
        old=from_request('比较至少三个停车治理方案，再看单向组织',DEMAND_VERSION)
        self.assertEqual(old['minimum_candidates'],3)
        self.assertNotIn('candidate_domain',old)

    def test_plan_must_keep_requested_demand_and_comparison(self):
        depth=from_request('比较多个停车方案，并检验需求变化下是否仍有效')
        with self.assertRaises(PrerequisiteError):
            verify_plan(depth,[dict(scenario='parking',demand_factors=[1.0],comparison_policies=2)])
        with self.assertRaises(PrerequisiteError):
            verify_plan(depth,[dict(scenario='parking',demand_factors=[0.9,1.0])])
        verify_plan(depth,[dict(scenario='parking',demand_factors=[0.9,1.0],comparison_policies=2)])

    def test_one_parking_group_costs_one_baseline_plus_two_candidates(self):
        request='长江村路停车拥堵，比较至少两个停车备选方案，最多3次仿真，不复跑'
        config,catalog,comparisons,scope,permissions,tool=planning_inputs(request,Path('source'),CATALOG)
        self.assertEqual(comparisons['research_depth']['minimum_candidates'],2)
        self.assertEqual(scope['parking_cost']['minimum_runs_per_group'],3)
        self.assertEqual(scope['regional_design']['minimum_distinct_candidates_per_parking_group'],2)
        self.assertEqual(permissions['intents'],['execute'])
        branches=[x['properties']['intent']['enum'][0] for x in tool['parameters']['properties']['decision']['anyOf']]
        self.assertEqual(branches,['execute'])
        obligation=study_units(config,comparisons)['required_design']
        self.assertEqual(obligation['strategies'],['parking'])
        self.assertEqual(obligation['minimum_distinct_treatments'],2)
        self.assertEqual(obligation['minimum_native_scenes_at_given_factors'],3)
        self.assertFalse(obligation['combination_group_counts_toward_this_requirement'])
        experiment=tool['parameters']['properties']['decision']['anyOf'][0]['properties']['experiments']['items']
        self.assertIn('完整对照组不计入',experiment['description'])
        draft=SimpleNamespace(history=[dict(content=json.dumps(dict(execution_scope=scope,
            study_units=study_units(config,comparisons)),ensure_ascii=False))])
        feedback=correction_feedback(draft,'invalid_prerequisite','停车候选数量不足')
        self.assertEqual(feedback['required_study_design'],obligation)
        self.assertIn('strategies=[parking]',feedback['next_step'])


if __name__=='__main__':unittest.main()
