import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent.map_data import map_data, paired_run, scenario_path, road_status, map_summary


class MapDataTests(unittest.TestCase):
    def test_native_status_boundaries_and_no_difference_explanation(self):
        self.assertEqual([road_status(x) for x in (.79,.8,1,1.01,1.5,1.51)],
                         ['normal','near_capacity','near_capacity','overloaded','overloaded','severe'])
        link=dict(id=1,name='路',start=1,end=2,group='core',before=[10,600,2],after=[10,600,2],
                  length=.5,changed=False,status=dict(before='severe',after='severe'),
                  delta=dict(time_pcu_h=0),treatments=[])
        base=dict(name='baseline',changes=[])
        result=map_summary([link],base,base,{'scenario':'diagnosis'})
        self.assertFalse(result['comparable'])
        self.assertIn('没有治理',result['reason'])
        result=map_summary([link],base,dict(name='policy',changes=[dict(parking=2)]),{})
        self.assertTrue(result['comparable'])
        self.assertEqual(result['changed_links'],0)
        self.assertIn('不能声称有效',result['reason'])
    def test_baseline_is_paired_by_demand_and_unfinished_run_cannot_be_selected(self):
        def run(name, factor, changes, done=True):
            return dict(name=name, demand_factor=factor, changes=changes, metrics={'core': {}},
                        execution={'status': 'completed' if done else 'running'})
        baseline = run('baseline', 1, [])
        matched = run('low_base', .9, [])
        candidate = run('low_policy', .9, [{'parking': 2}])
        state = {'runs': [baseline, matched, candidate, run('unfinished', 1, [], False)]}
        self.assertEqual(paired_run(state, 'low_policy'), (matched, candidate))
        for name in ('unfinished', '../baseline', 'missing'):
            with self.assertRaises(ValueError):
                paired_run(state, name)
        state['runs'].remove(matched)
        with self.assertRaises(ValueError):
            paired_run(state, 'low_policy')

    def test_geometry_joins_by_directed_endpoints_and_preserves_measured_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('base', 'policy'):
                (root/name/'data').mkdir(parents=True)
                (root/name/'data/coor.txt').write_text('1 100 200\n2 300 400\n', encoding='utf-8')
            def record(name, changes):
                return dict(name=name, scenario=name, changes=changes, demand_factor=.9,
                            metrics={'core': {}}, execution={'status': 'completed'})
            state = dict(id='test', task={}, runs=[record('base', []), record('policy', [{'parking': 2}])])
            (root/'state.json').write_text(json.dumps(state), encoding='utf-8')
            (root/'scopes.json').write_text(json.dumps(dict(core=[0], neighbor=[], network=[0, 1])))
            shape = root/'roads.shp'
            shape.write_bytes(b'fixture')
            shape.with_suffix('.dbf').write_bytes(b'fixture')
            shape.with_suffix('.prj').write_text('WGS_1984_UTM_Zone_50N')
            before = [dict(index=i, from_node=a, to_node=b, name='测试路', length_km=.3,
                           speed_kmh=10+i, flow_pcu_h=123+i, vc=1.2, parking=3) for i, (a, b) in enumerate([(1, 2), (2, 1)])]
            after = [dict(r, speed_kmh=20+i, parking=2) for i, r in enumerate(before)]
            shapes = {(2, 1): [[[300, 400], [250, 350], [100, 200]]],
                      (1, 2): [[[100, 200], [150, 250], [300, 400]]]}
            with patch('traffic_agent.map_data.results', side_effect=[before, after]), patch('traffic_agent.map_data.road_shapes', return_value=shapes):
                result = map_data(root, 'policy', shape)
            self.assertEqual(result['matched_shapes'], 2)
            self.assertTrue(result['meters'])
            self.assertEqual(result['links'][0]['paths'][0][1], [150, 250])
            self.assertEqual(result['links'][1]['paths'][0][1], [250, 350])
            self.assertEqual(result['links'][0]['before'], [10, 123, 1.2, 3])
            self.assertEqual(result['links'][0]['after'], [20, 123, 1.2, 2])
            # A same-ID map from another coordinate system cannot masquerade as matched SHP.
            shapes[(1, 2)] = [[[1000, 2000], [3000, 4000]]]
            with patch('traffic_agent.map_data.results', side_effect=[before, after]), patch('traffic_agent.map_data.road_shapes', return_value=shapes):
                result = map_data(root, 'policy', shape)
            self.assertFalse(result['meters'])
            self.assertEqual(result['links'][0]['geometry'], 'nodes')

            state['source'] = str(root/'examples'/'教学案例4'/'基础路网')
            (root/'state.json').write_text(json.dumps(state), encoding='utf-8')
            with patch('traffic_agent.map_data.results', side_effect=[before, after]), \
                 patch('traffic_agent.map_data.road_shapes', side_effect=AssertionError('wrong SHP')):
                result = map_data(root, 'policy')
            self.assertEqual(result['matched_shapes'], 0)
            self.assertEqual(result['dataset'], '软件自带教学案例4')
            self.assertTrue(all(link['geometry'] == 'nodes' for link in result['links']))
            self.assertIn('未配置专用SHP', result['geometry_note'])
            self.assertEqual(result['coordinate_system'], '案例平面坐标')

    def test_saved_scenario_cannot_escape_job(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                scenario_path(Path(folder), {'scenario': '../elsewhere'})

    def test_executed_oneway_and_combination_mark_only_closed_direction_without_changing_metrics(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('base', 'policy'):
                (root/name/'data').mkdir(parents=True)
                (root/name/'data/coor.txt').write_text('1 100 200\n2 300 400\n', encoding='utf-8')
            def record(name, changes):
                return dict(name=name, scenario=name, changes=changes, demand_factor=1,
                            metrics={'core': {}}, execution={'status': 'completed'})
            closure = dict(kind='oneway', closed_direction=[1, 2])
            policy = record('policy', [closure])
            policy['oneway_response'] = dict(passed=True, closed_direction=[1, 2])
            state = dict(id='test', task={}, runs=[record('base', []), policy])
            (root/'scopes.json').write_text(json.dumps(dict(core=[0, 1], neighbor=[], network=[0, 1])))
            before = [dict(index=i, from_node=a, to_node=b, name='测试路', length_km=.3,
                           speed_kmh=20, flow_pcu_h=100, vc=1.2, parking=3)
                      for i, (a, b) in enumerate([(1, 2), (2, 1)])]
            # A closure can leave a positive model speed; rendering must preserve it.
            after = [dict(r, speed_kmh=50, flow_pcu_h=0, vc=0) for r in before]
            def mapped():
                (root/'state.json').write_text(json.dumps(state), encoding='utf-8')
                with patch('traffic_agent.map_data.results', side_effect=[before, after]):
                    return map_data(root, 'policy', root/'no_shape.shp')
            result = mapped()
            self.assertEqual(result['comparison']['treated_links'], 1)
            self.assertEqual(result['comparison']['motor_closed_links'], 1)
            self.assertEqual(result['links'][0]['treatments'], ['机动车方向禁行'])
            self.assertEqual(result['links'][0]['motor_closure']['response'], 'passed')
            self.assertIsNone(result['links'][1]['motor_closure'])
            self.assertEqual(result['links'][0]['after'], [50, 0, 0, 3])
            self.assertEqual(result['links'][0]['status']['after'], 'normal')
            self.assertEqual(result['comparison']['highlights'][0]['motor_closure']['response'], 'passed')
            policy['changes'] = [dict(from_node=a, to_node=b, parking=2, old_parking=3)
                                 for a, b in [(1, 2), (2, 1)]] + [closure]
            result = mapped()
            self.assertEqual(result['comparison']['treated_links'], 2)
            self.assertEqual(result['comparison']['motor_closed_links'], 1)
            self.assertEqual(len(result['links'][0]['treatments']), 2)
            policy['oneway_response']['passed'] = False
            self.assertEqual(mapped()['links'][0]['motor_closure']['response'], 'failed')
            policy['oneway_response']['closed_direction'] = [2, 1]
            self.assertEqual(mapped()['links'][0]['motor_closure']['response'], 'not_checked')
            policy.pop('oneway_response')
            self.assertEqual(mapped()['links'][0]['motor_closure']['response'], 'not_checked')
            policy['changes'] = []
            result = mapped()
            self.assertEqual(result['comparison']['motor_closed_links'], 0)
            self.assertTrue(all(l['motor_closure'] is None for l in result['links']))


if __name__ == '__main__':
    unittest.main()
