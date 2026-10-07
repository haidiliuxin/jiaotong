import copy
import unittest
from datetime import datetime, timedelta, timezone
import tempfile
from pathlib import Path
from types import SimpleNamespace

from traffic_agent.robust_search import baseline_singletons, initial_changes, protocol, revise, search_actions
from traffic_agent.task import Task, parse_request
from traffic_agent.search_audit import replay


def row(node, speed=10):
    return dict(from_node=node, to_node=node + 1, parking=3, length_km=1,
                flow_pcu_h=100, speed_kmh=speed, adjusted_load_ratio=2)


class RobustSearchTests(unittest.TestCase):
    def setUp(self):
        self.rows = [row(n) for n in (1, 3, 5)]
        self.changes = initial_changes({"selected": self.rows})
        self.base = dict(name="baseline", core_rows=self.rows)

    def run_search(self, budget=11, fail=True, repeat=True, feedback=2,
                   backtrack=False, fail_singletons=False):
        task = Task.from_dict(dict(search_mode="robust", max_runs=budget, repeat_baseline=repeat,
                                  max_feedback=feedback, backtrack_on_singleton_failure=backtrack))
        search = search_actions(task, self.changes, self.base)
        sent, actions = None, []
        while True:
            try:
                a = search.send(sent)
            except StopIteration as end:
                return actions, end.value
            actions.append(copy.deepcopy(a))
            rows = [row(1, 11), row(3, 20), row(5, 15)] if a["changes"] else self.rows
            sent = dict(name=a["name"], changes=a["changes"], demand_factor=a["factor"], core_rows=rows,
                        comparison={"eligible": not (fail and (len(a["changes"]) == 3 or fail_singletons))},
                        metrics={"core": {"weighted_speed_kmh": 20}})

    def test_failure_changes_next_action_using_observed_link_benefit(self):
        actions, end = self.run_search()
        policies = [a for a in actions if a["kind"] == "policy"]
        self.assertEqual([a["factor"] for a in policies[:4]], [1.1, 1.1, 1.0, 0.9])
        self.assertEqual(policies[1]["changes"], [self.changes[1]])
        self.assertEqual(policies[1]["observed_run"], policies[0]["name"])
        self.assertEqual(policies[1]["ranking"][0]["from_node"], 3)
        self.assertEqual(end["early_rejections"][0]["unmeasured_factors"], [1.0, 0.9])
        self.assertEqual(end["actual_runs"], 11)
        self.assertEqual(actions[-1]["kind"], "repeat")
        self.assertTrue(all(c["parking"] == 1 for c in policies[-1]["changes"]))

    def test_different_observation_changes_retained_link(self):
        failed = dict(changes=self.changes, core_rows=[row(1, 30), row(3, 20), row(5, 15)])
        self.assertEqual(revise(failed, self.base)[0], [self.changes[0]])

    def test_tied_benefits_have_stable_node_order(self):
        failed = dict(changes=list(reversed(self.changes)), core_rows=self.rows)
        self.assertEqual(revise(failed, self.base)[0], [self.changes[0]])

    def test_budget_reserves_whole_candidate_grid_and_repeat(self):
        actions, end = self.run_search(budget=7)
        self.assertEqual(end["actual_runs"], 5)
        self.assertIn("预算", end["stop_reason"])
        self.assertEqual(actions[-1]["name"], "baseline_repeat")
        self.assertEqual(len([a for a in actions if a["kind"] == "policy"]), 1)

    def test_success_must_finish_grid_before_strengthening(self):
        actions, end = self.run_search(fail=False)
        policies = [a for a in actions if a["kind"] == "policy"]
        self.assertEqual([len(a["changes"]) for a in policies], [3] * 6)
        self.assertEqual([a["changes"][0]["parking"] for a in policies], [2] * 3 + [1] * 3)
        self.assertEqual(end["early_rejections"], [])

    def test_later_failure_moves_that_demand_to_front_of_next_candidate(self):
        search = search_actions(Task.from_dict(dict(search_mode="robust", max_runs=11)), self.changes, self.base)
        sent, policies = None, []
        while True:
            try:
                a = search.send(sent)
            except StopIteration:
                break
            if a["kind"] == "policy":
                policies.append(a)
            sent = dict(name=a["name"], changes=a["changes"], demand_factor=a["factor"], core_rows=self.rows,
                        comparison={"eligible": not (len(a["changes"]) == 3 and a["factor"] == 1)},
                        metrics={"core": {"weighted_speed_kmh": 20}})
        self.assertEqual([a["factor"] for a in policies[:5]], [1.1, 1.0, 1.0, 1.1, 0.9])
        self.assertEqual(policies[2]["observed_run"], policies[1]["name"])

    def test_zero_feedback_does_not_run_a_feedback_candidate(self):
        actions, end = self.run_search(feedback=0, repeat=False)
        self.assertEqual(end["candidates_started"], 1)
        self.assertFalse(any(a["kind"] == "repeat" for a in actions))

    def test_single_link_failure_stops(self):
        self.changes = self.changes[:1]
        search = search_actions(Task.from_dict(dict(search_mode="robust")), self.changes, self.base)
        sent = None
        while True:
            try:
                a = search.send(sent)
            except StopIteration as end:
                self.assertIn("单路段", end.value["stop_reason"])
                break
            sent = dict(name=a["name"], changes=a["changes"], demand_factor=a["factor"], comparison={"eligible": False})

    def test_backtracking_checks_other_core_directions_under_same_budget(self):
        actions, end = self.run_search(feedback=3, backtrack=True, fail_singletons=True)
        policies = [a for a in actions if a['kind'] == 'policy']
        self.assertEqual([a['changes'][0]['from_node'] for a in policies[1:]], [3, 1, 5])
        self.assertTrue(all(a['factor'] == 1.1 for a in policies))
        self.assertEqual([r['next_link'] for r in end['backtracks']], [[1, 2], [5, 6]])
        self.assertEqual(end['version'], 'adaptive-paired-search-v2')
        self.assertEqual(end['actual_runs'], 8)
        self.assertEqual(end['unused_budget'], 3)
        self.assertEqual(actions[-1]['kind'], 'repeat')
        self.assertEqual(policies[2]['ranking'], [])  # Untreated priority is not claimed as treatment benefit.
        self.assertIn('基准qL/v', protocol(Task.from_dict(dict(search_mode='robust',
            backtrack_on_singleton_failure=True)))['backtracking'])

    def test_backtracking_priority_uses_untreated_base_and_ties_by_nodes(self):
        self.rows = [row(1, 11), row(3, 9), row(5, 20)]
        self.changes = initial_changes({'selected': self.rows})
        self.base = dict(name='baseline', core_rows=self.rows)
        ranked = baseline_singletons(self.changes, self.base)
        self.assertEqual([key for _, key, _ in ranked], [(3, 4), (1, 2), (5, 6)])
        self.assertAlmostEqual(ranked[0][0], 100 / 9)
        actions, end = self.run_search(feedback=3, backtrack=True, fail_singletons=True)
        policies = [a for a in actions if a['kind'] == 'policy']
        self.assertEqual([a['changes'][0]['from_node'] for a in policies[1:]], [3, 1, 5])
        self.assertEqual(end['backtracks'][0]['next_link'], [1, 2])

    def test_backtracking_keeps_full_grid_and_repeat_in_reserve(self):
        actions, end = self.run_search(budget=9, feedback=3, backtrack=True, fail_singletons=True)
        self.assertEqual(len([a for a in actions if a['kind'] == 'policy']), 3)
        self.assertEqual(end['actual_runs'], 7)
        self.assertEqual(actions[-1]['kind'], 'repeat')
        self.assertIn('预算', end['stop_reason'])
        self.assertEqual(len(end['backtracks']), 1)

    def test_backtracking_does_not_record_an_unexecuted_last_candidate(self):
        actions, end = self.run_search(feedback=2, backtrack=True, fail_singletons=True)
        self.assertEqual(len([a for a in actions if a['kind'] == 'policy']), 3)
        self.assertEqual(len(end['backtracks']), 1)
        self.assertIn('上限', end['stop_reason'])

    def test_backtracking_flag_is_explicit_and_legacy_task_stays_serialized(self):
        self.assertNotIn('backtrack_on_singleton_failure', Task.from_dict({'search_mode': 'robust'}).as_dict())
        for fields in ({'backtrack_on_singleton_failure': 1},
                       {'search_mode': 'standard', 'backtrack_on_singleton_failure': True},
                       {'scenario': 'oneway', 'backtrack_on_singleton_failure': True}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Task.from_dict(dict({'search_mode': 'robust'}, **fields))

    def test_validation_rejects_unsupported_or_single_demand(self):
        for fields in ({"scenario": "oneway"}, {"demand_factors": [1]}, {"search_mode": "unknown"}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                Task.from_dict(dict({"search_mode": "robust"}, **fields))

    def test_bounded_natural_language_enables_mode_and_preserves_budget(self):
        task = parse_request("主动稳健停车治理；预算：9")
        self.assertEqual((task.search_mode, task.max_runs), ("robust", 9))
        self.assertEqual(parse_request("停车稳健搜索").max_runs, 11)
        self.assertEqual(Task.from_dict({"search_mode": "robust"}).max_runs, 11)
        self.assertEqual(parse_request("停车治理").search_mode, "standard")

    def replay_fixture(self, backtrack=False, all_fail=False):
        task = Task.from_dict(dict(search_mode="robust", max_runs=11,
                                  max_feedback=3 if backtrack else 2,
                                  backtrack_on_singleton_failure=backtrack))
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        base = dict(self.base, changes=[], demand_factor=1,
                    execution=dict(started=t.isoformat(), finished=(t + timedelta(seconds=1)).isoformat()))
        runs, traces = [base], []
        search = search_actions(task, self.changes, base)
        sent = None
        while True:
            try:
                action = search.send(sent)
            except StopIteration as end:
                return task, {"selected": self.rows}, runs, traces, end.value
            t += timedelta(seconds=10)
            traces.append(dict(action, decided_at=t.isoformat()))
            sent = dict(name=action["name"], changes=action["changes"], demand_factor=action["factor"],
                        core_rows=[row(1, 11), row(3, 20), row(5, 15)],
                        metrics={"core": {"weighted_speed_kmh": 20}},
                        comparison={"eligible": not (bool(action["changes"]) and
                                                     (all_fail or len(action["changes"]) == 3))},
                        execution=dict(started=(t + timedelta(seconds=1)).isoformat(), finished=(t + timedelta(seconds=2)).isoformat()))
            runs.append(sent)

    def test_audit_replays_complete_sequence(self):
        replay(*self.replay_fixture())

    def test_audit_replays_backtrack_and_rejects_changed_fallback(self):
        task, diagnosis, runs, traces, summary = self.replay_fixture(backtrack=True, all_fail=True)
        replay(task, diagnosis, runs, traces, summary)
        self.assertEqual(len(summary['backtracks']), 2)
        tampered = copy.deepcopy(traces)
        fallback = next(t for t in tampered if t['name'] == 'robust_p3_1.1')
        fallback['changes'] = [self.changes[2]]
        with self.assertRaisesRegex(ValueError, '决策'):
            replay(task, diagnosis, runs, tampered, summary)

    def test_audit_rejects_changed_decision_time_or_summary(self):
        for defect in ("changes", "time", "summary", "truncated", "extra"):
            task, diagnosis, runs, traces, summary = copy.deepcopy(self.replay_fixture())
            if defect == "changes":
                traces[3]["changes"] = self.changes
            elif defect == "time":
                traces[0]["decided_at"] = runs[1]["execution"]["finished"]
            elif defect == "summary":
                summary["unused_budget"] += 1
            elif defect == "truncated":
                traces.pop()
                runs.pop()
            else:
                traces.append(traces[-1])
                runs.append(runs[-1])
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                replay(task, diagnosis, runs, traces, summary)

    def test_uniform_choice_never_uses_incomplete_better_policy(self):
        from traffic_agent.robust_search import conclusion
        from test_decision_analysis import record, experiment
        state = experiment()
        state["runs"][1]["name"] = "baseline"
        state["runs"] += [record(f"complete_{f}", f, 95, policy=2) for f in [.9, 1, 1.1]]
        state["runs"].append(record("partial_better", 1, 50, policy=1))
        result = conclusion(state)
        self.assertEqual(result["recommendation"], "complete_1")
        self.assertEqual(result["recommendation_by_car_demand"]["1.0"], "partial_better")
        state["runs"] = state["runs"][:3] + state["runs"][-1:]
        self.assertEqual(conclusion(state)["recommendation"], "baseline")

    def integration_fixture(self, job, defect=None):
        from test_agent import link
        from traffic_agent.data import scoped_metrics
        task = Task.from_dict(dict(search_mode="robust", demand_factors=[1, 1.1], max_runs=5, max_feedback=0))
        rows = [link(0, 1, 2), link(1, 2, 3)]
        base = dict(name="baseline", changes=[], demand_factor=1, execution={"assigned_car_od_sum": 100, "status": "completed"})
        agent = SimpleNamespace(task=task, job=job, state=dict(id="test-search", task=task.as_dict(), runs=[base], sensitivity=[], decisions=[], status="running"),
                                event=lambda *args: None, persist=lambda: None)

        def simulation(name, changes, factor):
            # Tool calls must already have a persisted decision and no final recommendation.
            self.assertEqual(agent.state["search_trace"][-1]["name"], name)
            self.assertNotIn("recommendation", agent.state)
            output = copy.deepcopy(rows)
            for r in output:
                r["flow_pcu_h"] *= factor
            if changes:
                output[0].update(speed_kmh=20, parking=2)
            if defect == "repeat" and name == "baseline_repeat":
                output[0]["flow_pcu_h"] += 1
            observed = dict(name=name, changes=changes, demand_factor=factor,
                            execution=dict(status="completed", assigned_car_od_sum=100 * (1 if defect == "od" else factor)),
                            core_rows=[output[i] for i in agent.scope["core"]], metrics=scoped_metrics(output, agent.scope))
            agent.state["runs"].append(observed)
            return output, observed
        agent.simulation = simulation
        return agent, rows, base, {"selected": rows[:1]}

    def test_full_controller_pairs_and_finalizes_after_repeat(self):
        from traffic_agent.robust_search import analyze
        with tempfile.TemporaryDirectory() as folder:
            agent, rows, base, diagnosis = self.integration_fixture(Path(folder))
            analyze(agent, rows, base, diagnosis)
            self.assertEqual(agent.state["status"], "completed")
            self.assertEqual(agent.state["recommendation"], "robust_p1_1")
            self.assertTrue(agent.state["repeatability"]["exact_match"])
            self.assertEqual(agent.state["sensitivity"][0]["fixed_primary_policy"], "robust_p1_1.1")
            from traffic_agent.report import write_report
            write_report(Path(folder), agent.state)
            self.assertIn("内部实际分配需求比值 1.10000000", (Path(folder) / "report.md").read_text(encoding="utf-8"))
            self.assertTrue((Path(folder) / "report.html").exists())

    def test_repeat_failure_prevents_any_final_recommendation(self):
        from traffic_agent.robust_search import analyze
        with tempfile.TemporaryDirectory() as folder:
            agent, rows, base, diagnosis = self.integration_fixture(Path(folder), "repeat")
            with self.assertRaisesRegex(ValueError, "复跑"):
                analyze(agent, rows, base, diagnosis)
            self.assertNotIn("recommendation", agent.state)

    def test_v2_controller_freezes_tolerances_and_exports_versioned_recommendation(self):
        from traffic_agent.robust_search import analyze, protocol
        from traffic_agent.report import write_report
        with tempfile.TemporaryDirectory() as folder:
            agent, rows, base, diagnosis = self.integration_fixture(Path(folder))
            agent.task.recommendation_version = 'tolerance-v2'
            agent.state['task'] = agent.task.as_dict()
            self.assertEqual(protocol(agent.task)['selection_tolerances'], [0.001, 0.01, 0.001])
            analyze(agent, rows, base, diagnosis)
            self.assertEqual(agent.state['recommendation'], 'robust_p1_1')
            self.assertEqual(agent.state['recommendation_by_car_demand']['1.1'], 'robust_p1_1.1')
            write_report(Path(folder), agent.state)
            self.assertIn('容差推荐与复杂度', (Path(folder)/'report.md').read_text(encoding='utf-8'))

    def test_wrong_demand_response_stops_before_policy(self):
        from traffic_agent.robust_search import analyze
        with tempfile.TemporaryDirectory() as folder:
            agent, rows, base, diagnosis = self.integration_fixture(Path(folder), "od")
            with self.assertRaisesRegex(ValueError, "需求"):
                analyze(agent, rows, base, diagnosis)
            self.assertFalse(any(r["changes"] for r in agent.state["runs"]))

    def test_audit_rejects_missing_report_even_after_model_completion(self):
        from traffic_agent.search_audit import verify_search
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "报告导出"):
                verify_search(Path(folder), {"task": Task.from_dict({"search_mode": "robust"}).as_dict()})


if __name__ == "__main__":
    unittest.main()
