import copy
import unittest

from traffic_agent.decision_analysis import decision_analysis, spatial_effects


def record(name, factor, network=100, neighbor=10, policy=None, eligible=True):
    return dict(name=name, demand_factor=factor, changes=[] if policy is None else [{'parking': policy}],
                execution={'status': 'completed'}, metrics={
                    'network': {'running_time_pcu_h': network}, 'neighbor': {'running_time_pcu_h': neighbor}},
                comparison=dict(eligible=eligible, violations=[] if eligible else ['邻区超限'], new_neighbor_oversaturated_km=0))


def experiment():
    return dict(status='completed', task={'demand_factors': [.9, 1, 1.1]}, runs=[record(f'base_{f}', f) for f in [.9, 1, 1.1]])


class DecisionAnalysisTests(unittest.TestCase):
    def test_worst_case_not_average_and_exact_matching(self):
        state = experiment()
        for f, a, b in zip([.9, 1, 1.1], [60, 70, 99], [95, 95, 95]):
            state['runs'] += [record(f'A_{f}', f, a, policy=1), record(f'B_{f}', f, b, policy=2)]
        original = copy.deepcopy(state)
        result = decision_analysis(state)
        self.assertEqual(result['recommendation'], 'B_1')
        self.assertEqual(result['eligible_count'], 2)
        self.assertEqual(result['frontier_count'], 1)
        self.assertEqual(result['policies'][0]['dominated_by'], ['B_1'])
        self.assertEqual(state, original)  # Never rewrite archived policy decisions.
        # Increase only the high-demand baseline: correct pairing changes the worst case.
        state['runs'][2]['metrics']['network']['running_time_pcu_h'] = 200
        self.assertEqual(decision_analysis(state)['recommendation'], 'A_1')

    def test_one_violation_or_missing_demand_cannot_be_compensated(self):
        for defect in ('failure', 'missing', 'unfinished'):
            state = experiment()
            for f in [.9, 1, 1.1]:
                state['runs'].append(record(f'A_{f}', f, 50, policy=1))
            if defect == 'failure':
                state['runs'][-1]['comparison'].update(eligible=False, violations=['邻区超限'])
            elif defect == 'missing':
                state['runs'].pop()
            else:
                state['runs'][-1]['execution']['status'] = 'running'
            self.assertIsNone(decision_analysis(state)['recommendation'])

    def test_no_cherry_picking_of_conflicting_repeats(self):
        state = experiment()
        for f in [.9, 1, 1.1]:
            state['runs'].append(record(f'A_{f}', f, 95, policy=1))
        state['runs'].append(record('better_repeat', 1, 80, policy=1))
        self.assertIsNone(decision_analysis(state)['recommendation'])

    def test_empty_neighbor_allowed_but_zero_network_baseline_not_divided(self):
        state = experiment()
        for b in state['runs']:
            b['metrics']['neighbor']['running_time_pcu_h'] = 0
        for f in [.9, 1, 1.1]:
            state['runs'].append(record(f'A_{f}', f, 95, neighbor=0, policy=1))
        self.assertEqual(decision_analysis(state)['recommendation'], 'A_1')
        state['runs'][0]['metrics']['network']['running_time_pcu_h'] = 0
        self.assertIsNone(decision_analysis(state)['recommendation'])

    def test_single_demand_and_running_jobs_never_claim_final_robustness(self):
        state = experiment()
        for f in [.9, 1, 1.1]:
            state['runs'].append(record(f'A_{f}', f, 95, policy=1))
        state['status'] = 'running'
        self.assertFalse(decision_analysis(state)['finalized'])
        self.assertIsNone(decision_analysis(state)['recommendation'])
        state['status'] = 'completed'
        state['task']['demand_factors'] = [1]
        self.assertIsNone(decision_analysis(state)['recommendation'])

    def test_policy_order_does_not_change_identity(self):
        state = experiment()
        for f in [.9, 1, 1.1]:
            r = record(f'A_{f}', f, 95, policy=1)
            r['changes'] = [{'parking': 1, 'from_node': 1}, {'parking': 2, 'from_node': 3}]
            if f == 1:
                r['changes'].reverse()
            state['runs'].append(r)
        self.assertEqual(len(decision_analysis(state)['policies']), 1)
        self.assertIsNotNone(decision_analysis(state)['recommendation'])

    def test_tolerance_cannot_select_a_dominated_policy(self):
        state = experiment()
        for f in [.9, 1, 1.1]:
            state['runs'] += [record(f'A_{f}', f, 95-1e-10, neighbor=9, policy=1),
                              record(f'B_{f}', f, 95, neighbor=8, policy=2)]
        self.assertEqual(decision_analysis(state)['recommendation'], 'B_1')

    def test_spatial_cost_balance_and_gross_overload(self):
        def link(i, before, after):
            return dict(id=i, name='道路', start=i, end=i+1, group='network', length=1,
                        before=before, after=after)
        rows = [link(1, [10, 100, .8, 3], [10, 200, 1.2, 2]),
                link(2, [10, 300, 1.5, 3], [10, 50, .8, 2])]
        result = spatial_effects(rows)
        self.assertEqual(result['added_time_pcu_h'], 10)
        self.assertEqual(result['saved_time_pcu_h'], 25)
        self.assertEqual(result['net_time_pcu_h'], -15)
        self.assertEqual(result['new_network_overloaded_km'], 1)
        self.assertEqual(result['hotspots'][0]['id'], 1)
        self.assertEqual(result['improvements'][0]['id'], 2)

    def test_near_equal_objectives_cannot_form_a_dominance_cycle(self):
        state = experiment()
        for f in [.9, 1, 1.1]:
            for i, vector in enumerate([(0, 2, 1), (1, 0, 2), (2, 1, 0)]):
                r = record(f'{i}_{f}', f, 100+vector[0]*1e-9, 10+vector[1]*1e-10, policy=i)
                r['comparison']['new_neighbor_oversaturated_km'] = vector[2]*1e-9
                state['runs'].append(r)
        result = decision_analysis(state)
        self.assertEqual(result['frontier_count'], 3)
        self.assertIsNotNone(result['recommendation'])


if __name__ == '__main__':
    unittest.main()
