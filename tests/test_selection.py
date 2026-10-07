import copy
import unittest

from traffic_agent.agent import recommend
from traffic_agent.decision_analysis import decision_analysis
from traffic_agent.selection import select, record_selection
from traffic_agent.task import Task
from test_decision_analysis import experiment, record


def policy(name, time, count=1, neighbor=0, gross=0):
    return dict(name=name, worst=[time, neighbor, gross], changes=[{'parking': 2, 'from_node': i} for i in range(count)])


class SelectionTests(unittest.TestCase):
    def test_equivalent_prefers_simple_even_if_tiny_exact_dominance(self):
        result = select([policy('complex', -1, 2), policy('simple', -.9995)])
        self.assertEqual(result['recommendation'], 'simple')
        self.assertEqual(len(result['equivalent_candidates']), 2)

    def test_absolute_anchor_prevents_chained_ties_and_order_dependency(self):
        candidates = [policy('a', -1, 3), policy('b', -.9992, 2), policy('c', -.9984)]
        for order in (candidates, list(reversed(candidates))):
            result = select(order)
            self.assertEqual(result['recommendation'], 'b')
            self.assertNotIn('c', result['equivalent_candidates'])

    def test_boundaries_and_neighbor_cost_are_respected(self):
        self.assertEqual(select([policy('a', 0, 2), policy('b', .001)])['recommendation'], 'b')
        self.assertEqual(select([policy('a', 0, 2), policy('b', .00100001)])['recommendation'], 'a')
        self.assertEqual(select([policy('a', 0, 2), policy('b', 0, neighbor=.011)])['recommendation'], 'a')
        self.assertEqual(select([policy('a', 0, 2), policy('b', 0, gross=.002)])['recommendation'], 'a')

    def test_missing_or_failed_candidate_cannot_be_rescued_by_tolerance(self):
        state = experiment()
        for f in [.9, 1, 1.1]:
            state['runs'].append(record(f'complex_{f}', f, 95, policy=1))
            state['runs'].append(record(f'simple_{f}', f, 95.0001, policy=2, eligible=f != 1.1))
        original = copy.deepcopy(state)
        self.assertEqual(decision_analysis(state, 'tolerance-v2')['recommendation'], 'complex_1')
        state['runs'] = state['runs'][:-2]
        self.assertIsNone(decision_analysis(state, 'tolerance-v2')['recommendation'])
        self.assertNotIn('recommendation_version', original['task'])

    def test_record_selection_zero_eligible_and_nonfinite(self):
        self.assertIsNone(record_selection([record('fail', 1, policy=2, eligible=False)])['recommendation'])
        self.assertIsNone(recommend([record('missing_evidence', 1, policy=2)], 'tolerance-v2'))
        with self.assertRaises(ValueError):
            select([policy('bad', float('nan'))])

    def test_old_task_serialization_and_robust_results_preserved(self):
        task = Task.from_dict({})
        self.assertNotIn('recommendation_version', task.as_dict())
        task = Task.from_dict(dict(recommendation_version='tolerance-v2'))
        self.assertEqual(task.as_dict()['recommendation_version'], 'tolerance-v2')
        with self.assertRaises(ValueError):
            Task.from_dict(dict(recommendation_version='unknown'))
        state = experiment()
        for f in [.9, 1, 1.1]:
            state['runs'] += [record(f'A_{f}', f, 95, policy=1), record(f'B_{f}', f, 95.0001, policy=2)]
        original = copy.deepcopy(state)
        self.assertEqual(decision_analysis(state)['recommendation'], 'A_1')
        self.assertEqual(decision_analysis(state, 'tolerance-v2')['recommendation'], 'B_1')
        self.assertEqual(state, original)

    def test_absolute_compatibility_cannot_silently_mix_units(self):
        for settings in [dict(recommendation_version='tolerance-v2', recommendation_tolerance_pcu_h=.1),
                         dict(search_mode='robust', recommendation_tolerance_pcu_h=.1)]:
            with self.assertRaises(ValueError):
                Task.from_dict(settings)
        self.assertEqual(Task.from_dict({'recommendation_tolerance_pcu_h': .1}).selection_mode, .1)
        self.assertNotIn('recommendation_tolerance_pcu_h', Task.from_dict({}).as_dict())
        explicit = Task.from_dict({'recommendation_tolerance_pcu_h': 0})
        self.assertIn('recommendation_tolerance_pcu_h', explicit.as_dict())

    def test_rule_labels_distinguish_versions_and_search(self):
        from traffic_agent.tradeoffs import selection_rule
        self.assertIn('0.125 pcu', selection_rule({'recommendation_tolerance_pcu_h': .125}))
        self.assertIn('0.001个百分点', selection_rule({'recommendation_version':'tolerance-v2'}))
        self.assertIn('paired-minimax-v1', selection_rule({'search_mode':'robust'}))
