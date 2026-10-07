import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.audit_bus_impact import route_times


class BusImpactTests(unittest.TestCase):
    def test_actual_bus_speed_route_sum_and_stale_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "data").mkdir()
            (path / "result").mkdir()
            (path / "data/geom.txt").write_text("1 2 500 3 1 3.5 0 0 A\n2 3 1000 3 1 3.5 0 0 B\n", encoding="utf-8")
            (path / "data/manageL.txt").write_text("head\n1 2 0 3 0\n2 3 0 3 0\n", encoding="utf-8")
            (path / "data/BusNodeBus.txt").write_text("routes 1\nlabels\n7 3 1 2 3\n", encoding="utf-8")
            (path / "data/BusNodeBRT.txt").write_text("routes 0\nlabels\n", encoding="utf-8")
            speed = path / "result/SpdLinkBus.tsd"
            speed.write_text("{'format':'txt'}\n30\n15\n", encoding="utf-8")
            stamp = datetime.now(timezone.utc).isoformat()
            manifest = {"started": stamp, "finished": stamp}
            times, evidence = route_times(path, manifest)
            self.assertAlmostEqual(times["BusNodeBus.txt:7"]["minutes"], 5)
            speed.write_text("{'format':'txt'}\n30\n0.01\n", encoding="utf-8")
            times, evidence = route_times(path, manifest)
            self.assertIsNone(times["BusNodeBus.txt:7"]["minutes"])
            self.assertEqual(times["BusNodeBus.txt:7"]["flagged_segments"], 1)
            os.utime(speed, (time.time() - 86400, time.time() - 86400))
            with self.assertRaises(ValueError):
                route_times(path, manifest)


if __name__ == "__main__":
    unittest.main()
