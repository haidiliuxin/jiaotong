import copy
import unittest

from traffic_agent.study_comparison import comparison,programme_comparisons,VERSION,LEGACY_VERSION
import test_strategy_evidence as helpers


class StudyComparisonTests(unittest.TestCase):
    def state(self, **kwargs):
        return helpers.StrategyEvidenceTests().state(**kwargs)

    def test_combination_can_be_faster_but_rejected_while_parking_passes(self):
        state = self.state(bad_combination=True)
        result = comparison(state)['factors'][0]
        self.assertEqual([p['status'] for p in result['policies']], ['admissible', 'admissible', 'rejected'])
        contrast = result['combination_contrasts'][0]
        self.assertEqual((contrast['single_status'], contrast['combined_status']), ('admissible', 'rejected'))
        self.assertEqual(contrast['network_time_difference_pcu_h'], -10)
        self.assertEqual(result['policies'][0]['label'], '停车2级')
        self.assertEqual(contrast['single_ref'], 'synthetic:S1_parking')

    def test_unmeasured_or_failed_control_is_not_a_pass_and_missing_is_not_zero(self):
        state = self.state()
        for r in state['runs']:
            r.pop('oneway_response', None)
        self.assertEqual([p['status'] for p in comparison(state)['factors'][0]['policies']],
                         ['admissible', 'unmeasured', 'unmeasured'])
        state['runs'][-1]['oneway_response'] = dict(passed=False)
        self.assertEqual(comparison(state)['factors'][0]['policies'][-1]['status'], 'rejected')
        state['runs'][-1]['metrics']['neighbor'].pop('running_time_pcu_h')
        self.assertIsNone(comparison(state)['factors'][0]['combination_contrasts'][0]['neighbor_time_difference_pcu_h'])
        state['runs'][0]['execution']['status'] = 'failed'
        result = comparison(state)['factors'][0]
        self.assertIsNone(result['baseline_ref'])
        self.assertEqual(result['combination_contrasts'], [])
        self.assertTrue(all(p['status'] == 'unmeasured' for p in result['policies']))

    def test_never_contrast_mismatched_design_demand_or_unfinished_outputs(self):
        state = self.state()
        state['runs'][-1]['changes'][0]['parking'] = 1
        self.assertEqual(comparison(state)['factors'][0]['combination_contrasts'], [])
        state = self.state()
        extra = copy.deepcopy(state['runs'][-1])
        extra.update(name='foreign_demand', demand_factor=1.1)
        state['runs'][-1]['execution']['status'] = 'running'
        state['runs'].append(extra)
        result = comparison(state)['factors'][0]
        self.assertEqual(result['combination_contrasts'], [])
        self.assertNotIn('foreign_demand', str(result))
        self.assertNotIn('S3_combined', str(result))

    def test_control_core_neighbor_and_eligibility_remain_four_independent_checks(self):
        state=self.state(bad_combination=True)
        state['runs'][-1]['comparison']['new_neighbor_oversaturated_km']=1.03054
        result=comparison(state)['factors'][0]['policies']
        self.assertFalse(result[0]['checks']['control_applicable'])
        checks=result[-1]['checks']
        self.assertEqual(checks,dict(control_applicable=True,control_passed=True,
            core_time_decreased=True,neighbor_limits_passed=False,recommendation_admissible=False))
        self.assertEqual(result[-1]['control_directions'],[dict(closed=[1,2],retained=[2,1])])
        self.assertEqual(result[-1]['neighbor_limits']['new_oversaturated_limit_km'],.1)
        state['runs'][-1].pop('oneway_response')
        unknown=comparison(state)['factors'][0]['policies'][-1]['checks']
        self.assertIsNone(unknown['control_passed'])
        self.assertTrue(unknown['core_time_decreased'])
        self.assertFalse(unknown['recommendation_admissible'])
        state['runs'][-1]['comparison']['eligible']=True
        self.assertIsNone(comparison(state)['factors'][0]['policies'][-1]['checks']['recommendation_admissible'])

    def test_completed_coverage_is_per_group_not_expanded_to_all_policy_families(self):
        a,b=self.state(scenario='parking'),self.state()
        a['id']='parking';b['id']='oneway'
        extra=copy.deepcopy(a['runs'][-1]);extra['name']='parking_2';a['runs'].append(extra)
        failed=copy.deepcopy(b['runs'][-1]);failed['name']='failed';failed['execution']['status']='failed';b['runs'].append(failed)
        plan=dict(studies=[dict(id=f'study_{i}',job_id=s['id'],title=s['id'],task=s['task']) for i,s in enumerate((a,b),1)])
        states={s['id']:s for s in (a,b)}
        old=programme_comparisons(plan,states)
        self.assertTrue(all(g['version']==LEGACY_VERSION for g in old))
        self.assertTrue(all('completed_scenes' not in g and 'checks' not in g['factors'][0]['policies'][0] for g in old))
        groups=programme_comparisons(dict(plan,study_comparison_protocol=VERSION),states)
        self.assertEqual(groups[0]['coverage_text'],'新基准1场、仅停车2场；已测需求×1')
        self.assertEqual(groups[1]['coverage_text'],'新基准1场、仅停车1场、仅交通组织1场、停车＋交通组织1场；已测需求×1')
        self.assertEqual([len(g['completed_scenes']) for g in groups],[3,4])
        self.assertEqual(groups[0]['factors'][0]['combination_contrasts'],[])
        self.assertTrue(all(r['ref'].startswith(g['job_id']+':') for g in groups for r in g['completed_scenes']))

    def test_report_uses_actual_directions_limits_and_separate_failed_constraint(self):
        from traffic_agent.report import comparison_lines
        state=self.state(bad_combination=True)
        state['runs'][-1]['comparison']['new_neighbor_oversaturated_km']=1.03054
        text='\n'.join(comparison_lines([comparison(state)]))
        for expected in ('实际完成（由场景记录生成）','禁行1→2；保留2→1','| 通过 | 下降 | 超门槛 | 不具备 |',
                         '1.03054km（冻结上限0.10000km）','不相加为联合实施收益'):
            self.assertIn(expected,text)
        self.assertEqual(comparison_lines([comparison(state,version=LEGACY_VERSION)]),[])
        turn=comparison(self.state(scenario='circulation'))['factors'][0]['policies'][-1]
        self.assertEqual(turn['control_directions'],[dict(turn=[1,2,3])])
        tiny=comparison(state)
        tiny['factors'][0]['policies'][0]['neighbor_limits']['neighbor_time_change_pct']=.00000014
        self.assertIn('1.4e-07%', '\n'.join(comparison_lines([tiny])))


if __name__ == '__main__':
    unittest.main()
