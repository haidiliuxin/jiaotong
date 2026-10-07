import copy
import unittest
from traffic_agent.agent import recommend
from traffic_agent.task import Task
from traffic_agent.tradeoffs import tradeoffs


def candidate(name, time, count=1, eligible=True):
    return dict(name=name, changes=[{'parking': 2}] * count,
                comparison={'eligible': eligible}, metrics={'network': {'running_time_pcu_h': time}})


class SelectionTests(unittest.TestCase):
    def test_zero_preserves_exact_ranking_and_tolerance_prefers_simple(self):
        rows = [candidate('simple', 10.125), candidate('combined', 10, 2)]
        original = copy.deepcopy(rows)
        self.assertEqual(recommend(rows)['name'], 'combined')
        self.assertEqual(recommend(rows, .125)['name'], 'simple')
        self.assertEqual(recommend(rows, .124)['name'], 'combined')
        self.assertEqual(rows, original)

    def test_tolerance_never_admits_rejected_candidates(self):
        self.assertEqual(recommend([candidate('bad', 0, eligible=False), candidate('good', 10)], 100)['name'], 'good')
        self.assertIsNone(recommend([candidate('bad', 0, eligible=False)], 100))

    def test_invalid_tolerance_rejected_and_archives_default_zero(self):
        self.assertEqual(Task.from_dict({}).recommendation_tolerance_pcu_h, 0)
        for value in [-1, True, float('nan'), float('inf'), '1']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                Task.from_dict({'recommendation_tolerance_pcu_h': value})

    def test_tradeoffs_preserve_eligible_warning_and_saved_state(self):
        state = {'runs': [{'name': 'combined', 'demand_factor': 1.1,
                 'comparison': {'eligible': True, 'delta': {'network': {
                     'running_time_change_pct': -.2, 'oversaturated_km_change': .86}}}}]}
        original = copy.deepcopy(state)
        warnings = tradeoffs(state)
        self.assertTrue(warnings[0]['eligible'])
        self.assertIn('0.860000', warnings[0]['warnings'][0])
        self.assertEqual(state, original)
