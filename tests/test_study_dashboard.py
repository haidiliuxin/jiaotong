import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_agent import link
from traffic_agent.data import save_json, scoped_metrics
from traffic_agent.study_dashboard import study_map, summary
from traffic_agent.study_runner import sha


class StudyMapTests(unittest.TestCase):
    def test_verification_badge_does_not_survive_state_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            state = dict(id='test', status='completed', run_count=0, regions={}, runs=[], trace=[])
            save_json(job/'state.json', state)
            save_json(job/'selection.json', {'regions': []})
            save_json(job/'protocol.json', {})
            save_json(job/'verification.json', dict(passed=True, **{
                f'{name}_sha256': sha(job/f'{name}.json') for name in ('state', 'selection', 'protocol')}))
            self.assertTrue(summary(job)['verified'])
            save_json(job/'state.json', dict(state, id='changed-after-verification'))
            self.assertFalse(summary(job)['verified'])

    def test_map_uses_same_demand_and_real_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            job = Path(temp)
            scope = dict(core=[0, 1], neighbor=[], network=[0, 1])
            before = [link(0, 1, 2, flow=540), link(1, 2, 3, flow=540)]
            after = copy.deepcopy(before)
            after[0].update(speed_kmh=20, parking=2)
            physical, views, rows = [], [], {}
            for name, data, factor, changes in (
                ('baseline', [link(0, 1, 2), link(1, 2, 3)], 1.0, []),
                ('baseline_0.9', before, 0.9, []),
                ('case_001', after, 0.9, [{'from_node': 1, 'to_node': 2, 'old_parking': 3, 'parking': 2}])):
                path = job/'scenarios'/name/'base'
                (path/'data').mkdir(parents=True)
                (path/'data/coor.txt').write_text('1 0 0\n2 100 0\n3 200 0\n', encoding='utf-8')
                physical.append(dict(name=name, scenario=str(path.relative_to(job))))
                views.append(dict(name=name, physical_run=name, changes=changes, demand_factor=factor,
                                  metrics=scoped_metrics(data, scope)))
                rows[path.resolve()] = data
            save_json(job/'state.json', dict(runs=physical, regions={'low': {'strategies': {'feedback': {'runs': views}}}}))
            save_json(job/'selection.json', dict(regions=[dict(id='low', status='selected', scope=scope)]))
            study_map.cache_clear()
            with patch('traffic_agent.study_dashboard.results', side_effect=lambda p: rows[p]), \
                 patch('traffic_agent.study_dashboard.SHAPE', job/'absent.shp'):
                result = study_map(str(job), 'low', 'feedback', 'case_001')
                self.assertEqual(result['baseline'], 'baseline_0.9')
                self.assertEqual(result['factor'], 0.9)
                self.assertEqual(result['links'][0]['before'][1], 540)
                self.assertEqual(result['links'][0]['after'][0], 20)
                self.assertEqual(result['links'][0]['geometry'], 'nodes')
                self.assertEqual(result['links'][1]['group'], 'core')
                self.assertTrue(result['links'][0]['treated'])
                self.assertFalse(result['links'][1]['treated'])
                with self.assertRaises(ValueError):
                    study_map(str(job), 'low', 'feedback', 'unmeasured')
                with self.assertRaises(ValueError):
                    study_map(str(job), 'unknown', 'feedback', 'case_001')
            study_map.cache_clear()


if __name__ == '__main__':
    unittest.main()
