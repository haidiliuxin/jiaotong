import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from traffic_agent.dispatch import Dispatcher
from traffic_agent.agent import Agent
from traffic_agent.data import save_json
from traffic_agent.scenario_match import match_scenario
from traffic_agent.task import Task

ROWS = [dict(name="长江村路", from_node=3237, to_node=3236),
        dict(name="长江村路", from_node=3236, to_node=3237),
        dict(name="红景路", from_node=5990, to_node=5991),
        dict(name="进口道路", from_node=4797, to_node=5990)]
QUICK = dict(max_runs=2, max_links=1, demand_factors=[1], repeat_baseline=False, max_feedback=0)


class MatchingTests(unittest.TestCase):
    def match(self, text, overrides=None, mode="rules", client=None):
        return match_scenario(dict(request=text, overrides=overrides or QUICK, matcher=mode), Path("source"), client, ROWS)

    def model(self, scenario="parking", road="", **extra):
        args = dict(scenario=scenario, road_name=road, reason="路侧停车干扰", **extra)
        return Mock(request=Mock(return_value=dict(calls=[dict(name="match_scenario", arguments=json.dumps(args))],
                                                   metadata=dict(tokens=10))))

    def test_parking_occupation_and_road_detection(self):
        result = self.match("长江村路违停占道，评估治理")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["task"]["scenario"], "parking")
        self.assertEqual(result["task"]["road_name"], "长江村路")
        self.assertEqual(result["task"]["max_runs"], 2)

    def test_unknown_and_unsupported_never_launch_parking(self):
        for text, status in (("很堵怎么办", "needs_input"), ("施工占道", "needs_validation"),
                             ("信号配时优化", "needs_validation"), ("单向和禁转", "needs_input"),
                             ("5分钟后停车增加", "needs_input")):
            result = self.match(text)
            self.assertEqual(result["status"], status)
            self.assertNotIn("task", result)

    def test_missing_or_nonexistent_directions(self):
        self.assertEqual(self.match("长江村路单向通行")["status"], "needs_input")
        self.assertEqual(self.match("红景路禁转")["status"], "needs_input")
        with self.assertRaises(ValueError):
            self.match("单向；方向：1→2", dict(QUICK, max_runs=4, max_links=2))
        self.assertEqual(self.match("停车；路段：不存在的道路")["status"], "needs_input")

    def test_explicit_oneway_and_turn_routes(self):
        one = self.match("长江村路单向；方向：3237→3236", dict(QUICK, max_runs=4, max_links=2, search_mode="robust"))
        self.assertEqual(one["task"]["links"], [[3237, 3236]])
        self.assertEqual(one["task"]["search_mode"], "standard")
        turn = self.match("红景路禁转；转向：4797→5990→5991", dict(QUICK, max_runs=4))
        self.assertEqual(turn["task"]["turn"], [4797, 5990, 5991])
        self.assertEqual(turn["plan"]["capability"]["state"], "executable")

    def test_budget_and_direction_conflicts_rejected(self):
        for text in ("停车；预算：9", "停车；方向：3237→3236", "单向；方向：3237→3236；方向：3236→3237"):
            with self.assertRaises(ValueError): self.match(text)
        with self.assertRaises(ValueError):
            self.match("红景路单向；方向：3237→3236", dict(QUICK, max_runs=4, max_links=2))

    def test_model_classifies_only_and_validates_locally(self):
        client = self.model(road="长江村路")
        result = self.match("长江村路停车干扰", mode="model", client=client)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["task"]["max_runs"], 2)
        client.request.assert_called_once()
        self.assertEqual(result["model_usage"]["tokens"], 10)
        with self.assertRaises(ValueError):
            self.match("停车干扰", mode="model", client=self.model(road="长江村路"))
        with self.assertRaises(ValueError):
            self.match("停车干扰", mode="model", client=self.model(max_runs=30))
        self.assertEqual(self.match("施工占道", mode="model", client=self.model())["status"], "needs_input")


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.settings = Mock(revision=Mock(return_value=1))
        self.dispatch = Dispatcher(self.root / "source", self.root, self.settings)
        self.addCleanup(self.dispatch.close)
        self.fingerprints = patch("traffic_agent.dispatch.input_fingerprints", return_value={"input": "old"}).start()
        self.addCleanup(patch.stopall)
        self.match = patch("traffic_agent.dispatch.match_scenario", side_effect=lambda b,s,c=None: match_scenario(b,s,c,ROWS)).start()
        self.body = dict(request="长江村路停车占道", matcher="rules", overrides=QUICK)

    def test_cache_copies_and_restart_no_resume(self):
        first = self.dispatch.preview(self.body)
        first["task"]["max_runs"] = 30
        second = self.dispatch.preview(self.body)
        self.assertEqual(second["task"]["max_runs"], 2)
        self.assertEqual(first["ticket"], second["ticket"])
        self.match.assert_called_once()
        save_json(self.root / "dispatches/old.json", dict(job_id="old", status="starting"))
        fresh = Dispatcher(self.root / "source", self.root, self.settings)
        self.assertEqual(fresh.listing()[0]["status"], "interrupted")
        self.assertIsNone(fresh.active)
        with self.assertRaises(ValueError): fresh.launch(first["ticket"])

    def test_source_change_and_invalid_preview_prevent_run(self):
        preview = self.dispatch.preview(self.body)
        self.fingerprints.return_value = {"input": "new"}
        with self.assertRaisesRegex(ValueError, "源场景"):
            self.dispatch.launch(preview["ticket"])
        p = self.dispatch.preview(dict(self.body, request="施工占道"))
        with self.assertRaisesRegex(ValueError, "执行检查"):
            self.dispatch.launch(p["ticket"])

    def test_missing_compiler_rejects_control_study_before_creating_jobs(self):
        body=dict(request="长江村路单向；方向：3237→3236", matcher="rules",
                  overrides=dict(QUICK,max_runs=4,max_links=2))
        preview=self.dispatch.preview(body)
        with patch("traffic_agent.preflight.check",return_value=dict(errors=["本任务需要PATH中的MinGW gcc以编译原生调用包装器"])) as checked, \
                patch("traffic_agent.dispatch.Agent") as agent:
            with self.assertRaisesRegex(ValueError,"MinGW gcc"):
                self.dispatch.launch(preview["ticket"])
        checked.assert_called_once()
        agent.assert_not_called()
        self.assertFalse((self.root/"jobs").exists())
        self.assertFalse((self.root/"dispatches").exists())

    def test_listing_reads_live_completed_scenes_without_rewriting_records(self):
        record_path=self.root/'dispatches/live.json'
        state_path=self.root/'jobs/live/state.json'
        save_json(record_path,dict(job_id='live',status='running',progress=[
            dict(job_id='live',status='running',run_count=9),
            dict(job_id='pending',status='queued')]))
        original=record_path.read_bytes()
        self.dispatch.active=Mock(name='dispatch-live')
        self.dispatch.active.name='dispatch-live'
        state=dict(id='live',runs=[dict(name='baseline',execution=dict(status='completed')),
            dict(name='policy',execution=dict(status='running')),
            dict(name='failed',execution=dict(status='failed'))])
        save_json(state_path,state)
        rows=self.dispatch.listing()
        self.assertEqual(rows[0]['status'],'running')
        self.assertEqual(rows[0]['progress'][0]['completed_run_count'],1)
        self.assertIsNone(rows[0]['progress'][1]['completed_run_count'])
        state['runs'][1]['execution']['status']='completed'
        save_json(state_path,state)
        state_bytes=state_path.read_bytes()
        self.assertEqual(self.dispatch.listing()[0]['progress'][0]['completed_run_count'],2)
        self.assertEqual(record_path.read_bytes(),original)
        self.assertEqual(state_path.read_bytes(),state_bytes)
        self.match.assert_not_called()

    def test_listing_missing_scene_records_are_unknown_and_do_not_resume(self):
        save_json(self.root/'dispatches/old.json',dict(job_id='old',status='running',progress=[
            dict(job_id='old',status='running',run_count=7),dict(job_id='empty',status='queued')]))
        save_json(self.root/'jobs/empty/state.json',dict(runs=[]))
        row=self.dispatch.listing()[0]
        self.assertEqual(row['status'],'interrupted')
        self.assertIsNone(row['progress'][0]['completed_run_count'])
        self.assertEqual(row['progress'][1]['completed_run_count'],0)
        self.assertIsNone(self.dispatch.active)

    def test_agent_rechecks_preview_inputs_under_native_lock(self):
        task = Task.from_dict(dict(QUICK, request="停车干扰"))
        agent = Agent(task, self.root / "source", self.root, expected_inputs={"data": "old"})
        with patch("traffic_agent.agent.validate_source", return_value={}), \
                patch("traffic_agent.agent.input_fingerprints", return_value={"data": "changed"}), \
                patch("traffic_agent.agent.setup_runtime") as setup, patch("traffic_agent.report.write_report"):
            with self.assertRaisesRegex(ValueError, "任务卡不一致"):
                agent.run()
        setup.assert_not_called()
        self.assertEqual(agent.state["status"], "failed")
        self.assertEqual(agent.state["run_count"], 0)

    def test_launch_idempotent_busy_and_evidence(self):
        started, release = threading.Event(), threading.Event()
        with patch("traffic_agent.dispatch.Agent") as cls:
            agent = cls.return_value
            agent.job_id = "20260927_000000_abcdef"
            agent.job = self.root / "jobs" / agent.job_id
            agent.state = {"status": "completed", "run_count": 2}
            def run():
                agent.job.mkdir(parents=True)
                started.set(); release.wait(5)
            agent.run.side_effect = run
            first = self.dispatch.preview(self.body)
            try:
                job = self.dispatch.launch(first["ticket"])
                self.assertTrue(started.wait(2))
                self.assertTrue(self.dispatch.launch(first["ticket"])["reused"])
                other = self.dispatch.preview(dict(self.body, request="违停占道"))
                with self.assertRaisesRegex(ValueError, "正在执行"):
                    self.dispatch.launch(other["ticket"])
            finally:
                release.set(); self.dispatch.active.join(5)
            agent.run.assert_called_once()
            evidence = json.loads((agent.job / "dispatch.json").read_text("utf-8"))
            self.assertEqual(evidence["status"], "completed")
            self.assertEqual(evidence["task"]["max_runs"], 2)


if __name__ == "__main__":
    unittest.main()
