import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_agent import link
from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics
from traffic_agent.diagnostic_study import analyze, diagnose_network
from traffic_agent.dispatch import evidence_review
from traffic_agent.task import Task


class DiagnosticStudyTests(unittest.TestCase):
    def setUp(self):
        self.rows = [link(0, 1, 2, parking=0), link(1, 2, 3, vc=0.5),
                     dict(link(2, 3, 4, flow=2000), name="另一道路")]
        self.task = Task.from_dict(dict(scenario="diagnosis", road_name="测试路", demand_factors=[1.0, 1.2],
                                        max_runs=3, repeat_baseline=True))

    def test_diagnosis_reads_scope_not_just_parking_candidates(self):
        d = diagnose_network(self.rows, self.task)
        self.assertEqual(d["inspected_links"], 2)
        self.assertEqual(d["overloaded_links"], 1)
        self.assertEqual(d["selected"][0]["parking"], 0)
        self.assertNotIn(2, [r["index"] for r in d["candidates"]])

    def exercise(self, wrong_demand=False, wrong_repeat=False):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        agent = Agent(self.task, Path(temp.name)/"source", Path(temp.name)/"output")
        agent.job.mkdir(parents=True)
        base = dict(name="baseline", changes=[], demand_factor=1.0, execution=dict(status="completed", assigned_car_od_sum=100))
        agent.state["runs"].append(base); agent.state["run_count"] = 1
        calls = []
        def simulation(name, changes, factor=1):
            calls.append((name, changes, factor))
            rows = copy.deepcopy(self.rows)
            if factor != 1:
                for row in rows: row["flow_pcu_h"] *= factor
            if wrong_repeat and name == "baseline_repeat": rows[0]["flow_pcu_h"] += 1
            record = dict(name=name, changes=changes, demand_factor=factor,
                          execution=dict(status="completed", assigned_car_od_sum=100*(1 if wrong_demand else factor)),
                          metrics=scoped_metrics(rows, agent.scope))
            agent.state["runs"].append(record); agent.state["run_count"] += 1
            return rows, record
        with patch.object(agent, "simulation", side_effect=simulation), patch.object(agent, "event"):
            analyze(agent, self.rows, base, diagnose_network(self.rows, self.task))
        return agent, calls

    def test_only_untreated_runs_and_verified_demand_are_used(self):
        agent, calls = self.exercise()
        self.assertTrue(all(not changes for _, changes, _ in calls))
        self.assertEqual(len(calls), 2)
        self.assertEqual(agent.state["status"], "completed")
        self.assertEqual(agent.state["recommendation"], "baseline")
        self.assertIsNone(agent.state["robust_across_tested_demand"])
        self.assertTrue(agent.state["repeatability"]["exact_match"])
        observed = agent.state["diagnostic_observations"]
        self.assertEqual([x["inspected_links"] for x in observed], [2, 2])
        self.assertAlmostEqual(observed[1]["top_roads"][0]["time_pcu_h"], observed[0]["top_roads"][0]["time_pcu_h"]*1.2)
        self.assertEqual(evidence_review(agent.state)["kind"], "diagnosis")

    def test_mismatched_demand_and_repeat_fail(self):
        with self.assertRaisesRegex(ValueError, "需求未按设定比例"):
            self.exercise(wrong_demand=True)
        with self.assertRaisesRegex(ValueError, "复跑不一致"):
            self.exercise(wrong_repeat=True)

    def test_no_congestion_is_still_a_valid_observation(self):
        rows = [dict(r, vc=0.5) for r in self.rows]
        d = diagnose_network(rows, self.task)
        self.assertEqual(d["overloaded_links"], 0)
        self.assertEqual(len(d["selected"]), 2)

    def test_empty_scope_is_reported_without_switching_roads(self):
        task = Task.from_dict(dict(scenario="diagnosis", road_name="不存在路", max_runs=1, demand_factors=[1], repeat_baseline=False))
        d = diagnose_network(self.rows, task)
        self.assertEqual(d["inspected_links"], 0)
        self.assertEqual(d["selected"], [])

    def test_negative_result_review_does_not_claim_improvement(self):
        state = dict(status="completed", task=dict(scenario="parking"), recommendation="baseline", runs=[
            dict(name="baseline", changes=[], execution=dict(status="completed")),
            dict(name="bad", changes=[{}], execution=dict(status="completed"), comparison=dict(eligible=False, violations=["邻区超限"]))])
        r = evidence_review(state)
        self.assertEqual(r["rejected_count"], 1)
        self.assertIn("保留基准", r["outcome"])
        self.assertNotIn("delta", r)


if __name__ == "__main__":
    unittest.main()
