import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent.agent import Agent
from traffic_agent.data import scoped_metrics, changed_input_files
from traffic_agent.oneway_agent import analyze_oneway
from traffic_agent.task import Task
from traffic_agent.report import write_report
from tests.test_agent import link


class OneWayAgentTests(unittest.TestCase):
    def test_full_grid_budget_and_single_direction(self):
        self.assertEqual(Task.from_dict({"scenario": "oneway"}).max_runs, 13)
        with self.assertRaises(ValueError):
            Task.from_dict({"scenario": "oneway", "max_runs": 12})
        with self.assertRaises(ValueError):
            Task.from_dict({"scenario": "oneway", "links": [[1, 2], [3, 4]]})
        self.assertEqual(changed_input_files([{"kind": "oneway", "closed_direction": [1, 2]}]), {"data/ManageR1.txt"})

    def test_grid_rejects_failed_response_and_bad_combination_then_repeats(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = Task.from_dict({"scenario": "oneway", "max_runs": 13})
            agent = Agent(task, root / "source", root / "output")
            agent.job.mkdir(parents=True)
            baseline = [link(0, 1, 2), link(1, 2, 1), link(2, 2, 3, vc=.9)]
            base = {"name": "baseline", "scenario": "baseline", "changes": [], "demand_factor": 1,
                    "execution": {"assigned_car_od_sum": 100}}
            agent.state.update(runs=[base], run_count=1)
            def simulate(name, changes, factor=1):
                rows = copy.deepcopy(baseline)
                if changes:
                    for row in rows[:2]:
                        row["speed_kmh"] = 20
                if "S3_combined" in name:
                    rows[2]["vc"] = 1.1
                record = {"name": name, "scenario": name, "changes": changes, "demand_factor": factor,
                          "execution": {"assigned_car_od_sum": 100 * factor}, "metrics": scoped_metrics(rows, agent.scope)}
                agent.state["runs"].append(record)
                agent.state["run_count"] += 1
                return rows, record
            measured = {"passed": False, "closed_direction": [1, 2],
                        "flows": {m: {"before": 100, "after": 100} for m in ("car", "taxi", "motor")}}
            with patch.object(agent, "simulation", side_effect=simulate), patch.object(agent, "event"), \
                 patch("traffic_agent.oneway_agent.response", return_value=measured):
                analyze_oneway(agent, baseline, base, {"selected": baseline[:2]})
            self.assertEqual(agent.state["run_count"], 13)
            self.assertEqual(len(agent.state["demand_grid"]), 3)
            self.assertEqual(len(agent.state["decisions"]), 9)
            self.assertEqual(agent.state["recommendation"], "S1_parking")
            self.assertTrue(all("S1_parking" in v for v in agent.state["recommendation_by_car_demand"].values()))
            self.assertFalse(agent.state["oneway_adapter_verified"])
            self.assertTrue(agent.state["repeatability"]["exact_match"])
            self.assertTrue(agent.state["factorial_design"]["full_demand_grid"])
            write_report(agent.job, agent.state)
            report = (agent.job / "report.md").read_text(encoding="utf-8")
            self.assertIn("单向与停车完整组合", report)
            self.assertNotIn("S2为单个禁转", report)


if __name__ == "__main__":
    unittest.main()
