import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from traffic_agent.agent import Agent, compare, feedback, plan, recommend, risk_singleton_candidates
from traffic_agent.data import diagnose, geometry, metrics, numeric, prepare, results, scopes, scoped_metrics
from traffic_agent.task import Task, parse_request, request_demand_factors
from traffic_agent.scenario_match import explicit_fields
from traffic_agent.natural_plan import limits


def link(index, frm, to, speed=10, flow=600, vc=2, parking=3, length=0.5):
    return dict(index=index, from_node=frm, to_node=to, speed_kmh=speed, flow_pcu_h=flow,
                vc=vc, parking=parking, length_km=length, road_class=3, lanes=1, name="测试路",
                included=True, time_s=180, adjusted_load_ratio=2, adjusted_capacity_pcu_h=300)


class TaskTests(unittest.TestCase):
    def test_event_console_encoding_cannot_abort_recorded_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            agent=object.__new__(Agent)
            agent.job=Path(directory)
            output=io.BytesIO()
            console=io.TextIOWrapper(output,encoding='cp936',errors='strict')
            with redirect_stdout(console):
                agent.event('model_decision',dict(summary='耗时下降−28.5%'))
            console.flush()
            self.assertIn(b'\\u2212',output.getvalue())
            saved=json.loads((agent.job/'events.jsonl').read_text('utf-8'))
            self.assertEqual(saved['data']['summary'],'耗时下降−28.5%')

            class BrokenConsole:
                def write(self,text):raise OSError('closed output')
                def flush(self):raise OSError('closed output')
            with redirect_stdout(BrokenConsole()):
                agent.event('model_decision',dict(summary='第二方案继续'))
            self.assertEqual(len((agent.job/'events.jsonl').read_text('utf-8').splitlines()),2)

    def test_bounds_and_unknown_fields(self):
        for fields in ({"max_links": True}, {"max_runs": 2}, {"demand_factors": [0.9]},
                       {"demand_factors": [1, float("nan")]}, {"max_new_oversaturated_km": -1},
                       {"max_new_network_oversaturated_km": -1},
                       {"max_new_network_oversaturated_km": float("nan")},
                       {"max_new_network_oversaturated_km": None},
                       {"max_new_network_oversaturated_km": True},
                       {"execute_shell": "anything"}, {"assignment_module": "M6AssignVehicle1.exe"}, {"scenario": "made_up"},
                       {"links": [[1, 2], [1, 2]]}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Task.from_dict(fields)

    def test_template(self):
        task = parse_request("分析停车拥堵；路段：测试路；最多治理2条；需求系数：0.9,1,1.1；预算：9")
        self.assertEqual((task.road_name, task.max_links, task.max_runs), ("测试路", 2, 9))
        self.assertEqual(task.demand_factors, [0.9, 1, 1.1])

    def test_natural_discrete_demand_factors_are_frozen_across_entry_paths(self):
        for phrase in ('需求系数0.9和1.0', '需求系数为0.9、1.0',
                       '需求系数：0.9,1.0', '需求系数分别为 0.9 与 1.0'):
            text=f'请检验停车治理在需求变化下是否仍有效，{phrase}。最多4次仿真，不复跑。'
            with self.subTest(phrase=phrase):
                self.assertEqual(request_demand_factors(text),[0.9,1.0])
                self.assertEqual(explicit_fields(text)['demand_factors'],[0.9,1.0])
                self.assertEqual(limits(text)['demand_factors'],[0.9,1.0])
                self.assertEqual(parse_request(text).demand_factors,[0.9,1.0])
        self.assertEqual(request_demand_factors('停车治理最多4次仿真'),None)
        for phrase in ('需求系数0.9至1.1', '需求系数0.9和1.0；不做敏感性'):
            with self.subTest(conflict=phrase),self.assertRaises(ValueError):
                limits('停车治理；'+phrase)

    def test_capability_routes_do_not_simulate_other_policies(self):
        for text in ("施工占道", "公交优先", "信号配时", "通勤错峰", "转向绕行"):
            self.assertEqual(plan(parse_request(text))["capability"]["state"], "needs_validation")

    def test_oneway_direction_is_explicit_and_unambiguous(self):
        for arrow in ("→", "->"):
            task = parse_request(f"分析单向组织与停车组合；路段：长江村路；方向：3237{arrow}3236；预算：13")
            self.assertEqual(task.scenario, "oneway")
            self.assertEqual(task.links, [[3237, 3236]])
            self.assertEqual(task.max_runs, 13)
            self.assertEqual(plan(task)["capability"]["state"], "executable")
        for text in ("单向；方向：", "单向；方向：3237", "单向；方向：3237→3237",
                     "单向；方向：3237→3236、3236→3235", "单向；方向：3237→3236,3236→3235",
                     "停车；方向：3237→3236",
                     "单向；方向：3237→3236；方向：3236→3235"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_request(text)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        for folder in ("data", "cache", "result", "params"):
            (self.source / folder).mkdir(parents=True)
        (self.source / "data/manageL.txt").write_bytes("路段管理数量 1\r\n起点 终点 公交 停车 绿波\r\n1\t2  0\t3 0\r\n".encode("gb18030"))
        (self.source / "data/geom.txt").write_text("1 2 500 3 1 3.5 0 0 测试路\n", encoding="utf-8")
        (self.source / "data/Chnn.txt").write_bytes(b"2\r\n1 1\r\n2 2\r\n")
        (self.source / "result/ODCar.tsd").write_bytes(b"{'format':'table'}\r\n0.0000 10.0000\r\n20.0000 0.0000\r\n")
        (self.source / "result/SpdLinkVeh.tsd").write_bytes(b"999\n")
        self.edit = dict(from_node=1, to_node=2, old_parking=3, parking=2)

    def test_prepare_preserves_source_and_bytes_except_requested_fields(self):
        original = (self.source / "data/manageL.txt").read_bytes()
        dest = self.root / "new"
        edits = prepare(self.source, dest, [self.edit], 0.9)
        self.assertEqual((self.source / "data/manageL.txt").read_bytes(), original)
        self.assertEqual((dest / "data/manageL.txt").read_bytes(), original.replace(b"\t3 0", b"\t2 0"))
        self.assertEqual(numeric(dest / "result/ODCar.tsd"), [0, 9, 18, 0])
        self.assertFalse((dest / "result/SpdLinkVeh.tsd").exists())
        self.assertEqual(len(edits), 2)
        with self.assertRaises(ValueError):
            prepare(self.source, dest, [])

    def test_wrong_original_and_invalid_policy_fail(self):
        with self.assertRaises(ValueError):
            prepare(self.source, self.root / "bad", [dict(self.edit, old_parking=4)])
        with self.assertRaises(ValueError):
            prepare(self.source, self.root / "bad2", [dict(self.edit, parking=99)])

    def test_duplicate_edges_rejected(self):
        path = self.source / "data/geom.txt"
        path.write_bytes(path.read_bytes() * 2)
        with self.assertRaises(ValueError):
            geometry(self.source)

    def test_nonfinite_and_truncated_results_fail(self):
        for value in (b"nan\n", b"inf\n", b""):
            path = self.source / "result/SpdLinkVeh.tsd"
            path.write_bytes(value)
            with self.assertRaises(ValueError):
                numeric(path)
        for name in ("SpdLinkVeh.tsd", "VolLinkVeh.tsd", "VCLinkVeh.tsd", "TimeLink.tsd"):
            (self.source / "result" / name).write_bytes(b"10\n10\n")
        with self.assertRaises(ValueError):
            results(self.source)


class DecisionTests(unittest.TestCase):
    def test_primary_budget_stop_explains_reserved_repeat(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = Task.from_dict({"max_runs": 3, "demand_factors": [1], "repeat_baseline": True})
            agent = Agent(task, root / "source", root / "output")
            agent.job.mkdir(parents=True)
            baseline = [link(0, 1, 2)]
            base = {"name": "baseline", "changes": [], "demand_factor": 1, "execution": {"assigned_car_od_sum": 100}}
            agent.state.update(run_count=1, runs=[base])
            calls = []
            def simulate(name, changes, factor=1):
                calls.append(name)
                rows = copy.deepcopy(baseline)
                if changes:
                    rows[0]["speed_kmh"] = 20
                record = {"name": name, "changes": changes, "demand_factor": factor,
                          "metrics": scoped_metrics(rows, agent.scope), "core_rows": rows,
                          "execution": {"assigned_car_od_sum": 100}}
                agent.state["runs"].append(record)
                agent.state["run_count"] += 1
                return rows, record
            with patch.object(agent, "simulation", side_effect=simulate), patch.object(agent, "event"):
                agent.analyze(baseline, base, {"selected": baseline})
            self.assertEqual(calls, ["parking_1", "baseline_repeat"])
            decision = agent.state["decisions"][0]
            self.assertTrue(decision["next_changes"])
            self.assertFalse(decision["will_execute"])
            self.assertEqual(decision["execution_gate"], "reserved_budget")
            self.assertIn("预留1次", decision["execution_reason"])
            from traffic_agent.report import write_report
            write_report(agent.job, agent.state)
            self.assertIn("预留1次", (agent.job / "report.md").read_text(encoding="utf-8"))

    def test_full_controller_reserves_budget_and_adapts_both_failed_demand_levels(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            agent = Agent(Task(), root / "source", root / "output")
            agent.job.mkdir(parents=True)
            baseline = [link(0, 1, 2), link(1, 2, 3), link(2, 3, 4), link(3, 4, 5, vc=.9, length=.2)]
            base = {"name": "baseline", "changes": [], "execution": {"assigned_car_od_sum": 100},
                    "scenario": "baseline", "demand_factor": 1.0}
            agent.state.update(run_count=1, runs=[base])
            saved_rows = {}
            calls = []
            def simulate(name, changes, factor=1.0):
                self.assertLess(agent.state["run_count"], agent.task.max_runs)
                calls.append(name)
                rows = copy.deepcopy(baseline)
                for row in rows:
                    row["flow_pcu_h"] *= factor
                for change in changes:
                    row = next(r for r in rows if (r["from_node"], r["to_node"]) == (change["from_node"], change["to_node"]))
                    row["speed_kmh"] = 20 if change["parking"] == 2 else 30
                if factor != 1 and len(changes) == 3:
                    rows[3]["vc"] = 1.1
                record = {"name": name, "changes": changes, "demand_factor": factor, "scenario": name,
                          "execution": {"assigned_car_od_sum": 100 * factor},
                          "metrics": scoped_metrics(rows, agent.scope), "core_rows": rows[:3]}
                saved_rows[str(agent.job / name)] = rows
                agent.state["runs"].append(record)
                agent.state["run_count"] += 1
                return rows, record
            with patch.object(agent, "simulation", side_effect=simulate), patch.object(agent, "event"), \
                 patch("traffic_agent.agent.results", side_effect=lambda path: saved_rows[str(path)]):
                agent.analyze(baseline, base, {"selected": baseline[:3]})
            self.assertEqual(agent.state["run_count"], 10)
            self.assertEqual(calls[-2:], ["adapt_0p900", "adapt_1p100"])
            self.assertEqual(agent.state["recommendation_by_car_demand"],
                             {"1.0": "parking_2", "0.9": "adapt_0p900", "1.1": "adapt_1p100"})
            self.assertFalse(agent.state["robust_across_tested_demand"])
            self.assertTrue(all(not x["comparison"]["eligible"] and x["adaptation"]["comparison"]["eligible"]
                                for x in agent.state["sensitivity"]))

    def test_sensitivity_budget_fallback_keeps_failed_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            agent = Agent(Task(), root / "source", root / "output")
            agent.job.mkdir(parents=True)
            item = {"car_demand_factor": 0.9, "comparison": {"eligible": False, "violations": ["新增过饱和"]},
                    "matched_baseline": "low_base"}
            agent.state.update(run_count=10, recommendation="strong", runs=[{"name": "strong", "changes": [{"parking": 1}]}],
                               sensitivity=[item])
            agent.adapt_sensitivity()
            self.assertEqual(agent.state["recommendation_by_car_demand"]["0.9"], "low_base")
            self.assertFalse(item["comparison"]["eligible"])
            self.assertEqual(item["adaptation"]["status"], "budget_stop")

    def test_running_job_cannot_be_resumed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "state.json").write_text('{"status":"running"}', encoding="utf-8")
            with self.assertRaises(ValueError):
                Agent.adapt_completed(root)

    def test_fixed_scope_and_no_substitution_of_user_road(self):
        rows = [link(0, 1, 2), link(1, 2, 3), link(2, 8, 9)]
        scope = scopes(rows, [0], 1)
        self.assertEqual(scope["neighbor"], [1])
        task = Task.from_dict({"road_name": "不存在"})
        self.assertEqual(diagnose(rows, task)["selected"], [])

    def test_gross_new_overload_cannot_be_hidden_by_other_improvement(self):
        rows = [link(0, 1, 2), link(1, 2, 3, vc=0.9), link(2, 3, 4, vc=1.1)]
        after = copy.deepcopy(rows)
        after[0]["speed_kmh"] = 20
        after[1]["vc"], after[2]["vc"] = 1.1, 0.9
        assessment = compare(rows, after, {"core": [0], "neighbor": [1, 2], "network": [0, 1, 2]}, Task())
        self.assertFalse(assessment["eligible"])
        self.assertAlmostEqual(assessment["delta"]["neighbor"]["oversaturated_km_change"], 0)
        self.assertAlmostEqual(assessment["new_neighbor_oversaturated_km"], 0.5)

    def test_optional_network_gross_gate_rejects_remote_swap_without_changing_old_tasks(self):
        before = [link(0, 1, 2), link(1, 2, 3), link(2, 8, 9, vc=.9), link(3, 10, 11, vc=1.1)]
        after = copy.deepcopy(before)
        after[0]['speed_kmh'] = 20
        after[2]['vc'], after[3]['vc'] = 1.1, .9
        scope = {'core': [0], 'neighbor': [1], 'network': [0, 1, 2, 3]}
        old_task = Task.from_dict({})
        old = compare(before, after, scope, old_task)
        self.assertTrue(old['eligible'])
        self.assertNotIn('max_new_network_oversaturated_km', old_task.as_dict())
        self.assertNotIn('new_network_oversaturated_km', old)
        self.assertAlmostEqual(old['delta']['network']['oversaturated_km_change'], 0)
        guarded_task = Task.from_dict({'max_new_network_oversaturated_km': .1})
        guarded = compare(before, after, scope, guarded_task)
        self.assertFalse(guarded['eligible'])
        self.assertAlmostEqual(guarded['new_network_oversaturated_km'], .5)
        self.assertAlmostEqual(guarded['new_neighbor_oversaturated_km'], 0)
        self.assertIn('全网毛新增过饱和有向里程超过0.1km', guarded['violations'])
        self.assertTrue(compare(before, after, scope,
                                Task.from_dict({'max_new_network_oversaturated_km': .5}))['eligible'])
        self.assertEqual(guarded_task.as_dict()['max_new_network_oversaturated_km'], .1)

    def test_feedback_branches_and_baseline_fallback(self):
        edit = dict(from_node=1, to_node=2, old_parking=3, parking=2)
        record = {"changes": [edit, dict(edit, to_node=3)], "comparison": {"eligible": False}}
        changes, reason = feedback(record, [], {}, Task())
        self.assertEqual(len(changes), 1)
        record["changes"] = changes
        self.assertIsNone(feedback(record, [], {}, Task())[0])
        self.assertIsNone(recommend([record]))
        record.update(comparison={"eligible": True}, metrics={"core": {"weighted_speed_kmh": 17}}, core_rows=[link(0, 1, 2)])
        self.assertEqual(feedback(record, [], {}, Task())[0][0]["parking"], 1)

    def test_network_risk_scans_all_selected_singletons_only_when_budget_is_reserved(self):
        changes = [dict(from_node=i, to_node=i+1, old_parking=3, parking=2) for i in (1, 3, 5)]
        record = {'changes': changes, 'comparison': {'eligible': False,
                  'new_network_oversaturated_km': .785}}
        task = Task.from_dict({'max_new_network_oversaturated_km': .1,
                               'max_runs': 6, 'max_feedback': 3, 'demand_factors': [1.]})
        queue = risk_singleton_candidates(record, task, available_runs=3)
        self.assertEqual(queue, [[change] for change in changes])
        self.assertIsNot(queue[0][0], changes[0])
        self.assertEqual(changes[0]['parking'], 2)
        self.assertEqual(risk_singleton_candidates(record, task, available_runs=2), [])
        self.assertEqual(risk_singleton_candidates(record, Task.from_dict({
            'max_new_network_oversaturated_km': .1, 'max_runs': 4,
            'max_feedback': 2, 'demand_factors': [1.]}), available_runs=1), [])
        self.assertEqual(risk_singleton_candidates(record, Task.from_dict({
            'max_runs': 6, 'max_feedback': 3, 'demand_factors': [1.]}), available_runs=3), [])
        record['comparison']['new_network_oversaturated_km'] = .1
        self.assertEqual(risk_singleton_candidates(record, task, available_runs=3), [])

    def test_metric_definitions_and_timelink_sentinel(self):
        rows = [link(0, 1, 2, speed=10), link(1, 2, 3, speed=20)]
        rows[1]["time_s"] = 9999999999
        result = metrics(rows)
        self.assertAlmostEqual(result["weighted_speed_kmh"], 600 / 45)
        self.assertIsNone(result["timelink_sum_pcu_h"])
        self.assertEqual(result["timelink_flagged_links"], 1)
        self.assertIsNone(metrics([])["weighted_speed_kmh"])


if __name__ == "__main__":
    unittest.main()
