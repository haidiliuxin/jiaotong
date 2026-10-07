import tempfile
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from traffic_agent.oneway import OneWayNetwork, boundary_detour, close_motor_direction, access_rows, response, FLOW_FILES
from traffic_agent.circulation import TurnGraph
from traffic_agent.vehicle1 import prepare_parameters


def complete_network(protected=None):
    rows = [dict(from_node=a, to_node=b, length_km=1.0, included=True)
            for a in range(5) for b in range(5) if a != b]
    access = {(r["from_node"], r["to_node"]): (r["from_node"], r["to_node"], *([0] * 8)) for r in rows}
    return OneWayNetwork(rows, access, protected=protected)


class OneWayTests(unittest.TestCase):
    def test_diagnostic_parameter_schema_and_preserved_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "params").mkdir()
            executable = root / "model.exe"
            executable.write_bytes(b"fixture binary")
            raw = b"Mparam631.txt-header\r\n" + b"".join(b"label\r\n" + x + b"\r\n" for x in
                    (b"0", b"0", b"0", b"1", b"0", b"0", b"5", b"0.001"))
            (root / "params/Mparam631.txt").write_bytes(raw)
            for mode in ("car", "taxi", "motor", "truck"):
                (root / f"params/AsignParameter_{mode}.dat").write_bytes(b"od\r\nRwm\r\nvolume\r\n7.2\r\nszn\r\n")
            with patch("traffic_agent.vehicle1.VEHICLE1_SHA256", hashlib.sha256(executable.read_bytes()).hexdigest()):
                audit = prepare_parameters(root, executable)
                self.assertEqual(audit["derived_theta"], 3.3)
                self.assertEqual((root / "params/Mparam635.txt").read_bytes(), raw.replace(b"631", b"635", 1))
                self.assertEqual((root / "params/AsignParameter_car.dat").read_bytes(), b"od\r\nRwm\r\nvolume\r\n3.3\r\nszn\r\n")
                with self.assertRaises(ValueError):
                    prepare_parameters(root, executable)

    def test_diagnostic_unknown_binary_cannot_generate_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            executable = root / "model.exe"
            executable.write_bytes(b"another version")
            with self.assertRaisesRegex(ValueError, "二进制版本"):
                prepare_parameters(root, executable)
            self.assertFalse((root / "params/Mparam635.txt").exists())

    def test_boundary_and_all_modes(self):
        audit = complete_network().validate([1, 2])
        self.assertTrue(audit["eligible"])
        self.assertEqual(set(audit["modal_boundary_checks"]), {"car", "motor", "truck"})
        for records in audit["modal_boundary_checks"].values():
            self.assertEqual(len(records), 16)  # origin + 3 predecessors, destination + 3 successors
            self.assertTrue(all([1, 2] not in r["replacement"]["states"] for r in records))
        self.assertFalse(complete_network({(1, 2): ["two-node bus route"]}).validate([1, 2])["eligible"])
        graph = complete_network()
        graph.graphs["truck"].out[1] = [2]
        self.assertFalse(graph.validate([1, 2])["eligible"])

    def test_endpoint_route_does_not_prove_entry_or_exit_access(self):
        edges = [(0, 1), (1, 2), (1, 3), (3, 2), (2, 4)]
        rows = [dict(from_node=a, to_node=b, length_km=1, included=True) for a, b in edges]
        graph = TurnGraph(rows, {p: 0 for p in edges}, banned=[(0, 1, 3), (3, 2, 4)])
        self.assertIsNotNone(boundary_detour(graph, (1, 2), (-1, 1), (-1, 2), 5))
        self.assertIsNone(boundary_detour(graph, (1, 2), (0, 1), (-1, 2), 5))
        self.assertIsNone(boundary_detour(graph, (1, 2), (-1, 1), (2, 4), 5))

    def test_writer_preserves_nonmotor_reverse_and_gb_header(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ManageR1.txt"
            header = "起点 终点 bike car motor walk truck bus subway ship\r\n".encode("gb18030")
            reverse = b"2\t1  1 0 0 3 0 0 1 1\r\n"
            path.write_bytes(header + b"1\t2  1 0 0 3 0 0 1 1\r\n" + reverse)
            close_motor_direction(path, (1, 2))
            self.assertEqual(path.read_bytes(), header + b"1\t2  1 2 2 3 2 2 1 1\r\n" + reverse)
            self.assertEqual(access_rows(path)[(1, 2)], (1, 2, 1, 2, 2, 3, 2, 2, 1, 1))
            with self.assertRaises(ValueError):
                close_motor_direction(path, (1, 2))

    def test_ft_order_and_taxi_response_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            baseline, policy = Path(folder) / "base", Path(folder) / "policy"
            for scenario in (baseline, policy):
                (scenario / "result").mkdir(parents=True)
                for name in FLOW_FILES.values():
                    (scenario / "result" / name).write_text("10\n20\n" if scenario == baseline else "0\n30\n")
            (policy / "cache").mkdir()
            (policy / "data").mkdir()
            (policy / "data/ManageR1.txt").write_text("header\n1 2 0 2 2 0 2 2 0 0\n2 1 0 0 0 0 0 0 0 0\n")
            (policy / "cache/FT.dat").write_text("2 1\n1 2\n")
            (policy / "cache/ManageR1.dat").write_text("0 0 0 0 0 0 0 0\n0 2 2 0 2 2 0 0\n")
            rows = [dict(index=i, from_node=a, to_node=b) for i, (a, b) in enumerate([(1, 2), (2, 1)])]
            with patch("traffic_agent.oneway.geometry", return_value=rows):
                self.assertTrue(response(baseline, policy, [1, 2])["passed"])
                (policy / "cache/ManageR1.dat").write_text("0 0 0 0 0 0 0 0\n0 1 1 0 1 1 0 0\n")
                self.assertFalse(response(baseline, policy, [1, 2])["passed"])
                (policy / "cache/ManageR1.dat").write_text("0 0 0 0 0 0 0 0\n0 2 2 0 2 2 0 0\n")
                (policy / "result/VolLinkTax.tsd").write_text("1\n30\n")
                self.assertFalse(response(baseline, policy, [1, 2])["passed"])


if __name__ == "__main__":
    unittest.main()
