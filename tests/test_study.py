import copy
import unittest
from datetime import datetime, timedelta, timezone

from test_agent import link
from traffic_agent.data import diagnose, parking_candidates
from traffic_agent.study import candidate_actions, freeze_regions
from traffic_agent.study_runner import study_task
from traffic_agent.study_runner import risk_review, strategy_result
from traffic_agent.study_audit import replay
from traffic_agent.decision_analysis import policy_key


class RegionStudyTests(unittest.TestCase):
    def setUp(self):
        self.task = study_task()
        self.rows = [link(i, 2*i+1, 2*i+2) for i in range(3)]
        self.initial = [dict(from_node=r['from_node'], to_node=r['to_node'], old_parking=3, parking=2) for r in self.rows]
        self.region = dict(id='high', initial=self.initial)
        self.bases = {f: dict(core_rows=self.rows) for f in self.task.demand_factors}

    def drive(self, strategy, fail=True, bottleneck=True):
        generator = candidate_actions(strategy, self.region, self.task, self.bases)
        sent, actions = None, []
        while True:
            try:
                action = generator.send(sent)
            except StopIteration as stop:
                return actions, stop.value
            actions.append(copy.deepcopy(action))
            after = copy.deepcopy(self.rows)
            for r, speed in zip(after, [11, 20, 15]):
                r['speed_kmh'] = speed
                r['adjusted_load_ratio'] = 2 if bottleneck else 0.9
            sent = dict(name=action['name'], changes=action['changes'], demand_factor=action['factor'],
                        comparison=dict(eligible=not(fail and action['policy'] == 1)), core_rows=after,
                        metrics=dict(core=dict(weighted_speed_kmh=20 if bottleneck else 30)))

    def test_fixed_always_tests_full_strength_after_rejection(self):
        actions, summary = self.drive('fixed')
        self.assertEqual([a['factor'] for a in actions], [1.1, 1.1, 1.0, 0.9])
        self.assertTrue(all(c['parking'] == 1 for c in actions[1]['changes']))
        self.assertEqual(len(actions[1]['changes']), 3)
        self.assertEqual(summary['logical_scenes'], 8)
        self.assertEqual(summary['early_rejections'][0]['missing'], [1.0, 0.9])

    def test_feedback_changes_by_observation_with_same_budget(self):
        actions, summary = self.drive('feedback')
        self.assertEqual(actions[1]['changes'], [self.initial[1]])
        self.assertEqual(actions[1]['observed_runs'], [actions[0]['name']])
        self.assertEqual(summary['logical_scenes'], 8)
        self.assertEqual(summary['candidates_started'], 2)

    def test_full_success_uses_at_most_ten_scenes(self):
        for strategy in ('fixed', 'feedback'):
            actions, summary = self.drive(strategy, fail=False)
            self.assertEqual([a['factor'] for a in actions], [1.1, 1.0, 0.9]*2)
            self.assertEqual(summary['logical_scenes'], 10)
            self.assertEqual(summary['unused_budget'], 0)

    def test_no_bottleneck_stops_feedback_but_not_fixed(self):
        self.assertEqual(self.drive('feedback', False, False)[1]['logical_scenes'], 7)
        self.assertEqual(self.drive('fixed', False, False)[1]['logical_scenes'], 10)

    def test_single_link_failure_does_not_relabel_missing_as_pass(self):
        self.region['initial'] = self.initial[:1]
        actions, summary = self.drive('feedback')
        self.assertEqual(len(actions), 1)
        self.assertEqual(summary['logical_scenes'], 5)
        self.assertIn('单路段', summary['stop_reason'])

    def test_population_is_complete_and_display_stays_compatible(self):
        rows = [link(i, 2*i+1, 2*i+2) for i in range(40)]
        self.assertEqual(len(parking_candidates(rows, self.task)), 40)
        self.assertEqual(len(diagnose(rows, self.task)['candidates']), 30)
        selection = freeze_regions(rows, self.task, [0])
        self.assertEqual(selection['counts']['candidates'], 40)
        self.assertEqual(selection['counts']['legal_seeds'], 39)
        # Identical densities stay together; empty upper strata are not replaced.
        self.assertEqual([r['status'] for r in selection['regions']], ['no_candidate', 'no_candidate', 'selected'])
        self.assertEqual(selection['regions'][2]['seed'], [3, 4])

    def test_density_strata_are_disjoint_and_exclude_full_history(self):
        rows = []
        for cluster, extra in enumerate([0, 1, 2, 3, 4, 5]):
            start = cluster*100+1
            rows.append(link(len(rows), start, start+1))
            for n in range(extra):
                rows.append(link(len(rows), start+1, start+2+n, parking=0))
        selection = freeze_regions(rows, self.task, [])
        used = set()
        for r in selection['regions']:
            self.assertEqual(r['status'], 'selected')
            area = set(r['scope']['core']+r['scope']['neighbor'])
            self.assertFalse(area & used)
            used |= area
        high = selection['regions'][0]
        excluded = freeze_regions(rows, self.task, high['scope']['core'] + high['scope']['neighbor'])
        self.assertNotIn(high['seed'], [r.get('seed') for r in excluded['regions']])

    def test_gross_network_risk_does_not_cancel_relieved_links(self):
        before = [link(0, 1, 2, vc=0.9), link(1, 2, 3, vc=1.1)]
        after = [link(0, 1, 2, vc=1.1, speed=5), link(1, 2, 3, vc=0.9, speed=20)]
        risk = risk_review(before, after, {'core': [0], 'neighbor': [1], 'network': [0, 1]})
        self.assertEqual(risk['new_network_oversaturated_km'], 0.5)
        self.assertGreater(risk['gross_time_added_pcu_h'], 0)
        self.assertGreater(risk['gross_time_saved_pcu_h'], 0)
        self.assertEqual(risk['worst_links'][0]['from_node'], 1)

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
                        core_rows=copy.deepcopy(self.rows),
                        comparison=dict(eligible=eligible, violations=[] if eligible else ['test rejection'], new_neighbor_oversaturated_km=0),
                        risk_review=dict(new_network_oversaturated_km=0, inner_neighbor_time_change_pct=0, gross_time_added_pcu_h=0))
        physical = [record('baseline' if f == 1 else f'baseline_{f:g}', [], f) for f in (1, 1.1, 0.9)]
        bases = {r['demand_factor']: r for r in physical}
        entries = {s: dict(runs=list(physical)) for s in ('fixed', 'feedback')}
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
                    cache[key] = record(f'case_{len(cache)+1:03d}', trace['changes'], trace['factor'], trace['policy'] != 1)
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

    def test_replay_checks_sharing_and_logical_budget(self):
        result = replay(*self.replay_fixture())
        self.assertEqual(result['decisions'], 8)
        self.assertEqual(result['unique_policy_scenes'], 7)
        self.assertEqual(result['shared_observations'], 1)
        self.assertEqual(result['logical_scenes'], 16)

    def test_replay_rejects_posthoc_decision_and_unfair_accounting(self):
        for defect in ('time', 'premature', 'sharing', 'budget', 'changes', 'missing', 'extra'):
            task, selection, state = self.replay_fixture()
            if defect == 'time':
                state['trace'][0]['decided_at'] = state['trace'][0]['observed_at']
            elif defect == 'premature':
                state['trace'][4]['decided_at'] = state['trace'][2]['decided_at']
            elif defect == 'sharing':
                state['trace'][1]['shared'] = False
            elif defect == 'budget':
                state['regions']['high']['strategies']['feedback']['summary']['logical_scenes'] -= 1
            elif defect == 'changes':
                state['trace'][2]['changes'] = self.initial[:1]
            elif defect == 'missing':
                state['trace'].pop()
            else:
                state['run_count'] += 1
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                replay(task, selection, state)


if __name__ == '__main__':
    unittest.main()
