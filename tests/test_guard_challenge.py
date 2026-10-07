"""Synthetic controller tests; real TranStar performance is tested by sealed runs."""
import copy
import unittest

from test_agent import link
from traffic_agent.guard_challenge import (
    LOGICAL_BUDGET, STRATEGIES, candidate_actions, challenge_task, fixed_policies, protocol)


class GuardChallengeTests(unittest.TestCase):
    def setUp(self):
        self.task = challenge_task()
        self.rows = [link(i, 2*i+1, 2*i+2, speed=10, flow=600-100*i) for i in range(3)]
        self.initial = [dict(from_node=r['from_node'], to_node=r['to_node'],
                             old_parking=3, parking=2) for r in self.rows]
        self.region = dict(id='high', initial=self.initial)
        self.bases = {f: dict(core_rows=self.rows) for f in self.task.demand_factors}

    def drive(self, strategy, failures):
        controller = candidate_actions(strategy, self.region, self.task, self.bases)
        sent, actions = None, []
        while True:
            try:
                action = controller.send(sent)
            except StopIteration as finished:
                return actions, finished.value
            actions.append(copy.deepcopy(action))
            after = copy.deepcopy(self.rows)
            # Make joint feedback prefer the second link, while frozen baseline
            # priority still puts the first link ahead of it.
            after[0]['speed_kmh'] = 8
            after[1]['speed_kmh'] = 30
            sent = dict(name=action['name'], changes=action['changes'],
                        demand_factor=action['factor'],
                        comparison=dict(eligible=action['policy'] not in failures),
                        core_rows=after,
                        metrics=dict(core=dict(weighted_speed_kmh=30)))

    def test_frozen_safety_gate_and_equal_logical_budget(self):
        self.assertEqual(self.task.max_new_network_oversaturated_km, 0.1)
        frozen = protocol(self.task, [])
        self.assertEqual(frozen['logical_budget_per_region_strategy'], 16)
        self.assertEqual(frozen['strategies'], list(STRATEGIES))
        self.assertEqual(frozen['max_physical_scenes'], 67)
        for strategy in STRATEGIES:
            actions, summary = self.drive(strategy, failures={1, 2, 3, 4})
            self.assertEqual(summary['logical_scenes']+summary['unused_budget'], LOGICAL_BUDGET)
            self.assertLessEqual(summary['logical_scenes'], LOGICAL_BUDGET)
            self.assertEqual(summary['candidates_started'], 4)
            self.assertEqual(len([a for a in actions if a['factor'] == 1.1]), 4)

    def test_strong_fixed_grid_includes_every_core_singleton(self):
        self.assertEqual(fixed_policies(self.region, self.bases),
                         [self.initial, self.initial[:1], self.initial[1:2], self.initial[2:3]])
        for failures in (set(), {1, 2, 3, 4}):
            actions, _ = self.drive('fixed_full_singletons', failures)
            self.assertEqual([a['changes'] for a in actions if a['factor'] == 1.1],
                             fixed_policies(self.region, self.bases))

    def test_feedback_revises_then_backtracks_without_phantom_candidate(self):
        actions, summary = self.drive('feedback_backtracking_v2', {1, 2, 3, 4})
        self.assertEqual([a['changes'] for a in actions],
                         [self.initial, self.initial[1:2], self.initial[:1], self.initial[2:3]])
        self.assertEqual(len(summary['backtracks']), 2)
        self.assertEqual([b['next_link'] for b in summary['backtracks']], [[1, 2], [5, 6]])
        self.assertEqual(summary['early_rejections'][-1]['missing'], [1.0, 0.9])

    def test_short_core_uses_only_distinct_predefined_candidates(self):
        self.region['initial'] = self.initial[:1]
        fixed, fixed_end = self.drive('fixed_full_singletons', {1, 2})
        feedback, feedback_end = self.drive('feedback_backtracking_v2', {1, 2})
        self.assertEqual([a['changes'] for a in fixed], [self.initial[:1]])
        self.assertEqual(fixed_end['candidates_started'], 1)
        self.assertEqual(len(feedback), 1)
        self.assertEqual(feedback_end['backtracks'], [])


if __name__ == '__main__':
    unittest.main()
