import unittest

from scripts.benchmark_feedback import design_next
from traffic_agent.task import Task


class BenchmarkTests(unittest.TestCase):
    def test_second_decision_uses_only_first_and_fixed_does_not_adapt(self):
        initial = [dict(from_node=i, to_node=i+1, old_parking=3, parking=2) for i in [1, 3, 5]]
        first = {"name": "first", "changes": initial, "comparison": {"eligible": False}}
        decision = design_next(first, [], {}, Task(), initial)
        self.assertEqual(len(decision["adaptive"]), 1)
        self.assertEqual(decision["adaptive"][0]["parking"], 2)
        self.assertEqual(len(decision["fixed"]), 3)
        self.assertTrue(all(c["parking"] == 1 for c in decision["fixed"]))
        self.assertTrue(all(c["parking"] == 2 for c in initial))


if __name__ == "__main__":
    unittest.main()
