import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from traffic_agent.data import save_json
from traffic_agent.hard_access import verify_transform


class HardAccessTransformTests(unittest.TestCase):
    def fixture(self, folder):
        original = {
            "data/number.txt": b"3\r\n3\r\n9\r\n",
            "cache/FT.dat": b"1 2\r\n1 3\r\n2 3\r\n",
            "cache/tv.dat": b"2\r\n3\r\n3\r\n",
            "cache/n_r.dat": b"3\r\n2\r\n1\r\n0\r\n",
            "cache/rnode.dat": b"1\r\n2\r\n3\r\n",
            "cache/Rwm_car.dat": b"9999999999\r\n2.5\r\n4.2\r\n",
            "cache/Assign_car.od": b"0 1 2\r\n",
        }
        derived = dict(original, **{
            "data/number.txt": b"2\r\n3\r\n9\r\n",
            "cache/FT.dat": b"1 3\r\n2 3\r\n",
            "cache/tv.dat": b"3\r\n3\r\n",
            "cache/n_r.dat": b"3\r\n1\r\n1\r\n0\r\n",
            "cache/Rwm_car.dat": b"2.5\r\n4.2\r\n",
        })
        for name,data in original.items():
            if name not in ("cache/rnode.dat", "cache/Assign_car.od"):
                path = folder / "original_inputs" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        for name,data in derived.items():
            path = folder / "scenario" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        hashes = lambda data: {n:hashlib.sha256(v).hexdigest() for n,v in data.items()}
        save_json(folder / "manifest.json", dict(schema_version=2, mode="car", policy_closures=[[1,2]], input_hashes=hashes(original)))
        save_json(folder / "kept_original_indices.json", [1,2])
        save_json(folder / "native_inputs.json", hashes(derived))

    def test_original_bytes_prove_only_the_requested_edge_was_removed(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            _, ft, kept = verify_transform(folder)
            self.assertEqual(ft, [(1,2),(1,3),(2,3)])
            self.assertEqual(kept, [1,2])

    def test_altered_retained_cost_is_rejected_even_with_updated_native_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            path = folder / "scenario/cache/Rwm_car.dat"
            path.write_bytes(b"2.6\r\n4.2\r\n")
            native = json.loads((folder / "native_inputs.json").read_text(encoding="utf-8"))
            native["cache/Rwm_car.dat"] = hashlib.sha256(path.read_bytes()).hexdigest()
            save_json(folder / "native_inputs.json", native)
            with self.assertRaisesRegex(ValueError, "预定闭边之外"):
                verify_transform(folder)

    def test_changed_od_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder)
            (folder / "scenario/cache/Assign_car.od").write_bytes(b"0 2 2\r\n")
            with self.assertRaisesRegex(ValueError, "原始输入"):
                verify_transform(folder)


if __name__ == "__main__":
    unittest.main()
