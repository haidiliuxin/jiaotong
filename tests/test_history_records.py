"""Historical record failures stay isolated; all requests use a temporary loopback server."""
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener
from unittest.mock import patch

from traffic_agent.task import Task
from traffic_agent.web import make_handler

urlopen = build_opener(ProxyHandler({})).open


class HistoryRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.root / 'source', self.root))
        # Retain disconnect evidence without printing malformed private records.
        self.server_errors = []
        self.server.handle_error = lambda *args: self.server_errors.append(args[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'

    def stop_server(self):
        self.server.shutdown()
        self.server.RequestHandlerClass.dispatcher.close()
        self.server.server_close()
        self.thread.join()

    def write_record(self, job_id, value):
        path = self.root / 'jobs' / job_id / 'state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')
        return path

    def state(self, job_id):
        return dict(id=job_id, status='created', started='2026-10-02T12:00:00',
                    task=Task().as_dict(), run_count=0, runs=[], decisions=[], sensitivity=[])

    def get(self, route):
        try:
            with urlopen(self.url + route, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            with exc:
                return exc.code, json.load(exc)
        except OSError as exc:
            self.fail(f'History API disconnected instead of returning JSON: {type(exc).__name__}')

    def test_bad_json_keeps_valid_history_and_does_not_rewrite_records(self):
        good = '20261002_120000_aaaaaa'
        bad = '20261002_130000_bbbbbb'
        good_file = self.write_record(good, self.state(good))
        bad_file = self.write_record(bad, '{"private_token":"PRIVATE_RAW", broken')
        originals = {p: p.read_bytes() for p in (good_file, bad_file)}
        status, rows = self.get('/api/jobs')
        self.assertEqual(status, 200)
        self.assertIsInstance(rows, list)
        self.assertEqual([row['id'] for row in rows], [bad, good])
        self.assertEqual(rows[0]['status'], 'unreadable')
        self.assertTrue(rows[0]['error'])
        self.assertEqual(rows[1], dict(id=good, status='created', started='2026-10-02T12:00:00',
                                      run_count=0, recommendation=None))
        self.assertNotIn('PRIVATE', json.dumps(rows))
        self.assertEqual(self.get('/api/jobs/' + good)[0], 200)
        for path, original in originals.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.server_errors, [])

    def test_invalid_state_shapes_and_mismatched_id_are_identifiable_and_safe(self):
        job_id = '20261002_130000_bbbbbb'
        cases = [[], None, {}, dict(self.state(job_id), id='PRIVATE_ID'),
                 dict(self.state(job_id), task=[]), dict(self.state(job_id), task={}),
                 dict(self.state(job_id), task=dict(Task().as_dict(), demand_factors=None)),
                 dict(self.state(job_id), runs={}), dict(self.state(job_id), runs=[{}]),
                 dict(self.state(job_id), decisions=[None]), dict(self.state(job_id), status={}),
                 dict(self.state(job_id), run_count='PRIVATE_COUNT'),
                 dict(self.state(job_id), recommendation={'private': 'PRIVATE_RECOMMENDATION'})]
        for value in cases:
            with self.subTest(value=value):
                path = self.write_record(job_id, json.dumps(value))
                original = path.read_bytes()
                status, rows = self.get('/api/jobs')
                self.assertEqual(status, 200)
                self.assertEqual(rows[0]['id'], job_id)
                self.assertEqual(rows[0]['status'], 'unreadable')
                status, result = self.get('/api/jobs/' + job_id)
                self.assertEqual(status, 409)
                self.assertTrue(result['error'])
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertNotIn(str(self.root), json.dumps(result))
                self.assertEqual(path.read_bytes(), original)

    def test_unreadable_file_and_non_finite_json_do_not_disconnect_history(self):
        job_id = '20261002_130000_bbbbbb'
        path = self.write_record(job_id, self.state(job_id))
        original_read = Path.read_text
        def denied(candidate, *args, **kwargs):
            if candidate == path:
                raise PermissionError('PRIVATE_PATH_AND_SECRET')
            return original_read(candidate, *args, **kwargs)
        with patch.object(Path, 'read_text', denied):
            self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'unreadable')
            status, result = self.get('/api/jobs/' + job_id)
            self.assertEqual(status, 409)
            self.assertNotIn('PRIVATE', json.dumps(result))
        self.write_record(job_id, dict(self.state(job_id), run_count=float('nan')))
        self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'unreadable')
        self.assertEqual(self.get('/api/jobs/' + job_id)[0], 409)

    def test_corrupt_job_map_returns_controlled_generic_error(self):
        job_id = '20261002_130000_bbbbbb'
        self.write_record(job_id, '{"secret":"PRIVATE_MAP", invalid')
        status, result = self.get('/api/jobs/' + job_id + '/map')
        self.assertEqual(status, 409)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('line', result['error'])

    def test_corrupt_dispatch_rows_keep_valid_history_and_no_partial_public_record(self):
        good_id = '20261004_120000_aaaaaa'
        bad_id = '20261004_130000_bbbbbb'
        folder = self.root / 'dispatches'
        folder.mkdir()
        good = dict(job_id=good_id, status='completed', planning_trace=[])
        good_path = folder / (good_id + '.json')
        good_path.write_text(json.dumps(good), encoding='utf-8')
        bad_path = folder / (bad_id + '.json')
        base = dict(job_id=bad_id, status='completed', request='PRIVATE_PARTIAL', planning_trace=[])
        cases = [None, [], dict(base, job_id=None), dict(base, planning_trace=None),
                 dict(base, planning_trace=[None]), dict(base, progress=[None]),
                 dict(base, progress=[{}]), dict(base, planning_trace=[{}], programme_review=[]) ]
        for value in cases:
            with self.subTest(value=value):
                bad_path.write_text(json.dumps(value), encoding='utf-8')
                original = bad_path.read_bytes()
                status, rows = self.get('/api/dispatch')
                self.assertEqual(status, 200)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0], dict(status='unreadable', job_id=bad_id))
                self.assertEqual(rows[1]['job_id'], good_id)
                self.assertEqual(rows[1]['status'], 'completed')
                self.assertNotIn('PRIVATE', json.dumps(rows))
                self.assertEqual(bad_path.read_bytes(), original)
        self.assertEqual(self.server_errors, [])

    def test_nested_decision_and_metric_shapes_cannot_be_selected_as_valid_jobs(self):
        job_id = '20261002_130000_bbbbbb'
        decision = dict(observed_run='baseline', reason='保留基准', violations=[], will_execute=False)
        run = dict(name='baseline', demand_factor=1, changes=[], execution={'status': 'running'}, metrics=None)
        valid = dict(self.state(job_id), decisions=[decision], runs=[run])
        self.write_record(job_id, valid)
        self.assertEqual(self.get('/api/jobs/' + job_id)[0], 200)
        cases = [dict(valid, decisions=[{}]),
                 dict(valid, decisions=[dict(decision, violations=None)]),
                 dict(valid, decisions=[dict(decision, violations='PRIVATE_VIOLATIONS')]),
                 dict(valid, decisions=[dict(decision, violations=[{}])]),
                 dict(valid, runs=[dict(run, metrics={})]),
                 dict(valid, runs=[dict(run, metrics={'core': [], 'neighbor': {}, 'network': {}})]),
                 dict(valid, runs=[dict(run, metrics={'core': {}, 'neighbor': {}, 'network': None})])]
        for value in cases:
            with self.subTest(value=value):
                path = self.write_record(job_id, value)
                original = path.read_bytes()
                self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'unreadable')
                status, result = self.get('/api/jobs/' + job_id)
                self.assertEqual(status, 409)
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(path.read_bytes(), original)

    def test_malformed_event_lines_are_skipped_without_changing_native_event_payloads(self):
        job_id = '20261002_130000_bbbbbb'
        state_path = self.write_record(job_id, self.state(job_id))
        events_path = state_path.with_name('events.jsonl')
        native_events = [dict(time='2026-10-02T12:00:00', event='knowledge_retrieval', data=[{'title': '离线依据'}]),
                         dict(time='2026-10-02T12:00:01', event='tool_start', data={'module': '离线测试'}),
                         dict(time='2026-10-02T12:00:02', event='completion_check', data={'complete': False, 'unanswered': ['尚未完成']})]
        malformed = [None, {}, dict(time='2026-10-02', event='failure', data=None),
                     dict(time='2026-10-02', event='completion_check', data={'complete': False}),
                     dict(time='2026-10-02', event='model_policy_assessment', data={'name': 'baseline', 'eligible': False, 'violations': None}),
                     dict(time='2026-10-02', event='search_assessment', data={'name': 'baseline', 'eligible': False, 'violations': 'PRIVATE_RAW'})]
        events_path.write_text('\n'.join(json.dumps(row) for row in [*malformed, *native_events]) + '\n{"unfinished":', encoding='utf-8')
        original = events_path.read_bytes()
        status, result = self.get('/api/jobs/' + job_id)
        self.assertEqual(status, 200)
        self.assertEqual(result['events'], native_events)
        self.assertEqual(result['events_skipped'], 7)
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertEqual(events_path.read_bytes(), original)
        self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'created')

    def test_sensitivity_comparisons_and_optional_adaptation_keep_valid_history_compatible(self):
        job_id = '20261002_130000_bbbbbb'
        item = dict(car_demand_factor=1.1, comparison={'eligible': True, 'violations': []})
        for adaptation in (None, {'status': 'budget_stop', 'reason': '预算已用完'},
                           {'reason': '保留基准', 'comparison': {'violations': ['未通过约束']}}):
            state = dict(self.state(job_id), sensitivity=[dict(item, adaptation=adaptation)])
            self.write_record(job_id, state)
            self.assertEqual(self.get('/api/jobs/' + job_id)[0], 200)
        malformed = [{}, dict(item, comparison=None), dict(item, comparison={}),
                     dict(item, comparison={'violations': 'PRIVATE_BAD_COMPARISON'}),
                     dict(item, adaptation=[]), dict(item, adaptation={'reason': '测试', 'comparison': {}}),
                     dict(item, adaptation={'reason': '测试', 'comparison': {'violations': None}})]
        for entry in malformed:
            with self.subTest(entry=entry):
                path = self.write_record(job_id, dict(self.state(job_id), sensitivity=[entry]))
                original = path.read_bytes()
                self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'unreadable')
                status, result = self.get('/api/jobs/' + job_id)
                self.assertEqual(status, 409)
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(path.read_bytes(), original)

    def test_other_saved_render_collections_cannot_crash_the_selected_job(self):
        job_id = '20261002_130000_bbbbbb'
        base = self.state(job_id)
        run = dict(name='baseline', demand_factor=1, changes=[], execution={'status': 'running'})
        cases = [dict(base, task=dict(base['task'], links=['bad'])),
                 dict(base, task=dict(base['task'], turn='bad')),
                 dict(base, status='needs_validation', missing=None),
                 dict(base, search_trace=[{}]), dict(base, search_trace=[dict(ranking=[None])]),
                 dict(base, diagnostic_observations=[{}]),
                 dict(base, diagnostic_observations=[dict(top_roads=[None])]),
                 dict(base, selection_analysis={}),
                 dict(base, selection_analysis=dict(equivalent_candidates=[], candidates=[{}])),
                 dict(base, turn_access_profile={}, runs=[dict(run, turn_response={'movements': [None]})]),
                 dict(base, turn_access_profile={}, runs=[dict(run, turn_response={'movements': [{'turn': [1, 2, 3], 'flows': {}}]})]),
                 dict(base, runs=[dict(run, comparison={'violations': None})])]
        for value in cases:
            with self.subTest(value=value):
                self.write_record(job_id, value)
                self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'unreadable')
                self.assertEqual(self.get('/api/jobs/' + job_id)[0], 409)

    def test_legacy_native_turn_measurement_stays_readable_without_four_mode_profile(self):
        job_id = '20261004_130000_bbbbbb'
        state = self.state(job_id)
        state['task']['scenario'] = 'circulation'
        state['run_count'] = 1
        movement = dict(turn=[1, 2, 3], before_car_turn_flow=12, after_car_turn_flow=0, passed=True)
        state['runs'] = [dict(name='turn', demand_factor=1, changes=[dict(kind='turn', turn=[1, 2, 3])],
                              execution=dict(status='completed'), metrics=None,
                              turn_response=dict(passed=True, cache_rules_exact=True,
                                                 cached_rule_count=1, movements=[movement], graph=None))]
        path = self.write_record(job_id, state)
        original = path.read_bytes()
        self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'created')
        status, detail = self.get('/api/jobs/' + job_id)
        self.assertEqual(status, 200)
        self.assertEqual(detail['state']['runs'][0]['turn_response']['movements'], [movement])
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_planner_trace_and_comparison_delta_are_unreadable_in_listing(self):
        job_id = '20261004_130000_bbbbbb'
        base = self.state(job_id)
        run = dict(name='baseline', demand_factor=1, changes=[], execution=dict(status='running'))
        cases = [dict(base, planner_trace=None), dict(base, planner_trace=[None]),
                 dict(base, runs=[dict(run, comparison=dict(violations=[], delta=None))]),
                 dict(base, runs=[dict(run, comparison=dict(violations=[], delta=dict(core=[])))])]
        for value in cases:
            with self.subTest(value=value):
                self.write_record(job_id, value)
                self.assertEqual(self.get('/api/jobs')[1][0]['status'], 'unreadable')
                self.assertEqual(self.get('/api/jobs/' + job_id)[0], 409)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the browser behavior test')
    def test_history_ui_skips_unreadable_records_and_labels_them(self):
        script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:'model',textContent:'',innerHTML:'',hidden:false,style:{},dataset:{}});return elements.get(id);};
let rows=[],overview=0;
const context=vm.createContext({document:{getElementById:element},URLSearchParams,
 location:{search:'?job=20261002_130000_bbbbbb'},
 window:{trafficMap:{overview:async()=>overview++}},
 fetch:async()=>({ok:true,json:async()=>rows})});
const code=fs.readFileSync('traffic_agent/static/app.js','utf8');
vm.runInContext(code.slice(0,code.indexOf("$('oneway-template').addEventListener")),context);
(async()=>{
 rows=[{id:'20261002_130000_bbbbbb',status:'unreadable',error:'记录不可读'},
       {id:'20261002_120000_aaaaaa',status:'created',run_count:0}];
 await vm.runInContext('refresh()',context);
 assert.equal(vm.runInContext('selected',context),'20261002_120000_aaaaaa');
 const history=element('history').innerHTML;
 assert(history.includes('记录不可读'));assert(history.includes('disabled'));
 assert(!history.includes('unreadable'));
 // A selected record can become unreadable between refreshes.
 rows=[{id:'20261002_120000_aaaaaa',status:'unreadable',error:'记录不可读'}];
 vm.runInContext("current={state:{id:selected}};lastRender='previous';",context);
 for(const id of ['result-panel','conditional-panel','record-task-panel','turn-response-panel'])element(id).hidden=false;
 element('report-link').hidden=false;
 await vm.runInContext('refresh()',context);
 assert.equal(vm.runInContext('selected',context),null);assert.equal(overview,1);
 assert.equal(vm.runInContext('current',context),null);
 for(const id of ['result-panel','conditional-panel','record-task-panel','turn-response-panel','report-link'])assert(element(id).hidden,id);
 assert(element('job-progress').textContent.includes('不可读'));
 vm.runInContext("current={state:{id:'offline',status:'created',runs:[],decisions:[],sensitivity:[],run_count:0},events:[],events_skipped:2};draw()",context);
 assert(element('events').innerHTML.includes('2 条不可读日志记录'));
})().catch(e=>{console.error(e);process.exit(1);});
"""
        result = subprocess.run(['node', '-e', script], cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
