import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

from traffic_agent.circulation import TurnGraph, append_restrictions, restrictions, read_turn_volumes
from traffic_agent.data import changed_input_files
from traffic_agent.task import Task, parse_request
from traffic_agent.circulation_agent import analyze_circulation
from traffic_agent.data import scoped_metrics


def graph(edges, banned=(), protected=None):
    rows = [dict(from_node=a, to_node=b, length_km=length, included=True) for a, b, length in edges]
    return TurnGraph(rows, {(a, b): 0 for a, b, length in edges}, banned, protected)


class CirculationTests(unittest.TestCase):
    def test_entry_and_exit_states_not_merely_endpoint_path(self):
        # 2->4->3 reaches destination 3, but cannot restore outgoing state (2,3).
        g = graph([(1, 2, 1), (2, 3, 1), (2, 4, .2), (4, 3, .2)])
        self.assertFalse(g.validate([(1, 2, 3)])["eligible"])

    def test_replacement_respects_existing_turn_and_car_access(self):
        edges = [(1, 2, 1), (2, 3, 1), (2, 4, .2), (4, 5, .2), (5, 2, .2)]
        g = graph(edges)
        result = g.validate([(1, 2, 3)])
        self.assertTrue(result["eligible"])
        self.assertEqual(result["turns"][0]["replacement"]["nodes"], [1, 2, 4, 5, 2, 3])
        self.assertFalse(graph(edges, [(2, 4, 5)]).validate([(1, 2, 3)])["eligible"])
        self.assertFalse(graph(edges[:-1]).validate([(1, 2, 3)])["eligible"])
        self.assertFalse(g.validate([(1, 2, 3)], max_extra_km=.5)["eligible"])

    def test_combined_bans_cannot_rely_on_each_other(self):
        edges = [(1, 2, 1), (2, 3, 1), (2, 4, .2), (4, 5, .2), (5, 2, .2),
                 (4, 6, .2), (6, 1, .2)]
        g = graph(edges)
        self.assertTrue(g.validate([(1, 2, 3)])["eligible"])
        self.assertFalse(g.validate([(1, 2, 3), (1, 2, 4)])["eligible"])

    def test_bus_and_virtual_connector_are_protected(self):
        edges = [(1, 2, 1), (2, 3, 1), (2, 4, .2), (4, 5, .2), (5, 2, .2)]
        g = graph(edges, protected={(1, 2, 3): ["BusNodeBus.txt:7"]})
        self.assertFalse(g.validate([(1, 2, 3)])["eligible"])
        g = graph(edges)
        g.edges[(4, 5)]["included"] = False
        self.assertFalse(g.validate([(1, 2, 3)])["eligible"])

    def test_manageN_preserves_unrelated_bytes_and_group_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "manageN.txt"
            unrelated = b"9\t1\t1\t8\t9\t10\r\n"
            raw = "实施转向限制的节点数目：\t2\r\n节点 数量 序号 from via to\r\n".encode("gb18030") + b"2\t1\t1\t1\t2\t3\r\n" + unrelated
            path.write_bytes(raw)
            append_restrictions(path, [(4, 2, 5), (6, 7, 8)])
            self.assertEqual(restrictions(path), {(1, 2, 3), (4, 2, 5), (6, 7, 8), (8, 9, 10)})
            self.assertIn(unrelated, path.read_bytes())
            self.assertIn(b"2\t2\t1\t1\t2\t3", path.read_bytes())
            with self.assertRaises(ValueError):
                append_restrictions(path, [(1, 2, 3)])

    def test_turn_matrix_does_not_include_totals(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "turn.tsd"
            path.write_text("{'format':'txt'}\n交叉口编号: No.2\n进口－出口 3 4 进口流量\n1 8.50 2.00 10.50\n出口流量 8.50 2.00 10.50\n", encoding="utf-8")
            self.assertEqual(read_turn_volumes(path), {(1, 2, 3): 8.5, (1, 2, 4): 2.0})

    def test_trial_budget_and_input_whitelist(self):
        self.assertEqual(parse_request("单向道路组织").scenario, "oneway")
        with self.assertRaises(ValueError):
            Task.from_dict(dict(scenario="circulation", links=[[1, 2], [3, 4]]))
        with self.assertRaises(ValueError):
            Task.from_dict(dict(scenario="circulation", max_runs=4, demand_factors=[1], repeat_baseline=True))
        self.assertEqual(changed_input_files([{"kind": "turn", "turn": [1, 2, 3]}, {"parking": 2}]),
                         {"data/manageL.txt", "data/manageN.txt"})
        with self.assertRaises(ValueError):
            changed_input_files([{"kind": "shell"}])

    def test_failed_software_response_cannot_win_despite_better_metrics(self):
        from test_agent import link
        with tempfile.TemporaryDirectory() as folder:
            task = Task.from_dict(dict(scenario="circulation", max_runs=4, demand_factors=[1], repeat_baseline=False))
            baseline = [link(0, 1, 2)]
            base = {"name": "baseline"}
            agent = SimpleNamespace(job=Path(folder), task=task, scope=None, persist=lambda: None,
                                    event=lambda *args: None,
                                    state={"runs": [base], "decisions": [], "sensitivity": []})
            def simulate(name, changes):
                speed = {"S1_parking": 15, "S2_turn": 30, "S3_combined": 40}[name]
                rows = [link(0, 1, 2, speed=speed)]
                record = {"name": name, "changes": changes, "metrics": scoped_metrics(rows, agent.scope)}
                agent.state["runs"].append(record)
                return rows, record
            agent.simulation = simulate
            with patch("traffic_agent.circulation_agent.turn_response", return_value={"passed": False}):
                analyze_circulation(agent, baseline, base, {"selected": baseline, "turn": [3, 1, 2]})
            self.assertEqual(agent.state["recommendation"], "S1_parking")
            self.assertFalse(agent.state["turn_adapter_verified"])
            self.assertEqual(len(agent.state["runs"]), 4)


if __name__ == "__main__":
    unittest.main()
