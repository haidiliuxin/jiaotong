"""Synthetic observations here test controller logic, never model performance."""
import copy
import unittest
from datetime import datetime, timedelta, timezone

from test_agent import link
from traffic_agent.challenge import STRATEGIES, candidate_actions, challenge_task, fixed_policies
from traffic_agent.study_runner import strategy_result
from traffic_agent.study_audit import replay
from traffic_agent.decision_analysis import policy_key


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.task = challenge_task()
        self.rows = [link(i, 2*i+1, 2*i+2) for i in range(3)]
        self.initial = [dict(from_node=r['from_node'], to_node=r['to_node'], old_parking=3, parking=2) for r in self.rows]
        self.region = dict(id='high', initial=self.initial)
        self.bases = {f: dict(core_rows=self.rows) for f in self.task.demand_factors}

    def drive(self, strategy, failures=(1, 2), bottleneck=True):
        generator = candidate_actions(strategy, self.region, self.task, self.bases)
        sent, actions = None, []
        while True:
            try:
                action = generator.send(sent)
            except StopIteration as end:
                return actions, end.value
            actions.append(copy.deepcopy(action))
            after = copy.deepcopy(self.rows)
            for row, speed in zip(after, [11, 20, 15]):
                row.update(speed_kmh=speed, adjusted_load_ratio=2 if bottleneck else 0.9)
            sent = dict(name=action['name'], changes=action['changes'], demand_factor=action['factor'],
                        comparison=dict(eligible=action['policy'] not in failures), core_rows=after,
                        metrics=dict(core=dict(weighted_speed_kmh=20 if bottleneck else 30)))

    def test_backtracking_tries_unexplored_original_core_after_single_failure(self):
        old, old_summary = self.drive('feedback_halving')
        new, new_summary = self.drive('feedback_backtracking')
        self.assertEqual(len(old), 2)
        self.assertEqual(new[:2], [dict(a, strategy='feedback_backtracking',
            name=a['name'].replace('feedback_halving', 'feedback_backtracking'),
            observed_runs=[n.replace('feedback_halving','feedback_backtracking') for n in a['observed_runs']]) for a in old])
        self.assertEqual(new[1]['changes'], [self.initial[1]])
        self.assertEqual(new[2]['changes'], [self.initial[0]])
        self.assertEqual([a['factor'] for a in new], [1.1, 1.1, 1.1, 1.0, 0.9])
        self.assertEqual(new_summary['logical_scenes'], 9)
        self.assertEqual(len(new_summary['backtracks']), 1)
        self.assertEqual(old_summary['early_rejections'][1]['missing'], [1.0, 0.9])

    def test_fixed_sparse_sequence_does_not_depend_on_policy_outcomes(self):
        planned = fixed_policies(self.region, self.bases)
        self.assertEqual(planned, [self.initial, self.initial[:1], self.initial[1:2]])
        for failures in ((1,2,3), (), (2,)):
            actions, summary = self.drive('fixed_diverse', failures)
            policies = [a['changes'] for a in actions if a['factor'] == 1.1]
            self.assertEqual(policies, planned)
            self.assertLessEqual(summary['logical_scenes'], 13)

    def test_priority_uses_only_baseline_and_deterministic_tie_break(self):
        self.rows[2]['flow_pcu_h'] *= 2
        policies = fixed_policies(self.region, self.bases)
        self.assertEqual(policies[1:], [self.initial[2:3], self.initial[:1]])

    def test_failed_probe_keeps_previous_complete_winner(self):
        actions, summary = self.drive('feedback_backtracking', failures=(2,3))
        self.assertEqual([a['factor'] for a in actions[:3]], [1.1,1.0,0.9])
        self.assertEqual(summary['early_rejections'][0]['policy'], 2)
        self.assertEqual(summary['logical_scenes'], 9)
        # Retained observations, rather than the last attempt alone, are passed
        # to the shared audited decision_analysis by the runner.
        self.assertEqual(len([a for a in actions if a['policy']==1]), 3)

    def test_all_success_has_no_duplicate_policies_and_fits_budget(self):
        for strategy in STRATEGIES:
            actions, summary = self.drive(strategy, failures=())
            keys = [policy_key(a['changes']) for a in actions if a['factor']==1.1]
            self.assertEqual(len(keys),len(set(keys)))
            self.assertLessEqual(summary['logical_scenes'],13)
            self.assertEqual(summary['logical_scenes']+summary['unused_budget'],13)

    def test_single_core_cannot_invent_an_alternative(self):
        self.region['initial'] = self.initial[:1]
        actions, summary = self.drive('feedback_backtracking')
        self.assertEqual(len(actions),1)
        self.assertEqual(summary['backtracks'],[])
        fixed, _ = self.drive('fixed_diverse')
        self.assertEqual([a['changes'][0]['parking'] for a in fixed],[2,1])

    def test_satisfied_target_stops_without_spending_optional_budget(self):
        actions, summary = self.drive('feedback_backtracking', (), False)
        self.assertEqual(len(actions),3)
        self.assertEqual(summary['unused_budget'],6)

    def test_unregistered_budget_rejected(self):
        self.task.max_runs=14
        with self.assertRaises(ValueError):
            next(candidate_actions('feedback_backtracking', self.region, self.task, self.bases))

    def replay_fixture(self):
        # Synthetic observations only for unit testing the chronology auditor.
        clock = datetime(2026, 1, 1, tzinfo=timezone.utc)
        def stamp():
            nonlocal clock
            clock += timedelta(seconds=1)
            return clock.isoformat()
        def record(name, changes, factor, eligible=True):
            return dict(name=name, physical_run=name, changes=changes, demand_factor=factor,
                        execution=dict(status='completed', started=stamp(), finished=stamp()),
                        metrics=dict(network=dict(running_time_pcu_h=95 if changes else 100),
                                     neighbor=dict(running_time_pcu_h=10), core=dict(weighted_speed_kmh=20)),
                        core_rows=[dict(r, speed_kmh=s) for r,s in zip(self.rows,[11,20,15])] if changes else copy.deepcopy(self.rows),
                        comparison=dict(eligible=eligible, violations=[] if eligible else ['test rejection'], new_neighbor_oversaturated_km=0),
                        risk_review=dict(new_network_oversaturated_km=0, inner_neighbor_time_change_pct=0, gross_time_added_pcu_h=0))
        physical = [record('baseline' if f == 1 else f'baseline_{f:g}', [], f) for f in (1, 1.1, 0.9)]
        bases = {r['demand_factor']: r for r in physical}
        entries = {s: dict(runs=list(physical)) for s in STRATEGIES}
        controllers = {s: candidate_actions(s, self.region, self.task, bases) for s in entries}
        sent = {s: None for s in entries}
        traces, cache = [], {}
        while controllers:
            batch = []
            for strategy, controller in list(controllers.items()):
                try:
                    action = controller.send(sent[strategy])
                except StopIteration as end:
                    entries[strategy]['summary'] = end.value
                    del controllers[strategy]
                    continue
                trace = dict(action, sequence=len(traces)+1, decided_at=stamp())
                traces.append(trace)
                batch.append(trace)
            for trace in batch:
                key = (policy_key(trace['changes']), trace['factor'])
                shared = key in cache
                if not shared:
                    cache[key] = record(f'case_{len(cache)+1:03d}', trace['changes'], trace['factor'], len(trace['changes']) == 1 and trace['changes'][0]['from_node'] == 1)
                    physical.append(cache[key])
                p = cache[key]
                view = dict(p, name=trace['name'])
                sent[trace['strategy']] = view
                entries[trace['strategy']]['runs'].append(view)
                trace.update(shared=shared, physical_run=p['name'], observed_at=stamp())
        repeated = record('baseline_repeat', [], 1)
        physical.append(repeated)
        for entry in entries.values():
            entry['runs'].append(repeated)
            entry['result'] = strategy_result(self.task, entry['runs'], entry['summary'])
        return (self.task, {'regions': [dict(self.region, status='selected')]},
                dict(trace=traces, runs=physical, run_count=len(physical),
                     regions={'high': dict(status='completed', strategies=entries)}))

    def audited_replay(self, task, selection, state):
        return replay(task, selection, state, arms=STRATEGIES,
                      controller_factory=candidate_actions, budget=13)

    def test_three_arm_replay_and_shared_billing(self):
        fixture = self.replay_fixture()
        result = self.audited_replay(*fixture)
        self.assertEqual(result['unique_policy_scenes'], 5)
        self.assertEqual(result['logical_scenes'], 24)
        outcomes = fixture[2]['regions']['high']['strategies']
        self.assertEqual(outcomes['feedback_halving']['result']['recommendation'],'baseline')
        for strategy in ('fixed_diverse','feedback_backtracking'):
            self.assertNotEqual(outcomes[strategy]['result']['recommendation'],'baseline')

    def test_audit_rejects_leaked_feedback_or_unfair_counts(self):
        for defect in ('time', 'sharing', 'budget', 'backtrack', 'missing_arm'):
            task, selection, state = self.replay_fixture()
            if defect=='time':
                state['trace'][0]['decided_at']=state['trace'][0]['observed_at']
            elif defect=='sharing':
                state['trace'][1]['shared']=False
            elif defect=='budget':
                state['regions']['high']['strategies']['feedback_backtracking']['summary']['logical_scenes']-=1
            elif defect=='backtrack':
                next(t for t in state['trace'] if t['strategy']=='feedback_backtracking' and t['policy']==3)['changes']=self.initial[2:]
            else:
                del state['regions']['high']['strategies']['fixed_diverse']
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                self.audited_replay(task, selection, state)


if __name__ == '__main__':
    unittest.main()
