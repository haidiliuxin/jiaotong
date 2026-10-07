"""Startup geometry must not present saved outputs as current simulation."""
import tempfile
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener,ProxyHandler

from traffic_agent.reference_map import reference_map
from traffic_agent.web import make_handler


class ReferenceMapTests(unittest.TestCase):
    def fixture(self,root):
        (root/'data').mkdir()
        (root/'data/coor.txt').write_text('1 10 20\n2 30 40\n',encoding='utf-8')
        (root/'data/geom.txt').write_text('1 2 300 2 1 0 0 0 测试路\n2 1 300 2 1 0 0 0 测试路\n1 9 100 9 1 0 0 0 虚拟连接\n',encoding='utf-8')
        (root/'data/manageL.txt').write_text('1 2 0 3 0\n',encoding='utf-8')

    def test_no_result_directory_required_and_no_traffic_values_returned(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root)
            result=reference_map(root,root/'missing.shp')
            self.assertTrue(result['reference']); self.assertFalse(result['meters'])
            self.assertEqual(result['new_model_calls'],0)
            self.assertEqual(len(result['links']),2)
            self.assertEqual(result['links'][0]['paths'],[[[10,20],[30,40]]])
            for link in result['links']:
                self.assertTrue({'before','after','speed_kmh','flow_pcu_h','vc','parking'}.isdisjoint(link))
            self.assertTrue({'metrics','eligible','factor','job','baseline','selected','changes'}.isdisjoint(result))
            self.assertFalse((root/'result').exists())

    def test_shp_requires_matching_directed_endpoints(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root)
            shape=root/'roads.shp'; shape.write_bytes(b'fixture')
            shape.with_suffix('.dbf').write_bytes(b'fixture')
            shape.with_suffix('.prj').write_text('WGS_1984_UTM_Zone_50N',encoding='utf-8')
            shapes={(1,2):[[[10,20],[20,20],[30,40]]],(2,1):[[[10,20],[30,40]]]}
            with patch('traffic_agent.reference_map.road_shapes',return_value=shapes):
                result=reference_map(root,shape)
            self.assertEqual(result['matched_shapes'],1)
            self.assertEqual(result['links'][0]['geometry'],'shp')
            self.assertEqual(result['links'][1]['geometry'],'nodes')
            self.assertFalse(result['meters'])

    def test_other_dataset_never_loads_default_nanjing_shape(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.fixture(root)
            with patch('traffic_agent.reference_map.road_shapes', side_effect=AssertionError('wrong SHP')):
                result = reference_map(root)
            self.assertEqual(result['matched_shapes'], 0)
            self.assertTrue(all(link['geometry'] == 'nodes' for link in result['links']))
            self.assertIn('未配置专用SHP', result['geometry_note'])
            self.assertEqual(result['coordinate_system'], '案例平面坐标')

    def test_invalid_coordinates_fail_instead_of_plotting_fabricated_points(self):
        for coords in ('1 nan 20\n2 30 40\n','1 10 20\n1 30 40\n','1 10 20\n'):
            with self.subTest(coords=coords),tempfile.TemporaryDirectory() as temp:
                root=Path(temp); self.fixture(root)
                (root/'data/coor.txt').write_text(coords,encoding='utf-8')
                with self.assertRaises(ValueError):
                    reference_map(root,root/'missing.shp')

    def test_read_only_http_route_uses_configured_source_and_reports_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); self.fixture(root)
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(root,root))
            thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            url=f'http://127.0.0.1:{server.server_address[1]}'
            opener=build_opener(ProxyHandler({}))
            try:
                with patch('traffic_agent.reference_map.reference_map',side_effect=lambda source:reference_map(source,root/'missing.shp')), \
                     patch('traffic_agent.web.Agent') as agent:
                    with opener.open(url+'/api/reference-map?source=../../outside',timeout=5) as response:
                        result=json.load(response)
                    self.assertEqual(result['links'][0]['name'],'测试路')
                    self.assertTrue(result['reference'])
                    self.assertEqual(result['new_model_calls'],0)
                    with opener.open(url+'/api/jobs',timeout=5) as response:
                        self.assertEqual(json.load(response),[])
                    (root/'data/coor.txt').write_text('1 nan 20\n',encoding='utf-8')
                    with self.assertRaises(HTTPError) as error:
                        opener.open(url+'/api/reference-map',timeout=5)
                    self.assertEqual(error.exception.code,400)
                    self.assertIn('error',json.load(error.exception))
                    agent.assert_not_called()
                self.assertFalse((root/'jobs').exists())
                self.assertFalse((root/'runtime').exists())
            finally:
                server.shutdown(); server.server_close(); thread.join()

    def test_saved_job_reference_uses_its_own_dataset(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            configured = root/'configured'
            teaching = root/'examples'/'教学案例4'/'基础路网'
            configured.mkdir(parents=True)
            teaching.mkdir(parents=True)
            self.fixture(configured)
            self.fixture(teaching)
            (teaching/'data/coor.txt').write_text('1 100 200\n2 300 400\n', encoding='utf-8')
            job_id = '20260923_000000_abcdef'
            job = root/'jobs'/job_id
            job.mkdir(parents=True)
            (job/'state.json').write_text(json.dumps({'id': job_id, 'source': str(teaching)}), encoding='utf-8')
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(configured, root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_address[1]}'
            opener = build_opener(ProxyHandler({}))
            try:
                with opener.open(url+'/api/reference-map?job='+job_id, timeout=5) as response:
                    result = json.load(response)
                self.assertEqual(result['dataset'], '软件自带教学案例4')
                self.assertEqual(result['links'][0]['paths'], [[[100.0, 200.0], [300.0, 400.0]]])
                self.assertEqual(result['matched_shapes'], 0)
                with self.assertRaises(HTTPError) as error:
                    opener.open(url+'/api/reference-map?job=../../outside', timeout=5)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__=='__main__':
    unittest.main()
