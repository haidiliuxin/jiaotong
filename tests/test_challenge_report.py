"""Synthetic report inputs check missing-data and worst-case presentation."""
import copy
import unittest

from scripts.report_challenge import outcomes
from traffic_agent.challenge import STRATEGIES


class ChallengeReportTests(unittest.TestCase):
    def test_worst_core_uses_all_matching_demands_and_fallback_stays_missing(self):
        changes = [dict(from_node=1, to_node=2, old_parking=3, parking=2)]
        winner = dict(name='chosen', checks=[dict(run=n) for n in ('a','b','c')],
                      changes=changes, worst=[-0.01,0.02])
        entry = dict(summary=dict(logical_scenes=9,budget=13,backtracks=[]),
                     runs=[dict(name=n,comparison=dict(delta=dict(core=dict(running_time_change_pct=v))))
                           for n,v in zip(('a','b','c','unselected'),(-4,-2,-3,-99))],
                     result=dict(analysis=dict(policies=[winner], recommendation='chosen',eligible_count=1),
                                 recommendation='chosen', risks={'preserved':True}))
        strategies = {s:copy.deepcopy(entry) for s in STRATEGIES}
        fallback = strategies[STRATEGIES[1]]['result']
        fallback.update(analysis=dict(policies=[winner],recommendation=None,eligible_count=0),recommendation='baseline',risks=None)
        state = dict(regions=dict(high=dict(strategies=strategies)))
        selection = dict(regions=[dict(id='high',roads=['test'],status='selected'),dict(id='low',status='no_candidate')])
        before = copy.deepcopy(state)
        rows = outcomes(state,selection)
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['core_change'],-2)
        self.assertEqual(rows[0]['risks'],{'preserved':True})
        self.assertFalse(rows[1]['qualified'])
        self.assertIsNone(rows[1]['core_change'])
        self.assertIsNone(rows[1]['network_change'])
        self.assertEqual(rows[1]['changes'],[])
        self.assertEqual(state,before)


if __name__ == '__main__':
    unittest.main()
