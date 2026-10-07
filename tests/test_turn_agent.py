import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from traffic_agent.agent import plan
from traffic_agent.data import scoped_metrics
from traffic_agent.task import Task, parse_request
from traffic_agent.turn_agent import analyze_turn, response


class TurnAgentTests(unittest.TestCase):
    def test_ordinary_run_opens_only_explicit_turn_subflow(self):
        from traffic_agent.agent import Agent
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            explicit = Task.from_dict(dict(scenario='circulation', turn=[1, 2, 3]))
            legacy = Task.from_dict(dict(scenario='circulation'))
            with patch('traffic_agent.agent.runtime_lock', side_effect=ValueError('entry reached')) as lock, \
                    patch('traffic_agent.report.write_report'):
                blocked = Agent(legacy, root/'source', root/'legacy')
                blocked.run()
                self.assertEqual(blocked.state['status'], 'needs_validation')
                lock.assert_not_called()
                accepted = Agent(explicit, root/'source', root/'explicit')
                with self.assertRaisesRegex(ValueError, 'entry reached'):
                    accepted.run()
                lock.assert_called_once()

    def test_explicit_turn_is_bounded_and_reserves_full_grid(self):
        task = parse_request('禁转与停车组合；转向：4797→5990→5991；需求系数：0.9,1,1.1')
        self.assertEqual(task.turn, [4797, 5990, 5991])
        self.assertEqual(task.max_runs, 13)
        self.assertEqual(plan(task)['required_runs'], 13)
        self.assertEqual(plan(task)['capability']['state'], 'executable')
        self.assertEqual(plan(parse_request('禁转与停车组合'))['capability']['state'], 'needs_validation')
        for fields in [dict(scenario='parking', turn=[1, 2, 3]),
                       dict(scenario='circulation', turn=[1, 2, True]),
                       dict(scenario='circulation', turn=[1, 2, 1]),
                       dict(scenario='circulation', turn=[1, 2, 3], links=[[1, 2]]),
                       dict(scenario='circulation', turn=[1, 2, 3], max_runs=12)]:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Task.from_dict(fields)
        for text in ['禁转；转向：1→2', '禁转；转向：', '禁转；转向：1→2→3；转向：4→5→6']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_request(text)

    def test_complete_grid_keeps_failed_primary_visible_at_other_demand(self):
        from test_agent import link
        with tempfile.TemporaryDirectory() as folder:
            task = Task.from_dict(dict(scenario='circulation', turn=[1, 2, 3], repeat_baseline=False))
            baseline = [link(0, 2, 3)]
            base = dict(name='baseline', changes=[], demand_factor=1.0, execution={'assigned_car_od_sum': 100})
            agent = SimpleNamespace(job=Path(folder), task=task, persist=lambda: None, event=lambda *a: None,
                                    state=dict(runs=[base], decisions=[], sensitivity=[], turn_access_profile={'version': 2}))
            calls = []
            def simulate(name, changes, factor=1):
                protocol = json.loads((agent.job/'factorial_protocol.json').read_text(encoding='utf-8'))
                self.assertFalse(protocol['full_demand_grid'])
                label = name.split('_')[-2:]
                speed = 10 if not changes else 40 if label == ['S3', 'combined'] else 30 if label == ['S2', 'turn'] else 20
                rows = [link(0, 2, 3, speed=speed)]
                record = dict(name=name, changes=changes, demand_factor=factor, scenario=name,
                              metrics=scoped_metrics(rows, agent.scope), execution={'assigned_car_od_sum': factor*100})
                agent.state['runs'].append(record)
                calls.append((name, factor))
                return rows, record
            agent.simulation = simulate
            def measured(_agent, matched, record):
                self.assertEqual(matched['demand_factor'], record['demand_factor'])
                # Even spectacular modeled benefits cannot rescue an ineffective ban.
                ok = 'S2_turn' not in record['name'] and record['demand_factor'] != .9
                return {'passed': ok}
            with patch('traffic_agent.turn_agent.response', side_effect=measured):
                analyze_turn(agent, baseline, base, {'selected': baseline, 'turn': task.turn})
            self.assertEqual(len(calls), 11)  # baseline already supplied; 3*4 total.
            from traffic_agent.action_execution import factorial_sequence
            sequence=factorial_sequence(task.as_dict(),baseline_done=True)
            self.assertEqual(calls,[(r['name'],r['demand_factor']) for r in sequence])
            for run in agent.state['runs'][1:]:
                expected=next(x for x in sequence if x['name']==run['name'])
                for change in run['changes']:
                    if change.get('kind')!='turn':self.assertEqual(change['parking'],expected['parking_level'])
            self.assertTrue(agent.state['factorial_design']['full_demand_grid'])
            self.assertEqual(agent.state['recommendation'], 'S3_combined')
            self.assertEqual(agent.state['recommendation_by_car_demand']['0.9'], 'demand_0p900_S1_parking')
            self.assertFalse(agent.state['robust_across_tested_demand'])
            self.assertFalse(agent.state['turn_adapter_verified'])
            observed = next(s for s in agent.state['sensitivity'] if s['car_demand_factor'] == .9)
            self.assertEqual(observed['fixed_primary_policy'], 'demand_0p900_S3_combined')
            self.assertFalse(observed['comparison']['eligible'])

    def test_modal_residual_and_zero_car_baseline_cannot_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'policy/cache').mkdir(parents=True)
            (root/'policy/cache/ManageNM.dat').write_text('1\n1 2 3\n')
            def record(q):
                return dict(scenario='base', turn_access_evidence={'modes': {
                    m: {'turn_totals': [{'turn': [1, 2, 3], 'flow': q if m == 'car' else 0}]}
                    for m in ('car', 'taxi', 'motor', 'truck')}})
            base, policy = record(10), record(0)
            policy.update(scenario='policy', changes=[dict(kind='turn', turn=[1, 2, 3])])
            graph = SimpleNamespace(validate=lambda turns: {'eligible': True})
            with patch('traffic_agent.turn_agent.restrictions', return_value={(1, 2, 3)}), patch('traffic_agent.turn_agent.TurnGraph.load', return_value=graph):
                agent = SimpleNamespace(job=root)
                self.assertTrue(response(agent, base, policy)['passed'])
                policy['turn_access_evidence']['modes']['taxi']['turn_totals'][0]['flow'] = .01
                self.assertFalse(response(agent, base, policy)['passed'])
                policy['turn_access_evidence']['modes']['taxi']['turn_totals'][0]['flow'] = 0
                self.assertFalse(response(agent, record(0), policy)['passed'])


if __name__ == '__main__':
    unittest.main()
