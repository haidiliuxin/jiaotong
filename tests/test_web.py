import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
from unittest.mock import patch

from traffic_agent.web import make_handler
from traffic_agent.runner import runtime_lock
from traffic_agent.llm_client import ModelError

# This suite only contacts its own loopback HTTP server. Inherit no system proxy.
urlopen = build_opener(ProxyHandler({})).open


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.root / "source", cls.root))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def post(self, data, origin=None):
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        return urlopen(Request(self.url + "/api/plan", json.dumps(data).encode(), headers), timeout=5)

    def test_preview_validates_and_never_launches_models(self):
        with self.post({"request": "停车拥堵", "overrides": {"max_links": 2}}) as response:
            self.assertEqual(json.load(response)["task"]["max_links"], 2)
        with self.post({"request": "停车拥堵", "overrides": {"search_mode": "robust", "max_runs": 11}}) as response:
            preview = json.load(response)
            self.assertEqual(preview["task"]["search_mode"], "robust")
            self.assertEqual(preview["search_protocol"]["version"], "adaptive-paired-search-v1")
            self.assertNotIn("南京", preview["assumptions"][0])
        self.assertFalse((self.root / "runtime").exists())
        self.assertFalse((self.root / "jobs").exists())
        with self.post({"request": "信号优化"}) as response:
            self.assertEqual(json.load(response)["capability"]["state"], "needs_validation")

    def test_invalid_request_and_cross_origin_rejected(self):
        with self.assertRaises(HTTPError) as result:
            self.post({"task": {"max_runs": 2}})
        self.assertEqual(result.exception.code, 400)
        with self.assertRaises(HTTPError) as result:
            self.post({"request": "停车拥堵"}, origin="https://external.invalid")
        self.assertEqual(result.exception.code, 403)

    def test_turn_preview_opens_only_explicit_task(self):
        with self.post({'request': '禁转停车组合；路段：红景路；转向：4797→5990→5991；预算：13',
                        'overrides': {'max_links': 1}}) as response:
            result = json.load(response)
        self.assertEqual(result['capability']['state'], 'executable')
        self.assertEqual(result['task']['turn'], [4797, 5990, 5991])
        self.assertEqual(result['required_runs'], 13)
        with self.post({'request': '禁转停车组合'}) as response:
            self.assertEqual(json.load(response)['capability']['state'], 'needs_validation')

    def test_oneway_preview_preserves_closure_and_budget(self):
        with self.post({"request": "分析单向组织与停车组合；路段：长江村路；方向：3237→3236；预算：13",
                        "overrides": {"max_links": 2, "max_runs": 13}}) as response:
            task = json.load(response)["task"]
        self.assertEqual(task["scenario"], "oneway")
        self.assertEqual(task["links"], [[3237, 3236]])
        self.assertEqual(task["max_runs"], 13)
        self.assertFalse((self.root / "jobs").exists())
        self.assertFalse((self.root / "runtime").exists())

    def test_oneway_run_routes_task_to_the_agent(self):
        completed = threading.Event()
        with patch("traffic_agent.web.Agent") as agent_class:
            agent_class.return_value.job_id = "20260908_000000_abcdef"
            agent_class.return_value.run.side_effect = completed.set
            body = {"request": "单向停车组合；方向：3237→3236；预算：13"}
            with urlopen(Request(self.url + "/api/run", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"}), timeout=5) as response:
                self.assertEqual(response.status, 202)
            self.assertTrue(completed.wait(5))
            task = agent_class.call_args.args[0]
            self.assertEqual(task.scenario, "oneway")
            self.assertEqual(task.links, [[3237, 3236]])
            agent_class.return_value.run.assert_called_once_with()

    def test_not_an_arbitrary_file_server(self):
        with self.assertRaises(HTTPError) as result:
            urlopen(self.url + "/api/jobs/../../.git/config", timeout=5)
        self.assertEqual(result.exception.code, 404)
        with urlopen(self.url + "/", timeout=5) as response:
            self.assertIn("路策", response.read().decode())

    def test_model_settings_are_private_same_origin_and_no_automatic_calls(self):
        with patch("traffic_agent.dispatch_web.client_for") as client:
            with urlopen(self.url + "/api/settings/llm", timeout=5) as response:
                data = json.load(response)
            self.assertIn("key_configured", data)
            self.assertNotIn("secret", data)
            self.assertNotIn("api_key", data)
            with urlopen(self.url + "/api/dispatch", timeout=5) as response:
                self.assertEqual(json.load(response), [])
            client.assert_not_called()
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url + "/api/settings/llm", headers={"Origin": "https://other.invalid"}), timeout=5)
            self.assertEqual(error.exception.code, 403)

    def test_dispatch_launch_requires_frozen_ticket(self):
        body = json.dumps({"task": {"scenario": "parking"}}).encode()
        with patch("traffic_agent.dispatch.Agent") as agent:
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(self.url + "/api/dispatch/run", body, {"Content-Type": "application/json"}), timeout=5)
            self.assertEqual(error.exception.code, 400)
            agent.assert_not_called()

    def test_failed_probe_returns_safe_partial_costs_and_releases_lock(self):
        failure = ModelError('工具结果回传未得到确认', request_sent=True)
        failure.requests = [dict(tokens=12, request_model='test-model', raw='PRIVATE_RAW'),
                            dict(tokens=None, tokens_source='unavailable', secret='PRIVATE_SECRET')]
        with patch('traffic_agent.llm_settings.Settings.credentials', return_value=({}, 'OFFLINE_TEST')), \
                patch('traffic_agent.dispatch_web.client_for') as client:
            client.return_value.test.side_effect = [failure, dict(ok=True, requests=[dict(tokens=5)])]
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.url + '/api/settings/llm/test', b'{}', {'Content-Type': 'application/json'}), timeout=5)
            data = json.load(caught.exception)
            self.assertIn('2 次请求', data['error'])
            self.assertIn('已知 12 tokens', data['error'])
            self.assertIn('部分用量未知', data['error'])
            self.assertEqual([r['tokens'] for r in data['requests']], [12, None])
            self.assertNotIn('PRIVATE', json.dumps(data))
            with urlopen(Request(self.url + '/api/settings/llm/test', b'{}', {'Content-Type': 'application/json'}), timeout=5) as response:
                self.assertTrue(json.load(response)['ok'])
            self.assertEqual(client.return_value.test.call_count, 2)

    def test_live_runtime_lock_cannot_be_double_acquired(self):
        with runtime_lock(self.root):
            with self.assertRaises((RuntimeError, OSError)):
                with runtime_lock(self.root):
                    self.fail("第二个任务不应取得运行环境锁")
        with runtime_lock(self.root):
            pass


if __name__ == "__main__":
    unittest.main()
